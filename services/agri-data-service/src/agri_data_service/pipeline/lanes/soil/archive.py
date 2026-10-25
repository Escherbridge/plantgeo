"""The Open-Meteo ERA5-Land archive wire: governed request parameters, the canonical body, and its parse.

Restated from `pipeline/direct/soil/source.py` and `execution/open_meteo_lane.py`, neither of which a
strategy may import; each guard names its legacy sibling. Rationale: `soil/AGENTS.md` "The archive wire".
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from urllib.parse import parse_qsl, urlencode, urlsplit

from agri_data_service.foundation.canonical import canonical_json
from agri_data_service.ingest.open_meteo import (
    OPEN_METEO_ARCHIVE_BASE_URL,
    OPEN_METEO_ERA5_LAND_MODEL,
    archive_daily_url,
)
from agri_data_service.pipeline.lanes._shared.digest import sha256_hex
from agri_data_service.warehouse.field_products.soil import SOIL_SOURCE_PARAMETERS

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

#: The provider endpoint every soil request names (`lanes/_providers/open-meteo.toml [endpoints.archive]`).
ARCHIVE_ENDPOINT: Final = "archive"

#: ERA5-Land's native lattice (`execution/weather_observations/era5_land.py` OPEN_METEO_ARCHIVE_NATIVE_GRID_DEGREES).
NATIVE_GRID_DEGREES: Final = 0.1

#: Half a native box plus a float epsilon (`execution/open_meteo_lane.py::max_grid_offset_degrees`).
MAX_GRID_OFFSET_DEGREES: Final = NATIVE_GRID_DEGREES / 2 + 1e-6

#: The provider's one nondeterministic body field (`open_meteo_lane.NONDETERMINISTIC_RESPONSE_FIELDS`).
NONDETERMINISTIC_FIELDS: Final = frozenset({"generationtime_ms"})

#: Reviewed acceptance range per variable (`era5_land.py` OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS min/max).
VALUE_RANGES: Final[Mapping[str, tuple[float, float]]] = {
    "soil_moisture_0_to_7cm_mean": (0.0, 1.0),
    "soil_moisture_7_to_28cm_mean": (0.0, 1.0),
    "soil_moisture_28_to_100cm_mean": (0.0, 1.0),
    "soil_temperature_0_to_7cm_mean": (-100.0, 70.0),
    "soil_temperature_7_to_28cm_mean": (-100.0, 70.0),
    "soil_temperature_28_to_100cm_mean": (-100.0, 70.0),
    "soil_temperature_100_to_255cm_mean": (-100.0, 70.0),
    "vapour_pressure_deficit_max": (0.0, 15.0),
}

_ISO_DATE_LENGTH: Final = 10
_GMT: Final = "GMT"


class SoilArchiveBodyError(ValueError):
    """An archive body that cannot be bound to the cells and days it was asked for: the unit's `strategy_error`."""


Point = tuple[float, float]
"""A requested (latitude, longitude) centroid, as the request's own parameters spell it."""


@dataclass(frozen=True, slots=True)
class ArchiveAnswer:
    """One parsed archive body: per requested point, per variable, one value (or `None`) per asked day."""

    #: The legacy-shaped credential-free URL rows record as their part key (`soil/AGENTS.md` "Part keys").
    part_key: str
    #: SHA-256 of the canonical document; the legacy `response_sha256`.
    response_sha256: str
    points: tuple[Point, ...]
    days: tuple[date, ...]
    #: `values[parameter][point_index][day_index]`.
    values: Mapping[str, tuple[tuple[float | None, ...], ...]]

    def day_values(self, parameter: str, day: date) -> tuple[float | None, ...]:
        """One variable's value at every point on one day, in request order."""
        index = self.days.index(day)
        return tuple(series[index] for series in self.values[parameter])


def archive_parameters(points: Sequence[Point], first: date, last: date) -> tuple[tuple[str, str], ...]:
    """The governed query, in `ingest/open_meteo.py::_archive_daily_parameters` order, from its one public builder."""
    governed = archive_daily_url(
        points,
        SOIL_SOURCE_PARAMETERS,
        first,
        last,
        model=OPEN_METEO_ERA5_LAND_MODEL,
        # Passed so the builder never reads OPEN_METEO_API_KEY; only the query is kept.
        base_url=OPEN_METEO_ARCHIVE_BASE_URL,
    )
    return tuple(parse_qsl(urlsplit(governed).query, keep_blank_values=True))


def requested_points(parameters: Sequence[tuple[str, str]]) -> tuple[Point, ...]:
    """Read back the centroids a request asked for, in order, from its own `latitude`/`longitude` parameters."""
    query = dict(parameters)
    latitudes = [float(text) for text in query["latitude"].split(",")]
    longitudes = [float(text) for text in query["longitude"].split(",")]
    if len(latitudes) != len(longitudes):
        raise SoilArchiveBodyError("a request's latitude and longitude lists differ in length")
    return tuple(zip(latitudes, longitudes, strict=True))


def part_key(request_url: str, parameters: Sequence[tuple[str, str]]) -> str:
    """The answered host and path with the governed query in governed order: the legacy `soil_chunk_url` bytes."""
    split = urlsplit(request_url)
    return f"{split.scheme}://{split.netloc}{split.path}?{urlencode(list(parameters))}"


def canonical_document(body: bytes) -> bytes:
    """Strip the nondeterministic field and render canonical JSON (`open_meteo_lane.canonical_location_document`).

    `foundation/canonical.canonical_json` (ensure_ascii False), not `_shared.digest`: the archive's
    `daily_units` carry non-ASCII units, and the legacy digest was taken over raw UTF-8.
    """
    try:
        parsed = json.loads(body)
    except ValueError as error:
        raise SoilArchiveBodyError(f"the archive body is not JSON: {error}") from error
    if not isinstance(parsed, list):
        raise SoilArchiveBodyError("a multi-location archive body must be a JSON array")
    stripped: list[object] = []
    for location in parsed:
        if not isinstance(location, dict):
            raise SoilArchiveBodyError("the archive body must hold only location objects")
        stripped.append({key: value for key, value in location.items() if key not in NONDETERMINISTIC_FIELDS})
    return canonical_json(stripped).encode("utf-8")


def parse_answer(document: bytes, *, points: Sequence[Point], days: Sequence[date], key: str) -> ArchiveAnswer:
    """Bind a canonical document to the points and days asked, refusing any attribution it cannot prove."""
    locations = _ordered_locations(document, len(points))
    values: dict[str, list[tuple[float | None, ...]]] = {parameter: [] for parameter in SOIL_SOURCE_PARAMETERS}
    seen_native: set[Point] = set()
    for point, location in zip(points, locations, strict=True):
        native = _validated_grid_point(point, location)
        if native in seen_native:
            raise SoilArchiveBodyError(f"one native grid point {native} answered two requested cells")
        seen_native.add(native)
        daily = _daily_block(location, point=point)
        _require_named_days(daily, point=point, days=days)
        for parameter in SOIL_SOURCE_PARAMETERS:
            values[parameter].append(_bounded_series(daily, parameter, expected=len(days), point=point))
    return ArchiveAnswer(
        part_key=key,
        response_sha256=sha256_hex(document),
        points=tuple(points),
        days=tuple(days),
        values={parameter: tuple(series) for parameter, series in values.items()},
    )


def _ordered_locations(document: bytes, expected: int) -> list[dict[str, object]]:
    """One entry per requested point, in request order (`open_meteo_lane.ordered_locations`)."""
    parsed = json.loads(document)
    if not isinstance(parsed, list) or len(parsed) != expected:
        raise SoilArchiveBodyError(f"the archive body does not carry one entry per requested cell ({expected})")
    for index, location in enumerate(parsed):
        location_id = location.get("location_id")
        # The provider omits `location_id` on the first entry and numbers the rest from 1.
        if index and (isinstance(location_id, bool) or location_id != index):
            raise SoilArchiveBodyError("the archive body's locations are not in the requested order")
    return parsed


def _number(location: Mapping[str, object], name: str) -> float:
    value = location.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise SoilArchiveBodyError(f"an archive location is missing a numeric {name}")
    return float(value)


def _validated_grid_point(point: Point, location: Mapping[str, object]) -> Point:
    """The native node, within half a box of the asked centroid, on GMT (`open_meteo_lane.validated_grid_point`)."""
    latitude, longitude = _number(location, "latitude"), _number(location, "longitude")
    if abs(latitude - point[0]) > MAX_GRID_OFFSET_DEGREES or abs(longitude - point[1]) > MAX_GRID_OFFSET_DEGREES:
        raise SoilArchiveBodyError(f"the archive answered ({latitude}, {longitude}) for requested cell {point}")
    if location.get("timezone") != _GMT or location.get("utc_offset_seconds") != 0:
        raise SoilArchiveBodyError(f"the archive answer for {point} is not on the reviewed GMT time base")
    return (latitude, longitude)


def _daily_block(location: Mapping[str, object], *, point: Point) -> Mapping[str, object]:
    """The `daily` block carrying every requested variable (`direct/soil/source.py::_daily_block`)."""
    daily = location.get("daily")
    if not isinstance(daily, dict):
        raise SoilArchiveBodyError(f"the archive answer for {point} has no daily block")
    missing = sorted(set(SOIL_SOURCE_PARAMETERS).difference(daily))
    if missing:
        raise SoilArchiveBodyError(f"the archive answer for {point} omits {', '.join(missing)}")
    return daily


def _require_named_days(daily: Mapping[str, object], *, point: Point, days: Sequence[date]) -> None:
    """The daily axis is exactly `days`, by the publisher's ISO prefix (`direct/soil/source.py::_require_named_day`)."""
    named = daily.get("time")
    if not isinstance(named, list) or len(named) != len(days):
        raise SoilArchiveBodyError(f"the archive answer for {point} does not carry exactly {len(days)} day(s)")
    for expected, text in zip(days, named, strict=True):
        if not isinstance(text, str) or text[:_ISO_DATE_LENGTH] != expected.isoformat():
            raise SoilArchiveBodyError(f"the archive answered day {text!r} for {point} when {expected} was asked")


def _bounded_series(
    daily: Mapping[str, object], parameter: str, *, expected: int, point: Point
) -> tuple[float | None, ...]:
    """One variable's series; out-of-range refuses the body (`open_meteo_lane.bounded_numeric_series`)."""
    raw = daily.get(parameter)
    if not isinstance(raw, list) or len(raw) != expected:
        raise SoilArchiveBodyError(f"{parameter} at {point} does not align with its time axis")
    minimum, maximum = VALUE_RANGES[parameter]
    series: list[float | None] = []
    for value in raw:
        if value is None:
            series.append(None)
            continue
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise SoilArchiveBodyError(f"{parameter} at {point} carries a non-numeric value")
        if not minimum <= float(value) <= maximum:
            raise SoilArchiveBodyError(f"{parameter} at {point} is outside its reviewed range [{minimum}, {maximum}]")
        series.append(float(value))
    return tuple(series)


__all__ = [
    "ARCHIVE_ENDPOINT",
    "MAX_GRID_OFFSET_DEGREES",
    "NATIVE_GRID_DEGREES",
    "VALUE_RANGES",
    "ArchiveAnswer",
    "Point",
    "SoilArchiveBodyError",
    "archive_parameters",
    "canonical_document",
    "parse_answer",
    "part_key",
    "requested_points",
]
