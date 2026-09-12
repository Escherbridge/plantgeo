"""Publish the newest unfilled governed MTBS release day directly, one release day per lane-day lock.

Bypasses PostgreSQL entirely. The OLD path was
`pipeline/lanes/burn_severity.py::export_burn_severity_release_day` behind the registered
`_fill_burn_severity` adapter, reading `geo.features`; on 2026-09-07 that adapter became a
source-direct refusal naming this package, so this writer is the only path to a burn-severity
PARQUET day. The lane module survives the swap because
`pipeline/validation/burn_severity.py` still calls its `read_burn_severity_release_day` for the D1
reconciliation; only the export half is now unreachable.

`mtbs-forward` / `ingest-mtbs` IS STILL ACTIVE and still writes `geo.features`
(`docs/lanes/burn-severity.md` section 2); stopping it is an owner-confirmed Railway variable edit
this package never performs. That is not a second writer of this stream -- different store -- but
after the swap it is Postgres work with no Parquet consumer left, since `parquet-burn-severity` was
the reader of those rows and is retired.

There is no "settled through today" computation the way `drought/products.py::newest_settled_tuesday`
has one: a release day is not a step on a calendar cadence, it is whatever
`ingest/mtbs.py::MTBS_ANNUAL_RELEASE_DATES` says it is, and every entry in that table is already a
real, past release (`docs/lanes/burn-severity.md` section 3). So this walker has no
"not yet published" state to poll for -- see `adapter.py`'s own docstring for why there is no
`mirrored_past_proof`/`unsettled_refusal` machinery here, unlike `drought`'s weekly USDM Tuesday.
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
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.foundation.parquet.paths import partition_day_statuses
from agri_data_service.ingest.mtbs import inline_bbox_value
from agri_data_service.ingest.policy import UNCONFIGURED_BBOX_REASON, parse_bbox, resolve_bounded_bbox
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct import (
    BBOX_UNCONFIGURED,
    LANE_DAY_OUTCOMES,
    NO_WINDOW,
    REFUSE_WHOLE_RELEASE,
    SKIP_TURN_ON_UNCONFIGURED_BBOX,
    TIME_BUDGET_EXHAUSTED,
    DirectWriterContract,
)
from agri_data_service.pipeline.direct.burn_severity.adapter import (
    BURN_SEVERITY_DIRECT_KIND,
    DirectBurnSeverityAdapter,
    DirectBurnSeverityError,
)
from agri_data_service.pipeline.direct.burn_severity.products import (
    burn_severity_lane_registration,
    governed_release_days,
    release_days_by_ignition_year,
)
from agri_data_service.pipeline.direct.burn_severity.source import (
    fetch_burn_severity_release_day,
)
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
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_STREAM

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.paths import PartitionDayStatus
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.ingest.mtbs import BoundingBox
    from agri_data_service.pipeline.direct.burn_severity.source import BurnSeverityDaySource
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration

BURN_SEVERITY_DIRECT_ALL_TIERS: Final[tuple[ZoomTier, ...]] = (LANE_BASE_ZOOM_TIER, *DERIVED_ZOOM_TIERS)
BURN_SEVERITY_FORWARD_RUN_ID_PREFIX: Final = "burn-severity-forward:"
BURN_SEVERITY_DEFAULT_MAX_DAYS: Final = 1
BURN_SEVERITY_MAX_DAYS: Final = 5
BURN_SEVERITY_DEFAULT_TIME_BUDGET_SECONDS: Final = 300.0
BURN_SEVERITY_MAX_TIME_BUDGET_SECONDS: Final = 1_800.0
BURN_SEVERITY_DEFAULT_RETRY_ATTEMPTS: Final = 5
BURN_SEVERITY_MAX_RETRY_ATTEMPTS: Final = 10
BURN_SEVERITY_DEFAULT_RETRY_BASE_SECONDS: Final = 5.0
BURN_SEVERITY_MAX_RETRY_BASE_SECONDS: Final = 60.0
BURN_SEVERITY_DEFAULT_RETRY_MAX_SECONDS: Final = 60.0
BURN_SEVERITY_MAX_RETRY_MAX_SECONDS: Final = 300.0
BURN_SEVERITY_DEFAULT_CONTENTION_TIMEOUT_SECONDS: Final = 300.0
BURN_SEVERITY_MAX_CONTENTION_TIMEOUT_SECONDS: Final = 3_600.0
BURN_SEVERITY_STATEMENT_TIMEOUT_SECONDS: Final = 120
BURN_SEVERITY_MIN_DELAY_SECONDS: Final = 0.1
BURN_SEVERITY_TIME_BUDGET_OUTCOME: Final = TIME_BUDGET_EXHAUSTED

#: What this writer promises about its own failure policy, CLI surface and reported words; see
#: `pipeline/direct/__init__.py` for the axes and `tests/direct/test_direct_writer_contract.py` for
#: the table all eleven are read as.
WRITER_CONTRACT: Final = DirectWriterContract(
    slug="burn-severity",
    identity_defect=REFUSE_WHOLE_RELEASE,
    geometry_defect=REFUSE_WHOLE_RELEASE,
    unconfigured_bbox=SKIP_TURN_ON_UNCONFIGURED_BBOX,
    turn_outcomes=LANE_DAY_OUTCOMES | {TIME_BUDGET_EXHAUSTED, BBOX_UNCONFIGURED, NO_WINDOW},
    flags_absent_on_purpose={
        "--product": "one stream, BURN_SEVERITY_STREAM; see products.py. A `--product` here could "
        "only ever take one value.",
        "--max-records": "the release-day population is whatever MTBS's governed release table names "
        "for that day; capping it would publish a partial fire year that reads as a complete one.",
        "--max-records-per-day": "same reason as `--max-records`; a release day is a governed unit, "
        "not a fetch this writer is free to truncate.",
    },
    policy_basis="Identity is refused rather than counted because `ingest/mtbs.py` already guarantees "
    "`fire_id` is unique within one cohort's paged fetch (MtbsDuplicateFeatureError), so a duplicate "
    "reaching support.py means two ignition years reused one MTBS Fire_ID -- a data-shape error, not a "
    "record to drop. The unset-bbox skip matches ingest-mtbs, which this lane still runs beside: MTBS "
    "is a national archive bounded only by INGEST_BBOX, so an unset envelope would fetch the country.",
)
_MONTHS_PER_YEAR: Final = 12


class BurnSeverityForwardConfigError(ValueError):
    """Raised when a turn is asked for an unbounded or self-contradictory shape."""


@dataclass(frozen=True, slots=True)
class BurnSeverityForwardConfig:
    """Bound every source request, day count, retry series and contention wait of one turn."""

    max_days: int
    time_budget_seconds: float
    retry_attempts: int
    retry_base_seconds: float
    retry_max_seconds: float
    contention_timeout_seconds: float
    run_id: str | None = None
    bbox: str | None = None
    today: date | None = None
    current_snapshots: bool = False


def emit(payload: Mapping[str, object]) -> None:
    """Write one stable JSON progress record to stderr, leaving stdout for the terminal report."""
    print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)


async def run_burn_severity_forward(config: BurnSeverityForwardConfig) -> dict[str, object]:
    """Publish the newest unfilled governed release day(s), newest first, up to `max_days` of them."""
    _validate_config(config)
    run_id = config.run_id or f"{BURN_SEVERITY_FORWARD_RUN_ID_PREFIX}{uuid.uuid4()}"
    today = config.today or datetime.now(UTC).date()
    lane = burn_severity_lane_registration()
    by_day = release_days_by_ignition_year()
    days = governed_release_days()
    availability = AvailabilityExtensionTally()

    resolved_bbox = resolve_bounded_bbox(config.bbox)
    if resolved_bbox is None:
        report = _noop_report(
            run_id,
            days=days,
            availability=availability,
            outcome=BBOX_UNCONFIGURED,
            detail=UNCONFIGURED_BBOX_REASON,
        )
        emit({"event": "burn_severity_forward_noop", **report})
        return report
    if not days:
        report = _noop_report(
            run_id,
            days=days,
            availability=availability,
            outcome=NO_WINDOW,
            detail="MTBS_ANNUAL_RELEASE_DATES carries no governed release day",
        )
        emit({"event": "burn_severity_forward_noop", **report})
        return report
    bounding_box = parse_bbox(resolved_bbox)

    deadline = time.monotonic() + config.time_budget_seconds
    store = ObjectStore.from_settings()
    availability_storage = BotoAvailabilityStorage.from_settings()
    statuses = await _retry_async(
        "initial burn-severity R2 census",
        lambda: asyncio.to_thread(_tier_status_for_days, store, days),
        attempts=config.retry_attempts,
        base_seconds=config.retry_base_seconds,
        max_seconds=config.retry_max_seconds,
    )
    pending = _pending_days(statuses, days, newest_first=True)[: config.max_days]
    emit(
        {
            "event": "burn_severity_forward_started",
            "run_id": run_id,
            "layer": BURN_SEVERITY_STREAM,
            "namespace": f"layer={BURN_SEVERITY_STREAM}/kind={BURN_SEVERITY_DIRECT_KIND}/",
            "history_floor": lane.history_floor.isoformat(),
            "governed_release_days": [day.isoformat() for day in days],
            "selected_days": [day.isoformat() for day in pending],
        }
    )

    results: list[dict[str, object]] = []
    loader_database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_session(loader_database_url) as session:
        for day in pending:
            if time.monotonic() >= deadline:
                results.append(_skipped_result(day, outcome=BURN_SEVERITY_TIME_BUDGET_OUTCOME))
                continue
            result = await _publish_release_day_with_retries(
                session,
                store,
                lane,
                day,
                ignition_years=by_day[day],
                bounding_box=bounding_box,
                run_id=run_id,
                config=config,
                deadline=deadline,
                today=today,
                availability_storage=availability_storage,
                availability=availability,
            )
            results.append(result)
            emit({"event": "burn_severity_forward_release_complete", "run_id": run_id, **result})

    final_statuses = await _retry_async(
        "final burn-severity R2 census",
        lambda: asyncio.to_thread(_tier_status_for_days, store, days),
        attempts=config.retry_attempts,
        base_seconds=config.retry_base_seconds,
        max_seconds=config.retry_max_seconds,
    )
    window_backlog = _pending_days(final_statuses, days, newest_first=True)
    selected_and_settled = {
        day
        for day, result in zip(pending, results, strict=True)
        if result["outcome"] != BURN_SEVERITY_TIME_BUDGET_OUTCOME
    }
    remaining = tuple(day for day in window_backlog if day in selected_and_settled)
    if remaining:
        raise DirectBurnSeverityError(
            f"the bounded forward turn still has {len(remaining)} unfilled release day(s): "
            f"{', '.join(day.isoformat() for day in remaining)}"
        )
    return {
        "status": "completed",
        "run_id": run_id,
        "layer": BURN_SEVERITY_STREAM,
        "namespace": f"layer={BURN_SEVERITY_STREAM}/kind={BURN_SEVERITY_DIRECT_KIND}/",
        "history_floor": lane.history_floor.isoformat(),
        "governed_release_days": [day.isoformat() for day in days],
        "days_published": len(results),
        **availability.to_summary(),
        "results": results,
        "remaining_window_backlog": [day.isoformat() for day in window_backlog],
        "tier_status_counts": _tier_status_counts(final_statuses),
    }


def _noop_report(
    run_id: str,
    *,
    days: tuple[date, ...],
    availability: AvailabilityExtensionTally,
    outcome: str,
    detail: str,
) -> dict[str, object]:
    """Render a turn that published nothing, naming the state in a declared word as well as in prose.

    `outcome` is REQUIRED rather than defaulted: the two callers stop for genuinely different reasons
    -- no envelope to fetch over, versus no governed release day to fetch -- and a default would let
    a third caller inherit whichever was written first. The `detail` sentence stays: the word is what
    a monitor reads across all eleven writers, the sentence is what an operator reads.
    """
    return {
        "status": "completed",
        "run_id": run_id,
        "outcome": outcome,
        "layer": BURN_SEVERITY_STREAM,
        "namespace": f"layer={BURN_SEVERITY_STREAM}/kind={BURN_SEVERITY_DIRECT_KIND}/",
        "governed_release_days": [day.isoformat() for day in days],
        "days_published": 0,
        **availability.to_summary(),
        "results": [],
        "remaining_window_backlog": [],
        "tier_status_counts": {},
        "detail": detail,
    }


async def _publish_release_day_with_retries(  # noqa: PLR0913 - one caller-supplied coordinate per arg
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    day: date,
    *,
    ignition_years: tuple[int, ...],
    bounding_box: BoundingBox,
    run_id: str,
    config: BurnSeverityForwardConfig,
    deadline: float,
    today: date,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
) -> dict[str, object]:
    """Acquire the lane-day lock once, then refetch and republish under it for every bounded attempt."""
    contention_deadline = time.monotonic() + config.contention_timeout_seconds
    while True:
        async with postgres_lane_day_lock(session, _lane_day_lock_key(lane, day)) as granted:
            if granted:
                return await _publish_locked_release_day_with_retries(
                    session,
                    store,
                    lane,
                    day,
                    ignition_years=ignition_years,
                    bounding_box=bounding_box,
                    run_id=run_id,
                    config=config,
                    deadline=deadline,
                    today=today,
                    availability_storage=availability_storage,
                    availability=availability,
                )
        await session.rollback()
        remaining = min(contention_deadline, deadline) - time.monotonic()
        if remaining <= 0:
            raise DirectBurnSeverityError(
                f"lane-day contention for {day} exceeded {config.contention_timeout_seconds:g}s"
            )
        delay = min(
            remaining, _retry_delay(1, base_seconds=config.retry_base_seconds, max_seconds=config.retry_max_seconds)
        )
        emit(
            {
                "event": "burn_severity_forward_contention",
                "run_id": run_id,
                "day": day.isoformat(),
                "retry_in_seconds": round(delay, 3),
            }
        )
        await asyncio.sleep(delay)


async def _publish_locked_release_day_with_retries(  # noqa: PLR0913
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    day: date,
    *,
    ignition_years: tuple[int, ...],
    bounding_box: BoundingBox,
    run_id: str,
    config: BurnSeverityForwardConfig,
    deadline: float,
    today: date,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
) -> dict[str, object]:
    """Refetch before every write/verification attempt while one advisory lock remains held."""
    for write_attempt in range(1, config.retry_attempts + 1):
        if time.monotonic() >= deadline:
            return _skipped_result(day, outcome=BURN_SEVERITY_TIME_BUDGET_OUTCOME)
        adapter = DirectBurnSeverityAdapter(
            fetch_source=lambda: fetch_burn_severity_release_day(
                day,
                ignition_years,
                bounding_box=bounding_box,
                retry_attempts=1,
                retry_base_seconds=config.retry_base_seconds,
                retry_max_seconds=config.retry_max_seconds,
            )
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
                statement_timeout_seconds=BURN_SEVERITY_STATEMENT_TIMEOUT_SECONDS,
                availability_storage=availability_storage,
                availability_tally=availability,
            )
            await session.rollback()
        except Exception as error:  # every source/object-store failure surfaces as `raised`
            with suppress(Exception):
                await session.rollback()
            outcome = "raised"
            parts = rows = written_bytes = 0
            detail = f"{day.isoformat()}: {type(error).__name__}: {error}"
            source = adapter.source
        else:
            source = adapter.source

        if outcome in {"blocked", "absent", "written"} and source is None:
            raise DirectBurnSeverityError(
                f"burn-severity {day} returned {outcome} without a completed locked source fetch"
            )
        if outcome in {"blocked", "absent"}:
            if outcome == "blocked":
                raise DirectBurnSeverityError(detail or f"burn-severity {day} is blocked")
            assert source is not None  # `outcome == "absent"` only follows a completed fetch
            return _source_result(
                source, outcome=outcome, parts=parts, rows=rows, written_bytes=written_bytes, detail=detail
            )

        verified = False
        verification_error: Exception | None = None
        try:
            day_statuses = await asyncio.to_thread(_tier_status_day, store, day)
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
            raise DirectBurnSeverityError(
                f"burn-severity {day} did not publish a complete four-tier ladder after {write_attempt} "
                f"attempt(s): outcome={outcome}, detail={detail}{suffix}"
            )
        delay = _retry_delay(
            write_attempt, base_seconds=config.retry_base_seconds, max_seconds=config.retry_max_seconds
        )
        emit(
            {
                "event": "burn_severity_forward_r2_retry",
                "run_id": run_id,
                "day": day.isoformat(),
                "attempt": write_attempt,
                "outcome": outcome,
                "detail": detail,
                "retry_in_seconds": round(delay, 3),
            }
        )
        await asyncio.sleep(delay)
    raise AssertionError("bounded burn-severity publish attempts exhausted")


def _source_result(  # noqa: PLR0913 - one caller-supplied coordinate per arg
    source: BurnSeverityDaySource | None,
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
        "ignition_years": list(source.ignition_years) if source is not None else [],
        "fires": len(source.records) if source is not None else 0,
        "parts": parts,
        "rows_across_write": rows,
        "written_bytes": written_bytes,
        "detail": detail,
    }


def _skipped_result(day: date, *, outcome: str) -> dict[str, object]:
    """Render one day's result in the same shape `_source_result` produces, for a day never fetched."""
    return {
        "day": day.isoformat(),
        "outcome": outcome,
        "ignition_years": [],
        "fires": 0,
        "parts": 0,
        "rows_across_write": 0,
        "written_bytes": 0,
        "detail": None,
    }


def _tier_status_for_days(store: ObjectStore, days: Sequence[date]) -> dict[ZoomTier, dict[date, PartitionDayStatus]]:
    """Read every rung's completion status for exactly the given release days -- no other day.

    `partition_day_statuses` answers for every calendar day in `[first, last]`, which would mark
    every non-release day `missing` for this irregular `release_series` lane; those are filtered out
    here so a caller never has to reason about a day that could never be a real candidate.
    """
    if not days:
        return {tier: {} for tier in BURN_SEVERITY_DIRECT_ALL_TIERS}
    first_day, last_day = days[0], days[-1]
    keys_by_tier: dict[ZoomTier, list[str]] = {tier: [] for tier in BURN_SEVERITY_DIRECT_ALL_TIERS}
    cursor = date(first_day.year, first_day.month, 1)
    while cursor <= last_day:
        for tier in BURN_SEVERITY_DIRECT_ALL_TIERS:
            keys_by_tier[tier].extend(
                store.list_partition_keys(
                    BURN_SEVERITY_STREAM, BURN_SEVERITY_DIRECT_KIND, tier, year=cursor.year, month=cursor.month
                )
            )
        cursor = date(
            cursor.year + (1 if cursor.month == _MONTHS_PER_YEAR else 0),
            1 if cursor.month == _MONTHS_PER_YEAR else cursor.month + 1,
            1,
        )
    full = {
        tier: partition_day_statuses(
            layer=BURN_SEVERITY_STREAM,
            kind=BURN_SEVERITY_DIRECT_KIND,
            zoom=tier,
            first_day=first_day,
            last_day=last_day,
            keys=keys,
        )
        for tier, keys in keys_by_tier.items()
    }
    days_set = set(days)
    return {tier: {day: status for day, status in by_day.items() if day in days_set} for tier, by_day in full.items()}


def _tier_status_day(store: ObjectStore, day: date) -> dict[ZoomTier, PartitionDayStatus]:
    return {
        tier: partition_day_statuses(
            layer=BURN_SEVERITY_STREAM,
            kind=BURN_SEVERITY_DIRECT_KIND,
            zoom=tier,
            first_day=day,
            last_day=day,
            keys=store.list_partition_keys(
                BURN_SEVERITY_STREAM, BURN_SEVERITY_DIRECT_KIND, tier, year=day.year, month=day.month
            ),
        )[day]
        for tier in BURN_SEVERITY_DIRECT_ALL_TIERS
    }


def _pending_days(
    statuses: Mapping[ZoomTier, Mapping[date, PartitionDayStatus]],
    days: Sequence[date],
    *,
    newest_first: bool,
) -> tuple[date, ...]:
    """Every governed release day this turn still owes -- real gaps before rechecks.

    Unlike `drought/forward.py::_pending_weeks`, a governed absence here is ALWAYS re-examined,
    never bounded to a recent recheck window: `governed_release_days()` holds at most a handful of
    entries today and grows only through a governance action, so the unbounded rescan
    `pipeline/direct/AGENTS.md`'s drought section warns a 209-week history against costs nothing
    here. `backfill.py` reuses this unchanged, passing `newest_first=False`.
    """
    ordered = tuple(reversed(days)) if newest_first else tuple(days)
    pending: list[date] = []
    rechecks: list[date] = []
    for day in ordered:
        rung = {tier: statuses[tier].get(day, "missing") for tier in BURN_SEVERITY_DIRECT_ALL_TIERS}
        if "conflict" in rung.values():
            raise DirectBurnSeverityError(f"burn-severity {day.isoformat()} has a data/absence conflict: {rung}")
        if rung[LANE_BASE_ZOOM_TIER] == "absent":
            if any(rung[tier] in {"data", "incomplete"} for tier in DERIVED_ZOOM_TIERS):
                raise DirectBurnSeverityError(
                    f"burn-severity {day.isoformat()} is absent at z{LANE_BASE_ZOOM_TIER} but carries "
                    f"derived parts: {rung}"
                )
            rechecks.append(day)
            continue
        if any(status != "data" for status in rung.values()):
            pending.append(day)
    return (*pending, *rechecks)


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
                    "event": "burn_severity_forward_retry",
                    "operation": label,
                    "attempt": attempt,
                    "error_type": type(error).__name__,
                    "retry_in_seconds": round(delay, 3),
                }
            )
            await asyncio.sleep(delay)
    assert last_error is not None  # attempts >= 1 is enforced by `_validate_config`
    raise DirectBurnSeverityError(f"{label} failed after {attempts} attempts") from last_error


def _retry_delay(attempt: int, *, base_seconds: float, max_seconds: float) -> float:
    ceiling = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    return float(ceiling + random.uniform(0.0, min(1.0, ceiling / 4)))


def _validate_config(config: BurnSeverityForwardConfig) -> None:
    """Fail closed on every process-bound knob before a socket or a session is opened."""
    if not 1 <= config.max_days <= BURN_SEVERITY_MAX_DAYS:
        raise BurnSeverityForwardConfigError(f"--max-days must be between 1 and {BURN_SEVERITY_MAX_DAYS}")
    if not 1 <= config.retry_attempts <= BURN_SEVERITY_MAX_RETRY_ATTEMPTS:
        raise BurnSeverityForwardConfigError(
            f"--retry-attempts must be between 1 and {BURN_SEVERITY_MAX_RETRY_ATTEMPTS}"
        )
    bounds = {
        "--time-budget-seconds": (config.time_budget_seconds, BURN_SEVERITY_MAX_TIME_BUDGET_SECONDS),
        "--retry-base-seconds": (config.retry_base_seconds, BURN_SEVERITY_MAX_RETRY_BASE_SECONDS),
        "--retry-max-seconds": (config.retry_max_seconds, BURN_SEVERITY_MAX_RETRY_MAX_SECONDS),
        "--contention-timeout-seconds": (
            config.contention_timeout_seconds,
            BURN_SEVERITY_MAX_CONTENTION_TIMEOUT_SECONDS,
        ),
    }
    for name, (value, maximum) in bounds.items():
        if not math.isfinite(value) or not BURN_SEVERITY_MIN_DELAY_SECONDS <= value <= maximum:
            raise BurnSeverityForwardConfigError(
                f"{name} must be finite and between {BURN_SEVERITY_MIN_DELAY_SECONDS:g} and {maximum:g}, got {value!r}"
            )
    if config.retry_max_seconds < config.retry_base_seconds:
        raise BurnSeverityForwardConfigError("--retry-max-seconds must be at least --retry-base-seconds")


def parser() -> argparse.ArgumentParser:
    """Build the bounded, forward-only burn-severity lane operator.

    Every knob this parser does NOT expose is named in `WRITER_CONTRACT.flags_absent_on_purpose`
    with its reason, and `tests/direct/test_direct_writer_contract.py` fails if one of them quietly
    appears here or if a knob vanishes without an entry. `--product` is absent because this lane has
    exactly one stream.
    """
    built = argparse.ArgumentParser(description=__doc__)
    built.add_argument("--max-days", type=int, default=BURN_SEVERITY_DEFAULT_MAX_DAYS)
    built.add_argument("--bbox", default=None)
    built.add_argument("--time-budget-seconds", type=float, default=BURN_SEVERITY_DEFAULT_TIME_BUDGET_SECONDS)
    built.add_argument("--run-id", default=None)
    built.add_argument(
        "--current-snapshots",
        action="store_true",
        help="daily eligible-stage publication and weekly current-source capture",
    )
    built.add_argument("--retry-attempts", type=int, default=BURN_SEVERITY_DEFAULT_RETRY_ATTEMPTS)
    built.add_argument("--retry-base-seconds", type=float, default=BURN_SEVERITY_DEFAULT_RETRY_BASE_SECONDS)
    built.add_argument("--retry-max-seconds", type=float, default=BURN_SEVERITY_DEFAULT_RETRY_MAX_SECONDS)
    built.add_argument(
        "--contention-timeout-seconds", type=float, default=BURN_SEVERITY_DEFAULT_CONTENTION_TIMEOUT_SECONDS
    )
    return built


def parse_args(argv: Sequence[str] | None = None) -> BurnSeverityForwardConfig:
    """Validate every operator input at the boundary and hand back one bounded turn.

    `inline_bbox_value` rewrites `--bbox -125,42,...` to `--bbox=-125,42,...` before argparse ever
    sees it, matching `ingest/mtbs.py::main`'s own handling: a bare `--bbox -125,...` reads the
    leading `-125` as a second flag rather than this option's value.
    """
    raw = list(argv) if argv is not None else sys.argv[1:]
    built = parser()
    arguments = built.parse_args(inline_bbox_value(raw))
    config = BurnSeverityForwardConfig(
        max_days=arguments.max_days,
        time_budget_seconds=arguments.time_budget_seconds,
        retry_attempts=arguments.retry_attempts,
        retry_base_seconds=arguments.retry_base_seconds,
        retry_max_seconds=arguments.retry_max_seconds,
        contention_timeout_seconds=arguments.contention_timeout_seconds,
        run_id=arguments.run_id,
        bbox=arguments.bbox,
        current_snapshots=arguments.current_snapshots,
    )
    try:
        _validate_config(config)
    except BurnSeverityForwardConfigError as error:
        built.error(str(error))
    return config


async def main(argv: Sequence[str] | None = None) -> int:
    """Run one bounded turn and emit exactly one terminal report on stdout."""
    config = parse_args(argv)
    try:
        if config.current_snapshots:
            from agri_data_service.pipeline.direct.burn_severity.daily import (  # noqa: PLC0415 - reuses historical forward
                run_daily,
            )

            report = await run_daily(config)
        else:
            report = await run_burn_severity_forward(config)
    except Exception as error:  # the one terminal failure report a caller parses
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


__all__ = [
    "BURN_SEVERITY_DIRECT_ALL_TIERS",
    "BURN_SEVERITY_MAX_DAYS",
    "BURN_SEVERITY_TIME_BUDGET_OUTCOME",
    "BurnSeverityForwardConfig",
    "BurnSeverityForwardConfigError",
    "main",
    "parse_args",
    "parser",
    "run_burn_severity_forward",
]
