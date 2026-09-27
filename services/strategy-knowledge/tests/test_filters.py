"""One filter expression, two evaluators: the Chroma `where` shape and the Python predicate must agree."""

from strategy_knowledge.filters import (
    FilterRequest,
    build_filter,
    describe,
    matches,
    to_where,
)
from strategy_knowledge.vocabulary import NORTH_AMERICAN_REGIONS, REGIONS, broader_regions


def test_land_use_admits_general_and_untagged() -> None:
    expression = build_filter(FilterRequest(land_use=("cropland",)))
    assert to_where(expression) == {
        "$or": [
            {"lu_cropland": {"$eq": True}},
            {"lu_general": {"$eq": True}},
            {"lu_untagged": {"$eq": True}},
        ],
    }
    assert matches(expression, {"lu_cropland": True})
    assert matches(expression, {"lu_general": True})
    assert matches(expression, {"lu_untagged": True})
    assert not matches(expression, {"lu_forest": True})


def test_single_clause_is_unwrapped_and_fields_are_anded() -> None:
    assert to_where(build_filter(FilterRequest(category=("mulching",)))) == {"category": {"$in": ["mulching"]}}
    combined = to_where(build_filter(FilterRequest(category=("mulching",), min_evidence="peer_reviewed_experiment")))
    assert combined == {"$and": [{"category": {"$in": ["mulching"]}}, {"evidence_rank": {"$gte": 5}}]}
    assert build_filter(FilterRequest()) is None
    assert to_where(None) is None


def test_goals_match_stated_or_inferred() -> None:
    expression = build_filter(FilterRequest(goals=("erosion_control",)))
    assert matches(expression, {"goal_erosion_control": "inferred"})
    assert matches(expression, {"goal_erosion_control": "stated"})
    assert not matches(expression, {"goal_soil_health": "stated"})


def test_min_evidence_and_exclusions() -> None:
    expression = build_filter(FilterRequest(min_evidence="field_trial_or_case_study"))
    assert matches(expression, {"evidence_rank": 4})
    assert not matches(expression, {"evidence_rank": 3})
    assert not matches(expression, {})
    excluded = build_filter(FilterRequest(excluded_content_type=("noise",)))
    assert to_where(excluded) == {"content_type": {"$nin": ["noise"]}}
    assert not matches(excluded, {"content_type": "noise"})
    assert matches(excluded, {"content_type": "case_study"})


def test_explicit_content_type_overrides_the_default_exclusion() -> None:
    expression = build_filter(FilterRequest(content_type=("noise",), excluded_content_type=("noise",)))
    assert to_where(expression) == {"content_type": {"$in": ["noise"]}}


def test_strategy_link_filter_and_describe() -> None:
    request = FilterRequest(strategy_ids=("post-fire-straw-mulching",), region=("us_midwest",))
    expression = build_filter(request)
    assert matches(expression, {"linked_post-fire-straw-mulching": True, "region_untagged": True})
    assert not matches(expression, {"linked_post-fire-straw-mulching": True, "region_europe": True})
    echoed = describe(request)
    assert echoed["region"] == ["us_midwest"]
    assert echoed["region_also_admits"] == ["general", "global", "north_america_general", "(untagged)"]
    assert "land_use" not in echoed


def test_a_strategy_scope_matches_the_canonical_id_or_any_alias() -> None:
    expression = build_filter(FilterRequest(strategy_ids=("post-fire-straw-mulching", "post-fire-straw-or-hay")))
    assert matches(expression, {"linked_post-fire-straw-mulching": True})
    assert matches(expression, {"linked_post-fire-straw-or-hay": True})
    assert not matches(expression, {"linked_grass-cover-cropping": True})
    assert to_where(expression) == {
        "$or": [
            {"linked_post-fire-straw-mulching": {"$eq": True}},
            {"linked_post-fire-straw-or-hay": {"$eq": True}},
        ],
    }


def test_a_north_american_region_admits_global_and_north_america_general() -> None:
    inland = build_filter(FilterRequest(region=("pnw_inland",)))
    assert matches(inland, {"region_pnw_inland": True})
    assert matches(inland, {"region_global": True})
    assert matches(inland, {"region_north_america_general": True})
    assert matches(inland, {"region_general": True})
    assert matches(inland, {"region_untagged": True})
    assert not matches(inland, {"region_northern_rockies": True})
    assert not matches(inland, {"region_europe": True})


def test_a_region_outside_north_america_admits_global_only() -> None:
    europe = build_filter(FilterRequest(region=("europe",)))
    assert matches(europe, {"region_global": True})
    assert matches(europe, {"region_general": True})
    assert not matches(europe, {"region_north_america_general": True})
    assert describe(FilterRequest(region=("europe",)))["region_also_admits"] == ["general", "global", "(untagged)"]


def test_the_north_american_set_is_part_of_the_region_vocabulary() -> None:
    assert set(NORTH_AMERICAN_REGIONS) <= set(REGIONS)
    assert "north_america_general" in NORTH_AMERICAN_REGIONS
    assert "europe" not in NORTH_AMERICAN_REGIONS
    assert broader_regions("alaska") == ("general", "global", "north_america_general")
    assert broader_regions("oceania") == ("general", "global")
