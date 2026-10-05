"""Flows: each owner rule that PRODUCTION adds over V0_FROZEN, observed on real fixture cells through both seams."""

from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import polars as pl
import pytest

from agri_data_service.warehouse.plant_suitability.axes import AXIS_NAMES
from agri_data_service.warehouse.plant_suitability.config import (
    INTRODUCED_FLAG_PLAIN,
    PRODUCTION,
    V0_FROZEN,
    V0_PICK_DEFINITION,
    RuleConfig,
)
from agri_data_service.warehouse.plant_suitability.engine import (
    ENGINE_VERSION,
    METADATA_ADMITTED_SOURCES,
    METADATA_ATTRIBUTIONS,
    METADATA_ENGINE_VERSION,
    METADATA_EXCLUDED_SOURCES,
    METADATA_INPUTS_SHA256,
    METADATA_KEYS,
    METADATA_PICK_DEFINITION,
    METADATA_RULE_CONFIG,
    METADATA_SITE_INPUTS,
    METADATA_WITHHELD_SITE_INPUTS,
    candidates_for_cell,
    evaluate_cells,
    prepare,
)
from agri_data_service.warehouse.plant_suitability.labels import (
    AXIS_LABELS,
    CONFIRMED_FROST_FREE,
    FIRE_LABEL_PREFIX,
    IN_REGION_NO,
    IN_REGION_YES,
    NO_FIRE_CLAIM,
    SITE_FROST_FREE_MISSING,
    STRATIFICATION_PREFIX,
    UNCERTAIN_FROST_FREE,
    UNCHECKED_PREFIX,
    UNKNOWN_FROST_FREE,
    FireTextAllowList,
    assert_no_fire_claims,
    find_fire_claims,
)
from agri_data_service.warehouse.plant_suitability.licences import (
    ALL_RIGHTS_RESERVED,
    CC0,
    CC_BY,
    CC_BY_NC,
    CC_BY_NC_SA,
    CC_BY_ND,
    COMMERCIAL_USE_LICENCES,
    COPERNICUS_DEM,
    PRISM_TERMS_OF_USE,
    PUBLIC_DOMAIN,
    UNRECORDED,
    US_GOVERNMENT_WORK,
    SourceCredit,
)
from agri_data_service.warehouse.plant_suitability.schemas import (
    CELL_RECOMMENDATIONS_SCHEMA,
    GUILDS,
    SITE_INPUT_GROUPS,
)
from agri_data_service.warehouse.plant_suitability.site import SiteInputProvenance
from tests.plant_suitability.support import PRODUCTION_ON_FIXTURE, candidate, cell_row, served_labels

if TYPE_CHECKING:
    from tests.plant_suitability.support import SuitabilityFixture

SOIL_AXES = ("salinity", "calcareous", "root_depth")
CPS_394 = "(CPS 394)"
LEYMUS = "Leymus triticoides"
ODFW_SALINE_MIX_TAG = "ODFW 2017 p.7 [neighbour]"
# Leymus's in-region, unstratified Boise row, and the ODFW row carrying its saline/alkali qualifier (both unrecorded).
BLM_BOISE_DISTRICT_SOURCE = "blm_boise_nfesrp_2005"
ODFW_SALINE_MIX_SOURCE = "odfw_2017"
TN_2A_NEIGHBOUR_TAG = re.compile(r"NRCS TN PM-2A \(2017\) p\.\d+(?:-\d+)? \[neighbour\]")
FIRE_NAMED_SOURCE_ID, FIRE_NAMED_SOURCE = "blm_range_2026", "BLM Range Fire 2026"
# The PRISM data's access date (prototype site_conditions/climate/SOURCES.md) in the form PRISM's terms ask for.
PRISM_ACCESSED = "2026-09-26"
PRISM_ATTRIBUTION = "PRISM Group, Oregon State University, https://prism.oregonstate.edu, accessed 26 Sep 2026."
# The CC BY works the fixture's groups draw on, in the credit form served (ERA5 is the PRISM dry year's spread).
CC_BY_TERMS = "under CC BY 4.0, https://creativecommons.org/licenses/by/4.0/"
ERA5_CREDIT = f"ERA5 (Copernicus Climate Change Service), Open-Meteo.com, https://open-meteo.com, {CC_BY_TERMS}"
ERA5_LAND_CREDIT = (
    f"ERA5-Land (Copernicus Climate Change Service), Open-Meteo.com, https://open-meteo.com, {CC_BY_TERMS}"
)
SOILGRIDS_CREDIT = f"SoilGrids 2.0, ISRIC - World Soil Information, https://soilgrids.org, {CC_BY_TERMS}"
# The notice the Copernicus DEM licence prescribes for adapted GLO-90 data (the record low is lapse-adjusted to it).
COPERNICUS_DEM_NOTICE = (
    "produced using Copernicus WorldDEM-90 \u00a9 DLR e.V. 2010-2014 and \u00a9 Airbus Defence and Space GmbH "
    "2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved"
)
# A later pull than the prototype's, declared as production's own (not the fixture).
PRODUCTION_PULL, PRODUCTION_PULL_ATTRIBUTION = (
    "2026-10-03",
    "PRISM Group, Oregon State University, https://prism.oregonstate.edu, accessed 3 Oct 2026.",
)
# PRODUCTION's gate without the DEM licence, with the cold group optional so its withholding is served, not refused.
PRODUCTION_WITHOUT_DEM = replace(PRODUCTION_ON_FIXTURE, permitted_licences=COMMERCIAL_USE_LICENCES - {COPERNICUS_DEM})
COLD_OPTIONAL_WITHOUT_DEM = replace(
    PRODUCTION_WITHOUT_DEM, required_site_input_groups=PRODUCTION_ON_FIXTURE.required_site_input_groups - {"cold"}
)
# PRODUCTION's gate without CC BY: the precipitation group's ERA5 credit then withholds it although PRISM is permitted.
PRODUCTION_WITHOUT_CC_BY = replace(PRODUCTION_ON_FIXTURE, permitted_licences=COMMERCIAL_USE_LICENCES - {CC_BY})
# For subjects that come from licence-unrecorded guides, which real PRODUCTION drops at load.
PRODUCTION_WITHOUT_LICENCE_GATE = replace(PRODUCTION_ON_FIXTURE, permitted_licences=None)
# One changed value per RuleConfig field; the fingerprint flow fails if a field has no entry here.
FINGERPRINT_VARIANTS = {
    "name": "production_preview",
    "in_region_readings": frozenset(),
    "range_wide_origin_is_not_state_claim": False,
    "inherit_stratification": False,
    "null_restriction_depth_is_unknown": False,
    "introduced_flag_text": MappingProxyType(dict.fromkeys(GUILDS, INTRODUCED_FLAG_PLAIN)),
    "pick_definition": V0_PICK_DEFINITION,
    "permitted_licences": None,
    "required_site_input_groups": frozenset(),
    "label_unchecked_axes": False,
    "declared_empty_exclusion_states": frozenset({"OR"}),
    "dated_attribution_accessed_after": None,
    "allow_fixture_site_inputs": True,
}
# The review's round-2 bypasses (p03): invisible and combining characters, lookalike letters, then phrasings.
EGRESS_REFUSED = (
    "fi\u200ere-resistant hedge",
    "red\u200fuces wildfire",
    "fi\u202are-resistant hedge",
    "fi\u202e\u202cre-resistant hedge",
    "fi\u2066\u2069re-resistant hedge",
    "fi\u034fre-resistant hedge",
    "fi\ufe0fre-resistant hedge",
    "fi\u2062re-resistant hedge",
    "fi\u180ere-resistant hedge",
    "fi\U000e0020re-resistant hedge",
    "fi\u3164re-resistant hedge",
    "fi\u0307re-resistant hedge",
    "fire\u0301-resistant hedge",
    "fir\u0336e-resistant hedge",
    "planted to sl\u0585w wildfire",
    "l\u0585w flammability shrub",
    "less fl\u0251mmable hedge",
    "f\u0131re-resistant hedge",
    "st\u2c9fps wildfire",
    "f\u04cfre-resistant hedge",
    "f\u04cfame-retardant hedge",
    "fire\uff0dresistant hedge",
    "fire\u2027resistant hedge",
    "fire\ufe63resistant hedge",
    "this hedge resists the spread of fire",
    "a living fire barrier",
    "acts as a barrier to wildfire",
    "a green firewall around homes",
    "a slow-burning groundcover",
    "succulent leaves that burn slowly",
    "reduces burn severity near structures",
    "less fire-prone than cheatgrass",
    "moist foliage that is hard to ignite",
    "non-combustible zone planting",
    "a hedge that fights wildfire",
    "hinders fire spread",
    "impedes fire movement",
    "deters wildfire",
    "minimises wildfire damage",
    "lessens fire intensity",
    "fire-retardant hedge",
    "a buffer against wildfire",
    "keeps flames at bay",
    "a fire-safe hedge",
    "wildfire-wise",
    "fire-hardy landscaping that protects homes",
    "protects your house, barn, sheds and other outbuildings from wildfire",
    "container-grown willow;Sparks ceanothus",
    "sources: NRCS OR/WA guide 2000 Fire section p.3",
    # Round 3 (R2 p03): compounds, inflections and synonyms no whole-word list held.
    "reduces brushfire risk around homes",
    "stops grassfires",
    "a firescaping shrub",
    "a living fireline",
    "a green firestop",
    "noted for fireresistance",
    "unburnable foliage",
    "nonflammable foliage",
    "keeps flammables down",
    "rarely ignited",
    "reduceswildfire risk",
    "self-extinguishing foliage",
    "blaze-resistant hedge",
    "halts conflagration spread",
    "will not smoulder",
    "scorch-resistant groundcover",
    "pyro-resistant shrub",
    "embers seldom catch",
    # Letters spelled out, digits and signs for letters, and letters or symbols beyond Latin-1.
    "f i r e - r e s i s t a n t hedge",
    "f.i.r.e.-resistant hedge",
    "f1re-resistant hedge",
    "f|re-resistant hedge",
    "\ua730\u026a\u0280\u1d07-resistant hedge",
    "\u0192ire-resistant hedge",
    "fir\u03b5-resistant hedge",
    "fi\u027de-resistant hedge",
    "reduces fi\u0433e risk",
    "\U0001f1eb\U0001f1ee\U0001f1f7\U0001f1ea-resistant hedge",
    # Round 4 (R3): currency and Latin-1 signs for letters, punctuation inside a word, a mask sign for a letter.
    "fir\u20ac-resistant hedge",
    "fir\u20a4-resistant hedge",
    "fir\u00a3-resistant",
    "f\u00a1re resistant",
    "f\u00a6re resistant",
    "fi\u00b7re-resistant",
    "fi.re-resistant hedge",
    "fi-re resistant",
    "fi\u2024re",
    "fir'e resistant",
    "f*re-resistant",
)
# Real plant names holding a fire-family stem, and the fire-named citation PRODUCTION admits (BLM, federal).
EGRESS_SERVED = (
    "fireweed",
    "Chamerion angustifolium (fireweed)",
    "firewheel",
    "Gaillardia pulchella (firewheel)",
    "small burnet",
    "Sanguisorba minor (small burnet)",
    "burnet",
    "Great Burnet",
    "burningbush",
    "firethorn",
    # Plant names holding a fire-family stem, as the fixture and prototype serve them (the exemption list).
    "Penstemon eatonii (firecracker penstemon)",
    "Mentzelia laevicaulis (smoothstem blazingstar)",
    "Liatris punctata (dotted blazing star)",
    "Viburnum edule (squashberry)",
    "Ranunculus flammula",
    "Salicornia (swampfire)",
    "Agropyron cristatum;Pyrola asarifolia;Diospyros virginiana",
    # Round 4 (R3): real taxa the reviewer named (PLANTS / Flora of North America common names).
    "Erechtites hieraciifolius (American burnweed)",
    "Erechtites minimus (coast burnweed)",
    "Euonymus alatus (burning bush)",
    "Salix pyrolifolia",
    "Phemeranthus spinescens (spiny flameflower)",
    # Real texts the joined and masked readings must still serve: an engine-joined top three (";" is never joined at
    # egress), an exempt genus before a slash, a footnote mark.
    "grand fir;European alder",
    "Viburnum/Cornus hedge",
    "Erigeron compositus Cutleaf daisy*",
    # "ember" refuses only at a word's start; Latin-1 letters and typographic quotes are served.
    "seeded in September by members who remember",
    "sheep\u2019s fescue",
    "\u00c6gilops; Mu\u0308hlenbergia",
    f"native (per {FIRE_NAMED_SOURCE})",
    f"sources: {FIRE_NAMED_SOURCE} p.1 + NRCS ID TN PM-16 (2009) p.4",
    f"{FIRE_LABEL_PREFIX}Yes",
    NO_FIRE_CLAIM,
    "post_fire_restoration",
)


@pytest.fixture(scope="module")
def production_allow_list(suitability: SuitabilityFixture) -> FireTextAllowList:
    """The egress allow-list of a PRODUCTION engine over the fixture's guide rows."""
    return suitability.prepared(PRODUCTION_ON_FIXTURE).allow_list


def refused_at_egress(text: str, allow_list: FireTextAllowList) -> bool:
    """True when the egress guard refuses a served table holding the text."""
    try:
        assert_no_fire_claims(pl.DataFrame({"pick_label": [text]}), None, allow_list)
    except ValueError:
        return True
    return False


def test_bend_reads_tn_2a_as_an_in_region_guide(suitability: SuitabilityFixture) -> None:
    cell_id = suitability.role_cell("bend", "east_of_cascade_crest_cell")
    tn_2a_taxon = suitability.taxa("bend", "east_of_cascade_crest_taxon")

    v0 = candidate(suitability.candidates(cell_id, V0_FROZEN), tn_2a_taxon, "hedgerow_buffer")
    production = candidate(suitability.candidates(cell_id, PRODUCTION_ON_FIXTURE), tn_2a_taxon, "hedgerow_buffer")

    assert v0["is_pick"]
    assert not v0["in_region_supported"]
    assert IN_REGION_NO in v0["pick_label"]
    assert TN_2A_NEIGHBOUR_TAG.search(v0["pick_label"])
    assert production["is_pick"]
    assert production["in_region_supported"]
    assert IN_REGION_YES in production["pick_label"]
    assert "[neighbour]" not in production["pick_label"]


def test_an_in_region_uncertain_pick_outranks_a_confirmed_neighbour_pick(suitability: SuitabilityFixture) -> None:
    cell_id = suitability.role_cell("boise", "in_region_uncertain_versus_neighbour_cell")
    # Ungated: the confirmed neighbour pick here is named only by Xerces Great Basin 2022 (licence unrecorded).
    picks = suitability.candidates(cell_id, PRODUCTION_WITHOUT_LICENCE_GATE).filter(
        (pl.col("guild") == "hedgerow_buffer") & pl.col("is_pick")
    )
    uncertain = [candidate(picks, name) for name in suitability.taxa("boise", "in_region_uncertain_taxa")]
    neighbours = [candidate(picks, name) for name in suitability.taxa("boise", "confirmed_neighbour_taxa")]

    assert max(pick["rank"] for pick in uncertain) < min(pick["rank"] for pick in neighbours)
    assert all(UNCERTAIN_FROST_FREE in pick["pick_label"] for pick in uncertain)
    assert all(IN_REGION_YES in pick["pick_label"] for pick in uncertain)
    assert all(CONFIRMED_FROST_FREE in pick["pick_label"] for pick in neighbours)
    assert all(IN_REGION_NO in pick["pick_label"] for pick in neighbours)
    in_region_ranks = picks.filter(pl.col("in_region_supported"))["rank"]
    neighbour_ranks = picks.filter(~pl.col("in_region_supported"))["rank"]
    assert in_region_ranks.max() < neighbour_ranks.min()


def test_null_ec_caco3_and_restriction_depth_are_unknown_axes_never_failures(suitability: SuitabilityFixture) -> None:
    cell_id = suitability.role_cell("boise", "soil_failures_cell")
    measured = suitability.site_row(cell_id)
    soil_inputs = ("ec_ds_per_m", "caco3_percent", "restriction_depth_cm")
    shipped_null = measured.with_columns([pl.lit(None, dtype=pl.Float64).alias(column) for column in soil_inputs])

    v0_measured = suitability.candidates(measured, V0_FROZEN)
    production = suitability.candidates(shipped_null, PRODUCTION_ON_FIXTURE)

    assert v0_measured.filter(pl.col("axis_salinity") == 0).height > 0
    for axis in SOIL_AXES:
        assert production[f"axis_{axis}"].null_count() == production.height
    assert production.filter(pl.col("unknown_axis_count") < len(SOIL_AXES)).height == 0


def test_a_none_recorded_restriction_is_unknown_in_production_and_counted(
    suitability: SuitabilityFixture, v0_cells: pl.DataFrame, production_cells: pl.DataFrame
) -> None:
    cell_id = suitability.role_cell("boise", "restriction_none_recorded_cell")

    v0 = suitability.candidates(cell_id, V0_FROZEN)
    production = suitability.candidates(cell_id, PRODUCTION_ON_FIXTURE)
    v0_cell, production_cell = cell_row(v0_cells, cell_id), cell_row(production_cells, cell_id)

    assert set(v0["axis_root_depth"].to_list()) == {1}
    assert production["axis_root_depth"].null_count() == production.height
    assert any(v0_cell[f"{guild}_count_fully_known"] > 0 for guild in GUILDS)
    assert [production_cell[f"{guild}_count_fully_known"] for guild in GUILDS] == [0, 0, 0]


def test_production_serves_prism_precipitation_with_its_dated_attribution_on_both_seams(
    suitability: SuitabilityFixture,
) -> None:
    engine = suitability.prepared(PRODUCTION_ON_FIXTURE)
    cell_id = suitability.role_cell("bend", "east_of_cascade_crest_cell")

    table = engine.layer_table(suitability.site)
    point, point_metadata = engine.candidates_with_metadata(suitability.site_row(cell_id))

    metadata = {key.decode(): value.decode() for key, value in table.schema.metadata.items()}
    precipitation = json.loads(metadata[METADATA_SITE_INPUTS])["precipitation"]
    assert point_metadata == metadata
    assert precipitation["licence"] == PRISM_TERMS_OF_USE
    assert precipitation["accessed"] == PRISM_ACCESSED
    assert precipitation["attribution"] == PRISM_ATTRIBUTION
    assert precipitation["credits"] == [ERA5_CREDIT]
    # The complete credit list: one line per distinct work, ERA5-Land declared by two groups but listed once.
    expected_credits = sorted(
        [PRISM_ATTRIBUTION, ERA5_CREDIT, ERA5_LAND_CREDIT, SOILGRIDS_CREDIT, COPERNICUS_DEM_NOTICE]
    )
    assert json.loads(metadata[METADATA_ATTRIBUTIONS]) == expected_credits
    assert json.loads(metadata[METADATA_WITHHELD_SITE_INPUTS]) == {}
    assert point.filter(pl.col("is_pick")).height > 0


@pytest.mark.parametrize(
    ("precipitation", "config", "reason"),
    [
        pytest.param(
            {"licence": UNRECORDED, "accessed": None},
            PRODUCTION_ON_FIXTURE,
            r"withheld by its licence gate \{.*'precipitation': 'unrecorded'",
            id="unrecorded_before_the_owner_decision",
        ),
        pytest.param(
            {},
            PRODUCTION_WITHOUT_CC_BY,
            r"withheld by its licence gate \{.*'precipitation': 'CC-BY-4.0'",
            id="credited_era5_not_permitted",
        ),
        pytest.param(None, PRODUCTION_ON_FIXTURE, r"undeclared \['precipitation'\]", id="undeclared"),
    ],
)
def test_production_refuses_to_serve_a_precipitation_group_its_gate_withholds_or_nobody_declared(
    suitability: SuitabilityFixture, precipitation: dict[str, str | None] | None, config: RuleConfig, reason: str
) -> None:
    cell_id = suitability.role_cell("boise", "burned_cell")
    changed = dict(suitability.site_provenance.groups)
    if precipitation is None:
        del changed["precipitation"]
    else:
        changed["precipitation"] = replace(changed["precipitation"], **precipitation)
    declaration = SiteInputProvenance(changed)
    tables = (suitability.envelope(config), suitability.guide_rows, suitability.exclusions, config)

    with pytest.raises(ValueError, match=reason):
        prepare(*tables, declaration)
    with pytest.raises(ValueError, match=reason):
        evaluate_cells(suitability.site, *tables, declaration)
    with pytest.raises(ValueError, match=reason):
        candidates_for_cell(suitability.site_row(cell_id), *tables, declaration)


@pytest.mark.parametrize(
    ("group", "changes", "reason"),
    [
        pytest.param(
            "precipitation",
            {"accessed": None},
            r"\{'precipitation': 'PRISM-terms-of-use'\} carry a licence",
            id="no_access_date",
        ),
        pytest.param(
            "precipitation", {"accessed": "26 Sep 2026"}, r"'26 Sep 2026' is not written YYYY-MM-DD", id="prose_date"
        ),
        pytest.param(
            "precipitation", {"accessed": "20260926"}, r"'20260926' is not written YYYY-MM-DD", id="compact_date"
        ),
        pytest.param(
            "soil_ph_texture",
            {"credits": ()},
            r"\['soil_ph_texture'\] carry a licence that obliges a credit but declare none",
            id="cc_by_group_without_a_credit",
        ),
        pytest.param(
            "precipitation",
            {"credits": (SourceCredit("ERA5", "Open-Meteo.com", "https://open-meteo.com", UNRECORDED),)},
            r"no credit form \{'precipitation': \['unrecorded'\]\}",
            id="credit_under_a_licence_with_no_credit_form",
        ),
    ],
)
def test_a_site_input_declaration_that_cannot_state_its_credits_is_refused_on_construction(
    suitability: SuitabilityFixture, group: str, changes: dict[str, Any], reason: str
) -> None:
    groups = dict(suitability.site_provenance.groups)
    groups[group] = replace(groups[group], **changes)

    # Refused before any rule set sees it: the obligation is the licence's, so no preset (V0 included) can serve it.
    with pytest.raises(ValueError, match=reason):
        SiteInputProvenance(groups)


def test_the_record_low_credits_the_copernicus_dem_it_was_lapse_adjusted_to_while_the_cold_group_is_served(
    suitability: SuitabilityFixture,
) -> None:
    cell_id = suitability.role_cell("boise", "burned_cell")
    served = suitability.prepared(PRODUCTION_ON_FIXTURE)
    without_dem = suitability.prepared(COLD_OPTIONAL_WITHOUT_DEM)

    point, point_metadata = served.candidates_with_metadata(suitability.site_row(cell_id))
    cold = json.loads(point_metadata[METADATA_SITE_INPUTS])["cold"]
    withheld_metadata = without_dem.metadata()
    withheld_point = without_dem.candidates_for_cell(suitability.site_row(cell_id))

    assert cold["credits"] == [ERA5_LAND_CREDIT, COPERNICUS_DEM_NOTICE]
    assert COPERNICUS_DEM_NOTICE in json.loads(point_metadata[METADATA_ATTRIBUTIONS])
    assert point["axis_cold"].drop_nulls().len() > 0
    # The DEM credit is gated like the group's own licence: withheld, the record low and its notice go together,
    # while frost-free days still credit ERA5-Land.
    assert json.loads(withheld_metadata[METADATA_WITHHELD_SITE_INPUTS]) == {"cold": COPERNICUS_DEM}
    assert COPERNICUS_DEM_NOTICE not in json.loads(withheld_metadata[METADATA_ATTRIBUTIONS])
    assert ERA5_LAND_CREDIT in json.loads(withheld_metadata[METADATA_ATTRIBUTIONS])
    assert withheld_point["axis_cold"].drop_nulls().len() == 0
    with pytest.raises(ValueError, match=r"withheld by its licence gate \{'cold': 'Copernicus-DEM-licence'\}"):
        suitability.prepared(PRODUCTION_WITHOUT_DEM)


@pytest.mark.parametrize("accessed", ["2026-09-26", "2025-12-16"], ids=["prototype_pull", "earlier_date"])
def test_production_refuses_a_prism_pull_no_later_than_the_prototypes(
    suitability: SuitabilityFixture, accessed: str
) -> None:
    cell_id = suitability.role_cell("bend", "burned_cell")
    declaration = suitability.own_pull(accessed).site_provenance
    tables = (suitability.envelope(PRODUCTION), suitability.guide_rows, suitability.exclusions, PRODUCTION)
    reason = rf"needs its own pull of \{{'precipitation': '{accessed}'\}}"

    with pytest.raises(ValueError, match=reason):
        prepare(*tables, declaration)
    with pytest.raises(ValueError, match=reason):
        evaluate_cells(suitability.site, *tables, declaration)
    with pytest.raises(ValueError, match=reason):
        candidates_for_cell(suitability.site_row(cell_id), *tables, declaration)
    # V0 is the prototype's own rule set, so the prototype's pull serves under it.
    v0 = suitability.own_pull(accessed).candidates(cell_id, V0_FROZEN)
    assert v0.filter(pl.col("is_pick")).height > 0


@pytest.mark.parametrize("prism_accessed", [None, PRODUCTION_PULL], ids=["as_generated", "prism_re_dated"])
def test_production_refuses_the_fixture_declaration_even_with_a_fresh_prism_date(
    suitability: SuitabilityFixture, prism_accessed: str | None
) -> None:
    cell_id = suitability.role_cell("bend", "burned_cell")
    groups = dict(suitability.site_provenance.groups)
    if prism_accessed is not None:
        # The accident the guard exists for: the fixture declaration copied, with only the PRISM date changed.
        groups["precipitation"] = replace(groups["precipitation"], accessed=prism_accessed)
    declaration = SiteInputProvenance(groups)
    tables = (suitability.envelope(PRODUCTION), suitability.guide_rows, suitability.exclusions, PRODUCTION)
    reason = re.escape(f"does not serve the frozen fixture's pull, declared for site input groups {sorted(groups)}")

    with pytest.raises(ValueError, match=reason):
        prepare(*tables, declaration)
    with pytest.raises(ValueError, match=reason):
        evaluate_cells(suitability.site, *tables, declaration)
    with pytest.raises(ValueError, match=reason):
        candidates_for_cell(suitability.site_row(cell_id), *tables, declaration)


def test_production_serves_its_own_pull_under_that_date_with_the_picks_the_fixture_run_serves(
    suitability: SuitabilityFixture, production_cells: pl.DataFrame
) -> None:
    own_pull = suitability.own_pull(PRODUCTION_PULL).prepared(PRODUCTION)

    metadata = own_pull.metadata()
    declared = json.loads(metadata[METADATA_SITE_INPUTS])
    fixture_run = suitability.prepared(PRODUCTION_ON_FIXTURE).metadata()

    assert {group: source["fixture"] for group, source in declared.items()} == dict.fromkeys(SITE_INPUT_GROUPS, False)
    assert declared["precipitation"]["attribution"] == PRODUCTION_PULL_ATTRIBUTION
    assert PRODUCTION_PULL_ATTRIBUTION in json.loads(metadata[METADATA_ATTRIBUTIONS])
    assert PRISM_ATTRIBUTION not in json.loads(metadata[METADATA_ATTRIBUTIONS])
    # The fixture run says so twice in what it serves: its declaration and its rule-config fingerprint.
    assert json.loads(fixture_run[METADATA_SITE_INPUTS])["precipitation"]["fixture"] is True
    assert fixture_run[METADATA_RULE_CONFIG] != metadata[METADATA_RULE_CONFIG]
    assert own_pull.evaluate_cells(suitability.site).equals(production_cells)


def test_production_and_v0_read_the_same_precipitation_at_bend(suitability: SuitabilityFixture) -> None:
    # Before the owner's PRISM decision PRODUCTION read ERA5 here: 1,009 of 2,166 shared rows disagreed.
    bend_cells = suitability.manifest["cells"]["bend"]
    precipitation_axes = ["axis_dry_year_precip", "axis_mean_precip"]
    keys = ["cell_id", "guild", "plant_id"]

    def precipitation_reads(config: RuleConfig) -> pl.DataFrame:
        """Every (cell, guild, pool taxon) at the Bend fixture cells with its two precipitation axes."""
        engine = suitability.prepared(config)
        frames = [
            engine.candidates_for_cell(suitability.site_row(cell_id)).with_columns(pl.lit(cell_id).alias("cell_id"))
            for cell_id in bend_cells
        ]
        return pl.concat(frames).filter(pl.col("plant_id").is_not_null()).select(*keys, *precipitation_axes)

    v0, production = precipitation_reads(V0_FROZEN), precipitation_reads(PRODUCTION_ON_FIXTURE)
    shared = v0.join(production, on=keys, suffix="_production")
    disagreeing = shared.filter(
        pl.any_horizontal([pl.col(axis).ne_missing(pl.col(f"{axis}_production")) for axis in precipitation_axes])
    )

    assert shared.height > 0
    assert disagreeing.height == 0


@pytest.mark.parametrize("group", sorted(SITE_INPUT_GROUPS))
def test_production_requires_every_site_input_group_the_envelope_reads(
    suitability: SuitabilityFixture, group: str
) -> None:
    groups = dict(suitability.site_provenance.groups)
    groups[group] = replace(groups[group], licence=UNRECORDED)
    tables = (
        suitability.envelope(PRODUCTION_ON_FIXTURE),
        suitability.guide_rows,
        suitability.exclusions,
        PRODUCTION_ON_FIXTURE,
    )

    with pytest.raises(ValueError, match=rf"withheld by its licence gate \{{'{group}': 'unrecorded'\}}"):
        prepare(*tables, SiteInputProvenance(groups))


def test_a_production_pick_label_names_every_axis_it_could_not_check(suitability: SuitabilityFixture) -> None:
    # Bend's burned cell has picks with every axis known and picks with some unknown (Boise serves no EC or CaCO3).
    cell_id = suitability.role_cell("bend", "burned_cell")

    v0 = suitability.candidates(cell_id, V0_FROZEN).filter(pl.col("is_pick"))
    production = suitability.candidates(cell_id, PRODUCTION_ON_FIXTURE).filter(pl.col("is_pick"))

    unchecked = [
        ([AXIS_LABELS[axis] for axis in AXIS_NAMES if pick[f"axis_{axis}"] is None], pick["pick_label"])
        for pick in production.iter_rows(named=True)
    ]
    assert [label for names, label in unchecked if names], "no pick here has an unknown axis, so this proves nothing"
    assert [label for names, label in unchecked if not names], "every pick here has an unknown axis"
    for names, label in unchecked:
        assert label.endswith(f" | {UNCHECKED_PREFIX}{', '.join(names)}") if names else UNCHECKED_PREFIX not in label
    assert v0.filter(pl.col("unknown_axis_count") > 0).height > 0
    assert not [label for label in v0["pick_label"].to_list() if UNCHECKED_PREFIX in label]


def test_a_cell_without_frost_free_days_blames_the_site_value_not_plants(suitability: SuitabilityFixture) -> None:
    cell_id = suitability.role_cell("boise", "burned_cell")
    missing = suitability.site_row(cell_id).with_columns(pl.lit(None, dtype=pl.Float64).alias("median_frost_free_days"))

    for config in (V0_FROZEN, PRODUCTION_ON_FIXTURE):
        labels = suitability.candidates(missing, config).filter(pl.col("is_pick"))["pick_label"].to_list()
        assert labels
        assert all(SITE_FROST_FREE_MISSING in label for label in labels)
        assert not [label for label in labels if UNKNOWN_FROST_FREE in label]
    production = (
        suitability.candidates(missing, PRODUCTION_ON_FIXTURE).filter(pl.col("is_pick"))["pick_label"].to_list()
    )
    assert all(AXIS_LABELS["frost_free_days"] in label.split(UNCHECKED_PREFIX)[-1] for label in production)


def test_band_inheritance_keeps_leymus_triticoides_off_non_saline_cells(
    suitability: SuitabilityFixture, v0_cells: pl.DataFrame
) -> None:
    non_saline = suitability.role_cell("boise", "leymus_non_saline_cell")
    saline = suitability.role_cell("boise", "saline_cell")
    # Ungated: Leymus's in-region row (BLM Boise District plan) and its saline qualifier (ODFW) are licence-unrecorded.
    config = PRODUCTION_WITHOUT_LICENCE_GATE
    ungated_cells = suitability.evaluate(config, site=suitability.site_row(non_saline))

    v0_non_saline = candidate(suitability.candidates(non_saline, V0_FROZEN), LEYMUS, "post_fire_restoration")
    production_non_saline = candidate(suitability.candidates(non_saline, config), LEYMUS, "post_fire_restoration")
    production_saline = candidate(suitability.candidates(saline, config), LEYMUS, "post_fire_restoration")

    assert v0_non_saline["is_pick"]
    assert LEYMUS in cell_row(v0_cells, non_saline)["post_fire_restoration_top3"]
    assert not production_non_saline["is_pick"]
    assert production_non_saline["axis_source_applicability"] == 0
    assert LEYMUS not in (cell_row(ungated_cells, non_saline)["post_fire_restoration_top3"] or "")
    assert production_saline["is_pick"]
    assert production_saline["in_region_supported"]
    assert f"{STRATIFICATION_PREFIX}{ODFW_SALINE_MIX_TAG}" in production_saline["pick_label"]


def test_a_licence_excluded_guide_lends_no_stratification_to_an_admitted_one(suitability: SuitabilityFixture) -> None:
    cell_id = suitability.role_cell("boise", "leymus_non_saline_cell")
    # A variant that tests the rule, not the curation: the BLM list admitted, the ODFW 2017 document still excluded.
    blm_admitted = suitability.relicensed({BLM_BOISE_DISTRICT_SOURCE}, US_GOVERNMENT_WORK)
    both_admitted = suitability.relicensed({BLM_BOISE_DISTRICT_SOURCE, ODFW_SALINE_MIX_SOURCE}, US_GOVERNMENT_WORK)

    gated = candidate(blm_admitted.candidates(cell_id, PRODUCTION_ON_FIXTURE), LEYMUS, "post_fire_restoration")
    lent = candidate(both_admitted.candidates(cell_id, PRODUCTION_ON_FIXTURE), LEYMUS, "post_fire_restoration")

    assert gated["is_pick"]
    assert gated["axis_source_applicability"] == 1
    assert STRATIFICATION_PREFIX not in gated["pick_label"]
    assert not lent["is_pick"]
    assert lent["axis_source_applicability"] == 0


def test_no_origin_label_cites_a_licence_excluded_document(suitability: SuitabilityFixture) -> None:
    cell_id = suitability.role_cell("boise", "burned_cell")
    excluded_ids = sorted(suitability.prepared(PRODUCTION_ON_FIXTURE).excluded_sources)
    excluded_names = set(suitability.guide_rows.filter(pl.col("source_id").is_in(excluded_ids))["source_short_name"])

    def citing_excluded(candidates: pl.DataFrame) -> list[str]:
        labels = candidates["origin_label"].drop_nulls().to_list()
        return [label for label in labels if any(name in label for name in excluded_names)]

    assert citing_excluded(suitability.candidates(cell_id, PRODUCTION_WITHOUT_LICENCE_GATE))
    assert not citing_excluded(suitability.candidates(cell_id, PRODUCTION_ON_FIXTURE))


def test_range_wide_native_statements_no_longer_exempt_a_taxon_plants_does_not_record_in_the_state(
    suitability: SuitabilityFixture,
) -> None:
    cell_id = suitability.role_cell("boise", "burned_cell")
    v0 = suitability.candidates(cell_id, V0_FROZEN)
    production = suitability.candidates(cell_id, PRODUCTION_ON_FIXTURE)
    # Ungated: the state-scoped native statements come from Xerces Idaho 2017 (licence unrecorded).
    ungated = suitability.candidates(cell_id, PRODUCTION_WITHOUT_LICENCE_GATE)

    for taxon in suitability.taxa("boise", "range_wide_only_taxa"):
        v0_taxon, production_taxon = candidate(v0, taxon), candidate(production, taxon)
        assert not v0_taxon["introduced_flag"]
        assert "PLANTS does not record it in ID; native per in-region" in v0_taxon["origin_label"]
        assert production_taxon["introduced_flag"]
        assert production_taxon["origin_label"].startswith("not recorded in ID (PLANTS)")
        assert "is a range-wide statement" in production_taxon["origin_label"]
    for taxon in suitability.taxa("boise", "state_scoped_native_taxa"):
        ungated_taxon = candidate(ungated, taxon)
        assert not ungated_taxon["introduced_flag"]
        assert "native per in-region Xerces Idaho 2017" in ungated_taxon["origin_label"]


def test_only_greenstrip_cites_cps_394_in_the_introduced_flag(suitability: SuitabilityFixture) -> None:
    cell_id = suitability.role_cell("boise", "burned_cell")
    introduced = pl.col("origin_label").str.starts_with(INTRODUCED_FLAG_PLAIN)
    v0 = suitability.candidates(cell_id, V0_FROZEN).filter(introduced)
    production = suitability.candidates(cell_id, PRODUCTION_ON_FIXTURE).filter(introduced)

    for guild in GUILDS:
        v0_labels = v0.filter(pl.col("guild") == guild)["origin_label"].to_list()
        production_labels = production.filter(pl.col("guild") == guild)["origin_label"].to_list()
        assert v0_labels
        assert all(CPS_394 in label for label in v0_labels)
        assert production_labels
        assert all((CPS_394 in label) == (guild == "greenstrip") for label in production_labels)


def test_a_guide_row_without_a_page_is_cited_page_n_a(suitability: SuitabilityFixture) -> None:
    cell_id = suitability.role_cell("boise", "licence_cell")
    source_id = suitability.taxa("boise", "licence_kept_source_id")
    rows = suitability.guide_rows
    short_name = rows.filter(pl.col("source_id") == source_id)["source_short_name"].unique().item()
    unpaged = pl.when(pl.col("source_id") == source_id).then(pl.lit(None, dtype=pl.String)).otherwise(pl.col("page"))
    without_page = replace(suitability, guide_rows=rows.with_columns(unpaged.alias("page")))

    woody = without_page.candidates(cell_id, PRODUCTION_ON_FIXTURE).filter(pl.col("guild") == "hedgerow_buffer")

    for taxon in suitability.taxa("boise", "licence_kept_source_only_taxa"):
        assert f"{short_name} page n/a" in candidate(woody, taxon)["pick_label"]


def test_hedgerow_labels_carry_no_fire_field_and_the_other_guilds_open_with_the_verbatim_label(
    production_cells: pl.DataFrame,
) -> None:
    hedgerow = served_labels(production_cells, "hedgerow_buffer")
    assert hedgerow
    assert not [label for label in hedgerow if "fire-resistant" in label.lower()]
    for guild in ("greenstrip", "post_fire_restoration"):
        labels = served_labels(production_cells, guild)
        assert labels
        assert all(label.startswith(FIRE_LABEL_PREFIX) for label in labels)


def test_the_egress_guard_passes_the_served_layer_and_refuses_a_fire_word_in_it(
    production_cells: pl.DataFrame, production_allow_list: FireTextAllowList, suitability: SuitabilityFixture
) -> None:
    assert_no_fire_claims(
        production_cells, suitability.prepared(PRODUCTION_ON_FIXTURE).metadata(), production_allow_list
    )

    first_row = pl.int_range(pl.len()) == 0
    claimed = production_cells.with_columns(
        pl.when(first_row)
        .then(pl.lit("serviceberry; a hedge that reduces wildfire spread"))
        .otherwise(pl.col("hedgerow_buffer_top3_common_names"))
        .alias("hedgerow_buffer_top3_common_names")
    )
    with pytest.raises(ValueError, match=r"fire words outside the allow-list.+labels\.PLANT_NAME_EXEMPTIONS"):
        assert_no_fire_claims(claimed, None, production_allow_list)


@pytest.mark.parametrize(
    ("text", "refused"),
    [*((text, True) for text in EGRESS_REFUSED), *((text, False) for text in EGRESS_SERVED)],
)
def test_egress_serves_fire_words_only_inside_registered_citations_and_fixed_wording(
    production_allow_list: FireTextAllowList, text: str, refused: bool
) -> None:
    assert refused_at_egress(text, production_allow_list) is refused


@pytest.mark.parametrize(
    ("text", "is_claim"),
    [
        (f"{FIRE_LABEL_PREFIX}Yes | native (per NRCS TN PM-2A (2017))", False),
        (NO_FIRE_CLAIM, False),
        ("sources: BLM Soda Fire ESR 2016 p.5 + NRCS westside post-fire 2024 p.1", False),
        ("seeded after the Soda Fire to stabilise soil and limit weeds", False),
        ("fire-following annual; post-fire seeding", False),
        ("drought-resistant bunchgrass seeded after the Soda Fire", False),
        ("fireweed and firecracker penstemon for a windbreak, low-growing and less palatable to deer", False),
        ("small burnet slowly spreads; less burnet where grazed", False),
        # Round 2: a claim verb in one citation and a fire word in the next no longer compose a claim.
        ("sources: NRCS Lower Snake p.3 + BLM Range Fire 2026 p.1", False),
        ("sources: Protecting Pollinators p.2 + BLM Soda Fire ESR 2016 p.5", False),
        ("listed as: Poa secunda | sources: Limited edition p.1 + BLM Range Fire 2026 p.1", False),
        ("reduces wildfire", True),
        ("planted to slow the spread of fire", True),
        ("prevents fires near homes", True),
        ("a fire-resistant hedge", True),
        ("prevents the spread of the wildfire", True),
        ("reduces the rate of spread of fire", True),
        ("keeps fire from spreading", True),
        ("fire-slowing hedge", True),
        ("flame-resistant hedge", True),
        ("reduces flammability", True),
        ("FireSmart planting", True),
        ("a fire\u2011resistant hedge", True),
        ("Fire\u2010Resistant Plants for Home Landscapes", True),
        ("fire\u2013resistant hedge", True),
        ("fire-\u00adresistant hedge", True),
        ("fire\u00a0resistant hedge", True),
        ("fire  resistant hedge", True),
        ("fire_resistant hedge", True),
        ("reduces wild\u200dfire spread", True),
        ("red\u200buces wildfire", True),
        ("\uff46\uff49\uff52\uff45-resistant hedge", True),
        ("\ufb01re-resistant hedge", True),
        ("reduces fir\u0435 risk", True),
        ("shrubs resistant to fire", True),
        ("this shrub resists fire", True),
        ("low flammability planting", True),
        ("less flammable than cheatgrass", True),
        ("helps contain wildfire", True),
        ("firebreak planting", True),
        ("a living fuel break", True),
        ("reduces fuel loads near homes", True),
        ("defensible space shrub", True),
        ("inhibits fire spread", True),
        ("fire-resilient landscaping", True),
        ("reduces the chance that a single spark in dry grass becomes a wildfire", True),
        # Round 2 (p03): invisible, combining and lookalike characters, then the phrasings ingress now names.
        ("fi\u200ere-resistant hedge", True),
        ("fi\u202e\u202cre-resistant hedge", True),
        ("fi\u034fre-resistant hedge", True),
        ("fi\ufe0fre-resistant hedge", True),
        ("fi\U000e0020re-resistant hedge", True),
        ("fi\u3164re-resistant hedge", True),
        ("fir\u0336e-resistant hedge", True),
        ("planted to sl\u0585w wildfire", True),
        ("less fl\u0251mmable hedge", True),
        ("st\u2c9fps wildfire", True),
        ("f\u04cfre-resistant hedge", True),
        ("this hedge resists the spread of fire", True),
        ("a living fire barrier", True),
        ("acts as a barrier to wildfire", True),
        ("a buffer against wildfire", True),
        ("a shield against flames", True),
        ("a green firewall around homes", True),
        ("a slow-burning groundcover", True),
        ("less fire-prone than cheatgrass", True),
        ("reduces burn severity near structures", True),
        ("a hedge that fights wildfire", True),
        ("hinders fire spread", True),
        ("impedes fire movement", True),
        ("deters wildfire", True),
        ("keeps flames at bay", True),
        # Round 4 (R3): ingress reads signs as letters and joins punctuation inside a word, ";" included.
        ("f1r3-resistant hedge", True),
        ("f\u00a1re-res\u00a1stant hedge", True),
        ("fi.re-resistant hedge", True),
        ("fi;re-resistant hedge", True),
        ("grand fir;European alder", False),
    ],
)
def test_the_ingress_claim_pattern_separates_claims_from_the_allowed_wording(text: str, is_claim: bool) -> None:
    assert (find_fire_claims([text]) == [text]) is is_claim


@pytest.mark.parametrize(
    ("licence", "admitted"),
    [
        (PUBLIC_DOMAIN, True),
        (US_GOVERNMENT_WORK, True),
        (CC0, True),
        (CC_BY, True),
        (CC_BY_NC, False),
        (CC_BY_NC_SA, False),
        (CC_BY_ND, False),
        (ALL_RIGHTS_RESERVED, False),
        (UNRECORDED, False),
    ],
)
def test_the_licence_gate_admits_exactly_the_commercial_use_licence_ids(
    suitability: SuitabilityFixture, licence: str, admitted: bool
) -> None:
    source_id = suitability.taxa("boise", "licence_kept_source_id")

    metadata = suitability.relicensed({source_id}, licence).prepared(PRODUCTION_ON_FIXTURE).metadata()

    assert (source_id not in json.loads(metadata[METADATA_EXCLUDED_SOURCES])) is admitted
    assert (source_id in json.loads(metadata[METADATA_ADMITTED_SOURCES])) is admitted


def test_production_drops_licence_unrecorded_guides_and_keeps_federal_ones(suitability: SuitabilityFixture) -> None:
    cell_id = suitability.role_cell("boise", "licence_cell")
    dropped_source = suitability.taxa("boise", "licence_dropped_source_id")
    kept_source = suitability.taxa("boise", "licence_kept_source_id")
    woody = pl.col("guild") == "hedgerow_buffer"

    v0 = suitability.candidates(cell_id, V0_FROZEN).filter(woody)
    production = suitability.candidates(cell_id, PRODUCTION_ON_FIXTURE).filter(woody)
    metadata = suitability.prepared(PRODUCTION_ON_FIXTURE).metadata()

    excluded = json.loads(metadata[METADATA_EXCLUDED_SOURCES])
    assert excluded[dropped_source] == UNRECORDED
    assert kept_source not in excluded
    for taxon in suitability.taxa("boise", "licence_dropped_source_only_taxa"):
        assert candidate(v0, taxon)["is_pick"]
        assert taxon not in production["display_name"].to_list()
    for taxon in suitability.taxa("boise", "licence_kept_source_only_taxa"):
        assert candidate(production, taxon)["is_pick"]


def test_the_served_layer_names_its_exact_rule_set_inputs_and_sources_and_the_point_tool_carries_the_same(
    suitability: SuitabilityFixture,
) -> None:
    engine = suitability.prepared(PRODUCTION_ON_FIXTURE)
    ungated_engine = suitability.prepared(PRODUCTION_WITHOUT_LICENCE_GATE)
    cell_id = suitability.role_cell("boise", "burned_cell")

    table = engine.layer_table(suitability.site)
    ungated_table = ungated_engine.layer_table(suitability.site)
    _, point_metadata = engine.candidates_with_metadata(suitability.site_row(cell_id))

    metadata = {key.decode(): value.decode() for key, value in table.schema.metadata.items()}
    ungated_rules = ungated_table.schema.metadata[METADATA_RULE_CONFIG.encode()].decode()
    assert set(metadata) == set(METADATA_KEYS)
    assert point_metadata == metadata
    assert "not field-tested recommendations" in metadata[METADATA_PICK_DEFINITION]
    assert "absence from a cell is not evidence that it is unsuitable" in metadata[METADATA_PICK_DEFINITION]
    assert metadata[METADATA_RULE_CONFIG].startswith("production sha256:")
    assert ungated_rules == ungated_engine.metadata()[METADATA_RULE_CONFIG] != metadata[METADATA_RULE_CONFIG]
    assert ungated_rules.startswith("production sha256:")
    assert metadata[METADATA_ENGINE_VERSION] == ENGINE_VERSION
    admitted, excluded = (
        json.loads(metadata[METADATA_ADMITTED_SOURCES]),
        json.loads(metadata[METADATA_EXCLUDED_SOURCES]),
    )
    assert set(admitted) | set(excluded) == set(suitability.guide_rows["source_id"].to_list())
    assert not set(admitted) & set(excluded)
    assert {source["licence"] for source in admitted.values()} <= COMMERCIAL_USE_LICENCES
    assert admitted[FIRE_NAMED_SOURCE_ID] == {"short_name": FIRE_NAMED_SOURCE, "licence": US_GOVERNMENT_WORK}
    assert json.loads(metadata[METADATA_WITHHELD_SITE_INPUTS]) == {}
    assert set(json.loads(metadata[METADATA_SITE_INPUTS])) == set(SITE_INPUT_GROUPS)
    assert table.schema.remove_metadata().equals(CELL_RECOMMENDATIONS_SCHEMA)


def test_the_rule_config_fingerprint_changes_with_every_field(suitability: SuitabilityFixture) -> None:
    # Production's own pull, so every variant (fixtures allowed or not) serves and only the rule set differs.
    own_pull = suitability.own_pull(PRODUCTION_PULL)
    production = own_pull.prepared(PRODUCTION).metadata()[METADATA_RULE_CONFIG]

    variants = {
        name: own_pull.prepared(replace(PRODUCTION, **{name: value})).metadata()[METADATA_RULE_CONFIG]
        for name, value in FINGERPRINT_VARIANTS.items()
    }

    assert sorted(variants) == sorted(field.name for field in dataclasses.fields(RuleConfig))
    for name, fingerprint in variants.items():
        assert fingerprint != production, name
        assert fingerprint.startswith(f"{FINGERPRINT_VARIANTS['name'] if name == 'name' else 'production'} sha256:")


def test_the_inputs_digest_follows_every_loaded_input_and_the_site_declaration_never_row_or_list_order(
    suitability: SuitabilityFixture,
) -> None:
    engine = suitability.prepared(PRODUCTION_ON_FIXTURE)
    digest = engine.metadata()[METADATA_INPUTS_SHA256]
    # A licence-dropped source gains a Corvallis greenstrip row: dropped, yet it turns that guild's status.
    dropped_row = suitability.guide_rows.filter(
        pl.col("source_id") == suitability.taxa("boise", "licence_dropped_source_id")
    ).head(1)
    corvallis_greenstrip_row = dropped_row.with_columns(
        pl.lit("greenstrip").alias("guild"),
        pl.lit(["corvallis"]).alias("applies_to_regions"),
        pl.lit([True]).alias("in_region"),
        (pl.col("guide_row_id") + 10_000_000).alias("guide_row_id"),
    )
    dropped_row_added = replace(suitability, guide_rows=pl.concat([suitability.guide_rows, corvallis_greenstrip_row]))
    corvallis = suitability.site.filter(pl.col("region") == "corvallis").head(1)
    # Rows reversed, and every list cell reversed (a row's regions and in-region flags together, as pairs).
    species_lists = ("synonym_names", "recorded_states", "fire_resistant_values")
    guide_lists = ("matched_plant_ids", "noxious_states", "applies_to_regions", "in_region")
    reordered = replace(
        suitability,
        species=suitability.species.reverse().with_columns(pl.col(name).list.reverse() for name in species_lists),
        guide_rows=suitability.guide_rows.reverse().with_columns(pl.col(name).list.reverse() for name in guide_lists),
        exclusions=suitability.exclusions.reverse(),
    )
    federal_dropped = suitability.relicensed({suitability.taxa("boise", "licence_kept_source_id")}, UNRECORDED)
    groups = dict(suitability.site_provenance.groups)
    re_accessed = replace(
        suitability,
        site_provenance=SiteInputProvenance(
            {**groups, "precipitation": replace(groups["precipitation"], accessed="2026-10-03")}
        ),
    )

    dropped_row_engine = dropped_row_added.prepared(PRODUCTION_ON_FIXTURE)

    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert reordered.prepared(PRODUCTION_ON_FIXTURE).metadata()[METADATA_INPUTS_SHA256] == digest
    assert federal_dropped.prepared(PRODUCTION_ON_FIXTURE).metadata()[METADATA_INPUTS_SHA256] != digest
    assert re_accessed.prepared(PRODUCTION_ON_FIXTURE).metadata()[METADATA_INPUTS_SHA256] != digest
    assert engine.evaluate_cells(corvallis)["greenstrip_status"].to_list() == ["no_regional_guide"]
    assert dropped_row_engine.evaluate_cells(corvallis)["greenstrip_status"].to_list() == ["licence_excluded"]
    assert dropped_row_engine.metadata()[METADATA_INPUTS_SHA256] != digest
