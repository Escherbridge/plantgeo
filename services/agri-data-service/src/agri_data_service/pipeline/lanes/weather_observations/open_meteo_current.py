"""`weather-observations-direct-forward`: Open-Meteo CURRENT-CONDITIONS grid, bucketed into named days.

Strategy key `weather_observations.open_meteo_current` (spec S14). Imports only `ingest`, `warehouse`,
`pipeline/lanes/_shared` and `pipeline/runner/contract.py`. Why each rule holds:
`pipeline/lanes/weather_observations/AGENTS.md`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.ingest.identity import FeatureIdentity, MissingNativeKeyError, build_weather_observation_identity
from agri_data_service.ingest.open_meteo import CURRENT_FIELDS, OPEN_METEO_PROPERTY_SOURCE, bounded_sample_points, parse_current_weather
from agri_data_service.ingest.policy import (
    MAX_WEATHER_SAMPLE_POINTS,
    format_javascript_number,
    resolve_weather_sample_spacing_degrees,
)
from agri_data_service.pipeline.lanes._shared.digest import COMPACT_SEPARATORS, canonical_json_text, sha256_hex, sha256_hex_of_lines
from agri_data_service.pipeline.runner.contract import SourceRequest, SourceResponse, Unsettled, Written
from agri_data_service.warehouse.schemas.weather_observations import (
    WEATHER_OBSERVATIONS_GRAIN,
    WEATHER_OBSERVATIONS_SCHEMA,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import Region, RegionEnvelope
    from agri_data_service.pipeline.runner.contract import DayContext, ProviderClient, Settlement

#: The "forecast" endpoint `lanes/_providers/open-meteo.toml` declares -- the same host pair legacy
#: `pipeline/direct/weather_observations/source.py` resolves current-conditions through
#: (`ingest/provider_client.py::FORECAST_ENDPOINT`), never the "archive" endpoint.
ENDPOINT: Final = "forecast"

#: `SourceRequest.requested_parameters` / `SourceResponse.complete_parameters`: the one parameter
#: whose presence settles a point's reading. `parse_current_weather` either returns every
#: `CURRENT_VALUE_BOUNDS` field or raises, so there is no partially-valued response to track per field.
CURRENT_PARAMETER: Final = "current"

_OBSERVED_DAY_PREFIX_LENGTH: Final = 10


class WeatherObservationsRowError(ValueError):
    """Raised when an already-parsed reading cannot be bucketed into a named day; never caught locally.

    `parse_current_weather` (called from `fetch`) already proves every `CURRENT_VALUE_BOUNDS` field
    and the reading's freshness, so a response that reaches `settle`/`rows` is well-formed on its
    face. This error exists for the one thing that validation does not re-derive: whether
    `observedAt`'s leading ten characters are a canonical `YYYY-MM-DD` -- the day-bucketing rule
    `geo.feature_observation_day` and legacy `pipeline/direct/weather_observations/rows.py::observation_day`
    both apply, reproduced here (never imported; `pipeline/lanes/AGENTS.md` bans `pipeline.direct`).
    Raising here -- rather than dropping the one reading and writing the rest -- is this lane's
    `REFUSE_WHOLE_RELEASE` policy (`pipeline/direct/weather_observations/forward.py::WRITER_CONTRACT`
    `policy_basis`): "a dropped point silently shrinks the grid a day was written against". A point
    whose FETCH fails (network, stale, out-of-range value) simply produces no `SourceResponse` and
    makes the day partial instead (`write_and_recheck`'s ordinary case) -- this error is strictly for
    a reading that answered but cannot be placed on any day at all.
    """


@dataclass(frozen=True, slots=True)
class PointReading:
    """One sample point's validated current-conditions reading, its built identity, and when it was fetched."""

    latitude: float
    longitude: float
    observation: Mapping[str, object]
    identity: FeatureIdentity
    retrieved_at: datetime


def _bbox(envelope: RegionEnvelope) -> str:
    """The region's camera envelope as Open-Meteo's `west,south,east,north`; mirrors `fire_perimeters/wfigs.py::_bbox`."""
    return ",".join(
        format_javascript_number(value) for value in (envelope.west, envelope.south, envelope.east, envelope.north)
    )


def sample_points(region: Region) -> tuple[tuple[float, float], ...]:
    """The bounded sample grid for one turn: `region.default_camera_envelope` at the configured spacing.

    Reproduces `pipeline/direct/weather_observations/support.py::weather_sample_points` without
    importing it (S14 bans `pipeline.direct`): the grid comes from the REGION's camera envelope, not
    the legacy `INGEST_BBOX` environment variable -- a config lane has no CLI flag to override it
    with, and the region manifest is this track's one source of a lane's spatial footprint
    (`pipeline/lanes/AGENTS.md`). DEVIATION, recorded here rather than silently: a region whose
    camera envelope differs from whatever `INGEST_BBOX` is set to in production would sample a
    DIFFERENT grid than the legacy writer's; reconcile before any G6 cut-over. The spacing is still
    read from `WEATHER_SAMPLE_SPACING_DEGREES` at call time, matching the legacy default.
    """
    return tuple(
        bounded_sample_points(
            _bbox(region.default_camera_envelope), resolve_weather_sample_spacing_degrees(), MAX_WEATHER_SAMPLE_POINTS
        )
    )


def _point_unit(latitude: float, longitude: float) -> str:
    """The per-point source-unit identity: stable within a turn, as `SourceRequest.unit` requires."""
    return f"point:{format_javascript_number(latitude)}:{format_javascript_number(longitude)}"


def _current_weather_parameters(latitude: float, longitude: float) -> tuple[tuple[str, str], ...]:
    """Same query shape as legacy `_current_weather_parameters`/`current_weather_url`, key order included."""
    return (
        ("latitude", format_javascript_number(latitude)),
        ("longitude", format_javascript_number(longitude)),
        ("current", CURRENT_FIELDS),
        ("wind_speed_unit", "ms"),
        ("timeformat", "unixtime"),
        ("timezone", "GMT"),
    )


def _support_digest(region: Region) -> str:
    """Binds a checkpoint to the grid it was planned over: the bbox and the spacing that built it."""
    bbox = _bbox(region.default_camera_envelope)
    spacing = resolve_weather_sample_spacing_degrees()
    return sha256_hex(f"{bbox}@{spacing}".encode())


def _observation_day(observed_at_text: str) -> date:
    """`observedAt`'s first ten characters as a day. See `WeatherObservationsRowError` for why this re-checks."""
    if len(observed_at_text) < _OBSERVED_DAY_PREFIX_LENGTH:
        raise WeatherObservationsRowError(f"observedAt {observed_at_text!r} is too short to name a day")
    named = observed_at_text[:_OBSERVED_DAY_PREFIX_LENGTH]
    try:
        parsed = date.fromisoformat(named)
    except ValueError as error:
        raise WeatherObservationsRowError(f"observedAt day is not YYYY-MM-DD: {named!r}") from error
    if parsed.isoformat() != named:
        raise WeatherObservationsRowError(f"observedAt day is not canonical YYYY-MM-DD: {named!r}")
    return parsed


def _readings(day: date, responses: Sequence[SourceResponse]) -> dict[str, PointReading]:
    """Every point's reading that lands on `day`, keyed by its request unit; raises on an unbucketable one.

    One entry per SUCCESSFUL response -- a point whose fetch failed simply has none here, which is
    the runner's own retry ladder's decision (`pipeline/runner/AGENTS.md` "One retry ladder"), never
    this function's. A response's `request.days` covers the whole turn window (`plan_requests`), so a
    call for one day sees every OTHER day's readings too; those are skipped, not dropped -- `day`'s
    own answer just has nothing to say about them. Every response is still format-checked up front,
    regardless of which day it names: a malformed reading fails the WHOLE call, matching legacy's
    `direct_weather_observation_tables`, which built every named day's table from one poll in a
    single pass and refused the pass whole rather than per bucket.
    """
    readings: dict[str, PointReading] = {}
    for response in responses:
        point = response.payload
        if not isinstance(point, PointReading):
            continue
        observed_at_text = point.observation.get("observedAt")
        if not isinstance(observed_at_text, str):
            raise WeatherObservationsRowError(f"point {response.request.unit} has no observedAt")
        if _observation_day(observed_at_text) != day:
            continue
        readings[response.request.unit] = point
    return dict(sorted(readings.items()))


def _reading_digest_facts(point: PointReading) -> list[object]:
    """Everything a written row carries except its retrieval instant, in a fixed order (S11)."""
    observation = point.observation
    return [
        point.latitude,
        point.longitude,
        observation.get("observedAt"),
        observation.get("temperature"),
        observation.get("humidity"),
        observation.get("windSpeed"),
        observation.get("windDirection"),
        observation.get("precipitation"),
    ]


def day_source_digest(points: Iterable[PointReading]) -> str:
    """SHA-256 of the day's reading facts, excluding `ingested_at` (S11); reuses `pipeline/lanes/_shared/digest.py`."""
    ordered = sorted(points, key=lambda point: (point.latitude, point.longitude))
    return sha256_hex_of_lines(
        canonical_json_text(_reading_digest_facts(point), sort_keys=False, separators=COMPACT_SEPARATORS, allow_nan=True)
        for point in ordered
    )


def _row(point: PointReading, *, day: date) -> dict[str, object]:
    observation = point.observation
    external_id = point.identity.producer_local_id
    return {
        "latitude": point.latitude,
        "longitude": point.longitude,
        "observed_at": point.identity.observed_at,
        "observed_day": day,
        "external_id": external_id,
        "temperature_c": observation.get("temperature"),
        "relative_humidity_pct": observation.get("humidity"),
        "wind_speed_ms": observation.get("windSpeed"),
        "wind_direction_deg": observation.get("windDirection"),
        "precipitation_mm": observation.get("precipitation"),
        "source": OPEN_METEO_PROPERTY_SOURCE,
        # `direct:`-namespaced: no `geo.features` row backs a config-lane write either. See
        # `pipeline/direct/weather_observations/rows.py::_feature_id`'s docstring for why this is
        # NOT refreshed on a repeat grain match in the legacy writer; this strategy writes each row
        # once per `rows()` call and never merges in place, so that concern does not apply here.
        "feature_id": f"direct:{external_id}",
        "ingested_at": point.retrieved_at,
    }


@dataclass(frozen=True, slots=True)
class OpenMeteoCurrentConditionsStrategy:
    """One unit per bounded sample point, each covering the whole asked window; written from whoever answers."""

    def plan_requests(self, days: Sequence[date], lane: LaneConfig, region: Region) -> list[SourceRequest]:  # noqa: ARG002 - the Protocol's lane
        """One unit per sample point; `days` is the whole window, since a poll cannot be asked for one day.

        The `current` endpoint has no `start_date`/`end_date` -- every unit's response answers
        whichever of the asked days its instant happens to bucket into (`SourceRequest.days` "may be
        wider than the days the runner asked for"), so every unit declares the FULL window rather
        than guessing one day ahead of the poll.
        """
        asked = tuple(sorted(set(days)))
        digest = _support_digest(region)
        return [
            SourceRequest(
                unit=_point_unit(latitude, longitude),
                endpoint=ENDPOINT,
                days=asked,
                parameters=_current_weather_parameters(latitude, longitude),
                requested_parameters=frozenset({CURRENT_PARAMETER}),
                support_digest=digest,
            )
            for latitude, longitude in sample_points(region)
        ]

    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse:
        """One send per point; a stale, out-of-range or malformed reading fails this UNIT only (the day stays partial)."""
        response = await client.get(request.endpoint, request.query())
        payload = json.loads(response.body)
        observation = parse_current_weather(payload, response.retrieved_at)
        parameters = dict(request.parameters)
        latitude, longitude = float(parameters["latitude"]), float(parameters["longitude"])
        try:
            identity = build_weather_observation_identity(latitude, longitude, observation)
        except MissingNativeKeyError as error:
            raise WeatherObservationsRowError(f"point {request.unit} could not be identified: {error}") from error
        return SourceResponse(
            request=request,
            body=response.body,
            retrieved_at=response.retrieved_at,
            complete_parameters=request.requested_parameters,
            payload=PointReading(
                latitude=latitude,
                longitude=longitude,
                observation=observation,
                identity=identity,
                retrieved_at=response.retrieved_at,
            ),
        )

    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement:
        """Written from whatever points bucketed to `day`; `write_and_recheck` rewrites it as more answer."""
        present = _readings(day, responses)
        if not present:
            return Unsettled("unsettled", "no sample point has a reading bucketed to this day yet")
        expected = frozenset(request.unit for request in context.planned_units)
        return Written(
            expected_units=len(expected),
            present_units=len(present),
            source_digest=day_source_digest(present.values()),
            expected_unit_ids=expected,
            present_unit_ids=frozenset(present),
        )

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table:
        """One row per point whose reading bucketed to `day`, in the grain's order."""
        present = _readings(day, responses)
        records = [_row(point, day=day) for point in present.values()]
        table = pa.Table.from_pylist(records, schema=WEATHER_OBSERVATIONS_SCHEMA.arrow_schema)
        return table.sort_by([(column, "ascending") for column in WEATHER_OBSERVATIONS_GRAIN])


STRATEGY: Final = OpenMeteoCurrentConditionsStrategy()

__all__ = [
    "CURRENT_PARAMETER",
    "ENDPOINT",
    "STRATEGY",
    "OpenMeteoCurrentConditionsStrategy",
    "PointReading",
    "WeatherObservationsRowError",
    "day_source_digest",
    "sample_points",
]
