"""Hybrid ranking: weighted reciprocal-rank fusion, soft boosts, facet collapse and family diversity (DESIGN.md
section 9).

Pure functions over ids and metadata so every step is testable without an index; see AGENTS.md section
"Retrieval" for the weights and the measurements behind them.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from strategy_knowledge.lexical import DEFAULT_EXPANSION_WEIGHT
from strategy_knowledge.metadata import GOAL_PREFIX, PHASE_PREFIX, SOIL_PREFIX
from strategy_knowledge.query_intent import PH_CONDITIONS

RECIPROCAL_RANK_CONSTANT: Final = 60
#: One matched boost tag is worth this fraction of a rank-1 RRF contribution, 1 / (k + 1).
BOOST_WEIGHT: Final = 0.25
#: A requested goal the record states (rather than infers) is worth this fraction.
STATED_GOAL_WEIGHT: Final = 0.125
#: Ceiling on a record's summed bonus, in the same unit: half of one first place, so boosts reorder near-ties
#: but a record missing from one ranking can never overtake one both rankings put first (AGENTS.md "Retrieval").
MAXIMUM_TOTAL_BOOST: Final = 0.5
#: A record targeting only the opposite pH condition to the query's pH direction loses this fraction.
PH_DEMOTION_WEIGHT: Final = 0.5
DEFAULT_FAMILY_DIVERSITY: Final = 2
#: Weighted-RRF list weights, measured on the tuning eval sets (AGENTS.md "Retrieval").
DENSE_WEIGHT: Final = 1.0
LEXICAL_WEIGHT: Final = 0.8
#: `context_query`'s dense and lexical rankings enter at this multiple of the query's own weights.
CONTEXT_QUERY_WEIGHT: Final = 0.5

WeightedRanking = tuple[Sequence[str], float]


@dataclass(frozen=True, slots=True)
class FusionWeights:
    """Weighted-RRF list weights plus the BM25 expansion-token weight; the knowledge base holds one."""

    dense: float = DENSE_WEIGHT
    lexical: float = LEXICAL_WEIGHT
    context_query: float = CONTEXT_QUERY_WEIGHT
    expansion: float = DEFAULT_EXPANSION_WEIGHT

    def weighted(
        self,
        query_rankings: tuple[Sequence[str], Sequence[str]],
        context_rankings: tuple[Sequence[str], Sequence[str]] | None = None,
    ) -> list[WeightedRanking]:
        """(dense, lexical) rankings of the query, and of the context query when given, with their weights."""
        weighted = [(query_rankings[0], self.dense), (query_rankings[1], self.lexical)]
        if context_rankings is not None:
            weighted += [
                (context_rankings[0], self.context_query * self.dense),
                (context_rankings[1], self.context_query * self.lexical),
            ]
        return weighted


def weighted_reciprocal_rank_fusion(
    weighted_rankings: Iterable[WeightedRanking],
    constant: int = RECIPROCAL_RANK_CONSTANT,
) -> dict[str, float]:
    """Weighted reciprocal-rank fusion: score(d) = sum over lists of weight / (k + rank)."""
    fused: dict[str, float] = {}
    for ranking, weight in weighted_rankings:
        for rank, identifier in enumerate(ranking, start=1):
            fused[identifier] = fused.get(identifier, 0.0) + weight / (constant + rank)
    return fused


def reciprocal_rank_fusion(
    rankings: Iterable[Sequence[str]],
    constant: int = RECIPROCAL_RANK_CONSTANT,
) -> dict[str, float]:
    """Reciprocal-rank fusion (Cormack, Clarke & Buettcher 2009): score(d) = sum over lists of 1 / (k + rank)."""
    return weighted_reciprocal_rank_fusion(((ranking, 1.0) for ranking in rankings), constant)


def collapse_ranking(ranking: Sequence[str], metadata_by_id: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """A facet-document ranking as a strategy ranking: each strategy at its best facet's position."""
    strategies: dict[str, None] = {}
    for identifier in ranking:
        strategy_id = metadata_by_id.get(identifier, {}).get("strategy_id")
        if strategy_id is not None:
            strategies.setdefault(str(strategy_id), None)
    return list(strategies)


def fuse_strategies(
    weighted_rankings: Sequence[WeightedRanking],
    metadata_by_id: Mapping[str, Mapping[str, Any]],
    constant: int = RECIPROCAL_RANK_CONSTANT,
) -> tuple[dict[str, float], dict[str, str]]:
    """Collapse each facet ranking to strategies, then weighted RRF over those; also each strategy's best facet.

    Collapsing first lets the lists agree on a strategy that matched through different facets (AGENTS.md "Retrieval").
    """
    strategy_rankings = [(collapse_ranking(ranking, metadata_by_id), weight) for ranking, weight in weighted_rankings]
    scores = weighted_reciprocal_rank_fusion(strategy_rankings, constant)
    best_document: dict[str, str] = {}
    for identifier, _ in rank_documents(weighted_reciprocal_rank_fusion(weighted_rankings, constant)):
        strategy_id = metadata_by_id.get(identifier, {}).get("strategy_id")
        if strategy_id is not None:
            best_document.setdefault(str(strategy_id), identifier)
    return scores, best_document


@dataclass(frozen=True, slots=True)
class Boosts:
    """Soft preferences: matching records rise, non-matching records are never removed."""

    soil_conditions: tuple[str, ...] = ()
    fire_phase: tuple[str, ...] = ()
    stated_goals: tuple[str, ...] = ()
    #: Soil conditions the query's own words named (`query_intent.py`); hits they boost skip the family cap.
    query_intent_soil_conditions: tuple[str, ...] = ()
    #: The pH condition opposite to the query's pH direction; a record tagged with it and no boosted soil
    #: condition is demoted by `PH_DEMOTION_WEIGHT`.
    query_intent_soil_demotions: tuple[str, ...] = ()

    def as_response(self) -> dict[str, Any]:
        """Echo of the boosts applied."""
        return {
            "soil_conditions": list(self.soil_conditions),
            "fire_phase": list(self.fire_phase),
            "stated_goals": list(self.stated_goals),
            "query_intent_soil_conditions": list(self.query_intent_soil_conditions),
            "query_intent_soil_demotions": list(self.query_intent_soil_demotions),
            "weights": {
                "per_tag": BOOST_WEIGHT,
                "per_stated_goal": STATED_GOAL_WEIGHT,
                "maximum_total": MAXIMUM_TOTAL_BOOST,
                "ph_demotion": PH_DEMOTION_WEIGHT,
                "unit": "1/(k+1)",
            },
        }

    @property
    def diversity_exempt_tags(self) -> tuple[str, ...]:
        """Metadata keys whose match exempts a hit from the family cap: the query-intent pH condition only.

        Narrower than `query_intent_soil_conditions` on purpose (AGENTS.md "Retrieval"): a non-pH condition such
        as `erodible` can tag dozens of strategies in one family, and exempting all of them on a bare "washed
        out" mention would defeat family diversity the same way an unrestricted site-profile exemption would.
        """
        return tuple(
            f"{SOIL_PREFIX}{condition}" for condition in self.query_intent_soil_conditions if condition in PH_CONDITIONS
        )


def apply_boosts(
    fused: Mapping[str, float],
    metadata_by_id: Mapping[str, Mapping[str, Any]],
    boosts: Boosts,
    constant: int = RECIPROCAL_RANK_CONSTANT,
) -> tuple[dict[str, float], dict[str, list[str]]]:
    """Add the capped boost bonus to each fused score; return the new scores and which tags boosted each record.

    A pH demotion is listed in `boosted_by` as its key prefixed with "-".
    """
    unit = 1.0 / (constant + 1)
    soil_conditions = dict.fromkeys((*boosts.soil_conditions, *boosts.query_intent_soil_conditions))
    tags = [(f"{SOIL_PREFIX}{condition}", True, BOOST_WEIGHT) for condition in soil_conditions]
    tags += [(f"{PHASE_PREFIX}{phase}", True, BOOST_WEIGHT) for phase in dict.fromkeys(boosts.fire_phase)]
    tags += [(f"{GOAL_PREFIX}{goal}", "stated", STATED_GOAL_WEIGHT) for goal in dict.fromkeys(boosts.stated_goals)]
    intent_keys = {f"{SOIL_PREFIX}{condition}" for condition in boosts.query_intent_soil_conditions}
    demotion_keys = [f"{SOIL_PREFIX}{condition}" for condition in dict.fromkeys(boosts.query_intent_soil_demotions)]
    boosted: dict[str, float] = {}
    boosted_by: dict[str, list[str]] = {}
    for identifier, score in fused.items():
        metadata = metadata_by_id.get(identifier, {})
        matched = [key for key, value, _ in tags if metadata.get(key) == value]
        bonus = min(MAXIMUM_TOTAL_BOOST, sum(weight for key, value, weight in tags if metadata.get(key) == value))
        demoted = [key for key in demotion_keys if metadata.get(key) is True]
        if demoted and not intent_keys.intersection(matched):
            bonus -= PH_DEMOTION_WEIGHT
            matched += [f"-{key}" for key in demoted]
        boosted[identifier] = score + bonus * unit
        if matched:
            boosted_by[identifier] = matched
    return boosted, boosted_by


def rank_documents(scores: Mapping[str, float]) -> list[tuple[str, float]]:
    """(id, score) best first; ties broken by id so results are deterministic."""
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))


@dataclass(frozen=True, slots=True)
class StrategyHit:
    """One strategy after facet collapse: its best facet document stands for it."""

    strategy_id: str
    family_id: str | None
    score: float
    matched_facet: str
    document_id: str
    boosted_by: tuple[str, ...] = ()
    exempt_from_diversity: bool = False


def strategy_hits(
    scores: Mapping[str, float],
    best_document: Mapping[str, str],
    metadata_by_id: Mapping[str, Mapping[str, Any]],
    boosted_by: Mapping[str, Sequence[str]] | None = None,
    diversity_exempt_tags: Iterable[str] = (),
) -> list[StrategyHit]:
    """Strategy scores as hits, best first, each naming its best facet; one boosted by an exempt tag skips the cap."""
    exempt = frozenset(diversity_exempt_tags)
    hits = []
    for strategy_id, score in rank_documents(scores):
        document_id = best_document.get(strategy_id)
        if document_id is None:
            continue
        metadata = metadata_by_id.get(document_id, {})
        tags = tuple((boosted_by or {}).get(strategy_id, ()))
        hits.append(
            StrategyHit(
                strategy_id=strategy_id,
                family_id=metadata.get("family_id"),
                score=score,
                matched_facet=str(metadata.get("facet", "")),
                document_id=document_id,
                boosted_by=tags,
                exempt_from_diversity=bool(exempt.intersection(tags)),
            ),
        )
    return hits


@dataclass
class DiversityOutcome:
    """Strategies kept under the per-family cap, and the ones it held back, by family."""

    kept: list[StrategyHit] = field(default_factory=list)
    suppressed: dict[str, list[str]] = field(default_factory=dict)


def diversify_families(hits: Sequence[StrategyHit], cap: int = DEFAULT_FAMILY_DIVERSITY) -> DiversityOutcome:
    """At most `cap` strategies per family, in rank order; 0 disables; a strategy with no family is its own.

    An exempt hit (boosted by a soil condition the query named) is always kept and takes no family slot.
    """
    outcome = DiversityOutcome()
    taken: dict[str, int] = {}
    for hit in hits:
        family = hit.family_id
        if cap <= 0 or family is None or hit.exempt_from_diversity:
            outcome.kept.append(hit)
            continue
        if taken.get(family, 0) < cap:
            taken[family] = taken.get(family, 0) + 1
            outcome.kept.append(hit)
        else:
            outcome.suppressed.setdefault(family, []).append(hit.strategy_id)
    return outcome


def page_window[ItemT](items: Sequence[ItemT], offset: int, limit: int) -> tuple[list[ItemT], int | None]:
    """The requested page and the offset of the next one (None when this is the last page)."""
    page = list(items[offset : offset + limit])
    next_offset = offset + limit if offset + limit < len(items) else None
    return page, next_offset


def page_truncated(available: int, offset: int, limit: int, *, candidates_exhausted: bool) -> bool:
    """True when a page reaches past what the fixed candidate pool yielded while more documents could match."""
    return offset + limit > available and not candidates_exhausted
