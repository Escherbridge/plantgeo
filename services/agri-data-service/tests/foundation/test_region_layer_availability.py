"""The boot-with-global-lanes-only proof: a region that binds only `coverage: global` sources.

`federation.md` §4's checklist item -- "the platform's boot-with-global-lanes-only path still works
(the test that enables only `coverage: global` sources and checks the slider catalogue, legends and
agent tools report the rest as unavailable)" -- and §2's "the platform must run with a layer
unbound". The fabricated region here is the next deployment standing the pilot's tree up somewhere
PlantGeo has no US-specific feeds: MTBS, SSURGO, USDM, WFIGS, USGS NWIS, NOAA NWS and the Oregon OEM
portal all stop at the border, so seven layers arrive unbound and must each say so.

The PNW half of the file is the regression that makes the rest behaviour-neutral: the pilot binds
every platform layer, so nothing here is unbound and the census payload gains one additive field
whose every entry is `bound_*`.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.agent import tools as agent_tools
from agri_data_service.foundation.region import (
    PLATFORM_LAYER_SLUGS,
    LayerBinding,
    Region,
    RegionEnvelope,
    is_layer_bound,
    load_region,
    region_layer_availability,
)
from agri_data_service.foundation.region import manifest as region_manifest
from agri_data_service.interface.http.parquet_routes import region_layer_bindings
from tests.agent_fakes import FakeAgentWarehouse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

SELECTED_DAY = "2026-03-14"
BOISE_LONGITUDE = -116.2
BOISE_LATITUDE = 43.6

GLOBAL_ONLY_REGION_SLUG = "global-only-fixture"

#: The six PNW bindings whose source serves the whole planet, carried over unchanged. Keeping the
#: real source slugs means `assert_region_bindings_are_servable` runs against the same coverage
#: claims production does, so a fabricated region cannot boot on a claim the pilot could not.
GLOBAL_LAYER_BINDINGS = (
    LayerBinding(layer_slug="fire-detections", source_slug="firms", coverage="global"),
    LayerBinding(layer_slug="vegetation", source_slug="sentinel2_ndvi", coverage="global"),
    LayerBinding(layer_slug="weather-observations", source_slug="open_meteo", coverage="global"),
    LayerBinding(layer_slug="watersheds", source_slug="hydrosheds", coverage="global"),
    LayerBinding(layer_slug="signal", source_slug="era5_land_and_nasa_power", coverage="global"),
    LayerBinding(layer_slug="botanical-occurrences", source_slug="gbif", coverage="global"),
)

#: The seven platform layers the fabricated region binds nothing for -- every regionally-sourced
#: layer in the pilot. Derived from the two lists above rather than typed a third time, so adding a
#: platform layer cannot leave this test asserting over a stale set.
UNBOUND_LAYER_SLUGS = tuple(
    layer_slug
    for layer_slug in PLATFORM_LAYER_SLUGS
    if layer_slug not in {binding.layer_slug for binding in GLOBAL_LAYER_BINDINGS}
)

#: Which agent surface name reaches each unbound layer, for the per-layer refusal proof below.
UNBOUND_LAYER_AGENT_SURFACES = {
    "burn-severity": "burn-severity",
    "drought": "drought-areas",
    "evacuation-zones": "evacuation-zones",
    "fire-perimeters": "fire-perimeters",
    "sensors": "sensors",
    "soil-survey": "soil-survey",
    "water-gauges": "water-gauges",
}


def _global_only_region() -> Region:
    """A plausible non-US deployment: one envelope, one country, and only global sources bound.

    Coordinates are a test fixture naming the region it models (`federation.md` §1's permitted
    literals), not a footprint claim: this is a Kenyan highland box, chosen because no US-specific
    source reaches it, so "unbound" here is the honest answer rather than an arranged one.
    """
    return Region(
        slug=GLOBAL_ONLY_REGION_SLUG,
        display_name="Kenyan Highlands (fixture)",
        envelope=RegionEnvelope(west=34.0, south=-1.5, east=38.0, north=1.5),
        default_camera_envelope=RegionEnvelope(west=35.0, south=-1.0, east=37.0, north=1.0),
        crs=4326,
        lattice_pitch_degrees=0.01,
        lattice_origin_rule="floor_to_cell_origin",
        timezone="Africa/Nairobi",
        iso_country_codes=("KE",),
        admin_codes=("KE-30",),
        enabled_layers=GLOBAL_LAYER_BINDINGS,
    )


@pytest.fixture
def global_only_region(monkeypatch: pytest.MonkeyPatch) -> Region:
    """Register the fabricated manifest as this process's region, for the duration of one test.

    Seeds `load_region`'s cache rather than writing a `<slug>.json` into the installed package: the
    manifest's only door is `load_region()`, and a test that wrote a file would be asserting against
    a deployment artifact it had just invented. `_KNOWN_REGION_SLUGS` is widened in the same breath
    because `load_region` refuses an unregistered slug by design, which is the behaviour every OTHER
    region test pins and must not be softened here.
    """
    region = _global_only_region()
    monkeypatch.setattr(
        region_manifest,
        "_KNOWN_REGION_SLUGS",
        (*region_manifest._KNOWN_REGION_SLUGS, GLOBAL_ONLY_REGION_SLUG),  # noqa: SLF001 - the registry this fixture extends
    )
    monkeypatch.setitem(region_manifest._REGION_CACHE, GLOBAL_ONLY_REGION_SLUG, region)  # noqa: SLF001 - seeded, never re-parsed
    monkeypatch.setenv(region_manifest.REGION_ENV_VAR, GLOBAL_ONLY_REGION_SLUG)
    return region


@asynccontextmanager
async def _empty_run_context() -> AsyncIterator[None]:
    """A bound agent run context over a warehouse holding nothing.

    Every assertion below is about a refusal raised BEFORE any lane is touched, so the warehouse is
    deliberately empty: if a gate ever regressed, the tool would reach this fake and answer a
    lane-never-written refusal, which is a different error string and fails the assertion loudly
    rather than passing on an accident.
    """
    async with agent_tools.run_context(
        session_provider=None,
        warehouse_source=FakeAgentWarehouse(),
    ):
        yield


async def _tool_payload(tool: Callable[[], Any]) -> dict[str, Any]:
    """Run one agent tool inside an empty run context and decode its JSON payload."""
    async with _empty_run_context():
        return json.loads(await tool())


# --- The fabricated region -----------------------------------------------------------


def test_a_region_binding_only_global_sources_reports_the_rest_unbound(global_only_region: Region) -> None:
    availability = region_layer_availability(global_only_region)
    assert set(availability) == set(PLATFORM_LAYER_SLUGS)
    assert [slug for slug, status in availability.items() if status.binding == "unbound"] == sorted(
        UNBOUND_LAYER_SLUGS
    )
    assert all(availability[binding.layer_slug].binding == "bound_global" for binding in GLOBAL_LAYER_BINDINGS)


def test_an_unbound_layer_names_a_reason_and_no_source(global_only_region: Region) -> None:
    """A governed absence, not an omission: the payload says WHY, and never a source it does not have."""
    soil = region_layer_availability(global_only_region)["soil-survey"]
    assert soil.binding == "unbound"
    assert soil.source_slug is None
    assert soil.reason == "no_source_bound_in_region"


def test_the_app_boots_with_only_global_lanes_bound(global_only_region: Region) -> None:
    """`federation.md` §4's checklist item: an unbound layer is a governed absence, never a boot failure."""
    from agri_data_service import app as app_module

    assert load_region().slug == GLOBAL_ONLY_REGION_SLUG
    assert app_module.create_app() is not None


def test_the_capabilities_payload_marks_the_regional_layers_unbound(global_only_region: Region) -> None:
    """The census field the web slider reads, built from the same manifest the boot check passed."""
    bindings = {binding.layer: binding for binding in region_layer_bindings()}
    assert set(bindings) == set(PLATFORM_LAYER_SLUGS)
    for layer_slug in UNBOUND_LAYER_SLUGS:
        assert bindings[layer_slug].binding == "unbound", layer_slug
        assert bindings[layer_slug].source is None, layer_slug
        assert bindings[layer_slug].reason == "no_source_bound_in_region", layer_slug
    assert bindings["fire-detections"].binding == "bound_global"
    assert bindings["fire-detections"].source == "firms"
    assert bindings["fire-detections"].reason is None


@pytest.mark.parametrize("layer_slug", UNBOUND_LAYER_SLUGS)
async def test_every_unbound_layers_coverage_tool_refuses_by_region(
    global_only_region: Region,
    layer_slug: str,
) -> None:
    """The tool stays REGISTERED and answers a governed absence naming the layer and the region."""
    surface = UNBOUND_LAYER_AGENT_SURFACES[layer_slug]
    payload = await _tool_payload(
        lambda: agent_tools.query_observation_coverage_on_day(surface_name=surface, day=SELECTED_DAY)
    )
    assert payload["error"] == "not_available_in_region"
    assert payload["unbound_layers"] == [layer_slug]
    assert payload["region_slug"] == GLOBAL_ONLY_REGION_SLUG
    assert payload["region_display_name"] == global_only_region.display_name
    assert "REFUSAL, not an absence" in payload["note"]


async def test_an_unbound_feature_layer_refuses_before_it_reads_a_lane(global_only_region: Region) -> None:
    """`feature_value_near_point` is the other half of the triad, and answers the same way."""
    payload = await _tool_payload(
        lambda: agent_tools.query_feature_value_near_point(
            surface_name="soil-survey",
            day=SELECTED_DAY,
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
        )
    )
    assert payload["error"] == "not_available_in_region"
    assert payload["unbound_layers"] == ["soil-survey"]


async def test_an_unbound_release_lane_tool_refuses_by_region(global_only_region: Region) -> None:
    """Drought's own tool reads a fixed lane rather than a surface name, and is gated the same way."""
    payload = await _tool_payload(
        lambda: agent_tools.query_drought_history_at_point(
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
        )
    )
    assert payload["error"] == "not_available_in_region"
    assert payload["unbound_layers"] == ["drought"]


async def test_a_globally_bound_layers_tool_is_not_refused_by_region(global_only_region: Region) -> None:
    """The other direction, and the reason the test above is not vacuous.

    `fire-detections` binds FIRMS, which is global, so the region gate must let it through -- it
    then hits the empty fixture warehouse and refuses for a lane reason instead. Asserting the error
    is a LANE refusal, not just "not the region one", is what proves the gate ran and passed.
    """
    payload = await _tool_payload(
        lambda: agent_tools.query_observation_coverage_on_day(
            surface_name="fire-detections", day=SELECTED_DAY
        )
    )
    assert payload["error"] != "not_available_in_region"


# --- The PNW regression --------------------------------------------------------------


def test_the_pilot_binds_every_platform_layer() -> None:
    """Behaviour-neutrality, stated as a test: nothing in the pilot is unbound, so nothing changes."""
    pnw = load_region("pnw")
    availability = region_layer_availability(pnw)
    assert set(availability) == set(PLATFORM_LAYER_SLUGS)
    assert [status.layer_slug for status in availability.values() if status.binding == "unbound"] == []
    assert all(is_layer_bound(pnw, layer_slug) for layer_slug in PLATFORM_LAYER_SLUGS)


def test_the_pilots_census_field_is_additive_and_every_entry_is_bound() -> None:
    """The payload delta the client contract survives: one new field, no entry of it `unbound`."""
    bindings = region_layer_bindings()
    assert {binding.layer for binding in bindings} == set(PLATFORM_LAYER_SLUGS)
    assert all(binding.binding in {"bound_global", "bound_regional"} for binding in bindings)
    assert all(binding.source is not None and binding.reason is None for binding in bindings)
    assert set(bindings[0].to_wire()) == {"layer", "binding", "source", "reason"}
