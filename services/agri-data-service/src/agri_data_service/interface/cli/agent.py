"""CLI adapters over the agent's MCP tool surface and its OpenAI-completions provider.

Four leaves, in increasing order of what they need to be running:

- `tools` needs nothing. It prints the published schemas, so a schema regression is visible offline.
- `probe` needs the provider. It authenticates and prints what answered; it never reads a lane.
- `mcp-serve` needs whatever the client asks for. It serves every tool over stdio.
- `ask` needs both the provider and the warehouse, and is the only leaf that can spend R2 reads.

See `agent/AGENTS.md`, "The MCP tool surface", for the split between this and `/agent/analyze`.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import click

from agri_data_service.agent import tools as warehouse_tools
from agri_data_service.agent.llm import MAX_OUTPUT_TOKENS, LlmProviderError, OpenAiCompletionsClient
from agri_data_service.agent.mcp_server import reserved_stdout, serve_stdio, tool_descriptors
from agri_data_service.interface.cli._registry import register_commands

if TYPE_CHECKING:
    from collections.abc import Coroutine


@click.group()
def agent() -> None:
    """Serve and exercise the location-analysis agent's bounded warehouse tools."""


@click.command()
@click.option("--name", default=None, help="Print one tool's schema instead of all ten.")
def list_tools(name: str | None) -> None:
    """Print the MCP tool descriptors this server publishes."""
    descriptors = [descriptor for descriptor in tool_descriptors() if name is None or descriptor["name"] == name]
    if name is not None and not descriptors:
        available = ", ".join(descriptor["name"] for descriptor in tool_descriptors())
        raise click.ClickException(f"no tool named {name!r}; available: {available}")
    _emit(json.dumps(descriptors, indent=2, sort_keys=True))


@click.command()
def probe() -> None:
    """Authenticate against the configured provider and print what answered.

    Proves the credential, the base URL and the model name. Proves nothing about the warehouse.
    """
    _emit(json.dumps(_run(_probe()), indent=2, sort_keys=True, default=str))


@click.command()
@click.option("--name", required=True, help="The tool to call, exactly as `list-tools` names it.")
@click.option("--arguments", default="{}", help="The tool's arguments as a JSON object.")
def call_tool(name: str, arguments: str) -> None:
    """Call one warehouse tool directly, with no model in the loop.

    The shortest path to seeing what an MCP client would receive, including a typed refusal.
    """
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as error:
        raise click.ClickException(f"--arguments must be a JSON object: {error}") from error
    if not isinstance(parsed, dict):
        raise click.ClickException("--arguments must be a JSON object")
    _emit(_run(_call(name, parsed)))


@click.command()
@click.option("--question", required=True, help="What to ask about the coordinate.")
@click.option("--longitude", required=True, type=float, help="WGS84 longitude in decimal degrees.")
@click.option("--latitude", required=True, type=float, help="WGS84 latitude in decimal degrees.")
@click.option(
    "--max-tokens",
    type=int,
    default=MAX_OUTPUT_TOKENS,
    show_default=True,
    help="Output ceiling per turn. A gateway prices the request against this, so lower it on a thin balance.",
)
def ask(question: str, longitude: float, latitude: float, max_tokens: int) -> None:
    """Run one bounded tool-calling conversation against the live provider and the live warehouse.

    This is the only leaf that spends object-store reads: the model's tool calls run for real.
    """
    outcome = _run(_ask(question, longitude, latitude, max_tokens))
    _emit(json.dumps(outcome, indent=2, sort_keys=True, default=str))


@click.command()
def mcp_serve() -> None:
    """Serve the warehouse tools over MCP stdio until stdin closes."""
    asyncio.run(serve_stdio())


async def _probe() -> dict[str, object]:
    return await OpenAiCompletionsClient.from_settings().probe()


async def _call(name: str, arguments: dict[str, object]) -> str:
    from agri_data_service.agent.llm import tool_by_name  # noqa: PLC0415 - keep the import beside its one use.

    async with warehouse_tools.run_context():
        try:
            return str(await tool_by_name(name).call(arguments))
        except KeyError as error:
            raise click.ClickException(str(error)) from error


async def _ask(question: str, longitude: float, latitude: float, max_tokens: int) -> dict[str, object]:
    client = OpenAiCompletionsClient.from_settings()
    prompt = (
        f"The coordinate is longitude {longitude}, latitude {latitude} (WGS84 decimal degrees). "
        f"Use the warehouse tools to answer, quote the distances they report, and treat any typed "
        f"refusal as a statement about the lane rather than as an absence of data.\n\n{question}"
    )
    async with warehouse_tools.run_context():
        return await client.converse([{"role": "user", "content": prompt}], max_tokens=max_tokens)


def _run[T](work: Coroutine[Any, Any, T]) -> T:
    """Drive one coroutine, turning a provider or configuration failure into a CLI error.

    Runs under `reserved_stdout` because a warehouse read logs through a bare
    `structlog.get_logger()`, which prints to stdout: without the guard, `availability_coverage.py`'s
    census-fallback warning arrives as line one of this command's JSON and every `| jq` fails. The
    result is BUFFERED and printed by `_emit` after the guard closes, so a caller redirecting stdout
    gets only the payload and every diagnostic goes to stderr.
    """
    try:
        with reserved_stdout():
            return asyncio.run(work)
    except LlmProviderError as error:
        raise click.ClickException(f"provider call failed: {error}") from error
    except ValueError as error:
        raise click.ClickException(str(error)) from error


def _emit(payload: str) -> None:
    """Print one command's machine-readable answer, and nothing else, on stdout."""
    click.echo(payload)


register_commands(
    agent,
    (
        ("list-tools", list_tools),
        ("probe", probe),
        ("call-tool", call_tool),
        ("ask", ask),
        ("mcp-serve", mcp_serve),
    ),
)
