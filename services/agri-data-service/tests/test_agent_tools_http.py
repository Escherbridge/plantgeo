"""The live-agent bridge preserves the warehouse contract without requiring a model provider."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from agri_data_service.agent import strategy_knowledge, tools
from agri_data_service.agent.surfaces import AGENT_SURFACE_NAMES, surface_lanes
from agri_data_service.config import settings
from agri_data_service.routes import agent_tools as route
from tests.agent_fakes import FakeAgentWarehouse
from tests.parquet_ops.test_mtbs_snapshot_catalog import warehouse as snapshot_warehouse

SELECTED_DAY = "2024-03-14"
BOISE = {"longitude": -116.2, "latitude": 43.6}
OK = 200
BAD_REQUEST = 400


def request_for(name: str, arguments: dict[str, Any]) -> Any:
    """Build the HTTP adapter's input without starting listeners or touching a provider."""
    payload = {"name": name, "arguments": arguments}
    return SimpleNamespace(json=payload, body=json.dumps(payload).encode())


async def test_catalogue_covers_every_map_surface_and_omits_authoring() -> None:
    response = await route.list_agent_tools(SimpleNamespace())
    body = json.loads(response.body)
    assert set(body["surfaces"]) == set(AGENT_SURFACE_NAMES)
    assert set(body["value_surfaces"]) == set(AGENT_SURFACE_NAMES)
    names = {tool["function"]["name"] for tool in body["tools"]}
    assert names == {tool.name for tool in tools.WAREHOUSE_TOOLS} - {"species_information"}
    assert {"surface_evidence_for_selection", "list_environmental_layers"} <= names
    assert not {"signals_near_point", "signal_value_on_day", "signal_neighbors_in_time", "nearest_signal_cells"} & names


async def test_bridge_preserves_typed_unbound_region_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    source = FakeAgentWarehouse()
    monkeypatch.setenv("PLANTGEO_REGION", "kenya-highlands")
    monkeypatch.setattr(route, "run_context", lambda: tools.run_context(warehouse_source=source))
    response = await route.call_agent_tool(
        request_for(
            "surface_evidence_for_selection",
            {
                **BOISE,
                "surface_name": "soil-survey",
                "day": SELECTED_DAY,
                "range_start": SELECTED_DAY,
                "range_end": SELECTED_DAY,
            },
        )
    )
    assert response.status == OK
    body = json.loads(response.body)
    assert body["tool"] == "surface_evidence_for_selection"
    assert body["result"]["state"] == "refused"
    assert body["result"]["refusal_code"] == "not_available_in_region"
    assert source.executed == []


@pytest.mark.parametrize(
    "name",
    [
        "species_information",
        "execute_sql",
        "unregistered_tool",
        "signals_near_point",
        "signal_value_on_day",
        "signal_neighbors_in_time",
        "nearest_signal_cells",
    ],
)
async def test_bridge_rejects_calls_outside_environmental_registry(name: str) -> None:
    response = await route.call_agent_tool(request_for(name, {}))
    assert response.status == BAD_REQUEST
    assert json.loads(response.body)["code"] == "unknown_environmental_tool"


async def test_bridge_names_each_rejected_argument_without_echoing_its_value() -> None:
    """The live map agent's model needs to know WHICH argument broke; the old body said only the code."""
    arguments = {"surface_name": "soil-survey", "day": SELECTED_DAY, "latitude": 42.912345}
    response = await route.call_agent_tool(request_for("observation_coverage_on_day", arguments))
    assert response.status == BAD_REQUEST
    body = json.loads(response.body)
    assert body["tool"] == "observation_coverage_on_day"
    assert body["error"] == body["code"] == "invalid_tool_arguments"
    assert body["detail"].startswith("latitude: ")
    assert "42.912345" not in response.body.decode()


async def test_bridge_rejects_oversized_request_before_tool_execution() -> None:
    request = request_for("surface_evidence_for_selection", {})
    request.body = b"x" * (route.MAX_REQUEST_BYTES + 1)
    response = await route.call_agent_tool(request)
    assert response.status == BAD_REQUEST
    assert json.loads(response.body)["code"] == "tool_request_too_large"


@pytest.mark.parametrize(
    ("tool", "query_name"),
    [
        (tools.drought_history_at_point, "query_drought_history_at_point"),
        (tools.fire_history_near_point, "query_fire_history_near_point"),
    ],
)
async def test_history_tool_schema_carries_selected_day_to_query(
    monkeypatch: pytest.MonkeyPatch, tool: Any, query_name: str
) -> None:
    query = AsyncMock(return_value="{}")
    monkeypatch.setattr(tools, query_name, query)
    await tool.call({**BOISE, "as_of_day": SELECTED_DAY})
    assert query.call_args.kwargs["as_of"] == datetime(2024, 3, 14, tzinfo=UTC)


async def test_surface_values_read_map_lanes_without_the_generic_signal_plane() -> None:
    source = FakeAgentWarehouse()
    surface = "soil-field-moisture"
    lanes = surface_lanes(surface)
    day = date.fromisoformat(SELECTED_DAY)
    for lane in lanes[:-1]:
        source.listing_store.write_day(lane, "observed", 13, day)
    source.answer("agent_point_lane_rows", [{"longitude": -116.2, "latitude": 43.6, "value": 0.25}])
    async with tools.run_context(warehouse_source=source):
        payload = json.loads(await tools.query_surface_value_near_point(surface, SELECTED_DAY, **BOISE))
    assert payload["requested_day"] == SELECTED_DAY
    assert [result["parquet_lane"] for result in payload["lanes"]] == list(lanes)
    assert payload["lanes"][-1]["day_state"]["state"] == "lane_never_written"
    assert payload["lanes"][-1]["features"] == []
    assert payload["lanes"][0]["features"][0]["served_day"] == SELECTED_DAY
    assert source.executed
    for _, parameters in source.executed:
        assert "/signal/" not in str(parameters)
        assert "year=2024/month=03/day=14" in str(parameters)


@pytest.mark.parametrize("surface", ["soil-survey", "watersheds", "fire-perimeters", "evacuation-zones"])
async def test_static_surface_serves_applicable_snapshot_without_future_leakage(
    surface: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = FakeAgentWarehouse()
    lane = surface_lanes(surface)[0]
    previous = date(2026, 9, 1)
    selected = date(2026, 9, 10)
    future = date(2026, 9, 11)
    old_part = source.listing_store.write_day(lane, "observed", 13, previous)
    source.listing_store.write_day(lane, "observed", 13, future)
    list_keys = source.listing_store.list_keys

    def bounded_listing(*args: Any, **kwargs: Any) -> tuple[str, ...]:
        assert kwargs.get("year") is not None, "release lookup must not collect a whole-tier inventory"
        return list_keys(*args, **kwargs)

    monkeypatch.setattr(source.listing_store, "list_keys", bounded_listing)
    source.answer("agent_geometry_lane_rows", [{"name": "applicable snapshot", "covers_probe_point": True}])
    async with tools.run_context(warehouse_source=source):
        payload = json.loads(await tools.query_surface_value_near_point(surface, selected.isoformat(), **BOISE))
    result = payload["lanes"][0]
    assert payload["requested_day"] == selected.isoformat()
    assert result["requested_day"] == selected.isoformat()
    assert result["served_day"] == previous.isoformat()
    assert result["lane_nature"] == "static_lookup"
    assert result["day_state"]["state"] == "published"
    assert result["day_state"]["served_day"] == previous.isoformat()
    assert result["features"][0]["served_day"] == previous.isoformat()
    assert source.part_uris_for("agent_geometry_lane_rows") == [f"s3://fake-bucket/{old_part}"]


async def test_future_only_snapshot_cannot_answer_selected_day(monkeypatch: pytest.MonkeyPatch) -> None:
    source = FakeAgentWarehouse()
    source.listing_store.write_day("watersheds", "observed", 13, date(2026, 9, 11))
    list_keys = source.listing_store.list_keys

    def bounded_listing(*args: Any, **kwargs: Any) -> tuple[str, ...]:
        assert kwargs.get("year") is not None
        return list_keys(*args, **kwargs)

    monkeypatch.setattr(source.listing_store, "list_keys", bounded_listing)
    async with tools.run_context(warehouse_source=source):
        payload = json.loads(await tools.query_surface_value_near_point("watersheds", "2026-09-10", **BOISE))
    assert payload["lanes"][0]["day_state"]["state"] == "day_not_written"
    assert payload["lanes"][0]["served_day"] is None
    assert payload["lanes"][0]["features"] == []
    assert source.executed == []


async def test_missing_daily_sample_stops_existence_probe_after_first_key(monkeypatch: pytest.MonkeyPatch) -> None:
    source = FakeAgentWarehouse()
    lane = surface_lanes("climate-field-precipitation")[0]
    older_part = source.listing_store.write_day(lane, "observed", 13, date(2026, 8, 1))
    list_keys = source.listing_store.list_keys

    def bounded_listing(*args: Any, **kwargs: Any) -> tuple[str, ...]:
        assert kwargs.get("year") is not None
        return list_keys(*args, **kwargs)

    def first_key_only(*_args: Any) -> Any:
        yield older_part
        raise AssertionError("an existence probe must not request a second layout key")

    monkeypatch.setattr(source.listing_store, "list_keys", bounded_listing)
    monkeypatch.setattr(source.listing_store, "iter_tier_keys", first_key_only)
    async with tools.run_context(warehouse_source=source):
        payload = json.loads(
            await tools.query_surface_value_near_point("climate-field-precipitation", "2026-09-10", **BOISE)
        )
    assert payload["lanes"][0]["day_state"]["state"] == "day_not_written"
    assert payload["lanes"][0]["served_day"] is None
    assert source.executed == []


@pytest.mark.parametrize("invalid_identity", [False, True])
async def test_surface_snapshot_retains_mtbs_proof_over_returned_rows(invalid_identity: bool) -> None:
    _, listing, reader, descriptor = snapshot_warehouse()
    rows = next(iter(reader.rows_by_key.values()))
    row = dict(rows[0])
    if invalid_identity:
        row["release_identifier"] = "unverified release"
    source = FakeAgentWarehouse(listing_store=listing)
    source.answer("agent_geometry_lane_rows", [row])
    async with tools.run_context(warehouse_source=source):
        payload = json.loads(await tools.query_surface_value_near_point("burn-severity", "2026-09-12", **BOISE))
    if invalid_identity:
        assert payload["error"] == "parquet_serving_refused"
        assert payload["refusal_code"] == "mtbs_snapshot_rows_invalid"
    else:
        result = payload["lanes"][0]
        assert result["served_day"] == descriptor.available_day.isoformat()
        assert result["day_state"]["mtbs_snapshot"] == descriptor.to_wire()
        assert result["features"][0]["properties"]["release_identifier"] == descriptor.release_identifier


# --- server_context (seam S1) -------------------------------------------------------

SERVER_CONTEXT: dict[str, Any] = {
    "user_question": "my pasture has gone sour",
    "point": {"longitude": -116.13, "latitude": 43.66},
    "site_facts": {"soil_ph": 5.4, "burn_severity": "high"},
}
PRIVATE_ORIGIN = "http://plantgeo-strategy-knowledge.railway.internal:8000"
FINDINGS_ANSWER: dict[str, Any] = {"claim_tier": "literature_grounded", "corpus_version": "c0ffee", "results": []}


def request_with_context(name: str, arguments: dict[str, Any], server_context: Any) -> Any:
    """`request_for`, carrying the out-of-band S1 object beside the model's arguments."""
    payload = {"name": name, "arguments": arguments, "server_context": server_context}
    return SimpleNamespace(json=payload, body=json.dumps(payload).encode())


class _ContextProbe:
    """A registered tool stand-in that records the strategy context bound while it runs."""

    def __init__(self) -> None:
        self.seen: list[strategy_knowledge.StrategyContext | None] = []

    async def call(self, _arguments: dict[str, Any]) -> str:
        self.seen.append(strategy_knowledge.current_strategy_context())
        return "{}"


async def test_bridge_forwards_server_context_to_a_literature_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "strategy_knowledge_url", PRIVATE_ORIGIN)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(OK, json=FINDINGS_ANSWER)

    with strategy_knowledge.use_transport(httpx.MockTransport(handler)):
        response = await route.call_agent_tool(
            request_with_context(
                "search_strategy_research_findings",
                {"query": "lime", "site_profile": {"slope_pct": 40}, "region": "pnw_westside"},
                SERVER_CONTEXT,
            )
        )

    assert response.status == OK
    [request] = seen
    body = json.loads(request.content)
    assert body["context_query"] == SERVER_CONTEXT["user_question"]
    assert body["site_profile"] == {"soil_ph": 5.4, "burn_severity": "high", "region": "great_basin_high_desert"}
    assert "region" not in body
    assert "43.66" not in request.content.decode(), "the literature service never sees the coordinate"
    result = json.loads(response.body)["result"]
    assert result["site_profile_source"] == "server"
    assert result["site_profile_dropped"] == ["slope_pct", "region(argument)"]


async def test_bridge_without_server_context_labels_the_profile_caller_asserted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "strategy_knowledge_url", PRIVATE_ORIGIN)
    transport = httpx.MockTransport(lambda _request: httpx.Response(OK, json=FINDINGS_ANSWER))
    with strategy_knowledge.use_transport(transport):
        response = await route.call_agent_tool(
            request_for("search_strategy_research_findings", {"query": "lime", "site_profile": {"soil_ph": 5.0}})
        )
    assert response.status == OK
    assert json.loads(response.body)["result"]["site_profile_source"] == "caller_asserted"


@pytest.mark.parametrize(
    ("name", "binds"),
    [("search_environmental_strategies", True), ("list_environmental_layers", False)],
)
async def test_bridge_binds_server_context_only_for_the_literature_tools(
    monkeypatch: pytest.MonkeyPatch, name: str, binds: bool
) -> None:
    probe = _ContextProbe()
    monkeypatch.setattr(route, "tool_by_name", lambda _name: probe)
    response = await route.call_agent_tool(request_with_context(name, {}, SERVER_CONTEXT))
    assert response.status == OK
    [bound] = probe.seen
    if binds:
        assert bound is not None
        assert (bound.longitude, bound.latitude) == (-116.13, 43.66)
        assert bound.user_question == SERVER_CONTEXT["user_question"]
    else:
        assert bound is None, "other tools ignore server_context rather than fail on it"
    assert strategy_knowledge.current_strategy_context() is None, "the binding ends with the call"


async def test_bridge_ignores_a_malformed_server_context_for_a_non_literature_tool() -> None:
    """S1: server_context is honoured for the three literature tools only, and the contract says it
    is IGNORED, not an error, everywhere else -- a malformed one on a warehouse tool call must not
    turn into a 400 the caller never asked for."""
    off_globe = {**SERVER_CONTEXT, "point": {"longitude": 512.25, "latitude": 43.66}}
    response = await route.call_agent_tool(request_with_context("list_environmental_layers", {}, off_globe))
    assert response.status == OK


@pytest.mark.parametrize(
    ("server_context", "detail_prefix", "hidden"),
    [
        pytest.param(
            {**SERVER_CONTEXT, "point": {"longitude": 512.25, "latitude": 43.66}},
            "server_context.point.longitude: ",
            "512.25",
            id="off-globe-longitude",
        ),
        pytest.param(
            {**SERVER_CONTEXT, "site_facts": {"slope_pct": 37.5}},
            "server_context.site_facts.slope_pct: ",
            "37.5",
            id="slope-is-not-a-site-fact",
        ),
        pytest.param(
            {**SERVER_CONTEXT, "site_facts": {"soil_ph": 71.25}},
            "server_context.site_facts.soil_ph: ",
            "71.25",
            id="unscaled-soilgrids-ph",
        ),
        pytest.param(
            {**SERVER_CONTEXT, "coordinates": "-116.2,43.6"},
            "server_context.coordinates: ",
            "-116.2,43.6",
            id="unknown-key",
        ),
    ],
)
async def test_bridge_rejects_a_bad_server_context_without_echoing_it(
    server_context: dict[str, Any], detail_prefix: str, hidden: str
) -> None:
    response = await route.call_agent_tool(
        request_with_context("search_environmental_strategies", {"query": "lime"}, server_context)
    )
    assert response.status == BAD_REQUEST
    body = json.loads(response.body)
    assert body["error"] == body["code"] == "invalid_tool_arguments"
    assert body["tool"] == "search_environmental_strategies"
    assert body["detail"].startswith(detail_prefix)
    assert hidden not in response.body.decode()


async def test_bridge_keeps_the_generic_refusal_when_more_than_server_context_is_wrong() -> None:
    payload = {"arguments": {}, "server_context": {"point": {"longitude": 512.25, "latitude": 0}}}
    response = await route.call_agent_tool(SimpleNamespace(json=payload, body=json.dumps(payload).encode()))
    assert response.status == BAD_REQUEST
    assert json.loads(response.body)["code"] == "invalid_tool_request"


async def test_bridge_returns_400_not_500_for_an_unhashable_name_alongside_server_context() -> None:
    """`_parse_call` used to test `raw.get("name") not in LITERATURE_TOOL_NAMES` before pydantic ever
    validated `name`'s type; a frozenset membership check hashes its argument, so an unhashable `name`
    (a list, say) raised a bare `TypeError` here instead of the ordinary 400 a non-string name gets
    everywhere else (wave-2 fix-stage review)."""
    payload = {"name": ["not", "a", "string"], "arguments": {}, "server_context": {}}
    response = await route.call_agent_tool(SimpleNamespace(json=payload, body=json.dumps(payload).encode()))
    assert response.status == BAD_REQUEST
    assert json.loads(response.body)["code"] == "invalid_tool_request"


# --- ContextVar isolation under concurrency (minor finding, wave-2 fix-stage review) ------


class _InterleavingProbe:
    """A registered tool stand-in proving `bound_strategy_context` never leaks across two
    `call_agent_tool` runs in flight together: each records ITS OWN bound context's `user_question`
    before and after an `await` point where the event loop could interleave with the other call. A
    future regression that bound context outside the per-request task -- module level, a cached client
    -- would let one call observe the other's `user_question` here."""

    def __init__(self) -> None:
        self.observed: list[tuple[str, str | None, str | None]] = []

    async def call(self, arguments: dict[str, Any]) -> str:
        which = arguments["which"]
        before = strategy_knowledge.current_strategy_context()
        await asyncio.sleep(0)
        after = strategy_knowledge.current_strategy_context()
        self.observed.append((which, before.user_question if before else None, after.user_question if after else None))
        return "{}"


async def test_concurrent_literature_calls_keep_their_own_strategy_context(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = _InterleavingProbe()
    monkeypatch.setattr(route, "tool_by_name", lambda _name: probe)
    context_a = {**SERVER_CONTEXT, "user_question": "call A"}
    context_b = {**SERVER_CONTEXT, "user_question": "call B", "point": {"longitude": -120.5, "latitude": 45.5}}

    await asyncio.gather(
        route.call_agent_tool(request_with_context("search_environmental_strategies", {"which": "a"}, context_a)),
        route.call_agent_tool(request_with_context("search_environmental_strategies", {"which": "b"}, context_b)),
    )

    assert len(probe.observed) == 2
    for which, before, after in probe.observed:
        expected = "call A" if which == "a" else "call B"
        assert before == expected, "leaked the other concurrent call's context before the await point"
        assert after == expected, "leaked the other concurrent call's context after an interleaved await"
