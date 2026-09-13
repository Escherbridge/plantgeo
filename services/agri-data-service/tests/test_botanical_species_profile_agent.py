"""The registered model and MCP tools read pinned Parquet and preserve botanical claim limits."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.agent import tools as agent_tools
from agri_data_service.agent.botanical_species_profiles import species_information, use_profile_storage
from agri_data_service.agent.graph import AssessSufficiency, WarehouseEvidence
from agri_data_service.agent.llm import execute_tool_call, tool_schemas
from agri_data_service.agent.mcp_server import McpToolServer, tool_descriptors
from agri_data_service.pipeline.direct.botanical_species_profiles.publication import artifact_key
from tests.agent_fakes import FakeAgentWarehouse
from tests.planes.test_botanical_species_profiles import (
    FIXTURE_RELEASE_MISSING,
    ReadOnlyProfileStorage,
    fields_by_trait,
    publish_profile_fixture,
)

pytestmark = pytest.mark.skip(
    reason="botanical_species_profile_lookup_20260911: species_information exists but is deliberately not "
    "registered in agent/tools.py's WAREHOUSE_TOOLS, agent/graph.py, agent/mcp_server.py or agent/prompts.py "
    "pending the P4/P5 integration and independent data-governance/botanical-science/agent-honesty verdict "
    "the track's acceptance section requires. Note also: main already has an unrelated `species_information` "
    "tool (agent/tools.py, the companion/authoring lookup) that this track's tool must not collide with -- "
    "the pending patch imports it as `species_profile_information`. See conductor/tracks/"
    "botanical_species_profile_lookup_20260911/evidence/shared-registration-20260912.patch; delete this skip "
    "once it lands."
)

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

EXPECTED_ASSERTION_LIMIT = 100


def _forbid_sql() -> AbstractAsyncContextManager[AsyncSession]:
    raise AssertionError("a botanical profile must never read an editable database")


def test_registered_tool_has_the_same_bounded_identity_schema_in_every_model_surface() -> None:
    assert species_information in agent_tools.WAREHOUSE_TOOLS
    descriptor = next(item for item in tool_descriptors() if item["name"] == "species_information")
    function = next(item["function"] for item in tool_schemas() if item["function"]["name"] == "species_information")
    assert descriptor["inputSchema"] == function["parameters"]
    schema = function["parameters"]
    assert set(schema["required"]) == {"authority", "authority_version", "taxon_id", "release_id"}
    assert set(schema["properties"]) == {
        "authority",
        "authority_version",
        "taxon_id",
        "release_id",
        "assertion_limit",
        "cursor",
    }
    assert schema["properties"]["release_id"]["pattern"] == r"^bspf-[0-9a-f]{64}$"
    assert schema["properties"]["assertion_limit"]["maximum"] == EXPECTED_ASSERTION_LIMIT
    assert schema["properties"]["assertion_limit"]["minimum"] == 1


async def test_species_tool_reads_parquet_and_records_reference_evidence_without_local_coverage(tmp_path: Path) -> None:
    fixture = publish_profile_fixture(tmp_path)
    storage = ReadOnlyProfileStorage(fixture.storage)
    with use_profile_storage(storage):
        async with agent_tools.run_context(
            session_provider=_forbid_sql, warehouse_source=FakeAgentWarehouse()
        ) as ledger:
            result = json.loads(await species_information.call(fixture.parameters))
    assert result["state"] == "published"
    assert result["profile_release_id"] == fixture.release.release_id
    assert fields_by_trait(result)["soil_ph"]["state"] == "conflict"
    assert fields_by_trait(result)["heat_content"]["state"] == "refused"
    assert result["claim_limits"]["planting_recommendation"] == "refused"
    assert ledger == [
        {
            "tool": "species_information",
            "row_count": 0,
            "profile_count": 1,
            "state": "published",
            "profile_release_id": fixture.release.release_id,
            "evidence_domain": "botanical_reference",
        }
    ]
    evidence = WarehouseEvidence(tool_calls=tuple(ledger), populated_tools=(), refused=False)
    assert AssessSufficiency.decide(evidence, has_question=True).warehouse_is_sufficient is False


def test_static_lookup_does_not_make_complete_environmental_coverage_permanently_partial() -> None:
    environmental_tools = tuple(tool.name for tool in agent_tools.WAREHOUSE_TOOLS if tool.name != "species_information")
    evidence = WarehouseEvidence(tool_calls=(), populated_tools=environmental_tools, refused=False)
    verdict = AssessSufficiency.decide(evidence, has_question=True)
    assert verdict.warehouse_is_sufficient is True
    assert verdict.coverage["tools_available"] == len(environmental_tools)


async def test_openai_tool_call_returns_raw_citations_and_source_release_identity(tmp_path: Path) -> None:
    fixture = publish_profile_fixture(tmp_path)
    with use_profile_storage(ReadOnlyProfileStorage(fixture.storage)):
        result = await execute_tool_call(
            {
                "id": "profile-call",
                "type": "function",
                "function": {
                    "name": "species_information",
                    "arguments": json.dumps(fixture.parameters),
                },
            }
        )
    payload = json.loads(result["content"])
    assert result["role"] == "tool"
    assert result["tool_call_id"] == "profile-call"
    assert payload["release"] == fixture.release.manifest.model_dump(mode="json")
    assertion = payload["assertions"][0]
    assert assertion["source_url"] == "https://example.org/releases/v1/data.tsv"
    assert assertion["source_version"] == "v1"
    assert assertion["licence_id"] == "CC0-1.0"


async def _mcp_call(arguments: dict[str, Any]) -> dict[str, Any]:
    response = await McpToolServer().handle(
        {
            "jsonrpc": "2.0",
            "id": "profile",
            "method": "tools/call",
            "params": {"name": "species_information", "arguments": arguments},
        }
    )
    assert response is not None
    result: dict[str, Any] = response["result"]
    return result


async def test_mcp_lists_and_calls_the_published_species_tool(tmp_path: Path) -> None:
    fixture = publish_profile_fixture(tmp_path)
    listed = await McpToolServer().handle({"jsonrpc": "2.0", "id": "list", "method": "tools/list"})
    assert listed is not None
    assert any(tool["name"] == "species_information" for tool in listed["result"]["tools"])
    with use_profile_storage(ReadOnlyProfileStorage(fixture.storage)):
        result = await _mcp_call(fixture.parameters)
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["state"] == "published"
    assert payload["taxon_identity"] == fixture.request.identity.model_dump()
    assert payload["claim_limits"]["species_ranking"] == "refused"


@pytest.mark.parametrize("failure", ["unknown", "unpublished", "integrity"])
async def test_mcp_unknown_and_refusal_states_are_evidence_not_fabricated_success(tmp_path: Path, failure: str) -> None:
    fixture = publish_profile_fixture(tmp_path)
    arguments = fixture.parameters
    if failure == "unknown":
        arguments["taxon_id"] = "unmatched-canonical-id"
    elif failure == "unpublished":
        arguments["release_id"] = FIXTURE_RELEASE_MISSING
    else:
        (fixture.storage.root / artifact_key(fixture.release.release_id, "assertions")).write_bytes(b"corrupt")
    with use_profile_storage(ReadOnlyProfileStorage(fixture.storage)):
        result = await _mcp_call(arguments)
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["state"] == ("unknown" if failure == "unknown" else "refused")
    assert payload["assertions"] == []


async def test_mcp_cannot_substitute_a_name_only_or_latest_call() -> None:
    result = await _mcp_call({"name": "Syntheticus testii", "release_id": "latest"})
    assert result["isError"] is True
