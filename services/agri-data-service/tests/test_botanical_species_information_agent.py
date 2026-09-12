"""The species tool is reachable through the shared provider and MCP registries."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

from agri_data_service.agent import tools as agent_tools
from agri_data_service.agent.graph import AssessSufficiency, WarehouseEvidence
from agri_data_service.agent.llm import tool_schemas
from agri_data_service.agent.mcp_server import McpToolServer, tool_descriptors
from agri_data_service.planes.botanical_species_information import MAX_COMPANION_LIMIT

SPECIES_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def test_species_tool_schema_is_exact_uuid_and_bounded_on_both_surfaces() -> None:
    openai = next(item["function"] for item in tool_schemas() if item["function"]["name"] == "species_information")
    mcp = next(item for item in tool_descriptors() if item["name"] == "species_information")
    assert openai["parameters"] == mcp["inputSchema"]
    assert set(openai["parameters"]["required"]) == {"species_id"}
    assert openai["parameters"]["properties"]["companion_limit"]["maximum"] == MAX_COMPANION_LIMIT


async def test_mcp_call_uses_run_context_provider_and_does_not_count_as_location_coverage(monkeypatch: Any) -> None:
    marker = object()

    @asynccontextmanager
    async def provider() -> Any:
        yield marker

    async def fake_read(request: Any, *, session_provider: Any) -> dict[str, Any]:
        assert str(request.species_id) == SPECIES_ID
        async with session_provider() as supplied:
            assert supplied is marker
        return {
            "state": "found",
            "species_id": SPECIES_ID,
            "publication_state": "not_published",
            "profile_release_id": None,
        }

    monkeypatch.setattr(agent_tools, "read_species_information", fake_read)
    async with agent_tools.run_context(session_provider=provider) as ledger:
        response = await McpToolServer().handle(
            {
                "jsonrpc": "2.0",
                "id": "species",
                "method": "tools/call",
                "params": {"name": "species_information", "arguments": {"species_id": SPECIES_ID}},
            }
        )
    assert response is not None
    assert response["result"]["isError"] is False
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["publication_state"] == "not_published"
    assert ledger[0]["row_count"] == 0
    evidence = WarehouseEvidence(tool_calls=tuple(ledger), populated_tools=(), refused=False)
    expected_location_tools = len(agent_tools.WAREHOUSE_TOOLS) - 1
    assert AssessSufficiency.decide(evidence, has_question=True).coverage["tools_available"] == expected_location_tools


async def test_name_only_mcp_call_is_rejected_by_the_model_facing_schema() -> None:
    response = await McpToolServer().handle(
        {
            "jsonrpc": "2.0",
            "id": "species",
            "method": "tools/call",
            "params": {"name": "species_information", "arguments": {"name": "Lupinus argenteus"}},
        }
    )
    assert response is not None
    assert response["result"]["isError"] is True


async def test_graph_context_refuses_species_uuid_not_supplied_by_caller(monkeypatch: Any) -> None:
    async def fail_read(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("a mismatched identity must be rejected before opening the database")

    monkeypatch.setattr(agent_tools, "read_species_information", fail_read)
    async with agent_tools.run_context(allowed_species_id=""):
        payload = json.loads(await agent_tools.species_information(SPECIES_ID))
    assert payload["state"] == "refused"
    assert payload["reason"]["code"] == "invalid_species_information_request"


async def test_graph_context_binds_species_uuid_to_caller_input(monkeypatch: Any) -> None:
    async def fake_read(request: Any, *, session_provider: Any) -> dict[str, Any]:
        assert str(request.species_id) == SPECIES_ID
        assert session_provider is not None
        return {"state": "found", "publication_state": "not_published", "profile_release_id": None}

    monkeypatch.setattr(agent_tools, "read_species_information", fake_read)
    async with agent_tools.run_context(allowed_species_id=SPECIES_ID):
        allowed = json.loads(await agent_tools.species_information(SPECIES_ID))
        refused = json.loads(await agent_tools.species_information("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"))
    assert allowed["state"] == "found"
    assert refused["reason"]["code"] == "invalid_species_information_request"
