"""The signal lane's two remaining artifacts: the serving read and source-system validation.

Two sections, one per module, since this is the one test file the signal lane's wave-2 slice
owns (`conductor/code_styleguides/layer-lanes.md` section 1 puts one file per layer per lane, but
a single lane's test coverage lives together rather than splitting two ways for no reader's
benefit). The Monte Carlo forecast section left on 2026-09-18 with the forecaster itself; it is
`services/plantgeo-ml-service/tests/test_signal_forecast.py` now.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from agri_data_service.config import ObjectStoreCredentials
from agri_data_service.execution.coverage_contract import DayCoverage, DayState
from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.validation.signal import (
    WRITTEN_ZOOM_TIER,
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
from tests.parquet.test_objectstore_writer import (
    BASE_TIER,
    DETAIL_TIER,
    UNPUBLISHED_ZOOM,
    RecordingBackend,
    with_forecast_provenance,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier

# === shared fixtures =============================================================================

# The rung a lane export lands on, and the zoom a viewport asks for to be served it.
BASE_TIER_REQUEST = BASE_TIER

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
            # The spatial cell's representative point, which the coarse rungs re-floor onto a
            # grid. Spread per cell so a derived tier has more than one square to merge into.
            "cell_longitude": [-116.0 - index * 0.01 for index in range(count)],
            "cell_latitude": [43.0 + index * 0.01 for index in range(count)],
        }
    ).cast(SIGNAL_PLANE_SCHEMA.arrow_schema)


def _write_partition(
    base: Path, *, kind: PartitionKind, day: date, table: pa.Table, zoom: ZoomTier = BASE_TIER
) -> None:
    relative = partition_path(SIGNAL_PLANE_STREAM, kind, zoom, day)
    path = base / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


# === planes/signal.py: the serving read ==========================================================


class TestSignalPlaneServingRead:
    def test_reads_exactly_one_days_observed_rows(self, tmp_path: Path) -> None:
        _write_partition(tmp_path, kind="observed", day=A_DAY, table=_signal_table(day=A_DAY, cell_ids=["c1", "c2"]))
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        frame = read_signal_value_on_day(source, kind="observed", requested_zoom=BASE_TIER_REQUEST, day=A_DAY)

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

        observed = read_signal_value_on_day(source, kind="observed", requested_zoom=BASE_TIER_REQUEST, day=A_DAY)
        forecast = read_signal_value_on_day(source, kind="forecast", requested_zoom=BASE_TIER_REQUEST, day=A_DAY)

        assert observed["normalized_value"].to_list() == [1.0]
        assert observed["kind"].to_list() == ["observed"]
        assert forecast["normalized_value"].to_list() == [99.0]
        assert forecast["kind"].to_list() == ["forecast"]

    def test_a_real_forecast_partitions_provenance_columns_do_not_refuse_the_read(self, tmp_path: Path) -> None:
        """A `kind=forecast` file is observed PLUS six provenance columns; the observed pin made Polars refuse it."""
        _write_partition(
            tmp_path,
            kind="forecast",
            day=A_DAY,
            table=with_forecast_provenance(_signal_table(day=A_DAY, cell_ids=["c1"], value=99.0), issued_on=A_DAY),
        )
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        frame = read_signal_value_on_day(source, kind="forecast", requested_zoom=BASE_TIER_REQUEST, day=A_DAY)

        assert frame["normalized_value"].to_list() == [99.0]
        assert frame["kind"].to_list() == ["forecast"]
        # Projected back down to the observed grain: provenance is written, not yet served.
        assert set(frame.columns) == {*SIGNAL_PLANE_SCHEMA.column_names, "kind"}

    def test_a_day_with_nothing_written_returns_an_empty_typed_frame_not_an_error(self, tmp_path: Path) -> None:
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        frame = read_signal_value_on_day(source, kind="observed", requested_zoom=BASE_TIER_REQUEST, day=A_DAY)

        expected_columns = {"support_key", "signal_name", "cell_id", "observed_day", "normalized_value", "kind"}
        assert frame.height == 0
        assert set(frame.columns) >= expected_columns

    def test_cell_and_signal_filters_narrow_the_result(self, tmp_path: Path) -> None:
        table = _signal_table(day=A_DAY, cell_ids=["c1", "c2", "c3"])
        _write_partition(tmp_path, kind="observed", day=A_DAY, table=table)
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        frame = read_signal_value_on_day(
            source, kind="observed", requested_zoom=BASE_TIER_REQUEST, day=A_DAY, cell_ids=["c2"]
        )

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

        frame = read_signal_value_on_day(
            source, kind="observed", requested_zoom=BASE_TIER_REQUEST, day=A_DAY, signal_names=["wind_speed"]
        )

        assert frame["signal_name"].to_list() == ["wind_speed"]

    def test_time_window_spans_multiple_calendar_months(self, tmp_path: Path) -> None:
        first_day = date(2026, 6, 30)
        last_day = date(2026, 7, 4)
        _write_partition(tmp_path, kind="observed", day=first_day, table=_signal_table(day=first_day, cell_ids=["c1"]))
        _write_partition(tmp_path, kind="observed", day=last_day, table=_signal_table(day=last_day, cell_ids=["c1"]))
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        frame = read_signal_time_window(
            source, kind="observed", requested_zoom=BASE_TIER_REQUEST, first_day=first_day, last_day=last_day
        )

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

        frame = read_signal_time_window(
            source, kind="observed", requested_zoom=BASE_TIER_REQUEST, first_day=date(2026, 5, 1), last_day=present_day
        )

        assert frame.height == 1
        assert frame["observed_day"].to_list() == [present_day]

    def test_a_window_with_nothing_written_anywhere_returns_an_empty_frame(self, tmp_path: Path) -> None:
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        frame = read_signal_time_window(
            source,
            kind="observed",
            requested_zoom=BASE_TIER_REQUEST,
            first_day=date(2026, 1, 1),
            last_day=date(2026, 1, 31),
        )

        assert frame.height == 0

    def test_a_window_over_the_serving_budget_is_refused(self, tmp_path: Path) -> None:
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        with pytest.raises(SignalPlaneReadError, match="budget"):
            read_signal_time_window(
                source,
                kind="observed",
                requested_zoom=BASE_TIER_REQUEST,
                first_day=date(2020, 1, 1),
                last_day=date(2020, 1, 1) + timedelta(days=MAX_TIME_WINDOW_DAYS + 1),
            )

    def test_a_backwards_window_is_refused(self, tmp_path: Path) -> None:
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        with pytest.raises(SignalPlaneReadError, match="backwards"):
            read_signal_time_window(
                source,
                kind="observed",
                requested_zoom=BASE_TIER_REQUEST,
                first_day=A_DAY,
                last_day=A_DAY - timedelta(days=1),
            )

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
        store.write_partition(table, layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=A_DAY)

        missing = find_missing_export_partitions(
            store, kind="observed", first_day=A_DAY, last_day=A_DAY + timedelta(days=2)
        )

        assert missing == (A_DAY + timedelta(days=1), A_DAY + timedelta(days=2))

    def test_a_governed_absence_marker_counts_as_not_missing(self) -> None:
        backend = RecordingBackend()
        store = ObjectStore(backend)
        marked_day = A_DAY + timedelta(days=1)
        table = _signal_table(day=A_DAY, cell_ids=["c1"])
        store.write_partition(table, layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=A_DAY)
        store.write_absence(
            sample_absence(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=marked_day
        )

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
        store.write_partition(table, layer=SIGNAL_PLANE_STREAM, kind="forecast", zoom=BASE_TIER, day=A_DAY)

        missing = find_missing_export_partitions(store, kind="observed", first_day=A_DAY, last_day=A_DAY)

        assert missing == (A_DAY,)

    def test_a_derived_coarse_rung_never_satisfies_a_base_tier_export_gap(self) -> None:
        """The coarse rung is DOWNSTREAM of the base one; counting it as evidence is exactly backwards."""
        backend = RecordingBackend()
        store = ObjectStore(backend)
        table = _signal_table(day=A_DAY, cell_ids=["c1"])
        store.write_partition(table, layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=DETAIL_TIER, day=A_DAY)

        missing = find_missing_export_partitions(store, kind="observed", first_day=A_DAY, last_day=A_DAY)

        assert missing == (A_DAY,)
        assert WRITTEN_ZOOM_TIER != DETAIL_TIER, "the pinned tier is the base rung, not a derived one"


class TestSignalPlaneZoomAxis:
    """One rung per read: `zoom` is a partition on the same terms as `kind`."""

    def test_two_tiers_of_one_signal_day_never_stack_into_one_frame(self, tmp_path: Path) -> None:
        _write_partition(
            tmp_path, kind="observed", day=A_DAY, table=_signal_table(day=A_DAY, cell_ids=["c1"]), zoom=BASE_TIER
        )
        _write_partition(
            tmp_path, kind="observed", day=A_DAY, table=_signal_table(day=A_DAY, cell_ids=["c9"]), zoom=DETAIL_TIER
        )
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        at_base = read_signal_value_on_day(source, kind="observed", requested_zoom=BASE_TIER, day=A_DAY)
        at_detail = read_signal_value_on_day(source, kind="observed", requested_zoom=DETAIL_TIER, day=A_DAY)

        assert at_base["cell_id"].to_list() == ["c1"]
        assert at_detail["cell_id"].to_list() == ["c9"]

    def test_a_request_between_two_rungs_is_served_by_the_rung_below_it(self, tmp_path: Path) -> None:
        _write_partition(
            tmp_path, kind="observed", day=A_DAY, table=_signal_table(day=A_DAY, cell_ids=["c9"]), zoom=DETAIL_TIER
        )
        _write_partition(
            tmp_path, kind="observed", day=A_DAY, table=_signal_table(day=A_DAY, cell_ids=["c1"]), zoom=BASE_TIER
        )
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        served = read_signal_value_on_day(source, kind="observed", requested_zoom=UNPUBLISHED_ZOOM, day=A_DAY)

        assert served["cell_id"].to_list() == ["c9"], "rounding UP would serve z13 bytes to a z11 viewport"

    def test_a_month_spanning_window_reads_one_rung_end_to_end(self, tmp_path: Path) -> None:
        """A tier resolved per month could change the cell grid mid-series with nothing to signal it."""
        first_day, last_day = date(2026, 6, 28), date(2026, 7, 2)
        _write_partition(
            tmp_path,
            kind="observed",
            day=first_day,
            table=_signal_table(day=first_day, cell_ids=["c9"]),
            zoom=DETAIL_TIER,
        )
        _write_partition(
            tmp_path, kind="observed", day=last_day, table=_signal_table(day=last_day, cell_ids=["c1"]), zoom=BASE_TIER
        )
        source = SignalPlaneSource(root_uri=tmp_path.as_posix())

        frame = read_signal_time_window(
            source, kind="observed", requested_zoom=DETAIL_TIER, first_day=first_day, last_day=last_day
        )

        assert frame["cell_id"].to_list() == ["c9"]
        assert frame["observed_day"].to_list() == [first_day]
