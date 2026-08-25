"""The four-state resolver: which state one day is in, and the rows behind it.

Layer L4. Everything here is synchronous and bounded; the routes run it off the event loop.
See `AGENTS.md` in this directory for why a conflict and an unfinished export are not states.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.absence import GovernedAbsence, GovernedAbsenceError
from agri_data_service.foundation.parquet.paths import (
    absence_marker_path,
    completed_partition_days,
    try_parse_absence_marker_path,
    try_parse_partition_path,
)
from agri_data_service.interface.http import faults
from agri_data_service.interface.http.warehouse_reader import RowRead, day_of_part_key, part_keys_for_day
from agri_data_service.interface.http.wire import (
    AbsenceEvidence,
    DayEnvelope,
    DayNotWritten,
    GovernedAbsenceDay,
    LaneNeverWritten,
    PublishedDay,
    ServedRow,
)

if TYPE_CHECKING:
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.interface.http.request_params import ReadScope
    from agri_data_service.interface.http.warehouse_reader import PartitionRowReader, WarehouseListing

#: Rows one day read may return before it reports itself truncated.
DAY_ROW_BUDGET: Final = 40_000

#: Rows one WINDOW read may return in total, shared across its days in ascending order. The window
#: is answered by ONE scan, so the budget is the window's and not each day's.
WINDOW_ROW_BUDGET: Final = 120_000

#: How far back a release resolution walks before it reports that no release covers the day. Twelve
#: years reaches every lane's history floor except fire-detections, which is not a release series.
RELEASE_LOOKBACK_YEARS: Final = 12

#: The month a year rolls over on, named so the month walk below is not a bare literal.
DECEMBER: Final = 12


@dataclass(frozen=True, slots=True)
class DayStatusSets:
    """One tier's days, sorted into the four things a listing can say about them."""

    data: frozenset[date]
    absent: frozenset[date]
    conflict: frozenset[date]
    incomplete: frozenset[date]

    @property
    def resolvable(self) -> frozenset[date]:
        """Days a release resolution may land on: served, deliberately empty, or refused out loud."""
        return self.data | self.absent | self.conflict


def day_status_sets(keys: tuple[str, ...], *, layer: str, kind: PartitionKind, tier: ZoomTier) -> DayStatusSets:
    """Classify every day one listing mentions, applying `partition_day_statuses`' own rules to a whole tier."""
    part_days: set[date] = set()
    absent_days: set[date] = set()
    for key in keys:
        partition = try_parse_partition_path(key)
        if partition is not None and (partition.layer, partition.kind, partition.zoom) == (layer, kind, tier):
            part_days.add(partition.day)
            continue
        marker = try_parse_absence_marker_path(key)
        if marker is not None and (marker.layer, marker.kind, marker.zoom) == (layer, kind, tier):
            absent_days.add(marker.day)
    complete_days = completed_partition_days(keys, layer=layer, kind=kind, zoom=tier)
    conflict = part_days & absent_days
    return DayStatusSets(
        data=frozenset((part_days & complete_days) - conflict),
        absent=frozenset(absent_days - conflict),
        conflict=frozenset(conflict),
        incomplete=frozenset(part_days - complete_days - conflict),
    )


def resolve_day(
    listing: WarehouseListing,
    reader: PartitionRowReader,
    *,
    scope: ReadScope,
    day: date,
) -> DayEnvelope:
    """Answer one named day: the four states, or a refusal when the warehouse cannot state one."""
    month_keys = listing.list_keys(scope.layer, scope.kind, scope.tier, year=day.year, month=day.month)
    statuses = day_status_sets(month_keys, layer=scope.layer, kind=scope.kind, tier=scope.tier)
    if day in statuses.conflict:
        raise faults.day_conflict(layer=scope.layer, day=day.isoformat())
    if day in statuses.incomplete:
        raise faults.day_incomplete(layer=scope.layer, day=day.isoformat())
    if day in statuses.absent:
        return GovernedAbsenceDay(
            requested_day=day,
            served_day=day,
            absence=read_absence_evidence(listing, scope=scope, day=day),
        )
    if day in statuses.data:
        return _published(reader, scope=scope, keys=month_keys, requested_day=day, served_day=day)
    # A non-empty month listing already proves the lane has written SOMETHING at this tier, so the
    # whole-tier probe below is paid only by a request that landed outside every written month.
    if month_keys or listing.list_keys(scope.layer, scope.kind, scope.tier):
        return DayNotWritten(requested_day=day)
    return LaneNeverWritten(requested_day=day)


def resolve_window(
    listing: WarehouseListing,
    reader: PartitionRowReader,
    *,
    scope: ReadScope,
    first_day: date,
    last_day: date,
) -> tuple[DayEnvelope, ...]:
    """Answer EVERY day in the closed range, ascending; a gap day is stated, never omitted."""
    keys = _keys_for_months(listing, scope=scope, first_day=first_day, last_day=last_day)
    statuses = day_status_sets(keys, layer=scope.layer, kind=scope.kind, tier=scope.tier)
    span = tuple(first_day + timedelta(days=offset) for offset in range((last_day - first_day).days + 1))
    for day in span:
        if day in statuses.conflict:
            raise faults.day_conflict(layer=scope.layer, day=day.isoformat())
        if day in statuses.incomplete:
            raise faults.day_incomplete(layer=scope.layer, day=day.isoformat())
    published_days = tuple(day for day in span if day in statuses.data)
    rows_by_day, truncated_from = _window_rows(reader, scope=scope, keys=keys, published_days=published_days)
    lane_written = bool(keys) or bool(listing.list_keys(scope.layer, scope.kind, scope.tier))
    return tuple(
        _window_envelope(
            listing,
            scope=scope,
            day=day,
            statuses=statuses,
            rows_by_day=rows_by_day,
            truncated_from=truncated_from,
            lane_written=lane_written,
        )
        for day in span
    )


def resolve_release(
    listing: WarehouseListing,
    reader: PartitionRowReader,
    *,
    scope: ReadScope,
    as_of: date,
) -> DayEnvelope:
    """Answer with the newest release at or before `as_of`, reported at the release's OWN day."""
    for year in range(as_of.year, as_of.year - RELEASE_LOOKBACK_YEARS, -1):
        keys = listing.list_keys(scope.layer, scope.kind, scope.tier, year=year)
        statuses = day_status_sets(keys, layer=scope.layer, kind=scope.kind, tier=scope.tier)
        candidates = tuple(day for day in statuses.resolvable if day <= as_of)
        if not candidates:
            continue
        served_day = max(candidates)
        if served_day in statuses.conflict:
            raise faults.day_conflict(layer=scope.layer, day=served_day.isoformat())
        if served_day in statuses.absent:
            return GovernedAbsenceDay(
                requested_day=as_of,
                served_day=served_day,
                absence=read_absence_evidence(listing, scope=scope, day=served_day),
            )
        return _published(reader, scope=scope, keys=keys, requested_day=as_of, served_day=served_day)
    if listing.list_keys(scope.layer, scope.kind, scope.tier):
        return DayNotWritten(requested_day=as_of)
    return LaneNeverWritten(requested_day=as_of)


def read_absence_evidence(listing: WarehouseListing, *, scope: ReadScope, day: date) -> AbsenceEvidence:
    """Decode one governed-absence marker; an absence without its evidence is not served as one."""
    key = absence_marker_path(scope.layer, scope.kind, scope.tier, day)
    payload = listing.read_object(key)
    if payload is None:
        raise faults.ServingRefusalError(
            "absence_marker_unreadable",
            f"{scope.layer} {day.isoformat()} was listed as a governed absence and its marker is no longer "
            "readable; an absence with no evidence is indistinguishable from a silent failure",
            status=faults.HTTP_SERVICE_UNAVAILABLE,
        )
    try:
        absence = GovernedAbsence.from_json_bytes(payload)
    except GovernedAbsenceError as exc:
        raise faults.ServingRefusalError(
            "absence_marker_undecodable",
            f"{scope.layer} {day.isoformat()} carries a governed-absence marker this plane cannot decode: {exc}",
            status=faults.HTTP_SERVICE_UNAVAILABLE,
        ) from exc
    return AbsenceEvidence(
        reason=absence.reason,
        upstream_response=absence.upstream_response,
        recorded_at=absence.recorded_at,
        run_id=absence.run_id,
    )


def _published(
    reader: PartitionRowReader,
    *,
    scope: ReadScope,
    keys: tuple[str, ...],
    requested_day: date,
    served_day: date,
) -> PublishedDay:
    """Read one served day's rows under the single-day budget."""
    part_keys = part_keys_for_day(keys, layer=scope.layer, kind=scope.kind, tier=scope.tier, day=served_day)
    result = reader.read_rows(RowRead(scope=scope, keys=part_keys, row_budget=DAY_ROW_BUDGET))
    return PublishedDay(
        requested_day=requested_day,
        served_day=served_day,
        rows=tuple(row for _, row in result.rows),
        truncated=result.budget_exhausted or result.unpositioned_rows > 0,
    )


def _window_rows(
    reader: PartitionRowReader,
    *,
    scope: ReadScope,
    keys: tuple[str, ...],
    published_days: tuple[date, ...],
) -> tuple[dict[date, list[ServedRow]], date | None]:
    """Read every published day of a window in ONE bounded scan, ascending, and say where it stopped."""
    rows_by_day: dict[date, list[ServedRow]] = {day: [] for day in published_days}
    if not published_days:
        return (rows_by_day, None)
    part_keys = tuple(
        key
        for day in published_days
        for key in part_keys_for_day(keys, layer=scope.layer, kind=scope.kind, tier=scope.tier, day=day)
    )
    result = reader.read_rows(RowRead(scope=scope, keys=part_keys, row_budget=WINDOW_ROW_BUDGET))
    for key, row in result.rows:
        rows_by_day[day_of_part_key(key)].append(row)
    if result.unpositioned_rows > 0:
        # Rows whose position is null were excluded by the viewport and cannot be attributed to a day,
        # so every day of the batch reports that it did not serve everything it holds.
        return (rows_by_day, published_days[0])
    if not result.budget_exhausted:
        return (rows_by_day, None)
    served = tuple(day for day in published_days if rows_by_day[day])
    # Keys sort chronologically, so the scan filled days in order: the last day it reached is the
    # first day that may be short, and every published day after it was never read at all.
    return (rows_by_day, served[-1] if served else published_days[0])


def _window_envelope(  # noqa: PLR0913 - one argument per input the per-day decision genuinely needs
    listing: WarehouseListing,
    *,
    scope: ReadScope,
    day: date,
    statuses: DayStatusSets,
    rows_by_day: dict[date, list[ServedRow]],
    truncated_from: date | None,
    lane_written: bool,
) -> DayEnvelope:
    """Render one day of a window, given what the single scan managed to serve."""
    if day in statuses.absent:
        return GovernedAbsenceDay(
            requested_day=day,
            served_day=day,
            absence=read_absence_evidence(listing, scope=scope, day=day),
        )
    if day in statuses.data:
        return PublishedDay(
            requested_day=day,
            served_day=day,
            rows=tuple(rows_by_day[day]),
            truncated=truncated_from is not None and day >= truncated_from,
        )
    if lane_written:
        return DayNotWritten(requested_day=day)
    return LaneNeverWritten(requested_day=day)


def _keys_for_months(
    listing: WarehouseListing,
    *,
    scope: ReadScope,
    first_day: date,
    last_day: date,
) -> tuple[str, ...]:
    """List every month the closed range touches, once each."""
    months: list[tuple[int, int]] = []
    cursor = date(first_day.year, first_day.month, 1)
    while cursor <= last_day:
        months.append((cursor.year, cursor.month))
        cursor = (
            date(cursor.year + 1, 1, 1) if cursor.month == DECEMBER else date(cursor.year, cursor.month + 1, 1)
        )
    return tuple(
        key
        for year, month in months
        for key in listing.list_keys(scope.layer, scope.kind, scope.tier, year=year, month=month)
    )
