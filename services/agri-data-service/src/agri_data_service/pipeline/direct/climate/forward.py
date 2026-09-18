"""Publish the newest settled NASA POWER climate days directly, one product-day per lane-day lock."""

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
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct import (
    IDEMPOTENT_NOOP,
    LANE_DAY_OUTCOMES,
    NO_SUCH_DEFECT,
    NOT_BBOX_BOUNDED,
    NOT_YET_SETTLED,
    PUBLISHED,
    REFUSE_WHOLE_RELEASE,
    REQUEST_BUDGET_EXHAUSTED,
    SOURCE_UNSETTLED,
    TIME_BUDGET_EXHAUSTED,
    DirectWriterContract,
)
from agri_data_service.pipeline.direct.climate.adapter import (
    CLIMATE_DIRECT_KIND,
    DirectClimateFieldAdapter,
    DirectClimateFieldError,
    refuse_immutable_day,
)
from agri_data_service.pipeline.direct.climate.products import (
    CLIMATE_DEFAULT_TIME_BUDGET_SECONDS,
    CLIMATE_DISTINCT_PUBLICATION_CLOCKS,
    CLIMATE_PRODUCT_IDS,
    products_for,
)
from agri_data_service.pipeline.direct.climate.source import (
    ClimateSourceCache,
    ClimateTimeBudgetExhaustedError,
    fetch_climate_day,
)
from agri_data_service.pipeline.direct.climate.support import NASA_POWER_SUPPORT_CELL_COUNT, load_nasa_power_support
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
from agri_data_service.pipeline.parquet.source_checkpoint import SourceResponseCheckpoints
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.paths import PartitionDayStatus
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.direct.climate.products import ClimateFieldProduct
    from agri_data_service.pipeline.direct.climate.support import NasaPowerSupport
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage

CLIMATE_DIRECT_ALL_TIERS: Final[tuple[ZoomTier, ...]] = (LANE_BASE_ZOOM_TIER, *DERIVED_ZOOM_TIERS)
CLIMATE_DIRECT_RUN_ID_PREFIX: Final = "climate-nasa-power-forward:"
CLIMATE_DEFAULT_MAX_DAYS: Final = 1
CLIMATE_MAX_DAYS: Final = 5
CLIMATE_MAX_TIME_BUDGET_SECONDS: Final = 3_000.0
CLIMATE_DEFAULT_RETRY_ATTEMPTS: Final = 4
CLIMATE_MAX_RETRY_ATTEMPTS: Final = 10
CLIMATE_DEFAULT_RETRY_BASE_SECONDS: Final = 5.0
CLIMATE_MAX_RETRY_BASE_SECONDS: Final = 60.0
CLIMATE_DEFAULT_RETRY_MAX_SECONDS: Final = 60.0
CLIMATE_MAX_RETRY_MAX_SECONDS: Final = 300.0
CLIMATE_DEFAULT_CONTENTION_TIMEOUT_SECONDS: Final = 300.0
CLIMATE_MAX_CONTENTION_TIMEOUT_SECONDS: Final = 3_600.0
CLIMATE_STATEMENT_TIMEOUT_SECONDS: Final = 120
CLIMATE_MIN_DELAY_SECONDS: Final = 0.1
#: How far back one turn is willing to look for an unfilled day before reporting a backlog. The
#: whole owed window is bounded by the history floor, but a single turn must stay bounded too.
CLIMATE_BACKLOG_SCAN_DAYS: Final = 400
#: How far back a turn re-examines a day it has ALREADY settled, in either of two shapes. An ABSENT
#: day: POWER revises a fill-value day into real values once its inputs land, and
#: `adapter._retract_disproven_absence` is the only thing that undoes such a marker -- it runs only on
#: a day the walk selects, so an absence the walk skipped forever was permanent whatever the source
#: did next. A PARTIAL day: a completed day whose base marker counts fewer rows than the 397 support
#: cells, because some cells still answered a fill when it was written (`_partial_days_in_recheck_window`).
#: The all-cell absence predicate never sees such a day, and a `data` rung is never re-selected on its
#: own, so without this the missing cells were a permanent silent hole at every lag. Rechecks are
#: queued BEHIND real gaps, so they can never starve a day that has no data at all -- which also
#: means a product with a standing backlog rechecks nothing until that backlog drains; see
#: `pipeline/direct/AGENTS.md`, "A governed absence is re-examined, or it is permanent".
CLIMATE_ABSENCE_RECHECK_DAYS: Final = 14
#: The one outcome a bounded turn reports instead of failing when its wall clock runs out.
CLIMATE_TIME_BUDGET_OUTCOME: Final = TIME_BUDGET_EXHAUSTED
#: The outcome an all-fill day reports when nothing proves the release has moved past it. Not a
#: failure: the day is simply not settled yet, and the next turn asks again.
CLIMATE_SOURCE_UNSETTLED_OUTCOME: Final = SOURCE_UNSETTLED
#: The one outcome a bounded turn reports instead of fetching past its per-turn request budget.
CLIMATE_REQUEST_BUDGET_OUTCOME: Final = REQUEST_BUDGET_EXHAUSTED
#: The two day outcomes that mean the writer settled the day: values written, or an absence governed
#: with a proof. Every other word `_publish_locked_day` can return is a day left owed.
CLIMATE_DAY_WROTE_OUTCOMES: Final[frozenset[str]] = frozenset({"written", "absent"})
#: How many `source_unsettled` days a turn may step PAST, on to the next older owed day, before it
#: stops. The newest owed day is the frontier: an all-fill answer there can never be governed as
#: absent (nothing later is published to mirror against), so it is refused every turn until POWER
#: publishes it. With `--max-days` 1 and a newest-first backlog, a turn that stopped at that refusal
#: selected the SAME day every hour and the days beneath it never drained -- the 2026-09-18 13:40Z
#: production turn (`shortwave-radiation`, `settled_through` 2026-09-12, `backlog_days` 101) is the
#: measured shape. ONE skip covers an edge up to lag+1 (F unsettled, F-1 written, one day drained
#: per turn as a settled frontier would). It does NOT cover deeper jitter: at edge >= lag+2 the turn
#: asks F (skip) then F-1 (slot), both unsettled, writes nothing, and re-asks the same two days next
#: hour at 794 requests instead of 397. A larger constant cannot cure that -- the 794 budget is
#: exactly two 397-cell fan-outs, so `can_afford` refuses a third whatever this says. The cure for
#: deeper jitter is a CROSS-TURN skip (persist the refused frontier so the next turn starts a day
#: deeper); named as the follow-up in `climate/AGENTS.md`, not implemented here. A 429 deferral is
#: NOT a frontier (`_steps_past_unsettled_frontier`), so no skip re-asks a provider that throttled us.
CLIMATE_UNSETTLED_FRONTIER_SKIPS: Final = 1

#: What this writer promises about its own failure policy, CLI surface and reported words; see
#: `pipeline/direct/__init__.py` for the axes and `tests/direct/test_direct_writer_contract.py` for
#: the table all eleven are read as.
WRITER_CONTRACT: Final = DirectWriterContract(
    slug="climate",
    identity_defect=REFUSE_WHOLE_RELEASE,
    geometry_defect=NO_SUCH_DEFECT,
    unconfigured_bbox=NOT_BBOX_BOUNDED,
    turn_outcomes=LANE_DAY_OUTCOMES
    | {
        TIME_BUDGET_EXHAUSTED,
        REQUEST_BUDGET_EXHAUSTED,
        SOURCE_UNSETTLED,
        NOT_YET_SETTLED,
        IDEMPOTENT_NOOP,
        PUBLISHED,
    },
    flags_absent_on_purpose={
        "--bbox": "the support is the PINNED 397-cell NASA POWER lattice read from `agri.spatial_cell` "
        "(support.py), not an envelope. A bbox could only ever cut cells OUT of a fixed support, and a "
        "day written against a different cell count is not comparable with the history it extends -- "
        "`NASA_POWER_SUPPORT_CELL_COUNT` refuses one.",
        "--max-records": "the record count per day is the support cell count times the requested "
        "parameters, both fixed; the bound that matters is requests, and `--time-budget-seconds` plus "
        "CLIMATE_REQUEST_BUDGET_OUTCOME already spend it.",
        "--max-records-per-day": "same reason as `--max-records`, and a per-day spelling would imply a "
        "day whose size this writer can choose. It cannot: a short day is a refusal, not a truncation.",
    },
    policy_basis="A cell-keyed value plane has no geometry column, so `geometry_defect` cannot arise "
    "here rather than being refused. A POWER point that does not land on a support centroid IS an "
    "identity defect and is refused whole (`rows.py:46`) rather than counted, because matching is "
    "equality on the quantised centroid key: a counted-and-dropped cell would silently shrink the "
    "support, and shrinking the support is the one change that makes a day incomparable.",
)
MONTHS_PER_YEAR: Final = 12


@dataclass(frozen=True, slots=True)
class ClimateForwardConfig:
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
    #: Which recheck an idle turn takes first; `None` derives it from the wall clock (hours since the
    #: epoch), so successive hourly turns walk the recheck list round-robin. See `_pending_days`.
    recheck_rotation: int | None = None

    @property
    def request_budget(self) -> int:
        """Cap this turn's upstream point requests. See `pipeline/direct/AGENTS.md`, "The request budget"."""
        return NASA_POWER_SUPPORT_CELL_COUNT * self.max_days * CLIMATE_DISTINCT_PUBLICATION_CLOCKS


class ClimateForwardConfigError(ValueError):
    """Raised when a turn is asked for an unbounded or self-contradictory shape."""


def emit(payload: Mapping[str, object]) -> None:
    """Write one stable JSON progress record to stderr, leaving stdout for the terminal report."""
    print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)


def settled_through(product: ClimateFieldProduct, *, today: date) -> date:
    """Return the newest day this product may be held to, given its own publication lag."""
    return today - timedelta(days=product.publication_lag_days)


async def run_climate_forward(config: ClimateForwardConfig) -> dict[str, object]:
    """Publish the newest unfilled settled day of every selected product, newest first."""
    _validate_config(config)
    run_id = config.run_id or f"{CLIMATE_DIRECT_RUN_ID_PREFIX}{uuid.uuid4()}"
    today = config.today or datetime.now(UTC).date()
    products = products_for(config.product_id)
    store = ObjectStore.from_settings()
    availability_storage = BotoAvailabilityStorage.from_settings()
    cache = ClimateSourceCache(
        request_budget=config.request_budget, checkpoints=SourceResponseCheckpoints(availability_storage)
    )
    deadline = time.monotonic() + config.time_budget_seconds
    results: list[dict[str, object]] = []
    # ONE TALLY FOR THE WHOLE RUN. Without it every availability verdict lands only inside a day's
    # detail string, which is the failure `AvailabilityExtensionTally`'s own docstring forbids: a
    # `ladder_incomplete` or `retry_claim_failed` day is permanently outside the index, and a run
    # that states it in prose reports that loss as a green tick.
    availability = AvailabilityExtensionTally()

    loader_database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_session(loader_database_url) as session:
        support = await load_nasa_power_support(session)
        await session.rollback()
        for product in products:
            if time.monotonic() >= deadline:
                results.append(_skipped(product, today=today, outcome=CLIMATE_TIME_BUDGET_OUTCOME))
                continue
            results.append(
                await _publish_product(
                    session,
                    store,
                    product,
                    support=support,
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
    emit({"event": "climate_forward_complete", **report})
    return report


async def _publish_product(  # noqa: PLR0913 - the store, product, support, cache, clock and budget are distinct
    session: AsyncSession,
    store: ObjectStore,
    product: ClimateFieldProduct,
    *,
    support: NasaPowerSupport,
    cache: ClimateSourceCache,
    today: date,
    run_id: str,
    config: ClimateForwardConfig,
    deadline: float,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
) -> dict[str, object]:
    """Take one product's turn: census its owed window, then publish at most `max_days` days.

    A day the source refuses as unsettled does not count against `max_days`; the walk steps past it
    to the next older owed day, at most `CLIMATE_UNSETTLED_FRONTIER_SKIPS` times per turn, and names
    every such day in `unsettled_frontier_days`.

    THE OWED LEDGER IS DRAINED FIRST, once per product per run. Nothing else retries these claims:
    `retry_pending_availability` is otherwise called only from `run_gap_fill`, and activating
    `climate-nasa-power-direct-forward` deactivates the eight generic lanes through `conflicts_with`
    -- so a climate day whose pointer read failed writes a claim that no driver in this service would
    ever come back for, and the base-tier census never revisits a completed day.
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
    first_day = max(product.history_floor, ceiling - timedelta(days=CLIMATE_BACKLOG_SCAN_DAYS - 1))
    statuses = await asyncio.to_thread(_tier_status_window, store, product, first_day, ceiling)
    partial_days = await asyncio.to_thread(_partial_days_in_recheck_window, store, product, statuses)
    rotation = config.recheck_rotation if config.recheck_rotation is not None else _clock_recheck_rotation()
    backlog = _pending_days(product, statuses, partial_days=partial_days, recheck_rotation=rotation)
    published: list[dict[str, object]] = []
    # THE FRONTIER IS STEPPED PAST, NOT RETAKEN. `max_days` counts the days that took a slot; a day
    # the source refused as unsettled is stepped past instead, at most CLIMATE_UNSETTLED_FRONTIER_SKIPS
    # times per turn, so the next older owed day is fetched in the same turn -- under the same
    # `can_afford` and deadline checks every day passes.
    unsettled_frontier_days: list[date] = []
    for day in backlog:
        if len(published) - len(unsettled_frontier_days) >= config.max_days:
            break
        if time.monotonic() >= deadline:
            published.append(_stopped_day(day, outcome=CLIMATE_TIME_BUDGET_OUTCOME, detail="before the day started"))
            break
        await cache.restore(support, day, deadline=deadline)
        if time.monotonic() >= deadline:
            published.append(_stopped_day(day, outcome=CLIMATE_TIME_BUDGET_OUTCOME, detail="after checkpoint restore"))
            break
        if not cache.can_afford(support, day):
            published.append(
                _stopped_day(
                    day,
                    outcome=CLIMATE_REQUEST_BUDGET_OUTCOME,
                    detail=f"{cache.remaining_requests} of {cache.request_budget} request(s) left",
                )
            )
            break
        result = await _publish_day_with_retries(
            session,
            store,
            product,
            day,
            support=support,
            cache=cache,
            today=today,
            run_id=run_id,
            config=config,
            deadline=deadline,
            availability_storage=availability_storage,
            availability=availability,
            mirrored_past=_mirrored_past_day(statuses, day),
            existing_row_count=partial_days.get(day),
        )
        published.append(result)
        if _steps_past_unsettled_frontier(result, cache=cache, skips_taken=len(unsettled_frontier_days)):
            unsettled_frontier_days.append(day)
    return {
        "layer": product.stream,
        "product": product.product_id,
        "outcome": _product_outcome(backlog, published),
        "source_unsettled_days": sum(1 for day in published if day["outcome"] == CLIMATE_SOURCE_UNSETTLED_OUTCOME),
        "unsettled_frontier_days": [day.isoformat() for day in unsettled_frontier_days],
        "history_floor": product.history_floor.isoformat(),
        "settled_through": ceiling.isoformat(),
        "publication_lag_days": product.publication_lag_days,
        "scan_first_day": first_day.isoformat(),
        "backlog_days": len(backlog),
        "partial_day_rechecks": len(partial_days),
        "availability_retried_days": retried,
        "days": published,
    }


def _product_outcome(backlog: Sequence[date], published: Sequence[Mapping[str, object]]) -> str:
    """Report what the turn actually did to this product, never `published` for a day it did not write.

    THE MASK THIS REMOVES: a turn whose only selected day came back `source_unsettled` is a turn that
    wrote nothing, and reporting it as `published` is how a lane ticks green for months while its
    edge stands still -- exactly the shape of the 107-day shortwave stall. The two production turn
    reports of 2026-09-15 and 2026-09-16 (`.omc/research/runbook-20260915-shortwave/prod-logs/`)
    each read `"outcome": "published"` for shortwave radiation beside ONE day whose own outcome was
    `source_unsettled` with detail "NASA POWER answered 429"; nothing above the day level said so.
    A too-small lag manufactures wrong absences loudly; a too-large one, a source that stops
    publishing, or a quota refusal on every turn is silent unless the turn says so. The word is the
    FIRST day's own outcome, so `source_unsettled`, `time_budget_exhausted` and
    `request_budget_exhausted` each reach the run report under their own name.

    THE FIRST DAY MAY BE A STEPPED-PAST FRONTIER. When the walk skipped an unsettled frontier
    (`CLIMATE_UNSETTLED_FRONTIER_SKIPS`) the day that decided the turn is the one AFTER it: a written
    older day makes the turn `published`, a budget stop after the frontier is the turn's honest word
    (the frontier is still counted in `source_unsettled_days` and named in `unsettled_frontier_days`),
    and only a turn whose every day was unsettled reads `source_unsettled`.
    """
    # `max_days` is at least 1, so an empty `published` beside a non-empty backlog cannot arise; it is
    # still not a publication, and naming it a no-op keeps the word honest either way.
    if not backlog or not published:
        return IDEMPOTENT_NOOP
    if any(day["outcome"] in CLIMATE_DAY_WROTE_OUTCOMES for day in published):
        return PUBLISHED
    return next(
        (str(day["outcome"]) for day in published if day["outcome"] != CLIMATE_SOURCE_UNSETTLED_OUTCOME),
        CLIMATE_SOURCE_UNSETTLED_OUTCOME,
    )


def _steps_past_unsettled_frontier(
    result: Mapping[str, object], *, cache: ClimateSourceCache, skips_taken: int
) -> bool:
    """True when this day was refused as unsettled BY THE SOURCE and the turn may still step past one.

    A 429 deferral reports the same word (`adapter.unsettled_refusal` holds a `ClimateProviderDeferredError`)
    but is not a frontier: the provider throttled the turn and `cache.deferred_refusal` now stops every
    queued request before it starts, so stepping to an older day would only re-ask a closed provider.
    """
    # RELIES ON `can_afford` RUNNING IMMEDIATELY BEFORE THE FETCH, over a cache no other product touches
    # meanwhile: `source.fill_cell_day_cache` raises `ClimateProviderDeferredError` for a budget
    # shortfall WITHOUT setting `deferred_refusal`, so if products ever fetch concurrently that raise
    # becomes reachable and a budget shortfall would read here as a frontier.
    return (
        result["outcome"] == CLIMATE_SOURCE_UNSETTLED_OUTCOME
        and cache.deferred_refusal is None
        and skips_taken < CLIMATE_UNSETTLED_FRONTIER_SKIPS
    )


async def _retry_owed_availability(  # noqa: PLR0913 - one coordinate of the product's turn per arg
    session: AsyncSession,
    store: ObjectStore,
    product: ClimateFieldProduct,
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
                "event": "climate_forward_availability_retry_failed",
                "layer": product.stream,
                "detail": f"{type(error).__name__}: {error}",
            }
        )
        return 0
    for outcome in outcomes:
        availability.record(outcome)
        emit(
            {
                "event": "climate_forward_availability_retry",
                "layer": product.stream,
                "state": outcome.state,
                "detail": outcome.note,
            }
        )
    return len(outcomes)


def _skipped(product: ClimateFieldProduct, *, today: date, outcome: str) -> dict[str, object]:
    """Report a product that took no turn, naming why rather than reporting an empty success."""
    return {
        "layer": product.stream,
        "product": product.product_id,
        "outcome": outcome,
        "source_unsettled_days": 0,
        "unsettled_frontier_days": [],
        "history_floor": product.history_floor.isoformat(),
        "settled_through": settled_through(product, today=today).isoformat(),
        "publication_lag_days": product.publication_lag_days,
        "partial_day_rechecks": 0,
        "days": [],
    }


def _stopped_day(day: date, *, outcome: str, attempts: int = 0, detail: str | None = None) -> dict[str, object]:
    """Report one day a bound stopped before it published, without claiming a source receipt it has none of."""
    return {
        "day": day.isoformat(),
        "outcome": outcome,
        "attempts": attempts,
        "base_rows": 0,
        "fill_value_cells": 0,
        "source_receipt": None,
        "parts": 0,
        "rows_across_write": 0,
        "written_bytes": 0,
        "detail": detail,
    }


async def _publish_day_with_retries(  # noqa: PLR0913 - one lane-day coordinate per argument
    session: AsyncSession,
    store: ObjectStore,
    product: ClimateFieldProduct,
    day: date,
    *,
    support: NasaPowerSupport,
    cache: ClimateSourceCache,
    today: date,
    run_id: str,
    config: ClimateForwardConfig,
    deadline: float,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
    mirrored_past: date | None,
    existing_row_count: int | None = None,
) -> dict[str, object]:
    """Acquire the lane-day lock once, then refetch and republish under it for every bounded attempt."""
    refuse_immutable_day(product, day)
    lane = LANE_REGISTRY[product.stream]
    contention_deadline = min(time.monotonic() + config.contention_timeout_seconds, deadline)
    while True:
        if time.monotonic() >= deadline:
            return _stopped_day(
                day,
                outcome=CLIMATE_TIME_BUDGET_OUTCOME,
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
                    cache=cache,
                    today=today,
                    run_id=run_id,
                    config=config,
                    deadline=deadline,
                    availability_storage=availability_storage,
                    availability=availability,
                    mirrored_past=mirrored_past,
                    existing_row_count=existing_row_count,
                )
        await session.rollback()
        remaining = contention_deadline - time.monotonic()
        if remaining <= 0:
            if time.monotonic() >= deadline:
                return _stopped_day(
                    day,
                    outcome=CLIMATE_TIME_BUDGET_OUTCOME,
                    detail="the turn's time budget ran out while waiting for the lane-day lock",
                )
            raise DirectClimateFieldError(
                f"lane-day contention for {product.stream} {day.isoformat()} exceeded "
                f"{config.contention_timeout_seconds:g}s"
            )
        delay = min(remaining, _retry_delay(1, config=config))
        emit(
            {
                "event": "climate_forward_contention",
                "run_id": run_id,
                "layer": product.stream,
                "day": day.isoformat(),
                "retry_in_seconds": round(delay, 3),
            }
        )
        await asyncio.sleep(delay)


async def _publish_locked_day(  # noqa: PLR0913, PLR0911 - one lane-day coordinate per argument; one return per distinct day outcome
    session: AsyncSession,
    store: ObjectStore,
    product: ClimateFieldProduct,
    day: date,
    *,
    support: NasaPowerSupport,
    cache: ClimateSourceCache,
    today: date,
    run_id: str,
    config: ClimateForwardConfig,
    deadline: float,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
    mirrored_past: date | None,
    existing_row_count: int | None = None,
) -> dict[str, object]:
    """Refetch before every write attempt while one advisory lock stays held, then prove all four rungs."""
    lane = LANE_REGISTRY[product.stream]
    for attempt in range(1, config.retry_attempts + 1):
        if time.monotonic() >= deadline:
            return _stopped_day(
                day,
                outcome=CLIMATE_TIME_BUDGET_OUTCOME,
                attempts=attempt - 1,
                detail="the turn's time budget ran out before this attempt began",
            )
        adapter = DirectClimateFieldAdapter(
            product=product,
            fetch_source=lambda: fetch_climate_day(product, day=day, support=support, cache=cache, deadline=deadline),
            mirrored_past_proof=lambda: _mirrored_past_proof(product, day=day, mirrored_past=mirrored_past),
            existing_row_count=existing_row_count,
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
                statement_timeout_seconds=CLIMATE_STATEMENT_TIMEOUT_SECONDS,
                availability_storage=availability_storage,
                availability_tally=availability,
            )
            await session.rollback()
        except ClimateTimeBudgetExhaustedError as stop:
            with suppress(Exception):
                await session.rollback()
            return _stopped_day(day, outcome=CLIMATE_TIME_BUDGET_OUTCOME, attempts=attempt, detail=str(stop))
        except Exception as error:
            with suppress(Exception):
                await session.rollback()
            outcome, parts, rows, written_bytes = "raised", 0, 0, 0
            detail = f"{type(error).__name__}: {error}"
        if adapter.unchanged_partial is not None:
            # A SHORT DAY THAT HAS NOT GROWN IS LEFT ALONE. Nothing was cleared and nothing was
            # written; the day stays served and stays a recheck. Reported under the no-op name so a
            # turn that only met such days does not read `published`.
            return _day_result(
                product,
                day,
                adapter=adapter,
                outcome=IDEMPOTENT_NOOP,
                parts=0,
                rows=0,
                written_bytes=0,
                attempts=attempt,
                detail=str(adapter.unchanged_partial),
            )
        if adapter.unsettled_refusal is not None:
            # NOT A FAILURE AND NOT A RETRY. POWER has not published this day, so refetching it inside
            # the same turn asks the same question of the same release; the next turn is the soonest
            # the answer can differ. Reported as its own outcome so a run stays green.
            return _stopped_day(
                day,
                outcome=CLIMATE_SOURCE_UNSETTLED_OUTCOME,
                attempts=attempt,
                detail=str(adapter.unsettled_refusal),
            )
        if outcome == "blocked":
            raise DirectClimateFieldError(detail or f"{product.stream} {day.isoformat()} is blocked")
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
            raise DirectClimateFieldError(
                f"{product.stream} {day.isoformat()} did not publish a complete four-rung ladder after "
                f"{attempt} attempt(s): outcome={outcome}, detail={detail}, verification={verification_detail}"
            )
        delay = min(_retry_delay(attempt, config=config), max(0.0, deadline - time.monotonic()))
        if delay <= 0:
            return _stopped_day(
                day,
                outcome=CLIMATE_TIME_BUDGET_OUTCOME,
                attempts=attempt,
                detail=f"outcome={outcome}, detail={detail}, verification={verification_detail}",
            )
        emit(
            {
                "event": "climate_forward_retry",
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
    raise AssertionError("bounded climate publish attempts exhausted")


async def _verify_written_ladder(store: ObjectStore, product: ClimateFieldProduct, day: date) -> str | None:
    """Return why the day's four rungs do not all read `data` yet, or `None` when every one of them does."""
    try:
        tier_statuses = await asyncio.to_thread(_tier_status_day, store, product, day)
    except Exception as error:
        return f"{type(error).__name__}: {error}"
    if all(status == "data" for status in tier_statuses.values()):
        return None
    return f"tier statuses after the write were {tier_statuses}"


def _day_result(  # noqa: PLR0913 - the adapter evidence and the finalizer counters are separate facts
    product: ClimateFieldProduct,
    day: date,
    *,
    adapter: DirectClimateFieldAdapter,
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
        raise DirectClimateFieldError(
            f"{product.stream} {day.isoformat()} returned {outcome} without a completed locked source fetch"
        )
    return {
        "day": day.isoformat(),
        "outcome": outcome,
        "attempts": attempts,
        "base_rows": len(source.values),
        "fill_value_cells": source.fill_value_cells,
        "source_receipt": source.receipt.as_event(),
        "parts": parts,
        "rows_across_write": rows,
        "written_bytes": written_bytes,
        "detail": detail,
    }


def _tier_status_window(
    store: ObjectStore,
    product: ClimateFieldProduct,
    first_day: date,
    last_day: date,
) -> dict[ZoomTier, dict[date, PartitionDayStatus]]:
    """List every rung of one product across the owed window, one month prefix at a time."""
    keys_by_tier: dict[ZoomTier, list[str]] = {tier: [] for tier in CLIMATE_DIRECT_ALL_TIERS}
    cursor = date(first_day.year, first_day.month, 1)
    while cursor <= last_day:
        for tier in CLIMATE_DIRECT_ALL_TIERS:
            keys_by_tier[tier].extend(
                store.list_partition_keys(
                    product.stream, CLIMATE_DIRECT_KIND, tier, year=cursor.year, month=cursor.month
                )
            )
        cursor = date(
            cursor.year + (1 if cursor.month == MONTHS_PER_YEAR else 0),
            1 if cursor.month == MONTHS_PER_YEAR else cursor.month + 1,
            1,
        )
    return {
        tier: partition_day_statuses(
            layer=product.stream,
            kind=CLIMATE_DIRECT_KIND,
            zoom=tier,
            first_day=first_day,
            last_day=last_day,
            keys=keys,
        )
        for tier, keys in keys_by_tier.items()
    }


def _tier_status_day(
    store: ObjectStore,
    product: ClimateFieldProduct,
    day: date,
) -> dict[ZoomTier, PartitionDayStatus]:
    """Read the durable completion checkpoint of one product-day at every rung."""
    return {
        tier: partition_day_statuses(
            layer=product.stream,
            kind=CLIMATE_DIRECT_KIND,
            zoom=tier,
            first_day=day,
            last_day=day,
            keys=store.list_partition_keys(product.stream, CLIMATE_DIRECT_KIND, tier, year=day.year, month=day.month),
        )[day]
        for tier in CLIMATE_DIRECT_ALL_TIERS
    }


def _recheck_floor(days: Collection[date]) -> date:
    """Return the oldest day of the recheck window: the newest censused day minus the window, inclusive."""
    return max(days) - timedelta(days=CLIMATE_ABSENCE_RECHECK_DAYS - 1)


def _partial_days_in_recheck_window(
    store: ObjectStore,
    product: ClimateFieldProduct,
    statuses: Mapping[ZoomTier, Mapping[date, PartitionDayStatus]],
) -> dict[date, int]:
    """Return the completed days of the recheck window whose base marker counts fewer rows than support cells.

    Keyed to the marker's `row_count`, which the re-bind must strictly exceed before it writes.

    THE BASE COMPLETION MARKER IS THE DURABLE PER-DAY RECORD OF A SHORT DAY. `build_climate_day` drops
    a fill cell with no row and `_finalize_written_day` stamps `row_count` with what was written, so a
    day written while some cells still answered POWER's fill reads `row_count < 397` forever, with no
    new state to keep. Bounded: at most `CLIMATE_ABSENCE_RECHECK_DAYS` marker reads per product per
    turn, and only for days every rung already calls `data`.
    """
    base = statuses[LANE_BASE_ZOOM_TIER]
    if not base:
        return {}
    floor = _recheck_floor(base)
    partial: dict[date, int] = {}
    for day in base:
        if day < floor or any(statuses[tier][day] != "data" for tier in CLIMATE_DIRECT_ALL_TIERS):
            continue
        marker = store.read_completion_marker(product.stream, CLIMATE_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day)
        if marker is not None and marker.row_count < NASA_POWER_SUPPORT_CELL_COUNT:
            partial[day] = marker.row_count
    return partial


def _clock_recheck_rotation(now: datetime | None = None) -> int:
    """Hours since the epoch: consecutive for consecutive hourly turns, so the rechecks are walked round-robin."""
    stamped = now if now is not None else datetime.now(UTC)
    return int(stamped.timestamp()) // 3600


def _pending_days(
    product: ClimateFieldProduct,
    statuses: Mapping[ZoomTier, Mapping[date, PartitionDayStatus]],
    *,
    partial_days: Collection[date] = frozenset(),
    recheck_rotation: int = 0,
) -> tuple[date, ...]:
    """Return the owed days newest first, then the recent settled days this turn should re-examine.

    Two kinds of settled day are re-selected, both bounded to the newest `CLIMATE_ABSENCE_RECHECK_DAYS`
    and both queued behind every day that owes real work: a governed ABSENCE, because POWER revises a
    fill-value day once its inputs land, and a PARTIAL day named in `partial_days`, because a day
    written short of the support is otherwise `data` at every rung and never selected again.

    THE RECHECKS ARE ROUND-ROBIN, NOT NEWEST-FIRST. A turn takes `--max-days` (1) days, so an idle
    turn takes exactly one recheck; newest-first would take the same newest short day every hour
    while a cell trails, and a day behind it would never be re-examined again. Oldest-first alone
    starves the other way once the oldest is a standing no-op. The list is oldest-first and rotated
    by `recheck_rotation`, which the driver derives from the clock hour, so successive idle turns
    visit each recheck in turn. See `pipeline/direct/AGENTS.md`, "A governed absence is re-examined,
    or it is permanent".
    """
    days = tuple(statuses[CLIMATE_DIRECT_ALL_TIERS[0]])
    if not days:
        return ()
    recheck_floor = _recheck_floor(days)
    pending: list[date] = []
    rechecks: list[date] = []
    for day in reversed(days):
        rung = {tier: statuses[tier][day] for tier in CLIMATE_DIRECT_ALL_TIERS}
        if "conflict" in rung.values():
            raise DirectClimateFieldError(f"{product.stream} {day.isoformat()} has a data/absence conflict: {rung}")
        if rung[LANE_BASE_ZOOM_TIER] == "absent":
            if any(rung[tier] in {"data", "incomplete"} for tier in DERIVED_ZOOM_TIERS):
                raise DirectClimateFieldError(
                    f"{product.stream} {day.isoformat()} is absent at the base rung but carries derived parts: {rung}"
                )
            if day >= recheck_floor:
                rechecks.append(day)
            continue
        if any(status != "data" for status in rung.values()):
            pending.append(day)
        elif day in partial_days and day >= recheck_floor:
            rechecks.append(day)
    if not rechecks:
        return tuple(pending)
    oldest_first = rechecks[::-1]
    offset = recheck_rotation % len(oldest_first)
    return (*pending, *oldest_first[offset:], *oldest_first[:offset])


def _mirrored_past_day(
    statuses: Mapping[ZoomTier, Mapping[date, PartitionDayStatus]],
    day: date,
) -> date | None:
    """Return the earliest LATER settled day this product already publishes with values, or `None`.

    THE SIMPLEST HONEST PROOF that POWER has published past `day`: a day after it answered with rows,
    so an all-fill answer here is the source's verdict rather than its backlog. Read out of the
    census listing the turn already paid for -- no extra request, and no upstream call.
    """
    base = statuses[LANE_BASE_ZOOM_TIER]
    return next((later for later in sorted(base) if later > day and base[later] == "data"), None)


def _mirrored_past_proof(product: ClimateFieldProduct, *, day: date, mirrored_past: date | None) -> str | None:
    """Render the sentence a governed absence carries as its justification, or `None` when it has none."""
    if mirrored_past is None:
        return None
    return (
        f"{product.stream} is published with values for {mirrored_past.isoformat()}, which is later than "
        f"{day.isoformat()}, so the NASA POWER release has moved past this day and its all-fill answer is settled"
    )


def _retry_delay(attempt: int, *, config: ClimateForwardConfig) -> float:
    """Return a jittered, capped exponential wait so concurrent turns do not resynchronise."""
    ceiling = min(config.retry_max_seconds, config.retry_base_seconds * (2 ** max(0, attempt - 1)))
    return float(ceiling + random.uniform(0.0, min(1.0, ceiling / 4)))


def _validate_config(config: ClimateForwardConfig) -> None:
    """Fail closed on every process-bound knob before a socket or a session is opened."""
    if config.product_id != "all" and config.product_id not in CLIMATE_PRODUCT_IDS:
        raise ClimateForwardConfigError(
            f"--product must be one of {', '.join(CLIMATE_PRODUCT_IDS)} or all, got {config.product_id!r}"
        )
    if not 1 <= config.max_days <= CLIMATE_MAX_DAYS:
        raise ClimateForwardConfigError(f"--max-days must be between 1 and {CLIMATE_MAX_DAYS}")
    if not 1 <= config.retry_attempts <= CLIMATE_MAX_RETRY_ATTEMPTS:
        raise ClimateForwardConfigError(f"--retry-attempts must be between 1 and {CLIMATE_MAX_RETRY_ATTEMPTS}")
    bounds = {
        "--time-budget-seconds": (config.time_budget_seconds, CLIMATE_MAX_TIME_BUDGET_SECONDS),
        "--retry-base-seconds": (config.retry_base_seconds, CLIMATE_MAX_RETRY_BASE_SECONDS),
        "--retry-max-seconds": (config.retry_max_seconds, CLIMATE_MAX_RETRY_MAX_SECONDS),
        "--contention-timeout-seconds": (config.contention_timeout_seconds, CLIMATE_MAX_CONTENTION_TIMEOUT_SECONDS),
    }
    for name, (value, maximum) in bounds.items():
        if not math.isfinite(value) or not CLIMATE_MIN_DELAY_SECONDS <= value <= maximum:
            raise ClimateForwardConfigError(
                f"{name} must be finite and between {CLIMATE_MIN_DELAY_SECONDS:g} and {maximum:g}, got {value!r}"
            )
    if config.retry_max_seconds < config.retry_base_seconds:
        raise ClimateForwardConfigError("--retry-max-seconds must be at least --retry-base-seconds")


def parser() -> argparse.ArgumentParser:
    """Build the bounded, forward-only climate lane operator.

    Every knob this parser does NOT expose is named in `WRITER_CONTRACT.flags_absent_on_purpose`
    with its reason, and `tests/direct/test_direct_writer_contract.py` fails if one of them quietly
    appears here or if a knob vanishes without an entry. `--product` IS exposed, unlike nine of the
    eleven writers': this lane fans one POWER response out to eleven streams, so naming one is a
    real operator choice rather than a no-op.
    """
    built = argparse.ArgumentParser(description=__doc__)
    built.add_argument("--product", default="all", choices=[*CLIMATE_PRODUCT_IDS, "all"])
    built.add_argument("--max-days", type=int, default=CLIMATE_DEFAULT_MAX_DAYS)
    built.add_argument("--time-budget-seconds", type=float, default=CLIMATE_DEFAULT_TIME_BUDGET_SECONDS)
    built.add_argument("--run-id", default=None)
    built.add_argument("--retry-attempts", type=int, default=CLIMATE_DEFAULT_RETRY_ATTEMPTS)
    built.add_argument("--retry-base-seconds", type=float, default=CLIMATE_DEFAULT_RETRY_BASE_SECONDS)
    built.add_argument("--retry-max-seconds", type=float, default=CLIMATE_DEFAULT_RETRY_MAX_SECONDS)
    built.add_argument("--contention-timeout-seconds", type=float, default=CLIMATE_DEFAULT_CONTENTION_TIMEOUT_SECONDS)
    return built


def parse_args(argv: Sequence[str] | None = None) -> ClimateForwardConfig:
    """Validate every operator input at the boundary and hand back one bounded turn."""
    built = parser()
    arguments = built.parse_args(argv)
    config = ClimateForwardConfig(
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
    except ClimateForwardConfigError as error:
        built.error(str(error))
    return config


async def main(argv: Sequence[str] | None = None) -> int:
    """Run one bounded turn and emit exactly one terminal report on stdout."""
    config = parse_args(argv)
    try:
        report = await run_climate_forward(config)
    except Exception as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


__all__ = [
    "CLIMATE_ABSENCE_RECHECK_DAYS",
    "CLIMATE_BACKLOG_SCAN_DAYS",
    "CLIMATE_DAY_WROTE_OUTCOMES",
    "CLIMATE_DEFAULT_TIME_BUDGET_SECONDS",
    "CLIMATE_DIRECT_ALL_TIERS",
    "CLIMATE_MAX_DAYS",
    "CLIMATE_REQUEST_BUDGET_OUTCOME",
    "CLIMATE_SOURCE_UNSETTLED_OUTCOME",
    "CLIMATE_TIME_BUDGET_OUTCOME",
    "CLIMATE_UNSETTLED_FRONTIER_SKIPS",
    "ClimateForwardConfig",
    "ClimateForwardConfigError",
    "main",
    "parse_args",
    "parser",
    "run_climate_forward",
    "settled_through",
]
