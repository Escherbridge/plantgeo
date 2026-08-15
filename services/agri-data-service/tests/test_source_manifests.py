"""Tests for the North American intervention source-adapter manifest registry."""

# ruff: noqa: PLR2004

from pathlib import Path

from agri_data_service.execution.geospatial_pilot import SOURCE_ATTRIBUTE_ALLOWLISTS
from agri_data_service.ingest.validation.source_manifests import (
    NORTH_AMERICA_INTERVENTION_MANIFESTS,
    InterventionSourceManifest,
)

# This test file lives at services/agri-data-service/tests/, and the human-reviewed register
# lives at the repository root's docs/, three parents up from here.
REPOSITORY_MATRIX_PATH = Path(__file__).resolve().parents[3] / "docs" / "north-america-intervention-source-matrix.md"

# Previously hardcoded in geospatial_pilot.py; pinned here to prove the manifest-derived
# allowlist wiring is behavior-preserving, not a behavior change.
EXPECTED_PILOT_ALLOWLISTS: dict[str, tuple[str, ...]] = {
    "census-tigerweb-boise-2025": (
        "STATE",
        "PLACE",
        "GEOID",
        "BASENAME",
        "NAME",
        "LSADC",
        "FUNCSTAT",
        "AREALAND",
        "AREAWATER",
        "CENTLAT",
        "CENTLON",
    ),
    "osm-hillside-to-hollow-20260723": (
        "osm_type",
        "osm_id",
        "category",
        "type",
        "name",
    ),
    "usfs-wui-2020-hillside-hollow": (
        "objectid",
        "blk20",
        "state",
        "veg2019pc",
        "hu2020",
        "huden2020",
        "wuiflag2020",
        "wuiclass2020",
    ),
}


def test_registry_loads_and_every_entry_validates() -> None:
    assert len(NORTH_AMERICA_INTERVENTION_MANIFESTS) >= 60
    for key, manifest in NORTH_AMERICA_INTERVENTION_MANIFESTS.items():
        assert isinstance(manifest, InterventionSourceManifest)
        assert manifest.source_key == key


def test_exactly_three_implemented_entries_carry_attribute_allowlists() -> None:
    implemented = {
        key: manifest
        for key, manifest in NORTH_AMERICA_INTERVENTION_MANIFESTS.items()
        if manifest.adapter_status == "implemented"
    }
    assert set(implemented) == set(EXPECTED_PILOT_ALLOWLISTS)
    for key, manifest in implemented.items():
        assert manifest.attribute_allowlist == EXPECTED_PILOT_ALLOWLISTS[key]
        assert manifest.blocking_reason is None
        assert manifest.licence_snapshot_reference is not None
        assert manifest.licence_snapshot_checksum is not None


def test_every_register_row_is_present_with_a_minimum_count() -> None:
    # 6 continental + 30 US/Idaho + 12 Canada + 15 Mexico rows in the matrix as of 2026-07-23.
    assert len(NORTH_AMERICA_INTERVENTION_MANIFESTS) == 63


def test_spot_check_known_rows_match_the_matrix() -> None:
    idwr = NORTH_AMERICA_INTERVENTION_MANIFESTS["idwr-parcel-service"]
    assert idwr.adapter_status == "blocked"
    assert idwr.access_policy == "blocked"
    assert idwr.jurisdiction == "us-state"
    assert idwr.blocking_reason is not None
    assert "cannot be shared outside IDWR" in idwr.blocking_reason

    conabio = NORTH_AMERICA_INTERVENTION_MANIFESTS["conabio-satif-active-fire"]
    assert conabio.adapter_status == "blocked"
    assert conabio.licence_identifier == "CC BY-NC 2.5 MX"
    assert conabio.jurisdiction == "mexico-federal"

    usdm = NORTH_AMERICA_INTERVENTION_MANIFESTS["usdm-drought-history"]
    assert usdm.adapter_status == "blocked"
    assert usdm.blocking_reason is not None
    assert (
        "redistribution-licence-unverified" in usdm.blocking_reason or "redistribution licence" in usdm.blocking_reason
    )

    firms = NORTH_AMERICA_INTERVENTION_MANIFESTS["nasa-firms-viirs"]
    assert firms.jurisdiction == "continental"
    assert firms.adapter_status == "planned"
    assert firms.access_policy == "registration"

    boise = NORTH_AMERICA_INTERVENTION_MANIFESTS["census-tigerweb-boise-2025"]
    assert boise.adapter_status == "implemented"
    assert boise.jurisdiction == "us-federal"
    assert boise.maximum_inference_scale == "city_landscape"


def test_all_blocked_entries_carry_a_non_empty_blocking_reason() -> None:
    blocked = [m for m in NORTH_AMERICA_INTERVENTION_MANIFESTS.values() if m.adapter_status == "blocked"]
    assert len(blocked) >= 7
    for manifest in blocked:
        assert manifest.blocking_reason
        assert manifest.access_policy == "blocked"


def test_no_planned_entry_carries_a_blocking_reason_or_licence_snapshot() -> None:
    for manifest in NORTH_AMERICA_INTERVENTION_MANIFESTS.values():
        if manifest.adapter_status == "planned":
            assert manifest.blocking_reason is None
            assert manifest.licence_snapshot_reference is None
            assert manifest.licence_snapshot_checksum is None
            assert manifest.attribute_allowlist == ()


def test_geospatial_pilot_allowlists_are_derived_from_the_manifest_registry_and_unchanged() -> None:
    assert SOURCE_ATTRIBUTE_ALLOWLISTS == EXPECTED_PILOT_ALLOWLISTS


def test_matrix_document_is_readable_from_this_test_boundary() -> None:
    assert REPOSITORY_MATRIX_PATH.exists(), "the human-reviewed register this module transcribes must exist"
    text = REPOSITORY_MATRIX_PATH.read_text(encoding="utf-8")
    assert "Governance decisions from the matrix" in text
