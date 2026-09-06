"""An OpenAI-completions chat client for the agent's tool surface, built on the httpx we already have.

This is the SECOND provider path in this package and it is not interchangeable with the first.
`agent/graph.py` drives the Anthropic beta tool runner and structured outputs; nothing here does.
An OpenAI chat-completions endpoint -- OpenRouter, LM Studio, vLLM -- implements neither, so this
module reimplements only the part the tools actually need: publish ten JSON Schemas, receive
`tool_calls`, execute them through the same `BetaAsyncFunctionTool.call` the graph uses, post the
results back. See `agent/AGENTS.md`, "The MCP tool surface", for why the two paths coexist.

No new dependency: `httpx` is already a direct dependency of this service, and adding `openai`
would mean a `uv sync` that this repo's toolchain notes forbid on an unrelated change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import httpx

from agri_data_service.agent.tools import WAREHOUSE_TOOLS
from agri_data_service.config import settings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.config import AgentLlmCredentials

#: How many assistant turns one bounded `converse` is allowed before it stops asking for tools.
#: Mirrors `graph.MAX_WAREHOUSE_ITERATIONS`, so the two provider paths cost the same worst case.
MAX_TOOL_ITERATIONS: Final = 6

#: A probe is an authentication check, not an answer; it buys the smallest completion a provider
#: will sell. Some gateways reject `max_tokens: 0`, so this is 1 rather than 0.
PROBE_MAX_TOKENS: Final = 1

#: Sent on EVERY turn, and the same 16k `graph.MAX_OUTPUT_TOKENS` the Anthropic path uses. Omitting
#: it is not "no limit", it is the model's own maximum: OpenRouter prices a request against that
#: ceiling up front and answered `HTTP 402: you requested up to 64000 tokens, but can only afford
#: 42853` on a question whose real answer was a few hundred. An unbounded ceiling on a loop that
#: runs up to MAX_TOOL_ITERATIONS times is a cost hazard as well as a refusal.
MAX_OUTPUT_TOKENS: Final = 16_000

_CHAT_COMPLETIONS_PATH: Final = "/chat/completions"


class LlmProviderError(RuntimeError):
    """A provider call that did not return a usable completion.

    Carries the status and the provider's own message and NOTHING ELSE. The credential lives only
    in the request headers `AgentLlmCredentials.authorization_headers` builds; it is never put into
    an exception, a log line, or a payload, because every one of those is somewhere a caller prints.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def tool_schemas() -> list[dict[str, Any]]:
    """Publish every warehouse tool in the OpenAI `tools` shape, derived from the tool objects.

    Derived and never hand-spelled: `WAREHOUSE_TOOLS` is the one registry, and a second list here
    would drift the moment a tool gained a parameter. The description is the tool's own docstring,
    which is where the four-state contract and the caps are already written down for a model.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": json.loads(json.dumps(tool.input_schema)),
            },
        }
        for tool in WAREHOUSE_TOOLS
    ]


def tool_by_name(name: str) -> Any:
    """Return the registered tool with this name, or raise naming what is actually available."""
    for tool in WAREHOUSE_TOOLS:
        if tool.name == name:
            return tool
    available = ", ".join(sorted(tool.name for tool in WAREHOUSE_TOOLS))
    raise KeyError(f"no warehouse tool named {name!r}; available: {available}")


async def execute_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    """Run one `tool_calls` entry and render the `role: "tool"` message that answers it.

    A refusal is NOT an error here. Every tool already converts a serving fault into its typed
    refusal payload (`tools._refuses_serving_faults`), so `lane_columns_absent` and
    `parquet_availability_withheld` arrive as ordinary tool content and reach the model as the
    designed four-state answer. Only a malformed call -- unknown tool, unparseable arguments,
    arguments the schema rejects -- becomes an error message, and it says which.
    """
    function = tool_call.get("function") or {}
    name = str(function.get("name") or "")
    raw_arguments = function.get("arguments") or "{}"
    try:
        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else dict(raw_arguments)
        if not isinstance(arguments, dict):
            raise TypeError("tool arguments must be a JSON object")
        content = await tool_by_name(name).call(arguments)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        content = json.dumps({"error": f"{type(error).__name__}: {error}", "tool": name})
    return {
        "role": "tool",
        "tool_call_id": str(tool_call.get("id") or ""),
        "name": name,
        "content": content if isinstance(content, str) else json.dumps(content, default=str),
    }


@dataclass(frozen=True, slots=True)
class OpenAiCompletionsClient:
    """One configured OpenAI-completions endpoint, addressed over httpx."""

    credentials: AgentLlmCredentials
    transport: httpx.AsyncBaseTransport | None = None
    """Injected only by the wiring smoke test; production leaves it None and httpx opens sockets."""

    @classmethod
    def from_settings(cls, transport: httpx.AsyncBaseTransport | None = None) -> OpenAiCompletionsClient:
        """Build the client from the process settings, refusing by variable name when unconfigured."""
        return cls(credentials=settings.require_agent_llm(), transport=transport)

    def describe(self) -> dict[str, Any]:
        """A printable description of where this client points. Never includes the credential."""
        return {
            "base_url": self.credentials.base_url,
            "model": self.credentials.model,
            "auth_header": self.credentials.auth_header,
            "timeout_seconds": self.credentials.timeout_seconds,
            "tools_published": len(WAREHOUSE_TOOLS),
        }

    async def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Post one chat completion and return the decoded body, raising a typed provider error."""
        body: dict[str, Any] = {"model": self.credentials.model, "messages": list(messages)}
        if tools:
            body["tools"] = list(tools)
            body["tool_choice"] = "auto"
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        headers = {"Content-Type": "application/json", **self.credentials.authorization_headers()}
        async with httpx.AsyncClient(
            base_url=self.credentials.base_url,
            timeout=self.credentials.timeout_seconds,
            transport=self.transport,
        ) as client:
            try:
                response = await client.post(_CHAT_COMPLETIONS_PATH, json=body, headers=headers)
            except httpx.HTTPError as error:
                raise LlmProviderError(f"{type(error).__name__}: {error}") from error
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise LlmProviderError(_provider_message(response), status_code=response.status_code)
        try:
            decoded = response.json()
        except ValueError as error:
            raise LlmProviderError("provider returned a non-JSON body", status_code=response.status_code) from error
        if not isinstance(decoded, dict):
            raise LlmProviderError("provider returned a JSON body that is not an object")
        return decoded

    async def probe(self) -> dict[str, Any]:
        """Authenticate against the provider with the smallest possible completion.

        This proves the credential, the base URL and the model name. It proves nothing about the
        warehouse, which is not reached, and nothing about tool execution, which is not requested.
        """
        completion = await self.chat(
            [{"role": "user", "content": "reply with the single word: ok"}],
            max_tokens=PROBE_MAX_TOKENS,
        )
        return {
            "authenticated": True,
            "model_reported": completion.get("model"),
            "usage": completion.get("usage"),
            **self.describe(),
        }

    async def converse(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        max_iterations: int = MAX_TOOL_ITERATIONS,
        max_tokens: int = MAX_OUTPUT_TOKENS,
    ) -> dict[str, Any]:
        """Run one bounded tool-calling conversation and return the transcript and the final text.

        The caller supplies the whole message list, tool schemas are published every turn, and the
        loop ends when the model stops asking for tools or the iteration budget runs out. There is
        no sufficiency gate and no web pass here on purpose: this is the tool-surface harness, not
        `agent/graph.py`, and it must not grow a second, quieter copy of that policy.
        """
        transcript = [dict(message) for message in messages]
        schemas = tool_schemas()
        ledger: list[dict[str, Any]] = []
        for iteration in range(max_iterations):
            completion = await self.chat(transcript, tools=schemas, max_tokens=max_tokens)
            message = _first_message(completion)
            transcript.append(message)
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return {
                    "final_text": message.get("content") or "",
                    "iterations": iteration + 1,
                    "tool_calls": ledger,
                    "transcript": transcript,
                    "stopped_because": "model_answered",
                }
            for tool_call in tool_calls:
                result = await execute_tool_call(tool_call)
                ledger.append({"tool": result["name"], "arguments": _arguments_of(tool_call)})
                transcript.append(result)
        return {
            "final_text": "",
            "iterations": max_iterations,
            "tool_calls": ledger,
            "transcript": transcript,
            "stopped_because": "iteration_budget_exhausted",
        }


def _arguments_of(tool_call: dict[str, Any]) -> Any:
    """The arguments one tool call carried, decoded when they parse and echoed raw when they do not."""
    raw = (tool_call.get("function") or {}).get("arguments")
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _first_message(completion: dict[str, Any]) -> dict[str, Any]:
    """The assistant message from a completion, refusing loudly on a body with no choices."""
    choices = completion.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LlmProviderError(f"provider returned no choices: {json.dumps(completion, default=str)[:400]}")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise LlmProviderError("provider returned a choice with no message")
    return dict(message)


def _provider_message(response: httpx.Response) -> str:
    """Pull the provider's own error text out of a failed response, falling back to the raw body."""
    try:
        decoded = response.json()
    except ValueError:
        return f"HTTP {response.status_code}: {response.text[:400]}"
    error = decoded.get("error") if isinstance(decoded, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return f"HTTP {response.status_code}: {error['message']}"
    if isinstance(error, str):
        return f"HTTP {response.status_code}: {error}"
    return f"HTTP {response.status_code}: {json.dumps(decoded, default=str)[:400]}"
