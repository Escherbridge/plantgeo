"""Flow: the per-point tool and the batch build answer the same cell identically (one code path)."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import polars as pl
import pytest

from agri_data_service.warehouse.plant_suitability.config import V0_FROZEN
from agri_data_service.warehouse.plant_suitability.engine import STATUS_LICENCE_EXCLUDED, STATUS_NO_ELIGIBLE_TAXA
from agri_data_service.warehouse.plant_suitability.licences import ALL_RIGHTS_RESERVED, US_GOVERNMENT_WORK
from agri_data_service.warehouse.plant_suitability.ranking import TOP_PICK_COUNT
from agri_data_service.warehouse.plant_suitability.schemas import GUILDS
from tests.plant_suitability.support import PRODUCTION_ON_FIXTURE, cell_row

if TYPE_CHECKING:
    from agri_data_service.warehouse.plant_suitability.config import RuleConfig
    from tests.plant_suitability.support import SuitabilityFixture

ROLE_CELLS = (
    ("boise", "burned_cell"),
    ("boise", "saline_cell"),
    ("boise", "in_region_uncertain_versus_neighbour_cell"),
    ("bend", "uncertain_frost_free_cell"),
    ("bend", "east_of_cascade_crest_cell"),
    ("corvallis", "no_regional_guide_cell"),
    ("corvallis", "red_osier_dogwood_review_cell"),
)


@pytest.mark.parametrize("config", [V0_FROZEN, PRODUCTION_ON_FIXTURE], ids=["v0_frozen", "production"])
def test_the_point_tool_ranks_the_same_top_three_the_batch_build_serves(
    suitability: SuitabilityFixture, config: RuleConfig
) -> None:
    served = suitability.evaluate(config)

    for region, role in ROLE_CELLS:
        cell_id = suitability.role_cell(region, role)
        batch = cell_row(served, cell_id)
        ranked = suitability.candidates(cell_id, config)
        for guild in GUILDS:
            rows = ranked.filter(pl.col("guild") == guild)
            picks = rows.filter(pl.col("is_pick")).sort("rank")
            top = picks.head(TOP_PICK_COUNT)
            assert rows["status"].unique().to_list() == [batch[f"{guild}_status"]]
            assert picks.height == (batch[f"{guild}_count"] or 0)
            assert ";".join(top["display_name"].to_list()) == (batch[f"{guild}_top3"] or "")
            assert top["pick_label"].to_list() == (batch[f"{guild}_top3_labels"] or [])
            assert picks["rank"].to_list() == list(range(1, picks.height + 1))


def test_the_point_tool_says_why_a_guild_or_a_withheld_cell_has_no_candidates(suitability: SuitabilityFixture) -> None:
    no_guide = suitability.candidates(
        suitability.role_cell("corvallis", "no_regional_guide_cell"), PRODUCTION_ON_FIXTURE
    )
    withheld_id = suitability.role_cell("bend", "withheld_cell")
    withheld = suitability.candidates(withheld_id, PRODUCTION_ON_FIXTURE)

    greenstrip = no_guide.filter(pl.col("guild") == "greenstrip").select("status", "scoring_null_reason", "plant_id")
    assert greenstrip.rows() == [("no_regional_guide", None, None)]
    assert no_guide.filter(pl.col("guild") == "hedgerow_buffer")["status"].unique().to_list() == ["scored"]
    reason = suitability.site_row(withheld_id)["scoring_null_reason"][0]
    assert reason is not None
    status_rows = withheld.select("guild", "status", "scoring_null_reason", "plant_id").rows()
    assert status_rows == [(guild, None, reason, None) for guild in GUILDS]


def with_an_admitted_unmatched_row(fixture: SuitabilityFixture, region: str, guild: str) -> SuitabilityFixture:
    """The fixture plus one admitted guide row for the guild and region that names no PLANTS taxon (review p09)."""
    rows = fixture.guide_rows
    template = rows.filter((pl.col("guild") == guild) & pl.col("applies_to_regions").list.contains(region)).head(1)
    unmatched = template.with_columns(
        (pl.col("guide_row_id") + rows["guide_row_id"].max() + 1).alias("guide_row_id"),
        pl.lit("synthetic_unmatched_list").alias("source_id"),
        pl.lit("Synthetic unmatched list").alias("source_short_name"),
        pl.lit(US_GOVERNMENT_WORK).alias("license"),
        pl.lit([], dtype=pl.List(pl.Int64)).alias("matched_plant_ids"),
        pl.lit("unmatchable_rank").alias("match_route"),
    )
    return replace(fixture, guide_rows=pl.concat([rows, unmatched]))


def test_both_seams_say_whether_a_guild_is_licence_excluded_or_left_without_eligible_taxa(
    suitability: SuitabilityFixture,
) -> None:
    cell_id = suitability.role_cell("corvallis", "no_regional_guide_cell")
    restoration = pl.col("guild") == "post_fire_restoration"
    naming_restoration = restoration & pl.col("applies_to_regions").list.contains("corvallis")
    restoration_sources = suitability.guide_rows.filter(naming_restoration)["source_id"].unique().to_list()
    pool = suitability.candidates(cell_id, PRODUCTION_ON_FIXTURE).filter(restoration)["display_name"].to_list()
    listings = pl.DataFrame({"state": ["OR"] * len(pool), "scientific_name": pool})
    excluded = suitability.relicensed(restoration_sources, ALL_RIGHTS_RESERVED)
    variants = [
        (STATUS_LICENCE_EXCLUDED, excluded),
        # The gate removed every row naming a taxon; an admitted row naming none is no eligible taxon to report.
        (STATUS_LICENCE_EXCLUDED, with_an_admitted_unmatched_row(excluded, "corvallis", "post_fire_restoration")),
        (
            STATUS_NO_ELIGIBLE_TAXA,
            replace(suitability, exclusions=pl.concat([suitability.exclusions, listings], how="diagonal_relaxed")),
        ),
    ]

    for status, variant in variants:
        batch = cell_row(variant.evaluate(PRODUCTION_ON_FIXTURE, site=suitability.site_row(cell_id)), cell_id)
        point = variant.candidates(cell_id, PRODUCTION_ON_FIXTURE).filter(restoration)
        assert (batch["greenstrip_status"], batch["post_fire_restoration_status"]) == ("no_regional_guide", status)
        assert batch["post_fire_restoration_pool_size"] == 0
        assert batch["post_fire_restoration_count"] is None
        assert point.select("status", "plant_id").rows() == [(status, None)]
