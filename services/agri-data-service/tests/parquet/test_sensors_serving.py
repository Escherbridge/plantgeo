"""The sensors-plane serving read: coverage-by-listing, kind isolation, and the tall-grain contract.

Exercises `planes/sensors.py` against a local-file-backed `ObjectStoreBackend` -- real Parquet
bytes on disk, read back through the same `pl.scan_parquet` path production uses against the
bucket, with `storage_options={}` standing in for `polars_storage_options(credentials)`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.pipeline.parquet.objectstore import ListedObject, ObjectStore
from agri_data_service.planes.sensors import (
    SensorsPlaneError,
    read_sensors_readings,
    sensors_plane_coverage,
)
from agri_data_service.warehouse.schemas.sensors import SENSORS_SCHEMA, SENSORS_STREAM

JULY_THIRTIETH = date(2026, 7, 30)
AUGUST_FIRST = date(2026, 8, 1)
AUGUST_SECOND = date(2026, 8, 2)
AUGUST_THIRD = date(2026, 8, 3)


class LocalFileBackend:
    """`ObjectStoreBackend` over a temp directory: the exact key layout, real bytes, no network."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, key: str, payload: bytes, *, content_type: str) -> None:  # noqa: ARG002
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    def list_objects(self, prefix: str) -> Iterator[ListedObject]:
        if not self.root.exists():
            return
        for path in sorted(self.root.rglob("*")):
            if path.is_file():
                relative = path.relative_to(self.root).as_posix()
                if relative.startswith(prefix):
                    # A file's mtime is this backend's honest analogue of S3's `LastModified`.
                    yield ListedObject(key=relative, last_modified=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC))

    def size_of(self, key: str) -> int | None:
        path = self.root / key
        return path.stat().st_size if path.exists() else None


def sensor_table(*, day: date, station_id: str, measurements: tuple[str, ...] = ("temperature",)) -> pa.Table:
    """One station-day's tall-grain rows, shaped exactly like `pipeline/lanes/sensors.py`'s export."""
    count = len(measurements)
    return pa.table(
        {
            "sensor_id": pa.array([station_id] * count, pa.string()),
            "station_name": pa.array([f"{station_id} STATION"] * count, pa.string()),
            "network": pa.array(["ASOS"] * count, pa.string()),
            "observed_day": pa.array([day] * count, pa.date32()),
            "observed_at": pa.array(
                [datetime(day.year, day.month, day.day, 23, tzinfo=UTC)] * count, pa.timestamp("us", tz="UTC")
            ),
            "measurement_name": pa.array(list(measurements), pa.string()),
            "value": pa.array([21.5] * count, pa.float64()),
            "unit_code": pa.array(["wmoUnit:degC"] * count, pa.string()),
            "quality_control": pa.array(["V"] * count, pa.string()),
            "feature_id": pa.array([f"feature-{station_id}-{day.isoformat()}"] * count, pa.string()),
            "data_available_at": pa.array([None] * count, pa.timestamp("us", tz="UTC")),
        }
    ).cast(SENSORS_SCHEMA.arrow_schema)


def _store(tmp_path: Path) -> ObjectStore:
    return ObjectStore(LocalFileBackend(tmp_path))


def test_coverage_classifies_data_absent_and_missing_days(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write_partition(
        sensor_table(day=AUGUST_FIRST, station_id="KMSO"), layer=SENSORS_STREAM, kind="observed", day=AUGUST_FIRST
    )
    store.write_absence(
        GovernedAbsence(
            reason="no station in the coverage box reported anything",
            upstream_response="200 OK, zero features",
            recorded_at=datetime(2026, 8, 2, tzinfo=UTC),
            run_id="test-run-1",
        ),
        layer=SENSORS_STREAM,
        kind="observed",
        day=AUGUST_SECOND,
    )

    coverage = sensors_plane_coverage(store, kind="observed", first_day=AUGUST_FIRST, last_day=AUGUST_THIRD)

    assert coverage.data_days == (AUGUST_FIRST,)
    assert coverage.absent_days == (AUGUST_SECOND,)
    assert coverage.missing_days == (AUGUST_THIRD,)
    assert coverage.conflict_days == ()


def test_reading_returns_exactly_the_written_tall_rows_without_fabricating_missing_measurements(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.write_partition(
        sensor_table(day=AUGUST_FIRST, station_id="KMSO", measurements=("temperature", "windSpeed")),
        layer=SENSORS_STREAM,
        kind="observed",
        day=AUGUST_FIRST,
    )

    result = read_sensors_readings(
        store,
        bucket_root=tmp_path.as_posix(),
        storage_options={},
        kind="observed",
        first_day=AUGUST_FIRST,
        last_day=AUGUST_FIRST,
    )

    expected_row_count = 2  # exactly the two measurements KMSO reported, nothing fabricated for the other 14
    assert result.readings.height == expected_row_count
    assert set(result.readings["measurement_name"].to_list()) == {"temperature", "windSpeed"}
    assert set(result.readings.columns) == set(SENSORS_SCHEMA.column_names)


def test_forecast_kind_never_falls_through_to_observed_data(tmp_path: Path) -> None:
    """`kind` is a partition, not a column branch: a forecast read must never surface observed rows."""
    store = _store(tmp_path)
    store.write_partition(
        sensor_table(day=AUGUST_FIRST, station_id="KMSO"), layer=SENSORS_STREAM, kind="observed", day=AUGUST_FIRST
    )

    result = read_sensors_readings(
        store,
        bucket_root=tmp_path.as_posix(),
        storage_options={},
        kind="forecast",
        first_day=AUGUST_FIRST,
        last_day=AUGUST_FIRST,
    )

    assert result.readings.height == 0
    assert result.coverage.missing_days == (AUGUST_FIRST,)
    assert result.coverage.data_days == ()


def test_sensor_id_and_measurement_name_filters_narrow_the_read(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write_partition(
        pa.concat_tables(
            [
                sensor_table(day=AUGUST_FIRST, station_id="KMSO", measurements=("temperature", "windSpeed")),
                sensor_table(day=AUGUST_FIRST, station_id="KDLN", measurements=("temperature",)),
            ]
        ),
        layer=SENSORS_STREAM,
        kind="observed",
        day=AUGUST_FIRST,
    )

    result = read_sensors_readings(
        store,
        bucket_root=tmp_path.as_posix(),
        storage_options={},
        kind="observed",
        first_day=AUGUST_FIRST,
        last_day=AUGUST_FIRST,
        sensor_ids=("KMSO",),
        measurement_names=("windSpeed",),
    )

    assert result.readings.height == 1
    assert result.readings["sensor_id"].to_list() == ["KMSO"]
    assert result.readings["measurement_name"].to_list() == ["windSpeed"]


def test_window_spanning_two_months_is_covered(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write_partition(
        sensor_table(day=JULY_THIRTIETH, station_id="KGPI"), layer=SENSORS_STREAM, kind="observed", day=JULY_THIRTIETH
    )
    store.write_partition(
        sensor_table(day=AUGUST_FIRST, station_id="KGPI"), layer=SENSORS_STREAM, kind="observed", day=AUGUST_FIRST
    )

    result = read_sensors_readings(
        store,
        bucket_root=tmp_path.as_posix(),
        storage_options={},
        kind="observed",
        first_day=JULY_THIRTIETH,
        last_day=AUGUST_FIRST,
    )

    assert sorted(result.readings["observed_day"].to_list()) == [JULY_THIRTIETH, AUGUST_FIRST]
    assert result.coverage.data_days == (JULY_THIRTIETH, AUGUST_FIRST)


def test_backwards_window_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(SensorsPlaneError, match="runs backwards"):
        sensors_plane_coverage(store, kind="observed", first_day=AUGUST_SECOND, last_day=AUGUST_FIRST)


def test_empty_window_returns_a_correctly_typed_zero_row_frame_without_scanning(tmp_path: Path) -> None:
    store = _store(tmp_path)

    result = read_sensors_readings(
        store,
        bucket_root=tmp_path.as_posix(),
        storage_options={},
        kind="observed",
        first_day=AUGUST_FIRST,
        last_day=AUGUST_THIRD,
    )

    assert result.readings.height == 0
    assert set(result.readings.columns) == set(SENSORS_SCHEMA.column_names)
    assert result.coverage.missing_days == (AUGUST_FIRST, AUGUST_SECOND, AUGUST_THIRD)
