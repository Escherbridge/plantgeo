"""Flows: the curation-region facts the rules read (in-region readings, habitat MLRAs) come from release data."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import polars as pl

from agri_data_service.warehouse.plant_suitability.config import EAST_OF_CASCADE_CREST_READING
from agri_data_service.warehouse.plant_suitability.engine import METADATA_INPUTS_SHA256
from agri_data_service.warehouse.plant_suitability.labels import IN_REGION_NO, IN_REGION_YES
from agri_data_service.warehouse.plant_suitability.regions import pilot_region_data
from tests.plant_suitability.support import PRODUCTION_ON_FIXTURE, candidate, cell_row

if TYPE_CHECKING:
    from tests.plant_suitability.support import SuitabilityFixture

TN_2A_SOURCE_ID = "idpm_tn2a_2017"
WOODY = "hedgerow_buffer"
RESTORATION = "post_fire_restoration"
# A post-fire restoration pick carried at the Boise 43B cells only through forest_woodland guide rows.
FOREST_WOODLAND_TAXON = "Elymus glaucus"
FOREST_WOODLAND_MLRA = "43B"


def test_the_east_of_crest_reading_is_release_data_that_puts_tn_2a_in_region_at_bend(
    suitability: SuitabilityFixture,
) -> None:
    cell_id = suitability.role_cell("bend", "east_of_cascade_crest_cell")
    tn_2a_taxon = suitability.taxa("bend", "east_of_cascade_crest_taxon")
    pilot = pilot_region_data()
    reading = pilot.in_region_readings[EAST_OF_CASCADE_CREST_READING]
    narrowed_reading = replace(reading, source_ids=tuple(set(reading.source_ids) - {TN_2A_SOURCE_ID}))
    without_tn_2a = replace(suitability, region_data=replace(pilot, in_region_readings={
        EAST_OF_CASCADE_CREST_READING: narrowed_reading,
    }))  # fmt: skip

    in_region = candidate(suitability.candidates(cell_id, PRODUCTION_ON_FIXTURE), tn_2a_taxon, WOODY)
    neighbour = candidate(without_tn_2a.candidates(cell_id, PRODUCTION_ON_FIXTURE), tn_2a_taxon, WOODY)

    assert in_region["in_region_supported"]
    assert IN_REGION_YES in in_region["pick_label"]
    assert not neighbour["in_region_supported"]
    assert IN_REGION_NO in neighbour["pick_label"]
    assert "[neighbour]" in neighbour["pick_label"]
    # Same tables and rule set, other release data: the served digest says the inputs differ.
    pilot_digest = suitability.prepared(PRODUCTION_ON_FIXTURE).metadata()[METADATA_INPUTS_SHA256]
    assert without_tn_2a.prepared(PRODUCTION_ON_FIXTURE).metadata()[METADATA_INPUTS_SHA256] != pilot_digest


def test_the_forest_woodland_mlras_are_release_data_that_decide_where_a_guide_row_applies(
    suitability: SuitabilityFixture,
) -> None:
    boise_forest = (pl.col("region") == "boise") & (pl.col("mlra") == FOREST_WOODLAND_MLRA)
    cell_id = suitability.site.filter(boise_forest)["cell_id"][0]
    pilot = pilot_region_data()
    narrowed_mlras = tuple(mlra for mlra in pilot.forest_woodland_mlras() if mlra != FOREST_WOODLAND_MLRA)
    without_43b = replace(suitability, region_data=replace(pilot, habitat_mlras={"forest_woodland": narrowed_mlras}))
    site = suitability.site_row(cell_id)

    listed = candidate(suitability.candidates(cell_id, PRODUCTION_ON_FIXTURE), FOREST_WOODLAND_TAXON, RESTORATION)
    unlisted = candidate(without_43b.candidates(cell_id, PRODUCTION_ON_FIXTURE), FOREST_WOODLAND_TAXON, RESTORATION)
    listed_count = cell_row(suitability.evaluate(PRODUCTION_ON_FIXTURE, site), cell_id)[f"{RESTORATION}_count"]
    unlisted_count = cell_row(without_43b.evaluate(PRODUCTION_ON_FIXTURE, site), cell_id)[f"{RESTORATION}_count"]

    assert FOREST_WOODLAND_MLRA in pilot.forest_woodland_mlras()
    assert listed["is_pick"]
    assert listed["axis_source_applicability"] == 1
    assert not unlisted["is_pick"]
    assert unlisted["axis_source_applicability"] == 0
    assert unlisted_count < listed_count
