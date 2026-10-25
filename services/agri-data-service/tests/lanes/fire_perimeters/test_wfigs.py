"""`fire_perimeters.wfigs`: the watermark probe's fault classification, request planning, and digest stability.

Only the strategy's own pure surface is exercised here (Wave O rules: no builds, lint or test runs
accompany this slice's authoring, so these are read-for-correctness, not execution evidence). The
geometry-repair round trip `fetch` performs is real DuckDB work over a live-fetched population and is
NOT exercised by these tests; see `pipeline/lanes/fire_perimeters/AGENTS.md`, "What this slice did not
port", for why that is named rather than silently skipped.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from agri_data_service.foundation.lane_config import load_lane_configs
from agri_data_service.foundation.region import load_region
from agri_data_service.pipeline.lanes.fire_perimeters.wfigs import (
    DIRECT_FEATURE_ID_PREFIX,
    LAYER_ENDPOINT,
    PUBLISHED_STATUS,
    QUERY_ENDPOINT,
    SNAPSHOT_UNIT,
    STRATEGY,
    content_digest,
)
from agri_data_service.pipeline.runner.contract import ProbeWindow, SourceThrottledError, SourceUnavailableError
from agri_data_service.pipeline.runner.resolve import resolve_strategy
from tests.lane_config.builders import REAL_LANES_DIRECTORY
from tests.runner.fakes import NOW, ScriptedUpstream

if TYPE_CHECKING:
    from collections.abc import Mapping

LANE_ID = "fire-perimeters-direct-forward"
#: The probed `editingInfo.lastEditDate` on an ordinary turn: 2026-08-10T12:00:00Z.
LAST_EDIT_MS = 1_786_363_200_000
EXPECTED_WATERMARK_DAY = date(2026, 8, 10)
WINDOW = ProbeWindow(first=date(2026, 7, 28), last=date(2026, 8, 10))


def _layer_body(payload: Mapping[str, object]) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _edge_upstream(answer) -> ScriptedUpstream:  # noqa: ANN001 - the fixture's own callable shape
    return ScriptedUpstream(answer=answer)


# --- request planning -----------------------------------------------------------------------------


def test_shipped_lane_resolves_this_strategy() -> None:
    """The dark TOML parses, is `static_lookup`/`executor = "legacy"`, and S14-resolves to `STRATEGY`."""
    lane = load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw")).lanes[LANE_ID]
    assert lane.nature == "static_lookup"
    assert lane.executor == "legacy"
    assert lane.strategy == "fire_perimeters.wfigs"
    assert resolve_strategy(lane) is STRATEGY


def test_plan_requests_is_one_unit_over_the_region_envelope() -> None:
    """One snapshot unit, days passed through verbatim, bbox drawn from the region's camera envelope."""
    lane = load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw")).lanes[LANE_ID]
    region = load_region("pnw")
    envelope = region.default_camera_envelope
    requests = STRATEGY.plan_requests([EXPECTED_WATERMARK_DAY], lane, region)
    assert len(requests) == 1
    (request,) = requests
    assert request.unit == SNAPSHOT_UNIT
    assert request.endpoint == QUERY_ENDPOINT
    assert request.days == (EXPECTED_WATERMARK_DAY,)
    bbox = request.query()["bbox"]
    west, south, east, north = (float(part) for part in bbox.split(","))
    assert (west, south, east, north) == (envelope.west, envelope.south, envelope.east, envelope.north)
    assert request.support_digest  # bound to the bbox; non-empty and stable (asserted below)
    again = STRATEGY.plan_requests([EXPECTED_WATERMARK_DAY], lane, region)
    assert again[0].support_digest == request.support_digest


def test_plan_requests_passes_through_whichever_day_the_runner_resolved() -> None:
    """A static lookup has no calendar of its own; the unit answers whatever day `probe_edge` named."""
    lane = load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw")).lanes[LANE_ID]
    region = load_region("pnw")
    other_day = date(2020, 1, 1)
    (request,) = STRATEGY.plan_requests([other_day], lane, region)
    assert request.days == (other_day,)


# --- watermark-fault classification -----------------------------------------------------------------


async def test_probe_edge_ok_names_the_watermark_day() -> None:
    """A readable `editingInfo.lastEditDate` becomes the one valued day, UTC-dated."""
    upstream = _edge_upstream(
        lambda endpoint, params, probe: _layer_body({"editingInfo": {"lastEditDate": LAST_EDIT_MS}})  # noqa: ARG005
    )
    edge = await STRATEGY.probe_edge(upstream, WINDOW)
    assert edge.status == "ok"
    assert edge.valued_days == frozenset({EXPECTED_WATERMARK_DAY})
    assert edge.edge == EXPECTED_WATERMARK_DAY
    (send,) = upstream.sends
    assert send.endpoint == LAYER_ENDPOINT
    assert send.probe is True


async def test_probe_edge_classifies_a_missing_editing_info_as_invalid() -> None:
    upstream = _edge_upstream(lambda endpoint, params, probe: _layer_body({"id": 0}))  # noqa: ARG005
    edge = await STRATEGY.probe_edge(upstream, WINDOW)
    assert edge.status == "invalid"
    assert edge.valued_days == frozenset()


async def test_probe_edge_classifies_a_non_json_body_as_invalid() -> None:
    upstream = _edge_upstream(lambda endpoint, params, probe: b"<html>not json</html>")  # noqa: ARG005
    edge = await STRATEGY.probe_edge(upstream, WINDOW)
    assert edge.status == "invalid"


async def test_probe_edge_classifies_an_arcgis_error_body_as_invalid() -> None:
    upstream = _edge_upstream(
        lambda endpoint, params, probe: _layer_body({"error": {"code": 400, "message": "Invalid or missing input"}})  # noqa: ARG005
    )
    edge = await STRATEGY.probe_edge(upstream, WINDOW)
    assert edge.status == "invalid"


async def test_probe_edge_classifies_untracked_editing_as_blind() -> None:
    """`lastEditDate == 0` means editor tracking is off, not that the edit happened at the UNIX epoch."""
    upstream = _edge_upstream(
        lambda endpoint, params, probe: _layer_body({"editingInfo": {"lastEditDate": 0}})  # noqa: ARG005
    )
    edge = await STRATEGY.probe_edge(upstream, WINDOW)
    assert edge.status == "blind"
    assert edge.valued_days == frozenset()


async def test_probe_edge_classifies_a_429_as_deferred() -> None:
    def _answer(endpoint: str, params: dict[str, str], probe: bool) -> bytes:
        raise SourceThrottledError("upstream request failed with status 429")

    edge = await STRATEGY.probe_edge(_edge_upstream(_answer), WINDOW)
    assert edge.status == "deferred"


async def test_probe_edge_classifies_a_transport_failure_as_unavailable() -> None:
    def _answer(endpoint: str, params: dict[str, str], probe: bool) -> bytes:
        raise SourceUnavailableError("connection reset")

    edge = await STRATEGY.probe_edge(_edge_upstream(_answer), WINDOW)
    assert edge.status == "unavailable"


# --- watermark-unchanged short-circuit (content_digest stability) ----------------------------------


def _row(unique_fire_identifier: str, *, updated_at: datetime, gis_acres: float = 120.0) -> dict[str, object]:
    return {
        "feature_id": f"{DIRECT_FEATURE_ID_PREFIX}:{unique_fire_identifier}",
        "unique_fire_identifier": unique_fire_identifier,
        "observed_day": date(2026, 8, 9),
        "incident_name": "Test Fire",
        "irwin_id": None,
        "fire_discovery_at": None,
        "polygon_at": None,
        "gis_acres": gis_acres,
        "fire_cause": None,
        "incident_type_category": "WF",
        "poo_state": "US-OR",
        "percent_contained": 40.0,
        "severity": "high",
        "status": PUBLISHED_STATUS,
        "data_available_at": None,
        "updated_at": updated_at,
        "geometry_wkb": b"\x01\x02\x03",
    }


def test_content_digest_is_stable_across_a_pure_refetch() -> None:
    """A re-fetch that changes only the retrieval instant must digest identically (the S11 short-circuit).

    This is the property `probe_edge` leans on: when WFIGS has not changed, a refetch forced by a
    stale-looking receipt must still agree with the prior turn's digest so the runner's rewrite rule
    (`pipeline/runner/AGENTS.md` "Static lookup") treats the day as unchanged rather than rewriting an
    identical ~23 MB version every tick.
    """
    first = content_digest([_row("2026-OR-TST-000123", updated_at=NOW)])
    later = content_digest([_row("2026-OR-TST-000123", updated_at=datetime(2026, 8, 10, 6, tzinfo=UTC))])
    assert first == later


def test_content_digest_changes_when_a_real_column_changes() -> None:
    """A genuine content change (acreage) must move the digest, or a real update would be missed."""
    unchanged = content_digest([_row("2026-OR-TST-000123", updated_at=NOW)])
    changed = content_digest([_row("2026-OR-TST-000123", updated_at=NOW, gis_acres=4500.0)])
    assert unchanged != changed


def test_content_digest_is_order_independent() -> None:
    """Two populations differing only in input order digest identically (sorted by fire identifier)."""
    first = content_digest([_row("2026-OR-A-1", updated_at=NOW), _row("2026-OR-B-2", updated_at=NOW)])
    reordered = content_digest([_row("2026-OR-B-2", updated_at=NOW), _row("2026-OR-A-1", updated_at=NOW)])
    assert first == reordered
