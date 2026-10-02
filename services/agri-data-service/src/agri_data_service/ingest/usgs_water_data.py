"""USGS Water Data OGC API daily values: the tile layout, the two request shapes and their parsers.

The config lane `water-gauges-daily` reads daily mean discharge (parameter 00060, statistic 00003)
from `api.waterdata.usgs.gov/ogcapi/v1/collections/daily/items` and gauge names from the
`monitoring-locations` collection. Every fact here was observed by probe P4
(`docs/lanes/water-gauges.md` "Source probe"); the strategy that drives it is
`pipeline/lanes/water_gauges/usgs_water_data.py`, and the why lives in
`pipeline/lanes/water_gauges/AGENTS.md`.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from agri_data_service.ingest.http import UpstreamPayloadError
from agri_data_service.ingest.policy import format_javascript_number

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agri_data_service.foundation.region.manifest import RegionEnvelope

#: The provider file `lanes/_providers/usgs-water-data.toml` and its two endpoint names.
USGS_WATER_DATA_PROVIDER: Final = "usgs-water-data"
DAILY_VALUES_ENDPOINT: Final = "daily"
MONITORING_LOCATIONS_ENDPOINT: Final = "monitoring-locations"

#: The region manifest's `source_slug` for this source; bound to `water-gauges` at G4, not before.
USGS_WATER_DATA_SOURCE_SLUG: Final = "usgs_water_data"

DISCHARGE_PARAMETER_CODE: Final = "00060"
DAILY_MEAN_STATISTIC_ID: Final = "00003"
#: The primary stream category and its secondary types; see ingest/AGENTS.md.
STREAM_SITE_TYPE_FILTER: Final = "site_type_code = 'ST' OR site_type_code LIKE 'ST-%'"
#: The unit parameter 00060 is defined in; any other unit is a contract break, never converted.
DISCHARGE_UNIT: Final = "ft^3/s"
#: The collection's own `limit` maximum (OpenAPI `limit.maximum`); one unit is sized to fit one page.
MAX_ITEMS_PER_PAGE: Final = 50_000
#: The legacy NWIS missing-value sentinel; tested by value, never by sign (reverse flow is real).
MISSING_VALUE_SENTINEL: Final = -999999.0
#: NWIS rejected bBox areas over 25 square degrees; the 4-degree tile is kept as the retry grain.
MAX_TILE_DEGREES: Final = 4.0

#: The only daily-value properties requested (OpenAPI `properties` enum); geometry still arrives.
DAILY_VALUE_PROPERTIES: Final[tuple[str, ...]] = (
    "time_series_id",
    "monitoring_location_id",
    "parameter_code",
    "statistic_id",
    "time",
    "value",
    "unit_of_measure",
    "approval_status",
    "qualifier",
)
MONITORING_LOCATION_PROPERTIES: Final[tuple[str, ...]] = ("monitoring_location_name", "time_zone_abbreviation")

#: A site's STANDARD-time UTC offset by its `time_zone_abbreviation` (P4 saw `PST` and `MST` only). A
#: daylight-time abbreviation names the same zone, so it maps to that zone's standard offset (review M2).
STANDARD_UTC_OFFSETS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "UTC": "+00:00",
        "GMT": "+00:00",
        "AST": "-04:00",
        "ADT": "-04:00",
        "EST": "-05:00",
        "EDT": "-05:00",
        "CST": "-06:00",
        "CDT": "-06:00",
        "MST": "-07:00",
        "MDT": "-07:00",
        "PST": "-08:00",
        "PDT": "-08:00",
        "AKST": "-09:00",
        "AKDT": "-09:00",
        "HST": "-10:00",
        "HDT": "-10:00",
    }
)
#: The last resort when no site in the day's tiles names a known zone: a UTC-midnight stamp.
FALLBACK_UTC_OFFSET: Final = "+00:00"

_USGS_AGENCY_PREFIX: Final = "USGS-"
_NAMED_DAY_LENGTH: Final = 10


class UsgsWaterDataPayloadError(UpstreamPayloadError):
    """A body (or one feature of it) this contract cannot read: a whole page is the unit's `strategy_error`."""


def publisher_named_day(raw_time: str) -> date:
    """The first ten characters of the served `time` string, as a date: never parsed, converted, then truncated.

    The same rule as `pipeline/validation/water_gauges.py::publisher_named_day`, restated here because a
    strategy may not import `pipeline/validation`; `tests/lanes/water_gauges/test_usgs_water_data.py`
    holds the two equal.
    """
    return date.fromisoformat(raw_time[:_NAMED_DAY_LENGTH])


@dataclass(frozen=True, slots=True)
class TileBox:
    """One request tile: a west/south/east/north box no wider or taller than `MAX_TILE_DEGREES`."""

    west: float
    south: float
    east: float
    north: float

    @property
    def bbox(self) -> str:
        """The `bbox` query value and the tile's stable key, e.g. `-125,42,-121,46`."""
        return ",".join(format_javascript_number(value) for value in (self.west, self.south, self.east, self.north))


def tile_boxes(envelope: RegionEnvelope, max_tile_degrees: float = MAX_TILE_DEGREES) -> tuple[TileBox, ...]:
    """Split an envelope into row-major tiles, south to north then west to east; PNW's camera envelope gives 8."""
    tiles: list[TileBox] = []
    south = envelope.south
    while south < envelope.north:
        north = min(south + max_tile_degrees, envelope.north)
        west = envelope.west
        while west < envelope.east:
            east = min(west + max_tile_degrees, envelope.east)
            tiles.append(TileBox(west=west, south=south, east=east, north=north))
            west += max_tile_degrees
        south += max_tile_degrees
    return tuple(tiles)


def daily_values_query(tile: TileBox, first: date, last: date) -> tuple[tuple[str, str], ...]:
    """One tile's daily mean discharge for `[first, last]`; a date-only interval includes both ends (P4)."""
    return (
        ("f", "json"),
        ("bbox", tile.bbox),
        ("parameter_code", DISCHARGE_PARAMETER_CODE),
        ("statistic_id", DAILY_MEAN_STATISTIC_ID),
        ("filter", STREAM_SITE_TYPE_FILTER),
        ("time", f"{first.isoformat()}/{last.isoformat()}"),
        ("limit", str(MAX_ITEMS_PER_PAGE)),
        ("properties", ",".join(DAILY_VALUE_PROPERTIES)),
    )


def monitoring_locations_query(tile: TileBox) -> tuple[tuple[str, str], ...]:
    """Every stream site in one tile, with its name and time-zone abbreviation, and no geometry."""
    return (
        ("f", "json"),
        ("bbox", tile.bbox),
        ("filter", STREAM_SITE_TYPE_FILTER),
        ("limit", str(MAX_ITEMS_PER_PAGE)),
        ("skipGeometry", "true"),
        ("properties", ",".join(MONITORING_LOCATION_PROPERTIES)),
    )


@dataclass(frozen=True, slots=True)
class DailyValue:
    """One served daily value, its fields verbatim; `flow_cfs` is `None` for a served null or the sentinel."""

    monitoring_location_id: str
    time: str
    statistic_id: str
    value_text: str | None
    flow_cfs: float | None
    #: Required on a valued row; a served null may lack it, and such a row is never written.
    approval_status: str | None
    qualifiers: tuple[str, ...]
    time_series_id: str | None
    longitude: float | None
    latitude: float | None

    @property
    def named_day(self) -> date:
        """The day this value names: `publisher_named_day(time)`."""
        return publisher_named_day(self.time)

    @property
    def identity(self) -> str:
        """Spec §7a: `monitoring_location_id:time:statistic_id`, verbatim."""
        return f"{self.monitoring_location_id}:{self.time}:{self.statistic_id}"

    @property
    def site_number(self) -> str:
        """The site number without its `USGS-` agency prefix; another agency's id is kept whole."""
        return self.monitoring_location_id.removeprefix(_USGS_AGENCY_PREFIX)


@dataclass(frozen=True, slots=True)
class MonitoringLocation:
    """A stream site's name and time-zone abbreviation, as the monitoring-locations collection serves them."""

    name: str | None
    time_zone_abbreviation: str | None

    @property
    def standard_utc_offset(self) -> str | None:
        """The site's standard-time offset, or `None` for a missing or unknown abbreviation."""
        abbreviation = (self.time_zone_abbreviation or "").strip().upper()
        return STANDARD_UTC_OFFSETS.get(abbreviation)


@dataclass(frozen=True, slots=True)
class RejectedFeature:
    """A daily-values feature this contract cannot read: dropped and counted, never guessed (review H1)."""

    #: The day its `time` names, when it names one; `None` when even that is unreadable.
    named_day: date | None
    reason: str


@dataclass(frozen=True, slots=True)
class DailyValuesPage:
    """One single-page daily-values answer: the values it carried and the features it could not."""

    values: tuple[DailyValue, ...]
    rejected: tuple[RejectedFeature, ...] = ()


def _feature_collection(body: bytes, collection: str) -> Sequence[object]:
    """The `features` of one single-page FeatureCollection; a `next` link means the unit overflowed its page."""
    try:
        document = json.loads(body)
    except ValueError as error:
        raise UsgsWaterDataPayloadError(f"the {collection} response is not JSON") from error
    if not isinstance(document, dict) or document.get("type") != "FeatureCollection":
        raise UsgsWaterDataPayloadError(f"the {collection} response is not a FeatureCollection")
    features = document.get("features")
    if not isinstance(features, list):
        raise UsgsWaterDataPayloadError(f"the {collection} response carries no features array")
    links = document.get("links")
    if isinstance(links, list) and any(isinstance(link, dict) and link.get("rel") == "next" for link in links):
        raise UsgsWaterDataPayloadError(
            f"the {collection} response has a next page: the unit asked for more than {MAX_ITEMS_PER_PAGE} items"
        )
    return features


def _required_text(properties: Mapping[str, object], name: str) -> str:
    value = properties.get(name)
    if not isinstance(value, str) or not value.strip():
        raise UsgsWaterDataPayloadError(f"a daily value has no {name}")
    return value


def _optional_text(properties: Mapping[str, object], name: str) -> str | None:
    value = properties.get(name)
    return value if isinstance(value, str) and value.strip() else None


def _flow_cfs(value_text: str | None, identity: str) -> float | None:
    """Parse a served value string; `None` for a served null or the sentinel, and a refusal for non-numbers."""
    if value_text is None:
        return None
    try:
        flow = float(value_text)
    except ValueError as error:
        raise UsgsWaterDataPayloadError(f"daily value {identity} is not a number") from error
    if not math.isfinite(flow):
        raise UsgsWaterDataPayloadError(f"daily value {identity} is not finite")
    return None if flow == MISSING_VALUE_SENTINEL else flow


def _qualifiers(raw: object) -> tuple[str, ...]:
    """The served qualifier list (P4: null or a list of codes), a bare string accepted as one code."""
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,) if raw.strip() else ()
    if isinstance(raw, list) and all(isinstance(code, str) for code in raw):
        return tuple(code for code in raw if code.strip())
    raise UsgsWaterDataPayloadError("a daily value's qualifier is neither null, a string nor a list of strings")


def _point(geometry: object) -> tuple[float | None, float | None]:
    """A Point's (longitude, latitude), or two `None`s when the feature carries no usable point."""
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        return None, None
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:  # noqa: PLR2004 - a GeoJSON position is [x, y, ...]
        return None, None
    longitude, latitude = coordinates[0], coordinates[1]
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (longitude, latitude)):
        return None, None
    return float(longitude), float(latitude)


def _daily_value(feature: object) -> DailyValue:
    if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
        raise UsgsWaterDataPayloadError("a daily-values feature carries no properties object")
    properties: Mapping[str, object] = feature["properties"]
    monitoring_location_id = _required_text(properties, "monitoring_location_id")
    time = _required_text(properties, "time")
    statistic_id = _required_text(properties, "statistic_id")
    identity = f"{monitoring_location_id}:{time}:{statistic_id}"
    try:
        publisher_named_day(time)
    except ValueError as error:
        raise UsgsWaterDataPayloadError(f"daily value {identity} names no ISO day") from error
    if statistic_id != DAILY_MEAN_STATISTIC_ID:
        raise UsgsWaterDataPayloadError(f"daily value {identity} is statistic {statistic_id}, not the daily mean")
    parameter_code = _optional_text(properties, "parameter_code")
    if parameter_code is not None and parameter_code != DISCHARGE_PARAMETER_CODE:
        raise UsgsWaterDataPayloadError(f"daily value {identity} is parameter {parameter_code}, not discharge")
    value_text = _optional_text(properties, "value")
    unit = _optional_text(properties, "unit_of_measure")
    approval_status = _optional_text(properties, "approval_status")
    if value_text is not None and unit != DISCHARGE_UNIT:
        raise UsgsWaterDataPayloadError(f"daily value {identity} is in {unit!r}, not {DISCHARGE_UNIT!r}")
    if value_text is not None and approval_status is None:
        raise UsgsWaterDataPayloadError(f"daily value {identity} carries a value but no approval_status")
    longitude, latitude = _point(feature.get("geometry"))
    return DailyValue(
        monitoring_location_id=monitoring_location_id,
        time=time,
        statistic_id=statistic_id,
        value_text=value_text,
        flow_cfs=_flow_cfs(value_text, identity),
        approval_status=approval_status,
        qualifiers=_qualifiers(properties.get("qualifier")),
        time_series_id=_optional_text(properties, "time_series_id"),
        longitude=longitude,
        latitude=latitude,
    )


def _feature_named_day(feature: object) -> date | None:
    """The day an unreadable feature's `time` names, or `None` when that is unreadable too."""
    properties = feature.get("properties") if isinstance(feature, dict) else None
    raw_time = properties.get("time") if isinstance(properties, dict) else None
    if not isinstance(raw_time, str):
        return None
    try:
        return publisher_named_day(raw_time)
    except ValueError:
        return None


def parse_daily_values(body: bytes) -> DailyValuesPage:
    """Every daily value in one single-page answer; an unreadable feature is rejected alone, never the page.

    Only a page-level break refuses the whole answer: not JSON, not a FeatureCollection, or a `next` link.
    """
    values: list[DailyValue] = []
    rejected: list[RejectedFeature] = []
    for feature in _feature_collection(body, "daily-values"):
        try:
            values.append(_daily_value(feature))
        except UsgsWaterDataPayloadError as error:
            rejected.append(RejectedFeature(named_day=_feature_named_day(feature), reason=str(error)))
    return DailyValuesPage(values=tuple(values), rejected=tuple(rejected))


def parse_monitoring_locations(body: bytes) -> Mapping[str, MonitoringLocation]:
    """Monitoring-location id -> its name and time zone, from one single-page monitoring-locations answer."""
    locations: dict[str, MonitoringLocation] = {}
    for feature in _feature_collection(body, "monitoring-locations"):
        if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
            raise UsgsWaterDataPayloadError("a monitoring-locations feature carries no properties object")
        location_id = feature.get("id")
        if not isinstance(location_id, str) or not location_id.strip():
            raise UsgsWaterDataPayloadError("a monitoring-locations feature has no id")
        properties: Mapping[str, object] = feature["properties"]
        locations[location_id] = MonitoringLocation(
            name=_optional_text(properties, "monitoring_location_name"),
            time_zone_abbreviation=_optional_text(properties, "time_zone_abbreviation"),
        )
    return MappingProxyType(locations)


__all__ = [
    "DAILY_MEAN_STATISTIC_ID",
    "DAILY_VALUES_ENDPOINT",
    "DAILY_VALUE_PROPERTIES",
    "DISCHARGE_PARAMETER_CODE",
    "DISCHARGE_UNIT",
    "FALLBACK_UTC_OFFSET",
    "MAX_ITEMS_PER_PAGE",
    "MAX_TILE_DEGREES",
    "MISSING_VALUE_SENTINEL",
    "MONITORING_LOCATIONS_ENDPOINT",
    "MONITORING_LOCATION_PROPERTIES",
    "STANDARD_UTC_OFFSETS",
    "STREAM_SITE_TYPE_FILTER",
    "USGS_WATER_DATA_PROVIDER",
    "USGS_WATER_DATA_SOURCE_SLUG",
    "DailyValue",
    "DailyValuesPage",
    "MonitoringLocation",
    "RejectedFeature",
    "TileBox",
    "UsgsWaterDataPayloadError",
    "daily_values_query",
    "monitoring_locations_query",
    "parse_daily_values",
    "parse_monitoring_locations",
    "publisher_named_day",
    "tile_boxes",
]
