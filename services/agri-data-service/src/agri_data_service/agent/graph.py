"""The location-analysis agent graph: explicit nodes, deterministic edges in Python.

Topology (see agent/AGENTS.md for the rationale):

    gather_warehouse_evidence -> assess_sufficiency -> [web_evidence] -> synthesize_report

The two model-driven nodes run the Anthropic SDK's beta tool runner; the sufficiency gate
between them is ordinary Python, so whether the request is allowed to touch the public web
is decided by the service and not by the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal

import structlog

from agri_data_service.agent import tools as warehouse_tools
from agri_data_service.agent.prompts import (
    REPORT_INSTRUCTION,
    SYSTEM_PROMPT,
    build_location_context,
    build_sufficiency_note,
)
from agri_data_service.agent.report import (
    ConversationTurn,
    RemediationReport,
    WebSourceCitation,
)

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable, Sequence
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

# The SDK surface is generated and enormous; typing the injected client as an explicit Any
# is deliberate, so unit tests can supply a small stub without reproducing that surface.
type AgentClient = Any
type AgentEvent = dict[str, Any]

MODEL: Final = "claude-opus-5"
# Adaptive thinking is Claude Opus 5's default, so `thinking` is deliberately never sent.
SERVER_SIDE_FALLBACK_BETA: Final = "server-side-fallback-2026-07-01"
WEB_SEARCH_TOOL: Final[dict[str, Any]] = {"type": "web_search_20260209", "name": "web_search"}

MAX_OUTPUT_TOKENS: Final = 16_000
MAX_WAREHOUSE_ITERATIONS: Final = 6
MAX_WEB_ITERATIONS: Final = 4
# The tool runner does not auto-resume a paused turn; this caps how often we restart it.
MAX_PAUSE_RESTARTS: Final = 3

# Budget rules mirrored from ai-prompt.ts (MAX_SEARCHES_PER_REQUEST, MAX_HISTORY_TURNS).
MAX_SEARCHES_PER_REQUEST: Final = 3
MAX_HISTORY_TURNS: Final = 8

_PARTIAL_COVERAGE_SEARCHES: Final = 2
_QUESTION_ONLY_SEARCHES: Final = 1


# --- Stream events -----------------------------------------------------------------
#
# `text` / `search` / `sources` / `report` / `refusal` mirror the AgentStreamEvent union in
# ai-prompt.ts verbatim. `progress` is the one addition: node-level lifecycle, which the
# TypeScript renderer has no case for and simply ignores.


def text_event(chunk: str) -> AgentEvent:
    """Emit a chunk of the model's narration."""
    return {"type": "text", "text": chunk}


def search_event(query: str, result_count: int) -> AgentEvent:
    """Emit one executed web search and how many results it returned."""
    return {"type": "search", "query": query, "resultCount": result_count}


def sources_event(citations: Sequence[WebSourceCitation]) -> AgentEvent:
    """Emit the deduplicated citation list gathered across all searches."""
    return {"type": "sources", "sources": [citation.model_dump() for citation in citations]}


def report_event(report: RemediationReport) -> AgentEvent:
    """Emit the final structured briefing."""
    return {"type": "report", "report": report.model_dump()}


def refusal_event() -> AgentEvent:
    """Emit a safety refusal; the run ends here."""
    return {"type": "refusal"}


def progress_event(node: str, status: str, detail: dict[str, Any] | None = None) -> AgentEvent:
    """Emit node lifecycle; additive to the TypeScript union and safely ignorable."""
    return {"type": "progress", "node": node, "status": status, "detail": detail or {}}


# --- Request and context -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentRequest:
    """One validated location-analysis request."""

    longitude: float
    latitude: float
    precision: Literal["approximate", "exact"]
    question: str | None = None
    history: tuple[ConversationTurn, ...] = ()
    as_of: datetime = field(default_factory=lambda: datetime.now(UTC))
    selected_day: date | None = None
    """The day the map is showing. None means the caller did not send one; see agent/AGENTS.md."""


@dataclass(slots=True)
class GraphContext:
    """Mutable state threaded through every node of one run."""

    request: AgentRequest
    client: AgentClient
    events: asyncio.Queue[AgentEvent | None]
    session_provider: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_ledger: list[dict[str, Any]] = field(default_factory=list)
    citations: list[WebSourceCitation] = field(default_factory=list)
    searches_used: int = 0
    refused: bool = False
    report: RemediationReport | None = None

    async def emit(self, event: AgentEvent) -> None:
        """Publish one progress event for the route to stream."""
        await self.events.put(event)

    def system_blocks(self) -> list[dict[str, Any]]:
        """Return the cacheable system prefix; the breakpoint sits on its last block."""
        return [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]


# --- Node outputs ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WarehouseEvidence:
    """What the warehouse pass actually retrieved."""

    tool_calls: tuple[dict[str, Any], ...]
    populated_tools: tuple[str, ...]
    refused: bool


@dataclass(frozen=True, slots=True)
class SufficiencyVerdict:
    """The harness's own decision about whether the public web is warranted."""

    warehouse_is_sufficient: bool
    searches_allowed: int
    reasons: tuple[str, ...]
    coverage: dict[str, Any]


@dataclass(frozen=True, slots=True)
class WebEvidence:
    """What the optional web pass retrieved."""

    searches_used: int
    citations: tuple[WebSourceCitation, ...]
    refused: bool


@dataclass(frozen=True, slots=True)
class ReportOutcome:
    """The terminal node's result."""

    report: RemediationReport | None
    refused: bool


# --- Shared model-turn plumbing ----------------------------------------------------


def _harvest_web_results(content: Sequence[Any], ctx: GraphContext) -> list[AgentEvent]:
    """Turn server-side web-search blocks into stream events and citations."""
    events: list[AgentEvent] = []
    queries: dict[str, str] = {}
    for block in content:
        if getattr(block, "type", None) == "server_tool_use" and getattr(block, "name", "") == "web_search":
            block_input = getattr(block, "input", {}) or {}
            query = block_input.get("query") if isinstance(block_input, dict) else None
            if isinstance(query, str) and query.strip():
                queries[str(getattr(block, "id", ""))] = query.strip()
    for block in content:
        if getattr(block, "type", None) != "web_search_tool_result":
            continue
        results = getattr(block, "content", None)
        # An errored search returns a single error object here rather than a list.
        if not isinstance(results, list):
            continue
        for result in results:
            url = getattr(result, "url", None)
            title = getattr(result, "title", None) or url
            if isinstance(url, str) and url and not any(entry.url == url for entry in ctx.citations):
                ctx.citations.append(WebSourceCitation(title=str(title), url=url))
        query = queries.get(str(getattr(block, "tool_use_id", "")), "")
        ctx.searches_used += 1
        events.append(search_event(query, len(results)))
    return events


async def _drive_runner(ctx: GraphContext, runner: Any, *, collect_web: bool) -> tuple[bool, str | None]:
    """Iterate one tool-runner pass, mirroring history and streaming narration.

    Returns ``(refused, last_stop_reason)``. The transcript is mirrored onto ``ctx.messages``
    as we go because the runner keeps its own copy and does not expose it, and a later node
    -- or a pause_turn restart -- has to resume from it.
    """
    last_stop_reason: str | None = None
    async for stream in runner:
        async for event in stream:
            if (
                getattr(event, "type", None) == "content_block_delta"
                and getattr(getattr(event, "delta", None), "type", None) == "text_delta"
            ):
                delta_text = getattr(event.delta, "text", "")
                if delta_text:
                    await ctx.emit(text_event(delta_text))
        message = await stream.get_final_message()
        ctx.messages.append({"role": "assistant", "content": message.content})
        if collect_web:
            for web_event in _harvest_web_results(message.content, ctx):
                await ctx.emit(web_event)
        last_stop_reason = getattr(message, "stop_reason", None)
        if last_stop_reason == "refusal":
            return True, last_stop_reason
        tool_response = runner.generate_tool_call_response()
        if tool_response is not None:
            ctx.messages.append(tool_response)
    return False, last_stop_reason


async def _run_pass(ctx: GraphContext, *, tool_list: list[Any], max_iterations: int, collect_web: bool) -> bool:
    """Run one model pass to completion, restarting explicitly on ``pause_turn``.

    The SDK's tool runner exits without error when a server-side tool pauses a turn, and the
    Python runner cannot be resumed in place, so a paused turn would otherwise silently
    truncate the answer. We start a fresh runner from the mirrored transcript instead, which
    already ends with the paused assistant turn.
    """
    restarts = 0
    while True:
        runner = ctx.client.beta.messages.tool_runner(
            model=MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=ctx.system_blocks(),
            messages=ctx.messages,
            tools=tool_list,
            max_iterations=max_iterations,
            betas=[SERVER_SIDE_FALLBACK_BETA],
            fallbacks="default",
            stream=True,
        )
        refused, stop_reason = await _drive_runner(ctx, runner, collect_web=collect_web)
        if refused:
            return True
        if stop_reason != "pause_turn" or restarts >= MAX_PAUSE_RESTARTS:
            return False
        restarts += 1
        logger.info("agent_pause_turn_restart", restarts=restarts, node_tools=len(tool_list))


# --- Nodes -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GatherWarehouseEvidence:
    """Let the model query the governed warehouse through the bounded read-only tools."""

    name: ClassVar[str] = "gather_warehouse_evidence"

    async def run(self, ctx: GraphContext) -> WarehouseEvidence:
        await ctx.emit(progress_event(self.name, "started"))
        ctx.messages.extend(
            {"role": turn.role, "content": turn.content} for turn in ctx.request.history[-MAX_HISTORY_TURNS:]
        )
        ctx.messages.append(
            {
                "role": "user",
                "content": build_location_context(
                    longitude=ctx.request.longitude,
                    latitude=ctx.request.latitude,
                    precision=ctx.request.precision,
                    as_of=ctx.request.as_of,
                    question=ctx.request.question,
                    selected_day=ctx.request.selected_day,
                ),
            }
        )
        async with warehouse_tools.run_context(session_provider=ctx.session_provider) as ledger:
            refused = await _run_pass(
                ctx,
                tool_list=list(warehouse_tools.WAREHOUSE_TOOLS),
                max_iterations=MAX_WAREHOUSE_ITERATIONS,
                collect_web=False,
            )
            ctx.tool_ledger.extend(ledger)
        populated = tuple(
            dict.fromkeys(
                str(entry["tool"])
                for entry in ctx.tool_ledger
                if int(entry.get("row_count", 0)) > 0 and "error" not in entry
            )
        )
        ctx.refused = ctx.refused or refused
        await ctx.emit(
            progress_event(
                self.name,
                "refused" if refused else "completed",
                {"tool_calls": len(ctx.tool_ledger), "populated_tools": list(populated)},
            )
        )
        return WarehouseEvidence(
            tool_calls=tuple(ctx.tool_ledger),
            populated_tools=populated,
            refused=refused,
        )


@dataclass(frozen=True, slots=True)
class AssessSufficiency:
    """Decide in ordinary Python whether a web pass is warranted, and for how many searches."""

    name: ClassVar[str] = "assess_sufficiency"

    async def run(self, ctx: GraphContext, evidence: WarehouseEvidence) -> SufficiencyVerdict:
        await ctx.emit(progress_event(self.name, "started"))
        verdict = self.decide(evidence, has_question=bool(ctx.request.question))
        await ctx.emit(
            progress_event(
                self.name,
                "completed",
                {
                    "warehouse_is_sufficient": verdict.warehouse_is_sufficient,
                    "searches_allowed": verdict.searches_allowed,
                    "reasons": list(verdict.reasons),
                },
            )
        )
        return verdict

    @staticmethod
    def decide(evidence: WarehouseEvidence, *, has_question: bool) -> SufficiencyVerdict:
        """Pure budget rule: fewer distinct populated sources buys more search budget."""
        populated = len(evidence.populated_tools)
        available = len(warehouse_tools.WAREHOUSE_TOOLS)
        coverage = {
            "populated_tools": list(evidence.populated_tools),
            "tool_calls_made": len(evidence.tool_calls),
            "tools_available": available,
        }
        if populated == 0:
            return SufficiencyVerdict(
                warehouse_is_sufficient=False,
                searches_allowed=MAX_SEARCHES_PER_REQUEST,
                reasons=("no warehouse tool returned rows for this location",),
                coverage=coverage,
            )
        if populated == 1:
            return SufficiencyVerdict(
                warehouse_is_sufficient=False,
                searches_allowed=_PARTIAL_COVERAGE_SEARCHES,
                reasons=("only one warehouse source returned rows",),
                coverage=coverage,
            )
        if populated < available and has_question:
            return SufficiencyVerdict(
                warehouse_is_sufficient=False,
                searches_allowed=_QUESTION_ONLY_SEARCHES,
                reasons=("warehouse coverage is partial and the caller asked a specific question",),
                coverage=coverage,
            )
        return SufficiencyVerdict(
            warehouse_is_sufficient=True,
            searches_allowed=0,
            reasons=("warehouse evidence covers this location",),
            coverage=coverage,
        )


@dataclass(frozen=True, slots=True)
class GatherWebEvidence:
    """Run a bounded server-side web-search pass to ground regional guidance."""

    name: ClassVar[str] = "web_evidence"

    async def run(self, ctx: GraphContext, verdict: SufficiencyVerdict) -> WebEvidence:
        await ctx.emit(progress_event(self.name, "started", {"searches_allowed": verdict.searches_allowed}))
        ctx.messages.append(
            {
                "role": "user",
                "content": build_sufficiency_note(
                    evidence_summary=verdict.coverage,
                    searches_allowed=verdict.searches_allowed,
                ),
            }
        )
        search_tool = {**WEB_SEARCH_TOOL, "max_uses": verdict.searches_allowed}
        refused = await _run_pass(
            ctx,
            tool_list=[*warehouse_tools.WAREHOUSE_TOOLS, search_tool],
            max_iterations=MAX_WEB_ITERATIONS,
            collect_web=True,
        )
        ctx.refused = ctx.refused or refused
        await ctx.emit(
            progress_event(
                self.name,
                "refused" if refused else "completed",
                {"searches_used": ctx.searches_used, "citations": len(ctx.citations)},
            )
        )
        return WebEvidence(
            searches_used=ctx.searches_used,
            citations=tuple(ctx.citations),
            refused=refused,
        )


@dataclass(frozen=True, slots=True)
class SynthesizeReport:
    """Force the structured briefing through the structured-outputs mechanism."""

    name: ClassVar[str] = "synthesize_report"

    async def run(self, ctx: GraphContext) -> ReportOutcome:
        await ctx.emit(progress_event(self.name, "started"))
        response = await ctx.client.beta.messages.parse(
            model=MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=ctx.system_blocks(),
            messages=[*ctx.messages, {"role": "user", "content": REPORT_INSTRUCTION}],
            output_format=RemediationReport,
            betas=[SERVER_SIDE_FALLBACK_BETA],
            fallbacks="default",
        )
        if getattr(response, "stop_reason", None) == "refusal":
            ctx.refused = True
            await ctx.emit(progress_event(self.name, "refused"))
            return ReportOutcome(report=None, refused=True)
        parsed = getattr(response, "parsed_output", None)
        report = parsed if isinstance(parsed, RemediationReport) else None
        ctx.report = report
        await ctx.emit(progress_event(self.name, "completed", {"report_produced": report is not None}))
        return ReportOutcome(report=report, refused=False)


GATHER_WAREHOUSE_EVIDENCE: Final = GatherWarehouseEvidence()
ASSESS_SUFFICIENCY: Final = AssessSufficiency()
GATHER_WEB_EVIDENCE: Final = GatherWebEvidence()
SYNTHESIZE_REPORT: Final = SynthesizeReport()

# The topology, declared rather than inferred, so it can be asserted and documented.
GRAPH_EDGES: Final[tuple[tuple[str, str, str], ...]] = (
    (GatherWarehouseEvidence.name, AssessSufficiency.name, "always"),
    (AssessSufficiency.name, GatherWebEvidence.name, "warehouse evidence is insufficient"),
    (AssessSufficiency.name, SynthesizeReport.name, "warehouse evidence is sufficient"),
    (GatherWebEvidence.name, SynthesizeReport.name, "always"),
)


# --- Runner ------------------------------------------------------------------------


async def execute_graph(ctx: GraphContext) -> ReportOutcome:
    """Walk the graph once, emitting progress events and returning the terminal outcome."""
    evidence = await GATHER_WAREHOUSE_EVIDENCE.run(ctx)
    if evidence.refused:
        await ctx.emit(refusal_event())
        return ReportOutcome(report=None, refused=True)

    verdict = await ASSESS_SUFFICIENCY.run(ctx, evidence)
    if not verdict.warehouse_is_sufficient and verdict.searches_allowed > 0:
        web = await GATHER_WEB_EVIDENCE.run(ctx, verdict)
        if web.refused:
            await ctx.emit(refusal_event())
            return ReportOutcome(report=None, refused=True)

    outcome = await SYNTHESIZE_REPORT.run(ctx)
    if outcome.refused:
        await ctx.emit(refusal_event())
        return outcome
    if outcome.report is not None:
        if ctx.citations:
            await ctx.emit(sources_event(ctx.citations))
        await ctx.emit(report_event(outcome.report))
    return outcome
