"""The agent's provider wiring: schemas out, a credential on the wire, a tool call back in.

WHAT THIS FILE PROVES, and it is deliberately less than an end-to-end run: that the ten warehouse
tools publish usable JSON Schemas on both the MCP and the OpenAI surface, that the OpenAI client
addresses the configured provider with the configured credential and the published tools, that a
`tool_calls` reply round-trips through the real tool objects into a `role: "tool"` message, and
that MCP `initialize` -> `tools/list` -> `tools/call` answers the same tools over JSON-RPC.

WHAT IT DOES NOT PROVE: that any provider accepts the key (no socket is opened -- `httpx.MockTransport`
answers every request), and that any lane holds data (`FakeAgentWarehouse` answers every read). The
live half is `test_the_configured_provider_authenticates`, which is skipped unless
AGENT_LLM_LIVE_PROBE=1 is set, because a test suite must not spend a rate limit by default.

The refusal assertions are the point of the round trip. `observation_coverage_on_day` against an
unscripted warehouse refuses with `parquet_availability_withheld`, which is exactly what production
answers today -- no lane has a published availability receipt -- so the wiring is proven against the
refusal the caller will actually meet, not against a fabricated success.
"""
# ruff: noqa: PLR2004 - the small literal counts and JSON-RPC codes ARE the assertion; naming each one hides it.

from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from pydantic import SecretStr

from agri_data_service.agent import tools as agent_tools
from agri_data_service.agent.llm import (
    LlmProviderError,
    OpenAiCompletionsClient,
    execute_tool_call,
    tool_schemas,
)
from agri_data_service.agent.mcp_server import (
    DEFAULT_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    McpToolServer,
    reserved_stdout,
    tool_descriptors,
    write_message,
)
from agri_data_service.config import AgentLlmCredentials, Settings
from tests.agent_fakes import FakeAgentWarehouse

if TYPE_CHECKING:
    from collections.abc import Callable

WAREHOUSE_TOOL_COUNT = 12
SENTINEL_KEY = "sk-test-not-a-real-credential"
COVERAGE_TOOL = "observation_coverage_on_day"
COVERAGE_ARGUMENTS = {"surface_name": "vegetation", "day": "2026-03-14"}


def credentials(
    *, auth_header: str = "bearer", base_url: str = "https://provider.invalid/api/v1"
) -> AgentLlmCredentials:
    """A complete provider configuration whose key is a sentinel, never a real credential."""
    return AgentLlmCredentials(
        base_url=base_url,
        model="test/model-1",
        api_key=SecretStr(SENTINEL_KEY),
        auth_header=auth_header,  # type: ignore[arg-type]
        timeout_seconds=5.0,
    )


def client_answering(handler: Callable[[httpx.Request], httpx.Response], **kwargs: Any) -> OpenAiCompletionsClient:
    """A client whose every request is answered in-process; no socket is ever opened."""
    return OpenAiCompletionsClient(credentials=credentials(**kwargs), transport=httpx.MockTransport(handler))


def completion(message: dict[str, Any], *, model: str = "test/model-1") -> dict[str, Any]:
    """One OpenAI chat-completion body carrying a single choice."""
    return {"model": model, "choices": [{"index": 0, "message": message, "finish_reason": "stop"}]}


# --- The published schemas ---------------------------------------------------------


def test_every_warehouse_tool_publishes_a_usable_mcp_descriptor() -> None:
    descriptors = list(tool_descriptors())
    assert len(descriptors) == WAREHOUSE_TOOL_COUNT
    for descriptor in descriptors:
        assert descriptor["name"], "a tool with no name cannot be called"
        assert descriptor["description"].strip(), f"{descriptor['name']} publishes an empty description"
        schema = descriptor["inputSchema"]
        assert schema["type"] == "object", f"{descriptor['name']} must take an object"
        assert schema["properties"], f"{descriptor['name']} publishes no parameters"
        # A descriptor that cannot survive a JSON round trip cannot cross the stdio transport.
        assert json.loads(json.dumps(descriptor)) == descriptor


def test_the_two_surfaces_publish_the_same_tools_from_the_same_objects() -> None:
    mcp_names = [descriptor["name"] for descriptor in tool_descriptors()]
    openai_names = [schema["function"]["name"] for schema in tool_schemas()]
    assert mcp_names == openai_names
    assert set(mcp_names) >= {
        "signals_near_point",
        "drought_history_at_point",
        "fire_history_near_point",
        "feature_value_near_point",
        COVERAGE_TOOL,
        "observation_temporal_neighbors",
        "species_information",
    }


def test_the_spatial_tools_publish_their_coordinate_parameters() -> None:
    by_name = {descriptor["name"]: descriptor for descriptor in tool_descriptors()}
    for name in ("signals_near_point", "drought_history_at_point", "feature_value_near_point"):
        properties = by_name[name]["inputSchema"]["properties"]
        assert "longitude" in properties, name
        assert "latitude" in properties, name


# --- The credential on the wire ----------------------------------------------------


async def test_the_client_sends_the_bearer_credential_and_the_published_tools() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=completion({"role": "assistant", "content": "ok"}))

    client = client_answering(handler)
    await client.chat([{"role": "user", "content": "hello"}], tools=tool_schemas())

    assert seen["url"] == "https://provider.invalid/api/v1/chat/completions"
    assert seen["authorization"] == f"Bearer {SENTINEL_KEY}"
    assert seen["body"]["model"] == "test/model-1"
    assert len(seen["body"]["tools"]) == WAREHOUSE_TOOL_COUNT
    assert seen["body"]["tool_choice"] == "auto"


async def test_an_unauthenticated_local_provider_sends_no_authorization_header() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json=completion({"role": "assistant", "content": "ok"}))

    client = client_answering(handler, auth_header="none", base_url="http://localhost:1234/v1")
    await client.chat([{"role": "user", "content": "hello"}])
    assert seen["authorization"] is None


async def test_a_provider_error_carries_the_status_and_never_the_credential() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(401, json={"error": {"message": "No auth credentials found"}})

    with pytest.raises(LlmProviderError) as raised:
        await client_answering(handler).probe()
    assert raised.value.status_code == httpx.codes.UNAUTHORIZED
    assert "No auth credentials found" in str(raised.value)
    assert SENTINEL_KEY not in str(raised.value)


def test_the_client_description_never_carries_the_credential() -> None:
    described = json.dumps(client_answering(lambda _request: httpx.Response(200)).describe())
    assert SENTINEL_KEY not in described
    assert described.count("provider.invalid") == 1


# --- The tool call, round trip -----------------------------------------------------


async def test_a_tool_call_round_trips_from_the_provider_through_the_warehouse_and_back() -> None:
    """The model asks for a tool, the real tool runs against a fake warehouse, the answer goes back."""
    turns: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        turns.append(body)
        if len(turns) == 1:
            return httpx.Response(
                200,
                json=completion(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": COVERAGE_TOOL, "arguments": json.dumps(COVERAGE_ARGUMENTS)},
                            }
                        ],
                    }
                ),
            )
        return httpx.Response(200, json=completion({"role": "assistant", "content": "the lane withheld its index"}))

    client = client_answering(handler)
    async with agent_tools.run_context(warehouse_source=FakeAgentWarehouse()) as ledger:
        outcome = await client.converse([{"role": "user", "content": "is vegetation covered on 2026-03-14?"}])

    assert outcome["stopped_because"] == "model_answered"
    assert outcome["iterations"] == 2
    assert outcome["tool_calls"] == [{"tool": COVERAGE_TOOL, "arguments": COVERAGE_ARGUMENTS}]

    tool_message = next(message for message in outcome["transcript"] if message.get("role") == "tool")
    assert tool_message["tool_call_id"] == "call_1"
    payload = json.loads(tool_message["content"])
    assert payload["error"] == "parquet_availability_withheld", payload

    # The second request carried the whole transcript back, which is what makes it a conversation.
    assert [message["role"] for message in turns[1]["messages"]] == ["user", "assistant", "tool"]
    # And the tool actually ran: the ledger the graph judges sufficiency from recorded it.
    assert [entry["tool"] for entry in ledger] == [COVERAGE_TOOL]


async def test_an_unknown_tool_name_answers_the_model_instead_of_raising() -> None:
    answer = await execute_tool_call(
        {"id": "call_x", "type": "function", "function": {"name": "no_such_tool", "arguments": "{}"}}
    )
    assert answer["role"] == "tool"
    assert "no warehouse tool named 'no_such_tool'" in json.loads(answer["content"])["error"]


async def test_unparseable_arguments_answer_the_model_instead_of_raising() -> None:
    answer = await execute_tool_call(
        {"id": "call_y", "type": "function", "function": {"name": COVERAGE_TOOL, "arguments": "{not json"}}
    )
    assert "JSONDecodeError" in json.loads(answer["content"])["error"]


# --- The MCP surface ---------------------------------------------------------------


async def test_initialize_negotiates_a_known_protocol_revision() -> None:
    server = McpToolServer()
    response = await server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": SUPPORTED_PROTOCOL_VERSIONS[0]},
        }
    )
    assert response is not None
    result = response["result"]
    assert result["protocolVersion"] == SUPPORTED_PROTOCOL_VERSIONS[0]
    assert result["capabilities"]["tools"]["listChanged"] is False
    assert result["serverInfo"]["name"] == "plantgeo-agri-warehouse"


async def test_an_unknown_protocol_revision_falls_back_to_the_newest_we_speak() -> None:
    server = McpToolServer()
    response = await server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}
    )
    assert response is not None
    assert response["result"]["protocolVersion"] == DEFAULT_PROTOCOL_VERSION


async def test_the_initialized_notification_is_recorded_and_never_answered() -> None:
    server = McpToolServer()
    assert await server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.initialized is True


async def test_tools_list_publishes_every_warehouse_tool() -> None:
    response = await McpToolServer().handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert response is not None
    assert len(response["result"]["tools"]) == WAREHOUSE_TOOL_COUNT


async def test_a_typed_refusal_is_successful_content_and_not_an_mcp_error() -> None:
    """The refusal IS the answer. Flagging it isError would let a client read it as a broken tool."""
    server = McpToolServer()
    async with agent_tools.run_context(warehouse_source=FakeAgentWarehouse()):
        response = await server.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": COVERAGE_TOOL, "arguments": COVERAGE_ARGUMENTS},
            }
        )
    assert response is not None
    result = response["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"] == "parquet_availability_withheld"


async def test_arguments_the_schema_rejects_answer_iserror_rather_than_crashing() -> None:
    server = McpToolServer()
    response = await server.handle(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": COVERAGE_TOOL, "arguments": {}}}
    )
    assert response is not None
    assert response["result"]["isError"] is True


async def test_an_unknown_tool_is_invalid_params_and_names_what_exists() -> None:
    response = await McpToolServer().handle(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "nope", "arguments": {}}}
    )
    assert response is not None
    assert response["error"]["code"] == -32602
    assert COVERAGE_TOOL in response["error"]["message"]


async def test_an_unknown_method_answers_a_jsonrpc_error_and_keeps_the_session() -> None:
    server = McpToolServer()
    response = await server.handle({"jsonrpc": "2.0", "id": 6, "method": "resources/list"})
    assert response is not None
    assert response["error"]["code"] == -32601
    assert (await server.handle({"jsonrpc": "2.0", "id": 7, "method": "ping"}))["result"] == {}  # type: ignore[index]


async def test_a_diagnostic_printed_beneath_a_tool_call_cannot_reach_the_protocol_stream(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The measured corruption, locked down.

    `availability_coverage.py` logs through a bare `structlog.get_logger()`, whose unconfigured
    factory prints to stdout, and its census-fallback warning arrived between two protocol frames of
    a live session and killed the client's parser. `reserved_stdout` is the guarantee that no
    logger, library or stray print beneath a tool call can do that again.
    """
    with reserved_stdout() as stream:
        print("a library wrote this to stdout")
        write_message(stream, {"jsonrpc": "2.0", "id": 1, "result": {}})
    captured = capsys.readouterr()
    assert captured.out == '{"jsonrpc":"2.0","id":1,"result":{}}\n'
    assert "a library wrote this to stdout" in captured.err


def test_reserved_stdout_restores_the_stream_it_borrowed() -> None:
    before = sys.stdout
    with reserved_stdout():
        assert sys.stdout is not before
    assert sys.stdout is before


# --- The configuration contract ----------------------------------------------------


def test_require_agent_llm_names_every_variable_still_missing() -> None:
    with pytest.raises(ValueError, match="AGENT_LLM_BASE_URL, AGENT_LLM_MODEL, AGENT_LLM_API_KEY"):
        Settings(_env_file=None).require_agent_llm()  # type: ignore[call-arg]


def test_a_remote_provider_may_not_be_addressed_over_plaintext_http() -> None:
    with pytest.raises(ValueError, match="plaintext http on localhost"):
        Settings(_env_file=None, agent_llm_base_url="http://openrouter.ai/api/v1")  # type: ignore[call-arg]


def test_a_base_url_may_not_carry_an_embedded_credential() -> None:
    with pytest.raises(ValueError, match="credential-free"):
        Settings(_env_file=None, agent_llm_base_url="https://user:secret@provider.invalid/v1")  # type: ignore[call-arg]


def test_a_loopback_provider_may_be_addressed_over_plaintext_http() -> None:
    resolved = Settings(_env_file=None, agent_llm_base_url="http://localhost:1234/v1/")  # type: ignore[call-arg]
    assert resolved.agent_llm_base_url == "http://localhost:1234/v1"


# --- The live half, opt-in ---------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("AGENT_LLM_LIVE_PROBE") != "1",
    reason="set AGENT_LLM_LIVE_PROBE=1 to spend one real completion against the configured provider",
)
async def test_the_configured_provider_authenticates() -> None:
    """The only test here that opens a socket. Proves the key, the base URL and the model name."""
    outcome = await OpenAiCompletionsClient.from_settings().probe()
    assert outcome["authenticated"] is True
    assert outcome["tools_published"] == WAREHOUSE_TOOL_COUNT
    assert SENTINEL_KEY not in json.dumps(outcome, default=str)
