"""The MCP surface over the SDK's in-process transport: tool listing, one call of each tool, the claim tier."""

import json
from typing import Any, Final

import anyio
from mcp import Client

from strategy_knowledge.knowledge_base import KnowledgeBase
from strategy_knowledge.server import build_server
from strategy_knowledge.vocabulary import CLAIM_TIER

TOOL_CALLS: Final[dict[str, dict[str, Any]]] = {
    "list_facets": {},
    "search_strategies": {"query": "straw mulch on burned slopes", "site_profile": {"slope_pct": 40}},
    "get_strategy": {"ids": ["post-fire-straw-mulching"]},
    "get_family": {"family_id": "post-fire-mulching"},
    "compare_strategies": {"ids": ["post-fire-straw-mulching", "grass-cover-cropping"]},
    "search_findings": {"query": "sediment yield", "direction": "decrease"},
    "search_passages": {"query": "cereal rye winter cover"},
    "list_sources": {},
}


def _exercise(knowledge_base: KnowledgeBase) -> dict[str, Any]:
    """List the tools, call each once, and call compare_strategies with an id and its alias."""

    async def session() -> dict[str, Any]:
        server = build_server(lambda: knowledge_base)
        async with Client(server) as client:
            listed = await client.list_tools()
            results = {name: await client.call_tool(name, arguments) for name, arguments in TOOL_CALLS.items()}
            same_strategy = await client.call_tool(
                "compare_strategies",
                {"ids": ["post-fire-straw-mulching", "post-fire-straw-or-hay-mulching"]},
            )
            too_few = await client.call_tool("compare_strategies", {"ids": ["post-fire-straw-mulching"]})
        return {"tools": listed.tools, "results": results, "same_strategy": same_strategy, "too_few": too_few}

    return anyio.run(session)


def test_every_tool_answers_over_an_in_memory_session(knowledge_base: KnowledgeBase) -> None:
    outcome = _exercise(knowledge_base)
    tools = {tool.name: tool for tool in outcome["tools"]}
    assert set(tools) == set(TOOL_CALLS)
    for tool in tools.values():
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.description
    assert tools["compare_strategies"].input_schema["properties"]["ids"]["minItems"] == 2
    for name, result in outcome["results"].items():
        assert not result.is_error, (name, result.content)
        payload = json.loads(result.content[0].text)
        assert payload["claim_tier"] == CLAIM_TIER, name
        assert "corpus_version" in payload
        assert "index_is_stale" in payload
    strategies = json.loads(outcome["results"]["search_strategies"].content[0].text)
    assert strategies["results"]
    assert strategies["truncated"] is False


def test_compare_needs_two_distinct_strategies(knowledge_base: KnowledgeBase) -> None:
    outcome = _exercise(knowledge_base)
    assert outcome["same_strategy"].is_error
    assert "distinct" in outcome["same_strategy"].content[0].text
    assert outcome["too_few"].is_error


def test_descriptions_say_what_the_filters_and_magnitudes_really_are(knowledge_base: KnowledgeBase) -> None:
    tools = {tool.name: tool for tool in _exercise(knowledge_base)["tools"]}
    findings = " ".join((tools["search_findings"].description or "").split())
    assert "magnitude as reported (numbers verbatim from the source" in findings
    assert "VERBATIM magnitude" not in findings
    region = tools["search_strategies"].input_schema["properties"]["region"]["description"]
    assert "'global'" in region
    assert "'north_america_general'" in region
