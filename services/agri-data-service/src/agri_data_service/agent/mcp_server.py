"""An MCP stdio server publishing the agent's ten bounded warehouse tools.

The tools were always the reusable half of this package: `agent/graph.py` is one consumer of them
and an opinionated one, with a sufficiency gate and a web pass wired in. MCP is the second
consumer, and it deliberately carries none of that policy -- it lists the tools, calls one, and
returns exactly what the tool returned.

Hand-written JSON-RPC rather than the `mcp` package, for one reason: `mcp` is not a dependency of
this service and adding it means a `uv sync`, which this repo's toolchain notes forbid on an
unrelated change. The stdio transport is newline-delimited JSON-RPC 2.0 and the four methods below
are the whole of what a tools-only server owes; see `agent/AGENTS.md`, "The MCP tool surface".

STDOUT IS THE PROTOCOL. Every diagnostic goes to stderr, following the same
`structlog.PrintLogger(file=sys.stderr)` the CLI commands already use, because one stray print on
stdout desynchronises the client for the rest of the session.
"""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING, Any, Final

import structlog

from agri_data_service.agent import tools as warehouse_tools
from agri_data_service.agent.llm import tool_by_name, tool_schemas

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import TextIO

logger = structlog.wrap_logger(structlog.PrintLogger(file=sys.stderr))

SERVER_NAME: Final = "plantgeo-agri-warehouse"
SERVER_VERSION: Final = "0.1.0"

#: Revisions this server can speak. A client asking for one of these is answered in its own
#: revision; anything else is answered in the newest we know, which is what the spec asks for.
SUPPORTED_PROTOCOL_VERSIONS: Final = ("2024-11-05", "2025-03-26", "2025-06-18")
DEFAULT_PROTOCOL_VERSION: Final = SUPPORTED_PROTOCOL_VERSIONS[-1]

INSTRUCTIONS: Final = (
    "Bounded, read-only reads of PlantGeo's governed Parquet warehouse at a coordinate. Every tool "
    "caps its own radius, time window and row count and reports the applied bounds back. A tool "
    "that cannot honestly answer returns a typed refusal -- lane_columns_absent, "
    "parquet_availability_withheld, day_not_written -- rather than an empty result; read the "
    "refusal, do not treat it as 'no data here'."
)

_PARSE_ERROR: Final = -32700
_INVALID_REQUEST: Final = -32600
_METHOD_NOT_FOUND: Final = -32601
_INVALID_PARAMS: Final = -32602
_INTERNAL_ERROR: Final = -32603


class McpToolServer:
    """The method dispatcher, kept free of the transport so it can be driven directly by a test."""

    def __init__(self) -> None:
        self.protocol_version = DEFAULT_PROTOCOL_VERSION
        self.initialized = False

    async def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Answer one JSON-RPC message, or return None when it is a notification."""
        method = message.get("method")
        request_id = message.get("id")
        if not isinstance(method, str):
            return _error(request_id, _INVALID_REQUEST, "message carries no method")
        if request_id is None:
            # A notification. `notifications/initialized` is the only one that means anything here;
            # every other one is ignored on purpose, which is what the spec requires of a server.
            if method == "notifications/initialized":
                self.initialized = True
            return None
        try:
            return _result(request_id, await self._dispatch(method, message.get("params") or {}))
        except _MethodNotFoundError:
            return _error(request_id, _METHOD_NOT_FOUND, f"unknown method {method!r}")
        except _InvalidParamsError as error:
            return _error(request_id, _INVALID_PARAMS, str(error))
        except Exception as error:  # a failed method must answer, not kill the session
            logger.warning("mcp_method_failed", method=method, error=str(error), error_type=type(error).__name__)
            return _error(request_id, _INTERNAL_ERROR, f"{type(error).__name__}: {error}")

    async def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            return self._initialize(params)
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": list(tool_descriptors())}
        if method == "tools/call":
            return await self._call_tool(params)
        raise _MethodNotFoundError

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
            self.protocol_version = requested
        return {
            "protocolVersion": self.protocol_version,
            # `listChanged: false` is the honest answer: the tool registry is a module-level tuple
            # fixed at import, so this server will never send a tools/list_changed notification.
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS,
        }

    async def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        """Execute one tool and wrap its answer as MCP content.

        A typed refusal is a SUCCESSFUL call with refusal content, never `isError`. The refusal is
        the answer -- it says which lane withheld what and why -- and flagging it as a transport
        error would let a client render it as "the tool broke" and retry, which is exactly the
        fabricated-absence failure the four-state contract exists to prevent. Only a malformed
        call sets `isError`.
        """
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise _InvalidParamsError("tools/call requires a string `name`")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise _InvalidParamsError("tools/call `arguments` must be an object")
        try:
            tool = tool_by_name(name)
        except KeyError as error:
            raise _InvalidParamsError(str(error)) from error
        try:
            answer = await tool.call(arguments)
        except (TypeError, ValueError) as error:
            return _content(f"{type(error).__name__}: {error}", is_error=True)
        return _content(answer if isinstance(answer, str) else json.dumps(answer, default=str))


def tool_descriptors() -> Iterator[dict[str, Any]]:
    """Render every warehouse tool in the MCP `tools/list` shape, derived from the tool objects.

    Same source as `llm.tool_schemas`, different field names: MCP spells the schema `inputSchema`
    where OpenAI spells it `function.parameters`. Both are the tool's own `input_schema`, so a
    parameter added to a tool appears on both surfaces without either being edited.
    """
    for schema in tool_schemas():
        function = schema["function"]
        yield {
            "name": function["name"],
            "description": function["description"],
            "inputSchema": function["parameters"],
        }


class _MethodNotFoundError(Exception):
    """Raised by the dispatcher for a method this server does not implement."""


class _InvalidParamsError(Exception):
    """Raised for a well-formed request whose params this server cannot use."""


def _result(request_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _content(text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


@contextmanager
def reserved_stdout() -> Iterator[TextIO]:
    """Take exclusive ownership of the real stdout and point `sys.stdout` at stderr.

    NOT DEFENSIVE HOUSEKEEPING -- this was a measured corruption, twice. The service's warehouse
    modules log through a bare `structlog.get_logger()`, whose unconfigured default factory prints
    to stdout, and `availability_coverage.py`'s `availability_census_fallback` warning landed as
    line four of a live MCP session, between two protocol frames, and again as line one of
    `agri-service agent ask`'s JSON. Both consumers' parsers die on it. Rebinding `sys.stdout` for
    the duration means no logger, library or stray `print` anywhere beneath a tool call can reach
    the stream, which is a guarantee that chasing individual loggers cannot give.

    Used by both stdio surfaces: this server's transport, and the CLI leaves that print machine-
    readable JSON on stdout for a caller to pipe.

    The real stream is also forced to UTF-8: MCP is a UTF-8 protocol and a Windows console defaults
    to cp1252, which raises on the first non-Latin-1 character in a lane's data.
    """
    real_stdout = sys.stdout
    with suppress(AttributeError, OSError):  # a stream a test substituted; leave its encoding alone
        real_stdout.reconfigure(encoding="utf-8", newline="\n")  # type: ignore[union-attr]
    sys.stdout = sys.stderr
    try:
        yield real_stdout
    finally:
        sys.stdout = real_stdout


async def serve_stdio(server: McpToolServer | None = None) -> None:
    """Drive the newline-delimited JSON-RPC loop over stdin/stdout until stdin closes.

    stdin is read on a worker thread rather than through `asyncio.connect_read_pipe`, which is not
    supported for console handles on the Windows Proactor loop -- this service is developed on
    Windows and deployed on Linux, and one loop that works on both is worth the thread.

    The whole session runs inside one `tools.run_context`, so the tool ledger and the warehouse
    source are bound exactly as they are for a graph run. The source is left at its default, which
    is the real object store: an MCP client asking for a real answer must reach real data.
    """
    dispatcher = server or McpToolServer()
    with reserved_stdout() as stream:
        async with warehouse_tools.run_context() as ledger:
            logger.info("mcp_server_started", server=SERVER_NAME, tools=len(tool_schemas()))
            await _pump(dispatcher, stream)
            logger.info("mcp_server_stopped", tool_calls=len(ledger))


async def _pump(dispatcher: McpToolServer, stream: TextIO) -> None:
    """Read one message per line until stdin closes, answering each on the protocol stream."""
    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            return
        stripped = line.strip()
        if not stripped:
            continue
        try:
            message = json.loads(stripped)
        except json.JSONDecodeError as error:
            write_message(stream, _error(None, _PARSE_ERROR, f"invalid JSON: {error}"))
            continue
        if not isinstance(message, dict):
            write_message(stream, _error(None, _INVALID_REQUEST, "message must be a JSON object"))
            continue
        response = await dispatcher.handle(message)
        if response is not None:
            write_message(stream, response)


def write_message(stream: TextIO, message: dict[str, Any]) -> None:
    """Emit one protocol message as a single line, flushed. The only writer of the transport."""
    stream.write(json.dumps(message, separators=(",", ":"), default=str) + "\n")
    stream.flush()
