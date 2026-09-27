"""The loaded, read-only knowledge base behind the eight MCP tools (DESIGN.md section 11).

Stateless per call: every result carries the ids the next call needs, `claim_tier` and each record's
`review_state`. See AGENTS.md section "Knowledge base".
"""

import logging
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection

from strategy_knowledge.config import DEFAULT_CANDIDATE_POOL
from strategy_knowledge.corpus import CorpusStore, alias_groups
from strategy_knowledge.embedding import TextEmbedder
from strategy_knowledge.filters import FilterExpression, FilterRequest, build_filter, describe, to_where
from strategy_knowledge.index import (
    COLLECTION_NAMES,
    CORPUS_VERSION_METADATA,
    FINDINGS,
    PARTIAL_SINCE_METADATA,
    PASSAGES,
    STRATEGY_FACETS,
    IndexMissingError,
    IndexStamp,
    check_compatible,
    open_client,
)
from strategy_knowledge.lexical import LexicalIndex
from strategy_knowledge.metadata import FACET_TEXT_KEY, LIST_SEPARATOR
from strategy_knowledge.models import Family, Finding, RegistryStrategy
from strategy_knowledge.queries import FindingSearch, PassageSearch, SearchRequest, StrategySearch
from strategy_knowledge.search import (
    Boosts,
    StrategyHit,
    apply_boosts,
    collapse_to_strategies,
    diversify_families,
    page_truncated,
    page_window,
    rank_documents,
    reciprocal_rank_fusion,
)
from strategy_knowledge.site_profile import SiteDerivation, derive
from strategy_knowledge.vocabulary import (
    CLAIM_TIER,
    DEFAULT_EXCLUDED_CONTENT_TYPES,
    DEFAULT_EXCLUDED_RELEVANCE,
    DEFAULT_REVIEW_STATE,
    EVIDENCE_RANK,
    FACETS,
    NRCS_RESOURCE_CONCERNS,
    PASSAGE_REVIEW_STATE,
    VOCABULARIES,
)

logger = logging.getLogger(__name__)

SNIPPET_WORDS: Final = 60
MINIMUM_COMPARED_STRATEGIES: Final = 2
FIRST_SENTENCE: Final = re.compile(r"^(.+?[.!?])(?:\s|$)", re.DOTALL)
FILTER_SEMANTICS: Final = (
    "Values within one filter are OR'd; different filters are AND'd.",
    "land_use always also admits 'general' and records that carry no land_use tag.",
    (
        "region always also admits 'general', 'global' and records that carry no region tag, plus "
        "'north_america_general' when the requested region is North American."
    ),
    "search_findings and search_passages list an unknown strategy_id or source_id in not_found and return no results.",
    (
        "site_profile soil, slope, burn and precipitation values become soft boosts, never filters; its land_cover "
        "and region become land_use / region filters unless you pass those explicitly."
    ),
    "min_evidence keeps records whose evidence_rank is at least the named level's rank.",
    "search_passages leaves out content_type 'noise' and relevance 'off_topic' unless you ask for them.",
)
STRATEGY_COUNT_FIELDS: Final = (
    "category",
    "land_use",
    "region",
    "soil_conditions",
    "scale",
    "fire_phase",
    "evidence_strength",
)
FINDING_COUNT_FIELDS: Final = ("study_type", "direction", "evidence_strength", "land_use", "region")
PASSAGE_COUNT_FIELDS: Final = ("content_type", "relevance")


class RequestError(ValueError):
    """A request the knowledge base cannot answer as asked; the MCP layer returns its message as a tool error."""


@dataclass(frozen=True, slots=True)
class Candidates:
    """Fused scores over a fixed pool, and whether that pool held every document matching the filter."""

    scores: dict[str, float]
    exhausted: bool


@dataclass
class CollectionView:
    """One Chroma collection with its documents and metadata held in memory, plus its BM25 twin."""

    collection: Collection
    ids: list[str]
    documents: dict[str, str]
    metadatas: dict[str, dict[str, Any]]
    lexical: LexicalIndex

    @classmethod
    def load(cls, collection: Collection) -> "CollectionView":
        """Read every record once; BM25 indexes the document plus its display keywords."""
        result = collection.get(include=["documents", "metadatas"])
        ids = list(result["ids"])
        stored_documents = list(result["documents"] or [])
        stored_metadatas = list(result["metadatas"] or [])
        documents = [str(_item_at(stored_documents, index) or "") for index in range(len(ids))]
        metadatas = [dict(_item_at(stored_metadatas, index) or {}) for index in range(len(ids))]
        lexical_texts = [
            f"{document} {str(metadata.get('keywords', '')).replace(LIST_SEPARATOR, ' ')}"
            for document, metadata in zip(documents, metadatas, strict=True)
        ]
        return cls(
            collection=collection,
            ids=ids,
            documents=dict(zip(ids, documents, strict=True)),
            metadatas=dict(zip(ids, metadatas, strict=True)),
            lexical=LexicalIndex(ids, lexical_texts, metadatas),
        )

    @property
    def size(self) -> int:
        """Number of records."""
        return len(self.ids)


@dataclass
class OpenedIndex:
    """The loaded collections and what their metadata says about the corpus they reflect."""

    views: dict[str, CollectionView]
    corpus_version: str
    partial_since: str = ""


def _item_at(values: Sequence[Any], index: int) -> Any:
    """`values[index]`, or None past the end (a short documents/metadatas column)."""
    return values[index] if index < len(values) else None


def first_sentence(text: str, maximum_characters: int = 240) -> str:
    """A one-line summary: the first sentence, or a truncated prefix."""
    match = FIRST_SENTENCE.match(text.strip())
    sentence = match.group(1) if match else text.strip()
    return sentence if len(sentence) <= maximum_characters else f"{sentence[:maximum_characters].rstrip()}..."


def snippet(text: str, words: int = SNIPPET_WORDS) -> str:
    """The first `words` words of a text."""
    parts = text.split()
    return " ".join(parts[:words]) + (" ..." if len(parts) > words else "")


def _value_counts(records: Iterable[Mapping[str, Any]], field_name: str) -> Counter[str]:
    """How many records carry each value of a scalar or list field."""
    counts: Counter[str] = Counter()
    for record in records:
        value = record.get(field_name)
        values = value if isinstance(value, list) else [value]
        counts.update(str(item) for item in values if item is not None)
    return counts


class KnowledgeBase:
    """Corpus files + Chroma collections + BM25 indexes, opened once per server process."""

    def __init__(
        self,
        store: CorpusStore,
        embedder: TextEmbedder,
        index: OpenedIndex,
        candidate_pool: int = DEFAULT_CANDIDATE_POOL,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.views = dict(index.views)
        self.index_corpus_version = index.corpus_version
        self.index_partial_since = index.partial_since
        self.candidate_pool = max(1, candidate_pool)
        self.local_corpus_version = store.corpus_version()
        registry = store.load_registry()
        self.strategies = {strategy.strategy_id: strategy for strategy in registry}
        self.aliases = store.strategy_aliases(registry)
        self.alias_groups = alias_groups(self.aliases)
        self.families = {family.family_id: family for family in store.load_families()}
        self.sources = store.load_sources()
        self.findings: dict[str, tuple[Finding, str]] = {}
        self.chunk_counts: Counter[str] = Counter()
        for source_id in store.plan_source_ids():
            plan = store.load_plan(source_id)
            self.chunk_counts[source_id] = len(plan.chunks) if plan else 0
            findings_file = store.load_findings(source_id)
            for finding in findings_file.findings if findings_file else []:
                linked = list(dict.fromkeys(self.aliases.get(s, s) for s in finding.linked_strategy_ids))
                resolved = finding.model_copy(update={"linked_strategy_ids": linked})
                self.findings[finding.finding_id] = (resolved, source_id)
        if self.index_is_stale:
            logger.warning(
                "index corpus_version %r (partial updates since %r) does not match the local corpus %s; "
                "run `strategy-kb index`",
                self.index_corpus_version,
                self.index_partial_since or None,
                self.local_corpus_version,
            )

    @classmethod
    def open(
        cls,
        store: CorpusStore,
        embedder: TextEmbedder,
        client: ClientAPI | None = None,
        *,
        candidate_pool: int = DEFAULT_CANDIDATE_POOL,
    ) -> "KnowledgeBase":
        """Open every collection, refusing a missing index or one built for another model or schema."""
        client = client or open_client(str(store.chroma_path))
        existing = {collection.name for collection in client.list_collections()}
        missing = [name for name in COLLECTION_NAMES if name not in existing]
        if missing:
            raise IndexMissingError(f"collections {missing} do not exist; run `strategy-kb index --all` first")
        views = {}
        versions: set[str] = set()
        partial_since = ""
        for name in COLLECTION_NAMES:
            collection = client.get_collection(name, embedding_function=None)
            check_compatible(name, collection.metadata, embedder.model_name)
            metadata = collection.metadata or {}
            versions.add(str(metadata.get(CORPUS_VERSION_METADATA) or ""))
            partial_since = partial_since or str(metadata.get(PARTIAL_SINCE_METADATA) or "")
            views[name] = CollectionView.load(collection)
        # The three collections are stamped together; disagreement means an interrupted run, so treat it as stale.
        index_version = versions.pop() if len(versions) == 1 else ""
        return cls(store, embedder, OpenedIndex(views, index_version, partial_since), candidate_pool)

    @property
    def index_is_stale(self) -> bool:
        """True unless the last full index run saw exactly the local corpus and no partial run followed it."""
        return bool(self.index_partial_since) or self.index_corpus_version != self.local_corpus_version

    def index_staleness(self) -> list[str]:
        """Why the loaded index is not full and fresh against the local corpus; empty exactly when not stale."""
        stamp = IndexStamp(self.index_corpus_version, self.index_partial_since, missing_collections=())
        return stamp.stale_against(self.local_corpus_version)

    def _envelope(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Wrap every response with the claim tier and the index's corpus version."""
        return {
            "claim_tier": CLAIM_TIER,
            "corpus_version": self.index_corpus_version,
            "index_is_stale": self.index_is_stale,
            **payload,
        }

    def _candidates(self, view: CollectionView, query: str, expression: FilterExpression | None) -> Candidates:
        """Dense (Chroma) and lexical (BM25) rankings under one filter, fused by RRF over a fixed pool.

        The pool never depends on the page asked for, so page N and page N + 1 come from one ranking.
        """
        if view.size == 0:
            return Candidates({}, exhausted=True)
        pool = min(view.size, self.candidate_pool)
        result = view.collection.query(
            query_embeddings=self.embedder.embed([query]),  # type: ignore[arg-type]
            n_results=pool,
            where=to_where(expression),
            include=["distances"],
        )
        dense = list(result["ids"][0]) if result["ids"] else []
        lexical = view.lexical.search(query, expression, pool)
        # Dense search returns every filter match when fewer than `pool` exist, so the pool then holds them all.
        exhausted = pool >= view.size or len(dense) < pool
        return Candidates(reciprocal_rank_fusion([dense, lexical]), exhausted=exhausted)

    def _source_card(self, source_id: str) -> dict[str, Any]:
        """The citation facts of one source."""
        source = self.sources.get(source_id)
        if source is None:
            return {"source_id": source_id}
        return {
            "source_id": source_id,
            "url": source.url,
            "title": source.title,
            "publisher": source.publisher,
            "year": source.year,
            "source_type": source.source_type,
        }

    def _resolve(self, strategy_id: str) -> RegistryStrategy | None:
        """A strategy by id, following a merge alias to the canonical record."""
        return self.strategies.get(strategy_id) or self.strategies.get(self.aliases.get(strategy_id, ""))

    def _scope_ids(self, strategy_id: str | None) -> tuple[str, ...]:
        """A scope filter's ids: the canonical id and every alias it absorbed.

        The index stores canonical ids, but a source indexed before a later merge still carries the old id.
        """
        if not strategy_id:
            return ()
        canonical = self.aliases.get(strategy_id, strategy_id)
        return (canonical, *self.alias_groups.get(canonical, ()))

    def _unknown_scope(self, *, strategy_id: str | None = None, source_id: str | None = None) -> list[str]:
        """Scope ids that name nothing: a strategy_id no registry id or alias resolves, a source_id not registered."""
        unknown = []
        if strategy_id and self._resolve(strategy_id) is None:
            unknown.append(strategy_id)
        if source_id and source_id not in self.sources:
            unknown.append(source_id)
        return unknown

    def _not_found(
        self,
        request: SearchRequest,
        applied_filters: dict[str, Any],
        not_found: list[str],
    ) -> dict[str, Any]:
        """The answer to a search scoped to an unknown id: no results, the unknown ids in `not_found`."""
        return self._envelope(
            {
                "query": request.query,
                "applied_filters": applied_filters,
                "results": [],
                "not_found": not_found,
                "ranked_candidates": 0,
                "offset": request.offset,
                "next_offset": None,
                "truncated": False,
                "candidate_pool": self.candidate_pool,
            },
        )

    def _distinct_strategies(self, ids: Sequence[str]) -> tuple[dict[str, RegistryStrategy], dict[str, str], list[str]]:
        """(canonical id -> strategy, canonical id -> first id asked for it, ids not found), in request order."""
        found: dict[str, RegistryStrategy] = {}
        requested_as: dict[str, str] = {}
        not_found: list[str] = []
        for requested in ids:
            strategy = self._resolve(requested)
            if strategy is None:
                if requested not in not_found:
                    not_found.append(requested)
            elif strategy.strategy_id not in found:
                found[strategy.strategy_id] = strategy
                requested_as[strategy.strategy_id] = requested
        return found, requested_as, not_found

    def list_facets(self) -> dict[str, Any]:
        """Vocabularies with counts per value across strategies, findings and passages."""
        strategy_rows = [strategy.model_dump(mode="json") for strategy in self.strategies.values()]
        finding_rows = [finding.model_dump(mode="json") for finding, _ in self.findings.values()]
        passage_rows = list(self.views[PASSAGES].metadatas.values())
        goal_states = Counter(
            (goal, state) for strategy in self.strategies.values() for goal, state in strategy.goals.items()
        )
        vocabularies: dict[str, list[dict[str, Any]]] = {}
        for field_name in dict.fromkeys((*STRATEGY_COUNT_FIELDS, *FINDING_COUNT_FIELDS, *PASSAGE_COUNT_FIELDS)):
            counts = {
                kind: _value_counts(rows, field_name)
                for kind, rows, fields in (
                    ("strategies", strategy_rows, STRATEGY_COUNT_FIELDS),
                    ("findings", finding_rows, FINDING_COUNT_FIELDS),
                    ("passages", passage_rows, PASSAGE_COUNT_FIELDS),
                )
                if field_name in fields
            }
            vocabularies[field_name] = [
                {"value": value, **{kind: found[value] for kind, found in counts.items()}}
                for value in VOCABULARIES[field_name]
            ]
        goals = [
            {
                "goal": goal,
                "nrcs_resource_concerns": list(concerns),
                "strategies_stated": goal_states[goal, "stated"],
                "strategies_inferred": goal_states[goal, "inferred"],
            }
            for goal, concerns in NRCS_RESOURCE_CONCERNS.items()
        ]
        return self._envelope(
            {
                "vocabularies": vocabularies,
                "goals": goals,
                "evidence_rank": EVIDENCE_RANK,
                "facets": list(FACETS),
                "totals": {
                    "strategies": len(self.strategies),
                    "families": len({s.family_id for s in self.strategies.values() if s.family_id}),
                    "findings": len(self.findings),
                    "passages": self.views[PASSAGES].size,
                    "sources": len(self.sources),
                },
                "filter_semantics": list(FILTER_SEMANTICS),
            },
        )

    def search_strategies(self, request: StrategySearch) -> dict[str, Any]:
        """Hybrid search over strategy facets, collapsed to strategies with a per-family cap."""
        derivation = derive(request.site_profile)
        filter_request = FilterRequest(
            goals=tuple(request.goals),
            land_use=tuple(request.land_use or derivation.land_use),
            region=tuple(request.region or derivation.region),
            soil_conditions=tuple(request.soil_conditions),
            scale=tuple(request.scale),
            category=tuple(request.category),
            fire_phase=tuple(request.fire_phase),
            min_evidence=request.min_evidence,
        )
        boosts = Boosts(
            soil_conditions=tuple(dict.fromkeys(derivation.soil_condition_boosts)),
            fire_phase=tuple(derivation.fire_phase_boosts),
            stated_goals=tuple(request.goals),
        )
        view = self.views[STRATEGY_FACETS]
        candidates = self._candidates(view, request.query, build_filter(filter_request))
        scores, boosted_by = apply_boosts(candidates.scores, view.metadatas, boosts)
        hits = collapse_to_strategies(scores, view.metadatas, boosted_by)
        diversity = diversify_families(hits, request.family_diversity)
        page, next_offset = page_window(diversity.kept, request.offset, request.limit)
        return self._envelope(
            {
                "query": request.query,
                "applied_filters": describe(filter_request),
                "boosts": boosts.as_response(),
                "site_profile": _derivation_echo(request.site_profile, derivation, request.land_use, request.region),
                "results": [self._strategy_hit(rank, hit) for rank, hit in enumerate(page, start=request.offset + 1)],
                "family_diversity": {"cap": request.family_diversity, "held_back_by_family": diversity.suppressed},
                **self._paging(request, len(diversity.kept), next_offset, candidates),
            },
        )

    def _paging(
        self,
        request: SearchRequest,
        available: int,
        next_offset: int | None,
        candidates: Candidates,
    ) -> dict[str, Any]:
        """Paging fields: `truncated` when the fixed candidate pool ran out before the page did."""
        truncated = page_truncated(available, request.offset, request.limit, candidates_exhausted=candidates.exhausted)
        return {
            "ranked_candidates": available,
            "offset": request.offset,
            "next_offset": next_offset,
            "truncated": truncated,
            "candidate_pool": self.candidate_pool,
        }

    def _strategy_hit(self, rank: int, hit: StrategyHit) -> dict[str, Any]:
        """One ranked strategy as `search_strategies` reports it; the snippet is the raw facet text."""
        strategy = self.strategies.get(hit.strategy_id)
        view = self.views[STRATEGY_FACETS]
        metadata = view.metadatas.get(hit.document_id, {})
        facet_text = str(metadata.get(FACET_TEXT_KEY) or view.documents.get(hit.document_id, ""))
        entry: dict[str, Any] = {
            "rank": rank,
            "strategy_id": hit.strategy_id,
            "score": round(hit.score, 6),
            "matched_facet": hit.matched_facet,
            "matched_facet_snippet": snippet(facet_text),
            "family_id": hit.family_id,
            "boosted_by": list(hit.boosted_by),
        }
        if strategy is not None:
            entry.update(
                name=strategy.name,
                summary=first_sentence(strategy.summary),
                category=strategy.category,
                goals=dict(strategy.goals),
                fire_phase=list(strategy.fire_phase),
                evidence_strength=strategy.evidence_strength,
                evidence_rank=EVIDENCE_RANK[strategy.evidence_strength],
                citation_count=len(strategy.sources),
                review_state=strategy.review_state or DEFAULT_REVIEW_STATE,
            )
        return entry

    def get_strategy(self, ids: Sequence[str]) -> dict[str, Any]:
        """Full records with facets and citations (URL, locator, excerpt); an alias and its canonical id give one."""
        strategies, requested_as, not_found = self._distinct_strategies(ids)
        found = []
        for strategy_id, strategy in strategies.items():
            record = strategy.model_dump(mode="json", exclude={"sources"})
            record["citations"] = [
                {**self._source_card(citation.source_id), "locator": citation.locator, "excerpt": citation.excerpt}
                for citation in strategy.sources
            ]
            record["review_state"] = strategy.review_state or DEFAULT_REVIEW_STATE
            if requested_as[strategy_id] != strategy_id:
                record["resolved_from"] = requested_as[strategy_id]
            found.append(record)
        return self._envelope({"strategies": found, "not_found": not_found})

    def get_family(self, family_id: str) -> dict[str, Any]:
        """A family and its members with one-line summaries, each canonical strategy once."""
        family = self.families.get(family_id) or Family(family_id=family_id)
        registry_members = [s.strategy_id for s in self.strategies.values() if s.family_id == family_id]
        strategies, _, _ = self._distinct_strategies([*family.member_strategy_ids, *registry_members])
        members = [
            {
                "strategy_id": strategy.strategy_id,
                "name": strategy.name,
                "summary": first_sentence(strategy.summary),
                "category": strategy.category,
                "region": list(strategy.region),
                "evidence_strength": strategy.evidence_strength,
                "review_state": strategy.review_state or DEFAULT_REVIEW_STATE,
            }
            for strategy in strategies.values()
        ]
        return self._envelope(
            {
                "family_id": family_id,
                "name": family.name,
                "description": family.description,
                "found": bool(members) or family_id in self.families,
                "members": members,
            },
        )

    def compare_strategies(self, ids: Sequence[str]) -> dict[str, Any]:
        """A side-by-side matrix: goals, fit, rates, cost/labor, evidence, risks; needs two distinct strategies."""
        present, requested_as, not_found = self._distinct_strategies(ids)
        if len(present) + len(not_found) < MINIMUM_COMPARED_STRATEGIES:
            raise RequestError(
                f"compare_strategies needs at least {MINIMUM_COMPARED_STRATEGIES} distinct strategies; "
                f"{list(dict.fromkeys(ids))} name only one (a merged id resolves to the strategy that absorbed it)",
            )
        rows = [
            {"attribute": attribute, "values": {s.strategy_id: read(s) for s in present.values()}}
            for attribute, read in COMPARISON_ROWS
        ]
        return self._envelope(
            {
                "strategy_ids": list(present),
                "resolved_from": {
                    strategy_id: asked for strategy_id, asked in requested_as.items() if asked != strategy_id
                },
                "review_state": {s.strategy_id: s.review_state or DEFAULT_REVIEW_STATE for s in present.values()},
                "rows": rows,
                "not_found": not_found,
            },
        )

    def search_findings(self, request: FindingSearch) -> dict[str, Any]:
        """Hybrid search over research findings; each result carries its verbatim excerpt and source.

        Findings carry no soil or fire-phase tags, so the site profile's soil/phase boosts are not applied here,
        and the echo says so rather than listing boosts that did nothing. An unknown strategy_id is reported in
        `not_found` (like get_strategy) rather than answered with a silent empty page.
        """
        derivation = derive(request.site_profile)
        filter_request = FilterRequest(
            goals=tuple(request.goals),
            land_use=tuple(request.land_use or derivation.land_use),
            region=tuple(request.region or derivation.region),
            min_evidence=request.min_evidence,
            study_type=tuple(request.study_type),
            direction=request.direction,
            strategy_ids=self._scope_ids(request.strategy_id),
        )
        not_found = self._unknown_scope(strategy_id=request.strategy_id)
        if not_found:
            return self._not_found(request, describe(filter_request), not_found)
        view = self.views[FINDINGS]
        boosts = Boosts(stated_goals=tuple(request.goals))
        candidates = self._candidates(view, request.query, build_filter(filter_request))
        scores, _ = apply_boosts(candidates.scores, view.metadatas, boosts)
        ranked = [(identifier, score) for identifier, score in rank_documents(scores) if identifier in self.findings]
        page, next_offset = page_window(ranked, request.offset, request.limit)
        results = []
        for rank, (identifier, score) in enumerate(page, start=request.offset + 1):
            finding, source_id = self.findings[identifier]
            results.append(
                {
                    "rank": rank,
                    "score": round(score, 6),
                    **finding.model_dump(mode="json"),
                    "source": self._source_card(source_id),
                    "review_state": DEFAULT_REVIEW_STATE,
                },
            )
        return self._envelope(
            {
                "query": request.query,
                "applied_filters": describe(filter_request),
                "boosts": boosts.as_response(),
                "site_profile": _derivation_echo(
                    request.site_profile,
                    derivation,
                    request.land_use,
                    request.region,
                    soil_and_phase_boosts_applied=False,
                ),
                "results": results,
                "not_found": [],
                **self._paging(request, len(ranked), next_offset, candidates),
            },
        )

    def search_passages(self, request: PassageSearch) -> dict[str, Any]:
        """Hybrid search over verbatim source windows; a row whose stored record is malformed is skipped.

        An unknown source_id or strategy_id is reported in `not_found` rather than answered with an empty page.
        """
        filter_request = FilterRequest(
            source_id=request.source_id,
            content_type=tuple(request.content_type),
            relevance=tuple(request.relevance),
            strategy_ids=self._scope_ids(request.strategy_id),
            excluded_content_type=() if request.content_type else DEFAULT_EXCLUDED_CONTENT_TYPES,
            excluded_relevance=() if request.relevance else DEFAULT_EXCLUDED_RELEVANCE,
        )
        not_found = self._unknown_scope(strategy_id=request.strategy_id, source_id=request.source_id)
        if not_found:
            return self._not_found(request, describe(filter_request), not_found)
        view = self.views[PASSAGES]
        candidates = self._candidates(view, request.query, build_filter(filter_request))
        ranked = [
            (identifier, score)
            for identifier, score in rank_documents(candidates.scores)
            if _is_displayable_passage(view, identifier)
        ]
        page, next_offset = page_window(ranked, request.offset, request.limit)
        results = [
            self._passage(rank, identifier, score) for rank, (identifier, score) in enumerate(page, request.offset + 1)
        ]
        return self._envelope(
            {
                "query": request.query,
                "applied_filters": describe(filter_request),
                "results": results,
                "not_found": [],
                **self._paging(request, len(ranked), next_offset, candidates),
            },
        )

    def _passage(self, rank: int, identifier: str, score: float) -> dict[str, Any]:
        """One passage window as `search_passages` reports it (every metadata key read with a default)."""
        view = self.views[PASSAGES]
        metadata = view.metadatas.get(identifier, {})
        document = view.documents.get(identifier, "")
        section_path = str(metadata.get("section_path", ""))
        text = document.split("\n\n", 1)[1] if section_path and "\n\n" in document else document
        return {
            "rank": rank,
            "score": round(score, 6),
            "passage_id": identifier,
            "chunk_id": metadata.get("chunk_id"),
            "window_index": metadata.get("window_index"),
            "section_path": section_path.split(" > ") if section_path else [],
            "title": metadata.get("title"),
            "content_type": metadata.get("content_type"),
            "relevance": metadata.get("relevance"),
            "line_start": metadata.get("line_start"),
            "line_end": metadata.get("line_end"),
            "linked_strategy_ids": _split(metadata.get("strategy_ids")),
            "linked_finding_ids": _split(metadata.get("finding_ids")),
            "text": text,
            "source": self._source_card(str(metadata.get("source_id", ""))),
            "review_state": PASSAGE_REVIEW_STATE,
        }

    def list_sources(self) -> dict[str, Any]:
        """Every registered source with type, year, publisher, goals and chunk/finding counts."""
        finding_counts = Counter(source_id for _, source_id in self.findings.values())
        passage_counts = Counter(str(m.get("source_id")) for m in self.views[PASSAGES].metadatas.values())
        citing = Counter(source_id for strategy in self.strategies.values() for source_id in _source_ids(strategy))
        sources = []
        for source_id, source in sorted(self.sources.items()):
            sources.append(
                {
                    **self._source_card(source_id),
                    "wildfire_relevance": source.wildfire_relevance,
                    "region_focus": list(source.region_focus),
                    "goals": dict(source.goals) or self._derived_source_goals(source_id),
                    "goals_origin": "source_record" if source.goals else "aggregated_from_chunks",
                    "review_state": source.review_state or DEFAULT_REVIEW_STATE,
                    "chunks": self.chunk_counts.get(source_id, 0),
                    "passages": passage_counts.get(source_id, 0),
                    "findings": finding_counts.get(source_id, 0),
                    "strategies_citing": citing.get(source_id, 0),
                },
            )
        return self._envelope({"sources": sources})

    def _derived_source_goals(self, source_id: str) -> dict[str, str]:
        """Goals of a source without a record-level goals object: 'stated' if any chunk states it."""
        plan = self.store.load_plan(source_id)
        goals: dict[str, str] = {}
        for chunk in plan.chunks if plan else []:
            for goal, state in chunk.goals.items():
                if goals.get(goal) != "stated":
                    goals[goal] = state
        return goals


def _split(value: Any) -> list[str]:
    """Undo a display-only `|`-joined list."""
    return [part for part in str(value or "").split(LIST_SEPARATOR) if part]


def _is_displayable_passage(view: CollectionView, identifier: str) -> bool:
    """A passage row with its text and the chunk and source it came from; anything else is skipped, not raised."""
    metadata = view.metadatas.get(identifier)
    if not metadata or not view.documents.get(identifier):
        logger.warning("passage %s has no stored text or metadata; skipped", identifier)
        return False
    return bool(metadata.get("chunk_id") and metadata.get("source_id"))


def _derivation_echo(
    profile: Any,
    derivation: SiteDerivation,
    explicit_land_use: Sequence[str],
    explicit_region: Sequence[str],
    *,
    soil_and_phase_boosts_applied: bool = True,
) -> dict[str, Any] | None:
    """How the site profile was used, including where an explicit filter overrode a derived one."""
    if profile is None:
        return None
    echo = derivation.as_response()
    if not soil_and_phase_boosts_applied:
        derived = echo["boosts"]
        echo["boosts"] = {"soil_conditions": [], "fire_phase": []}
        if derived["soil_conditions"] or derived["fire_phase"]:
            echo["notes"] = [
                *echo["notes"],
                (
                    "soil and fire-phase boosts are not applied to findings (findings carry no soil or phase tags); "
                    "search_strategies applies them"
                ),
            ]
    overridden = [
        name
        for name, explicit, derived in (
            ("land_use", explicit_land_use, derivation.land_use),
            ("region", explicit_region, derivation.region),
        )
        if explicit and derived
    ]
    echo["overridden_by_explicit_filters"] = overridden
    return echo


def _source_ids(strategy: RegistryStrategy) -> list[str]:
    return list(dict.fromkeys(citation.source_id for citation in strategy.sources))


COMPARISON_ROWS: Final = (
    ("name", lambda s: s.name),
    ("family_id", lambda s: s.family_id),
    ("category", lambda s: s.category),
    ("goals", lambda s: dict(s.goals)),
    ("fire_phase", lambda s: list(s.fire_phase)),
    ("land_use", lambda s: list(s.land_use)),
    ("region", lambda s: list(s.region)),
    ("soil_conditions", lambda s: list(s.soil_conditions)),
    ("scale", lambda s: list(s.scale)),
    ("slope_guidance", lambda s: s.slope_guidance),
    ("fit", lambda s: s.facets.fit),
    ("application_rate", lambda s: s.application_rate),
    ("timing", lambda s: s.timing),
    ("materials", lambda s: list(s.materials)),
    ("equipment", lambda s: list(s.equipment)),
    ("cost_level", lambda s: s.cost_level),
    ("labor_intensity", lambda s: s.labor_intensity),
    ("time_to_effect", lambda s: s.time_to_effect),
    ("benefits", lambda s: list(s.benefits)),
    ("risks_limitations", lambda s: list(s.risks_limitations)),
    ("outcomes", lambda s: s.facets.outcomes),
    ("evidence_strength", lambda s: s.evidence_strength),
    ("citation_count", lambda s: len(s.sources)),
    ("source_ids", _source_ids),
    ("nrcs_practice_code", lambda s: s.nrcs_practice_code),
)
