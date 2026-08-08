"""The agent graph walks deterministic edges, stays bounded, and keeps the stream contract."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from agri_data_service.agent import graph as agent_graph
from agri_data_service.agent import tools as agent_tools
from agri_data_service.agent.report import (
    Observation,
    RemediationRecommendation,
    RemediationReport,
    RiskSummary,
    report_json_schema,
)
from agri_data_service.config import settings
from agri_data_service.routes import agent_analysis as agent_route

_HTTP_SERVICE_UNAVAILABLE = 503
_HTTP_BAD_REQUEST = 400
_EXPECTED_SEARCHES_WHEN_SINGLE_SOURCE = 2
_EXPECTED_MODEL_PASSES_WITH_WEB = 2


# --- Database stubs ----------------------------------------------------------------


class _Mappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def all(self) -> list[dict[str, Any]]:
        return self.rows


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> _Mappings:
        return _Mappings(self.rows)


class _Session:
    """Records the statement and bound parameters of every read."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.statements: list[str] = []
        self.parameters: list[dict[str, Any]] = []

    async def execute(self, statement: object, parameters: dict[str, Any]) -> _Result:
        self.statements.append(str(statement))
        self.parameters.append(parameters)
        return _Result(self.rows)


def _session_provider(session: _Session) -> Any:
    @asynccontextmanager
    async def provider() -> AsyncIterator[_Session]:
        yield session

    return provider


# --- Anthropic SDK stubs -----------------------------------------------------------


def _text_block(text: str) -> Any:
    return SimpleNamespace(type="text", text=text)


def _message(*blocks: Any, stop_reason: str = "end_turn") -> Any:
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


class _Stream:
    """One model turn: a few text deltas, then the assembled message."""

    def __init__(self, message: Any, deltas: tuple[str, ...] = ()) -> None:
        self.message = message
        self.deltas = deltas

    async def __aiter__(self) -> AsyncIterator[Any]:
        for delta in self.deltas:
            yield SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text_delta", text=delta),
            )

    async def get_final_message(self) -> Any:
        return self.message


class _Runner:
    """Async-iterable stand-in for BetaAsyncStreamingToolRunner."""

    def __init__(self, streams: list[_Stream], ledger_entries: list[dict[str, Any]] | None = None) -> None:
        self._streams = list(streams)
        self._ledger_entries = list(ledger_entries or [])

    def __aiter__(self) -> _Runner:
        return self

    async def __anext__(self) -> _Stream:
        if not self._streams:
            raise StopAsyncIteration
        # Recording here stands in for the real tools running inside the runner.
        for entry in self._ledger_entries:
            agent_tools._record(str(entry["tool"]), int(entry["row_count"]), {})
        self._ledger_entries = []
        return self._streams.pop(0)

    def generate_tool_call_response(self) -> dict[str, Any] | None:
        return None


class _Messages:
    def __init__(self, runners: list[_Runner], parse_response: Any) -> None:
        self._runners = list(runners)
        self._parse_response = parse_response
        self.tool_runner_calls: list[dict[str, Any]] = []
        self.parse_calls: list[dict[str, Any]] = []

    def tool_runner(self, **kwargs: Any) -> _Runner:
        self.tool_runner_calls.append(kwargs)
        return self._runners.pop(0) if self._runners else _Runner([])

    async def parse(self, **kwargs: Any) -> Any:
        self.parse_calls.append(kwargs)
        return self._parse_response


def _client(runners: list[_Runner], parse_response: Any) -> Any:
    messages = _Messages(runners, parse_response)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages))


def _report() -> RemediationReport:
    return RemediationReport(
        riskSummary=RiskSummary(
            level="moderate",
            headline="Multi-year drought with recent fire activity nearby.",
            factors=["four consecutive D2 weeks", "detections within 8 km"],
            evidenceOrigin="warehouse",
            evidenceSources=["drought", "fireDetections"],
        ),
        observations=[
            Observation(
                statement="Drought severity reached D2 in each of the last four published weeks.",
                evidenceOrigin="warehouse",
                evidenceSource="drought",
            )
        ],
        remediation=[
            RemediationRecommendation(
                strategy="fuel_reduction",
                title="Thin ladder fuels within the defensible perimeter",
                rationale="Detections cluster upslope and the stand is continuous.",
                timeframe="short_term",
                confidence="moderate",
                consultProfessionals=["wildfire_mitigation_specialist", "forester"],
                evidenceOrigin="model_inference",
            )
        ],
        professionalConsultation="Ask a local wildfire mitigation specialist to confirm the treatment spacing.",
    )


def _parsed(report: RemediationReport | None, stop_reason: str = "end_turn") -> Any:
    return SimpleNamespace(parsed_output=report, stop_reason=stop_reason)


def _context(client: Any, *, question: str | None = None) -> agent_graph.GraphContext:
    return agent_graph.GraphContext(
        request=agent_graph.AgentRequest(
            longitude=-116.2,
            latitude=43.6,
            precision="approximate",
            question=question,
            as_of=datetime(2026, 8, 8, tzinfo=UTC),
        ),
        client=client,
        events=asyncio.Queue(),
        session_provider=_session_provider(_Session()),
    )


def _drain(context: agent_graph.GraphContext) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    while not context.events.empty():
        events.append(context.events.get_nowait())
    return events


def _types(events: list[dict[str, Any]]) -> list[str]:
    return [event["type"] for event in events]


# --- Topology ----------------------------------------------------------------------


def test_graph_topology_is_declared_and_terminal() -> None:
    """The declared edges must form the documented four-node walk with one optional branch."""
    edges = agent_graph.GRAPH_EDGES
    assert [source for source, _target, _label in edges] == [
        "gather_warehouse_evidence",
        "assess_sufficiency",
        "assess_sufficiency",
        "web_evidence",
    ]
    assert {target for _source, target, _label in edges} == {
        "assess_sufficiency",
        "web_evidence",
        "synthesize_report",
    }
    # synthesize_report is terminal: nothing leaves it.
    assert not [source for source, _target, _label in edges if source == "synthesize_report"]


# --- Sufficiency gate --------------------------------------------------------------


def _evidence(*populated: str) -> agent_graph.WarehouseEvidence:
    return agent_graph.WarehouseEvidence(
        tool_calls=tuple({"tool": name, "row_count": 1} for name in populated),
        populated_tools=populated,
        refused=False,
    )


def test_sufficiency_gate_closes_web_when_warehouse_covers_the_point() -> None:
    """Two or more populated sources and no specific question means no web budget at all."""
    verdict = agent_graph.AssessSufficiency.decide(
        _evidence("signals_near_point", "drought_history_at_point"),
        has_question=False,
    )
    assert verdict.warehouse_is_sufficient is True
    assert verdict.searches_allowed == 0


def test_sufficiency_gate_opens_full_budget_when_warehouse_is_empty() -> None:
    """No warehouse rows at all buys the full mirrored search budget."""
    verdict = agent_graph.AssessSufficiency.decide(_evidence(), has_question=False)
    assert verdict.warehouse_is_sufficient is False
    assert verdict.searches_allowed == agent_graph.MAX_SEARCHES_PER_REQUEST


def test_sufficiency_gate_opens_partial_budget_for_a_single_source() -> None:
    """One populated source is partial coverage, not sufficiency."""
    verdict = agent_graph.AssessSufficiency.decide(_evidence("drought_history_at_point"), has_question=False)
    assert verdict.warehouse_is_sufficient is False
    assert verdict.searches_allowed == _EXPECTED_SEARCHES_WHEN_SINGLE_SOURCE


def test_sufficiency_gate_allows_one_search_for_a_specific_question() -> None:
    """Partial coverage plus a caller question buys exactly one search."""
    verdict = agent_graph.AssessSufficiency.decide(
        _evidence("signals_near_point", "drought_history_at_point"),
        has_question=True,
    )
    assert verdict.warehouse_is_sufficient is False
    assert verdict.searches_allowed == 1


# --- Graph walks -------------------------------------------------------------------


async def test_graph_happy_path_skips_web_and_emits_a_report() -> None:
    """Sufficient warehouse evidence must reach the report without a second model pass."""
    runner = _Runner(
        [_Stream(_message(_text_block("Reading the warehouse.")), deltas=("Reading ", "the warehouse."))],
        ledger_entries=[
            {"tool": "signals_near_point", "row_count": 3},
            {"tool": "drought_history_at_point", "row_count": 12},
        ],
    )
    client = _client([runner], _parsed(_report()))
    context = _context(client)

    outcome = await agent_graph.execute_graph(context)

    assert outcome.refused is False
    assert outcome.report is not None
    assert len(client.beta.messages.tool_runner_calls) == 1, "the web pass must not run"
    event_types = _types(_drain(context))
    assert "text" in event_types
    assert event_types[-1] == "report"
    assert "search" not in event_types


async def test_graph_runs_the_web_pass_when_the_warehouse_is_empty() -> None:
    """An empty warehouse must open the web pass, which then carries the search tool."""
    warehouse_runner = _Runner([_Stream(_message(_text_block("Nothing stored here.")))])
    web_message = _message(
        SimpleNamespace(
            type="server_tool_use",
            name="web_search",
            id="srv_1",
            input={"query": "Idaho fuel reduction cost share"},
        ),
        SimpleNamespace(
            type="web_search_tool_result",
            tool_use_id="srv_1",
            content=[SimpleNamespace(url="https://example.gov/program", title="Cost share program")],
        ),
        _text_block("Found a state program."),
    )
    client = _client([warehouse_runner, _Runner([_Stream(web_message)])], _parsed(_report()))
    context = _context(client)

    outcome = await agent_graph.execute_graph(context)

    assert outcome.report is not None
    calls = client.beta.messages.tool_runner_calls
    assert len(calls) == _EXPECTED_MODEL_PASSES_WITH_WEB, "the web pass must run"
    warehouse_tool_names = {getattr(tool, "name", None) for tool in calls[0]["tools"]}
    assert "web_search" not in warehouse_tool_names
    web_tools = calls[1]["tools"]
    search_tool = next(tool for tool in web_tools if isinstance(tool, dict))
    assert search_tool["type"] == "web_search_20260209"
    assert search_tool["max_uses"] == agent_graph.MAX_SEARCHES_PER_REQUEST
    events = _drain(context)
    assert {"search", "sources", "report"} <= set(_types(events))
    search_event = next(event for event in events if event["type"] == "search")
    assert search_event["query"] == "Idaho fuel reduction cost share"
    assert search_event["resultCount"] == 1
    sources_event = next(event for event in events if event["type"] == "sources")
    assert sources_event["sources"] == [{"title": "Cost share program", "url": "https://example.gov/program"}]


async def test_graph_emits_refusal_and_stops() -> None:
    """A refused warehouse pass ends the run with a refusal and no report."""
    runner = _Runner([_Stream(_message(_text_block(""), stop_reason="refusal"))])
    client = _client([runner], _parsed(_report()))
    context = _context(client)

    outcome = await agent_graph.execute_graph(context)

    assert outcome.refused is True
    assert outcome.report is None
    assert _types(_drain(context))[-1] == "refusal"
    assert not client.beta.messages.parse_calls, "a refusal must not reach report synthesis"


async def test_graph_restarts_the_runner_on_pause_turn() -> None:
    """A paused turn must be resumed explicitly; the SDK runner does not do it for us."""
    paused = _Runner([_Stream(_message(_text_block("Working."), stop_reason="pause_turn"))])
    resumed = _Runner(
        [_Stream(_message(_text_block("Done.")))],
        ledger_entries=[
            {"tool": "signals_near_point", "row_count": 1},
            {"tool": "forecast_summary_for_cell", "row_count": 4},
        ],
    )
    client = _client([paused, resumed], _parsed(_report()))
    context = _context(client)

    outcome = await agent_graph.execute_graph(context)

    assert outcome.report is not None
    assert len(client.beta.messages.tool_runner_calls) == _EXPECTED_MODEL_PASSES_WITH_WEB, (
        "the paused turn must be restarted"
    )


async def test_report_synthesis_sends_the_structured_output_format() -> None:
    """The final round must use structured outputs, opus-5 and the server-side fallback."""
    runner = _Runner(
        [_Stream(_message(_text_block("ok")))],
        ledger_entries=[
            {"tool": "signals_near_point", "row_count": 2},
            {"tool": "drought_history_at_point", "row_count": 2},
        ],
    )
    client = _client([runner], _parsed(_report()))
    context = _context(client)

    await agent_graph.execute_graph(context)

    parse_call = client.beta.messages.parse_calls[0]
    assert parse_call["output_format"] is RemediationReport
    assert parse_call["model"] == "claude-opus-5"
    assert parse_call["fallbacks"] == "default"
    assert parse_call["betas"] == [agent_graph.SERVER_SIDE_FALLBACK_BETA]
    assert "thinking" not in parse_call, "adaptive thinking is the model default and must not be sent"


async def test_system_prefix_carries_one_cache_breakpoint() -> None:
    """Prompt caching depends on a stable system prefix with the breakpoint on its last block."""
    context = _context(_client([], _parsed(None)))
    blocks = context.system_blocks()
    assert len(blocks) == 1
    assert blocks[-1]["cache_control"] == {"type": "ephemeral"}
    assert "43.6" not in blocks[0]["text"], "per-request context must never enter the cached prefix"


# --- Tool bounds -------------------------------------------------------------------


async def test_signal_tool_clamps_radius_and_window() -> None:
    """Over-large arguments are clamped by the service, and the clamp is reported back."""
    session = _Session([])
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        raw = await agent_tools.query_signals_near_point(
            longitude=-116.2,
            latitude=43.6,
            radius_meters=10_000_000.0,
            days_back=999_999,
        )
    payload = json.loads(raw)
    assert payload["applied_bounds"]["radius_meters"] == agent_tools.MAX_RADIUS_METERS
    assert payload["applied_bounds"]["days_back"] == agent_tools.MAX_DAYS_BACK
    assert session.parameters[0]["radius_meters"] == agent_tools.MAX_RADIUS_METERS
    assert session.parameters[0]["cell_limit"] == agent_tools.MAX_CELL_FANOUT
    assert "agri.signal_observation" in session.statements[0]


async def test_tools_reject_an_out_of_range_coordinate_without_querying() -> None:
    """A bad coordinate must never reach the database."""
    session = _Session([])
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        raw = await agent_tools.query_drought_history_at_point(longitude=999.0, latitude=43.6)
    assert "error" in json.loads(raw)
    assert not session.statements


async def test_forecast_tool_reads_only_the_published_serving_view() -> None:
    """The agent must not be able to see a draft or unvalidated forecast."""
    session = _Session([])
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        await agent_tools.query_forecast_summary_for_cell(longitude=-116.2, latitude=43.6)
    statement = session.statements[0]
    assert "agri.v_forecast_series_serving" in statement
    assert "agri.forecast_value" not in statement


async def test_every_tool_statement_is_read_only() -> None:
    """No agent-facing statement may mutate the warehouse."""
    session = _Session([])
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        await agent_tools.query_signals_near_point(longitude=-116.2, latitude=43.6)
        await agent_tools.query_drought_history_at_point(longitude=-116.2, latitude=43.6)
        await agent_tools.query_fire_history_near_point(longitude=-116.2, latitude=43.6)
        await agent_tools.query_forecast_summary_for_cell(longitude=-116.2, latitude=43.6)
    assert len(session.statements) == len(agent_tools.WAREHOUSE_TOOLS)
    for statement in session.statements:
        # The beginner-doc headers are prose and legitimately contain English words that
        # collide with SQL verbs ("drops the rest"); only executable lines are scanned.
        executable = "\n".join(line for line in statement.splitlines() if not line.lstrip().startswith("--")).upper()
        for verb in ("INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE", "CREATE ", "DROP", "ALTER"):
            assert verb not in executable, f"{verb} must not appear in an agent tool statement"


def test_tool_schemas_publish_bounded_arguments() -> None:
    """Every model-facing tool must advertise a coordinate-only, bounded surface."""
    for tool in agent_tools.WAREHOUSE_TOOLS:
        definition = tool.to_dict()
        properties = definition["input_schema"]["properties"]
        assert {"longitude", "latitude"} <= set(properties)
        assert definition["description"]


# --- Report contract ---------------------------------------------------------------


def test_report_round_trips_through_pydantic() -> None:
    """The report must survive a dump/validate cycle with the frontend's field names intact."""
    report = _report()
    dumped = report.model_dump()
    assert set(dumped) == {"riskSummary", "observations", "remediation", "professionalConsultation"}
    assert set(dumped["riskSummary"]) == {
        "level",
        "headline",
        "factors",
        "evidenceOrigin",
        "evidenceSources",
    }
    assert RemediationReport.model_validate(dumped) == report


def test_report_json_schema_is_closed() -> None:
    """Structured outputs require every object to forbid unknown properties."""
    schema = report_json_schema()
    assert schema["additionalProperties"] is False
    for definition in schema.get("$defs", {}).values():
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False


def test_report_rejects_a_vocabulary_the_frontend_cannot_render() -> None:
    """Enum drift against regional-intelligence.ts must fail loudly, not render blank."""
    payload = _report().model_dump()
    payload["remediation"][0]["strategy"] = "space_lasers"
    with pytest.raises(ValueError, match="strategy"):
        RemediationReport.model_validate(payload)


# --- Stream and route contract -----------------------------------------------------


def test_stream_events_match_the_typescript_union() -> None:
    """Event shapes are the frontend contract and must stay byte-compatible with ai-prompt.ts."""
    assert agent_graph.text_event("hi") == {"type": "text", "text": "hi"}
    assert agent_graph.search_event("q", 2) == {"type": "search", "query": "q", "resultCount": 2}
    assert agent_graph.refusal_event() == {"type": "refusal"}
    sources = agent_graph.sources_event([agent_graph.WebSourceCitation(title="t", url="u")])
    assert sources == {"type": "sources", "sources": [{"title": "t", "url": "u"}]}
    report = agent_graph.report_event(_report())
    assert report["type"] == "report"
    assert set(report["report"]) == {"riskSummary", "observations", "remediation", "professionalConsultation"}


def test_sse_frames_name_the_event_and_carry_json() -> None:
    """Each frame is a standard SSE record named by its own event type."""
    frame = agent_route._frame(agent_graph.text_event("hello"))
    assert frame.startswith("event: text\ndata: ")
    assert frame.endswith("\n\n")
    body = json.loads(frame.split("data: ", 1)[1].strip())
    assert body == {"type": "text", "text": "hello"}


async def test_analyze_returns_503_without_a_configured_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The service stays fully functional without the key; only this route degrades."""
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    response = await agent_route.analyze_location(SimpleNamespace(json={"longitude": -116.2, "latitude": 43.6}))
    assert response is not None
    assert response.status == _HTTP_SERVICE_UNAVAILABLE
    body = json.loads(response.body)
    assert body["code"] == "agent_disabled"
    assert "ANTHROPIC_API_KEY" in body["error"]


async def test_analyze_rejects_an_out_of_range_coordinate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ingress validation happens before any client is constructed."""
    monkeypatch.setattr(settings, "anthropic_api_key", SimpleNamespace(get_secret_value=lambda: "sk-test"))
    response = await agent_route.analyze_location(SimpleNamespace(json={"longitude": 999.0, "latitude": 43.6}))
    assert response is not None
    assert response.status == _HTTP_BAD_REQUEST
    assert json.loads(response.body)["code"] == "invalid_request"


async def test_analyze_streams_the_graph_over_sse(monkeypatch: pytest.MonkeyPatch) -> None:
    """The route opens an SSE response, leads with the disclaimer, and drains the graph queue."""
    monkeypatch.setattr(settings, "anthropic_api_key", SimpleNamespace(get_secret_value=lambda: "sk-test"))
    runner = _Runner(
        [_Stream(_message(_text_block("Checking.")), deltas=("Check", "ing."))],
        ledger_entries=[
            {"tool": "signals_near_point", "row_count": 1},
            {"tool": "drought_history_at_point", "row_count": 1},
        ],
    )
    monkeypatch.setattr(agent_route, "build_agent_client", lambda: _client([runner], _parsed(_report())))
    monkeypatch.setattr(
        agent_graph.warehouse_tools,
        "published_reader_session",
        _session_provider(_Session()),
    )

    sent: list[str] = []

    class _Response:
        async def send(self, chunk: str) -> None:
            sent.append(chunk)

    async def respond(**kwargs: Any) -> _Response:
        assert kwargs["content_type"] == "text/event-stream"
        return _Response()

    request = SimpleNamespace(json={"longitude": -116.2, "latitude": 43.6}, respond=respond)
    assert await agent_route.analyze_location(request) is None

    names = [chunk.split("\n", 1)[0] for chunk in sent]
    assert names[0] == "event: disclaimer"
    assert "event: report" in names
    assert "event: error" not in names


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="needs a live ANTHROPIC_API_KEY; the unit suite never calls the real API",
)
async def test_live_report_synthesis_returns_the_declared_schema() -> None:
    """Smoke-test the real structured-outputs round trip when a key is present."""
    client = agent_route.build_agent_client()
    response = await client.beta.messages.parse(
        model=agent_graph.MODEL,
        max_tokens=agent_graph.MAX_OUTPUT_TOKENS,
        system=[{"type": "text", "text": agent_graph.SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": "No warehouse evidence resolved. Produce the briefing."}],
        output_format=RemediationReport,
        betas=[agent_graph.SERVER_SIDE_FALLBACK_BETA],
        fallbacks="default",
    )
    assert isinstance(response.parsed_output, RemediationReport)
