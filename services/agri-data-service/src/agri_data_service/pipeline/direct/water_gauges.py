"""Transform parsed USGS NWIS readings into the dedicated water-gauges Parquet namespace.

A PUBLISHED DAY GOVERNED `absent` IS RECONCILED, NOT REFUSED, WHEN THE FETCH CARRIES ROWS FOR IT.
Until 2026-09-15 `DirectWaterGaugesForwardAdapter` raised on a `status=absent` day outright -- the
construct that held `sensors-direct-forward`'s breaker for a week (`sensors/adapter.py`, 2026-09-06..13)
when a retired Postgres adapter had governed days absent that the live poll later answered with rows.
Observed rows disprove an absence claim, so the marker is retracted at every tier immediately before the
first write -- the shape of `sensors/adapter.py::_retract_disproven_absence` -- with the marker's full
provenance carried out on `OverturnedAbsence`, which `pipeline/parquet/water_gauges_forward.py` re-emits
on the day's checkpoint. A marker with NO incoming rows is never touched: the merge's own empty-day
refusal runs first. On THIS lane the branch is closure of the class rather than a live repair: the
forward owns only days at or after `WATER_GAUGES_DIRECT_WRITER_START_DAY`, and the registry's
`writer_ceiling` kept the retired walker below that floor, so the two windows never overlapped.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.absence import GovernedAbsenceError
from agri_data_service.foundation.parquet.paths import partition_day_statuses
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.ingest.identity import build_streamflow_gauge_identity
from agri_data_service.ingest.usgs_nwis import USGS_PROPERTY_SOURCE
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.warehouse.schemas.water_gauges import (
    WATER_GAUGES_GRAIN,
    WATER_GAUGES_SCHEMA,
    WATER_GAUGES_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.absence import GovernedAbsence
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore


WATER_GAUGES_SOURCE_COLUMNS: Final[tuple[str, ...]] = (
    "site_number",
    "observed_at",
    "observed_day",
    "site_name",
    "latitude",
    "longitude",
    "flow_cfs",
    "percentile",
    "condition",
    "trend",
    "source",
)
WATER_GAUGES_PROVENANCE_COLUMNS: Final[tuple[str, ...]] = (
    "geometry_linked",
    "data_available_at",
    "ingested_at",
)
PUBLISHER_DAY_TEXT_LENGTH: Final = 10


class DirectWaterGaugesError(ValueError):
    """Raised when a parsed NWIS record cannot satisfy the Parquet contract."""


@dataclass(frozen=True, slots=True)
class DirectWaterGaugesWriteResult:
    """The lane adapter result consumed by the shared lane-day finalizer."""

    part_count: int
    row_count: int
    byte_count: int
    absence_recorded: bool = False


@dataclass(frozen=True, slots=True)
class DirectWaterGaugesMerge:
    """One lossless merge at the registered water-gauges grain."""

    table: pa.Table
    existing_rows: int
    incoming_rows: int
    added_rows: int
    updated_rows: int
    recovered_duplicate_rows: int = 0


@dataclass(frozen=True, slots=True)
class RetractedAbsenceMarker:
    """One governed-absence marker exactly as it stood at one tier when this writer retracted it."""

    tier: ZoomTier
    absence: GovernedAbsence

    def as_event(self) -> dict[str, object]:
        """Render the marker's whole provenance; nothing the original producer recorded is summarised away."""
        return {
            "tier": self.tier,
            "reason": self.absence.reason,
            "upstream_response": self.absence.upstream_response,
            "recorded_at": self.absence.recorded_at.astimezone(UTC).isoformat(),
            "run_id": self.absence.run_id,
        }


@dataclass(frozen=True, slots=True)
class OverturnedAbsence:
    """The audit trail of one governed absence this fetch disproved with observed rows, marker by marker."""

    day: date
    overturned_by_run_id: str
    incoming_rows: int
    markers: tuple[RetractedAbsenceMarker, ...]

    @property
    def tiers(self) -> tuple[ZoomTier, ...]:
        """Every tier a marker was retracted from, base rung last like the ladder that wrote them."""
        return tuple(marker.tier for marker in self.markers)

    def as_event(self) -> dict[str, object]:
        """Render the retraction for the forward's JSON report and the sibling-shaped stderr event."""
        return {
            "day": self.day.isoformat(),
            "overturned_by": "observed_rows",
            "overturned_by_run_id": self.overturned_by_run_id,
            "incoming_rows": self.incoming_rows,
            "tiers": list(self.tiers),
            "markers": [marker.as_event() for marker in self.markers],
        }


def publisher_named_day(record: Mapping[str, object]) -> date:
    """Return the opaque calendar day NWIS named in `updatedAt`."""
    value = record.get("updatedAt")
    if not isinstance(value, str) or len(value) < PUBLISHER_DAY_TEXT_LENGTH:
        raise DirectWaterGaugesError("NWIS reading has no publisher-named updatedAt day")
    named = value[:PUBLISHER_DAY_TEXT_LENGTH]
    try:
        parsed = date.fromisoformat(named)
    except ValueError as error:
        raise DirectWaterGaugesError(f"NWIS updatedAt day is not YYYY-MM-DD: {named!r}") from error
    if parsed.isoformat() != named:
        raise DirectWaterGaugesError(f"NWIS updatedAt day is not canonical YYYY-MM-DD: {named!r}")
    return parsed


def tables_by_publisher_day(
    records: Sequence[Mapping[str, object]],
    *,
    ingested_at: datetime,
) -> dict[date, pa.Table]:
    """Build one registered-schema Arrow table per actual NWIS source day."""
    if ingested_at.tzinfo is None or ingested_at.utcoffset() is None:
        raise DirectWaterGaugesError("ingested_at must include a timezone")
    rows: dict[date, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        if record.get("updatedAtIsWallClock"):
            raise DirectWaterGaugesError("wall-clock fallback records cannot enter direct water-gauges Parquet")
        identity = build_streamflow_gauge_identity(record)
        if identity.observed_at is None:
            raise DirectWaterGaugesError("NWIS reading has no observed instant")
        day = publisher_named_day(record)
        rows[day].append(
            {
                "site_number": str(record["siteNo"]),
                "observed_at": identity.observed_at,
                "observed_day": day,
                "site_name": str(record.get("siteName", "") or ""),
                "latitude": record.get("lat"),
                "longitude": record.get("lon"),
                "flow_cfs": record.get("flowCfs"),
                "percentile": record.get("percentile"),
                "condition": record.get("condition"),
                "trend": record.get("trend"),
                "source": USGS_PROPERTY_SOURCE,
                "geometry_linked": False,
                "data_available_at": identity.data_available_at,
                "ingested_at": ingested_at,
            }
        )
    return {
        day: pa.Table.from_pylist(day_rows, schema=WATER_GAUGES_SCHEMA.arrow_schema)
        for day, day_rows in sorted(rows.items())
    }


def _grain_key(row: Mapping[str, object], *, expected_day: date) -> tuple[str, datetime]:
    """Validate and return the exact base-rung grain for one row."""
    site_number = row.get(WATER_GAUGES_GRAIN[0])
    observed_at = row.get(WATER_GAUGES_GRAIN[1])
    observed_day = row.get("observed_day")
    if not isinstance(site_number, str) or not site_number.strip():
        raise DirectWaterGaugesError("a base water-gauges row has no site_number")
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise DirectWaterGaugesError("a base water-gauges row has no timezone-aware observed_at")
    if observed_day != expected_day:
        raise DirectWaterGaugesError(
            f"a base water-gauges row names observed_day={observed_day!r}, expected {expected_day.isoformat()}"
        )
    return site_number, observed_at


def _validate_table(table: pa.Table, *, label: str) -> None:
    """Refuse a base table that is not exactly the registered schema."""
    if table.schema != WATER_GAUGES_SCHEMA.arrow_schema:
        raise DirectWaterGaugesError(f"{label} water-gauges table does not match the registered Arrow schema")


def merge_water_gauges_day(
    existing: pa.Table | None,
    incoming: pa.Table,
    *,
    day: date,
) -> DirectWaterGaugesMerge:
    """Retain accumulated grains, refresh repeated source fields, and append unseen IV readings."""
    if incoming.num_rows == 0:
        raise DirectWaterGaugesError("a direct water-gauges merge cannot publish an empty source day")
    _validate_table(incoming, label="incoming")
    if existing is not None:
        _validate_table(existing, label="published")

    existing_rows = [] if existing is None else existing.to_pylist()
    incoming_rows = incoming.to_pylist()
    merged_rows: list[dict[str, object]] = []
    indices_by_grain: dict[tuple[str, datetime], list[int]] = defaultdict(list)
    recovered_duplicate_rows = 0
    for row in existing_rows:
        key = _grain_key(row, expected_day=day)
        indices_by_grain[key].append(len(merged_rows))
        merged_rows.append(row)

    added_rows = 0
    updated_rows = 0
    incoming_seen: set[tuple[str, datetime]] = set()
    for row in incoming_rows:
        key = _grain_key(row, expected_day=day)
        if key in incoming_seen:
            raise DirectWaterGaugesError(f"one NWIS IV fetch returned duplicate grain {key!r}")
        incoming_seen.add(key)
        existing_indices = indices_by_grain.get(key, [])
        if not existing_indices:
            indices_by_grain[key].append(len(merged_rows))
            merged_rows.append(row)
            added_rows += 1
            continue
        if len(existing_indices) > 1:
            raise DirectWaterGaugesError(
                f"incoming NWIS grain {key!r} maps to {len(existing_indices)} existing rows; "
                "refusing an ambiguous refresh so no historical duplicate is changed or dropped"
            )
        row_index = existing_indices[0]
        prior = merged_rows[row_index]
        refreshed = dict(prior)
        for column in WATER_GAUGES_SOURCE_COLUMNS:
            refreshed[column] = row[column]
        merged_rows[row_index] = refreshed
        updated_rows += 1

    return DirectWaterGaugesMerge(
        table=pa.Table.from_pylist(merged_rows, schema=WATER_GAUGES_SCHEMA.arrow_schema),
        existing_rows=len(existing_rows),
        incoming_rows=len(incoming_rows),
        added_rows=added_rows,
        updated_rows=updated_rows,
        recovered_duplicate_rows=recovered_duplicate_rows,
    )


@dataclass(frozen=True, slots=True)
class DirectWaterGaugesAdapter:
    """Write one source-direct historical day through the shared lane-day finalizer."""

    table: pa.Table

    async def __call__(
        self,
        session: AsyncSession,
        store: ObjectStore,
        *,
        day: date,
        run_id: str,
    ) -> DirectWaterGaugesWriteResult:
        """Write one validated z13 part; the shared finalizer owns tiers, prune, and markers."""
        del session, run_id
        validated = merge_water_gauges_day(None, self.table, day=day).table
        receipt = store.write_partition(
            validated,
            layer=WATER_GAUGES_STREAM,
            kind="observed",
            zoom=LANE_BASE_ZOOM_TIER,
            day=day,
        )
        return DirectWaterGaugesWriteResult(
            part_count=1,
            row_count=receipt.row_count,
            byte_count=receipt.byte_count,
        )


@dataclass(slots=True)
class DirectWaterGaugesForwardAdapter:
    """Merge one IV fetch into its published day while the caller holds the lane-day lock."""

    incoming: pa.Table
    merge: DirectWaterGaugesMerge | None = None
    #: The absence this fetch overturned, kept across retries: a retraction whose following write
    #: failed must still be reported, or the marker vanished with no record of who removed it.
    absence_overturned: OverturnedAbsence | None = field(default=None, init=False)

    async def __call__(
        self,
        session: AsyncSession,
        store: ObjectStore,
        *,
        day: date,
        run_id: str,
    ) -> DirectWaterGaugesWriteResult:
        """Write one full z13 part; the shared finalizer owns tiers, prune, and markers."""
        del session
        keys = store.list_partition_keys(
            WATER_GAUGES_STREAM,
            "observed",
            LANE_BASE_ZOOM_TIER,
            year=day.year,
            month=day.month,
        )
        status = partition_day_statuses(
            layer=WATER_GAUGES_STREAM,
            kind="observed",
            zoom=LANE_BASE_ZOOM_TIER,
            first_day=day,
            last_day=day,
            keys=keys,
        )[day]
        if status == "data":
            existing = store.read_partition(WATER_GAUGES_STREAM, "observed", LANE_BASE_ZOOM_TIER, day)
            merged = merge_water_gauges_day(existing, self.incoming, day=day)
        elif status in ("missing", "absent"):
            # `absent`: a marker calls this day empty and the fetch holds rows for it. The rows win, and
            # `_retract_disproven_absence` removes the marker below -- AFTER this merge has proven the
            # fetch non-empty and well-formed, so a marker is never touched for a fetch that says nothing.
            merged = merge_water_gauges_day(None, self.incoming, day=day)
        elif status == "incomplete":
            if self.merge is not None:
                # The previous attempt formed this table before its first R2 mutation. Replay it
                # exactly; reading a transient mix of new part-0 and old surplus is unnecessary.
                merged = self.merge
            else:
                existing = store.read_partition(WATER_GAUGES_STREAM, "observed", LANE_BASE_ZOOM_TIER, day)
                # Preserve every physical source row. Grain-only recovery cannot distinguish an
                # interrupted generation from legitimate PostgreSQL duplicates, so ambiguity is
                # refused by the normal merge instead of deleting evidence.
                merged = merge_water_gauges_day(existing, self.incoming, day=day)
        else:
            raise DirectWaterGaugesError(
                f"refusing to merge IV rows into water-gauges z13 {day.isoformat()} with status={status}: a day "
                "holding both part files and an absence marker needs an admin to decide which claim is true"
            )
        # Save the complete intended population before the first object mutation. Object-store
        # retries and the post-write content verifier both consume this checkpoint.
        self.merge = merged
        self._retract_disproven_absence(store, day=day, run_id=run_id)
        receipt = store.write_partition(
            merged.table,
            layer=WATER_GAUGES_STREAM,
            kind="observed",
            zoom=LANE_BASE_ZOOM_TIER,
            day=day,
        )
        return DirectWaterGaugesWriteResult(
            part_count=1,
            row_count=receipt.row_count,
            byte_count=receipt.byte_count,
        )

    def _retract_disproven_absence(self, store: ObjectStore, *, day: date, run_id: str) -> OverturnedAbsence | None:
        """Retract an absence this fetch's rows disprove -- inside the lock, before the first write, provenance kept.

        The shape of `sensors/adapter.py::_retract_disproven_absence`: EVERY TIER, not only the base
        rung, because an absence is propagated up the ladder and a base-only retraction leaves
        z0/z05/z09 asserting a governed absence over a day that now carries rows. The inverse stays
        fail-closed -- no fetch ever removes published data or governs a day absent.

        Every marker is READ BEFORE ANY IS CLEARED. A marker this lane could meet was written by the
        retired Postgres-reading gap-fill adapter, so its `reason`/`run_id` are the only record of why
        the day was ever called empty; a marker that cannot be decoded is refused rather than deleted
        blind.
        """
        markers: list[RetractedAbsenceMarker] = []
        for tier in ZOOM_TIERS:
            if not store.absence_exists(WATER_GAUGES_STREAM, "observed", tier, day):
                continue
            try:
                absence = store.read_absence(WATER_GAUGES_STREAM, "observed", tier, day)
            except GovernedAbsenceError as error:
                raise DirectWaterGaugesError(
                    f"water-gauges z{tier} {day.isoformat()} carries an absence marker this writer cannot decode, "
                    f"so it is refused rather than retracted without its provenance: {error}"
                ) from error
            if absence is None:  # retracted by another hand between the listing and this read
                continue
            markers.append(RetractedAbsenceMarker(tier=tier, absence=absence))
        if not markers:
            return None
        cleared: list[RetractedAbsenceMarker] = []
        try:
            for marker in markers:
                store.clear_absence_marker(WATER_GAUGES_STREAM, "observed", marker.tier, day)
                cleared.append(marker)
        finally:
            # A clear refused part-way has already removed every marker before it. Record those NOW,
            # so the bounded retry (which re-reads the survivors and clears them) reports the UNION of
            # tiers across attempts rather than losing the first attempt's removals with its exception.
            if cleared:
                self._record_retraction(day=day, run_id=run_id, cleared=tuple(cleared))
        return self.absence_overturned

    def _record_retraction(self, *, day: date, run_id: str, cleared: tuple[RetractedAbsenceMarker, ...]) -> None:
        """Fold one attempt's cleared markers into the adapter's running record and announce the UNION on stderr.

        Emitted once per attempt that cleared anything; the last one is the union. A log reader taking
        the last event per (run_id, day) therefore gets the truth without summing across attempts.
        """
        previous = self.absence_overturned
        self.absence_overturned = OverturnedAbsence(
            day=day,
            overturned_by_run_id=run_id,
            incoming_rows=self.incoming.num_rows,
            markers=cleared if previous is None else previous.markers + cleared,
        )
        # stderr, matching every sibling retraction: stdout carries the forward's parsed report,
        # which re-emits the running record on the day's attempt and checkpoint (`water_gauges_forward.py`).
        print(
            json.dumps(
                {
                    "event": "water_gauges_forward_absence_retracted",
                    "layer": WATER_GAUGES_STREAM,
                    "run_id": run_id,
                    "tier": LANE_BASE_ZOOM_TIER,
                    **self.absence_overturned.as_event(),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )


__all__ = [
    "WATER_GAUGES_PROVENANCE_COLUMNS",
    "WATER_GAUGES_SOURCE_COLUMNS",
    "DirectWaterGaugesAdapter",
    "DirectWaterGaugesError",
    "DirectWaterGaugesForwardAdapter",
    "DirectWaterGaugesMerge",
    "DirectWaterGaugesWriteResult",
    "OverturnedAbsence",
    "RetractedAbsenceMarker",
    "merge_water_gauges_day",
    "publisher_named_day",
    "tables_by_publisher_day",
]
