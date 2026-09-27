"""The pure `site-brief/1` builder: golden parity, integer rounding, texture triangle and the C3 site facts.

The golden fixture is shared with the web lane (`src/__tests__/services/site-brief.test.ts`); both compare
`canonical(build(inputs)) == canonical(expected)`. Rationale: agent/AGENTS.md, "Site brief".
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Final

import pytest

from agri_data_service.agent import strategy_knowledge
from agri_data_service.agent.site_brief import (
    BRIEF_VERSION,
    MAX_LITERATURE_SEED_CHARACTERS,
    SOIL_UNITS,
    UNAVAILABLE_REASONS,
    UNCLASSIFIED_TEXTURE,
    build_site_brief,
    canonical_json,
    literature_seed,
    normalised_texture,
    reaction_class,
    round_half_up,
    site_facts_from_brief,
    soc_band,
    texture_class,
)
from agri_data_service.pipeline.direct.soil_properties.products import PROPERTY_UNITS

GOLDEN_PATH: Final = Path(__file__).resolve().parent / "fixtures" / "site_brief_golden.json"
GOLDEN: Final[dict[str, Any]] = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
CASES: Final[list[dict[str, Any]]] = GOLDEN["cases"]
EXPECTED_CASE_COUNT: Final = 12
WORKED_EXAMPLE: Final = CASES[0]
MODEL_ESTIMATE_LABEL: Final = "SoilGrids v2.0 250 m model estimate"
#: Words C9 forbids for a SoilGrids value ("never call a SoilGrids value a measurement... measured
#: or observed"). "sampled" is deliberately NOT here: CONTRACT C5.3 pins the soil label's own text
#: as "...model estimate, sampled at the centre of a ~500 m cell...", describing how the ESTIMATE
#: was read off the raster grid, not a claim that the site was physically measured or observed.
MEASUREMENT_WORDS: Final = ("measured", "measurement", "observed")
TENTHS_PER_WHOLE: Final = 1000
PERCENT_GRID_STEP: Final = 10


def _case(name: str) -> dict[str, Any]:
    return next(case for case in CASES if case["name"] == name)


# --- Golden parity ---------------------------------------------------------------------


def test_the_golden_fixture_pins_twelve_named_cases() -> None:
    assert GOLDEN["fixture_version"] == "site-brief-golden/1"
    assert len(CASES) == EXPECTED_CASE_COUNT
    assert len({case["name"] for case in CASES}) == EXPECTED_CASE_COUNT


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_the_builder_reproduces_every_golden_case_canonically(case: dict[str, Any]) -> None:
    assert canonical_json(build_site_brief(case["inputs"])) == canonical_json(case["expected"])


def test_case_one_is_the_contract_worked_example() -> None:
    """CONTRACT C5.2's worked example, number by number."""
    brief = build_site_brief(WORKED_EXAMPLE["inputs"])
    topsoil = brief["soil"]["topsoil_0_30cm"]
    assert canonical_json(topsoil) == canonical_json(
        {
            "ph": 5.8,
            "soc_pct": 2,
            "clay_pct": 19.7,
            "sand_pct": 38.4,
            "silt_pct": 42,
            "cfvo_vol_pct": 10.3,
            "bdod_g_cm3": 1.28,
            "texture_class": "loam",
            "reaction_class": "moderately acid",
            "soc_band": "high organic carbon",
            "label": "SoilGrids v2.0 250 m model estimate, 0-30 cm (thickness-weighted)",
        }
    )
    assert brief["fire"]["days_since_fire"] == 412  # noqa: PLR2004 - C5.2: 2026-09-27 minus 2025-08-11.
    assert brief["literature_seed"] == (
        "moderately acid loam topsoil; high organic carbon; burned 2025, high severity; moderate drought; "
        "grassland/pasture"
    )


def test_the_builder_is_pure_and_leaves_its_inputs_untouched() -> None:
    inputs = copy.deepcopy(WORKED_EXAMPLE["inputs"])
    first = canonical_json(build_site_brief(inputs))
    assert inputs == WORKED_EXAMPLE["inputs"]
    assert canonical_json(build_site_brief(inputs)) == first


def test_every_unavailable_section_names_a_reason_from_the_one_vocabulary() -> None:
    for case in CASES:
        brief = build_site_brief(case["inputs"])
        for section in ("soil", "fire", "drought", "weather", "land_cover"):
            if brief[section]["state"] == "unavailable":
                assert brief[section]["reason"] in UNAVAILABLE_REASONS, (case["name"], section)


def test_a_soil_no_cell_answer_carries_its_radius() -> None:
    soil = build_site_brief(_case("urban_no_cell_within_radius")["inputs"])["soil"]
    assert soil == {"state": "unavailable", "reason": "no_cell_within_radius", "radius_m": 1000}


def test_an_unknown_reason_is_refused_rather_than_passed_through() -> None:
    inputs = copy.deepcopy(WORKED_EXAMPLE["inputs"])
    inputs["soil"] = {"state": "unavailable", "reason": "looked_fine_to_me"}
    with pytest.raises(ValueError, match="unknown unavailable reason"):
        build_site_brief(inputs)


def test_every_section_unavailable_yields_no_descriptor_and_an_empty_seed() -> None:
    brief = build_site_brief(_case("every_section_unavailable")["inputs"])
    assert brief["descriptors"] == []
    assert brief["literature_seed"] == ""
    assert brief["brief_version"] == BRIEF_VERSION


# --- Labelling (O2): soil is a model estimate, everywhere -------------------------------


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_soil_text_is_labelled_a_model_estimate_and_never_a_measurement(case: dict[str, Any]) -> None:
    brief = build_site_brief(case["inputs"])
    soil = brief["soil"]
    if soil["state"] != "available":
        return
    assert soil["basis"] == "model_estimate"
    assert soil["label"].startswith(MODEL_ESTIMATE_LABEL)
    assert soil["topsoil_0_30cm"]["label"].startswith(MODEL_ESTIMATE_LABEL)
    soil_texts = [soil["label"], soil["topsoil_0_30cm"]["label"], *(item["text"] for item in brief["descriptors"][:2])]
    soil_text = " ".join(soil_texts)
    assert not any(word in soil_text.lower() for word in MEASUREMENT_WORDS)
    assert all("SoilGrids model estimate" in descriptor["text"] for descriptor in brief["descriptors"][:2])


# --- Integer arithmetic (C5.2) ----------------------------------------------------------


@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    [
        pytest.param(1725, 30, 58, id="contract-ph-tie-rounds-up"),
        pytest.param(1665, 30, 56, id="ph-tie-5.55"),
        pytest.param(3915, 30, 131, id="bdod-tie-130.5"),
        pytest.param(12450, 100, 125, id="distance-tie-12.45-km"),
        pytest.param(605, 10, 61, id="fraction-tie-60.5"),
        pytest.param(1724, 30, 57, id="just-below-a-tie"),
        pytest.param(0, 30, 0, id="zero"),
    ],
)
def test_round_half_up_by_integer_division(numerator: int, denominator: int, expected: int) -> None:
    assert round_half_up(numerator, denominator) == expected


@pytest.mark.parametrize(("numerator", "denominator"), [(-1, 30), (5, 0)])
def test_round_half_up_refuses_outside_its_domain(numerator: int, denominator: int) -> None:
    with pytest.raises(ValueError, match="round_half_up"):
        round_half_up(numerator, denominator)


def test_the_tie_case_rounds_every_tie_up() -> None:
    brief = build_site_brief(_case("half_up_ties_ph_bdod_distance_fraction")["inputs"])
    assert brief["soil"]["topsoil_0_30cm"]["ph"] == 5.6  # noqa: PLR2004 - N=1665, a .5 tie.
    assert brief["soil"]["topsoil_0_30cm"]["bdod_g_cm3"] == 1.31  # noqa: PLR2004 - N=3915, a .5 tie.
    assert brief["weather"]["label"].endswith("12.5 km away")
    assert brief["land_cover"]["fraction_pct"] == 61  # noqa: PLR2004 - 605 permille, a .5 tie.


# --- Classes ------------------------------------------------------------------------------


def test_the_texture_triangle_classifies_every_point_of_a_one_percent_grid() -> None:
    """USDA soil texture triangle: no (sand, silt, clay) on the 1% grid reaches `unclassified`."""
    unclassified = [
        (sand, TENTHS_PER_WHOLE - sand - clay, clay)
        for clay in range(0, TENTHS_PER_WHOLE + 1, PERCENT_GRID_STEP)
        for sand in range(0, TENTHS_PER_WHOLE - clay + 1, PERCENT_GRID_STEP)
        if texture_class(sand, TENTHS_PER_WHOLE - sand - clay, clay) == UNCLASSIFIED_TEXTURE
    ]
    assert unclassified == []


@pytest.mark.parametrize(
    ("sand", "silt", "clay", "expected"),
    [
        pytest.param(384, 419, 197, "loam", id="contract-worked-example"),
        pytest.param(300, 430, 270, "clay loam", id="clay-270-is-clay-loam"),
        pytest.param(300, 431, 269, "loam", id="clay-269-is-loam"),
        pytest.param(250, 600, 150, "silt loam", id="silt-loam"),
        pytest.param(920, 50, 30, "sand", id="sand"),
        pytest.param(50, 850, 100, "silt", id="silt"),
        pytest.param(100, 200, 700, "clay", id="clay"),
    ],
)
def test_texture_boundaries(sand: int, silt: int, clay: int, expected: str) -> None:
    assert texture_class(sand, silt, clay) == expected


def test_texture_normalises_to_one_thousand_tenths() -> None:
    assert normalised_texture(197, 384, 420) == (384, 419, 197)
    assert normalised_texture(0, 0, 0) is None


@pytest.mark.parametrize(
    ("ph_tenths", "expected"),
    [
        (34, "ultra acid"),
        (35, "extremely acid"),
        (60, "moderately acid"),
        (61, "slightly acid"),
        (73, "neutral"),
        (84, "moderately alkaline"),
        (91, "very strongly alkaline"),
    ],
)
def test_reaction_classes(ph_tenths: int, expected: str) -> None:
    assert reaction_class(ph_tenths) == expected


@pytest.mark.parametrize(
    ("tenths", "expected"),
    [(9, "low"), (10, "moderate"), (19, "moderate"), (20, "high"), (39, "high"), (40, "very high")],
)
def test_soc_bands(tenths: int, expected: str) -> None:
    assert soc_band(tenths) == expected


def test_the_unit_table_mirrors_the_lane_products() -> None:
    assert {code: (key, divisor) for code, key, divisor in SOIL_UNITS} == {
        code: (unit.output_key, unit.divisor) for code, unit in PROPERTY_UNITS.items()
    }


# --- literature_seed (C5.4) ---------------------------------------------------------------


def test_the_seed_is_cut_at_the_last_space_inside_the_limit() -> None:
    long_seed = "word " * 200
    descriptors = [{"text": "t", "seed": long_seed.strip()}]
    seed = literature_seed(descriptors)
    assert len(seed) <= MAX_LITERATURE_SEED_CHARACTERS
    assert not seed.endswith(" ")
    assert seed.split(" ")[-1] == "word"


# --- Canonical JSON (C5.5) ----------------------------------------------------------------


def test_canonical_json_sorts_keys_and_renders_integral_floats_as_integers() -> None:
    assert canonical_json({"b": 2.0, "a": [1.5, True, None, 'x"y']}) == '{"a":[1.5,true,null,"x\\"y"],"b":2}'


def test_canonical_json_refuses_a_non_finite_number() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        canonical_json(float("nan"))


# --- Site facts for the literature context (C3) ---------------------------------------------


def test_the_brief_maps_onto_c3_site_facts_with_provenance() -> None:
    brief = build_site_brief(WORKED_EXAMPLE["inputs"])
    facts, provenance, query = site_facts_from_brief(brief)
    assert facts == {
        "soil_ph": 5.8,
        "soil_organic_carbon_pct": 2.0,
        "sand_pct": 38.4,
        "clay_pct": 19.7,
        "days_since_fire": 412,
        "burn_severity": "high",
        "land_cover": "Grassland/Pasture",
    }
    assert set(provenance) == set(facts)
    assert provenance["soil_ph"]["basis"] == "model_estimate"
    assert provenance["soil_ph"]["label"] == (
        "SoilGrids v2.0 250 m model estimate, 0-30 cm (thickness-weighted), cell centre 140 m away"
    )
    assert provenance["days_since_fire"]["basis"] == "measured"
    assert provenance["land_cover"]["basis"] == "classified_remote_sensing"
    assert query == brief["literature_seed"]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_every_golden_brief_yields_valid_server_context(case: dict[str, Any]) -> None:
    """The C3 objects built from any brief validate against the bridge's own models."""
    facts, provenance, query = site_facts_from_brief(build_site_brief(case["inputs"]))
    context = strategy_knowledge.ServerContext.model_validate(
        {
            "point": {"longitude": -116.2, "latitude": 43.6},
            "site_facts": facts or None,
            "site_facts_provenance": provenance,
            "site_brief_query": query,
        }
    )
    assert set(context.site_facts_provenance or {}) == set(facts)
    assert (query is None) == (case["name"] == "every_section_unavailable")


def test_an_unavailable_soil_section_contributes_no_soil_fact() -> None:
    facts, provenance, _query = site_facts_from_brief(build_site_brief(_case("soil_reads_disabled")["inputs"]))
    assert not {"soil_ph", "soil_organic_carbon_pct", "sand_pct", "clay_pct"} & set(facts)
    assert not {"soil_ph", "soil_organic_carbon_pct", "sand_pct", "clay_pct"} & set(provenance)
