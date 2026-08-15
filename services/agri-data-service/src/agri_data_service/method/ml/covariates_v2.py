"""Pure half of the covariate layer: the pinned versions, the vector types, the site-climate math.

Every function here is database-free. The governed reads that fill these types live in
`execution/recommendation_lane.py`, because the layer import contract forbids SQLAlchemy in
`method/`. Rationale and the v2 as-of contract: `method/AGENTS.md` under `covariates_v2.py`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.canonical import canonical_json, sha256_digest

if TYPE_CHECKING:
    from collections.abc import Mapping

SCHEMA_VERSION_V1: Final = "agri_covariates_v1"
SCHEMA_VERSION_V2: Final = "agri_covariates_v2"
SUPPORTED_SCHEMA_VERSIONS: Final = (SCHEMA_VERSION_V1, SCHEMA_VERSION_V2)

# The as-of regime each version actually implements, recorded in every artifact and receipt so
# a number read out of a JSONB column never travels without the qualification it needs.
AS_OF_MODE_BY_SCHEMA_VERSION: Final[Mapping[str, str]] = {
    SCHEMA_VERSION_V1: "global",
    SCHEMA_VERSION_V2: "per_issue_date_preferred_earliest_fallback",
}

CLIMATE_WINDOW_DAYS: Final = 365
# A trailing year is reported only when nearly complete; a partial year silently summed would
# understate annual precipitation and read as a drier site than the streams observed.
CLIMATE_MIN_COMPLETE_DAYS: Final = 330

# Hargreaves-Samani (1985) reference evapotranspiration, used only to derive an aridity CLASS
# from governed temperature. It is a declared proxy, not a governed stream: every consumer
# records the term as `derived_proxy` so no reader mistakes it for an observation.
_SOLAR_CONSTANT_MJ_PER_M2_MIN: Final = 0.0820
_MJ_PER_M2_TO_MM: Final = 0.408
_HARGREAVES_COEFFICIENT: Final = 0.0023
_HARGREAVES_TEMPERATURE_OFFSET: Final = 17.8

# UNEP (1997) aridity index bands over precipitation / reference evapotranspiration.
_ARIDITY_BANDS: Final[tuple[tuple[float, str], ...]] = (
    (0.03, "hyper_arid"),
    (0.20, "arid"),
    (0.50, "semi_arid"),
    (0.65, "dry_subhumid"),
)
_ARIDITY_WETTEST_CLASS: Final = "humid"


class CovariateReadError(RuntimeError):
    """Raised when a governed covariate read cannot be satisfied as asked."""


def require_supported_schema_version(schema_version: str) -> str:
    """Reject an unknown feature schema version at ingress rather than at the database."""
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise CovariateReadError(
            f"unknown feature schema version {schema_version!r}; supported: {', '.join(SUPPORTED_SCHEMA_VERSIONS)}"
        )
    return schema_version


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    """One pinned position in the covariate vector."""

    feature_index: int
    feature_name: str
    feature_kind: str
    stream_key: str
    lag_days: int
    window_days: int


@dataclass(frozen=True, slots=True)
class CovariateVector:
    """One day's covariate vector for one cell, in pinned feature order, with its coverage."""

    cell_id: str
    observed_date: date
    as_of_time: datetime
    schema_version: str
    feature_names: tuple[str, ...]
    feature_values: tuple[float | None, ...]
    present_count: int
    max_data_available_at: datetime | None

    @property
    def is_complete(self) -> bool:
        """True when every pinned position carries a value; partial stays partial otherwise."""
        return self.present_count == len(self.feature_values)

    def value_of(self, feature_name: str) -> float | None:
        """Read one position by name; raises when the name is not in this schema version."""
        try:
            position = self.feature_names.index(feature_name)
        except ValueError as exc:
            raise CovariateReadError(f"{feature_name!r} is not a feature of {self.schema_version}") from exc
        return self.feature_values[position]

    def to_payload(self) -> list[dict[str, object]]:
        """The ordered, checksummable rendering stored on a training instance."""
        return [
            {"feature_index": index + 1, "feature_name": name, "feature_value": value}
            for index, (name, value) in enumerate(zip(self.feature_names, self.feature_values, strict=True))
        ]

    @property
    def checksum(self) -> str:
        """Digest over the pinned identity and the ordered values."""
        return sha256_digest(
            canonical_json(
                {
                    "digest_version": "agri_covariate_vector_v1",
                    "schema_version": self.schema_version,
                    "cell_id": self.cell_id,
                    "observed_date": self.observed_date.isoformat(),
                    "as_of_time": self.as_of_time.astimezone(UTC).isoformat(),
                    "features": self.to_payload(),
                }
            )
        )


@dataclass(frozen=True, slots=True)
class CoverageSummary:
    """What a covariate rebuild actually produced, per feature kind."""

    schema_version: str
    as_of_mode: str
    day_count: int
    complete_day_count: int
    emitted_row_count: int
    present_row_count: int
    present_by_feature_kind: Mapping[str, tuple[int, int]]

    def to_summary(self) -> dict[str, object]:
        """The receipt/report rendering."""
        return {
            "schema_version": self.schema_version,
            "as_of_mode": self.as_of_mode,
            "day_count": self.day_count,
            "complete_day_count": self.complete_day_count,
            "emitted_row_count": self.emitted_row_count,
            "present_row_count": self.present_row_count,
            "present_by_feature_kind": {
                kind: {"present": present, "emitted": emitted}
                for kind, (present, emitted) in sorted(self.present_by_feature_kind.items())
            },
        }


@dataclass(frozen=True, slots=True)
class SiteClimateTerms:
    """The envelope-comparable site climate a day's governed history supports.

    Every field is either derived from governed observations in the trailing
    `CLIMATE_WINDOW_DAYS`, or `None` because the window was not complete enough to state one.
    Nothing here is defaulted.
    """

    observed_date: date
    mean_annual_precipitation_mm: float | None
    mean_annual_temperature_c: float | None
    growing_season_frost_free_days: int | None
    aridity: str | None
    aridity_index: float | None
    contributing_day_count: int

    def to_payload(self) -> dict[str, object]:
        """The rendering recorded in an instance's envelope-match evidence."""
        return {
            "observed_date": self.observed_date.isoformat(),
            "mean_annual_precipitation_mm": self.mean_annual_precipitation_mm,
            "mean_annual_temperature_c": self.mean_annual_temperature_c,
            "growing_season_frost_free_days": self.growing_season_frost_free_days,
            "aridity": self.aridity,
            "aridity_index": self.aridity_index,
            "contributing_day_count": self.contributing_day_count,
        }


def _extraterrestrial_radiation_mm(latitude_degrees: float, day_of_year: int) -> float:
    """FAO-56 extraterrestrial radiation for a latitude and day of year, in mm/day equivalent."""
    latitude = math.radians(latitude_degrees)
    inverse_distance = 1.0 + 0.033 * math.cos(2.0 * math.pi * day_of_year / 365.0)
    declination = 0.409 * math.sin(2.0 * math.pi * day_of_year / 365.0 - 1.39)
    sunset_argument = -math.tan(latitude) * math.tan(declination)
    sunset_hour_angle = math.acos(max(-1.0, min(1.0, sunset_argument)))
    radiation = (
        (24.0 * 60.0 / math.pi)
        * _SOLAR_CONSTANT_MJ_PER_M2_MIN
        * inverse_distance
        * (
            sunset_hour_angle * math.sin(latitude) * math.sin(declination)
            + math.cos(latitude) * math.cos(declination) * math.sin(sunset_hour_angle)
        )
    )
    return radiation * _MJ_PER_M2_TO_MM


def hargreaves_reference_evapotranspiration_mm(
    *,
    latitude_degrees: float,
    day_of_year: int,
    temperature_min_c: float,
    temperature_max_c: float,
) -> float:
    """Hargreaves-Samani daily reference evapotranspiration in mm, from governed temperature only."""
    if temperature_max_c < temperature_min_c:
        raise CovariateReadError("temperature_max_c must not be below temperature_min_c")
    mean_temperature = (temperature_max_c + temperature_min_c) / 2.0
    radiation = _extraterrestrial_radiation_mm(latitude_degrees, day_of_year)
    return max(
        0.0,
        _HARGREAVES_COEFFICIENT
        * radiation
        * (mean_temperature + _HARGREAVES_TEMPERATURE_OFFSET)
        * math.sqrt(temperature_max_c - temperature_min_c),
    )


def classify_aridity(aridity_index: float) -> str:
    """Map a precipitation/reference-evapotranspiration ratio onto its UNEP (1997) class."""
    for threshold, label in _ARIDITY_BANDS:
        if aridity_index < threshold:
            return label
    return _ARIDITY_WETTEST_CLASS
