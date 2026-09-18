"""The `Region` manifest: validators, `PLANTGEO_REGION` resolution, and the `pnw.json` load path.

`federation.md` §1 makes this the one typed footprint declaration; these tests pin the validators
that keep an inverted envelope or an unpitched lattice from ever becoming a loadable manifest, and
the `load_region` contract `manifest.py`'s docstring states: env var wins, missing slug is unknown,
unknown slug raises rather than falling back to the pilot.

Goes through `load_region()` everywhere, never a module-level `PNW` constant -- `foundation/region`
exports no such constant (`STYLE-REVIEW-W1.md` B2): the package's only door is the function, so
these tests exercise exactly what every other caller can reach.
"""

from __future__ import annotations

from types import MappingProxyType

import pytest
from pydantic import ValidationError

from agri_data_service.foundation.region import LayerBinding, Region, RegionEnvelope, load_region
from agri_data_service.foundation.region.manifest import REGION_ENV_VAR

EXPECTED_LATTICE_PITCH_DEGREES = 0.01  # TIER_RESOLUTION_DEGREES[9], warehouse/parquet/tiers.py
#: Eleven `layer-lanes.md` §1 original slugs, minus `interventions` (Postgres, no lane), plus
#: `drought` and `signal` (both named in that section's "thirteen registered streams") and
#: `botanical-occurrences` (a served plane not counted in that section's tally; see this
#: package's `AGENTS.md` "Layer bindings, cited" for why the count agrees with "thirteen" anyway).
EXPECTED_ENABLED_LAYER_COUNT = 13


def test_pnw_manifest_loads_with_the_documented_values() -> None:
    pnw = load_region("pnw")
    assert pnw.slug == "pnw"
    assert pnw.envelope == RegionEnvelope(west=-126.0, south=41.0, east=-110.0, north=50.0)
    assert pnw.default_camera_envelope == RegionEnvelope(west=-125.0, south=42.0, east=-111.0, north=49.0)
    assert pnw.sub_envelopes["burn_severity"] == RegionEnvelope(west=-125.0, south=42.0, east=-111.0, north=49.0)
    assert pnw.sub_envelopes["botanical_seed"] == RegionEnvelope(west=-125.0, south=41.0, east=-110.0, north=50.0)
    assert pnw.lattice_pitch_degrees == EXPECTED_LATTICE_PITCH_DEGREES
    assert pnw.lattice_origin_rule == "floor_to_cell_origin"
    assert pnw.timezone == "America/Los_Angeles"
    assert pnw.iso_country_codes == ("US",)
    assert pnw.admin_codes == ("US-WA", "US-OR", "US-ID")
    assert len(pnw.enabled_layers) == EXPECTED_ENABLED_LAYER_COUNT


def test_pnw_manifest_is_frozen() -> None:
    pnw = load_region("pnw")
    with pytest.raises(ValidationError):
        pnw.slug = "not-pnw"  # type: ignore[misc]


def test_pnw_manifest_sub_envelopes_cannot_be_mutated_in_place() -> None:
    """`frozen=True` blocks reassigning `sub_envelopes`; this is the OTHER half -- mutating its
    contents in place, which `frozen` alone does not stop (STYLE-REVIEW-W1.md S2)."""
    pnw = load_region("pnw")
    assert isinstance(pnw.sub_envelopes, MappingProxyType)
    with pytest.raises(TypeError):
        pnw.sub_envelopes["burn_severity"] = RegionEnvelope(west=0.0, south=0.0, east=1.0, north=1.0)  # type: ignore[index]


@pytest.mark.parametrize(
    ("layer_slug", "source_slug", "coverage"),
    [
        ("burn-severity", "mtbs", "regional"),
        ("fire-detections", "firms", "global"),
        ("vegetation", "sentinel2_ndvi", "global"),
        ("sensors", "noaa_nws", "regional"),
        ("weather-observations", "open_meteo", "global"),
        ("watersheds", "hydrosheds", "global"),
        ("drought", "usdm", "regional"),
        ("signal", "era5_land_and_nasa_power", "global"),
        ("botanical-occurrences", "gbif", "global"),
    ],
)
def test_a_sample_of_layer_bindings_match_the_grepped_source(layer_slug: str, source_slug: str, coverage: str) -> None:
    pnw = load_region("pnw")
    bindings = {binding.layer_slug: binding for binding in pnw.enabled_layers}
    assert bindings[layer_slug].source_slug == source_slug
    assert bindings[layer_slug].coverage == coverage


def test_interventions_is_not_an_enabled_layer_binding() -> None:
    """`interventions` has no Parquet lane at all (`layer-lanes.md` §1) and stays in Postgres; a
    binding here would claim it as a source-bound layer this manifest governs (STYLE-REVIEW-W1.md B1)."""
    pnw = load_region("pnw")
    slugs = {binding.layer_slug for binding in pnw.enabled_layers}
    assert "interventions" not in slugs


def test_envelope_rejects_west_east_out_of_order() -> None:
    with pytest.raises(ValidationError, match="west"):
        RegionEnvelope(west=-100.0, south=41.0, east=-110.0, north=50.0)


def test_envelope_rejects_south_north_out_of_order() -> None:
    with pytest.raises(ValidationError, match="south"):
        RegionEnvelope(west=-126.0, south=50.0, east=-110.0, north=41.0)


def test_envelope_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError):
        RegionEnvelope(west=-126.0, south=41.0, east=-110.0, north=50.0, altitude=0.0)  # type: ignore[call-arg]


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
    assert load_region().slug == "pnw"
    assert load_region("pnw").slug == "pnw"


def test_load_region_caches_by_resolved_slug() -> None:
    """Two calls for the same slug return the SAME object, not merely an equal one -- proof the
    cache-by-slug half of `load_region()` (STYLE-REVIEW-W1.md B2) actually memoises."""
    assert load_region("pnw") is load_region("pnw")


def test_load_region_reads_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REGION_ENV_VAR, "pnw")
    assert load_region().slug == "pnw"


def test_load_region_raises_for_an_unknown_slug() -> None:
    with pytest.raises(ValueError, match="unknown region"):
        load_region("nonexistent-region")


def test_load_region_raises_for_an_unknown_env_var_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REGION_ENV_VAR, "nonexistent-region")
    with pytest.raises(ValueError, match="unknown region"):
        load_region()
