"""The `direct:` id each config lane writes is byte-identical to its legacy `pipeline/direct/<lane>/rows.py` twin.

Lane modules may never import `pipeline/direct/**`, so each restates the prefix with a "keep synced by hand"
comment; a drift would manufacture a G6 `--compare` delta on every row. This test is the executable form of
those four comments. See `pipeline/lanes/_shared/AGENTS.md`, "The `direct:` feature-id prefix".
"""

from __future__ import annotations

from agri_data_service.pipeline.direct.burn_severity import rows as legacy_burn_severity_rows
from agri_data_service.pipeline.direct.drought import rows as legacy_drought_rows
from agri_data_service.pipeline.direct.fire_perimeters import rows as legacy_fire_perimeters_rows
from agri_data_service.pipeline.direct.watersheds import rows as legacy_watersheds_rows
from agri_data_service.pipeline.lanes.burn_severity import release_rows as lane_burn_severity_rows
from agri_data_service.pipeline.lanes.drought import usdm_releases as lane_drought
from agri_data_service.pipeline.lanes.fire_perimeters import wfigs as lane_fire_perimeters
from agri_data_service.pipeline.lanes.watersheds import nhdplus as lane_watersheds


def test_watersheds_feature_ids_match_the_legacy_writer() -> None:
    assert lane_watersheds.DIRECT_FEATURE_ID_PREFIX == legacy_watersheds_rows.DIRECT_FEATURE_ID_PREFIX
    assert lane_watersheds.direct_feature_id("170900011201") == legacy_watersheds_rows.direct_feature_id("170900011201")


def test_drought_area_ids_match_the_legacy_writer() -> None:
    assert lane_drought._DIRECT_AREA_ID_PREFIX == legacy_drought_rows.DIRECT_AREA_ID_PREFIX
    assert lane_drought._direct_area_id("2024-07-02", 3) == legacy_drought_rows.direct_area_id("2024-07-02", 3)


def test_burn_severity_feature_ids_match_the_legacy_writer() -> None:
    assert lane_burn_severity_rows.DIRECT_FEATURE_ID_PREFIX == legacy_burn_severity_rows.DIRECT_FEATURE_ID_PREFIX
    assert lane_burn_severity_rows.direct_feature_id("OR4321") == legacy_burn_severity_rows.direct_feature_id("OR4321")


def test_fire_perimeters_feature_ids_match_the_legacy_writer() -> None:
    """The lane composes `f"{PREFIX}:{identifier}"` inline (`wfigs.py::_conform`); the legacy writer has a function."""
    identifier = "{2024-ORDEF-000123}"
    assert lane_fire_perimeters.DIRECT_FEATURE_ID_PREFIX == legacy_fire_perimeters_rows.DIRECT_FEATURE_ID_PREFIX
    assert f"{lane_fire_perimeters.DIRECT_FEATURE_ID_PREFIX}:{identifier}" == (
        legacy_fire_perimeters_rows.direct_feature_id(identifier)
    )
