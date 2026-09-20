"""Tests for the open Boise pilot writer's pre-database contracts."""

# ruff: noqa: PLR2004

from pathlib import Path

import pytest

from agri_data_service.execution.geospatial_capture import (
    geospatial_capture_root,
    load_geospatial_capture_plan,
)
from agri_data_service.execution.geospatial_pilot import (
    GAP_INPUTS,
    SOURCE_ATTRIBUTE_ALLOWLISTS,
    load_validated_pilot_bundle,
)

BOISE_CAPTURE_EVIDENCE = (
    Path(__file__).resolve().parents[3]
    / "conductor"
    / "tracks"
    / "unified_intervention_layer_20260913"
    / "evidence"
    / "boise-intervention-capture-v1.json"
)


def test_gap_register_spans_requested_property_intervention_families() -> None:
    names = set(GAP_INPUTS)
    assert "structure_inventory_and_defensible_space_inspection" in names
    assert "watershed_drainage_wetland_groundwater_and_infiltration" in names
    assert "aquaponics_hydroponics_feasibility" in names
    assert "silvopasture_agroforestry_feasibility" in names
    assert "legal_cadastral_boundary_and_use_authority" in names
    assert "exclude" in GAP_INPUTS["current_drought_weather_and_soil_moisture"]
    assert "USDM" in GAP_INPUTS["current_drought_weather_and_soil_moisture"]


def test_property_allowlist_excludes_address_owner_and_ranking_fields() -> None:
    allowed = set(SOURCE_ATTRIBUTE_ALLOWLISTS["osm-hillside-to-hollow-20260723"])
    assert allowed == {"osm_type", "osm_id", "category", "type", "name"}
    assert not {"display_name", "place_id", "importance", "owner", "address"} & allowed


def test_validated_bundle_rejects_missing_content_addressed_capture(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_validated_pilot_bundle(BOISE_CAPTURE_EVIDENCE, tmp_path)


def test_repository_capture_plan_derives_content_addressed_root() -> None:
    plan = load_geospatial_capture_plan(BOISE_CAPTURE_EVIDENCE)
    root = geospatial_capture_root(Path("capture"), plan)

    assert root.parent.name == "boise-hillside-hollow-20260723"
    assert len(root.name) == 64
