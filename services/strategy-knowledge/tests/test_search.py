"""Ranking primitives: weighted reciprocal-rank fusion, boosts, facet collapse, family diversity, paging, BM25."""

import pytest

from strategy_knowledge.filters import FilterRequest, build_filter
from strategy_knowledge.lexical import LexicalIndex, lexical_terms, stem, tokenize
from strategy_knowledge.search import (
    BOOST_WEIGHT,
    MAXIMUM_TOTAL_BOOST,
    PH_DEMOTION_WEIGHT,
    Boosts,
    FusionWeights,
    StrategyHit,
    apply_boosts,
    collapse_ranking,
    diversify_families,
    fuse_strategies,
    page_window,
    rank_documents,
    reciprocal_rank_fusion,
    strategy_hits,
    weighted_reciprocal_rank_fusion,
)

FACET_METADATA = {
    "s1::overview": {"strategy_id": "s1", "facet": "overview", "family_id": "f"},
    "s1::how_to": {"strategy_id": "s1", "facet": "how_to", "family_id": "f"},
    "s2::fit": {"strategy_id": "s2", "facet": "fit"},
}


def test_reciprocal_rank_fusion_uses_k_60() -> None:
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"]])
    assert fused["a"] == pytest.approx(1 / 61)
    assert fused["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert fused["c"] == pytest.approx(1 / 62)
    assert [identifier for identifier, _ in rank_documents(fused)] == ["b", "a", "c"]


def test_weighted_fusion_scales_each_list() -> None:
    fused = weighted_reciprocal_rank_fusion([(["a", "b"], 1.0), (["b"], 0.5)])
    assert fused["a"] == pytest.approx(1 / 61)
    assert fused["b"] == pytest.approx(1 / 62 + 0.5 / 61)


def test_fusion_weights_add_the_context_lists_at_a_multiple() -> None:
    weights = FusionWeights(dense=1.0, lexical=0.8, context_query=0.5)
    assert weights.weighted((["d"], ["l"])) == [(["d"], 1.0), (["l"], 0.8)]
    with_context = weights.weighted((["d"], ["l"]), (["cd"], ["cl"]))
    assert with_context[2] == (["cd"], 0.5)
    assert with_context[3][0] == ["cl"]
    assert with_context[3][1] == pytest.approx(0.4)


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


def test_query_intent_soil_conditions_boost_like_site_profile_ones() -> None:
    boosts = Boosts(soil_conditions=("acidic",), query_intent_soil_conditions=("acidic", "compacted"))
    scores, boosted_by = apply_boosts({"lime": 0.01}, {"lime": {"soil_acidic": True}}, boosts)
    assert scores["lime"] == pytest.approx(0.01 + BOOST_WEIGHT / 61)
    assert boosted_by["lime"] == ["soil_acidic"]
    assert boosts.as_response()["query_intent_soil_conditions"] == ["acidic", "compacted"]
    # Both boost the score (line above); only the pH condition is exempt from the family-diversity cap below.
    assert boosts.diversity_exempt_tags == ("soil_acidic",)


def test_diversity_exemption_is_narrower_than_the_boosted_conditions() -> None:
    # A non-pH condition ("erodible") can tag dozens of strategies in one family; exempting it too would
    # defeat family diversity on a bare "washed out" mention (AGENTS.md "Retrieval", 2026-09-27 review).
    assert Boosts(query_intent_soil_conditions=("erodible",)).diversity_exempt_tags == ()
    assert Boosts(query_intent_soil_conditions=("erodible", "alkaline")).diversity_exempt_tags == ("soil_alkaline",)


def test_the_opposite_ph_condition_is_demoted_unless_the_record_also_matches_the_intent() -> None:
    boosts = Boosts(query_intent_soil_conditions=("acidic",), query_intent_soil_demotions=("alkaline",))
    metadata = {"sulfur": {"soil_alkaline": True}, "both": {"soil_alkaline": True, "soil_acidic": True}, "plain": {}}
    scores, boosted_by = apply_boosts({"sulfur": 0.02, "both": 0.02, "plain": 0.02}, metadata, boosts)
    assert scores["sulfur"] == pytest.approx(0.02 - PH_DEMOTION_WEIGHT / 61)
    assert scores["both"] == pytest.approx(0.02 + BOOST_WEIGHT / 61)
    assert scores["plain"] == pytest.approx(0.02)
    assert boosted_by["sulfur"] == ["-soil_alkaline"]
    assert boosts.as_response()["query_intent_soil_demotions"] == ["alkaline"]


def test_collapse_ranking_keeps_each_strategy_at_its_best_facet() -> None:
    ranking = ["s1::how_to", "s2::fit", "s1::overview", "orphan"]
    assert collapse_ranking(ranking, FACET_METADATA) == ["s1", "s2"]


def test_a_strategy_counts_once_per_list_however_many_facets_match() -> None:
    dense = ["s1::overview", "s1::how_to", "s2::fit"]
    lexical = ["s2::fit", "s1::how_to"]
    scores, best = fuse_strategies([(dense, 1.0), (lexical, 1.0)], FACET_METADATA)
    assert scores["s1"] == pytest.approx(1 / 61 + 1 / 62)
    assert scores["s2"] == pytest.approx(1 / 62 + 1 / 61)
    assert best == {"s1": "s1::how_to", "s2": "s2::fit"}


def test_strategy_hits_report_the_best_facet_and_family() -> None:
    hits = strategy_hits({"s1": 0.03, "s2": 0.025}, {"s1": "s1::how_to", "s2": "s2::fit"}, FACET_METADATA)
    assert [(hit.strategy_id, hit.matched_facet, hit.score) for hit in hits] == [
        ("s1", "how_to", 0.03),
        ("s2", "fit", 0.025),
    ]
    assert hits[0].family_id == "f"
    assert hits[1].family_id is None
    assert not any(hit.exempt_from_diversity for hit in hits)


def _hit(strategy_id: str, family_id: str | None, score: float, *, exempt: bool = False) -> StrategyHit:
    return StrategyHit(
        strategy_id,
        family_id,
        score,
        "overview",
        f"{strategy_id}::overview",
        exempt_from_diversity=exempt,
    )


def test_family_diversity_caps_members_per_family() -> None:
    hits = [_hit("a", "f", 0.9), _hit("b", "f", 0.8), _hit("c", "f", 0.7), _hit("d", "g", 0.6), _hit("e", None, 0.5)]
    capped = diversify_families(hits, cap=2)
    assert [hit.strategy_id for hit in capped.kept] == ["a", "b", "d", "e"]
    assert capped.suppressed == {"f": ["c"]}
    assert [hit.strategy_id for hit in diversify_families(hits, cap=1).kept] == ["a", "d", "e"]
    assert len(diversify_families(hits, cap=0).kept) == len(hits)


def test_an_intent_boosted_hit_skips_the_family_cap_and_takes_no_slot() -> None:
    hits = [_hit("sulfur", "f", 0.03), _hit("other", "f", 0.02), _hit("lime", "f", 0.014, exempt=True)]
    capped = diversify_families(hits, cap=1)
    assert [hit.strategy_id for hit in capped.kept] == ["sulfur", "lime"]
    assert capped.suppressed == {"f": ["other"]}


def test_strategy_hits_mark_hits_boosted_by_an_exempt_tag() -> None:
    boosts = Boosts(query_intent_soil_conditions=("acidic",))
    scores, boosted_by = apply_boosts({"lime": 0.01, "other": 0.02}, {"lime": {"soil_acidic": True}}, boosts)
    best = {"lime": "lime::fit", "other": "other::fit"}
    facets = {"lime::fit": {"strategy_id": "lime", "facet": "fit"}, "other::fit": {"strategy_id": "other"}}
    hits = strategy_hits(scores, best, facets, boosted_by, boosts.diversity_exempt_tags)
    assert {hit.strategy_id: hit.exempt_from_diversity for hit in hits} == {"lime": True, "other": False}


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


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("crops", "crop"),
        ("mulches", "mulch"),
        ("mulching", "mulch"),
        ("mulched", "mulch"),
        ("cropping", "crop"),
        ("tilling", "till"),
        ("strategies", "strategy"),
        ("grasses", "grass"),
        ("analysis", "analysis"),
        ("seed", "seed"),
        ("burned", "burned"),
        ("burnt", "burnt"),
        ("burning", "burn"),
        ("ph", "ph"),
        ("2024", "2024"),
    ],
)
def test_light_stemming_folds_plurals_and_verb_forms_but_keeps_burned(word: str, expected: str) -> None:
    assert stem(word) == expected


def test_burn_and_burned_stay_distinct_for_bm25() -> None:
    assert lexical_terms("prescribed burns")[-1] == "burn"
    assert lexical_terms("burned slopes")[0] == "burned"
    assert stem("lime") == stem("liming") == stem("limed")


def test_bm25_matches_across_plural_and_verb_forms() -> None:
    index = LexicalIndex(
        ["mulch-doc", "grazing-doc", "compost-doc", "cover-doc"],
        ["mulching burned slopes", "grazing cattle", "compost piles", "cover crop rye"],
        [{}, {}, {}, {}],
    )
    assert index.search("mulches on a slope", None, 10) == ["mulch-doc"]


def test_expansion_tokens_add_a_weighted_second_query() -> None:
    index = LexicalIndex(
        ["lime-doc", "sulfur-doc", "filler-doc"],
        ["agricultural limestone raises soil ph", "elemental sulfur lowers soil ph", "compost pile turning"],
        [{}, {}, {}],
    )
    assert index.search("sour ground", None, 10) == []
    assert index.search("sour ground", None, 10, expansion_tokens=("limestone",)) == ["lime-doc"]
    assert index.search("soil ph", None, 10, expansion_tokens=("limestone",))[0] == "lime-doc"
    unexpanded = index.search("soil ph", None, 10, expansion_tokens=("limestone",), expansion_weight=0.0)
    assert set(unexpanded) == {"lime-doc", "sulfur-doc"}
