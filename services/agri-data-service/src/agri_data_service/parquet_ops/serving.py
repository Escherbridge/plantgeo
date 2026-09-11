"""The four-state resolver: which state one day is in, and the rows behind it."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Final, Protocol

from agri_data_service.foundation.parquet.absence import GovernedAbsence, GovernedAbsenceError
from agri_data_service.foundation.parquet.paths import (
    absence_marker_path,
    classify_partition_day,
    tier_day_objects,
)
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.mtbs_snapshot_catalog import VerifiedMtbsSnapshot, verify_snapshot_absence
from agri_data_service.parquet_ops.warehouse_reader import RowRead, day_of_part_key, part_keys_for_day
from agri_data_service.parquet_ops.wire import (
    AbsenceEvidence,
    DayEnvelope,
    DayNotWritten,
    GovernedAbsenceDay,
    LaneNeverWritten,
    PublishedDay,
    ServedRow,
)
from agri_data_service.warehouse.mtbs_releases import MTBS_ANNUAL_RELEASE_DATES
from agri_data_service.warehouse.mtbs_snapshots import MTBS_SNAPSHOT_FIRST_DAY

if TYPE_CHECKING:
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.parquet_ops.request_params import ReadScope
    from agri_data_service.parquet_ops.warehouse_reader import PartitionRowReader, WarehouseListing

#: Rows one day read may return before it reports itself truncated.
DAY_ROW_BUDGET: Final = 40_000
MTBS_FIRST_CAPTURE_YEAR: Final = 2018
MTBS_LAST_CAPTURE_YEAR: Final = 2026

#: Rows one WINDOW read may return in total, shared across its days in ascending order. The window
#: is answered by ONE scan, so the budget is the window's and not each day's.
WINDOW_ROW_BUDGET: Final = 120_000

#: How far back a release resolution walks before it reports that no release covers the day. Twelve
#: years reaches every lane's history floor except fire-detections, which is not a release series.
RELEASE_LOOKBACK_YEARS: Final = 12

#: The month a year rolls over on, named so the month walk below is not a bare literal.
DECEMBER: Final = 12


class AbsenceMarkerSource(Protocol):
    """The one object read a governed-absence decode needs; every listing already satisfies it."""

    def read_object(self, relative_key: str) -> bytes | None: ...


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
    """Classify every day one listing mentions, through the SAME rule `partition_day_statuses` uses.

    NO SECOND DEFINITION, and no marker is opened. `classify_partition_day` decides both, so a
    derived rung that generalised every base row away reads `data` here and in the census, while an
    ordinary marker with no parts beside it -- a LOST rung -- reads `incomplete` in both and is
    refused out loud rather than served as a day the warehouse never wrote.
    """
    objects = tier_day_objects(keys, layer=layer, kind=kind, zoom=tier)
    sorted_days: dict[str, set[date]] = {"data": set(), "absent": set(), "conflict": set(), "incomplete": set()}
    for day in objects.named_days:
        status = classify_partition_day(day, objects, zoom=tier)
        if status != "missing":
            sorted_days[status].add(day)
    return DayStatusSets(
        data=frozenset(sorted_days["data"]),
        absent=frozenset(sorted_days["absent"]),
        conflict=frozenset(sorted_days["conflict"]),
        incomplete=frozenset(sorted_days["incomplete"]),
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
    if _snapshot_scope(scope, day) and day in statuses.data | statuses.absent:
        snapshot = _exact_snapshot(listing, scope=scope, day=day)
        return _snapshot_envelope(listing, reader, scope=scope, snapshot=snapshot, requested_day=day, keys=month_keys)
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
    snapshots = {
        day: _exact_snapshot(listing, scope=scope, day=day)
        for day in span
        if _snapshot_scope(scope, day) and day in statuses.data | statuses.absent
    }
    for snapshot in snapshots.values():
        _snapshot_keys(snapshot, scope=scope, keys=keys)
    rows_by_day, truncated_from = _window_rows(reader, scope=scope, keys=keys, published_days=published_days)
    lane_written = bool(keys) or bool(listing.list_keys(scope.layer, scope.kind, scope.tier))
    envelopes = tuple(
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
    return tuple(_attach_window_snapshot(envelope, snapshots, scope=scope) for envelope in envelopes)


def _attach_window_snapshot(
    envelope: DayEnvelope, snapshots: dict[date, VerifiedMtbsSnapshot], *, scope: ReadScope
) -> DayEnvelope:
    snapshot = snapshots.get(envelope.requested_day)
    if snapshot is not None:
        if isinstance(envelope, PublishedDay):
            return _snapshot_rows(snapshot, envelope)
        if isinstance(envelope, GovernedAbsenceDay):
            _verify_served_absence(snapshot, scope=scope, absence=envelope.absence)
            return replace(envelope, mtbs_snapshot=snapshot.descriptor)
    return envelope


def resolve_release(
    listing: WarehouseListing,
    reader: PartitionRowReader,
    *,
    scope: ReadScope,
    as_of: date,
) -> DayEnvelope:
    """Answer with the newest release at or before `as_of`, reported at the release's OWN day."""
    snapshot = _latest_snapshot(listing, scope=scope, day=as_of)
    if snapshot is not None:
        day = snapshot.descriptor.available_day
        keys = listing.list_keys(scope.layer, scope.kind, scope.tier, year=day.year, month=day.month)
        return _snapshot_envelope(listing, reader, scope=scope, snapshot=snapshot, requested_day=as_of, keys=keys)
    eligible_days = frozenset(MTBS_ANNUAL_RELEASE_DATES.values()) if scope.layer == "burn-severity" else None
    for year in range(as_of.year, as_of.year - RELEASE_LOOKBACK_YEARS, -1):
        keys = listing.list_keys(scope.layer, scope.kind, scope.tier, year=year)
        statuses = day_status_sets(keys, layer=scope.layer, kind=scope.kind, tier=scope.tier)
        candidates = tuple(
            day for day in statuses.resolvable if day <= as_of and (eligible_days is None or day in eligible_days)
        )
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


def read_absence_evidence(listing: AbsenceMarkerSource, *, scope: ReadScope, day: date) -> AbsenceEvidence:
    """Decode one governed-absence marker; an absence without its evidence is not served as one.

    Typed to the ONE method it uses rather than to `WarehouseListing`, so the immutable-snapshot
    store -- which lists a different key space and implements no `list_keys` -- decodes a forward
    day's absence through this decoder instead of respelling its two fail-closed refusals.
    """
    key = absence_marker_path(scope.layer, scope.kind, scope.tier, day)
    payload = listing.read_object(key)
    if payload is None:
        raise faults.absence_marker_unreadable(layer=scope.layer, day=day.isoformat())
    try:
        absence = GovernedAbsence.from_json_bytes(payload)
    except GovernedAbsenceError as exc:
        raise faults.absence_marker_undecodable(
            layer=scope.layer,
            day=day.isoformat(),
            detail=str(exc),
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
    result = reader.read_rows(
        RowRead(scope=scope, keys=part_keys, row_budget=WINDOW_ROW_BUDGET, per_day_truncation=True)
    )
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
        cursor = date(cursor.year + 1, 1, 1) if cursor.month == DECEMBER else date(cursor.year, cursor.month + 1, 1)
    return tuple(
        key
        for year, month in months
        for key in listing.list_keys(scope.layer, scope.kind, scope.tier, year=year, month=month)
    )


def _snapshot_scope(scope: ReadScope, day: date) -> bool:
    return scope.layer == "burn-severity" and scope.kind == "observed" and day >= MTBS_SNAPSHOT_FIRST_DAY


def _latest_snapshot(listing: WarehouseListing, *, scope: ReadScope, day: date) -> VerifiedMtbsSnapshot | None:
    if not _snapshot_scope(scope, day):
        return None
    loader = getattr(listing, "mtbs_snapshot_loader", None)
    if loader is None:
        return None
    result = loader(day)
    if result is not None and not isinstance(result, VerifiedMtbsSnapshot):
        raise faults.ServingRefusalError(
            "mtbs_snapshot_invalid", "Current MTBS snapshot admission returned invalid metadata"
        )
    return result


def _exact_snapshot(listing: WarehouseListing, *, scope: ReadScope, day: date) -> VerifiedMtbsSnapshot:
    result = _latest_snapshot(listing, scope=scope, day=day)
    if result is None or result.descriptor.available_day != day:
        raise faults.ServingRefusalError(
            "mtbs_snapshot_unregistered", "Current MTBS day lacks verified snapshot evidence"
        )
    return result


def _snapshot_keys(snapshot: VerifiedMtbsSnapshot, *, scope: ReadScope, keys: tuple[str, ...]) -> None:
    row = snapshot.rung(scope.tier)
    parts = part_keys_for_day(keys, layer=scope.layer, kind=scope.kind, tier=scope.tier, day=row.day)
    statuses = day_status_sets(keys, layer=scope.layer, kind=scope.kind, tier=scope.tier)
    expected_days = statuses.absent if row.terminal_state == "governed_absence" else statuses.data
    if row.day not in expected_days or tuple(sorted(parts)) != tuple(
        sorted(receipt.key for receipt in row.data_receipts)
    ):
        raise faults.ServingRefusalError(
            "mtbs_snapshot_stale", "Current MTBS physical partition differs from its availability evidence"
        )


def _snapshot_rows(snapshot: VerifiedMtbsSnapshot, envelope: PublishedDay) -> PublishedDay:
    descriptor = snapshot.descriptor
    fire_ids: set[str] = set()
    if len(envelope.rows) > descriptor.source_row_count:
        raise faults.ServingRefusalError(
            "mtbs_snapshot_rows_invalid", "Current MTBS result exceeds captured source count"
        )
    for row in envelope.rows:
        fire_id = row.get("fire_id")
        if not isinstance(fire_id, str) or not fire_id or fire_id in fire_ids:
            raise faults.ServingRefusalError(
                "mtbs_snapshot_rows_invalid", "Current MTBS fire identities are missing or repeated"
            )
        fire_ids.add(fire_id)
        fire_year = row.get("fire_year")
        if (
            row.get("release_identifier") != descriptor.release_identifier
            or row.get("observed_day") != descriptor.available_day
            or row.get("data_available_at") != datetime.combine(descriptor.available_day, time.min, tzinfo=UTC)
            or not isinstance(fire_year, int)
            or isinstance(fire_year, bool)
            or not MTBS_FIRST_CAPTURE_YEAR <= fire_year <= MTBS_LAST_CAPTURE_YEAR
        ):
            raise faults.ServingRefusalError(
                "mtbs_snapshot_rows_invalid", "Current MTBS rows differ from their captured snapshot identity"
            )
    return replace(envelope, mtbs_snapshot=descriptor)


def _snapshot_envelope(  # noqa: PLR0913 - one selected proof, physical inventory, and normal read context
    listing: WarehouseListing,
    reader: PartitionRowReader,
    *,
    scope: ReadScope,
    snapshot: VerifiedMtbsSnapshot,
    requested_day: date,
    keys: tuple[str, ...],
) -> DayEnvelope:
    _snapshot_keys(snapshot, scope=scope, keys=keys)
    day = snapshot.descriptor.available_day
    if snapshot.rung(scope.tier).terminal_state == "governed_absence":
        absence = read_absence_evidence(listing, scope=scope, day=day)
        _verify_served_absence(snapshot, scope=scope, absence=absence)
        return GovernedAbsenceDay(requested_day, day, absence, snapshot.descriptor)
    return _snapshot_rows(
        snapshot, _published(reader, scope=scope, keys=keys, requested_day=requested_day, served_day=day)
    )


def _verify_served_absence(snapshot: VerifiedMtbsSnapshot, *, scope: ReadScope, absence: AbsenceEvidence) -> None:
    payload = GovernedAbsence(
        reason=absence.reason,
        upstream_response=absence.upstream_response,
        recorded_at=absence.recorded_at,
        run_id=absence.run_id,
    ).to_json_bytes()
    verify_snapshot_absence(snapshot, tier=scope.tier, payload=payload)
