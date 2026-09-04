"""Publish the newest settled vegetation NDVI days directly, one day per lane-day lock.

ONE STREAM, so this driver is `pipeline/direct/soil/forward.py` flattened: no `--product` fan-out,
no per-product request-budget cache. The lock, retry, mirrored-past-proof, absence-recheck and
ladder-verification shapes are the same idiom as `soil`/`climate`; see `pipeline/direct/AGENTS.md`.
"""

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
from agri_data_service.pipeline.direct.vegetation.adapter import (
    DirectVegetationAdapter,
    DirectVegetationError,
    refuse_pre_ownership_day,
)
from agri_data_service.pipeline.direct.vegetation.products import (
    VEGETATION_DIRECT_KIND,
    VEGETATION_DIRECT_WRITER_START_DAY,
)
from agri_data_service.pipeline.direct.vegetation.source import (
    VegetationTimeBudgetExhaustedError,
    fetch_vegetation_day,
)
from agri_data_service.pipeline.direct.vegetation.support import load_vegetation_support
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
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_STREAM

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.paths import PartitionDayStatus
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.direct.vegetation.support import VegetationSupport
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration

VEGETATION_DIRECT_ALL_TIERS: Final[tuple[ZoomTier, ...]] = (LANE_BASE_ZOOM_TIER, *DERIVED_ZOOM_TIERS)
VEGETATION_DIRECT_RUN_ID_PREFIX: Final = "vegetation-sentinel2-ndvi-forward:"
VEGETATION_DEFAULT_MAX_DAYS: Final = 1
VEGETATION_MAX_DAYS: Final = 5
VEGETATION_DEFAULT_TIME_BUDGET_SECONDS: Final = 900.0
VEGETATION_MAX_TIME_BUDGET_SECONDS: Final = 3_000.0
VEGETATION_DEFAULT_RETRY_ATTEMPTS: Final = 4
VEGETATION_MAX_RETRY_ATTEMPTS: Final = 10
VEGETATION_DEFAULT_RETRY_BASE_SECONDS: Final = 5.0
VEGETATION_MAX_RETRY_BASE_SECONDS: Final = 60.0
VEGETATION_DEFAULT_RETRY_MAX_SECONDS: Final = 60.0
VEGETATION_MAX_RETRY_MAX_SECONDS: Final = 300.0
VEGETATION_DEFAULT_CONTENTION_TIMEOUT_SECONDS: Final = 300.0
VEGETATION_MAX_CONTENTION_TIMEOUT_SECONDS: Final = 3_600.0
VEGETATION_STATEMENT_TIMEOUT_SECONDS: Final = 120
VEGETATION_MIN_DELAY_SECONDS: Final = 0.1
#: How far back one turn looks for an unfilled day before reporting a backlog. Bounded so a single
#: turn's tier-status census stays cheap; `pipeline/direct/soil/forward.py` SOIL_BACKLOG_SCAN_DAYS
#: records the identical judgement.
VEGETATION_BACKLOG_SCAN_DAYS: Final = 400
#: How far back a turn re-examines a day it has already governed as absent, behind every day that
#: owes real work. `pipeline/direct/AGENTS.md`, "A governed absence is re-examined, or it is permanent".
VEGETATION_ABSENCE_RECHECK_DAYS: Final = 14
VEGETATION_TIME_BUDGET_OUTCOME: Final = "time_budget_exhausted"
VEGETATION_SOURCE_UNSETTLED_OUTCOME: Final = "source_unsettled"
MONTHS_PER_YEAR: Final = 12


@dataclass(frozen=True, slots=True)
class VegetationForwardConfig:
    """Bound every day count, retry series and contention wait of one turn."""

    max_days: int
    time_budget_seconds: float
    retry_attempts: int
    retry_base_seconds: float
    retry_max_seconds: float
    contention_timeout_seconds: float
    run_id: str | None = None
    today: date | None = None


class VegetationForwardConfigError(ValueError):
    """Raised when a turn is asked for an unbounded or self-contradictory shape."""


def emit(payload: Mapping[str, object]) -> None:
    """Write one stable JSON progress record to stderr, leaving stdout for the terminal report."""
    print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)


def _registered_lane() -> LaneRegistration:
    """Read `history_floor`/`publication_lag_days` off the live Postgres-path registration.

    Reading rather than re-declaring keeps this direct writer's settled-edge math identical to the
    registered lane's (`pipeline/parquet/lane_registry.py:867-881`) without a second, driftable copy.
    """
    return LANE_REGISTRY[VEGETATION_PLANE_STREAM]


def settled_through(*, today: date) -> date:
    """Return the newest day this lane may be held to, given its registered publication lag."""
    return today - timedelta(days=_registered_lane().publication_lag_days)


def history_floor() -> date:
    """Return the first day this writer may ever consider, floored by the ownership handoff boundary."""
    return max(_registered_lane().history_floor, VEGETATION_DIRECT_WRITER_START_DAY + timedelta(days=1))


async def run_vegetation_forward(config: VegetationForwardConfig) -> dict[str, object]:
    """Publish the newest unfilled settled vegetation day, newest first, up to `max_days` per turn."""
    _validate_config(config)
    run_id = config.run_id or f"{VEGETATION_DIRECT_RUN_ID_PREFIX}{uuid.uuid4()}"
    today = config.today or datetime.now(UTC).date()
    store = ObjectStore.from_settings()
    availability_storage = BotoAvailabilityStorage.from_settings()
    deadline = time.monotonic() + config.time_budget_seconds
    availability = AvailabilityExtensionTally()

    loader_database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_session(loader_database_url) as session:
        support = await load_vegetation_support(session)
        await session.rollback()
        result = await _publish(
            session,
            store,
            support=support,
            today=today,
            run_id=run_id,
            config=config,
            deadline=deadline,
            availability_storage=availability_storage,
            availability=availability,
        )

    report: dict[str, object] = {
        "status": "completed",
        "run_id": run_id,
        "today": today.isoformat(),
        "layer": VEGETATION_PLANE_STREAM,
        **availability.to_summary(),
        **result,
    }
    emit({"event": "vegetation_forward_complete", **report})
    return report


async def _publish(  # noqa: PLR0913 - the store, support, clock and budget are distinct coordinates
    session: AsyncSession,
    store: ObjectStore,
    *,
    support: VegetationSupport,
    today: date,
    run_id: str,
    config: VegetationForwardConfig,
    deadline: float,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
) -> dict[str, object]:
    """Census the owed window once, then publish at most `max_days` days."""
    floor = history_floor()
    ceiling = settled_through(today=today)
    if ceiling < floor:
        return _skipped(today=today, outcome="not_yet_settled", floor=floor, ceiling=ceiling)
    retried = await _retry_owed_availability(
        session, store, deadline=deadline, availability_storage=availability_storage, availability=availability
    )
    first_day = max(floor, ceiling - timedelta(days=VEGETATION_BACKLOG_SCAN_DAYS - 1))
    statuses = await asyncio.to_thread(_tier_status_window, store, first_day, ceiling)
    backlog = _pending_days(statuses)
    selected = backlog[: config.max_days]
    published: list[dict[str, object]] = []
    for day in selected:
        if time.monotonic() >= deadline:
            published.append(_stopped_day(day, outcome=VEGETATION_TIME_BUDGET_OUTCOME, detail="before the day started"))
            break
        published.append(
            await _publish_day_with_retries(
                session,
                store,
                day,
                support=support,
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
        "outcome": "idempotent_noop" if not backlog else "published",
        "history_floor": floor.isoformat(),
        "settled_through": ceiling.isoformat(),
        "publication_lag_days": _registered_lane().publication_lag_days,
        "scan_first_day": first_day.isoformat(),
        "backlog_days": len(backlog),
        "availability_retried_days": retried,
        "days": published,
    }


async def _retry_owed_availability(
    session: AsyncSession,
    store: ObjectStore,
    *,
    deadline: float,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
) -> int:
    """Retry this lane's owed availability claims once, inside the turn's budget. Never raises."""
    if time.monotonic() >= deadline:
        return 0
    try:
        outcomes = await retry_pending_availability(
            session,
            store,
            lane=VEGETATION_PLANE_STREAM,
            kind=GAP_FILL_PARTITION_KIND,
            availability=availability_storage,
            now=lambda: datetime.now(UTC),
        )
    except Exception as error:  # an owed index entry may never stop this lane from publishing
        emit({"event": "vegetation_forward_availability_retry_failed", "detail": f"{type(error).__name__}: {error}"})
        return 0
    for outcome in outcomes:
        availability.record(outcome)
        emit({"event": "vegetation_forward_availability_retry", "state": outcome.state, "detail": outcome.note})
    return len(outcomes)


def _skipped(*, today: date, outcome: str, floor: date, ceiling: date) -> dict[str, object]:
    """Report a turn that took no publication, naming why rather than reporting an empty success."""
    del today
    return {
        "outcome": outcome,
        "history_floor": floor.isoformat(),
        "settled_through": ceiling.isoformat(),
        "publication_lag_days": _registered_lane().publication_lag_days,
        "days": [],
    }


def _stopped_day(day: date, *, outcome: str, attempts: int = 0, detail: str | None = None) -> dict[str, object]:
    """Report one day a bound stopped before it published, without claiming a source receipt it has none of."""
    return {
        "day": day.isoformat(),
        "outcome": outcome,
        "attempts": attempts,
        "cells_filled": 0,
        "source_receipt": None,
        "parts": 0,
        "rows_across_write": 0,
        "written_bytes": 0,
        "detail": detail,
    }


async def _publish_day_with_retries(  # noqa: PLR0913 - one lane-day coordinate per argument
    session: AsyncSession,
    store: ObjectStore,
    day: date,
    *,
    support: VegetationSupport,
    today: date,
    run_id: str,
    config: VegetationForwardConfig,
    deadline: float,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
    mirrored_past: date | None,
) -> dict[str, object]:
    """Acquire the lane-day lock once, then refetch and republish under it for every bounded attempt."""
    refuse_pre_ownership_day(day)
    lane = LANE_REGISTRY[VEGETATION_PLANE_STREAM]
    contention_deadline = min(time.monotonic() + config.contention_timeout_seconds, deadline)
    while True:
        if time.monotonic() >= deadline:
            return _stopped_day(
                day,
                outcome=VEGETATION_TIME_BUDGET_OUTCOME,
                detail="the turn's time budget ran out before the lane-day lock was granted",
            )
        async with postgres_lane_day_lock(session, _lane_day_lock_key(lane, day)) as granted:
            if granted:
                return await _publish_locked_day(
                    session,
                    store,
                    day,
                    support=support,
                    today=today,
                    run_id=run_id,
                    config=config,
                    deadline=deadline,
                    availability_storage=availability_storage,
                    availability=availability,
                    mirrored_past=mirrored_past,
                )
        remaining = contention_deadline - time.monotonic()
        if remaining <= 0:
            return _stopped_day(
                day,
                outcome="lock_contended",
                detail=f"{config.contention_timeout_seconds}s contention timeout reached without the lock",
            )
        await asyncio.sleep(min(1.0, remaining))


async def _publish_locked_day(  # noqa: PLR0913 - one lane-day coordinate per argument
    session: AsyncSession,
    store: ObjectStore,
    day: date,
    *,
    support: VegetationSupport,
    today: date,
    run_id: str,
    config: VegetationForwardConfig,
    deadline: float,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
    mirrored_past: date | None,
) -> dict[str, object]:
    """Refetch before every write attempt while one advisory lock stays held, then prove all four rungs."""
    lane = LANE_REGISTRY[VEGETATION_PLANE_STREAM]
    for attempt in range(1, config.retry_attempts + 1):
        if time.monotonic() >= deadline:
            return _stopped_day(
                day,
                outcome=VEGETATION_TIME_BUDGET_OUTCOME,
                attempts=attempt - 1,
                detail="the turn's time budget ran out before this attempt began",
            )
        adapter = DirectVegetationAdapter(
            fetch_source=lambda: fetch_vegetation_day(day=day, support=support, deadline=deadline),
            mirrored_past_proof=lambda: _mirrored_past_proof(day=day, mirrored_past=mirrored_past),
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
                statement_timeout_seconds=VEGETATION_STATEMENT_TIMEOUT_SECONDS,
                availability_storage=availability_storage,
                availability_tally=availability,
            )
            await session.rollback()
        except VegetationTimeBudgetExhaustedError as stop:
            with suppress(Exception):
                await session.rollback()
            return _stopped_day(day, outcome=VEGETATION_TIME_BUDGET_OUTCOME, attempts=attempt, detail=str(stop))
        except Exception as error:
            with suppress(Exception):
                await session.rollback()
            outcome, parts, rows, written_bytes = "raised", 0, 0, 0
            detail = f"{type(error).__name__}: {error}"
        if adapter.unsettled_refusal is not None:
            # NOT A FAILURE AND NOT A RETRY. Refetching this day inside the same turn asks the same
            # question of the same catalogue; the next turn is the soonest the answer can differ.
            return _stopped_day(
                day,
                outcome=VEGETATION_SOURCE_UNSETTLED_OUTCOME,
                attempts=attempt,
                detail=str(adapter.unsettled_refusal),
            )
        if outcome == "blocked":
            raise DirectVegetationError(detail or f"vegetation {day.isoformat()} is blocked")
        if outcome == "absent":
            return _day_result(
                day,
                adapter=adapter,
                outcome=outcome,
                parts=parts,
                rows=rows,
                written_bytes=written_bytes,
                attempts=attempt,
                detail=detail,
            )
        # A READ-BACK, NOT A CLAIM: only a day whose four rungs all read `data` is accepted as published.
        verification_detail = await _verify_written_ladder(store, day) if outcome == "written" else None
        if outcome == "written" and verification_detail is None:
            return _day_result(
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
            raise DirectVegetationError(
                f"vegetation {day.isoformat()} did not publish a complete four-rung ladder after {attempt} "
                f"attempt(s): outcome={outcome}, detail={detail}, verification={verification_detail}"
            )
        delay = min(_retry_delay(attempt, config=config), max(0.0, deadline - time.monotonic()))
        if delay <= 0:
            return _stopped_day(
                day,
                outcome=VEGETATION_TIME_BUDGET_OUTCOME,
                attempts=attempt,
                detail=f"outcome={outcome}, detail={detail}, verification={verification_detail}",
            )
        emit(
            {
                "event": "vegetation_forward_retry",
                "run_id": run_id,
                "day": day.isoformat(),
                "attempt": attempt,
                "outcome": outcome,
                "detail": detail,
                "retry_in_seconds": round(delay, 3),
            }
        )
        await asyncio.sleep(delay)
    raise AssertionError("bounded vegetation publish attempts exhausted")


async def _verify_written_ladder(store: ObjectStore, day: date) -> str | None:
    """Return why the day's four rungs do not all read `data` yet, or `None` when every one of them does."""
    try:
        tier_statuses = await asyncio.to_thread(_tier_status_day, store, day)
    except Exception as error:
        return f"{type(error).__name__}: {error}"
    if all(status == "data" for status in tier_statuses.values()):
        return None
    return f"tier statuses after the write were {tier_statuses}"


def _day_result(  # noqa: PLR0913 - the adapter evidence and the finalizer counters are separate facts
    day: date,
    *,
    adapter: DirectVegetationAdapter,
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
        raise DirectVegetationError(f"vegetation {day.isoformat()} returned {outcome} without a completed fetch")
    return {
        "day": day.isoformat(),
        "outcome": outcome,
        "attempts": attempts,
        "cells_filled": len(source.values),
        "source_receipt": source.receipt.as_event(),
        "parts": parts,
        "rows_across_write": rows,
        "written_bytes": written_bytes,
        "detail": detail,
    }


def _tier_status_window(
    store: ObjectStore, first_day: date, last_day: date
) -> dict[ZoomTier, dict[date, PartitionDayStatus]]:
    """List every rung across the owed window, one month prefix at a time."""
    keys_by_tier: dict[ZoomTier, list[str]] = {tier: [] for tier in VEGETATION_DIRECT_ALL_TIERS}
    cursor = date(first_day.year, first_day.month, 1)
    while cursor <= last_day:
        for tier in VEGETATION_DIRECT_ALL_TIERS:
            keys_by_tier[tier].extend(
                store.list_partition_keys(
                    VEGETATION_PLANE_STREAM, VEGETATION_DIRECT_KIND, tier, year=cursor.year, month=cursor.month
                )
            )
        cursor = date(
            cursor.year + (1 if cursor.month == MONTHS_PER_YEAR else 0),
            1 if cursor.month == MONTHS_PER_YEAR else cursor.month + 1,
            1,
        )
    return {
        tier: partition_day_statuses(
            layer=VEGETATION_PLANE_STREAM,
            kind=VEGETATION_DIRECT_KIND,
            zoom=tier,
            first_day=first_day,
            last_day=last_day,
            keys=keys,
        )
        for tier, keys in keys_by_tier.items()
    }


def _tier_status_day(store: ObjectStore, day: date) -> dict[ZoomTier, PartitionDayStatus]:
    """Read the durable completion checkpoint of one day at every rung."""
    return {
        tier: partition_day_statuses(
            layer=VEGETATION_PLANE_STREAM,
            kind=VEGETATION_DIRECT_KIND,
            zoom=tier,
            first_day=day,
            last_day=day,
            keys=store.list_partition_keys(
                VEGETATION_PLANE_STREAM, VEGETATION_DIRECT_KIND, tier, year=day.year, month=day.month
            ),
        )[day]
        for tier in VEGETATION_DIRECT_ALL_TIERS
    }


def _pending_days(statuses: Mapping[ZoomTier, Mapping[date, PartitionDayStatus]]) -> tuple[date, ...]:
    """Return the owed days newest first, then the recent absences this turn should re-examine."""
    days = tuple(statuses[VEGETATION_DIRECT_ALL_TIERS[0]])
    if not days:
        return ()
    recheck_floor = max(days) - timedelta(days=VEGETATION_ABSENCE_RECHECK_DAYS - 1)
    pending: list[date] = []
    rechecks: list[date] = []
    for day in reversed(days):
        rung = {tier: statuses[tier][day] for tier in VEGETATION_DIRECT_ALL_TIERS}
        if "conflict" in rung.values():
            raise DirectVegetationError(f"vegetation {day.isoformat()} has a data/absence conflict: {rung}")
        if rung[LANE_BASE_ZOOM_TIER] == "absent":
            if any(rung[tier] in {"data", "incomplete"} for tier in DERIVED_ZOOM_TIERS):
                raise DirectVegetationError(
                    f"vegetation {day.isoformat()} is absent at the base rung but carries derived parts: {rung}"
                )
            if day >= recheck_floor:
                rechecks.append(day)
            continue
        if any(status != "data" for status in rung.values()):
            pending.append(day)
    return (*pending, *rechecks)


def _mirrored_past_day(statuses: Mapping[ZoomTier, Mapping[date, PartitionDayStatus]], day: date) -> date | None:
    """Return the earliest LATER settled day this lane already publishes with values, or `None`."""
    base = statuses[LANE_BASE_ZOOM_TIER]
    return next((later for later in sorted(base) if later > day and base[later] == "data"), None)


def _mirrored_past_proof(*, day: date, mirrored_past: date | None) -> str | None:
    """Render the sentence a governed absence carries as its justification, or `None` when it has none."""
    if mirrored_past is None:
        return None
    return (
        f"vegetation is published with values for {mirrored_past.isoformat()}, which is later than "
        f"{day.isoformat()}, so Earth Search's catalogue has moved past this day and its zero-cell answer is settled"
    )


def _retry_delay(attempt: int, *, config: VegetationForwardConfig) -> float:
    """Return a jittered, capped exponential wait so concurrent turns do not resynchronise."""
    ceiling = min(config.retry_max_seconds, config.retry_base_seconds * (2 ** max(0, attempt - 1)))
    return float(ceiling + random.uniform(0.0, min(1.0, ceiling / 4)))


def _validate_config(config: VegetationForwardConfig) -> None:
    """Fail closed on every process-bound knob before a socket or a session is opened."""
    if not 1 <= config.max_days <= VEGETATION_MAX_DAYS:
        raise VegetationForwardConfigError(f"--max-days must be between 1 and {VEGETATION_MAX_DAYS}")
    if not 1 <= config.retry_attempts <= VEGETATION_MAX_RETRY_ATTEMPTS:
        raise VegetationForwardConfigError(f"--retry-attempts must be between 1 and {VEGETATION_MAX_RETRY_ATTEMPTS}")
    bounds = {
        "--time-budget-seconds": (config.time_budget_seconds, VEGETATION_MAX_TIME_BUDGET_SECONDS),
        "--retry-base-seconds": (config.retry_base_seconds, VEGETATION_MAX_RETRY_BASE_SECONDS),
        "--retry-max-seconds": (config.retry_max_seconds, VEGETATION_MAX_RETRY_MAX_SECONDS),
        "--contention-timeout-seconds": (config.contention_timeout_seconds, VEGETATION_MAX_CONTENTION_TIMEOUT_SECONDS),
    }
    for name, (value, maximum) in bounds.items():
        if not math.isfinite(value) or not VEGETATION_MIN_DELAY_SECONDS <= value <= maximum:
            raise VegetationForwardConfigError(
                f"{name} must be finite and between {VEGETATION_MIN_DELAY_SECONDS:g} and {maximum:g}, got {value!r}"
            )
    if config.retry_max_seconds < config.retry_base_seconds:
        raise VegetationForwardConfigError("--retry-max-seconds must be at least --retry-base-seconds")


def parser() -> argparse.ArgumentParser:
    """Build the bounded, forward-only vegetation lane operator."""
    built = argparse.ArgumentParser(description=__doc__)
    built.add_argument("--max-days", type=int, default=VEGETATION_DEFAULT_MAX_DAYS)
    built.add_argument("--time-budget-seconds", type=float, default=VEGETATION_DEFAULT_TIME_BUDGET_SECONDS)
    built.add_argument("--run-id", default=None)
    built.add_argument("--retry-attempts", type=int, default=VEGETATION_DEFAULT_RETRY_ATTEMPTS)
    built.add_argument("--retry-base-seconds", type=float, default=VEGETATION_DEFAULT_RETRY_BASE_SECONDS)
    built.add_argument("--retry-max-seconds", type=float, default=VEGETATION_DEFAULT_RETRY_MAX_SECONDS)
    built.add_argument(
        "--contention-timeout-seconds", type=float, default=VEGETATION_DEFAULT_CONTENTION_TIMEOUT_SECONDS
    )
    return built


def parse_args(argv: Sequence[str] | None = None) -> VegetationForwardConfig:
    """Validate every operator input at the boundary and hand back one bounded turn."""
    built = parser()
    arguments = built.parse_args(argv)
    config = VegetationForwardConfig(
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
    except VegetationForwardConfigError as error:
        built.error(str(error))
    return config


async def main(argv: Sequence[str] | None = None) -> int:
    """Run one bounded turn and emit exactly one terminal report on stdout."""
    config = parse_args(argv)
    try:
        report = await run_vegetation_forward(config)
    except Exception as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


__all__ = [
    "VEGETATION_ABSENCE_RECHECK_DAYS",
    "VEGETATION_BACKLOG_SCAN_DAYS",
    "VEGETATION_DEFAULT_TIME_BUDGET_SECONDS",
    "VEGETATION_DIRECT_ALL_TIERS",
    "VEGETATION_MAX_DAYS",
    "VEGETATION_SOURCE_UNSETTLED_OUTCOME",
    "VEGETATION_TIME_BUDGET_OUTCOME",
    "VegetationForwardConfig",
    "VegetationForwardConfigError",
    "history_floor",
    "main",
    "parse_args",
    "parser",
    "run_vegetation_forward",
    "settled_through",
]
