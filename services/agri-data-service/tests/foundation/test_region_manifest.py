"""The `Region` manifest: validators, `PLANTGEO_REGION` resolution, and the `pnw.json` load path.

`federation.md` §1 makes this the one typed footprint declaration; these tests pin the validators
that keep an inverted envelope or an unpitched lattice from ever becoming a loadable manifest, and
the `load_region` contract `manifest.py`'s docstring states: env var wins, missing slug is unknown,
unknown slug raises rather than falling back to the pilot.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agri_data_service.foundation.region import PNW, LayerBinding, Region, RegionEnvelope, load_region
from agri_data_service.foundation.region.manifest import REGION_ENV_VAR

EXPECTED_LATTICE_PITCH_DEGREES = 0.01  # TIER_RESOLUTION_DEGREES[9], warehouse/parquet/tiers.py
EXPECTED_ENABLED_LAYER_COUNT = 11


def test_pnw_manifest_loads_with_the_documented_values() -> None:
    assert PNW.slug == "pnw"
    assert PNW.envelope == RegionEnvelope(west=-126.0, south=41.0, east=-110.0, north=50.0)
    assert PNW.default_camera_envelope == RegionEnvelope(west=-125.0, south=42.0, east=-111.0, north=49.0)
    assert PNW.sub_envelopes["burn_severity"] == RegionEnvelope(west=-125.0, south=42.0, east=-111.0, north=49.0)
    assert PNW.sub_envelopes["botanical_seed"] == RegionEnvelope(west=-125.0, south=41.0, east=-110.0, north=50.0)
    assert PNW.lattice_pitch_degrees == EXPECTED_LATTICE_PITCH_DEGREES
    assert PNW.lattice_origin_rule == "floor_to_cell_origin"
    assert PNW.timezone == "America/Los_Angeles"
    assert PNW.iso_country_codes == ("US",)
    assert PNW.admin_codes == ("US-WA", "US-OR", "US-ID")
    assert len(PNW.enabled_layers) == EXPECTED_ENABLED_LAYER_COUNT


def test_pnw_manifest_is_frozen() -> None:
    with pytest.raises(ValidationError):
        PNW.slug = "not-pnw"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("layer_slug", "source_slug", "coverage"),
    [
        ("burn-severity", "mtbs", "regional"),
        ("fire-detections", "firms", "global"),
        ("weather-observations", "open_meteo", "global"),
        ("watersheds", "hydrosheds", "global"),
    ],
)
def test_a_sample_of_layer_bindings_match_the_grepped_source(layer_slug: str, source_slug: str, coverage: str) -> None:
    bindings = {binding.layer_slug: binding for binding in PNW.enabled_layers}
    assert bindings[layer_slug].source_slug == source_slug
    assert bindings[layer_slug].coverage == coverage


def test_envelope_rejects_west_east_out_of_order() -> None:
    with pytest.raises(ValidationError, match="west"):
        RegionEnvelope(west=-100.0, south=41.0, east=-110.0, north=50.0)


def test_envelope_rejects_south_north_out_of_order() -> None:
    with pytest.raises(ValidationError, match="south"):
        RegionEnvelope(west=-126.0, south=50.0, east=-110.0, north=41.0)


def test_region_rejects_a_nonpositive_lattice_pitch() -> None:
    with pytest.raises(ValidationError, match="lattice_pitch_degrees"):
        Region(
            slug="broken",
            display_name="Broken",
            envelope=RegionEnvelope(west=-126.0, south=41.0, east=-110.0, north=50.0),
            default_camera_envelope=RegionEnvelope(west=-125.0, south=42.0, east=-111.0, north=49.0),
            crs=4326,
            lattice_pitch_degrees=0.0,
            lattice_origin_rule="floor_to_cell_origin",
            timezone="America/Los_Angeles",
            iso_country_codes=("US",),
            admin_codes=("US-WA",),
            enabled_layers=(LayerBinding(layer_slug="sensors", source_slug="noaa_nws", coverage="regional"),),
        )


def test_load_region_defaults_to_pnw() -> None:
    assert load_region() is PNW
    assert load_region("pnw") is PNW


def test_load_region_reads_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REGION_ENV_VAR, "pnw")
    assert load_region() is PNW


def test_load_region_raises_for_an_unknown_slug() -> None:
    with pytest.raises(ValueError, match="unknown region"):
        load_region("nonexistent-region")


def test_load_region_raises_for_an_unknown_env_var_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REGION_ENV_VAR, "nonexistent-region")
    with pytest.raises(ValueError, match="unknown region"):
        load_region()
