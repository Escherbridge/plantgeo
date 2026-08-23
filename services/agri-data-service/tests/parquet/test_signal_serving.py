"""The signal lane's three remaining artifacts: the serving read, the Monte Carlo forecast, and
source-system validation.

Three sections, one per module, since this is the one test file the signal lane's wave-2 slice
owns (`conductor/code_styleguides/layer-lanes.md` section 1 puts one file per layer per lane, but
a single lane's test coverage lives together rather than splitting three ways for no reader's
benefit).
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import numpy
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from agri_data_service.config import ObjectStoreCredentials
from agri_data_service.execution.coverage_contract import DayCoverage, DayState
from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.method.monte_carlo.signal import (
    ERA5_LAND_PUBLICATION_LAG_DAYS,
    METHOD_NAME_ADDITIVE_ANOMALY,
    METHOD_NAME_EMPIRICAL_RESAMPLE,
    NASA_POWER_PUBLICATION_LAG_DAYS,
    PUBLISHED_QUANTILES,
    SURFACE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS,
    InsufficientSignalHistoryError,
    ObservedSignalDay,
    SignalSeriesSpecError,
    SimulationRequest,
    issued_on_for,
    series_spec_for,
    simulate_signal_forecast,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.validation.signal import (
    SignalLaneOutcome,
    SignalValidationError,
    classify_signal_day,
    exported_signal_row_counts,
    find_missing_export_partitions,
)
from agri_data_service.planes.signal import (
    MAX_TIME_WINDOW_DAYS,
    SignalPlaneReadError,
    SignalPlaneSource,
    read_signal_time_window,
    read_signal_value_on_day,
)
from agri_data_service.warehouse.parquet.schema import SIGNAL_PLANE_SCHEMA, SIGNAL_PLANE_STREAM
from tests.parquet.test_governed_absence import sample_absence
from tests.parquet.test_objectstore_writer import RecordingBackend, with_forecast_provenance

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from agri_data_service.foundation.parquet.paths import PartitionKind

# === shared fixtures =============================================================================

A_DAY = date(2026, 7, 15)
EXPECTED_FULL_LATTICE_CELLS = 397


def _signal_table(
    *, day: date, cell_ids: Sequence[str], signal_name: str = "precipitation", value: float = 1.0
) -> pa.Table:
    """One day's exported grain for `cell_ids`, all carrying `signal_name`/`value`."""
    count = len(cell_ids)
    return pa.table(
        {
            "support_key": ["surface"] * count,
            "signal_name": [signal_name] * count,
            "normalized_unit": ["mm/day"] * count,
            "cell_id": list(cell_ids),
            "observed_day": [day] * count,
            "normalized_value": [value] * count,
            "observation_count": [1] * count,
            "newest_observed_at": [datetime(day.year, day.month, day.day, 12, tzinfo=UTC)] * count,
            "coverage_fraction": [1.0] * count,
            "allowed_client_exposure": [False] * count,
        }
    ).cast(SIGNAL_PLANE_SCHEMA.arrow_schema)


def _write_partition(base: Path, *, kind: PartitionKind, day: date, table: pa.Table) -> None:
    relative = partition_path(SIGNAL_PLANE_STREAM, kind, day)
    path = base / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


# === planes/signal.py: the serving read ==========================================================


class TestSignalPlaneServingRead:
    def test_reads_exactly_one_days_observed_rows(self, tmp_path: Path) -> None:
        _write_partition(tmp_path, kind="observed", day=A_DAY, table=_signal_table(day=A_DAY, cell_ids=["c1", "c2"]))
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        frame = read_signal_value_on_day(source, kind="observed", day=A_DAY)

        expected_row_count = 2
        assert frame.height == expected_row_count
        assert set(frame["kind"].to_list()) == {"observed"}
        assert set(frame["cell_id"].to_list()) == {"c1", "c2"}
        assert frame["observed_day"].to_list() == [A_DAY, A_DAY]

    def test_never_blends_observed_and_forecast_for_the_same_day(self, tmp_path: Path) -> None:
        """The whole point of `kind` as a partition: a reader can never fall through to the other one."""
        _write_partition(
            tmp_path, kind="observed", day=A_DAY, table=_signal_table(day=A_DAY, cell_ids=["c1"], value=1.0)
        )
        _write_partition(
            tmp_path, kind="forecast", day=A_DAY, table=_signal_table(day=A_DAY, cell_ids=["c1"], value=99.0)
        )
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        observed = read_signal_value_on_day(source, kind="observed", day=A_DAY)
        forecast = read_signal_value_on_day(source, kind="forecast", day=A_DAY)

        assert observed["normalized_value"].to_list() == [1.0]
        assert observed["kind"].to_list() == ["observed"]
        assert forecast["normalized_value"].to_list() == [99.0]
        assert forecast["kind"].to_list() == ["forecast"]

    def test_a_day_with_nothing_written_returns_an_empty_typed_frame_not_an_error(self, tmp_path: Path) -> None:
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        frame = read_signal_value_on_day(source, kind="observed", day=A_DAY)

        expected_columns = {"support_key", "signal_name", "cell_id", "observed_day", "normalized_value", "kind"}
        assert frame.height == 0
        assert set(frame.columns) >= expected_columns

    def test_cell_and_signal_filters_narrow_the_result(self, tmp_path: Path) -> None:
        table = _signal_table(day=A_DAY, cell_ids=["c1", "c2", "c3"])
        _write_partition(tmp_path, kind="observed", day=A_DAY, table=table)
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        frame = read_signal_value_on_day(source, kind="observed", day=A_DAY, cell_ids=["c2"])

        assert frame["cell_id"].to_list() == ["c2"]

    def test_signal_name_filter_narrows_the_result(self, tmp_path: Path) -> None:
        table = pa.concat_tables(
            [
                _signal_table(day=A_DAY, cell_ids=["c1"], signal_name="precipitation"),
                _signal_table(day=A_DAY, cell_ids=["c1"], signal_name="wind_speed"),
            ]
        )
        _write_partition(tmp_path, kind="observed", day=A_DAY, table=table)
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        frame = read_signal_value_on_day(source, kind="observed", day=A_DAY, signal_names=["wind_speed"])

        assert frame["signal_name"].to_list() == ["wind_speed"]

    def test_time_window_spans_multiple_calendar_months(self, tmp_path: Path) -> None:
        first_day = date(2026, 6, 30)
        last_day = date(2026, 7, 4)
        _write_partition(tmp_path, kind="observed", day=first_day, table=_signal_table(day=first_day, cell_ids=["c1"]))
        _write_partition(tmp_path, kind="observed", day=last_day, table=_signal_table(day=last_day, cell_ids=["c1"]))
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        frame = read_signal_time_window(source, kind="observed", first_day=first_day, last_day=last_day)

        expected_row_count = 2
        assert frame.height == expected_row_count
        assert sorted(frame["observed_day"].to_list()) == [first_day, last_day]

    def test_time_window_tolerates_a_month_with_nothing_written(self, tmp_path: Path) -> None:
        """One present month plus one empty month must not raise -- an absent month is not an error."""
        present_day = date(2026, 7, 4)
        _write_partition(
            tmp_path, kind="observed", day=present_day, table=_signal_table(day=present_day, cell_ids=["c1"])
        )
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        frame = read_signal_time_window(source, kind="observed", first_day=date(2026, 5, 1), last_day=present_day)

        assert frame.height == 1
        assert frame["observed_day"].to_list() == [present_day]

    def test_a_window_with_nothing_written_anywhere_returns_an_empty_frame(self, tmp_path: Path) -> None:
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        frame = read_signal_time_window(source, kind="observed", first_day=date(2026, 1, 1), last_day=date(2026, 1, 31))

        assert frame.height == 0

    def test_a_window_over_the_serving_budget_is_refused(self, tmp_path: Path) -> None:
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        with pytest.raises(SignalPlaneReadError, match="budget"):
            read_signal_time_window(
                source,
                kind="observed",
                first_day=date(2020, 1, 1),
                last_day=date(2020, 1, 1) + timedelta(days=MAX_TIME_WINDOW_DAYS + 1),
            )

    def test_a_backwards_window_is_refused(self, tmp_path: Path) -> None:
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        with pytest.raises(SignalPlaneReadError, match="backwards"):
            read_signal_time_window(source, kind="observed", first_day=A_DAY, last_day=A_DAY - timedelta(days=1))

    def test_source_root_uri_always_ends_with_a_slash(self) -> None:
        source = SignalPlaneSource(root_uri="C:/tmp/no-trailing-slash")

        assert source.root_uri.endswith("/")

    def test_from_credentials_builds_an_s3_root_uri_and_carries_the_bucket_auth(self) -> None:
        credentials = ObjectStoreCredentials(
            endpoint_url="https://storage.example.com",
            region="sjc",
            bucket="plantgeo-warehouse",
            access_key_id="access-key-value",
            secret_access_key="secret-key-value",
        )

        source = SignalPlaneSource.from_credentials(credentials, prefix="sandbox/")

        assert source.root_uri == "s3://plantgeo-warehouse/sandbox/"
        assert source.storage_options["aws_endpoint_url"] == "https://storage.example.com"
        assert source.storage_options["aws_access_key_id"] == "access-key-value"


# === method/monte_carlo/signal.py: the 30-day forecast ===========================================

_SEASONAL_CYCLE_DAYS = 366


def _observed_temperature_series(  # noqa: PLR0913 - one argument per synthetic-series knob, all keyword-only
    *, start: date, days: int, seed: int, mean: float = 10.0, amplitude: float = 15.0, noise: float = 1.0
) -> tuple[ObservedSignalDay, ...]:
    """A smooth, strongly seasonal synthetic series -- the shape the additive-anomaly path expects."""
    rng = numpy.random.default_rng(seed)
    rows = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        day_of_year = day.timetuple().tm_yday
        seasonal = mean + amplitude * math.sin(2.0 * math.pi * (day_of_year - 80) / _SEASONAL_CYCLE_DAYS)
        value = float(seasonal + rng.normal(0.0, noise))
        rows.append(ObservedSignalDay(observed_day=day, value=value, observation_checksum=f"temp-{offset}"))
    return tuple(rows)


def _observed_humidity_series(*, start: date, days: int, seed: int) -> tuple[ObservedSignalDay, ...]:
    """A series that presses against both the 0 and 100 percent bounds, to prove clipping holds."""
    rng = numpy.random.default_rng(seed)
    rows = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        day_of_year = day.timetuple().tm_yday
        seasonal = 90.0 + 15.0 * math.sin(2.0 * math.pi * day_of_year / _SEASONAL_CYCLE_DAYS)
        value = float(seasonal + rng.normal(0.0, 12.0))
        rows.append(ObservedSignalDay(observed_day=day, value=value, observation_checksum=f"rh-{offset}"))
    return tuple(rows)


def _observed_precipitation_series(*, start: date, days: int, seed: int) -> tuple[ObservedSignalDay, ...]:
    """A zero-inflated, right-skewed synthetic series -- exactly the shape a symmetric bootstrap breaks on."""
    rng = numpy.random.default_rng(seed)
    rows = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        day_of_year = day.timetuple().tm_yday
        wet_probability = 0.3 + 0.2 * math.sin(2.0 * math.pi * day_of_year / _SEASONAL_CYCLE_DAYS)
        is_wet = rng.random() < wet_probability
        value = float(rng.exponential(4.0)) if is_wet else 0.0
        rows.append(ObservedSignalDay(observed_day=day, value=value, observation_checksum=f"precip-{offset}"))
    return tuple(rows)


HISTORY_START = date(2020, 1, 1)
HISTORY_DAYS = 1500
ISSUED_ON = date(2023, 6, 15)
RELATIVE_HUMIDITY_UPPER_BOUND_PERCENT = 100.0


class TestSignalMonteCarloForecast:
    def test_carries_all_six_provenance_columns(self) -> None:
        observations = _observed_temperature_series(start=HISTORY_START, days=HISTORY_DAYS, seed=1)
        request = SimulationRequest(horizon_days=14, simulation_count=300, seed=42)

        run = simulate_signal_forecast(
            spec=series_spec_for("air_temperature_mean"),
            observations=observations,
            issued_on=ISSUED_ON,
            request=request,
            series_key="cell-1:air_temperature_mean",
        )

        assert run.random_seed == request.seed
        assert run.ensemble_size == request.simulation_count
        assert run.horizon_days == request.horizon_days
        assert run.issued_on == ISSUED_ON
        sha256_hex_length = 64
        assert len(run.forecast_run_id) == sha256_hex_length
        assert {row.quantile for row in run.rows} == set(PUBLISHED_QUANTILES)
        assert len(run.rows) == request.horizon_days * len(PUBLISHED_QUANTILES)
        assert run.method_name == METHOD_NAME_ADDITIVE_ANOMALY

    def test_reproducible_given_the_same_seed_and_inputs(self) -> None:
        observations = _observed_temperature_series(start=HISTORY_START, days=HISTORY_DAYS, seed=1)
        request = SimulationRequest(horizon_days=10, simulation_count=200, seed=7)
        kwargs = {
            "spec": series_spec_for("air_temperature_mean"),
            "observations": observations,
            "issued_on": ISSUED_ON,
            "request": request,
            "series_key": "cell-1:air_temperature_mean",
        }

        first = simulate_signal_forecast(**kwargs)  # type: ignore[arg-type]
        second = simulate_signal_forecast(**kwargs)  # type: ignore[arg-type]

        assert first == second

    def test_a_different_seed_moves_the_forecast_run_id_and_the_draws(self) -> None:
        observations = _observed_temperature_series(start=HISTORY_START, days=HISTORY_DAYS, seed=1)
        spec = series_spec_for("air_temperature_mean")

        first = simulate_signal_forecast(
            spec=spec,
            observations=observations,
            issued_on=ISSUED_ON,
            request=SimulationRequest(horizon_days=10, simulation_count=200, seed=1),
            series_key="cell-1:air_temperature_mean",
        )
        second = simulate_signal_forecast(
            spec=spec,
            observations=observations,
            issued_on=ISSUED_ON,
            request=SimulationRequest(horizon_days=10, simulation_count=200, seed=2),
            series_key="cell-1:air_temperature_mean",
        )

        assert first.forecast_run_id != second.forecast_run_id
        assert first.rows != second.rows

    def test_quantiles_are_ordered_low_median_high_on_every_horizon_day(self) -> None:
        observations = _observed_temperature_series(start=HISTORY_START, days=HISTORY_DAYS, seed=3)
        request = SimulationRequest(horizon_days=14, simulation_count=500, seed=11)

        run = simulate_signal_forecast(
            spec=series_spec_for("air_temperature_mean"),
            observations=observations,
            issued_on=ISSUED_ON,
            request=request,
            series_key="cell-1:air_temperature_mean",
        )

        for step in range(1, request.horizon_days + 1):
            by_quantile = {row.quantile: row.value for row in run.rows if row.horizon_step == step}
            assert by_quantile[0.1] <= by_quantile[0.5] <= by_quantile[0.9]

    def test_additive_anomaly_draws_respect_the_series_declared_bounds(self) -> None:
        observations = _observed_humidity_series(start=HISTORY_START, days=HISTORY_DAYS, seed=4)
        spec = series_spec_for("relative_humidity")

        run = simulate_signal_forecast(
            spec=spec,
            observations=observations,
            issued_on=ISSUED_ON,
            request=SimulationRequest(horizon_days=14, simulation_count=500, seed=13),
            series_key="cell-1:relative_humidity",
        )

        assert all(0.0 <= row.value <= RELATIVE_HUMIDITY_UPPER_BOUND_PERCENT for row in run.rows)

    def test_precipitation_never_produces_a_negative_draw(self) -> None:
        """The design problem the layer-lanes contract calls out by name: no symmetric-bootstrap negatives."""
        observations = _observed_precipitation_series(start=HISTORY_START, days=HISTORY_DAYS, seed=5)
        spec = series_spec_for("precipitation")

        run = simulate_signal_forecast(
            spec=spec,
            observations=observations,
            issued_on=ISSUED_ON,
            request=SimulationRequest(horizon_days=30, simulation_count=2000, seed=17),
            series_key="cell-1:precipitation",
        )

        assert run.method_name == METHOD_NAME_EMPIRICAL_RESAMPLE
        assert all(row.value >= 0.0 for row in run.rows)
        # A meaningfully wet synthetic series should still draw SOME positive rainfall -- proving the
        # non-negativity is not merely because every draw collapsed to zero.
        assert any(row.value > 0.0 for row in run.rows)

    def test_a_forecast_never_sees_observations_after_its_own_issued_on(self) -> None:
        """Time-honesty, proven rather than asserted: identical history except for what comes after
        `issued_on` must produce an identical forecast."""
        full_history = _observed_temperature_series(start=HISTORY_START, days=HISTORY_DAYS, seed=6)
        truncated_history = tuple(row for row in full_history if row.observed_day <= ISSUED_ON)
        spec = series_spec_for("air_temperature_mean")
        request = SimulationRequest(horizon_days=10, simulation_count=200, seed=23)

        with_future = simulate_signal_forecast(
            spec=spec, observations=full_history, issued_on=ISSUED_ON, request=request, series_key="cell-1:x"
        )
        without_future = simulate_signal_forecast(
            spec=spec, observations=truncated_history, issued_on=ISSUED_ON, request=request, series_key="cell-1:x"
        )

        assert with_future == without_future

    def test_insufficient_history_is_refused_never_fabricated(self) -> None:
        sparse_history = _observed_temperature_series(start=date(2026, 1, 1), days=10, seed=8)
        spec = series_spec_for("air_temperature_mean")

        with pytest.raises(InsufficientSignalHistoryError):
            simulate_signal_forecast(
                spec=spec,
                observations=sparse_history,
                issued_on=date(2026, 1, 10),
                request=SimulationRequest(horizon_days=5, simulation_count=100, seed=1),
                series_key="cell-1:x",
            )

    def test_issued_on_respects_each_signals_own_producer_lag(self) -> None:
        today = date(2026, 8, 22)

        nasa_issued = issued_on_for(series_spec_for("air_temperature_mean"), today=today)
        era5_issued = issued_on_for(series_spec_for("vapor_pressure_deficit"), today=today)
        radiation_issued = issued_on_for(series_spec_for("surface_shortwave_radiation"), today=today)

        assert nasa_issued == today - timedelta(days=NASA_POWER_PUBLICATION_LAG_DAYS)
        assert era5_issued == today - timedelta(days=ERA5_LAND_PUBLICATION_LAG_DAYS)
        assert radiation_issued == today - timedelta(days=SURFACE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS)
        # The whole point of a per-signal lag: radiation must NOT inherit the blanket NASA constant.
        assert radiation_issued != nasa_issued

    def test_an_undeclared_signal_name_is_refused_by_name(self) -> None:
        with pytest.raises(SignalSeriesSpecError, match="not_a_real_signal"):
            series_spec_for("not_a_real_signal")


# === pipeline/validation/signal.py: reconcile against the source system ==========================


class TestClassifySignalDay:
    def test_a_fully_covered_day_matches_cleanly(self) -> None:
        coverage = DayCoverage(
            day=A_DAY,
            state=DayState.COVERED,
            observed_cell_count=EXPECTED_FULL_LATTICE_CELLS,
            expected_cell_count=EXPECTED_FULL_LATTICE_CELLS,
        )

        finding = classify_signal_day(
            day=A_DAY,
            source_key="nasa-power-daily",
            signal_name="air_temperature_mean",
            coverage=coverage,
            exported_row_count=EXPECTED_FULL_LATTICE_CELLS,
        )

        assert finding.outcome is SignalLaneOutcome.MATCHED
        assert not finding.is_reportable_gap

    def test_the_radiation_hole_is_flagged_as_an_unexplained_source_gap(self) -> None:
        """docs/lanes/weather-observations.md section 5 item 1's ready-made first test case."""
        coverage = DayCoverage(
            day=A_DAY, state=DayState.MISSING, observed_cell_count=0, expected_cell_count=EXPECTED_FULL_LATTICE_CELLS
        )

        finding = classify_signal_day(
            day=A_DAY,
            source_key="nasa-power-daily",
            signal_name="surface_shortwave_radiation",
            coverage=coverage,
            exported_row_count=0,
        )

        assert finding.outcome is SignalLaneOutcome.UNEXPLAINED_SOURCE_GAP
        assert finding.is_reportable_gap
        description = finding.describe()
        assert A_DAY.isoformat() in description
        assert "nasa-power-daily" in description
        assert "surface_shortwave_radiation" in description

    def test_export_shortfall_when_the_lane_wrote_fewer_rows_than_the_source_holds(self) -> None:
        coverage = DayCoverage(
            day=A_DAY,
            state=DayState.COVERED,
            observed_cell_count=EXPECTED_FULL_LATTICE_CELLS,
            expected_cell_count=EXPECTED_FULL_LATTICE_CELLS,
        )

        finding = classify_signal_day(
            day=A_DAY,
            source_key="nasa-power-daily",
            signal_name="air_temperature_mean",
            coverage=coverage,
            exported_row_count=300,
        )

        assert finding.outcome is SignalLaneOutcome.EXPORT_SHORTFALL
        assert finding.is_reportable_gap

    def test_export_excess_is_flagged_even_on_an_otherwise_missing_day(self) -> None:
        coverage = DayCoverage(
            day=A_DAY, state=DayState.MISSING, observed_cell_count=0, expected_cell_count=EXPECTED_FULL_LATTICE_CELLS
        )

        finding = classify_signal_day(
            day=A_DAY,
            source_key="nasa-power-daily",
            signal_name="surface_shortwave_radiation",
            coverage=coverage,
            exported_row_count=5,
        )

        assert finding.outcome is SignalLaneOutcome.EXPORT_EXCESS

    def test_a_governed_absence_is_not_a_reportable_gap(self) -> None:
        coverage = DayCoverage(
            day=A_DAY, state=DayState.ABSENT, observed_cell_count=0, expected_cell_count=EXPECTED_FULL_LATTICE_CELLS
        )

        finding = classify_signal_day(
            day=A_DAY,
            source_key="open-meteo-era5-land-archive",
            signal_name="vapor_pressure_deficit",
            coverage=coverage,
            exported_row_count=0,
        )

        assert finding.outcome is SignalLaneOutcome.GOVERNED_ABSENT
        assert not finding.is_reportable_gap

    def test_a_partial_source_day_is_reported_but_not_treated_as_a_defect(self) -> None:
        coverage = DayCoverage(
            day=A_DAY, state=DayState.THIN, observed_cell_count=150, expected_cell_count=EXPECTED_FULL_LATTICE_CELLS
        )

        finding = classify_signal_day(
            day=A_DAY,
            source_key="nasa-power-daily",
            signal_name="soil_wetness_surface",
            coverage=coverage,
            exported_row_count=150,
        )

        assert finding.outcome is SignalLaneOutcome.SOURCE_PARTIAL
        assert finding.is_reportable_gap


class TestExportedSignalRowCounts:
    def test_counts_rows_per_signal_name(self) -> None:
        table = pa.concat_tables(
            [
                _signal_table(day=A_DAY, cell_ids=["c1", "c2"], signal_name="air_temperature_mean"),
                _signal_table(day=A_DAY, cell_ids=["c1"], signal_name="precipitation"),
            ]
        )

        counts = exported_signal_row_counts(table, day=A_DAY)

        assert counts == {"air_temperature_mean": 2, "precipitation": 1}

    def test_an_empty_table_counts_nothing(self) -> None:
        empty = SIGNAL_PLANE_SCHEMA.arrow_schema.empty_table()

        assert exported_signal_row_counts(empty, day=A_DAY) == {}

    def test_a_table_mixing_days_is_refused(self) -> None:
        mixed = pa.concat_tables(
            [
                _signal_table(day=A_DAY, cell_ids=["c1"]),
                _signal_table(day=A_DAY + timedelta(days=1), cell_ids=["c1"]),
            ]
        )

        with pytest.raises(SignalValidationError, match="other than"):
            exported_signal_row_counts(mixed, day=A_DAY)


class TestFindMissingExportPartitions:
    def test_a_day_with_neither_data_nor_absence_is_missing(self) -> None:
        backend = RecordingBackend()
        store = ObjectStore(backend)
        table = _signal_table(day=A_DAY, cell_ids=["c1"])
        store.write_partition(table, layer=SIGNAL_PLANE_STREAM, kind="observed", day=A_DAY)

        missing = find_missing_export_partitions(
            store, kind="observed", first_day=A_DAY, last_day=A_DAY + timedelta(days=2)
        )

        assert missing == (A_DAY + timedelta(days=1), A_DAY + timedelta(days=2))

    def test_a_governed_absence_marker_counts_as_not_missing(self) -> None:
        backend = RecordingBackend()
        store = ObjectStore(backend)
        marked_day = A_DAY + timedelta(days=1)
        table = _signal_table(day=A_DAY, cell_ids=["c1"])
        store.write_partition(table, layer=SIGNAL_PLANE_STREAM, kind="observed", day=A_DAY)
        store.write_absence(sample_absence(), layer=SIGNAL_PLANE_STREAM, kind="observed", day=marked_day)

        missing = find_missing_export_partitions(store, kind="observed", first_day=A_DAY, last_day=marked_day)

        assert missing == ()

    def test_a_backwards_window_is_refused(self) -> None:
        backend = RecordingBackend()
        store = ObjectStore(backend)

        with pytest.raises(SignalValidationError, match="backwards"):
            find_missing_export_partitions(store, kind="observed", first_day=A_DAY, last_day=A_DAY - timedelta(days=1))

    def test_a_kind_never_reported_via_the_other_kinds_listing(self) -> None:
        """A forecast partition must never satisfy an observed-day gap check, or vice versa."""
        backend = RecordingBackend()
        store = ObjectStore(backend)
        table = with_forecast_provenance(_signal_table(day=A_DAY, cell_ids=["c1"]), issued_on=A_DAY)
        store.write_partition(table, layer=SIGNAL_PLANE_STREAM, kind="forecast", day=A_DAY)

        missing = find_missing_export_partitions(store, kind="observed", first_day=A_DAY, last_day=A_DAY)

        assert missing == (A_DAY,)
