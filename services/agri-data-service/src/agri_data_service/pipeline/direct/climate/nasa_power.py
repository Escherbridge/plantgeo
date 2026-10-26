"""NASA POWER daily point URL and response-value guards shared by the climate source and edge probe.

Moved 2026-10-26 from the purged `execution/weather_observations/nasa_power.py` historical backfill;
values are unchanged. See `pipeline/direct/climate/AGENTS.md`.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

import httpx

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

NASA_POWER_DAILY_URL: Final = "https://power.larc.nasa.gov/api/temporal/daily/point"
#: POWER's numeric fill values (-999 and friends) sit at or below this; they mean "no observation".
NASA_POWER_MISSING_VALUE_CEILING: Final = -900.0


def nasa_power_daily_point_url(  # noqa: PLR0913 - one argument per governed query field, all keyword-only
    *,
    latitude: float,
    longitude: float,
    parameters: Sequence[str],
    start_date: date,
    end_date: date,
    time_standard: str = "UTC",
) -> httpx.URL:
    """Build the credential-free, canonical POWER daily point URL for one coordinate and window."""
    return httpx.URL(NASA_POWER_DAILY_URL).copy_merge_params(
        {
            "community": "AG",
            "latitude": _format_coordinate(latitude),
            "longitude": _format_coordinate(longitude),
            "parameters": ",".join(parameters),
            "start": start_date.strftime("%Y%m%d"),
            "end": end_date.strftime("%Y%m%d"),
            "format": "JSON",
            "time-standard": time_standard,
        }
    )


def extract_nasa_power_parameter_values(decoded: object) -> dict[str, dict[str, object]]:
    """Return `properties.parameter` as string-keyed series, refusing any other response shape."""
    if not isinstance(decoded, dict):
        raise ValueError("NASA POWER response must be a JSON object")
    properties = decoded.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("NASA POWER response is missing properties")
    parameter_values = properties.get("parameter")
    if not isinstance(parameter_values, dict):
        raise ValueError("NASA POWER response is missing properties.parameter")
    result: dict[str, dict[str, object]] = {}
    for parameter, series in parameter_values.items():
        if not isinstance(parameter, str) or not isinstance(series, dict):
            raise ValueError("NASA POWER parameter series must be string-keyed JSON objects")
        result[parameter] = series
    return result


def nasa_power_observed_value(value: object) -> float | None:
    """Return one finite POWER value, or None for a null or a provider fill value."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("NASA POWER parameter values must be numeric or null")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("NASA POWER parameter values must be finite")
    # Preserve the missingness rather than treating a sentinel as an environmental value.
    return None if number <= NASA_POWER_MISSING_VALUE_CEILING else number


def _format_coordinate(value: float) -> str:
    return format(value, ".6f").rstrip("0").rstrip(".")


__all__ = [
    "NASA_POWER_DAILY_URL",
    "NASA_POWER_MISSING_VALUE_CEILING",
    "extract_nasa_power_parameter_values",
    "nasa_power_daily_point_url",
    "nasa_power_observed_value",
]
