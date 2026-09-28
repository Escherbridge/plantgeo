"""Flows: inputs that must never reach a served table are refused at load, removed from pools, or stop both seams."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import polars as pl
import pytest

from agri_data_service.warehouse.plant_suitability.config import PRODUCTION, V0_FROZEN
from agri_data_service.warehouse.plant_suitability.schemas import GUILDS
from tests.plant_suitability.support import cell_row

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.plant_suitability.support import SuitabilityFixture

NOXIOUS_STATE = "ID"


def replaced_value(frame: pl.DataFrame, column: str, old: str, new: str | None) -> pl.DataFrame:
    """The frame with every `old` in the column replaced by `new`."""
    return frame.with_columns(
        pl.when(pl.col(column) == old).then(pl.lit(new, dtype=pl.String)).otherwise(pl.col(column)).alias(column)
    )


def unresolved_wetland_route(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """Every envelope row as if resolve_wetland_ratings had never run."""
    return replace(fixture, species=fixture.species.with_columns(pl.lit(None, dtype=pl.String).alias("nwpl_route")))


def hyphenated_origin_scope(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """'range-wide' for 'range_wide'."""
    return replace(fixture, guide_rows=replaced_value(fixture.guide_rows, "origin_scope", "range_wide", "range-wide"))


def short_guild_name(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """'hedgerow' for 'hedgerow_buffer'."""
    return replace(fixture, guide_rows=replaced_value(fixture.guide_rows, "guild", "hedgerow_buffer", "hedgerow"))


def unknown_plant_role(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """'shrubs' for 'shrub'."""
    return replace(fixture, guide_rows=replaced_value(fixture.guide_rows, "plant_role", "shrub", "shrubs"))


def unknown_match_route(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """A route name the matcher never emits."""
    return replace(fixture, guide_rows=replaced_value(fixture.guide_rows, "match_route", "accepted_name", "exact"))


def region_spanning_two_states(fixture: SuitabilityFixture) -> SuitabilityFixture:
    """Two Boise cells, one of them relabelled Oregon."""
    boise = fixture.site.filter(pl.col("region") == "boise").head(2)
    relabelled = boise.with_columns(
        pl.when(pl.int_range(pl.len()) == 0).then(pl.lit("OR")).otherwise("state").alias("state")
    )
    return replace(fixture, site=relabelled)


@pytest.mark.parametrize(
    ("corrupt", "message"),
    [
        pytest.param(unresolved_wetland_route, r"nulls in required columns \['nwpl_route'\]", id="null_nwpl_route"),
        pytest.param(hyphenated_origin_scope, r"unknown origin_scope values \['range-wide'\]", id="origin_scope"),
        pytest.param(short_guild_name, r"unknown guild values \['hedgerow'\]", id="guild"),
        pytest.param(unknown_plant_role, r"unknown plant_role values \['shrubs'\]", id="plant_role"),
        pytest.param(unknown_match_route, r"unknown match_route values \['exact'\]", id="match_route"),
        pytest.param(region_spanning_two_states, r"region boise spans states", id="region_spans_two_states"),
    ],
)
def test_malformed_inputs_are_refused_before_any_cell_is_scored(
    suitability: SuitabilityFixture, corrupt: Callable[[SuitabilityFixture], SuitabilityFixture], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        corrupt(suitability).evaluate(PRODUCTION)


def served_top_three(cells: pl.DataFrame, cell_id: str) -> set[str]:
    """Every name any guild serves in its top three at the cell."""
    row = cell_row(cells, cell_id)
    return {name for guild in GUILDS for name in (row[f"{guild}_top3"] or "").split(";") if name}


def named(names: set[str], subject: str) -> set[str]:
    """The names that are the subject taxon, or that belong to the subject genus."""
    return {name for name in names if subject in (name, name.split(" ")[0])}


def test_state_noxious_listings_remove_a_taxon_from_the_pool_and_every_top_three(
    suitability: SuitabilityFixture, v0_cells: pl.DataFrame
) -> None:
    binomial_taxon = suitability.taxa("boise", "noxious_binomial_taxon")
    genus = suitability.taxa("boise", "noxious_genus")
    annotated_taxon = suitability.taxa("boise", "noxious_annotation_taxon")
    annotated_id = suitability.species.filter(pl.col("accepted_name") == annotated_taxon)["plant_id"].item()
    listings = pl.DataFrame(
        {"state": [NOXIOUS_STATE, NOXIOUS_STATE], "scientific_name": [binomial_taxon, f"{genus} spp."]},
        schema={"state": pl.String, "scientific_name": pl.String},
    )
    annotated_rows = pl.col("matched_plant_ids").list.contains(annotated_id).fill_null(value=False)
    listed = replace(
        suitability,
        exclusions=pl.concat([suitability.exclusions, listings], how="diagonal_relaxed"),
        guide_rows=suitability.guide_rows.with_columns(
            pl.when(annotated_rows)
            .then(pl.lit([NOXIOUS_STATE], dtype=pl.List(pl.String)))
            .otherwise(pl.col("noxious_states"))
            .alias("noxious_states")
        ),
    )

    listed_cells = listed.evaluate(V0_FROZEN)

    boise_cells = v0_cells.filter(pl.col("region") == "boise")["cell_id"].to_list()
    for subject in (binomial_taxon, genus, annotated_taxon):
        picked_at = [cell_id for cell_id in boise_cells if named(served_top_three(v0_cells, cell_id), subject)]
        assert picked_at, f"{subject} is in no v0 top three, so this flow proves nothing"
        assert not [cell_id for cell_id in picked_at if named(served_top_three(listed_cells, cell_id), subject)]
        pool = listed.candidates(picked_at[0], V0_FROZEN)["display_name"].drop_nulls().to_list()
        assert not named(set(pool), subject)


@pytest.mark.parametrize(
    ("appended_to_woody_source_names", "message"),
    [
        pytest.param(" hedge that reduces wildfire", "claim a fire effect", id="fire_claim"),
        pytest.param(" wildfire edition", "hedgerow_buffer labels mention fire", id="fire_word_on_woody_buffer"),
    ],
)
def test_both_seams_refuse_to_serve_fire_wording_that_arrives_in_a_source_name(
    suitability: SuitabilityFixture, appended_to_woody_source_names: str, message: str
) -> None:
    woody = pl.col("guild") == "hedgerow_buffer"
    renamed = replace(
        suitability,
        guide_rows=suitability.guide_rows.with_columns(
            pl.when(woody)
            .then(pl.col("source_short_name") + appended_to_woody_source_names)
            .otherwise(pl.col("source_short_name"))
            .alias("source_short_name")
        ),
    )
    cell_id = suitability.role_cell("boise", "burned_cell")

    with pytest.raises(ValueError, match=message):
        renamed.evaluate(PRODUCTION, site=suitability.site_row(cell_id))
    with pytest.raises(ValueError, match=message):
        renamed.candidates(cell_id, PRODUCTION)
