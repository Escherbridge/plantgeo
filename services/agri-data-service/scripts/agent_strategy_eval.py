"""Agent eval harness for the strategy-knowledge (literature) tools: does the model reach for them,
land in the right family, and never invent an id it never retrieved.

Drives the SAME tool-calling path `agri-service agent ask` uses -- `OpenAiCompletionsClient.converse`
(`agent/llm.py`) over `agent.tools.WAREHOUSE_TOOLS`, under `agent.tools.run_context()` -- against the
live provider and the live warehouse. `search_environmental_strategies` / `get_environmental_strategies`
/ `search_strategy_research_findings` (`agent/tools.py`, backed by `agent/strategy_knowledge.py`) are
three of those tools; this harness scores whether the model called them and the call SUCCEEDED,
whether the answer actually names a retrieved strategy from the scenario's expected family, whether a
strategy the scenario forbids was recommended in the answer regardless of retrieval, whether the
answer names any id-shaped token it never actually retrieved this run (a hallucination, whether or
not that id happens to be real elsewhere in the corpus), whether a literature claim reads as
attributed, and whether every percentage the answer states traces back to the question or a tool
result (never the prompt or the published tool schemas, which are full of bare numbers used only as
parameter examples).

WHY NOT JUST CALL `interface/cli/agent.py::_ask`. That is the literal same-path function, but it
takes no model override, and this worker's brief forbids touching that file even for the small
refactor a `--model` hook would need. `_build_prompt` below reconstructs `_ask`'s three-line prompt
template by hand instead; see "Open items" in the worker's final report for the drift risk that
creates.

WHAT IT NEVER DOES. No mutation, no bucket write, no git operation. `--dry-run` touches only the two
local corpus JSON files under `--registry-dir`; a live run's only network calls are the ones
`agent.tools.WAREHOUSE_TOOLS` and `agent.llm.OpenAiCompletionsClient` already make for `agent ask`.

STRATEGY_KNOWLEDGE_URL MUST LAND IN `os.environ` BEFORE THE FIRST `agri_data_service` IMPORT.
`agri_data_service.config.settings` is a module-level singleton built once, at import time
(`config.py::settings = Settings()`); every import path this script needs -- `agent.tools`,
`agent.llm`, `agent.strategy_knowledge` -- pulls that same singleton in transitively. `main()`
therefore parses `--strategy-knowledge-url` and sets the environment variable BEFORE importing
anything from `agri_data_service`, and every such import below is deferred into a function body
(`# noqa: PLC0415`) for exactly that reason -- a top-level import here would freeze whatever the
process's ambient `STRATEGY_KNOWLEDGE_URL` happened to be, silently ignoring the CLI flag.

WHAT COMES OUT. `--out` receives one `<scenario-id>.json` per scenario (question, coordinates, final
answer text, iteration count, the full `tool_calls` ledger and `transcript` `agent.llm.converse`
returned, latency, and the deterministic score), plus `summary.json` and a human-readable
`summary.md` table. Exit status is 0 only when every scenario scored passed.

Run from services/agri-data-service, with `strategy-kb serve --transport http` already listening
on the loopback URL passed to --strategy-knowledge-url (default http://127.0.0.1:8765):

    uv run python scripts/agent_strategy_eval.py --out .omc/research/agent-evals-20260926
    uv run python scripts/agent_strategy_eval.py --out DIR --scenario boise-foothills-post-fire
    uv run python scripts/agent_strategy_eval.py --out DIR --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

SERVICE_ROOT: Final = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_ROOT / "src"))

SCENARIOS_FILE: Final = Path(__file__).resolve().parent / "agent_strategy_eval_scenarios.json"

#: Loopback default for a `strategy-kb serve --transport http` this harness expects already running.
DEFAULT_STRATEGY_KNOWLEDGE_URL: Final = "http://127.0.0.1:8765"

#: The `strategy-kb` corpus this repository's copy of `services/strategy-knowledge` builds its index
#: from; `--dry-run` reads it directly rather than needing the service up.
DEFAULT_REGISTRY_DIRECTORY: Final = SERVICE_ROOT.parent / "strategy-knowledge" / ".cache" / "corpus" / "strategies"

SUMMARY_JSON_NAME: Final = "summary.json"
SUMMARY_MARKDOWN_NAME: Final = "summary.md"
DRY_RUN_REPORT_NAME: Final = "dry_run_validation.json"

#: The three model-facing literature tools (`agent/tools.py`), restated rather than imported: this
#: worker does not import `agent/**` (owned by a parallel worker in the same pass), and CONTRACT.C3
#: freezes these names, so restating them here carries no real drift risk.
STRATEGY_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "search_environmental_strategies",
        "get_environmental_strategies",
        "search_strategy_research_findings",
    }
)

#: Exact refusal codes `agent/strategy_knowledge.py` (`NOT_CONFIGURED`, `UNAVAILABLE`,
#: `REJECTED_ARGUMENTS`) puts under the tool payload's `"error"` key, restated for the same reason.
STRATEGY_REFUSAL_REASONS: Final[frozenset[str]] = frozenset(
    {
        "strategy_knowledge_not_configured",
        "strategy_knowledge_unavailable",
        "strategy_knowledge_rejected_arguments",
    }
)

#: Every typed refusal in this codebase -- warehouse or strategy-knowledge -- opens its `note` with
#: exactly this sentence (`tools.py`; `strategy_knowledge.py::_REFUSAL_NOTES`), so its presence alone
#: proves a refusal happened even when the reason code above is not one of the three named ones.
_REFUSAL_TEXT_MARKER: Final = "this is a refusal"

#: Id shape used everywhere in the strategy-knowledge corpus: lowercase words joined by hyphens.
#: Calibrated to >=3 segments (>=2 hyphens) against strategy_registry.json/families.json: real ids
#: run 2-8 segments, but ordinary English hyphenated compounds ("post-fire", "long-term",
#: "peer-reviewed") are almost always exactly 2. Requiring 3 catches a fabricated- or copied-wrong id
#: without flagging ordinary prose; it is threshold-tuned, not schema-exact, and a genuine 2-segment
#: id (e.g. "windbreak-shelterbelt") sitting alone in prose will not be caught by this pattern.
_KEBAB_ID_PATTERN: Final = re.compile(r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+){2,}\b")

#: Simple regex for "the answer frames this as research/literature", not a citation validator.
_LITERATURE_ATTRIBUTION_PATTERN: Final = re.compile(
    r"\b(research|literature|studies|studied|peer[- ]reviewed|according to (a|the) (study|finding))\b",
    re.IGNORECASE,
)

#: A span the answer set off as an identifier rather than used in prose -- backticks or parens are
#: how a model typesets an id it is naming ("`post-fire-straw-mulching`", "(YYYY-MM-DD)"), and plain
#: hyphenated prose ("state-of-the-art") never gets this treatment. Only text inside a matched span
#: is scanned for a kebab-id-shaped token; unwrapped prose is not.
_IDENTIFIER_SPAN_PATTERN: Final = re.compile(r"`([^`]+)`|\(([^()]+)\)")

#: A percentage the answer states, in either the symbol or the word spelling. Matched case-
#: insensitively; the numeric magnitude alone (see `_numeric_magnitude`) is what gets compared
#: against the question/prompt/tool-result text, so "40%" and "40 percent" are interchangeable.
#: The trailing `\b` sits INSIDE the `percent` alternative, not after the whole group: `%` is
#: already a non-word character, so a `\b` immediately after it can never match text like "40%,"
#: or "40%" at end-of-string (neither side of that position is a word character); "percent" still
#: needs it, to keep "40 percentage increase" from matching as "40 percent".
_PERCENTAGE_PATTERN: Final = re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|percent\b)", re.IGNORECASE)

#: Any bare number, used only to (a) find the magnitude inside a percentage match and (b) collect
#: the "other numbers" the answer states, reported for information and never scored.
_NUMERIC_PATTERN: Final = re.compile(r"\b\d+(?:\.\d+)?\b")

#: A "%"/"percent"/"pct" marker, used only to scope `_numbers_near_percent_marker`'s proximity check
#: -- a bare number sitting near one of these in a TOOL RESULT still supports a percentage the answer
#: states, even when the result never spells the two out as one contiguous "40%"/"40 percent" span
#: (e.g. a `"value": 40, "unit": "percent"` pair).
_PERCENT_MARKER_PATTERN: Final = re.compile(r"%|percent|pct", re.IGNORECASE)

#: How close (characters, either direction) a bare number must sit to a `_PERCENT_MARKER_PATTERN`
#: match to count, in `_numbers_near_percent_marker`.
_PERCENT_MARKER_PROXIMITY: Final = 40

#: A negation phrase that marks a forbidden mention as advice AGAINST it rather than a recommendation
#: of it, when it sits close enough BEFORE that mention -- see `_mention_is_negated`. "not" alone also
#: covers "do not X" and "X is not recommended" (both contain the bare word "not"), so those are not
#: listed separately; "avoid X"/"don't use X"/"instead of X"/"not X" are the patterns this list is
#: calibrated against.
_NEGATION_MARKERS: Final[tuple[str, ...]] = ("avoid", "don't", "instead of", "not")

#: How many words immediately before a forbidden mention `_mention_is_negated` scans for a
#: `_NEGATION_MARKERS` hit. A negation must GOVERN the mention to suppress it -- one sitting this
#: close before it does; one governing an unrelated, later clause in the same (crudely split)
#: sentence ("Apply elemental sulfur at 200 lb/ac; do not apply when wet.") must not.
_NEGATION_WORD_WINDOW: Final = 6

#: Splits `final_text` into sentences for `_forbidden_hit`'s same-sentence negation check. Deliberately
#: crude (period/question mark/exclamation mark followed by whitespace, or a newline) -- this is a
#: scope for "did a negation word sit next to this mention", not a real sentence tokenizer.
_SENTENCE_SPLIT_PATTERN: Final = re.compile(r"(?<=[.!?])\s+|\n+")

#: Small stopword list of connector/temporal words that recur in nearly every strategy or family
#: name in this corpus (most post-fire strategies literally start with "post-fire ...") and would
#: otherwise inflate `_strategy_named_in_answer`'s token-overlap score without indicating the model
#: actually named THIS strategy rather than merely discussing the same hazard.
_NAME_TOKEN_STOPWORDS: Final[frozenset[str]] = frozenset({"with", "from", "into", "after", "before", "post", "fire"})
_SIGNIFICANT_TOKEN_MIN_LENGTH: Final = 4

#: Fraction of a strategy name's significant tokens (`_significant_name_tokens`) that must appear
#: somewhere in the scored text for rule (c) of `_strategy_named_in_answer` to count as a match.
#: Calibrated so a same-family strategy sharing one or two words never clears it (real names run
#: 3-8 significant tokens; one shared word is at most ~33-50%), while a genuine recommendation
#: compressed into a short markdown heading, with its remaining words elsewhere in the answer,
#: reliably does.
_NAME_TOKEN_OVERLAP_THRESHOLD: Final = 0.8

_NON_ALNUM_SPACE_PATTERN: Final = re.compile(r"[^a-z0-9\s]")
_WHITESPACE_RUN_PATTERN: Final = re.compile(r"\s+")
#: Splits a registry name into its NAME VARIANT segments -- "Contour log erosion barriers / log
#: terraces" yields "Contour log erosion barriers" and "log terraces" separately, so a model naming
#: only the first half of a compound name still counts.
_NAME_SEGMENT_SPLIT_PATTERN: Final = re.compile(r"\s*/\s*|\s+or\s+|;")
_PARENTHETICAL_PATTERN: Final = re.compile(r"\([^)]*\)")
_NAME_WORD_PATTERN: Final = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class ScenarioExpectation:
    """One eval scenario: a coordinate, a question, and what a correct answer must and must not do."""

    id: str
    longitude: float
    latitude: float
    question: str
    expect_strategy_tool: bool
    expected_family_ids: tuple[str, ...]
    forbidden_strategy_ids: tuple[str, ...]
    notes: str


def _scenario_from_json(payload: dict[str, Any]) -> ScenarioExpectation:
    """Build one scenario from its JSON entry, naming the missing field rather than raising KeyError."""
    try:
        return ScenarioExpectation(
            id=str(payload["id"]),
            longitude=float(payload["longitude"]),
            latitude=float(payload["latitude"]),
            question=str(payload["question"]),
            expect_strategy_tool=bool(payload["expect_strategy_tool"]),
            expected_family_ids=tuple(payload.get("expected_family_ids", ())),
            forbidden_strategy_ids=tuple(payload.get("forbidden_strategy_ids", ())),
            notes=str(payload.get("notes", "")),
        )
    except KeyError as error:
        raise ValueError(f"scenario entry missing required field {error}") from error


def load_scenarios(path: Path, *, only: Iterable[str] | None = None) -> list[ScenarioExpectation]:
    """Every scenario in `path`, or only the ids in `only`, in `only`'s order.

    Raises ValueError naming any id in `only` the file does not contain, so a `--scenario` typo
    fails loudly instead of silently running zero scenarios.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON list of scenarios")
    scenarios = [_scenario_from_json(entry) for entry in payload]
    if only is None:
        return scenarios
    wanted = list(only)
    by_id = {scenario.id: scenario for scenario in scenarios}
    missing = [scenario_id for scenario_id in wanted if scenario_id not in by_id]
    if missing:
        available = ", ".join(sorted(by_id))
        raise ValueError(f"unknown scenario id(s) {missing}; available: {available}")
    return [by_id[scenario_id] for scenario_id in wanted]


def load_registry_ids(directory: Path) -> tuple[frozenset[str], frozenset[str]]:
    """The known `(family_ids, strategy_ids)` read straight from the strategy-knowledge corpus.

    Reads `families.json`/`strategy_registry.json` under `directory` -- the same files `strategy-kb`
    builds its index from -- so `--dry-run` catches a scenario naming an id that was renamed or
    retired, without needing the service running.
    """
    families_path = directory / "families.json"
    strategies_path = directory / "strategy_registry.json"
    if not families_path.exists() or not strategies_path.exists():
        raise FileNotFoundError(
            f"no strategy-knowledge corpus at {directory}; expected families.json and strategy_registry.json"
        )
    families = json.loads(families_path.read_text(encoding="utf-8"))
    strategies = json.loads(strategies_path.read_text(encoding="utf-8"))["strategies"]
    family_ids = frozenset(str(family["family_id"]) for family in families)
    strategy_ids = frozenset(str(strategy["strategy_id"]) for strategy in strategies)
    return family_ids, strategy_ids


def validate_scenarios_against_registry(
    scenarios: Sequence[ScenarioExpectation],
    *,
    family_ids: frozenset[str],
    strategy_ids: frozenset[str],
) -> list[str]:
    """Every scenario id/family reference the loaded registry does not actually contain."""
    problems: list[str] = []
    for scenario in scenarios:
        problems.extend(
            f"{scenario.id}: expected_family_ids names unknown family {family_id!r}"
            for family_id in scenario.expected_family_ids
            if family_id not in family_ids
        )
        problems.extend(
            f"{scenario.id}: forbidden_strategy_ids names unknown strategy {strategy_id!r}"
            for strategy_id in scenario.forbidden_strategy_ids
            if strategy_id not in strategy_ids
        )
    return problems


# --- Deterministic scoring, no agri_data_service import required -------------------


@dataclass(frozen=True, slots=True)
class RetrievedStrategy:
    """One strategy named anywhere in a strategy tool's JSON result this run."""

    strategy_id: str
    family_id: str | None
    name: str | None


def _walk_json(node: Any) -> Iterator[dict[str, Any]]:
    """Yield every dict anywhere inside one parsed JSON tree, depth-first."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_json(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_json(item)


def _retrieved_strategies(content: str) -> tuple[RetrievedStrategy, ...]:
    """Every strategy one strategy tool's JSON result actually named, duck-typed on field names.

    Matches the registry's own field names (`strategy_id`, `family_id`, `name` -- see
    `strategy_knowledge.py::_STRATEGY_HIT_KEYS`/`_STRATEGY_RECORD_KEYS`) wherever they appear, so
    this survives nesting under `results`/`strategies`/echoed filters without needing the exact
    wrapper shape. `search_strategy_research_findings` results carry no inline `strategy_id`, only
    `linked_strategy_ids` (`_FINDING_KEYS`); those ids are collected too, with no family attached.
    """
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return ()
    found: list[RetrievedStrategy] = []
    seen_ids: set[str] = set()
    for node in _walk_json(parsed):
        strategy_id = node.get("strategy_id")
        if isinstance(strategy_id, str) and strategy_id and strategy_id not in seen_ids:
            seen_ids.add(strategy_id)
            family_id = node.get("family_id")
            name = node.get("name")
            found.append(
                RetrievedStrategy(
                    strategy_id=strategy_id,
                    family_id=family_id if isinstance(family_id, str) else None,
                    name=name if isinstance(name, str) else None,
                )
            )
        linked_ids = node.get("linked_strategy_ids")
        if isinstance(linked_ids, list):
            for linked_id in linked_ids:
                if isinstance(linked_id, str) and linked_id and linked_id not in seen_ids:
                    seen_ids.add(linked_id)
                    found.append(RetrievedStrategy(strategy_id=linked_id, family_id=None, name=None))
    return tuple(found)


def _refusal_reasons(content: str) -> frozenset[str]:
    """The named refusal reason(s) in one tool result, or `{"unnamed_refusal"}` for an unnamed one."""
    reasons = {reason for reason in STRATEGY_REFUSAL_REASONS if reason in content}
    if reasons:
        return frozenset(reasons)
    if _REFUSAL_TEXT_MARKER in content.lower():
        return frozenset({"unnamed_refusal"})
    return frozenset()


def _sentences(text: str) -> list[str]:
    """Split `text` into the crude sentence-ish spans `_forbidden_hit`'s negation check scopes to."""
    return _SENTENCE_SPLIT_PATTERN.split(text)


def _mention_trigger_words(forbidden_id: str, strategy: RetrievedStrategy | None) -> tuple[str, ...]:
    """Every word that can indicate a mention of `forbidden_id` starting: the id's own hyphen-split
    words, plus (when this run retrieved it) its registry name's significant tokens.

    Used only to locate roughly WHERE a mention starts, so `_mention_is_negated` knows which words
    precede it -- NOT to decide whether a mention happened at all. `_forbidden_hit` already
    established that via the exact id substring / name-variant / token-overlap triggers before ever
    calling this.
    """
    words = {word for word in forbidden_id.lower().replace("_", "-").split("-") if word}
    if strategy is not None and strategy.name is not None:
        words.update(_significant_name_tokens(strategy.name))
    return tuple(words)


def _mention_start_index(trigger_words: tuple[str, ...], lowered_sentence: str) -> int | None:
    """The earliest position any of `trigger_words` occurs as a whole word in `lowered_sentence`, or
    None when none of them do."""
    positions = [
        match.start()
        for word in trigger_words
        for match in (re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", lowered_sentence),)
        if match is not None
    ]
    return min(positions) if positions else None


def _mention_is_negated(lowered_sentence: str, mention_start: int) -> bool:
    """Whether a `_NEGATION_MARKERS` phrase sits in the `_NEGATION_WORD_WINDOW` words immediately
    BEFORE `mention_start` -- "avoid X"/"don't use X"/"instead of X"/"not X" all put it there. A
    marker appearing only AFTER the mention, governing some unrelated later clause, is deliberately
    invisible to this check.
    """
    preceding_words = lowered_sentence[:mention_start].split()
    preceding_window = " ".join(preceding_words[-_NEGATION_WORD_WINDOW:])
    return any(marker in preceding_window for marker in _NEGATION_MARKERS)


def _forbidden_hit(
    forbidden_ids: tuple[str, ...],
    retrieved: Sequence[RetrievedStrategy],
    final_text: str,
) -> bool:
    """A forbidden strategy counts as a hit when the answer RECOMMENDS it, regardless of retrieval.

    Naming a forbidden strategy -- its bare id, or (when this run retrieved it) any of the same NAMED
    triggers `_strategy_named_in_answer` uses for a family-hit: its id, a NAME VARIANT of its registry
    name, or enough of its significant name tokens -- is a hit in any sentence, UNLESS a negation
    (`_mention_is_negated`) sits in the few words immediately before that specific mention. Scoped to
    the mention's own preceding words, not "anywhere in the sentence": a negation governing an
    unrelated clause elsewhere in the same (crudely split) sentence -- "Apply elemental sulfur at 200
    lb/ac; do not apply when wet." -- must never launder the earlier mention into a non-hit just
    because a negation word appears somewhere after it. Scoped to one sentence overall (not the whole
    answer) so a negation early in a long answer still cannot launder a real recommendation stated
    later.
    """
    if not forbidden_ids:
        return False
    retrieved_by_id = {strategy.strategy_id: strategy for strategy in retrieved}
    for sentence in _sentences(final_text):
        lowered_sentence = sentence.lower()
        for forbidden_id in forbidden_ids:
            strategy = retrieved_by_id.get(forbidden_id)
            named = forbidden_id.lower() in lowered_sentence or (
                strategy is not None and _strategy_named_in_answer(strategy, sentence)
            )
            if not named:
                continue
            trigger_words = _mention_trigger_words(forbidden_id, strategy)
            mention_start = _mention_start_index(trigger_words, lowered_sentence)
            if mention_start is not None and _mention_is_negated(lowered_sentence, mention_start):
                continue
            return True
    return False


def _normalise_name_text(text: str) -> str:
    """Fold `text` to lowercase, hyphens to spaces, strip all other punctuation/markdown, and
    collapse whitespace -- so a registry name and a model's own markdown-formatted paraphrase of it
    (bullets, bold markers, hyphenation) compare on the same normalised spelling.
    """
    lowered = text.lower().replace("-", " ")
    stripped = _NON_ALNUM_SPACE_PATTERN.sub(" ", lowered)
    return _WHITESPACE_RUN_PATTERN.sub(" ", stripped).strip()


def _name_variants(name: str) -> tuple[str, ...]:
    """Every normalised spelling of a (possibly long, compound) registry `name` a model plausibly
    reproduces: the full name; the name with any parenthetical part removed; and each
    " / "/" or "/";"-separated segment of both (`_NAME_SEGMENT_SPLIT_PATTERN`).
    """
    without_parens = _PARENTHETICAL_PATTERN.sub(" ", name)
    variants = {_normalise_name_text(name), _normalise_name_text(without_parens)}
    for source in (name, without_parens):
        variants.update(_normalise_name_text(part) for part in _NAME_SEGMENT_SPLIT_PATTERN.split(source))
    return tuple(variant for variant in variants if variant)


def _significant_name_tokens(name: str) -> tuple[str, ...]:
    """Words (length >= 4) from `name`, minus `_NAME_TOKEN_STOPWORDS` -- what rule (c) of
    `_strategy_named_in_answer` requires `_NAME_TOKEN_OVERLAP_THRESHOLD` of to appear in the text.
    """
    words = _NAME_WORD_PATTERN.findall(name.lower())
    return tuple(
        word for word in words if len(word) >= _SIGNIFICANT_TOKEN_MIN_LENGTH and word not in _NAME_TOKEN_STOPWORDS
    )


def _strategy_named_in_answer(strategy: RetrievedStrategy, text: str) -> bool:
    """Whether `text` names `strategy` closely enough to count as actually recommending it.

    Three independent triggers, checked in order: (a) the strategy's own id, hyphen/underscore and
    case normalised; (b) any NAME VARIANT of its registry name (`_name_variants`) appearing as a
    substring of `text` once both are normalised the same way; or (c) at least
    `_NAME_TOKEN_OVERLAP_THRESHOLD` of the name's significant tokens (`_significant_name_tokens`)
    each appearing somewhere in `text`. Rule (c) is what lets a long compound registry name like
    "Agricultural lime application to correct soil acidity" match a model's own short markdown
    heading ("**1. Agricultural Lime Application**") even though neither (a) nor (b) fires on the
    heading alone -- the name's remaining words only need to appear somewhere else in the same text,
    not on the same line, since a model routinely splits one recommendation's name, evidence and
    application detail across separate headings and bullets.

    Used both for a positive family-hit, scoped by the caller to the whole answer, and for
    `_forbidden_hit`, scoped to one sentence to preserve its existing negation window.
    """
    if _contains_token(_normalise_id_text(text), _normalise_id_text(strategy.strategy_id)):
        return True
    if strategy.name is None:
        return False
    normalised_text = _normalise_name_text(text)
    if any(variant in normalised_text for variant in _name_variants(strategy.name)):
        return True
    tokens = _significant_name_tokens(strategy.name)
    if not tokens:
        return False
    matched = sum(1 for token in tokens if _contains_token(normalised_text, token))
    return (matched / len(tokens)) >= _NAME_TOKEN_OVERLAP_THRESHOLD


def _family_hit_from_answer(
    scenario: ScenarioExpectation,
    retrieved: Sequence[RetrievedStrategy],
    final_text: str,
) -> bool:
    """Whether the ANSWER itself names a retrieved strategy from one of the expected families.

    A tool retrieving the right family is not enough on its own -- the model may retrieve five
    candidates and recommend the one from the wrong family. This requires `_strategy_named_in_answer`
    to match the specific retrieved strategy's own id, a name variant, or enough of its significant
    name tokens against the whole answer before its family counts.

    A FORBIDDEN strategy can never satisfy this rule, even when it shares an expected family with a
    legitimate strategy and its own description clears the 80% token-overlap threshold somewhere in
    the answer. Concretely: "Avoid elemental sulfur application to lower soil pH; this acid-loving
    crops treatment will harm your yield." names 8/8 of a forbidden strategy's significant tokens in
    one sentence that is entirely advice AGAINST it -- without this guard that would count as a family
    hit for a same-family scenario just because the description was long enough to name.
    """
    for strategy in retrieved:
        if strategy.strategy_id in scenario.forbidden_strategy_ids:
            continue
        if strategy.family_id not in scenario.expected_family_ids:
            continue
        if _strategy_named_in_answer(strategy, final_text):
            return True
    return False


def _normalise_id_text(text: str) -> str:
    """Case-fold and unify `_`/`-` so `review_or_meta_analysis` (a tool result's raw field value)
    and the model's own `review-or-meta-analysis` spelling of it compare equal."""
    return text.lower().replace("_", "-")


def _contains_token(haystack: str, token: str) -> bool:
    """Whether `token` (already normalised) occurs in `haystack` (already normalised) as a whole
    id, not as a substring run into surrounding alphanumerics."""
    return re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", haystack) is not None


def _hallucinated_ids(
    final_text: str,
    retrieved_strategy_ids: Sequence[str],
    *,
    registry_strategy_ids: frozenset[str] = frozenset(),
    known_text: str = "",
) -> tuple[str, ...]:
    """Id-shaped tokens the answer names that this run never actually retrieved.

    Two independent triggers, both normalised (`_`/`-` and case) before comparing:
    (a) a REAL registry strategy_id named ANYWHERE in the answer that this run never retrieved --
        a genuine id is unambiguous regardless of how the model typesets it, so plain prose still
        counts; and
    (b) an id-SHAPED token that is not a known registry id, counted only when the answer sets it
        off as an identifier (backticks or parentheses) AND it does not appear anywhere in
        `known_text` -- the question, the prompt, and every tool result's raw text this run,
        including URLs and echoed field values like `family_id`/`study_type`. That second guard is
        what keeps a hyphenated date template ("(YYYY-MM-DD)") or an echoed underscore-cased field
        value ("review_or_meta_analysis" quoted back as "review-or-meta-analysis") from being
        flagged: both are text the model actually saw this turn, so naming them back is not
        invention. Plain prose compounds ("state-of-the-art") are never wrapped in backticks or
        parens by a model quoting an id, so the formatting guard alone excludes them from (b).
    """
    normalised_final = _normalise_id_text(final_text)
    retrieved = {_normalise_id_text(strategy_id) for strategy_id in retrieved_strategy_ids}
    registry = {_normalise_id_text(strategy_id) for strategy_id in registry_strategy_ids}
    known = _normalise_id_text(known_text)

    real_id_hits = {
        strategy_id for strategy_id in registry - retrieved if _contains_token(normalised_final, strategy_id)
    }
    unknown_token_hits = {
        token
        for span_match in _IDENTIFIER_SPAN_PATTERN.finditer(final_text)
        for token in _KEBAB_ID_PATTERN.findall(_normalise_id_text(span_match.group(1) or span_match.group(2) or ""))
        if token not in retrieved and token not in registry and not _contains_token(known, token)
    }
    return tuple(sorted(real_id_hits | unknown_token_hits))


def _tool_call_succeeded(content: str) -> bool:
    """Whether one strategy tool call's JSON result is a real answer, not a refusal or an error.

    Covers both failure shapes this codebase produces: a typed strategy-knowledge refusal
    (`_refusal_reasons`) and `agent/llm.py::execute_tool_call`'s own `{"error": ..., "tool": ...}`
    rendering of an exception raised outside that typed-refusal path. Anything else -- including an
    empty `results` list -- is a successful call that simply found nothing.
    """
    if _refusal_reasons(content):
        return False
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return False
    return not (isinstance(parsed, dict) and "error" in parsed)


def _numeric_magnitude(mention: str) -> str:
    """The bare number inside one percentage mention, e.g. `"40 percent"` -> `"40"`."""
    match = _NUMERIC_PATTERN.search(mention)
    return match.group(0) if match else mention


def _numbers_near_percent_marker(tool_result_text: str) -> frozenset[str]:
    """Bare numbers in `tool_result_text` sitting within `_PERCENT_MARKER_PROXIMITY` characters of a
    `_PERCENT_MARKER_PATTERN` marker -- a tool result that reports a magnitude and its percent unit as
    separate fields/words (e.g. `"value": 40, "unit": "percent"`) still supports the answer's "40%"
    even though the result never contains one contiguous "40%"/"40 percent" span.
    """
    marker_starts = [match.start() for match in _PERCENT_MARKER_PATTERN.finditer(tool_result_text)]
    if not marker_starts:
        return frozenset()
    return frozenset(
        match.group(0)
        for match in _NUMERIC_PATTERN.finditer(tool_result_text)
        if any(abs(match.start() - marker_start) <= _PERCENT_MARKER_PROXIMITY for marker_start in marker_starts)
    )


def _unsupported_percentages(final_text: str, percentage_form_text: str, tool_result_text: str) -> tuple[str, ...]:
    """Percentage mentions in the answer whose magnitude is not actually traceable to the question or
    a tool result this run.

    Compares magnitudes rather than exact substrings, so "40%" in the answer is supported by either
    "40%" or "40 percent" in `percentage_form_text` -- the wording may differ, but contract C4 only
    requires the MAGNITUDE be quoted as reported, not the exact phrasing. `percentage_form_text` is
    deliberately the question plus every tool result, NEVER `context_text` (the built prompt and the
    published tool schemas): those are full of bare numbers used only as parameter examples -- e.g. a
    `slope_pct` tool schema listing "30, 40, 70, 10, 5, 100" -- and treating any of those as support
    let an invented "cuts runoff 30%" pass just because 30 happens to appear in a schema description.
    A bare number that is not itself percent-formatted can still support a claim via
    `_numbers_near_percent_marker`, but ONLY when it occurs in `tool_result_text` specifically.
    """
    known_magnitudes = {
        _numeric_magnitude(match.group(0)) for match in _PERCENTAGE_PATTERN.finditer(percentage_form_text)
    }
    known_magnitudes |= _numbers_near_percent_marker(tool_result_text)
    return tuple(
        mention
        for match in _PERCENTAGE_PATTERN.finditer(final_text)
        for mention in (match.group(0),)
        if _numeric_magnitude(mention) not in known_magnitudes
    )


def _other_numeric_mentions(final_text: str) -> tuple[str, ...]:
    """Every non-percentage number in the answer, reported as information only -- never scored."""
    percentage_spans = [match.span() for match in _PERCENTAGE_PATTERN.finditer(final_text)]
    return tuple(
        match.group(0)
        for match in _NUMERIC_PATTERN.finditer(final_text)
        if not any(start <= match.start() < end for start, end in percentage_spans)
    )


@dataclass(frozen=True, slots=True)
class ScenarioScore:
    """One scenario's deterministic verdict, independent of which model produced the transcript."""

    strategy_tool_called: bool
    strategy_tool_attempted_count: int
    strategy_tool_succeeded_count: int
    warehouse_tool_call_count: int
    refusals_seen: tuple[str, ...]
    retrieved_strategy_ids: tuple[str, ...]
    retrieved_family_ids: tuple[str, ...]
    retrieved_family_hit: bool
    family_hit: bool
    forbidden_hit: bool
    hallucinated_ids: tuple[str, ...]
    literature_attribution: bool
    unsupported_percentages: tuple[str, ...]
    other_numeric_mentions: tuple[str, ...]
    passed: bool
    reasons: tuple[str, ...]


def score_transcript(  # noqa: PLR0912, PLR0913 - one branch and one input per scored PASS rule.
    scenario: ScenarioExpectation,
    final_text: str,
    transcript: Sequence[dict[str, Any]],
    *,
    provider_error: str | None = None,
    context_text: str = "",
    registry_strategy_ids: frozenset[str] = frozenset(),
) -> ScenarioScore:
    """Score one completed conversation against its scenario's expectations.

    PASSES when: the run actually produced a conversation (no provider error, a non-empty
    transcript); a strategy tool call SUCCEEDED and the answer itself names one of its results from
    an expected family (or, for a control scenario, no strategy tool was even attempted); no
    forbidden strategy was recommended in the answer, in any sentence not carrying its own negation;
    the answer names no id-shaped token this run could not have known about; a successful strategy
    tool call is followed by an answer that reads as literature-attributed; and every percentage the
    answer states traces back to the question or a tool result this run.

    `context_text` is everything the model was given this turn besides the transcript itself (the
    built prompt, the published tool schemas) -- passed through into the hallucination check as more
    of the "known text" a mention is allowed to have come from. It is deliberately EXCLUDED from the
    percentage check (see `_unsupported_percentages`): the published tool schemas carry bare numbers
    as parameter examples, and those must never be read as support for a stated percentage.
    `registry_strategy_ids` is the full corpus of real strategy ids, used only to catch a genuine id
    named in plain prose that this run never retrieved (see `_hallucinated_ids`).
    """
    tool_messages = [message for message in transcript if isinstance(message, dict) and message.get("role") == "tool"]
    strategy_messages = [message for message in tool_messages if message.get("name") in STRATEGY_TOOL_NAMES]
    warehouse_messages = [message for message in tool_messages if message.get("name") not in STRATEGY_TOOL_NAMES]
    tool_contents = [str(message.get("content", "")) for message in tool_messages]

    refusals_seen: set[str] = set()
    for content in tool_contents:
        refusals_seen |= _refusal_reasons(content)

    retrieved: list[RetrievedStrategy] = []
    for message in strategy_messages:
        retrieved.extend(_retrieved_strategies(str(message.get("content", ""))))

    retrieved_strategy_ids = tuple(sorted({strategy.strategy_id for strategy in retrieved}))
    retrieved_family_ids = tuple(sorted({strategy.family_id for strategy in retrieved if strategy.family_id}))

    strategy_tool_attempted_count = len(strategy_messages)
    strategy_tool_succeeded_count = sum(
        1 for message in strategy_messages if _tool_call_succeeded(str(message.get("content", "")))
    )
    strategy_tool_called = strategy_tool_attempted_count > 0
    strategy_tool_satisfied = strategy_tool_succeeded_count > 0

    retrieved_family_hit = any(family_id in scenario.expected_family_ids for family_id in retrieved_family_ids)
    family_hit = _family_hit_from_answer(scenario, retrieved, final_text)
    forbidden_hit = _forbidden_hit(scenario.forbidden_strategy_ids, retrieved, final_text)

    known_text = "\n".join([scenario.question, context_text, *tool_contents])
    hallucinated_ids = _hallucinated_ids(
        final_text,
        retrieved_strategy_ids,
        registry_strategy_ids=registry_strategy_ids,
        known_text=known_text,
    )
    literature_attribution = _LITERATURE_ATTRIBUTION_PATTERN.search(final_text) is not None
    tool_result_text = "\n".join(tool_contents)
    percentage_form_text = "\n".join([scenario.question, tool_result_text])
    unsupported_percentages = _unsupported_percentages(final_text, percentage_form_text, tool_result_text)
    other_numeric_mentions = _other_numeric_mentions(final_text)

    reasons: list[str] = []
    if provider_error is not None:
        reasons.append(f"provider error: {provider_error}")
    elif not transcript:
        reasons.append("empty transcript; the conversation never produced a message")

    if scenario.expect_strategy_tool:
        if not strategy_tool_called:
            reasons.append("expected a strategy tool call; none was made")
        elif not strategy_tool_satisfied:
            reasons.append(
                f"expected a successful strategy tool call; {strategy_tool_attempted_count} attempted, 0 succeeded"
            )
        elif not family_hit:
            reasons.append(
                "no retrieved strategy's family_id is both expected and named in the answer "
                f"(need one of {list(scenario.expected_family_ids)})"
            )
        if forbidden_hit:
            reasons.append(
                f"a forbidden strategy was recommended in the answer: {list(scenario.forbidden_strategy_ids)}"
            )
    elif strategy_tool_called:
        reasons.append("control scenario expected no strategy tool call, but one was made")

    if hallucinated_ids:
        reasons.append(
            f"final answer names id-shaped strategy tokens never retrieved this run: {list(hallucinated_ids)}"
        )
    if strategy_tool_satisfied and not literature_attribution:
        reasons.append("a strategy tool succeeded but the answer reads with no literature attribution")
    if unsupported_percentages:
        reasons.append(
            "final answer states percentage(s) not traceable to the question, prompt or tool results: "
            f"{list(unsupported_percentages)}"
        )

    return ScenarioScore(
        strategy_tool_called=strategy_tool_called,
        strategy_tool_attempted_count=strategy_tool_attempted_count,
        strategy_tool_succeeded_count=strategy_tool_succeeded_count,
        warehouse_tool_call_count=len(warehouse_messages),
        refusals_seen=tuple(sorted(refusals_seen)),
        retrieved_strategy_ids=retrieved_strategy_ids,
        retrieved_family_ids=retrieved_family_ids,
        retrieved_family_hit=retrieved_family_hit,
        family_hit=family_hit,
        forbidden_hit=forbidden_hit,
        hallucinated_ids=hallucinated_ids,
        literature_attribution=literature_attribution,
        unsupported_percentages=unsupported_percentages,
        other_numeric_mentions=other_numeric_mentions,
        passed=not reasons,
        reasons=tuple(reasons),
    )


@dataclass(frozen=True, slots=True)
class ScenarioRunResult:
    """One scenario's live outcome: the raw conversation plus its deterministic score."""

    scenario: ScenarioExpectation
    final_text: str
    iterations: int
    stopped_because: str
    tool_calls: tuple[dict[str, Any], ...]
    transcript: tuple[dict[str, Any], ...]
    latency_seconds: float
    provider_error: str | None
    score: ScenarioScore


# --- CLI and live orchestration ------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--out", type=Path, required=True, help="Directory for the per-scenario JSON, summary.json and summary.md."
    )
    parser.add_argument(
        "--scenario",
        dest="scenario_ids",
        action="append",
        default=[],
        help="Scenario id to run; repeatable. Default: every scenario in the file.",
    )
    parser.add_argument(
        "--strategy-knowledge-url",
        default=DEFAULT_STRATEGY_KNOWLEDGE_URL,
        help="STRATEGY_KNOWLEDGE_URL to export for the process before settings load.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Per-turn output ceiling; unset mirrors agent.llm.MAX_OUTPUT_TOKENS.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate scenarios against the registry; no LLM call.")
    parser.add_argument("--model", default=None, help="Override AGENT_LLM_MODEL for this run only.")
    parser.add_argument(
        "--registry-dir",
        type=Path,
        default=None,
        help=f"Strategy-knowledge corpus directory for --dry-run. Default: {DEFAULT_REGISTRY_DIRECTORY}",
    )
    return parser


def _build_prompt(scenario: ScenarioExpectation) -> str:
    """The prompt template `interface/cli/agent.py::_ask` builds, reconstructed here.

    Duplicated rather than imported -- see the module docstring's "WHY NOT JUST CALL _ask" -- so a
    wording change to `_ask`'s template needs a matching hand-edit here. Flagged as an open item.
    """
    return (
        f"The coordinate is longitude {scenario.longitude}, latitude {scenario.latitude} "
        f"(WGS84 decimal degrees). Use the warehouse tools to answer, quote the distances they "
        f"report, and treat any typed refusal as a statement about the lane rather than as an "
        f"absence of data.\n\n{scenario.question}"
    )


def _run_dry(scenarios: Sequence[ScenarioExpectation], registry_dir: Path, out: Path) -> int:
    """Validate every scenario's ids against the on-disk corpus; write and print the verdict."""
    try:
        family_ids, strategy_ids = load_registry_ids(registry_dir)
    except FileNotFoundError as error:
        report: dict[str, Any] = {"ok": False, "error": str(error)}
        (out / DRY_RUN_REPORT_NAME).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"error: {error}", file=sys.stderr)
        return 1
    problems = validate_scenarios_against_registry(scenarios, family_ids=family_ids, strategy_ids=strategy_ids)
    report = {
        "ok": not problems,
        "registry_dir": str(registry_dir),
        "scenarios_checked": [scenario.id for scenario in scenarios],
        "problems": problems,
    }
    (out / DRY_RUN_REPORT_NAME).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print(f"{len(scenarios)} scenario(s) validate against {registry_dir}")
    return 0


async def _run_live(scenarios: Sequence[ScenarioExpectation], args: argparse.Namespace) -> int:
    """Run every scenario through the live tool-calling client, writing results as each completes."""
    from agri_data_service.agent import tools as warehouse_tools  # noqa: PLC0415 - see module docstring
    from agri_data_service.agent.llm import (  # noqa: PLC0415 - see module docstring
        MAX_OUTPUT_TOKENS,
        LlmProviderError,
        OpenAiCompletionsClient,
        tool_schemas,
    )

    max_tokens = args.max_tokens if args.max_tokens is not None else MAX_OUTPUT_TOKENS
    client = OpenAiCompletionsClient.from_settings()
    if args.model:
        client = dataclasses.replace(client, credentials=client.credentials.model_copy(update={"model": args.model}))

    # Best-effort: an id genuinely in the corpus is still worth catching in `_hallucinated_ids`
    # even without a checked-out corpus, so a missing directory degrades to "skip that check"
    # rather than failing the whole live run.
    try:
        _, registry_strategy_ids = load_registry_ids(args.registry_dir or DEFAULT_REGISTRY_DIRECTORY)
    except FileNotFoundError:
        registry_strategy_ids = frozenset()

    # Every tool description (including the "as ISO YYYY-MM-DD" phrasing several warehouse tools
    # use) is published to the model on every turn via `tools=schemas`, not as a chat message -- so
    # it never appears in `transcript` even though the model plainly saw it. Folding the schema text
    # into `context_text` is what keeps a model quoting that phrasing back (e.g. asking the caller
    # for "(YYYY-MM-DD)") from being flagged as an invented id.
    tool_schema_text = json.dumps(tool_schemas())

    async def run_one(scenario: ScenarioExpectation) -> ScenarioRunResult:
        prompt = _build_prompt(scenario)
        provider_error: str | None = None
        outcome: dict[str, Any]
        started = time.monotonic()
        try:
            async with warehouse_tools.run_context():
                outcome = await client.converse([{"role": "user", "content": prompt}], max_tokens=max_tokens)
        except LlmProviderError as error:
            outcome = {
                "final_text": "",
                "iterations": 0,
                "tool_calls": [],
                "transcript": [],
                "stopped_because": "provider_error",
            }
            provider_error = str(error)
        latency_seconds = time.monotonic() - started
        final_text = str(outcome.get("final_text", ""))
        transcript = list(outcome.get("transcript") or ())
        return ScenarioRunResult(
            scenario=scenario,
            final_text=final_text,
            iterations=int(outcome.get("iterations", 0)),
            stopped_because=str(outcome.get("stopped_because", "")),
            tool_calls=tuple(outcome.get("tool_calls") or ()),
            transcript=tuple(transcript),
            latency_seconds=latency_seconds,
            provider_error=provider_error,
            score=score_transcript(
                scenario,
                final_text,
                transcript,
                provider_error=provider_error,
                context_text=f"{prompt}\n{tool_schema_text}",
                registry_strategy_ids=registry_strategy_ids,
            ),
        )

    results: list[ScenarioRunResult] = []
    for scenario in scenarios:
        result = await run_one(scenario)
        results.append(result)
        _write_scenario_result(args.out, result)
    _write_summary(
        args.out, results, model=client.credentials.model, strategy_knowledge_url=args.strategy_knowledge_url
    )
    return 0 if all(result.score.passed for result in results) else 1


def _write_scenario_result(out: Path, result: ScenarioRunResult) -> None:
    """Persist one scenario's full transcript and score, named after the scenario id."""
    payload = {
        "scenario_id": result.scenario.id,
        "longitude": result.scenario.longitude,
        "latitude": result.scenario.latitude,
        "question": result.scenario.question,
        "expect_strategy_tool": result.scenario.expect_strategy_tool,
        "expected_family_ids": list(result.scenario.expected_family_ids),
        "forbidden_strategy_ids": list(result.scenario.forbidden_strategy_ids),
        "final_text": result.final_text,
        "iterations": result.iterations,
        "stopped_because": result.stopped_because,
        "latency_seconds": result.latency_seconds,
        "provider_error": result.provider_error,
        "tool_calls": list(result.tool_calls),
        "transcript": list(result.transcript),
        "score": dataclasses.asdict(result.score),
    }
    (out / f"{result.scenario.id}.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def _write_summary(
    out: Path,
    results: Sequence[ScenarioRunResult],
    *,
    model: str,
    strategy_knowledge_url: str,
) -> None:
    """Write `summary.json` (machine-readable) and `summary.md` (one row per scenario) into `out`."""
    passed = sum(1 for result in results if result.score.passed)
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": model,
        "strategy_knowledge_url": strategy_knowledge_url,
        "scenarios_run": len(results),
        "scenarios_passed": passed,
        "scenarios_failed": len(results) - passed,
        "scores": [{"scenario_id": result.scenario.id, **dataclasses.asdict(result.score)} for result in results],
    }
    (out / SUMMARY_JSON_NAME).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / SUMMARY_MARKDOWN_NAME).write_text(_summary_markdown(results), encoding="utf-8")


def _summary_markdown(results: Sequence[ScenarioRunResult]) -> str:
    """One markdown table row per scenario: what was expected, what happened, pass/fail and why."""
    lines = [
        "| scenario | expect_strategy_tool | strategy_tool_called | family_hit | forbidden_hit "
        "| hallucinated_ids | passed | reasons |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        score = result.score
        lines.append(
            f"| {result.scenario.id} | {result.scenario.expect_strategy_tool} | {score.strategy_tool_called} | "
            f"{score.family_hit} | {score.forbidden_hit} | {', '.join(score.hallucinated_ids) or '-'} | "
            f"{score.passed} | {'; '.join(score.reasons) or '-'} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, export STRATEGY_KNOWLEDGE_URL, then dry-validate or run every scenario live."""
    args = _build_parser().parse_args(argv)
    # MUST happen before any agri_data_service import; see the module docstring.
    os.environ["STRATEGY_KNOWLEDGE_URL"] = args.strategy_knowledge_url
    try:
        scenarios = load_scenarios(SCENARIOS_FILE, only=args.scenario_ids or None)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        return _run_dry(scenarios, args.registry_dir or DEFAULT_REGISTRY_DIRECTORY, args.out)
    try:
        return asyncio.run(_run_live(scenarios, args))
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
