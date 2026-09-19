"""The boot-with-global-lanes-only proof: a region that binds only `coverage: global` sources.

`federation.md` §4's checklist item -- "the platform's boot-with-global-lanes-only path still works
(the test that enables only `coverage: global` sources and checks the slider catalogue, legends and
agent tools report the rest as unavailable)" -- and §2's "the platform must run with a layer
unbound". The fabricated region here is the next deployment standing the pilot's tree up somewhere
PlantGeo has no US-specific feeds: MTBS, SSURGO, USDM, WFIGS, USGS NWIS, NOAA NWS and the Oregon OEM
portal all stop at the border, so seven layers arrive unbound and must each say so.

The PNW half of the file is the regression that keeps the rest honest: the pilot binds every
platform layer but `land-context`, which is in the vocabulary precisely so a surface can ask about
it and be told `unbound` with a reason rather than nothing at all (STYLE-REVIEW-W5 B1).
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

#: The platform layers the fabricated region binds nothing for -- every regionally-sourced layer in
#: the pilot, plus `land-context`, which no region binds yet. Derived from the two lists above
#: rather than typed a third time, so adding a platform layer cannot leave this test asserting over
#: a stale set.
UNBOUND_LAYER_SLUGS = tuple(
    layer_slug
    for layer_slug in PLATFORM_LAYER_SLUGS
    if layer_slug not in {binding.layer_slug for binding in GLOBAL_LAYER_BINDINGS}
)

#: The platform layers the PILOT itself leaves unbound. `land-context` is in the vocabulary because
#: a surface must be able to ask about it and get a governed absence with a reason; no land-context
#: lane is published in any region, so the honest manifest answer is `unbound`, not silence
#: (STYLE-REVIEW-W5 B1).
#: `fire-risk` and `weather-forecast` joined the vocabulary on 2026-09-19 (track
#: `plantgeo_ml_service_20260918`, FR-5a and FR-12) and are unbound for the same reason
#: `land-context` is: they are platform layers whose writer, `services/plantgeo-ml-service`, has
#: published nothing yet. Binding them before a partition exists would report a working layer over
#: an empty prefix, which is the one failure the binding rule exists to prevent.
PILOT_UNBOUND_LAYER_SLUGS = ("fire-risk", "land-context", "weather-forecast")

#: Which agent surface name reaches each unbound layer, for the per-layer refusal proof below.
#: `land-context` is deliberately absent: it is a reference plane read over tRPC and has no agent
#: surface at all, so there is no tool refusal to prove for it.
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
        # The vocabulary is the PLATFORM's, identical in every manifest; only the bindings differ.
        platform_layers=PLATFORM_LAYER_SLUGS,
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
        # The private registry this fixture extends, never replaces.
        (*region_manifest._KNOWN_REGION_SLUGS, GLOBAL_ONLY_REGION_SLUG),
    )
    # The private cache, seeded rather than re-parsed: there is no `<slug>.json` to read.
    monkeypatch.setitem(region_manifest._REGION_CACHE, GLOBAL_ONLY_REGION_SLUG, region)
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
    unbound = [slug for slug, status in availability.items() if status.binding == "unbound"]
    assert unbound == sorted(UNBOUND_LAYER_SLUGS)
    assert all(availability[binding.layer_slug].binding == "bound_global" for binding in GLOBAL_LAYER_BINDINGS)


def test_an_unbound_layer_names_a_reason_and_no_source(global_only_region: Region) -> None:
    """A governed absence, not an omission: the payload says WHY, and never a source it does not have."""
    soil = region_layer_availability(global_only_region)["soil-survey"]
    assert soil.binding == "unbound"
    assert soil.source_slug is None
    assert soil.reason == "no_source_bound_in_region"


# ARG001 below and in the four tests after it: the fixture is applied for its SIDE EFFECT (it
# registers the fabricated region as this process's manifest). pytest resolves a fixture by
# parameter name, so the name can be neither dropped nor `_`-prefixed the way a dummy would be.
def test_the_app_boots_with_only_global_lanes_bound(
    global_only_region: Region,  # noqa: ARG001
) -> None:
    """`federation.md` §4's checklist item: an unbound layer is a governed absence, never a boot failure."""
    # Imported inside the test so `create_app()` is first reached with the fixture's region
    # already registered, never at module import time.
    from agri_data_service import app as app_module  # noqa: PLC0415

    assert load_region().slug == GLOBAL_ONLY_REGION_SLUG
    assert app_module.create_app() is not None


def test_the_capabilities_payload_marks_the_regional_layers_unbound(
    global_only_region: Region,  # noqa: ARG001
) -> None:
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


def test_every_unbound_layer_with_an_agent_surface_is_covered_below() -> None:
    """The parametrized refusal proof walks a hand-spelled map; this is what keeps it complete.

    Three layers may be missing from it, and only because none of them has an agent surface to
    refuse through -- any OTHER unbound layer dropping out of the map would silently shrink the
    proof. `land-context` is a reference plane read over tRPC. `fire-risk` and `weather-forecast`
    are written by `services/plantgeo-ml-service` and reach no agent tool in this service yet; when
    one gains a surface it belongs in the map, and this assertion is what will say so.
    """
    assert set(UNBOUND_LAYER_SLUGS) - set(UNBOUND_LAYER_AGENT_SURFACES) == {
        "fire-risk",
        "land-context",
        "weather-forecast",
    }


@pytest.mark.parametrize("layer_slug", sorted(UNBOUND_LAYER_AGENT_SURFACES))
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


async def test_an_unbound_feature_layer_refuses_before_it_reads_a_lane(
    global_only_region: Region,  # noqa: ARG001
) -> None:
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


async def test_an_unbound_release_lane_tool_refuses_by_region(
    global_only_region: Region,  # noqa: ARG001
) -> None:
    """Drought's own tool reads a fixed lane rather than a surface name, and is gated the same way."""
    payload = await _tool_payload(
        lambda: agent_tools.query_drought_history_at_point(
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
        )
    )
    assert payload["error"] == "not_available_in_region"
    assert payload["unbound_layers"] == ["drought"]


async def test_a_globally_bound_layers_tool_is_not_refused_by_region(
    global_only_region: Region,  # noqa: ARG001
) -> None:
    """The other direction, and the reason the test above is not vacuous.

    `fire-detections` binds FIRMS, which is global, so the region gate must let it through -- it
    then hits the empty fixture warehouse and refuses for a lane reason instead. Asserting the error
    is a LANE refusal, not just "not the region one", is what proves the gate ran and passed.
    """
    payload = await _tool_payload(
        lambda: agent_tools.query_observation_coverage_on_day(surface_name="fire-detections", day=SELECTED_DAY)
    )
    assert payload["error"] != "not_available_in_region"


# --- The PNW regression --------------------------------------------------------------


def test_the_pilot_binds_every_platform_layer_but_the_three_with_no_publisher() -> None:
    """The pilot's governed absences, stated as a test rather than as an omission.

    One of the three is `land-context`, whose reference plane has no published lane. The other two
    joined on 2026-09-19 and are unbound for the same shape of reason: `fire-risk` and
    `weather-forecast` are written by `services/plantgeo-ml-service`, which has published nothing.
    """
    pnw = load_region("pnw")
    availability = region_layer_availability(pnw)
    assert set(availability) == set(PLATFORM_LAYER_SLUGS)
    unbound = [status.layer_slug for status in availability.values() if status.binding == "unbound"]
    assert unbound == sorted(PILOT_UNBOUND_LAYER_SLUGS)
    assert availability["land-context"].source_slug is None
    assert availability["land-context"].reason == "no_source_bound_in_region"
    assert all(
        is_layer_bound(pnw, layer_slug)
        for layer_slug in PLATFORM_LAYER_SLUGS
        if layer_slug not in PILOT_UNBOUND_LAYER_SLUGS
    )


def test_the_pilots_manifest_restates_the_platform_vocabulary_exactly() -> None:
    """The manifest field the WEB tree compiles in, pinned to the service's own enumeration.

    `src/lib/region/pnw.ts` carries the same list and `src/__tests__/region/manifest-parity.test.ts`
    diffs it against `pnw.json`; this is the other end of that chain. A slug added here and not
    there leaves the two trees disagreeing about whether a layer is a governed absence or not a
    federated layer at all (STYLE-REVIEW-W5 B1).
    """
    assert load_region("pnw").platform_layers == PLATFORM_LAYER_SLUGS


def test_the_pilots_census_field_carries_the_whole_vocabulary_with_one_absence() -> None:
    """The payload delta the client contract survives: one field, every platform layer stated."""
    bindings = region_layer_bindings()
    assert {binding.layer for binding in bindings} == set(PLATFORM_LAYER_SLUGS)
    bound = [binding for binding in bindings if binding.layer not in PILOT_UNBOUND_LAYER_SLUGS]
    assert all(binding.binding in {"bound_global", "bound_regional"} for binding in bound)
    assert all(binding.source is not None and binding.reason is None for binding in bound)
    land_context = next(binding for binding in bindings if binding.layer == "land-context")
    assert (land_context.binding, land_context.source, land_context.reason) == (
        "unbound",
        None,
        "no_source_bound_in_region",
    )
    assert set(bindings[0].to_wire()) == {"layer", "binding", "source", "reason"}
