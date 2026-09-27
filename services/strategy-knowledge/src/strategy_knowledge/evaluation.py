"""Golden-query evaluation of `search_strategies`/`search_findings`/`search_passages`: hit@k, MRR@k, a
forbidden@3 violation rate, and a per-kind and per-family breakdown.

The golden file is a JSON list of `GoldenQuery`. Required: `query`, `expected_any_of` (a strategy_id for
`kind` "golden"/"paraphrase"/"lay"/"contrast"/"family"/"agent"; a finding_id for "findings"; a source_id
for "passages" — search_passages has no stable pre-computed passage id to label blind, so passage coverage
scores at the source level). `filters` takes any request field for that kind's search (goals, region, ...
for strategies/findings; source_id, content_type, ... for passages). `forbidden_at_3` names ids that must
NOT rank in the top 3 (used by the raise/lower-pH contrast pairs). `context_query` is forwarded to the
request's own `context_query` field (contract seam S3) once that field exists on the target request model;
until then it is recorded but silently dropped, so a golden file written ahead of the retrieval change
still validates. Expected ids are resolved through the strategy alias map first (a no-op for finding/source
ids), because search returns canonical ids only. See AGENTS.md section "Evaluation".
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, Any, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from strategy_knowledge.queries import MAXIMUM_LIMIT, FindingSearch, PassageSearch, StrategySearch
from strategy_knowledge.query_intent import MAXIMUM_CONTEXT_QUERY_CHARACTERS

DEFAULT_K: Final = 5
FORBIDDEN_TOP_K: Final = 3

Kind = Literal["golden", "paraphrase", "lay", "contrast", "family", "agent", "findings", "passages"]


class StrategySearcher(Protocol):
    """Anything with the knowledge base's three search tools."""

    def search_strategies(self, request: StrategySearch) -> dict[str, Any]:
        """Ranked strategies for a request."""
        ...

    def search_findings(self, request: FindingSearch) -> dict[str, Any]:
        """Ranked findings for a request."""
        ...

    def search_passages(self, request: PassageSearch) -> dict[str, Any]:
        """Ranked passages for a request."""
        ...


class GoldenQuery(BaseModel):
    """One labelled query, scored against the search endpoint `kind` selects (strategies unless
    `kind` is "findings" or "passages")."""

    model_config = ConfigDict(extra="forbid")

    query: str
    filters: dict[str, Any] = Field(default_factory=dict)
    expected_any_of: Annotated[list[str], Field(min_length=1)]
    k: Annotated[int, Field(ge=1, le=MAXIMUM_LIMIT)] = DEFAULT_K
    forbidden_at_3: list[str] = Field(
        default_factory=list,
        description="Ids that must not rank in the top 3 (paired with a raise/lower-pH-style contrast query).",
    )
    kind: Kind = "golden"
    context_query: Annotated[str | None, Field(max_length=MAXIMUM_CONTEXT_QUERY_CHARACTERS)] = None

    @model_validator(mode="after")
    def _expected_and_forbidden_are_disjoint(self) -> "GoldenQuery":
        """An id cannot be both a right answer and a forbidden one; a 2026-09-27 review found `agent_queries_
        heldout.json` doing exactly that, which let a wrong-direction top hit score as a hit and a violation
        at once (AGENTS.md "Evaluation")."""
        overlap = set(self.expected_any_of) & set(self.forbidden_at_3)
        if overlap:
            raise ValueError(f"expected_any_of and forbidden_at_3 overlap: {sorted(overlap)}")
        return self


GOLDEN_FILE: Final = TypeAdapter(list[GoldenQuery])

_REQUEST_MODEL: Final[Mapping[str, type[StrategySearch] | type[FindingSearch] | type[PassageSearch]]] = {
    "findings": FindingSearch,
    "passages": PassageSearch,
}


def reciprocal_rank(ranked_ids: Sequence[str], expected: Sequence[str], k: int) -> float:
    """1 / rank of the first expected id within the top k, else 0."""
    wanted = set(expected)
    for rank, identifier in enumerate(ranked_ids[:k], start=1):
        if identifier in wanted:
            return 1.0 / rank
    return 0.0


def forbidden_hit(ranked_ids: Sequence[str], forbidden: Sequence[str], k: int = FORBIDDEN_TOP_K) -> str | None:
    """The first forbidden id ranked within the top k, else None."""
    forbidden_set = set(forbidden)
    for identifier in ranked_ids[:k]:
        if identifier in forbidden_set:
            return identifier
    return None


def canonical_ids(strategy_ids: Sequence[str], aliases: Mapping[str, str]) -> list[str]:
    """Each id resolved through the alias map (a merged-away id becomes the strategy that absorbed it), deduplicated.

    A no-op for finding_id/source_id values, which never appear as alias keys.
    """
    return list(dict.fromkeys(aliases.get(strategy_id, strategy_id) for strategy_id in strategy_ids))


def _rank_for(searcher: StrategySearcher, item: GoldenQuery) -> tuple[list[str], bool]:
    """Ranked ids from the search endpoint `item.kind` selects, and whether `context_query` was forwarded.

    Fetches at least `FORBIDDEN_TOP_K` results even when `item.k` is smaller, so an item written with `k` < 3
    can still detect a forbidden id ranked at position `k + 1..3` (AGENTS.md "Evaluation"); `top_ids`/hit@k still
    report only the first `item.k` of what comes back.
    """
    limit = max(item.k, FORBIDDEN_TOP_K) if item.forbidden_at_3 else item.k
    payload: dict[str, Any] = {**item.filters, "query": item.query, "limit": limit, "offset": 0}
    model_cls = _REQUEST_MODEL.get(item.kind, StrategySearch)
    context_query_sent = item.context_query is not None and "context_query" in model_cls.model_fields
    if context_query_sent:
        payload["context_query"] = item.context_query
    if item.kind == "findings":
        results = searcher.search_findings(FindingSearch.model_validate(payload))["results"]
        ranked = [str(result["finding_id"]) for result in results]
    elif item.kind == "passages":
        results = searcher.search_passages(PassageSearch.model_validate(payload))["results"]
        ranked = [str(result["source"]["source_id"]) for result in results]
    else:
        results = searcher.search_strategies(StrategySearch.model_validate(payload))["results"]
        ranked = [str(result["strategy_id"]) for result in results]
    return ranked, context_query_sent


def _mean(values: Iterable[bool | float]) -> float:
    """Arithmetic mean, 0.0 for an empty sequence."""
    values = list(values)
    return round(sum(values) / len(values), 4) if values else 0.0


def _breakdown_by_kind(rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """hit@k, MRR and the forbidden@3 violation rate, grouped by `kind`."""
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_kind.setdefault(row["kind"], []).append(row)
    return {
        kind: {
            "queries": len(kind_rows),
            "hit_at_k": _mean(row["hit"] for row in kind_rows),
            "mrr": _mean(row["reciprocal_rank"] for row in kind_rows),
            "forbidden_at_3_violation_rate": _forbidden_violation_rate(kind_rows),
        }
        for kind, kind_rows in by_kind.items()
    }


def _forbidden_violation_rate(rows: Sequence[dict[str, Any]]) -> float:
    """Share of rows configuring `forbidden_at_3` whose top 3 actually contained one; 0.0 when none configure it."""
    guarded = [row for row in rows if row["forbidden_at_3"]]
    return _mean(row["forbidden_violation"] is not None for row in guarded)


def _strategy_families(searcher: object) -> dict[str, str]:
    """strategy_id -> family_id, read off the searcher's loaded registry when it exposes one (`KnowledgeBase.
    strategies`); empty for a test double that carries no registry, which simply skips family coverage."""
    strategies = getattr(searcher, "strategies", None)
    if not isinstance(strategies, Mapping):
        return {}
    families = {}
    for strategy_id, strategy in strategies.items():
        family_id = getattr(strategy, "family_id", None)
        if family_id:
            families[strategy_id] = family_id
    return families


def _family_coverage(rows: Sequence[dict[str, Any]], family_of: Mapping[str, str]) -> dict[str, Any] | None:
    """Which families the golden set's expected ids touch, and the hit rate for the queries touching each.

    None when the searcher exposed no registry (family coverage cannot be computed, as opposed to computed
    and empty).
    """
    if not family_of:
        return None
    per_family: dict[str, dict[str, int]] = {family_id: {"queries": 0, "hits": 0} for family_id in family_of.values()}
    for row in rows:
        touched = {family_of[identifier] for identifier in row["expected_canonical"] if identifier in family_of}
        for family_id in touched:
            per_family[family_id]["queries"] += 1
            per_family[family_id]["hits"] += int(row["hit"])
    tested = sorted(family_id for family_id, stats in per_family.items() if stats["queries"])
    untested = sorted(family_id for family_id in per_family if family_id not in tested)
    return {
        "tested_families": tested,
        "untested_families": untested,
        "per_family_hit_rate": {
            family_id: round(stats["hits"] / stats["queries"], 4)
            for family_id, stats in per_family.items()
            if stats["queries"]
        },
    }


def evaluate(
    searcher: StrategySearcher,
    golden: Sequence[GoldenQuery],
    aliases: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run every golden query against the search endpoint its `kind` selects; report per-query ranks, overall
    hit@k/MRR/forbidden@3, a per-`kind` breakdown, and per-family coverage (strategy-kind queries only).

    `aliases` (superseded id -> canonical id, the knowledge base's `aliases`) resolves expected and forbidden
    ids first, so a golden file written before a registry merge still scores the strategy that absorbed its id.
    """
    aliases = aliases or {}
    family_of = _strategy_families(searcher)
    per_query = []
    for item in golden:
        ranked, context_query_sent = _rank_for(searcher, item)
        expected = canonical_ids(item.expected_any_of, aliases)
        forbidden = canonical_ids(item.forbidden_at_3, aliases)
        score = reciprocal_rank(ranked, expected, item.k)
        violation = forbidden_hit(ranked, forbidden) if forbidden else None
        per_query.append(
            {
                "query": item.query,
                "kind": item.kind,
                "k": item.k,
                "hit": score > 0,
                "reciprocal_rank": round(score, 4),
                "first_hit_rank": round(1 / score) if score else None,
                "top_ids": ranked[: item.k],
                "expected_any_of": item.expected_any_of,
                "expected_canonical": expected,
                "forbidden_at_3": item.forbidden_at_3,
                "forbidden_violation": violation,
                "context_query_sent": context_query_sent,
            },
        )
    count = len(per_query)
    return {
        "queries": count,
        "hit_at_k": _mean(row["hit"] for row in per_query),
        "mrr": _mean(row["reciprocal_rank"] for row in per_query),
        "forbidden_at_3_violation_rate": _forbidden_violation_rate(per_query),
        "by_kind": _breakdown_by_kind(per_query),
        "family_coverage": _family_coverage(per_query, family_of),
        "per_query": per_query,
    }
