"""The agent graph walks deterministic edges, stays bounded, and keeps the stream contract."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

from agri_data_service.agent import graph as agent_graph
from agri_data_service.agent import strategy_knowledge
from agri_data_service.agent import tools as agent_tools
from agri_data_service.agent.report import (
    ConversationTurn,
    Observation,
    RemediationRecommendation,
    RemediationReport,
    RiskSummary,
    downgrade_literature_claims,
    report_json_schema,
)
from agri_data_service.agent.surfaces import AGENT_SURFACE_NAMES
from agri_data_service.config import settings
from agri_data_service.parquet_ops.faults import ServingRefusalError
from agri_data_service.routes import agent_analysis as agent_route
from tests.agent_fakes import FakeAgentWarehouse, published_lane

_HTTP_SERVICE_UNAVAILABLE = 503
_HTTP_BAD_REQUEST = 400
_EXPECTED_SEARCHES_WHEN_SINGLE_SOURCE = 2
_EXPECTED_MODEL_PASSES_WITH_WEB = 2

# One day inside every contracted lane's horizon, and the instant the window tools are asked at.
_SELECTED_DATE = date(2026, 3, 14)
_AS_OF = datetime(2026, 3, 14, tzinfo=UTC)


def _warehouse(*, published: Sequence[date] = ()) -> FakeAgentWarehouse:
    """An in-memory Parquet warehouse whose VPD lane published the named days."""
    source = FakeAgentWarehouse()
    for day in published:
        source.listing_store.write_day("soil-field-vpd", "observed", 13, day)
    return source


#: The real reader, kept before the autouse stub below replaces it for every graph walk.
_REAL_READ_SITE_BRIEF_INPUTS = agent_graph.read_site_brief_inputs
_BRIEF_SECTIONS = ("soil", "fire", "drought", "weather", "land_cover")
_GOLDEN_BRIEF_CASES = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "site_brief_golden.json").read_text(encoding="utf-8")
)["cases"]


def _unavailable_brief_inputs(request: agent_graph.AgentRequest) -> dict[str, Any]:
    """Brief inputs with every section unavailable, the shape a graph walk sees with reads disabled."""
    return {
        "built_on": request.as_of.date().isoformat(),
        "point": {
            "longitude_e5": round(request.longitude * 100_000),
            "latitude_e5": round(request.latitude * 100_000),
        },
        **{section: {"state": "unavailable", "reason": "reads_disabled"} for section in _BRIEF_SECTIONS},
    }


@pytest.fixture(autouse=True)
def _no_live_site_brief_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Graph walks here script the model; the brief node's five lane reads must never reach a bucket."""

    async def unavailable(request: agent_graph.AgentRequest) -> dict[str, Any]:
        return _unavailable_brief_inputs(request)

    monkeypatch.setattr(agent_graph, "read_site_brief_inputs", unavailable)


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


_PLANE_PROBE_MARKER = "-- agent_materialized_plane_populated"


def executable_sql(statement: str) -> str:
    """`statement` with every `--` comment line blanked out.

    Every `.sql` file in this service opens with a walkthrough that NAMES the relation the query
    was repointed away from, because "reads X, not the 26 GB Y" is the single most useful thing a
    reader can learn from the header. A bare `"Y" not in statement` therefore fails on the
    explanation rather than on the query. Assertions about what a statement READS are made against
    the executable text; assertions about what it SAYS can still use the raw string.
    """
    return "\n".join("" if line.lstrip().startswith("--") else line for line in statement.splitlines())


class _Session:
    """Records the statement and bound parameters of every read."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.statements: list[str] = []
        self.parameters: list[dict[str, Any]] = []

    async def execute(self, statement: object, parameters: dict[str, Any]) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        self.parameters.append(parameters)
        if sql.lstrip().startswith(_PLANE_PROBE_MARKER):
            # Every pre-aggregated relation this run names is built. A tool refuses by name when
            # one is not, which test_agent_signal_time_tools.py covers; here it must not fire.
            return _Result(
                [
                    {"relation_name": name, "relation_exists": True, "relation_kind": "m", "is_populated": True}
                    for name in parameters.get("relation_names", [])
                ]
            )
        return _Result(self.rows)

    def statements_excluding_plane_probes(self) -> list[str]:
        """Every recorded statement that is not the catalog probe."""
        return [sql for sql in self.statements if not sql.lstrip().startswith(_PLANE_PROBE_MARKER)]

    def parameters_excluding_plane_probes(self) -> list[dict[str, Any]]:
        """The bound parameters of every recorded statement that is not the catalog probe."""
        return [
            bound
            for sql, bound in zip(self.statements, self.parameters, strict=True)
            if not sql.lstrip().startswith(_PLANE_PROBE_MARKER)
        ]

    def markers_excluding_plane_probes(self) -> list[str]:
        """Each non-probe statement's line-one marker, in execution order."""
        return [
            sql.lstrip().splitlines()[0].removeprefix("-- ").strip() for sql in self.statements_excluding_plane_probes()
        ]


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
            agent_tools._record(str(entry["tool"]), int(entry["row_count"]), dict(entry.get("detail", {})))
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
    """The declared edges must form the documented five-node walk with one optional branch."""
    edges = agent_graph.GRAPH_EDGES
    assert [source for source, _target, _label in edges] == [
        "build_site_brief",
        "gather_warehouse_evidence",
        "assess_sufficiency",
        "assess_sufficiency",
        "web_evidence",
    ]
    assert {target for _source, target, _label in edges} == {
        "gather_warehouse_evidence",
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
        _evidence("surface_evidence_for_selection", "drought_history_at_point"),
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
        _evidence("surface_evidence_for_selection", "drought_history_at_point"),
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
            {"tool": "surface_evidence_for_selection", "row_count": 3},
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
    """An empty warehouse opens web search without re-exposing scoped species-information access."""
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
    web_tool_names = {getattr(tool, "name", None) for tool in web_tools}
    assert "species_information" not in web_tool_names
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


@pytest.mark.parametrize(
    ("caller_species_id", "requested_species_id"),
    [
        ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        (None, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    ],
)
async def test_web_pass_keeps_species_access_caller_scoped(
    monkeypatch: pytest.MonkeyPatch,
    caller_species_id: str | None,
    requested_species_id: str,
) -> None:
    """The later pass must not regain an unscoped species reader through its injected provider."""
    marker = object()
    provider_entries: list[object] = []

    @asynccontextmanager
    async def provider() -> Any:
        provider_entries.append(marker)
        yield marker

    async def fail_read(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("a web-pass species request must be refused before opening the provider")

    async def attempt_species_read(
        _ctx: agent_graph.GraphContext,
        **_kwargs: Any,
    ) -> bool:
        payload = json.loads(await agent_tools.species_information(requested_species_id))
        assert payload["state"] == "refused"
        assert payload["reason"]["code"] == "invalid_species_information_request"
        return False

    monkeypatch.setattr(agent_tools, "read_species_information", fail_read)
    monkeypatch.setattr(agent_graph, "_run_pass", attempt_species_read)
    context = _context(_client([], _parsed(None)))
    context.request = agent_graph.AgentRequest(
        longitude=-116.2,
        latitude=43.6,
        precision="approximate",
        species_id=caller_species_id,
    )
    context.session_provider = provider

    result = await agent_graph.GATHER_WEB_EVIDENCE.run(
        context,
        agent_graph.SufficiencyVerdict(
            warehouse_is_sufficient=False,
            searches_allowed=1,
            reasons=("synthetic regression",),
            coverage={},
        ),
    )

    assert result.refused is False
    assert provider_entries == [], "an invalid species UUID must not open the injected provider"


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
            {"tool": "surface_evidence_for_selection", "row_count": 1},
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
            {"tool": "surface_evidence_for_selection", "row_count": 2},
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


# --- Literature claims need a literature answer ------------------------------------

_MEASURED_LEDGER: tuple[dict[str, Any], ...] = (
    {"tool": "surface_evidence_for_selection", "row_count": 3},
    {"tool": "drought_history_at_point", "row_count": 12},
)


def _literature_entry(tool: str, state: str, *, result_count: int = 1) -> dict[str, Any]:
    """A ledger entry matching `strategy_knowledge.ask`'s shape; `result_count` only rides an answer."""
    detail = {"evidence_domain": strategy_knowledge.LITERATURE_EVIDENCE_DOMAIN, "state": state}
    if state == "answered":
        detail["result_count"] = result_count
    return {"tool": tool, "row_count": 0, "detail": detail}


def _literature_report() -> RemediationReport:
    payload = _report().model_dump()
    payload["remediation"][0].update({"evidenceOrigin": "literature", "evidenceSource": "strategy-knowledge"})
    payload["observations"].append(
        {
            "statement": "Cited trials report thinning lowers crown-fire spread.",
            "evidenceOrigin": "literature",
            "evidenceSource": "strategy-knowledge",
        }
    )
    return RemediationReport.model_validate(payload)


async def _run_with_ledger(*entries: dict[str, Any]) -> tuple[agent_graph.ReportOutcome, list[dict[str, Any]]]:
    runner = _Runner([_Stream(_message(_text_block("ok")))], ledger_entries=[*_MEASURED_LEDGER, *entries])
    context = _context(_client([runner], _parsed(_literature_report())))
    outcome = await agent_graph.execute_graph(context)
    return outcome, _drain(context)


@pytest.mark.parametrize(
    "entries",
    [
        pytest.param((), id="no-literature-call"),
        pytest.param(
            (
                _literature_entry("search_environmental_strategies", "refused"),
                _literature_entry("search_strategy_research_findings", "refused"),
            ),
            id="only-refusals",
        ),
        pytest.param(
            (_literature_entry("search_environmental_strategies", "answered", result_count=0),),
            id="answered-but-zero-records",
        ),
    ],
)
async def test_an_unbacked_literature_claim_is_downgraded_and_recorded(entries: tuple[dict[str, Any], ...]) -> None:
    outcome, events = await _run_with_ledger(*entries)

    assert outcome.report is not None
    recommendation = outcome.report.remediation[0]
    assert (recommendation.evidenceOrigin, recommendation.evidenceSource) == ("model_inference", None)
    assert recommendation.title == _report().remediation[0].title, "the claim is relabelled, never dropped"
    assert outcome.report.observations[1].evidenceOrigin == "model_inference"
    assert outcome.report.observations[0].evidenceOrigin == "warehouse", "non-literature claims are untouched"
    [downgrade] = [event for event in events if event.get("status") == "literature_downgraded"]
    assert downgrade["node"] == "synthesize_report"
    assert downgrade["detail"]["claims"] == ["observations[1]", "remediation[0]"]
    [streamed] = [event for event in events if event["type"] == "report"]
    assert streamed["report"]["remediation"][0]["evidenceOrigin"] == "model_inference"


async def test_a_literature_claim_backed_by_an_answered_call_is_kept() -> None:
    outcome, events = await _run_with_ledger(
        _literature_entry("search_environmental_strategies", "refused"),
        _literature_entry("get_environmental_strategies", "answered", result_count=2),
    )

    assert outcome.report is not None
    recommendation = outcome.report.remediation[0]
    assert (recommendation.evidenceOrigin, recommendation.evidenceSource) == ("literature", "strategy-knowledge")
    assert not [event for event in events if event.get("status") == "literature_downgraded"]


def test_literature_answered_requires_a_populated_result_count() -> None:
    """`state: "answered"` alone is not enough: a real search that matched nothing backs no claim."""
    assert (
        agent_graph.literature_answered(
            ({"tool": "search_environmental_strategies", "state": "answered", "result_count": 0},)
        )
        is False
    )
    assert (
        agent_graph.literature_answered(
            ({"tool": "search_environmental_strategies", "state": "answered", "result_count": 1},)
        )
        is True
    )
    # A missing result_count (an older or malformed ledger entry) fails closed, the same as zero.
    assert agent_graph.literature_answered(({"tool": "get_environmental_strategies", "state": "answered"},)) is False


async def test_a_literature_answer_from_the_web_pass_backs_a_claim() -> None:
    """The web pass also offers the literature tools, so its ledger is kept for this check."""
    warehouse_runner = _Runner([_Stream(_message(_text_block("Nothing stored here.")))])
    web_runner = _Runner(
        [_Stream(_message(_text_block("Read the literature.")))],
        ledger_entries=[_literature_entry("search_strategy_research_findings", "answered")],
    )
    context = _context(_client([warehouse_runner, web_runner], _parsed(_literature_report())))

    outcome = await agent_graph.execute_graph(context)

    assert outcome.report is not None
    assert outcome.report.remediation[0].evidenceOrigin == "literature"
    assert agent_graph.literature_answered(context.tool_ledger)


def test_the_request_builds_the_server_literature_context() -> None:
    """Seam S2: the user's own turns (latest last) and the map point; no site_facts this graph can trust."""
    request = agent_graph.AgentRequest(
        longitude=-116.2,
        latitude=43.6,
        precision="exact",
        question="and after the fire?",
        history=(
            ConversationTurn(role="user", content="my pasture is sour"),
            ConversationTurn(role="assistant", content="Lime is often used."),
            ConversationTurn(role="user", content="how much?"),
        ),
    )
    context = request.strategy_context()
    assert context.user_question == "my pasture is sour\nhow much?\nand after the fire?"
    assert (context.longitude, context.latitude) == (-116.2, 43.6)
    assert context.site_facts is None
    assert strategy_knowledge.server_site_profile(context) == strategy_knowledge.StrategySiteProfile(
        region="great_basin_high_desert"
    )


def test_an_off_globe_request_keeps_its_question_but_drops_the_point() -> None:
    request = agent_graph.AgentRequest(longitude=-316.2, latitude=43.6, precision="exact", question="why?")
    context = request.strategy_context()
    assert context.user_question == "why?"
    assert context.longitude is None
    assert context.latitude is None


async def test_both_model_passes_bind_the_server_literature_context(monkeypatch: pytest.MonkeyPatch) -> None:
    bound: list[strategy_knowledge.StrategyContext | None] = []

    async def record_context(_ctx: agent_graph.GraphContext, **_kwargs: Any) -> bool:
        bound.append(strategy_knowledge.current_strategy_context())
        return False

    monkeypatch.setattr(agent_graph, "_run_pass", record_context)
    context = _context(_client([], _parsed(None)), question="stop the erosion")

    await agent_graph.GATHER_WAREHOUSE_EVIDENCE.run(context)
    await agent_graph.GATHER_WEB_EVIDENCE.run(
        context,
        agent_graph.SufficiencyVerdict(
            warehouse_is_sufficient=False, searches_allowed=1, reasons=("synthetic",), coverage={}
        ),
    )

    assert bound == [context.request.strategy_context()] * len(bound)
    assert len(bound) == _EXPECTED_MODEL_PASSES_WITH_WEB
    [first, _] = bound
    assert first is not None
    assert first.user_question == "stop the erosion"
    assert strategy_knowledge.current_strategy_context() is None, "the binding ends with each pass"


def test_downgrading_a_report_without_literature_is_a_no_op() -> None:
    report = _report()
    assert downgrade_literature_claims(report) == (report, ())


def test_coverage_counts_location_tools_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """species_information and the three literature tools are never measured coverage."""
    monkeypatch.delenv("SOIL_PROPERTIES_READS_ENABLED", raising=False)
    verdict = agent_graph.AssessSufficiency.decide(_evidence(), has_question=False)
    names = {tool.name for tool in agent_tools.published_warehouse_tools()}
    assert names >= agent_graph.LITERATURE_TOOLS
    assert verdict.coverage["tools_available"] == len(names - {"species_information"} - agent_graph.LITERATURE_TOOLS)


async def test_system_prefix_carries_one_cache_breakpoint() -> None:
    """Prompt caching depends on a stable system prefix with the breakpoint on its last block."""
    context = _context(_client([], _parsed(None)))
    blocks = context.system_blocks()
    assert len(blocks) == 1
    assert blocks[-1]["cache_control"] == {"type": "ephemeral"}
    assert "43.6" not in blocks[0]["text"], "per-request context must never enter the cached prefix"


# --- Tool bounds -------------------------------------------------------------------


async def test_tools_reject_an_out_of_range_coordinate_without_querying() -> None:
    """A bad coordinate must never reach the warehouse."""
    session = _Session([])
    source = _warehouse()
    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        raw = await agent_tools.query_drought_history_at_point(longitude=999.0, latitude=43.6)
    assert "error" in json.loads(raw)
    assert not session.statements
    assert source.markers() == []


@pytest.mark.parametrize("as_of", [None, _AS_OF])
async def test_forecast_tool_refuses_retired_plane_without_any_database_read(as_of: datetime | None) -> None:
    """The retired forecast plane stays closed until its governed Parquet replacement is admitted."""
    source = _warehouse(published=[_SELECTED_DATE])
    session = _Session([])
    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        raw = await agent_tools.query_forecast_summary_for_cell(longitude=-116.2, latitude=43.6, as_of=as_of)

    assert session.statements == [], "retired relations must not even receive a readiness probe"
    assert source.markers() == [], "an unpublished forecast must not borrow observed environmental readings"
    payload = json.loads(raw)
    assert payload["error"] == "forecast_parquet_lane_not_published"
    assert payload["resolved_cell"] is None
    assert payload["forecast_values"] == []


async def test_the_drought_tool_reads_the_lane_the_map_serves_and_not_the_empty_one() -> None:
    """The live truthfulness bug, pinned through its second repoint.

    `agri.drought_polygon_snapshot` held zero rows and had no forward producer, so the original
    statement SUCCEEDED and returned nothing on every call -- which the agent could only read as
    "no drought was recorded here", on days the map paints drought. The fix then was to read
    `geo.drought_areas`, the plane the map served. The map now serves drought from the Parquet
    `drought` lane, so this tool reads that, and the assertion is still deliberately about the
    PLANE rather than about a row: sameness of source is what makes disagreement impossible rather
    than merely unlikely.
    """
    source = _warehouse()
    source.listing_store.write_day("drought", "observed", 13, date(2026, 3, 10))
    session = _Session([])
    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        await agent_tools.query_drought_history_at_point(longitude=-116.2, latitude=43.6, as_of=_AS_OF)

    addressed = source.part_uris_for("agent_drought_release_severity")
    assert addressed
    assert all("layer=drought/" in uri for uri in addressed)
    statement = source.statement_for("agent_drought_release_severity")
    assert "agri.drought_polygon_snapshot" not in statement
    assert "geo.drought_areas" not in statement
    # The polygon column is decoded for the containment test and never projected: on the PostgreSQL
    # side that column hid about 495 MB of TOAST behind 1,040 rows, and the reason survives the move.
    assert "ST_Intersects(ST_GeomFromWKB(geom), ST_Point(?, ?))" in statement
    assert "SELECT geom" not in executable_sql(statement)


async def test_the_drought_tool_reports_a_release_that_found_no_drought_as_a_fact() -> None:
    """A published release with no covering polygon is evidence; an empty list is not."""
    source = _warehouse()
    for day in (date(2026, 3, 3), date(2026, 3, 10), date(2026, 3, 17)):
        source.listing_store.write_day("drought", "observed", 13, day)
    source.answer(
        "agent_drought_release_severity",
        [
            {
                "valid_date": date(2026, 3, 10),
                "published_class_count": 5,
                "severity_class": None,
                "covering_class_count": 0,
                "published_at": None,
            }
        ],
    )
    async with agent_tools.run_context(session_provider=_session_provider(_Session([])), warehouse_source=source):
        raw = await agent_tools.query_drought_history_at_point(
            longitude=-116.2, latitude=43.6, as_of=datetime(2026, 3, 20, tzinfo=UTC)
        )

    payload = json.loads(raw)
    assert payload["releases_returned"] == 1
    assert payload["releases_with_drought_over_point"] == 0
    only = payload["weekly_severity"][0]
    assert only["severity_class"] is None
    assert only["covering_class_count"] == 0
    # The neighbouring releases travel with it, so a day between two Tuesdays gets a real gap. They
    # come from the LISTING now rather than from `geo.mv_drought_release_index`.
    assert only["prev_valid_date"] == "2026-03-03"
    assert only["next_valid_date"] == "2026-03-17"
    # The note has to separate the two absences or the model will collapse them.
    assert "existed and found no drought here" in payload["note"]
    assert "no release was published in the span at all" in payload["note"]


async def test_the_fire_tool_prefilters_on_a_degree_box_before_the_exact_geodesic_test() -> None:
    """A per-row geodesic distance over a year of partitions is the read that must not be issued."""
    source = _warehouse()
    source.listing_store.write_day("fire-detections", "observed", 13, _SELECTED_DATE)
    source.listing_store.write_day("burn-severity", "observed", 13, _SELECTED_DATE)
    async with agent_tools.run_context(session_provider=_session_provider(_Session([])), warehouse_source=source):
        await agent_tools.query_fire_history_near_point(longitude=-116.2, latitude=43.6, as_of=_AS_OF)

    point_statement = source.statement_for("agent_point_lane_rows")
    assert "BETWEEN ? AND ?" in point_statement, "the degree box runs before the geodesic test"
    assert "ST_Distance_Spheroid" in point_statement
    west, east, south, north = source.arguments_for("agent_point_lane_rows")[:4]
    # The box is sized per axis for this latitude; a fixed metres-per-degree figure clips its
    # east-west edges away from the equator.
    assert (east - west) > (north - south)
    assert (north - south) / 2 >= agent_tools.DEFAULT_RADIUS_METERS / 110_574.0
    # A polygon lane cannot be narrowed the same way, so it is clipped by an envelope instead.
    assert "ST_MakeEnvelope(?, ?, ?, ?)" in source.statement_for("agent_geometry_lane_rows")


async def test_every_tool_statement_is_read_only() -> None:
    """Published selection reads and retained product readers never mutate the warehouse."""
    selected_day = "2026-03-14"
    session = _Session([])
    source = _warehouse(published=[_SELECTED_DATE])
    source.listing_store.write_day("drought", "observed", 13, date(2026, 3, 10))
    source.listing_store.write_day("fire-detections", "observed", 13, _SELECTED_DATE)
    source.listing_store.write_day("burn-severity", "observed", 13, _SELECTED_DATE)
    source.listing_store.write_day("water-gauges", "observed", 13, _SELECTED_DATE)
    source.listing_store.write_day("soil-field-moisture-0-7cm", "observed", 13, _SELECTED_DATE)
    source.evidence["vegetation"] = published_lane("vegetation", [_SELECTED_DATE])
    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        await agent_tools.query_drought_history_at_point(longitude=-116.2, latitude=43.6, as_of=_AS_OF)
        await agent_tools.query_fire_history_near_point(longitude=-116.2, latitude=43.6, as_of=_AS_OF)
        await agent_tools.query_forecast_summary_for_cell(longitude=-116.2, latitude=43.6, as_of=_AS_OF)
        await agent_tools.query_observation_coverage_on_day(surface_name="vegetation", day=selected_day)
        await agent_tools.query_observation_temporal_neighbors(surface_name="vegetation", day=selected_day)
        await agent_tools.query_feature_value_near_point(
            surface_name="water-gauges", day=selected_day, longitude=-116.2, latitude=43.6
        )
        await agent_tools.query_surface_value_near_point(
            surface_name="soil-field-moisture", day=selected_day, longitude=-116.2, latitude=43.6
        )
        await agent_tools.query_surface_evidence_for_selection(
            surface_name="soil-field-vpd",
            day=selected_day,
            longitude=-116.2,
            latitude=43.6,
            range_start=selected_day,
            range_end=selected_day,
        )
        await agent_tools.list_environmental_layers.call({})
    # Both generic discovery and selection reads are exercised alongside the product readers.
    assert {"surface_evidence_for_selection", "list_environmental_layers"} <= {
        tool.name for tool in agent_tools.WAREHOUSE_TOOLS
    }
    assert session.statements == [], "environmental tools must not query retired PostgreSQL relations"
    for statement in [sql for sql, _ in source.executed] + session.statements:
        # The beginner-doc headers are prose and legitimately contain English words that
        # collide with SQL verbs ("drops the rest"); only executable lines are scanned.
        executable = "\n".join(line for line in statement.splitlines() if not line.lstrip().startswith("--")).upper()
        for verb in ("INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE", "CREATE ", "DROP", "ALTER"):
            assert verb not in executable, f"{verb} must not appear in an agent tool statement"


def test_tool_schemas_publish_bounded_arguments() -> None:
    """Every model-facing tool must advertise a bounded surface, keyed by place, layer or literature query.

    Two are not point-scoped: observation_coverage_on_day and observation_temporal_neighbors ask about
    a whole map surface on a day, so a coordinate would be a parameter they had nothing to do with.
    The three strategy-knowledge tools are coordinate-free by design -- the literature service never
    sees a location -- and are keyed instead by a length-capped query or one to five strategy ids,
    with a result cap of ten. Every tool is still keyed by SOMETHING the service validates, so none of
    them can be handed an unbounded question. `list_environmental_layers` is the one genuine
    exception: the catalogue has no spatial or surface scope to bound at all, by construction.
    """
    surface_only = {"observation_coverage_on_day", "observation_temporal_neighbors"}
    # Answers a catalogue question, not a spatial one -- no bbox, no coordinate, no surface.
    unscoped = {"list_environmental_layers"}
    literature_searches = {"search_environmental_strategies", "search_strategy_research_findings"}
    for tool in agent_tools.WAREHOUSE_TOOLS:
        definition = tool.to_dict()
        name = definition["name"]
        properties = definition["input_schema"]["properties"]
        if name == "species_information":
            assert {"species_id", "companion_limit"} == set(properties), name
        elif name in surface_only:
            assert {"surface_name", "day"} <= set(properties), name
        elif name in unscoped:
            assert properties == {}, name
        elif name in literature_searches:
            assert not {"longitude", "latitude"} & set(properties), name
            assert properties["query"]["maxLength"] == strategy_knowledge.MAX_QUERY_CHARACTERS, name
            assert properties["limit"]["maximum"] == strategy_knowledge.MAX_RESULTS, name
        elif name == "get_environmental_strategies":
            assert set(properties) == {"strategy_ids"}, name
            assert properties["strategy_ids"]["maxItems"] == strategy_knowledge.MAX_STRATEGY_IDS, name
        else:
            assert {"longitude", "latitude"} <= set(properties), name
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


def test_evidence_read_ids_match_the_typescript_optional_contract() -> None:
    """IDs are trimmed, non-null, bounded, and reserved for warehouse-origin claims."""
    payload = _report().model_dump()
    payload["riskSummary"]["evidenceReadIds"] = ["  temporal-1  "]
    parsed = RemediationReport.model_validate(payload)
    assert parsed.riskSummary.evidenceReadIds == ["temporal-1"]
    assert parsed.model_dump()["riskSummary"]["evidenceReadIds"] == ["temporal-1"]

    for invalid in (None, ["   "], ["x" * 101]):
        changed = _report().model_dump()
        changed["riskSummary"]["evidenceReadIds"] = invalid
        with pytest.raises(ValueError, match=r"evidenceReadIds|string"):
            RemediationReport.model_validate(changed)

    for origin in ("web", "model_inference"):
        changed = _report().model_dump()
        changed["riskSummary"]["evidenceOrigin"] = origin
        changed["riskSummary"]["evidenceReadIds"] = []
        with pytest.raises(ValueError, match="warehouse-origin"):
            RemediationReport.model_validate(changed)


def test_report_json_schema_is_closed() -> None:
    """Structured outputs require every object to forbid unknown properties."""
    schema = report_json_schema()
    assert schema["additionalProperties"] is False
    for definition in schema.get("$defs", {}).values():
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False


def test_report_accepts_each_governed_surface_as_claim_evidence() -> None:
    """Tool surfaces remain citable without widening the initial freshness roster."""
    for surface in AGENT_SURFACE_NAMES:
        payload = _report().model_dump()
        payload["riskSummary"]["evidenceSources"] = [surface]
        payload["observations"][0]["evidenceSource"] = surface
        assert RemediationReport.model_validate(payload).riskSummary.evidenceSources == [surface]


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
            {"tool": "surface_evidence_for_selection", "row_count": 1},
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
        system=[{"type": "text", "text": agent_graph.system_prompt()}],
        messages=[{"role": "user", "content": "No warehouse evidence resolved. Produce the briefing."}],
        output_format=RemediationReport,
        betas=[agent_graph.SERVER_SIDE_FALLBACK_BETA],
        fallbacks="default",
    )
    assert isinstance(response.parsed_output, RemediationReport)


# --- Site brief node (soil data plane, CONTRACT C3/C5) -------------------------------------------


async def test_the_brief_node_seeds_the_first_turn_and_the_literature_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SITE_BRIEF_ENABLED", "true")
    worked = _GOLDEN_BRIEF_CASES[0]

    async def golden_inputs(_request: agent_graph.AgentRequest) -> dict[str, Any]:
        return worked["inputs"]

    bound: list[strategy_knowledge.StrategyContext | None] = []

    async def record_context(_ctx: agent_graph.GraphContext, **_kwargs: Any) -> bool:
        bound.append(strategy_knowledge.current_strategy_context())
        return False

    monkeypatch.setattr(agent_graph, "read_site_brief_inputs", golden_inputs)
    monkeypatch.setattr(agent_graph, "_run_pass", record_context)
    context = _context(_client([], _parsed(None)))

    brief = await agent_graph.BUILD_SITE_BRIEF.run(context)
    await agent_graph.GATHER_WAREHOUSE_EVIDENCE.run(context)

    assert context.site_brief == brief
    assert brief["literature_seed"] == worked["expected"]["literature_seed"]
    first_turn = context.messages[0]["content"]
    assert "## Site brief (server-read, site-brief/1)" in first_turn
    assert "SoilGrids v2.0 250 m model estimate" in first_turn
    [literature] = bound
    assert literature is not None
    assert literature.user_question is None, "brief text never becomes the user's question"
    assert literature.site_brief_query == worked["expected"]["literature_seed"]
    assert literature.site_facts is not None
    assert literature.site_facts.soil_ph == worked["expected"]["soil"]["topsoil_0_30cm"]["ph"]
    assert literature.site_facts_provenance is not None
    assert literature.site_facts_provenance["soil_ph"].basis == "model_estimate"
    events = _drain(context)
    completed = next(
        event for event in events if event.get("node") == "build_site_brief" and event["status"] == "completed"
    )
    assert completed["detail"]["sections"] == dict.fromkeys(_BRIEF_SECTIONS, "available")


async def test_a_graph_walk_with_every_section_unavailable_still_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SITE_BRIEF_ENABLED", "true")
    runner = _Runner(
        [_Stream(_message(_text_block("Reading the warehouse.")))],
        ledger_entries=[
            {"tool": "surface_evidence_for_selection", "row_count": 3},
            {"tool": "drought_history_at_point", "row_count": 12},
        ],
    )
    context = _context(_client([runner], _parsed(_report())))

    outcome = await agent_graph.execute_graph(context)

    assert outcome.report is not None
    assert context.site_brief is not None
    assert context.site_brief["descriptors"] == []
    assert context.strategy_context().site_brief_query is None


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        pytest.param(agent_graph.SectionUnavailableError("lane_never_written"), "lane_never_written", id="stated"),
        pytest.param(
            ServingRefusalError("serving_at_capacity", "busy"), "serving_at_capacity", id="serving-at-capacity"
        ),
        pytest.param(ServingRefusalError("read_timed_out", "slow"), "timeout", id="serving-timeout"),
        pytest.param(AssertionError("unbound warehouse"), "read_failed", id="anything-else"),
    ],
)
async def test_a_failing_section_becomes_an_unavailable_reason(error: Exception, reason: str) -> None:
    async def failing() -> dict[str, Any]:
        raise error

    assert await agent_graph._bounded_section("soil", failing) == {"state": "unavailable", "reason": reason}


async def test_a_slow_section_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_graph, "SITE_BRIEF_SECTION_TIMEOUT_SECONDS", 0.01)

    async def slow() -> dict[str, Any]:
        await asyncio.sleep(1)
        return {"state": "available"}

    assert await agent_graph._bounded_section("weather", slow) == {"state": "unavailable", "reason": "timeout"}


async def test_the_real_reader_normalises_the_point_and_isolates_every_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing(_request: agent_graph.AgentRequest) -> dict[str, Any]:
        raise agent_graph.SectionUnavailableError("not_published")

    readers = (
        "read_soil_section",
        "read_fire_section",
        "read_drought_section",
        "read_weather_section",
        "read_land_cover_section",
    )
    for reader in readers:
        monkeypatch.setattr(agent_graph, reader, failing)
    request = agent_graph.AgentRequest(longitude=-116.2, latitude=43.6, precision="exact", as_of=_AS_OF)

    inputs = await _REAL_READ_SITE_BRIEF_INPUTS(request)

    assert inputs["built_on"] == _AS_OF.date().isoformat()
    assert inputs["point"] == {"longitude_e5": -11620000, "latitude_e5": 4360000}
    assert all(inputs[section] == {"state": "unavailable", "reason": "not_published"} for section in _BRIEF_SECTIONS)


async def test_the_drought_section_reads_the_newest_release_class(monkeypatch: pytest.MonkeyPatch) -> None:
    async def drought(*_args: Any, **_kwargs: Any) -> str:
        return json.dumps(
            {"weekly_severity": [{"valid_date": "2026-03-10", "severity_class": 1}, {"valid_date": "2026-03-03"}]}
        )

    monkeypatch.setattr(agent_graph.warehouse_tools, "query_drought_history_at_point", drought)
    request = agent_graph.AgentRequest(longitude=-116.2, latitude=43.6, precision="exact", as_of=_AS_OF)
    assert await agent_graph.read_drought_section(request) == {
        "state": "available",
        "usdm_class": "D1",
        "week_of": "2026-03-10",
    }


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"error": "parquet_lane_never_written"}, "lane_never_written"),
        ({"error": "not_available_in_region"}, "not_bound_in_region"),
        ({"error": "parquet_serving_refused", "refusal_code": "serving_at_capacity"}, "serving_at_capacity"),
        ({"weekly_severity": []}, "not_published"),
    ],
)
async def test_drought_refusals_map_onto_reasons(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any], reason: str
) -> None:
    async def drought(*_args: Any, **_kwargs: Any) -> str:
        return json.dumps(payload)

    monkeypatch.setattr(agent_graph.warehouse_tools, "query_drought_history_at_point", drought)
    request = agent_graph.AgentRequest(longitude=-116.2, latitude=43.6, precision="exact", as_of=_AS_OF)
    with pytest.raises(agent_graph.SectionUnavailableError) as raised:
        await agent_graph.read_drought_section(request)
    assert raised.value.reason == reason


def test_the_dominant_cdl_class_breaks_ties_to_the_lower_code() -> None:
    properties = {
        "class_areas_json": json.dumps({"176": 450.0, "24": 450.0, "1": 10.0}),
        "class_names_json": json.dumps({"176": "Grassland/Pasture", "24": "Winter Wheat", "1": "Corn"}),
    }
    assert agent_graph._dominant_class(properties) == (24, "Winter Wheat", 450.0)


def test_a_perimeter_counts_only_when_it_contains_the_point() -> None:
    inside = {"covers_probe_point": True, "properties": {"ignition_date": date(2025, 8, 11), "severity_class": "High"}}
    outside = {**inside, "covers_probe_point": False}
    assert agent_graph._perimeter_fire("burn-severity", inside) == (date(2025, 8, 11), "high")
    assert agent_graph._perimeter_fire("burn-severity", outside) is None


# --- Review 1 fixes: flags, follow-ups, fail-open, slot cap, reader parity -----------------------

#: sha256 of the wave-2 SYSTEM_PROMPT at HEAD 14abe742: both soil flags off must send it byte for byte.
_WAVE_TWO_SYSTEM_PROMPT_SHA256 = "d39c4000824058d26e7a59bc0eff84b1fc9290e10224895ce3f02973e17aafef"


def _follow_up_context(client: Any) -> agent_graph.GraphContext:
    return agent_graph.GraphContext(
        request=agent_graph.AgentRequest(
            longitude=-116.2,
            latitude=43.6,
            precision="approximate",
            question="What about cover crops?",
            history=(
                ConversationTurn(role="user", content="Analyze this location"),
                ConversationTurn(role="assistant", content="A saved report."),
            ),
            as_of=datetime(2026, 8, 8, tzinfo=UTC),
        ),
        client=client,
        events=asyncio.Queue(),
        session_provider=_session_provider(_Session()),
    )


async def test_both_flags_off_is_the_wave_two_graph_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review M7: no brief read, no brief event, no site facts, and the wave-2 prompt byte for byte."""
    monkeypatch.delenv("SITE_BRIEF_ENABLED", raising=False)
    monkeypatch.delenv("SOIL_PROPERTIES_READS_ENABLED", raising=False)
    reads: list[str] = []

    async def must_not_read(_request: agent_graph.AgentRequest) -> dict[str, Any]:
        reads.append("brief")
        raise AssertionError("no brief read with SITE_BRIEF_ENABLED off")

    async def record_context(_ctx: agent_graph.GraphContext, **_kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(agent_graph, "read_site_brief_inputs", must_not_read)
    monkeypatch.setattr(agent_graph, "_run_pass", record_context)
    context = _context(_client([], _parsed(None)))

    assert await agent_graph.BUILD_SITE_BRIEF.run(context) is None
    await agent_graph.GATHER_WAREHOUSE_EVIDENCE.run(context)

    assert reads == []
    assert context.site_brief is None
    assert context.site_soil is None
    assert context.strategy_context() == context.request.strategy_context()
    assert not [event for event in _drain(context) if event.get("node") == "build_site_brief"]
    request = context.request
    assert context.messages[-1]["content"] == agent_graph.build_location_context(
        longitude=request.longitude,
        latitude=request.latitude,
        precision=request.precision,
        as_of=request.as_of,
        question=request.question,
        selected_day=request.selected_day,
        species_id=request.species_id,
        map_selection=request.map_selection,
    )
    system = context.system_blocks()[0]["text"]
    assert hashlib.sha256(system.encode("utf-8")).hexdigest() == _WAVE_TWO_SYSTEM_PROMPT_SHA256
    assert "any measured site facts" in system
    assert "## Site brief and soil model estimates" not in system


@pytest.mark.parametrize("flag", ["SITE_BRIEF_ENABLED", "SOIL_PROPERTIES_READS_ENABLED"])
def test_either_soil_flag_sends_the_labelled_system_prompt(monkeypatch: pytest.MonkeyPatch, flag: str) -> None:
    monkeypatch.delenv("SITE_BRIEF_ENABLED", raising=False)
    monkeypatch.delenv("SOIL_PROPERTIES_READS_ENABLED", raising=False)
    monkeypatch.setenv(flag, "true")
    system = _context(_client([], _parsed(None))).system_blocks()[0]["text"]
    assert "each with a basis label" in system
    assert "## Site brief and soil model estimates" in system


async def test_a_follow_up_makes_only_the_soil_read_and_hands_it_to_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review M6: history replays no brief, so a follow-up gets the one soil read, labelled."""
    monkeypatch.setenv("SITE_BRIEF_ENABLED", "true")
    worked = _GOLDEN_BRIEF_CASES[0]
    reads: list[str] = []

    async def soil(_request: agent_graph.AgentRequest) -> dict[str, Any]:
        reads.append("soil")
        return worked["inputs"]["soil"]

    async def other(_request: agent_graph.AgentRequest) -> dict[str, Any]:
        reads.append("other")
        raise AssertionError("a follow-up reads soil only")

    for name in ("read_fire_section", "read_drought_section", "read_weather_section", "read_land_cover_section"):
        monkeypatch.setattr(agent_graph, name, other)
    monkeypatch.setattr(agent_graph, "read_site_brief_inputs", other)
    monkeypatch.setattr(agent_graph, "read_soil_section", soil)
    bound: list[strategy_knowledge.StrategyContext | None] = []

    async def record_context(_ctx: agent_graph.GraphContext, **_kwargs: Any) -> bool:
        bound.append(strategy_knowledge.current_strategy_context())
        return False

    monkeypatch.setattr(agent_graph, "_run_pass", record_context)
    context = _follow_up_context(_client([], _parsed(None)))

    assert await agent_graph.BUILD_SITE_BRIEF.run(context) is None
    await agent_graph.GATHER_WAREHOUSE_EVIDENCE.run(context)

    assert reads == ["soil"]
    assert context.site_brief is None
    assert context.site_soil == worked["expected"]["soil"]
    turn = context.messages[-1]["content"]
    assert "## Soil estimate (server-read, SoilGrids v2.0)" in turn
    assert "SoilGrids v2.0 250 m model estimate" in turn
    [literature] = bound
    assert literature is not None
    assert literature.user_question is not None
    assert literature.site_facts is not None
    assert literature.site_facts.soil_ph == worked["expected"]["soil"]["topsoil_0_30cm"]["ph"]
    assert literature.site_facts_provenance is not None
    assert literature.site_facts_provenance["soil_ph"].basis == "model_estimate"
    assert literature.site_brief_query is None
    completed = [event for event in _drain(context) if event.get("node") == "build_site_brief"][-1]
    assert completed["detail"] == {"follow_up": True, "sections": {"soil": "available"}}


async def test_an_unbuildable_brief_fails_open_and_the_graph_still_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review m4: an unexpected input shape never kills the graph; the run continues with no brief."""
    monkeypatch.setenv("SITE_BRIEF_ENABLED", "true")

    async def malformed(request: agent_graph.AgentRequest) -> dict[str, Any]:
        inputs = _unavailable_brief_inputs(request)
        inputs["drought"] = {"state": "available", "usdm_class": "D9", "week_of": "2026-08-04"}
        return inputs

    monkeypatch.setattr(agent_graph, "read_site_brief_inputs", malformed)
    runner = _Runner(
        [_Stream(_message(_text_block("Reading the warehouse.")))],
        ledger_entries=[{"tool": "drought_history_at_point", "row_count": 12}],
    )
    context = _context(_client([runner], _parsed(_report())))

    outcome = await agent_graph.execute_graph(context)

    assert outcome.report is not None
    assert context.site_brief is None
    assert context.strategy_context() == context.request.strategy_context()
    skipped = [event for event in _drain(context) if event.get("node") == "build_site_brief"][-1]
    assert skipped["status"] == "skipped"
    assert skipped["detail"] == {"reason": "brief_unbuildable"}


def test_a_soil_estimate_never_counts_as_a_measured_layer() -> None:
    """Review M3: soil_properties_at_point is a model estimate, so it cannot raise sufficiency."""
    ledger = [
        {"tool": "soil_properties_at_point", "row_count": 1},
        {"tool": "drought_history_at_point", "row_count": 12},
    ]
    assert agent_graph.populated_sources(ledger) == ("drought_history_at_point",)
    assert agent_graph.populated_sources(ledger[:1]) == ()


async def test_brief_reads_hold_at_most_two_serving_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review M8: the five sections gather concurrently, but only two plane reads run at once."""
    in_flight = 0
    peak = 0

    async def slotted_read(_request: agent_graph.AgentRequest) -> dict[str, Any]:
        async def read() -> dict[str, Any]:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return {"state": "unavailable", "reason": "not_published"}

        return await agent_graph._slotted(read)

    for name in (
        "read_soil_section",
        "read_fire_section",
        "read_drought_section",
        "read_weather_section",
        "read_land_cover_section",
    ):
        monkeypatch.setattr(agent_graph, name, slotted_read)
    request = agent_graph.AgentRequest(longitude=-116.2, latitude=43.6, precision="exact", as_of=_AS_OF)

    inputs = await _REAL_READ_SITE_BRIEF_INPUTS(request)

    assert peak == agent_graph.SITE_BRIEF_READ_CONCURRENCY, "five gathered sections, never more than the cap"
    assert all(inputs[section]["reason"] == "not_published" for section in _BRIEF_SECTIONS)
    assert agent_graph._BRIEF_READ_SLOTS.get() is None, "the cap never leaks past one brief"


async def test_the_fire_reads_run_concurrently_and_grade_only_the_same_mtbs_fire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review M8/M9: the three fire reads overlap; severity is the same day's MTBS class, never WFIGS's."""
    started: list[str] = []
    release = asyncio.Event()

    async def lane_features(lane: str, _request: Any, **_kwargs: Any) -> dict[str, Any]:
        started.append(lane)
        await release.wait()
        if lane == "burn-severity":
            features = [
                {"covers_probe_point": True, "properties": {"ignition_date": "2025-08-11", "severity_class": 4}},
                {"covers_probe_point": True, "properties": {"ignition_date": "2020-07-01", "severity_class": 2}},
                {"covers_probe_point": False, "properties": {"ignition_date": "2026-01-01", "severity_class": 3}},
            ]
        else:
            features = [
                {
                    "covers_probe_point": True,
                    "properties": {"fire_discovery_at": "2025-08-11T12:00:00Z", "severity": "Low"},
                },
                {"covers_probe_point": True, "properties": {"fire_discovery_at": "2027-01-01T00:00:00Z"}},
            ]
        return {"features": features, "day_state": {"state": "published"}}

    async def detections(_request: Any, _today: date) -> int:
        started.append("fire-detections")
        await release.wait()
        return 3

    monkeypatch.setattr(agent_graph, "_lane_features", lane_features)
    monkeypatch.setattr(agent_graph, "_recent_detections", detections)
    request = agent_graph.AgentRequest(longitude=-116.2, latitude=43.6, precision="exact", as_of=_AS_OF)

    section = asyncio.create_task(agent_graph.read_fire_section(request))
    for _ in range(5):
        await asyncio.sleep(0)
    assert sorted(started) == ["burn-severity", "fire-detections", "fire-perimeters"], "all three in flight"
    release.set()

    assert await section == {
        "state": "available",
        "latest_fire_day": "2025-08-11",
        "burn_severity": "high",
        "detections_last_30_days": 3,
        "source": agent_graph.FIRE_SOURCE_LABEL,
    }


async def test_a_weather_reading_missing_a_number_is_read_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review M9 (CONTRACT C5.1): the brief never invents a value; both languages refuse the reading."""

    async def lane_features(_lane: str, _request: Any, **_kwargs: Any) -> dict[str, Any]:
        reading = {"observed_at": "2026-03-14T15:00:00Z", "temperature_c": None, "relative_humidity_pct": 38}
        return {
            "day_state": {"state": "published"},
            "features": [{"distance_meters": 1200.0, "properties": reading}],
        }

    monkeypatch.setattr(agent_graph, "_lane_features", lane_features)
    request = agent_graph.AgentRequest(longitude=-116.2, latitude=43.6, precision="exact", as_of=_AS_OF)
    with pytest.raises(agent_graph.SectionUnavailableError) as raised:
        await agent_graph.read_weather_section(request)
    assert raised.value.reason == "read_failed"


async def test_a_dominant_cdl_class_without_a_name_is_read_failed_not_its_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review M9 (CONTRACT C5.1): a numeric code never becomes a literature seed."""
    cell = {
        "class_areas_json": json.dumps({"176": 500.0, "24": -5.0, "x": 900.0}),
        "class_names_json": json.dumps({"24": "Winter Wheat"}),
        "cell_area_ha": 900.0,
        "observed_year": 2025,
        "release_day": "2026-01-30",
        "aggregation_cell_m": 3000,
    }

    async def lane_features(_lane: str, _request: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"day_state": {"state": "published"}, "features": [{"covers_probe_point": True, "properties": cell}]}

    monkeypatch.setattr(agent_graph, "_lane_features", lane_features)
    assert agent_graph._dominant_class(cell) == (176, None, 500.0), "non-positive areas and non-integer codes skip"
    request = agent_graph.AgentRequest(longitude=-116.2, latitude=43.6, precision="exact", as_of=_AS_OF)
    with pytest.raises(agent_graph.SectionUnavailableError) as raised:
        await agent_graph.read_land_cover_section(request)
    assert raised.value.reason == "read_failed"


async def test_a_capped_detection_read_is_read_failed_and_no_published_day_is_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review M9: an undercount is not a count; an unpublished window adds nothing, as on the web."""

    class _Window:
        lane_written = True
        evidence_source = None

        def __init__(self, days: list[date]) -> None:
            self.days = days

        def published_days(self, _first: date, _last: date) -> list[date]:
            return self.days

        def part_keys(self, days: list[date]) -> tuple[str, ...]:
            return tuple(day.isoformat() for day in days)

    windows = [_Window([]), _Window([_AS_OF.date()])]

    async def lane_window(**_kwargs: Any) -> _Window:
        return windows.pop(0)

    async def capped_rows(*_args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"detection_count": 1}] * kwargs["row_limit"]

    monkeypatch.setattr(agent_graph.warehouse, "lane_window", lane_window)
    monkeypatch.setattr(agent_graph.warehouse_tools, "_lane_rows", capped_rows)
    request = agent_graph.AgentRequest(longitude=-116.2, latitude=43.6, precision="exact", as_of=_AS_OF)

    assert await agent_graph._recent_detections(request, _AS_OF.date()) == 0
    with pytest.raises(agent_graph.SectionUnavailableError) as raised:
        await agent_graph._recent_detections(request, _AS_OF.date())
    assert raised.value.reason == "read_failed"
