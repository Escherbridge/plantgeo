"""Provider-independent, bounded environmental tools for the authenticated Next.js agent."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sanic import Blueprint, Request
from sanic import json as json_response
from sanic.response import HTTPResponse

from agri_data_service.agent.llm import argument_error_detail, tool_by_name, tool_schemas
from agri_data_service.agent.strategy_knowledge import LITERATURE_TOOL_NAMES, ServerContext, bound_strategy_context
from agri_data_service.agent.surfaces import AGENT_SURFACE_NAMES, FEATURE_SURFACE_NAMES
from agri_data_service.agent.tools import WAREHOUSE_TOOLS, run_context

agent_tools_bp = Blueprint("agent_tools", url_prefix="/agent-tools")

MAX_REQUEST_BYTES: Final = 32 * 1024
MAX_RESPONSE_BYTES: Final = 2 * 1024 * 1024
TOOL_TIMEOUT_SECONDS: Final = 12
_BAD_REQUEST: Final = 400
_UNAVAILABLE: Final = 503
_HEADERS: Final = {"Cache-Control": "no-store"}


class AgentToolCallRequest(BaseModel):
    """One registered environmental tool call; neither SQL nor storage paths are accepted."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any]
    server_context: ServerContext | None = None
    """Seam S1 (+ soil C3 delta: `site_facts_provenance`, `site_brief_query`); literature tools only.

    See agent/AGENTS.md, "Server-owned site facts".
    """


def environmental_tool_schemas() -> list[dict[str, Any]]:
    """Keep canonical species authoring behind its caller-bound route.

    `tool_schemas()` is already flag-gated (`tools.published_warehouse_tools`), so
    `soil_properties_at_point` drops out of this list on its own with
    SOIL_PROPERTIES_READS_ENABLED unset -- this is the catalogue Gemini's function-declaration
    complexity is measured against.
    """
    return [schema for schema in tool_schemas() if schema["function"]["name"] != "species_information"]


def _callable_tool_names() -> set[str]:
    """Every tool name `/call` will accept, independent of catalogue publication.

    Deliberately WIDER than `environmental_tool_schemas()`: with SOIL_PROPERTIES_READS_ENABLED
    unset, `soil_properties_at_point` is unpublished but still a name this service has registered,
    so a call for it reaches its own typed `reads_disabled` refusal (agent/soil_properties.py)
    rather than the generic `unknown_environmental_tool` a name nobody registered gets. That keeps
    this route's contract the one `test_the_soil_tool_is_bridge_callable_and_off_by_default` pins.
    """
    return {tool.name for tool in WAREHOUSE_TOOLS} - {"species_information"}


@agent_tools_bp.get("/")
async def list_agent_tools(_request: Request) -> HTTPResponse:
    """Expose the shared registry without constructing a model client or reading a lane."""
    return json_response(
        {
            "tools": environmental_tool_schemas(),
            "surfaces": list(AGENT_SURFACE_NAMES),
            "feature_surfaces": list(FEATURE_SURFACE_NAMES),
            "value_surfaces": list(AGENT_SURFACE_NAMES),
        },
        headers=_HEADERS,
    )


def _only_server_context_failed(error: ValidationError) -> bool:
    """Whether every issue sits under `server_context`, so the rest of the request was well formed."""
    issues = error.errors(include_url=False, include_context=False, include_input=False)
    return bool(issues) and all(issue["loc"][:1] == ("server_context",) for issue in issues)


def _refusal(name: str, code: str, status: int, detail: str | None = None) -> HTTPResponse:
    body = {"tool": name, "error": code, "code": code}
    if detail:
        body["detail"] = detail
    return json_response(body, status=status, headers=_HEADERS)


def _parse_call(request: Request, names: set[str]) -> AgentToolCallRequest | HTTPResponse:
    """The validated call, or the 400 refusal that says why there is none."""
    if len(request.body) > MAX_REQUEST_BYTES:
        return _refusal("", "tool_request_too_large", _BAD_REQUEST)
    try:
        raw = request.json or {}
    except ValueError:
        return _refusal("", "invalid_tool_request", _BAD_REQUEST)
    if isinstance(raw, dict) and "server_context" in raw:
        # Seam S1: server_context is honoured for the three literature tools only, and the contract
        # says it is IGNORED, not an error, on every other call -- so a malformed one is dropped
        # before validation rather than merely unread after a 400 the caller never asked for.
        # `name` is checked for `str` BEFORE the `in` test: `LITERATURE_TOOL_NAMES` is a frozenset,
        # and membership hashes its argument, so an unhashable `name` (a list, say) would otherwise
        # raise TypeError here instead of reaching pydantic's ordinary "name must be a string" 400
        # (wave-2 fix-stage review).
        name = raw.get("name")
        if not isinstance(name, str) or name not in LITERATURE_TOOL_NAMES:
            raw = {key: value for key, value in raw.items() if key != "server_context"}
    try:
        payload = AgentToolCallRequest.model_validate(raw)
    except ValidationError as error:
        if not _only_server_context_failed(error):
            return _refusal("", "invalid_tool_request", _BAD_REQUEST)
        # Value-free, bounded field detail (`include_input=False`); see agent/AGENTS.md.
        name = raw.get("name") if isinstance(raw, dict) else None
        return _refusal(
            name if isinstance(name, str) and name in names else "",
            "invalid_tool_arguments",
            _BAD_REQUEST,
            argument_error_detail(error),
        )
    if payload.name not in names:
        return _refusal(payload.name, "unknown_environmental_tool", _BAD_REQUEST)
    return payload


@agent_tools_bp.post("/call")
async def call_agent_tool(request: Request) -> HTTPResponse:
    """Execute one schema-validated tool inside the existing Parquet admission boundary."""
    payload = _parse_call(request, _callable_tool_names())
    if isinstance(payload, HTTPResponse):
        return payload
    strategy_context = (
        payload.server_context.strategy_context()
        if payload.server_context is not None and payload.name in LITERATURE_TOOL_NAMES
        else None
    )
    try:
        async with asyncio.timeout(TOOL_TIMEOUT_SECONDS), run_context():
            with bound_strategy_context(strategy_context):
                result = await tool_by_name(payload.name).call(payload.arguments)
        content = json.loads(result) if isinstance(result, str) else result
        encoded = json.dumps(content, default=str, allow_nan=False)
    except (TimeoutError, TypeError, ValueError) as error:
        timed_out = isinstance(error, TimeoutError)
        return _refusal(
            payload.name,
            "tool_read_timeout" if timed_out else "invalid_tool_arguments",
            _UNAVAILABLE if timed_out else _BAD_REQUEST,
            # Which argument broke which rule, bounded and value-free; see agent/AGENTS.md.
            None if timed_out else argument_error_detail(error),
        )
    if len(encoded.encode("utf-8")) > MAX_RESPONSE_BYTES:
        return _refusal(payload.name, "tool_response_too_large", _UNAVAILABLE)
    return json_response({"tool": payload.name, "result": content}, headers=_HEADERS)
