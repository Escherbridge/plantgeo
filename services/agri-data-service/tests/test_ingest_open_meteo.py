"""Open-Meteo ingestion: grid densification, payload validation, and the millisecond timestamp inside the key."""

# ruff: noqa: PLR2004

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import pytest

from agri_data_service.ingest.http import UpstreamPayloadError
from agri_data_service.ingest.open_meteo import (
    DEFAULT_WEATHER_LAYER_NAME,
    MAX_OBSERVATION_AGE,
    OPEN_METEO_BASE_URL,
    OPEN_METEO_BOUNDS,
    OPEN_METEO_CHANNEL,
    OPEN_METEO_SOURCE,
    WEATHER_LAYER_VARIABLE,
    bounded_sample_points,
    build_weather_write,
    current_weather_url,
    get_current_weather,
    parse_current_weather,
    resolve_weather_layer_name,
    run_weather_ingestion_job,
)
from agri_data_service.ingest.policy import MAX_WEATHER_SAMPLE_POINTS, PACIFIC_NORTHWEST_COVERAGE_BBOX

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.ingest.writer import FeatureWrite

NOW = datetime(2026, 8, 3, 14, 10, tzinfo=UTC)
OBSERVATION_EPOCH_SECONDS = 1_785_766_500  # 2026-08-03T14:15:00Z

# Captured 2026-08-03 read-only from production `geo.features` on the `weather-observations` layer:
# the last element is the exact `properties->>'id'` the TypeScript job stored. The default 1-degree
# sample spacing over the PNW bbox lands every grid centre on a `.5` degree, so this row does not
# exercise a `toFixed` tie by itself -- that hazard belongs to identity.py's own golden file. What
# this fixture pins is the observedAt-inside-the-key shape (trap T3) against a real stored row.
RECORDED_OBSERVATION = (
    1_785_816_000,  # Open-Meteo `current.time` (Unix seconds) for 2026-08-04T04:00:00Z.
    46.5,
    -124.5,
    {
        "temperature_2m": 16.6,
        "relative_humidity_2m": 84,
        "wind_speed_10m": 8.61,
        "wind_direction_10m": 9,
        "precipitation": 0,
    },
    "46.5000:-124.5000:2026-08-04T04:00:00.000Z",
)


class RecordingWriter:
    """A feature writer that records what a job handed it, so a job test needs no database."""

    def __init__(self) -> None:
        self.writes: list[FeatureWrite] = []

    async def __call__(self, writes: Sequence[FeatureWrite]) -> int:
        self.writes = list(writes)
        return len(self.writes)


def _payload(epoch_seconds: int = OBSERVATION_EPOCH_SECONDS, **overrides: float) -> dict[str, object]:
    current: dict[str, object] = {
        "time": epoch_seconds,
        "temperature_2m": 21.5,
        "relative_humidity_2m": 40.0,
        "wind_speed_10m": 3.5,
        "wind_direction_10m": 180.0,
        "precipitation": 0.0,
    }
    current.update(overrides)
    return {"current": current}


@pytest.fixture(autouse=True)
def _clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("INGEST_BBOX", "WEATHER_SAMPLE_SPACING_DEGREES", "WEATHER_LAYER_ID"):
        monkeypatch.delenv(variable, raising=False)


def test_the_grid_is_densified_never_sliced() -> None:
    # A 0.25 degree grid over the coverage box would be 56 x 28 = 1568 points; spacing grows until it fits.
    points = bounded_sample_points(PACIFIC_NORTHWEST_COVERAGE_BBOX, 0.25)
    assert 0 < len(points) <= 150
    latitudes = [latitude for latitude, _ in points]
    longitudes = [longitude for _, longitude in points]
    # Slicing a 1568-point list would blank the eastern half; densifying keeps the full extent covered.
    assert min(longitudes) > -125.0
    assert max(longitudes) < -111.0
    assert min(latitudes) > 42.0
    assert max(latitudes) < 49.0


def test_a_grid_that_already_fits_keeps_its_requested_spacing() -> None:
    assert len(bounded_sample_points("-120,44,-118,46", 1.0)) == 4


def test_the_request_url_formats_coordinates_the_way_javascript_does() -> None:
    url = current_weather_url(44.5, -119.0)
    assert "latitude=44.5" in url
    assert "longitude=-119" in url
    assert "timeformat=unixtime" in url
    assert "timezone=GMT" in url


def test_the_observation_timestamp_is_the_javascript_iso_form_with_milliseconds() -> None:
    # Trap T3: `datetime.isoformat()` would emit "+00:00" and no fraction, forking every weather key.
    observation = parse_current_weather(_payload(), NOW)
    assert observation["observedAt"] == "2026-08-03T14:15:00.000Z"


def test_a_stale_observation_is_refused() -> None:
    with pytest.raises(UpstreamPayloadError, match="stale"):
        parse_current_weather(_payload(), datetime(2026, 8, 3, 20, 0, tzinfo=UTC))


@pytest.mark.parametrize(
    "overrides",
    [
        {"temperature_2m": 200.0},
        {"relative_humidity_2m": -1.0},
        {"wind_speed_10m": 999.0},
        {"wind_direction_10m": 400.0},
        {"precipitation": -1.0},
    ],
)
def test_an_out_of_range_reading_is_refused(overrides: dict[str, float]) -> None:
    with pytest.raises(UpstreamPayloadError, match="invalid"):
        parse_current_weather(_payload(**overrides), NOW)


@pytest.mark.parametrize("payload", [{}, {"current": {}}, {"current": {"time": -1}}, "not-an-object"])
def test_a_malformed_payload_is_refused(payload: object) -> None:
    with pytest.raises(UpstreamPayloadError, match="invalid"):
        parse_current_weather(payload, NOW)


def test_a_sample_point_keys_the_coordinates_at_four_digits_and_the_instant_verbatim() -> None:
    observation = parse_current_weather(_payload(), NOW)
    write = build_weather_write(42.5, -111.5, observation, "weather-observations")
    assert write is not None
    assert write.external_id == "42.5000:-111.5000:2026-08-03T14:15:00.000Z"
    assert write.natural_key == "open-meteo:42.5000:-111.5000:2026-08-03T14:15:00.000Z"
    assert write.channel == "layer:weather-observations"
    assert write.properties["source"] == "Open-Meteo"
    assert write.properties["geometry"] == {"type": "Point", "coordinates": [-111.5, 42.5]}


async def test_an_unset_bbox_is_skipped_and_never_failed() -> None:
    result = await run_weather_ingestion_job(RecordingWriter())
    assert result.source == OPEN_METEO_SOURCE
    assert result.status == "skipped"
    assert result.reason == "INGEST_BBOX is not configured"


async def test_one_failing_sample_point_never_discards_the_rest_of_the_grid() -> None:
    answered: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        answered.append(str(request.url))
        if len(answered) == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            content=json.dumps(_payload()).encode(),
            headers={"content-type": "application/json"},
        )

    writer = RecordingWriter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await run_weather_ingestion_job(writer, bbox="-120,44,-118,46", client=client, now=NOW)

    assert result.status == "ingested"
    assert result.records_seen == 4
    assert result.records_written == 3
    assert result.details["unavailable_points"] == 1
    assert len(writer.writes) == 3


def test_the_upstream_bounds_and_freshness_window_are_pinned_to_the_typescript_values() -> None:
    assert OPEN_METEO_BASE_URL == "https://api.open-meteo.com/v1/forecast"
    assert OPEN_METEO_BOUNDS.max_bytes == 128 * 1024
    assert OPEN_METEO_BOUNDS.timeout_seconds == 5.0
    assert timedelta(hours=3) == MAX_OBSERVATION_AGE
    assert OPEN_METEO_CHANNEL == "layer:weather-observations"


def test_resolve_weather_layer_name_defaults_and_reads_the_environment_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert resolve_weather_layer_name() == DEFAULT_WEATHER_LAYER_NAME == "weather-observations"
    monkeypatch.setenv(WEATHER_LAYER_VARIABLE, "custom-weather-layer")
    assert resolve_weather_layer_name() == "custom-weather-layer"


def test_the_densified_grid_redistributes_across_both_axes_rather_than_favouring_the_first_columns() -> None:
    # A naive 0.1deg spacing wants a 140x70 = 9800-point grid over the PNW bbox; growth must scale
    # spacing on BOTH axes until the grid fits. A bug that instead sliced the first 150 points off
    # the naive column-major list would still satisfy "every point lies strictly inside the bbox"
    # (test_the_grid_is_densified_never_sliced, above) because slicing keeps only the westernmost
    # few columns -- it would not touch the min/max bounds, only the number of distinct longitudes
    # reached. Pinning the distinct-value counts is what actually catches that failure mode.
    points = bounded_sample_points(PACIFIC_NORTHWEST_COVERAGE_BBOX, 0.1)
    assert len(points) == 128
    assert len(points) <= MAX_WEATHER_SAMPLE_POINTS
    assert len({lon for _lat, lon in points}) == 16
    assert len({lat for lat, _lon in points}) == 8


async def test_get_current_weather_rejects_coordinates_outside_wgs84_bounds() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200))) as client:
        with pytest.raises(ValueError, match="WGS84"):
            await get_current_weather(client, 91.0, 0.0)
        with pytest.raises(ValueError, match="WGS84"):
            await get_current_weather(client, 0.0, 181.0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"temperature_2m": -100.0},
        {"temperature_2m": 70.0},
        {"relative_humidity_2m": 0.0},
        {"relative_humidity_2m": 100.0},
        {"wind_speed_10m": 0.0},
        {"wind_speed_10m": 150.0},
        {"wind_direction_10m": 0.0},
        {"wind_direction_10m": 360.0},
        {"precipitation": 0.0},
        {"precipitation": 1_000.0},
    ],
)
def test_a_value_exactly_at_its_bound_is_accepted_not_rejected(overrides: dict[str, float]) -> None:
    parse_current_weather(_payload(**overrides), NOW)  # does not raise


def test_a_recorded_production_observation_still_keys_to_the_stored_external_id() -> None:
    unix_time, latitude, longitude, current, stored_external_id = RECORDED_OBSERVATION
    # Freshness is relative to when the row was captured, not to the module-level NOW fixture
    # (which predates this row): use an instant shortly after the recorded observedAt.
    captured_at = datetime(2026, 8, 4, 4, 30, tzinfo=UTC)
    observation = parse_current_weather({"current": {"time": unix_time, **current}}, captured_at)
    write = build_weather_write(latitude, longitude, observation, "weather-observations")
    assert write is not None
    assert write.external_id == stored_external_id
    assert write.natural_key == f"open-meteo:{stored_external_id}"
    assert write.properties["observedAt"] == "2026-08-04T04:00:00.000Z"
    assert not str(write.properties["observedAt"]).endswith("+00:00")
    assert write.properties["source"] == "Open-Meteo"
    assert write.properties["geometry"] == {"type": "Point", "coordinates": [-124.5, 46.5]}
    assert write.channel == "layer:weather-observations"
