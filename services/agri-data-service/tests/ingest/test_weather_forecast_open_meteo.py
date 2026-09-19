"""The `weather-forecast` source: coverage, bounded fetch budget, byte-identical replay, wind and precipitation.

W8-E S2 (`.omc/ultrapilot-20260918/W8-E-PLAN.md` §2 row S2). See
`agri_data_service.ingest.weather_forecast.AGENTS.md` for the normalisation decisions this module
makes at the source boundary.
"""

# ruff: noqa: PLR2004

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import httpx
import pytest

from agri_data_service.foundation.region.source_coverage import SourceCoverageClaim
from agri_data_service.ingest.http import UpstreamPayloadError
from agri_data_service.ingest.open_meteo import OpenMeteoRateLimitError
from agri_data_service.ingest.weather_forecast.open_meteo import (
    MAX_FORECAST_RUN_DAYS,
    MAX_FORECAST_RUN_LOCATIONS,
    OPEN_METEO_SINGLE_RUN_BASE_URL,
    OPEN_METEO_WEATHER_FORECAST_SOURCE,
    WEATHER_FORECAST_COVERAGE,
    WEATHER_FORECAST_RUN_BOUNDS,
    WEATHER_FORECAST_SOURCE_SLUG,
    WEATHER_FORECAST_UPSTREAM_VARIABLES,
    OpenMeteoWeatherForecastSource,
    WeatherForecastRequestError,
    fetch_forecast_run,
    fetch_forecast_run_text,
    forecast_run_id,
    forecast_run_url,
    parse_forecast_run_payload,
)
from agri_data_service.ingest.weather_forecast.source_protocol import (
    WeatherForecastLocationPayload,
    WeatherForecastRunPayload,
    WeatherForecastSamplePayload,
    WeatherForecastSource,
)

FIXTURE_PATH: Final = Path(__file__).resolve().parents[1] / "fixtures" / "weather_forecast_open_meteo_run_response.json"
FIXTURE_SHA256: Final = "9ff34930651251183b21b60528d1f1e1ba77579f56eb3a43240c7cb2269a954a"

RUN_INIT_TIME: Final = datetime(2026, 9, 18, 0, 0, tzinfo=UTC)
COORDINATES: Final = ((46.5, -124.5), (45.0, -122.0))
VARIABLES: Final = sorted(WEATHER_FORECAST_UPSTREAM_VARIABLES)


def _mock_client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- Coverage and protocol conformance ----------------------------------------------------------


def test_the_open_meteo_source_declares_global_coverage() -> None:
    """§1b: `coverage="global"` -- Open-Meteo's Single Runs endpoint is not restricted to one country."""
    assert isinstance(WEATHER_FORECAST_COVERAGE, SourceCoverageClaim)
    assert WEATHER_FORECAST_COVERAGE.coverage == "global"
    assert WEATHER_FORECAST_COVERAGE.iso_country_codes == ()


def test_the_open_meteo_source_satisfies_the_weather_forecast_source_protocol() -> None:
    """`isinstance` against the runtime-checkable protocol, the same conformance check the drought
    and burn-severity lanes prove in `tests/foundation/test_source_protocols.py`."""
    source = OPEN_METEO_WEATHER_FORECAST_SOURCE
    assert isinstance(source, WeatherForecastSource)
    assert source.source_slug == WEATHER_FORECAST_SOURCE_SLUG
    assert isinstance(source.coverage, SourceCoverageClaim)


def test_a_fabricated_non_open_meteo_source_satisfies_the_same_protocol() -> None:
    """The whole point of a protocol: a second provider needs no `OpenMeteoWeatherForecastSource` ancestry."""

    class FabricatedForecastSource:
        source_slug = "fabricated-nwp"
        coverage = WEATHER_FORECAST_COVERAGE

        async def fetch_run(
            self,
            *,
            model_init_time: datetime,
            coordinates: object,
            variables: object,
            forecast_days: int,
        ) -> object:
            raise NotImplementedError

    assert isinstance(FabricatedForecastSource(), WeatherForecastSource)


def test_the_parsed_run_satisfies_the_payload_protocols() -> None:
    """The concrete dataclasses parsed off a real fixture must satisfy the protocols structurally."""
    body = FIXTURE_PATH.read_bytes()
    run = parse_forecast_run_payload(
        body.decode("utf-8"),
        coordinates=COORDINATES,
        variables=VARIABLES,
        model="gfs_global",
        model_init_time=RUN_INIT_TIME,
        fetched_at=datetime(2026, 9, 18, 6, 0, tzinfo=UTC),
    )
    assert isinstance(run, WeatherForecastRunPayload)
    assert isinstance(run.locations[0], WeatherForecastLocationPayload)
    assert isinstance(run.locations[0].samples[0], WeatherForecastSamplePayload)


# --- Bounded fetch budget: refuses over budget rather than truncating ---------------------------


def test_forecast_run_url_refuses_more_locations_than_the_budget() -> None:
    over_budget = tuple((0.0, 0.0) for _ in range(MAX_FORECAST_RUN_LOCATIONS + 1))
    with pytest.raises(WeatherForecastRequestError, match="locations"):
        forecast_run_url(over_budget, VARIABLES, RUN_INIT_TIME, 1)


def test_forecast_run_url_refuses_zero_locations() -> None:
    with pytest.raises(WeatherForecastRequestError, match="locations"):
        forecast_run_url((), VARIABLES, RUN_INIT_TIME, 1)


def test_forecast_run_url_refuses_an_unreviewed_variable() -> None:
    with pytest.raises(WeatherForecastRequestError, match="reviewed catalogue"):
        forecast_run_url(COORDINATES, (*VARIABLES, "surface_pressure"), RUN_INIT_TIME, 1)


def test_forecast_run_url_refuses_unsorted_variables() -> None:
    with pytest.raises(WeatherForecastRequestError, match="sorted"):
        forecast_run_url(COORDINATES, tuple(reversed(VARIABLES)), RUN_INIT_TIME, 1)


def test_forecast_run_url_refuses_forecast_days_over_the_ceiling() -> None:
    with pytest.raises(WeatherForecastRequestError, match="forecast_days"):
        forecast_run_url(COORDINATES, VARIABLES, RUN_INIT_TIME, MAX_FORECAST_RUN_DAYS + 1)


def test_forecast_run_url_refuses_forecast_days_below_the_floor() -> None:
    with pytest.raises(WeatherForecastRequestError, match="forecast_days"):
        forecast_run_url(COORDINATES, VARIABLES, RUN_INIT_TIME, 0)


def test_forecast_run_url_refuses_coordinates_outside_wgs84_bounds() -> None:
    with pytest.raises(WeatherForecastRequestError, match="WGS84"):
        forecast_run_url(((91.0, 0.0),), VARIABLES, RUN_INIT_TIME, 1)


def test_forecast_run_url_refuses_a_naive_model_init_time() -> None:
    with pytest.raises(WeatherForecastRequestError, match="timezone-aware"):
        forecast_run_url(COORDINATES, VARIABLES, datetime(2026, 9, 18, 0, 0), 1)  # noqa: DTZ001 - the point under test


def test_forecast_run_url_carries_the_bounded_shape() -> None:
    url = forecast_run_url(COORDINATES, VARIABLES, RUN_INIT_TIME, 3)
    assert url.startswith(OPEN_METEO_SINGLE_RUN_BASE_URL + "?")
    assert "models=gfs_global" in url
    assert "run=2026-09-18T00%3A00" in url
    assert "forecast_days=3" in url
    assert "cell_selection=nearest" in url
    assert "timezone=UTC" in url


def test_forecast_run_id_matches_the_frozen_schema_format() -> None:
    """`<provider>:<model>:<init_time ISO>` -- `warehouse/schemas/weather_forecast.py:73`."""
    assert forecast_run_id("gfs_global", RUN_INIT_TIME) == "open-meteo:gfs_global:2026-09-18T00:00:00+00:00"


# --- Rate limit: raises OpenMeteoRateLimitError, never a retry loop -----------------------------


async def test_a_429_raises_open_meteo_rate_limit_error_without_retrying() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, json={"reason": "Daily API request limit exceeded."})

    async with _mock_client(handler) as client:
        with pytest.raises(OpenMeteoRateLimitError) as failure:
            await fetch_forecast_run_text(client, forecast_run_url(COORDINATES, VARIABLES, RUN_INIT_TIME, 1))

    assert failure.value.scope == "day"
    assert len(calls) == 1, "a 429 must not be retried -- the plan's S2 acceptance is explicit"


async def test_an_unclassifiable_429_body_reports_an_unknown_scope() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    async with _mock_client(handler) as client:
        with pytest.raises(OpenMeteoRateLimitError) as failure:
            await fetch_forecast_run_text(client, forecast_run_url(COORDINATES, VARIABLES, RUN_INIT_TIME, 1))

    assert failure.value.scope == "unknown"


async def test_a_non_json_content_type_is_a_payload_error() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>", headers={"content-type": "text/html"})

    async with _mock_client(handler) as client:
        with pytest.raises(UpstreamPayloadError, match="not JSON"):
            await fetch_forecast_run_text(client, forecast_run_url(COORDINATES, VARIABLES, RUN_INIT_TIME, 1))


# --- Byte-identical replay of a recorded response ------------------------------------------------


async def test_a_replayed_recorded_response_is_byte_identical() -> None:
    """§2 row S2 acceptance: "replay of a recorded response is byte-identical."

    The fixture's own SHA-256 guards it from an accidental edit, matching
    `tests/direct/climate/test_source.py`'s "the capture is a fixture; it must not be edited"
    convention. The fetched text must equal the fixture's raw bytes exactly, and re-fetching (the
    replay) must produce the identical text a second time.
    """
    body = FIXTURE_PATH.read_bytes()
    assert hashlib.sha256(body).hexdigest() == FIXTURE_SHA256, "the fixture is recorded evidence; do not edit it"

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    url = forecast_run_url(COORDINATES, VARIABLES, RUN_INIT_TIME, 1)
    async with _mock_client(handler) as client:
        first = await fetch_forecast_run_text(client, url)
    async with _mock_client(handler) as client:
        second = await fetch_forecast_run_text(client, url)

    assert first == second, "replaying the same recorded response must be byte-identical across calls"
    assert first == body.decode("utf-8"), "the fetched text must carry the recorded bytes exactly, unmutated"


async def test_fetch_forecast_run_parses_the_replayed_fixture_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """The full `fetch_forecast_run` path (URL, fetch, parse) over the recorded fixture, no live network."""
    body = FIXTURE_PATH.read_bytes()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    monkeypatch.setattr(
        "agri_data_service.ingest.weather_forecast.open_meteo.upstream_client",
        lambda _bounds: _mock_client(handler),
    )

    run = await fetch_forecast_run(
        model_init_time=RUN_INIT_TIME,
        coordinates=COORDINATES,
        variables=VARIABLES,
        forecast_days=1,
    )

    assert run.run_id == forecast_run_id("gfs_global", RUN_INIT_TIME)
    assert run.provider_issue_time is None
    assert len(run.locations) == 2


# --- Parsing: shape, wind derivation, precipitation window, missingness -------------------------


def _parse_fixture() -> object:
    body = FIXTURE_PATH.read_bytes().decode("utf-8")
    return parse_forecast_run_payload(
        body,
        coordinates=COORDINATES,
        variables=VARIABLES,
        model="gfs_global",
        model_init_time=RUN_INIT_TIME,
        fetched_at=datetime(2026, 9, 18, 6, 0, tzinfo=UTC),
    )


def _samples(run: object, location_index: int, variable: str) -> list[object]:
    return [sample for sample in run.locations[location_index].samples if sample.variable == variable]


def test_parse_rejects_a_response_with_the_wrong_number_of_locations() -> None:
    with pytest.raises(UpstreamPayloadError, match="one entry per requested location"):
        parse_forecast_run_payload(
            "[]",
            coordinates=COORDINATES,
            variables=VARIABLES,
            model="gfs_global",
            model_init_time=RUN_INIT_TIME,
            fetched_at=RUN_INIT_TIME,
        )


def test_parse_accepts_a_bare_object_for_one_coordinate() -> None:
    single_location_body = FIXTURE_PATH.read_text(encoding="utf-8")
    first_entry = json.loads(single_location_body)[0]
    run = parse_forecast_run_payload(
        json.dumps(first_entry),
        coordinates=(COORDINATES[0],),
        variables=VARIABLES,
        model="gfs_global",
        model_init_time=RUN_INIT_TIME,
        fetched_at=RUN_INIT_TIME,
    )
    assert len(run.locations) == 1
    assert run.locations[0].latitude == pytest.approx(46.502)


def test_wind_is_derived_into_u_v_components_with_the_named_formula() -> None:
    """AGENTS.md "Wind: derived from speed/direction": `u=-speed*sin(direction)`, `v=-speed*cos(direction)`."""
    run = _parse_fixture()
    speed = _samples(run, 1, "wind_speed_10m")[0].value  # location 1, hour 0: speed=1.5, direction=90
    direction = _samples(run, 1, "wind_direction_10m")[0].value
    east = _samples(run, 1, "wind_u_10m")[0].value
    north = _samples(run, 1, "wind_v_10m")[0].value

    assert speed == pytest.approx(1.5)
    assert direction == pytest.approx(90.0)
    assert east == pytest.approx(-1.5 * math.sin(math.radians(90.0)))
    assert north == pytest.approx(-1.5 * math.cos(math.radians(90.0)))
    # A due-east meteorological bearing (wind FROM 90 deg / from the east) blows westward: u < 0.
    assert east < 0
    assert north == pytest.approx(0.0, abs=1e-9)


def test_calm_wind_leaves_direction_and_components_as_a_governed_absence() -> None:
    """Location 0, hour 2: `wind_speed_10m=0.0`. Speed stays a real zero; bearing is undefined."""
    run = _parse_fixture()
    speed_sample = _samples(run, 0, "wind_speed_10m")[2]
    direction_sample = _samples(run, 0, "wind_direction_10m")[2]
    u_sample = _samples(run, 0, "wind_u_10m")[2]
    v_sample = _samples(run, 0, "wind_v_10m")[2]

    assert speed_sample.value == 0.0
    assert speed_sample.missing_reason is None, "a numeric zero is a value, never a stand-in for absence"
    for sample in (direction_sample, u_sample, v_sample):
        assert sample.value is None
        assert sample.missing_reason == "not_generated"


def test_a_null_upstream_reading_becomes_a_governed_absence() -> None:
    """Location 0, hour 2: `cloud_cover=null` in the fixture."""
    run = _parse_fixture()
    cloud_cover_hour_2 = _samples(run, 0, "cloud_cover")[2]
    assert cloud_cover_hour_2.value is None
    assert cloud_cover_hour_2.missing_reason == "not_generated"


def test_precipitation_valid_time_is_shifted_one_hour_before_the_provider_timestamp() -> None:
    """AGENTS.md "The precipitation window": Open-Meteo's preceding-hour sum becomes a following-hour window."""
    run = _parse_fixture()
    precipitation = _samples(run, 0, "precipitation")

    hour_1 = next(sample for sample in precipitation if sample.value == pytest.approx(1.2))
    assert hour_1.valid_time == datetime(2026, 9, 18, 0, 0, tzinfo=UTC)  # provider's 01:00, shifted back 1h
    assert hour_1.interval_start == hour_1.valid_time
    assert hour_1.interval_end == datetime(2026, 9, 18, 1, 0, tzinfo=UTC)


def test_a_zero_precipitation_reading_is_a_value_not_an_absence() -> None:
    run = _parse_fixture()
    precipitation = _samples(run, 1, "precipitation")
    assert all(sample.value == 0.0 for sample in precipitation)
    assert all(sample.missing_reason is None for sample in precipitation)


def test_instantaneous_variables_keep_the_providers_own_timestamp() -> None:
    run = _parse_fixture()
    temperature = _samples(run, 0, "temperature_2m")
    assert temperature[0].valid_time == RUN_INIT_TIME
    assert temperature[0].interval_start is None
    assert temperature[0].interval_end is None
    assert temperature[0].value == pytest.approx(15.5)
    assert temperature[0].unit == "degC"
    assert temperature[0].statistic == "instantaneous"
    assert temperature[0].support == "sampled_point"


def test_every_sample_names_a_variable_in_the_frozen_catalogue() -> None:
    """Cross-checked against `warehouse/schemas/weather_forecast.py`'s `VARIABLES` keys."""
    frozen_variables = {
        "temperature_2m",
        "relative_humidity_2m",
        "cloud_cover",
        "precipitation",
        "wind_u_10m",
        "wind_v_10m",
        "wind_speed_10m",
        "wind_direction_10m",
    }
    run = _parse_fixture()
    for location in run.locations:
        for sample in location.samples:
            assert sample.variable in frozen_variables


# --- Domain isolation: this package imports no sibling ingest domain ----------------------------


def test_the_weather_forecast_source_module_carries_a_source_slug_and_coverage_class_var() -> None:
    """A binding has to be a VALUE a manifest can resolve to (`AGENTS.md`), so both must be class attrs."""
    assert OpenMeteoWeatherForecastSource.source_slug == "open-meteo"
    assert OpenMeteoWeatherForecastSource.coverage is WEATHER_FORECAST_COVERAGE


def test_the_run_bounds_match_the_frozen_plan_budget() -> None:
    """`.omc/ultrapilot-20260918/W8-E-PLAN.md` §2, "Bounded fetch budget (S2)": 64 MiB / 120 s."""
    assert WEATHER_FORECAST_RUN_BOUNDS.max_bytes == 64 * 1024 * 1024
    assert WEATHER_FORECAST_RUN_BOUNDS.timeout_seconds == 120.0
