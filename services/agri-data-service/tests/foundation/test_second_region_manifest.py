"""The second-region proof: a real second manifest this tree ships, selected by `PLANTGEO_REGION`.

`federation.md` §4's checklist item -- "the platform's boot-with-global-lanes-only path still works"
-- proved against `foundation/region/kenya_highlands.json` rather than against a fabricated `Region`
built inside a test. `test_region_layer_availability.py` already holds the fixture version of the
same proof; this file is the one that also exercises the packaging path a forward deployment
actually walks: a registered slug, a JSON file read out of the installed package, `crs: null`, and
no `sub_envelopes` at all. See `foundation/region/AGENTS.md`, section "Why a second manifest is data
rather than a fixture".

What this proves: the app BOOTS under the second manifest, the four regionally-sourced layers are
governed absences with a named reason, the coverage payload says so, and every agent surface over
an unbound layer refuses by region while a globally-bound one still reaches its lane question.

What it deliberately does NOT prove: nothing here asserts that any data, Parquet partition or tile
archive exists for this footprint. None does. A manifest is a declaration of what a deployment
WOULD serve and of what it will honestly refuse; filling it is a different piece of work.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.agent import tools as agent_tools
from agri_data_service.foundation.region import (
    PLATFORM_LAYER_SLUGS,
    UNBOUND_REASON_NO_SOURCE,
    Region,
    is_layer_bound,
    load_region,
    region_layer_availability,
)
from agri_data_service.foundation.region.manifest import REGION_ENV_VAR
from agri_data_service.interface.http.parquet_routes import region_layer_bindings

from tests.agent_fakes import FakeAgentWarehouse

# The surface-name map is imported rather than hand-spelled a second time: the sibling module
# already asserts that it covers every unbound layer with an agent surface, so a layer added to the
# platform vocabulary cannot quietly shrink this file's proof either.
from tests.foundation.test_region_layer_availability import UNBOUND_LAYER_AGENT_SURFACES

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

SECOND_REGION_SLUG = "kenya-highlands"
SELECTED_DAY = "2026-03-14"
#: A point inside the second manifest's envelope; a test fixture naming the region it models
#: (`federation.md` §1's permitted literals), never a footprint claim.
NAIROBI_LONGITUDE = 36.8
NAIROBI_LATITUDE = -1.3

#: The six sources that serve the whole planet, which are the only ones this manifest may bind.
EXPECTED_GLOBAL_BINDINGS = {
    "fire-detections": "firms",
    "vegetation": "sentinel2_ndvi",
    "weather-observations": "open_meteo",
    "watersheds": "hydrosheds",
    "signal": "era5_land_and_nasa_power",
    "botanical-occurrences": "gbif",
}

#: The four layers the task of this proof names explicitly: every one has a US-scoped source in the
#: pilot and no source at all here. `evacuation-zones`, `fire-perimeters`, `sensors` and
#: `water-gauges` are unbound for the same reason and are covered by the derived set below.
REGIONALLY_SOURCED_UNBOUND_LAYERS = ("burn-severity", "drought", "land-context", "soil-survey")

#: Every platform layer this manifest binds nothing for, derived from the bindings rather than
#: typed a third time, so adding a platform layer cannot leave this file asserting over a stale set.
UNBOUND_LAYER_SLUGS = tuple(
    layer_slug for layer_slug in PLATFORM_LAYER_SLUGS if layer_slug not in EXPECTED_GLOBAL_BINDINGS
)


@pytest.fixture
def second_region(monkeypatch: pytest.MonkeyPatch) -> Region:
    """Select the shipped second manifest for one test, through the env var a deployment sets.

    No monkeypatched registry and no seeded cache, unlike the fabricated-region fixture: the whole
    point of this file is that `PLANTGEO_REGION=kenya-highlands` resolves a manifest this package
    already ships.
    """
    monkeypatch.setenv(REGION_ENV_VAR, SECOND_REGION_SLUG)
    return load_region()


@asynccontextmanager
async def _empty_run_context() -> AsyncIterator[None]:
    """A bound agent run context over a warehouse holding nothing.

    Every assertion below is about a refusal raised BEFORE any lane is touched, so an empty
    warehouse is what makes the negative case loud: a regressed gate would reach this fake and
    answer a lane-never-written refusal, a different error string, rather than pass by accident.
    """
    async with agent_tools.run_context(session_provider=None, warehouse_source=FakeAgentWarehouse()):
        yield


async def _tool_payload(tool: Callable[[], Any]) -> dict[str, Any]:
    """Run one agent tool inside an empty run context and decode its JSON payload."""
    async with _empty_run_context():
        return json.loads(await tool())


# --- The manifest itself -------------------------------------------------------------


def test_the_second_manifest_loads_from_the_installed_package() -> None:
    """The packaging path: a registered slug reads its own JSON data file, with no env var set."""
    region = load_region(SECOND_REGION_SLUG)
    assert region.slug == SECOND_REGION_SLUG
    assert region.display_name == "Kenya Highlands"
    assert (region.envelope.west, region.envelope.south) == (34.0, -5.0)
    assert (region.envelope.east, region.envelope.north) == (42.0, 5.0)
    assert region.timezone == "Africa/Nairobi"
    assert region.iso_country_codes == ("KE",)
    assert region.admin_codes == ("KE-13", "KE-31", "KE-35", "KE-36")
    assert region.lattice_pitch_degrees == load_region("pnw").lattice_pitch_degrees
    assert region.lattice_origin_rule == "floor_to_cell_origin"


def test_the_second_manifest_declares_no_projected_crs_and_no_sub_envelopes() -> None:
    """Both optional fields at their empty end, which no other manifest exercises today.

    `crs: null` and an empty `sub_envelopes` are the shapes a manifest author writing the NEXT
    region starts from; if either stopped validating, the failure would first be seen by them.
    """
    region = load_region(SECOND_REGION_SLUG)
    assert region.crs is None
    assert dict(region.sub_envelopes) == {}


def test_the_second_manifest_binds_only_global_sources() -> None:
    """`federation.md` §2: a manifest may only bind a source whose coverage contains its envelope."""
    region = load_region(SECOND_REGION_SLUG)
    bindings = {binding.layer_slug: binding for binding in region.enabled_layers}
    assert {slug: binding.source_slug for slug, binding in bindings.items()} == EXPECTED_GLOBAL_BINDINGS
    assert all(binding.coverage == "global" for binding in bindings.values())


def test_the_env_var_selects_the_second_manifest(second_region: Region) -> None:
    """The deployment's own selector, and the reason the registry is keyed by slug at all."""
    assert second_region.slug == SECOND_REGION_SLUG
    assert load_region().slug == SECOND_REGION_SLUG


def test_the_pilot_stays_the_default_when_nothing_selects_a_region(monkeypatch: pytest.MonkeyPatch) -> None:
    """Registering a second manifest must not move the default out from under an unset deployment."""
    monkeypatch.delenv(REGION_ENV_VAR, raising=False)
    assert load_region().slug == "pnw"


# --- The cross-manifest invariant ----------------------------------------------------


def test_both_manifests_state_the_same_platform_vocabulary() -> None:
    """The vocabulary is platform-wide; only the BINDINGS are per region (STYLE-REVIEW-W5 B1).

    If the two manifests could disagree here, "unbound" and "not a federated layer at all" would
    mean different things per deployment, and a surface could not tell a governed absence from a
    slug this build has never heard of.
    """
    assert load_region(SECOND_REGION_SLUG).platform_layers == load_region("pnw").platform_layers
    assert load_region(SECOND_REGION_SLUG).platform_layers == PLATFORM_LAYER_SLUGS


def test_the_two_manifests_bind_different_sources_over_that_one_vocabulary() -> None:
    """The other half: identical vocabulary, genuinely different footprints and bindings."""
    pilot = load_region("pnw")
    second = load_region(SECOND_REGION_SLUG)
    assert second.envelope != pilot.envelope
    assert set(second.iso_country_codes).isdisjoint(pilot.iso_country_codes)
    pilot_layers = {binding.layer_slug for binding in pilot.enabled_layers}
    second_layers = {binding.layer_slug for binding in second.enabled_layers}
    assert second_layers < pilot_layers


# --- Boot, availability and the coverage payload -------------------------------------


# ARG001 here and below: the fixture is applied for its SIDE EFFECT (it selects the region for this
# process). pytest resolves a fixture by parameter name, so the name can be neither dropped nor
# `_`-prefixed the way a dummy argument would be.
def test_the_app_boots_under_the_second_manifest(second_region: Region) -> None:
    """Coverage and conformance both pass: an unbound layer is a governed absence, never a crash."""
    # Imported inside the test so `create_app()` is first reached with the region already selected.
    from agri_data_service import app as app_module  # noqa: PLC0415

    assert load_region().slug == second_region.slug
    assert app_module.create_app() is not None


def test_the_regionally_sourced_layers_are_unbound_with_a_reason(second_region: Region) -> None:
    availability = region_layer_availability(second_region)
    assert set(availability) == set(PLATFORM_LAYER_SLUGS)
    unbound = [slug for slug, status in availability.items() if status.binding == "unbound"]
    assert unbound == sorted(UNBOUND_LAYER_SLUGS)
    for layer_slug in REGIONALLY_SOURCED_UNBOUND_LAYERS:
        assert availability[layer_slug].binding == "unbound", layer_slug
        assert availability[layer_slug].source_slug is None, layer_slug
        assert availability[layer_slug].reason == UNBOUND_REASON_NO_SOURCE, layer_slug
        assert not is_layer_bound(second_region, layer_slug), layer_slug
    for layer_slug, source_slug in EXPECTED_GLOBAL_BINDINGS.items():
        assert availability[layer_slug].binding == "bound_global", layer_slug
        assert availability[layer_slug].source_slug == source_slug, layer_slug


def test_the_coverage_payload_reports_the_same_absences(
    second_region: Region,  # noqa: ARG001
) -> None:
    """`/api/v1/parquet/coverage`'s `layer_bindings` field, built from the manifest that just booted."""
    bindings = {binding.layer: binding for binding in region_layer_bindings()}
    assert set(bindings) == set(PLATFORM_LAYER_SLUGS)
    for layer_slug in UNBOUND_LAYER_SLUGS:
        assert bindings[layer_slug].binding == "unbound", layer_slug
        assert bindings[layer_slug].source is None, layer_slug
        assert bindings[layer_slug].reason == UNBOUND_REASON_NO_SOURCE, layer_slug
    assert bindings["fire-detections"].binding == "bound_global"
    assert bindings["fire-detections"].source == "firms"
    assert bindings["fire-detections"].reason is None


# --- The agent surfaces --------------------------------------------------------------


@pytest.mark.parametrize("layer_slug", sorted(UNBOUND_LAYER_AGENT_SURFACES))
async def test_every_unbound_layers_coverage_tool_refuses_by_region(
    second_region: Region,
    layer_slug: str,
) -> None:
    """The tool stays REGISTERED and answers a governed absence naming the layer and the region."""
    payload = await _tool_payload(
        lambda: agent_tools.query_observation_coverage_on_day(
            surface_name=UNBOUND_LAYER_AGENT_SURFACES[layer_slug],
            day=SELECTED_DAY,
        )
    )
    assert payload["error"] == "not_available_in_region"
    assert payload["unbound_layers"] == [layer_slug]
    assert payload["region_slug"] == SECOND_REGION_SLUG
    assert payload["region_display_name"] == second_region.display_name


async def test_an_unbound_feature_layer_refuses_before_it_reads_a_lane(
    second_region: Region,  # noqa: ARG001
) -> None:
    """The point-question half of the triad, asked at a coordinate inside the second envelope."""
    payload = await _tool_payload(
        lambda: agent_tools.query_feature_value_near_point(
            surface_name="soil-survey",
            day=SELECTED_DAY,
            longitude=NAIROBI_LONGITUDE,
            latitude=NAIROBI_LATITUDE,
        )
    )
    assert payload["error"] == "not_available_in_region"
    assert payload["unbound_layers"] == ["soil-survey"]


async def test_the_drought_lane_tool_refuses_by_region(
    second_region: Region,  # noqa: ARG001
) -> None:
    """Drought's own tool reads a fixed lane rather than a surface name, and is gated the same way."""
    payload = await _tool_payload(
        lambda: agent_tools.query_drought_history_at_point(
            longitude=NAIROBI_LONGITUDE,
            latitude=NAIROBI_LATITUDE,
        )
    )
    assert payload["error"] == "not_available_in_region"
    assert payload["unbound_layers"] == ["drought"]


@pytest.mark.parametrize("surface_name", ["fire-detections", "vegetation", "weather-observations"])
async def test_a_globally_bound_layers_tool_reaches_its_lane_question(
    second_region: Region,  # noqa: ARG001
    surface_name: str,
) -> None:
    """The other direction, and the reason the refusals above are not vacuous.

    Each of these binds a global source here, so the region gate must let it through -- it then
    hits the empty fixture warehouse and refuses for a LANE reason instead. Asserting the error is
    not the region one is what proves the gate ran and passed.
    """
    payload = await _tool_payload(
        lambda: agent_tools.query_observation_coverage_on_day(surface_name=surface_name, day=SELECTED_DAY)
    )
    assert payload.get("error") != "not_available_in_region"
