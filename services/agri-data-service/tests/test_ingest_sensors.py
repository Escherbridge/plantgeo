"""NOAA NWS sensor ingestion: identity/route parity, the roster pagination terminator, and honest skips."""

# ruff: noqa: PLR2004

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import pytest

from agri_data_service.ingest.geometry import geometry_key_for
from agri_data_service.ingest.identity import MissingNativeKeyError
from agri_data_service.ingest.policy import PACIFIC_NORTHWEST_COVERAGE_BBOX
from agri_data_service.ingest.sensors import (
    NO_STATIONS_REASON,
    NWS_API_PRODUCER,
    NWS_OBSERVATION_RETENTION,
    NWS_SENSOR_SOURCE,
    SENSORS_CHANNEL,
    SENSORS_PROPERTY_SOURCE,
    SensorStation,
    build_sensor_reading_identity,
    build_sensor_reading_write,
    fetch_state_stations,
    is_inside_bbox,
    nws_sensor_source,
    parse_observation,
    parse_station_page,
    resolve_max_stations,
    run_sensor_ingestion_job,
)
from agri_data_service.ingest.source import FetchRequest

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.ingest.writer import FeatureWrite

RECORDED_STATION_IDENTIFIER = "KBOI"
RECORDED_TIMESTAMP = "2026-08-04T13:00:00+00:00"
STATION = SensorStation(
    station_identifier=RECORDED_STATION_IDENTIFIER,
    name="Boise Air Terminal",
    network="ASOS",
    latitude=43.5651,
    longitude=-116.2229,
)


class RecordingWriter:
    """A feature writer that records what a job handed it, so a job test needs no database."""

    def __init__(self) -> None:
        self.writes: list[FeatureWrite] = []

    async def __call__(self, writes: Sequence[FeatureWrite]) -> int:
        self.writes = list(writes)
        return len(self.writes)


@pytest.fixture(autouse=True)
def _clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in (
        "INGEST_BBOX",
        "INGEST_MAX_SOURCE_RECORDS",
        "SENSORS_LAYER_ID",
        "NWS_API_USER_AGENT",
        "SENSOR_STATION_STATES",
        "SENSOR_STATION_NETWORKS",
        "SENSOR_MAX_STATIONS",
    ):
        monkeypatch.delenv(variable, raising=False)


def _reading(**overrides: object) -> dict[str, object]:
    reading: dict[str, object] = {
        "stationIdentifier": RECORDED_STATION_IDENTIFIER,
        "stationName": STATION.name,
        "network": STATION.network,
        "timestamp": RECORDED_TIMESTAMP,
        "latitude": STATION.latitude,
        "longitude": STATION.longitude,
        "readings": {"temperature": {"value": 24.4, "unitCode": "wmoUnit:degC"}},
    }
    reading.update(overrides)
    return reading


def test_the_reading_identity_is_stable_across_two_runs_over_the_same_payload() -> None:
    reading = _reading()
    first = build_sensor_reading_identity(reading)
    second = build_sensor_reading_identity(reading)
    assert first.natural_key == second.natural_key
    assert first.natural_key == f"{NWS_API_PRODUCER}:{RECORDED_STATION_IDENTIFIER}:{RECORDED_TIMESTAMP}"


def test_observed_at_comes_from_the_upstream_timestamp_never_the_run_clock() -> None:
    # build_sensor_reading_identity takes no "now" parameter, so the only timestamp source is the reading.
    identity = build_sensor_reading_identity(_reading(timestamp="2026-08-04T12:47:00+00:00"))
    assert identity.observed_at == datetime(2026, 8, 4, 12, 47, tzinfo=UTC)


def test_a_reading_missing_its_station_identifier_raises_rather_than_being_synthesised() -> None:
    with pytest.raises(MissingNativeKeyError, match="stationIdentifier"):
        build_sensor_reading_identity(_reading(stationIdentifier=None))
    with pytest.raises(MissingNativeKeyError, match="stationIdentifier"):
        build_sensor_reading_identity(_reading(stationIdentifier="  "))


def test_a_reading_missing_its_timestamp_raises_rather_than_being_synthesised() -> None:
    with pytest.raises(MissingNativeKeyError, match="timestamp"):
        build_sensor_reading_identity(_reading(timestamp=None))


def test_a_timestamp_with_no_utc_offset_is_refused() -> None:
    with pytest.raises(ValueError, match="UTC offset"):
        build_sensor_reading_identity(_reading(timestamp="2026-08-04T12:47:00"))


def test_the_key_matches_the_live_push_route_byte_for_byte() -> None:
    # Proven live: `nws-api:KBOI:2026-08-04T13:00:00+00:00`, identical to the `${sensor_id}:${timestamp}`
    # featureId route.ts already mints, so a pulled and a pushed reading for one observation converge.
    identity = build_sensor_reading_identity(_reading())
    assert identity.natural_key == "nws-api:KBOI:2026-08-04T13:00:00+00:00"


def test_the_geometry_dimension_is_keyed_per_station_not_per_reading() -> None:
    # A ground station never moves: one geo.geometry chain per station, confirmed and versioned as the
    # hourly readings arrive. Without entity_local_id every reading would open its own single-version
    # chain -- 591 stations an hour -- and the conformed dimension would be defeated for this producer.
    first = build_sensor_reading_identity(_reading(timestamp="2026-08-04T13:00:00+00:00"))
    second = build_sensor_reading_identity(_reading(timestamp="2026-08-04T14:00:00+00:00"))
    assert first.entity_key == second.entity_key == f"{NWS_API_PRODUCER}:{RECORDED_STATION_IDENTIFIER}"
    assert first.natural_key != second.natural_key
    assert first.observation_is_its_own_entity is False
    assert geometry_key_for(first) == f"{NWS_API_PRODUCER}:{RECORDED_STATION_IDENTIFIER}"


def test_is_inside_bbox_accepts_only_points_within_the_configured_box() -> None:
    assert is_inside_bbox(43.6, -116.2, PACIFIC_NORTHWEST_COVERAGE_BBOX) is True
    assert is_inside_bbox(20.0, -116.2, PACIFIC_NORTHWEST_COVERAGE_BBOX) is False
    assert is_inside_bbox(43.6, 0.0, PACIFIC_NORTHWEST_COVERAGE_BBOX) is False


def test_a_reading_writes_the_sensor_contract_shape_the_push_route_already_expects() -> None:
    request = FetchRequest(bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX, max_records=10)
    write = build_sensor_reading_write(_reading(), request)
    assert write is not None
    assert write.natural_key == "nws-api:KBOI:2026-08-04T13:00:00+00:00"
    assert write.channel == SENSORS_CHANNEL
    assert write.properties["sensor_id"] == RECORDED_STATION_IDENTIFIER
    assert write.properties["timestamp"] == RECORDED_TIMESTAMP
    assert write.properties["source"] == SENSORS_PROPERTY_SOURCE
    assert write.properties["geometry"] == {"type": "Point", "coordinates": [STATION.longitude, STATION.latitude]}
    assert write.properties["readings"] == {"temperature": {"value": 24.4, "unitCode": "wmoUnit:degC"}}


@pytest.mark.parametrize(
    "overrides",
    [
        {"latitude": None},
        {"longitude": None},
        {"readings": "not-a-mapping"},
        {"stationIdentifier": ""},
    ],
)
def test_a_reading_missing_a_required_field_yields_no_write(overrides: dict[str, object]) -> None:
    request = FetchRequest(bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX, max_records=10)
    assert build_sensor_reading_write(_reading(**overrides), request) is None


def test_an_observation_is_dated_by_its_own_timestamp_and_located_by_the_roster_not_its_own_geometry() -> None:
    # The observation Feature's own geometry is rounded to 2dp; the roster carries full precision.
    # Both are upstream values, so using the roster point is a precision choice, never a synthesised one.
    feature = {
        "properties": {"stationId": RECORDED_STATION_IDENTIFIER, "timestamp": RECORDED_TIMESTAMP},
        "geometry": {"type": "Point", "coordinates": [-116.23, 43.57]},
    }
    record = parse_observation(feature, STATION)
    assert record is not None
    assert record["latitude"] == STATION.latitude
    assert record["longitude"] == STATION.longitude
    assert record["timestamp"] == RECORDED_TIMESTAMP


def test_an_observation_carrying_no_timestamp_is_dropped_rather_than_dated_by_the_poll_clock() -> None:
    feature = {
        "properties": {"stationId": RECORDED_STATION_IDENTIFIER},
        "geometry": {"type": "Point", "coordinates": [0, 0]},
    }
    assert parse_observation(feature, STATION) is None


def test_an_explicit_null_measurement_is_dropped_rather_than_stored_as_zero() -> None:
    feature = {
        "properties": {
            "stationId": RECORDED_STATION_IDENTIFIER,
            "timestamp": RECORDED_TIMESTAMP,
            "temperature": None,
            "windSpeed": {"value": 4.1, "unitCode": "wmoUnit:km_h-1"},
        },
        "geometry": {"type": "Point", "coordinates": [0, 0]},
    }
    record = parse_observation(feature, STATION)
    assert record is not None
    assert "temperature" not in record["readings"]
    assert record["readings"]["windSpeed"] == {"value": 4.1, "unitCode": "wmoUnit:km_h-1"}


def test_resolve_max_stations_defaults_and_clamps_the_configured_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_max_stations() == 750
    monkeypatch.setenv("SENSOR_MAX_STATIONS", "50000")
    assert resolve_max_stations() == 5_000
    monkeypatch.setenv("SENSOR_MAX_STATIONS", "0")
    assert resolve_max_stations() == 1


def test_a_roster_page_terminates_on_an_empty_feature_list_not_on_the_cursor() -> None:
    # api.weather.gov keeps answering pagination.next forever, well past the end of a roster; a
    # cursor-driven walk would never stop. feature_count==0 is what actually ends it.
    payload = {
        "type": "FeatureCollection",
        "features": [],
        "pagination": {"next": "https://api.weather.gov/stations?s=99999"},
    }
    page = parse_station_page(payload, PACIFIC_NORTHWEST_COVERAGE_BBOX, frozenset())
    assert page.feature_count == 0
    assert page.stations == ()


async def test_a_state_roster_drains_in_one_request_even_when_the_upstream_never_stops_offering_a_next_page() -> None:
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(str(request.url))
        body = {
            "type": "FeatureCollection",
            "features": [],
            "pagination": {"next": "https://api.weather.gov/stations?s=99999"},
        }
        return httpx.Response(200, content=json.dumps(body).encode(), headers={"content-type": "application/geo+json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        stations = await fetch_state_stations(client, "ID", PACIFIC_NORTHWEST_COVERAGE_BBOX, frozenset())

    assert stations == []
    assert len(attempts) == 1


def test_the_composed_source_pins_history_to_the_measured_retention_window() -> None:
    now = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    source = nws_sensor_source(now)
    assert source.source_name == NWS_SENSOR_SOURCE
    assert source.producer == NWS_API_PRODUCER
    assert source.freshness.max_observation_age == NWS_OBSERVATION_RETENTION
    assert source.history_capability().supported is True
    assert source.history_capability().earliest == now - NWS_OBSERVATION_RETENTION


async def test_an_unset_bbox_is_skipped_and_never_failed() -> None:
    result = await run_sensor_ingestion_job(RecordingWriter())
    assert result.source == NWS_SENSOR_SOURCE
    assert result.status == "skipped"
    assert result.reason == "INGEST_BBOX is not configured"


async def test_an_empty_roster_is_an_honest_skip_with_no_rows_written() -> None:
    empty_page = {"type": "FeatureCollection", "features": []}
    response = httpx.Response(
        200, content=json.dumps(empty_page).encode(), headers={"content-type": "application/geo+json"}
    )
    writer = RecordingWriter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: response)) as client:
        result = await run_sensor_ingestion_job(writer, bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX, client=client)

    assert result.status == "skipped"
    assert result.reason == NO_STATIONS_REASON
    assert result.records_written == 0
    assert writer.writes == []
