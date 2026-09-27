"""EB1 loop hardening: the identical-rejected-call guard, the final no-tools answer turn, and
`agent_system_message` -- CONTRACT-WAVE2.md seam S5 and STRATEGIES.md workstream B.2/B.3.

Same `httpx.MockTransport` style as `test_agent_provider_wiring.py`: no socket is ever opened, and
`FakeAgentWarehouse` answers every tool read.
"""

from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import SecretStr

if TYPE_CHECKING:
    import pytest

from agri_data_service.agent import strategy_knowledge
from agri_data_service.agent import tools as agent_tools
from agri_data_service.agent.llm import (
    REPEATED_REJECTED_CALL_ERROR,
    OpenAiCompletionsClient,
    agent_system_message,
)
from agri_data_service.agent.mcp_server import INSTRUCTIONS
from agri_data_service.config import AgentLlmCredentials
from tests.agent_fakes import FakeAgentWarehouse, published_lane

COVERAGE_TOOL = "observation_coverage_on_day"
BAD_ARGUMENTS = {"surface_name": "vegetation"}  # missing required `day` -- a schema rejection
GOOD_ARGUMENTS = {"surface_name": "vegetation", "day": "2026-03-14"}
STRATEGY_TOOL = "search_environmental_strategies"
UNAVAILABLE_ARGUMENTS = {"query": "lime for sour pasture"}


def credentials() -> AgentLlmCredentials:
    return AgentLlmCredentials(
        base_url="https://provider.invalid/api/v1",
        model="test/model-1",
        api_key=SecretStr("sk-test-not-a-real-credential"),
        auth_header="bearer",
        timeout_seconds=5.0,
    )


def completion(message: dict[str, Any]) -> dict[str, Any]:
    return {"model": "test/model-1", "choices": [{"index": 0, "message": message, "finish_reason": "stop"}]}


def tool_call_message(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
        ],
    }


# --- Identical-rejected-call guard --------------------------------------------------


async def test_a_repeated_identical_call_after_a_rejection_is_not_executed_again() -> None:
    """The model retries the SAME broken arguments twice; the second attempt must not reach the
    real tool -- it is answered with `repeated_rejected_call` instead."""
    turns: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        turns.append(json.loads(request.content))
        if len(turns) <= 2:
            return httpx.Response(
                200, json=completion(tool_call_message(f"call_{len(turns)}", COVERAGE_TOOL, BAD_ARGUMENTS))
            )
        return httpx.Response(200, json=completion({"role": "assistant", "content": "gave up"}))

    client = OpenAiCompletionsClient(credentials=credentials(), transport=httpx.MockTransport(handler))
    async with agent_tools.run_context(warehouse_source=FakeAgentWarehouse()):
        outcome = await client.converse([{"role": "user", "content": "is vegetation covered?"}])

    tool_messages = [message for message in outcome["transcript"] if message.get("role") == "tool"]
    assert len(tool_messages) == 2
    first_payload = json.loads(tool_messages[0]["content"])
    second_payload = json.loads(tool_messages[1]["content"])
    assert first_payload["error"] != REPEATED_REJECTED_CALL_ERROR, "the FIRST attempt must still run for real"
    assert second_payload["error"] == REPEATED_REJECTED_CALL_ERROR
    assert "change the arguments" in second_payload["detail"]
    # The ledger records the skip rather than pretending the tool ran a second time.
    assert outcome["tool_calls"][1]["skipped"] == REPEATED_REJECTED_CALL_ERROR
    assert "skipped" not in outcome["tool_calls"][0]


async def test_a_repeat_with_different_arguments_is_not_guarded() -> None:
    """The guard keys on canonical arguments, not the tool name alone -- a genuinely corrected retry
    must still reach the real tool."""
    turns: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        turns.append(json.loads(request.content))
        if len(turns) == 1:
            return httpx.Response(200, json=completion(tool_call_message("call_1", COVERAGE_TOOL, BAD_ARGUMENTS)))
        if len(turns) == 2:
            return httpx.Response(200, json=completion(tool_call_message("call_2", COVERAGE_TOOL, GOOD_ARGUMENTS)))
        return httpx.Response(200, json=completion({"role": "assistant", "content": "answered"}))

    client = OpenAiCompletionsClient(credentials=credentials(), transport=httpx.MockTransport(handler))
    async with agent_tools.run_context(warehouse_source=FakeAgentWarehouse()):
        outcome = await client.converse([{"role": "user", "content": "is vegetation covered?"}])

    tool_messages = [message for message in outcome["transcript"] if message.get("role") == "tool"]
    payloads = [json.loads(message["content"]) for message in tool_messages]
    assert all(payload.get("error") != REPEATED_REJECTED_CALL_ERROR for payload in payloads)


async def test_argument_key_order_does_not_defeat_the_guard() -> None:
    """Canonical-JSON comparison: the same arguments in a different key order are still the SAME call.

    `day` as an int (rather than the required ISO string) fails the tool's schema regardless of
    pydantic's key order, so both dicts below are rejected identically -- only their key ORDER differs.
    """
    rejected = {"surface_name": "vegetation", "day": 12345}
    reordered = {"day": 12345, "surface_name": "vegetation"}
    turns: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        turns.append(json.loads(request.content))
        if len(turns) == 1:
            return httpx.Response(200, json=completion(tool_call_message("call_1", COVERAGE_TOOL, rejected)))
        if len(turns) == 2:
            return httpx.Response(200, json=completion(tool_call_message("call_2", COVERAGE_TOOL, reordered)))
        return httpx.Response(200, json=completion({"role": "assistant", "content": "answered"}))

    client = OpenAiCompletionsClient(credentials=credentials(), transport=httpx.MockTransport(handler))
    async with agent_tools.run_context(warehouse_source=FakeAgentWarehouse()):
        outcome = await client.converse([{"role": "user", "content": "is vegetation covered?"}])

    tool_messages = [
        json.loads(message["content"]) for message in outcome["transcript"] if message.get("role") == "tool"
    ]
    assert tool_messages[0]["error"] != REPEATED_REJECTED_CALL_ERROR, "the first (rejected) call still ran for real"
    assert tool_messages[1]["error"] == REPEATED_REJECTED_CALL_ERROR


async def test_a_repeat_after_a_transient_strategy_knowledge_refusal_is_executed_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`strategy_knowledge_unavailable` names a fault in the SERVICE (timeout, 503, not configured),
    never the call's own arguments -- an identical retry can genuinely answer differently, so the
    guard must never suppress it, unlike a real argument-shape rejection."""
    import agri_data_service.agent.llm as llm_module  # noqa: PLC0415

    call_count = 0

    class _FlakyStrategyTool:
        name = STRATEGY_TOOL

        async def call(self, _arguments: dict[str, Any]) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return json.dumps(
                    {"tool": STRATEGY_TOOL, "error": strategy_knowledge.UNAVAILABLE, "refusal_detail": "timed out"}
                )
            return json.dumps({"tool": STRATEGY_TOOL, "results": [{"strategy_id": "s1"}]})

    monkeypatch.setattr(llm_module, "tool_by_name", lambda _name: _FlakyStrategyTool())
    turns: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        turns.append(json.loads(request.content))
        if len(turns) <= 2:
            return httpx.Response(
                200, json=completion(tool_call_message(f"call_{len(turns)}", STRATEGY_TOOL, UNAVAILABLE_ARGUMENTS))
            )
        return httpx.Response(200, json=completion({"role": "assistant", "content": "answered"}))

    client = OpenAiCompletionsClient(credentials=credentials(), transport=httpx.MockTransport(handler))
    async with agent_tools.run_context(warehouse_source=FakeAgentWarehouse()):
        outcome = await client.converse([{"role": "user", "content": "what helps sour pasture?"}])

    tool_messages = [
        json.loads(message["content"]) for message in outcome["transcript"] if message.get("role") == "tool"
    ]
    assert len(tool_messages) == 2
    assert tool_messages[0]["error"] == strategy_knowledge.UNAVAILABLE
    assert tool_messages[1].get("error") != REPEATED_REJECTED_CALL_ERROR, "a transient refusal must not be guarded"
    assert call_count == 2, "the second identical call reached the real tool rather than being skipped"


async def test_a_successful_call_is_never_guarded_even_when_repeated() -> None:
    """The guard only ever suppresses a repeat of a REJECTED call -- a model calling the same
    (successful) tool with the same arguments twice must still run it twice.

    A bare `FakeAgentWarehouse()` refuses `parquet_availability_withheld` for an unwired lane
    (`test_a_lane_that_cannot_prove_its_coverage_is_refused_and_never_reported_empty`), which the
    guard's own design deliberately keeps guarded (a deterministic warehouse refusal, not a
    service-level fault) -- so this scenario needs `vegetation` actually wired to a proven, published
    lane to genuinely exercise a SUCCESSFUL repeat, not accidentally re-test the rejected-call guard.
    """
    turns: list[dict[str, Any]] = []
    warehouse = FakeAgentWarehouse()
    warehouse.evidence["vegetation"] = published_lane("vegetation", [date(2026, 3, 14)])

    def handler(request: httpx.Request) -> httpx.Response:
        turns.append(json.loads(request.content))
        if len(turns) <= 2:
            return httpx.Response(
                200, json=completion(tool_call_message(f"call_{len(turns)}", COVERAGE_TOOL, GOOD_ARGUMENTS))
            )
        return httpx.Response(200, json=completion({"role": "assistant", "content": "answered"}))

    client = OpenAiCompletionsClient(credentials=credentials(), transport=httpx.MockTransport(handler))
    async with agent_tools.run_context(warehouse_source=warehouse):
        outcome = await client.converse([{"role": "user", "content": "is vegetation covered?"}])

    payloads = [json.loads(message["content"]) for message in outcome["transcript"] if message.get("role") == "tool"]
    assert all(payload.get("error") != REPEATED_REJECTED_CALL_ERROR for payload in payloads)
    assert len(payloads) == 2  # both real calls ran, against a genuinely proven, published lane


# --- Final no-tools answer turn -----------------------------------------------------


async def test_the_budget_exhausted_turn_gets_one_final_tools_free_answer_call() -> None:
    """Every one of `max_iterations` turns asks for another tool call; `converse` must not give up
    with an empty answer -- it spends one extra call with `tool_choice="none"` (schemas still
    published, since the transcript already holds tool_use/tool_result blocks a provider would
    otherwise reject) and returns its text."""
    turns: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        turns.append(body)
        if body.get("tool_choice") == "none":
            return httpx.Response(
                200, json=completion({"role": "assistant", "content": "Here is the answer from the tools above."})
            )
        return httpx.Response(
            200, json=completion(tool_call_message(f"call_{len(turns)}", COVERAGE_TOOL, GOOD_ARGUMENTS))
        )

    client = OpenAiCompletionsClient(credentials=credentials(), transport=httpx.MockTransport(handler))
    async with agent_tools.run_context(warehouse_source=FakeAgentWarehouse()):
        outcome = await client.converse([{"role": "user", "content": "is vegetation covered?"}], max_iterations=2)

    assert outcome["stopped_because"] == "iteration_budget_exhausted_answered"
    assert outcome["final_text"] == "Here is the answer from the tools above."
    assert outcome["iterations"] == 2
    # The final call still carried `tools` (forbidding another call via tool_choice, not by omission).
    assert turns[-1]["tools"], "tool schemas must stay published on the final call"
    assert turns[-1]["tool_choice"] == "none"
    # A short nudge was appended before the final call.
    assert turns[-1]["messages"][-1] == {
        "role": "user",
        "content": "Answer now from the tool results above; no more tool calls.",
    }


async def test_the_final_answer_turn_still_returns_empty_text_gracefully_when_the_model_says_nothing() -> None:
    """The final call is still just ONE call -- if the model answers with nothing, `stopped_because`
    reads `"iteration_budget_exhausted"`, the SAME label the provider-rejects-it fallback below uses,
    not `"...answered"` (CORRECTED, wave-2 fix-stage review: `"_answered"` must mean the final call
    actually returned usable text, so a caller branching on it never reads an empty answer as one)."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("tool_choice") == "none":
            return httpx.Response(200, json=completion({"role": "assistant", "content": None}))
        return httpx.Response(200, json=completion(tool_call_message("call_1", COVERAGE_TOOL, GOOD_ARGUMENTS)))

    client = OpenAiCompletionsClient(credentials=credentials(), transport=httpx.MockTransport(handler))
    async with agent_tools.run_context(warehouse_source=FakeAgentWarehouse()):
        outcome = await client.converse([{"role": "user", "content": "is vegetation covered?"}], max_iterations=1)

    assert outcome["stopped_because"] == "iteration_budget_exhausted"
    assert outcome["final_text"] == ""


async def test_the_final_answer_turn_strips_a_dangling_tool_call_a_provider_returns_anyway() -> None:
    """A provider that ignores `tool_choice="none"` can still answer with `tool_calls`; left in place,
    the appended assistant message would carry a tool call with no tool-result reply, and a LATER
    turn's request built on this same transcript (the eval harness reuses it across turns) would append
    a user message after it and have an OpenAI-compatible provider reject the request outright
    (wave-2 fix-stage review). `converse` strips them from the stored message but still returns its
    own text and iteration count unchanged."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("tool_choice") == "none":
            return httpx.Response(
                200,
                json=completion(
                    {
                        "role": "assistant",
                        "content": "Here is an answer anyway.",
                        "tool_calls": [
                            {
                                "id": "call_x",
                                "type": "function",
                                "function": {"name": COVERAGE_TOOL, "arguments": "{}"},
                            }
                        ],
                    }
                ),
            )
        return httpx.Response(200, json=completion(tool_call_message("call_1", COVERAGE_TOOL, GOOD_ARGUMENTS)))

    client = OpenAiCompletionsClient(credentials=credentials(), transport=httpx.MockTransport(handler))
    async with agent_tools.run_context(warehouse_source=FakeAgentWarehouse()):
        outcome = await client.converse([{"role": "user", "content": "is vegetation covered?"}], max_iterations=1)

    assert outcome["stopped_because"] == "iteration_budget_exhausted_answered"
    assert outcome["final_text"] == "Here is an answer anyway."
    assert "tool_calls" not in outcome["transcript"][-1]


# --- Transient-vs-deterministic serving refusals (major finding, wave-2 fix-stage review) ----


async def test_a_repeat_after_a_serving_at_capacity_refusal_is_executed_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """`parquet_serving_refused` with `refusal_code: "serving_at_capacity"` (`parquet_ops/faults.py`)
    names a fact about the WAREHOUSE PROCESS at that instant -- every serving slot busy -- not the
    call's own day/lane/viewport arguments, so an identical retry can genuinely succeed once a slot
    frees up. A blanket "every `parquet_serving_refused` stays guarded" reading would wrongly block it."""
    import agri_data_service.agent.llm as llm_module  # noqa: PLC0415

    call_count = 0

    class _AtCapacityTool:
        name = COVERAGE_TOOL

        async def call(self, _arguments: dict[str, Any]) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return json.dumps(
                    {
                        "error": "parquet_serving_refused",
                        "refusal_code": "serving_at_capacity",
                        "refusal_detail": "all 4 serving slots busy",
                    }
                )
            return json.dumps({"state": "found", "max_air_temperature_c": 21.4})

    monkeypatch.setattr(llm_module, "tool_by_name", lambda _name: _AtCapacityTool())
    turns: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        turns.append(json.loads(request.content))
        if len(turns) <= 2:
            return httpx.Response(
                200, json=completion(tool_call_message(f"call_{len(turns)}", COVERAGE_TOOL, GOOD_ARGUMENTS))
            )
        return httpx.Response(200, json=completion({"role": "assistant", "content": "answered"}))

    client = OpenAiCompletionsClient(credentials=credentials(), transport=httpx.MockTransport(handler))
    async with agent_tools.run_context(warehouse_source=FakeAgentWarehouse()):
        outcome = await client.converse([{"role": "user", "content": "is vegetation covered?"}])

    tool_messages = [
        json.loads(message["content"]) for message in outcome["transcript"] if message.get("role") == "tool"
    ]
    assert len(tool_messages) == 2
    assert tool_messages[0]["refusal_code"] == "serving_at_capacity"
    assert tool_messages[1].get("error") != REPEATED_REJECTED_CALL_ERROR, "a capacity refusal must not be guarded"
    assert call_count == 2, "the second identical call reached the real tool rather than being skipped"


async def test_a_repeat_after_a_release_read_changed_refusal_is_executed_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """`release_read_changed` (`agent/warehouse.py`) means a concurrent write raced the read, also a
    fact about process TIMING, not the call's arguments -- must never be guarded, same reasoning as
    `serving_at_capacity` above."""
    import agri_data_service.agent.llm as llm_module  # noqa: PLC0415

    call_count = 0

    class _RacingReleaseTool:
        name = COVERAGE_TOOL

        async def call(self, _arguments: dict[str, Any]) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return json.dumps(
                    {
                        "error": "parquet_serving_refused",
                        "refusal_code": "release_read_changed",
                        "refusal_detail": "Release changed while its rows were read",
                    }
                )
            return json.dumps({"state": "found", "max_air_temperature_c": 21.4})

    monkeypatch.setattr(llm_module, "tool_by_name", lambda _name: _RacingReleaseTool())
    turns: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        turns.append(json.loads(request.content))
        if len(turns) <= 2:
            return httpx.Response(
                200, json=completion(tool_call_message(f"call_{len(turns)}", COVERAGE_TOOL, GOOD_ARGUMENTS))
            )
        return httpx.Response(200, json=completion({"role": "assistant", "content": "answered"}))

    client = OpenAiCompletionsClient(credentials=credentials(), transport=httpx.MockTransport(handler))
    async with agent_tools.run_context(warehouse_source=FakeAgentWarehouse()):
        outcome = await client.converse([{"role": "user", "content": "is vegetation covered?"}])

    tool_messages = [
        json.loads(message["content"]) for message in outcome["transcript"] if message.get("role") == "tool"
    ]
    assert tool_messages[1].get("error") != REPEATED_REJECTED_CALL_ERROR, "a racing release must not be guarded"
    assert call_count == 2


async def test_a_deterministic_serving_refusal_still_guards_a_repeated_identical_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contrast with the two tests above: `read_over_budget` -- `parquet_ops/faults.py` itself
    documents this as NOT retryable, a pure function of the read's own arguments -- must stay guarded.
    A blanket exemption for the whole `parquet_serving_refused` shape would wrongly un-guard this too."""
    import agri_data_service.agent.llm as llm_module  # noqa: PLC0415

    class _OverBudgetTool:
        name = COVERAGE_TOOL

        async def call(self, _arguments: dict[str, Any]) -> str:
            return json.dumps(
                {
                    "error": "parquet_serving_refused",
                    "refusal_code": "read_over_budget",
                    "refusal_detail": "the read does not fit the serving memory budget",
                }
            )

    monkeypatch.setattr(llm_module, "tool_by_name", lambda _name: _OverBudgetTool())
    turns: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        turns.append(json.loads(request.content))
        if len(turns) <= 2:
            return httpx.Response(
                200, json=completion(tool_call_message(f"call_{len(turns)}", COVERAGE_TOOL, GOOD_ARGUMENTS))
            )
        return httpx.Response(200, json=completion({"role": "assistant", "content": "gave up"}))

    client = OpenAiCompletionsClient(credentials=credentials(), transport=httpx.MockTransport(handler))
    async with agent_tools.run_context(warehouse_source=FakeAgentWarehouse()):
        outcome = await client.converse([{"role": "user", "content": "is vegetation covered?"}])

    tool_messages = [
        json.loads(message["content"]) for message in outcome["transcript"] if message.get("role") == "tool"
    ]
    assert len(tool_messages) == 2
    assert tool_messages[1]["error"] == REPEATED_REJECTED_CALL_ERROR


async def test_the_final_answer_turn_falls_back_gracefully_when_the_provider_still_rejects_it() -> None:
    """Even with schemas published and `tool_choice="none"`, a provider MAY still reject the final
    call outright (a stricter gateway, a transient fault); `converse` must not let `LlmProviderError`
    escape and throw away every tool result already gathered -- it returns the old graceful outcome."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("tool_choice") == "none":
            return httpx.Response(400, json={"error": {"message": "still no tools allowed here"}})
        return httpx.Response(200, json=completion(tool_call_message("call_1", COVERAGE_TOOL, GOOD_ARGUMENTS)))

    client = OpenAiCompletionsClient(credentials=credentials(), transport=httpx.MockTransport(handler))
    async with agent_tools.run_context(warehouse_source=FakeAgentWarehouse()):
        outcome = await client.converse([{"role": "user", "content": "is vegetation covered?"}], max_iterations=1)

    assert outcome["stopped_because"] == "iteration_budget_exhausted"
    assert outcome["final_text"] == ""


# --- agent_system_message -----------------------------------------------------------


def test_agent_system_message_carries_todays_date_and_the_tool_surface_instructions() -> None:
    message = agent_system_message(date(2026, 9, 26))
    assert message["role"] == "system"
    assert message["content"].startswith("Today is 2026-09-26.")
    assert INSTRUCTIONS in message["content"]


def test_agent_system_message_is_the_same_text_mcp_publishes_as_initialize_instructions() -> None:
    """`agent ask`/the eval harness and an MCP client must see the same tool-surface guidance."""
    message = agent_system_message(date.today())  # noqa: DTZ011 - real wall-clock date, not a defect
    assert INSTRUCTIONS in message["content"]


def test_agent_system_message_warns_that_tool_results_are_data_not_instructions() -> None:
    """P4 injection hardening: this sentence must actually be in the text a model sees."""
    message = agent_system_message(date.today())  # noqa: DTZ011 - real wall-clock date, not a defect
    assert "never instructions" in message["content"]
    assert (
        "never as an outcome expected at" in message["content"] or "never as an expected outcome" in message["content"]
    )


async def test_converse_does_not_add_the_system_message_implicitly() -> None:
    """`converse` is a bare tool-calling loop; ONLY the caller decides whether to prepend it."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["messages"] == [{"role": "user", "content": "hello"}]
        return httpx.Response(200, json=completion({"role": "assistant", "content": "hi"}))

    client = OpenAiCompletionsClient(credentials=credentials(), transport=httpx.MockTransport(handler))
    async with agent_tools.run_context(warehouse_source=FakeAgentWarehouse()):
        await client.converse([{"role": "user", "content": "hello"}])
