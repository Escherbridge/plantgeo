"""NOAA NWS ground-station ingestion: the keyless observation-station puller behind the sensors layer."""

from __future__ import annotations

import asyncio
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final
from urllib.parse import quote, urlencode

import structlog

from agri_data_service.ingest.http import UpstreamBounds, UpstreamPayloadError, fetch_bounded_json, upstream_client
from agri_data_service.ingest.identity import FeatureIdentity, MissingNativeKeyError
from agri_data_service.ingest.layer_binding import LayerBinding
from agri_data_service.ingest.policy import (
    UNCONFIGURED_BBOX_REASON,
    javascript_number,
    parse_bbox,
    resolve_bounded_bbox,
    resolve_max_source_records,
)
from agri_data_service.ingest.results import IngestionJobResult, skipped_result
from agri_data_service.ingest.source import (
    FetchRequest,
    FreshnessRule,
    FunctionSource,
    HistoryCapability,
    select_writes,
)
from agri_data_service.ingest.writer import FeatureWrite

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import httpx

    from agri_data_service.ingest.source import HistoryWindow, UpstreamRecord
    from agri_data_service.ingest.writer import FeatureWriter

logger = structlog.get_logger()

NWS_SENSOR_SOURCE: Final = "nws-sensors"
NWS_API_PRODUCER: Final = "nws-api"
SENSORS_PROPERTY_SOURCE: Final = "NOAA NWS"

SENSORS_LAYER: Final = LayerBinding(
    variable="SENSORS_LAYER_ID",
    default="sensors",
    channel="layer:sensors",
)
SENSORS_CHANNEL: Final = SENSORS_LAYER.channel
SENSORS_LAYER_VARIABLE: Final = SENSORS_LAYER.variable
DEFAULT_SENSORS_LAYER_NAME: Final = SENSORS_LAYER.default

NWS_API_BASE_URL: Final = "https://api.weather.gov"
NWS_STATIONS_URL: Final = f"{NWS_API_BASE_URL}/stations"
NWS_ACCEPT_HEADER: Final = "application/geo+json"

# api.weather.gov issues no key and checks no token. It asks only that a caller identify itself,
# which is why this is a User-Agent and not a credential; set NWS_API_USER_AGENT to add a contact.
NWS_USER_AGENT_VARIABLE: Final = "NWS_API_USER_AGENT"
DEFAULT_NWS_USER_AGENT: Final = "plantgeo-agri-data-service"

STATION_STATES_VARIABLE: Final = "SENSOR_STATION_STATES"
STATION_NETWORKS_VARIABLE: Final = "SENSOR_STATION_NETWORKS"
MAX_STATIONS_VARIABLE: Final = "SENSOR_MAX_STATIONS"

STATION_PAGE_BOUNDS: Final = UpstreamBounds(max_bytes=8 * 1024 * 1024, timeout_seconds=20.0)
OBSERVATION_BOUNDS: Final = UpstreamBounds(max_bytes=1024 * 1024, timeout_seconds=15.0)

# Measured, not assumed: of the states this box touches, only these four place any station inside
# PACIFIC_NORTHWEST_COVERAGE_BBOX; NV/CA/UT rosters sit south of the 42N line. Widen
# SENSOR_STATION_STATES whenever INGEST_BBOX moves -- the bbox filter is authoritative either way,
# and this list only bounds which rosters get drained.
DEFAULT_STATION_STATES: Final[tuple[str, ...]] = ("WA", "OR", "ID", "MT")

# The named, documented networks. The roster also carries roughly 1,350 in-box stations whose
# `provider` is blank; they stay out of the default rather than being ingested unlabelled. Widen
# with SENSOR_STATION_NETWORKS (e.g. "ASOS,RAWS,HADS,MesoWest,APRSWXNET").
DEFAULT_STATION_NETWORKS: Final[tuple[str, ...]] = ("ASOS", "ASOS-HFM", "RAWS", "NonFedAWOS")

DEFAULT_MAX_STATIONS: Final = 750
MIN_MAX_STATIONS: Final = 1
MAX_MAX_STATIONS: Final = 5_000

STATION_PAGE_SIZE: Final = 500
MAX_STATION_PAGES_PER_STATE: Final = 30
STATION_POLL_BATCH_SIZE: Final = 50
MAX_CONCURRENT_STATION_REQUESTS: Final = 8

# api.weather.gov keeps a rolling week of per-station observations. Six days is the depth a live
# bisect actually returned records at, so six days is the only depth this source will promise.
NWS_OBSERVATION_RETENTION: Final = timedelta(days=6)

NO_STATIONS_REASON: Final = "NOAA NWS published no observation stations inside INGEST_BBOX for the configured networks"

MIN_COORDINATE_COUNT: Final = 2

# Every measurement api.weather.gov publishes on an observation. A station that does not report one
# sends it back as an explicit null, which is dropped rather than stored as a zero.
OBSERVATION_MEASUREMENTS: Final[tuple[str, ...]] = (
    "temperature",
    "dewpoint",
    "relativeHumidity",
    "windDirection",
    "windSpeed",
    "windGust",
    "barometricPressure",
    "seaLevelPressure",
    "visibility",
    "precipitationLastHour",
    "precipitationLast3Hours",
    "precipitationLast6Hours",
    "maxTemperatureLast24Hours",
    "minTemperatureLast24Hours",
    "windChill",
    "heatIndex",
)


@dataclass(frozen=True, slots=True)
class SensorStation:
    """One NWS observation station inside the coverage box: its native id, label, network and WGS84 point."""

    station_identifier: str
    name: str | None
    network: str | None
    latitude: float
    longitude: float


def resolve_sensors_layer_name() -> str:
    """Read SENSORS_LAYER_ID at call time, matching the name the push route already resolves."""
    return SENSORS_LAYER.resolve()


def resolve_nws_user_agent() -> str:
    """Read NWS_API_USER_AGENT at call time; NWS asks a caller to identify itself and issues no key."""
    return os.environ.get(NWS_USER_AGENT_VARIABLE, "").strip() or DEFAULT_NWS_USER_AGENT


def nws_request_headers() -> dict[str, str]:
    """The headers every api.weather.gov call carries: the caller identity and the GeoJSON accept."""
    return {"User-Agent": resolve_nws_user_agent(), "Accept": NWS_ACCEPT_HEADER}


def _configured_tokens(variable_name: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    """Read a comma-separated environment list at call time, falling back when it is unset or blank."""
    tokens = tuple(token.strip() for token in os.environ.get(variable_name, "").split(",") if token.strip())
    return tokens or fallback


def resolve_station_states() -> tuple[str, ...]:
    """Read SENSOR_STATION_STATES at call time; these bound which rosters are drained, not which stations pass."""
    return tuple(state.upper() for state in _configured_tokens(STATION_STATES_VARIABLE, DEFAULT_STATION_STATES))


def resolve_station_networks() -> frozenset[str]:
    """Read SENSOR_STATION_NETWORKS at call time, upper-cased so the roster match is case-insensitive."""
    return frozenset(
        network.upper() for network in _configured_tokens(STATION_NETWORKS_VARIABLE, DEFAULT_STATION_NETWORKS)
    )


def resolve_max_stations() -> int:
    """Read SENSOR_MAX_STATIONS at call time, clamped so one poll cycle cannot fan out without bound."""
    raw = os.environ.get(MAX_STATIONS_VARIABLE, "").strip()
    if not raw:
        return DEFAULT_MAX_STATIONS
    parsed = javascript_number(raw)
    if not math.isfinite(parsed):
        return DEFAULT_MAX_STATIONS
    return int(min(MAX_MAX_STATIONS, max(MIN_MAX_STATIONS, parsed)))


def _optional_text(value: object) -> str | None:
    """Return a trimmed non-blank upstream string, or None for an absent, blank or non-string field."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _finite_number(value: object) -> float | None:
    """Return a finite upstream number, rejecting a boolean, a non-number, and the NaN jsonb cannot hold."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _point_coordinates(geometry: object) -> tuple[float, float] | None:
    """Return a GeoJSON point's (latitude, longitude), or None when the shape is not a usable point."""
    if not isinstance(geometry, dict):
        return None
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < MIN_COORDINATE_COUNT:
        return None
    longitude = _finite_number(coordinates[0])
    latitude = _finite_number(coordinates[1])
    if longitude is None or latitude is None:
        return None
    return latitude, longitude


def is_inside_bbox(latitude: float, longitude: float, bbox: str) -> bool:
    """True when a WGS84 point falls inside an already policy-checked `west,south,east,north` box."""
    west, south, east, north = parse_bbox(bbox)
    return west <= longitude <= east and south <= latitude <= north


def _matches_networks(network: str | None, networks: frozenset[str]) -> bool:
    """True when a station's network passes the configured filter; an empty filter accepts every network."""
    if not networks:
        return True
    return network is not None and network.upper() in networks


def _station_from_feature(feature: object, bbox: str, networks: frozenset[str]) -> SensorStation | None:
    """Map one station Feature to a station, dropping it when it is out of box or off the configured networks."""
    if not isinstance(feature, dict):
        return None
    properties = feature.get("properties")
    point = _point_coordinates(feature.get("geometry"))
    if not isinstance(properties, dict) or point is None:
        return None
    station_identifier = _optional_text(properties.get("stationIdentifier"))
    network = _optional_text(properties.get("provider"))
    if station_identifier is None or not _matches_networks(network, networks):
        return None
    latitude, longitude = point
    if not is_inside_bbox(latitude, longitude, bbox):
        return None
    return SensorStation(
        station_identifier=station_identifier,
        name=_optional_text(properties.get("name")),
        network=network,
        latitude=latitude,
        longitude=longitude,
    )


def _next_station_page(payload: Mapping[str, object]) -> str | None:
    """Return the roster's pagination cursor, refusing any URL that would walk off api.weather.gov."""
    pagination = payload.get("pagination")
    if not isinstance(pagination, dict):
        return None
    following = pagination.get("next")
    if not isinstance(following, str) or not following.startswith(f"{NWS_API_BASE_URL}/"):
        return None
    return following


@dataclass(frozen=True, slots=True)
class StationPage:
    """One drained roster page: the stations that passed the filters, its cursor, and the features it held."""

    stations: tuple[SensorStation, ...]
    next_url: str | None
    feature_count: int


def parse_station_page(payload: object, bbox: str, networks: frozenset[str]) -> StationPage:
    """Filter one roster page to the coverage box and the configured networks, and return its cursor.

    `feature_count` is what ends the walk, not the cursor: api.weather.gov keeps handing back a
    `pagination.next` forever, well past the end of a state's roster, so a cursor-driven loop never
    terminates on its own and every run would burn the whole page budget.
    """
    if not isinstance(payload, dict):
        raise UpstreamPayloadError("NOAA NWS returned an invalid station page")
    features = payload.get("features")
    if not isinstance(features, list):
        raise UpstreamPayloadError("NOAA NWS returned an invalid station page")
    stations = tuple(
        station for feature in features if (station := _station_from_feature(feature, bbox, networks)) is not None
    )
    return StationPage(stations=stations, next_url=_next_station_page(payload), feature_count=len(features))


async def fetch_state_stations(
    client: httpx.AsyncClient,
    state: str,
    bbox: str,
    networks: frozenset[str],
) -> list[SensorStation]:
    """Drain one state's bounded roster, keeping only in-box stations on the configured networks."""
    page_url: str | None = f"{NWS_STATIONS_URL}?{urlencode({'state': state, 'limit': STATION_PAGE_SIZE})}"
    headers = nws_request_headers()
    stations: list[SensorStation] = []
    drained = False
    for _page in range(MAX_STATION_PAGES_PER_STATE):
        if page_url is None:
            drained = True
            break
        payload = await fetch_bounded_json(client, page_url, STATION_PAGE_BOUNDS, headers)
        page = parse_station_page(payload, bbox, networks)
        stations.extend(page.stations)
        if page.feature_count == 0:
            drained = True
            break
        page_url = page.next_url
    if not drained:
        logger.warning("nws_station_roster_truncated", state=state, pages=MAX_STATION_PAGES_PER_STATE)
    return stations


async def fetch_station_roster(client: httpx.AsyncClient, bbox: str) -> list[SensorStation]:
    """Drain every configured state's roster concurrently and return one deterministic, capped station list."""
    states = resolve_station_states()
    networks = resolve_station_networks()
    rosters = await asyncio.gather(
        *(fetch_state_stations(client, state, bbox, networks) for state in states),
        return_exceptions=True,
    )

    unavailable_states: list[str] = []
    by_identifier: dict[str, SensorStation] = {}
    for state, roster in zip(states, rosters, strict=True):
        if isinstance(roster, BaseException):
            unavailable_states.append(state)
            continue
        for station in roster:
            by_identifier.setdefault(station.station_identifier, station)
    if unavailable_states:
        logger.warning("nws_station_states_unavailable", unavailable=unavailable_states)

    # Sorted before it is capped, so every run polls the same stations rather than whichever state
    # resolved first. A re-run therefore revisits the same readings and mints no new rows.
    ordered = sorted(by_identifier.values(), key=lambda station: station.station_identifier)
    return ordered[: resolve_max_stations()]


def observation_url(station_identifier: str, window: HistoryWindow | None = None) -> str:
    """Build one station's latest-observation URL, or its bounded history URL when a past window is asked for."""
    station_path = quote(station_identifier, safe="")
    if window is None:
        return f"{NWS_API_BASE_URL}/stations/{station_path}/observations/latest"
    query = urlencode(
        {
            "start": window.start.astimezone(UTC).isoformat(),
            "end": window.end.astimezone(UTC).isoformat(),
        }
    )
    return f"{NWS_API_BASE_URL}/stations/{station_path}/observations?{query}"


def _measurement(value: object) -> dict[str, object] | None:
    """Return one reported measurement with its unit and quality flag, rejecting the null NWS sends for absent."""
    if not isinstance(value, dict):
        return None
    reading = _finite_number(value.get("value"))
    if reading is None:
        return None
    measurement: dict[str, object] = {"value": reading}
    unit_code = _optional_text(value.get("unitCode"))
    if unit_code is not None:
        measurement["unitCode"] = unit_code
    quality_control = _optional_text(value.get("qualityControl"))
    if quality_control is not None:
        measurement["qualityControl"] = quality_control
    return measurement


def observation_readings(properties: Mapping[str, object]) -> dict[str, object]:
    """Collect the measurements a station actually reported; no value is converted, defaulted or invented."""
    readings: dict[str, object] = {}
    for measurement_name in OBSERVATION_MEASUREMENTS:
        measurement = _measurement(properties.get(measurement_name))
        if measurement is not None:
            readings[measurement_name] = measurement
    description = _optional_text(properties.get("textDescription"))
    if description is not None:
        readings["textDescription"] = description
    return readings


def parse_observation(feature: object, station: SensorStation) -> dict[str, object] | None:
    """Map one NWS observation Feature onto the sensor contract, or None when it carries no dated reading.

    The point comes from the roster rather than the observation: the observation's own geometry is
    rounded to two decimals, while the roster carries the station's full-precision location. Both are
    upstream values, so this is a precision choice, never a synthesised coordinate.
    """
    if not isinstance(feature, dict):
        return None
    properties = feature.get("properties")
    if not isinstance(properties, dict):
        return None
    timestamp = _optional_text(properties.get("timestamp"))
    if timestamp is None:
        return None
    return {
        "stationIdentifier": _optional_text(properties.get("stationId")) or station.station_identifier,
        "stationName": _optional_text(properties.get("stationName")) or station.name,
        "network": station.network,
        "timestamp": timestamp,
        "latitude": station.latitude,
        "longitude": station.longitude,
        "readings": observation_readings(properties),
    }


def _required_text(record: Mapping[str, object], field_name: str) -> str:
    """Mirror of identity.py's private reader; delete it when this builder moves into identity.py."""
    value = record.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise MissingNativeKeyError(f"{field_name} is required and must not be blank")
    return value


def _parse_upstream_timestamp(value: str) -> datetime:
    """Parse the upstream instant that dates the reading, refusing one that carries no UTC offset."""
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as error:
        raise ValueError("timestamp must be an ISO-8601 instant") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must carry a UTC offset")
    return parsed.astimezone(UTC)


def build_sensor_reading_identity(record: Mapping[str, object]) -> FeatureIdentity:
    """Build the NWS reading identity: the station's native id and the upstream instant, verbatim.

    `stationIdentifier:timestamp` is byte-identical to the `${sensor_id}:${timestamp}` featureId the
    live push route already mints, so a pulled reading and a pushed one for the same observation
    resolve to one row. Polling a station before it reports again returns the same upstream
    timestamp, so a re-run is idempotent; a genuinely new reading changes it and earns a new row.

    `entity_local_id` is the bare station id, so geo.geometry is keyed per station rather than per
    reading: a ground station never moves, and its one geometry chain is confirmed and versioned as
    the hourly readings arrive. Without it every reading would open a brand-new single-version chain
    -- 591 stations an hour -- and the conformed dimension would be defeated for this producer. This
    is the same split identity.py already applies to USGS gauges (`site_number`) and Open-Meteo
    sample points.
    """
    station_identifier = _required_text(record, "stationIdentifier")
    timestamp = _required_text(record, "timestamp")
    return FeatureIdentity(
        producer=NWS_API_PRODUCER,
        producer_local_id=f"{station_identifier}:{timestamp}",
        observed_at=_parse_upstream_timestamp(timestamp),
        entity_local_id=station_identifier,
    )


def build_sensor_reading_write(record: UpstreamRecord, _request: FetchRequest) -> FeatureWrite | None:
    """Build one reading's write, returning None when the upstream supplied nothing stable to key it by."""
    try:
        identity = build_sensor_reading_identity(record)
    except (MissingNativeKeyError, ValueError):
        return None
    latitude = _finite_number(record.get("latitude"))
    longitude = _finite_number(record.get("longitude"))
    readings = record.get("readings")
    if latitude is None or longitude is None or not isinstance(readings, dict):
        return None

    properties: dict[str, object] = {
        "sensor_id": record["stationIdentifier"],
        "timestamp": record["timestamp"],
        # The same instant under the key the read model actually dates rows by.
        # `environmental-read-model.ts` derives every observation day from
        # COALESCE(observedAt, updatedAt, polygonDateTime); `timestamp` is in none of them, so
        # without this every NWS reading is undatable and the `sensors` layer reports
        # `earliestObservedDate: null` -- "Not yet observed at this date" on every tick of the
        # axis -- while the warehouse holds thousands of properly dated readings. `timestamp` is
        # kept verbatim beside it so the push route at src/app/api/ingest/sensors/route.ts and
        # its consumers stay byte-compatible. Stored unconverted, because OBSERVATION_DAY reads
        # the ISO string's own date part: the day the PUBLISHER named, not a re-zoned one.
        "observedAt": record["timestamp"],
        "readings": readings,
        "source": SENSORS_PROPERTY_SOURCE,
        "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
    }
    station_name = _optional_text(record.get("stationName"))
    if station_name is not None:
        properties["station_name"] = station_name
    network = _optional_text(record.get("network"))
    if network is not None:
        properties["network"] = network

    return FeatureWrite(
        layer_reference=resolve_sensors_layer_name(),
        identity=identity,
        properties=properties,
        channel=SENSORS_CHANNEL,
    )


async def fetch_station_observations(
    client: httpx.AsyncClient,
    station: SensorStation,
    window: HistoryWindow | None = None,
) -> list[dict[str, object]]:
    """Fetch one station's latest reading, or its readings inside a past window, as sensor-contract records."""
    payload = await fetch_bounded_json(
        client,
        observation_url(station.station_identifier, window),
        OBSERVATION_BOUNDS,
        nws_request_headers(),
    )
    features: list[object] = [payload] if window is None else _collection_features(payload)
    records = (parse_observation(feature, station) for feature in features)
    return [record for record in records if record is not None]


def _collection_features(payload: object) -> list[object]:
    """Return a history FeatureCollection's features, rejecting a body that is not one."""
    if not isinstance(payload, dict):
        raise UpstreamPayloadError("NOAA NWS returned an invalid observation collection")
    features = payload.get("features")
    if not isinstance(features, list):
        raise UpstreamPayloadError("NOAA NWS returned an invalid observation collection")
    return features


async def _gather_station_batch(
    client: httpx.AsyncClient,
    stations: Sequence[SensorStation],
    window: HistoryWindow | None,
) -> list[list[dict[str, object]] | BaseException]:
    """Poll one batch under a concurrency ceiling, keeping one station's failure from discarding the rest."""
    concurrency = asyncio.Semaphore(MAX_CONCURRENT_STATION_REQUESTS)

    async def poll(station: SensorStation) -> list[dict[str, object]]:
        async with concurrency:
            return await fetch_station_observations(client, station, window)

    return await asyncio.gather(*(poll(station) for station in stations), return_exceptions=True)


async def collect_sensor_records(
    client: httpx.AsyncClient,
    stations: Sequence[SensorStation],
    window: HistoryWindow | None = None,
    max_records: int | None = None,
) -> list[dict[str, object]]:
    """Poll the roster in bounded batches, stopping at the record ceiling rather than buffering a whole sweep."""
    ceiling = resolve_max_source_records() if max_records is None else max_records
    records: list[dict[str, object]] = []
    unavailable_stations = 0
    polled = 0
    for offset in range(0, len(stations), STATION_POLL_BATCH_SIZE):
        batch = stations[offset : offset + STATION_POLL_BATCH_SIZE]
        polled += len(batch)
        for collection in await _gather_station_batch(client, batch, window):
            if isinstance(collection, BaseException):
                unavailable_stations += 1
                continue
            records.extend(collection)
        if len(records) >= ceiling:
            logger.warning("nws_station_poll_truncated", polled=polled, stations=len(stations), ceiling=ceiling)
            break
    if unavailable_stations:
        logger.info("nws_stations_unavailable", stations=unavailable_stations, polled=polled)
    return records


async def _poll_configured_roster(
    client: httpx.AsyncClient,
    bbox: str,
    window: HistoryWindow | None,
) -> list[dict[str, object]]:
    """Resolve the roster and poll it; an empty roster yields no records rather than an invented one."""
    stations = await fetch_station_roster(client, bbox)
    if not stations:
        return []
    return await collect_sensor_records(client, stations, window)


async def _fetch_sensor_records(request: FetchRequest, window: HistoryWindow | None) -> list[dict[str, object]]:
    """Poll the roster, owning a bounded client only when the caller supplied none."""
    if request.client is not None:
        return await _poll_configured_roster(request.client, request.bbox, window)
    async with upstream_client(OBSERVATION_BOUNDS) as owned_client:
        return await _poll_configured_roster(owned_client, request.bbox, window)


async def fetch_current_sensor_records(request: FetchRequest) -> list[dict[str, object]]:
    """Fetch the latest reading published by every in-box station on the configured networks."""
    return await _fetch_sensor_records(request, None)


async def fetch_sensor_history_records(request: FetchRequest, window: HistoryWindow) -> list[dict[str, object]]:
    """Fetch every in-box station's readings inside one past window of NWS' rolling retention."""
    return await _fetch_sensor_records(request, window)


def nws_sensor_source(now: datetime | None = None) -> FunctionSource:
    """Compose the NWS sensor source, pinning its history floor to the retention measured at composition time.

    The retention window rolls, so the floor is resolved per run rather than frozen into a module
    constant. A backfill reaching past it is refused in typed terms by `HistoryCapability.require`,
    which is what turns "NWS keeps only a week" into an operator-visible skip instead of a silent
    empty window.
    """
    moment = now if now is not None else datetime.now(UTC)
    return FunctionSource(
        source_name=NWS_SENSOR_SOURCE,
        producer=NWS_API_PRODUCER,
        channel=SENSORS_CHANNEL,
        freshness=FreshnessRule(max_observation_age=NWS_OBSERVATION_RETENTION),
        resolve_layer_reference=resolve_sensors_layer_name,
        fetch_current_records=fetch_current_sensor_records,
        build_feature_write=build_sensor_reading_write,
        history=HistoryCapability(supported=True, earliest=moment - NWS_OBSERVATION_RETENTION),
        fetch_history_records=fetch_sensor_history_records,
        shape="feature",
    )


async def _run_sensor_job(
    write_features: FeatureWriter,
    bbox: str,
    client: httpx.AsyncClient,
    now: datetime | None,
) -> IngestionJobResult:
    """Resolve the roster, poll it and write what it published; an empty roster is an honest skip."""
    stations = await fetch_station_roster(client, bbox)
    if not stations:
        return skipped_result(NWS_SENSOR_SOURCE, NO_STATIONS_REASON)

    request = FetchRequest(bbox=bbox, max_records=resolve_max_source_records(), now=now, client=client)
    records = await collect_sensor_records(client, stations, None, request.max_records)
    selection = select_writes(nws_sensor_source(now), records, request)
    return IngestionJobResult(
        source=NWS_SENSOR_SOURCE,
        status="ingested",
        records_seen=len(records),
        records_written=await write_features(selection.writes),
        truncated=selection.truncated,
        details={"stations": len(stations), "rejected": selection.rejected},
    )


async def run_sensor_ingestion_job(
    write_features: FeatureWriter,
    *,
    bbox: str | None = None,
    client: httpx.AsyncClient | None = None,
    now: datetime | None = None,
) -> IngestionJobResult:
    """Poll NOAA NWS ground stations inside the coverage box and write the readings they actually published.

    The sensors layer has always had a push endpoint (`src/app/api/ingest/sensors/route.ts`) and no
    producer, which is the whole reason it holds zero features. This job is that producer: NOAA's
    api.weather.gov aggregates ASOS, RAWS, NonFedAWOS, HADS, MesoWest and APRSWXNET ground stations,
    issues no API key, and was the only candidate that answered with real data on no credential at
    all (OpenAQ v2 is retired, OpenAQ v3 / PurpleAir / Synoptic / AirNow all demand a key).

    Two shapes of honesty are load-bearing here. Every row is dated by `properties.timestamp` from
    the station's own report, never by the poll clock, and the natural key folds that same timestamp
    in, so re-polling an unchanged station rewrites nothing. And when no station answers inside the
    box -- an unset INGEST_BBOX, a network filter that matches nothing, an upstream outage -- the job
    skips with a typed reason and writes zero rows. It never manufactures a reading to fill the map.

    Scale is bounded on purpose: NWS publishes no bulk "all stations latest" endpoint, so the puller
    issues one request per station. The roster is filtered to the coverage box and the named networks,
    sorted, capped by SENSOR_MAX_STATIONS, and polled in batches under a concurrency ceiling.
    """
    area = resolve_bounded_bbox(bbox)
    if area is None:
        return skipped_result(NWS_SENSOR_SOURCE, UNCONFIGURED_BBOX_REASON)
    if client is not None:
        return await _run_sensor_job(write_features, area, client, now)
    async with upstream_client(OBSERVATION_BOUNDS) as owned_client:
        return await _run_sensor_job(write_features, area, owned_client, now)
