"""Promote persisted NDVI cells and author their affected governed days to the full Parquet ladder."""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal

from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.db.vegetation_publication import (
    VEGETATION_FINGERPRINT_HEX_LENGTH,
    VEGETATION_PUBLICATION_LOOKBACK_DAYS,
    VegetationPublicationTarget,
    acknowledge_vegetation_publication,
    enqueue_vegetation_publication,
    pending_vegetation_publication,
    record_vegetation_publication_attempt,
    try_postgres_vegetation_publication_barrier,
    unlocked_vegetation_publication_barrier,
    vegetation_day_fingerprints,
    vegetation_publication_is_fully_enrolled,
)
from agri_data_service.execution.vegetation_ndvi_plane import (
    CELL_BATCH_SIZE,
    GRID_NAME,
    SOURCE_LAYER_NAME,
    CorpusChangedDuringRegistrationError,
    RegistrationSummary,
    prefixed_cell_key,
    register_governed_forward_plane,
)
from agri_data_service.foundation.parquet.paths import partition_day_statuses, try_parse_partition_path
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.availability_extension import AvailabilityExtensionTally
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
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from contextlib import AbstractAsyncContextManager
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.ingest.writer import FeatureWrite
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage

    VegetationLaneDayLock = Callable[[AsyncSession, str], AbstractAsyncContextManager[bool]]

_AFFECTED_DAYS_SQL: Final = text(load_query_sql("pipeline/vegetation_forward_affected_days.sql"))
_CHANGED_SCOPE_SQL: Final = text(load_query_sql("pipeline/vegetation_forward_changed_scope.sql"))
_SOURCE_REVISION_SQL: Final = text(load_query_sql("pipeline/vegetation_forward_revision.sql"))

VEGETATION_FORWARD_CHECKPOINT_PREFIX: Final = "vegetation-forward-v2:"
VEGETATION_FORWARD_LEGACY_CHECKPOINT_PREFIX: Final = "vegetation-forward-v1:"
VEGETATION_FORWARD_MAX_DAYS_PER_RUN: Final = 25
VEGETATION_FORWARD_TIME_BUDGET_SECONDS: Final = 600.0
VEGETATION_FORWARD_MAX_ATTEMPTS: Final = 3
VEGETATION_FORWARD_RETRY_BASE_SECONDS: Final = 1.0
VEGETATION_FORWARD_MAX_RETRY_SECONDS: Final = 15.0
VEGETATION_FORWARD_STATEMENT_TIMEOUT_SECONDS: Final = 120
VEGETATION_FORWARD_ZOOM_TIERS: Final = (LANE_BASE_ZOOM_TIER, *DERIVED_ZOOM_TIERS)

ForwardDayOutcome = Literal["checkpointed", "written", "contended"]
ForwardStopReason = Literal["complete", "day_limit", "time_budget"]

_TRANSIENT_DATABASE_ERRORS: Final = (
    OperationalError,
    InterfaceError,
    SQLAlchemyTimeoutError,
    CorpusChangedDuringRegistrationError,
)


class VegetationForwardError(RuntimeError):
    """Raised when persisted vegetation cannot be promoted or published without weakening parity."""


class VegetationForwardIncompleteError(VegetationForwardError):
    """Raised when a bounded forward run leaves governed touched days for a later resume."""


@dataclass(frozen=True, slots=True)
class VegetationForwardScope:
    """The unique selected grid cells and latest selected publisher day."""

    cell_keys: tuple[str, ...]
    cutoff_day: date
    observed_days: tuple[date, ...]
    cell_days: tuple[tuple[str, date], ...]


@dataclass(frozen=True, slots=True)
class VegetationForwardDayResult:
    """One affected governed day's terminal result."""

    day: date
    outcome: ForwardDayOutcome
    attempt_count: int
    row_count: int = 0
    byte_count: int = 0
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class VegetationForwardSummary:
    """The bounded governed promotion and Parquet-authoring result."""

    scope: VegetationForwardScope
    registration: RegistrationSummary
    source_revision: int
    affected_day_count: int
    examined_day_count: int
    stop_reason: ForwardStopReason
    days: tuple[VegetationForwardDayResult, ...]
    #: Every availability verdict this run's days produced. A day in the bucket and not in the index
    #: is a number here rather than a sentence inside one day's detail string.
    availability: AvailabilityExtensionTally = field(default_factory=AvailabilityExtensionTally)

    @property
    def written_day_count(self) -> int:
        return sum(result.outcome == "written" for result in self.days)

    @property
    def checkpointed_day_count(self) -> int:
        return sum(result.outcome == "checkpointed" for result in self.days)

    @property
    def contended_day_count(self) -> int:
        return sum(result.outcome == "contended" for result in self.days)

    def to_details(self) -> dict[str, int]:
        return {
            **self.availability.to_summary(),
            "affected_days": self.affected_day_count,
            "checkpointed_days": self.checkpointed_day_count,
            "contended_days": self.contended_day_count,
            "examined_days": self.examined_day_count,
            "forward_complete": int(self.stop_reason == "complete" and self.contended_day_count == 0),
            "pending_days": self.affected_day_count - self.written_day_count - self.checkpointed_day_count,
            "requested_cells": len(self.scope.cell_keys),
            "source_revision": self.source_revision,
            "written_days": self.written_day_count,
        }


@dataclass(frozen=True, slots=True)
class VegetationPublicationDrainSummary:
    """One unconditional defensive enqueue and global fair queue drain."""

    through_day: date
    defensive_day_count: int
    pending_day_count: int
    remaining_day_count: int
    source_revision: int
    stop_reason: ForwardStopReason
    days: tuple[VegetationForwardDayResult, ...]
    #: Every availability verdict this drain's days produced; see `VegetationForwardSummary`.
    availability: AvailabilityExtensionTally = field(default_factory=AvailabilityExtensionTally)

    @property
    def written_day_count(self) -> int:
        return sum(result.outcome == "written" for result in self.days)

    @property
    def checkpointed_day_count(self) -> int:
        return sum(result.outcome == "checkpointed" for result in self.days)

    @property
    def contended_day_count(self) -> int:
        return sum(result.outcome == "contended" for result in self.days)

    @property
    def is_complete(self) -> bool:
        return self.stop_reason == "complete" and not self.remaining_day_count and not self.contended_day_count

    def to_details(self) -> dict[str, int | str]:
        return {
            **self.availability.to_summary(),
            "checkpointed_days": self.checkpointed_day_count,
            "contended_days": self.contended_day_count,
            "defensive_days": self.defensive_day_count,
            "forward_complete": int(self.is_complete),
            "pending_days": self.pending_day_count,
            "remaining_days": self.remaining_day_count,
            "source_revision": self.source_revision,
            "stop_reason": self.stop_reason,
            "written_days": self.written_day_count,
        }


def vegetation_forward_scope(writes: Sequence[FeatureWrite]) -> VegetationForwardScope:
    """Derive the governed selection from the exact NDVI writes accepted for persistence."""
    cell_keys: list[str] = []
    observed_days: list[date] = []
    for write in writes:
        grid_cell = write.grid_cell
        observed_at = write.identity.observed_at
        if write.layer_reference != SOURCE_LAYER_NAME:
            raise VegetationForwardError(
                f"vegetation forward publication requires raw layer {SOURCE_LAYER_NAME!r}, "
                f"not {write.layer_reference!r}"
            )
        if grid_cell is None or grid_cell.grid_name != GRID_NAME:
            raise VegetationForwardError("every vegetation forward write must carry its Sentinel-2 grid cell")
        if observed_at is None or observed_at.utcoffset() is None:
            raise VegetationForwardError(
                "every vegetation forward write must carry a timezone-aware publisher observation time"
            )
        cell_keys.append(grid_cell.cell_key)
        observed_days.append(observed_at.astimezone(UTC).date())
    if not cell_keys:
        raise VegetationForwardError("vegetation forward publication requires at least one persisted cell")
    return VegetationForwardScope(
        cell_keys=tuple(dict.fromkeys(cell_keys)),
        cutoff_day=max(observed_days),
        observed_days=tuple(sorted(set(observed_days))),
        cell_days=tuple(sorted(set(zip(cell_keys, observed_days, strict=True)))),
    )


async def changed_vegetation_forward_scope(
    session: AsyncSession,
    *,
    since: datetime,
    through_day: date,
) -> VegetationForwardScope:
    """Load the exact raw vegetation cell-days changed in one operator-pinned window."""
    if since.utcoffset() is None:
        raise ValueError("since must include a UTC offset")
    result = await session.execute(
        _CHANGED_SCOPE_SQL,
        {"since": since.astimezone(UTC), "through_day": through_day},
    )
    cell_days = tuple(
        sorted(
            {
                (str(row["cell_key"]), row["observed_day"])
                for row in result.mappings()
                if isinstance(row["observed_day"], date)
            }
        )
    )
    if not cell_days:
        raise VegetationForwardError(
            f"no valid raw vegetation cell-day changed since {since.astimezone(UTC).isoformat()} "
            f"through {through_day.isoformat()}"
        )
    return VegetationForwardScope(
        cell_keys=tuple(dict.fromkeys(cell_key for cell_key, _day in cell_days)),
        cutoff_day=through_day,
        observed_days=tuple(sorted({day for _cell_key, day in cell_days})),
        cell_days=cell_days,
    )


async def _affected_days(
    session: AsyncSession,
    *,
    scope: VegetationForwardScope,
    source_release_id: UUID,
) -> tuple[date, ...]:
    days: set[date] = set()
    governed_cell_days: set[tuple[str, date]] = set()
    for start in range(0, len(scope.cell_days), CELL_BATCH_SIZE):
        pair_batch = scope.cell_days[start : start + CELL_BATCH_SIZE]
        result = await session.execute(
            _AFFECTED_DAYS_SQL,
            {
                "prefixed_cell_keys": [prefixed_cell_key(cell_key) for cell_key, _day in pair_batch],
                "cutoff_day": scope.cutoff_day,
                "observed_days": [observed_day for _cell_key, observed_day in pair_batch],
                "source_release_id": source_release_id,
            },
        )
        for row in result.mappings():
            observed_day = row["observed_day"]
            cell_key = str(row["cell_key"])
            if not isinstance(observed_day, date):
                raise VegetationForwardError(
                    f"governed affected day came back as {type(observed_day).__name__}, not date"
                )
            days.add(observed_day)
            governed_cell_days.add((cell_key, observed_day))
    if not days:
        raise VegetationForwardError("the promoted vegetation selection resolved to no governed observation day")
    missing_cell_days = {
        (prefixed_cell_key(cell_key), observed_day) for cell_key, observed_day in scope.cell_days
    } - governed_cell_days
    if missing_cell_days:
        sample = ", ".join(f"{cell_key}@{day.isoformat()}" for cell_key, day in sorted(missing_cell_days)[:5])
        raise VegetationForwardError(
            f"forward registration did not materialise {len(missing_cell_days)} selected vegetation cell-day(s): "
            f"{sample}"
        )
    return tuple(sorted(days, reverse=True))


async def _source_revision(session: AsyncSession) -> int:
    result = await session.execute(_SOURCE_REVISION_SQL)
    row = result.mappings().one()
    revision = int(row["observation_count"])
    if revision <= 0:
        raise VegetationForwardError("the governed vegetation plane has no accepted observation to publish")
    return revision


def _checkpoint_run_id(source_fingerprint: str) -> str:
    return f"{VEGETATION_FORWARD_CHECKPOINT_PREFIX}{source_fingerprint}"


def _checkpoint_fingerprint(run_id: str) -> str | None:
    if not run_id.startswith(VEGETATION_FORWARD_CHECKPOINT_PREFIX):
        return None
    fingerprint = run_id.removeprefix(VEGETATION_FORWARD_CHECKPOINT_PREFIX)
    return (
        fingerprint
        if len(fingerprint) == VEGETATION_FINGERPRINT_HEX_LENGTH
        and all(char in "0123456789abcdef" for char in fingerprint)
        else None
    )


def _legacy_checkpoint_revision(run_id: str) -> int | None:
    if not run_id.startswith(VEGETATION_FORWARD_LEGACY_CHECKPOINT_PREFIX):
        return None
    try:
        revision = int(run_id.removeprefix(VEGETATION_FORWARD_LEGACY_CHECKPOINT_PREFIX))
    except ValueError:
        return None
    return revision if revision > 0 else None


def _ladder_checkpoint_is_current(
    store: ObjectStore,
    *,
    day: date,
    source_fingerprint: str,
    legacy_source_revision: int | None = None,
) -> bool:
    for tier in VEGETATION_FORWARD_ZOOM_TIERS:
        keys = store.list_partition_keys(
            VEGETATION_PLANE_STREAM,
            GAP_FILL_PARTITION_KIND,
            tier,
            year=day.year,
            month=day.month,
        )
        status = partition_day_statuses(
            layer=VEGETATION_PLANE_STREAM,
            kind=GAP_FILL_PARTITION_KIND,
            zoom=tier,
            first_day=day,
            last_day=day,
            keys=keys,
        )[day]
        if status != "data":
            return False
        marker = store.read_completion_marker(
            VEGETATION_PLANE_STREAM,
            GAP_FILL_PARTITION_KIND,
            tier,
            day,
        )
        if marker is None:
            return False
        marker_fingerprint = _checkpoint_fingerprint(marker.run_id)
        legacy_revision = _legacy_checkpoint_revision(marker.run_id)
        current = marker_fingerprint == source_fingerprint
        if legacy_source_revision is not None:
            current = current or (legacy_revision is not None and legacy_revision >= legacy_source_revision)
        if not current:
            return False
        part_indexes = {
            parsed.part_index
            for key in keys
            if (parsed := try_parse_partition_path(key)) is not None
            and parsed.layer == VEGETATION_PLANE_STREAM
            and parsed.kind == GAP_FILL_PARTITION_KIND
            and parsed.zoom == tier
            and parsed.day == day
        }
        if part_indexes != set(range(marker.part_count)):
            return False
    return True


def _retry_delay(attempt: int, base_seconds: float) -> float:
    return float(min(base_seconds * (2 ** (attempt - 1)), VEGETATION_FORWARD_MAX_RETRY_SECONDS))


async def _prepare_forward(
    session: AsyncSession,
    *,
    scope: VegetationForwardScope,
    max_attempts: int,
    retry_base_seconds: float,
    sleep: Callable[[float], Awaitable[None]],
) -> tuple[RegistrationSummary, int, tuple[VegetationPublicationTarget, ...]]:
    for attempt in range(1, max_attempts + 1):
        try:
            registration = await register_governed_forward_plane(
                session,
                cutoff_day=scope.cutoff_day,
                cell_days=scope.cell_days,
            )
            affected_days = await _affected_days(
                session,
                scope=scope,
                source_release_id=registration.plane.source_release_id,
            )
            fingerprints = {
                target.day: target
                for target in await vegetation_day_fingerprints(
                    session,
                    first_day=min(affected_days),
                    last_day=max(affected_days),
                )
            }
            missing_fingerprints = sorted(set(affected_days) - fingerprints.keys())
            if missing_fingerprints:
                raise VegetationForwardError(
                    "promoted vegetation days have no exact governed fingerprint: "
                    + ", ".join(day.isoformat() for day in missing_fingerprints)
                )
            affected_targets = tuple(fingerprints[day] for day in sorted(affected_days))
            source_revision = await _source_revision(session)
            await session.commit()
            return registration, source_revision, affected_targets
        except _TRANSIENT_DATABASE_ERRORS:
            await session.rollback()
            if attempt == max_attempts:
                raise
            await sleep(_retry_delay(attempt, retry_base_seconds))
        except Exception:
            await session.rollback()
            raise
    raise AssertionError("bounded preparation attempts exhausted without returning or raising")


async def _write_day_once(  # noqa: PLR0913 - one lane-day coordinate or injected seam per arg
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    source_fingerprint: str,
    lane_day_lock: VegetationLaneDayLock,
    availability_storage: AvailabilityStorage | None,
    availability: AvailabilityExtensionTally,
) -> VegetationForwardDayResult:
    """Publish one vegetation day through the shared lane-day contract, index entry included.

    `availability_storage` is threaded rather than defaulted away: this writer owns the vegetation
    lane's forward edge, so a day it publishes without an index entry is a day
    `PARQUET_COVERAGE_AUTHORITY=availability` withholds from the slider.
    """
    lane = LANE_REGISTRY[VEGETATION_PLANE_STREAM]
    try:
        async with lane_day_lock(session, _lane_day_lock_key(lane, day)) as granted:
            if not granted:
                return VegetationForwardDayResult(
                    day=day,
                    outcome="contended",
                    attempt_count=1,
                    detail="another writer holds this vegetation lane-day",
                )
            if _ladder_checkpoint_is_current(store, day=day, source_fingerprint=source_fingerprint):
                return VegetationForwardDayResult(day=day, outcome="checkpointed", attempt_count=1)
            outcome, _parts, rows, written_bytes, detail = await fill_one_lane_day(
                session,
                store,
                lane,
                day=day,
                run_id=_checkpoint_run_id(source_fingerprint),
                now=lambda: datetime.now(UTC),
                today=datetime.now(UTC).date(),
                lane_day_lock=unlocked_lane_day,
                vegetation_publication_barrier=unlocked_vegetation_publication_barrier,
                statement_timeout_seconds=VEGETATION_FORWARD_STATEMENT_TIMEOUT_SECONDS,
                availability_storage=availability_storage,
                availability_tally=availability,
            )
            if outcome != "written" or not _ladder_checkpoint_is_current(
                store,
                day=day,
                source_fingerprint=source_fingerprint,
            ):
                raise VegetationForwardError(
                    detail or f"vegetation {day.isoformat()} did not finish all four governed tiers"
                )
            return VegetationForwardDayResult(
                day=day,
                outcome="written",
                attempt_count=1,
                row_count=rows,
                byte_count=written_bytes,
                detail=detail,
            )
    finally:
        with suppress(Exception):
            await session.rollback()


async def _write_day_with_retry(  # noqa: PLR0913 - retry policy and injected seams form one bounded operation.
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    source_fingerprint: str,
    max_attempts: int,
    retry_base_seconds: float,
    lane_day_lock: VegetationLaneDayLock,
    sleep: Callable[[float], Awaitable[None]],
    availability_storage: AvailabilityStorage | None,
    availability: AvailabilityExtensionTally,
) -> VegetationForwardDayResult:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = await _write_day_once(
                session,
                store,
                day=day,
                source_fingerprint=source_fingerprint,
                lane_day_lock=lane_day_lock,
                availability_storage=availability_storage,
                availability=availability,
            )
        except Exception as error:
            last_error = error
        else:
            if result.outcome != "contended" or attempt == max_attempts:
                return VegetationForwardDayResult(
                    day=result.day,
                    outcome=result.outcome,
                    attempt_count=attempt,
                    row_count=result.row_count,
                    byte_count=result.byte_count,
                    detail=result.detail,
                )
        with suppress(Exception):
            await session.rollback()
        if attempt < max_attempts:
            await sleep(_retry_delay(attempt, retry_base_seconds))
    assert last_error is not None
    raise VegetationForwardError(
        f"vegetation {day.isoformat()} failed after {max_attempts} attempt(s): "
        f"{type(last_error).__name__}: {last_error}"
    ) from last_error


async def _defensive_enqueue(
    session: AsyncSession,
    store: ObjectStore,
    *,
    through_day: date,
) -> tuple[int, int]:
    """Revalidate the rolling source window and author durable work for every stale physical ladder."""
    first_day = through_day - timedelta(days=VEGETATION_PUBLICATION_LOOKBACK_DAYS)
    targets = await vegetation_day_fingerprints(session, first_day=first_day, last_day=through_day)
    if not targets:
        await session.rollback()
        return 0, 0
    await enqueue_vegetation_publication(session, targets)
    legacy_source_revision = await _source_revision(session)
    for target in targets:
        if _ladder_checkpoint_is_current(
            store,
            day=target.day,
            source_fingerprint=target.source_fingerprint,
            legacy_source_revision=legacy_source_revision,
        ):
            await acknowledge_vegetation_publication(session, target)
        else:
            await enqueue_vegetation_publication(session, (target,), force=True)
    if await vegetation_publication_is_fully_enrolled(session):
        await enqueue_vegetation_publication(
            session,
            await vegetation_day_fingerprints(session, last_day=through_day),
        )
    await session.commit()
    return len(targets), legacy_source_revision


async def _drain_pending_vegetation(  # noqa: PLR0913 - bounded drain policy and injected seams form one operation.
    session: AsyncSession,
    store: ObjectStore,
    *,
    through_day: date,
    defensive_day_count: int,
    source_revision: int,
    max_days_per_run: int,
    time_budget_seconds: float,
    max_attempts: int,
    retry_base_seconds: float,
    lane_day_lock: VegetationLaneDayLock,
    sleep: Callable[[float], Awaitable[None]],
    monotonic: Callable[[], float],
    availability_storage: AvailabilityStorage | None,
) -> VegetationPublicationDrainSummary:
    pending = await pending_vegetation_publication(session, limit=2_147_483_647)
    await session.rollback()
    # ONE TALLY PER RUN, and this drain IS the run: every vegetation day of every entry point flows
    # through it, so a tally minted above it would be per-caller rather than per-run.
    availability = AvailabilityExtensionTally()
    started_at = monotonic()
    results: list[VegetationForwardDayResult] = []
    stop_reason: ForwardStopReason = "complete"
    for target in pending:
        if monotonic() - started_at >= time_budget_seconds:
            stop_reason = "time_budget"
            break
        if len(results) >= max_days_per_run:
            stop_reason = "day_limit"
            break
        try:
            result = await _write_day_with_retry(
                session,
                store,
                day=target.day,
                source_fingerprint=target.source_fingerprint,
                max_attempts=max_attempts,
                retry_base_seconds=retry_base_seconds,
                lane_day_lock=lane_day_lock,
                sleep=sleep,
                availability_storage=availability_storage,
                availability=availability,
            )
        except Exception as error:
            await record_vegetation_publication_attempt(
                session,
                target,
                error=f"{type(error).__name__}: {error}",
            )
            await session.commit()
            raise
        await record_vegetation_publication_attempt(
            session,
            target,
            error="another writer holds this vegetation lane-day" if result.outcome == "contended" else None,
        )
        if result.outcome != "contended":
            if not _ladder_checkpoint_is_current(
                store,
                day=target.day,
                source_fingerprint=target.source_fingerprint,
            ):
                await session.rollback()
                raise VegetationForwardError(
                    f"vegetation {target.day.isoformat()} lost its verified marker before queue acknowledgement"
                )
            if not await acknowledge_vegetation_publication(session, target):
                await session.rollback()
                raise VegetationForwardError(
                    f"vegetation {target.day.isoformat()} advanced while fingerprint {target.source_fingerprint} "
                    "was being acknowledged"
                )
        await session.commit()
        results.append(result)
    remaining = await pending_vegetation_publication(session, limit=2_147_483_647)
    await session.rollback()
    if remaining and stop_reason == "complete":
        stop_reason = "day_limit"
    return VegetationPublicationDrainSummary(
        through_day=through_day,
        defensive_day_count=defensive_day_count,
        pending_day_count=len(pending),
        remaining_day_count=len(remaining),
        source_revision=source_revision,
        stop_reason=stop_reason,
        days=tuple(results),
        availability=availability,
    )


async def catch_up_vegetation_publication(  # noqa: PLR0913 - explicit cron bounds are the contract.
    session: AsyncSession,
    store: ObjectStore,
    *,
    through_day: date,
    max_days_per_run: int = VEGETATION_FORWARD_MAX_DAYS_PER_RUN,
    time_budget_seconds: float = VEGETATION_FORWARD_TIME_BUDGET_SECONDS,
    max_attempts: int = VEGETATION_FORWARD_MAX_ATTEMPTS,
    retry_base_seconds: float = VEGETATION_FORWARD_RETRY_BASE_SECONDS,
    lane_day_lock: VegetationLaneDayLock = postgres_lane_day_lock,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    availability_storage: AvailabilityStorage | None = None,
) -> VegetationPublicationDrainSummary:
    """Unconditionally revalidate 45 days, then fairly drain every durable pending day."""
    if max_days_per_run <= 0:
        raise ValueError("max_days_per_run must be positive")
    if time_budget_seconds <= 0:
        raise ValueError("time_budget_seconds must be positive")
    if not 1 <= max_attempts <= VEGETATION_FORWARD_MAX_ATTEMPTS:
        raise ValueError(f"max_attempts must be between 1 and {VEGETATION_FORWARD_MAX_ATTEMPTS}")
    if not 0 <= retry_base_seconds <= VEGETATION_FORWARD_MAX_RETRY_SECONDS:
        raise ValueError(f"retry_base_seconds must be between 0 and {VEGETATION_FORWARD_MAX_RETRY_SECONDS:g}")
    async with try_postgres_vegetation_publication_barrier(session) as granted:
        if granted is False:
            await session.rollback()
            raise VegetationForwardIncompleteError(
                "vegetation catch-up deferred because an exact audit or another publisher holds the source barrier"
            )
        changed_since = datetime.combine(
            through_day - timedelta(days=VEGETATION_PUBLICATION_LOOKBACK_DAYS),
            datetime.min.time(),
            tzinfo=UTC,
        )
        try:
            catch_up_scope = await changed_vegetation_forward_scope(
                session,
                since=changed_since,
                through_day=through_day,
            )
        except VegetationForwardError as error:
            if "no valid raw vegetation cell-day changed" not in str(error):
                raise
        else:
            await _prepare_forward(
                session,
                scope=catch_up_scope,
                max_attempts=max_attempts,
                retry_base_seconds=retry_base_seconds,
                sleep=sleep,
            )
        defensive_days, source_revision = await _defensive_enqueue(session, store, through_day=through_day)
        return await _drain_pending_vegetation(
            session,
            store,
            through_day=through_day,
            defensive_day_count=defensive_days,
            source_revision=source_revision,
            max_days_per_run=max_days_per_run,
            time_budget_seconds=time_budget_seconds,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
            lane_day_lock=lane_day_lock,
            sleep=sleep,
            monotonic=monotonic,
            availability_storage=availability_storage,
        )


async def forward_vegetation_scope(  # noqa: PLR0913 - explicit bounds and seams are the orchestration contract.
    session: AsyncSession,
    store: ObjectStore,
    scope: VegetationForwardScope,
    *,
    max_days_per_run: int = VEGETATION_FORWARD_MAX_DAYS_PER_RUN,
    time_budget_seconds: float = VEGETATION_FORWARD_TIME_BUDGET_SECONDS,
    max_attempts: int = VEGETATION_FORWARD_MAX_ATTEMPTS,
    retry_base_seconds: float = VEGETATION_FORWARD_RETRY_BASE_SECONDS,
    lane_day_lock: VegetationLaneDayLock = postgres_lane_day_lock,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    availability_storage: AvailabilityStorage | None = None,
) -> VegetationForwardSummary:
    """Promote one exact NDVI scope, then publish a bounded resumable slice of its affected days."""
    if max_days_per_run <= 0:
        raise ValueError("max_days_per_run must be positive")
    if time_budget_seconds <= 0:
        raise ValueError("time_budget_seconds must be positive")
    if not 1 <= max_attempts <= VEGETATION_FORWARD_MAX_ATTEMPTS:
        raise ValueError(f"max_attempts must be between 1 and {VEGETATION_FORWARD_MAX_ATTEMPTS}")
    if not 0 <= retry_base_seconds <= VEGETATION_FORWARD_MAX_RETRY_SECONDS:
        raise ValueError(f"retry_base_seconds must be between 0 and {VEGETATION_FORWARD_MAX_RETRY_SECONDS:g}")
    async with try_postgres_vegetation_publication_barrier(session) as granted:
        if granted is False:
            await session.rollback()
            raise VegetationForwardIncompleteError(
                "raw vegetation persisted; governed publication deferred behind the exact-audit barrier"
            )
        registration, registration_revision, _affected_targets = await _prepare_forward(
            session,
            scope=scope,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
            sleep=sleep,
        )
        defensive_days, source_revision = await _defensive_enqueue(session, store, through_day=scope.cutoff_day)
        drain = await _drain_pending_vegetation(
            session,
            store,
            through_day=scope.cutoff_day,
            defensive_day_count=defensive_days,
            source_revision=max(registration_revision, source_revision),
            max_days_per_run=max_days_per_run,
            time_budget_seconds=time_budget_seconds,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
            lane_day_lock=lane_day_lock,
            sleep=sleep,
            monotonic=monotonic,
            availability_storage=availability_storage,
        )
    return VegetationForwardSummary(
        scope=scope,
        registration=registration,
        source_revision=drain.source_revision,
        affected_day_count=drain.pending_day_count,
        examined_day_count=len(drain.days),
        stop_reason=drain.stop_reason,
        days=drain.days,
        availability=drain.availability,
    )


async def forward_persisted_vegetation(  # noqa: PLR0913 - explicit bounds and seams are the orchestration contract.
    session: AsyncSession,
    store: ObjectStore,
    writes: Sequence[FeatureWrite],
    *,
    max_days_per_run: int = VEGETATION_FORWARD_MAX_DAYS_PER_RUN,
    time_budget_seconds: float = VEGETATION_FORWARD_TIME_BUDGET_SECONDS,
    max_attempts: int = VEGETATION_FORWARD_MAX_ATTEMPTS,
    retry_base_seconds: float = VEGETATION_FORWARD_RETRY_BASE_SECONDS,
    lane_day_lock: VegetationLaneDayLock = postgres_lane_day_lock,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    availability_storage: AvailabilityStorage | None = None,
) -> VegetationForwardSummary:
    """Promote the writes accepted by ingestion and publish their exact governed day scope."""
    return await forward_vegetation_scope(
        session,
        store,
        vegetation_forward_scope(writes),
        max_days_per_run=max_days_per_run,
        time_budget_seconds=time_budget_seconds,
        max_attempts=max_attempts,
        retry_base_seconds=retry_base_seconds,
        lane_day_lock=lane_day_lock,
        sleep=sleep,
        monotonic=monotonic,
        availability_storage=availability_storage,
    )


async def forward_changed_vegetation(  # noqa: PLR0913 - CLI bounds are explicit operator controls.
    session: AsyncSession,
    store: ObjectStore,
    *,
    since: datetime,
    through_day: date,
    max_days_per_run: int = VEGETATION_FORWARD_MAX_DAYS_PER_RUN,
    time_budget_seconds: float = VEGETATION_FORWARD_TIME_BUDGET_SECONDS,
    max_attempts: int = VEGETATION_FORWARD_MAX_ATTEMPTS,
    retry_base_seconds: float = VEGETATION_FORWARD_RETRY_BASE_SECONDS,
    availability_storage: AvailabilityStorage | None = None,
) -> VegetationForwardSummary:
    """Promote and publish raw vegetation changes without repeating upstream sampling."""
    scope = await changed_vegetation_forward_scope(session, since=since, through_day=through_day)
    return await forward_vegetation_scope(
        session,
        store,
        scope,
        max_days_per_run=max_days_per_run,
        time_budget_seconds=time_budget_seconds,
        max_attempts=max_attempts,
        retry_base_seconds=retry_base_seconds,
        availability_storage=availability_storage,
    )


def bind_vegetation_forward_writer(
    session: AsyncSession,
    *,
    store: ObjectStore | None = None,
    availability_storage: AvailabilityStorage | None = None,
) -> Callable[[Sequence[FeatureWrite]], Awaitable[Mapping[str, int]]]:
    """Bind the ingest session to the post-persistence callback, constructing object storage lazily.

    The availability adapter is built from the SAME settings the store is, at the same moment and
    with the same failure mode -- neither opens a socket -- so a day this callback publishes reaches
    the lane's index rather than being withheld the next time coverage is asked for it.
    """
    resolved_store = store
    resolved_availability = availability_storage

    async def forward(writes: Sequence[FeatureWrite]) -> Mapping[str, int]:
        nonlocal resolved_store, resolved_availability
        if resolved_store is None:
            resolved_store = ObjectStore.from_settings()
        if resolved_availability is None:
            resolved_availability = BotoAvailabilityStorage.from_settings()
        summary = await forward_persisted_vegetation(
            session,
            resolved_store,
            writes,
            availability_storage=resolved_availability,
        )
        if summary.stop_reason != "complete" or summary.contended_day_count:
            raise VegetationForwardIncompleteError(
                f"vegetation forward publication stopped as {summary.stop_reason} with "
                f"{summary.contended_day_count} contended and {summary.to_details()['pending_days']} pending day(s); "
                "completion markers retain resume state"
            )
        return summary.to_details()

    return forward


__all__ = [
    "VEGETATION_FORWARD_CHECKPOINT_PREFIX",
    "VEGETATION_FORWARD_LEGACY_CHECKPOINT_PREFIX",
    "VEGETATION_FORWARD_MAX_ATTEMPTS",
    "VEGETATION_FORWARD_MAX_DAYS_PER_RUN",
    "VEGETATION_FORWARD_TIME_BUDGET_SECONDS",
    "VegetationForwardDayResult",
    "VegetationForwardError",
    "VegetationForwardIncompleteError",
    "VegetationForwardScope",
    "VegetationForwardSummary",
    "VegetationPublicationDrainSummary",
    "bind_vegetation_forward_writer",
    "catch_up_vegetation_publication",
    "changed_vegetation_forward_scope",
    "forward_changed_vegetation",
    "forward_persisted_vegetation",
    "forward_vegetation_scope",
    "vegetation_forward_scope",
]
