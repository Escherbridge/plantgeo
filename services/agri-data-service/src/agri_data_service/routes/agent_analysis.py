"""SSE endpoint for the location-analysis agent graph."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, Final, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sanic import Blueprint, Request
from sanic import json as json_response
from sanic.response import HTTPResponse  # noqa: TC002 - sanic-ext evaluates handler annotations at runtime.

from agri_data_service.agent.graph import (
    MAX_HISTORY_TURNS,
    AgentRequest,
    GraphContext,
    execute_graph,
)
from agri_data_service.agent.report import AI_GENERATED_DISCLAIMER, ConversationTurn
from agri_data_service.config import settings

logger = structlog.get_logger()

agent_bp = Blueprint("agent", url_prefix="/agent")

_HTTP_BAD_REQUEST: Final = 400
_HTTP_SERVICE_UNAVAILABLE: Final = 503
_MAX_QUESTION_LENGTH: Final = 2_000
_AGENT_DISABLED_MESSAGE: Final = "agent disabled: ANTHROPIC_API_KEY not configured"

_SSE_HEADERS: Final = {
    "Cache-Control": "no-store",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class AgentAnalyzeRequest(BaseModel):
    """Validated ingress payload for one location analysis."""

    model_config = ConfigDict(extra="forbid")

    longitude: float = Field(ge=-180.0, le=180.0)
    latitude: float = Field(ge=-90.0, le=90.0)
    precision: Literal["approximate", "exact"] = "approximate"
    question: str | None = Field(default=None, max_length=_MAX_QUESTION_LENGTH)
    history: list[ConversationTurn] = Field(default_factory=list, max_length=MAX_HISTORY_TURNS)


def _frame(event: dict[str, Any]) -> str:
    """Render one event as a standard SSE frame named by its own type."""
    return f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'), default=str)}\n\n"


def build_agent_client() -> Any:
    """Construct the Anthropic client from the configured key; import is deferred by design."""
    from anthropic import AsyncAnthropic  # noqa: PLC0415 - keep SDK import off the app-start path.

    key = settings.anthropic_api_key
    if key is None:
        raise ValueError(_AGENT_DISABLED_MESSAGE)
    return AsyncAnthropic(api_key=key.get_secret_value())


@agent_bp.post("/analyze")
async def analyze_location(request: Request) -> HTTPResponse | None:
    """Run the agent graph for one coordinate and stream its events back over SSE."""
    if settings.anthropic_api_key is None:
        return json_response(
            {"error": _AGENT_DISABLED_MESSAGE, "code": "agent_disabled"},
            status=_HTTP_SERVICE_UNAVAILABLE,
        )
    try:
        payload = AgentAnalyzeRequest.model_validate(request.json or {})
    except ValidationError as error:
        return json_response(
            {"error": "invalid request", "code": "invalid_request", "detail": error.errors(include_url=False)},
            status=_HTTP_BAD_REQUEST,
        )

    agent_request = AgentRequest(
        longitude=payload.longitude,
        latitude=payload.latitude,
        precision=payload.precision,
        question=payload.question,
        history=tuple(payload.history),
        as_of=datetime.now(UTC),
    )
    context = GraphContext(
        request=agent_request,
        client=build_agent_client(),
        events=asyncio.Queue(),
    )

    response = await request.respond(content_type="text/event-stream", headers=_SSE_HEADERS)
    await response.send(_frame({"type": "disclaimer", "disclaimer": AI_GENERATED_DISCLAIMER}))

    async def drive() -> None:
        """Walk the graph, then close the queue however it ends."""
        try:
            await execute_graph(context)
        except Exception as error:  # a failed run must close the stream, not hang it
            logger.warning("agent_run_failed", error=str(error), error_type=type(error).__name__)
            await context.events.put({"type": "error", "message": f"{type(error).__name__}: {error}"})
        finally:
            await context.events.put(None)

    task = asyncio.create_task(drive())
    try:
        while True:
            event = await context.events.get()
            if event is None:
                break
            await response.send(_frame(event))
    except asyncio.CancelledError:
        task.cancel()
        raise
    except Exception as error:  # a dropped client surfaces here as a failed write
        task.cancel()
        logger.info("agent_stream_closed", error=str(error))
    finally:
        await asyncio.gather(task, return_exceptions=True)
    return None
