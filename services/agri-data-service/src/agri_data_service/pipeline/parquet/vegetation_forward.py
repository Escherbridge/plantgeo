"""Promote persisted NDVI cells and author their affected governed days to the full Parquet ladder."""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final, Literal

from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from agri_data_service.db.sql_queries import load_query_sql
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
from agri_data_service.pipeline.parquet.gap_fill import (
    GAP_FILL_PARTITION_KIND,
    _lane_day_lock_key,
    fill_one_lane_day,
    postgres_lane_day_lock,
    statement_timeout,
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

    VegetationLaneDayLock = Callable[[AsyncSession, str], AbstractAsyncContextManager[bool]]

_AFFECTED_DAYS_SQL: Final = text(load_query_sql("pipeline/vegetation_forward_affected_days.sql"))
_SOURCE_REVISION_SQL: Final = text(load_query_sql("pipeline/vegetation_forward_revision.sql"))

VEGETATION_FORWARD_CHECKPOINT_PREFIX: Final = "vegetation-forward-v1:"
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


def _checkpoint_run_id(source_revision: int) -> str:
    return f"{VEGETATION_FORWARD_CHECKPOINT_PREFIX}{source_revision}"


def _checkpoint_revision(run_id: str) -> int | None:
    if not run_id.startswith(VEGETATION_FORWARD_CHECKPOINT_PREFIX):
        return None
    try:
        revision = int(run_id.removeprefix(VEGETATION_FORWARD_CHECKPOINT_PREFIX))
    except ValueError:
        return None
    return revision if revision > 0 else None


def _ladder_checkpoint_is_current(store: ObjectStore, *, day: date, source_revision: int) -> bool:
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
        marker_revision = _checkpoint_revision(marker.run_id)
        if marker_revision is None or marker_revision < source_revision:
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
) -> tuple[RegistrationSummary, int, tuple[date, ...]]:
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
            await session.commit()
            await session.execute(statement_timeout(VEGETATION_FORWARD_STATEMENT_TIMEOUT_SECONDS))
            source_revision = await _source_revision(session)
            await session.rollback()
            return registration, source_revision, affected_days
        except _TRANSIENT_DATABASE_ERRORS:
            await session.rollback()
            if attempt == max_attempts:
                raise
            await sleep(_retry_delay(attempt, retry_base_seconds))
        except Exception:
            await session.rollback()
            raise
    raise AssertionError("bounded preparation attempts exhausted without returning or raising")


async def _write_day_once(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    source_revision: int,
    lane_day_lock: VegetationLaneDayLock,
) -> VegetationForwardDayResult:
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
            if _ladder_checkpoint_is_current(store, day=day, source_revision=source_revision):
                return VegetationForwardDayResult(day=day, outcome="checkpointed", attempt_count=1)
            outcome, _parts, rows, written_bytes, detail = await fill_one_lane_day(
                session,
                store,
                lane,
                day=day,
                run_id=_checkpoint_run_id(source_revision),
                now=lambda: datetime.now(UTC),
                today=datetime.now(UTC).date(),
                lane_day_lock=unlocked_lane_day,
                statement_timeout_seconds=VEGETATION_FORWARD_STATEMENT_TIMEOUT_SECONDS,
            )
            if outcome != "written" or not _ladder_checkpoint_is_current(
                store,
                day=day,
                source_revision=source_revision,
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
    source_revision: int,
    max_attempts: int,
    retry_base_seconds: float,
    lane_day_lock: VegetationLaneDayLock,
    sleep: Callable[[float], Awaitable[None]],
) -> VegetationForwardDayResult:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = await _write_day_once(
                session,
                store,
                day=day,
                source_revision=source_revision,
                lane_day_lock=lane_day_lock,
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
) -> VegetationForwardSummary:
    """Promote one persisted NDVI selection, then publish a bounded resumable slice of its affected days."""
    if max_days_per_run <= 0:
        raise ValueError("max_days_per_run must be positive")
    if time_budget_seconds <= 0:
        raise ValueError("time_budget_seconds must be positive")
    if not 1 <= max_attempts <= VEGETATION_FORWARD_MAX_ATTEMPTS:
        raise ValueError(f"max_attempts must be between 1 and {VEGETATION_FORWARD_MAX_ATTEMPTS}")
    if not 0 <= retry_base_seconds <= VEGETATION_FORWARD_MAX_RETRY_SECONDS:
        raise ValueError(f"retry_base_seconds must be between 0 and {VEGETATION_FORWARD_MAX_RETRY_SECONDS:g}")
    scope = vegetation_forward_scope(writes)
    registration, source_revision, affected_days = await _prepare_forward(
        session,
        scope=scope,
        max_attempts=max_attempts,
        retry_base_seconds=retry_base_seconds,
        sleep=sleep,
    )
    started_at = monotonic()
    results: list[VegetationForwardDayResult] = []
    examined = 0
    noncheckpointed = 0
    stop_reason: ForwardStopReason = "complete"
    for day in affected_days:
        if monotonic() - started_at >= time_budget_seconds:
            stop_reason = "time_budget"
            break
        if noncheckpointed >= max_days_per_run:
            stop_reason = "day_limit"
            break
        examined += 1
        result = await _write_day_with_retry(
            session,
            store,
            day=day,
            source_revision=source_revision,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
            lane_day_lock=lane_day_lock,
            sleep=sleep,
        )
        results.append(result)
        if result.outcome != "checkpointed":
            noncheckpointed += 1
    return VegetationForwardSummary(
        scope=scope,
        registration=registration,
        source_revision=source_revision,
        affected_day_count=len(affected_days),
        examined_day_count=examined,
        stop_reason=stop_reason,
        days=tuple(results),
    )


def bind_vegetation_forward_writer(
    session: AsyncSession,
    *,
    store: ObjectStore | None = None,
) -> Callable[[Sequence[FeatureWrite]], Awaitable[Mapping[str, int]]]:
    """Bind the ingest session to the post-persistence callback, constructing object storage lazily."""
    resolved_store = store

    async def forward(writes: Sequence[FeatureWrite]) -> Mapping[str, int]:
        nonlocal resolved_store
        if resolved_store is None:
            resolved_store = ObjectStore.from_settings()
        summary = await forward_persisted_vegetation(session, resolved_store, writes)
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
    "VEGETATION_FORWARD_MAX_ATTEMPTS",
    "VEGETATION_FORWARD_MAX_DAYS_PER_RUN",
    "VEGETATION_FORWARD_TIME_BUDGET_SECONDS",
    "VegetationForwardDayResult",
    "VegetationForwardError",
    "VegetationForwardIncompleteError",
    "VegetationForwardScope",
    "VegetationForwardSummary",
    "bind_vegetation_forward_writer",
    "forward_persisted_vegetation",
    "vegetation_forward_scope",
]
