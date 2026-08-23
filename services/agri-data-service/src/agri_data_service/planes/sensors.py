"""DuckDB/Polars serving read for the `sensors` lane's Parquet stream.

Layer L3 (planes): may import foundation, method, warehouse, pipeline; may NOT import interface.
See `conductor/code_styleguides/layer-lanes.md` section 2: `kind` is a partition, not a column
branch. Every function here is scoped to exactly one `kind` for its whole call; nothing here ever
reads both `observed` and `forecast` into one answer, and neither ever appears as a data column.

Coverage is discovered by LISTING object keys (`pipeline/parquet/AGENTS.md`: "gap detection is a
listing, not a scan"), never by opening files to look for gaps. Row content is then read with a
`pl.scan_parquet` glob scoped to the single already-confirmed-present `kind`, with
`hive_partitioning` explicitly disabled so `kind`/`year`/`month`/`day` never leak back in as data
columns -- the frozen layout's partition keys stay partition keys, and the schema this module
returns is exactly `SENSORS_SCHEMA`'s, nothing more.

The sensors grain is TALL: one row per (sensor_id, observed_day, measurement_name) that was
actually reported (`warehouse/schemas/sensors.py`). This module never pivots to a wide shape and
never fabricates the twelve-or-so measurement names a given row does not carry -- a station that
reported 3 of 16 fields on a day contributes exactly 3 rows, and a caller asking for a measurement
name a station never reported gets zero rows for it, never a null standing in for "not reported".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final

import polars as pl

from agri_data_service.foundation.parquet.paths import MONTHS_PER_YEAR, partition_day_statuses, stream_prefix
from agri_data_service.warehouse.schemas.sensors import SENSORS_SCHEMA, SENSORS_STREAM

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agri_data_service.foundation.parquet.paths import PartitionDayStatus, PartitionKind
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

# A serving read stays bounded even if a caller forgets to narrow it: one year plus margin
# comfortably covers the map's lookback plus the lane's 30-day forecast horizon.
MAX_QUERY_WINDOW_DAYS: Final = 400


class SensorsPlaneError(ValueError):
    """Raised when a sensors-plane read is asked for something it cannot honestly answer."""


@dataclass(frozen=True, slots=True)
class SensorsPlaneCoverage:
    """Per-day partition status for one requested window of exactly one kind."""

    kind: PartitionKind
    first_day: date
    last_day: date
    statuses: Mapping[date, PartitionDayStatus]

    @property
    def data_days(self) -> tuple[date, ...]:
        """Days a Parquet part file was actually written for."""
        return tuple(sorted(day for day, status in self.statuses.items() if status == "data"))

    @property
    def absent_days(self) -> tuple[date, ...]:
        """Days carrying a governed-absence marker: a deliberate, evidenced non-write."""
        return tuple(sorted(day for day, status in self.statuses.items() if status == "absent"))

    @property
    def missing_days(self) -> tuple[date, ...]:
        """Days covered by neither data nor a governed absence: a real, unexplained gap."""
        return tuple(sorted(day for day, status in self.statuses.items() if status == "missing"))

    @property
    def conflict_days(self) -> tuple[date, ...]:
        """Days carrying both data and an absence marker: only a manual admin slip produces this."""
        return tuple(sorted(day for day, status in self.statuses.items() if status == "conflict"))


@dataclass(frozen=True, slots=True)
class SensorsPlaneResult:
    """One kind-scoped serving read: its coverage evidence plus the rows it could actually answer."""

    kind: PartitionKind
    coverage: SensorsPlaneCoverage
    readings: pl.DataFrame


def _validate_window(first_day: date, last_day: date) -> None:
    if last_day < first_day:
        raise SensorsPlaneError(f"window {first_day.isoformat()}..{last_day.isoformat()} runs backwards")
    span_days = (last_day - first_day).days + 1
    if span_days > MAX_QUERY_WINDOW_DAYS:
        raise SensorsPlaneError(
            f"a sensors-plane read window of {span_days} days exceeds the {MAX_QUERY_WINDOW_DAYS}-day budget"
        )


def _year_months(first_day: date, last_day: date) -> tuple[tuple[int, int], ...]:
    """Return every distinct (year, month) the window touches, in order -- bounds the listing fan-out."""
    months: list[tuple[int, int]] = []
    cursor = date(first_day.year, first_day.month, 1)
    while cursor <= last_day:
        months.append((cursor.year, cursor.month))
        next_month = 1 if cursor.month == MONTHS_PER_YEAR else cursor.month + 1
        next_year = cursor.year + 1 if cursor.month == MONTHS_PER_YEAR else cursor.year
        cursor = date(next_year, next_month, 1)
    return tuple(months)


def sensors_plane_coverage(
    store: ObjectStore,
    *,
    kind: PartitionKind,
    first_day: date,
    last_day: date,
) -> SensorsPlaneCoverage:
    """Classify every day in [first_day, last_day] for one kind by LISTING objects, never scanning them."""
    _validate_window(first_day, last_day)
    keys: list[str] = []
    for year, month in _year_months(first_day, last_day):
        keys.extend(store.list_partition_keys(SENSORS_STREAM, kind, year=year, month=month))
    statuses = partition_day_statuses(
        layer=SENSORS_STREAM, kind=kind, first_day=first_day, last_day=last_day, keys=keys
    )
    return SensorsPlaneCoverage(kind=kind, first_day=first_day, last_day=last_day, statuses=statuses)


def _empty_readings_frame() -> pl.DataFrame:
    frame = pl.from_arrow(SENSORS_SCHEMA.arrow_schema.empty_table())
    if not isinstance(frame, pl.DataFrame):
        raise SensorsPlaneError("pl.from_arrow of an empty Arrow table unexpectedly returned a Series")
    return frame


def _sensors_glob(bucket_root: str, store: ObjectStore, kind: PartitionKind) -> str:
    return f"{bucket_root.rstrip('/')}/{store.key_for(stream_prefix(SENSORS_STREAM, kind))}**/*.parquet"


def read_sensors_readings(  # noqa: PLR0913
    store: ObjectStore,
    *,
    bucket_root: str,
    storage_options: Mapping[str, str],
    kind: PartitionKind,
    first_day: date,
    last_day: date,
    sensor_ids: Sequence[str] | None = None,
    measurement_names: Sequence[str] | None = None,
) -> SensorsPlaneResult:
    """Read one kind's readings over one bounded window: list for coverage, then a Hive-pruned scan.

    `bucket_root` is the bucket-and-prefix URI a Parquet path lives under (e.g.
    `s3://plantgeo-warehouse` in production, using `store.prefix` beneath it; a local directory
    in a test backed by a file-based `ObjectStoreBackend`). `storage_options` is
    `polars_storage_options(credentials)` in production and `{}` against a local root.

    Never fabricates rows: a coverage day with `status="data"` contributes exactly the rows its
    Parquet partition holds, filtered to the caller's `sensor_ids`/`measurement_names` if given; a
    station that did not report a measurement contributes no row for it, not a null one. If
    listing finds no data day in the window at all, this returns an empty, correctly-typed frame
    without ever calling `pl.scan_parquet` -- a `kind=forecast` window with nothing written yet
    (no writer exists for this lane as of this module) answers honestly rather than raising on a
    glob that matches zero files.
    """
    coverage = sensors_plane_coverage(store, kind=kind, first_day=first_day, last_day=last_day)
    if not coverage.data_days:
        return SensorsPlaneResult(kind=kind, coverage=coverage, readings=_empty_readings_frame())

    glob = _sensors_glob(bucket_root, store, kind)
    lazy = pl.scan_parquet(glob, storage_options=dict(storage_options), hive_partitioning=False)
    lazy = lazy.filter(pl.col("observed_day").is_between(first_day, last_day, closed="both"))
    if sensor_ids is not None:
        lazy = lazy.filter(pl.col("sensor_id").is_in(list(sensor_ids)))
    if measurement_names is not None:
        lazy = lazy.filter(pl.col("measurement_name").is_in(list(measurement_names)))
    readings = lazy.sort(list(SENSORS_SCHEMA.sort_columns)).collect()

    missing_columns = set(SENSORS_SCHEMA.column_names) - set(readings.columns)
    if missing_columns:
        raise SensorsPlaneError(
            f"sensors {kind} Parquet is missing columns the registered schema requires: {sorted(missing_columns)}"
        )
    return SensorsPlaneResult(kind=kind, coverage=coverage, readings=readings)
