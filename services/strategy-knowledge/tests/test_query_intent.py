"""Query intent: lay vocabulary, pH direction, capped soil-condition boosts and BM25-only expansion tokens."""

import pytest

from strategy_knowledge.query_intent import (
    LAY_VOCABULARY,
    MAXIMUM_EXPANSION_TOKENS,
    MAXIMUM_INTENT_BOOSTS,
    PH_DIRECTION_CONDITION,
    QueryIntent,
    normalise_text,
    parse_intent,
)
from strategy_knowledge.vocabulary import SOIL_CONDITIONS


def test_the_table_is_keyed_by_the_soil_condition_vocabulary() -> None:
    assert set(LAY_VOCABULARY) <= set(SOIL_CONDITIONS)
    assert set(PH_DIRECTION_CONDITION.values()) <= set(SOIL_CONDITIONS)
    for vocabulary in LAY_VOCABULARY.values():
        assert vocabulary.phrases
        assert vocabulary.expansion_tokens
        assert all(phrase == normalise_text(phrase) for phrase in vocabulary.phrases)


@pytest.mark.parametrize(
    ("query", "direction"),
    [
        ("how do I raise the pH of my acidic garden soil", "raise"),
        ("soil is too acidic, need to sweeten it", "raise"),
        ("sour ground, nothing will grow", "raise"),
        ("pH is way down in the hayfield", "raise"),
        ("need to lower soil pH for my blueberries, it's too alkaline", "lower"),
        ("want to acidify the soil for acid-loving plants like blueberries", "lower"),
        ("high pH calcareous soil", "lower"),
        ("fix soil chemistry", None),
        ("improve the soil", None),
    ],
)
def test_ph_direction(query: str, direction: str | None) -> None:
    assert parse_intent(query).ph_direction == direction


def test_a_ph_direction_boosts_its_condition_and_expands_toward_its_amendment() -> None:
    raising = parse_intent("sour pasture soil")
    assert raising.lay_terms == ("sour",)
    assert raising.soil_condition_boosts[0] == "acidic"
    assert "lime" in raising.expansion_tokens
    assert "sulfur" not in raising.expansion_tokens
    lowering = parse_intent("lower the pH for blueberries")
    assert lowering.soil_condition_boosts[0] == "alkaline"
    assert "sulfur" in lowering.expansion_tokens
    assert "lime" not in lowering.expansion_tokens


def test_the_demotion_is_the_opposite_ph_condition() -> None:
    assert parse_intent("raise soil pH").soil_condition_demotions == ("alkaline",)
    assert parse_intent("lower soil pH").soil_condition_demotions == ("acidic",)
    assert parse_intent("hardpan").soil_condition_demotions == ()


@pytest.mark.parametrize(
    ("query", "direction"),
    [
        # A crop mention alone is not a pH cue: irrigation for blueberries says nothing about soil pH.
        ("deficit irrigation for blueberries to save water", None),
        # "free lime"/"lime-induced" are a symptom of EXCESS lime (an alkaline problem), not a request to add
        # lime; only the bare "calcareous" condition should register, giving lower.
        ("lime-induced iron chlorosis on calcareous soil with free lime", "lower"),
        # The soil IS becoming acidic (ammonium fertilizer's side effect); that is a case FOR liming, not
        # a request to acidify it further.
        ("soil getting more acidic from years of ammonium fertilizer", "raise"),
        # "sulfur deficiency" is a nutrient problem, not an amendment request; "acid soil" alone gives raise.
        ("sulfur deficiency in canola on acid soil", "raise"),
        # "sour cherry" is a fruit cultivar, not a soil-taste description.
        ("sour cherry orchard cover crop", None),
    ],
)
def test_ph_direction_does_not_false_positive_on_crop_names_or_symptom_words(
    query: str,
    direction: str | None,
) -> None:
    # A 2026-09-27 review found the previous vocabulary inverted or false-positived on all five of these
    # (AGENTS.md "Query intent").
    assert parse_intent(query).ph_direction == direction


def test_contradicting_ph_cues_give_no_direction_and_no_ph_boost() -> None:
    intent = parse_intent("sour soil for acid-loving plants")
    assert intent.ph_direction is None
    assert not {"acidic", "alkaline"} & set(intent.soil_condition_boosts)
    assert set(intent.lay_terms) == {"sour", "acid-loving"}


@pytest.mark.parametrize(
    ("query", "condition", "token"),
    [
        ("hardpan under the garden, water just sits on top", "compacted", "compaction"),
        ("salty field, salt crust on top", "saline_sodic", "salinity"),
        ("field got burnt and now it's all washed out", "burned_high_severity", "burned"),
        ("sandy ground, water runs right through it", "sandy_coarse", "sandy"),
    ],
)
def test_lay_terms_boost_their_soil_condition_and_expand_bm25(query: str, condition: str, token: str) -> None:
    intent = parse_intent(query)
    assert condition in intent.soil_condition_boosts
    assert token in intent.expansion_tokens


def test_terrain_words_expand_but_never_boost() -> None:
    intent = parse_intent("replant trees on a hillside")
    assert intent.lay_terms == ("hillside",)
    assert intent.soil_condition_boosts == ()
    assert "slope" in intent.expansion_tokens


def test_a_weak_cue_acts_only_alone() -> None:
    alone = parse_intent("nothing will grow here")
    assert alone.soil_condition_boosts == ("nutrient_poor",)
    with_salt = parse_intent("salty field, nothing will grow")
    assert with_salt.soil_condition_boosts == ("saline_sodic",)
    assert "nothing will grow" in with_salt.lay_terms


def test_boosts_and_expansion_are_capped() -> None:
    intent = parse_intent("sour salty hardpan, burnt and sandy, toxic and washed out, won't soak in")
    assert len(intent.soil_condition_boosts) == MAXIMUM_INTENT_BOOSTS
    assert intent.soil_condition_boosts[0] == "acidic"
    assert len(intent.expansion_tokens) <= MAXIMUM_EXPANSION_TOKENS
    assert len(set(intent.expansion_tokens)) == len(intent.expansion_tokens)


def test_the_context_query_is_read_with_the_query() -> None:
    assert parse_intent("improve soil chemistry").ph_direction is None
    intent = parse_intent("improve soil chemistry", "our pasture is sour and thin")
    assert intent.ph_direction == "raise"
    assert intent.lay_terms == ("sour",)


def test_phrases_match_whole_words_after_normalising() -> None:
    assert parse_intent("resourceful farming").lay_terms == ()
    assert parse_intent("toxicity of aluminum").lay_terms == ()
    assert parse_intent("It WON’T GROW").lay_terms == ("won't grow",)
    assert normalise_text("  Sour  GROUND ") == "sour ground"


def test_the_echo_has_the_contract_keys() -> None:
    echo = parse_intent("hardpan").as_response()
    assert set(echo) == {"lay_terms", "ph_direction", "soil_condition_boosts", "expansion_tokens"}
    assert echo["soil_condition_boosts"] == ["compacted"]
    assert parse_intent("hardpan").as_response(soil_condition_boosts_applied=False)["soil_condition_boosts"] == []
    assert QueryIntent().as_response() == {
        "lay_terms": [],
        "ph_direction": None,
        "soil_condition_boosts": [],
        "expansion_tokens": [],
    }
