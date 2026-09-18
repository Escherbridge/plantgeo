"""Merge one poll's rows into their published day, preserving every prior reading.

Ported from `water_gauges.py::merge_water_gauges_day` / `DirectWaterGaugesForwardAdapter`, because
this lane has the same shape water-gauges does and climate/soil do not: a day is filled by many
INCREMENTAL polls over time, not one settled-day fetch. The grain differs (three columns here,
`(latitude, longitude, observed_at)`, against water-gauges' two) and there is no duplicate-source
reconciliation problem to solve -- `bounded_sample_points` returns the same float coordinates on
every call for one bbox and spacing, so a repeat grain match is always the SAME point reporting the
SAME instant again, never an ambiguous historical duplicate. A match therefore always refreshes
cleanly; `merge_water_gauges_day`'s "ambiguous match, refuse" branch has no counterpart here.

A PUBLISHED DAY GOVERNED `absent` IS RECONCILED, NOT REFUSED, WHEN THE POLL CARRIES ROWS FOR IT.
`pipeline/direct/AGENTS.md` ("No governed-absence path in the forward writer, deliberately") says this
writer must never MANUFACTURE an absence, and that still holds -- but until 2026-09-15 the inverse case
raised: a `status=absent` day with polled rows was refused outright, the same construct that held
`sensors-direct-forward`'s breaker for a week (`sensors/adapter.py`, 2026-09-06..13) when the retired
Postgres adapter had governed days absent that the live poll later answered. Observed rows disprove an
absence claim, so the marker is retracted at every tier immediately before the first write -- the shape
of `sensors/adapter.py::_retract_disproven_absence` and `climate/adapter.py`'s -- and the marker's full
provenance travels out on `OverturnedAbsence`, which `forward.py` re-emits on the day's checkpoint.
A marker with NO incoming rows is never touched: the merge's own empty-poll refusal runs first, so
nothing is retracted for a day this poll has nothing to say about. On THIS lane the branch is closure
of the class rather than a live repair: the poll buckets only today (and yesterday within three hours
of UTC midnight), and no producer has governed those days absent since the 2026-09-07 swap.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.absence import GovernedAbsenceError
from agri_data_service.foundation.parquet.paths import partition_day_statuses
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.weather_observations.rows import (
    WEATHER_OBSERVATIONS_SOURCE_COLUMNS,
)
from agri_data_service.warehouse.schemas.weather_observations import (
    WEATHER_OBSERVATIONS_GRAIN,
    WEATHER_OBSERVATIONS_SCHEMA,
    WEATHER_OBSERVATIONS_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.absence import GovernedAbsence
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

#: `Final` so the value narrows to the `PartitionKind` literal rather than to bare `str`.
WEATHER_OBSERVATIONS_DIRECT_KIND: Final = "observed"


class DirectWeatherObservationsError(ValueError):
    """Raised when a polled row or a published day cannot satisfy the direct-write contract."""


@dataclass(frozen=True, slots=True)
class DirectWeatherObservationsWriteResult:
    """The lane adapter result consumed by the shared lane-day finalizer."""

    part_count: int
    row_count: int
    byte_count: int
    absence_recorded: bool = False


@dataclass(frozen=True, slots=True)
class DirectWeatherObservationsMerge:
    """One lossless merge at the registered weather-observations grain."""

    table: pa.Table
    existing_rows: int
    incoming_rows: int
    added_rows: int
    updated_rows: int


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


def _grain_key(row: Mapping[str, object], *, expected_day: date) -> tuple[float, float, datetime]:
    """Validate and return the exact base-rung grain for one row."""
    latitude = row.get(WEATHER_OBSERVATIONS_GRAIN[0])
    longitude = row.get(WEATHER_OBSERVATIONS_GRAIN[1])
    observed_at = row.get(WEATHER_OBSERVATIONS_GRAIN[2])
    observed_day = row.get("observed_day")
    if isinstance(latitude, bool) or not isinstance(latitude, int | float):
        raise DirectWeatherObservationsError("a base weather-observations row has no latitude")
    if isinstance(longitude, bool) or not isinstance(longitude, int | float):
        raise DirectWeatherObservationsError("a base weather-observations row has no longitude")
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise DirectWeatherObservationsError("a base weather-observations row has no timezone-aware observed_at")
    if observed_day != expected_day:
        raise DirectWeatherObservationsError(
            f"a base weather-observations row names observed_day={observed_day!r}, expected {expected_day.isoformat()}"
        )
    return float(latitude), float(longitude), observed_at


def _validate_table(table: pa.Table, *, label: str) -> None:
    """Refuse a base table that is not exactly the registered schema."""
    if table.schema != WEATHER_OBSERVATIONS_SCHEMA.arrow_schema:
        raise DirectWeatherObservationsError(
            f"{label} weather-observations table does not match the registered Arrow schema"
        )


def merge_weather_observations_day(
    existing: pa.Table | None,
    incoming: pa.Table,
    *,
    day: date,
) -> DirectWeatherObservationsMerge:
    """Retain every published reading, refresh a repeated point-instant, append every unseen one."""
    if incoming.num_rows == 0:
        raise DirectWeatherObservationsError("a direct weather-observations merge cannot publish an empty poll")
    _validate_table(incoming, label="incoming")
    if existing is not None:
        _validate_table(existing, label="published")

    existing_rows = [] if existing is None else existing.to_pylist()
    incoming_rows = incoming.to_pylist()
    merged_rows: list[dict[str, object]] = []
    indices_by_grain: dict[tuple[float, float, datetime], int] = {}
    for row in existing_rows:
        key = _grain_key(row, expected_day=day)
        indices_by_grain[key] = len(merged_rows)
        merged_rows.append(row)

    added_rows = 0
    updated_rows = 0
    incoming_seen: set[tuple[float, float, datetime]] = set()
    for row in incoming_rows:
        key = _grain_key(row, expected_day=day)
        if key in incoming_seen:
            raise DirectWeatherObservationsError(f"one poll returned duplicate grain {key!r}")
        incoming_seen.add(key)
        existing_index = indices_by_grain.get(key)
        if existing_index is None:
            indices_by_grain[key] = len(merged_rows)
            merged_rows.append(row)
            added_rows += 1
            continue
        prior = merged_rows[existing_index]
        refreshed = dict(prior)
        for column in WEATHER_OBSERVATIONS_SOURCE_COLUMNS:
            refreshed[column] = row[column]
        merged_rows[existing_index] = refreshed
        updated_rows += 1

    return DirectWeatherObservationsMerge(
        table=pa.Table.from_pylist(merged_rows, schema=WEATHER_OBSERVATIONS_SCHEMA.arrow_schema),
        existing_rows=len(existing_rows),
        incoming_rows=len(incoming_rows),
        added_rows=added_rows,
        updated_rows=updated_rows,
    )


@dataclass(slots=True)
class DirectWeatherObservationsForwardAdapter:
    """Merge one poll's day-bucketed rows into its published day while the caller holds the lane-day lock."""

    incoming: pa.Table
    merge: DirectWeatherObservationsMerge | None = field(default=None, init=False)
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
    ) -> DirectWeatherObservationsWriteResult:
        """Write one full z13 part; the shared finalizer owns tiers, prune, and markers."""
        del session
        keys = store.list_partition_keys(
            WEATHER_OBSERVATIONS_STREAM,
            WEATHER_OBSERVATIONS_DIRECT_KIND,
            LANE_BASE_ZOOM_TIER,
            year=day.year,
            month=day.month,
        )
        status = partition_day_statuses(
            layer=WEATHER_OBSERVATIONS_STREAM,
            kind=WEATHER_OBSERVATIONS_DIRECT_KIND,
            zoom=LANE_BASE_ZOOM_TIER,
            first_day=day,
            last_day=day,
            keys=keys,
        )[day]
        if status == "data":
            existing = store.read_partition(
                WEATHER_OBSERVATIONS_STREAM, WEATHER_OBSERVATIONS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day
            )
            merged = merge_weather_observations_day(existing, self.incoming, day=day)
        elif status in ("missing", "absent"):
            # `absent`: a marker calls this day empty and the poll holds rows for it. The rows win, and
            # `_retract_disproven_absence` removes the marker below -- AFTER this merge has proven the
            # poll non-empty and well-formed, so a marker is never touched for a poll that says nothing.
            merged = merge_weather_observations_day(None, self.incoming, day=day)
        elif status == "incomplete":
            if self.merge is not None:
                # The previous attempt formed this table before its first R2 mutation. Replay it
                # exactly, matching `water_gauges.py`'s discipline for the same crash-recovery case.
                merged = self.merge
            else:
                existing = store.read_partition(
                    WEATHER_OBSERVATIONS_STREAM, WEATHER_OBSERVATIONS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day
                )
                merged = merge_weather_observations_day(existing, self.incoming, day=day)
        else:
            raise DirectWeatherObservationsError(
                f"refusing to merge poll rows into weather-observations z13 {day.isoformat()} with status={status}: "
                "a day holding both part files and an absence marker needs an admin to decide which claim is true"
            )
        # Save the complete intended population before the first object mutation, matching
        # `water_gauges.py`'s checkpoint discipline for object-store retries.
        self.merge = merged
        self._retract_disproven_absence(store, day=day, run_id=run_id)
        receipt = store.write_partition(
            merged.table,
            layer=WEATHER_OBSERVATIONS_STREAM,
            kind=WEATHER_OBSERVATIONS_DIRECT_KIND,
            zoom=LANE_BASE_ZOOM_TIER,
            day=day,
        )
        return DirectWeatherObservationsWriteResult(
            part_count=1,
            row_count=receipt.row_count,
            byte_count=receipt.byte_count,
        )

    def _retract_disproven_absence(self, store: ObjectStore, *, day: date, run_id: str) -> OverturnedAbsence | None:
        """Retract an absence this poll's rows disprove -- inside the lock, before the first write, provenance kept.

        The shape of `sensors/adapter.py::_retract_disproven_absence`: EVERY TIER, not only the base
        rung, because an absence is propagated up the ladder and a base-only retraction leaves
        z0/z05/z09 asserting a governed absence over a day that now carries rows. The inverse stays
        fail-closed -- no poll ever removes published data or governs a day absent.

        Every marker is READ BEFORE ANY IS CLEARED. The markers this lane could meet were written by a
        producer that no longer exists (`pipeline/lanes/weather_observations.py`, retired 2026-09-07),
        so their `reason`/`run_id` are the only record of why the day was ever called empty; a marker
        that cannot be decoded is refused rather than deleted blind.
        """
        markers: list[RetractedAbsenceMarker] = []
        for tier in ZOOM_TIERS:
            if not store.absence_exists(WEATHER_OBSERVATIONS_STREAM, WEATHER_OBSERVATIONS_DIRECT_KIND, tier, day):
                continue
            try:
                absence = store.read_absence(WEATHER_OBSERVATIONS_STREAM, WEATHER_OBSERVATIONS_DIRECT_KIND, tier, day)
            except GovernedAbsenceError as error:
                raise DirectWeatherObservationsError(
                    f"weather-observations z{tier} {day.isoformat()} carries an absence marker this writer cannot "
                    f"decode, so it is refused rather than retracted without its provenance: {error}"
                ) from error
            if absence is None:  # retracted by another hand between the listing and this read
                continue
            markers.append(RetractedAbsenceMarker(tier=tier, absence=absence))
        if not markers:
            return None
        cleared: list[RetractedAbsenceMarker] = []
        try:
            for marker in markers:
                store.clear_absence_marker(
                    WEATHER_OBSERVATIONS_STREAM, WEATHER_OBSERVATIONS_DIRECT_KIND, marker.tier, day
                )
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
        # which re-emits the running record on the day's attempt and checkpoint (`forward.py`).
        print(
            json.dumps(
                {
                    "event": "weather_observations_forward_absence_retracted",
                    "layer": WEATHER_OBSERVATIONS_STREAM,
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
    "WEATHER_OBSERVATIONS_DIRECT_KIND",
    "DirectWeatherObservationsError",
    "DirectWeatherObservationsForwardAdapter",
    "DirectWeatherObservationsMerge",
    "DirectWeatherObservationsWriteResult",
    "OverturnedAbsence",
    "RetractedAbsenceMarker",
    "merge_weather_observations_day",
]
