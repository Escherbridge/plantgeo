"""Single definition of a warehouse feature identity and the timestamp that dates its first version."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

FIRMS_PRODUCER: Final = "firms"
USGS_NWIS_PRODUCER: Final = "usgs-nwis"
OPEN_METEO_PRODUCER: Final = "open-meteo"
WFIGS_PRODUCER: Final = "wfigs"
USDM_PRODUCER: Final = "usdm"
MTBS_PRODUCER: Final = "mtbs"

PRODUCER_TOKEN_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9-]{1,98}$")
MAX_NATURAL_KEY_LENGTH: Final = 255

COORDINATE_DIGITS: Final = 4
MAX_FIXED_DIGITS: Final = 100
MICROSECONDS_PER_MILLISECOND: Final = 1000
ISO_DATE_PATTERN: Final = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
ISO_ZONE_SUFFIX_PATTERN: Final = re.compile(r"(?:Z|[+-]\d{2}:\d{2})$", re.IGNORECASE)
FIRMS_TIME_PATTERN: Final = re.compile(r"^\d{4}$")
FIRMS_TIME_DIGITS: Final = 4

# A perimeter cannot predate the discovery of its own fire, so `fireDiscoveryDateTime` is a defensible lower
# bound when WFIGS publishes a JSON null `polygonDateTime`. Order matters and matches
# scripts/backfill-geometry.sql. See ingest/AGENTS.md "identity.py: WFIGS dates from its discovery time".
WFIGS_OBSERVATION_FIELDS: Final = ("polygonDateTime", "fireDiscoveryDateTime")

MIN_DROUGHT_MONITOR_CATEGORY: Final = 0
MAX_DROUGHT_MONITOR_CATEGORY: Final = 4

PRODUCER_BY_LAYER_NAME: Mapping[str, str] = MappingProxyType(
    {
        "fire-detections": FIRMS_PRODUCER,
        "water-gauges": USGS_NWIS_PRODUCER,
        "weather-observations": OPEN_METEO_PRODUCER,
        "fire-perimeters": WFIGS_PRODUCER,
    }
)


class MissingNativeKeyError(ValueError):
    """Raised when an upstream record supplies no stable native key; never synthesise one."""


@dataclass(frozen=True, slots=True)
class FeatureIdentity:
    """One producer's identity for one feature, plus the timestamp that dates its first geometry version."""

    producer: str
    producer_local_id: str
    observed_at: datetime | None
    entity_local_id: str | None = None

    def __post_init__(self) -> None:
        """Enforce the governed producer token, the 255-character key ceiling, and a tz-aware observation."""
        if PRODUCER_TOKEN_PATTERN.match(self.producer) is None:
            raise ValueError(f"producer must match {PRODUCER_TOKEN_PATTERN.pattern}")
        if not self.producer_local_id:
            raise MissingNativeKeyError("producer_local_id must not be blank")
        if self.entity_local_id is not None and not self.entity_local_id:
            raise MissingNativeKeyError("entity_local_id must not be blank when supplied")
        if len(self.natural_key) > MAX_NATURAL_KEY_LENGTH:
            raise ValueError(f"natural_key must not exceed {MAX_NATURAL_KEY_LENGTH} characters")
        if len(self.entity_key) > MAX_NATURAL_KEY_LENGTH:
            raise ValueError(f"entity_key must not exceed {MAX_NATURAL_KEY_LENGTH} characters")
        if self.observed_at is None:
            return
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(UTC))

    @property
    def natural_key(self) -> str:
        """The observation identity: byte-identical to the TypeScript `properties->>'id'` it replaces."""
        return f"{self.producer}:{self.producer_local_id}"

    @property
    def entity_key(self) -> str:
        """The geo.geometry identity: the enduring place this observation was taken at, not the observation."""
        return f"{self.producer}:{self.entity_local_id or self.producer_local_id}"

    @property
    def observation_is_its_own_entity(self) -> bool:
        """True when the producer emits one distinct place per observation, so both keys coincide."""
        return self.entity_local_id is None


def format_javascript_fixed(value: float, digits: int = COORDINATE_DIGITS) -> str:
    """Render a number exactly as JavaScript `Number.prototype.toFixed(digits)` renders it."""
    if not math.isfinite(value):
        raise ValueError("value must be a finite number")
    if not 0 <= digits <= MAX_FIXED_DIGITS:
        raise ValueError(f"digits must be between 0 and {MAX_FIXED_DIGITS}")
    # `Decimal(float)` is the double's EXACT binary value, which is what ECMA-262 rounds. Never
    # `Decimal(repr(value))` -- that re-rounds the shortest round-trip string, turning every
    # coordinate whose double sits just below a midpoint into a literal tie and pushing it away
    # from zero, which changes the key. See ingest/AGENTS.md "identity.py".
    exact = Decimal(0) if value == 0 else Decimal(value)
    return str(exact.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def format_coordinate(value: float) -> str:
    """Render a coordinate exactly as the TypeScript `coordinates[n].toFixed(4)` rendered it."""
    return format_javascript_fixed(value, COORDINATE_DIGITS)


def format_javascript_timestamp(moment: datetime) -> str:
    """Render an instant exactly as JavaScript `Date.prototype.toISOString()` renders it."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("moment must include a timezone")
    utc_moment = moment.astimezone(UTC)
    milliseconds = utc_moment.microsecond // MICROSECONDS_PER_MILLISECOND
    return (
        f"{utc_moment.year:04d}-{utc_moment.month:02d}-{utc_moment.day:02d}"
        f"T{utc_moment.hour:02d}:{utc_moment.minute:02d}:{utc_moment.second:02d}"
        f".{milliseconds:03d}Z"
    )


def _required_text(payload: Mapping[str, object], field_name: str) -> str:
    """Return an upstream string field untrimmed, rejecting an absent or blank value."""
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise MissingNativeKeyError(f"{field_name} is required and must not be blank")
    return value


def _required_acquisition_time(properties: Mapping[str, object]) -> str:
    """Return FIRMS `acqTime` exactly as the TypeScript interpolates it, unpadded."""
    value = properties.get("acqTime")
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return _required_text(properties, "acqTime")


def _required_coordinate(coordinates: Sequence[object] | None, index: int, axis_name: str) -> float:
    """Return one GeoJSON coordinate ordinate, rejecting an absent or non-numeric value."""
    value = coordinates[index] if coordinates is not None and len(coordinates) > index else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise MissingNativeKeyError(f"{axis_name} coordinate is required for a native key")
    return float(value)


def _optional_zoned_timestamp(value: object) -> datetime | None:
    """Port of `environmental-time.ts:6-12`: accept only an explicitly zoned ISO-8601 string, else fall through."""
    if not isinstance(value, str) or ISO_ZONE_SUFFIX_PATTERN.search(value.strip()) is None:
        return None
    try:
        return _parse_upstream_timestamp(value, "observedAt")
    except ValueError:
        return None


def _firms_observation_time(properties: Mapping[str, object], acquisition_date: str, acquisition_time: str) -> datetime:
    """Parse an explicitly zoned FIRMS `observedAt`, else `acqDate` plus a zero-padded `acqTime`, as a UTC instant."""
    explicit = _optional_zoned_timestamp(properties.get("observedAt"))
    if explicit is not None:
        return explicit
    date_match = ISO_DATE_PATTERN.match(acquisition_date.strip())
    padded_time = acquisition_time.strip().rjust(FIRMS_TIME_DIGITS, "0")
    if date_match is None or FIRMS_TIME_PATTERN.match(padded_time) is None:
        raise ValueError("FIRMS acqDate and acqTime must form a YYYY-MM-DD and HHMM pair")
    try:
        return datetime(
            int(date_match.group(1)),
            int(date_match.group(2)),
            int(date_match.group(3)),
            int(padded_time[:2]),
            int(padded_time[2:]),
            tzinfo=UTC,
        )
    except ValueError as error:
        raise ValueError("FIRMS acqDate and acqTime must form a valid UTC calendar instant") from error


def _parse_upstream_timestamp(value: str, field_name: str) -> datetime:
    """Parse an upstream ISO-8601 instant into UTC without re-emitting the string that keys it."""
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as error:
        raise ValueError(f"{field_name} must be an ISO-8601 instant") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must carry a UTC offset")
    return parsed.astimezone(UTC)


def build_firms_identity(properties: Mapping[str, object], coordinates: Sequence[object] | None) -> FeatureIdentity:
    """Build the FIRMS active-fire detection identity: satellite, acquisition date/time and 4dp coordinates."""
    satellite = _required_text(properties, "satellite")
    acquisition_date = _required_text(properties, "acqDate")
    acquisition_time = _required_acquisition_time(properties)
    longitude = _required_coordinate(coordinates, 0, "longitude")
    latitude = _required_coordinate(coordinates, 1, "latitude")
    producer_local_id = ":".join(
        (
            satellite,
            acquisition_date,
            acquisition_time,
            format_coordinate(latitude),
            format_coordinate(longitude),
        )
    )
    return FeatureIdentity(
        producer=FIRMS_PRODUCER,
        producer_local_id=producer_local_id,
        observed_at=_firms_observation_time(properties, acquisition_date, acquisition_time),
    )


def build_streamflow_gauge_identity(gauge: Mapping[str, object]) -> FeatureIdentity:
    """Build the USGS NWIS gauge identity: site number and the upstream reading time, verbatim."""
    site_number = _required_text(gauge, "siteNo")
    updated_at = _required_text(gauge, "updatedAt")
    return FeatureIdentity(
        producer=USGS_NWIS_PRODUCER,
        producer_local_id=f"{site_number}:{updated_at}",
        observed_at=_parse_upstream_timestamp(updated_at, "updatedAt"),
        entity_local_id=site_number,
    )


def build_weather_observation_identity(
    latitude: float,
    longitude: float,
    observation: Mapping[str, object],
) -> FeatureIdentity:
    """Build the Open-Meteo observation identity: 4dp sample-grid coordinates and the upstream instant, verbatim."""
    observed_at_text = _required_text(observation, "observedAt")
    sample_point = f"{format_coordinate(latitude)}:{format_coordinate(longitude)}"
    return FeatureIdentity(
        producer=OPEN_METEO_PRODUCER,
        producer_local_id=f"{sample_point}:{observed_at_text}",
        observed_at=_parse_upstream_timestamp(observed_at_text, "observedAt"),
        entity_local_id=sample_point,
    )


def _first_supplied_timestamp(payload: Mapping[str, object], field_names: Sequence[str]) -> datetime | None:
    """Parse the first of the named fields the producer actually supplied, rejecting one it supplied unparseably."""
    for field_name in field_names:
        value = payload.get(field_name)
        if isinstance(value, str) and value.strip():
            return _parse_upstream_timestamp(value, field_name)
    return None


def build_fire_perimeter_identity(perimeter: Mapping[str, object]) -> FeatureIdentity:
    """Build the WFIGS perimeter identity: the bare fire identifier, dated by its polygon time or its discovery."""
    return FeatureIdentity(
        producer=WFIGS_PRODUCER,
        producer_local_id=_required_text(perimeter, "uniqueFireIdentifier"),
        observed_at=_first_supplied_timestamp(perimeter, WFIGS_OBSERVATION_FIELDS),
    )


def _release_date(valid_date: str | date) -> date:
    """Return a USDM release date from a `YYYY-MM-DD` string or a stored `date`, rejecting a blank or unreal one."""
    if isinstance(valid_date, datetime):
        return valid_date.date()
    if isinstance(valid_date, date):
        return valid_date
    if not valid_date.strip():
        raise MissingNativeKeyError("validDate is required and must not be blank")
    if ISO_DATE_PATTERN.match(valid_date.strip()) is None:
        raise ValueError("validDate must be a YYYY-MM-DD release date")
    try:
        return date.fromisoformat(valid_date.strip())
    except ValueError as error:
        raise ValueError("validDate must be a real calendar date") from error


def build_drought_area_identity(valid_date: str | date, drought_monitor_category: int) -> FeatureIdentity:
    """Build the USDM area identity from the release valid date and drought class, dated at midnight UTC."""
    if isinstance(drought_monitor_category, bool):
        raise MissingNativeKeyError("dmCategory must be a drought class integer")
    if not MIN_DROUGHT_MONITOR_CATEGORY <= drought_monitor_category <= MAX_DROUGHT_MONITOR_CATEGORY:
        raise ValueError(
            f"dmCategory must be a D{MIN_DROUGHT_MONITOR_CATEGORY}-D{MAX_DROUGHT_MONITOR_CATEGORY} drought class"
        )
    release_date = _release_date(valid_date)
    return FeatureIdentity(
        producer=USDM_PRODUCER,
        producer_local_id=f"{release_date.isoformat()}:{drought_monitor_category}",
        observed_at=datetime(release_date.year, release_date.month, release_date.day, tzinfo=UTC),
    )


def build_burn_severity_identity(properties: Mapping[str, object]) -> FeatureIdentity:
    """Build the MTBS burn-severity identity from the native `Fire_ID`; its release date is not yet available."""
    return FeatureIdentity(
        producer=MTBS_PRODUCER,
        producer_local_id=_required_text(properties, "Fire_ID"),
        observed_at=None,
    )
