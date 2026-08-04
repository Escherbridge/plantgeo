"""Bounded-ingestion policy: the bbox contract, call-time environment reads, and observation freshness."""

from __future__ import annotations

import math
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Final

INGEST_BBOX_VARIABLE: Final = "INGEST_BBOX"
MAX_SOURCE_RECORDS_VARIABLE: Final = "INGEST_MAX_SOURCE_RECORDS"
WEATHER_SAMPLE_SPACING_VARIABLE: Final = "WEATHER_SAMPLE_SPACING_DEGREES"

# Canonical coverage box, moved here from `src/__tests__/services/ingestion-jobs.test.ts:3`
# when that test was deleted. See ingest/AGENTS.md "policy.py".
PACIFIC_NORTHWEST_COVERAGE_BBOX: Final = "-125,42,-111,49"

BBOX_ORDINATE_COUNT: Final = 4
MIN_LONGITUDE: Final = -180.0
MAX_LONGITUDE: Final = 180.0
MIN_LATITUDE: Final = -90.0
MAX_LATITUDE: Final = 90.0
MAX_LONGITUDE_SPAN: Final = 30.0
MAX_LATITUDE_SPAN: Final = 20.0

DEFAULT_MAX_SOURCE_RECORDS: Final = 10_000
MIN_MAX_SOURCE_RECORDS: Final = 1_000
MAX_MAX_SOURCE_RECORDS: Final = 50_000

DEFAULT_WEATHER_SAMPLE_SPACING_DEGREES: Final = 1.0
MIN_WEATHER_SAMPLE_SPACING_DEGREES: Final = 0.25
MAX_WEATHER_SAMPLE_SPACING_DEGREES: Final = 5.0
# Bounds the Open-Meteo fan-out regardless of how dense INGEST_BBOX makes the grid.
MAX_WEATHER_SAMPLE_POINTS: Final = 150

MAX_FUTURE_SKEW: Final = timedelta(minutes=5)

UNCONFIGURED_BBOX_REASON: Final = "INGEST_BBOX is not configured"

_LEADING_NUMBER: Final = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


class BoundedIngestionPolicyError(ValueError):
    """Raised when INGEST_BBOX is malformed or falls outside the bounded ingestion policy."""


def javascript_number(text: str) -> float:
    """Parse a string as JavaScript `Number(text)` parses it, returning NaN where JavaScript returns NaN."""
    candidate = text.strip()
    if not candidate:
        return 0.0
    # Python's float() accepts digit separators and JavaScript's Number() does not.
    if "_" in candidate:
        return math.nan
    try:
        return float(candidate)
    except ValueError:
        return math.nan


def javascript_parse_float(text: str) -> float | None:
    """Parse a leading numeric prefix as JavaScript `parseFloat` does, returning None where it returns NaN."""
    match = _LEADING_NUMBER.match(text.strip())
    if match is None:
        return None
    try:
        return float(match.group(0))
    except ValueError:  # pragma: no cover - the pattern already guarantees a parseable prefix.
        return None


def format_javascript_number(value: float) -> str:
    """Render a number as JavaScript renders it in a template string or `Array.prototype.join`."""
    if not math.isfinite(value):
        raise ValueError("value must be a finite number")
    if value == 0:
        return "0"
    if value.is_integer():
        return str(int(value))
    return repr(value)


def resolve_bounded_bbox(override: str | None = None) -> str | None:
    """Return the policy-checked ingestion bbox, or None when neither an override nor INGEST_BBOX is set."""
    value = (override or "").strip() or os.environ.get(INGEST_BBOX_VARIABLE, "").strip()
    if not value:
        return None

    ordinates = [javascript_number(part) for part in value.split(",")]
    if len(ordinates) != BBOX_ORDINATE_COUNT or any(not math.isfinite(ordinate) for ordinate in ordinates):
        raise BoundedIngestionPolicyError("INGEST_BBOX must be west,south,east,north")

    west, south, east, north = ordinates
    if (
        west < MIN_LONGITUDE
        or east > MAX_LONGITUDE
        or south < MIN_LATITUDE
        or north > MAX_LATITUDE
        or west >= east
        or south >= north
        or east - west > MAX_LONGITUDE_SPAN
        or north - south > MAX_LATITUDE_SPAN
    ):
        raise BoundedIngestionPolicyError("INGEST_BBOX is outside the bounded ingestion policy")

    return ",".join(format_javascript_number(ordinate) for ordinate in ordinates)


def parse_bbox(bbox: str) -> tuple[float, float, float, float]:
    """Split an already policy-checked `west,south,east,north` bbox into its four ordinates."""
    west, south, east, north = (javascript_number(part) for part in bbox.split(","))
    return west, south, east, north


def resolve_max_source_records() -> int:
    """Read INGEST_MAX_SOURCE_RECORDS at call time so a cron environment change needs no restart."""
    raw = os.environ.get(MAX_SOURCE_RECORDS_VARIABLE, "").strip()
    if not raw:
        return DEFAULT_MAX_SOURCE_RECORDS
    parsed = javascript_number(raw)
    if not math.isfinite(parsed):
        return DEFAULT_MAX_SOURCE_RECORDS
    return int(min(MAX_MAX_SOURCE_RECORDS, max(MIN_MAX_SOURCE_RECORDS, parsed)))


def resolve_weather_sample_spacing_degrees() -> float:
    """Read WEATHER_SAMPLE_SPACING_DEGREES at call time, clamped to a sane densification range."""
    raw = os.environ.get(WEATHER_SAMPLE_SPACING_VARIABLE, "").strip()
    if not raw:
        return DEFAULT_WEATHER_SAMPLE_SPACING_DEGREES
    parsed = javascript_number(raw)
    if not math.isfinite(parsed):
        return DEFAULT_WEATHER_SAMPLE_SPACING_DEGREES
    return min(MAX_WEATHER_SAMPLE_SPACING_DEGREES, max(MIN_WEATHER_SAMPLE_SPACING_DEGREES, parsed))


def is_fresh_observation(observed_at: datetime, max_age: timedelta, now: datetime | None = None) -> bool:
    """Reject an observation older than `max_age` or skewed more than five minutes into the future."""
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must include a timezone")
    moment = now if now is not None else datetime.now(UTC)
    return moment - max_age <= observed_at <= moment + MAX_FUTURE_SKEW
