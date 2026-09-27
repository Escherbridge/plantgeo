"""The HTTP transport: `/health`, `/ready`, `POST /v1/tools/{tool_name}` and MCP streamable HTTP at `/mcp`.

One Starlette app over the same tool table and knowledge-base slot the stdio server uses. JSON access-log lines
go to stdout and a `limit_concurrency` cap fails excess load fast; see AGENTS.md sections "HTTP transport",
"Observability" and "Deploy (Railway)".
"""

import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from functools import partial
from typing import Any, Final

import anyio.to_thread
import uvicorn
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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
#: Echoed on every response; taken from the caller when present (AGENTS.md "Observability").
REQUEST_ID_HEADER: Final = "X-Request-ID"
#: A caller-sent request id must look like this or it is replaced with a generated one (AGENTS.md "Observability"):
#: an unbounded or odd-charset header would otherwise land in every log line for the request's lifetime.
REQUEST_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def log_access_event(event: str, /, **fields: Any) -> None:
    """One compact JSON line to real stdout: HTTP-mode observability never carries query or passage text."""
    print(json.dumps({"event": event, **fields}, separators=(",", ":")))


class AccessLogMiddleware:
    """Assigns/echoes `X-Request-ID` and logs one `mcp_request` line per `/mcp` call (AGENTS.md "Observability").

    A raw ASGI middleware, not `BaseHTTPMiddleware`: the mounted MCP app's response is read straight through to
    `send`, so nothing here buffers or reshapes it. The id is exposed to route handlers via `scope["state"]`,
    which `Request.state` reads (Starlette's own mechanism for passing per-request context without middleware
    subclassing).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        caller_request_id = Headers(scope=scope).get(REQUEST_ID_HEADER)
        request_id = (
            caller_request_id
            if caller_request_id and REQUEST_ID_PATTERN.fullmatch(caller_request_id)
            else uuid.uuid4().hex
        )
        scope.setdefault("state", {})["request_id"] = request_id
        is_mcp_request = scope["path"].startswith(MCP_PATH)
        started = time.perf_counter()
        status: dict[str, int] = {}

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)
            await send(message)

        await self.app(scope, receive, send_with_request_id)
        if is_mcp_request:
            log_access_event(
                "mcp_request",
                status=status.get("code"),
                ms=round((time.perf_counter() - started) * 1000, 1),
                request_id=request_id,
            )


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
        """One MCP tool over plain HTTP: the JSON body is its arguments, the response its exact MCP JSON text.

        Logs exactly one `tool_call` line to stdout per call (AGENTS.md "Observability"): never the arguments or
        the answer, only the outcome, timing, served corpus_version and request id. The log happens in a
        `finally` so a body read that raises before any `finish()` call (a client disconnecting mid-stream) is
        still counted, as `aborted`, instead of leaving the call with no record at all.
        """
        started = time.perf_counter()
        name = request.path_params["tool_name"]
        request_id = getattr(request.state, "request_id", None)
        logged_status: str | None = None

        def finish(response: Response, status: str) -> Response:
            nonlocal logged_status
            logged_status = status
            return response

        def log(status: str) -> None:
            knowledge_base = service.slot.value
            log_access_event(
                "tool_call",
                tool=name,
                status=status,
                ms=round((time.perf_counter() - started) * 1000, 1),
                corpus_version=knowledge_base.index_corpus_version if knowledge_base is not None else None,
                request_id=request_id,
            )

        try:
            body = await read_capped_body(request)
            if body is None:
                oversized = _error(413, error="request_too_large", limit_bytes=MAXIMUM_REQUEST_BYTES)
                return finish(oversized, "request_too_large")
            if name not in service.tools:
                return finish(_error(404, error="unknown_tool", tools=service.tools.names), "unknown_tool")
            if service.slot.value is None:
                return finish(_error(503, error="not_ready"), "not_ready")
            try:
                arguments = parse_arguments(body)
                text = await anyio.to_thread.run_sync(partial(_call_and_render, service, name, arguments))
            except ToolArgumentsError as error:
                return finish(_error(400, error="invalid_arguments", detail=str(error)), "invalid_arguments")
            except KnowledgeBaseNotLoadedError:
                return finish(_error(503, error="not_ready"), "not_ready")
            except Exception:
                logger.exception("tool %s failed", name)
                return finish(_error(500, error="internal_error"), "internal_error")
            return finish(Response(text, media_type="application/json"), "ok")
        finally:
            log(logged_status if logged_status is not None else "aborted")

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
    middleware = [Middleware(AccessLogMiddleware)]
    return Starlette(routes=routes, middleware=middleware, lifespan=lifespan)


def _call_and_render(service: StrategyKnowledgeService, name: str, arguments: dict[str, Any]) -> str:
    """Run a tool on a worker thread (as the SDK does for sync tools) and render its MCP text."""
    return service.tools.render(name, service.tools.call(name, arguments))


def serve_http(settings: Settings, *, host: str, port: int, pull_from_bucket: bool = False) -> None:
    """Serve HTTP with uvicorn; a failed pull or a stale pulled index fails startup and exits 3, never serving.

    `limit_concurrency` (AGENTS.md "Observability") caps concurrent requests; uvicorn answers 503 to whatever
    arrives past the cap instead of queuing it behind a slow tool call.
    """
    # protect_stdout=False: HTTP never claims fd 1 as a wire, and `log_access_event` below is contracted to
    # write real stdout for the process's whole lifetime (AGENTS.md "Observability") -- see `server._lifespan`.
    service = build_service(
        lambda: load_serving_knowledge_base(settings, pull_from_bucket=pull_from_bucket), protect_stdout=False
    )
    app = build_http_app(service, extra_allowed_hosts=settings.allowed_hosts)
    uvicorn.run(
        app,
        host=host,
        port=port,
        lifespan="on",
        log_config=None,
        server_header=False,
        limit_concurrency=settings.limit_concurrency,
    )
