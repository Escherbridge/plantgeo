"""Ranking primitives: reciprocal-rank fusion, boosts, facet collapse, family diversity, paging, BM25."""

import pytest

from strategy_knowledge.filters import FilterRequest, build_filter
from strategy_knowledge.lexical import LexicalIndex, tokenize
from strategy_knowledge.search import (
    BOOST_WEIGHT,
    MAXIMUM_TOTAL_BOOST,
    Boosts,
    StrategyHit,
    apply_boosts,
    collapse_to_strategies,
    diversify_families,
    page_window,
    rank_documents,
    reciprocal_rank_fusion,
)


def test_reciprocal_rank_fusion_uses_k_60() -> None:
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"]])
    assert fused["a"] == pytest.approx(1 / 61)
    assert fused["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert fused["c"] == pytest.approx(1 / 62)
    assert [identifier for identifier, _ in rank_documents(fused)] == ["b", "a", "c"]


def test_rank_documents_breaks_ties_by_id() -> None:
    assert rank_documents({"z": 0.5, "a": 0.5, "m": 0.9}) == [("m", 0.9), ("a", 0.5), ("z", 0.5)]


def test_boosts_add_a_fraction_of_a_first_place_contribution() -> None:
    fused = {"sandy": 0.01, "plain": 0.01, "stated": 0.01}
    metadata = {
        "sandy": {"soil_sandy_coarse": True},
        "plain": {},
        "stated": {"goal_erosion_control": "stated"},
    }
    boosts = Boosts(soil_conditions=("sandy_coarse",), stated_goals=("erosion_control",))
    scores, boosted_by = apply_boosts(fused, metadata, boosts)
    assert scores["sandy"] == pytest.approx(0.01 + BOOST_WEIGHT / 61)
    assert scores["plain"] == pytest.approx(0.01)
    assert scores["stated"] > scores["plain"]
    assert scores["stated"] < scores["sandy"]
    assert boosted_by == {"sandy": ["soil_sandy_coarse"], "stated": ["goal_erosion_control"]}


def test_a_boosted_irrelevant_record_never_outranks_a_first_place_in_both_rankings() -> None:
    every_tag = ("steep_slope", "hydrophobic", "burned_high_severity", "erodible", "sandy_coarse")
    boosts = Boosts(soil_conditions=every_tag, fire_phase=("post_fire_emergency",), stated_goals=("erosion_control",))
    irrelevant_metadata = {
        **{f"soil_{condition}": True for condition in every_tag},
        "phase_post_fire_emergency": True,
        "goal_erosion_control": "stated",
    }
    fused = reciprocal_rank_fusion([["relevant", "irrelevant"], ["relevant"]])
    scores, boosted_by = apply_boosts(fused, {"relevant": {}, "irrelevant": irrelevant_metadata}, boosts)
    assert scores["irrelevant"] == pytest.approx(1 / 62 + MAXIMUM_TOTAL_BOOST / 61)
    assert scores["relevant"] > scores["irrelevant"]
    assert len(boosted_by["irrelevant"]) == len(every_tag) + 2
    best_single_list = {"alone": 1 / 61}
    capped, _ = apply_boosts(best_single_list, {"alone": irrelevant_metadata}, boosts)
    assert capped["alone"] < 2 / 61


def test_collapse_keeps_each_strategys_best_facet() -> None:
    scores = {"s1::overview": 0.02, "s1::how_to": 0.03, "s2::fit": 0.025}
    metadata = {
        "s1::overview": {"strategy_id": "s1", "facet": "overview", "family_id": "f"},
        "s1::how_to": {"strategy_id": "s1", "facet": "how_to", "family_id": "f"},
        "s2::fit": {"strategy_id": "s2", "facet": "fit"},
    }
    hits = collapse_to_strategies(scores, metadata)
    assert [(hit.strategy_id, hit.matched_facet, hit.score) for hit in hits] == [
        ("s1", "how_to", 0.03),
        ("s2", "fit", 0.025),
    ]
    assert hits[0].family_id == "f"
    assert hits[1].family_id is None


def _hit(strategy_id: str, family_id: str | None, score: float) -> StrategyHit:
    return StrategyHit(strategy_id, family_id, score, "overview", f"{strategy_id}::overview")


def test_family_diversity_caps_members_per_family() -> None:
    hits = [_hit("a", "f", 0.9), _hit("b", "f", 0.8), _hit("c", "f", 0.7), _hit("d", "g", 0.6), _hit("e", None, 0.5)]
    capped = diversify_families(hits, cap=2)
    assert [hit.strategy_id for hit in capped.kept] == ["a", "b", "d", "e"]
    assert capped.suppressed == {"f": ["c"]}
    assert [hit.strategy_id for hit in diversify_families(hits, cap=1).kept] == ["a", "d", "e"]
    assert len(diversify_families(hits, cap=0).kept) == len(hits)


def test_page_window_reports_next_offset() -> None:
    items = list(range(5))
    assert page_window(items, 0, 2) == ([0, 1], 2)
    assert page_window(items, 4, 2) == ([4], None)
    assert page_window(items, 3, 2) == ([3, 4], None)


def test_tokenize_drops_stopwords_and_splits_units() -> None:
    expected = ["apply", "2", "tons", "acre", "charboss", "biochar"]
    assert tokenize("Apply 2 tons/acre of the CharBoss biochar") == expected


def test_bm25_applies_the_same_filter() -> None:
    index = LexicalIndex(
        ["cropland-doc", "forest-doc", "general-doc"],
        ["cereal rye cover crop", "cereal rye in forest openings", "rye grows anywhere"],
        [{"lu_cropland": True}, {"lu_forest": True}, {"lu_general": True}],
    )
    expression = build_filter(FilterRequest(land_use=("cropland",)))
    assert index.search("cereal rye", expression, 10) == ["cropland-doc", "general-doc"]
    assert index.search("cereal rye", None, 1) == ["cropland-doc"]
    assert index.search("unrelated words", None, 10) == []


def test_bm25_on_an_empty_collection_returns_nothing() -> None:
    assert LexicalIndex([], [], []).search("anything", None, 5) == []
