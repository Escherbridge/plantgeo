"""Reconcile the captured Single Runs source through publication and selected agent context."""

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from agri_data_service.agent.weather_forecast import WeatherForecastContext, read_weather_forecast_context
from agri_data_service.pipeline.direct.weather_forecast.artifacts import ForecastSample, activate_run, prepare_run
from agri_data_service.pipeline.direct.weather_forecast.source import (
    LICENCE,
    MODEL,
    OUTPUT_VARIABLES,
    PRODUCT,
    PROVIDER,
    SingleRunRequest,
    normalize_single_run,
)
from agri_data_service.planes.weather_forecast import ForecastSelection, read_selected_forecast
from agri_data_service.warehouse.weather_forecast.contracts import ForecastRun, SpatialSupport

FIXTURES = Path(__file__).parents[1] / "fixtures" / "weather_forecast"


def test_real_source_to_immutable_run_and_same_selected_agent_context(tmp_path: Path) -> None:
    receipt = json.loads((FIXTURES / "ecmwf_ifs_20260908T0000_boise.receipt.json").read_text())
    captured = (FIXTURES / "ecmwf_ifs_20260908T0000_boise.json").read_bytes()
    source = captured[: receipt["bytes"]]
    initialized = datetime(2026, 9, 8, tzinfo=UTC)
    published = datetime(2026, 9, 12, 2, tzinfo=UTC)
    request = SingleRunRequest(latitude=43.615, longitude=-116.2023, model_init_at=initialized)
    run = ForecastRun(
        run_id="ecmwf-ifs-20260908T0000",
        product_id=PRODUCT,
        provider=PROVIDER,
        model=MODEL,
        model_init_at=initialized,
        provider_issued_at=None,
        fetched_at=published - timedelta(minutes=2),
        admitted_at=published - timedelta(minutes=1),
        published_at=published,
        licence=LICENCE,
        source_url=request.url,
        source_payload_sha256=sha256(source).hexdigest(),
        support=SpatialSupport(kind="sampled_point"),
        variables=OUTPUT_VARIABLES,
    )
    series = normalize_single_run(source.decode(), request, run)
    first = series.values[0]
    sample = ForecastSample(sample_id=first.sample_id, longitude=first.longitude, latitude=first.latitude)
    end = initialized + timedelta(days=10)
    manifest = prepare_run(
        root=tmp_path, series=series, samples=(sample,), start=initialized, end=end, source_payload=source
    )
    selection = ForecastSelection(
        run_id=run.run_id,
        longitude=request.longitude,
        latitude=request.latitude,
        start=initialized,
        end=end,
        timezone="America/Boise",
    )
    before = read_selected_forecast(
        root=tmp_path, product_id=PRODUCT, selection=selection, requested_zoom=9, now=published, max_distance_m=10_000
    )
    assert before.status == "not_yet_generated"
    activate_run(root=tmp_path, product_id=PRODUCT, run_id=run.run_id, expected_manifest_sha256=None, now=published)
    result = read_weather_forecast_context(
        WeatherForecastContext(
            mode="forecast",
            location_source="search",
            product_id=PRODUCT,
            selection=selection,
            requested_zoom=9,
            max_distance_m=10_000,
        ),
        root=tmp_path,
        now=published,
    )
    assert result["status"] == "stale_run"
    assert result["selection"] == selection.model_dump(mode="json")
    assert result["run"]["provider_issued_at"] is None
    assert len(result["values"]) == manifest.row_count == len(series.values)
    assert manifest.status_counts["missing"] == len([row for row in series.values if row.status == "missing"])
    assert {(row["longitude"], row["latitude"]) for row in result["values"]} == {(sample.longitude, sample.latitude)}
    assert result["sample_distance_m"] > 0
