"""Propagate settled vegetation absence evidence across the complete zoom ladder."""

from __future__ import annotations

import time
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.paths import PartitionDayStatus, PartitionKind, partition_day_statuses
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS, ZoomTier
from agri_data_service.pipeline.parquet.gap_fill import _lane_day_lock_key, postgres_lane_day_lock
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.vegetation_source import fetch_source_cell_days
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_STREAM

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

ABSENCE_WRITE_ATTEMPTS: Final = 3
VEGETATION_KIND: Final[PartitionKind] = "observed"
BASE_ZOOM: Final[ZoomTier] = ZOOM_TIERS[-1]


@dataclass(frozen=True, slots=True)
class VegetationAbsenceLadderReport:
    """Measured effect of one bounded, resumable absence propagation pass."""

    dry_run: bool
    eligible_days: int
    remaining_days: int
    completed_days: int
    contended_days: int
    already_written_markers: int
    would_write_markers: int
    written_markers: int
    failures: tuple[str, ...]

    @property
    def is_clean(self) -> bool:
        return (
            not self.failures
            and self.contended_days == 0
            and self.remaining_days == 0
            and (not self.dry_run or self.would_write_markers == 0)
        )

    def to_summary(self) -> dict[str, object]:
        return {
            "already_written_markers": self.already_written_markers,
            "clean": self.is_clean,
            "completed_days": self.completed_days,
            "contended_days": self.contended_days,
            "eligible_days": self.eligible_days,
            "remaining_days": self.remaining_days,
            "dry_run": self.dry_run,
            "failure_count": len(self.failures),
            "failures": list(self.failures),
            "would_write_markers": self.would_write_markers,
            "written_markers": self.written_markers,
        }


@dataclass(frozen=True, slots=True)
class VegetationAbsenceRetractionReport:
    """Measured effect of one exact, resumable unsettled-absence retraction."""

    dry_run: bool
    requested_days: int
    eligible_days: int
    already_missing_days: int
    completed_days: int
    contended_days: int
    would_remove_markers: int
    removed_markers: int
    failures: tuple[str, ...]

    @property
    def is_clean(self) -> bool:
        return not self.failures and self.contended_days == 0 and (not self.dry_run or self.would_remove_markers == 0)

    def to_summary(self) -> dict[str, object]:
        return {
            "already_missing_days": self.already_missing_days,
            "clean": self.is_clean,
            "completed_days": self.completed_days,
            "contended_days": self.contended_days,
            "dry_run": self.dry_run,
            "eligible_days": self.eligible_days,
            "failure_count": len(self.failures),
            "failures": list(self.failures),
            "removed_markers": self.removed_markers,
            "requested_days": self.requested_days,
            "would_remove_markers": self.would_remove_markers,
        }


def _retry[T](
    operation: Callable[[], T],
    *,
    attempts: int,
    sleeper: Callable[[float], None],
) -> T:
    if attempts < 1:
        raise ValueError("attempts must be at least one")
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception:
            if attempt == attempts:
                raise
            sleeper(0.25 * 2 ** (attempt - 1))
    raise AssertionError("retry loop exhausted without returning or raising")


def _tier_statuses(
    store: ObjectStore,
    *,
    zoom: ZoomTier,
    first_day: date,
    last_day: date,
) -> dict[date, PartitionDayStatus]:
    keys: list[str] = []
    if first_day.year == last_day.year and first_day.month == last_day.month:
        keys.extend(
            store.list_partition_keys(
                VEGETATION_PLANE_STREAM,
                VEGETATION_KIND,
                zoom,
                year=first_day.year,
                month=first_day.month,
            )
        )
    else:
        for year in range(first_day.year, last_day.year + 1):
            keys.extend(store.list_partition_keys(VEGETATION_PLANE_STREAM, VEGETATION_KIND, zoom, year=year))
    return partition_day_statuses(
        layer=VEGETATION_PLANE_STREAM,
        kind=VEGETATION_KIND,
        zoom=zoom,
        first_day=first_day,
        last_day=last_day,
        keys=keys,
    )


def _tier_status_for_day(store: ObjectStore, *, zoom: ZoomTier, day: date) -> PartitionDayStatus:
    """Re-list one day at one rung and return its current state."""
    return _tier_statuses(store, zoom=zoom, first_day=day, last_day=day)[day]


async def propagate_vegetation_absence_ladders(  # noqa: PLR0912, PLR0913, PLR0915
    session: AsyncSession,
    store: ObjectStore,
    *,
    cell_ids: Sequence[UUID],
    first_day: date,
    last_day: date,
    max_days: int | None = None,
    dry_run: bool = True,
    attempts: int = ABSENCE_WRITE_ATTEMPTS,
    progress: Callable[[dict[str, object]], None] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> VegetationAbsenceLadderReport:
    """Copy source-verified z13 absence evidence to z9/z5/z0 under the lane-day lock."""
    if last_day < first_day:
        raise ValueError("last day precedes first day")
    if max_days is not None and max_days < 1:
        raise ValueError("max days must be at least one")

    source_rows = await fetch_source_cell_days(
        session,
        cell_ids=cell_ids,
        first_day=first_day,
        last_day=last_day,
    )
    await session.rollback()
    source_days = frozenset(row.observed_day for row in source_rows)
    statuses = {
        zoom: _retry(
            partial(_tier_statuses, store, zoom=zoom, first_day=first_day, last_day=last_day),
            attempts=attempts,
            sleeper=sleeper,
        )
        for zoom in ZOOM_TIERS
    }
    eligible = tuple(
        day
        for day, status in statuses[BASE_ZOOM].items()
        if status == "absent"
        and day not in source_days
        and any(statuses[zoom][day] != "absent" for zoom in DERIVED_ZOOM_TIERS)
    )
    selected = eligible if max_days is None else eligible[:max_days]
    remaining = len(eligible) - len(selected)
    lane = LANE_REGISTRY[VEGETATION_PLANE_STREAM]
    completed = 0
    contended = 0
    already = 0
    would_write = 0
    written = 0
    failures: list[str] = []

    for index, day in enumerate(selected, start=1):
        async with postgres_lane_day_lock(session, _lane_day_lock_key(lane, day)) as granted:
            if not granted:
                contended += 1
            else:
                try:
                    current_source = await fetch_source_cell_days(
                        session,
                        cell_ids=cell_ids,
                        first_day=day,
                        last_day=day,
                    )
                    await session.rollback()
                    if current_source:
                        raise ValueError("governed source gained rows after the opening census")
                    current_statuses = {
                        zoom: _retry(
                            partial(_tier_status_for_day, store, zoom=zoom, day=day),
                            attempts=attempts,
                            sleeper=sleeper,
                        )
                        for zoom in ZOOM_TIERS
                    }
                    if current_statuses[BASE_ZOOM] != "absent":
                        raise ValueError(f"z13 status changed to {current_statuses[BASE_ZOOM]} after the census")
                    evidence = _retry(
                        partial(
                            store.read_absence,
                            VEGETATION_PLANE_STREAM,
                            VEGETATION_KIND,
                            BASE_ZOOM,
                            day,
                        ),
                        attempts=attempts,
                        sleeper=sleeper,
                    )
                    if evidence is None:
                        raise ValueError("z13 absence evidence disappeared after the census")
                    for zoom in DERIVED_ZOOM_TIERS:
                        status = current_statuses[zoom]
                        if status == "absent":
                            existing = _retry(
                                partial(
                                    store.read_absence,
                                    VEGETATION_PLANE_STREAM,
                                    VEGETATION_KIND,
                                    zoom,
                                    day,
                                ),
                                attempts=attempts,
                                sleeper=sleeper,
                            )
                            if existing != evidence:
                                raise ValueError(f"z{zoom} absence evidence differs from z13")
                            already += 1
                            continue
                        if status != "missing":
                            raise ValueError(f"z{zoom} status is {status}, not missing or absent")
                        if dry_run:
                            would_write += 1
                            continue
                        _retry(
                            partial(
                                store.write_absence,
                                evidence,
                                layer=VEGETATION_PLANE_STREAM,
                                kind=VEGETATION_KIND,
                                zoom=zoom,
                                day=day,
                            ),
                            attempts=attempts,
                            sleeper=sleeper,
                        )
                        written += 1
                    completed += 1
                except Exception as error:
                    failures.append(f"{day.isoformat()}: {type(error).__name__}: {error}")
                finally:
                    await session.rollback()
        if progress is not None:
            progress(
                {
                    "completed_days": completed,
                    "day": day.isoformat(),
                    "eligible_days": len(eligible),
                    "failure_count": len(failures),
                    "selected_days": len(selected),
                    "visited_days": index,
                    "written_markers": written,
                }
            )

    source_rows_after = await fetch_source_cell_days(
        session,
        cell_ids=cell_ids,
        first_day=first_day,
        last_day=last_day,
    )
    await session.rollback()
    if source_rows_after != source_rows:
        failures.append("governed source keys or release counts changed between the opening and closing census")

    return VegetationAbsenceLadderReport(
        dry_run=dry_run,
        eligible_days=len(eligible),
        remaining_days=remaining,
        completed_days=completed,
        contended_days=contended,
        already_written_markers=already,
        would_write_markers=would_write,
        written_markers=written,
        failures=tuple(failures),
    )


async def retract_unsettled_vegetation_absences(  # noqa: PLR0912, PLR0913, PLR0915
    session: AsyncSession,
    store: ObjectStore,
    *,
    cell_ids: Sequence[UUID],
    days: Sequence[date],
    coverage_last_day: date,
    dry_run: bool = True,
    attempts: int = ABSENCE_WRITE_ATTEMPTS,
    progress: Callable[[dict[str, object]], None] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> VegetationAbsenceRetractionReport:
    """Retract exact source-empty absence ladders beyond the settled cutoff."""
    requested = tuple(days)
    if not requested:
        raise ValueError("at least one day is required")
    if requested != tuple(sorted(set(requested))):
        raise ValueError("days must be sorted and unique")
    if any(day <= coverage_last_day for day in requested):
        raise ValueError("every retraction day must be after coverage-last-day")

    source_rows = await fetch_source_cell_days(
        session,
        cell_ids=cell_ids,
        first_day=requested[0],
        last_day=requested[-1],
    )
    await session.rollback()
    target_source_days = {row.observed_day for row in source_rows if row.observed_day in requested}
    lane = LANE_REGISTRY[VEGETATION_PLANE_STREAM]
    eligible = 0
    already_missing = 0
    completed = 0
    contended = 0
    would_remove = 0
    removed = 0
    failures: list[str] = []

    for index, day in enumerate(requested, start=1):
        if day in target_source_days:
            failures.append(f"{day.isoformat()}: governed source contains rows")
            continue
        async with postgres_lane_day_lock(session, _lane_day_lock_key(lane, day)) as granted:
            if not granted:
                contended += 1
                continue
            try:
                current_source = await fetch_source_cell_days(
                    session,
                    cell_ids=cell_ids,
                    first_day=day,
                    last_day=day,
                )
                await session.rollback()
                if current_source:
                    raise ValueError("governed source gained rows after the opening census")
                current_statuses = {
                    zoom: _retry(
                        partial(_tier_status_for_day, store, zoom=zoom, day=day),
                        attempts=attempts,
                        sleeper=sleeper,
                    )
                    for zoom in ZOOM_TIERS
                }
                unsafe = {
                    zoom: status for zoom, status in current_statuses.items() if status not in {"missing", "absent"}
                }
                if unsafe:
                    raise ValueError(f"refusing non-absence state: {unsafe}")
                present = tuple(zoom for zoom in reversed(ZOOM_TIERS) if current_statuses[zoom] == "absent")
                if not present:
                    already_missing += 1
                    completed += 1
                    continue
                eligible += 1
                evidence = [
                    _retry(
                        partial(store.read_absence, VEGETATION_PLANE_STREAM, VEGETATION_KIND, zoom, day),
                        attempts=attempts,
                        sleeper=sleeper,
                    )
                    for zoom in present
                ]
                if evidence[0] is None or any(item != evidence[0] for item in evidence[1:]):
                    raise ValueError("absence evidence is missing or differs across present tiers")
                if dry_run:
                    would_remove += len(present)
                    completed += 1
                    continue
                for zoom in present:
                    _retry(
                        partial(store.clear_absence_marker, VEGETATION_PLANE_STREAM, VEGETATION_KIND, zoom, day),
                        attempts=attempts,
                        sleeper=sleeper,
                    )
                    status = _retry(
                        partial(_tier_status_for_day, store, zoom=zoom, day=day),
                        attempts=attempts,
                        sleeper=sleeper,
                    )
                    if status != "missing":
                        raise ValueError(f"z{zoom} remained {status} after absence retraction")
                    removed += 1
                completed += 1
            except Exception as error:
                failures.append(f"{day.isoformat()}: {type(error).__name__}: {error}")
            finally:
                await session.rollback()
        if progress is not None:
            progress(
                {
                    "completed_days": completed,
                    "day": day.isoformat(),
                    "failure_count": len(failures),
                    "removed_markers": removed,
                    "requested_days": len(requested),
                    "visited_days": index,
                }
            )

    source_rows_after = await fetch_source_cell_days(
        session,
        cell_ids=cell_ids,
        first_day=requested[0],
        last_day=requested[-1],
    )
    await session.rollback()
    target_source_days_after = {row.observed_day for row in source_rows_after if row.observed_day in requested}
    if target_source_days_after != target_source_days:
        failures.append("governed source days changed between the opening and closing census")

    return VegetationAbsenceRetractionReport(
        dry_run=dry_run,
        requested_days=len(requested),
        eligible_days=eligible,
        already_missing_days=already_missing,
        completed_days=completed,
        contended_days=contended,
        would_remove_markers=would_remove,
        removed_markers=removed,
        failures=tuple(failures),
    )


__all__ = [
    "ABSENCE_WRITE_ATTEMPTS",
    "VegetationAbsenceLadderReport",
    "VegetationAbsenceRetractionReport",
    "propagate_vegetation_absence_ladders",
    "retract_unsettled_vegetation_absences",
]
