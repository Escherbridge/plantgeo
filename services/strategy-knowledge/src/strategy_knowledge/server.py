"""The standalone `strategy-knowledge` MCP server: eight read-only tools (DESIGN.md section 11).

Built on the official SDK's `MCPServer` (mcp 2.x; the 1.x name was FastMCP). Each tool's docstring is the
description the calling model reads. One tool table serves MCP (stdio or `/mcp`) and `POST /v1/tools/{name}`
(`http_app.py`). See AGENTS.md sections "MCP server", "stdout is the transport" and "HTTP transport".
"""

import inspect
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any, Final

import anyio.to_thread
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools import Tool
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field, ValidationError

from strategy_knowledge.config import Settings
from strategy_knowledge.corpus import CorpusStore
from strategy_knowledge.embedding import TextEmbedder, embedder_for
from strategy_knowledge.index import IndexMissingError
from strategy_knowledge.knowledge_base import KnowledgeBase, RequestError
from strategy_knowledge.queries import (
    DEFAULT_LIMIT,
    MAXIMUM_FAMILY_DIVERSITY,
    MAXIMUM_LIMIT,
    FindingSearch,
    PassageSearch,
    StrategySearch,
)
from strategy_knowledge.search import DEFAULT_FAMILY_DIVERSITY
from strategy_knowledge.site_profile import SiteProfile
from strategy_knowledge.storage import BucketSync
from strategy_knowledge.streams import reserved_stdout
from strategy_knowledge.vocabulary import (
    Category,
    ContentType,
    Direction,
    EvidenceStrength,
    FirePhase,
    Goal,
    LandUse,
    Region,
    Relevance,
    Scale,
    SoilCondition,
    StudyType,
)

logger = logging.getLogger(__name__)

SERVER_NAME: Final = "strategy-knowledge"
SERVER_VERSION: Final = "0.1.0"
INSTRUCTIONS: Final = (
    "Retrieval-only, literature-grounded knowledge base of environmental enrichment strategies: soil health, "
    "water, carbon, erosion, wildfire resilience, biodiversity, nutrients, remediation, biomass circularity and "
    "drought adaptation. Call list_facets first to learn the vocabularies. search_strategies ranks strategies; "
    "get_strategy returns full records with verbatim citations; get_family and compare_strategies help choose; "
    "search_findings and search_passages return research results and verbatim source text. The server is "
    "location-agnostic: read site values from the warehouse tools and pass them as site_profile. Every answer "
    "is claim_tier 'literature_grounded': it reports what cited sources say, never a computed causal effect; "
    "report each magnitude as reported (numbers verbatim from the source) and never extrapolate one."
)
READ_ONLY: Final = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

QueryText = Annotated[str, Field(min_length=1, description="What you are looking for, in plain language.")]
Goals = Annotated[
    list[Goal] | None,
    Field(description="Outcomes wanted; a record matches if it states or infers any of them (stated ranks higher)."),
]
LandUses = Annotated[list[LandUse] | None, Field(description="Land uses; 'general' and untagged records always pass.")]
Regions = Annotated[
    list[Region] | None,
    Field(
        description=(
            "Regions; 'general', 'global' and untagged records always pass, and 'north_america_general' also passes "
            "for a North American region."
        ),
    ),
]
MinimumEvidence = Annotated[
    EvidenceStrength | None,
    Field(description="Weakest evidence level to keep (ranks: see list_facets.evidence_rank)."),
]
SiteValues = Annotated[
    SiteProfile | None,
    Field(description="Site values from the warehouse tools; soil/slope/burn values boost, land_cover/region filter."),
]
Limit = Annotated[int, Field(ge=1, le=MAXIMUM_LIMIT, description="Results per page (1-50).")]
Offset = Annotated[int, Field(ge=0, description="Results to skip; pass the previous response's next_offset.")]
StrategyIds = Annotated[list[str], Field(min_length=1, description="strategy_id values from a search result.")]
ComparedIds = Annotated[
    list[str],
    Field(min_length=2, description="Two or more distinct strategy_id values from search results."),
]


class KnowledgeBaseNotLoadedError(ToolError):
    """A tool ran before the lifespan opened the knowledge base (HTTP answers it 503, MCP as a tool error)."""


class ToolArgumentsError(ValueError):
    """Arguments a tool refuses: its argument model rejected them, or the knowledge base cannot answer as asked."""


class ServingRefusedError(RuntimeError):
    """`serve --pull-from-bucket` must not serve: the pull failed or the pulled index is not full and fresh."""


@dataclass
class LoadedKnowledgeBase:
    """The knowledge base the server lifespan opens; tools read it once the session is up."""

    loader: Callable[[], KnowledgeBase]
    value: KnowledgeBase | None = None

    def get(self) -> KnowledgeBase:
        """The opened knowledge base; a tool can only run after the lifespan loaded it."""
        if self.value is None:
            raise KnowledgeBaseNotLoadedError("the knowledge base is not loaded yet")
        return self.value


@dataclass(frozen=True)
class ToolTable:
    """The eight tools by name, built once: MCP registers them and `POST /v1/tools/{name}` dispatches to them.

    Both surfaces validate with the same SDK argument model and serialise with the same SDK converter, so an
    HTTP answer is byte-identical to the MCP tool's text (AGENTS.md section "HTTP transport").
    """

    tools: Mapping[str, Tool]

    @property
    def names(self) -> list[str]:
        """Tool names in registration order."""
        return list(self.tools)

    def __contains__(self, name: object) -> bool:
        """Whether a tool of this name exists."""
        return name in self.tools

    def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Validate `arguments` with the tool's MCP argument model and run it; refusals raise ToolArgumentsError."""
        tool = self.tools[name]
        accepted = _parameter_names(tool)
        unexpected = sorted(set(arguments) - accepted)
        if unexpected:
            raise ToolArgumentsError(f"unknown argument(s) {unexpected}; {name} accepts {sorted(accepted)}")
        try:
            validated = tool.fn_metadata.validate_arguments(dict(arguments))
        except ValidationError as error:
            raise ToolArgumentsError(_describe_validation_error(error)) from error
        try:
            payload: dict[str, Any] = tool.fn(**validated)
        except KnowledgeBaseNotLoadedError:
            raise
        except ToolError as error:
            raise ToolArgumentsError(str(error)) from error
        return payload

    def render(self, name: str, payload: dict[str, Any]) -> str:
        """The payload as the text content the SDK sends for this tool over MCP (`FuncMetadata.convert_result`)."""
        converted = self.tools[name].fn_metadata.convert_result(payload)
        blocks = converted.content if isinstance(converted, CallToolResult) else []
        return "".join(block.text for block in blocks if isinstance(block, TextContent))


def _parameter_names(tool: Tool) -> set[str]:
    """Every argument name a tool's SDK argument model accepts (field names and aliases)."""
    fields = tool.fn_metadata.arg_model.model_fields
    return {*fields, *(field.alias for field in fields.values() if field.alias)}


def _describe_validation_error(error: ValidationError) -> str:
    """One clause per rejected argument: where and why, without echoing the rejected value."""
    return "; ".join(
        f"{'.'.join(str(part) for part in detail['loc']) or 'arguments'}: {detail['msg']}"
        for detail in error.errors(include_url=False, include_input=False)
    )


@dataclass(frozen=True)
class StrategyKnowledgeService:
    """One process's MCP server, the tool table it registers, and the slot its lifespan fills."""

    server: MCPServer
    tools: ToolTable
    slot: LoadedKnowledgeBase


def _lifespan(slot: LoadedKnowledgeBase) -> Callable[[MCPServer], Any]:
    """The session lifespan: rebind `sys.stdout`, then open the knowledge base (AGENTS.md "stdout is the transport").

    The SDK enters it inside `stdio_server()`, i.e. after the transport claimed fd 1, so the index load and any
    ONNX model download or stray print land on stderr, never on the wire. Over HTTP the streamable-HTTP session
    manager enters it once per process (AGENTS.md "HTTP transport").
    """

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[None]:
        with reserved_stdout():
            slot.value = await anyio.to_thread.run_sync(slot.loader)
            logger.info("serving %s (corpus %s)", SERVER_NAME, slot.value.index_corpus_version or "never fully built")
            yield

    return lifespan


def _answer(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run one knowledge-base call, turning a request it cannot answer into a tool error the model reads."""
    try:
        return call()
    except RequestError as error:
        raise ToolError(str(error)) from error


def _tool_functions(slot: LoadedKnowledgeBase) -> tuple[Callable[..., dict[str, Any]], ...]:
    """The eight tool bodies over one knowledge-base slot; their signatures and docstrings are the MCP schema."""

    def list_facets() -> dict[str, Any]:
        """Call this first. Returns every controlled vocabulary (goal, category, land_use, region, soil_conditions,
        scale, fire_phase, evidence_strength, study_type, direction, content_type, relevance) with how many
        strategies, findings and passages carry each value, the goals' NRCS resource concerns, the evidence
        ranks behind min_evidence, the filter semantics, and the corpus_version. Use the values verbatim as
        filters in the search tools.
        """
        return _answer(lambda: slot.get().list_facets())

    def search_strategies(
        *,
        query: QueryText,
        goals: Goals = None,
        land_use: LandUses = None,
        region: Regions = None,
        soil_conditions: Annotated[
            list[SoilCondition] | None,
            Field(description="Hard filter on targeted soil conditions; prefer site_profile, which only boosts."),
        ] = None,
        scale: Annotated[list[Scale] | None, Field(description="Operational scales to keep.")] = None,
        category: Annotated[list[Category] | None, Field(description="Primary strategy categories to keep.")] = None,
        fire_phase: Annotated[
            list[FirePhase] | None,
            Field(description="Fire phases to keep (only wildfire-resilience strategies carry a phase)."),
        ] = None,
        site_profile: SiteValues = None,
        min_evidence: MinimumEvidence = None,
        family_diversity: Annotated[
            int,
            Field(ge=0, le=MAXIMUM_FAMILY_DIVERSITY, description="Max strategies per family (default 2; 0 = no cap)."),
        ] = DEFAULT_FAMILY_DIVERSITY,
        limit: Limit = DEFAULT_LIMIT,
        offset: Offset = 0,
    ) -> dict[str, Any]:
        """Rank land-management strategies for a need. Hybrid search (dense MiniLM + BM25, reciprocal-rank
        fusion) over each strategy's four facets (overview, how_to, fit, outcomes), collapsed to one row per
        strategy with the facet that matched best. Returns strategy_id, name, one-line summary, family_id,
        goals (stated vs inferred), fire_phase, evidence strength and rank, citation_count, review_state and
        score, plus the filters and site-profile derivation actually applied, and the strategies held back by
        the per-family cap (open them with get_family). Page with offset = next_offset; truncated = true means
        the fixed candidate pool ran out before the page did (narrow the query or filters). Next: get_strategy
        for full records and citations, compare_strategies to choose between candidates.
        """
        request = StrategySearch(
            query=query,
            goals=goals or [],
            land_use=land_use or [],
            region=region or [],
            soil_conditions=soil_conditions or [],
            scale=scale or [],
            category=category or [],
            fire_phase=fire_phase or [],
            site_profile=site_profile,
            min_evidence=min_evidence,
            family_diversity=family_diversity,
            limit=limit,
            offset=offset,
        )
        return _answer(lambda: slot.get().search_strategies(request))

    def get_strategy(ids: StrategyIds) -> dict[str, Any]:
        """Full records for strategy ids: summary, actions, verbatim application_rate and timing, materials,
        equipment, soil conditions, slope guidance, scale, cost and labor, benefits, risks and limitations,
        NRCS practice code, goals, family, the four facet texts, review_state, and every citation with source
        URL, title, publisher, year, locator and a verbatim excerpt. Ids merged into another strategy resolve
        to it (resolved_from shows the id you asked for); unknown ids are listed in not_found.
        """
        return _answer(lambda: slot.get().get_strategy(ids))

    def get_family(
        family_id: Annotated[str, Field(min_length=1, description="family_id from a strategy result.")],
    ) -> dict[str, Any]:
        """A family of related strategies (regional or rate variants of one intervention) with each member's
        strategy_id, name, one-line summary, category, region and evidence strength. Use it to see the variants
        search_strategies held back under its per-family cap.
        """
        return _answer(lambda: slot.get().get_family(family_id))

    def compare_strategies(ids: ComparedIds) -> dict[str, Any]:
        """A side-by-side matrix for two or more distinct strategies: goals, fire phase, land use, region, soil
        conditions, scale, slope guidance, fit facet, verbatim application rate and timing, materials,
        equipment, cost and labor level, time to effect, benefits, risks and limitations, outcomes facet,
        evidence strength and citation count. Values are what the cited sources say; nothing is computed. A
        merged id counts as the strategy that absorbed it, so an id and its alias are one strategy.
        """
        return _answer(lambda: slot.get().compare_strategies(ids))

    def search_findings(
        *,
        query: QueryText,
        goals: Goals = None,
        land_use: LandUses = None,
        region: Regions = None,
        site_profile: SiteValues = None,
        min_evidence: MinimumEvidence = None,
        study_type: Annotated[list[StudyType] | None, Field(description="Study designs to keep.")] = None,
        direction: Annotated[
            Direction | None,
            Field(description="Keep findings whose effect direction on the response is this."),
        ] = None,
        strategy_id: Annotated[str | None, Field(description="Keep findings linked to this strategy.")] = None,
        limit: Limit = DEFAULT_LIMIT,
        offset: Offset = 0,
    ) -> dict[str, Any]:
        """Search research findings: measured, reviewed or quantified results extracted from the sources. Each
        result has a paraphrased claim, conditions, driver/response variables, direction, the magnitude as
        reported (numbers verbatim from the source, or null), study type, evidence strength, linked strategy
        ids, a verbatim excerpt with its line range, and the source URL. Report magnitudes only as reported;
        never extrapolate an effect size. An unknown strategy_id comes back in not_found with no results.
        """
        request = FindingSearch(
            query=query,
            goals=goals or [],
            land_use=land_use or [],
            region=region or [],
            site_profile=site_profile,
            min_evidence=min_evidence,
            study_type=study_type or [],
            direction=direction,
            strategy_id=strategy_id,
            limit=limit,
            offset=offset,
        )
        return _answer(lambda: slot.get().search_findings(request))

    def search_passages(
        *,
        query: QueryText,
        source_id: Annotated[str | None, Field(description="Keep passages from this source only.")] = None,
        content_type: Annotated[
            list[ContentType] | None,
            Field(description="Content types to keep; default leaves out 'noise'."),
        ] = None,
        relevance: Annotated[
            list[Relevance] | None,
            Field(description="Relevance levels to keep; default leaves out 'off_topic'."),
        ] = None,
        strategy_id: Annotated[str | None, Field(description="Keep passages linked to this strategy.")] = None,
        limit: Limit = DEFAULT_LIMIT,
        offset: Offset = 0,
    ) -> dict[str, Any]:
        """Search verbatim source text in windows of at most 180 words. Each result carries passage_id, its
        parent chunk_id and window_index, section_path, title, content_type, relevance, the raw file's line
        range, linked strategy and finding ids, the verbatim text and the source URL. Use it to check exactly
        what a source says before relying on a strategy or finding. An unknown source_id or strategy_id comes
        back in not_found with no results.
        """
        request = PassageSearch(
            query=query,
            source_id=source_id,
            content_type=content_type or [],
            relevance=relevance or [],
            strategy_id=strategy_id,
            limit=limit,
            offset=offset,
        )
        return _answer(lambda: slot.get().search_passages(request))

    def list_sources() -> dict[str, Any]:
        """Every source in the corpus: source_id, URL, title, publisher, year, source type, wildfire relevance,
        region focus, goals, and how many chunks, passages, findings and citing strategies it has. Use a
        source_id to scope search_passages.
        """
        return _answer(lambda: slot.get().list_sources())

    return (
        list_facets,
        search_strategies,
        get_strategy,
        get_family,
        compare_strategies,
        search_findings,
        search_passages,
        list_sources,
    )


def build_tool_table(slot: LoadedKnowledgeBase) -> ToolTable:
    """Register each tool once: read-only, unstructured JSON text, its cleaned docstring as the description."""
    tools = [
        Tool.from_function(
            function,
            description=inspect.cleandoc(function.__doc__ or ""),
            annotations=READ_ONLY,
            structured_output=False,
        )
        for function in _tool_functions(slot)
    ]
    return ToolTable({tool.name: tool for tool in tools})


def build_service(load_knowledge_base: Callable[[], KnowledgeBase]) -> StrategyKnowledgeService:
    """The MCP server over the shared tool table; the knowledge base is opened by the server lifespan, not here."""
    slot = LoadedKnowledgeBase(load_knowledge_base)
    table = build_tool_table(slot)
    server: MCPServer = MCPServer(
        SERVER_NAME,
        instructions=INSTRUCTIONS,
        version=SERVER_VERSION,
        lifespan=_lifespan(slot),
        tools=list(table.tools.values()),
    )
    return StrategyKnowledgeService(server=server, tools=table, slot=slot)


def build_server(load_knowledge_base: Callable[[], KnowledgeBase]) -> MCPServer:
    """Register the eight tools; the knowledge base is opened by the session lifespan, not here."""
    return build_service(load_knowledge_base).server


def open_knowledge_base(settings: Settings, embedder: TextEmbedder | None = None) -> KnowledgeBase:
    """Open the index and warm the embedder (the first call may download the ONNX model); run it in the lifespan."""
    chosen = embedder or embedder_for(settings.embedding_model)
    knowledge_base = KnowledgeBase.open(
        CorpusStore(settings.cache_dir),
        chosen,
        candidate_pool=settings.candidate_pool,
    )
    chosen.embed(["warm-up"])
    return knowledge_base


def pull_published_corpus(settings: Settings) -> None:
    """`strategy-kb sync pull --with-index`; a conflict, refused key or index refusal refuses to serve."""
    report = BucketSync(settings, CorpusStore(settings.cache_dir)).pull(with_index=True)
    if report.failed:
        raise ServingRefusedError(
            f"bucket pull failed: conflicts {report.conflicts}, rejected keys {report.rejected_keys}, "
            f"index refusals {report.index_refusals}",
        )
    logger.info("pulled %d key(s) from the bucket, %d unchanged", len(report.transferred), report.unchanged)


def load_serving_knowledge_base(settings: Settings, *, pull_from_bucket: bool = False) -> KnowledgeBase:
    """Open the knowledge base; with `pull_from_bucket`, pull first and refuse an index that is not full and fresh.

    Safe in-process: nothing opens a Chroma client on the cache before the pull swaps `chroma/` in
    (AGENTS.md sections "Storage" and "HTTP transport").
    """
    if not pull_from_bucket:
        return open_knowledge_base(settings)
    pull_published_corpus(settings)
    try:
        knowledge_base = open_knowledge_base(settings)
    except IndexMissingError as error:
        reason = f"no complete index after the pull (is index/LATEST published?): {error}"
        raise ServingRefusedError(reason) from error
    problems = knowledge_base.index_staleness()
    if problems:
        raise ServingRefusedError(f"the pulled index is not full and fresh: {'; '.join(problems)}")
    return knowledge_base


def serve(settings: Settings, *, pull_from_bucket: bool = False) -> None:
    """Serve MCP over stdio until the client closes stdin; the lifespan opens the knowledge base.

    Order matters: `stdio_server()` claims fd 1 only if `sys.stdout` is still the real stdout, so nothing that
    could print (bucket pull, index load, model download) runs before `run()`; it all runs in the lifespan, after
    the claim (AGENTS.md section "stdout is the transport").
    """
    build_server(lambda: load_serving_knowledge_base(settings, pull_from_bucket=pull_from_bucket)).run("stdio")
