"""Flows: each owner rule that PRODUCTION adds over V0_FROZEN, observed on real fixture cells through both seams."""

from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING

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
    PUBLIC_DOMAIN,
    UNRECORDED,
    US_GOVERNMENT_WORK,
)
from agri_data_service.warehouse.plant_suitability.schemas import (
    CELL_RECOMMENDATIONS_SCHEMA,
    GUILDS,
    SITE_INPUT_GROUPS,
)
from agri_data_service.warehouse.plant_suitability.site import SiteInputProvenance
from tests.plant_suitability.support import candidate, cell_row, served_labels

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
# For subjects that come from licence-unrecorded guides, which real PRODUCTION drops at load.
PRODUCTION_WITHOUT_LICENCE_GATE = replace(PRODUCTION, permitted_licences=None)
# One changed value per RuleConfig field; the fingerprint flow fails if a field has no entry here.
FINGERPRINT_VARIANTS = {
    "name": "production_preview",
    "in_region_overrides": frozenset(),
    "range_wide_origin_is_not_state_claim": False,
    "inherit_stratification": False,
    "null_restriction_depth_is_unknown": False,
    "introduced_flag_text": MappingProxyType(dict.fromkeys(GUILDS, INTRODUCED_FLAG_PLAIN)),
    "pick_definition": V0_PICK_DEFINITION,
    "permitted_licences": None,
    "required_site_input_groups": frozenset(),
    "label_unchecked_axes": False,
    "declared_empty_exclusion_states": frozenset({"OR"}),
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
    return suitability.prepared(PRODUCTION).allow_list


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
    # Ungated so PRISM is served: TN 2A's 7-12 in bands never apply on ERA5, which is too wet in the Bend rain shadow.
    config = PRODUCTION_WITHOUT_LICENCE_GATE

    v0 = candidate(suitability.candidates(cell_id, V0_FROZEN), tn_2a_taxon, "hedgerow_buffer")
    production = candidate(suitability.candidates(cell_id, config), tn_2a_taxon, "hedgerow_buffer")

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
    production = suitability.candidates(shipped_null, PRODUCTION)

    assert v0_measured.filter(pl.col("axis_salinity") == 0).height > 0
    for axis in SOIL_AXES:
        assert production[f"axis_{axis}"].null_count() == production.height
    assert production.filter(pl.col("unknown_axis_count") < len(SOIL_AXES)).height == 0


def test_a_none_recorded_restriction_is_unknown_in_production_and_counted(
    suitability: SuitabilityFixture, v0_cells: pl.DataFrame, production_cells: pl.DataFrame
) -> None:
    cell_id = suitability.role_cell("boise", "restriction_none_recorded_cell")

    v0 = suitability.candidates(cell_id, V0_FROZEN)
    production = suitability.candidates(cell_id, PRODUCTION)
    v0_cell, production_cell = cell_row(v0_cells, cell_id), cell_row(production_cells, cell_id)

    assert set(v0["axis_root_depth"].to_list()) == {1}
    assert production["axis_root_depth"].null_count() == production.height
    assert any(v0_cell[f"{guild}_count_fully_known"] > 0 for guild in GUILDS)
    assert [production_cell[f"{guild}_count_fully_known"] for guild in GUILDS] == [0, 0, 0]


def test_production_refuses_to_serve_picks_blind_to_the_prism_precipitation_it_holds_no_licence_for(
    suitability: SuitabilityFixture,
) -> None:
    cell_id = suitability.role_cell("boise", "burned_cell")
    prism = suitability.site_provenance
    undeclared = SiteInputProvenance(
        {group: source for group, source in prism.groups.items() if group != "precipitation"}
    )
    tables = (suitability.envelope(PRODUCTION), suitability.guide_rows, suitability.exclusions, PRODUCTION)

    for provenance, reason in (
        (prism, r"withheld by its licence gate \{'precipitation': 'unrecorded'\}"),
        (undeclared, r"undeclared \['precipitation'\]"),
    ):
        with pytest.raises(ValueError, match=reason):
            prepare(*tables, provenance)
        with pytest.raises(ValueError, match=reason):
            evaluate_cells(suitability.site, *tables, provenance)
        with pytest.raises(ValueError, match=reason):
            candidates_for_cell(suitability.site_row(cell_id), *tables, provenance)


@pytest.mark.parametrize("group", sorted(SITE_INPUT_GROUPS))
def test_production_requires_every_site_input_group_the_envelope_reads(
    suitability: SuitabilityFixture, group: str
) -> None:
    groups = dict(suitability.provenance(PRODUCTION).groups)
    groups[group] = replace(groups[group], licence=UNRECORDED)
    tables = (suitability.envelope(PRODUCTION), suitability.guide_rows, suitability.exclusions, PRODUCTION)

    with pytest.raises(ValueError, match=rf"withheld by its licence gate \{{'{group}': 'unrecorded'\}}"):
        prepare(*tables, SiteInputProvenance(groups))


def test_a_production_pick_label_names_every_axis_it_could_not_check(suitability: SuitabilityFixture) -> None:
    # Bend's burned cell has picks with every axis known and picks with some unknown (Boise serves no EC or CaCO3).
    cell_id = suitability.role_cell("bend", "burned_cell")

    v0 = suitability.candidates(cell_id, V0_FROZEN).filter(pl.col("is_pick"))
    production = suitability.candidates(cell_id, PRODUCTION).filter(pl.col("is_pick"))

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

    for config in (V0_FROZEN, PRODUCTION):
        labels = suitability.candidates(missing, config).filter(pl.col("is_pick"))["pick_label"].to_list()
        assert labels
        assert all(SITE_FROST_FREE_MISSING in label for label in labels)
        assert not [label for label in labels if UNKNOWN_FROST_FREE in label]
    production = suitability.candidates(missing, PRODUCTION).filter(pl.col("is_pick"))["pick_label"].to_list()
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

    gated = candidate(blm_admitted.candidates(cell_id, PRODUCTION), LEYMUS, "post_fire_restoration")
    lent = candidate(both_admitted.candidates(cell_id, PRODUCTION), LEYMUS, "post_fire_restoration")

    assert gated["is_pick"]
    assert gated["axis_source_applicability"] == 1
    assert STRATIFICATION_PREFIX not in gated["pick_label"]
    assert not lent["is_pick"]
    assert lent["axis_source_applicability"] == 0


def test_no_origin_label_cites_a_licence_excluded_document(suitability: SuitabilityFixture) -> None:
    cell_id = suitability.role_cell("boise", "burned_cell")
    excluded_ids = sorted(suitability.prepared(PRODUCTION).excluded_sources)
    excluded_names = set(suitability.guide_rows.filter(pl.col("source_id").is_in(excluded_ids))["source_short_name"])

    def citing_excluded(candidates: pl.DataFrame) -> list[str]:
        labels = candidates["origin_label"].drop_nulls().to_list()
        return [label for label in labels if any(name in label for name in excluded_names)]

    assert citing_excluded(suitability.candidates(cell_id, PRODUCTION_WITHOUT_LICENCE_GATE))
    assert not citing_excluded(suitability.candidates(cell_id, PRODUCTION))


def test_range_wide_native_statements_no_longer_exempt_a_taxon_plants_does_not_record_in_the_state(
    suitability: SuitabilityFixture,
) -> None:
    cell_id = suitability.role_cell("boise", "burned_cell")
    v0 = suitability.candidates(cell_id, V0_FROZEN)
    production = suitability.candidates(cell_id, PRODUCTION)
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
    production = suitability.candidates(cell_id, PRODUCTION).filter(introduced)

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

    woody = without_page.candidates(cell_id, PRODUCTION).filter(pl.col("guild") == "hedgerow_buffer")

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
    assert_no_fire_claims(production_cells, suitability.prepared(PRODUCTION).metadata(), production_allow_list)

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

    metadata = suitability.relicensed({source_id}, licence).prepared(PRODUCTION).metadata()

    assert (source_id not in json.loads(metadata[METADATA_EXCLUDED_SOURCES])) is admitted
    assert (source_id in json.loads(metadata[METADATA_ADMITTED_SOURCES])) is admitted


def test_production_drops_licence_unrecorded_guides_and_keeps_federal_ones(suitability: SuitabilityFixture) -> None:
    cell_id = suitability.role_cell("boise", "licence_cell")
    dropped_source = suitability.taxa("boise", "licence_dropped_source_id")
    kept_source = suitability.taxa("boise", "licence_kept_source_id")
    woody = pl.col("guild") == "hedgerow_buffer"

    v0 = suitability.candidates(cell_id, V0_FROZEN).filter(woody)
    production = suitability.candidates(cell_id, PRODUCTION).filter(woody)
    metadata = suitability.prepared(PRODUCTION).metadata()

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
    engine = suitability.prepared(PRODUCTION)
    ungated_engine = suitability.prepared(PRODUCTION_WITHOUT_LICENCE_GATE)
    cell_id = suitability.role_cell("boise", "burned_cell")

    table = engine.layer_table(suitability.served_site(suitability.site, PRODUCTION))
    ungated_table = ungated_engine.layer_table(suitability.site)
    _, point_metadata = engine.candidates_with_metadata(
        suitability.served_site(suitability.site_row(cell_id), PRODUCTION)
    )

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
    production = suitability.prepared(PRODUCTION).metadata()[METADATA_RULE_CONFIG]

    variants = {
        name: suitability.prepared(replace(PRODUCTION, **{name: value})).metadata()[METADATA_RULE_CONFIG]
        for name, value in FINGERPRINT_VARIANTS.items()
    }

    assert sorted(variants) == sorted(field.name for field in dataclasses.fields(RuleConfig))
    for name, fingerprint in variants.items():
        assert fingerprint != production, name
        assert fingerprint.startswith(f"{FINGERPRINT_VARIANTS['name'] if name == 'name' else 'production'} sha256:")


def test_the_inputs_digest_follows_every_loaded_input_and_the_site_declaration_never_row_or_list_order(
    suitability: SuitabilityFixture,
) -> None:
    engine = suitability.prepared(PRODUCTION)
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
    corvallis = suitability.served_site(suitability.site.filter(pl.col("region") == "corvallis").head(1), PRODUCTION)
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
    re_released = replace(
        suitability, era5_precipitation_source=replace(suitability.era5_precipitation_source, release="a later pull")
    )

    dropped_row_engine = dropped_row_added.prepared(PRODUCTION)

    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert reordered.prepared(PRODUCTION).metadata()[METADATA_INPUTS_SHA256] == digest
    assert federal_dropped.prepared(PRODUCTION).metadata()[METADATA_INPUTS_SHA256] != digest
    assert re_released.prepared(PRODUCTION).metadata()[METADATA_INPUTS_SHA256] != digest
    assert engine.evaluate_cells(corvallis)["greenstrip_status"].to_list() == ["no_regional_guide"]
    assert dropped_row_engine.evaluate_cells(corvallis)["greenstrip_status"].to_list() == ["licence_excluded"]
    assert dropped_row_engine.metadata()[METADATA_INPUTS_SHA256] != digest
