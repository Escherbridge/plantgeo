"""Hybrid ranking: reciprocal-rank fusion, soft boosts, facet collapse and family diversity (DESIGN.md section 9).

Pure functions over ids and metadata so every step is testable without an index; see AGENTS.md section
"Retrieval".
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from strategy_knowledge.metadata import GOAL_PREFIX, PHASE_PREFIX, SOIL_PREFIX

RECIPROCAL_RANK_CONSTANT: Final = 60
#: One matched boost tag is worth this fraction of a rank-1 RRF contribution, 1 / (k + 1).
BOOST_WEIGHT: Final = 0.25
#: A requested goal the record states (rather than infers) is worth this fraction.
STATED_GOAL_WEIGHT: Final = 0.125
#: Ceiling on a record's summed bonus, in the same unit: half of one first place, so boosts reorder near-ties
#: but a record missing from one ranking can never overtake one both rankings put first (AGENTS.md "Retrieval").
MAXIMUM_TOTAL_BOOST: Final = 0.5
DEFAULT_FAMILY_DIVERSITY: Final = 2


def reciprocal_rank_fusion(
    rankings: Iterable[Sequence[str]],
    constant: int = RECIPROCAL_RANK_CONSTANT,
) -> dict[str, float]:
    """Reciprocal-rank fusion (Cormack, Clarke & Buettcher 2009): score(d) = sum over lists of 1 / (k + rank)."""
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, identifier in enumerate(ranking, start=1):
            fused[identifier] = fused.get(identifier, 0.0) + 1.0 / (constant + rank)
    return fused


@dataclass(frozen=True, slots=True)
class Boosts:
    """Soft preferences: matching records rise, non-matching records are never removed."""

    soil_conditions: tuple[str, ...] = ()
    fire_phase: tuple[str, ...] = ()
    stated_goals: tuple[str, ...] = ()

    def as_response(self) -> dict[str, Any]:
        """Echo of the boosts applied."""
        return {
            "soil_conditions": list(self.soil_conditions),
            "fire_phase": list(self.fire_phase),
            "stated_goals": list(self.stated_goals),
            "weights": {
                "per_tag": BOOST_WEIGHT,
                "per_stated_goal": STATED_GOAL_WEIGHT,
                "maximum_total": MAXIMUM_TOTAL_BOOST,
                "unit": "1/(k+1)",
            },
        }


def apply_boosts(
    fused: Mapping[str, float],
    metadata_by_id: Mapping[str, Mapping[str, Any]],
    boosts: Boosts,
    constant: int = RECIPROCAL_RANK_CONSTANT,
) -> tuple[dict[str, float], dict[str, list[str]]]:
    """Add the capped boost bonus to each fused score; return the new scores and which tags boosted each record."""
    unit = 1.0 / (constant + 1)
    tags = [(f"{SOIL_PREFIX}{condition}", True, BOOST_WEIGHT) for condition in dict.fromkeys(boosts.soil_conditions)]
    tags += [(f"{PHASE_PREFIX}{phase}", True, BOOST_WEIGHT) for phase in dict.fromkeys(boosts.fire_phase)]
    tags += [(f"{GOAL_PREFIX}{goal}", "stated", STATED_GOAL_WEIGHT) for goal in dict.fromkeys(boosts.stated_goals)]
    boosted: dict[str, float] = {}
    boosted_by: dict[str, list[str]] = {}
    for identifier, score in fused.items():
        metadata = metadata_by_id.get(identifier, {})
        matched = [key for key, value, _ in tags if metadata.get(key) == value]
        bonus = min(MAXIMUM_TOTAL_BOOST, sum(weight for key, value, weight in tags if metadata.get(key) == value))
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


def collapse_to_strategies(
    scores: Mapping[str, float],
    metadata_by_id: Mapping[str, Mapping[str, Any]],
    boosted_by: Mapping[str, Sequence[str]] | None = None,
) -> list[StrategyHit]:
    """Best fused score over each strategy's facets, reporting the matched facet; best strategy first."""
    best: dict[str, StrategyHit] = {}
    for identifier, score in rank_documents(scores):
        metadata = metadata_by_id.get(identifier)
        if metadata is None or "strategy_id" not in metadata:
            continue
        strategy_id = str(metadata["strategy_id"])
        if strategy_id in best:
            continue
        best[strategy_id] = StrategyHit(
            strategy_id=strategy_id,
            family_id=metadata.get("family_id"),
            score=score,
            matched_facet=str(metadata.get("facet", "")),
            document_id=identifier,
            boosted_by=tuple((boosted_by or {}).get(identifier, ())),
        )
    return list(best.values())


@dataclass
class DiversityOutcome:
    """Strategies kept under the per-family cap, and the ones it held back, by family."""

    kept: list[StrategyHit] = field(default_factory=list)
    suppressed: dict[str, list[str]] = field(default_factory=dict)


def diversify_families(hits: Sequence[StrategyHit], cap: int = DEFAULT_FAMILY_DIVERSITY) -> DiversityOutcome:
    """At most `cap` strategies per family, in rank order; 0 disables; a strategy with no family is its own."""
    outcome = DiversityOutcome()
    taken: dict[str, int] = {}
    for hit in hits:
        family = hit.family_id
        if cap <= 0 or family is None:
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
