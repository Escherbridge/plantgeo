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

import pytest

from agri_data_service.foundation.lane_config import load_lane_configs
from agri_data_service.foundation.region import load_region
from agri_data_service.pipeline.lanes.fire_perimeters.wfigs import (
    DIRECT_FEATURE_ID_PREFIX,
    GEOMETRY_REPAIRED_AREA_CHANGE_KEY,
    GEOMETRY_REPAIRED_KEY,
    LAYER_ENDPOINT,
    PUBLISHED_STATUS,
    QUERY_ENDPOINT,
    SNAPSHOT_UNIT,
    STRATEGY,
    FirePerimeterSnapshot,
    content_digest,
)
from agri_data_service.pipeline.runner.contract import (
    ProbeWindow,
    SourceRequest,
    SourceResponse,
    SourceThrottledError,
    SourceUnavailableError,
)
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


async def test_probe_edge_classifies_a_genuinely_malformed_arcgis_error_body_as_invalid() -> None:
    """A real schema/parameter fault (not a busy throttle) stays `invalid`, never masked as `deferred`."""
    upstream = _edge_upstream(
        lambda endpoint, params, probe: _layer_body({"error": {"code": 400, "message": "Invalid or missing input"}})  # noqa: ARG005
    )
    edge = await STRATEGY.probe_edge(upstream, WINDOW)
    assert edge.status == "invalid"


async def test_probe_edge_classifies_a_200_busy_arcgis_body_as_deferred() -> None:
    """ArcGIS throttles INSIDE an HTTP 200 body (`lanes/_providers/nifc-wfigs.toml`'s own note; M1).

    A `SourceThrottledError` can only model an HTTP 429 -- this upstream never raises one, so a
    throttle this shape could only ever surface by reading the body, which is what `probe_edge` must
    do rather than folding every error body into `invalid`.
    """
    upstream = _edge_upstream(
        lambda endpoint, params, probe: _layer_body({"error": {"code": 500, "message": "Too many requests"}})  # noqa: ARG005
    )
    edge = await STRATEGY.probe_edge(upstream, WINDOW)
    assert edge.status == "deferred"
    assert edge.valued_days == frozenset()


async def test_probe_edge_classifies_untracked_editing_as_blind() -> None:
    """`lastEditDate == 0` means editor tracking is off, not that the edit happened at the UNIX epoch."""
    upstream = _edge_upstream(
        lambda endpoint, params, probe: _layer_body({"editingInfo": {"lastEditDate": 0}})  # noqa: ARG005
    )
    edge = await STRATEGY.probe_edge(upstream, WINDOW)
    assert edge.status == "blind"
    assert edge.valued_days == frozenset()


async def test_probe_edge_lets_a_429_propagate_to_the_runners_own_cooldown_ladder() -> None:
    """M1 (corrected after ring review): `turn.py::_spend_probe` owns Retry-After, not this function."""

    def _answer(endpoint: str, params: dict[str, str], probe: bool) -> bytes:  # noqa: ARG001 - the fake mirrors ProviderClient.get
        raise SourceThrottledError("upstream request failed with status 429")

    with pytest.raises(SourceThrottledError):
        await STRATEGY.probe_edge(_edge_upstream(_answer), WINDOW)


async def test_probe_edge_lets_a_transport_failure_propagate_to_the_runners_own_ladder() -> None:
    def _answer(endpoint: str, params: dict[str, str], probe: bool) -> bytes:  # noqa: ARG001 - the fake mirrors ProviderClient.get
        raise SourceUnavailableError("connection reset")

    with pytest.raises(SourceUnavailableError):
        await STRATEGY.probe_edge(_edge_upstream(_answer), WINDOW)


async def test_probe_edge_masks_a_second_same_utc_day_edit_until_rollover() -> None:
    """H1 pinning test (re-read after ring review): the masking mechanism, made concrete here.

    `editingInfo.lastEditDate` at 01:00Z and at 21:00Z on the SAME UTC day report the identical
    `valued_days`/`edge`, which is exactly what lets `pipeline/runner/turn.py::_static_forward`'s
    `served_day >= watermark_day` gate call a second, genuinely different same-day edit "already
    current" without this strategy's `settle`/`content_digest` ever being consulted. This is not a
    bug in `probe_edge` to fix -- a day-granularity watermark cannot do otherwise -- it is the
    mechanism `docs/lanes/fire-perimeters.md` §8's H1 item and the owner register row
    `static_freshness_contract` name as an open residual.
    """
    morning_ms = 1_786_323_600_000  # 2026-08-10T01:00:00Z
    evening_ms = 1_786_395_600_000  # 2026-08-10T21:00:00Z

    async def _edge_for(last_edit_ms: int):  # noqa: ANN202 - local helper, inferred return type
        upstream = _edge_upstream(
            lambda endpoint, params, probe: _layer_body({"editingInfo": {"lastEditDate": last_edit_ms}})  # noqa: ARG005
        )
        return await STRATEGY.probe_edge(upstream, WINDOW)

    morning = await _edge_for(morning_ms)
    evening = await _edge_for(evening_ms)

    assert morning.status == evening.status == "ok"
    assert morning.valued_days == evening.valued_days == frozenset({date(2026, 8, 10)})
    assert morning.edge == evening.edge == date(2026, 8, 10)


# --- watermark-unchanged short-circuit (content_digest stability) ----------------------------------


def _row(
    unique_fire_identifier: str,
    *,
    updated_at: datetime,
    gis_acres: float = 120.0,
    repaired: bool = False,
    area_change: float | None = None,
) -> dict[str, object]:
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
        GEOMETRY_REPAIRED_KEY: repaired,
        GEOMETRY_REPAIRED_AREA_CHANGE_KEY: area_change,
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


# --- restored repair reporting (L3) ----------------------------------------------------------------


def test_snapshot_repairs_lists_only_the_repaired_rows_with_their_area_change() -> None:
    """`FirePerimeterSnapshot.repairs` restores the legacy writer's `.repairs` property (L3)."""
    snapshot = FirePerimeterSnapshot(
        rows=(
            _row("2026-OR-UNCHANGED-1", updated_at=NOW, repaired=False, area_change=None),
            _row("2026-OR-REPAIRED-2", updated_at=NOW, repaired=True, area_change=0.015),
        ),
        fetched_at=NOW,
        perimeters_seen=2,
        rejected=0,
        collapsed=0,
    )

    repairs = snapshot.repairs

    assert len(repairs) == 1
    assert (repairs[0].unique_fire_identifier, repairs[0].area_change) == ("2026-OR-REPAIRED-2", 0.015)


def test_rows_strips_the_repair_audit_keys_before_building_the_arrow_table() -> None:
    """`rows()` whitelists schema columns (L3): the repair-audit keys never leak into the base rung."""
    snapshot = FirePerimeterSnapshot(
        rows=(_row("2026-OR-REPAIRED-3", updated_at=NOW, repaired=True, area_change=-0.02),),
        fetched_at=NOW,
        perimeters_seen=1,
        rejected=0,
        collapsed=0,
    )
    request = SourceRequest(unit=SNAPSHOT_UNIT, endpoint=QUERY_ENDPOINT, days=(date(2026, 8, 10),))
    response = SourceResponse(request=request, body=b"{}", retrieved_at=NOW, payload=snapshot)

    table = STRATEGY.rows(date(2026, 8, 10), [response])

    assert GEOMETRY_REPAIRED_KEY not in table.column_names
    assert GEOMETRY_REPAIRED_AREA_CHANGE_KEY not in table.column_names
    assert table.num_rows == 1
