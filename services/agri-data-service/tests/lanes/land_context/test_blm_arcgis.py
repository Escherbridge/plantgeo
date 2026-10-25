"""`land_context.blm_arcgis`: real request planning against the three admitted BLM ArcGIS services
(`products.py`), the TIGERweb exclusion, the no-local-scratch rule, and the shipped dark TOMLs'
legacy-path safety (spec section 7 land-context row; plan Phase 4, `s-land-context`).

Only `plan_requests` and `fetch` are driven here: `settle`/`rows` are deliberately unimplemented
(module docstring) and are checked only for the `NotImplementedError` they raise. No full runner turn
is driven -- `executor = "legacy"` on every shipped TOML means the runner never actually dispatches
this strategy (see `test_the_shipped_toml_stays_on_the_legacy_path`).
"""

from __future__ import annotations

import ast
import inspect
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final

import pytest

from agri_data_service.execution.lane_specs import LANE_SPECS
from agri_data_service.foundation.lane_config import load_lane_configs
from agri_data_service.foundation.region import load_region
from agri_data_service.pipeline.lanes.land_context import blm_arcgis
from agri_data_service.pipeline.lanes.land_context.products import (
    CENSUS_STATES,
    FIELD_OFFICES,
    PRODUCTS,
    SURFACE_IDAHO,
    SURFACE_ORWA,
)
from agri_data_service.pipeline.runner.contract import ProviderResponse
from tests.lane_config.builders import REAL_LANES_DIRECTORY

LANE_IDS = ("land-context-blm-forward", "land-context-blm-reconcile", "land-context-blm-backfill")
ASKED_DAY: date = date(2026, 10, 24)


NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)


class _FakeClient:
    """A `ProviderClient` that answers one canned body regardless of the endpoint asked."""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def get(self, endpoint: str, parameters: dict[str, str], *, probe: bool = False) -> ProviderResponse:
        del probe
        self.calls.append((endpoint, dict(parameters)))
        return ProviderResponse(body=self.body, request_url=f"https://gis.blm.gov/{endpoint}", retrieved_at=NOW)


def _lane_set():
    return load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw"))


def _lane(lane_id: str):
    configs = _lane_set()
    assert dict(configs.quarantined) == {}, dict(configs.quarantined)
    return configs.lanes[lane_id], load_region("pnw")


# --- the shipped TOMLs stay dark and on the legacy path -----------------------------------------


@pytest.mark.parametrize("lane_id", LANE_IDS)
def test_the_shipped_toml_loads_dark_with_the_shared_strategy(lane_id: str) -> None:
    lane, _ = _lane(lane_id)
    assert lane.enabled is False
    assert lane.kind == "ingest"
    assert lane.nature == "static_lookup"
    assert lane.strategy == "land_context.blm_arcgis"
    assert lane.schedule.gap_fill_cron is None, "static_lookup has no gap-fill turn (lane_catalogue.py)"


@pytest.mark.parametrize("lane_id", LANE_IDS)
def test_the_shipped_toml_stays_on_the_legacy_path(lane_id: str) -> None:
    """`executor = "legacy"` names an existing `LANE_SPECS` row, so the lane TOML changes nothing about
    which path dispatches it (execution/AGENTS.md "Lane catalogue"): flipping this to `executor =
    "config"` would take the id OFF `LANE_SPECS` the moment it loads, enabled or not."""
    lane, _ = _lane(lane_id)
    assert lane.executor == "legacy"
    assert lane_id in LANE_SPECS, "executor = 'legacy' needs a legacy spec to run it"


def test_only_the_forward_toml_owns_the_three_land_context_streams() -> None:
    configs = _lane_set()
    forward = configs.lanes["land-context-blm-forward"]
    reconcile = configs.lanes["land-context-blm-reconcile"]
    backfill = configs.lanes["land-context-blm-backfill"]
    assert {stream.slug for stream in forward.streams} == {
        "land-context-boundaries",
        "land-context-offices",
        "land-context-contacts",
    }
    assert reconcile.streams == ()
    assert backfill.streams == ()


# --- plan_requests is real: the three admitted ArcGIS services, products.py's own filters --------


def test_plan_requests_asks_exactly_the_three_admitted_blm_services() -> None:
    lane, region = _lane("land-context-blm-forward")

    requests = blm_arcgis.STRATEGY.plan_requests([ASKED_DAY], lane, region)

    assert [request.unit for request in requests] == [
        SURFACE_ORWA.slug,
        SURFACE_IDAHO.slug,
        FIELD_OFFICES.slug,
    ]
    assert all(request.days == (ASKED_DAY,) for request in requests)


def test_plan_requests_never_asks_the_tigerweb_census_product() -> None:
    """`products.py::PRODUCTS` still admits CENSUS_STATES on the legacy path; this strategy must not."""
    assert CENSUS_STATES in PRODUCTS
    assert CENSUS_STATES not in blm_arcgis.PLANNED_PRODUCTS
    lane, region = _lane("land-context-blm-forward")

    requests = blm_arcgis.STRATEGY.plan_requests([ASKED_DAY], lane, region)

    assert CENSUS_STATES.slug not in {request.unit for request in requests}


def test_plan_requests_mirrors_the_legacy_non_spatial_inventory_query() -> None:
    lane, region = _lane("land-context-blm-forward")

    requests = blm_arcgis.STRATEGY.plan_requests([ASKED_DAY], lane, region)
    orwa = next(request for request in requests if request.unit == SURFACE_ORWA.slug)

    assert orwa.endpoint == "or-ownership-query"
    assert orwa.query() == {"f": "json", "where": SURFACE_ORWA.where, "returnIdsOnly": "true"}
    assert orwa.requested_parameters == frozenset({blm_arcgis.OBJECT_INVENTORY_PARAMETER})


def test_plan_requests_mirrors_the_legacy_spatial_inventory_query() -> None:
    """SURFACE_IDAHO is the one admitted product with an envelope; the spatial params ride along."""
    lane, region = _lane("land-context-blm-forward")

    requests = blm_arcgis.STRATEGY.plan_requests([ASKED_DAY], lane, region)
    idaho = next(request for request in requests if request.unit == SURFACE_IDAHO.slug)

    assert idaho.endpoint == "national-sma-query"
    assert idaho.query() == {
        "f": "json",
        "where": SURFACE_IDAHO.where,
        "returnIdsOnly": "true",
        "geometry": SURFACE_IDAHO.envelope,
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }


def test_plan_requests_binds_a_distinct_support_digest_per_service() -> None:
    lane, region = _lane("land-context-blm-forward")

    requests = blm_arcgis.STRATEGY.plan_requests([ASKED_DAY], lane, region)

    digests = {request.support_digest for request in requests}
    assert len(digests) == len(requests), "each service's filter must bind its own checkpoint support"


# --- fetch() parses a real-shaped ArcGIS object-ID inventory answer -------------------------------


async def test_fetch_parses_a_real_shaped_object_id_inventory() -> None:
    lane, region = _lane("land-context-blm-forward")
    request = next(
        request
        for request in blm_arcgis.STRATEGY.plan_requests([ASKED_DAY], lane, region)
        if request.unit == FIELD_OFFICES.slug
    )
    client = _FakeClient(json.dumps({"objectIds": [3, 1, 2]}).encode("utf-8"))

    response = await blm_arcgis.STRATEGY.fetch(request, client)

    assert response.payload == (1, 2, 3)
    assert response.complete_parameters == frozenset({blm_arcgis.OBJECT_INVENTORY_PARAMETER})
    assert client.calls == [(request.endpoint, request.query())]


_REFUSED_BODY: Final = json.dumps({"error": {"code": 400, "message": "Invalid or missing input parameters."}})
#: A real-shaped ArcGIS truncation: a PARTIAL objectIds list still rides alongside the flag
#: (`source.py::CaptureSession.query` checks this before `object_ids()` ever sees the payload).
_TRUNCATED_BODY: Final = json.dumps({"objectIds": [1, 2], "exceededTransferLimit": True})


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"not json", id="unparseable"),
        pytest.param(_REFUSED_BODY, id="refused"),
        pytest.param(_TRUNCATED_BODY, id="exceeded-transfer-limit"),
        pytest.param(json.dumps({"objectIds": [1, 1, 2]}), id="duplicate-id"),
        pytest.param(json.dumps({"objectIds": [1, "2"]}), id="non-integer-id"),
    ],
)
async def test_fetch_refuses_a_malformed_object_inventory(body: str | bytes) -> None:
    lane, region = _lane("land-context-blm-forward")
    request = blm_arcgis.STRATEGY.plan_requests([ASKED_DAY], lane, region)[0]
    raw = body if isinstance(body, bytes) else body.encode("utf-8")
    client = _FakeClient(raw)

    with pytest.raises(ValueError, match=request.unit):
        await blm_arcgis.STRATEGY.fetch(request, client)


async def test_fetch_truncation_refusal_matches_the_legacy_wording() -> None:
    """Pins the exact phrase `source.py::CaptureSession.query` raises for the same condition, not just
    that SOME ValueError fired: a server-truncated inventory is a partial population, never complete."""
    lane, region = _lane("land-context-blm-forward")
    request = blm_arcgis.STRATEGY.plan_requests([ASKED_DAY], lane, region)[0]
    client = _FakeClient(_TRUNCATED_BODY.encode("utf-8"))

    with pytest.raises(ValueError, match=r"ArcGIS truncated .*; refusing a partial population"):
        await blm_arcgis.STRATEGY.fetch(request, client)


# --- settle/rows are an explicit, documented refusal, never a guessed implementation -------------


def test_settle_and_rows_are_deferred_to_activation() -> None:
    with pytest.raises(NotImplementedError, match="deferred to activation"):
        blm_arcgis.STRATEGY.settle(ASKED_DAY, (), context=None)  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError, match="deferred to activation"):
        blm_arcgis.STRATEGY.rows(ASKED_DAY, ())


# --- the scratch-dir rule: no local filesystem staging, ever -------------------------------------


def test_the_strategy_module_touches_no_local_scratch_directory() -> None:
    """Mirrors the caution metadata.json also carries for crop-cover's legacy `--capture-root
    /tmp/...` literal: every answer here flows through SourceRequest/SourceResponse and the runner's
    own ports: a strategy leaf must never stage to a local temp path itself."""
    source_path = Path(inspect.getfile(blm_arcgis))
    text = source_path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(source_path))

    imported_roots = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}

    forbidden = {"tempfile", "shutil", "os"}
    offenders = imported_roots & forbidden
    assert not offenders, f"blm_arcgis.py imports a local-filesystem module: {offenders}"
    assert "/tmp" not in text
    assert "open(" not in text
