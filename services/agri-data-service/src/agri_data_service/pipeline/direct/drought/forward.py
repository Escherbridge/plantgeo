"""Publish the newest settled USDM release directly, one release Tuesday per lane-day lock.

Bypasses PostgreSQL entirely: `pipeline/lanes/drought.py::export_drought_release` (the registered
`_fill_drought` adapter) reads `geo.drought_areas` and is the OLD path this writer replaces for every
day it owns, exactly as `pipeline/direct/fire_detections.py` replaces `_fill_fire_detections` for its
own bounded window. Unlike fire, there is no co-existing Postgres producer left running for older
days -- `postgres-drought`/`ingest-drought` are both stopped (owner decision 2026-09-04) -- so this
module owns the FULL floor-to-settled window; `backfill.py` walks it oldest-first, this module walks
it newest-first. See `pipeline/direct/AGENTS.md`, "Drought".
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
from agri_data_service.pipeline.direct.drought.adapter import (
    DROUGHT_DIRECT_KIND,
    DirectDroughtAdapter,
    DirectDroughtError,
)
from agri_data_service.pipeline.direct.drought.products import (
    drought_lane_registration,
    newest_settled_tuesday,
    release_weeks,
)
from agri_data_service.pipeline.direct.drought.source import DroughtDaySource, fetch_drought_day
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.availability_extension import AvailabilityExtensionTally
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.gap_fill import (
    _lane_day_lock_key,
    fill_one_lane_day,
    postgres_lane_day_lock,
    unlocked_lane_day,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS
from agri_data_service.warehouse.schemas.drought import DROUGHT_STREAM

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.paths import PartitionDayStatus
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration

DROUGHT_DIRECT_ALL_TIERS: Final[tuple[ZoomTier, ...]] = (LANE_BASE_ZOOM_TIER, *DERIVED_ZOOM_TIERS)
DROUGHT_FORWARD_RUN_ID_PREFIX: Final = "drought-forward:"
DROUGHT_DEFAULT_MAX_DAYS: Final = 1
DROUGHT_MAX_DAYS: Final = 5
DROUGHT_DEFAULT_TIME_BUDGET_SECONDS: Final = 300.0
DROUGHT_MAX_TIME_BUDGET_SECONDS: Final = 1_800.0
DROUGHT_DEFAULT_RETRY_ATTEMPTS: Final = 5
DROUGHT_MAX_RETRY_ATTEMPTS: Final = 10
DROUGHT_DEFAULT_RETRY_BASE_SECONDS: Final = 5.0
DROUGHT_MAX_RETRY_BASE_SECONDS: Final = 60.0
DROUGHT_DEFAULT_RETRY_MAX_SECONDS: Final = 60.0
DROUGHT_MAX_RETRY_MAX_SECONDS: Final = 300.0
DROUGHT_DEFAULT_CONTENTION_TIMEOUT_SECONDS: Final = 300.0
DROUGHT_MAX_CONTENTION_TIMEOUT_SECONDS: Final = 3_600.0
DROUGHT_STATEMENT_TIMEOUT_SECONDS: Final = 120
DROUGHT_MIN_DELAY_SECONDS: Final = 0.1
#: How many weeks back one turn scans the object store for owed or recheck-eligible releases. Bounded
#: below by the lane's own `history_floor`, so this is a per-turn LISTING cost cap, not a horizon claim
#: -- `backfill.py` is what actually walks the whole floor-to-settled window.
DROUGHT_BACKLOG_SCAN_WEEKS: Final = 60
#: How far back a turn re-examines a Tuesday it has ALREADY governed as absent. USDM has never missed
#: a weekly release across the 209/209 measured window (`pipeline/parquet/lane_registry.py`'s
#: `DROUGHT_STREAM` `floor_basis`), so this is a safety margin, not an expected path -- kept short
#: because a recheck costs one live request. See `pipeline/direct/AGENTS.md`, "A governed absence is
#: re-examined, or it is permanent".
DROUGHT_ABSENCE_RECHECK_WEEKS: Final = 8
DROUGHT_TIME_BUDGET_OUTCOME: Final = "time_budget_exhausted"
DROUGHT_SOURCE_UNSETTLED_OUTCOME: Final = "source_unsettled"
MONTHS_PER_YEAR: Final = 12


class DroughtForwardConfigError(ValueError):
    """Raised when a turn is asked for an unbounded or self-contradictory shape."""


@dataclass(frozen=True, slots=True)
class DroughtForwardConfig:
    """Bound every source request, week count, retry series and contention wait of one turn."""

    max_days: int
    time_budget_seconds: float
    retry_attempts: int
    retry_base_seconds: float
    retry_max_seconds: float
    contention_timeout_seconds: float
    run_id: str | None = None
    today: date | None = None


def emit(payload: Mapping[str, object]) -> None:
    """Write one stable JSON progress record to stderr, leaving stdout for the terminal report."""
    print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)


async def run_drought_forward(config: DroughtForwardConfig) -> dict[str, object]:
    """Publish the newest unfilled settled USDM release(s), newest first, up to `max_days` of them."""
    _validate_config(config)
    run_id = config.run_id or f"{DROUGHT_FORWARD_RUN_ID_PREFIX}{uuid.uuid4()}"
    today = config.today or datetime.now(UTC).date()
    lane = drought_lane_registration()
    settled_through = newest_settled_tuesday(today=today, publication_lag_days=lane.publication_lag_days)
    first_day = max(lane.history_floor, settled_through - timedelta(weeks=DROUGHT_BACKLOG_SCAN_WEEKS - 1))
    weeks = release_weeks(first_day, settled_through)
    deadline = time.monotonic() + config.time_budget_seconds
    availability = AvailabilityExtensionTally()

    if not weeks:
        report = _noop_report(
            run_id, first_day=first_day, settled_through=settled_through, lane=lane, availability=availability
        )
        emit({"event": "drought_forward_noop", **report})
        return report

    store = ObjectStore.from_settings()
    availability_storage = BotoAvailabilityStorage.from_settings()
    statuses = await _retry_async(
        "initial drought R2 census",
        lambda: asyncio.to_thread(_tier_status_for_weeks, store, weeks),
        attempts=config.retry_attempts,
        base_seconds=config.retry_base_seconds,
        max_seconds=config.retry_max_seconds,
    )
    pending = _pending_weeks(statuses, weeks)[: config.max_days]
    emit(
        {
            "event": "drought_forward_started",
            "run_id": run_id,
            "layer": DROUGHT_STREAM,
            "namespace": f"layer={DROUGHT_STREAM}/kind={DROUGHT_DIRECT_KIND}/",
            "first_day": first_day.isoformat(),
            "settled_through": settled_through.isoformat(),
            "history_floor": lane.history_floor.isoformat(),
            "selected_weeks": [week.isoformat() for week in pending],
        }
    )

    results: list[dict[str, object]] = []
    loader_database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_session(loader_database_url) as session:
        for day in pending:
            if time.monotonic() >= deadline:
                results.append(_skipped_result(day, outcome=DROUGHT_TIME_BUDGET_OUTCOME))
                continue
            result = await _publish_release_with_retries(
                session,
                store,
                lane,
                day,
                today=today,
                run_id=run_id,
                config=config,
                deadline=deadline,
                statuses=statuses,
                availability_storage=availability_storage,
                availability=availability,
            )
            results.append(result)
            emit({"event": "drought_forward_release_complete", "run_id": run_id, **result})

    final_statuses = await _retry_async(
        "final drought R2 census",
        lambda: asyncio.to_thread(_tier_status_for_weeks, store, weeks),
        attempts=config.retry_attempts,
        base_seconds=config.retry_base_seconds,
        max_seconds=config.retry_max_seconds,
    )
    window_backlog = _pending_weeks(final_statuses, weeks)
    selected_and_settled = {
        day
        for day, result in zip(pending, results, strict=True)
        if result["outcome"] not in {DROUGHT_TIME_BUDGET_OUTCOME, DROUGHT_SOURCE_UNSETTLED_OUTCOME}
    }
    remaining = tuple(day for day in window_backlog if day in selected_and_settled)
    if remaining:
        raise DirectDroughtError(
            f"the bounded forward window still has {len(remaining)} unfilled release(s): "
            f"{', '.join(day.isoformat() for day in remaining)}"
        )
    return {
        "status": "completed",
        "run_id": run_id,
        "layer": DROUGHT_STREAM,
        "namespace": f"layer={DROUGHT_STREAM}/kind={DROUGHT_DIRECT_KIND}/",
        "first_day": first_day.isoformat(),
        "settled_through": settled_through.isoformat(),
        "history_floor": lane.history_floor.isoformat(),
        "days_published": len(results),
        **availability.to_summary(),
        "results": results,
        "remaining_window_backlog": [day.isoformat() for day in window_backlog],
        "tier_status_counts": _tier_status_counts(final_statuses),
    }


def _noop_report(
    run_id: str,
    *,
    first_day: date,
    settled_through: date,
    lane: LaneRegistration,
    availability: AvailabilityExtensionTally,
) -> dict[str, object]:
    return {
        "status": "completed",
        "run_id": run_id,
        "layer": DROUGHT_STREAM,
        "namespace": f"layer={DROUGHT_STREAM}/kind={DROUGHT_DIRECT_KIND}/",
        "first_day": first_day.isoformat(),
        "settled_through": settled_through.isoformat(),
        "history_floor": lane.history_floor.isoformat(),
        "days_published": 0,
        **availability.to_summary(),
        "results": [],
        "remaining_window_backlog": [],
        "tier_status_counts": {},
        "detail": "no release Tuesday falls inside the scanned window",
    }


async def _publish_release_with_retries(  # noqa: PLR0913 - one caller-supplied coordinate per arg
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    day: date,
    *,
    today: date,
    run_id: str,
    config: DroughtForwardConfig,
    deadline: float,
    statuses: Mapping[ZoomTier, Mapping[date, PartitionDayStatus]],
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
) -> dict[str, object]:
    """Acquire the lane-day lock once, then refetch and republish under it for every bounded attempt."""
    contention_deadline = time.monotonic() + config.contention_timeout_seconds
    while True:
        async with postgres_lane_day_lock(session, _lane_day_lock_key(lane, day)) as granted:
            if granted:
                return await _publish_locked_release_with_retries(
                    session,
                    store,
                    lane,
                    day,
                    today=today,
                    run_id=run_id,
                    config=config,
                    deadline=deadline,
                    statuses=statuses,
                    availability_storage=availability_storage,
                    availability=availability,
                )
        await session.rollback()
        remaining = min(contention_deadline, deadline) - time.monotonic()
        if remaining <= 0:
            raise DirectDroughtError(f"lane-day contention for {day} exceeded {config.contention_timeout_seconds:g}s")
        delay = min(
            remaining, _retry_delay(1, base_seconds=config.retry_base_seconds, max_seconds=config.retry_max_seconds)
        )
        emit(
            {
                "event": "drought_forward_contention",
                "run_id": run_id,
                "day": day.isoformat(),
                "retry_in_seconds": round(delay, 3),
            }
        )
        await asyncio.sleep(delay)


async def _publish_locked_release_with_retries(  # noqa: PLR0913
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    day: date,
    *,
    today: date,
    run_id: str,
    config: DroughtForwardConfig,
    deadline: float,
    statuses: Mapping[ZoomTier, Mapping[date, PartitionDayStatus]],
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
) -> dict[str, object]:
    """Refetch before every write/verification attempt while one advisory lock remains held."""
    for write_attempt in range(1, config.retry_attempts + 1):
        if time.monotonic() >= deadline:
            return _skipped_result(day, outcome=DROUGHT_TIME_BUDGET_OUTCOME)
        adapter = DirectDroughtAdapter(
            fetch_source=lambda: fetch_drought_day(
                day,
                retry_attempts=1,
                retry_base_seconds=config.retry_base_seconds,
                retry_max_seconds=config.retry_max_seconds,
            ),
            mirrored_past_proof=lambda: _mirrored_past_proof(statuses, day=day),
        )
        direct_lane = replace(lane, adapter=adapter)
        try:
            outcome, parts, rows, written_bytes, detail = await fill_one_lane_day(
                session,
                store,
                direct_lane,
                day=day,
                run_id=run_id,
                now=lambda: datetime.now(UTC),
                today=today,
                lane_day_lock=unlocked_lane_day,
                statement_timeout_seconds=DROUGHT_STATEMENT_TIMEOUT_SECONDS,
                availability_storage=availability_storage,
                availability_tally=availability,
            )
            await session.rollback()
        except Exception as error:  # a source-unsettled refusal must not fail the turn
            with suppress(Exception):
                await session.rollback()
            if adapter.unsettled_refusal is not None:
                # A REFUSAL, not a failure: USDM has not published this Tuesday yet and nothing
                # proves the release moved past it. Returned immediately, spending no more of this
                # release's retry budget -- the next turn is the soonest the answer can change.
                return _source_result(
                    adapter.source,
                    outcome=DROUGHT_SOURCE_UNSETTLED_OUTCOME,
                    parts=0,
                    rows=0,
                    written_bytes=0,
                    detail=str(error),
                )
            outcome = "raised"
            parts = rows = written_bytes = 0
            detail = f"{day.isoformat()}: {type(error).__name__}: {error}"
            source = adapter.source
        else:
            source = adapter.source

        if outcome in {"blocked", "absent", "written"} and source is None:
            raise DirectDroughtError(f"drought {day} returned {outcome} without a completed locked source fetch")
        if outcome in {"blocked", "absent"}:
            if outcome == "blocked":
                raise DirectDroughtError(detail or f"drought {day} is blocked")
            assert source is not None  # `outcome == "absent"` only follows a completed fetch
            return _source_result(
                source, outcome=outcome, parts=parts, rows=rows, written_bytes=written_bytes, detail=detail
            )

        verified = False
        verification_error: Exception | None = None
        try:
            day_statuses = await asyncio.to_thread(_tier_status_week, store, day)
            verified = all(status == "data" for status in day_statuses.values())
        except Exception as error:  # reported below, never silently swallowed
            verification_error = error
        if outcome == "written" and verified:
            assert source is not None  # `outcome == "written"` only follows a completed fetch
            return _source_result(
                source, outcome=outcome, parts=parts, rows=rows, written_bytes=written_bytes, detail=detail
            )
        if write_attempt >= config.retry_attempts:
            suffix = (
                f"; verification failed: {type(verification_error).__name__}: {verification_error}"
                if verification_error is not None
                else ""
            )
            raise DirectDroughtError(
                f"drought {day} did not publish a complete four-tier ladder after {write_attempt} attempt(s): "
                f"outcome={outcome}, detail={detail}{suffix}"
            )
        delay = _retry_delay(
            write_attempt, base_seconds=config.retry_base_seconds, max_seconds=config.retry_max_seconds
        )
        emit(
            {
                "event": "drought_forward_r2_retry",
                "run_id": run_id,
                "day": day.isoformat(),
                "attempt": write_attempt,
                "outcome": outcome,
                "detail": detail,
                "retry_in_seconds": round(delay, 3),
            }
        )
        await asyncio.sleep(delay)
    raise AssertionError("bounded drought publish attempts exhausted")


def _source_result(  # noqa: PLR0913 - one caller-supplied coordinate per arg
    source: DroughtDaySource | None,
    *,
    outcome: str,
    parts: int,
    rows: int,
    written_bytes: int,
    detail: str | None,
) -> dict[str, object]:
    """Render the source evidence captured inside the lane-day lock."""
    return {
        "day": source.day.isoformat() if source is not None else None,
        "outcome": outcome,
        "published": source is not None and source.release is not None,
        "areas": len(source.release.areas) if source is not None and source.release is not None else 0,
        "parts": parts,
        "rows_across_write": rows,
        "written_bytes": written_bytes,
        "detail": detail,
    }


def _skipped_result(day: date, *, outcome: str) -> dict[str, object]:
    """Render one week's result in the same shape `_source_result` produces, for a day never fetched."""
    return {
        "day": day.isoformat(),
        "outcome": outcome,
        "published": False,
        "areas": 0,
        "parts": 0,
        "rows_across_write": 0,
        "written_bytes": 0,
        "detail": None,
    }


def _tier_status_for_weeks(
    store: ObjectStore,
    weeks: Sequence[date],
) -> dict[ZoomTier, dict[date, PartitionDayStatus]]:
    """Read every rung's completion status for exactly the given release Tuesdays -- no other day.

    `partition_day_statuses` answers for every calendar day in `[first, last]`, which would mark
    every non-Tuesday `missing` for this weekly `release_series` lane; those are filtered out here so
    a caller never has to reason about a day that could never be a real candidate.
    """
    if not weeks:
        return {tier: {} for tier in DROUGHT_DIRECT_ALL_TIERS}
    first_day, last_day = weeks[0], weeks[-1]
    keys_by_tier: dict[ZoomTier, list[str]] = {tier: [] for tier in DROUGHT_DIRECT_ALL_TIERS}
    cursor = date(first_day.year, first_day.month, 1)
    while cursor <= last_day:
        for tier in DROUGHT_DIRECT_ALL_TIERS:
            keys_by_tier[tier].extend(
                store.list_partition_keys(
                    DROUGHT_STREAM, DROUGHT_DIRECT_KIND, tier, year=cursor.year, month=cursor.month
                )
            )
        cursor = date(
            cursor.year + (1 if cursor.month == MONTHS_PER_YEAR else 0),
            1 if cursor.month == MONTHS_PER_YEAR else cursor.month + 1,
            1,
        )
    full = {
        tier: partition_day_statuses(
            layer=DROUGHT_STREAM, kind=DROUGHT_DIRECT_KIND, zoom=tier, first_day=first_day, last_day=last_day, keys=keys
        )
        for tier, keys in keys_by_tier.items()
    }
    weeks_set = set(weeks)
    return {tier: {day: status for day, status in by_day.items() if day in weeks_set} for tier, by_day in full.items()}


def _tier_status_week(store: ObjectStore, day: date) -> dict[ZoomTier, PartitionDayStatus]:
    return {
        tier: partition_day_statuses(
            layer=DROUGHT_STREAM,
            kind=DROUGHT_DIRECT_KIND,
            zoom=tier,
            first_day=day,
            last_day=day,
            keys=store.list_partition_keys(DROUGHT_STREAM, DROUGHT_DIRECT_KIND, tier, year=day.year, month=day.month),
        )[day]
        for tier in DROUGHT_DIRECT_ALL_TIERS
    }


def _pending_weeks(
    statuses: Mapping[ZoomTier, Mapping[date, PartitionDayStatus]],
    weeks: Sequence[date],
) -> tuple[date, ...]:
    """Return owed release Tuesdays newest first, then recent ABSENCES this turn should re-examine."""
    if not weeks:
        return ()
    recheck_floor = weeks[-1] - timedelta(weeks=DROUGHT_ABSENCE_RECHECK_WEEKS - 1)
    pending: list[date] = []
    rechecks: list[date] = []
    for day in reversed(weeks):
        rung = {tier: statuses[tier].get(day, "missing") for tier in DROUGHT_DIRECT_ALL_TIERS}
        if "conflict" in rung.values():
            raise DirectDroughtError(f"drought {day.isoformat()} has a data/absence conflict: {rung}")
        if rung[LANE_BASE_ZOOM_TIER] == "absent":
            if any(rung[tier] in {"data", "incomplete"} for tier in DERIVED_ZOOM_TIERS):
                raise DirectDroughtError(
                    f"drought {day.isoformat()} is absent at z{LANE_BASE_ZOOM_TIER} but carries derived parts: {rung}"
                )
            if day >= recheck_floor:
                rechecks.append(day)
            continue
        if any(status != "data" for status in rung.values()):
            pending.append(day)
    return (*pending, *rechecks)


def _mirrored_past_proof(statuses: Mapping[ZoomTier, Mapping[date, PartitionDayStatus]], *, day: date) -> str | None:
    """Render the sentence a governed absence carries, or `None` when no later week is published yet.

    Read out of the CENSUS SNAPSHOT taken at the top of the turn -- no extra request. A release
    published LATER IN THE SAME TURN will not yet be reflected here, which under-proves rather than
    over-proves: at most it delays a governed absence by one tick, never fabricates one early.
    """
    base = statuses.get(LANE_BASE_ZOOM_TIER, {})
    mirrored_past = next((later for later in sorted(base) if later > day and base[later] == "data"), None)
    if mirrored_past is None:
        return None
    return (
        f"drought is published with a release for {mirrored_past.isoformat()}, which is later than "
        f"{day.isoformat()}, so USDM's weekly cadence has moved past this Tuesday"
    )


def _tier_status_counts(statuses: Mapping[ZoomTier, Mapping[date, PartitionDayStatus]]) -> dict[str, dict[str, int]]:
    return {
        f"z{tier}": {
            status: sum(1 for held in by_day.values() if held == status)
            for status in ("data", "absent", "missing", "incomplete", "conflict")
        }
        for tier, by_day in statuses.items()
    }


async def _retry_async[T](
    label: str,
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    base_seconds: float,
    max_seconds: float,
) -> T:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except Exception as error:  # every R2 census failure is retried the same way
            last_error = error
            if attempt >= attempts:
                break
            delay = _retry_delay(attempt, base_seconds=base_seconds, max_seconds=max_seconds)
            emit(
                {
                    "event": "drought_forward_retry",
                    "operation": label,
                    "attempt": attempt,
                    "error_type": type(error).__name__,
                    "retry_in_seconds": round(delay, 3),
                }
            )
            await asyncio.sleep(delay)
    assert last_error is not None  # attempts >= 1 is enforced by `_validate_config`
    raise DirectDroughtError(f"{label} failed after {attempts} attempts") from last_error


def _retry_delay(attempt: int, *, base_seconds: float, max_seconds: float) -> float:
    ceiling = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    return float(ceiling + random.uniform(0.0, min(1.0, ceiling / 4)))


def _validate_config(config: DroughtForwardConfig) -> None:
    """Fail closed on every process-bound knob before a socket or a session is opened."""
    if not 1 <= config.max_days <= DROUGHT_MAX_DAYS:
        raise DroughtForwardConfigError(f"--max-days must be between 1 and {DROUGHT_MAX_DAYS}")
    if not 1 <= config.retry_attempts <= DROUGHT_MAX_RETRY_ATTEMPTS:
        raise DroughtForwardConfigError(f"--retry-attempts must be between 1 and {DROUGHT_MAX_RETRY_ATTEMPTS}")
    bounds = {
        "--time-budget-seconds": (config.time_budget_seconds, DROUGHT_MAX_TIME_BUDGET_SECONDS),
        "--retry-base-seconds": (config.retry_base_seconds, DROUGHT_MAX_RETRY_BASE_SECONDS),
        "--retry-max-seconds": (config.retry_max_seconds, DROUGHT_MAX_RETRY_MAX_SECONDS),
        "--contention-timeout-seconds": (config.contention_timeout_seconds, DROUGHT_MAX_CONTENTION_TIMEOUT_SECONDS),
    }
    for name, (value, maximum) in bounds.items():
        if not math.isfinite(value) or not DROUGHT_MIN_DELAY_SECONDS <= value <= maximum:
            raise DroughtForwardConfigError(
                f"{name} must be finite and between {DROUGHT_MIN_DELAY_SECONDS:g} and {maximum:g}, got {value!r}"
            )
    if config.retry_max_seconds < config.retry_base_seconds:
        raise DroughtForwardConfigError("--retry-max-seconds must be at least --retry-base-seconds")


def parser() -> argparse.ArgumentParser:
    """Build the bounded, forward-only drought lane operator. No `--product`: this lane has one."""
    built = argparse.ArgumentParser(description=__doc__)
    built.add_argument("--max-days", type=int, default=DROUGHT_DEFAULT_MAX_DAYS)
    built.add_argument("--time-budget-seconds", type=float, default=DROUGHT_DEFAULT_TIME_BUDGET_SECONDS)
    built.add_argument("--run-id", default=None)
    built.add_argument("--retry-attempts", type=int, default=DROUGHT_DEFAULT_RETRY_ATTEMPTS)
    built.add_argument("--retry-base-seconds", type=float, default=DROUGHT_DEFAULT_RETRY_BASE_SECONDS)
    built.add_argument("--retry-max-seconds", type=float, default=DROUGHT_DEFAULT_RETRY_MAX_SECONDS)
    built.add_argument("--contention-timeout-seconds", type=float, default=DROUGHT_DEFAULT_CONTENTION_TIMEOUT_SECONDS)
    return built


def parse_args(argv: Sequence[str] | None = None) -> DroughtForwardConfig:
    """Validate every operator input at the boundary and hand back one bounded turn."""
    built = parser()
    arguments = built.parse_args(argv)
    config = DroughtForwardConfig(
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
    except DroughtForwardConfigError as error:
        built.error(str(error))
    return config


async def main(argv: Sequence[str] | None = None) -> int:
    """Run one bounded turn and emit exactly one terminal report on stdout."""
    config = parse_args(argv)
    try:
        report = await run_drought_forward(config)
    except Exception as error:  # the one terminal failure report a caller parses
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


__all__ = [
    "DROUGHT_ABSENCE_RECHECK_WEEKS",
    "DROUGHT_BACKLOG_SCAN_WEEKS",
    "DROUGHT_DIRECT_ALL_TIERS",
    "DROUGHT_MAX_DAYS",
    "DROUGHT_SOURCE_UNSETTLED_OUTCOME",
    "DROUGHT_TIME_BUDGET_OUTCOME",
    "DroughtForwardConfig",
    "DroughtForwardConfigError",
    "main",
    "parse_args",
    "parser",
    "run_drought_forward",
]
