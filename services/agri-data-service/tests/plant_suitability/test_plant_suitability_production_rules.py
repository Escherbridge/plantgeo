"""Flows: each owner rule that PRODUCTION adds over V0_FROZEN, observed on real fixture cells through both seams."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import TYPE_CHECKING

import polars as pl
import pytest

from agri_data_service.warehouse.plant_suitability.config import (
    INTRODUCED_FLAG_PLAIN,
    PRODUCTION,
    V0_FROZEN,
)
from agri_data_service.warehouse.plant_suitability.engine import (
    layer_metadata,
    licence_excluded_sources,
    with_layer_metadata,
)
from agri_data_service.warehouse.plant_suitability.labels import (
    CONFIRMED_FROST_FREE,
    FIRE_LABEL_PREFIX,
    IN_REGION_NO,
    IN_REGION_YES,
    NO_FIRE_CLAIM,
    UNCERTAIN_FROST_FREE,
    assert_no_fire_claims,
    find_fire_claims,
)
from agri_data_service.warehouse.plant_suitability.schemas import GUILDS
from tests.plant_suitability.support import candidate, cell_row, served_labels

if TYPE_CHECKING:
    from tests.plant_suitability.support import SuitabilityFixture

SOIL_AXES = ("salinity", "calcareous", "root_depth")
CPS_394 = "(CPS 394)"
LEYMUS = "Leymus triticoides"
ODFW_SALINE_MIX_TAG = "ODFW 2017 p.7 [neighbour]"
TN_2A_NEIGHBOUR_TAG = re.compile(r"NRCS TN PM-2A \(2017\) p\.\d+(?:-\d+)? \[neighbour\]")
# For subjects that come from licence-unrecorded guides, which real PRODUCTION drops at load.
PRODUCTION_WITHOUT_LICENCE_GATE = replace(PRODUCTION, permitted_licences=None)


def test_bend_reads_tn_2a_as_an_in_region_guide(suitability: SuitabilityFixture) -> None:
    cell_id = suitability.role_cell("bend", "east_of_cascade_crest_cell")
    tn_2a_taxon = suitability.taxa("bend", "east_of_cascade_crest_taxon")

    v0 = candidate(suitability.candidates(cell_id, V0_FROZEN), tn_2a_taxon, "hedgerow_buffer")
    production = candidate(suitability.candidates(cell_id, PRODUCTION), tn_2a_taxon, "hedgerow_buffer")

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
    assert f"site stratification from {ODFW_SALINE_MIX_TAG}" in production_saline["pick_label"]


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


def test_the_fire_claim_guard_passes_the_served_layer_and_rejects_an_effect_claim(
    suitability: SuitabilityFixture, production_cells: pl.DataFrame
) -> None:
    assert_no_fire_claims(
        production_cells, layer_metadata(PRODUCTION, licence_excluded_sources(suitability.guide_rows, PRODUCTION))
    )

    first_row = pl.int_range(pl.len()) == 0
    claimed = production_cells.with_columns(
        pl.when(first_row)
        .then(pl.lit("serviceberry; a hedge that reduces wildfire spread"))
        .otherwise(pl.col("hedgerow_buffer_top3_common_names"))
        .alias("hedgerow_buffer_top3_common_names")
    )
    with pytest.raises(ValueError, match="claim a fire effect"):
        assert_no_fire_claims(claimed)


@pytest.mark.parametrize(
    ("text", "is_claim"),
    [
        (f"{FIRE_LABEL_PREFIX}Yes | native (per NRCS TN PM-2A (2017))", False),
        (NO_FIRE_CLAIM, False),
        ("sources: BLM Soda Fire ESR 2016 p.5 + NRCS westside post-fire 2024 p.1", False),
        ("seeded after the Soda Fire to stabilise soil and limit weeds", False),
        ("fire-following annual; post-fire seeding", False),
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
    ],
)
def test_the_fire_claim_pattern_separates_claims_from_the_allowed_wording(text: str, is_claim: bool) -> None:
    assert (find_fire_claims([text]) == [text]) is is_claim


@pytest.mark.parametrize(
    ("licence", "admitted"),
    [
        ("US Government work", True),
        ("  cc by ", True),
        ("CC BY-NC 4.0", False),
        ("CC BY-NC4.0", False),
        ("non commercial use only", False),
        ("not for commercial use", False),
        ("All rights reserved", False),
        (None, False),
    ],
)
def test_the_licence_gate_admits_only_an_exact_permitted_licence(
    suitability: SuitabilityFixture, licence: str | None, admitted: bool
) -> None:
    source_id = suitability.taxa("boise", "licence_kept_source_id")
    relicensed = suitability.guide_rows.with_columns(
        pl.when(pl.col("source_id") == source_id)
        .then(pl.lit(licence, dtype=pl.String))
        .otherwise(pl.col("license"))
        .alias("license")
    )

    excluded = licence_excluded_sources(relicensed, PRODUCTION)

    assert (source_id not in excluded) is admitted


def test_production_drops_licence_unrecorded_guides_and_keeps_federal_ones(suitability: SuitabilityFixture) -> None:
    cell_id = suitability.role_cell("boise", "licence_cell")
    dropped_source = suitability.taxa("boise", "licence_dropped_source_id")
    kept_source = suitability.taxa("boise", "licence_kept_source_id")
    woody = pl.col("guild") == "hedgerow_buffer"

    v0 = suitability.candidates(cell_id, V0_FROZEN).filter(woody)
    production = suitability.candidates(cell_id, PRODUCTION).filter(woody)
    metadata = layer_metadata(PRODUCTION, licence_excluded_sources(suitability.guide_rows, PRODUCTION))

    excluded = json.loads(metadata["plantgeo:excluded_sources"])
    dropped_licences = suitability.guide_rows.filter(pl.col("source_id") == dropped_source)["license"].unique()
    assert excluded[dropped_source] == dropped_licences.item()
    assert kept_source not in excluded
    for taxon in suitability.taxa("boise", "licence_dropped_source_only_taxa"):
        assert candidate(v0, taxon)["is_pick"]
        assert taxon not in production["display_name"].to_list()
    for taxon in suitability.taxa("boise", "licence_kept_source_only_taxa"):
        assert candidate(production, taxon)["is_pick"]


def test_served_metadata_states_that_absence_is_not_evidence_of_unsuitability(
    suitability: SuitabilityFixture, production_cells: pl.DataFrame
) -> None:
    excluded_sources = licence_excluded_sources(suitability.guide_rows, PRODUCTION)
    metadata = with_layer_metadata(production_cells, PRODUCTION, excluded_sources).schema.metadata

    pick_definition = metadata[b"plantgeo:pick_definition"].decode()
    assert "not field-tested recommendations" in pick_definition
    assert "absence from a cell is not evidence that it is unsuitable" in pick_definition
    assert metadata[b"plantgeo:rule_config"] == b"production"
