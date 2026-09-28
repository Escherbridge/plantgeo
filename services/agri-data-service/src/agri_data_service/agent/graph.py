"""The location-analysis agent graph: explicit nodes, deterministic edges in Python.

Topology (see agent/AGENTS.md for the rationale):

    build_site_brief -> gather_warehouse_evidence -> assess_sufficiency -> [web_evidence] -> synthesize_report

The two model-driven nodes run the Anthropic SDK's beta tool runner; the sufficiency gate
between them is ordinary Python, so whether the request is allowed to touch the public web
is decided by the service and not by the model.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal

import structlog
from pydantic import ValidationError

from agri_data_service.agent import soil_properties, strategy_knowledge, warehouse
from agri_data_service.agent import tools as warehouse_tools
from agri_data_service.agent.prompts import (
    REPORT_INSTRUCTION,
    build_location_context,
    build_site_brief_section,
    build_soil_estimate_section,
    build_sufficiency_note,
    system_prompt,
)
from agri_data_service.agent.report import (
    ConversationTurn,
    RemediationReport,
    WebSourceCitation,
    downgrade_literature_claims,
)
from agri_data_service.agent.selection_context import MapSelection, bind_selection_tools
from agri_data_service.agent.site_brief import (
    build_site_brief,
    build_soil_section,
    burn_severity_of,
    site_brief_enabled,
    site_facts_from_brief,
)
from agri_data_service.parquet_ops.faults import ServingRefusalError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
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


# The scoped species tool is available only during the warehouse pass.
def warehouse_tools_for_web() -> tuple[Any, ...]:
    """The web pass's tool set: currently published warehouse tools minus `species_information`.

    A FUNCTION, not a module-level constant: `warehouse_tools.published_warehouse_tools()` reads
    SOIL_PROPERTIES_READS_ENABLED fresh on every call, and caching this tuple at import would freeze
    `soil_properties_at_point`'s publication at whatever the flag happened to be when the process
    started.
    """
    return tuple(tool for tool in warehouse_tools.published_warehouse_tools() if tool.name != "species_information")


MAX_OUTPUT_TOKENS: Final = 16_000
MAX_WAREHOUSE_ITERATIONS: Final = 6
MAX_WEB_ITERATIONS: Final = 4
# The tool runner does not auto-resume a paused turn; this caps how often we restart it.
MAX_PAUSE_RESTARTS: Final = 3

# Budget rules mirrored from ai-prompt.ts (MAX_SEARCHES_PER_REQUEST, MAX_HISTORY_TURNS).
MAX_SEARCHES_PER_REQUEST: Final = 3
MAX_HISTORY_TURNS: Final = 8

# The strategy-knowledge (literature) tools: never coverage, and the only backing for a literature claim.
LITERATURE_TOOLS: Final = strategy_knowledge.LITERATURE_TOOL_NAMES

# Never a measured surface: catalogue, coverage and reference reads, including the literature tools.
_METADATA_TOOLS: Final = (
    frozenset(
        {
            "list_environmental_layers",
            "observation_coverage_on_day",
            "observation_temporal_neighbors",
            "botanical_occurrence_current_release",
            "species_information",
        }
    )
    | LITERATURE_TOOLS
)

#: Tools whose values are model estimates (SoilGrids), never a measured layer: they never raise sufficiency.
_MODEL_ESTIMATE_TOOLS: Final = frozenset({"soil_properties_at_point"})

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
    species_id: str | None = None
    """Canonical authoring UUID supplied by the caller; absent disables model botanical reads."""
    map_selection: MapSelection | None = None

    def active_selection(self) -> MapSelection:
        """Resolve a supplied selection or an explicitly single-day legacy request."""
        if self.map_selection is not None:
            return self.map_selection
        day = self.selected_day or self.as_of.date()
        return MapSelection(day=day, range_start=day, range_end=day)

    def is_base_run(self) -> bool:
        """The site brief is built on the first turn or when no question was typed (agent/AGENTS.md, "Site brief").

        Mirrors `regional-context.ts::assembleRegionalContext`'s `isBaseRun`. A follow-up does NOT carry
        the brief in its history (the saved turns are the questions and answers only), so `BuildSiteBrief`
        gives it the one soil read instead of the brief's five section reads.
        """
        return not self.history or not self.question

    def strategy_context(self) -> strategy_knowledge.StrategyContext:
        """Server-owned literature context: the user's own words and the map point, no site_facts.

        No measured read this graph runs yields a site_facts value it can trust; see agent/AGENTS.md,
        "Server-owned site facts".
        """
        user_turns = [turn.content for turn in self.history if turn.role == "user"]
        if self.question:
            user_turns.append(self.question)
        user_question = "\n".join(user_turns) or None
        try:
            return strategy_knowledge.StrategyContext(
                user_question=user_question, longitude=self.longitude, latitude=self.latitude, site_facts=None
            )
        except ValidationError:
            # The route already refuses an off-globe point; this only keeps a bad one out of the region lookup.
            return strategy_knowledge.StrategyContext(user_question=user_question)


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
    site_brief: dict[str, Any] | None = None
    """The server-built `site-brief/1`, set by `build_site_brief`; see agent/AGENTS.md, "Site brief"."""
    literature_context: strategy_knowledge.StrategyContext | None = None
    """The brief-enriched literature context; None falls back to the request's own."""
    site_soil: dict[str, Any] | None = None
    """A follow-up's one soil read (C5.3 soil section), set by `build_site_brief` instead of a brief."""

    def strategy_context(self) -> strategy_knowledge.StrategyContext:
        """The literature context both model passes bind: brief-enriched when the brief node ran."""
        return self.literature_context or self.request.strategy_context()

    async def emit(self, event: AgentEvent) -> None:
        """Publish one progress event for the route to stream."""
        await self.events.put(event)

    def system_blocks(self) -> list[dict[str, Any]]:
        """Return the cacheable system prefix; the breakpoint sits on its last block."""
        return [
            {
                "type": "text",
                "text": system_prompt(),
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


def populated_sources(ledger: Sequence[dict[str, Any]]) -> tuple[str, ...]:
    """Count independent measured layers, excluding catalogue and coverage-only metadata and model estimates."""
    return tuple(
        dict.fromkeys(
            str(entry.get("surface_name") or entry["tool"])
            for entry in ledger
            if entry["tool"] not in _METADATA_TOOLS
            and entry["tool"] not in _MODEL_ESTIMATE_TOOLS
            and int(entry.get("row_count", 0)) > 0
            and "error" not in entry
            and "refusal_code" not in entry
        )
    )


def literature_answered(ledger: Sequence[dict[str, Any]]) -> bool:
    """Whether any strategy-knowledge call answered AND found at least one record during this run.

    An 'answered' call that matched nothing (result_count 0) is a real, reachable answer -- the model
    still learns nothing matched -- but it backs no claim, so it must not unlock literature provenance
    the way a populated answer does. `result_count` rides every answered ledger entry
    (`strategy_knowledge.py::ask`): the two searches count their `results` list,
    `get_environmental_strategies` counts found `strategies`.
    """
    return any(
        entry["tool"] in LITERATURE_TOOLS and entry.get("state") == "answered" and int(entry.get("result_count", 0)) > 0
        for entry in ledger
    )


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


# --- Site brief readers ---------------------------------------------------------------
#
# Each section is read independently, bounded in time, and fails soft to a C5.6 reason: one section's
# failure never sinks the brief, and the brief never invents a value. Normalisation to integers happens
# here, outside the pure builder. See agent/AGENTS.md, "Site brief".

SITE_BRIEF_SECTION_TIMEOUT_SECONDS: Final = 3.0
#: At most this many brief reads hold a slot on the 3-slot serving plane at once (CONTRACT C5.1; review M8).
SITE_BRIEF_READ_CONCURRENCY: Final = 2
DROUGHT_WEEKS_BACK: Final = 2
FIRE_DETECTION_WINDOW_DAYS: Final = 30
FIRE_DETECTION_RADIUS_METERS: Final = 10_000.0
#: A perimeter must CONTAIN the point; the radius only bounds the candidate box.
FIRE_PERIMETER_RADIUS_METERS: Final = 100.0
FIRE_PERIMETER_ROWS: Final = 25
FIRE_PERIMETER_LANES: Final = ("fire-perimeters", "burn-severity")
FIRE_SOURCE_LABEL: Final = "fire-perimeters + burn-severity (MTBS)"
BRIEF_SECTIONS: Final = ("soil", "fire", "drought", "weather", "land_cover")
WEATHER_RADIUS_METERS: Final = 50_000.0
#: Today, then this many days back (UTC): the newest published day answers.
WEATHER_DAYS_BACK: Final = 1
WEATHER_ROWS: Final = 50
LAND_COVER_RADIUS_METERS: Final = 100.0
LAND_COVER_ROWS: Final = 4
_SERVING_REASONS: Final = {"serving_at_capacity": "serving_at_capacity", "read_timed_out": "timeout"}
_TOOL_ERROR_REASONS: Final = {
    "parquet_lane_never_written": "lane_never_written",
    "not_available_in_region": "not_bound_in_region",
}


#: The brief's read slots, set by `read_site_brief_inputs` for the section tasks it gathers.
_BRIEF_READ_SLOTS: contextvars.ContextVar[asyncio.Semaphore | None] = contextvars.ContextVar(
    "site_brief_read_slots", default=None
)


async def _slotted[T](read: Callable[[], Awaitable[T]]) -> T:
    """Run one serving-plane read inside the brief's slot cap; outside a brief it runs directly."""
    slots = _BRIEF_READ_SLOTS.get()
    if slots is None:
        return await read()
    async with slots:
        return await read()


class SectionUnavailableError(Exception):
    """A brief section that cannot be stated, carrying its C5.6 reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _unavailable(reason: str) -> dict[str, Any]:
    return {"state": "unavailable", "reason": reason}


def _section_reason(error: Exception) -> str:
    """Map a reader failure onto the one reason vocabulary."""
    if isinstance(error, SectionUnavailableError):
        return error.reason
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, ServingRefusalError):
        return _SERVING_REASONS.get(error.code, "read_failed")
    return "read_failed"


async def _bounded_section(name: str, read: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    """Run one section reader under its timeout; any failure becomes an unavailable section."""
    try:
        async with asyncio.timeout(SITE_BRIEF_SECTION_TIMEOUT_SECONDS):
            return await read()
    except Exception as error:  # per-section isolation: one reader's fault must not sink the brief
        reason = _section_reason(error)
        logger.info("site_brief_section_unavailable", section=name, reason=reason, error=type(error).__name__)
        return _unavailable(reason)


def _half_up(value: float) -> int:
    """Round a reader float half up to an integer (C5.1 normalisation)."""
    return math.floor(value + 0.5)


def _ascii(text: object) -> str:
    """Strip control and non-ASCII characters from a source string (C5.1)."""
    return "".join(character for character in str(text) if " " <= character <= "~").strip()


def _as_date(value: object) -> date | None:
    """A date from a date, an instant, or an ISO string; None otherwise."""
    if isinstance(value, datetime):
        return value.astimezone(UTC).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value[:10])
    return None


async def _lane_features(lane: str, request: AgentRequest, *, day: date, radius: float, rows: int) -> dict[str, Any]:
    """One lane's features near the point through the surface helper; a never-written lane refuses."""
    result = await _slotted(
        lambda: warehouse_tools._surface_lane_result(  # the tools' own bounded surface read
            lane,
            selected_day=day,
            longitude=request.longitude,
            latitude=request.latitude,
            radius_meters=radius,
            row_limit=rows,
        )
    )
    if result["day_state"].get("state") == "lane_never_written":
        raise SectionUnavailableError("lane_never_written")
    return result


async def read_drought_section(request: AgentRequest) -> dict[str, Any]:
    """The newest USDM release's class over the point, through the drought tool."""
    payload = json.loads(
        await _slotted(
            lambda: warehouse_tools.query_drought_history_at_point(
                request.longitude, request.latitude, weeks_back=DROUGHT_WEEKS_BACK, as_of=request.as_of
            )
        )
    )
    error = payload.get("error")
    if error == "parquet_serving_refused":
        raise SectionUnavailableError(_SERVING_REASONS.get(str(payload.get("refusal_code")), "read_failed"))
    if error is not None:
        raise SectionUnavailableError(_TOOL_ERROR_REASONS.get(str(error), "read_failed"))
    releases = payload.get("weekly_severity") or []
    if not releases:
        raise SectionUnavailableError("not_published")
    newest = releases[0]
    severity = newest.get("severity_class")
    return {
        "state": "available",
        "usdm_class": "none" if severity is None else f"D{int(severity)}",
        "week_of": str(newest["valid_date"])[:10],
    }


def _perimeter_fire(lane: str, feature: Mapping[str, Any]) -> tuple[date, str | None] | None:
    """(fire day, severity) of one perimeter that contains the point.

    Only an MTBS burn carries a severity, mapped by class code or name (CONTRACT C5.1); a WFIGS
    perimeter dates a fire but never grades it, as on the web.
    """
    if not feature.get("covers_probe_point"):
        return None
    properties = feature.get("properties") or {}
    if lane == "burn-severity":
        day = _as_date(properties.get("ignition_date"))
        severity = burn_severity_of(properties.get("severity_class"))
    else:
        day = _as_date(properties.get("fire_discovery_at")) or _as_date(properties.get("observed_day"))
        severity = None
    if day is None:
        return None
    return day, severity


async def _recent_detections(request: AgentRequest, today: date) -> int:
    """Satellite fire detections within the radius over the last 30 days, summed by cell."""
    first_day = today - timedelta(days=FIRE_DETECTION_WINDOW_DAYS - 1)
    window = await warehouse.lane_window(layer="fire-detections", first_day=first_day, last_day=today)
    if not window.lane_written:
        raise SectionUnavailableError("lane_never_written")
    days = window.published_days(first_day, today)
    if not days:
        return 0  # unpublished days add nothing, as on the web
    rows = await _slotted(
        lambda: warehouse_tools._lane_rows(  # the tools' own bounded proximity read
            "fire-detections",
            part_keys=window.part_keys(days),
            longitude=request.longitude,
            latitude=request.latitude,
            radius_meters=FIRE_DETECTION_RADIUS_METERS,
            row_limit=warehouse_tools.MAX_FIRE_FEATURE_FANOUT,
            operation="agent_site_brief_fire_detections",
            evidence_source=window.evidence_source,
        )
    )
    if len(rows) >= warehouse_tools.MAX_FIRE_FEATURE_FANOUT:
        # A capped read is an undercount, and an undercount is not a statement about the site.
        raise SectionUnavailableError("read_failed")
    return sum(int(row.get("detection_count") or 0) for row in rows)


async def read_fire_section(request: AgentRequest) -> dict[str, Any]:
    """The latest mapped fire containing the point and recent satellite detections nearby.

    The three reads run concurrently (review M8); any failure fails the section, never a zero. The
    severity is the same fire's MTBS class when exactly one is recorded for that day (CONTRACT C5.1).
    """
    today = request.as_of.astimezone(UTC).date()
    perimeter_lane, burn_lane = FIRE_PERIMETER_LANES
    perimeters, burns, detections = await asyncio.gather(
        _lane_features(
            perimeter_lane, request, day=today, radius=FIRE_PERIMETER_RADIUS_METERS, rows=FIRE_PERIMETER_ROWS
        ),
        _lane_features(burn_lane, request, day=today, radius=FIRE_PERIMETER_RADIUS_METERS, rows=FIRE_PERIMETER_ROWS),
        _recent_detections(request, today),
        return_exceptions=True,
    )
    # Every read settles before the section answers; the first failure, in lane order, names the reason.
    if isinstance(perimeters, BaseException):
        raise perimeters
    if isinstance(burns, BaseException):
        raise burns
    if isinstance(detections, BaseException):
        raise detections
    fires = [
        fire
        for lane, result in ((perimeter_lane, perimeters), (burn_lane, burns))
        for feature in result["features"]
        if (fire := _perimeter_fire(lane, feature)) is not None and fire[0] <= today
    ]
    latest_day = max((day for day, _severity in fires), default=None)
    severities = {severity for day, severity in fires if day == latest_day and severity is not None}
    return {
        "state": "available",
        "latest_fire_day": latest_day.isoformat() if latest_day else None,
        "burn_severity": next(iter(severities)) if len(severities) == 1 else None,
        "detections_last_30_days": detections,
        "source": FIRE_SOURCE_LABEL,
    }


async def read_weather_section(request: AgentRequest) -> dict[str, Any]:
    """The nearest weather-station reading on the newest written of today and yesterday."""
    today = request.as_of.astimezone(UTC).date()
    for day in (today - timedelta(days=back) for back in range(WEATHER_DAYS_BACK + 1)):
        result = await _lane_features(
            "weather-observations", request, day=day, radius=WEATHER_RADIUS_METERS, rows=WEATHER_ROWS
        )
        if result["day_state"].get("state") != "published":
            continue
        readings = [feature for feature in result["features"] if feature.get("distance_meters") is not None]
        if not readings:
            raise SectionUnavailableError("no_observation_within_radius")
        nearest = min(readings, key=_weather_rank)
        properties = nearest["properties"]
        observed_at = _as_instant(properties.get("observed_at"))
        temperature = properties.get("temperature_c")
        humidity = properties.get("relative_humidity_pct")
        if observed_at is None or temperature is None or humidity is None:
            # The brief never invents a value: a reading missing either number is unreadable (CONTRACT C5.1).
            raise SectionUnavailableError("read_failed")
        return {
            "state": "available",
            "observed_at": observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "distance_m": _half_up(float(nearest["distance_meters"])),
            "temperature_tenths_c": _half_up(float(temperature) * 10),
            "relative_humidity_pct": _half_up(float(humidity)),
        }
    raise SectionUnavailableError("not_published")


def _weather_rank(feature: Mapping[str, Any]) -> tuple[int, float]:
    """Nearest station first, then its newest reading."""
    instant = _as_instant((feature.get("properties") or {}).get("observed_at"))
    return _half_up(float(feature["distance_meters"])), -instant.timestamp() if instant else 0.0


def _as_instant(value: object) -> datetime | None:
    """A UTC instant from a datetime or an ISO string; None otherwise."""
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _class_code(raw: object) -> int | None:
    """A CDL class code key as an integer, or None for a key that is not one."""
    try:
        return int(str(raw))
    except ValueError:
        return None


def _dominant_class(properties: Mapping[str, Any]) -> tuple[int, str | None, float] | None:
    """(code, name or None, hectares) of the largest classified CDL class; ties to the lower code.

    Only integer codes with a finite positive area count, as on the web. A dominant class without a
    name has name None: the caller refuses it rather than seed literature with a bare number.
    """
    areas: dict[int, float] = {}
    for raw_code, raw_area in json.loads(properties["class_areas_json"]).items():
        code = _class_code(raw_code)
        area = float(raw_area) if isinstance(raw_area, int | float) and not isinstance(raw_area, bool) else math.nan
        if code is not None and math.isfinite(area) and area > 0:
            areas[code] = area
    names = {
        code: str(name)
        for raw_code, name in json.loads(properties.get("class_names_json") or "{}").items()
        if (code := _class_code(raw_code)) is not None and name is not None
    }
    if not areas:
        return None
    code = min(areas, key=lambda candidate: (-areas[candidate], candidate))
    return code, names.get(code), areas[code]


async def read_land_cover_section(request: AgentRequest) -> dict[str, Any]:
    """The dominant USDA CDL class of the crop-cover cell containing the point."""
    today = request.as_of.astimezone(UTC).date()
    result = await _lane_features(
        "crop-cover", request, day=today, radius=LAND_COVER_RADIUS_METERS, rows=LAND_COVER_ROWS
    )
    if result["day_state"].get("state") != "published":
        raise SectionUnavailableError("not_published")
    covering = [feature for feature in result["features"] if feature.get("covers_probe_point")]
    if not covering:
        raise SectionUnavailableError("outside_release_coverage")
    properties = covering[0]["properties"]
    dominant = _dominant_class(properties)
    cell_area = float(properties["cell_area_ha"])
    if dominant is None or cell_area <= 0:
        raise SectionUnavailableError("outside_release_coverage")
    code, name, hectares = dominant
    class_name = _ascii(name) if name is not None else ""
    if not class_name:
        raise SectionUnavailableError("read_failed")
    release_day = _as_date(properties.get("release_day"))
    return {
        "state": "available",
        "class_name": class_name,
        "class_code": code,
        "fraction_permille": _half_up(hectares / cell_area * 1000),
        "edition_year": int(properties["observed_year"]),
        "release_day": release_day.isoformat() if release_day else "",
        "cell_m": int(properties["aggregation_cell_m"]),
    }


async def read_soil_section(request: AgentRequest) -> dict[str, Any]:
    """The soil-properties lane read; it already fails soft and honours the kill switch."""
    return await _slotted(
        lambda: soil_properties.read_soil_properties(
            request.longitude, request.latitude, as_of=request.as_of.astimezone(UTC).date()
        )
    )


async def read_site_brief_inputs(request: AgentRequest) -> dict[str, Any]:
    """Read and normalise all five sections concurrently into `SiteBriefInputs` (C5.1)."""
    readers: dict[str, Callable[[AgentRequest], Awaitable[dict[str, Any]]]] = {
        "soil": read_soil_section,
        "fire": read_fire_section,
        "drought": read_drought_section,
        "weather": read_weather_section,
        "land_cover": read_land_cover_section,
    }
    # One slot cap per brief: the gathered section tasks copy this context, so they share it (review M8).
    token = _BRIEF_READ_SLOTS.set(asyncio.Semaphore(SITE_BRIEF_READ_CONCURRENCY))
    try:
        sections = await asyncio.gather(
            *(_bounded_section(name, functools.partial(reader, request)) for name, reader in readers.items())
        )
    finally:
        _BRIEF_READ_SLOTS.reset(token)
    return {
        "built_on": request.as_of.astimezone(UTC).date().isoformat(),
        "point": {
            "longitude_e5": _half_up(request.longitude * 100_000),
            "latitude_e5": _half_up(request.latitude * 100_000),
        },
        **dict(zip(readers, sections, strict=True)),
    }


def brief_literature_context(request: AgentRequest, brief: Mapping[str, Any]) -> strategy_knowledge.StrategyContext:
    """The request's literature context plus the brief's site facts, provenance and seed (C3)."""
    base = request.strategy_context()
    facts, provenance, query = site_facts_from_brief(brief)
    try:
        return strategy_knowledge.StrategyContext(
            user_question=base.user_question,
            longitude=base.longitude,
            latitude=base.latitude,
            site_facts=strategy_knowledge.SiteFacts.model_validate(facts) if facts else None,
            site_facts_provenance={
                key: strategy_knowledge.FactProvenance.model_validate(label) for key, label in provenance.items()
            },
            site_brief_query=query,
        )
    except ValidationError:
        logger.warning("site_brief_context_rejected", keys=sorted(facts))
        return base


# --- Nodes -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuildSiteBrief:
    """Read the site brief's sections before the model starts; see agent/AGENTS.md, "Site brief"."""

    name: ClassVar[str] = "build_site_brief"

    async def run(self, ctx: GraphContext) -> dict[str, Any] | None:
        if not site_brief_enabled():
            # Review M7: flag off is the wave-2 graph -- no read, no progress event, no context.
            return None
        await ctx.emit(progress_event(self.name, "started"))
        if not ctx.request.is_base_run():
            await self._follow_up(ctx)
            return None
        try:
            # Brief reads are server context, not model evidence: their ledger is discarded on purpose.
            async with warehouse_tools.run_context(session_provider=ctx.session_provider):
                inputs = await read_site_brief_inputs(ctx.request)
            brief = build_site_brief(inputs)
            literature_context = brief_literature_context(ctx.request, brief)
        except Exception as error:  # review m4: an unexpected shape fails open to no brief, never kills the graph
            logger.warning("site_brief_unbuildable", error=type(error).__name__)
            await ctx.emit(progress_event(self.name, "skipped", {"reason": "brief_unbuildable"}))
            return None
        ctx.site_brief = brief
        ctx.literature_context = literature_context
        states = {section: brief[section]["state"] for section in BRIEF_SECTIONS}
        await ctx.emit(
            progress_event(self.name, "completed", {"sections": states, "descriptors": len(brief["descriptors"])})
        )
        return brief

    async def _follow_up(self, ctx: GraphContext) -> None:
        """A follow-up's history holds only the saved turns, never the brief: the one soil read seeds it (review M6)."""
        async with warehouse_tools.run_context(session_provider=ctx.session_provider):
            soil = await _bounded_section("soil", functools.partial(read_soil_section, ctx.request))
        if soil.get("state") == "available":
            try:
                section = build_soil_section(soil)
                literature_context = brief_literature_context(ctx.request, {"soil": section})
            except Exception as error:  # review m4: fail open, as the base run does
                logger.warning("site_soil_unbuildable", error=type(error).__name__)
            else:
                ctx.site_soil = section
                ctx.literature_context = literature_context
        await ctx.emit(
            progress_event(self.name, "completed", {"follow_up": True, "sections": {"soil": soil.get("state")}})
        )


@dataclass(frozen=True, slots=True)
class GatherWarehouseEvidence:
    """Let the model query the governed warehouse through the bounded read-only tools."""

    name: ClassVar[str] = "gather_warehouse_evidence"

    async def run(self, ctx: GraphContext) -> WarehouseEvidence:
        await ctx.emit(progress_event(self.name, "started"))
        ctx.messages.extend(
            {"role": turn.role, "content": turn.content} for turn in ctx.request.history[-MAX_HISTORY_TURNS:]
        )
        location = build_location_context(
            longitude=ctx.request.longitude,
            latitude=ctx.request.latitude,
            precision=ctx.request.precision,
            as_of=ctx.request.as_of,
            question=ctx.request.question,
            selected_day=ctx.request.selected_day,
            species_id=ctx.request.species_id,
            map_selection=ctx.request.map_selection,
        )
        brief = build_site_brief_section(ctx.site_brief) if ctx.site_brief is not None else ""
        soil = build_soil_estimate_section(ctx.site_soil) if ctx.site_soil is not None else ""
        ctx.messages.append({"role": "user", "content": location + brief + soil})
        async with warehouse_tools.run_context(
            session_provider=ctx.session_provider,
            allowed_species_id=ctx.request.species_id or "",
            strategy_context=ctx.strategy_context(),
        ) as ledger:
            refused = await _run_pass(
                ctx,
                tool_list=bind_selection_tools(
                    warehouse_tools.published_warehouse_tools(),
                    longitude=ctx.request.longitude,
                    latitude=ctx.request.latitude,
                    selection=ctx.request.active_selection(),
                ),
                max_iterations=MAX_WAREHOUSE_ITERATIONS,
                collect_web=False,
            )
            ctx.tool_ledger.extend(ledger)
        populated = populated_sources(ctx.tool_ledger)
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
        available = sum(
            tool.name != "species_information" and tool.name not in LITERATURE_TOOLS
            for tool in warehouse_tools.published_warehouse_tools()
        )
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
        if has_question:
            return SufficiencyVerdict(
                warehouse_is_sufficient=False,
                searches_allowed=_QUESTION_ONLY_SEARCHES,
                reasons=("the specific question may require external regional guidance",),
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
        # The web pass deliberately omits species_information, but retains the caller-bound
        # context as a fail-closed backstop if a later tool-list change reintroduces it.
        async with warehouse_tools.run_context(
            session_provider=ctx.session_provider,
            allowed_species_id=ctx.request.species_id or "",
            strategy_context=ctx.strategy_context(),
        ) as ledger:
            refused = await _run_pass(
                ctx,
                tool_list=[
                    *bind_selection_tools(
                        warehouse_tools_for_web(),
                        longitude=ctx.request.longitude,
                        latitude=ctx.request.latitude,
                        selection=ctx.request.active_selection(),
                    ),
                    search_tool,
                ],
                max_iterations=MAX_WEB_ITERATIONS,
                collect_web=True,
            )
            # Sufficiency was already judged; kept so a literature call made here can back a claim.
            ctx.tool_ledger.extend(ledger)
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
        if report is not None and not literature_answered(ctx.tool_ledger):
            # Downgrade, not reject: see agent/AGENTS.md, "A literature claim needs a literature answer".
            report, downgraded = downgrade_literature_claims(report)
            if downgraded:
                await ctx.emit(
                    progress_event(
                        self.name,
                        "literature_downgraded",
                        {
                            "claims": list(downgraded),
                            "reason": "no strategy-knowledge call answered this run",
                            "relabelled_as": "model_inference",
                        },
                    )
                )
        ctx.report = report
        await ctx.emit(progress_event(self.name, "completed", {"report_produced": report is not None}))
        return ReportOutcome(report=report, refused=False)


BUILD_SITE_BRIEF: Final = BuildSiteBrief()
GATHER_WAREHOUSE_EVIDENCE: Final = GatherWarehouseEvidence()
ASSESS_SUFFICIENCY: Final = AssessSufficiency()
GATHER_WEB_EVIDENCE: Final = GatherWebEvidence()
SYNTHESIZE_REPORT: Final = SynthesizeReport()

# The topology, declared rather than inferred, so it can be asserted and documented.
GRAPH_EDGES: Final[tuple[tuple[str, str, str], ...]] = (
    (BuildSiteBrief.name, GatherWarehouseEvidence.name, "always"),
    (GatherWarehouseEvidence.name, AssessSufficiency.name, "always"),
    (AssessSufficiency.name, GatherWebEvidence.name, "warehouse evidence is insufficient"),
    (AssessSufficiency.name, SynthesizeReport.name, "warehouse evidence is sufficient"),
    (GatherWebEvidence.name, SynthesizeReport.name, "always"),
)


# --- Runner ------------------------------------------------------------------------


async def execute_graph(ctx: GraphContext) -> ReportOutcome:
    """Walk the graph once, emitting progress events and returning the terminal outcome."""
    await BUILD_SITE_BRIEF.run(ctx)
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
