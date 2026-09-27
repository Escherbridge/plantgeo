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
    assert not score.unbound_numbers
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
    assert score.unbound_numbers == ("40%",)
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
    assert score.unbound_numbers == ()
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
    assert score.unbound_numbers == ("30%",)
    assert not score.passed


def test_percentage_binding_requires_the_records_own_text_to_state_it_as_a_percentage() -> None:
    """CORRECTED (wave-2 fix-stage review): binding used to search a record's text for the bare
    magnitude DIGITS alone ("40" inside "40 (percent basis)"), so any digit run the record happened to
    contain -- formatted as a percentage there or not -- could support an unrelated claim. Binding now
    requires the record's OWN text to state a real percentage mention with the same magnitude
    (`_record_percentage_magnitudes`); "40 (percent basis)" has no contiguous "%"/"percent" right after
    the digit, so it is not one, and no longer supports the answer's "40%" the way it used to."""
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
    assert score.unbound_numbers == ("40%",)
    assert not score.passed


def test_percentage_does_not_bind_to_a_decimal_count_or_year_span_sharing_its_digits() -> None:
    """Regression (wave-2 fix-stage review, the per-record form of the false pass E3 was built to
    remove): `_contains_token`'s alphanumeric boundary lets "." and "-" border a match, so the bare
    magnitude "65" used to bind to a record's "r = 0.65" (a correlation coefficient, not a percentage)
    purely because both contain the digits "65"; likewise "12" must not bind to "12 sites" (a count)
    or a "12-year" span. None of these states a real percentage, so none may ground the claim."""
    scenario = _scenario()
    finding_payload = {
        "tool": "search_strategy_research_findings",
        "results": [
            {
                "finding_id": "f4",
                "claim": (
                    "No-till showed r = 0.65 between residue cover and infiltration across 12 sites "
                    "over a 12-year study."
                ),
                "linked_strategy_ids": ["post-fire-straw-mulching"],
            }
        ],
    }
    transcript = [
        _tool_message("search_environmental_strategies", _search_result(POST_FIRE_MULCHING_HIT)),
        _tool_message("search_strategy_research_findings", finding_payload),
    ]
    final_text = "No-till raised yields by 65%, seen at 12% of sites."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert score.unbound_numbers == ("65%", "12%")
    assert not score.passed


def test_other_numeric_mentions_are_informational_only() -> None:
    """Rule 8: non-percentage numbers are reported but never affect pass/fail."""
    scenario = _scenario()
    transcript = [_tool_message("search_environmental_strategies", _search_result(POST_FIRE_MULCHING_HIT))]
    final_text = "Research shows post-fire-straw-mulching should be applied within 2 weeks of the fire."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert "2" in score.other_numeric_mentions
    assert not score.unbound_numbers
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
    """The eight scenarios this file carries must actually name real corpus ids, not just plausible
    ones -- and must number 8-10 with at least one multi-turn conversation (CONTRACT-WAVE2.md seam
    S5)."""
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
        "palouse-hardpan-compaction",
        "idaho-was-there-a-fire-here",
    }
    assert 8 <= len(scenarios) <= 10
    assert sum(1 for scenario in scenarios if len(scenario.turns) >= 2) >= 1


# --- Multi-turn scenarios (E4, CONTRACT-WAVE2.md seam S5) ---------------------------


def test_scenario_from_json_defaults_turns_to_the_single_question() -> None:
    scenario = _scenario(question="What now?")
    assert scenario.turns == ("What now?",)


def test_scenario_from_json_reads_an_explicit_multi_turn_list() -> None:
    scenario = _scenario(question="First ask", turns=["First ask", "Second ask"])
    assert scenario.turns == ("First ask", "Second ask")
    assert scenario.question == "First ask"  # kept for backward compatibility / a human skimming the file


# --- Scorer normalisation (E1): JSON decode, NFKC/dash folding, range parsing -------


def test_scannable_text_decodes_a_json_string_content_to_its_real_text() -> None:
    assert EVAL._scannable_text('"plain refusal text"') == "plain refusal text"


def test_scannable_text_falls_back_to_the_raw_string_when_not_json() -> None:
    assert EVAL._scannable_text("not json at all") == "not json at all"


def test_scannable_text_round_trips_a_json_escaped_minus_sign_to_the_real_character() -> None:
    """The FINDINGS.md regression: `json.dumps` escapes U+2212 to the six-character sequence
    `\\u2212`; decoding first is what makes it comparable to a plain hyphen after normalisation."""
    content = json.dumps({"magnitude": "−65%"})
    decoded = EVAL._scannable_text(content)
    assert "−" in decoded
    assert "\\u2212" not in decoded
    assert EVAL._normalise_scanned_text(decoded) == '{"magnitude": "-65%"}'


def test_percentage_mentions_parses_a_hyphenated_range() -> None:
    mentions = EVAL._percentage_mentions("losses fell 20-30% across the trial.")
    assert mentions == (("20-30%", ("20", "30")),)


def test_percentage_mentions_parses_a_worded_range() -> None:
    mentions = EVAL._percentage_mentions("losses fell 20 to 30 percent across the trial.")
    assert mentions == (("20 to 30 percent", ("20", "30")),)


def test_percentage_mentions_does_not_double_count_a_ranges_own_trailing_number() -> None:
    """A range like '20-30%' must produce ONE two-magnitude mention, never also a spurious
    single-magnitude '30%' mention from the same span."""
    mentions = EVAL._percentage_mentions("losses fell 20-30% in the trial.")
    assert len(mentions) == 1


# --- Negation: OPEN DEFECT fixed (FINDINGS.md "Scorer caveats", 2026-09-27) ---------


def test_negation_word_boundary_does_not_match_inside_another_or_notably() -> None:
    """The old substring check read 'not' inside 'another'/'notably' as a negation marker."""
    scenario = _scenario(forbidden_strategy_ids=[SULFUR_HIT["strategy_id"]])
    transcript = [_tool_message("search_environmental_strategies", _search_result(SULFUR_HIT))]

    another = "Another option is elemental-sulfur-soil-acidification for lowering pH quickly."
    assert EVAL.score_transcript(scenario, another, transcript).forbidden_hit

    notably = "Notably, elemental-sulfur-soil-acidification can lower pH within one season."
    assert EVAL.score_transcript(scenario, notably, transcript).forbidden_hit


def test_negation_word_boundary_still_catches_a_real_do_not_negation() -> None:
    scenario = _scenario(forbidden_strategy_ids=[SULFUR_HIT["strategy_id"]])
    transcript = [_tool_message("search_environmental_strategies", _search_result(SULFUR_HIT))]
    final_text = "Do not till the soil; apply elemental-sulfur-soil-acidification instead."
    # The negation ("Do not") sits immediately before "till", not before the sulfur mention later in
    # the same sentence -- the sulfur recommendation itself is NOT negated by it.
    assert EVAL.score_transcript(scenario, final_text, transcript).forbidden_hit


def test_negation_matches_avoid_inflections_and_a_typographic_apostrophe() -> None:
    """Minor finding (wave-2 fix-stage review): the word-bounded pattern must still catch 'avoiding'/
    'avoids'/'avoided' (a plain 'avoid' substring caught these for free before the `\\b` fix) and a
    model's "don’t" written with the typographic right single quote NFKC never folds on its own."""
    scenario = _scenario(forbidden_strategy_ids=[SULFUR_HIT["strategy_id"]])
    transcript = [_tool_message("search_environmental_strategies", _search_result(SULFUR_HIT))]

    avoiding = "We recommend avoiding elemental-sulfur-soil-acidification on this soil."
    assert not EVAL.score_transcript(scenario, avoiding, transcript).forbidden_hit

    curly_dont = "Don’t use elemental-sulfur-soil-acidification here; it will worsen drainage."
    assert not EVAL.score_transcript(scenario, curly_dont, transcript).forbidden_hit


def test_negation_anchors_at_the_real_mention_not_an_earlier_shared_word() -> None:
    """OPEN DEFECT regression: the old anchor picked the EARLIEST occurrence of any hyphen-split word
    of the id anywhere in the sentence, so an unrelated earlier "till" mention describing a DIFFERENT
    forbidden strategy could point the negation window at the wrong place. Two distinct forbidden
    strategies, only the first ("till") governed by "Do not" -- the second (sulfur) must still hit."""
    till_hit = {"strategy_id": "conventional-tillage-pass", "family_id": "tillage-x", "name": "Till the soil deeply"}
    scenario = _scenario(forbidden_strategy_ids=[till_hit["strategy_id"], SULFUR_HIT["strategy_id"]])
    transcript = [_tool_message("search_environmental_strategies", _search_result(till_hit, SULFUR_HIT))]
    final_text = "Do not till the soil; apply elemental-sulfur-soil-acidification instead."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert score.forbidden_hit


# --- Clause-headed markers: "instead of"/"rather than"/"without" (major finding, wave-2 fix-stage
# review) --------------------------------------------------------------------------------------


def test_forbidden_hit_fires_when_instead_of_precedes_a_different_recommendation_before_the_mention() -> None:
    """FIXED (wave-2 fix-stage review): "instead of"/"rather than" negate the noun phrase they
    directly head, not everything within a fixed word window. "Instead of lime, use <forbidden>"
    recommends the forbidden strategy -- the marker's own phrase ("lime") ends at the comma, well
    before the mention -- so this must be a hit. The prior window-scan treated "instead of" as
    negating anything within reach, including a DIFFERENT recommendation past a comma."""
    scenario = _scenario(
        expected_family_ids=["soil-chemistry-correction"],
        forbidden_strategy_ids=[SULFUR_HIT["strategy_id"]],
    )
    transcript = [_tool_message("search_environmental_strategies", _search_result(LIME_HIT, SULFUR_HIT))]
    final_text = f'Instead of lime, use "{SULFUR_HIT["name"]}".'
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert score.forbidden_hit
    assert not score.passed


def test_forbidden_hit_fires_when_rather_than_precedes_a_different_recommendation_before_the_mention() -> None:
    scenario = _scenario(
        expected_family_ids=["soil-chemistry-correction"],
        forbidden_strategy_ids=[SULFUR_HIT["strategy_id"]],
    )
    transcript = [_tool_message("search_environmental_strategies", _search_result(LIME_HIT, SULFUR_HIT))]
    final_text = f'Rather than lime, apply "{SULFUR_HIT["name"]}".'
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert score.forbidden_hit
    assert not score.passed


def test_forbidden_hit_fires_when_without_precedes_a_different_clause_before_the_mention() -> None:
    """ "Without X first, apply <forbidden>" behaves the same way: the marker's own clause ends at the
    comma, so it does not reach into the NEXT clause's recommendation."""
    scenario = _scenario(
        expected_family_ids=["soil-chemistry-correction"],
        forbidden_strategy_ids=[SULFUR_HIT["strategy_id"]],
    )
    transcript = [_tool_message("search_environmental_strategies", _search_result(LIME_HIT, SULFUR_HIT))]
    final_text = f'Without liming first, apply "{SULFUR_HIT["name"]}".'
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert score.forbidden_hit
    assert not score.passed


def test_forbidden_hit_still_excludes_a_genuine_instead_of_negation_directly_before_the_mention() -> None:
    """The marker still negates when nothing (no comma, no clause verb) sits between it and the
    mention -- "instead of <forbidden>, use X" is a real negation, the mirror image of the three
    "Instead of X, use <forbidden>" shapes above."""
    scenario = _scenario(
        expected_family_ids=["soil-chemistry-correction"],
        forbidden_strategy_ids=[SULFUR_HIT["strategy_id"]],
    )
    transcript = [_tool_message("search_environmental_strategies", _search_result(LIME_HIT, SULFUR_HIT))]
    final_text = f'Instead of "{SULFUR_HIT["name"]}", use lime.'
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert not score.forbidden_hit


# --- Record-bound grounding (E3, CONTRACT-WAVE2.md seam S5) -------------------------


def test_grounding_binds_a_json_escaped_minus_sign_percentage_to_its_finding_record() -> None:
    """The corrected FINDINGS.md regression: Haiku's '~65%' IS in finding F8101's magnitude, but the
    real defect was dropping its 'mixed' direction, not fabricating the number."""
    finding_payload = {
        "tool": "search_strategy_research_findings",
        "results": [
            {
                "finding_id": "F8101",
                "claim": "reduced total N losses (relative median change = −65%)",
                "direction": "mixed",
                "magnitude": "−65%",
                "linked_strategy_ids": ["reduced-tillage-no-till"],
            }
        ],
    }
    transcript = [_tool_message("search_strategy_research_findings", finding_payload)]
    scenario = _scenario(expected_family_ids=["tillage-reduction"])

    with_qualifier = (
        "Research found reduced tillage cut nitrogen losses by about ~65%, but the same review found "
        "a mixed effect: soluble phosphorus losses rose."
    )
    grounded_score = EVAL.score_transcript(scenario, with_qualifier, transcript)
    assert "65%" in grounded_score.grounded_numbers
    assert not grounded_score.unbound_numbers
    assert not grounded_score.direction_dropped

    dropped_text = "Research found reduced tillage cut nitrogen losses by about ~65%."
    dropped_score = EVAL.score_transcript(scenario, dropped_text, transcript)
    assert "65%" in dropped_score.grounded_numbers
    assert dropped_score.direction_dropped
    assert not dropped_score.passed
    assert any("direction" in reason for reason in dropped_score.reasons)


def test_grounding_never_binds_against_anywhere_in_tool_text_only_a_records_own_text() -> None:
    """A number sitting in one record's text must not ground a DIFFERENT, unrelated number the answer
    states about a strategy that carries no such figure itself -- the exact "anywhere in this turn's
    tool text" false-pass FINDINGS.md's 'Scorer caveats' warns against."""
    finding_payload = {
        "tool": "search_strategy_research_findings",
        "results": [
            {
                "finding_id": "f-unrelated",
                "claim": "A separate study found a 12% change in an unrelated crop.",
                "linked_strategy_ids": ["post-fire-straw-mulching"],
            }
        ],
    }
    transcript = [
        _tool_message("search_environmental_strategies", _search_result(POST_FIRE_MULCHING_HIT)),
        _tool_message("search_strategy_research_findings", finding_payload),
    ]
    scenario = _scenario()
    final_text = "Research shows post-fire-straw-mulching cuts erosion by 40%, right away."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert score.unbound_numbers == ("40%",)
    assert "40%" not in score.grounded_numbers


def test_grounding_flags_a_literature_number_restated_as_a_site_specific_promise() -> None:
    finding_payload = {
        "tool": "search_strategy_research_findings",
        "results": [
            {
                "finding_id": "f-site-promise",
                "claim": "Straw mulch reduced sediment yield by 40 percent in the treated plots.",
                "linked_strategy_ids": ["post-fire-straw-mulching"],
            }
        ],
    }
    transcript = [_tool_message("search_strategy_research_findings", finding_payload)]
    scenario = _scenario()
    final_text = "Apply straw mulch -- your field will see a 40% drop in erosion this winter."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert score.site_promise
    assert not score.passed
    assert any("site" in reason for reason in score.reasons)


def test_grounding_binds_a_strategy_records_own_summary_text_not_only_findings() -> None:
    strategy_record = {
        "strategy_id": "post-fire-straw-mulching",
        "family_id": "post-fire-mulching",
        "name": "Post-fire straw mulching",
        "summary": "Apply straw mulch at 2 tons per acre to cut hillslope erosion by roughly 55 percent.",
    }
    transcript = [
        _tool_message(
            "get_environmental_strategies", {"tool": "get_environmental_strategies", "strategies": [strategy_record]}
        )
    ]
    scenario = _scenario()
    final_text = "Research shows post-fire-straw-mulching cuts erosion by 55%, applied right after the fire."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert "55%" in score.grounded_numbers
    assert not score.unbound_numbers


# --- Budget guard (E4/E5, CONTRACT-WAVE2.md seam S5) --------------------------------


def test_budget_error_is_none_within_the_default_budget() -> None:
    assert EVAL._budget_error(scenario_count=8, model_count=2, samples=1, allow_over_budget=False) is None


def test_budget_error_refuses_over_twenty_runs_without_the_override() -> None:
    error = EVAL._budget_error(scenario_count=8, model_count=2, samples=2, allow_over_budget=False)
    assert error is not None
    assert "32 run(s)" in error
    assert "--allow-over-budget" in error


def test_budget_error_is_none_when_the_override_is_passed() -> None:
    assert EVAL._budget_error(scenario_count=8, model_count=2, samples=2, allow_over_budget=True) is None


def test_budget_error_is_none_exactly_at_the_boundary() -> None:
    assert EVAL._budget_error(scenario_count=10, model_count=2, samples=1, allow_over_budget=False) is None


# --- Offline rescore mode (E5) -------------------------------------------------------


def test_rescore_flips_a_verdict_that_the_old_scorer_got_wrong(tmp_path: Path) -> None:
    """A stored transcript scored PASS by an old, more lenient scorer (no grounding at all) must come
    back FAIL under the current one, with the flip recorded."""
    stored = {
        "scenario_id": "boise-foothills-post-fire",
        "longitude": -116.13,
        "latitude": 43.66,
        "question": "How do I stop erosion?",
        "expect_strategy_tool": True,
        "expected_family_ids": ["post-fire-mulching"],
        "forbidden_strategy_ids": [],
        "final_text": "Research shows post-fire-straw-mulching cuts erosion by 40%, right away.",
        "transcript": [
            _tool_message("search_environmental_strategies", _search_result(POST_FIRE_MULCHING_HIT)),
        ],
        "provider_error": None,
        "score": {"passed": True, "reasons": []},  # the OLD (wrong) verdict this round corrects
    }
    result_dir = tmp_path / "haiku-4.5"
    result_dir.mkdir()
    (result_dir / "boise-foothills-post-fire.json").write_text(json.dumps(stored), encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    exit_code = EVAL._run_rescore(result_dir, out_dir, tmp_path / "no-such-registry")
    assert exit_code == 0
    report = json.loads((out_dir / EVAL.RESCORE_REPORT_NAME).read_text(encoding="utf-8"))
    assert report["rescored_turns"] == 1
    assert report["verdicts_changed"] == 1
    row = report["rows"][0]
    assert row["old_passed"] is True
    assert row["new_passed"] is False
    assert row["verdict_changed"] is True
    assert (out_dir / EVAL.RESCORE_MARKDOWN_NAME).exists()


def test_rescore_ignores_summary_and_non_result_json_files(tmp_path: Path) -> None:
    input_dir = tmp_path / "results"
    input_dir.mkdir()
    (input_dir / EVAL.SUMMARY_JSON_NAME).write_text(json.dumps({"generated_at": "x"}), encoding="utf-8")
    (input_dir / "not-a-result.json").write_text(json.dumps({"unrelated": True}), encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    exit_code = EVAL._run_rescore(input_dir, out_dir, tmp_path / "no-such-registry")
    assert exit_code == 0
    report = json.loads((out_dir / EVAL.RESCORE_REPORT_NAME).read_text(encoding="utf-8"))
    assert report["rescored_turns"] == 0


def test_rescore_handles_the_new_multi_turn_stored_shape(tmp_path: Path) -> None:
    stored = {
        "scenario_id": "western-wa-sour-pasture",
        "longitude": -122.95,
        "latitude": 46.66,
        "question": "sour pasture?",
        "expect_strategy_tool": True,
        "expected_family_ids": ["soil-chemistry-correction"],
        "forbidden_strategy_ids": ["elemental-sulfur-soil-acidification"],
        "passed": False,
        "turns": [
            {
                "final_text": "Apply lime-application-acidic-soils to raise the pH.",
                "transcript": [_tool_message("search_environmental_strategies", _search_result(LIME_HIT))],
                "provider_error": None,
                "score": {"passed": True, "reasons": []},
            },
            {
                "final_text": "A lime rate of 2 tons per acre should work within a season.",
                "transcript": [_tool_message("search_environmental_strategies", _search_result(LIME_HIT))],
                "provider_error": None,
                "score": {"passed": False, "reasons": ["something"]},
            },
        ],
    }
    input_dir = tmp_path / "results"
    input_dir.mkdir()
    (input_dir / "western-wa-sour-pasture.json").write_text(json.dumps(stored), encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    EVAL._run_rescore(input_dir, out_dir, tmp_path / "no-such-registry")
    report = json.loads((out_dir / EVAL.RESCORE_REPORT_NAME).read_text(encoding="utf-8"))
    assert report["rescored_turns"] == 2
    assert [row["turn_index"] for row in report["rows"]] == [0, 1]


def test_rescore_rebuilds_turns_from_each_stored_turns_own_user_message(tmp_path: Path) -> None:
    """Major finding (wave-2 fix-stage review): `_write_scenario_result` never writes a top-level
    `question` field for a new-shape multi-turn result, so reading `payload["question"]` for `turns`
    always answered "" and silently dropped every real user turn from `known_text` -- manufacturing a
    hallucination flag out of ordinary tool-schema boilerplate the model actually saw (e.g. the stored
    `context_text` this fix now persists per turn, containing "(YYYY-MM-DD)")."""
    stored = {
        "scenario_id": "control-max-temperature",
        "longitude": -116.2,
        "latitude": 43.6,
        # No top-level "question" -- exactly what `_write_scenario_result` actually writes.
        "expect_strategy_tool": False,
        "expected_family_ids": [],
        "forbidden_strategy_ids": [],
        "passed": True,
        "turns": [
            {
                "user_message": "What was the maximum air temperature here on the selected day?",
                "final_text": "The high was 24C, reported (in YYYY-MM-DD format) as 2026-03-14.",
                # A non-empty transcript: score_transcript treats an empty one as a provider that never
                # produced a message, which is not what this fixture is testing.
                "transcript": [
                    {"role": "assistant", "content": "The high was 24C, reported (in YYYY-MM-DD format) as 2026-03-14."}
                ],
                "provider_error": None,
                "context_text": (
                    "What was the maximum air temperature here on the selected day?\n"
                    "as ISO YYYY-MM-DD such as 2026-03-14 (YYYY-MM-DD format)"
                ),
                "score": {"passed": True, "reasons": []},
            }
        ],
    }
    input_dir = tmp_path / "results"
    input_dir.mkdir()
    (input_dir / "control-max-temperature.json").write_text(json.dumps(stored), encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    EVAL._run_rescore(input_dir, out_dir, tmp_path / "no-such-registry")

    report = json.loads((out_dir / EVAL.RESCORE_REPORT_NAME).read_text(encoding="utf-8"))
    row = report["rows"][0]
    # "yyyy-mm-dd" is a real kebab-id-shaped token in the answer; it must be recognised as known
    # tool-schema boilerplate (via the turn's own persisted context_text) rather than flagged as a
    # fabricated strategy id.
    assert row["new_passed"] is True
    assert not row["new_reasons"]


def test_rescore_falls_back_to_a_legacy_schema_snapshot_when_context_text_is_missing(tmp_path: Path) -> None:
    """A result stored BEFORE this fix persisted `context_text` has none on disk; the fallback
    snapshot (`_LEGACY_TOOL_SCHEMA_KNOWN_TEXT`) must still cover the one documented false-positive
    (`(YYYY-MM-DD)`) so re-scoring an old file does not manufacture a new hallucination flag."""
    stored = {
        "scenario_id": "control-max-temperature",
        "longitude": -116.2,
        "latitude": 43.6,
        "expect_strategy_tool": False,
        "expected_family_ids": [],
        "forbidden_strategy_ids": [],
        "passed": True,
        "turns": [
            {
                "user_message": "What was the maximum air temperature here on the selected day?",
                "final_text": "The high was 24C, reported (in YYYY-MM-DD format) as 2026-03-14.",
                # A non-empty transcript: score_transcript treats an empty one as a provider that never
                # produced a message, which is not what this fixture is testing.
                "transcript": [
                    {"role": "assistant", "content": "The high was 24C, reported (in YYYY-MM-DD format) as 2026-03-14."}
                ],
                "provider_error": None,
                # No "context_text" -- the pre-fix stored shape.
                "score": {"passed": True, "reasons": []},
            }
        ],
    }
    input_dir = tmp_path / "results"
    input_dir.mkdir()
    (input_dir / "control-max-temperature.json").write_text(json.dumps(stored), encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    EVAL._run_rescore(input_dir, out_dir, tmp_path / "no-such-registry")

    report = json.loads((out_dir / EVAL.RESCORE_REPORT_NAME).read_text(encoding="utf-8"))
    row = report["rows"][0]
    assert row["new_passed"] is True
    assert not row["new_reasons"]


# --- Budget guard: zero/negative samples (minor finding, wave-2 fix-stage review) ----


def test_budget_error_refuses_zero_or_negative_samples_even_with_the_override() -> None:
    """`--samples 0` (or a negative value) makes `total_runs <= 0`, which would otherwise slip under
    the budget ceiling, run nothing, and let `all(())` report a vacuous exit code 0 that reads as a
    passing round."""
    assert EVAL._budget_error(scenario_count=1, model_count=1, samples=0, allow_over_budget=True) is not None
    assert EVAL._budget_error(scenario_count=1, model_count=1, samples=-3, allow_over_budget=False) is not None
    assert "--samples" in EVAL._budget_error(scenario_count=1, model_count=1, samples=0, allow_over_budget=True)


# --- Record-bound grounding: core-text priority and sentence-scoped site_promise ----
# (wave-2 fix-stage review, minor findings)


def test_grounding_ignores_a_records_conditions_only_match_when_another_record_has_a_core_match() -> None:
    """A record's free-form `conditions` text can coincidentally contain an unrelated digit run (a
    trial length, a year count). Binding tries `claim`/`magnitude`/`excerpt`/`summary` (core text)
    FIRST across every record; once ANY record binds there, a different record whose only match is a
    coincidence in `conditions` never joins `bound` at all -- this is the exact double-binding
    FINDINGS-adjacent wave-2 review flags: '10%' bound to a positive record AND to a mixed record
    whose conditions coincidentally say '10 year', reporting a false `direction_dropped`."""
    positive_strategy = {
        "strategy_id": "cover-cropping-organic-matter-maintenance",
        "family_id": "cover-cropping",
        "name": "Cover cropping for organic matter",
        "summary": "Cover cropping increased infiltration by 10 percent in field trials.",
    }
    decoy_finding = {
        "tool": "search_strategy_research_findings",
        "results": [
            {
                "finding_id": "f-decoy-conditions",
                "claim": "A separate practice showed no measurable change.",
                "conditions": "Northern Great Plains, measured over a 10 year rotation study.",
                "direction": "mixed",
                "linked_strategy_ids": ["cover-cropping-organic-matter-maintenance"],
            }
        ],
    }
    transcript = [
        _tool_message(
            "get_environmental_strategies", {"tool": "get_environmental_strategies", "strategies": [positive_strategy]}
        ),
        _tool_message("search_strategy_research_findings", decoy_finding),
    ]
    scenario = _scenario(expected_family_ids=["cover-cropping"])
    final_text = "Research shows cover cropping increased infiltration by 10 percent."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert "10%" in score.grounded_numbers
    assert not score.direction_dropped


def test_grounding_direction_dropped_requires_every_bound_record_to_need_a_qualifier() -> None:
    """A number bound to BOTH an unqualified positive record and a mixed one has a legitimate,
    unqualified source for stating it plainly -- only flag `direction_dropped` when EVERY record the
    number bound to needs the qualifier."""
    positive_strategy = {
        "strategy_id": "cover-cropping-organic-matter-maintenance",
        "family_id": "cover-cropping",
        "name": "Cover cropping for organic matter",
        "summary": "Cover cropping increased infiltration by 20 percent with no downside reported.",
    }
    mixed_finding = {
        "tool": "search_strategy_research_findings",
        "results": [
            {
                "finding_id": "f-mixed-20",
                "claim": "A 20 percent gain in one trial came with a mixed effect elsewhere.",
                "direction": "mixed",
                "linked_strategy_ids": ["cover-cropping-organic-matter-maintenance"],
            }
        ],
    }
    transcript = [
        _tool_message(
            "get_environmental_strategies", {"tool": "get_environmental_strategies", "strategies": [positive_strategy]}
        ),
        _tool_message("search_strategy_research_findings", mixed_finding),
    ]
    scenario = _scenario(expected_family_ids=["cover-cropping"])
    final_text = "Research shows cover cropping increased infiltration by 20 percent."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert "20%" in score.grounded_numbers
    assert not score.direction_dropped


def test_grounding_site_promise_requires_a_bound_number_in_the_same_sentence() -> None:
    """The site-promise phrasing alone, with no literature number in the SAME sentence, is not a
    grounding defect -- only a bound number restated as a promise at the caller's own site is."""
    finding_payload = {
        "tool": "search_strategy_research_findings",
        "results": [
            {
                "finding_id": "f-no-number",
                "claim": "Straw mulch reduced erosion in the treated plots.",
                "linked_strategy_ids": ["post-fire-straw-mulching"],
            }
        ],
    }
    transcript = [_tool_message("search_strategy_research_findings", finding_payload)]
    scenario = _scenario()
    final_text = "Apply straw mulch now. Your field will thank you for it."
    score = EVAL.score_transcript(scenario, final_text, transcript)
    assert not score.site_promise


# --- Per-turn scoping of tool-activity counts (minor finding, wave-2 fix-stage review) ----


def test_new_messages_scopes_tool_activity_counts_to_this_turns_own_messages() -> None:
    """Tool-activity COUNTS (`strategy_tool_attempted_count`/`succeeded_count`/
    `warehouse_tool_call_count`) stay scoped to `new_messages`, never the whole cumulative transcript,
    so a later turn's own numbers in a failure message are never inflated by an earlier turn's calls.

    CORRECTED (wave-2 fix-stage review): this test used to also assert that `expect_strategy_tool`
    itself fails outright whenever a turn makes no NEW call, even when an earlier turn's retrieval is
    reused. That overshot: it failed a legitimate reused-evidence follow-up turn (see the reuse test
    below) with a misleading "none was made" reason. This turn still fails here, but for the RIGHT
    reason -- the answer never actually NAMES the reused lime strategy, so `family_hit` is the one
    that misses."""
    turn_1_call = _tool_message("search_environmental_strategies", _search_result(LIME_HIT))
    cumulative_transcript = [
        {"role": "user", "content": "sour pasture?"},
        turn_1_call,
        {"role": "assistant", "content": "Apply lime-application-acidic-soils."},
        {"role": "user", "content": "how much lime?"},
        {"role": "assistant", "content": "A rate of 2 tons per acre should work."},
    ]
    scenario = _scenario(expected_family_ids=["soil-chemistry-correction"])
    new_messages = cumulative_transcript[3:]  # only this turn's user question and answer -- no tool call

    score = EVAL.score_transcript(
        scenario,
        "A rate of 2 tons per acre should work.",
        cumulative_transcript,
        new_messages=new_messages,
    )
    assert score.strategy_tool_attempted_count == 0
    assert score.warehouse_tool_call_count == 0
    assert not score.strategy_tool_called
    assert not score.passed
    assert "family_id is both expected and named in the answer" in score.reasons[0]
    # Retrieved records stay CUMULATIVE: the earlier turn's lime strategy is still real evidence this
    # conversation has in hand, so naming it here is not a hallucination.
    assert "lime-application-acidic-soils" in score.retrieved_strategy_ids


def test_a_followup_turn_reusing_an_earlier_successful_retrieval_satisfies_expect_strategy_tool() -> None:
    """FIXED (wave-2 fix-stage review): a follow-up turn that answers from an EARLIER turn's
    successful retrieval, actually NAMING that strategy, must not fail with "expected a strategy tool
    call; none was made" merely because it made no NEW call of its own -- reusing already-retrieved
    evidence for a natural follow-up ("how much lime?") is legitimate. `family_hit` (also cumulative)
    still gates that the reused evidence is genuinely what the answer relies on; only the "no call"
    failure reasons are waived, and only when a call actually succeeded somewhere in the conversation."""
    turn_1_call = _tool_message("search_environmental_strategies", _search_result(LIME_HIT))
    cumulative_transcript = [
        {"role": "user", "content": "sour pasture?"},
        turn_1_call,
        {"role": "assistant", "content": "Apply lime-application-acidic-soils, per the literature."},
        {"role": "user", "content": "how much lime?"},
        {
            "role": "assistant",
            "content": "Apply lime-application-acidic-soils at 2 tons per acre, per the literature.",
        },
    ]
    scenario = _scenario(expected_family_ids=["soil-chemistry-correction"])
    new_messages = cumulative_transcript[3:]  # only this turn's question and answer -- no NEW tool call

    score = EVAL.score_transcript(
        scenario,
        "Apply lime-application-acidic-soils at 2 tons per acre, per the literature.",
        cumulative_transcript,
        new_messages=new_messages,
    )
    assert score.strategy_tool_attempted_count == 0
    assert not score.strategy_tool_called
    assert score.family_hit
    assert score.passed, score.reasons


# --- Live multi-turn orchestration (major finding, wave-2 fix-stage review) ----------
#
# `_run_live`'s `run_turn`/`run_conversation` closures had NO test at all before this fix: a fresh
# `StrategyContext` per turn, `user_question` accumulating over turns, the transcript threading
# forward, and `ScenarioRunResult.passed` requiring EVERY turn to pass. `OpenAiCompletionsClient.
# converse` is monkeypatched with a recorder so this runs with no network and no real provider.


def _patch_agent_llm_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr  # noqa: PLC0415

    from agri_data_service.config import AgentLlmCredentials, settings  # noqa: PLC0415

    # `require_agent_llm` is a METHOD, not a pydantic field, so it is patched on the CLASS (as
    # `test_job_run_supersession.py`'s `require_local_source_loader_database_url` patch does) --
    # `settings` is a frozen-field pydantic model whose `__setattr__` rejects a non-field instance
    # attribute outright.
    monkeypatch.setattr(
        type(settings),
        "require_agent_llm",
        lambda _self: AgentLlmCredentials(
            base_url="https://provider.invalid/api/v1",
            model="test/model-1",
            api_key=SecretStr("sk-test-not-a-real-credential"),
            auth_header="bearer",
            timeout_seconds=5.0,
        ),
    )


async def test_run_live_opens_a_fresh_context_per_turn_and_threads_the_transcript(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A fresh `StrategyContext` per user turn, seeded with every turn asked SO FAR; the transcript
    threaded forward from one turn into the next; and the stored result's own `final_text` per turn."""
    import argparse  # noqa: PLC0415

    from agri_data_service.agent import strategy_knowledge  # noqa: PLC0415
    from agri_data_service.agent.llm import OpenAiCompletionsClient  # noqa: PLC0415

    _patch_agent_llm_credentials(monkeypatch)
    calls: list[dict[str, Any]] = []

    async def fake_converse(
        _self: Any,
        messages: list[dict[str, Any]],
        *,
        max_iterations: int = 6,  # noqa: ARG001 - matches the real converse() signature
        max_tokens: int = 16_000,  # noqa: ARG001 - matches the real converse() signature
    ) -> dict[str, Any]:
        calls.append({"messages": list(messages), "context": strategy_knowledge.current_strategy_context()})
        answer = f"answer {len(calls)}"
        return {
            "final_text": answer,
            "iterations": 1,
            "tool_calls": [],
            "transcript": [*messages, {"role": "assistant", "content": answer}],
            "stopped_because": "model_answered",
        }

    monkeypatch.setattr(OpenAiCompletionsClient, "converse", fake_converse)

    scenario = _scenario(
        id="two-turn",
        turns=["first question", "second question"],
        expect_strategy_tool=False,
        expected_family_ids=[],
    )
    args = argparse.Namespace(
        out=tmp_path,
        models=["test/model-1"],
        samples=1,
        allow_over_budget=False,
        max_tokens=None,
        registry_dir=tmp_path / "no-such-registry",
        strategy_knowledge_url="http://127.0.0.1:8765",
    )

    exit_code = await EVAL._run_live([scenario], args)

    assert exit_code == 0
    assert len(calls) == 2
    assert calls[0]["context"].user_question == "first question"
    assert calls[1]["context"].user_question == "first question\nsecond question"
    # Turn 1's user message carries the coordinate preamble; turn 2's does not (only turn 1 opens it).
    assert calls[0]["messages"][-1]["content"].endswith(scenario.turns[0])
    assert calls[1]["messages"][-1] == {"role": "user", "content": scenario.turns[1]}
    # Turn 2 opens with turn 1's own (threaded) transcript, so it always carries strictly more.
    assert len(calls[1]["messages"]) > len(calls[0]["messages"])

    stored = json.loads((tmp_path / "test__model-1" / "sample-0" / "two-turn.json").read_text(encoding="utf-8"))
    assert stored["passed"] is True
    assert [turn["final_text"] for turn in stored["turns"]] == ["answer 1", "answer 2"]


async def test_run_live_fails_the_conversation_when_any_one_turn_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CONTRACT-WAVE2.md seam S5: 'a conversation passes only if every turn passes' -- a scenario that
    expects a strategy tool call must fail when NO turn ever makes one, even though every turn
    otherwise answers cleanly."""
    import argparse  # noqa: PLC0415

    from agri_data_service.agent.llm import OpenAiCompletionsClient  # noqa: PLC0415

    _patch_agent_llm_credentials(monkeypatch)

    async def fake_converse(
        _self: Any,
        messages: list[dict[str, Any]],
        *,
        max_iterations: int = 6,  # noqa: ARG001 - matches the real converse() signature
        max_tokens: int = 16_000,  # noqa: ARG001 - matches the real converse() signature
    ) -> dict[str, Any]:
        answer = "a plain answer with no tool call"
        return {
            "final_text": answer,
            "iterations": 1,
            "tool_calls": [],
            "transcript": [*messages, {"role": "assistant", "content": answer}],
            "stopped_because": "model_answered",
        }

    monkeypatch.setattr(OpenAiCompletionsClient, "converse", fake_converse)

    scenario = _scenario(id="never-calls-the-tool", turns=["what helps here?"], expect_strategy_tool=True)
    args = argparse.Namespace(
        out=tmp_path,
        models=["test/model-1"],
        samples=1,
        allow_over_budget=False,
        max_tokens=None,
        registry_dir=tmp_path / "no-such-registry",
        strategy_knowledge_url="http://127.0.0.1:8765",
    )

    exit_code = await EVAL._run_live([scenario], args)

    assert exit_code == 1
    stored = json.loads(
        (tmp_path / "test__model-1" / "sample-0" / "never-calls-the-tool.json").read_text(encoding="utf-8")
    )
    assert stored["passed"] is False
