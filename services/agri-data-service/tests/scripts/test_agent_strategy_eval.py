"""Deterministic scoring for the strategy-knowledge agent eval harness, on canned transcripts only.

No network, no LLM, and no `agri_data_service` import: the script defers every such import into a
function body until AFTER `STRATEGY_KNOWLEDGE_URL` lands in `os.environ` (see its module docstring),
so `load_scripts_module` executing this file at collection time must never trip that wire. Every
tool-result fixture below mirrors the real payload shape `agent/strategy_knowledge.py::ask`/`refusal`
builds (`tool`, `error`/`note`, `results`/`strategies` with `strategy_id`/`family_id`/`name`), so a
shape drift there would also break these tests, not just the harness.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

import pytest

from tests.scripts import load_scripts_module

if TYPE_CHECKING:
    from pathlib import Path

EVAL = load_scripts_module("agent_strategy_eval.py", "plantgeo_agent_strategy_eval")

POST_FIRE_MULCHING_HIT: Final = {
    "strategy_id": "post-fire-straw-mulching",
    "family_id": "post-fire-mulching",
    "name": "Post-fire straw mulching",
}
LIME_HIT: Final = {
    "strategy_id": "lime-application-acidic-soils",
    "family_id": "soil-chemistry-correction",
    "name": "Agricultural lime application to correct soil acidity",
}
SULFUR_HIT: Final = {
    "strategy_id": "elemental-sulfur-soil-acidification",
    "family_id": "soil-chemistry-correction",
    "name": "Elemental sulfur application to lower soil pH for acid-loving crops",
}
COVER_CROP_HIT: Final = {
    "strategy_id": "legume-cover-cropping",
    "family_id": "cover-cropping",
    "name": "Legume cover cropping",
}


def _tool_message(name: str, content: dict[str, Any] | str) -> dict[str, Any]:
    """One `role: "tool"` transcript entry, matching what `agent/llm.py::execute_tool_call` renders."""
    body = content if isinstance(content, str) else json.dumps(content)
    return {"role": "tool", "tool_call_id": "call_1", "name": name, "content": body}


def _search_result(*hits: dict[str, Any]) -> dict[str, Any]:
    """A `search_environmental_strategies` result payload, in `agent/strategy_knowledge.py`'s real shape."""
    return {
        "tool": "search_environmental_strategies",
        "evidence_domain": "literature_reference",
        "claim_tier": "literature_grounded",
        "corpus_version": "test-corpus",
        "results": list(hits),
        "result_count": len(hits),
    }


def _scenario(**overrides: Any) -> Any:
    payload: dict[str, Any] = {
        "id": "test-scenario",
        "longitude": -116.0,
        "latitude": 43.0,
        "question": "What should I do?",
        "expect_strategy_tool": True,
        "expected_family_ids": ["post-fire-mulching"],
        "forbidden_strategy_ids": [],
        "notes": "",
    }
    payload.update(overrides)
    return EVAL._scenario_from_json(payload)


def test_scenario_passes_on_a_family_hit_named_in_the_answer_with_no_hallucination() -> None:
    scenario = _scenario()
    final_text = "Research supports applying post-fire-straw-mulching to the burned slope right away."
    transcript = [
        {"role": "user", "content": "..."},
        _tool_message("search_environmental_strategies", _search_result(POST_FIRE_MULCHING_HIT)),
        {"role": "assistant", "content": final_text},
    ]
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert score.passed
    assert score.strategy_tool_called
    assert score.strategy_tool_attempted_count == 1
    assert score.strategy_tool_succeeded_count == 1
    assert score.warehouse_tool_call_count == 0
    assert score.retrieved_family_hit
    assert score.family_hit
    assert not score.hallucinated_ids
    assert not score.forbidden_hit
    assert score.literature_attribution
    assert not score.unsupported_percentages
    assert score.retrieved_strategy_ids == ("post-fire-straw-mulching",)
    assert score.retrieved_family_ids == ("post-fire-mulching",)


def test_family_hit_requires_the_answer_to_actually_name_the_retrieved_strategy() -> None:
    """Retrieving the right family is not enough: rule 4 requires the ANSWER to name it."""
    scenario = _scenario()
    transcript = [_tool_message("search_environmental_strategies", _search_result(POST_FIRE_MULCHING_HIT))]
    final_text = "Get a professional out to look at the slope before winter."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert score.retrieved_family_hit  # the retrieval itself was in the right family ...
    assert not score.family_hit  # ... but the answer never actually names it
    assert not score.passed
    assert any("family_id" in reason for reason in score.reasons)


def test_scenario_fails_on_a_family_miss() -> None:
    scenario = _scenario(expected_family_ids=["soil-chemistry-correction"])
    transcript = [_tool_message("search_environmental_strategies", _search_result(COVER_CROP_HIT))]
    score = EVAL.score_transcript(scenario, "Plant a legume cover crop.", transcript)
    assert not score.passed
    assert score.strategy_tool_called
    assert not score.retrieved_family_hit
    assert not score.family_hit
    assert any("family_id" in reason for reason in score.reasons)


def test_hallucination_flags_a_real_registry_id_named_in_plain_prose() -> None:
    """A genuine corpus id is unambiguous regardless of formatting -- no backticks/parens needed."""
    scenario = _scenario()
    transcript = [_tool_message("search_environmental_strategies", _search_result(POST_FIRE_MULCHING_HIT))]
    final_text = "You could also consider contour-log-erosion-barriers for the slope."
    score = EVAL.score_transcript(
        scenario,
        final_text,
        transcript,
        registry_strategy_ids=frozenset({"post-fire-straw-mulching", "contour-log-erosion-barriers"}),
    )
    assert "contour-log-erosion-barriers" in score.hallucinated_ids
    # The actually-retrieved id is never treated as a hallucination just for being mentioned.
    assert "post-fire-straw-mulching" not in score.hallucinated_ids


def test_hallucination_ignores_a_plain_prose_compound_not_set_off_as_an_identifier() -> None:
    """ "state-of-the-art" is kebab-shaped but never wrapped in backticks/parens by a real id quote."""
    scenario = _scenario()
    transcript = [_tool_message("search_environmental_strategies", _search_result(POST_FIRE_MULCHING_HIT))]
    final_text = "This is a state-of-the-art, research-backed approach using post-fire-straw-mulching."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert not score.hallucinated_ids


def test_hallucination_requires_identifier_formatting_for_an_unknown_token() -> None:
    """An unknown (non-registry) token in plain prose is not flagged; the same token quoted as an
    identifier, and never retrieved or seen anywhere else, is."""
    scenario = _scenario()
    transcript = [_tool_message("search_environmental_strategies", _search_result(POST_FIRE_MULCHING_HIT))]
    unwrapped = "Try prescribed-goat-grazing-rotation, a technique never returned by any tool call."
    wrapped = "Try `prescribed-goat-grazing-rotation`, a technique never returned by any tool call."
    assert not EVAL.score_transcript(scenario, unwrapped, transcript).hallucinated_ids
    score = EVAL.score_transcript(scenario, wrapped, transcript)
    assert "prescribed-goat-grazing-rotation" in score.hallucinated_ids


def test_hallucination_normalises_underscores_against_a_tool_results_field_value() -> None:
    """`review_or_meta_analysis` (a raw `study_type` value) and the model's own hyphenated spelling
    of it must compare equal, or a model merely echoing the field back gets flagged as inventing it."""
    scenario = _scenario()
    finding_payload = {
        "tool": "search_strategy_research_findings",
        "results": [
            {
                "finding_id": "f9",
                "study_type": "review_or_meta_analysis",
                "claim": "Straw mulch reduced post-fire sediment yield.",
                "linked_strategy_ids": ["post-fire-straw-mulching"],
            }
        ],
    }
    transcript = [_tool_message("search_strategy_research_findings", finding_payload)]
    final_text = "This is a `review-or-meta-analysis` finding worth trusting."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert "review-or-meta-analysis" not in score.hallucinated_ids


def test_hallucination_exempts_a_token_present_in_the_context_text() -> None:
    """`context_text` carries what the model saw outside the transcript (the prompt, the published
    tool schemas) -- a date-format template quoted back from there is not an invented id."""
    scenario = _scenario(expect_strategy_tool=False, expected_family_ids=[])
    final_text = "Please provide the dates (YYYY-MM-DD) so I can proceed."
    transcript = [{"role": "assistant", "content": final_text}]
    score = EVAL.score_transcript(
        scenario,
        final_text,
        transcript,
        context_text="...day: the calendar day to answer for, as ISO YYYY-MM-DD...",
    )
    assert "yyyy-mm-dd" not in score.hallucinated_ids


def test_answer_names_a_compound_registry_name_via_its_first_segment() -> None:
    """Regression for the live boise-foothills-post-fire transcript: Haiku recommended the
    retrieved, expected-family strategy under a shortened heading ("Contour Log Erosion Barriers")
    that only spells out the first " / "-separated segment of the real compound registry name."""
    contour_log_hit = {
        "strategy_id": "contour-log-erosion-barriers",
        "family_id": "post-fire-erosion-barriers",
        "name": "Contour log erosion barriers / log terraces",
    }
    scenario = _scenario(expected_family_ids=["post-fire-erosion-barriers"])
    transcript = [_tool_message("search_environmental_strategies", _search_result(contour_log_hit))]
    final_text = (
        "### **1. Contour Log Erosion Barriers (Priority if you have materials)**\n\n"
        "Research supports this as a first-year post-fire treatment."
    )
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert score.family_hit
    assert score.passed


def test_answer_names_a_long_registry_name_via_scattered_significant_tokens() -> None:
    """Regression for the live western-wa-sour-pasture transcript: Haiku split "Agricultural lime
    application to correct soil acidity" across a heading and separate bullets. Neither the id nor
    the full name appears on one line, but enough of the name's significant tokens appear across the
    answer for the 80% token-overlap rule to count it as named."""
    scenario = _scenario(expected_family_ids=["soil-chemistry-correction"])
    transcript = [_tool_message("search_environmental_strategies", _search_result(LIME_HIT))]
    final_text = (
        "### **1. Agricultural Lime Application (Primary Strategy)**\n\n"
        "According to the literature, apply agricultural limestone at the rate your soil lab "
        "recommends.\n\n"
        "Once you correct pH, resample to catch acidity early next season."
    )
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert score.family_hit
    assert score.passed


def test_name_token_overlap_does_not_match_on_a_single_shared_word() -> None:
    """A paraphrase sharing exactly one word with a multi-token registry name must not clear the
    80% overlap threshold -- otherwise any answer mentioning "cover" at all would count as naming
    "Legume cover cropping"."""
    scenario = _scenario(expected_family_ids=["cover-cropping"])
    transcript = [_tool_message("search_environmental_strategies", _search_result(COVER_CROP_HIT))]
    final_text = "Improving pasture cover this season will help reduce erosion overall."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert not score.family_hit
    assert not score.passed


def test_family_hit_excludes_a_forbidden_strategy_even_when_its_description_clears_the_token_threshold() -> None:
    """Regression for the described false-PASS: a forbidden strategy's own significant name tokens,
    spelled out at length in a sentence that is entirely advice AGAINST it, used to clear the 80%
    token-overlap threshold and register as a family hit purely because it shares the expected family
    with a legitimate strategy -- turning an answer that only tells the user to AVOID the forbidden
    strategy into a scored PASS. A forbidden strategy can never satisfy `family_hit`."""
    scenario = _scenario(
        expected_family_ids=["soil-chemistry-correction"],
        forbidden_strategy_ids=["elemental-sulfur-soil-acidification"],
    )
    transcript = [_tool_message("search_environmental_strategies", _search_result(SULFUR_HIT))]
    final_text = (
        "Research shows you should avoid elemental sulfur application to lower soil pH; this "
        "acid-loving crops treatment will harm your yield."
    )
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert not score.forbidden_hit  # properly negated -- not the bug under test here
    assert not score.family_hit
    assert not score.passed


def test_forbidden_hit_detects_a_compound_names_segment_not_just_its_id() -> None:
    """`_forbidden_hit` uses the same NAMED logic as a family-hit: naming a forbidden strategy by a
    segment of its compound registry name is still a recommendation of it, not just its bare id."""
    contour_log_hit = {
        "strategy_id": "contour-log-erosion-barriers",
        "family_id": "post-fire-erosion-barriers",
        "name": "Contour log erosion barriers / log terraces",
    }
    scenario = _scenario(forbidden_strategy_ids=["contour-log-erosion-barriers"])
    transcript = [
        _tool_message("search_environmental_strategies", _search_result(contour_log_hit, POST_FIRE_MULCHING_HIT))
    ]
    final_text = (
        "Research supports post-fire-straw-mulching for your slope. "
        "Contour Log Erosion Barriers could also help stabilize the hillside."
    )
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert score.forbidden_hit
    assert not score.passed


def test_forbidden_hit_negation_still_applies_to_a_name_variant_match() -> None:
    """The negation guard scopes to the sentence carrying the NAME VARIANT match, same as it already
    does for a bare id match."""
    contour_log_hit = {
        "strategy_id": "contour-log-erosion-barriers",
        "family_id": "post-fire-erosion-barriers",
        "name": "Contour log erosion barriers / log terraces",
    }
    scenario = _scenario(forbidden_strategy_ids=["contour-log-erosion-barriers"])
    transcript = [
        _tool_message("search_environmental_strategies", _search_result(contour_log_hit, POST_FIRE_MULCHING_HIT))
    ]
    final_text = (
        "Research supports post-fire-straw-mulching for your slope. "
        "Avoid Contour Log Erosion Barriers due to anchoring failures."
    )
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert not score.forbidden_hit
    assert score.family_hit
    assert score.passed


def test_scenario_fails_on_a_forbidden_strategy_id_named_in_the_answer() -> None:
    scenario = _scenario(
        expected_family_ids=["soil-chemistry-correction"],
        forbidden_strategy_ids=["elemental-sulfur-soil-acidification"],
    )
    transcript = [_tool_message("search_environmental_strategies", _search_result(LIME_HIT, SULFUR_HIT))]
    final_text = "Apply elemental-sulfur-soil-acidification to fix the sour soil."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert not score.passed
    assert score.forbidden_hit


def test_scenario_fails_on_a_forbidden_strategys_retrieved_name_quoted_in_the_answer() -> None:
    scenario = _scenario(
        expected_family_ids=["soil-chemistry-correction"],
        forbidden_strategy_ids=["elemental-sulfur-soil-acidification"],
    )
    transcript = [_tool_message("search_environmental_strategies", _search_result(LIME_HIT, SULFUR_HIT))]
    final_text = f'Use "{SULFUR_HIT["name"]}" here, since the soil needs a lower pH.'
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert not score.passed
    assert score.forbidden_hit


def test_forbidden_strategy_retrieved_but_not_named_is_not_a_hit() -> None:
    """Retrieval alone is not penalised -- only naming a forbidden strategy in the answer is."""
    scenario = _scenario(
        expected_family_ids=["soil-chemistry-correction"],
        forbidden_strategy_ids=["elemental-sulfur-soil-acidification"],
    )
    transcript = [_tool_message("search_environmental_strategies", _search_result(LIME_HIT, SULFUR_HIT))]
    final_text = "Research recommends applying lime-application-acidic-soils to raise the pH."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert not score.forbidden_hit
    assert score.passed


def test_forbidden_hit_fires_even_when_the_forbidden_strategy_was_never_retrieved() -> None:
    """Rule 5: a forbidden id recommended in the answer is a hit regardless of retrieval."""
    scenario = _scenario(
        expected_family_ids=["soil-chemistry-correction"],
        forbidden_strategy_ids=["elemental-sulfur-soil-acidification"],
    )
    transcript = [_tool_message("search_environmental_strategies", _search_result(LIME_HIT))]
    final_text = "Consider elemental-sulfur-soil-acidification as an alternative treatment."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert score.forbidden_hit


def test_forbidden_hit_excludes_a_negated_mention_in_the_same_sentence() -> None:
    scenario = _scenario(
        expected_family_ids=["soil-chemistry-correction"],
        forbidden_strategy_ids=["elemental-sulfur-soil-acidification"],
    )
    transcript = [_tool_message("search_environmental_strategies", _search_result(LIME_HIT, SULFUR_HIT))]
    final_text = "Avoid elemental-sulfur-soil-acidification here; apply lime-application-acidic-soils instead."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert not score.forbidden_hit


def test_forbidden_hit_still_fires_when_the_negation_is_in_a_different_sentence() -> None:
    scenario = _scenario(
        expected_family_ids=["soil-chemistry-correction"],
        forbidden_strategy_ids=["elemental-sulfur-soil-acidification"],
    )
    transcript = [_tool_message("search_environmental_strategies", _search_result(LIME_HIT, SULFUR_HIT))]
    final_text = "Avoid low-pH amendments overall. Apply elemental-sulfur-soil-acidification to fix it."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert score.forbidden_hit


def test_forbidden_hit_requires_the_negation_to_govern_the_mention_not_just_share_a_sentence() -> None:
    """Regression: a negation marker appearing well AFTER the forbidden mention, governing an
    unrelated later clause ("do not apply when wet"), must not launder the earlier recommendation
    into a non-hit. The old whole-sentence "any negation marker anywhere" check let this through."""
    elemental_sulfur_hit = {
        "strategy_id": "elemental-sulfur",
        "family_id": "soil-chemistry-correction",
        "name": "Elemental sulfur",
    }
    scenario = _scenario(
        expected_family_ids=["soil-chemistry-correction"],
        forbidden_strategy_ids=["elemental-sulfur"],
    )
    transcript = [_tool_message("search_environmental_strategies", _search_result(elemental_sulfur_hit))]
    final_text = "Apply elemental sulfur at 200 lb/ac; do not apply when wet."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert score.forbidden_hit
    assert not score.passed


def test_control_scenario_passes_when_no_strategy_tool_is_called() -> None:
    scenario = _scenario(expect_strategy_tool=False, expected_family_ids=[])
    transcript = [_tool_message("observation_coverage_on_day", {"state": "found", "max_air_temperature_c": 21.4})]
    score = EVAL.score_transcript(scenario, "The maximum air temperature was 21.4C.", transcript)
    assert score.passed
    assert not score.strategy_tool_called
    assert score.warehouse_tool_call_count == 1


def test_control_scenario_fails_when_a_strategy_tool_is_called_anyway() -> None:
    scenario = _scenario(expect_strategy_tool=False, expected_family_ids=[])
    transcript = [_tool_message("search_environmental_strategies", _search_result(POST_FIRE_MULCHING_HIT))]
    score = EVAL.score_transcript(scenario, "You should mulch.", transcript)
    assert not score.passed
    assert score.strategy_tool_called


def test_named_refusal_reason_is_detected_regardless_of_which_tool_returned_it() -> None:
    scenario = _scenario()
    refusal_payload = {
        "tool": "search_environmental_strategies",
        "error": "strategy_knowledge_unavailable",
        "refusal_detail": "the request failed (ConnectError)",
        "note": "This is a REFUSAL, not an absence. The strategy-knowledge literature service could not answer.",
    }
    transcript = [_tool_message("search_environmental_strategies", refusal_payload)]
    score = EVAL.score_transcript(scenario, "The strategy service could not be reached.", transcript)
    assert "strategy_knowledge_unavailable" in score.refusals_seen
    assert score.strategy_tool_called  # a refusal is still a tool call, not a missing one
    assert not score.retrieved_strategy_ids


def test_unnamed_refusal_text_alone_is_still_detected() -> None:
    scenario = _scenario()
    transcript = [_tool_message("search_environmental_strategies", "This is a REFUSAL, not an absence. Odd shape.")]
    score = EVAL.score_transcript(scenario, "No literature could be checked.", transcript)
    assert score.refusals_seen == ("unnamed_refusal",)


def test_expect_strategy_tool_requires_a_successful_call_not_just_an_attempt() -> None:
    """Rule 6: a refused strategy tool call is still an attempt, but does not satisfy the scenario."""
    scenario = _scenario()
    refusal_payload = {
        "tool": "search_environmental_strategies",
        "error": "strategy_knowledge_unavailable",
        "note": "This is a REFUSAL, not an absence. The strategy-knowledge literature service could not answer.",
    }
    transcript = [_tool_message("search_environmental_strategies", refusal_payload)]
    score = EVAL.score_transcript(scenario, "The literature service could not be reached.", transcript)
    assert score.strategy_tool_attempted_count == 1
    assert score.strategy_tool_succeeded_count == 0
    assert not score.passed
    assert any("0 succeeded" in reason for reason in score.reasons)


def test_a_generic_tool_execution_error_also_counts_as_not_succeeded() -> None:
    """`execute_tool_call`'s own `{"error": ..., "tool": ...}` shape for an uncaught exception is
    not a typed refusal, but must still count as an unsuccessful call."""
    scenario = _scenario()
    error_payload = {"error": "ValueError: bad radius", "tool": "search_environmental_strategies"}
    transcript = [_tool_message("search_environmental_strategies", error_payload)]
    score = EVAL.score_transcript(scenario, "Something went wrong.", transcript)
    assert score.strategy_tool_attempted_count == 1
    assert score.strategy_tool_succeeded_count == 0
    assert not score.passed


def test_literature_attribution_required_when_a_strategy_tool_succeeded() -> None:
    """Rule 7: a successful strategy tool call without any literature-attributed language fails."""
    scenario = _scenario()
    transcript = [_tool_message("search_environmental_strategies", _search_result(POST_FIRE_MULCHING_HIT))]
    final_text = "Apply post-fire-straw-mulching to the burned slope right away."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert not score.literature_attribution
    assert not score.passed
    assert any("literature attribution" in reason for reason in score.reasons)


def test_finding_results_carry_linked_strategy_ids_not_a_bare_strategy_id() -> None:
    finding_payload = {
        "tool": "search_strategy_research_findings",
        "results": [
            {
                "finding_id": "f1",
                "claim": "Straw mulch reduced post-fire sediment yield.",
                "linked_strategy_ids": ["post-fire-straw-mulching"],
            }
        ],
    }
    scenario = _scenario()
    transcript = [_tool_message("search_strategy_research_findings", finding_payload)]
    score = EVAL.score_transcript(scenario, "Straw mulch reduces sediment yield after fire.", transcript)
    assert score.retrieved_strategy_ids == ("post-fire-straw-mulching",)
    # A finding never carries a family_id, so it cannot itself produce a family hit.
    assert not score.retrieved_family_hit
    assert not score.family_hit


def test_provider_error_fails_the_scenario_even_when_otherwise_a_control_pass() -> None:
    """Rule 3: a provider error must never score as a vacuous control-scenario pass."""
    scenario = _scenario(expect_strategy_tool=False, expected_family_ids=[])
    score = EVAL.score_transcript(scenario, "", [], provider_error="ConnectError: timed out")
    assert not score.passed
    assert any("provider error" in reason for reason in score.reasons)


def test_empty_transcript_fails_the_scenario_even_when_otherwise_a_control_pass() -> None:
    """Rule 3: an empty transcript (no provider error, but nothing was ever said) must also fail."""
    scenario = _scenario(expect_strategy_tool=False, expected_family_ids=[])
    score = EVAL.score_transcript(scenario, "", [])
    assert not score.passed
    assert any("empty transcript" in reason for reason in score.reasons)


def test_unsupported_percentage_fails_the_scenario() -> None:
    """Rule 8: a percentage the answer states must trace back to the question, prompt or a tool result."""
    scenario = _scenario()
    transcript = [_tool_message("search_environmental_strategies", _search_result(POST_FIRE_MULCHING_HIT))]
    final_text = "Research shows post-fire-straw-mulching cuts erosion by 40%, right away."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert score.unsupported_percentages == ("40%",)
    assert not score.passed


def test_a_percentage_reported_in_a_tool_result_is_not_flagged() -> None:
    scenario = _scenario()
    finding_payload = {
        "tool": "search_strategy_research_findings",
        "results": [
            {
                "finding_id": "f2",
                "claim": "Straw mulch reduced sediment yield by 40 percent in the treated plots.",
                "linked_strategy_ids": ["post-fire-straw-mulching"],
            }
        ],
    }
    transcript = [
        _tool_message("search_environmental_strategies", _search_result(POST_FIRE_MULCHING_HIT)),
        _tool_message("search_strategy_research_findings", finding_payload),
    ]
    final_text = "Research shows post-fire-straw-mulching cuts erosion by 40%, matching the reviewed finding."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert score.unsupported_percentages == ()
    assert score.passed


def test_unsupported_percentage_ignores_schema_or_prompt_boilerplate_numbers() -> None:
    """Regression: `context_text` (the built prompt plus the published tool schemas) must never
    support a percentage claim, even when it contains the exact same bare number formatted like a
    percent parameter example (e.g. a `slope_pct` schema description listing "30, 40, 70, 10, 5,
    100") -- that previously let an invented "cuts runoff 30%" pass just because 30 appears in the
    schema boilerplate every scenario receives."""
    scenario = _scenario()
    transcript = [_tool_message("search_environmental_strategies", _search_result(POST_FIRE_MULCHING_HIT))]
    final_text = "Research shows post-fire-straw-mulching cuts runoff by 30%."
    score = EVAL.score_transcript(
        scenario,
        final_text,
        transcript,
        context_text="slope_pct: the slope percentage, e.g. 30, 40, 70, 10, 5, 100",
    )
    assert score.unsupported_percentages == ("30%",)
    assert not score.passed


def test_unsupported_percentage_is_supported_by_a_bare_number_near_a_percent_marker_in_a_tool_result() -> None:
    """A tool result reporting the magnitude and its percent unit as separate tokens ("40 (percent
    basis)" rather than one contiguous "40%"/"40 percent" span) still supports the answer's "40%",
    per the ~40-character proximity rule."""
    scenario = _scenario()
    finding_payload = {
        "tool": "search_strategy_research_findings",
        "results": [
            {
                "finding_id": "f3",
                "claim": "Straw mulch reduced sediment yield 40 (percent basis) in the treated plots.",
                "linked_strategy_ids": ["post-fire-straw-mulching"],
            }
        ],
    }
    transcript = [
        _tool_message("search_environmental_strategies", _search_result(POST_FIRE_MULCHING_HIT)),
        _tool_message("search_strategy_research_findings", finding_payload),
    ]
    final_text = "Research shows post-fire-straw-mulching cuts erosion by 40%."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert score.unsupported_percentages == ()
    assert score.passed


def test_other_numeric_mentions_are_informational_only() -> None:
    """Rule 8: non-percentage numbers are reported but never affect pass/fail."""
    scenario = _scenario()
    transcript = [_tool_message("search_environmental_strategies", _search_result(POST_FIRE_MULCHING_HIT))]
    final_text = "Research shows post-fire-straw-mulching should be applied within 2 weeks of the fire."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert "2" in score.other_numeric_mentions
    assert not score.unsupported_percentages
    assert score.passed


def test_load_scenarios_filters_by_id_and_rejects_unknown_ids(tmp_path: Path) -> None:
    scenarios_path = tmp_path / "scenarios.json"
    scenarios_path.write_text(
        json.dumps(
            [
                {
                    "id": "a",
                    "longitude": 0.0,
                    "latitude": 0.0,
                    "question": "?",
                    "expect_strategy_tool": True,
                    "expected_family_ids": [],
                    "forbidden_strategy_ids": [],
                },
                {
                    "id": "b",
                    "longitude": 0.0,
                    "latitude": 0.0,
                    "question": "?",
                    "expect_strategy_tool": False,
                    "expected_family_ids": [],
                    "forbidden_strategy_ids": [],
                },
            ]
        ),
        encoding="utf-8",
    )
    scenarios = EVAL.load_scenarios(scenarios_path, only=["b"])
    assert [scenario.id for scenario in scenarios] == ["b"]
    with pytest.raises(ValueError, match="unknown scenario id"):
        EVAL.load_scenarios(scenarios_path, only=["missing"])


def test_scenario_from_json_names_the_missing_field() -> None:
    with pytest.raises(ValueError, match="question"):
        EVAL._scenario_from_json(
            {
                "id": "a",
                "longitude": 0.0,
                "latitude": 0.0,
                "expect_strategy_tool": True,
            }
        )


def test_load_registry_ids_reads_the_tiny_inline_registry(tmp_path: Path) -> None:
    family_entry = {
        "family_id": "post-fire-mulching",
        "name": "Post-fire mulching",
        "description": "x",
        "member_strategy_ids": [],
    }
    (tmp_path / "families.json").write_text(json.dumps([family_entry]), encoding="utf-8")
    (tmp_path / "strategy_registry.json").write_text(
        json.dumps({"strategies": [{"strategy_id": "post-fire-straw-mulching", "family_id": "post-fire-mulching"}]}),
        encoding="utf-8",
    )
    family_ids, strategy_ids = EVAL.load_registry_ids(tmp_path)
    assert family_ids == frozenset({"post-fire-mulching"})
    assert strategy_ids == frozenset({"post-fire-straw-mulching"})


def test_load_registry_ids_refuses_a_missing_corpus(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        EVAL.load_registry_ids(tmp_path)


def test_validate_scenarios_against_registry_flags_unknown_ids() -> None:
    scenarios = [_scenario(expected_family_ids=["not-a-real-family"], forbidden_strategy_ids=["not-a-real-strategy"])]
    problems = EVAL.validate_scenarios_against_registry(
        scenarios,
        family_ids=frozenset({"post-fire-mulching"}),
        strategy_ids=frozenset({"post-fire-straw-mulching"}),
    )
    assert any("not-a-real-family" in problem for problem in problems)
    assert any("not-a-real-strategy" in problem for problem in problems)


def test_validate_scenarios_against_registry_accepts_real_ids() -> None:
    scenarios = [
        _scenario(expected_family_ids=["post-fire-mulching"], forbidden_strategy_ids=["post-fire-straw-mulching"])
    ]
    problems = EVAL.validate_scenarios_against_registry(
        scenarios,
        family_ids=frozenset({"post-fire-mulching"}),
        strategy_ids=frozenset({"post-fire-straw-mulching"}),
    )
    assert problems == []


def test_the_shipped_scenario_file_validates_against_the_real_corpus() -> None:
    """The six scenarios this worker wrote must actually name real corpus ids, not just plausible ones."""
    registry_dir = EVAL.SERVICE_ROOT.parent / "strategy-knowledge" / ".cache" / "corpus" / "strategies"
    if not (registry_dir / "families.json").exists():
        pytest.skip(f"no strategy-knowledge corpus checked out at {registry_dir}")
    scenarios = EVAL.load_scenarios(EVAL.SCENARIOS_FILE)
    family_ids, strategy_ids = EVAL.load_registry_ids(registry_dir)
    problems = EVAL.validate_scenarios_against_registry(scenarios, family_ids=family_ids, strategy_ids=strategy_ids)
    assert problems == []
    assert {scenario.id for scenario in scenarios} == {
        "boise-foothills-post-fire",
        "western-wa-sour-pasture",
        "owyhee-cheatgrass",
        "palouse-wheat-carbon",
        "treasure-valley-water-cut",
        "control-max-temperature",
    }
