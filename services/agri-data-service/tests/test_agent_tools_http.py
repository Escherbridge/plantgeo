"""The live-agent bridge preserves the warehouse contract without requiring a model provider."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agri_data_service.agent import tools
from agri_data_service.agent.llm import tool_schemas
from agri_data_service.agent.surfaces import AGENT_SURFACE_NAMES, surface_lanes
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
    assert set(body["value_surfaces"]) == set(AGENT_SURFACE_NAMES) - {"drought-areas"}
    names = {tool["function"]["name"] for tool in body["tools"]}
    assert names == {tool.name for tool in tools.WAREHOUSE_TOOLS} - {"species_information"}
    assert "surface_value_near_point" in names


async def test_bridge_preserves_typed_missing_lane_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    source = FakeAgentWarehouse()
    monkeypatch.setattr(route, "run_context", lambda: tools.run_context(warehouse_source=source))
    response = await route.call_agent_tool(
        request_for("feature_value_near_point", {**BOISE, "surface_name": "interventions", "day": SELECTED_DAY})
    )
    assert response.status == OK
    body = json.loads(response.body)
    assert body["tool"] == "feature_value_near_point"
    assert body["result"]["error"] == "surface_not_served_from_parquet"
    assert source.executed == []


@pytest.mark.parametrize("name", ["species_information", "execute_sql", "unregistered_tool"])
async def test_bridge_rejects_calls_outside_environmental_registry(name: str) -> None:
    response = await route.call_agent_tool(request_for(name, {}))
    assert response.status == BAD_REQUEST
    assert json.loads(response.body)["code"] == "unknown_environmental_tool"


async def test_bridge_rejects_oversized_request_before_tool_execution() -> None:
    request = request_for("surface_value_near_point", {})
    request.body = b"x" * (route.MAX_REQUEST_BYTES + 1)
    response = await route.call_agent_tool(request)
    assert response.status == BAD_REQUEST
    assert json.loads(response.body)["code"] == "tool_request_too_large"


@pytest.mark.parametrize(
    ("tool", "query_name"),
    [
        (tools.signals_near_point, "query_signals_near_point"),
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


async def test_signal_tool_keeps_filter_and_selected_day_in_schema_and_forwarding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = AsyncMock(return_value="{}")
    monkeypatch.setattr(tools, "query_signals_near_point", query)
    schema = next(item for item in tool_schemas() if item["function"]["name"] == "signals_near_point")
    properties = schema["function"]["parameters"]["properties"]
    assert {"signal_names", "as_of_day"} <= properties.keys()

    await tools.signals_near_point.call({**BOISE, "signal_names": ["soil_moisture"], "as_of_day": SELECTED_DAY})
    assert query.call_args.kwargs["signal_names"] == ["soil_moisture"]
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
