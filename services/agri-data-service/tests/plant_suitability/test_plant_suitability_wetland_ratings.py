"""Flow: building the envelope's NWPL columns from the NWPL list reproduces the ratings v0 scored with."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
from polars.testing import assert_frame_equal

from agri_data_service.warehouse.plant_suitability.wetland import resolve_wetland_ratings

if TYPE_CHECKING:
    from tests.plant_suitability.support import SuitabilityFixture

RATING_COLUMNS = ["plant_id", "nwpl_aw", "nwpl_wmvc", "nwpl_route"]


def test_resolved_ratings_match_v0_including_the_red_osier_and_labrador_tea_renames(
    suitability: SuitabilityFixture,
) -> None:
    unrated = suitability.species.with_columns(
        [pl.lit(None, dtype=pl.String).alias(column) for column in RATING_COLUMNS[1:]]
    )

    resolved = resolve_wetland_ratings(unrated, suitability.guide_rows, suitability.wetland_list)

    frozen = suitability.species.select(RATING_COLUMNS).sort("plant_id")
    assert_frame_equal(resolved.select(RATING_COLUMNS).sort("plant_id"), frozen)
    ratings = {
        row["accepted_name"]: (row["nwpl_aw"], row["nwpl_wmvc"], row["nwpl_route"])
        for row in resolved.filter(pl.col("accepted_name").str.starts_with("Cornus sericea")).to_dicts()
        + resolved.filter(pl.col("accepted_name") == "Ledum glandulosum").to_dicts()
    }
    assert ratings["Cornus sericea ssp. sericea"] == ("FACW", "FACW", "nwpl_name_resolution")
    assert ratings["Ledum glandulosum"] == ("FACW", "OBL", "nwpl_name_resolution")
