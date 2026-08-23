"""The weather-observations side lane's serving read and its (deliberately partial) source validation.

Two sections, one per artifact this file is the sole test coverage for: `planes/weather_observations.py`
(Polars serving read over the `kind=observed` object-store Parquet plane) and
`pipeline/validation/weather_observations.py` (the structural checks this producer's shape allows,
and the explicit statement of the value-level reconciliation it cannot perform). No test here touches
a live database, a live bucket, or the live Open-Meteo API -- Parquet bytes come from `ObjectStore`
over `RecordingBackend` (mirrors `tests/parquet/test_weather_observations_lane.py`), materialized to
a local `tmp_path` so `polars.scan_parquet` reads real files with no network or S3 credentials.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.config import ObjectStoreCredentials
from agri_data_service.foundation.parquet.paths import MAX_GAP_WINDOW_DAYS
from agri_data_service.ingest.open_meteo import MAX_OBSERVATION_AGE, bounded_sample_points
from agri_data_service.ingest.policy import MAX_FUTURE_SKEW, UNCONFIGURED_BBOX_REASON
from agri_data_service.pipeline.lanes.weather_observations import (
    read_weather_observations_day as fetch_exported_day_table,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.validation.weather_observations import (
    LATTICE_COVERAGE_CHECK,
    WeatherObservationsValidationError,
    WeatherObservationsValidityCheck,
    validate_weather_observations_partition,
)
from agri_data_service.planes.weather_observations import (
    OBSERVED_KIND,
    WeatherObservationsServingError,
    bucket_object_root,
    read_weather_observations_day,
    read_weather_observations_window,
    weather_observations_scan_pattern,
)
from agri_data_service.warehouse.schemas.weather_observations import (
    WEATHER_OBSERVATIONS_SCHEMA,
    WEATHER_OBSERVATIONS_STREAM,
)
from tests.parquet.test_objectstore_writer import RecordingBackend
from tests.parquet.test_weather_observations_lane import (
    AUGUST_SIXTH,
    LAYER_ID,
    RecordingSession,
    weather_observation_row,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

FAR_FUTURE_DAY = date(2099, 1, 1)


def _table_from_rows(rows: Sequence[dict[str, object]]) -> pa.Table:
    """Build a table conforming to `WEATHER_OBSERVATIONS_SCHEMA` from exporter-shaped row dicts."""
    columns = {name: [row[name] for row in rows] for name in WEATHER_OBSERVATIONS_SCHEMA.column_names}
    return pa.table(columns).cast(WEATHER_OBSERVATIONS_SCHEMA.arrow_schema)


def _materialize(backend: RecordingBackend, root: Path) -> None:
    """Write every object `backend` recorded to real files under `root`, at the same relative keys."""
    for key, payload in backend.objects.items():
        target = root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


# --- planes/weather_observations.py: the serving read --------------------------------------------


def test_scan_pattern_only_ever_targets_the_observed_kind() -> None:
    """There is no parameter that could widen this to `kind=forecast` -- the path is hard-coded."""
    pattern = weather_observations_scan_pattern(root="s3://bucket/prefix")

    assert pattern == f"s3://bucket/prefix/layer={WEATHER_OBSERVATIONS_STREAM}/kind=observed/**/*.parquet"
    assert "kind=forecast" not in pattern


def test_bucket_object_root_honors_an_empty_or_set_store_prefix() -> None:
    credentials = ObjectStoreCredentials(
        endpoint_url="https://storage.example.com",
        region="sjc",
        bucket="plantgeo-parquet",
        access_key_id="access-key",
        secret_access_key="secret-key",
    )

    assert (
        bucket_object_root(credentials=credentials, store=ObjectStore(RecordingBackend())) == "s3://plantgeo-parquet/"
    )
    assert (
        bucket_object_root(credentials=credentials, store=ObjectStore(RecordingBackend(), prefix="sandbox"))
        == "s3://plantgeo-parquet/sandbox/"
    )


def test_read_weather_observations_day_preserves_every_distinct_instant_at_one_point(tmp_path: Path) -> None:
    """The grain is (latitude, longitude, observed_at), not a day key: several polls can land per point per day."""
    first_poll = weather_observation_row(43.6150, -116.2023, external_id="a")
    second_poll = dict(first_poll)
    second_poll["external_id"] = "b"
    second_poll["observed_at"] = datetime(2026, 8, 6, 15, 0, 0, tzinfo=UTC)
    second_poll["ingested_at"] = datetime(2026, 8, 6, 15, 1, 0, tzinfo=UTC)
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(
        _table_from_rows([first_poll, second_poll]),
        layer=WEATHER_OBSERVATIONS_STREAM,
        kind="observed",
        day=AUGUST_SIXTH,
    )
    _materialize(backend, tmp_path)
    root = tmp_path.resolve().as_posix()

    reading = read_weather_observations_day(root=root, day=AUGUST_SIXTH)

    expected_row_count = 2
    assert reading.kind == OBSERVED_KIND
    assert reading.frame.height == expected_row_count
    assert set(reading.frame["observed_at"].to_list()) == {first_poll["observed_at"], second_poll["observed_at"]}


def test_read_weather_observations_day_is_an_honest_empty_when_nothing_was_ever_polled(tmp_path: Path) -> None:
    """A stream nothing has ever been written to hits the zero-glob-match path, not an exception."""
    root = tmp_path.resolve().as_posix()

    reading = read_weather_observations_day(root=root, day=AUGUST_SIXTH)

    assert reading.frame.is_empty()
    assert reading.frame.columns == list(WEATHER_OBSERVATIONS_SCHEMA.column_names)


def test_read_weather_observations_day_is_an_honest_empty_for_a_future_date_not_a_signal_fallback(
    tmp_path: Path,
) -> None:
    """A future date must never fall back to the `signal` stream's forecast -- this lane simply has none."""
    row = weather_observation_row(43.6150, -116.2023, external_id="a")
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(_table_from_rows([row]), layer=WEATHER_OBSERVATIONS_STREAM, kind="observed", day=AUGUST_SIXTH)
    _materialize(backend, tmp_path)
    root = tmp_path.resolve().as_posix()

    reading = read_weather_observations_day(root=root, day=FAR_FUTURE_DAY)

    assert reading.frame.is_empty()
    assert reading.kind == OBSERVED_KIND


def test_read_weather_observations_window_refuses_a_backwards_or_oversized_window(tmp_path: Path) -> None:
    root = tmp_path.resolve().as_posix()

    with pytest.raises(WeatherObservationsServingError, match="backwards"):
        read_weather_observations_window(root=root, first_day=AUGUST_SIXTH, last_day=date(2026, 8, 1))

    with pytest.raises(WeatherObservationsServingError, match="budget"):
        read_weather_observations_window(
            root=root, first_day=date(2000, 1, 1), last_day=date(2000, 1, 1) + timedelta(days=MAX_GAP_WINDOW_DAYS + 1)
        )


# --- pipeline/validation/weather_observations.py: what can and cannot be reconciled --------------


def test_a_clean_partition_reports_no_failing_checks_and_states_the_reconciliation_gap() -> None:
    table = _table_from_rows(
        [
            weather_observation_row(43.6150, -116.2023, external_id="a"),
            weather_observation_row(43.7, -116.1, external_id="b"),
        ]
    )
    expected_row_count = 2

    report = validate_weather_observations_partition(table, day=AUGUST_SIXTH)

    assert report.is_structurally_clean
    assert report.failing_checks == ()
    assert report.row_count == expected_row_count
    assert AUGUST_SIXTH.isoformat() in report.source_reconciliation_note
    assert "retains no history" in report.source_reconciliation_note
    lattice_check = next(check for check in report.checks if check.name == LATTICE_COVERAGE_CHECK)
    assert not lattice_check.evaluated
    assert lattice_check.skipped_reason == UNCONFIGURED_BBOX_REASON


@pytest.mark.asyncio
async def test_validating_the_table_the_real_exporter_produces_is_clean() -> None:
    """End-to-end proof: the exact table `pipeline/lanes/weather_observations.py` writes validates clean."""
    session = RecordingSession(
        rows=[
            weather_observation_row(43.6150, -116.2023, external_id="a"),
            weather_observation_row(43.7, -116.1, external_id="b"),
        ]
    )

    table = await fetch_exported_day_table(session, day=AUGUST_SIXTH, layer_id=LAYER_ID)  # type: ignore[arg-type]
    report = validate_weather_observations_partition(table, day=AUGUST_SIXTH)

    assert report.is_structurally_clean


def test_an_empty_table_is_refused_rather_than_reported_as_a_clean_bill_of_health() -> None:
    empty_table = pa.table({name: [] for name in WEATHER_OBSERVATIONS_SCHEMA.column_names}).cast(
        WEATHER_OBSERVATIONS_SCHEMA.arrow_schema
    )

    with pytest.raises(WeatherObservationsValidationError, match="zero-row"):
        validate_weather_observations_partition(empty_table, day=AUGUST_SIXTH)


def test_day_scope_check_catches_a_row_exported_under_the_wrong_day() -> None:
    row = weather_observation_row(43.6150, -116.2023, external_id="a")
    row["observed_day"] = date(2026, 8, 7)
    table = _table_from_rows([row])

    report = validate_weather_observations_partition(table, day=AUGUST_SIXTH)

    check = next(c for c in report.checks if c.name == "day_scope")
    assert check.is_failing
    assert check.count == 1
    assert not report.is_structurally_clean


def test_observed_day_consistency_check_catches_a_mismatched_calendar_date() -> None:
    row = weather_observation_row(43.6150, -116.2023, external_id="a")
    row["observed_at"] = datetime(2026, 8, 7, 1, 0, 0, tzinfo=UTC)  # observed_day is left at AUGUST_SIXTH
    table = _table_from_rows([row])

    report = validate_weather_observations_partition(table, day=AUGUST_SIXTH)

    check = next(c for c in report.checks if c.name == "observed_day_consistency")
    assert check.is_failing
    assert check.count == 1


def test_duplicate_grain_check_catches_two_rows_sharing_one_instant() -> None:
    row_a = weather_observation_row(43.6150, -116.2023, external_id="a")
    row_b = dict(row_a)
    row_b["external_id"] = "b"
    row_b["feature_id"] = "a-different-feature-row"
    table = _table_from_rows([row_a, row_b])

    report = validate_weather_observations_partition(table, day=AUGUST_SIXTH)

    check = next(c for c in report.checks if c.name == "duplicate_grain")
    assert check.is_failing
    assert check.count == 1  # one EXTRA row beyond the first at that (latitude, longitude, observed_at)


def test_unexpected_source_check_catches_a_non_open_meteo_row() -> None:
    row = weather_observation_row(43.6150, -116.2023, external_id="a")
    row["source"] = "Some Other Producer"
    table = _table_from_rows([row])

    report = validate_weather_observations_partition(table, day=AUGUST_SIXTH)

    check = next(c for c in report.checks if c.name == "unexpected_source")
    assert check.is_failing
    assert check.count == 1


def test_value_bound_check_catches_an_out_of_range_temperature() -> None:
    row = weather_observation_row(43.6150, -116.2023, external_id="a")
    row["temperature_c"] = 999.0
    table = _table_from_rows([row])

    report = validate_weather_observations_partition(table, day=AUGUST_SIXTH)

    check = next(c for c in report.checks if c.name == "value_bounds_temperature_c")
    assert check.is_failing
    assert check.count == 1


def test_stale_observation_check_catches_a_reading_older_than_the_freshness_bound() -> None:
    row = weather_observation_row(43.6150, -116.2023, external_id="a")
    observed_at = row["observed_at"]
    assert isinstance(observed_at, datetime)
    row["ingested_at"] = observed_at + MAX_OBSERVATION_AGE + timedelta(minutes=1)
    table = _table_from_rows([row])

    report = validate_weather_observations_partition(table, day=AUGUST_SIXTH)

    check = next(c for c in report.checks if c.name == "stale_observation")
    assert check.is_failing
    assert check.count == 1


def test_future_skewed_observation_check_catches_a_clock_skewed_instant() -> None:
    row = weather_observation_row(43.6150, -116.2023, external_id="a")
    ingested_at = row["ingested_at"]
    assert isinstance(ingested_at, datetime)
    row["observed_at"] = ingested_at + MAX_FUTURE_SKEW + timedelta(minutes=1)
    table = _table_from_rows([row])

    report = validate_weather_observations_partition(table, day=AUGUST_SIXTH)

    check = next(c for c in report.checks if c.name == "future_skewed_observation")
    assert check.is_failing
    assert check.count == 1


def test_lattice_coverage_check_reports_the_unpolled_points_of_a_configured_grid() -> None:
    bbox = "-116.5,43.0,-115.5,44.0"
    spacing_degrees = 0.5
    expected_points = bounded_sample_points(bbox, spacing_degrees)
    minimum_expected_points = 2
    assert len(expected_points) >= minimum_expected_points
    only_the_first_point = weather_observation_row(expected_points[0][0], expected_points[0][1], external_id="a")
    table = _table_from_rows([only_the_first_point])

    report = validate_weather_observations_partition(
        table, day=AUGUST_SIXTH, configured_bbox=bbox, configured_sample_spacing_degrees=spacing_degrees
    )

    check = next(c for c in report.checks if c.name == LATTICE_COVERAGE_CHECK)
    assert check.evaluated
    assert check.count == len(expected_points) - 1


def test_lattice_coverage_check_is_clean_when_every_configured_point_answered() -> None:
    bbox = "-116.5,43.0,-115.5,44.0"
    spacing_degrees = 0.5
    expected_points = bounded_sample_points(bbox, spacing_degrees)
    rows = [
        weather_observation_row(latitude, longitude, external_id=f"p{index}")
        for index, (latitude, longitude) in enumerate(expected_points)
    ]
    table = _table_from_rows(rows)

    report = validate_weather_observations_partition(
        table, day=AUGUST_SIXTH, configured_bbox=bbox, configured_sample_spacing_degrees=spacing_degrees
    )

    check = next(c for c in report.checks if c.name == LATTICE_COVERAGE_CHECK)
    assert check.evaluated
    assert check.count == 0
    assert not check.is_failing


def test_validity_check_requires_a_skip_reason_when_unevaluated() -> None:
    with pytest.raises(WeatherObservationsValidationError, match="skip reason"):
        WeatherObservationsValidityCheck(name="x", breaks="y", evaluated=False)


def test_validity_check_rejects_a_skip_reason_on_an_evaluated_check() -> None:
    with pytest.raises(WeatherObservationsValidationError, match="skip reason"):
        WeatherObservationsValidityCheck(name="x", breaks="y", evaluated=True, skipped_reason="nope")


def test_validity_check_rejects_a_nonzero_count_on_an_unevaluated_check() -> None:
    with pytest.raises(WeatherObservationsValidationError, match="count"):
        WeatherObservationsValidityCheck(name="x", breaks="y", evaluated=False, skipped_reason="because", count=1)
