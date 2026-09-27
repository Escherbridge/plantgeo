"""DESIGN.md section 10 rules: which site values become filters, which become boosts, and the echo."""

import pytest

from strategy_knowledge.site_profile import SiteProfile, derive, land_use_for_cover, severity_class


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"slope_pct": 30}, ["steep_slope"]),
        ({"slope_pct": 29.9}, []),
        ({"soil_ph": 5.4}, ["acidic"]),
        ({"soil_ph": 5.5}, []),
        ({"soil_ph": 7.9}, ["alkaline"]),
        ({"soil_ph": 7.8}, []),
        ({"sand_pct": 70}, ["sandy_coarse"]),
        ({"clay_pct": 40}, ["clay_heavy"]),
        ({"clay_pct": 39}, []),
        ({"soil_organic_carbon_pct": 0.9}, ["low_organic_matter"]),
        ({"soil_organic_carbon_pct": 1.0}, []),
        ({"electrical_conductivity_ds_m": 4}, ["saline_sodic"]),
        ({"annual_precip_mm": 349}, ["droughty"]),
        ({"annual_precip_mm": 350}, []),
        ({"burn_severity": "High"}, ["burned_high_severity", "hydrophobic"]),
        ({"burn_severity": "moderate"}, ["burned_low_moderate_severity"]),
        ({"burn_severity": "low"}, ["burned_low_moderate_severity"]),
        ({"burn_severity": 4}, ["burned_high_severity", "hydrophobic"]),
        ({"burn_severity": "unburned to low"}, []),
    ],
)
def test_soil_boosts(values: dict[str, object], expected: list[str]) -> None:
    derivation = derive(SiteProfile.model_validate(values))
    assert derivation.soil_condition_boosts == expected
    assert derivation.land_use == []
    assert derivation.region == []


@pytest.mark.parametrize(
    ("days", "expected"),
    [(0, ["post_fire_emergency"]), (60, ["post_fire_emergency"]), (61, ["post_fire_recovery"]),
     (1095, ["post_fire_recovery"]), (1096, []), (-5, [])],
)  # fmt: skip
def test_days_since_fire_boosts_phase(days: int, expected: list[str]) -> None:
    assert derive(SiteProfile(days_since_fire=days)).fire_phase_boosts == expected


@pytest.mark.parametrize(
    ("land_cover", "expected"),
    [
        ("Cultivated Crops", ("cropland",)),
        (82, ("cropland",)),
        ("81", ("pasture",)),
        ("Hay/Pasture", ("pasture",)),
        ("Shrub/Scrub", ("rangeland",)),
        ("Grassland/Herbaceous", ("rangeland",)),
        ("Evergreen Forest", ("forest",)),
        (43, ("forest",)),
        ("Developed, Low Intensity", ("garden_residential", "wildland_urban_interface")),
        ("Woody Wetlands", ("riparian",)),
        ("Emergent Herbaceous Wetlands", ("riparian",)),
        ("Open Water", ()),
        (31, ()),
    ],
)
def test_land_cover_maps_to_land_use(land_cover: str | int, expected: tuple[str, ...]) -> None:
    assert land_use_for_cover(land_cover) == expected


def test_land_cover_and_region_become_filters_with_notes() -> None:
    derivation = derive(SiteProfile(land_cover="Deciduous Forest", region="us_midwest", slope_pct=45))
    assert derivation.land_use == ["forest"]
    assert derivation.region == ["us_midwest"]
    assert derivation.soil_condition_boosts == ["steep_slope"]
    echo = derivation.as_response()
    assert echo["filters"] == {"land_use": ["forest"], "region": ["us_midwest"]}
    assert echo["boosts"]["soil_conditions"] == ["steep_slope"]
    assert any("slope_pct >= 30" in note for note in echo["notes"])


def test_unmapped_land_cover_is_noted_not_filtered() -> None:
    derivation = derive(SiteProfile(land_cover="Barren Land"))
    assert derivation.land_use == []
    assert any("no land_use mapping" in note for note in derivation.notes)


def test_no_profile_derives_nothing() -> None:
    derivation = derive(None)
    assert derivation.as_response() == {
        "filters": {"land_use": [], "region": []},
        "boosts": {"soil_conditions": [], "fire_phase": []},
        "notes": [],
    }


def test_severity_class_accepts_codes_and_words() -> None:
    assert severity_class(3) == "low_moderate"
    assert severity_class("4") == "high"
    assert severity_class("increased greenness") is None


def test_unknown_region_is_rejected() -> None:
    with pytest.raises(ValueError, match="region"):
        SiteProfile.model_validate({"region": "atlantis"})
