"""The lock-holding sensors forward adapter: merge one poll into its day, retracting disproven absences.

The merge unit -- the whole (sensor_id, observed_day) block, and why -- moved to
`pipeline/lanes/sensors/merge.py` (p4-extract); `merge_sensors_day`, `DirectSensorsMerge` and
`DirectSensorsError` are re-exported here for legacy importers (`DEPRECATED_ALIASES.md`).

A PUBLISHED DAY GOVERNED `absent` IS RECONCILED, NOT REFUSED, WHEN THE POLL CARRIES ROWS FOR IT.
`source.py` argues this writer must never MANUFACTURE an absence over a day the retired adapter
published, and that still holds -- but the inverse case went unhandled: the retired adapter governed
days absent that NWS's rolling window later answered with real readings, and refusing them
(`status=absent` raised) failed every bucket for a week (`sensors-direct-forward`, 2026-09-06..13).
Observed rows disprove an absence claim the way a POWER revision disproves climate's, so the marker is
retracted at every tier immediately before the first write -- the shape of
`climate/adapter.py::_retract_disproven_absence` -- and the marker's full provenance travels out on
`OverturnedAbsence`, which `forward.py` re-emits on the day's checkpoint. A marker with NO incoming
rows is never touched: the merge's own empty-poll refusal runs first, so nothing is retracted for a
day this poll has nothing to say about.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, date
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.absence import GovernedAbsenceError
from agri_data_service.foundation.parquet.paths import partition_day_statuses
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.lanes.sensors.merge import DirectSensorsError, DirectSensorsMerge, merge_sensors_day
from agri_data_service.warehouse.schemas.sensors import SENSORS_STREAM

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pyarrow as pa  # type: ignore[import-untyped]
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.absence import GovernedAbsence
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

#: `Final` so the value narrows to the `PartitionKind` literal rather than to bare `str`. Matches the
#: kind the retired Postgres-reading adapter used (`pipeline/lanes/sensors.py::export_sensors_day`,
#: deleted 2026-09-07), so a day this writer publishes lands in the SAME namespace as the days that
#: adapter already published.
SENSORS_DIRECT_KIND: Final = "observed"


@dataclass(frozen=True, slots=True)
class DirectSensorsWriteResult:
    """The lane adapter result consumed by the shared lane-day finalizer."""

    part_count: int
    row_count: int
    byte_count: int
    absence_recorded: bool = False


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
    """The audit trail of one governed absence this poll disproved with observed rows, marker by marker."""

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


@dataclass(slots=True)
class DirectSensorsForwardAdapter:
    """Merge one poll's day-bucketed rows into its published day while the caller holds the lane-day lock."""

    incoming: pa.Table
    merge: DirectSensorsMerge | None = field(default=None, init=False)
    #: The absence this poll overturned, kept across retries: a retraction whose following write
    #: failed must still be reported, or the marker vanished with no record of who removed it.
    absence_overturned: OverturnedAbsence | None = field(default=None, init=False)

    async def __call__(
        self,
        session: AsyncSession,
        store: ObjectStore,
        *,
        day: date,
        run_id: str,
    ) -> DirectSensorsWriteResult:
        """Write one full z13 part; the shared finalizer owns tiers, prune, and markers."""
        del session
        keys = store.list_partition_keys(
            SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, year=day.year, month=day.month
        )
        status = partition_day_statuses(
            layer=SENSORS_STREAM,
            kind=SENSORS_DIRECT_KIND,
            zoom=LANE_BASE_ZOOM_TIER,
            first_day=day,
            last_day=day,
            keys=keys,
        )[day]
        if status == "data":
            existing = store.read_partition(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day)
            merged = merge_sensors_day(existing, self.incoming, day=day)
        elif status in ("missing", "absent"):
            # `absent`: a marker calls this day empty and the poll holds rows for it. The rows win, and
            # `_retract_disproven_absence` removes the marker below -- AFTER this merge has proven the
            # poll non-empty and well-formed, so a marker is never touched for a poll that says nothing.
            merged = merge_sensors_day(None, self.incoming, day=day)
        elif status == "incomplete":
            if self.merge is not None:
                # The previous attempt formed this table before its first R2 mutation. Replay it
                # exactly, matching `water_gauges.py`'s/`weather_observations`'s discipline for the
                # same crash-recovery case.
                merged = self.merge
            else:
                existing = store.read_partition(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day)
                merged = merge_sensors_day(existing, self.incoming, day=day)
        else:
            raise DirectSensorsError(
                f"refusing to merge poll rows into sensors z13 {day.isoformat()} with status={status}: a day "
                "holding both part files and an absence marker needs an admin to decide which claim is true"
            )
        # Save the complete intended population before the first object mutation, matching the
        # sibling writers' checkpoint discipline for object-store retries.
        self.merge = merged
        self._retract_disproven_absence(store, day=day, run_id=run_id)
        receipt = store.write_partition(
            merged.table,
            layer=SENSORS_STREAM,
            kind=SENSORS_DIRECT_KIND,
            zoom=LANE_BASE_ZOOM_TIER,
            day=day,
        )
        return DirectSensorsWriteResult(
            part_count=1,
            row_count=receipt.row_count,
            byte_count=receipt.byte_count,
        )

    def _retract_disproven_absence(self, store: ObjectStore, *, day: date, run_id: str) -> None:
        """Retract an absence this poll's rows disprove -- inside the lock, before the first write, provenance kept.

        The shape of `climate/adapter.py::_retract_disproven_absence`: EVERY TIER, not only the base
        rung, because an absence is propagated up the ladder and a base-only retraction leaves
        z0/z05/z09 asserting a governed absence over a day that now carries rows. The inverse stays
        fail-closed -- no poll ever removes published data or governs a day absent.

        Every marker is READ BEFORE ANY IS CLEARED. The markers this lane meets were written by a
        producer that no longer exists (`pipeline/lanes/sensors.py`), so their `reason`/`run_id` are
        the only record of why the day was ever called empty; a marker that cannot be decoded is
        refused rather than deleted blind.

        A CLEAR THAT FAILS MID-LADDER STILL RECORDS WHAT IT CLEARED. The base rung goes last, so the
        day stays `absent` and the bounded retry re-selects it; the retry then retracts only the
        survivors, and `_record_overturn` unions the two attempts so the day's report names every
        tier that was ever retracted, not only the ones the final attempt touched.

        The stderr event is emitted once per attempt that cleared anything, so a partial clear
        followed by a successful retry emits twice; the last event is the union and is the truth.
        """
        markers: list[RetractedAbsenceMarker] = []
        for tier in ZOOM_TIERS:
            if not store.absence_exists(SENSORS_STREAM, SENSORS_DIRECT_KIND, tier, day):
                continue
            try:
                absence = store.read_absence(SENSORS_STREAM, SENSORS_DIRECT_KIND, tier, day)
            except GovernedAbsenceError as error:
                raise DirectSensorsError(
                    f"sensors z{tier} {day.isoformat()} carries an absence marker this writer cannot decode, so it "
                    f"is refused rather than retracted without its provenance: {error}"
                ) from error
            if absence is None:  # retracted by another hand between the listing and this read
                continue
            markers.append(RetractedAbsenceMarker(tier=tier, absence=absence))
        if not markers:
            return
        cleared: list[RetractedAbsenceMarker] = []
        for marker in markers:
            try:
                store.clear_absence_marker(SENSORS_STREAM, SENSORS_DIRECT_KIND, marker.tier, day)
            except Exception:
                if cleared:
                    self._record_overturn(cleared, day=day, run_id=run_id)
                raise
            cleared.append(marker)
        self._record_overturn(cleared, day=day, run_id=run_id)

    def _record_overturn(self, cleared: Sequence[RetractedAbsenceMarker], *, day: date, run_id: str) -> None:
        """Fold newly cleared markers into this day's overturn record and announce it on stderr."""
        previous = self.absence_overturned
        carried = () if previous is None or previous.day != day else previous.markers
        already = {marker.tier for marker in carried}
        overturned = OverturnedAbsence(
            day=day,
            overturned_by_run_id=run_id,
            incoming_rows=self.incoming.num_rows,
            markers=(*carried, *(marker for marker in cleared if marker.tier not in already)),
        )
        self.absence_overturned = overturned
        # stderr, matching every sibling retraction: stdout carries the forward's parsed report,
        # which re-emits this same record on the day's checkpoint (`forward.py`).
        print(
            json.dumps(
                {
                    "event": "sensors_forward_absence_retracted",
                    "layer": SENSORS_STREAM,
                    "run_id": run_id,
                    "tier": LANE_BASE_ZOOM_TIER,
                    **overturned.as_event(),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )


__all__ = [
    "SENSORS_DIRECT_KIND",
    "DirectSensorsError",
    "DirectSensorsForwardAdapter",
    "DirectSensorsMerge",
    "DirectSensorsWriteResult",
    "OverturnedAbsence",
    "RetractedAbsenceMarker",
    "merge_sensors_day",
]
