"""Local forecast integrity, complete inventory, pinned reads and pointer transactions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from agri_data_service.pipeline.direct.weather_forecast import artifacts
from agri_data_service.pipeline.direct.weather_forecast.artifacts import (
    ArtifactError,
    ForecastSample,
    activate_run,
    prepare_run,
    read_run,
)
from agri_data_service.planes.weather_forecast import (
    ForecastSelection,
    read_forecast_field,
    read_selected_forecast,
)
from agri_data_service.warehouse.weather_forecast.contracts import (
    ForecastRun,
    ForecastSeries,
    ForecastValue,
    SpatialSupport,
)

START = datetime(2026, 9, 11, tzinfo=UTC)
END = START + timedelta(hours=2)
NOW = START + timedelta(hours=3)
SOURCE = b'{"fixture":"local-only"}'
EXPECTED_BLOB_COUNT = 2
EXPECTED_MIDDLE_ZOOM = 9
EXPECTED_FIELD_ZOOM = 5
EXPECTED_FIELD_COUNT = 2
NEARBY_DISTANCE_MIN_M = 70
NEARBY_DISTANCE_MAX_M = 90
SAMPLES = (
    ForecastSample(sample_id="a", longitude=-120.0, latitude=45.0),
    ForecastSample(sample_id="b", longitude=-119.0, latitude=45.0),
)


def _series(run_id: str = "run-a") -> ForecastSeries:
    run = ForecastRun(
        run_id=run_id,
        product_id="weather",
        provider="fixture",
        model="fixture",
        model_init_at=START,
        provider_issued_at=None,
        fetched_at=START + timedelta(minutes=1),
        admitted_at=START + timedelta(minutes=2),
        published_at=START + timedelta(minutes=3),
        licence="fixture-only",
        source_url="https://example.org/fixture",
        source_payload_sha256=sha256(SOURCE).hexdigest(),
        support=SpatialSupport(kind="sampled_point"),
        variables=("temperature_2m",),
    )
    values = tuple(
        ForecastValue(
            run_id=run_id,
            sample_id=sample.sample_id,
            longitude=sample.longitude,
            latitude=sample.latitude,
            variable="temperature_2m",
            unit="degC",
            valid_at=START + timedelta(hours=hour),
            lead_seconds=hour * 3600,
            value=None if sample.sample_id == "b" else float(hour),
            status="missing" if sample.sample_id == "b" else "available",
        )
        for sample in SAMPLES
        for hour in range(2)
    )
    return ForecastSeries(run=run, values=values)


def _prepare(root: Path, run_id: str = "run-a") -> artifacts.ForecastManifest:
    return prepare_run(root=root, series=_series(run_id), samples=SAMPLES, start=START, end=END, source_payload=SOURCE)


def _selection(run_id: str = "run-a", longitude: float = -120.0) -> ForecastSelection:
    return ForecastSelection(
        run_id=run_id, longitude=longitude, latitude=45.0, start=START, end=END, timezone="America/Denver"
    )


def _publish(root: Path) -> artifacts.ForecastManifest:
    manifest = _prepare(root)
    activate_run(root=root, product_id="weather", run_id="run-a", expected_manifest_sha256=None, now=NOW)
    return manifest


def test_immutable_replay_and_complete_missingness_receipt(tmp_path: Path) -> None:
    manifest = _prepare(tmp_path)
    assert _prepare(tmp_path) == manifest
    verified, series = read_run(root=tmp_path, product_id="weather", run_id="run-a")
    assert verified == manifest
    assert series == _series()
    assert manifest.status_counts == {"available": 2, "missing": 2}
    assert len(list((tmp_path / "blobs").iterdir())) == EXPECTED_BLOB_COUNT
    assert not (tmp_path / "weather" / "active.json").exists()


def test_missing_inventory_and_same_run_conflicts_refuse(tmp_path: Path) -> None:
    series = _series()
    incomplete = ForecastSeries(run=series.run, values=series.values[:-1])
    with pytest.raises(ArtifactError, match="inventory"):
        prepare_run(root=tmp_path, series=incomplete, samples=SAMPLES, start=START, end=END, source_payload=SOURCE)
    _prepare(tmp_path)
    changed = series.model_dump(mode="json")
    changed["values"][0]["value"] = 55.0
    with pytest.raises(ArtifactError, match="identity conflict"):
        prepare_run(
            root=tmp_path,
            series=ForecastSeries.model_validate(changed),
            samples=SAMPLES,
            start=START,
            end=END,
            source_payload=SOURCE,
        )


def test_revalidated_construct_bypass_and_bad_source_are_refused(tmp_path: Path) -> None:
    series = _series()
    forged = series.model_copy(update={"values": (series.values[0].model_copy(update={"run_id": "other"}),)})
    with pytest.raises(ValueError, match="series values must belong to the pinned run"):
        prepare_run(root=tmp_path, series=forged, samples=SAMPLES, start=START, end=END, source_payload=SOURCE)
    with pytest.raises(ArtifactError, match="source payload"):
        prepare_run(root=tmp_path, series=series, samples=SAMPLES, start=START, end=END, source_payload=b"different")


def test_conditional_activation_replay_conflict_rollback_and_pinning(tmp_path: Path) -> None:
    _prepare(tmp_path)
    _prepare(tmp_path, "run-b")
    first = activate_run(root=tmp_path, product_id="weather", run_id="run-a", expected_manifest_sha256=None, now=NOW)
    assert activate_run(root=tmp_path, product_id="weather", run_id="run-a", expected_manifest_sha256=None) == first
    with pytest.raises(ArtifactError, match="compare-and-swap"):
        activate_run(root=tmp_path, product_id="weather", run_id="run-b", expected_manifest_sha256=None)
    second = activate_run(root=tmp_path, product_id="weather", run_id="run-b", expected_manifest_sha256=first)
    pinned = read_selected_forecast(
        root=tmp_path, product_id="weather", selection=_selection(), requested_zoom=11, now=NOW, max_distance_m=1000
    )
    assert pinned.run is not None
    assert pinned.run.run_id == "run-a"
    assert pinned.zoom == EXPECTED_MIDDLE_ZOOM
    assert activate_run(root=tmp_path, product_id="weather", run_id="run-a", expected_manifest_sha256=second) == first


def test_interrupted_manifest_write_can_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = artifacts._atomic

    def fail_manifest(path: Path, data: bytes, *, immutable: bool) -> None:
        if path.suffix == ".json":
            raise OSError("simulated interruption")
        original(path, data, immutable=immutable)

    monkeypatch.setattr(artifacts, "_atomic", fail_manifest)
    with pytest.raises(OSError, match="interruption"):
        _prepare(tmp_path)
    assert not (tmp_path / "weather" / "runs" / "run-a.json").exists()
    monkeypatch.setattr(artifacts, "_atomic", original)
    _prepare(tmp_path)
    assert read_run(root=tmp_path, product_id="weather", run_id="run-a")[1] == _series()


@pytest.mark.parametrize("blob", ["source", "parquet"])
def test_corrupt_blob_refuses_reader_and_activation(tmp_path: Path, blob: str) -> None:
    manifest = _publish(tmp_path)
    digest = manifest.parquet_sha256 if blob == "parquet" else manifest.run.source_payload_sha256
    (tmp_path / "blobs" / f"{digest}.{blob}").write_bytes(b"corruption")
    result = read_selected_forecast(
        root=tmp_path, product_id="weather", selection=_selection(), requested_zoom=13, now=NOW, max_distance_m=1000
    )
    assert result.status == "upstream_unavailable"
    assert result.values == ()
    assert result.run is None
    with pytest.raises(ArtifactError):
        activate_run(root=tmp_path, product_id="weather", run_id="run-a", expected_manifest_sha256=None)


def test_unknown_run_and_missing_blob_have_different_statuses(tmp_path: Path) -> None:
    args = {
        "root": tmp_path,
        "product_id": "weather",
        "selection": _selection(),
        "requested_zoom": 13,
        "now": NOW,
        "max_distance_m": 1000,
    }
    assert read_selected_forecast(**args).status == "not_yet_generated"
    manifest = _publish(tmp_path)
    (tmp_path / "blobs" / (manifest.parquet_sha256 + ".parquet")).unlink()
    assert read_selected_forecast(**args).status == "upstream_unavailable"


def test_location_preserves_coordinates_and_reports_distance_missingness_and_staleness(tmp_path: Path) -> None:
    _publish(tmp_path)
    selected = _selection(longitude=-120.001)
    result = read_selected_forecast(
        root=tmp_path, product_id="weather", selection=selected, requested_zoom=13, now=NOW, max_distance_m=1000
    )
    assert result.selection == selected
    assert result.status == "available"
    assert result.sample_distance_m is not None
    assert NEARBY_DISTANCE_MIN_M < result.sample_distance_m < NEARBY_DISTANCE_MAX_M
    assert all(row.longitude == SAMPLES[0].longitude for row in result.values)
    outside = read_selected_forecast(
        root=tmp_path, product_id="weather", selection=selected, requested_zoom=13, now=NOW, max_distance_m=10
    )
    assert outside.status == "outside_domain"
    assert outside.values == ()
    missing = read_selected_forecast(
        root=tmp_path,
        product_id="weather",
        selection=_selection(longitude=-119.0),
        requested_zoom=13,
        now=NOW,
        max_distance_m=0,
    )
    assert missing.status == "missing"
    assert all(row.value is None for row in missing.values)
    stale = read_selected_forecast(
        root=tmp_path,
        product_id="weather",
        selection=selected,
        requested_zoom=13,
        now=NOW + timedelta(days=3),
        max_distance_m=1000,
    )
    assert stale.status == "stale_run"
    assert stale.values == result.values


def test_exact_window_refuses_instead_of_using_neighbouring_times(tmp_path: Path) -> None:
    _publish(tmp_path)
    selected = ForecastSelection(
        run_id="run-a", longitude=-120.0, latitude=45.0, start=END, end=END + timedelta(hours=1), timezone="UTC"
    )
    result = read_selected_forecast(
        root=tmp_path, product_id="weather", selection=selected, requested_zoom=13, now=NOW, max_distance_m=1000
    )
    assert result.status == "exact_absence"
    assert result.values == ()


def test_field_filters_sample_coordinates_and_refuses_row_budget(tmp_path: Path) -> None:
    _publish(tmp_path)
    result = read_forecast_field(
        root=tmp_path,
        product_id="weather",
        run_id="run-a",
        requested_zoom=5,
        bbox=(-120.1, 44.9, -119.9, 45.1),
        start=START,
        end=END,
        variable="temperature_2m",
        now=NOW,
    )
    assert result.zoom == EXPECTED_FIELD_ZOOM
    assert result.status == "available"
    assert len(result.values) == EXPECTED_FIELD_COUNT
    assert all(row.longitude == SAMPLES[0].longitude for row in result.values)
    assert "geometry" not in result.model_dump()
    with pytest.raises(ValueError, match="row budget"):
        read_forecast_field(
            root=tmp_path,
            product_id="weather",
            run_id="run-a",
            requested_zoom=5,
            bbox=(-121.0, 44.0, -118.0, 46.0),
            start=START,
            end=END,
            variable="temperature_2m",
            now=NOW,
            max_rows=1,
        )


def test_bounds_path_and_hour_contracts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ArtifactError, match="identity"):
        read_run(root=tmp_path, product_id="../escape", run_id="run-a")
    with pytest.raises(ValueError, match="forecast reader window exceeds ten days"):
        ForecastSelection(
            run_id="run-a", longitude=-120.0, latitude=45.0, start=START, end=START + timedelta(days=11), timezone="UTC"
        )
    with pytest.raises(ValueError, match="forecast reader requires hourly boundaries"):
        ForecastSelection(
            run_id="run-a", longitude=-120.0, latitude=45.0, start=START + timedelta(minutes=1), end=END, timezone="UTC"
        )
    monkeypatch.setattr(artifacts, "MAX_SOURCE_BYTES", 1)
    with pytest.raises(ArtifactError, match="byte budget"):
        _prepare(tmp_path)


def test_competing_pointer_updates_have_one_winner(tmp_path: Path) -> None:
    _prepare(tmp_path)
    _prepare(tmp_path, "run-b")

    def activate(run_id: str) -> str:
        try:
            activate_run(root=tmp_path, product_id="weather", run_id=run_id, expected_manifest_sha256=None)
        except ArtifactError:
            return "conflict"
        return "activated"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(activate, ("run-a", "run-b")))
    assert sorted(results) == ["activated", "conflict"]


def test_prepared_run_requires_receipt_and_publication_time_is_actual_commit(tmp_path: Path) -> None:
    _prepare(tmp_path)
    before = read_selected_forecast(
        root=tmp_path, product_id="weather", selection=_selection(), requested_zoom=13, now=NOW, max_distance_m=1000
    )
    assert before.status == "not_yet_generated"
    assert before.run is None
    activate_run(root=tmp_path, product_id="weather", run_id="run-a", expected_manifest_sha256=None, now=NOW)
    after = read_selected_forecast(
        root=tmp_path, product_id="weather", selection=_selection(), requested_zoom=13, now=NOW, max_distance_m=1000
    )
    assert after.status == "available"
    assert after.run is not None
    assert after.run.published_at == NOW
    earlier = read_selected_forecast(
        root=tmp_path,
        product_id="weather",
        selection=_selection(),
        requested_zoom=13,
        now=NOW - timedelta(hours=1),
        max_distance_m=1000,
    )
    assert earlier.status == "not_yet_generated"
    assert earlier.values == ()


def test_canonical_schema_keeps_all_missing_values_numeric(tmp_path: Path) -> None:
    series = _series()
    fields = series.model_dump(mode="json")
    for row in fields["values"]:
        row["value"] = None
        row["status"] = "missing"
    prepared = prepare_run(
        root=tmp_path,
        series=ForecastSeries.model_validate(fields),
        samples=SAMPLES,
        start=START,
        end=END,
        source_payload=SOURCE,
    )
    _, restored = read_run(root=tmp_path, product_id="weather", run_id="run-a")
    assert all(row.value is None for row in restored.values)
    assert prepared.status_counts == {"missing": 4}


def test_receipt_survives_pointer_interruption_and_retry_preserves_publication_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepare(tmp_path)
    original = artifacts._atomic

    def fail_pointer(path: Path, data: bytes, *, immutable: bool) -> None:
        if path.name == "active.json":
            raise OSError("pointer interruption")
        original(path, data, immutable=immutable)

    monkeypatch.setattr(artifacts, "_atomic", fail_pointer)
    with pytest.raises(OSError, match="pointer interruption"):
        activate_run(root=tmp_path, product_id="weather", run_id="run-a", expected_manifest_sha256=None, now=NOW)
    assert not (tmp_path / "weather" / "active.json").exists()
    assert artifacts.read_published_run(root=tmp_path, product_id="weather", run_id="run-a")[1].run.published_at == NOW
    monkeypatch.setattr(artifacts, "_atomic", original)
    activate_run(
        root=tmp_path, product_id="weather", run_id="run-a", expected_manifest_sha256=None, now=NOW + timedelta(hours=1)
    )
    assert (tmp_path / "weather" / "active.json").exists()
    assert artifacts.read_published_run(root=tmp_path, product_id="weather", run_id="run-a")[1].run.published_at == NOW
