"""Merge one poll's rows into their published day, preserving every prior reading.

Ported from `water_gauges.py::merge_water_gauges_day` / `DirectWaterGaugesForwardAdapter`, because
this lane has the same shape water-gauges does and climate/soil do not: a day is filled by many
INCREMENTAL polls over time, not one settled-day fetch. The grain differs (three columns here,
`(latitude, longitude, observed_at)`, against water-gauges' two) and there is no duplicate-source
reconciliation problem to solve -- `bounded_sample_points` returns the same float coordinates on
every call for one bbox and spacing, so a repeat grain match is always the SAME point reporting the
SAME instant again, never an ambiguous historical duplicate. A match therefore always refreshes
cleanly; `merge_water_gauges_day`'s "ambiguous match, refuse" branch has no counterpart here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.paths import partition_day_statuses
from agri_data_service.pipeline.direct.weather_observations.rows import (
    WEATHER_OBSERVATIONS_SOURCE_COLUMNS,
)
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.warehouse.schemas.weather_observations import (
    WEATHER_OBSERVATIONS_GRAIN,
    WEATHER_OBSERVATIONS_SCHEMA,
    WEATHER_OBSERVATIONS_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession

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

    async def __call__(
        self,
        session: AsyncSession,
        store: ObjectStore,
        *,
        day: date,
        run_id: str,
    ) -> DirectWeatherObservationsWriteResult:
        """Write one full z13 part; the shared finalizer owns tiers, prune, and markers."""
        del session, run_id
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
        elif status == "missing":
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
                f"refusing to merge poll rows into weather-observations z13 {day.isoformat()} with status={status}"
            )
        # Save the complete intended population before the first object mutation, matching
        # `water_gauges.py`'s checkpoint discipline for object-store retries.
        self.merge = merged
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


__all__ = [
    "WEATHER_OBSERVATIONS_DIRECT_KIND",
    "DirectWeatherObservationsError",
    "DirectWeatherObservationsForwardAdapter",
    "DirectWeatherObservationsMerge",
    "DirectWeatherObservationsWriteResult",
    "merge_weather_observations_day",
]
