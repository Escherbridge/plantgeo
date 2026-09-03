"""Publish the newest settled ERA5-Land soil days directly, one product-day per lane-day lock."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import sys
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.foundation.parquet.paths import partition_day_statuses
from agri_data_service.pipeline.direct.soil.adapter import (
    SOIL_DIRECT_KIND,
    DirectSoilFieldAdapter,
    DirectSoilFieldError,
    refuse_immutable_day,
)
from agri_data_service.pipeline.direct.soil.products import (
    SOIL_DEFAULT_TIME_BUDGET_SECONDS,
    SOIL_PRODUCT_IDS,
    products_for,
)
from agri_data_service.pipeline.direct.soil.source import (
    ERA5_LAND_CHUNK_CELL_COUNT,
    SoilSourceCache,
    SoilTimeBudgetExhaustedError,
    fetch_soil_day,
    support_chunks,
)
from agri_data_service.pipeline.direct.soil.support import ERA5_LAND_SUPPORT_CELL_COUNT, load_era5_land_support
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.availability_extension import (
    AvailabilityExtensionTally,
    retry_pending_availability,
)
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.gap_fill import (
    GAP_FILL_PARTITION_KIND,
    _lane_day_lock_key,
    fill_one_lane_day,
    postgres_lane_day_lock,
    unlocked_lane_day,
)
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.paths import PartitionDayStatus
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.direct.soil.products import SoilFieldProduct
    from agri_data_service.pipeline.direct.soil.source import Era5LandChunk
    from agri_data_service.pipeline.direct.soil.support import Era5LandSupport
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage

SOIL_DIRECT_ALL_TIERS: Final[tuple[ZoomTier, ...]] = (LANE_BASE_ZOOM_TIER, *DERIVED_ZOOM_TIERS)
SOIL_DIRECT_RUN_ID_PREFIX: Final = "soil-era5-land-forward:"
SOIL_DEFAULT_MAX_DAYS: Final = 1
SOIL_MAX_DAYS: Final = 5
SOIL_MAX_TIME_BUDGET_SECONDS: Final = 3_000.0
SOIL_DEFAULT_RETRY_ATTEMPTS: Final = 4
SOIL_MAX_RETRY_ATTEMPTS: Final = 10
SOIL_DEFAULT_RETRY_BASE_SECONDS: Final = 5.0
SOIL_MAX_RETRY_BASE_SECONDS: Final = 60.0
SOIL_DEFAULT_RETRY_MAX_SECONDS: Final = 60.0
SOIL_MAX_RETRY_MAX_SECONDS: Final = 300.0
SOIL_DEFAULT_CONTENTION_TIMEOUT_SECONDS: Final = 300.0
SOIL_MAX_CONTENTION_TIMEOUT_SECONDS: Final = 3_600.0
SOIL_STATEMENT_TIMEOUT_SECONDS: Final = 120
SOIL_MIN_DELAY_SECONDS: Final = 0.1
#: How far back one turn is willing to look for an unfilled day before reporting a backlog. The
#: whole owed window is bounded by the history floor, but a single turn must stay bounded too.
SOIL_BACKLOG_SCAN_DAYS: Final = 400
#: How far back a turn re-examines a day it has ALREADY governed as absent. The archive backfills a
#: day it first answered null for, and `adapter._retract_disproven_absence` is the only thing that
#: undoes such a marker -- it runs only on a day the walk selects, so an absence the walk skipped
#: forever was permanent whatever the archive did next. Bounded, because rechecking the whole history
#: would spend every turn re-fetching days that settled years ago. Rechecks are queued BEHIND real
#: gaps, so they can never starve a day that has no data at all.
SOIL_ABSENCE_RECHECK_DAYS: Final = 14
#: The one outcome a bounded turn reports instead of failing when its wall clock runs out.
SOIL_TIME_BUDGET_OUTCOME: Final = "time_budget_exhausted"
#: The outcome an all-null day reports when nothing proves the mirror has moved past it. Not a
#: failure: the day is simply not settled yet, and the next turn asks again.
SOIL_SOURCE_UNSETTLED_OUTCOME: Final = "source_unsettled"
#: The one outcome a bounded turn reports instead of fetching past its per-turn request budget.
SOIL_REQUEST_BUDGET_OUTCOME: Final = "request_budget_exhausted"
MONTHS_PER_YEAR: Final = 12


@dataclass(frozen=True, slots=True)
class SoilForwardConfig:
    """Bound every source request, day count, retry series and contention wait of one turn."""

    product_id: str
    max_days: int
    time_budget_seconds: float
    retry_attempts: int
    retry_base_seconds: float
    retry_max_seconds: float
    contention_timeout_seconds: float
    run_id: str | None = None
    today: date | None = None

    @property
    def request_budget(self) -> int:
        """Cap this turn's upstream chunk requests.

        Counted in CHUNKS, not cells: one archive request carries fifty locations and every
        variable, so a day costs `ceil(1568 / 50) = 32` requests no matter how many products are
        selected. The `+ 1` is integer-ceiling arithmetic, not slack. All eight products share one
        publication clock, so unlike the climate writer there is no second edge to multiply by.
        """
        chunks_per_day = -(-ERA5_LAND_SUPPORT_CELL_COUNT // ERA5_LAND_CHUNK_CELL_COUNT)
        return chunks_per_day * self.max_days


class SoilForwardConfigError(ValueError):
    """Raised when a turn is asked for an unbounded or self-contradictory shape."""


def emit(payload: Mapping[str, object]) -> None:
    """Write one stable JSON progress record to stderr, leaving stdout for the terminal report."""
    print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)


def settled_through(product: SoilFieldProduct, *, today: date) -> date:
    """Return the newest day this product may be held to, given its own publication lag."""
    return today - timedelta(days=product.publication_lag_days)


async def run_soil_forward(config: SoilForwardConfig) -> dict[str, object]:
    """Publish the newest unfilled settled day of every selected product, newest first."""
    _validate_config(config)
    run_id = config.run_id or f"{SOIL_DIRECT_RUN_ID_PREFIX}{uuid.uuid4()}"
    today = config.today or datetime.now(UTC).date()
    products = products_for(config.product_id)
    store = ObjectStore.from_settings()
    availability_storage = BotoAvailabilityStorage.from_settings()
    cache = SoilSourceCache(request_budget=config.request_budget)
    deadline = time.monotonic() + config.time_budget_seconds
    results: list[dict[str, object]] = []
    # ONE TALLY FOR THE WHOLE RUN. Without it every availability verdict lands only inside a day's
    # detail string: a `ladder_incomplete` or `retry_claim_failed` day would be permanently outside
    # the index while the run reported that loss as a green tick.
    availability = AvailabilityExtensionTally()

    loader_database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_session(loader_database_url) as session:
        support = await load_era5_land_support(session)
        chunks = support_chunks(support)
        await session.rollback()
        for product in products:
            if time.monotonic() >= deadline:
                results.append(_skipped(product, today=today, outcome=SOIL_TIME_BUDGET_OUTCOME))
                continue
            results.append(
                await _publish_product(
                    session,
                    store,
                    product,
                    support=support,
                    chunks=chunks,
                    cache=cache,
                    today=today,
                    run_id=run_id,
                    config=config,
                    deadline=deadline,
                    availability_storage=availability_storage,
                    availability=availability,
                )
            )

    report: dict[str, object] = {
        "status": "completed",
        "run_id": run_id,
        "today": today.isoformat(),
        "product": config.product_id,
        "streams": [product.stream for product in products],
        "request_budget": cache.request_budget,
        "requests_spent": cache.requests_spent,
        **availability.to_summary(),
        "results": results,
    }
    emit({"event": "soil_forward_complete", **report})
    return report


async def _publish_product(  # noqa: PLR0913 - the store, product, support, cache, clock and budget are distinct
    session: AsyncSession,
    store: ObjectStore,
    product: SoilFieldProduct,
    *,
    support: Era5LandSupport,
    chunks: Sequence[Era5LandChunk],
    cache: SoilSourceCache,
    today: date,
    run_id: str,
    config: SoilForwardConfig,
    deadline: float,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
) -> dict[str, object]:
    """Take one product's turn: census its owed window, then publish at most `max_days` days.

    THE OWED LEDGER IS DRAINED FIRST, once per product per run. Nothing else retries these claims:
    `retry_pending_availability` is otherwise called only from `run_gap_fill`, and activating
    `soil-era5-land-direct-forward` deactivates the eight generic lanes through `conflicts_with` --
    so a soil day whose pointer read failed writes a claim that no driver in this service would ever
    come back for, and the base-tier census never revisits a completed day.
    """
    ceiling = settled_through(product, today=today)
    if ceiling < product.history_floor:
        return _skipped(product, today=today, outcome="not_yet_settled")
    retried = await _retry_owed_availability(
        session,
        store,
        product,
        deadline=deadline,
        availability_storage=availability_storage,
        availability=availability,
    )
    first_day = max(product.history_floor, ceiling - timedelta(days=SOIL_BACKLOG_SCAN_DAYS - 1))
    statuses = await asyncio.to_thread(_tier_status_window, store, product, first_day, ceiling)
    backlog = _pending_days(product, statuses)
    selected = backlog[: config.max_days]
    published: list[dict[str, object]] = []
    for day in selected:
        if time.monotonic() >= deadline:
            published.append(_stopped_day(day, outcome=SOIL_TIME_BUDGET_OUTCOME, detail="before the day started"))
            break
        if not cache.can_afford(chunks, day):
            published.append(
                _stopped_day(
                    day,
                    outcome=SOIL_REQUEST_BUDGET_OUTCOME,
                    detail=f"{cache.remaining_requests} of {cache.request_budget} request(s) left",
                )
            )
            break
        published.append(
            await _publish_day_with_retries(
                session,
                store,
                product,
                day,
                support=support,
                chunks=chunks,
                cache=cache,
                today=today,
                run_id=run_id,
                config=config,
                deadline=deadline,
                availability_storage=availability_storage,
                availability=availability,
                mirrored_past=_mirrored_past_day(statuses, day),
            )
        )
    return {
        "layer": product.stream,
        "product": product.product_id,
        "outcome": "idempotent_noop" if not backlog else "published",
        "history_floor": product.history_floor.isoformat(),
        "settled_through": ceiling.isoformat(),
        "publication_lag_days": product.publication_lag_days,
        "scan_first_day": first_day.isoformat(),
        "backlog_days": len(backlog),
        "availability_retried_days": retried,
        "days": published,
    }


async def _retry_owed_availability(  # noqa: PLR0913 - one coordinate of the product's turn per arg
    session: AsyncSession,
    store: ObjectStore,
    product: SoilFieldProduct,
    *,
    deadline: float,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
) -> int:
    """Retry this product's owed availability claims once, inside the turn's budget. Never raises.

    Bounded by the SAME deadline the publication walk is: the retry re-verifies every physical part
    of a day, so a run whose clock has already run out must not start one. An unindexed day is not
    lost by waiting -- its claim is what makes it recoverable -- but a turn that overran its budget
    for it would cost the next product its whole turn.
    """
    if time.monotonic() >= deadline:
        return 0
    try:
        outcomes = await retry_pending_availability(
            session,
            store,
            lane=LANE_REGISTRY[product.stream].slug,
            kind=GAP_FILL_PARTITION_KIND,
            availability=availability_storage,
            now=lambda: datetime.now(UTC),
        )
    except Exception as error:  # an owed index entry may never stop a product from publishing
        emit(
            {
                "event": "soil_forward_availability_retry_failed",
                "layer": product.stream,
                "detail": f"{type(error).__name__}: {error}",
            }
        )
        return 0
    for outcome in outcomes:
        availability.record(outcome)
        emit(
            {
                "event": "soil_forward_availability_retry",
                "layer": product.stream,
                "state": outcome.state,
                "detail": outcome.note,
            }
        )
    return len(outcomes)


def _skipped(product: SoilFieldProduct, *, today: date, outcome: str) -> dict[str, object]:
    """Report a product that took no turn, naming why rather than reporting an empty success."""
    return {
        "layer": product.stream,
        "product": product.product_id,
        "outcome": outcome,
        "history_floor": product.history_floor.isoformat(),
        "settled_through": settled_through(product, today=today).isoformat(),
        "publication_lag_days": product.publication_lag_days,
        "days": [],
    }


def _stopped_day(day: date, *, outcome: str, attempts: int = 0, detail: str | None = None) -> dict[str, object]:
    """Report one day a bound stopped before it published, without claiming a source receipt it has none of."""
    return {
        "day": day.isoformat(),
        "outcome": outcome,
        "attempts": attempts,
        "base_rows": 0,
        "null_value_cells": 0,
        "source_receipt": None,
        "parts": 0,
        "rows_across_write": 0,
        "written_bytes": 0,
        "detail": detail,
    }


async def _publish_day_with_retries(  # noqa: PLR0913 - one lane-day coordinate per argument
    session: AsyncSession,
    store: ObjectStore,
    product: SoilFieldProduct,
    day: date,
    *,
    support: Era5LandSupport,
    chunks: Sequence[Era5LandChunk],
    cache: SoilSourceCache,
    today: date,
    run_id: str,
    config: SoilForwardConfig,
    deadline: float,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
    mirrored_past: date | None,
) -> dict[str, object]:
    """Acquire the lane-day lock once, then refetch and republish under it for every bounded attempt."""
    refuse_immutable_day(product, day)
    lane = LANE_REGISTRY[product.stream]
    contention_deadline = min(time.monotonic() + config.contention_timeout_seconds, deadline)
    while True:
        if time.monotonic() >= deadline:
            return _stopped_day(
                day,
                outcome=SOIL_TIME_BUDGET_OUTCOME,
                detail="the turn's time budget ran out before the lane-day lock was granted",
            )
        async with postgres_lane_day_lock(session, _lane_day_lock_key(lane, day)) as granted:
            if granted:
                return await _publish_locked_day(
                    session,
                    store,
                    product,
                    day,
                    support=support,
                    chunks=chunks,
                    cache=cache,
                    today=today,
                    run_id=run_id,
                    config=config,
                    deadline=deadline,
                    availability_storage=availability_storage,
                    availability=availability,
                    mirrored_past=mirrored_past,
                )
        await session.rollback()
        remaining = contention_deadline - time.monotonic()
        if remaining <= 0:
            if time.monotonic() >= deadline:
                return _stopped_day(
                    day,
                    outcome=SOIL_TIME_BUDGET_OUTCOME,
                    detail="the turn's time budget ran out while waiting for the lane-day lock",
                )
            raise DirectSoilFieldError(
                f"lane-day contention for {product.stream} {day.isoformat()} exceeded "
                f"{config.contention_timeout_seconds:g}s"
            )
        delay = min(remaining, _retry_delay(1, config=config))
        emit(
            {
                "event": "soil_forward_contention",
                "run_id": run_id,
                "layer": product.stream,
                "day": day.isoformat(),
                "retry_in_seconds": round(delay, 3),
            }
        )
        await asyncio.sleep(delay)


async def _publish_locked_day(  # noqa: PLR0913 - one lane-day coordinate per argument
    session: AsyncSession,
    store: ObjectStore,
    product: SoilFieldProduct,
    day: date,
    *,
    support: Era5LandSupport,
    chunks: Sequence[Era5LandChunk],
    cache: SoilSourceCache,
    today: date,
    run_id: str,
    config: SoilForwardConfig,
    deadline: float,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
    mirrored_past: date | None,
) -> dict[str, object]:
    """Refetch before every write attempt while one advisory lock stays held, then prove all four rungs."""
    lane = LANE_REGISTRY[product.stream]
    for attempt in range(1, config.retry_attempts + 1):
        if time.monotonic() >= deadline:
            return _stopped_day(
                day,
                outcome=SOIL_TIME_BUDGET_OUTCOME,
                attempts=attempt - 1,
                detail="the turn's time budget ran out before this attempt began",
            )
        adapter = DirectSoilFieldAdapter(
            product=product,
            fetch_source=lambda: fetch_soil_day(
                product,
                day=day,
                support=support,
                chunks=chunks,
                cache=cache,
                deadline=deadline,
            ),
            mirrored_past_proof=lambda: _mirrored_past_proof(product, day=day, mirrored_past=mirrored_past),
        )
        try:
            outcome, parts, rows, written_bytes, detail = await fill_one_lane_day(
                session,
                store,
                replace(lane, adapter=adapter),
                day=day,
                run_id=run_id,
                now=lambda: datetime.now(UTC),
                today=today,
                lane_day_lock=unlocked_lane_day,
                statement_timeout_seconds=SOIL_STATEMENT_TIMEOUT_SECONDS,
                availability_storage=availability_storage,
                availability_tally=availability,
            )
            await session.rollback()
        except SoilTimeBudgetExhaustedError as stop:
            with suppress(Exception):
                await session.rollback()
            return _stopped_day(day, outcome=SOIL_TIME_BUDGET_OUTCOME, attempts=attempt, detail=str(stop))
        except Exception as error:
            with suppress(Exception):
                await session.rollback()
            outcome, parts, rows, written_bytes = "raised", 0, 0, 0
            detail = f"{type(error).__name__}: {error}"
        if adapter.unsettled_refusal is not None:
            # NOT A FAILURE AND NOT A RETRY. The archive has not reached this day, so refetching it
            # inside the same turn asks the same question of the same mirror; the next turn is the
            # soonest the answer can differ. Reported as its own outcome so a run stays green.
            return _stopped_day(
                day,
                outcome=SOIL_SOURCE_UNSETTLED_OUTCOME,
                attempts=attempt,
                detail=str(adapter.unsettled_refusal),
            )
        if outcome == "blocked":
            raise DirectSoilFieldError(detail or f"{product.stream} {day.isoformat()} is blocked")
        if outcome == "absent":
            return _day_result(
                product,
                day,
                adapter=adapter,
                outcome=outcome,
                parts=parts,
                rows=rows,
                written_bytes=written_bytes,
                attempts=attempt,
                detail=detail,
            )
        # A READ-BACK, NOT A CLAIM: `written` is the writer's own word for what it just did, and only
        # a day whose four rungs all read `data` out of the bucket is accepted as published.
        verification_detail = await _verify_written_ladder(store, product, day) if outcome == "written" else None
        if outcome == "written" and verification_detail is None:
            return _day_result(
                product,
                day,
                adapter=adapter,
                outcome=outcome,
                parts=parts,
                rows=rows,
                written_bytes=written_bytes,
                attempts=attempt,
                detail=detail,
            )
        if attempt >= config.retry_attempts:
            raise DirectSoilFieldError(
                f"{product.stream} {day.isoformat()} did not publish a complete four-rung ladder after "
                f"{attempt} attempt(s): outcome={outcome}, detail={detail}, verification={verification_detail}"
            )
        delay = min(_retry_delay(attempt, config=config), max(0.0, deadline - time.monotonic()))
        if delay <= 0:
            return _stopped_day(
                day,
                outcome=SOIL_TIME_BUDGET_OUTCOME,
                attempts=attempt,
                detail=f"outcome={outcome}, detail={detail}, verification={verification_detail}",
            )
        emit(
            {
                "event": "soil_forward_retry",
                "run_id": run_id,
                "layer": product.stream,
                "day": day.isoformat(),
                "attempt": attempt,
                "outcome": outcome,
                "detail": detail,
                "retry_in_seconds": round(delay, 3),
            }
        )
        await asyncio.sleep(delay)
    raise AssertionError("bounded soil publish attempts exhausted")


async def _verify_written_ladder(store: ObjectStore, product: SoilFieldProduct, day: date) -> str | None:
    """Return why the day's four rungs do not all read `data` yet, or `None` when every one of them does."""
    try:
        tier_statuses = await asyncio.to_thread(_tier_status_day, store, product, day)
    except Exception as error:
        return f"{type(error).__name__}: {error}"
    if all(status == "data" for status in tier_statuses.values()):
        return None
    return f"tier statuses after the write were {tier_statuses}"


def _day_result(  # noqa: PLR0913 - the adapter evidence and the finalizer counters are separate facts
    product: SoilFieldProduct,
    day: date,
    *,
    adapter: DirectSoilFieldAdapter,
    outcome: str,
    parts: int,
    rows: int,
    written_bytes: int,
    attempts: int,
    detail: str | None,
) -> dict[str, object]:
    """Render one lane-day, carrying the source receipt the publication was justified by."""
    source = adapter.source
    if source is None:
        raise DirectSoilFieldError(
            f"{product.stream} {day.isoformat()} returned {outcome} without a completed locked source fetch"
        )
    return {
        "day": day.isoformat(),
        "outcome": outcome,
        "attempts": attempts,
        "base_rows": len(source.values),
        "null_value_cells": source.null_value_cells,
        "source_receipt": source.receipt.as_event(),
        "parts": parts,
        "rows_across_write": rows,
        "written_bytes": written_bytes,
        "detail": detail,
    }


def _tier_status_window(
    store: ObjectStore,
    product: SoilFieldProduct,
    first_day: date,
    last_day: date,
) -> dict[ZoomTier, dict[date, PartitionDayStatus]]:
    """List every rung of one product across the owed window, one month prefix at a time."""
    keys_by_tier: dict[ZoomTier, list[str]] = {tier: [] for tier in SOIL_DIRECT_ALL_TIERS}
    cursor = date(first_day.year, first_day.month, 1)
    while cursor <= last_day:
        for tier in SOIL_DIRECT_ALL_TIERS:
            keys_by_tier[tier].extend(
                store.list_partition_keys(product.stream, SOIL_DIRECT_KIND, tier, year=cursor.year, month=cursor.month)
            )
        cursor = date(
            cursor.year + (1 if cursor.month == MONTHS_PER_YEAR else 0),
            1 if cursor.month == MONTHS_PER_YEAR else cursor.month + 1,
            1,
        )
    return {
        tier: partition_day_statuses(
            layer=product.stream,
            kind=SOIL_DIRECT_KIND,
            zoom=tier,
            first_day=first_day,
            last_day=last_day,
            keys=keys,
        )
        for tier, keys in keys_by_tier.items()
    }


def _tier_status_day(
    store: ObjectStore,
    product: SoilFieldProduct,
    day: date,
) -> dict[ZoomTier, PartitionDayStatus]:
    """Read the durable completion checkpoint of one product-day at every rung."""
    return {
        tier: partition_day_statuses(
            layer=product.stream,
            kind=SOIL_DIRECT_KIND,
            zoom=tier,
            first_day=day,
            last_day=day,
            keys=store.list_partition_keys(product.stream, SOIL_DIRECT_KIND, tier, year=day.year, month=day.month),
        )[day]
        for tier in SOIL_DIRECT_ALL_TIERS
    }


def _pending_days(
    product: SoilFieldProduct,
    statuses: Mapping[ZoomTier, Mapping[date, PartitionDayStatus]],
) -> tuple[date, ...]:
    """Return the owed days newest first, then the recent ABSENCES this turn should re-examine.

    A governed absence is not permanent evidence -- the archive backfills a day it first answered
    null for -- so the newest `SOIL_ABSENCE_RECHECK_DAYS` of them are re-selected, behind every day
    that owes real work. See `pipeline/direct/AGENTS.md`.
    """
    days = tuple(statuses[SOIL_DIRECT_ALL_TIERS[0]])
    if not days:
        return ()
    recheck_floor = max(days) - timedelta(days=SOIL_ABSENCE_RECHECK_DAYS - 1)
    pending: list[date] = []
    rechecks: list[date] = []
    for day in reversed(days):
        rung = {tier: statuses[tier][day] for tier in SOIL_DIRECT_ALL_TIERS}
        if "conflict" in rung.values():
            raise DirectSoilFieldError(f"{product.stream} {day.isoformat()} has a data/absence conflict: {rung}")
        if rung[LANE_BASE_ZOOM_TIER] == "absent":
            if any(rung[tier] in {"data", "incomplete"} for tier in DERIVED_ZOOM_TIERS):
                raise DirectSoilFieldError(
                    f"{product.stream} {day.isoformat()} is absent at the base rung but carries derived parts: {rung}"
                )
            if day >= recheck_floor:
                rechecks.append(day)
            continue
        if any(status != "data" for status in rung.values()):
            pending.append(day)
    return (*pending, *rechecks)


def _mirrored_past_day(
    statuses: Mapping[ZoomTier, Mapping[date, PartitionDayStatus]],
    day: date,
) -> date | None:
    """Return the earliest LATER settled day this product already publishes with values, or `None`.

    THE SIMPLEST HONEST PROOF that the archive has mirrored past `day`: a day after it answered with
    rows, so an all-null answer here is the archive's verdict rather than its backlog. Read out of
    the census listing the turn already paid for -- no extra request, and no upstream call.
    """
    base = statuses[LANE_BASE_ZOOM_TIER]
    return next((later for later in sorted(base) if later > day and base[later] == "data"), None)


def _mirrored_past_proof(product: SoilFieldProduct, *, day: date, mirrored_past: date | None) -> str | None:
    """Render the sentence a governed absence carries as its justification, or `None` when it has none."""
    if mirrored_past is None:
        return None
    return (
        f"{product.stream} is published with values for {mirrored_past.isoformat()}, which is later than "
        f"{day.isoformat()}, so the ERA5-Land mirror has moved past this day and its all-null answer is settled"
    )


def _retry_delay(attempt: int, *, config: SoilForwardConfig) -> float:
    """Return a jittered, capped exponential wait so concurrent turns do not resynchronise."""
    ceiling = min(config.retry_max_seconds, config.retry_base_seconds * (2 ** max(0, attempt - 1)))
    return float(ceiling + random.uniform(0.0, min(1.0, ceiling / 4)))


def _validate_config(config: SoilForwardConfig) -> None:
    """Fail closed on every process-bound knob before a socket or a session is opened."""
    if config.product_id != "all" and config.product_id not in SOIL_PRODUCT_IDS:
        raise SoilForwardConfigError(
            f"--product must be one of {', '.join(SOIL_PRODUCT_IDS)} or all, got {config.product_id!r}"
        )
    if not 1 <= config.max_days <= SOIL_MAX_DAYS:
        raise SoilForwardConfigError(f"--max-days must be between 1 and {SOIL_MAX_DAYS}")
    if not 1 <= config.retry_attempts <= SOIL_MAX_RETRY_ATTEMPTS:
        raise SoilForwardConfigError(f"--retry-attempts must be between 1 and {SOIL_MAX_RETRY_ATTEMPTS}")
    bounds = {
        "--time-budget-seconds": (config.time_budget_seconds, SOIL_MAX_TIME_BUDGET_SECONDS),
        "--retry-base-seconds": (config.retry_base_seconds, SOIL_MAX_RETRY_BASE_SECONDS),
        "--retry-max-seconds": (config.retry_max_seconds, SOIL_MAX_RETRY_MAX_SECONDS),
        "--contention-timeout-seconds": (config.contention_timeout_seconds, SOIL_MAX_CONTENTION_TIMEOUT_SECONDS),
    }
    for name, (value, maximum) in bounds.items():
        if not math.isfinite(value) or not SOIL_MIN_DELAY_SECONDS <= value <= maximum:
            raise SoilForwardConfigError(
                f"{name} must be finite and between {SOIL_MIN_DELAY_SECONDS:g} and {maximum:g}, got {value!r}"
            )
    if config.retry_max_seconds < config.retry_base_seconds:
        raise SoilForwardConfigError("--retry-max-seconds must be at least --retry-base-seconds")


def parser() -> argparse.ArgumentParser:
    """Build the bounded, forward-only soil lane operator."""
    built = argparse.ArgumentParser(description=__doc__)
    built.add_argument("--product", default="all", choices=[*SOIL_PRODUCT_IDS, "all"])
    built.add_argument("--max-days", type=int, default=SOIL_DEFAULT_MAX_DAYS)
    built.add_argument("--time-budget-seconds", type=float, default=SOIL_DEFAULT_TIME_BUDGET_SECONDS)
    built.add_argument("--run-id", default=None)
    built.add_argument("--retry-attempts", type=int, default=SOIL_DEFAULT_RETRY_ATTEMPTS)
    built.add_argument("--retry-base-seconds", type=float, default=SOIL_DEFAULT_RETRY_BASE_SECONDS)
    built.add_argument("--retry-max-seconds", type=float, default=SOIL_DEFAULT_RETRY_MAX_SECONDS)
    built.add_argument("--contention-timeout-seconds", type=float, default=SOIL_DEFAULT_CONTENTION_TIMEOUT_SECONDS)
    return built


def parse_args(argv: Sequence[str] | None = None) -> SoilForwardConfig:
    """Validate every operator input at the boundary and hand back one bounded turn."""
    built = parser()
    arguments = built.parse_args(argv)
    config = SoilForwardConfig(
        product_id=arguments.product,
        max_days=arguments.max_days,
        time_budget_seconds=arguments.time_budget_seconds,
        retry_attempts=arguments.retry_attempts,
        retry_base_seconds=arguments.retry_base_seconds,
        retry_max_seconds=arguments.retry_max_seconds,
        contention_timeout_seconds=arguments.contention_timeout_seconds,
        run_id=arguments.run_id,
    )
    try:
        _validate_config(config)
    except SoilForwardConfigError as error:
        built.error(str(error))
    return config


async def main(argv: Sequence[str] | None = None) -> int:
    """Run one bounded turn and emit exactly one terminal report on stdout."""
    config = parse_args(argv)
    try:
        report = await run_soil_forward(config)
    except Exception as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


__all__ = [
    "SOIL_ABSENCE_RECHECK_DAYS",
    "SOIL_BACKLOG_SCAN_DAYS",
    "SOIL_DEFAULT_TIME_BUDGET_SECONDS",
    "SOIL_DIRECT_ALL_TIERS",
    "SOIL_MAX_DAYS",
    "SOIL_REQUEST_BUDGET_OUTCOME",
    "SOIL_SOURCE_UNSETTLED_OUTCOME",
    "SOIL_TIME_BUDGET_OUTCOME",
    "SoilForwardConfig",
    "SoilForwardConfigError",
    "main",
    "parse_args",
    "parser",
    "run_soil_forward",
    "settled_through",
]
