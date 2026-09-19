"""FR-12: the bounded Open-Meteo run fetch, replayed from the 2026-09-19 probe. No live HTTP.

Every fixture under `tests/fixtures/open_meteo/` is a verbatim capture from
`.omc/research/forecast-s3-probe-20260919/`, so what these cases assert about the provider is what
the provider actually answered.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final

import httpx
import pytest

from plantgeo_ml_service.pipeline.sources import open_meteo
from plantgeo_ml_service.pipeline.sources.open_meteo import (
    MAX_FORECAST_RUN_DAYS,
    MAX_FORECAST_RUN_LOCATIONS,
    MAX_SNAP_DEGREES,
    OPEN_METEO_FORECAST_RUN_MODEL,
    WEATHER_FORECAST_RUN_BOUNDS,
    OpenMeteoRateLimitError,
    WeatherForecastRun,
    fetch_forecast_run_bytes,
    forecast_run_url,
    parse_forecast_run,
    redact_credentials,
    wind_components,
)
from plantgeo_ml_service.pipeline.sources.protocol import (
    SourceBounds,
    SourcePayloadError,
    SourceRequestError,
    SourceTransportError,
)
from plantgeo_ml_service.warehouse.weather_forecast import UPSTREAM_VARIABLES, VARIABLES

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

FIXTURES: Final = Path(__file__).resolve().parent / "fixtures" / "open_meteo"

MULTI_LOCATION_PAYLOAD: Final = (FIXTURES / "multi-location-run-20260918.json").read_bytes()
PRECIPITATION_PAYLOAD: Final = (FIXTURES / "precipitation-crosscheck-20260918.json").read_bytes()

RUN_INIT: Final = datetime(2026, 9, 18, tzinfo=UTC)
FETCHED_AT: Final = datetime(2026, 9, 19, 2, 0, tzinfo=UTC)

#: The two coordinates `multi-location-request.txt` asked for, in request order.
MULTI_LOCATION_COORDINATES: Final = ((46.25, -120.25), (47.25, -119.25))

#: Hours in one calendar day, named so a count assertion is not a bare number.
HOURS_PER_DAY: Final = 24

CALM_METRES_PER_SECOND: Final = 0.0

#: One arbitrary but exact wind reading, used to prove the u/v conversion round-trips.
SPEED_METRES_PER_SECOND: Final = 10.0
BEARING_DEGREES: Final = 315.0
DEGREES_PER_TURN: Final = 360.0

#: The four coordinates `precipitation-crosscheck-request.txt` asked for, in request order.
PRECIPITATION_COORDINATES: Final = ((5.4, 100.3), (22.4, 90.4), (-3.1, -60.0), (9.0, -79.0))


def _parsed(
    payload: bytes,
    coordinates: Sequence[tuple[float, float]],
    variables: Sequence[str] = UPSTREAM_VARIABLES,
) -> WeatherForecastRun:
    """Parse one captured response exactly as a live fetch would have."""
    return parse_forecast_run(
        payload,
        coordinates=coordinates,
        variables=variables,
        model=OPEN_METEO_FORECAST_RUN_MODEL,
        model_init_time=RUN_INIT,
        fetched_at=FETCHED_AT,
        request_url=forecast_run_url(coordinates, variables, RUN_INIT, 2),
    )


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    """Build a client whose whole transport is a handler, so no test can reach the network."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_the_fetcher_requests_exactly_the_schemas_own_variable_catalogue() -> None:
    """Style finding S6: one catalogue, not two tables with a comment asking them to stay equal."""
    assert open_meteo.UPSTREAM_VARIABLES is UPSTREAM_VARIABLES
    assert set(UPSTREAM_VARIABLES) <= set(VARIABLES)
    assert all(VARIABLES[name].requested_from_provider for name in UPSTREAM_VARIABLES)
    assert tuple(sorted(UPSTREAM_VARIABLES)) == UPSTREAM_VARIABLES


def test_the_built_url_is_the_recorded_probe_request() -> None:
    recorded = (FIXTURES / "multi-location-request.txt").read_text(encoding="utf-8").strip()

    assert forecast_run_url(MULTI_LOCATION_COORDINATES, UPSTREAM_VARIABLES, RUN_INIT, 2) == recorded


def test_a_multi_location_response_pairs_by_snapped_coordinate() -> None:
    """The array is in request order, and the pairing is PROVEN by the coordinates that came back."""
    run = _parsed(MULTI_LOCATION_PAYLOAD, MULTI_LOCATION_COORDINATES)

    assert len(run.locations) == len(MULTI_LOCATION_COORDINATES)
    for location, (latitude, longitude) in zip(run.locations, MULTI_LOCATION_COORDINATES, strict=True):
        assert abs(location.latitude - latitude) < MAX_SNAP_DEGREES
        assert abs(location.longitude - longitude) < MAX_SNAP_DEGREES


def test_a_response_paired_against_the_wrong_request_order_is_refused() -> None:
    """Reversing the request makes every entry closer to the OTHER coordinate; that is the fault."""
    with pytest.raises(SourcePayloadError):
        _parsed(MULTI_LOCATION_PAYLOAD, tuple(reversed(MULTI_LOCATION_COORDINATES)))


def test_the_hourly_precipitation_timestamp_labels_the_start_of_its_accumulation_hour() -> None:
    """The provider's own daily sum is the proof: hours 00:00..23:00 sum to that day's total."""
    document = json.loads(PRECIPITATION_PAYLOAD)
    daily = document[0]["daily"]
    expected = daily["precipitation_sum"][daily["time"].index("2026-09-19")]
    run = _parsed(PRECIPITATION_PAYLOAD, PRECIPITATION_COORDINATES, ("precipitation",))

    samples = [
        sample
        for sample in run.locations[0].samples
        if sample.variable == "precipitation" and sample.valid_time.date() == datetime(2026, 9, 19, tzinfo=UTC).date()
    ]
    assert len(samples) == HOURS_PER_DAY
    assert round(sum(sample.value for sample in samples if sample.value is not None), 6) == pytest.approx(expected)
    for sample in samples:
        if sample.value is not None:
            assert sample.interval_start == sample.valid_time
            assert sample.interval_end == sample.valid_time + timedelta(hours=1)


def test_no_sample_is_valid_before_the_run_that_produced_it() -> None:
    """Style finding S7: the deleted one-hour shift emitted a window preceding `model_init_time`."""
    run = _parsed(MULTI_LOCATION_PAYLOAD, MULTI_LOCATION_COORDINATES)

    for location in run.locations:
        for sample in location.samples:
            assert sample.valid_time >= RUN_INIT
            if sample.interval_start is not None:
                assert sample.interval_start >= RUN_INIT


def test_calm_publishes_a_real_zero_speed_and_an_absent_bearing() -> None:
    """A SYNTHETIC one-hour payload: the probe captured no calm hour, and calm is a named rule."""
    calm = json.dumps(
        {
            "latitude": 46.215424,
            "longitude": -120.234375,
            "hourly": {
                "time": ["2026-09-18T00:00"],
                "wind_speed_10m": [0.0],
                "wind_direction_10m": [180.0],
            },
        }
    ).encode("utf-8")

    run = _parsed(calm, ((46.25, -120.25),), ("wind_direction_10m", "wind_speed_10m"))

    readings = {sample.variable: sample for sample in run.locations[0].samples}
    assert readings["wind_speed_10m"].value == CALM_METRES_PER_SECOND
    assert readings["wind_speed_10m"].missing_reason is None
    for absent in ("wind_direction_10m", "wind_u_10m", "wind_v_10m"):
        assert readings[absent].value is None
        assert readings[absent].missing_reason == "not_generated"


def test_the_wind_components_round_trip_the_single_point_reading() -> None:
    east, north = wind_components(SPEED_METRES_PER_SECOND, BEARING_DEGREES)

    assert math.hypot(east, north) == pytest.approx(SPEED_METRES_PER_SECOND)
    assert math.degrees(math.atan2(-east, -north)) % DEGREES_PER_TURN == pytest.approx(BEARING_DEGREES)


def test_the_receipt_records_a_credential_free_url_and_the_response_digest() -> None:
    run = _parsed(MULTI_LOCATION_PAYLOAD, MULTI_LOCATION_COORDINATES)

    receipt = run.receipt
    assert receipt.response_sha256 == hashlib.sha256(MULTI_LOCATION_PAYLOAD).hexdigest()
    assert receipt.response_bytes == len(MULTI_LOCATION_PAYLOAD)
    assert receipt.location_count == len(MULTI_LOCATION_COORDINATES)
    assert "apikey" not in receipt.request_url


def test_a_credential_query_parameter_never_reaches_a_receipt() -> None:
    redacted = redact_credentials("https://example.test/v1/forecast?latitude=1&apikey=secret&token=other")

    assert "secret" not in redacted
    assert "other" not in redacted
    assert "latitude=1" in redacted


@pytest.mark.parametrize(
    ("coordinates", "variables", "forecast_days"),
    [
        ((), UPSTREAM_VARIABLES, 2),
        (tuple((0.0, 0.0) for _ in range(MAX_FORECAST_RUN_LOCATIONS + 1)), UPSTREAM_VARIABLES, 2),
        (((0.0, 0.0),), tuple(reversed(UPSTREAM_VARIABLES)), 2),
        (((0.0, 0.0),), ("sea_surface_temperature",), 2),
        (((0.0, 0.0),), UPSTREAM_VARIABLES, 0),
        (((0.0, 0.0),), UPSTREAM_VARIABLES, MAX_FORECAST_RUN_DAYS + 1),
        (((91.0, 0.0),), UPSTREAM_VARIABLES, 2),
    ],
)
def test_an_over_budget_request_is_refused_rather_than_truncated(
    coordinates: Sequence[tuple[float, float]],
    variables: Sequence[str],
    forecast_days: int,
) -> None:
    with pytest.raises(SourceRequestError):
        forecast_run_url(coordinates, variables, RUN_INIT, forecast_days)


async def test_a_rate_limited_run_raises_once_with_its_classified_quota_scope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(429, json={"reason": "Daily API request limit exceeded"})

    async with _client(handler) as client:
        with pytest.raises(OpenMeteoRateLimitError) as refusal:
            await fetch_forecast_run_bytes(client, "https://single-runs-api.open-meteo.test/v1/forecast?latitude=0")

    assert refusal.value.scope == "day"


async def test_a_response_past_the_byte_ceiling_is_refused_mid_read() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"x" * 4_096, headers={"content-type": "application/json"})

    async with _client(handler) as client:
        with pytest.raises(SourceTransportError):
            await fetch_forecast_run_bytes(
                client,
                "https://single-runs-api.open-meteo.test/v1/forecast?latitude=0",
                SourceBounds(max_bytes=1_024, timeout_seconds=1.0),
            )


async def test_a_replayed_response_is_returned_byte_for_byte() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200, content=MULTI_LOCATION_PAYLOAD, headers={"content-type": "application/json; charset=utf-8"}
        )

    async with _client(handler) as client:
        payload = await fetch_forecast_run_bytes(
            client, "https://single-runs-api.open-meteo.test/v1/forecast?latitude=0", WEATHER_FORECAST_RUN_BOUNDS
        )

    assert payload == MULTI_LOCATION_PAYLOAD


async def test_a_non_json_answer_is_refused_as_a_payload_fault() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"<html></html>", headers={"content-type": "text/html"})

    async with _client(handler) as client:
        with pytest.raises(SourcePayloadError):
            await fetch_forecast_run_bytes(client, "https://single-runs-api.open-meteo.test/v1/forecast?latitude=0")
