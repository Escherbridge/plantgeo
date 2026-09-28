"""The strategy-knowledge literature tools: pass-through, bounded projection, typed refusals, provenance.

No socket is opened: `strategy_knowledge.use_transport` answers every request in-process through
`httpx.MockTransport`. The two drift tests read the strategy-knowledge service's own vocabulary source
and the TypeScript report vocabulary, so a value added on either side without this one fails here.
"""

from __future__ import annotations

import ast
import asyncio
import gzip
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, get_args

import httpx
import pytest

from agri_data_service.agent import graph as agent_graph
from agri_data_service.agent import prompts as agent_prompts
from agri_data_service.agent import strategy_knowledge
from agri_data_service.agent import tools as agent_tools
from agri_data_service.agent.llm import execute_tool_call
from agri_data_service.agent.mcp_server import tool_descriptors
from agri_data_service.agent.report import (
    EvidenceOrigin,
    Observation,
    RegionalEvidenceSource,
    RegionalToolEvidenceSource,
    RemediationRecommendation,
    RemediationReport,
    RiskSummary,
)
from agri_data_service.config import Settings, settings
from agri_data_service.routes import agent_tools as bridge
from tests.agent_fakes import FakeAgentWarehouse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

REPO_ROOT = Path(__file__).resolve().parents[3]
STRATEGY_SOURCE = REPO_ROOT / "services" / "strategy-knowledge" / "src" / "strategy_knowledge"
TS_VOCABULARY = REPO_ROOT / "src" / "lib" / "regional-intelligence.ts"
PRIVATE_ORIGIN = "http://plantgeo-strategy-knowledge.railway.internal:8000"
LITERATURE_TOOLS = (
    "search_environmental_strategies",
    "get_environmental_strategies",
    "search_strategy_research_findings",
)
REFUSAL_OPENING = "This is a REFUSAL, not an absence"
CORPUS_VERSION = "c0ffee"
OK = 200
#: C3 of the frozen contract: the tool deadline and the response cap.
CONTRACT_DEADLINE_SECONDS = 8
ONE_MEBIBYTE = 1024 * 1024

STRATEGY_SEARCH: dict[str, Any] = {
    "claim_tier": "literature_grounded",
    "corpus_version": CORPUS_VERSION,
    "index_is_stale": False,
    "query": "stabilise steep burned slopes",
    "applied_filters": {"goals": ["erosion_control"]},
    "boosts": {"soil_conditions": ["steep_slope", "burned_high_severity"], "fire_phase": []},
    "site_profile": {"notes": ["slope_pct >= 30 -> boost steep_slope"]},
    "results": [
        {
            "rank": 1,
            "strategy_id": "post-fire-straw-mulching",
            "score": 0.031,
            "matched_facet": "how_to",
            "matched_facet_snippet": "Spread certified weed-free straw by hand or helicopter ...",
            "family_id": "mulching",
            "boosted_by": ["steep_slope"],
            "name": "Post-fire straw mulching",
            "summary": "Straw mulch reduces first-year hillslope erosion.",
            "category": "mulching",
            "goals": {"erosion_control": "stated"},
            "fire_phase": ["post_fire_emergency"],
            "evidence_strength": "field_trial_or_case_study",
            "evidence_rank": 4,
            "citation_count": 3,
            "review_state": "machine_extracted",
        }
    ],
    "family_diversity": {"cap": 2, "held_back_by_family": {"mulching": ["wood-strand-mulching"]}},
    "ranked_candidates": 7,
    "offset": 0,
    "next_offset": 5,
    "truncated": False,
    "candidate_pool": 200,
}

STRATEGY_RECORDS: dict[str, Any] = {
    "claim_tier": "literature_grounded",
    "corpus_version": CORPUS_VERSION,
    "index_is_stale": False,
    "strategies": [
        {
            "strategy_id": "post-fire-straw-mulching",
            "name": "Post-fire straw mulching",
            "summary": "Straw mulch reduces first-year hillslope erosion.",
            "category": "mulching",
            "family_id": "mulching",
            "goals": {"erosion_control": "stated"},
            "actions": ["Apply certified weed-free straw", "Prioritise slopes above 30 percent"],
            "application_rate": "1-2 tons per acre",
            "materials": ["straw"],
            "facets": {"overview": "A long paraphrased overview.", "how_to": "A long how-to facet."},
            "evidence_strength": "field_trial_or_case_study",
            "review_state": "machine_extracted",
            "citations": [
                {
                    "source_id": "07-usfs-baer",
                    "url": "https://source.example/baer",
                    "title": "BAER treatment effectiveness",
                    "publisher": "USFS",
                    "year": 2019,
                    "locator": "p. 4",
                    "excerpt": "straw mulch reduced sediment yield in the first year",
                },
                {
                    "source_id": "07-usfs-baer",
                    "url": "https://source.example/baer",
                    "title": "BAER treatment effectiveness",
                    "publisher": "USFS",
                    "year": 2019,
                    "locator": "p. 9",
                    "excerpt": "a second excerpt from the same source",
                },
            ],
        }
    ],
    "not_found": ["no-such-strategy"],
}

FINDING_SEARCH: dict[str, Any] = {
    "claim_tier": "literature_grounded",
    "corpus_version": CORPUS_VERSION,
    "index_is_stale": False,
    "query": "straw mulch effect on post-fire erosion",
    "applied_filters": {"strategy_ids": ["post-fire-straw-mulching"]},
    "results": [
        {
            "rank": 1,
            "score": 0.2,
            "finding_id": "f-mulch-1",
            "claim": "Straw mulch lowered first-year sediment yield on burned hillslopes.",
            "variables": [{"name": "mulch cover", "role": "driver"}],
            "direction": "decrease",
            "magnitude": "sediment yield fell 60-80%",
            "conditions": "first year after high-severity fire",
            "study_type": "field_experiment",
            "evidence_strength": "peer_reviewed_experiment",
            "goals": {"erosion_control": "stated"},
            "linked_strategy_ids": ["post-fire-straw-mulching"],
            "excerpt": "mulching reduced sediment yields by 60 to 80 percent",
            "line_start": 120,
            "line_end": 124,
            "source": {
                "source_id": "11-robichaud",
                "url": "https://source.example/robichaud",
                "title": "Post-fire mulching effectiveness",
                "publisher": "Journal of Hydrology",
                "year": 2013,
                "source_type": "peer_reviewed_study",
            },
            "review_state": "machine_extracted",
        }
    ],
    "not_found": [],
    "ranked_candidates": 1,
}


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the tools at a private-network origin; every request is still answered in-process."""
    monkeypatch.setattr(settings, "strategy_knowledge_url", PRIVATE_ORIGIN)


def answering(
    body: dict[str, Any], seen: list[httpx.Request], *, status: int = OK
) -> Callable[[httpx.Request], httpx.Response]:
    """A handler that records each request and answers `body`."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    return handler


def raising(error_type: type[httpx.TransportError]) -> Callable[[httpx.Request], httpx.Response]:
    """A handler that fails at the transport, the way a dead or unreachable service does."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise error_type("simulated transport failure", request=request)

    return handler


async def _oversized_chunks() -> AsyncIterator[bytes]:
    """A body with no Content-Length that crosses the 1 MiB budget while streaming."""
    for _ in range(3):
        yield b"x" * (strategy_knowledge.MAX_RESPONSE_BYTES // 2)


async def call(tool: Any, arguments: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run one tool inside a run context and return its decoded payload and the ledger it wrote."""
    async with agent_tools.run_context(warehouse_source=FakeAgentWarehouse()) as ledger:
        payload = json.loads(await tool.call(arguments))
    return payload, ledger


# --- Pass-through and projection ---------------------------------------------------


@pytest.mark.usefixtures("configured")
async def test_strategy_search_passes_arguments_through_and_projects_the_answer() -> None:
    seen: list[httpx.Request] = []
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(STRATEGY_SEARCH, seen))):
        payload, ledger = await call(
            agent_tools.search_environmental_strategies,
            {
                "query": "stabilise steep burned slopes",
                "goals": ["erosion_control"],
                "site_profile": {"slope_pct": 42, "burn_severity": "high"},
                "limit": 3,
            },
        )

    [request] = seen
    assert str(request.url) == f"{PRIVATE_ORIGIN}/v1/tools/search_strategies"
    assert request.headers["accept-encoding"] == "identity", "the 1 MiB cap must count wire bytes"
    assert json.loads(request.content) == {
        "query": "stabilise steep burned slopes",
        "goals": ["erosion_control"],
        "site_profile": {"slope_pct": 42.0, "burn_severity": "high"},
        "limit": 3,
    }
    [hit] = payload["results"]
    assert hit["strategy_id"] == "post-fire-straw-mulching"
    assert hit["name"] == "Post-fire straw mulching"
    assert hit["summary"] == "Straw mulch reduces first-year hillslope erosion."
    assert hit["family_id"] == "mulching"
    assert hit["goals"] == {"erosion_control": "stated"}
    assert hit["evidence_strength"] == "field_trial_or_case_study"
    assert not {"matched_facet_snippet", "score", "evidence_rank"} & set(hit)
    assert not {"family_diversity", "candidate_pool", "next_offset", "truncated", "boosts"} & set(payload)
    assert payload["cite_as"] == {"evidenceOrigin": "literature", "evidenceSource": "strategy-knowledge"}
    assert payload["claim_tier"] == "literature_grounded"
    assert payload["corpus_version"] == CORPUS_VERSION
    assert payload["result_count"] == 1
    assert "NOT a measurement" in payload["note"]
    assert ledger == [
        {
            "tool": "search_environmental_strategies",
            "row_count": 0,
            "evidence_domain": "literature_reference",
            "state": "answered",
            "result_count": 1,
            "corpus_version": CORPUS_VERSION,
        }
    ]


@pytest.mark.usefixtures("configured")
async def test_region_filter_is_forwarded_as_a_list() -> None:
    seen: list[httpx.Request] = []
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(STRATEGY_SEARCH, seen))):
        await call(
            agent_tools.search_environmental_strategies,
            {"query": "mulch", "region": ["pnw_westside", "pnw_inland"]},
        )
    [request] = seen
    assert json.loads(request.content)["region"] == ["pnw_westside", "pnw_inland"]


@pytest.mark.usefixtures("configured")
async def test_a_lone_region_string_is_normalized_to_a_one_element_list() -> None:
    """Live eval (gemini-2.5-flash-lite): every call sent `region="pnw_westside"` rather than a list,
    and was rejected outright with "region: Unexpected keyword argument" before this filter existed."""
    seen: list[httpx.Request] = []
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(FINDING_SEARCH, seen))):
        await call(
            agent_tools.search_strategy_research_findings,
            {"query": "mulch", "region": "pnw_westside"},
        )
    [request] = seen
    assert json.loads(request.content)["region"] == ["pnw_westside"]


@pytest.mark.usefixtures("configured")
async def test_strategy_records_keep_actions_and_citation_titles_and_drop_facet_bulk() -> None:
    seen: list[httpx.Request] = []
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(STRATEGY_RECORDS, seen))):
        payload, ledger = await call(
            agent_tools.get_environmental_strategies,
            {"strategy_ids": ["post-fire-straw-mulching", "no-such-strategy"]},
        )

    [request] = seen
    assert request.url.path == "/v1/tools/get_strategy"
    assert json.loads(request.content) == {"ids": ["post-fire-straw-mulching", "no-such-strategy"]}
    [record] = payload["strategies"]
    assert record["actions"] == ["Apply certified weed-free straw", "Prioritise slopes above 30 percent"]
    assert record["application_rate"] == "1-2 tons per acre"
    assert "facets" not in record
    assert record["citations"] == [
        {
            "title": "BAER treatment effectiveness",
            "url": "https://source.example/baer",
            "publisher": "USFS",
            "year": 2019,
        }
    ]
    assert payload["not_found"] == ["no-such-strategy"]
    assert ledger[0]["row_count"] == 0
    assert ledger[0]["result_count"] == 1


@pytest.mark.usefixtures("configured")
async def test_findings_keep_magnitudes_and_excerpts_verbatim_with_their_source() -> None:
    seen: list[httpx.Request] = []
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(FINDING_SEARCH, seen))):
        payload, ledger = await call(
            agent_tools.search_strategy_research_findings,
            {"query": "straw mulch effect on post-fire erosion", "strategy_id": "post-fire-straw-mulching"},
        )

    [request] = seen
    assert request.url.path == "/v1/tools/search_findings"
    assert json.loads(request.content) == {
        "query": "straw mulch effect on post-fire erosion",
        "strategy_id": "post-fire-straw-mulching",
        "limit": strategy_knowledge.DEFAULT_RESULTS,
    }
    [finding] = payload["results"]
    assert finding["magnitude"] == "sediment yield fell 60-80%"
    assert finding["excerpt"] == "mulching reduced sediment yields by 60 to 80 percent"
    assert finding["conditions"] == "first year after high-severity fire"
    assert finding["source"] == {
        "title": "Post-fire mulching effectiveness",
        "url": "https://source.example/robichaud",
        "publisher": "Journal of Hydrology",
        "year": 2013,
    }
    assert not {"line_start", "line_end", "score"} & set(finding)
    assert ledger[0]["evidence_domain"] == "literature_reference"


# --- Typed refusals ----------------------------------------------------------------


async def test_an_unconfigured_service_refuses_without_sending_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "strategy_knowledge_url", None)
    seen: list[httpx.Request] = []
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(STRATEGY_SEARCH, seen))):
        payload, ledger = await call(agent_tools.search_environmental_strategies, {"query": "mulch"})

    assert seen == []
    assert payload["error"] == strategy_knowledge.NOT_CONFIGURED
    assert payload["note"].startswith(REFUSAL_OPENING)
    assert ledger == [
        {
            "tool": "search_environmental_strategies",
            "row_count": 0,
            "evidence_domain": "literature_reference",
            "state": "refused",
            "error": strategy_knowledge.NOT_CONFIGURED,
        }
    ]


@pytest.mark.parametrize(
    ("handler", "code"),
    [
        pytest.param(
            lambda _request: httpx.Response(503, json={"error": "not_ready"}),
            strategy_knowledge.UNAVAILABLE,
            id="not-ready",
        ),
        pytest.param(
            lambda _request: httpx.Response(502, text="bad gateway"),
            strategy_knowledge.UNAVAILABLE,
            id="5xx",
        ),
        pytest.param(raising(httpx.ConnectError), strategy_knowledge.UNAVAILABLE, id="connection-refused"),
        pytest.param(raising(httpx.ReadTimeout), strategy_knowledge.UNAVAILABLE, id="read-timeout"),
        pytest.param(
            lambda _request: httpx.Response(200, content=b"x" * (strategy_knowledge.MAX_RESPONSE_BYTES + 1)),
            strategy_knowledge.UNAVAILABLE,
            id="declared-over-budget",
        ),
        pytest.param(
            lambda _request: httpx.Response(200, content=_oversized_chunks()),
            strategy_knowledge.UNAVAILABLE,
            id="streamed-over-budget",
        ),
        pytest.param(
            lambda _request: httpx.Response(200, text="not json"),
            strategy_knowledge.UNAVAILABLE,
            id="not-json",
        ),
        pytest.param(
            lambda _request: httpx.Response(400, json={"error": "invalid_arguments", "detail": "goals: bad value"}),
            strategy_knowledge.REJECTED_ARGUMENTS,
            id="rejected",
        ),
    ],
)
@pytest.mark.usefixtures("configured")
async def test_every_service_failure_is_a_typed_refusal_payload(
    handler: Callable[[httpx.Request], httpx.Response], code: str
) -> None:
    with strategy_knowledge.use_transport(httpx.MockTransport(handler)):
        payload, ledger = await call(agent_tools.search_strategy_research_findings, {"query": "mulch"})

    assert payload["error"] == code
    assert payload["note"].startswith(REFUSAL_OPENING)
    assert "results" not in payload
    assert ledger == [
        {
            "tool": "search_strategy_research_findings",
            "row_count": 0,
            "evidence_domain": "literature_reference",
            "state": "refused",
            "error": code,
        }
    ]


@pytest.mark.usefixtures("configured")
async def test_a_rejection_carries_the_service_detail() -> None:
    body = {"error": "invalid_arguments", "detail": "goals: bad value"}
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(body, [], status=400))):
        payload, _ = await call(agent_tools.search_environmental_strategies, {"query": "mulch"})
    assert payload["refusal_detail"] == "HTTP 400: invalid_arguments: goals: bad value"


@pytest.mark.usefixtures("configured")
async def test_a_compressed_answer_is_refused_unread_so_the_cap_stays_on_wire_bytes() -> None:
    """Valid gzip that would decode fine: only the Content-Encoding check can refuse it."""
    compressed = gzip.compress(json.dumps(FINDING_SEARCH).encode())

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=compressed, headers={"content-encoding": "gzip", "content-type": "application/json"}
        )

    with strategy_knowledge.use_transport(httpx.MockTransport(handler)):
        payload, ledger = await call(agent_tools.search_strategy_research_findings, {"query": "mulch"})
    assert payload["error"] == strategy_knowledge.UNAVAILABLE
    assert payload["refusal_detail"] == "the service compressed a response requested as identity"
    assert ledger[0]["state"] == "refused"


@pytest.mark.usefixtures("configured")
async def test_a_slow_service_is_cut_at_the_wall_clock_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(strategy_knowledge, "REQUEST_TIMEOUT_SECONDS", 0.05)

    async def slow(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=STRATEGY_RECORDS)

    with strategy_knowledge.use_transport(httpx.MockTransport(slow)):
        payload, _ = await call(agent_tools.get_environmental_strategies, {"strategy_ids": ["a"]})
    assert payload["error"] == strategy_knowledge.UNAVAILABLE
    assert "TimeoutError" in payload["refusal_detail"]


def test_the_request_deadline_fits_inside_the_bridge_deadline() -> None:
    assert strategy_knowledge.REQUEST_TIMEOUT_SECONDS <= CONTRACT_DEADLINE_SECONDS
    assert strategy_knowledge.REQUEST_TIMEOUT_SECONDS < bridge.TOOL_TIMEOUT_SECONDS
    assert strategy_knowledge.MAX_RESPONSE_BYTES == ONE_MEBIBYTE


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("search_environmental_strategies", {"query": "mulch", "goals": ["space_lasers"]}),
        ("search_environmental_strategies", {"query": "mulch", "limit": 11}),
        ("search_environmental_strategies", {"query": ""}),
        ("search_environmental_strategies", {"query": "mulch", "longitude": -116.2}),
        ("search_environmental_strategies", {"query": "mulch", "region": ["mars"]}),
        ("search_environmental_strategies", {"query": "mulch", "region": "mars"}),
        ("get_environmental_strategies", {"strategy_ids": []}),
        ("get_environmental_strategies", {"strategy_ids": ["a", "b", "c", "d", "e", "f"]}),
        ("search_strategy_research_findings", {"query": "mulch", "min_evidence": "vibes"}),
        ("search_strategy_research_findings", {"query": "mulch", "region": ["mars"]}),
    ],
)
@pytest.mark.usefixtures("configured")
async def test_arguments_outside_the_published_schema_never_reach_the_service(
    tool_name: str, arguments: dict[str, Any]
) -> None:
    seen: list[httpx.Request] = []
    tool = next(tool for tool in agent_tools.WAREHOUSE_TOOLS if tool.name == tool_name)
    with (
        strategy_knowledge.use_transport(httpx.MockTransport(answering(STRATEGY_SEARCH, seen))),
        pytest.raises(ValueError, match="Invalid arguments"),
    ):
        await tool.call(arguments)
    assert seen == []


@pytest.mark.usefixtures("configured")
async def test_selection_context_stuffed_into_site_profile_is_ignored_and_reported() -> None:
    """Live eval (gemini-2.5-flash-lite): Gemini stuffed selection context into site_profile.

    site_profile is advisory (boost-only, capped) in the strategy service, so this must not sink an
    otherwise-good query the way a bad FILTER does (agent/AGENTS.md, "site_profile is advisory"):
    every stuffed key is unknown to `StrategySiteProfile`, gets dropped and reported, and the call
    still reaches the service with query as its only argument.
    """
    stuffed = {
        "day": "2023-07-15",
        "latitude": 42.9,
        "longitude": -116.9,
        "range_start": "2023-07-01",
        "surface_name": "soil-field-bulk-density",
    }
    arguments = {"query": "keep crops going with less irrigation water", "site_profile": stuffed}
    seen: list[httpx.Request] = []
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(STRATEGY_SEARCH, seen))):
        answer = await execute_tool_call(
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "search_environmental_strategies", "arguments": json.dumps(arguments)},
            }
        )

    [request] = seen
    assert "site_profile" not in json.loads(request.content), "every stuffed key was unknown, so none survive"
    payload = json.loads(answer["content"])
    assert payload["tool"] == "search_environmental_strategies"
    ignored_fields = {entry["field"] for entry in payload["site_profile_ignored"]}
    assert ignored_fields == set(stuffed)
    for entry in payload["site_profile_ignored"]:
        assert "Extra inputs are not permitted" in entry["reason"]
    assert "site_profile_ignored" in payload["note"]
    for value in ("2023-07-15", "2023-07-01", "42.9", "116.9", "soil-field-bulk-density"):
        assert value not in answer["content"], value


@pytest.mark.usefixtures("configured")
async def test_an_unknown_site_profile_key_is_dropped_and_reported_without_sinking_the_call() -> None:
    """A single bad site_profile key must not reject the whole call the way a bad FILTER does."""
    seen: list[httpx.Request] = []
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(STRATEGY_SEARCH, seen))):
        payload, ledger = await call(
            agent_tools.search_environmental_strategies,
            {"query": "mulch", "site_profile": {"slope": 40, "slope_pct": 55}},
        )
    [request] = seen
    assert json.loads(request.content)["site_profile"] == {"slope_pct": 55.0}
    assert payload["site_profile_ignored"] == [{"field": "slope", "reason": "Extra inputs are not permitted"}]
    assert ledger[0]["state"] == "answered"


@pytest.mark.usefixtures("configured")
async def test_an_out_of_range_site_profile_value_is_dropped_and_reported() -> None:
    """`days_since_fire` is valid; `region` outside the enum is dropped -- one bad hint, not a lost query."""
    seen: list[httpx.Request] = []
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(STRATEGY_SEARCH, seen))):
        payload, _ = await call(
            agent_tools.search_environmental_strategies,
            {"query": "mulch", "site_profile": {"days_since_fire": 14, "region": "idaho"}},
        )
    [request] = seen
    assert json.loads(request.content)["site_profile"] == {"days_since_fire": 14}
    [entry] = payload["site_profile_ignored"]
    assert entry["field"] == "region"
    assert "idaho" not in entry["reason"]
    assert len(entry["reason"]) <= strategy_knowledge._MAX_SITE_PROFILE_REASON_CHARACTERS


def test_the_literature_descriptions_carry_the_calling_rules_without_a_system_prompt() -> None:
    """`agent ask` and MCP clients see no system prompt: the published description is all a model reads."""
    published = {schema["function"]["name"]: schema["function"] for schema in bridge.environmental_tool_schemas()}
    for name in LITERATURE_TOOLS:
        description = " ".join(published[name]["description"].split())
        assert "Needs NO date, coordinate or map layer" in description, name
        assert 'evidenceOrigin "literature" and evidenceSource "strategy-knowledge"' in description, name
        assert "NOT a measurement at this site" in description or "NOT measurements at this site" in description
    for name in ("search_environmental_strategies", "search_strategy_research_findings"):
        profile = " ".join(published[name]["parameters"]["properties"]["site_profile"]["description"].split())
        assert "MEASURED site facts only" in profile, name
        assert "Never the selected day, coordinates, a date range or a surface/layer name" in profile, name
        for field in strategy_knowledge.StrategySiteProfile.model_fields:
            assert field in profile, (name, field)


# --- Registry, sufficiency gate and bridge -----------------------------------------


def test_the_literature_tools_reach_every_agent_surface() -> None:
    registry = {tool.name for tool in agent_tools.WAREHOUSE_TOOLS}
    web_pass = {tool.name for tool in agent_graph.warehouse_tools_for_web()}
    http_bridge = {schema["function"]["name"] for schema in bridge.environmental_tool_schemas()}
    mcp = {descriptor["name"] for descriptor in tool_descriptors()}
    assert set(LITERATURE_TOOLS) <= registry & web_pass & http_bridge & mcp


def test_the_literature_tools_publish_coordinate_free_bounded_portable_schemas() -> None:
    """No `$ref`, `$defs` or `anyOf`: the live map agent forwards these schemas to Gemini via OpenRouter."""
    schemas = {tool.name: tool.to_dict()["input_schema"] for tool in agent_tools.WAREHOUSE_TOOLS}
    service_profile_fields = _model_fields(STRATEGY_SOURCE / "site_profile.py", "SiteProfile")
    for name in LITERATURE_TOOLS:
        assert not {"$ref", "$defs", "anyOf"} & _schema_keys(schemas[name]), name
    for name in ("search_environmental_strategies", "search_strategy_research_findings"):
        schema = schemas[name]
        assert not {"longitude", "latitude"} & set(schema["properties"]), name
        assert schema["required"] == ["query"], name
        assert schema["properties"]["goals"]["items"]["enum"] == list(get_args(strategy_knowledge.Goal)), name
        assert schema["properties"]["region"]["items"]["enum"] == list(get_args(strategy_knowledge.Region)), name
        site_profile = schema["properties"]["site_profile"]
        assert site_profile["type"] == "object", name
        assert list(site_profile["properties"]) == service_profile_fields, name
        assert site_profile["additionalProperties"] is False, name
        assert site_profile["description"], name
    assert list(strategy_knowledge.StrategySiteProfile.model_fields) == service_profile_fields
    assert schemas["get_environmental_strategies"]["properties"]["strategy_ids"]["minItems"] == 1


def test_numeric_site_codes_are_sent_as_the_strings_the_service_parses() -> None:
    profile = strategy_knowledge.StrategySiteProfile.model_validate({"land_cover": 82, "burn_severity": 4})
    arguments = strategy_knowledge.service_arguments(site_profile=profile, goals=None, strategy_ids=[])
    assert arguments == {"site_profile": {"burn_severity": "4", "land_cover": "82"}}


def test_literature_ledger_rows_never_count_as_measured_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    """Metadata-exempt by NAME, so even a non-zero row_count could not open or close the web gate."""
    monkeypatch.delenv("SOIL_PROPERTIES_READS_ENABLED", raising=False)
    ledger = tuple(
        {"tool": name, "row_count": 3, "evidence_domain": "literature_reference", "state": "answered"}
        for name in LITERATURE_TOOLS
    )
    assert agent_graph.populated_sources(ledger) == ()
    evidence = agent_graph.WarehouseEvidence(tool_calls=ledger, populated_tools=(), refused=False)
    verdict = agent_graph.AssessSufficiency.decide(evidence, has_question=True)
    assert verdict.warehouse_is_sufficient is False
    assert verdict.searches_allowed == agent_graph.MAX_SEARCHES_PER_REQUEST
    assert frozenset(LITERATURE_TOOLS) == agent_graph.LITERATURE_TOOLS
    location_tools = {tool.name for tool in agent_tools.published_warehouse_tools()} - {
        "species_information",
        *LITERATURE_TOOLS,
    }
    assert verdict.coverage["tools_available"] == len(location_tools), "literature is never coverage"


async def test_the_bridge_returns_a_refusal_as_ordinary_tool_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "strategy_knowledge_url", None)
    payload = {"name": "search_environmental_strategies", "arguments": {"query": "mulch"}}
    response = await bridge.call_agent_tool(SimpleNamespace(json=payload, body=json.dumps(payload).encode()))
    assert response.status == OK
    body = json.loads(response.body)
    assert body["tool"] == "search_environmental_strategies"
    assert body["result"]["error"] == strategy_knowledge.NOT_CONFIGURED


# --- Configuration -----------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [PRIVATE_ORIGIN, "https://x.example", "https://x.example/", "http://localhost:8000", "http://127.0.0.1:8000"],
)
def test_strategy_knowledge_url_accepts_a_private_or_https_origin(url: str) -> None:
    assert Settings(_env_file=None, strategy_knowledge_url=url).strategy_knowledge_url == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "http://x.railway.internal.evil.example",
        "https://user:secret@x.example",
        "https://x.example/v1",
        "https://x.example/?q=1",
        "https://x.example/#top",
        "https://x.example:notaport",
        "ftp://x.example",
        # urlsplit drops tab/CR/LF silently, so each of these would validate and 500 at call time in httpx.
        "http://loc\nalhost:8000",
        "http://localhost:\t8000",
        "http://localhost:8000\r\n",
        "https://x.exa\x00mple",
        "http://local host:8000",
    ],
)
def test_strategy_knowledge_url_rejects_anything_but_a_safe_origin(url: str) -> None:
    with pytest.raises(ValueError, match="STRATEGY_KNOWLEDGE_URL"):
        Settings(_env_file=None, strategy_knowledge_url=url)


def test_a_blank_strategy_knowledge_url_is_unset() -> None:
    assert Settings(_env_file=None, strategy_knowledge_url="  ").strategy_knowledge_url is None


# --- Provenance vocabulary ---------------------------------------------------------


def test_literature_is_a_report_origin_and_strategy_knowledge_a_citable_source() -> None:
    assert get_args(EvidenceOrigin) == ("warehouse", "web", "literature", "model_inference")
    assert strategy_knowledge.LITERATURE_EVIDENCE_ORIGIN in get_args(EvidenceOrigin)
    assert strategy_knowledge.STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE in get_args(RegionalToolEvidenceSource)
    claim = {
        "evidenceOrigin": "literature",
        "statement": "Straw mulch is reported to reduce first-year erosion on burned slopes.",
        "evidenceSource": "strategy-knowledge",
    }
    assert Observation.model_validate(claim).evidenceOrigin == "literature"
    with pytest.raises(ValueError, match="warehouse-origin"):
        Observation.model_validate({**claim, "evidenceReadIds": ["read-1"]})


_OBSERVATION: dict[str, Any] = {"statement": "Straw mulch is reported to reduce first-year erosion on burned slopes."}
_RECOMMENDATION: dict[str, Any] = {
    "strategy": "erosion_control",
    "title": "Mulch the steepest burned slopes",
    "rationale": "Cited field trials report lower first-year sediment yield.",
    "timeframe": "immediate",
    "confidence": "moderate",
    "consultProfessionals": ["hydrologist"],
}


@pytest.mark.parametrize(
    ("claim_type", "base"),
    [(Observation, _OBSERVATION), (RemediationRecommendation, _RECOMMENDATION)],
    ids=["observation", "recommendation"],
)
def test_literature_and_strategy_knowledge_only_travel_together(claim_type: Any, base: dict[str, Any]) -> None:
    """Genuine conflicts still reject: a literature claim naming a real other source, and a
    non-literature origin naming strategy-knowledge on a claim `_pair_literature_provenance` never
    touches (warehouse cannot be normalised into literature)."""
    paired = claim_type.model_validate({**base, "evidenceOrigin": "literature", "evidenceSource": "strategy-knowledge"})
    assert (paired.evidenceOrigin, paired.evidenceSource) == ("literature", "strategy-knowledge")
    for source in ("drought", "soil-survey"):
        claim = {**base, "evidenceOrigin": "literature", "evidenceSource": source}
        with pytest.raises(ValueError, match='requires evidenceSource "strategy-knowledge"'):
            claim_type.model_validate(claim)
    with pytest.raises(ValueError, match='allowed only with evidenceOrigin "literature"'):
        claim_type.model_validate({**base, "evidenceOrigin": "warehouse", "evidenceSource": "strategy-knowledge"})


@pytest.mark.parametrize(
    ("claim_type", "base"),
    [(Observation, _OBSERVATION), (RemediationRecommendation, _RECOMMENDATION)],
    ids=["observation", "recommendation"],
)
def test_literature_provenance_is_normalised_before_the_strict_check(claim_type: Any, base: dict[str, Any]) -> None:
    """Mirrors `pairLiteratureProvenance` in remediation-report.ts: fill a missing source on a
    literature claim, strip a mistaken one off web/model_inference, never reject either direction."""
    for mislabelled in (
        {"evidenceOrigin": "literature"},
        {"evidenceOrigin": "literature", "evidenceSource": None},
        {"evidenceOrigin": "literature", "evidenceSource": ""},
    ):
        claim = claim_type.model_validate({**base, **mislabelled})
        assert (claim.evidenceOrigin, claim.evidenceSource) == ("literature", "strategy-knowledge")
    for origin in ("web", "model_inference"):
        claim = claim_type.model_validate({**base, "evidenceOrigin": origin, "evidenceSource": "strategy-knowledge"})
        assert claim.evidenceOrigin == origin
        assert claim.evidenceSource is None


def test_a_report_with_one_mislabelled_literature_claim_still_parses() -> None:
    """The graph has no correction round (agent/AGENTS.md, "Literature/strategy-knowledge
    normalisation"): a single mislabelled claim must not fail the whole `RemediationReport` parse."""
    payload: dict[str, Any] = {
        "riskSummary": {
            "level": "moderate",
            "headline": "Burned slopes above a reservoir.",
            "factors": ["high burn severity"],
            "evidenceOrigin": "model_inference",
            "evidenceSources": [],
        },
        "observations": [
            {**_OBSERVATION, "evidenceOrigin": "warehouse", "evidenceSource": "soil-phh2o"},
        ],
        "remediation": [
            # Mislabelled: literature with no evidenceSource at all.
            {**_RECOMMENDATION, "evidenceOrigin": "literature"},
        ],
        "professionalConsultation": "Consult a hydrologist before acting.",
    }
    report = RemediationReport.model_validate(payload)
    [item] = report.remediation
    assert (item.evidenceOrigin, item.evidenceSource) == ("literature", "strategy-knowledge")


def test_literature_never_carries_the_risk_summary() -> None:
    """Matches remediation-report.ts: the risk judgement is interpretation, never literature."""
    risk: dict[str, Any] = {
        "level": "moderate",
        "headline": "Burned slopes above a reservoir.",
        "factors": ["high burn severity"],
        "evidenceOrigin": "model_inference",
        "evidenceSources": [],
    }
    assert RiskSummary.model_validate(risk).evidenceOrigin == "model_inference"
    with pytest.raises(ValueError, match="riskSummary cannot be literature"):
        RiskSummary.model_validate({**risk, "evidenceOrigin": "literature"})
    with pytest.raises(ValueError, match="riskSummary cannot be literature"):
        RiskSummary.model_validate({**risk, "evidenceSources": ["drought", "strategy-knowledge"]})


def test_the_typescript_vocabulary_matches_the_python_projection() -> None:
    """regional-intelligence.ts is the single definition; the Python Literals must EQUAL it, not overlap it."""
    assert _typescript_array("EVIDENCE_ORIGINS") == get_args(EvidenceOrigin)
    typescript_tool_sources = _typescript_array("REGIONAL_TOOL_EVIDENCE_SOURCES")
    python_tool_sources = get_args(RegionalToolEvidenceSource)
    assert len(set(python_tool_sources)) == len(python_tool_sources), "duplicate Python tool source"
    assert set(typescript_tool_sources) == set(python_tool_sources)
    assert set(_typescript_array("REGIONAL_EVIDENCE_SOURCES")) == set(get_args(RegionalEvidenceSource))


@pytest.mark.parametrize("alias", ["Goal", "FirePhase", "LandUse", "Region", "EvidenceStrength"])
def test_filter_vocabulary_matches_the_strategy_service(alias: str) -> None:
    expected = _literal_values(STRATEGY_SOURCE / "vocabulary.py", alias)
    assert get_args(getattr(strategy_knowledge, alias)) == expected


def _typescript_array(name: str) -> tuple[str, ...]:
    """The string members of one `export const NAME = [...] as const;` array."""
    source = TS_VOCABULARY.read_text(encoding="utf-8")
    block = re.search(rf"export const {name} = \[(.*?)\] as const;", source, re.DOTALL)
    assert block, f"{name} moved or was renamed in regional-intelligence.ts"
    return tuple(re.findall(r'"([^"]+)"', block.group(1)))


def _literal_values(module: Path, alias: str) -> tuple[str, ...]:
    """The members of one module-level `Alias = Literal[...]` assignment, read without importing it."""
    for node in ast.parse(module.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and alias in _assigned_names(node):
            assert isinstance(node.value, ast.Subscript), alias
            members = node.value.slice
            elements = members.elts if isinstance(members, ast.Tuple) else [members]
            return tuple(ast.literal_eval(element) for element in elements)
    raise AssertionError(f"{alias} is no longer a module-level Literal in {module.name}")


def _assigned_names(node: ast.Assign) -> set[str]:
    """The plain names one assignment binds."""
    return {target.id for target in node.targets if isinstance(target, ast.Name)}


def _schema_keys(node: Any) -> set[str]:
    """Every key used anywhere in a JSON Schema."""
    if isinstance(node, list):
        return set().union(*(_schema_keys(item) for item in node))
    if isinstance(node, dict):
        return set(node).union(*(_schema_keys(value) for value in node.values()))
    return set()


def _model_fields(module: Path, class_name: str) -> list[str]:
    """The annotated field names of one class, in declaration order, read without importing it."""
    for node in ast.parse(module.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return [
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
            ]
    raise AssertionError(f"{class_name} is no longer defined in {module.name}")


# --- Server-owned site context (seams S1/S2) -----------------------------------------

WESTERN_WA = (-122.95, 46.66)
"""`western-wa-sour-pasture` in scripts/agent_strategy_eval_scenarios.json."""
BOISE_FOOTHILLS = (-116.13, 43.66)
"""`boise-foothills-post-fire` in scripts/agent_strategy_eval_scenarios.json."""
PACIFIC_OCEAN = (-130.0, 45.0)
USER_QUESTION = "my pasture has gone sour\nwhat can I do about it?"
SERVER_SOIL_PH = 5.2
EXPECTED_DROPPED = ["slope_pct", "soil_ph", "region(argument)"]
REQUEST_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
CONCURRENT_CALLS = 10
EXCLUDED_SITE_FACT_KEYS = frozenset({"slope_pct", "region"})


def _context(
    point: tuple[float, float] | None = BOISE_FOOTHILLS,
    *,
    user_question: str | None = USER_QUESTION,
    site_facts: dict[str, Any] | None = None,
) -> strategy_knowledge.StrategyContext:
    return strategy_knowledge.StrategyContext(
        user_question=user_question,
        longitude=point[0] if point else None,
        latitude=point[1] if point else None,
        site_facts=strategy_knowledge.SiteFacts.model_validate(site_facts) if site_facts else None,
    )


async def call_with_context(
    tool: Any, arguments: dict[str, Any], context: strategy_knowledge.StrategyContext
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """`call`, with a server context bound for the run the way the graph and the eval harness bind it."""
    async with agent_tools.run_context(warehouse_source=FakeAgentWarehouse(), strategy_context=context) as ledger:
        payload = json.loads(await tool.call(arguments))
    return payload, ledger


@pytest.mark.parametrize(
    ("point", "region"),
    [
        pytest.param(WESTERN_WA, "pnw_westside", id="western-wa-eval-point"),
        pytest.param(BOISE_FOOTHILLS, "great_basin_high_desert", id="boise-foothills-eval-point"),
        pytest.param(PACIFIC_OCEAN, None, id="pacific-ocean"),
        pytest.param((-40.0, 30.0), None, id="atlantic-ocean"),
        pytest.param((-157.86, 21.31), None, id="honolulu-unlabelled"),
        pytest.param((-121.3, 47.0), None, id="on-the-cascade-crest"),
        pytest.param((-115.47, 32.63), None, id="mexicali-border-strip"),
        pytest.param((-82.5, 45.8), None, id="manitoulin-island-border-strip"),
        pytest.param((-68.3, 47.37), None, id="st-john-river-border-strip"),
        pytest.param((-122.33, 47.61), "pnw_westside", id="seattle"),
        pytest.param((-122.68, 45.52), "pnw_westside", id="portland"),
        pytest.param((-117.43, 47.66), "pnw_inland", id="spokane"),
        pytest.param((-117.18, 46.73), "pnw_inland", id="palouse-eval-point"),
        pytest.param((-121.31, 44.06), "pnw_inland", id="bend"),
        pytest.param((-116.9, 42.9), "great_basin_high_desert", id="owyhee-eval-point"),
        pytest.param((-111.89, 40.76), "great_basin_high_desert", id="salt-lake-city"),
        pytest.param((-114.0, 46.87), "northern_rockies", id="missoula"),
        pytest.param((-104.99, 39.74), "southern_rockies", id="denver"),
        pytest.param((-119.79, 36.74), "california", id="fresno"),
        pytest.param((-112.07, 33.45), "southwest", id="phoenix"),
        pytest.param((-96.8, 32.78), "great_plains_texas", id="dallas"),
        pytest.param((-93.6, 41.59), "us_midwest", id="des-moines"),
        pytest.param((-84.39, 33.75), "us_southeast", id="atlanta"),
        pytest.param((-74.0, 40.71), "us_northeast", id="new-york"),
        pytest.param((-149.9, 61.2), "alaska", id="anchorage"),
        pytest.param((-114.07, 51.05), "canada", id="calgary"),
        pytest.param((-99.13, 19.43), "latin_america", id="mexico-city"),
        pytest.param((2.35, 48.86), "europe", id="paris"),
        pytest.param((36.82, -1.29), "africa", id="nairobi"),
        pytest.param((151.21, -33.87), "oceania", id="sydney"),
        pytest.param((116.4, 39.9), "asia", id="beijing"),
    ],
)
def test_the_region_table_labels_a_point_or_declines(point: tuple[float, float], region: str | None) -> None:
    assert strategy_knowledge.derive_region(*point) == region


@pytest.mark.parametrize("point", [(float("nan"), 43.6), (-116.2, float("nan")), (200.0, 43.6), (-116.2, 95.0)])
def test_a_point_off_the_globe_has_no_region(point: tuple[float, float]) -> None:
    assert strategy_knowledge.derive_region(*point) is None


def test_the_region_table_only_names_specific_published_regions() -> None:
    """The service's filter already admits the broad values; a box naming one would match everything."""
    published = set(get_args(strategy_knowledge.Region))
    named = {box.region for box in strategy_knowledge.REGION_BOXES if box.region is not None}
    assert named <= published
    assert not named & {"north_america_general", "global", "general"}
    for box in strategy_knowledge.REGION_BOXES:
        assert box.west < box.east, box.area
        assert box.south < box.north, box.area


def test_every_ambiguity_band_precedes_every_labelled_box() -> None:
    """First match wins, so a band listed after a labelled box it overlaps would never apply."""
    regions = [box.region for box in strategy_knowledge.REGION_BOXES]
    first_labelled = next(index for index, region in enumerate(regions) if region is not None)
    assert all(region is not None for region in regions[first_labelled:])


def test_site_facts_mirror_the_service_profile_minus_slope_and_region() -> None:
    service_fields = _model_fields(STRATEGY_SOURCE / "site_profile.py", "SiteProfile")
    expected = [field for field in service_fields if field not in EXCLUDED_SITE_FACT_KEYS]
    assert list(strategy_knowledge.SiteFacts.model_fields) == expected


@pytest.mark.parametrize("key", sorted(EXCLUDED_SITE_FACT_KEYS))
def test_site_facts_refuse_slope_and_region(key: str) -> None:
    """No slope surface exists, and region is derived from the point, never supplied."""
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        strategy_knowledge.SiteFacts.model_validate({key: "pnw_inland" if key == "region" else 30})


def test_the_user_question_is_cleaned_and_front_truncated() -> None:
    latest = "what now?"
    padded = "x" * strategy_knowledge.MAX_USER_QUESTION_CHARACTERS + "\n" + latest
    context = strategy_knowledge.StrategyContext(user_question=padded)
    assert context.user_question is not None
    assert len(context.user_question) <= strategy_knowledge.MAX_USER_QUESTION_CHARACTERS
    assert context.user_question.endswith(latest), "the latest turn is last and must survive truncation"
    cleaned = strategy_knowledge.StrategyContext(user_question="sour\x00 pasture\x1b\nhelp\t")
    assert cleaned.user_question == "sour pasture\nhelp"
    assert strategy_knowledge.StrategyContext(user_question=" \x07 ").user_question is None


def test_a_context_point_is_both_halves_or_neither() -> None:
    with pytest.raises(ValueError, match="longitude and latitude must be given together"):
        strategy_knowledge.StrategyContext(longitude=-116.2)


def test_the_bridge_wire_shape_maps_onto_the_python_context() -> None:
    wire = strategy_knowledge.ServerContext.model_validate(
        {
            "user_question": USER_QUESTION,
            "point": {"longitude": -116.2, "latitude": 43.6},
            "site_facts": {"soil_ph": SERVER_SOIL_PH},
        }
    )
    context = wire.strategy_context()
    assert (context.longitude, context.latitude) == (-116.2, 43.6)
    assert context.user_question == USER_QUESTION
    assert context.site_facts == strategy_knowledge.SiteFacts(soil_ph=SERVER_SOIL_PH)


@pytest.mark.usefixtures("configured")
async def test_a_server_context_replaces_the_model_site_profile_and_region() -> None:
    seen: list[httpx.Request] = []
    context = _context(site_facts={"soil_ph": SERVER_SOIL_PH, "burn_severity": "high"})
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(STRATEGY_SEARCH, seen))):
        payload, ledger = await call_with_context(
            agent_tools.search_environmental_strategies,
            {
                "query": "fix acidic pasture",
                "region": "pnw_inland",
                "site_profile": {"slope_pct": 50, "soil_ph": 9.0},
            },
            context,
        )

    [request] = seen
    body = json.loads(request.content)
    assert body["site_profile"] == {
        "soil_ph": SERVER_SOIL_PH,
        "burn_severity": "high",
        "region": "great_basin_high_desert",
    }
    assert "region" not in body, "the model's top-level region filter is discarded"
    assert body["context_query"] == USER_QUESTION
    assert "longitude" not in request.content.decode(), "the service still never sees a coordinate"
    assert "-116.13" not in request.content.decode()
    assert payload["site_profile_source"] == "server"
    assert payload["site_profile_dropped"] == EXPECTED_DROPPED
    assert "site_profile_ignored" not in payload
    assert "site_profile_source 'server'" in payload["note"]
    assert ledger[0]["result_count"] == 1, "the ledger keeps backing the literature gate"


@pytest.mark.usefixtures("configured")
async def test_a_server_context_over_the_ocean_sends_no_site_profile() -> None:
    seen: list[httpx.Request] = []
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(FINDING_SEARCH, seen))):
        payload, _ = await call_with_context(
            agent_tools.search_strategy_research_findings,
            {"query": "mulch"},
            _context(PACIFIC_OCEAN, user_question=None),
        )
    [request] = seen
    body = json.loads(request.content)
    assert "site_profile" not in body
    assert "context_query" not in body, "an empty question is never forwarded"
    assert payload["site_profile_source"] == "server"
    assert payload["site_profile_dropped"] == []


@pytest.mark.usefixtures("configured")
async def test_findings_search_forwards_the_verbatim_question() -> None:
    seen: list[httpx.Request] = []
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(FINDING_SEARCH, seen))):
        await call_with_context(
            agent_tools.search_strategy_research_findings, {"query": "lime rate"}, _context(WESTERN_WA)
        )
    [request] = seen
    body = json.loads(request.content)
    assert body["context_query"] == USER_QUESTION
    assert body["query"] == "lime rate", "the model's own query is never rewritten"
    assert body["site_profile"] == {"region": "pnw_westside"}


@pytest.mark.usefixtures("configured")
async def test_get_never_forwards_the_question_but_still_carries_the_labels() -> None:
    seen: list[httpx.Request] = []
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(STRATEGY_RECORDS, seen))):
        payload, _ = await call_with_context(
            agent_tools.get_environmental_strategies, {"strategy_ids": ["post-fire-straw-mulching"]}, _context()
        )
    [request] = seen
    assert json.loads(request.content) == {"ids": ["post-fire-straw-mulching"]}
    assert payload["site_profile_source"] == "server"
    assert payload["site_profile_dropped"] == []


@pytest.mark.parametrize(
    ("arguments", "source"),
    [
        pytest.param({"query": "mulch", "site_profile": {"soil_ph": 6.0}}, "caller_asserted", id="asserted"),
        pytest.param({"query": "mulch"}, "none", id="no-profile"),
        pytest.param({"query": "mulch", "site_profile": {}}, "none", id="empty-profile"),
    ],
)
@pytest.mark.usefixtures("configured")
async def test_without_a_server_context_the_model_profile_is_labelled(arguments: dict[str, Any], source: str) -> None:
    seen: list[httpx.Request] = []
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(STRATEGY_SEARCH, seen))):
        payload, _ = await call(agent_tools.search_environmental_strategies, arguments)
    [request] = seen
    assert "context_query" not in json.loads(request.content)
    assert payload["site_profile_source"] == source
    assert "site_profile_dropped" not in payload


@pytest.mark.usefixtures("configured")
async def test_a_refusal_under_a_server_context_still_carries_the_labels() -> None:
    body = {"error": "invalid_arguments", "detail": "context_query: extra"}
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(body, [], status=400))):
        payload, ledger = await call_with_context(
            agent_tools.search_environmental_strategies, {"query": "mulch"}, _context()
        )
    assert payload["error"] == strategy_knowledge.REJECTED_ARGUMENTS
    assert payload["site_profile_source"] == "server"
    assert ledger[0]["state"] == "refused"


@pytest.mark.usefixtures("configured")
async def test_a_zero_record_answer_under_a_server_context_still_backs_nothing() -> None:
    empty = {**STRATEGY_SEARCH, "results": []}
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(empty, []))):
        payload, ledger = await call_with_context(
            agent_tools.search_environmental_strategies, {"query": "mulch"}, _context()
        )
    assert payload["result_count"] == 0
    assert ledger[0]["result_count"] == 0
    assert agent_graph.literature_answered(ledger) is False


async def test_the_context_is_unbound_after_the_run() -> None:
    async with agent_tools.run_context(warehouse_source=FakeAgentWarehouse(), strategy_context=_context()):
        assert strategy_knowledge.current_strategy_context() == _context()
    assert strategy_knowledge.current_strategy_context() is None


def test_the_model_facing_schemas_never_gain_the_server_context() -> None:
    """S2: the context stays out of band; a model can neither see nor set it."""
    schemas = {tool.name: tool.to_dict()["input_schema"] for tool in agent_tools.WAREHOUSE_TOOLS}
    for name in LITERATURE_TOOLS:
        assert not {"context_query", "server_context", "point", "site_facts"} & _schema_keys(schemas[name]), name


# --- Lone-string list filters (B1) -------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "arguments", "field", "expected"),
    [
        ("search_environmental_strategies", {"query": "q", "goals": "soil_health"}, "goals", ["soil_health"]),
        ("search_environmental_strategies", {"query": "q", "land_use": "pasture"}, "land_use", ["pasture"]),
        (
            "search_environmental_strategies",
            {"query": "q", "fire_phase": "post_fire_recovery"},
            "fire_phase",
            ["post_fire_recovery"],
        ),
        ("search_environmental_strategies", {"query": "q", "region": "pnw_inland"}, "region", ["pnw_inland"]),
        ("search_strategy_research_findings", {"query": "q", "goals": "erosion_control"}, "goals", ["erosion_control"]),
        ("get_environmental_strategies", {"strategy_ids": "post-fire-mulching"}, "ids", ["post-fire-mulching"]),
    ],
)
@pytest.mark.usefixtures("configured")
async def test_every_list_filter_accepts_a_lone_string(
    tool_name: str, arguments: dict[str, Any], field: str, expected: list[str]
) -> None:
    seen: list[httpx.Request] = []
    tool = next(tool for tool in agent_tools.WAREHOUSE_TOOLS if tool.name == tool_name)
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(STRATEGY_SEARCH, seen))):
        await call(tool, arguments)
    [request] = seen
    assert json.loads(request.content)[field] == expected


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("search_environmental_strategies", {"query": "q", "goals": "space_lasers"}),
        ("search_environmental_strategies", {"query": "q", "land_use": "moon"}),
        ("search_strategy_research_findings", {"query": "q", "goals": "vibes"}),
        ("get_environmental_strategies", {"strategy_ids": ""}),
    ],
)
@pytest.mark.usefixtures("configured")
async def test_a_bad_lone_string_is_still_rejected(tool_name: str, arguments: dict[str, Any]) -> None:
    seen: list[httpx.Request] = []
    tool = next(tool for tool in agent_tools.WAREHOUSE_TOOLS if tool.name == tool_name)
    with (
        strategy_knowledge.use_transport(httpx.MockTransport(answering(STRATEGY_SEARCH, seen))),
        pytest.raises(ValueError, match="Invalid arguments"),
    ):
        await tool.call(arguments)
    assert seen == []


# --- Observability and concurrency (P3, P5) ----------------------------------------


class _RecordingLogger:
    """Stands in for the module's structlog logger; the app caches loggers, so capture_logs is unreliable."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def info(self, event: str, **fields: Any) -> None:
        self.events.append({"event": event, **fields})


@pytest.mark.usefixtures("configured")
async def test_each_call_sends_a_fresh_request_id_and_logs_one_event(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _RecordingLogger()
    monkeypatch.setattr(strategy_knowledge, "logger", recorder)
    seen: list[httpx.Request] = []
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(FINDING_SEARCH, seen))):
        await call(agent_tools.search_strategy_research_findings, {"query": "mulch"})
        await call(agent_tools.search_strategy_research_findings, {"query": "mulch"})

    identifiers = [request.headers["x-request-id"] for request in seen]
    assert all(REQUEST_ID_PATTERN.fullmatch(identifier) for identifier in identifiers)
    assert len(set(identifiers)) == len(identifiers)
    assert all(request.headers["accept-encoding"] == "identity" for request in seen)
    assert [event["request_id"] for event in recorder.events] == identifiers
    for event in recorder.events:
        assert event["event"] == "literature_call"
        assert event["tool"] == "search_strategy_research_findings"
        assert event["status"] == "answered"
        assert event["result_count"] == 1
        assert event["ms"] >= 0


async def test_a_refusal_logs_its_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "strategy_knowledge_url", None)
    recorder = _RecordingLogger()
    monkeypatch.setattr(strategy_knowledge, "logger", recorder)
    await call(agent_tools.search_environmental_strategies, {"query": "mulch"})
    [event] = recorder.events
    assert event["status"] == strategy_knowledge.NOT_CONFIGURED
    assert event["request_id"] is None
    assert event["result_count"] is None


@pytest.mark.usefixtures("configured")
async def test_the_service_concurrency_cap_is_a_typed_unavailable_refusal() -> None:
    """strategy-knowledge answers a plain-text 503 above its uvicorn `limit_concurrency`."""

    def over_capacity(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    with strategy_knowledge.use_transport(httpx.MockTransport(over_capacity)):
        payload, ledger = await call(agent_tools.search_environmental_strategies, {"query": "mulch"})
    assert payload["error"] == strategy_knowledge.UNAVAILABLE
    assert payload["refusal_detail"] == "HTTP 503"
    assert ledger[0]["state"] == "refused"


def _peak_tracking_transport() -> tuple[httpx.MockTransport, list[int]]:
    """A slow transport that records how many requests it held at once, per request."""
    in_flight = [0]
    peaks: list[int] = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        in_flight[0] += 1
        peaks.append(in_flight[0])
        await asyncio.sleep(0.01)
        in_flight[0] -= 1
        return httpx.Response(200, json=FINDING_SEARCH)

    return httpx.MockTransport(handler), peaks


async def _burst() -> list[strategy_knowledge.StrategyAnswer]:
    return await asyncio.gather(
        *(
            strategy_knowledge.ask("search_strategy_research_findings", "search_findings", {"query": "mulch"})
            for _ in range(CONCURRENT_CALLS)
        )
    )


@pytest.mark.usefixtures("configured")
async def test_concurrent_calls_are_capped() -> None:
    transport, peaks = _peak_tracking_transport()
    with strategy_knowledge.use_transport(transport):
        answers = await _burst()
    assert max(peaks) == strategy_knowledge.MAX_CONCURRENT_CALLS
    assert all(answer.ledger_detail["state"] == "answered" for answer in answers)


@pytest.mark.usefixtures("configured")
def test_the_concurrency_cap_is_safe_across_event_loops() -> None:
    """A Semaphore binds to the loop it first waits on; one shared across loops raises under contention."""
    transport, peaks = _peak_tracking_transport()
    with strategy_knowledge.use_transport(transport):
        for _ in range(2):
            answers = asyncio.run(_burst())
            assert all(answer.ledger_detail["state"] == "answered" for answer in answers)
    assert max(peaks) == strategy_knowledge.MAX_CONCURRENT_CALLS


# --- Soil data plane C3/C4 delta: provenance-aware site facts and the brief seed -----------------

BRIEF_SEED = "moderately acid loam topsoil; high organic carbon; moderate drought; grassland/pasture"
BRIEF_SOIL_PH = 5.8
BRIEF_DAYS_SINCE_FIRE = 412
SOIL_LABEL = "SoilGrids v2.0 250 m model estimate, 0-30 cm (thickness-weighted), cell centre 140 m away"
SOIL_PROVENANCE: dict[str, Any] = {
    "basis": "model_estimate",
    "source": "SoilGrids v2.0",
    "release_id": "soilgrids-v2.0/2020-06-02",
    "depth": "0-30 cm (thickness-weighted)",
    "resolution_m": 250,
    "distance_m": 140,
    "label": SOIL_LABEL,
}
FIRE_PROVENANCE: dict[str, Any] = {
    "basis": "measured",
    "source": "fire-perimeters + burn-severity (MTBS)",
    "label": "Mapped fire perimeter and MTBS burn severity, burned 2025-08-11",
}
QUERY_INTENT: dict[str, Any] = {
    "lay_terms": ["sour"],
    "ph_direction": "raise",
    "soil_condition_boosts": ["acidic"],
    "expansion_tokens": ["lime"],
}
SOIL_DISTANCE_CEILING = 2_000


def _brief_context(
    *,
    user_question: str | None = None,
    provenance: dict[str, dict[str, Any]] | None = None,
    site_brief_query: str | None = BRIEF_SEED,
) -> strategy_knowledge.StrategyContext:
    """A new-shape (web brief) context: soil, fire and land cover facts with per-key provenance."""
    labels = {"soil_ph": SOIL_PROVENANCE, "days_since_fire": FIRE_PROVENANCE} if provenance is None else provenance
    return strategy_knowledge.StrategyContext(
        user_question=user_question,
        longitude=BOISE_FOOTHILLS[0],
        latitude=BOISE_FOOTHILLS[1],
        site_facts=strategy_knowledge.SiteFacts(
            soil_ph=BRIEF_SOIL_PH, days_since_fire=BRIEF_DAYS_SINCE_FIRE, land_cover="Grassland/Pasture"
        ),
        site_facts_provenance={key: strategy_knowledge.FactProvenance.model_validate(v) for key, v in labels.items()},
        site_brief_query=site_brief_query,
    )


@pytest.mark.usefixtures("configured")
async def test_with_no_typed_question_the_brief_seed_is_the_context_query() -> None:
    seen: list[httpx.Request] = []
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(STRATEGY_SEARCH, seen))):
        payload, _ = await call_with_context(
            agent_tools.search_environmental_strategies, {"query": "improve pasture"}, _brief_context()
        )
    [request] = seen
    assert json.loads(request.content)["context_query"] == BRIEF_SEED
    assert payload["context_query_source"] == "site_brief"


@pytest.mark.usefixtures("configured")
async def test_a_typed_question_outranks_the_brief_seed() -> None:
    seen: list[httpx.Request] = []
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(FINDING_SEARCH, seen))):
        payload, _ = await call_with_context(
            agent_tools.search_strategy_research_findings,
            {"query": "lime"},
            _brief_context(user_question=USER_QUESTION),
        )
    [request] = seen
    assert json.loads(request.content)["context_query"] == USER_QUESTION
    assert payload["context_query_source"] == "user_question"


@pytest.mark.usefixtures("configured")
async def test_with_neither_question_nor_seed_nothing_is_forwarded() -> None:
    seen: list[httpx.Request] = []
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(STRATEGY_SEARCH, seen))):
        payload, _ = await call_with_context(
            agent_tools.search_environmental_strategies, {"query": "mulch"}, _brief_context(site_brief_query=None)
        )
    [request] = seen
    assert "context_query" not in json.loads(request.content)
    assert payload["context_query_source"] == "none"


@pytest.mark.usefixtures("configured")
async def test_a_new_shape_caller_loses_every_site_fact_without_provenance() -> None:
    """C3 critic #11: land_cover arrives without a label, so it is dropped and named, never forwarded."""
    seen: list[httpx.Request] = []
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(STRATEGY_SEARCH, seen))):
        payload, _ = await call_with_context(
            agent_tools.search_environmental_strategies, {"query": "acid soil"}, _brief_context()
        )
    [request] = seen
    sent = json.loads(request.content)["site_profile"]
    assert sent == {
        "soil_ph": BRIEF_SOIL_PH,
        "days_since_fire": BRIEF_DAYS_SINCE_FIRE,
        "region": "great_basin_high_desert",
    }
    assert payload["site_profile_dropped"] == ["land_cover(no_provenance)"]
    provenance = payload["site_profile_provenance"]
    assert provenance["soil_ph"] == {"basis": "model_estimate", "label": SOIL_LABEL}
    assert provenance["days_since_fire"]["basis"] == "measured"
    assert provenance["region"] == strategy_knowledge.REGION_PROVENANCE
    assert "land_cover" not in provenance


@pytest.mark.usefixtures("configured")
async def test_a_legacy_caller_keeps_its_facts_and_each_is_echoed_as_legacy() -> None:
    seen: list[httpx.Request] = []
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(STRATEGY_SEARCH, seen))):
        payload, _ = await call_with_context(
            agent_tools.search_environmental_strategies,
            {"query": "acid soil"},
            _context(site_facts={"soil_ph": SERVER_SOIL_PH, "burn_severity": "high"}),
        )
    assert payload["site_profile_dropped"] == []
    assert payload["site_profile_provenance"]["soil_ph"] == {"basis": strategy_knowledge.LEGACY_PROVENANCE_BASIS}
    assert payload["site_profile_provenance"]["burn_severity"] == {"basis": strategy_knowledge.LEGACY_PROVENANCE_BASIS}
    assert payload["context_query_source"] == "user_question"


@pytest.mark.usefixtures("configured")
async def test_reading_records_reports_no_context_query_source() -> None:
    """C4: only the two searches forward context_query, so only they report its source."""
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(STRATEGY_RECORDS, []))):
        payload, _ = await call_with_context(
            agent_tools.get_environmental_strategies, {"strategy_ids": ["post-fire-straw-mulching"]}, _brief_context()
        )
    assert "context_query_source" not in payload
    assert payload["site_profile_source"] == "server"


def test_a_model_estimate_distance_is_bounded_by_the_soil_radius() -> None:
    too_far = {**SOIL_PROVENANCE, "distance_m": SOIL_DISTANCE_CEILING + 1}
    with pytest.raises(ValueError, match="model_estimate distance_m"):
        strategy_knowledge.FactProvenance.model_validate(too_far)
    measured = {**FIRE_PROVENANCE, "distance_m": 12_400}
    assert strategy_knowledge.FactProvenance.model_validate(measured).distance_m == measured["distance_m"]


def test_a_provenance_basis_outside_the_contract_is_refused() -> None:
    with pytest.raises(ValueError, match="basis"):
        strategy_knowledge.FactProvenance.model_validate({**SOIL_PROVENANCE, "basis": "observed"})


def test_the_site_brief_query_is_stripped_of_every_control_character() -> None:
    assert _brief_context(site_brief_query="acid\n loam\x00 topsoil\t").site_brief_query == "acid loam topsoil"
    assert _brief_context(site_brief_query=" \x07 ").site_brief_query is None
    with pytest.raises(ValueError, match="600"):
        _brief_context(site_brief_query="x" * (strategy_knowledge.MAX_SITE_BRIEF_QUERY_CHARACTERS + 1))


def test_the_bridge_wire_shape_carries_provenance_and_the_brief_seed() -> None:
    wire = strategy_knowledge.ServerContext.model_validate(
        {
            "point": {"longitude": -116.2, "latitude": 43.6},
            "site_facts": {"soil_ph": BRIEF_SOIL_PH},
            "site_facts_provenance": {"soil_ph": SOIL_PROVENANCE},
            "site_brief_query": BRIEF_SEED,
        }
    )
    context = wire.strategy_context()
    assert context.site_brief_query == BRIEF_SEED
    assert context.site_facts_provenance is not None
    assert context.site_facts_provenance["soil_ph"].basis == "model_estimate"
    assert context.user_question is None, "brief text never becomes the user's question"


def test_site_fact_docstrings_and_the_prompt_no_longer_say_measured_only() -> None:
    """O2: server-read site facts include model estimates, and every one carries a basis label."""
    assert "Measured site facts" not in (strategy_knowledge.SiteFacts.__doc__ or "")
    assert "Measured site facts" not in (strategy_knowledge.StrategySiteProfile.__doc__ or "")
    assert "any measured site facts" not in agent_prompts.SYSTEM_PROMPT
    assert "each with a basis label" in agent_prompts.SYSTEM_PROMPT


# --- CONTRACT-WAVE2 S3 echoes pass through verbatim ----------------------------------------------


@pytest.mark.usefixtures("configured")
async def test_query_intent_and_context_query_used_pass_through_unchanged() -> None:
    answered = {**STRATEGY_SEARCH, "query_intent": QUERY_INTENT, "context_query_used": False}
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(answered, []))):
        payload, _ = await call_with_context(
            agent_tools.search_environmental_strategies, {"query": "sour pasture"}, _brief_context()
        )
    assert payload["query_intent"] == QUERY_INTENT
    assert payload["context_query_used"] is False


@pytest.mark.usefixtures("configured")
async def test_the_s3_echoes_are_absent_when_the_service_omits_them() -> None:
    with strategy_knowledge.use_transport(httpx.MockTransport(answering(FINDING_SEARCH, []))):
        answer = await strategy_knowledge.ask("search_strategy_research_findings", "search_findings", {"query": "x"})
    assert "query_intent" not in answer.payload
    assert "context_query_used" not in answer.payload
