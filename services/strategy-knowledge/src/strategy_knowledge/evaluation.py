"""Golden-query evaluation of `search_strategies`: hit@k and mean reciprocal rank (MRR@k).

The golden file is `[{"query", "filters", "expected_any_of": [strategy_id, ...], "k"}]`; `filters` takes any
`search_strategies` parameter. Expected ids are resolved through the strategy alias map first, because search
returns canonical ids only. See AGENTS.md section "Evaluation".
"""

from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Final, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from strategy_knowledge.queries import MAXIMUM_LIMIT, StrategySearch

DEFAULT_K: Final = 5


class StrategySearcher(Protocol):
    """Anything with the knowledge base's `search_strategies`."""

    def search_strategies(self, request: StrategySearch) -> dict[str, Any]:
        """Ranked strategies for a request."""
        ...


class GoldenQuery(BaseModel):
    """One labelled query."""

    model_config = ConfigDict(extra="forbid")

    query: str
    filters: dict[str, Any] = Field(default_factory=dict)
    expected_any_of: Annotated[list[str], Field(min_length=1)]
    k: Annotated[int, Field(ge=1, le=MAXIMUM_LIMIT)] = DEFAULT_K


GOLDEN_FILE: Final = TypeAdapter(list[GoldenQuery])


def reciprocal_rank(ranked_ids: Sequence[str], expected: Sequence[str], k: int) -> float:
    """1 / rank of the first expected id within the top k, else 0."""
    wanted = set(expected)
    for rank, identifier in enumerate(ranked_ids[:k], start=1):
        if identifier in wanted:
            return 1.0 / rank
    return 0.0


def canonical_ids(strategy_ids: Sequence[str], aliases: Mapping[str, str]) -> list[str]:
    """Each id resolved through the alias map (a merged-away id becomes the strategy that absorbed it), deduplicated."""
    return list(dict.fromkeys(aliases.get(strategy_id, strategy_id) for strategy_id in strategy_ids))


def evaluate(
    searcher: StrategySearcher,
    golden: Sequence[GoldenQuery],
    aliases: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run every golden query and report per-query ranks plus mean hit@k and MRR@k.

    `aliases` (superseded id -> canonical id, the knowledge base's `aliases`) resolves expected ids first, so a
    golden file written before a registry merge still scores the strategy that absorbed its id.
    """
    per_query = []
    for item in golden:
        request = StrategySearch.model_validate({**item.filters, "query": item.query, "limit": item.k, "offset": 0})
        ranked = [result["strategy_id"] for result in searcher.search_strategies(request)["results"]]
        expected = canonical_ids(item.expected_any_of, aliases or {})
        score = reciprocal_rank(ranked, expected, item.k)
        per_query.append(
            {
                "query": item.query,
                "k": item.k,
                "hit": score > 0,
                "reciprocal_rank": round(score, 4),
                "first_hit_rank": round(1 / score) if score else None,
                "top_ids": ranked[: item.k],
                "expected_any_of": item.expected_any_of,
                "expected_canonical": expected,
            },
        )
    count = len(per_query)
    return {
        "queries": count,
        "hit_at_k": round(sum(row["hit"] for row in per_query) / count, 4) if count else 0.0,
        "mrr": round(sum(row["reciprocal_rank"] for row in per_query) / count, 4) if count else 0.0,
        "per_query": per_query,
    }
