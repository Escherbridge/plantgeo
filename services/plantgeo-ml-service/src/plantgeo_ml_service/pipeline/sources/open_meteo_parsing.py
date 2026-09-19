"""Parsing one Open-Meteo Single Runs response into the lane's own shape: pairing, hours, samples.

Layer L3 and PURE: bytes in, dataclasses out, no socket. Split out of `open_meteo.py` when that
module passed the size ceiling; the fetch, the URL grammar and the rate-limit classification stay
there. Two corrections carried from the sibling's deleted ingest live here and are cited inline:
the one-hour precipitation shift is gone (the provider's hourly timestamp labels the START of the
accumulation hour), and multi-location pairing is CHECKED rather than assumed. See `AGENTS.md`.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal
from urllib.parse import urlencode, urlsplit, urlunsplit

from plantgeo_ml_service.pipeline.sources.protocol import (
    GLOBAL_SOURCE_COVERAGE,
    SourceCoverage,
    SourcePayloadError,
    SourceReceipt,
)
from plantgeo_ml_service.warehouse.weather_forecast import (
    NOT_GENERATED,
    VARIABLES,
    value_is_plausible,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


WEATHER_FORECAST_SOURCE_SLUG: Final = "open-meteo"
WEATHER_FORECAST_COVERAGE: Final[SourceCoverage] = GLOBAL_SOURCE_COVERAGE

#: The one model probed on 2026-09-18. A second model is a second reviewed literal, never a
#: caller-supplied string.
OPEN_METEO_FORECAST_RUN_MODEL: Final = "gfs_global"
OpenMeteoForecastRunModel = Literal["gfs_global"]

#: Query parameters stripped from a recorded URL. Open-Meteo's free tier sends none of them; the
#: receipt is credential-free BY CONSTRUCTION rather than by the current plan happening to be free.
CREDENTIAL_QUERY_KEYS: Final[frozenset[str]] = frozenset({"apikey", "api_key", "key", "token"})

_ISO_HOUR_LENGTH: Final = len("2026-09-11T00:00")
_ACCUMULATION_HOURS: Final = 1

#: Every reading this endpoint answers for is a point sample off the model's native grid.
WEATHER_FORECAST_SUPPORT: Final = "sampled_point"

#: The furthest a provider may move a requested point onto its own grid before the answer stops
#: being about the point that was asked for. GFS global is a 0.25-degree grid, so a legitimate snap
#: is at most ~0.177 degrees away; anything beyond this is a pairing fault, not a snap.
MAX_SNAP_DEGREES: Final = 0.5

#: Float slack when deciding which request an answer is closest to. Well under any real grid pitch.
PAIRING_TOLERANCE_DEGREES: Final = 1e-9

DEGREES_PER_TURN: Final = 360.0


def forecast_run_id(model: OpenMeteoForecastRunModel, model_init_time: datetime) -> str:
    """Return `<provider>:<model>:<init_time ISO>`, the identity one published series is pinned by."""
    return f"{WEATHER_FORECAST_SOURCE_SLUG}:{model}:{model_init_time.isoformat()}"


def redact_credentials(url: str) -> str:
    """Return `url` with every credential-bearing query parameter removed, for the source receipt."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [
        (name, value)
        for name, value in (pair.split("=", 1) if "=" in pair else (pair, "") for pair in parts.query.split("&"))
        if name.lower() not in CREDENTIAL_QUERY_KEYS
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept, safe=","), parts.fragment))


@dataclass(frozen=True, slots=True)
class WeatherForecastSample:
    """One variable's reading at one valid instant, at one requested location."""

    valid_time: datetime
    interval_start: datetime | None
    interval_end: datetime | None
    variable: str
    value: float | None
    missing_reason: str | None
    support: str = WEATHER_FORECAST_SUPPORT


@dataclass(frozen=True, slots=True)
class WeatherForecastLocation:
    """One requested point's full sample series, at the coordinates the provider answered for."""

    latitude: float
    longitude: float
    samples: tuple[WeatherForecastSample, ...]


@dataclass(frozen=True, slots=True)
class WeatherForecastRun:
    """One admitted NWP model run, in exactly the shape the lane reads."""

    run_id: str
    model_init_time: datetime
    provider_issue_time: datetime | None
    fetched_at: datetime
    locations: tuple[WeatherForecastLocation, ...]
    receipt: SourceReceipt


def wind_components(speed: float, direction_degrees: float) -> tuple[float, float]:
    """Return earth-relative 10 m u/v from a meteorological (from-true-north) speed/direction pair.

    `u = -speed * sin(direction)`, `v = -speed * cos(direction)`; see `AGENTS.md` for why the signs
    are negative and why this single-point conversion is not the forbidden scalar bearing average.
    """
    radians = math.radians(direction_degrees)
    return -speed * math.sin(radians), -speed * math.cos(radians)


def paired_entry_indexes(
    entries: Sequence[Mapping[str, object]],
    coordinates: Sequence[tuple[float, float]],
) -> tuple[int, ...]:
    """Return, per requested coordinate, the response entry that answers it, or refuse the pairing.

    WHAT ACTUALLY GUARDS THE PAIRING IS `location_id`, and this docstring used to claim otherwise.
    The provider stamps every entry after the first with its own request index, so rule 1 below is
    the identity check and the two geometric rules are CORROBORATION, not the derivation. The
    function therefore returns the identity permutation: there is no reordering to discover, only a
    claim to refuse. A provider that stopped stamping `location_id` would fail rule 1 rather than
    silently fall back to coordinate matching.

    1. Entry `i` must name `location_id == i` (the first entry omits the field, as the provider
       numbers from 1 in request order). This is the pairing.
    2. Every entry must land within `MAX_SNAP_DEGREES` of the request at its own index. A nearest
       snap on a 0.25-degree grid moves a point at most ~0.177 degrees; anything beyond the ceiling
       is not a snap, whatever the entry claims to answer for.
    3. No pair of entries may be BETTER OFF SWAPPED -- that is, there is no `(i, j)` where entry
       `i`'s answer is strictly closer to request `j` AND entry `j`'s answer is strictly closer to
       request `i`. A reorder always produces such a pair; a one-sided "closer to the neighbour"
       does not, which is the common and legitimate case of two requested cells snapping to ONE
       provider grid point at unequal distances from it. Refusing that would reject a correct
       response from any lattice finer than the provider's grid.

    The swap search is quadratic in the batch, bounded by `MAX_FORECAST_RUN_LOCATIONS` (200), so it
    costs at most 40,000 squared-distance comparisons for one request.
    """
    if len(entries) != len(coordinates):
        raise SourcePayloadError(
            f"the Open-Meteo run response carries {len(entries)} entries for {len(coordinates)} requested locations"
        )
    answers: list[tuple[float, float]] = []
    for index, entry in enumerate(entries):
        location_id = entry.get("location_id")
        # The first entry omits `location_id`; the rest are numbered from 1 in request order.
        if index and location_id != index:
            raise SourcePayloadError(f"the Open-Meteo run response entry at index {index} names location {location_id}")
        answered = _entry_coordinates(entry)
        if _squared_degrees(answered, coordinates[index]) > MAX_SNAP_DEGREES**2:
            raise SourcePayloadError(
                f"the Open-Meteo run response entry at index {index} answered for {answered}, further than "
                f"{MAX_SNAP_DEGREES} degrees from the requested {coordinates[index]}"
            )
        answers.append(answered)
    _refuse_beneficial_swap(answers, coordinates)
    return tuple(range(len(entries)))


def _refuse_beneficial_swap(
    answers: Sequence[tuple[float, float]],
    coordinates: Sequence[tuple[float, float]],
) -> None:
    """Refuse when two entries would BOTH sit closer to each other's request; that is a reorder."""
    own = [_squared_degrees(answer, request) for answer, request in zip(answers, coordinates, strict=True)]
    for left in range(len(answers)):
        for right in range(left + 1, len(answers)):
            left_prefers_right = _squared_degrees(answers[left], coordinates[right]) + PAIRING_TOLERANCE_DEGREES
            right_prefers_left = _squared_degrees(answers[right], coordinates[left]) + PAIRING_TOLERANCE_DEGREES
            if left_prefers_right < own[left] and right_prefers_left < own[right]:
                raise SourcePayloadError(
                    f"the Open-Meteo run response entries at indexes {left} and {right} each answer the other's "
                    "requested location; the array is not in request order"
                )


def parse_forecast_run(  # noqa: PLR0913 - one keyword per fact the caller's bounded fetch resolved
    payload: bytes,
    *,
    coordinates: Sequence[tuple[float, float]],
    variables: Sequence[str],
    model: OpenMeteoForecastRunModel,
    model_init_time: datetime,
    fetched_at: datetime,
    request_url: str,
) -> WeatherForecastRun:
    """Parse one run response into the lane's own shape, with pairing verified by snapped coordinate."""
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise SourcePayloadError("the Open-Meteo run response was not valid UTF-8 JSON") from error
    entries = parsed if isinstance(parsed, list) else [parsed]
    if not all(isinstance(entry, dict) for entry in entries):
        raise SourcePayloadError("the Open-Meteo run response entries must be location objects")
    locations = tuple(
        _parsed_location(entries[index], variables) for index in paired_entry_indexes(entries, coordinates)
    )
    return WeatherForecastRun(
        run_id=forecast_run_id(model, model_init_time),
        model_init_time=model_init_time,
        # Open-Meteo echoes no model version, run identifier or release instant. Always None;
        # `generationtime_ms` is response computation duration and is deliberately not read.
        provider_issue_time=None,
        fetched_at=fetched_at,
        locations=locations,
        receipt=SourceReceipt(
            source_slug=WEATHER_FORECAST_SOURCE_SLUG,
            request_url=redact_credentials(request_url),
            response_sha256=hashlib.sha256(payload).hexdigest(),
            response_bytes=len(payload),
            location_count=len(locations),
        ),
    )


def _entry_coordinates(entry: Mapping[str, object]) -> tuple[float, float]:
    """Read one entry's answered `(latitude, longitude)`, refusing a non-numeric or absent pair."""
    latitude, longitude = entry.get("latitude"), entry.get("longitude")
    numeric = (
        not isinstance(latitude, bool)
        and isinstance(latitude, int | float)
        and not isinstance(longitude, bool)
        and isinstance(longitude, int | float)
    )
    if not numeric:
        raise SourcePayloadError("an Open-Meteo run response entry was missing its latitude or longitude")
    return float(latitude), float(longitude)  # type: ignore[arg-type]  # narrowed by `numeric`


def _squared_degrees(left: tuple[float, float], right: tuple[float, float]) -> float:
    """Return the squared planar degree distance between two coordinate pairs.

    Planar and unweighted on purpose: this decides WHICH REQUEST an answer belongs to at grid-cell
    scale, where a great-circle distance would order the same candidates identically.
    """
    return (left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2


def _parsed_location(entry: Mapping[str, object], variables: Sequence[str]) -> WeatherForecastLocation:
    """Build one location's whole sample series from its response entry."""
    latitude, longitude = _entry_coordinates(entry)
    hourly = entry.get("hourly")
    if not isinstance(hourly, dict):
        raise SourcePayloadError("an Open-Meteo run response entry carried no hourly block")
    raw_hours = hourly.get("time")
    if not isinstance(raw_hours, list) or not raw_hours:
        raise SourcePayloadError("an Open-Meteo run response entry carried no hourly time axis")
    hours = [_parsed_hour(value) for value in raw_hours]
    return WeatherForecastLocation(
        latitude=latitude,
        longitude=longitude,
        samples=_location_samples(hourly, variables, hours),
    )


def _parsed_hour(raw_time: object) -> datetime:
    """Parse one `hourly.time` entry (`YYYY-MM-DDTHH:MM`, UTC by the `timezone=UTC` parameter)."""
    if not isinstance(raw_time, str) or len(raw_time) != _ISO_HOUR_LENGTH:
        raise SourcePayloadError("the Open-Meteo run response carried an unexpected hourly time entry")
    try:
        return datetime.fromisoformat(raw_time).replace(tzinfo=UTC)
    except ValueError as error:
        raise SourcePayloadError("the Open-Meteo run response carried an unparseable hourly time entry") from error


def _bounded_series(hourly: Mapping[str, object], variable: str, expected_length: int) -> list[float | None]:
    """Read one hourly field, validating its physical range and refusing a misaligned series.

    A `None` entry -- the provider's own "not generated this hour" -- survives as `None`; a
    non-numeric, non-null entry is a payload error, never silently dropped.
    """
    raw = hourly.get(variable)
    if not isinstance(raw, list) or len(raw) != expected_length:
        raise SourcePayloadError(f"the Open-Meteo run response's {variable} does not align with its time axis")
    values: list[float | None] = []
    for entry in raw:
        if entry is None:
            values.append(None)
            continue
        if isinstance(entry, bool) or not isinstance(entry, int | float) or not math.isfinite(entry):
            raise SourcePayloadError(f"the Open-Meteo run response's {variable} carried a non-numeric value")
        numeric = float(entry)
        if not value_is_plausible(variable, numeric):
            raise SourcePayloadError(f"the Open-Meteo run response's {variable} carried a value outside its range")
        values.append(numeric)
    return values


def _location_samples(
    hourly: Mapping[str, object],
    variables: Sequence[str],
    hours: Sequence[datetime],
) -> tuple[WeatherForecastSample, ...]:
    """Build every sample row for one location: the requested variables plus the derived wind pair."""
    series = {
        variable: _bounded_series(hourly, variable, len(hours)) for variable in variables if variable in VARIABLES
    }
    instantaneous = tuple(
        variable
        for variable in variables
        if variable in series and variable not in {"precipitation", "wind_speed_10m", "wind_direction_10m"}
    )
    samples: list[WeatherForecastSample] = []
    for index, hour in enumerate(hours):
        samples.extend(_instantaneous_sample(hour, variable, series[variable][index]) for variable in instantaneous)
        if "precipitation" in series:
            samples.append(_precipitation_sample(hour, series["precipitation"][index]))
        if "wind_speed_10m" in series and "wind_direction_10m" in series:
            samples.extend(_wind_samples(hour, series["wind_speed_10m"][index], series["wind_direction_10m"][index]))
    return tuple(samples)


def _instantaneous_sample(
    hour: datetime,
    variable: str,
    value: float | None,
    *,
    missing_reason: str | None = None,
) -> WeatherForecastSample:
    """Build one instantaneous reading, defaulting an absent value to a governed absence."""
    if variable not in VARIABLES:
        raise SourcePayloadError(f"variable {variable!r} is not in the weather-forecast catalogue")
    resolved = (missing_reason or NOT_GENERATED) if value is None else None
    return WeatherForecastSample(
        valid_time=hour,
        interval_start=None,
        interval_end=None,
        variable=variable,
        value=value,
        missing_reason=resolved,
    )


def _precipitation_sample(hour: datetime, millimetres: float | None) -> WeatherForecastSample:
    """Build one precipitation accumulation; the provider's timestamp labels the window's START.

    Proven by the provider's own daily sums (`.omc/research/forecast-s3-probe-20260919/`): hours
    00:00..23:00 of a day sum to that day's `precipitation_sum`. The deleted agri code shifted this
    row an hour earlier and emitted a window preceding the run's own initialization, which made
    `lead_hours` negative for a column typed as the forecast lead (style finding S7). No shift here.
    """
    interval_end = hour + timedelta(hours=_ACCUMULATION_HOURS)
    return WeatherForecastSample(
        valid_time=hour,
        interval_start=None if millimetres is None else hour,
        interval_end=None if millimetres is None else interval_end,
        variable="precipitation",
        value=millimetres,
        missing_reason=None if millimetres is not None else NOT_GENERATED,
    )


def _wind_samples(hour: datetime, speed: float | None, direction: float | None) -> tuple[WeatherForecastSample, ...]:
    """Return four wind rows: the provider's speed and direction, plus the derived u/v components.

    CALM HAS NO DEFINED BEARING. At `speed == 0.0` the direction and both components are a governed
    absence even when the provider supplied a (physically meaningless) bearing; `wind_speed_10m`
    keeps the real zero, because a numeric zero is a value and never a stand-in for absence.
    """
    speed_sample = _instantaneous_sample(hour, "wind_speed_10m", speed)
    if speed is None or direction is None or speed == 0.0:
        return (
            speed_sample,
            _instantaneous_sample(hour, "wind_direction_10m", None, missing_reason=NOT_GENERATED),
            _instantaneous_sample(hour, "wind_u_10m", None, missing_reason=NOT_GENERATED),
            _instantaneous_sample(hour, "wind_v_10m", None, missing_reason=NOT_GENERATED),
        )
    east, north = wind_components(speed, direction)
    return (
        speed_sample,
        _instantaneous_sample(hour, "wind_direction_10m", direction),
        _instantaneous_sample(hour, "wind_u_10m", east),
        _instantaneous_sample(hour, "wind_v_10m", north),
    )


__all__ = [
    "CREDENTIAL_QUERY_KEYS",
    "MAX_SNAP_DEGREES",
    "OPEN_METEO_FORECAST_RUN_MODEL",
    "PAIRING_TOLERANCE_DEGREES",
    "WEATHER_FORECAST_COVERAGE",
    "WEATHER_FORECAST_SOURCE_SLUG",
    "WEATHER_FORECAST_SUPPORT",
    "OpenMeteoForecastRunModel",
    "WeatherForecastLocation",
    "WeatherForecastRun",
    "WeatherForecastSample",
    "forecast_run_id",
    "paired_entry_indexes",
    "parse_forecast_run",
    "redact_credentials",
    "wind_components",
]
