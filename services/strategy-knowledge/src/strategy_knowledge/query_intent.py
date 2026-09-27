"""Query intent: lay vocabulary and pH direction -> capped soil-condition boosts and BM25-only expansion tokens.

A data table keyed by the `SoilCondition` enum, read from the query plus the optional `context_query` (the
user's verbatim words). The dense query text is never rewritten. See AGENTS.md section "Query intent".
"""

import re
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Final, Literal

from strategy_knowledge.vocabulary import SoilCondition

PhDirection = Literal["raise", "lower"]

#: Longest `context_query` the request models accept (contract seam S3).
MAXIMUM_CONTEXT_QUERY_CHARACTERS: Final = 2000
#: Soil conditions one query's intent may boost; the per-record cap in `search.py` still applies on top.
MAXIMUM_INTENT_BOOSTS: Final = 2
MAXIMUM_EXPANSION_TOKENS: Final = 12
#: An explicit direction phrase ("raise the pH") outweighs a condition word ("acidic", "sour").
EXPLICIT_DIRECTION_WEIGHT: Final = 2
CONDITION_DIRECTION_WEIGHT: Final = 1


@dataclass(frozen=True, slots=True)
class LayVocabulary:
    """Farmer wording for one soil condition and the corpus words BM25 should also look for."""

    phrases: tuple[str, ...]
    expansion_tokens: tuple[str, ...]
    #: A weak cue ("won't grow") acts only when no other condition was named.
    weak: bool = False
    #: False for terrain words ("hillside"): they expand BM25 but boost nothing (AGENTS.md "Query intent").
    boosts: bool = True


#: SoilCondition -> lay vocabulary, in priority order: when more than `MAXIMUM_INTENT_BOOSTS` conditions are named,
#: the earlier entries win. acidic / alkaline boost only through `ph_direction` (a "sour" and "acid-loving" query
#: that disagrees boosts neither). Bare crop names ("blueberries") were removed after a 2026-09-27 review found
#: they fire on crop mentions with no pH intent at all (AGENTS.md "Query intent"); "acid-loving" is kept because
#: it states a pH preference directly, not merely a crop.
LAY_VOCABULARY: Final[dict[SoilCondition, LayVocabulary]] = {
    "acidic": LayVocabulary(("sour", "sweeten", "sweetening"), ("acidic", "acidity", "ph")),
    "alkaline": LayVocabulary(("acid-loving", "acid loving", "chalky soil", "soil is chalky"), ("alkaline", "ph")),
    "saline_sodic": LayVocabulary(
        ("salty", "salt crust", "white crust", "salted", "slick spots"),
        ("saline", "salinity", "salt", "sodic", "sodicity", "leaching"),
    ),
    "compacted": LayVocabulary(
        ("hardpan", "hard pan", "plow pan", "plowpan", "plough pan", "packed down", "packed hard", "water just sits"),
        ("compaction", "compacted", "hardpan", "subsoil"),
    ),
    "hydrophobic": LayVocabulary(
        ("water repellent", "repels water", "won't soak in", "wont soak in", "beads up"),
        ("hydrophobic", "repellent", "infiltration"),
    ),
    "burned_high_severity": LayVocabulary(
        ("burnt", "burned over", "scorched", "torched", "burnt over"),
        ("burned", "fire", "wildfire"),
    ),
    "contaminated": LayVocabulary(
        ("poisoned", "polluted", "toxic", "mine tailings"),
        ("contaminated", "contaminant", "metals", "remediation"),
    ),
    "sandy_coarse": LayVocabulary(
        ("sandy", "runs right through", "drains too fast", "won't hold water", "wont hold water"),
        ("sandy", "sand", "coarse", "retention"),
    ),
    "clay_heavy": LayVocabulary(("gumbo", "heavy clay", "sticky clay"), ("clay", "heavy")),
    "steep_slope": LayVocabulary(("hillside", "hillsides", "steep"), ("slope", "steep"), boosts=False),
    "erodible": LayVocabulary(
        (
            "washed out",
            "washing out",
            "washes out",
            "washed away",
            "washing away",
            "washes away",
            "gully",
            "gullies",
            "blowing away",
            "blows away",
        ),
        ("erosion", "runoff", "sediment"),
    ),
    "droughty": LayVocabulary(("dries out", "dried out", "bone dry", "no rain"), ("drought", "droughty", "moisture")),
    "nutrient_poor": LayVocabulary(
        ("won't grow", "wont grow", "nothing will grow", "nothing grows", "worn out", "tired soil", "dead soil"),
        ("fertility", "degraded"),
        weak=True,
    ),
}

#: pH direction -> (explicit phrases as regular expressions, the soil condition a matching strategy targets,
#: BM25 expansion tokens). Raising pH treats acidic soil (lime); lowering it treats alkaline soil (sulfur).
_POSSESSIVE: Final = r"(?:(?:the|my|our|soil|its|their|garden|field)\s+)*"
#: "lime"/"sulfur" count as a direction cue only near an application verb ("apply lime", "adding sulfur",
#: "lime application"), never as a bare mention: "lime-induced chlorosis", "free lime" and "sulfur deficiency"
#: are about a symptom or a nutrient, not an amendment the query is asking for (AGENTS.md "Query intent").
_APPLICATION_VERBS: Final = (
    r"(?:apply|applying|application|add|adding|addition|use|using|amend|amending|amendment|spread|spreading|"
    r"treat|treating|treatment|need|needs|needed)"
)


def _amendment_pattern(noun: str) -> str:
    """An amendment noun counted as a direction cue only next to an application verb, either word order."""
    return rf"\b{_APPLICATION_VERBS}\s+(?:\w+\s+){{0,2}}{noun}\b|\b{noun}\s+(?:application|treatment|amendment)\b"


PH_DIRECTION_PATTERNS: Final[dict[PhDirection, tuple[str, ...]]] = {
    "raise": (
        rf"\b(?:raise|raising|increase|increasing|boost|boosting|bring up|bringing up)\s+{_POSSESSIVE}ph\b",
        r"\bph\s+(?:is\s+|was\s+)?(?:too\s+|very\s+|way\s+|really\s+)?(?:low|down)\b",
        r"\blow\s+(?:soil\s+)?ph\b",
        r"\btoo\s+acidic\b",
        # The soil IS becoming acidic (a problem lime corrects), not a request to acidify it further.
        r"\b(?:getting|become|becoming|turning|going|grown)\s+(?:more|too|increasingly)\s+acidic\b",
        r"\b(?:correct|correcting|fix|fixing|neutralize|neutralise|reduce|reducing)\s+(?:the\s+|soil\s+)*acidity\b",
        r"\bless\s+acidic\b",
        _amendment_pattern(r"(?:lime|liming|limestone)"),
    ),
    "lower": (
        rf"\b(?:lower|lowering|reduce|reducing|decrease|decreasing|drop|dropping|bring down)\s+{_POSSESSIVE}ph\b",
        r"\bph\s+(?:is\s+|was\s+)?(?:too\s+|very\s+|way\s+|really\s+)?(?:high|up)\b",
        r"\bhigh\s+(?:soil\s+)?ph\b",
        r"\btoo\s+alkaline\b",
        r"\b(?:acidify|acidifying|acidification)\b",
        _amendment_pattern(r"(?:elemental\s+)?(?:sulfur|sulphur)"),
    ),
}
#: A bare condition word: "acidic soil" usually means fixing it (raise), "alkaline soil" lowering it.
PH_CONDITION_PATTERNS: Final[dict[PhDirection, tuple[str, ...]]] = {
    "raise": (r"\bacidic\b", r"\bacid\s+soils?\b"),
    "lower": (r"\balkaline\b", r"\bcalcareous\b"),
}
PH_DIRECTION_CONDITION: Final[dict[PhDirection, SoilCondition]] = {"raise": "acidic", "lower": "alkaline"}
PH_DIRECTION_EXPANSION: Final[dict[PhDirection, tuple[str, ...]]] = {
    "raise": ("lime", "limestone", "liming", "acidic", "acidity"),
    "lower": ("elemental", "sulfur", "acidify", "alkaline"),
}
PH_CONDITIONS: Final[frozenset[str]] = frozenset(PH_DIRECTION_CONDITION.values())
APOSTROPHES: Final = str.maketrans({"’": "'", "‘": "'", "`": "'"})


@dataclass(frozen=True, slots=True)
class QueryIntent:
    """What the query and context said beyond their literal words; echoed as `query_intent`."""

    lay_terms: tuple[str, ...] = ()
    ph_direction: PhDirection | None = None
    soil_condition_boosts: tuple[str, ...] = ()
    expansion_tokens: tuple[str, ...] = ()

    @property
    def soil_condition_demotions(self) -> tuple[str, ...]:
        """The pH condition opposite to `ph_direction`: raising pH demotes alkaline-soil records, and vice versa."""
        if self.ph_direction is None:
            return ()
        opposite: PhDirection = "lower" if self.ph_direction == "raise" else "raise"
        return (PH_DIRECTION_CONDITION[opposite],)

    def as_response(self, *, soil_condition_boosts_applied: bool = True) -> dict[str, Any]:
        """The contract-seam S3 echo; findings carry no soil tags, so their echo lists no soil boosts."""
        return {
            "lay_terms": list(self.lay_terms),
            "ph_direction": self.ph_direction,
            "soil_condition_boosts": list(self.soil_condition_boosts) if soil_condition_boosts_applied else [],
            "expansion_tokens": list(self.expansion_tokens),
        }


def normalise_text(text: str) -> str:
    """NFKC, lower-case, straight apostrophes, single spaces: the form every phrase is matched against."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).translate(APOSTROPHES).lower()).strip()


#: Produce names "sour" describes a cultivar's taste on, not soil ("sour cherry pie", "candied sour orange
#: peel"): excluded so the word keeps meaning soil-taste everywhere else, per contract S5's "sour" pasture case.
_SOUR_FALSE_FRIENDS: Final = ("cherry", "cherries", "orange", "oranges", "apple", "apples", "grape", "grapes", "gum")


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """A whole-word match for a lay phrase (letters, digits and apostrophes may not touch either end)."""
    if phrase == "sour":
        excluded = "|".join(_SOUR_FALSE_FRIENDS)
        return re.compile(rf"(?<![a-z0-9'])sour(?![a-z0-9'])(?!\s+(?:{excluded})\b)")
    return re.compile(rf"(?<![a-z0-9']){re.escape(phrase)}(?![a-z0-9'])")


_LAY_PATTERNS: Final = {
    condition: [(phrase, _phrase_pattern(phrase)) for phrase in vocabulary.phrases]
    for condition, vocabulary in LAY_VOCABULARY.items()
}
_DIRECTION_PATTERNS: Final = {
    direction: [re.compile(pattern) for pattern in patterns] for direction, patterns in PH_DIRECTION_PATTERNS.items()
}
_CONDITION_PATTERNS: Final = {
    direction: [re.compile(pattern) for pattern in patterns] for direction, patterns in PH_CONDITION_PATTERNS.items()
}


def _ph_direction(text: str, lay_conditions: Collection[str]) -> PhDirection | None:
    """Weighted vote of explicit phrases, bare condition words and lay pH words; a tie means no direction."""
    votes: dict[PhDirection, int] = {"raise": 0, "lower": 0}
    for direction in votes:
        explicit = sum(bool(pattern.search(text)) for pattern in _DIRECTION_PATTERNS[direction])
        condition = sum(bool(pattern.search(text)) for pattern in _CONDITION_PATTERNS[direction])
        lay = int(PH_DIRECTION_CONDITION[direction] in lay_conditions)
        votes[direction] = EXPLICIT_DIRECTION_WEIGHT * explicit + CONDITION_DIRECTION_WEIGHT * (condition + lay)
    if votes["raise"] == votes["lower"]:
        return None
    return "raise" if votes["raise"] > votes["lower"] else "lower"


def parse_intent(query: str, context_query: str | None = None) -> QueryIntent:
    """Lay terms, pH direction, soil-condition boosts (at most `MAXIMUM_INTENT_BOOSTS`) and expansion tokens."""
    text = normalise_text(f"{query} {context_query or ''}")
    lay_terms: list[str] = []
    named: dict[SoilCondition, LayVocabulary] = {}
    for condition, patterns in _LAY_PATTERNS.items():
        matched = [phrase for phrase, pattern in patterns if pattern.search(text)]
        if matched:
            lay_terms.extend(matched)
            named[condition] = LAY_VOCABULARY[condition]
    direction = _ph_direction(text, named.keys())
    strong = [condition for condition, vocabulary in named.items() if not vocabulary.weak]
    chosen = [condition for condition in (strong or list(named)) if condition not in PH_CONDITIONS]
    if direction is not None:
        chosen.insert(0, PH_DIRECTION_CONDITION[direction])
    boosted = [condition for condition in chosen if condition in PH_CONDITIONS or LAY_VOCABULARY[condition].boosts]
    boosts = tuple(boosted[:MAXIMUM_INTENT_BOOSTS])
    tokens = list(PH_DIRECTION_EXPANSION[direction]) if direction is not None else []
    for condition in chosen:
        tokens.extend(LAY_VOCABULARY[condition].expansion_tokens)
    return QueryIntent(
        lay_terms=tuple(dict.fromkeys(lay_terms)),
        ph_direction=direction,
        soil_condition_boosts=boosts,
        expansion_tokens=tuple(dict.fromkeys(tokens))[:MAXIMUM_EXPANSION_TOKENS],
    )
