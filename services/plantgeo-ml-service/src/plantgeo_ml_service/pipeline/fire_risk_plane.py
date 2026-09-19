"""Assembling one feature plane's frame: the horizon cross, the seasonality, the row checksums.

Layer L3, spec FR-5. Split out of `fire_risk_features.py` when that module passed the size ceiling.
PURE frame shaping over cells that were already read and joined; nothing here opens a bucket or a
clock. Why seasonality is computed rather than read from a fact table lives in `AGENTS-fire-risk.md`.
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import TYPE_CHECKING, Final

import polars as pl

from plantgeo_ml_service.foundation.canonical import canonical_json, sha256_digest

if TYPE_CHECKING:
    from datetime import date

FEATURE_SET_VERSION: Final = "fire-risk-features-v1"

#: The NDVI feature's published column name. Declared here with the rest of the input vocabulary,
#: because `FIRE_RISK_FEATURE_NAMES` names it and the two must not drift.
NDVI_FEATURE_COLUMN: Final = "normalized_difference_vegetation_index"

#: The model's input order. An artifact pins its own copy and refuses a frame that disagrees.
FIRE_RISK_FEATURE_NAMES: Final[tuple[str, ...]] = (
    "vapor_pressure_deficit",
    "air_temperature_mean",
    "relative_humidity",
    "wind_speed",
    "precipitation",
    "surface_soil_water_content",
    "root_zone_soil_water_content",
    "soil_temperature",
    NDVI_FEATURE_COLUMN,
    "detection_count_history",
    "radiative_power_history",
    "high_confidence_detection_history",
    "prior_burn_envelope_overlap",
    "regional_drought_category",
    "day_of_year_sine",
    "day_of_year_cosine",
    "photoperiod_seconds",
)

GRAIN_COLUMNS: Final[tuple[str, ...]] = ("cell_longitude", "cell_latitude", "valid_day")

SECONDS_PER_HOUR: Final = 3_600
HOURS_PER_DAY: Final = 24
DAYS_PER_COMMON_YEAR: Final = 365
DAYS_PER_LEAP_YEAR: Final = 366


def cyclical_day_of_year(day: date) -> tuple[float, float]:
    """Return the sine and cosine of one day's position in its own year, cycle closed."""
    year_length = DAYS_PER_LEAP_YEAR if _is_leap_year(day.year) else DAYS_PER_COMMON_YEAR
    angle = 2.0 * math.pi * (day.timetuple().tm_yday - 1) / year_length
    return math.sin(angle), math.cos(angle)


def photoperiod_seconds(latitude: float, day: date) -> float:
    """Return daylight seconds from latitude and date, computed, never read from a fact table.

    FAO-56 (Allen et al. 1998) equations 24 and 25: solar declination, then the sunset hour angle.
    The cosine argument is clipped, so a polar day answers a full day rather than a domain error.
    """
    declination = 0.409 * math.sin(2.0 * math.pi * day.timetuple().tm_yday / DAYS_PER_COMMON_YEAR - 1.39)
    cosine = -math.tan(math.radians(latitude)) * math.tan(declination)
    sunset_hour_angle = math.acos(min(1.0, max(-1.0, cosine)))
    return HOURS_PER_DAY / math.pi * sunset_hour_angle * SECONDS_PER_HOUR


# --- Frame assembly ----------------------------------------------------------------------------------


def crossed_with_horizons(cells: pl.DataFrame, *, issued_on: date, horizons: tuple[int, ...]) -> pl.DataFrame:
    """Repeat each cell once per horizon, because only the seasonal features vary with the valid day."""
    horizon_frame = pl.DataFrame(
        {
            "horizon_days": list(horizons),
            "valid_day": [issued_on + timedelta(days=horizon) for horizon in horizons],
            "issued_on": [issued_on] * len(horizons),
        },
        schema={"horizon_days": pl.Int64, "valid_day": pl.Date, "issued_on": pl.Date},
    )
    return cells.join(horizon_frame, how="cross")


def with_seasonality(frame: pl.DataFrame) -> pl.DataFrame:
    """Add the mandatory cyclical day-of-year pair and the computed photoperiod."""
    year = pl.col("valid_day").dt.year()
    is_leap = ((year % 4 == 0) & ((year % 100 != 0) | (year % 400 == 0))).cast(pl.Float64)
    year_length = is_leap * float(DAYS_PER_LEAP_YEAR) + (1.0 - is_leap) * float(DAYS_PER_COMMON_YEAR)
    ordinal_day = pl.col("valid_day").dt.ordinal_day().cast(pl.Float64)
    angle = (ordinal_day - 1.0) * (2.0 * math.pi) / year_length
    declination = (ordinal_day * (2.0 * math.pi / DAYS_PER_COMMON_YEAR) - 1.39).sin() * 0.409
    cosine = -(pl.col("cell_latitude") * (math.pi / 180.0)).tan() * declination.tan()
    sunset_hour_angle = cosine.clip(-1.0, 1.0).arccos()
    return frame.with_columns(
        angle.sin().alias("day_of_year_sine"),
        angle.cos().alias("day_of_year_cosine"),
        (sunset_hour_angle * (HOURS_PER_DAY / math.pi * SECONDS_PER_HOUR)).alias("photoperiod_seconds"),
    )


def with_checksums(frame: pl.DataFrame) -> pl.DataFrame:
    """Add the feature-set version and one canonical-JSON digest per row, over the grain and features."""
    digests = [_row_checksum(row) for row in frame.iter_rows(named=True)]
    return frame.with_columns(
        pl.lit(FEATURE_SET_VERSION).alias("feature_set_version"),
        pl.Series("feature_checksum", digests, dtype=pl.String),
    )


def _row_checksum(row: dict[str, object]) -> str:
    """Return the digest of one row's grain and features, so a scored row names the vector it saw."""
    payload = {
        "feature_set_version": FEATURE_SET_VERSION,
        "cell_longitude": row["cell_longitude"],
        "cell_latitude": row["cell_latitude"],
        "valid_day": str(row["valid_day"]),
        "stratum": row["stratum"],
        "features": {name: row[name] for name in FIRE_RISK_FEATURE_NAMES},
    }
    return sha256_digest(canonical_json(payload))


def empty_feature_frame() -> pl.DataFrame:
    """Return a typed, zero-row plane so an issue day with no cells has the same columns as a full one."""
    schema: dict[str, type[pl.DataType]] = {
        "cell_longitude": pl.Float64,
        "cell_latitude": pl.Float64,
        "valid_day": pl.Date,
        "issued_on": pl.Date,
        "horizon_days": pl.Int64,
        "stratum": pl.String,
    }
    for name in FIRE_RISK_FEATURE_NAMES:
        schema[name] = pl.Float64
    schema["feature_set_version"] = pl.String
    schema["feature_checksum"] = pl.String
    return pl.DataFrame(schema=schema)


def _is_leap_year(year: int) -> bool:
    """Return whether a year carries 366 days under the proleptic Gregorian rule."""
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


__all__ = [
    "DAYS_PER_COMMON_YEAR",
    "DAYS_PER_LEAP_YEAR",
    "FEATURE_SET_VERSION",
    "FIRE_RISK_FEATURE_NAMES",
    "GRAIN_COLUMNS",
    "HOURS_PER_DAY",
    "NDVI_FEATURE_COLUMN",
    "SECONDS_PER_HOUR",
    "crossed_with_horizons",
    "cyclical_day_of_year",
    "empty_feature_frame",
    "photoperiod_seconds",
    "with_checksums",
    "with_seasonality",
]
