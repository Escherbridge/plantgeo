"""The vegetation lane's serving read, source reconciliation, and forecast provenance conformance.

Three sections, one per artifact this file is the sole test coverage for:
`planes/vegetation.py` (DuckDB/Polars serving read), `pipeline/validation/vegetation.py` (source
reconciliation), and the provenance reshape added to `method/monte_carlo/vegetation_ndvi_forecast.py`.
No test here touches a live database or a live bucket -- Parquet bytes come from `ObjectStore` over
`RecordingBackend` (mirrors `tests/parquet/test_vegetation_lane.py`), materialized to a local
`tmp_path` so `polars.scan_parquet` reads real files without any network or S3 credentials.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.config import ObjectStoreCredentials
from agri_data_service.foundation.parquet.paths import (
    MAX_GAP_WINDOW_DAYS,
    PartitionPathError,
    absence_marker_path,
    completion_marker_path,
    partition_path,
)
from agri_data_service.method.monte_carlo.vegetation_ndvi_forecast import (
    DAYS_PER_SEASONAL_CYCLE,
    HIGH_QUANTILE,
    LOW_QUANTILE,
    MEDIAN_QUANTILE,
    HorizonQuantiles,
    ProvenancedForecastRow,
    SeasonalHistory,
    SimulationRequest,
    provenanced_forecast_rows,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.validation.vegetation import (
    DUPLICATE_SOURCE_RELEASES,
    INCOMPLETE_PARTITION,
    MISSING_FROM_PARQUET,
    WRITTEN_ZOOM_TIER,
    reconcile_against_source,
)
from agri_data_service.pipeline.vegetation_source import (
    CELL_BATCH_SIZE,
    SourceCellDay,
    VegetationValidationError,
    fetch_source_cell_days,
)
from agri_data_service.planes.vegetation import (
    VegetationServingError,
    bucket_object_root,
    read_vegetation_partition,
    read_vegetation_window,
    vegetation_scan_pattern,
)
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_SCHEMA, VEGETATION_PLANE_STREAM
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

# The rung a lane export lands on, and the zoom a viewport asks for to be served it.
BASE_TIER_REQUEST = BASE_TIER

AUGUST_FIRST = date(2026, 8, 1)
AUGUST_THIRD = date(2026, 8, 3)
AUGUST_SIXTH = date(2026, 8, 6)
EXPECTED_QUANTILE_ROWS_PER_STEP = 3


def _vegetation_table(day: date, cell_ids: Sequence[str]) -> pa.Table:
    """Build a table conforming to `VEGETATION_PLANE_SCHEMA` for `cell_ids` on one day."""
    count = len(cell_ids)
    return pa.table(
        {
            "cell_id": pa.array(list(cell_ids), pa.string()),
            "grid_name": pa.array(["sentinel2-ndvi-0p25deg"] * count, pa.string()),
            "metric_name": pa.array(["ndvi"] * count, pa.string()),
            "metric_unit": pa.array(["ndvi_index"] * count, pa.string()),
            "observed_day": pa.array([day] * count, pa.date32()),
            "metric_value": pa.array([0.55] * count, pa.float64()),
            "observation_checksum": pa.array(["a" * 64] * count, pa.string()),
            "data_available_at": pa.array([datetime(2026, 8, 6, 12, tzinfo=UTC)] * count, pa.timestamp("us", tz="UTC")),
            "release_count": pa.array([1] * count, pa.int64()),
            "allowed_client_exposure": pa.array([True] * count, pa.bool_()),
            "cell_longitude": pa.array([-116.0 - i * 0.01 for i in range(count)], pa.float64()),
            "cell_latitude": pa.array([43.0 + i * 0.01 for i in range(count)], pa.float64()),
        }
    )


def _materialize(backend: RecordingBackend, root: Path) -> None:
    """Write every object `backend` recorded to real files under `root`, at the same relative keys."""
    for key, payload in backend.objects.items():
        target = root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


# --- planes/vegetation.py: the serving read -----------------------------------------------------


def test_vegetation_scan_pattern_starts_inside_the_requested_kind_and_tier() -> None:
    """Neither the other kind's directory nor another rung's appears in the pattern at all."""
    pattern = vegetation_scan_pattern(root="s3://bucket/prefix", kind="observed", zoom=BASE_TIER)

    assert pattern == "s3://bucket/prefix/layer=vegetation/kind=observed/zoom=13/**/*.parquet"
    assert "kind=forecast" not in pattern
    # The wildcard begins AFTER `zoom=`, so it can never expand into a sibling rung.
    assert pattern.index("zoom=13") < pattern.index("**")


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


def test_read_vegetation_partition_returns_only_the_requested_kind_and_window(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(
        _vegetation_table(AUGUST_FIRST, ["c1", "c2"]),
        layer=VEGETATION_PLANE_STREAM,
        kind="observed",
        zoom=BASE_TIER,
        day=AUGUST_FIRST,
    )
    store.write_partition(
        _vegetation_table(AUGUST_THIRD, ["c1", "c2"]),
        layer=VEGETATION_PLANE_STREAM,
        kind="observed",
        zoom=BASE_TIER,
        day=AUGUST_THIRD,
    )
    _materialize(backend, tmp_path)
    root = tmp_path.resolve().as_posix()

    table = read_vegetation_partition(
        root=root, requested_zoom=BASE_TIER_REQUEST, kind="observed", first_day=AUGUST_FIRST, last_day=date(2026, 8, 2)
    )

    assert table.columns == list(VEGETATION_PLANE_SCHEMA.column_names)
    assert table["cell_id"].to_list() == ["c1", "c2"]
    assert set(table["observed_day"].to_list()) == {AUGUST_FIRST}


def test_read_vegetation_partition_is_an_honest_empty_when_the_kind_was_never_written(tmp_path: Path) -> None:
    """`kind="forecast"` has no exporter wired for this lane yet -- zero rows, not a crash."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(
        _vegetation_table(AUGUST_FIRST, ["c1"]),
        layer=VEGETATION_PLANE_STREAM,
        kind="observed",
        zoom=BASE_TIER,
        day=AUGUST_FIRST,
    )
    _materialize(backend, tmp_path)
    root = tmp_path.resolve().as_posix()

    table = read_vegetation_partition(
        root=root, requested_zoom=BASE_TIER_REQUEST, kind="forecast", first_day=AUGUST_FIRST, last_day=AUGUST_THIRD
    )

    assert table.is_empty()
    assert table.columns == list(VEGETATION_PLANE_SCHEMA.column_names)


def test_a_real_forecast_partitions_provenance_columns_do_not_refuse_the_read(tmp_path: Path) -> None:
    """A `kind=forecast` file is observed PLUS six provenance columns; the observed pin made Polars refuse it."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(
        with_forecast_provenance(_vegetation_table(AUGUST_FIRST, ["c1"]), issued_on=AUGUST_FIRST),
        layer=VEGETATION_PLANE_STREAM,
        kind="forecast",
        zoom=BASE_TIER,
        day=AUGUST_FIRST,
    )
    _materialize(backend, tmp_path)
    root = tmp_path.resolve().as_posix()

    table = read_vegetation_partition(
        root=root, requested_zoom=BASE_TIER_REQUEST, kind="forecast", first_day=AUGUST_FIRST, last_day=AUGUST_THIRD
    )
    window = read_vegetation_window(
        root=root, requested_zoom=BASE_TIER_REQUEST, first_day=AUGUST_FIRST, last_day=AUGUST_THIRD
    )

    assert table.height == 1
    # Projected back down to the observed grain so both kinds hand callers one uniform shape.
    assert table.columns == list(VEGETATION_PLANE_SCHEMA.column_names)
    assert window.forecast.height == 1
    assert window.observed.is_empty()


def test_read_vegetation_window_never_blends_observed_and_forecast(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(
        _vegetation_table(AUGUST_FIRST, ["c1"]),
        layer=VEGETATION_PLANE_STREAM,
        kind="observed",
        zoom=BASE_TIER,
        day=AUGUST_FIRST,
    )
    _materialize(backend, tmp_path)
    root = tmp_path.resolve().as_posix()

    window = read_vegetation_window(
        root=root, requested_zoom=BASE_TIER_REQUEST, first_day=AUGUST_FIRST, last_day=AUGUST_THIRD
    )

    assert window.observed.height == 1
    assert window.forecast.is_empty()


def test_read_vegetation_partition_refuses_a_backwards_or_oversized_window(tmp_path: Path) -> None:
    root = tmp_path.resolve().as_posix()

    with pytest.raises(VegetationServingError, match="backwards"):
        read_vegetation_partition(
            root=root, requested_zoom=BASE_TIER_REQUEST, kind="observed", first_day=AUGUST_THIRD, last_day=AUGUST_FIRST
        )

    with pytest.raises(VegetationServingError, match="budget"):
        read_vegetation_partition(
            root=root,
            requested_zoom=BASE_TIER_REQUEST,
            kind="observed",
            first_day=date(2000, 1, 1),
            last_day=date(2000, 1, 1) + timedelta(days=MAX_GAP_WINDOW_DAYS + 1),
        )


def test_read_vegetation_partition_rejects_an_unknown_kind(tmp_path: Path) -> None:
    root = tmp_path.resolve().as_posix()

    with pytest.raises(PartitionPathError):
        read_vegetation_partition(
            root=root,
            requested_zoom=BASE_TIER_REQUEST,
            kind="not-a-kind",
            first_day=AUGUST_FIRST,
            last_day=AUGUST_THIRD,
        )  # type: ignore[arg-type]


# --- the zoom axis: one rung per read, and a blend that is not expressible ------------------------


def test_two_tiers_of_one_cell_day_never_stack_into_one_ndvi_answer(tmp_path: Path) -> None:
    """Two rungs of one cell-day are two AVERAGES of the same reflectance, both in range and both plausible.

    There is nothing structurally wrong with a blended frame: `cell_id`s are real, values are inside
    [-1, 1], the sort holds. Only reading one rung at a time keeps the answer meaningful.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(
        _vegetation_table(AUGUST_FIRST, ["c1"]),
        layer=VEGETATION_PLANE_STREAM,
        kind="observed",
        zoom=BASE_TIER,
        day=AUGUST_FIRST,
    )
    store.write_partition(
        _vegetation_table(AUGUST_FIRST, ["c9"]),
        layer=VEGETATION_PLANE_STREAM,
        kind="observed",
        zoom=DETAIL_TIER,
        day=AUGUST_FIRST,
    )
    _materialize(backend, tmp_path)
    root = tmp_path.resolve().as_posix()

    at_base = read_vegetation_partition(
        root=root, requested_zoom=BASE_TIER, kind="observed", first_day=AUGUST_FIRST, last_day=AUGUST_THIRD
    )
    at_detail = read_vegetation_partition(
        root=root, requested_zoom=DETAIL_TIER, kind="observed", first_day=AUGUST_FIRST, last_day=AUGUST_THIRD
    )

    assert at_base["cell_id"].to_list() == ["c1"]
    assert at_detail["cell_id"].to_list() == ["c9"]


def test_a_window_reads_both_kinds_at_one_tier_and_records_which(tmp_path: Path) -> None:
    """A settled half at one resolution and a projected half at another would share a `cell_id` space."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(
        _vegetation_table(AUGUST_FIRST, ["c9"]),
        layer=VEGETATION_PLANE_STREAM,
        kind="observed",
        zoom=DETAIL_TIER,
        day=AUGUST_FIRST,
    )
    _materialize(backend, tmp_path)

    window = read_vegetation_window(
        root=tmp_path.resolve().as_posix(),
        requested_zoom=UNPUBLISHED_ZOOM,
        first_day=AUGUST_FIRST,
        last_day=AUGUST_THIRD,
    )

    assert window.zoom == DETAIL_TIER, "z11 resolves DOWN to z9, never up to z13"
    assert window.observed["cell_id"].to_list() == ["c9"]
    assert window.forecast.is_empty()


# --- pipeline/validation/vegetation.py: source reconciliation -----------------------------------


class _Result:
    def __init__(self, rows: Sequence[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> Sequence[dict[str, object]]:
        return self._rows


class RecordingSession:
    """Captures each statement's bound cell batch and window; answers with canned source rows."""

    def __init__(self, rows_by_cell: dict[str, list[dict[str, object]]]) -> None:
        self._rows_by_cell = rows_by_cell
        self.batches: list[list[str]] = []

    async def execute(self, _statement: Any, params: dict[str, Any]) -> _Result:
        batch = list(params["cell_ids"])
        self.batches.append(batch)
        rows = [row for cell_id in batch for row in self._rows_by_cell.get(cell_id, [])]
        return _Result(rows)


def _source_row(cell_id: str, day: date, release_count: int) -> dict[str, object]:
    return {"cell_id": cell_id, "observed_day": day, "source_release_count": release_count}


@pytest.mark.asyncio
async def test_fetch_source_cell_days_reads_in_bounded_batches() -> None:
    cell_ids = [f"c{i}" for i in range(CELL_BATCH_SIZE + 5)]
    rows_by_cell = {cell_id: [_source_row(cell_id, AUGUST_FIRST, 1)] for cell_id in cell_ids}
    session = RecordingSession(rows_by_cell)

    result = await fetch_source_cell_days(session, cell_ids=cell_ids, first_day=AUGUST_FIRST, last_day=AUGUST_FIRST)  # type: ignore[arg-type]

    assert [len(batch) for batch in session.batches] == [CELL_BATCH_SIZE, 5]
    assert len(result) == len(cell_ids)
    assert all(isinstance(row, SourceCellDay) for row in result)


@pytest.mark.asyncio
async def test_fetch_source_cell_days_refuses_a_backwards_window() -> None:
    session = RecordingSession({})

    with pytest.raises(VegetationValidationError, match="backwards"):
        await fetch_source_cell_days(session, cell_ids=["c1"], first_day=AUGUST_THIRD, last_day=AUGUST_FIRST)  # type: ignore[arg-type]
    assert session.batches == []


def test_reconcile_reports_a_source_day_with_no_written_partition() -> None:
    source_cell_days = (SourceCellDay(cell_id="c1", observed_day=AUGUST_FIRST, source_release_count=1),)

    report = reconcile_against_source(
        source_cell_days=source_cell_days,
        written_partition_keys=(),
        first_day=AUGUST_FIRST,
        last_day=AUGUST_FIRST,
    )

    assert not report.is_clean
    assert report.findings[0].kind == MISSING_FROM_PARQUET
    assert report.findings[0].observed_day == AUGUST_FIRST


def test_reconcile_does_not_flag_a_day_the_source_never_held() -> None:
    """Sentinel-2 NDVI is cloud-gated and genuinely sparse; a source-side absence is not a defect."""
    report = reconcile_against_source(
        source_cell_days=(),
        written_partition_keys=(),
        first_day=AUGUST_FIRST,
        last_day=AUGUST_THIRD,
    )

    assert report.is_clean
    assert report.source_day_count == 0


def test_reconcile_reports_a_partition_without_completion_marker_as_incomplete() -> None:
    """A day holding part files but no completion marker is a killed mid-upload, not a covered day."""
    source_cell_days = (SourceCellDay(cell_id="c1", observed_day=AUGUST_FIRST, source_release_count=1),)
    written = (partition_path(VEGETATION_PLANE_STREAM, "observed", WRITTEN_ZOOM_TIER, AUGUST_FIRST),)

    report = reconcile_against_source(
        source_cell_days=source_cell_days, written_partition_keys=written, first_day=AUGUST_FIRST, last_day=AUGUST_FIRST
    )

    assert not report.is_clean
    incomplete_findings = [f for f in report.findings if f.kind == INCOMPLETE_PARTITION]
    assert len(incomplete_findings) == 1
    assert incomplete_findings[0].observed_day == AUGUST_FIRST
    assert "completion marker" in incomplete_findings[0].detail


def test_reconcile_treats_a_completed_partition_as_covering_its_day() -> None:
    """A partition WITH its completion marker is a finished export and counts as covered."""
    source_cell_days = (SourceCellDay(cell_id="c1", observed_day=AUGUST_FIRST, source_release_count=1),)
    written = (
        partition_path(VEGETATION_PLANE_STREAM, "observed", WRITTEN_ZOOM_TIER, AUGUST_FIRST),
        completion_marker_path(VEGETATION_PLANE_STREAM, "observed", WRITTEN_ZOOM_TIER, AUGUST_FIRST),
    )

    report = reconcile_against_source(
        source_cell_days=source_cell_days, written_partition_keys=written, first_day=AUGUST_FIRST, last_day=AUGUST_FIRST
    )

    assert report.is_clean


def test_reconcile_treats_a_governed_absence_marker_as_covering_its_day() -> None:
    """A day the source holds no row for AND that carries an absence marker is doubly not a gap."""
    written = (absence_marker_path(VEGETATION_PLANE_STREAM, "observed", WRITTEN_ZOOM_TIER, AUGUST_FIRST),)

    report = reconcile_against_source(
        source_cell_days=(), written_partition_keys=written, first_day=AUGUST_FIRST, last_day=AUGUST_FIRST
    )

    assert report.is_clean


def test_reconcile_sees_overlapping_source_releases_the_exporter_dedups_away() -> None:
    """The known defect: `register_governed_plane` can mint duplicate releases per cell-day."""
    source_cell_days = (
        SourceCellDay(cell_id="c1", observed_day=AUGUST_FIRST, source_release_count=2),
        SourceCellDay(cell_id="c2", observed_day=AUGUST_FIRST, source_release_count=1),
    )
    written = (partition_path(VEGETATION_PLANE_STREAM, "observed", WRITTEN_ZOOM_TIER, AUGUST_FIRST),)

    report = reconcile_against_source(
        source_cell_days=source_cell_days, written_partition_keys=written, first_day=AUGUST_FIRST, last_day=AUGUST_FIRST
    )

    assert not report.is_clean
    duplicate_findings = [f for f in report.findings if f.kind == DUPLICATE_SOURCE_RELEASES]
    assert len(duplicate_findings) == 1
    assert "1 cell(s)" in duplicate_findings[0].detail


# --- method/monte_carlo/vegetation_ndvi_forecast.py: forecast-row provenance ---------------------


def _sample_history(cutoff_day: date) -> SeasonalHistory:
    return SeasonalHistory(
        cutoff_day=cutoff_day,
        history_start_day=date(2022, 8, 5),
        governed_day_count=40,
        training_day_count=36,
        climatology_by_day_of_year=tuple(0.5 for _ in range(DAYS_PER_SEASONAL_CYCLE)),
        climatology_sample_counts=tuple(4 for _ in range(DAYS_PER_SEASONAL_CYCLE)),
        anomaly_values=(0.01, -0.02, 0.03),
        anomaly_days_of_year=(200, 201, 202),
        latest_observed_day=cutoff_day,
        latest_observed_value=0.55,
        anchor_day=cutoff_day,
        anchor_anomaly=0.02,
        lag_one_autocorrelation=0.4,
        autocorrelation_pair_count=10,
        mean_observation_gap_days=6.0,
        daily_persistence=0.9,
    )


def test_provenanced_forecast_rows_carries_every_contract_column() -> None:
    history = _sample_history(AUGUST_FIRST)
    quantiles = (
        HorizonQuantiles(
            horizon_step=1,
            valid_day=date(2026, 8, 2),
            low_value=0.4,
            median_value=0.5,
            high_value=0.6,
            innovation_pool_size=5,
        ),
        HorizonQuantiles(
            horizon_step=2,
            valid_day=date(2026, 8, 3),
            low_value=0.41,
            median_value=0.51,
            high_value=0.61,
            innovation_pool_size=5,
        ),
    )
    request = SimulationRequest(horizon_days=2, simulation_count=1000, seed=42)
    forecast_run_id = "a" * 64

    rows = provenanced_forecast_rows(
        history=history, quantiles=quantiles, request=request, forecast_run_id=forecast_run_id
    )

    assert len(rows) == len(quantiles) * EXPECTED_QUANTILE_ROWS_PER_STEP
    assert all(isinstance(row, ProvenancedForecastRow) for row in rows)
    for row in rows:
        assert row.forecast_run_id == forecast_run_id
        assert row.random_seed == request.seed
        assert row.ensemble_size == request.simulation_count
        assert row.issued_on == history.cutoff_day
        assert row.quantile in (LOW_QUANTILE, MEDIAN_QUANTILE, HIGH_QUANTILE)

    step_one = {row.quantile: row.metric_value for row in rows if row.horizon_days == 1}
    assert step_one == {LOW_QUANTILE: 0.4, MEDIAN_QUANTILE: 0.5, HIGH_QUANTILE: 0.6}


def test_provenanced_forecast_rows_rejects_a_blank_run_id() -> None:
    history = _sample_history(AUGUST_FIRST)
    request = SimulationRequest(horizon_days=1, simulation_count=1000, seed=1)

    with pytest.raises(ValueError, match="non-blank"):
        provenanced_forecast_rows(history=history, quantiles=(), request=request, forecast_run_id="   ")
