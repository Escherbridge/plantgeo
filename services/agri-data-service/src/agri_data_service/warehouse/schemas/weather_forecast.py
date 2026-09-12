"""Fixture-only immutable forecast contract; see pipeline/direct/weather_forecast/AGENTS.md."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, TypedDict

from agri_data_service.warehouse.parquet.weather_forecast_arrow import arrow as pa

VERSION: Final = "weather-forecast/v1"
MAX_HOURS: Final = 72
MAX_WINDOW_HOURS: Final = 48
HOURS_PER_DAY: Final = 24
SECONDS_PER_HOUR: Final = 3600
MAX_BYTES: Final = 2 * 1024 * 1024
SAMPLES: Final = ((40.0, -105.0), (40.1, -105.0), (40.0, -104.9), (40.1, -104.9))
MAX_POINTS: Final = len(SAMPLES)
CALM_THRESHOLD: Final = 0.01


@dataclass(frozen=True, slots=True)
class Variable:
    """One frozen fixture variable definition."""

    unit: str
    statistic: str
    minimum: float
    maximum: float


VARIABLES: Final = {
    "temperature_2m": Variable("degC", "instantaneous", -100, 70),
    "relative_humidity_2m": Variable("%", "instantaneous", 0, 100),
    "cloud_cover": Variable("%", "instantaneous", 0, 100),
    "precipitation": Variable("mm", "sum_over_following_hour", 0, 1000),
    "wind_u_10m": Variable("m/s", "instantaneous_earth_relative", -150, 150),
    "wind_v_10m": Variable("m/s", "instantaneous_earth_relative", -150, 150),
    "wind_speed_10m": Variable("m/s", "derived_vector_magnitude", 0, 150),
    "wind_direction_10m": Variable("degree", "meteorological_from_true_north", 0, 360),
}
MAX_ROWS: Final = MAX_HOURS * MAX_POINTS * len(VARIABLES)

ARROW_SCHEMA: Final = pa.schema(
    [
        pa.field("run_id", pa.string(), nullable=False),
        pa.field("latitude", pa.float64(), nullable=False),
        pa.field("longitude", pa.float64(), nullable=False),
        pa.field("valid_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("interval_start", pa.timestamp("us", tz="UTC")),
        pa.field("interval_end", pa.timestamp("us", tz="UTC")),
        pa.field("lead_hours", pa.int16(), nullable=False),
        pa.field("variable", pa.string(), nullable=False),
        pa.field("value", pa.float64()),
        pa.field("missing_reason", pa.string()),
    ],
    metadata={b"contract": VERSION.encode(), b"admission": b"synthetic-fixture-only"},
)


def utc_instant(value: str) -> datetime:
    """Require an explicit UTC instant aligned to an hour."""
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() != timedelta(0) or result.minute or result.second or result.microsecond:
        raise ValueError("Expected an hour-aligned explicit UTC instant")
    return result.astimezone(UTC)


def iso(value: datetime) -> str:
    """Render an aware instant with an explicit UTC suffix."""
    if value.tzinfo is None:
        raise ValueError("Naive timestamps are forbidden")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def validate_run_id(run_id: str) -> str:
    """Accept only versioned fixture run identifiers, never filesystem paths."""
    if re.fullmatch(r"fixture-\d{8}T\d{6}Z-v1", run_id) is None:
        raise ValueError("Unsupported fixture run identifier")
    return run_id


class ForecastRun(TypedDict):
    """Immutable fixture run and synthetic lifecycle identity."""

    run_id: str
    provider: str
    model: str
    initialization_time: str
    issue_time: str
    fetched_at: str
    admitted_at: str
    published_at: str
    lifecycle_basis: str
    valid_start: str
    valid_end: str
    licence: str
    support: str
    source_resolution: str
    cadence: str
    production_admitted: bool


class ForecastRow(TypedDict):
    """One variable at one exact sampled point and valid hour."""

    run_id: str
    latitude: float
    longitude: float
    valid_time: datetime
    interval_start: datetime | None
    interval_end: datetime | None
    lead_hours: int
    variable: str
    value: float | None
    missing_reason: str | None


def fixture_run(initialization: datetime) -> ForecastRun:
    """Declare synthetic timestamps and support without claiming provider admission."""
    initialization = utc_instant(iso(initialization))
    return {
        "run_id": f"fixture-{initialization.strftime('%Y%m%dT%H%M%SZ')}-v1",
        "provider": "PlantGeo fixture",
        "model": "deterministic-fixture-v1",
        "initialization_time": iso(initialization),
        "issue_time": iso(initialization),
        "fetched_at": iso(initialization),
        "admitted_at": iso(initialization),
        "published_at": iso(initialization),
        "lifecycle_basis": "synthetic_fixture_clock",
        "valid_start": iso(initialization),
        "valid_end": iso(initialization + timedelta(hours=MAX_HOURS)),
        "licence": "CC0-1.0",
        "support": "sampled_point",
        "source_resolution": "not-applicable: synthetic samples",
        "cadence": "manual fixture generation only",
        "production_admitted": False,
    }


def variable_catalogue() -> dict[str, dict[str, object]]:
    """Render fresh copies of the frozen variable catalogue."""
    return {name: asdict(variable) for name, variable in VARIABLES.items()}


def wind_from_components(u: float, v: float) -> tuple[float, float | None]:
    """Convert earth-relative components to speed and meteorological direction."""
    speed = math.hypot(u, v)
    return speed, None if speed < CALM_THRESHOLD else math.degrees(math.atan2(-u, -v)) % 360
