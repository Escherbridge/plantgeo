"""Flow: the per-point tool and the batch build answer the same cell identically (one code path)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
import pytest

from agri_data_service.warehouse.plant_suitability.config import PRODUCTION, V0_FROZEN
from agri_data_service.warehouse.plant_suitability.ranking import TOP_PICK_COUNT
from agri_data_service.warehouse.plant_suitability.schemas import GUILDS
from tests.plant_suitability.support import cell_row

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


@pytest.mark.parametrize("config", [V0_FROZEN, PRODUCTION], ids=["v0_frozen", "production"])
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
    no_guide = suitability.candidates(suitability.role_cell("corvallis", "no_regional_guide_cell"), PRODUCTION)
    withheld_id = suitability.role_cell("bend", "withheld_cell")
    withheld = suitability.candidates(withheld_id, PRODUCTION)

    greenstrip = no_guide.filter(pl.col("guild") == "greenstrip").select("status", "scoring_null_reason", "plant_id")
    assert greenstrip.rows() == [("no_regional_guide", None, None)]
    assert no_guide.filter(pl.col("guild") == "hedgerow_buffer")["status"].unique().to_list() == ["scored"]
    reason = suitability.site_row(withheld_id)["scoring_null_reason"][0]
    assert reason is not None
    status_rows = withheld.select("guild", "status", "scoring_null_reason", "plant_id").rows()
    assert status_rows == [(guild, None, reason, None) for guild in GUILDS]
