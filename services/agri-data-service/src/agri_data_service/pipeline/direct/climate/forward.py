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
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.gap_fill import (
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
#: The one outcome a bounded turn reports instead of failing when its wall clock runs out.
CLIMATE_TIME_BUDGET_OUTCOME: Final = "time_budget_exhausted"
#: The one outcome a bounded turn reports instead of fetching past its per-turn request budget.
CLIMATE_REQUEST_BUDGET_OUTCOME: Final = "request_budget_exhausted"
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
    cache = ClimateSourceCache(request_budget=config.request_budget)
    deadline = time.monotonic() + config.time_budget_seconds
    results: list[dict[str, object]] = []

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
) -> dict[str, object]:
    """Take one product's turn: census its owed window, then publish at most `max_days` days."""
    ceiling = settled_through(product, today=today)
    if ceiling < product.history_floor:
        return _skipped(product, today=today, outcome="not_yet_settled")
    first_day = max(product.history_floor, ceiling - timedelta(days=CLIMATE_BACKLOG_SCAN_DAYS - 1))
    statuses = await asyncio.to_thread(_tier_status_window, store, product, first_day, ceiling)
    backlog = _pending_days(product, statuses)
    selected = backlog[: config.max_days]
    published: list[dict[str, object]] = []
    for day in selected:
        if time.monotonic() >= deadline:
            published.append(_stopped_day(day, outcome=CLIMATE_TIME_BUDGET_OUTCOME, detail="before the day started"))
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
        published.append(
            await _publish_day_with_retries(
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
        "days": published,
    }


def _skipped(product: ClimateFieldProduct, *, today: date, outcome: str) -> dict[str, object]:
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


async def _publish_locked_day(  # noqa: PLR0913 - one lane-day coordinate per argument
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
        verified = False
        verification_detail: str | None = None
        if outcome == "written":
            try:
                tier_statuses = await asyncio.to_thread(_tier_status_day, store, product, day)
                verified = all(status == "data" for status in tier_statuses.values())
                if not verified:
                    verification_detail = f"tier statuses after the write were {tier_statuses}"
            except Exception as error:
                verification_detail = f"{type(error).__name__}: {error}"
        if outcome == "written" and verified:
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


def _pending_days(
    product: ClimateFieldProduct,
    statuses: Mapping[ZoomTier, Mapping[date, PartitionDayStatus]],
) -> tuple[date, ...]:
    """Return the owed days newest first: a complete day at every rung is an idempotent no-op."""
    days = tuple(statuses[CLIMATE_DIRECT_ALL_TIERS[0]])
    pending: list[date] = []
    for day in reversed(days):
        rung = {tier: statuses[tier][day] for tier in CLIMATE_DIRECT_ALL_TIERS}
        if "conflict" in rung.values():
            raise DirectClimateFieldError(f"{product.stream} {day.isoformat()} has a data/absence conflict: {rung}")
        if rung[LANE_BASE_ZOOM_TIER] == "absent":
            if any(rung[tier] in {"data", "incomplete"} for tier in DERIVED_ZOOM_TIERS):
                raise DirectClimateFieldError(
                    f"{product.stream} {day.isoformat()} is absent at the base rung but carries derived parts: {rung}"
                )
            continue
        if any(status != "data" for status in rung.values()):
            pending.append(day)
    return tuple(pending)


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
    """Build the bounded, forward-only climate lane operator."""
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
    "CLIMATE_BACKLOG_SCAN_DAYS",
    "CLIMATE_DEFAULT_TIME_BUDGET_SECONDS",
    "CLIMATE_DIRECT_ALL_TIERS",
    "CLIMATE_MAX_DAYS",
    "CLIMATE_REQUEST_BUDGET_OUTCOME",
    "CLIMATE_TIME_BUDGET_OUTCOME",
    "ClimateForwardConfig",
    "ClimateForwardConfigError",
    "main",
    "parse_args",
    "parser",
    "run_climate_forward",
    "settled_through",
]
