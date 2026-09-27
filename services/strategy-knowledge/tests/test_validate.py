"""The ported validators: coverage and overlap, verbatim excerpts, enums, id references, registry rules."""

import copy
from typing import Any

from conftest import SOURCE_ID

from strategy_knowledge.corpus import CorpusStore, is_valid_source_id, read_json, write_json
from strategy_knowledge.validate import (
    coverage_problems,
    excerpt_found,
    merge_ranges,
    normalised_forms,
    raw_forms_lookup,
    strategy_id_space,
    validate_corpus,
    validate_source_block,
    validate_strategy,
)

KNOWN_IDS = {
    "break-hydrophobic-soil-layer",
    "post-fire-straw-mulching",
    "post-fire-hydromulch-application",
    "biochar-amendment-coarse-soils",
    "grass-cover-cropping",
}


def _block(document: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(document["sources"][0])


def _forms_for(raw_lines: list[str]) -> Any:
    forms = normalised_forms("\n".join(raw_lines))
    return lambda source_id: forms if source_id == SOURCE_ID else None


def test_merge_ranges_coalesces_touching_ranges() -> None:
    assert merge_ranges([(5, 9), (1, 3), (4, 4), (12, 15)]) == [(1, 9), (12, 15)]
    assert merge_ranges([]) == []


def test_coverage_reports_overlaps_gaps_and_strays() -> None:
    problems = coverage_problems("source", [(1, 10)], [(1, 4), (4, 8), (11, 12)])
    assert "source: 1 overlapping lines, first [4]" in problems
    assert "source: 2 uncovered lines, first [9, 10]" in problems
    assert "source: 2 outside assignment lines, first [11, 12]" in problems
    assert coverage_problems("source", [(1, 5)], [(1, 2), (3, 5)]) == []


def test_excerpts_survive_line_wrap_hyphens_quotes_and_case() -> None:
    forms = normalised_forms("The effect was signifi-\ncant in “burned” soils.\nUse a tow- behind spreader.")
    assert excerpt_found("the effect was significant", forms)
    assert excerpt_found('in "Burned" soils', forms)
    assert excerpt_found("use a tow-behind spreader", forms)
    assert not excerpt_found("the effect was negligible", forms)
    assert not excerpt_found("   ", forms)


def test_fixture_block_is_clean(fixture_document: dict[str, Any], fixture_raw_lines: list[str]) -> None:
    report = validate_source_block(
        _block(fixture_document),
        fixture_raw_lines,
        [(1, 33)],
        KNOWN_IDS,
        _forms_for(fixture_raw_lines),
    )
    assert report.problems == []
    assert report.stats["chunks"] == 5
    assert report.stats["findings_verified"] == 3
    assert report.stats["candidates_matching_existing"] == 1


def test_broken_block_reports_each_problem(fixture_document: dict[str, Any], fixture_raw_lines: list[str]) -> None:
    block = _block(fixture_document)
    block["chunks"][0]["chunk_id"] = f"{SOURCE_ID}#L7-99"
    block["chunks"][1]["linked_strategy_ids"] = ["no-such-strategy"]
    block["chunks"][2]["content_type"] = "poetry"
    block["findings"][0]["excerpt"] = "straw mulch doubled sediment yield in every single winter"
    del block["chunks"][3]
    report = validate_source_block(block, fixture_raw_lines, [(1, 33)], KNOWN_IDS, _forms_for(fixture_raw_lines))
    joined = "\n".join(report.problems)
    assert "chunk_id does not match" in joined
    assert "unknown linked strategy no-such-strategy" in joined
    assert "content_type='poetry' not in content_type" in joined
    assert "excerpt not at its lines" in joined
    assert "uncovered lines, first [27, 28, 29, 30, 31]" in joined


def test_strategy_rules() -> None:
    record = {
        "strategy_id": "demo",
        "name": "Demo",
        "summary": "A demo.",
        "category": "mulching",
        "land_use": ["general"],
        "region": ["us_midwest"],
        "actions": ["do it"],
        "evidence_strength": "expert_guidance",
        "fire_phase": ["pre_fire"],
        "goals": {"erosion_control": "stated"},
        "sources": [{"source_id": "24-google-ai-mode", "excerpt": "anything"}],
    }
    report = validate_strategy(record, lambda _source_id: None, frozenset())
    joined = "\n".join(report.problems)
    assert "fire_phase set without the wildfire_resilience goal" in joined
    assert "no primary (non-AI) citation" in joined
    assert "region" not in joined


def test_strategy_without_actions_warns_instead_of_failing() -> None:
    record = {
        "strategy_id": "abstract-only",
        "name": "Abstract only",
        "summary": "From a research abstract.",
        "category": "monitoring_assessment",
        "land_use": ["rangeland"],
        "region": ["great_basin_high_desert"],
        "actions": [],
        "evidence_strength": "peer_reviewed_experiment",
        "sources": [],
    }
    report = validate_strategy(record, lambda _source_id: None, frozenset())
    assert not any("actions" in problem for problem in report.problems)
    assert any("no actions" in warning for warning in report.warnings)


def test_excerpt_word_bounds(fixture_document: dict[str, Any], fixture_raw_lines: list[str]) -> None:
    block = _block(fixture_document)
    block["findings"][0]["excerpt"] = "reduced sediment yield by 50-70%"
    block["findings"][1]["excerpt"] = "biochar applied at 5-10% by volume"
    report = validate_source_block(block, fixture_raw_lines, [(1, 33)], KNOWN_IDS, _forms_for(fixture_raw_lines))
    assert any(f"{SOURCE_ID}#F17: excerpt 5 words" in problem for problem in report.problems)
    assert not any(f"{SOURCE_ID}#F23: excerpt" in problem for problem in report.problems)
    assert any(f"{SOURCE_ID}#F23: excerpt 6 words" in warning for warning in report.warnings)
    long_excerpt = " ".join(["word"] * 26)
    block["findings"][2]["excerpt"] = long_excerpt
    report = validate_source_block(block, fixture_raw_lines, [(1, 33)], KNOWN_IDS, _forms_for(fixture_raw_lines))
    assert any(f"{SOURCE_ID}#F30: excerpt 26 words" in problem for problem in report.problems)


def test_a_candidate_may_not_cite_the_ai_synthesis_source(
    fixture_document: dict[str, Any],
    fixture_raw_lines: list[str],
) -> None:
    block = _block(fixture_document)
    candidate = block["candidate_strategies"][0]
    candidate["sources"].append({"source_id": "24-google-ai-mode-synthesis", "excerpt": "anything at all goes here"})
    report = validate_source_block(block, fixture_raw_lines, [(1, 33)], KNOWN_IDS, _forms_for(fixture_raw_lines))
    assert any("cites the AI-synthesis source 24-google-ai-mode-synthesis" in problem for problem in report.problems)


def test_once_a_registry_exists_links_must_resolve_into_it() -> None:
    registry_rows = [{"strategy_id": "kept", "merged_from": ["absorbed"]}]
    space = strategy_id_space(registry_rows, [("matched-candidate", "absorbed"), ("new-candidate", None)], {"stale"})
    assert space.registry_present
    assert space.existing == {"kept", "absorbed"}
    assert space.linkable == {"kept", "absorbed", "matched-candidate"}
    assert strategy_id_space([], [], {"from-file"}).linkable == {"from-file"}


def test_stored_links_to_an_unreconciled_candidate_are_reported(fixture_store: CorpusStore) -> None:
    plan_path = fixture_store.plan_path(SOURCE_ID)
    plan = read_json(plan_path)
    plan["chunks"][0]["linked_strategy_ids"] = ["winter-cereal-rye-cover", "never-reconciled"]
    write_json(plan_path, plan)
    joined = "\n".join(validate_corpus(fixture_store, SOURCE_ID).problems)
    assert "unknown linked strategy never-reconciled" in joined
    assert "unknown linked strategy winter-cereal-rye-cover" not in joined


def test_source_ids_follow_the_pattern() -> None:
    assert is_valid_source_id("17b-wsu-fs069e-global-climate-change")
    assert is_valid_source_id("20261003-nrcs-cover-crop-guide-a1b2c3")
    for invalid in ("ab", "../escape", "Upper-case", "has space", "a/b", "-leading-hyphen", "x" * 122):
        assert not is_valid_source_id(invalid)


def test_stored_fixture_corpus_validates(fixture_store: CorpusStore) -> None:
    report = validate_corpus(fixture_store)
    assert report.problems == []
    assert report.stats["registry_strategies"] == 5


def test_raw_change_invalidates_the_plan(fixture_store: CorpusStore) -> None:
    raw_path = fixture_store.raw_path(SOURCE_ID)
    raw_path.write_text(raw_path.read_text(encoding="utf-8") + "\nAppended line.", encoding="utf-8", newline="\n")
    report = validate_corpus(fixture_store, SOURCE_ID)
    assert any("raw file sha256 differs from sources.json" in problem for problem in report.problems)


def test_raw_forms_lookup_is_cached_and_tolerates_unknown_sources(fixture_store: CorpusStore) -> None:
    lookup = raw_forms_lookup(fixture_store)
    assert lookup("unknown-source") is None
    assert lookup(SOURCE_ID) is lookup(SOURCE_ID)


def test_magnitude_numbers_must_be_printed_near_the_finding(
    fixture_document: dict[str, Any],
    fixture_raw_lines: list[str],
) -> None:
    clean = validate_source_block(
        _block(fixture_document),
        fixture_raw_lines,
        [(1, 33)],
        KNOWN_IDS,
        _forms_for(fixture_raw_lines),
    )
    assert not any("magnitude" in warning for warning in clean.warnings)
    block = _block(fixture_document)
    block["findings"][0]["magnitude"] = "reduced sediment yield by 55-70%"
    report = validate_source_block(block, fixture_raw_lines, [(1, 33)], KNOWN_IDS, _forms_for(fixture_raw_lines))
    assert report.problems == []
    assert any(f"{SOURCE_ID}#F17: magnitude number(s) ['55']" in warning for warning in report.warnings)


def test_a_repeated_candidate_with_two_match_targets_is_a_problem(
    fixture_document: dict[str, Any],
    fixture_raw_lines: list[str],
) -> None:
    block = _block(fixture_document)
    row = block["candidate_strategies"][0]
    block["candidate_strategies"] = [row, {**row, "matches_existing": "post-fire-straw-mulching"}]
    report = validate_source_block(block, fixture_raw_lines, [(1, 33)], KNOWN_IDS, _forms_for(fixture_raw_lines))
    assert any("candidate winter-cereal-rye-cover is repeated with conflicting" in p for p in report.problems)
