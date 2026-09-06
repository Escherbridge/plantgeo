"""Open-Meteo source adapter: grid densification, payload validation, the millisecond timestamp in the key.

The forward `geo.features` job this file also covered (`run_weather_ingestion_job`) and its row
builder (`build_weather_write`) were deleted 2026-09-06 with the `ingest-weather` verb and the
`postgres-weather` lane.
"""

# ruff: noqa: PLR2004

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from agri_data_service.ingest.http import UpstreamPayloadError
from agri_data_service.ingest.identity import build_weather_observation_identity
from agri_data_service.ingest.open_meteo import (
    DEFAULT_WEATHER_LAYER_NAME,
    MAX_OBSERVATION_AGE,
    OPEN_METEO_BASE_URL,
    OPEN_METEO_BOUNDS,
    OPEN_METEO_CHANNEL,
    OPEN_METEO_FORECAST_HISTORY_RETENTION,
    OPEN_METEO_FORECAST_PAST_DAYS_MAXIMUM,
    WEATHER_LAYER_VARIABLE,
    bounded_sample_points,
    current_weather_url,
    get_current_weather,
    parse_current_weather,
    resolve_weather_layer_name,
    weather_history_capability,
)
from agri_data_service.ingest.policy import MAX_WEATHER_SAMPLE_POINTS, PACIFIC_NORTHWEST_COVERAGE_BBOX

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
    """The key contract, asserted on the identity itself now that `build_weather_write` is gone.

    `build_weather_write` built the `geo.features` row and was deleted 2026-09-06 with
    `run_weather_ingestion_job`. It never computed the key -- it delegated to
    `build_weather_observation_identity`, which `pipeline/direct/weather_observations/rows.py` calls
    for exactly the same purpose -- so the pin moves down one layer and loses nothing.
    """
    observation = parse_current_weather(_payload(), NOW)
    identity = build_weather_observation_identity(42.5, -111.5, observation)
    assert identity.producer_local_id == "42.5000:-111.5000:2026-08-03T14:15:00.000Z"
    assert identity.natural_key == "open-meteo:42.5000:-111.5000:2026-08-03T14:15:00.000Z"


# TWO JOB TESTS STOOD HERE AND ARE DELETED WITH THEIR SUBJECT (2026-09-06): the unset-bbox skip and
# the one-failing-point isolation both exercised `run_weather_ingestion_job`. Its replacement,
# `pipeline/direct/weather_observations/forward.py`, reuses the same `get_current_weather` per point
# and states the same isolation rule in `source.py::poll_current_conditions`, which `tests/direct/`
# covers; the bbox refusal moved to `support.py::weather_sample_points` raising `WeatherSupportError`.


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


def test_the_history_declaration_rolls_with_the_clock_rather_than_freezing_at_import() -> None:
    # The forecast endpoint keeps a ROLLING past window, so the floor is resolved per call the way
    # `sensors.nws_sensor_source` resolves NWS' retention. Declared, not implemented: no fetcher
    # walks a past Open-Meteo window yet. See ingest/AGENTS.md "history declarations".
    assert OPEN_METEO_FORECAST_PAST_DAYS_MAXIMUM == 92
    assert timedelta(days=92) == OPEN_METEO_FORECAST_HISTORY_RETENTION
    capability = weather_history_capability(NOW)
    assert capability.supported is True
    assert capability.earliest == NOW - OPEN_METEO_FORECAST_HISTORY_RETENTION
    assert weather_history_capability(NOW + timedelta(days=1)).earliest != capability.earliest


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
    identity = build_weather_observation_identity(latitude, longitude, observation)
    assert identity.producer_local_id == stored_external_id
    assert identity.natural_key == f"open-meteo:{stored_external_id}"
    assert observation["observedAt"] == "2026-08-04T04:00:00.000Z"
    assert not str(observation["observedAt"]).endswith("+00:00")
