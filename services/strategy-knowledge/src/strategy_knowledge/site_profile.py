"""DESIGN.md section 10: site values -> land_use / region filters and soil / fire-phase boosts.

Soil, slope and burn conditions are soft boosts because tags are sparse (untagged is not unsuitable); see
AGENTS.md section "Site profile".
"""

import re
from dataclasses import dataclass, field
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field

from strategy_knowledge.vocabulary import Region

STEEP_SLOPE_PERCENT: Final = 30.0
ACIDIC_BELOW_PH: Final = 5.5
ALKALINE_ABOVE_PH: Final = 7.8
SANDY_COARSE_SAND_PERCENT: Final = 70.0
CLAY_HEAVY_CLAY_PERCENT: Final = 40.0
LOW_ORGANIC_CARBON_PERCENT: Final = 1.0
SALINE_SODIC_CONDUCTIVITY_DS_M: Final = 4.0
POST_FIRE_EMERGENCY_DAYS: Final = 60
POST_FIRE_RECOVERY_DAYS: Final = 1095
DROUGHTY_PRECIPITATION_MM: Final = 350.0

#: NLCD 2019 Anderson Level II codes -> land_use (DESIGN.md section 10 mapping).
NLCD_CODE_LAND_USE: Final[dict[int, tuple[str, ...]]] = {
    21: ("garden_residential", "wildland_urban_interface"),
    22: ("garden_residential", "wildland_urban_interface"),
    23: ("garden_residential", "wildland_urban_interface"),
    24: ("garden_residential", "wildland_urban_interface"),
    41: ("forest",),
    42: ("forest",),
    43: ("forest",),
    51: ("rangeland",),
    52: ("rangeland",),
    71: ("rangeland",),
    72: ("rangeland",),
    81: ("pasture",),
    82: ("cropland",),
    90: ("riparian",),
    95: ("riparian",),
}
#: NLCD class-name keywords, checked in order: wetland words first, because "Emergent Herbaceous Wetlands"
#: also contains "herbaceous".
NLCD_NAME_LAND_USE: Final[tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]] = (
    (("wetland",), ("riparian",)),
    (("crop",), ("cropland",)),
    (("pasture", "hay"), ("pasture",)),
    (("forest",), ("forest",)),
    (("developed",), ("garden_residential", "wildland_urban_interface")),
    (("shrub", "scrub", "grassland", "herbaceous", "sedge"), ("rangeland",)),
)
HIGH_SEVERITY_WORDS: Final = ("high",)
LOW_MODERATE_SEVERITY_WORDS: Final = ("low", "moderate")
#: MTBS thematic burn-severity classes: 2 = low, 3 = moderate, 4 = high.
MTBS_SEVERITY_CLASSES: Final[dict[int, str]] = {2: "low_moderate", 3: "low_moderate", 4: "high"}


class SiteProfile(BaseModel):
    """Site values the agent read from the warehouse tools; every field is optional."""

    model_config = ConfigDict(extra="forbid")

    slope_pct: Annotated[float | None, Field(description="Slope in percent; >= 30 boosts steep_slope.")] = None
    soil_ph: Annotated[float | None, Field(description="Soil pH; < 5.5 acidic, > 7.8 alkaline.")] = None
    sand_pct: Annotated[float | None, Field(description="Sand fraction in percent; >= 70 sandy_coarse.")] = None
    clay_pct: Annotated[float | None, Field(description="Clay fraction in percent; >= 40 clay_heavy.")] = None
    soil_organic_carbon_pct: Annotated[
        float | None,
        Field(description="Soil organic carbon in percent; < 1.0 low_organic_matter."),
    ] = None
    electrical_conductivity_ds_m: Annotated[
        float | None,
        Field(description="Electrical conductivity in dS/m; >= 4 saline_sodic."),
    ] = None
    burn_severity: Annotated[
        str | int | None,
        Field(description="'high', 'moderate', 'low' (or MTBS class 2-4); high also boosts hydrophobic."),
    ] = None
    days_since_fire: Annotated[
        int | None,
        Field(description="Days since the fire; <= 60 post_fire_emergency, <= 1095 post_fire_recovery."),
    ] = None
    annual_precip_mm: Annotated[float | None, Field(description="Annual precipitation; < 350 mm droughty.")] = None
    land_cover: Annotated[
        str | int | None,
        Field(description="NLCD class name ('Cultivated Crops') or code (82); mapped to land_use filters."),
    ] = None
    region: Annotated[Region | None, Field(description="Region enum value, passed through as a filter.")] = None


@dataclass
class SiteDerivation:
    """What a site profile turned into: hard filters, soft boosts, and a note per rule that fired."""

    land_use: list[str] = field(default_factory=list)
    region: list[str] = field(default_factory=list)
    soil_condition_boosts: list[str] = field(default_factory=list)
    fire_phase_boosts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_response(self) -> dict[str, Any]:
        """The echo every search response carries so the agent can see and adjust the derivation."""
        return {
            "filters": {"land_use": self.land_use, "region": self.region},
            "boosts": {"soil_conditions": self.soil_condition_boosts, "fire_phase": self.fire_phase_boosts},
            "notes": self.notes,
        }


def land_use_for_cover(land_cover: str | int) -> tuple[str, ...]:
    """Map an NLCD class code or name to land_use values; unknown classes map to nothing."""
    text = str(land_cover).strip().lower()
    if text.isdigit():
        return NLCD_CODE_LAND_USE.get(int(text), ())
    words = re.sub(r"[^a-z]+", " ", text)
    for keywords, land_uses in NLCD_NAME_LAND_USE:
        if any(keyword in words for keyword in keywords):
            return land_uses
    return ()


def severity_class(burn_severity: str | int) -> str | None:
    """Normalise a burn severity to 'high', 'low_moderate' or None (unburned / unknown)."""
    if isinstance(burn_severity, int):
        return MTBS_SEVERITY_CLASSES.get(burn_severity)
    text = burn_severity.strip().lower()
    if text.isdigit():
        return MTBS_SEVERITY_CLASSES.get(int(text))
    if "unburned" in text:
        return None
    if any(word in text for word in HIGH_SEVERITY_WORDS):
        return "high"
    if any(word in text for word in LOW_MODERATE_SEVERITY_WORDS):
        return "low_moderate"
    return None


def _soil_boosts(profile: SiteProfile) -> list[tuple[str, str]]:
    """(soil condition, reason) pairs for the numeric soil and terrain rules."""
    rules = [
        (profile.slope_pct, lambda value: value >= STEEP_SLOPE_PERCENT, "steep_slope", "slope_pct >= 30"),
        (profile.soil_ph, lambda value: value < ACIDIC_BELOW_PH, "acidic", "soil_ph < 5.5"),
        (profile.soil_ph, lambda value: value > ALKALINE_ABOVE_PH, "alkaline", "soil_ph > 7.8"),
        (profile.sand_pct, lambda value: value >= SANDY_COARSE_SAND_PERCENT, "sandy_coarse", "sand_pct >= 70"),
        (profile.clay_pct, lambda value: value >= CLAY_HEAVY_CLAY_PERCENT, "clay_heavy", "clay_pct >= 40"),
        (
            profile.soil_organic_carbon_pct,
            lambda value: value < LOW_ORGANIC_CARBON_PERCENT,
            "low_organic_matter",
            "soil_organic_carbon_pct < 1.0",
        ),
        (
            profile.electrical_conductivity_ds_m,
            lambda value: value >= SALINE_SODIC_CONDUCTIVITY_DS_M,
            "saline_sodic",
            "electrical_conductivity_ds_m >= 4",
        ),
        (
            profile.annual_precip_mm,
            lambda value: value < DROUGHTY_PRECIPITATION_MM,
            "droughty",
            "annual_precip_mm < 350",
        ),
    ]
    return [(condition, reason) for value, rule, condition, reason in rules if value is not None and rule(value)]


def derive(profile: SiteProfile | None) -> SiteDerivation:
    """Apply every DESIGN.md section 10 rule to a site profile."""
    derivation = SiteDerivation()
    if profile is None:
        return derivation
    for condition, reason in _soil_boosts(profile):
        derivation.soil_condition_boosts.append(condition)
        derivation.notes.append(f"{reason} -> boost {condition}")
    if profile.burn_severity is not None:
        severity = severity_class(profile.burn_severity)
        if severity == "high":
            derivation.soil_condition_boosts.extend(["burned_high_severity", "hydrophobic"])
            derivation.notes.append("burn_severity high -> boost burned_high_severity, hydrophobic")
        elif severity == "low_moderate":
            derivation.soil_condition_boosts.append("burned_low_moderate_severity")
            derivation.notes.append("burn_severity low/moderate -> boost burned_low_moderate_severity")
        else:
            derivation.notes.append(f"burn_severity {profile.burn_severity!r} not recognised; no boost")
    if profile.days_since_fire is not None and profile.days_since_fire >= 0:
        if profile.days_since_fire <= POST_FIRE_EMERGENCY_DAYS:
            derivation.fire_phase_boosts.append("post_fire_emergency")
            derivation.notes.append("days_since_fire <= 60 -> boost post_fire_emergency")
        elif profile.days_since_fire <= POST_FIRE_RECOVERY_DAYS:
            derivation.fire_phase_boosts.append("post_fire_recovery")
            derivation.notes.append("days_since_fire <= 1095 -> boost post_fire_recovery")
    if profile.land_cover is not None:
        land_uses = land_use_for_cover(profile.land_cover)
        derivation.land_use.extend(land_uses)
        derivation.notes.append(
            f"land_cover {profile.land_cover!r} -> land_use {list(land_uses)}"
            if land_uses
            else f"land_cover {profile.land_cover!r} has no land_use mapping; no land_use filter",
        )
    if profile.region is not None:
        derivation.region.append(profile.region)
        derivation.notes.append(f"region {profile.region} -> region filter")
    return derivation
