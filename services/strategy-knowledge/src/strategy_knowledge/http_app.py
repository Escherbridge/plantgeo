"""The HTTP transport: `/health`, `/ready`, `POST /v1/tools/{tool_name}` and MCP streamable HTTP at `/mcp`.

One Starlette app over the same tool table and knowledge-base slot the stdio server uses. See AGENTS.md sections
"HTTP transport" and "Deploy (Railway)".
"""

import json
import logging
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from functools import partial
from typing import Any, Final

import anyio.to_thread
import uvicorn
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from strategy_knowledge.config import Settings
from strategy_knowledge.server import (
    KnowledgeBaseNotLoadedError,
    StrategyKnowledgeService,
    ToolArgumentsError,
    build_service,
    load_serving_knowledge_base,
)

logger = logging.getLogger(__name__)

#: Largest request body any route reads; tool arguments are a few hundred bytes (AGENTS.md "HTTP transport").
MAXIMUM_REQUEST_BYTES: Final = 32 * 1024
MCP_PATH: Final = "/mcp"
TOOL_PATH: Final = "/v1/tools/{tool_name}"
LOOPBACK_HOSTS: Final = ("127.0.0.1", "localhost", "[::1]")
#: Railway private-network name of this service (contract C2); `RAILWAY_PRIVATE_DOMAIN` is added at run time too.
RAILWAY_SERVICE_HOST: Final = "plantgeo-strategy-knowledge.railway.internal"


def transport_security(extra_hosts: Iterable[str] = ()) -> TransportSecuritySettings:
    """DNS-rebinding protection for `/mcp`: loopback, the Railway private host and `extra_hosts`, any port."""
    hosts = dict.fromkeys((*LOOPBACK_HOSTS, RAILWAY_SERVICE_HOST, *extra_hosts))
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[pattern for host in hosts for pattern in (host, f"{host}:*")],
        allowed_origins=[pattern for host in LOOPBACK_HOSTS for pattern in (f"http://{host}", f"http://{host}:*")],
    )


async def read_capped_body(request: Request, limit: int = MAXIMUM_REQUEST_BYTES) -> bytes | None:
    """The request body, or None as soon as it (declared or streamed) is larger than `limit` bytes."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return None
    body = bytearray()
    async for piece in request.stream():
        body.extend(piece)
        if len(body) > limit:
            return None
    return bytes(body)


def parse_arguments(body: bytes) -> dict[str, Any]:
    """A tool's arguments from a JSON-object body; an empty body means no arguments."""
    if not body.strip():
        return {}
    try:
        arguments = json.loads(body)
    except (ValueError, RecursionError) as error:
        raise ToolArgumentsError(f"the request body is not JSON: {error}") from error
    if not isinstance(arguments, dict):
        raise ToolArgumentsError("the request body must be a JSON object of the tool's arguments")
    return arguments


def _error(status_code: int, **payload: Any) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code)


def build_http_app(service: StrategyKnowledgeService, *, extra_allowed_hosts: Iterable[str] = ()) -> Starlette:
    """The Starlette app; its lifespan runs the MCP session manager, whose lifespan loads the knowledge base."""
    mcp_app = service.server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        stateless_http=True,
        max_request_body_size=MAXIMUM_REQUEST_BYTES,
        transport_security=transport_security(extra_allowed_hosts),
    )
    session_manager = service.server.session_manager

    async def health(_request: Request) -> Response:
        """Liveness: the process answers; never touches the knowledge base."""
        return JSONResponse({"status": "ok"})

    async def ready(_request: Request) -> Response:
        """Readiness: the knowledge base is loaded and its index is full and fresh."""
        knowledge_base = service.slot.value
        if knowledge_base is None:
            return _error(503, status="not_ready", reason="the knowledge base is not loaded yet")
        problems = knowledge_base.index_staleness()
        if problems:
            return _error(503, status="not_ready", reason="; ".join(problems))
        return JSONResponse(
            {"status": "ready", "corpus_version": knowledge_base.index_corpus_version, "index_is_stale": False},
        )

    async def call_tool(request: Request) -> Response:
        """One MCP tool over plain HTTP: the JSON body is its arguments, the response its exact MCP JSON text."""
        body = await read_capped_body(request)
        if body is None:
            return _error(413, error="request_too_large", limit_bytes=MAXIMUM_REQUEST_BYTES)
        name = request.path_params["tool_name"]
        if name not in service.tools:
            return _error(404, error="unknown_tool", tools=service.tools.names)
        if service.slot.value is None:
            return _error(503, error="not_ready")
        response: Response
        try:
            arguments = parse_arguments(body)
            text = await anyio.to_thread.run_sync(partial(_call_and_render, service, name, arguments))
        except ToolArgumentsError as error:
            response = _error(400, error="invalid_arguments", detail=str(error))
        except KnowledgeBaseNotLoadedError:
            response = _error(503, error="not_ready")
        except Exception:
            logger.exception("tool %s failed", name)
            response = _error(500, error="internal_error")
        else:
            response = Response(text, media_type="application/json")
        return response

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        # A mounted app's own lifespan never runs, so the session manager is entered here, exactly once.
        async with session_manager.run():
            yield

    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/ready", ready, methods=["GET"]),
        Route(TOOL_PATH, call_tool, methods=["POST"]),
        Mount("/", app=mcp_app),
    ]
    return Starlette(routes=routes, lifespan=lifespan)


def _call_and_render(service: StrategyKnowledgeService, name: str, arguments: dict[str, Any]) -> str:
    """Run a tool on a worker thread (as the SDK does for sync tools) and render its MCP text."""
    return service.tools.render(name, service.tools.call(name, arguments))


def serve_http(settings: Settings, *, host: str, port: int, pull_from_bucket: bool = False) -> None:
    """Serve HTTP with uvicorn; a failed pull or a stale pulled index fails startup and exits 3, never serving."""
    service = build_service(lambda: load_serving_knowledge_base(settings, pull_from_bucket=pull_from_bucket))
    app = build_http_app(service, extra_allowed_hosts=settings.allowed_hosts)
    uvicorn.run(app, host=host, port=port, lifespan="on", log_config=None, server_header=False)
