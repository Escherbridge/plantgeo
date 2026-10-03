"""Flow: building the envelope's NWPL columns from the NWPL list reproduces the ratings v0 scored with."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
from polars.testing import assert_frame_equal

from agri_data_service.warehouse.plant_suitability.config import PRODUCTION, V0_FROZEN
from agri_data_service.warehouse.plant_suitability.wetland import resolve_wetland_ratings

if TYPE_CHECKING:
    from tests.plant_suitability.support import SuitabilityFixture

RATING_COLUMNS = ["plant_id", "nwpl_aw", "nwpl_wmvc", "nwpl_route"]
# NWPL files these under Peritoma, a name only licence-unrecorded Xerces guides print for them.
RATED_ONLY_THROUGH_EXCLUDED_NAMES = ("Cleome lutea", "Cleome serrulata")


def unrated(suitability: SuitabilityFixture) -> pl.DataFrame:
    """The fixture envelope as if resolve_wetland_ratings had never run."""
    return suitability.species.with_columns(
        [pl.lit(None, dtype=pl.String).alias(column) for column in RATING_COLUMNS[1:]]
    )


def test_resolved_ratings_match_v0_including_the_red_osier_and_labrador_tea_renames(
    suitability: SuitabilityFixture,
) -> None:
    resolved = resolve_wetland_ratings(
        unrated(suitability), suitability.guide_rows, suitability.wetland_list, V0_FROZEN
    )

    frozen = suitability.species.select(RATING_COLUMNS).sort("plant_id")
    assert_frame_equal(resolved.select(RATING_COLUMNS).sort("plant_id"), frozen)
    ratings = {
        row["accepted_name"]: (row["nwpl_aw"], row["nwpl_wmvc"], row["nwpl_route"])
        for row in resolved.filter(pl.col("accepted_name").str.starts_with("Cornus sericea")).to_dicts()
        + resolved.filter(pl.col("accepted_name") == "Ledum glandulosum").to_dicts()
    }
    assert ratings["Cornus sericea ssp. sericea"] == ("FACW", "FACW", "nwpl_name_resolution")
    assert ratings["Ledum glandulosum"] == ("FACW", "OBL", "nwpl_name_resolution")


def test_production_resolves_ratings_only_from_names_its_licence_gate_admits(suitability: SuitabilityFixture) -> None:
    arguments = (unrated(suitability), suitability.guide_rows, suitability.wetland_list)

    v0 = resolve_wetland_ratings(*arguments, V0_FROZEN).filter(
        pl.col("accepted_name").is_in(RATED_ONLY_THROUGH_EXCLUDED_NAMES)
    )
    production = resolve_wetland_ratings(*arguments, PRODUCTION).filter(
        pl.col("accepted_name").is_in(RATED_ONLY_THROUGH_EXCLUDED_NAMES)
    )

    assert set(v0["nwpl_route"]) == {"regional_list_name"}
    assert set(production["nwpl_route"]) == {"not_listed"}
    assert production["nwpl_aw"].null_count() == production.height == len(RATED_ONLY_THROUGH_EXCLUDED_NAMES)
