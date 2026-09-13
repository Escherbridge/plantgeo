"""Provider-independent, bounded environmental tools for the authenticated Next.js agent."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sanic import Blueprint, Request
from sanic import json as json_response
from sanic.response import HTTPResponse  # noqa: TC002 - Sanic evaluates annotations at runtime.

from agri_data_service.agent.llm import tool_by_name, tool_schemas
from agri_data_service.agent.surfaces import AGENT_SURFACE_NAMES, FEATURE_SURFACE_NAMES
from agri_data_service.agent.tools import run_context

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


def environmental_tool_schemas() -> list[dict[str, Any]]:
    """Keep canonical species authoring behind its caller-bound route."""
    return [schema for schema in tool_schemas() if schema["function"]["name"] != "species_information"]


@agent_tools_bp.get("/")
async def list_agent_tools(_request: Request) -> HTTPResponse:
    """Expose the shared registry without constructing a model client or reading a lane."""
    return json_response(
        {
            "tools": environmental_tool_schemas(),
            "surfaces": list(AGENT_SURFACE_NAMES),
            "feature_surfaces": list(FEATURE_SURFACE_NAMES),
            "value_surfaces": [surface for surface in AGENT_SURFACE_NAMES if surface != "drought-areas"],
        },
        headers=_HEADERS,
    )


def _refusal(name: str, code: str, status: int) -> HTTPResponse:
    return json_response({"tool": name, "error": code, "code": code}, status=status, headers=_HEADERS)


@agent_tools_bp.post("/call")
async def call_agent_tool(request: Request) -> HTTPResponse:
    """Execute one schema-validated tool inside the existing Parquet admission boundary."""
    if len(request.body) > MAX_REQUEST_BYTES:
        return _refusal("", "tool_request_too_large", _BAD_REQUEST)
    try:
        payload = AgentToolCallRequest.model_validate(request.json or {})
    except (ValidationError, ValueError):
        return _refusal("", "invalid_tool_request", _BAD_REQUEST)
    names = {schema["function"]["name"] for schema in environmental_tool_schemas()}
    if payload.name not in names:
        return _refusal(payload.name, "unknown_environmental_tool", _BAD_REQUEST)
    try:
        async with asyncio.timeout(TOOL_TIMEOUT_SECONDS), run_context():
            result = await tool_by_name(payload.name).call(payload.arguments)
        content = json.loads(result) if isinstance(result, str) else result
        encoded = json.dumps(content, default=str, allow_nan=False)
    except (TimeoutError, TypeError, ValueError) as error:
        timed_out = isinstance(error, TimeoutError)
        return _refusal(
            payload.name,
            "tool_read_timeout" if timed_out else "invalid_tool_arguments",
            _UNAVAILABLE if timed_out else _BAD_REQUEST,
        )
    if len(encoded.encode("utf-8")) > MAX_RESPONSE_BYTES:
        return _refusal(payload.name, "tool_response_too_large", _UNAVAILABLE)
    return json_response({"tool": payload.name, "result": content}, headers=_HEADERS)
