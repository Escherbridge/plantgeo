"""Agent eval harness for the strategy-knowledge (literature) tools: does the model reach for them,
land in the right family, and never invent an id it never retrieved -- across a MULTI-TURN
conversation, with every literature number bound to the retrieved record it actually cites
(CONTRACT-WAVE2.md seam S5, replacing the earlier pass^k plan).

Drives the SAME tool-calling path `agri-service agent ask` uses -- `OpenAiCompletionsClient.converse`
(`agent/llm.py`) over `agent.tools.WAREHOUSE_TOOLS`, under `agent.tools.run_context()` -- against the
live provider and the live warehouse, one `tools.run_context(strategy_context=...)` per user turn.
`search_environmental_strategies` / `get_environmental_strategies` / `search_strategy_research_findings`
(`agent/tools.py`, backed by `agent/strategy_knowledge.py`) are three of those tools; this harness
scores, on EVERY turn, whether the model called them and the call SUCCEEDED, whether the answer
actually names a retrieved strategy from the scenario's expected family, whether a strategy the
scenario forbids was recommended in the answer regardless of retrieval, whether the answer names any
id-shaped token it never actually retrieved this run (a hallucination, whether or not that id happens
to be real elsewhere in the corpus), whether a literature claim reads as attributed, and whether every
literature number the answer states is bound to a retrieved record's own text (`score_grounding`) --
never merely "somewhere in this turn's tool text". A conversation passes only when every one of its
turns does.

WHY NOT JUST CALL `interface/cli/agent.py::_ask`. That is the literal same-path function, but it
takes no model override and no mid-conversation continuation, and this worker's brief forbids
touching that file beyond the one shared `agent_system_message` hook. `_build_prompt` below
reconstructs `_ask`'s coordinate-preamble template by hand for the FIRST turn only; see "Open items"
in the worker's final report for the drift risk that creates. The SYSTEM message itself is no longer
hand-duplicated this way -- both callers now build it from `agent.llm.agent_system_message`.

WHAT IT NEVER DOES. No mutation, no bucket write, no git operation. `--dry-run` touches only the two
local corpus JSON files under `--registry-dir`; `--rescore` touches only stored result files already
on disk, offline; a live run's only network calls are the ones `agent.tools.WAREHOUSE_TOOLS` and
`agent.llm.OpenAiCompletionsClient` already make for `agent ask`.

STRATEGY_KNOWLEDGE_URL MUST LAND IN `os.environ` BEFORE THE FIRST `agri_data_service` IMPORT.
`agri_data_service.config.settings` is a module-level singleton built once, at import time
(`config.py::settings = Settings()`); every import path this script needs -- `agent.tools`,
`agent.llm`, `agent.strategy_knowledge` -- pulls that same singleton in transitively. `main()`
therefore parses `--strategy-knowledge-url` and sets the environment variable BEFORE importing
anything from `agri_data_service`, and every such import below is deferred into a function body
(`# noqa: PLC0415`) for exactly that reason -- a top-level import here would freeze whatever the
process's ambient `STRATEGY_KNOWLEDGE_URL` happened to be, silently ignoring the CLI flag. `--rescore`
is the one exception: it never imports `agri_data_service` at all, so it needs no such export.

BUDGET. `scenarios x models x samples` must not exceed `MAX_TOTAL_RUNS_WITHOUT_OVERRIDE` (20) unless
`--allow-over-budget` is passed -- CONTRACT-WAVE2.md seam S5's "10-20 agent runs total per measurement
round". `--model` is repeatable (default: `DEFAULT_MODELS`, Haiku 4.5 and Gemini 2.5 Flash-Lite);
`--samples` repeats every (scenario, model) pair independently, each its own run directory.

WHAT COMES OUT. `--out` receives `<model-slug>/sample-<n>/<scenario-id>.json`, one per conversation --
every turn's user message, final answer text, iteration count, `tool_calls` ledger, transcript and
per-turn score -- plus `summary.json` and a human-readable `summary.md` (one row per turn). Exit
status is 0 only when every conversation's every turn passed. `--rescore DIR` instead writes
`rescore.json`/`rescore.md`: an old-verdict-vs-new-verdict table for every turn stored under `DIR`.

Run from services/agri-data-service, with `strategy-kb serve --transport http` already listening
on the loopback URL passed to --strategy-knowledge-url (default http://127.0.0.1:8765):

    uv run python scripts/agent_strategy_eval.py --out .omc/research/agent-evals-20260926/roundN
    uv run python scripts/agent_strategy_eval.py --out DIR --scenario boise-foothills-post-fire
    uv run python scripts/agent_strategy_eval.py --out DIR --model anthropic/claude-haiku-4.5 --samples 3
    uv run python scripts/agent_strategy_eval.py --out DIR --dry-run
    uv run python scripts/agent_strategy_eval.py --out DIR --rescore .omc/research/agent-evals-20260926
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
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

SERVICE_ROOT: Final = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_ROOT / "src"))

SCENARIOS_FILE: Final = Path(__file__).resolve().parent / "agent_strategy_eval_scenarios.json"
#: Soil data plane DESIGN section 8: base runs with no typed question, seeded by the server-built site brief.
SITE_BRIEF_SCENARIOS_FILE: Final = Path(__file__).resolve().parent / "agent_site_brief_eval_scenarios.json"

#: Loopback default for a `strategy-kb serve --transport http` this harness expects already running.
DEFAULT_STRATEGY_KNOWLEDGE_URL: Final = "http://127.0.0.1:8765"

#: The `strategy-kb` corpus this repository's copy of `services/strategy-knowledge` builds its index
#: from; `--dry-run` reads it directly rather than needing the service up.
DEFAULT_REGISTRY_DIRECTORY: Final = SERVICE_ROOT.parent / "strategy-knowledge" / ".cache" / "corpus" / "strategies"

SUMMARY_JSON_NAME: Final = "summary.json"
SUMMARY_MARKDOWN_NAME: Final = "summary.md"
DRY_RUN_REPORT_NAME: Final = "dry_run_validation.json"
RESCORE_REPORT_NAME: Final = "rescore.json"
RESCORE_MARKDOWN_NAME: Final = "rescore.md"

#: CONTRACT-WAVE2.md seam S5: "replaces pass^k" with a 10-20-run-total multi-turn budget, at these
#: two models unless `--model` overrides them.
DEFAULT_MODELS: Final[tuple[str, ...]] = ("anthropic/claude-haiku-4.5", "google/gemini-2.5-flash-lite")

#: `scenarios x models x samples` above this refuses to run without `--allow-over-budget` -- see
#: `_run_live`'s budget guard and CONTRACT-WAVE2.md seam S5.
MAX_TOTAL_RUNS_WITHOUT_OVERRIDE: Final = 20

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

#: NFKC-fold, then map every dash-like codepoint a source or a model might use for a negative
#: magnitude or a range to the ASCII hyphen: MINUS SIGN, FIGURE DASH, EN DASH. Also folds the
#: typographic RIGHT SINGLE QUOTATION MARK to a plain apostrophe, since NFKC does not: a model's
#: "don't" is as likely to carry U+2019 as an ASCII "'", and `_NEGATION_MARKER_PATTERN` must match
#: both. Applied by `_normalise_scanned_text` to every string this scorer runs a regex over -- the
#: final answer, the question, and every tool result -- so "−65%" (U+2212, escaped by `json.dumps`)
#: reads as "-65%" before any pattern below ever sees it. Regression: FINDINGS.md "Scorer caveats" --
#: decoding the JSON first (see `_scannable_text`) turns the literal 6-character escape sequence back
#: into a real minus sign; THIS table is what then makes that sign comparable to a plain hyphen.
_DASH_NORMALISATION_TABLE: Final = str.maketrans({"−": "-", "‒": "-", "–": "-", "’": "'"})


def _normalise_scanned_text(text: str) -> str:
    """NFKC-fold `text` and fold its dash-like codepoints to ASCII hyphen (`_DASH_NORMALISATION_TABLE`)."""
    return unicodedata.normalize("NFKC", text).translate(_DASH_NORMALISATION_TABLE)


def _scannable_text(content: str) -> str:
    """The text of one tool message's content, JSON-decoded first so an escaped Unicode character
    (`json.dumps`'s default `ensure_ascii=True` turns a real U+2212 into the literal six characters
    `\\u2212`) reads as the real character it names, falling back to the raw string when `content` is
    not valid JSON. `json.loads` already un-escapes on the way in; a structured result is then
    re-rendered with `ensure_ascii=False` so the round trip cannot re-introduce the same escaping.
    """
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return content
    if isinstance(parsed, str):
        return parsed
    return json.dumps(parsed, ensure_ascii=False, default=str)


#: A percentage the answer states, in either the symbol or the word spelling. Matched case-
#: insensitively; the numeric magnitude alone (see `_numeric_magnitude`) is what gets compared
#: against the question/prompt/tool-result text, so "40%" and "40 percent" are interchangeable.
#: The trailing `\b` sits INSIDE the `percent` alternative, not after the whole group: `%` is
#: already a non-word character, so a `\b` immediately after it can never match text like "40%,"
#: or "40%" at end-of-string (neither side of that position is a word character); "percent" still
#: needs it, to keep "40 percentage increase" from matching as "40 percent".
_PERCENTAGE_PATTERN: Final = re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|percent\b)", re.IGNORECASE)

#: A RANGE percentage -- "20-30%" or "20 to 30 percent" -- scored as one mention carrying BOTH
#: endpoints (`_percentage_mentions`), not as a single-number mention that only captures the second
#: half. Matched before `_PERCENTAGE_PATTERN` and excluded from it by span, so a range is never
#: double-counted as its own trailing single-number mention too.
_RANGE_PERCENTAGE_PATTERN: Final = re.compile(
    r"\b(\d+(?:\.\d+)?)\s?(?:-|to)\s?(\d+(?:\.\d+)?)\s?(?:%|percent\b)", re.IGNORECASE
)

#: Any bare number, used only to (a) find the magnitude inside a percentage match and (b) collect
#: the "other numbers" the answer states, reported for information and never scored.
_NUMERIC_PATTERN: Final = re.compile(r"\b\d+(?:\.\d+)?\b")

#: Word-bounded negation markers that mark a forbidden mention as advice AGAINST it rather than a
#: recommendation of it, when one sits close enough BEFORE that mention -- see `_mention_is_negated`.
#: OPEN DEFECT, FIXED (FINDINGS.md "Scorer caveats", 2026-09-27): the previous version matched these
#: as plain substrings ("not" in "another"/"notably"/"cannot"), so an unrelated word containing one of
#: these letters sequences anywhere before a mention could falsely suppress a real recommendation.
#: Every marker here is now `\b`-bounded, so only the WHOLE word/phrase counts. `avoid(?:s|ed|ing)?`
#: covers the inflections a plain "avoid" substring used to catch for free ("avoiding elemental
#: sulfur"); `don't`/`do not` are additional common phrasings a real-world answer uses. The apostrophe
#: is ASCII because `_normalise_scanned_text` folds U+2019 to it before this pattern ever runs.
#: `instead of`/`rather than`/`without` are deliberately NOT here -- see `_CLAUSE_HEADED_MARKER_PATTERN`.
_NEGATION_MARKER_PATTERN: Final = re.compile(r"\b(?:avoid(?:s|ed|ing)?|don't|do not|not|never)\b", re.IGNORECASE)

#: How many words immediately before a forbidden mention `_mention_is_negated` scans for a
#: `_NEGATION_MARKER_PATTERN` hit. A negation must GOVERN the mention to suppress it -- one sitting
#: this close before it does; one governing an unrelated, later clause in the same (crudely split)
#: sentence ("Apply elemental sulfur at 200 lb/ac; do not apply when wet.") must not.
_NEGATION_WORD_WINDOW: Final = 6

#: Contrastive markers whose governed noun phrase can END before a LATER mention in the same
#: word-window -- "Instead of lime, use X" and "Without liming first, apply X" name X as the
#: RECOMMENDATION, not the thing avoided, even though the marker sits within `_NEGATION_WORD_WINDOW`
#: words of X. A plain window scan (like `_NEGATION_MARKER_PATTERN`'s) cannot tell the two apart, so
#: these are checked separately by `_clause_headed_marker_negates`, which requires no comma or
#: clause-ending verb between the marker and the mention (wave-2 fix-stage review: the previous window
#: scan false-suppressed exactly this "Instead of X, use <forbidden>" shape).
_CLAUSE_HEADED_MARKER_PATTERN: Final = re.compile(r"\b(?:instead of|rather than|without)\b", re.IGNORECASE)

#: Ends a `_CLAUSE_HEADED_MARKER_PATTERN` marker's own noun phrase: a comma, or a verb that starts a
#: new recommendation clause. Either one between the marker and a mention means the marker no longer
#: governs that mention.
_CLAUSE_BOUNDARY_PATTERN: Final = re.compile(
    r",|\b(?:use|uses|used|using|apply|applies|applied|applying|try|choose|recommend|add)\b", re.IGNORECASE
)

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
    """One eval scenario: a coordinate, one or more user turns, and what a correct answer must and
    must not do, checked independently on EVERY turn.

    `turns` is CONTRACT-WAVE2.md seam S5's multi-turn scenario: 2-3 user messages the runner feeds
    one at a time, each opening a fresh `tools.run_context(strategy_context=...)` seeded with every
    user turn asked SO FAR. `question` is kept as the single-turn, backward-compatible field (the
    scenario JSON's required field, and what a single-turn scenario's `turns` defaults to); a
    multi-turn scenario sets both, with `question` naming the first turn for a human skimming the
    file and `turns` carrying the whole conversation.
    """

    id: str
    longitude: float
    latitude: float
    question: str
    turns: tuple[str, ...]
    expect_strategy_tool: bool
    expected_family_ids: tuple[str, ...]
    forbidden_strategy_ids: tuple[str, ...]
    notes: str
    base_run: bool = False
    """Soil data plane: turn 0 is the base run -- no typed question, the site brief prepended, and
    `user_question` None so the brief's seed becomes `context_query` (C4)."""


def _scenario_from_json(payload: dict[str, Any]) -> ScenarioExpectation:
    """Build one scenario from its JSON entry, naming the missing field rather than raising KeyError."""
    try:
        question = str(payload["question"])
        raw_turns = payload.get("turns")
        turns = tuple(str(turn) for turn in raw_turns) if raw_turns else (question,)
        return ScenarioExpectation(
            id=str(payload["id"]),
            longitude=float(payload["longitude"]),
            latitude=float(payload["latitude"]),
            question=question,
            turns=turns,
            expect_strategy_tool=bool(payload["expect_strategy_tool"]),
            expected_family_ids=tuple(payload.get("expected_family_ids", ())),
            forbidden_strategy_ids=tuple(payload.get("forbidden_strategy_ids", ())),
            notes=str(payload.get("notes", "")),
            base_run=bool(payload.get("base_run", False)),
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


def _mention_position(forbidden_id: str, strategy: RetrievedStrategy | None, lowered_sentence: str) -> int | None:
    """Where the mention `_forbidden_hit` already found actually STARTS in `lowered_sentence`.

    OPEN DEFECT, FIXED (FINDINGS.md "Scorer caveats", 2026-09-27): the previous version anchored the
    negation window at the EARLIEST occurrence of any one of the id's hyphen-split words ANYWHERE in
    the sentence, so an unrelated earlier use of a shared word -- the sentence's own "soil" long
    before an id ending in "-soil-acidification" -- pointed the window at the wrong place entirely
    and could read an unrelated, distant negation as governing the real mention. This anchors at the
    REAL match instead: the id's own literal substring (rule (a)), or a NAME VARIANT's literal span
    (rule (b)), located with a lenient pattern that tolerates a model punctuating a compound name
    differently from the registry (spaces/hyphens/case).

    A rule (c) match (scattered significant-token overlap, no single contiguous span) anchors at the
    EARLIEST of its own matched tokens instead of going unanchored (2026-09-27 fix-stage review,
    `test_family_hit_excludes_a_forbidden_strategy_even_when_its_description_clears_the_token_threshold`):
    "avoid elemental sulfur application..." must still read as negated even though "elemental sulfur
    application to lower soil pH" and "acid-loving crops" are split across the same sentence by other
    words, because a real negation immediately precedes the very first token the match is built from.
    This only ever narrows a hit into a non-hit when an actual negation marker governs that token, so
    an unrelated distant negation elsewhere in the sentence still cannot launder an unqualified mention.
    """
    normalised_id = forbidden_id.lower().replace("_", "-")
    index = lowered_sentence.find(normalised_id)
    if index != -1:
        return index
    if strategy is None or strategy.name is None:
        return None
    for variant in _name_variants(strategy.name):
        words = variant.split()
        if not words:
            continue
        pattern = re.compile(r"\b" + r"[\W_]+".join(re.escape(word) for word in words) + r"\b")
        match = pattern.search(lowered_sentence)
        if match is not None:
            return match.start()
    earliest: int | None = None
    for token in _significant_name_tokens(strategy.name):
        match = re.search(rf"\b{re.escape(token)}\b", lowered_sentence)
        if match is not None and (earliest is None or match.start() < earliest):
            earliest = match.start()
    return earliest


def _clause_headed_marker_negates(lowered_sentence: str, mention_start: int) -> bool:
    """Whether an `instead of`/`rather than`/`without` marker before `mention_start` still GOVERNS it.

    Scans every such marker before the mention (not just the last `_NEGATION_WORD_WINDOW` words: the
    marker's own noun phrase can run longer than that window, e.g. "instead of applying elemental
    sulfur"), and negates only when the text between the marker and the mention has no comma and no
    clause-ending verb (`_CLAUSE_BOUNDARY_PATTERN`) -- either one closes the marker's noun phrase and
    hands the sentence to a NEW recommendation before the mention, as in "Instead of lime, use X."
    """
    for match in _CLAUSE_HEADED_MARKER_PATTERN.finditer(lowered_sentence[:mention_start]):
        between = lowered_sentence[match.end() : mention_start]
        if _CLAUSE_BOUNDARY_PATTERN.search(between) is None:
            return True
    return False


def _mention_is_negated(lowered_sentence: str, mention_start: int) -> bool:
    """Whether a negation GOVERNS `mention_start`: a plain `_NEGATION_MARKER_PATTERN` phrase in the
    `_NEGATION_WORD_WINDOW` words immediately before it ("avoid X"/"don't use X"/"not X"/"never X"),
    or a `_CLAUSE_HEADED_MARKER_PATTERN` phrase whose own noun phrase reaches it uninterrupted
    (`_clause_headed_marker_negates`). A marker appearing only AFTER the mention, governing some
    unrelated later clause, is deliberately invisible to either check.

    The plain-marker window never crosses a semicolon into an EARLIER clause (2026-09-27 fix-stage
    review): "Do not till the soil; apply elemental-sulfur-soil-acidification instead." must not read
    the "Do not" governing "till" as also suppressing the sulfur recommendation two clauses later --
    `_sentences` splits only on sentence-ending punctuation, so both clauses share one "sentence" here.
    """
    preceding_clause = lowered_sentence[:mention_start].rsplit(";", 1)[-1]
    preceding_words = preceding_clause.split()
    preceding_window = " ".join(preceding_words[-_NEGATION_WORD_WINDOW:])
    if _NEGATION_MARKER_PATTERN.search(preceding_window) is not None:
        return True
    return _clause_headed_marker_negates(lowered_sentence, mention_start)


def _forbidden_hit(
    forbidden_ids: tuple[str, ...],
    retrieved: Sequence[RetrievedStrategy],
    final_text: str,
) -> bool:
    """A forbidden strategy counts as a hit when the answer RECOMMENDS it, regardless of retrieval.

    Naming a forbidden strategy -- its bare id, or (when this run retrieved it) any of the same NAMED
    triggers `_strategy_named_in_answer` uses for a family-hit: its id, a NAME VARIANT of its registry
    name, or enough of its significant name tokens -- is a hit in any sentence, UNLESS a negation
    (`_mention_is_negated`) sits in the few words immediately before that specific mention's REAL
    position (`_mention_position`). A scattered token-overlap match (rule (c)) has no single
    contiguous span, so it anchors at the EARLIEST of its own matched tokens instead: a real negation
    has to govern that first token specifically, so a negation elsewhere in the sentence still cannot
    launder a real recommendation it cannot actually be shown to govern.

    Scoped to the mention's own preceding words, not "anywhere in the sentence": a negation governing
    an unrelated clause elsewhere in the same (crudely split) sentence -- "Apply elemental sulfur at
    200 lb/ac; do not apply when wet." -- must never launder the earlier mention into a non-hit just
    because a negation word appears somewhere after it. Scoped to one sentence overall (not the whole
    answer) so a negation early in a long answer still cannot launder a real recommendation stated
    later.
    """
    if not forbidden_ids:
        return False
    retrieved_by_id = {strategy.strategy_id: strategy for strategy in retrieved}
    for sentence in _sentences(_normalise_scanned_text(final_text)):
        lowered_sentence = sentence.lower()
        for forbidden_id in forbidden_ids:
            strategy = retrieved_by_id.get(forbidden_id)
            named = forbidden_id.lower() in lowered_sentence or (
                strategy is not None and _strategy_named_in_answer(strategy, sentence)
            )
            if not named:
                continue
            mention_start = _mention_position(forbidden_id, strategy, lowered_sentence)
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


def _record_percentage_magnitudes(text: str) -> frozenset[tuple[str, ...]]:
    """Every magnitude tuple `_percentage_mentions` finds in `text`, as a set for membership binding.

    `score_grounding` used to bind a PERCENTAGE mention to a record by running `_contains_token` on
    the bare magnitude digits alone, and `_contains_token`'s own alphanumeric boundary lets `.` and
    `-` border a match -- so "65%" bound to a record whose text held "0.65" (a correlation
    coefficient), "12 sites" (a count) or "12-year" (a year span), none of which is a percentage at
    all. This binds against the record's OWN percentage mentions instead -- it must actually state a
    "%"/"percent" figure with the SAME magnitude(s), not merely contain the same digits somewhere
    (wave-2 fix-stage review: the per-record form of the "anywhere in this turn's tool text" false
    pass E3 set out to remove).
    """
    return frozenset(magnitudes for _span, magnitudes in _percentage_mentions(text))


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


def _percentage_mentions(text: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Every percentage mention in `text`, as `(raw_span, magnitudes)`: one magnitude for an ordinary
    mention (`"40%"` -> `("40",)`), or two for a RANGE (`"20-30%"`/`"20 to 30 percent"` ->
    `("20", "30")`). A range is scored as ONE mention carrying its own `(min, max)` pair, never as two
    independent single-number mentions -- `_RANGE_PERCENTAGE_PATTERN` is matched first and its spans
    are excluded from the plain `_PERCENTAGE_PATTERN` pass so a range is never double-counted.
    """
    range_spans: list[tuple[int, int]] = []
    mentions: list[tuple[str, tuple[str, ...]]] = []
    for match in _RANGE_PERCENTAGE_PATTERN.finditer(text):
        range_spans.append(match.span())
        mentions.append((match.group(0), (match.group(1), match.group(2))))
    for match in _PERCENTAGE_PATTERN.finditer(text):
        if any(start <= match.start() < end for start, end in range_spans):
            continue
        mentions.append((match.group(0), (_numeric_magnitude(match.group(0)),)))
    return tuple(mentions)


#: The finding/strategy fields E3's record-bound grounding reads a record's own text from. `_CORE`
#: is tried FIRST: a finding's `magnitude`/`excerpt`/`claim` is where a number it actually reports
#: lives, whereas `conditions` is free-form prose ("Northern Great Plains, 10 years no-till") that can
#: coincidentally contain an unrelated digit run ("10") and wrongly bind a percentage to the wrong
#: record. `conditions` is only consulted as a FALLBACK, when no record's core text binds the number
#: at all (wave-2 fix-stage review). `summary`/`benefits`/`application_rate`/`risks_limitations`/
#: `actions`/`name` for a strategy has no such free-form field, so its core text is its whole text.
#: (`agent/strategy_knowledge.py::_FINDING_KEYS`/`_STRATEGY_RECORD_KEYS`, restated for the same
#: no-new-import-risk reason as `STRATEGY_TOOL_NAMES`.)
_FINDING_CORE_TEXT_KEYS: Final = ("claim", "magnitude", "excerpt")
_FINDING_TEXT_KEYS: Final = (*_FINDING_CORE_TEXT_KEYS, "conditions")
_STRATEGY_TEXT_KEYS: Final = ("name", "summary", "application_rate", "benefits", "risks_limitations", "actions")

#: A record's own direction that requires a qualifier before a bound number can be restated plainly
#: -- see `score_grounding`'s `direction_dropped`.
_QUALIFIER_REQUIRED_DIRECTIONS: Final[frozenset[str]] = frozenset({"mixed", "negative", "no_effect"})

#: A word/phrase in the SAME sentence as a bound number that shows the answer preserved a mixed or
#: negative record's nuance, rather than restating only its favourable half. Loose by design, same
#: spirit as the rest of this scorer's text heuristics.
_QUALIFIER_MARKER_PATTERN: Final = re.compile(
    r"\b(mixed|but |however|trade-?off|although|while |partial(?:ly)?|not all|no effect|did not|"
    r"offset|at the cost of|on the other hand)\b",
    re.IGNORECASE,
)

#: A literature number restated as an outcome PROMISED at the caller's own site -- e.g. "your field
#: will...", "you can expect...", "on your land you'll..." -- rather than reported as the cited
#: record's own finding. Loose phrasing match, per CONTRACT-WAVE2.md seam S5's own examples.
_SITE_PROMISE_PATTERN: Final = re.compile(
    r"\byour (?:field|farm|land|site|property|pasture|soil|crop)s?\s+will\b"
    r"|\byou (?:can|should|will)\s+expect\b"
    r"|\bon your (?:land|field|farm|site|property)\b[^.\n]{0,60}\byou(?:'ll| will)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class LiteratureRecord:
    """One finding or strategy record this run actually retrieved, reduced to its own searchable
    text -- what `score_grounding` binds a literature number against, instead of "anywhere in this
    turn's tool text" (the exact false-pass FINDINGS.md's "Scorer caveats" warns record-bound
    grounding must replace)."""

    record_id: str
    kind: str  # "finding" | "strategy"
    direction: str | None
    text: str
    core_text: str
    """`text` minus a finding's free-form `conditions`; see `_FINDING_CORE_TEXT_KEYS`. Tried first."""


def _record_text(entry: dict[str, Any], keys: Sequence[str]) -> str:
    """`entry`'s own text, from `keys` only, normalised (`_normalise_scanned_text`) for token matching."""
    parts: list[str] = []
    for key in keys:
        value = entry.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(item for item in value if isinstance(item, str))
    return _normalise_scanned_text(" ".join(parts))


def _literature_records(tool_contents: Sequence[str]) -> tuple[LiteratureRecord, ...]:
    """Every finding/strategy record named anywhere in this run's strategy-tool results, reduced to
    its own text. Duck-typed on field names (same approach as `_retrieved_strategies`): a dict
    carrying `finding_id` is a finding; a dict carrying `strategy_id` AND (`summary` or `name`) is a
    full strategy RECORD, not merely a search hit naming an id in passing.
    """
    records: list[LiteratureRecord] = []
    seen: set[str] = set()
    for content in tool_contents:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            continue
        for node in _walk_json(parsed):
            finding_id = node.get("finding_id")
            if isinstance(finding_id, str) and finding_id and finding_id not in seen:
                seen.add(finding_id)
                direction = node.get("direction")
                records.append(
                    LiteratureRecord(
                        record_id=finding_id,
                        kind="finding",
                        direction=direction if isinstance(direction, str) else None,
                        text=_record_text(node, _FINDING_TEXT_KEYS),
                        core_text=_record_text(node, _FINDING_CORE_TEXT_KEYS),
                    )
                )
                continue
            strategy_id = node.get("strategy_id")
            if (
                isinstance(strategy_id, str)
                and strategy_id
                and strategy_id not in seen
                and ("summary" in node or "name" in node)
            ):
                seen.add(strategy_id)
                strategy_text = _record_text(node, _STRATEGY_TEXT_KEYS)
                records.append(
                    LiteratureRecord(
                        record_id=strategy_id,
                        kind="strategy",
                        direction=None,
                        text=strategy_text,
                        core_text=strategy_text,  # a strategy record has no free-form conditions field
                    )
                )
    return tuple(records)


@dataclass(frozen=True, slots=True)
class GroundingReport:
    """E3's record-bound grounding verdict for one turn's final answer (CONTRACT-WAVE2.md seam S5)."""

    grounded_numbers: tuple[str, ...]
    unbound_numbers: tuple[str, ...]
    direction_dropped: tuple[str, ...]
    site_promise: tuple[str, ...]


def score_grounding(final_text: str, tool_contents: Sequence[str]) -> GroundingReport:
    """Bind every percentage the answer states to the retrieved record(s) whose OWN text contains it.

    A percentage with no bound record is `unbound_numbers` -- never "somewhere in this turn's tool
    text", which is the exact validator FINDINGS.md's "Scorer caveats" says would both false-fail a
    correctly quoted figure and pass a genuine distortion. A record binds only when IT ITSELF states a
    percentage mention with the same magnitude(s) (`_record_percentage_magnitudes`), never merely the
    same bare digits: "65%" does not bind to a record whose text holds "0.65" (a correlation
    coefficient), "12 sites" (a count) or a "12-year" span, none of which is a percentage. Binding
    tries each record's CORE text first (`LiteratureRecord.core_text`) and only falls back to its full
    text (a finding's `conditions` included) when no record's core text matches, so an unrelated digit
    run in free-form conditions text ("10 years") cannot wrongly bind a percentage away from the
    record that actually reports it.
    A number is `direction_dropped` only when EVERY record it bound to has a mixed/negative/no_effect
    direction and the SAME sentence carries no qualifier (`_QUALIFIER_MARKER_PATTERN`) -- a number also
    bound to an unqualified positive record is not flagged, since the answer had a legitimate source
    for stating it plainly. `site_promise` flags the answer phrasing a literature number as an outcome
    guaranteed AT THE CALLER'S SITE, scoped to sentences that ALSO bound a percentage this turn -- the
    phrase alone, with no literature number in the same sentence, is not a grounding defect.
    """
    normalised_final_text = _normalise_scanned_text(final_text)
    records = _literature_records(tool_contents)
    # Precomputed ONCE per record (not per mention): each record's own percentage mentions, core text
    # first. See `_record_percentage_magnitudes`.
    core_magnitudes = {record.record_id: _record_percentage_magnitudes(record.core_text) for record in records}
    full_magnitudes = {record.record_id: _record_percentage_magnitudes(record.text) for record in records}
    grounded: list[str] = []
    unbound: list[str] = []
    dropped: list[str] = []
    site_promise: list[str] = []
    for sentence in _sentences(normalised_final_text):
        sentence_bound_a_number = False
        for _span, magnitudes in _percentage_mentions(sentence):
            mention_label = f"{'-'.join(magnitudes)}%"
            core_bound = [record for record in records if magnitudes in core_magnitudes[record.record_id]]
            bound = core_bound or [record for record in records if magnitudes in full_magnitudes[record.record_id]]
            if not bound:
                unbound.append(mention_label)
                continue
            grounded.append(mention_label)
            sentence_bound_a_number = True
            if _QUALIFIER_MARKER_PATTERN.search(sentence):
                continue
            if all(record.direction in _QUALIFIER_REQUIRED_DIRECTIONS for record in bound):
                dropped.extend(
                    f"{mention_label} (record {record.record_id}: direction={record.direction})" for record in bound
                )
        if sentence_bound_a_number:
            site_promise.extend(match.group(0) for match in _SITE_PROMISE_PATTERN.finditer(sentence))
    return GroundingReport(
        grounded_numbers=tuple(grounded),
        unbound_numbers=tuple(unbound),
        direction_dropped=tuple(dropped),
        site_promise=tuple(site_promise),
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
    grounded_numbers: tuple[str, ...]
    unbound_numbers: tuple[str, ...]
    direction_dropped: tuple[str, ...]
    site_promise: tuple[str, ...]
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
    new_messages: Sequence[dict[str, Any]] | None = None,
) -> ScenarioScore:
    """Score one completed conversation against its scenario's expectations.

    PASSES when: the run actually produced a conversation (no provider error, a non-empty
    transcript); a strategy tool call SUCCEEDED and the answer itself names one of its results from
    an expected family (or, for a control scenario, no strategy tool was even attempted); no
    forbidden strategy was recommended in the answer, in any sentence not carrying its own negation;
    the answer names no id-shaped token this run could not have known about; a successful strategy
    tool call is followed by an answer that reads as literature-attributed; and every literature
    number the answer states is bound to a retrieved record (`score_grounding`), with that record's
    direction preserved and never restated as a promise at the caller's own site.

    `context_text` is everything the model was given this turn besides the transcript itself (the
    built prompt, the published tool schemas) -- passed through into the hallucination check as more
    of the "known text" a mention is allowed to have come from. It plays no part in grounding: a
    number is bound to a RETRIEVED RECORD (`score_grounding`) or it is not, never to the prompt or the
    published tool schemas, which are full of bare numbers used only as parameter examples.
    `registry_strategy_ids` is the full corpus of real strategy ids, used only to catch a genuine id
    named in plain prose that this run never retrieved (see `_hallucinated_ids`).

    `new_messages` is THIS TURN's own slice of `transcript` (the messages appended since the previous
    turn ended), for a multi-turn conversation. Tool-ACTIVITY *counts* -- how many strategy calls this
    turn made, the warehouse call count, the refusals seen -- are scored against `new_messages` alone,
    never the whole cumulative `transcript`, so a later turn's own numbers in a failure message are
    never inflated by an earlier turn's calls. `expect_strategy_tool` itself is different: a later turn
    that answers from an EARLIER turn's successful retrieval, with no new call of its own, DOES satisfy
    it -- `strategy_evidence_in_hand` looks at the whole cumulative `transcript`, not just this turn --
    because `family_hit` (also cumulative) still gates whether that reused evidence is actually the one
    the answer names; only the "no call was made" / "no call succeeded" failure reasons are waived, and
    only when a genuinely successful call sits somewhere in this conversation already (wave-2
    fix-stage review, correcting the fix-stage-EB1 draft that instead failed a legitimate reused-
    evidence follow-up turn outright). RETRIEVED RECORDS stay cumulative over the whole `transcript`
    regardless -- grounding and the hallucination check may still cite a strategy this run retrieved on
    an earlier turn, since that record is real evidence the conversation actually has in hand. Omitting
    `new_messages` (single-turn scoring, and `--rescore`'s replay of a stored single-turn result)
    scores tool activity against the whole `transcript`, unchanged from before this parameter existed.
    """
    tool_messages = [message for message in transcript if isinstance(message, dict) and message.get("role") == "tool"]
    strategy_messages = [message for message in tool_messages if message.get("name") in STRATEGY_TOOL_NAMES]
    # `_scannable_text` json-decodes each message's content BEFORE any regex ever sees it, so a
    # `json.dumps`-escaped Unicode character (a literal U+2212 minus sign, say) reads as the real
    # character it names rather than as the literal `−` six-character escape sequence -- see
    # `_scannable_text`'s docstring and FINDINGS.md "Scorer caveats".
    tool_contents = [_scannable_text(str(message.get("content", ""))) for message in tool_messages]
    strategy_contents = [_scannable_text(str(message.get("content", ""))) for message in strategy_messages]

    turn_scope = new_messages if new_messages is not None else transcript
    turn_tool_messages = [
        message for message in turn_scope if isinstance(message, dict) and message.get("role") == "tool"
    ]
    turn_strategy_messages = [message for message in turn_tool_messages if message.get("name") in STRATEGY_TOOL_NAMES]
    turn_warehouse_messages = [
        message for message in turn_tool_messages if message.get("name") not in STRATEGY_TOOL_NAMES
    ]
    turn_tool_contents = [_scannable_text(str(message.get("content", ""))) for message in turn_tool_messages]
    turn_strategy_contents = [_scannable_text(str(message.get("content", ""))) for message in turn_strategy_messages]

    refusals_seen: set[str] = set()
    for content in turn_tool_contents:
        refusals_seen |= _refusal_reasons(content)

    retrieved: list[RetrievedStrategy] = []
    for content in strategy_contents:
        retrieved.extend(_retrieved_strategies(content))

    retrieved_strategy_ids = tuple(sorted({strategy.strategy_id for strategy in retrieved}))
    retrieved_family_ids = tuple(sorted({strategy.family_id for strategy in retrieved if strategy.family_id}))

    strategy_tool_attempted_count = len(turn_strategy_messages)
    strategy_tool_succeeded_count = sum(1 for content in turn_strategy_contents if _tool_call_succeeded(content))
    strategy_tool_called = strategy_tool_attempted_count > 0
    strategy_tool_satisfied = strategy_tool_succeeded_count > 0
    # A follow-up turn that answers from evidence a PRIOR turn already retrieved, with no new call of
    # its own, is not a miss: `strategy_contents` is cumulative over the whole transcript (unlike the
    # turn-scoped counts above), so this is true the instant any turn up to and including this one
    # succeeded. Only `family_hit` below (also cumulative) still gates whether that evidence is
    # actually the one named in the answer (wave-2 fix-stage review; EB1's own handoff proposed a
    # per-turn expectation override instead, which would need a scenario-schema change out of scope
    # for this fix stage).
    strategy_evidence_in_hand = strategy_tool_satisfied or any(
        _tool_call_succeeded(content) for content in strategy_contents
    )

    retrieved_family_hit = any(family_id in scenario.expected_family_ids for family_id in retrieved_family_ids)
    family_hit = _family_hit_from_answer(scenario, retrieved, final_text)
    forbidden_hit = _forbidden_hit(scenario.forbidden_strategy_ids, retrieved, final_text)

    known_text = "\n".join([*scenario.turns, context_text, *tool_contents])
    hallucinated_ids = _hallucinated_ids(
        final_text,
        retrieved_strategy_ids,
        registry_strategy_ids=registry_strategy_ids,
        known_text=known_text,
    )
    literature_attribution = _LITERATURE_ATTRIBUTION_PATTERN.search(final_text) is not None
    grounding = score_grounding(final_text, strategy_contents)
    other_numeric_mentions = _other_numeric_mentions(final_text)

    reasons: list[str] = []
    if provider_error is not None:
        reasons.append(f"provider error: {provider_error}")
    elif not transcript:
        reasons.append("empty transcript; the conversation never produced a message")

    if scenario.expect_strategy_tool:
        if not strategy_tool_called and not strategy_evidence_in_hand:
            reasons.append("expected a strategy tool call; none was made this turn or on an earlier turn")
        elif not strategy_evidence_in_hand:
            reasons.append(
                f"expected a successful strategy tool call; {strategy_tool_attempted_count} attempted this turn, "
                "0 succeeded, and none succeeded on an earlier turn either"
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
    if grounding.unbound_numbers:
        reasons.append(
            "final answer states literature number(s) not bound to any retrieved record: "
            f"{list(grounding.unbound_numbers)}"
        )
    if grounding.direction_dropped:
        reasons.append(
            "a bound record's mixed/negative/no_effect direction was dropped with no qualifier in the "
            f"same sentence: {list(grounding.direction_dropped)}"
        )
    if grounding.site_promise:
        reasons.append(
            f"literature restated as an outcome promised at the caller's own site: {list(grounding.site_promise)}"
        )

    return ScenarioScore(
        strategy_tool_called=strategy_tool_called,
        strategy_tool_attempted_count=strategy_tool_attempted_count,
        strategy_tool_succeeded_count=strategy_tool_succeeded_count,
        warehouse_tool_call_count=len(turn_warehouse_messages),
        refusals_seen=tuple(sorted(refusals_seen)),
        retrieved_strategy_ids=retrieved_strategy_ids,
        retrieved_family_ids=retrieved_family_ids,
        retrieved_family_hit=retrieved_family_hit,
        family_hit=family_hit,
        forbidden_hit=forbidden_hit,
        hallucinated_ids=hallucinated_ids,
        literature_attribution=literature_attribution,
        grounded_numbers=grounding.grounded_numbers,
        unbound_numbers=grounding.unbound_numbers,
        direction_dropped=grounding.direction_dropped,
        site_promise=grounding.site_promise,
        other_numeric_mentions=other_numeric_mentions,
        passed=not reasons,
        reasons=tuple(reasons),
    )


@dataclass(frozen=True, slots=True)
class TurnRunResult:
    """One user turn's live outcome within a (possibly multi-turn) scenario conversation."""

    turn_index: int
    user_message: str
    final_text: str
    iterations: int
    stopped_because: str
    tool_calls: tuple[dict[str, Any], ...]
    transcript: tuple[dict[str, Any], ...]
    """The FULL conversation transcript up to and including this turn -- what the next turn, if any,
    is appended onto."""
    latency_seconds: float
    provider_error: str | None
    context_text: str
    """Everything besides the transcript the model saw this turn (prompt + published tool schemas) --
    persisted so `--rescore` can rebuild the SAME `known_text` the live run used; see
    `score_transcript`'s `context_text` docstring and `_rescore_stored_rows`."""
    score: ScenarioScore
    soil_labelling: SoilLabellingReport | None = None
    """Soil data plane criteria 1-3; recorded on every turn, never part of `score.passed`."""


@dataclass(frozen=True, slots=True)
class ScenarioRunResult:
    """One scenario's live outcome under one model and one sample: every turn plus the conversation-
    level verdict. CONTRACT-WAVE2.md seam S5: "a conversation passes only if every turn passes."
    """

    scenario: ScenarioExpectation
    model: str
    sample_index: int
    turns: tuple[TurnRunResult, ...]

    @property
    def passed(self) -> bool:
        """Every turn passed, and there was at least one -- an empty `turns` is never a vacuous pass."""
        return bool(self.turns) and all(turn.score.passed for turn in self.turns)


# --- Soil labelling (soil data plane DESIGN section 8, criteria 1-3) ---------------------

_SOIL_TERM_PATTERN: Final = re.compile(
    r"\b(?:soil ph|ph|organic carbon|soc|clay|sand|silt|bulk density|cec|cation exchange|coarse fragments|"
    r"soilgrids|topsoil)\b",
    re.IGNORECASE,
)
_SOIL_LABEL_PATTERN: Final = re.compile(r"model[- ]estimate", re.IGNORECASE)
_MEASUREMENT_CLAIM_PATTERN: Final = re.compile(r"\b(?:measured|measurement|sampled|lab[- ]tested)\b", re.IGNORECASE)
_CONTEXT_QUERY_SOURCE_PATTERN: Final = re.compile(r'"context_query_source"\s*:\s*"([a-z_]+)"')


@dataclass(frozen=True, slots=True)
class SoilLabellingReport:
    """Whether every soil number carries its model-estimate label and none is called a measurement."""

    soil_number_sentences: int
    unlabelled_sentences: tuple[str, ...]
    measurement_sentences: tuple[str, ...]
    context_query_sources: tuple[str, ...]

    @property
    def passed(self) -> bool:
        """Criteria 1 and 2: no unlabelled soil number and no SoilGrids value called measured."""
        return not self.unlabelled_sentences and not self.measurement_sentences


def score_soil_labelling(final_text: str, tool_contents: Sequence[str] = ()) -> SoilLabellingReport:
    """Scan an answer sentence by sentence for soil numbers without "model estimate" and for measured claims."""
    unlabelled: list[str] = []
    measured: list[str] = []
    soil_numbers = 0
    for sentence in _sentences(_scannable_text(final_text)):
        mentions_soil = _SOIL_TERM_PATTERN.search(sentence) is not None
        if not mentions_soil:
            continue
        lowered = sentence.lower()
        if _NUMERIC_PATTERN.search(sentence):
            soil_numbers += 1
            if _SOIL_LABEL_PATTERN.search(sentence) is None:
                unlabelled.append(sentence)
        claims = [
            match
            for match in _MEASUREMENT_CLAIM_PATTERN.finditer(lowered)
            if not _mention_is_negated(lowered, match.start())
        ]
        if claims and "soilgrids" in lowered:
            measured.append(sentence)
    found = (match.group(1) for content in tool_contents for match in _CONTEXT_QUERY_SOURCE_PATTERN.finditer(content))
    sources = tuple(dict.fromkeys(found))
    return SoilLabellingReport(
        soil_number_sentences=soil_numbers,
        unlabelled_sentences=tuple(unlabelled),
        measurement_sentences=tuple(measured),
        context_query_sources=sources,
    )


def _tool_message_contents(messages: Sequence[dict[str, Any]]) -> tuple[str, ...]:
    """Every tool-result message's text content, in order."""
    return tuple(str(message.get("content", "")) for message in messages if message.get("role") == "tool")


# --- CLI and live orchestration ------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--out", type=Path, required=True, help="Directory for the per-run JSON, summary.json and summary.md."
    )
    parser.add_argument(
        "--scenarios-file",
        type=Path,
        default=None,
        help=(
            f"Scenario JSON to load. Default: {SCENARIOS_FILE.name}; the soil data plane's base runs live in "
            f"{SITE_BRIEF_SCENARIOS_FILE.name}."
        ),
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
    parser.add_argument(
        "--model",
        dest="models",
        action="append",
        default=[],
        help=f"Model to run against; repeatable. Default: {' and '.join(DEFAULT_MODELS)}.",
    )
    parser.add_argument("--samples", type=int, default=1, help="Samples per scenario per model. Default: 1.")
    parser.add_argument(
        "--allow-over-budget",
        action="store_true",
        help=f"Run even when scenarios x models x samples exceeds {MAX_TOTAL_RUNS_WITHOUT_OVERRIDE}.",
    )
    parser.add_argument(
        "--rescore",
        type=Path,
        default=None,
        help=(
            "Offline mode: re-score stored per-scenario result files under this directory (layout "
            "under .omc/research/agent-evals-20260926/*) with the CURRENT scorer, no network, and "
            "write an old-verdict vs new-verdict table to --out. Takes precedence over --dry-run."
        ),
    )
    parser.add_argument(
        "--registry-dir",
        type=Path,
        default=None,
        help=f"Strategy-knowledge corpus directory for --dry-run/--rescore. Default: {DEFAULT_REGISTRY_DIRECTORY}",
    )
    return parser


def _build_prompt(scenario: ScenarioExpectation, site_brief_section: str = "") -> str:
    """The FIRST user turn's coordinate preamble, matching `interface/cli/agent.py::_ask`'s template.

    Duplicated rather than imported -- see the module docstring's "WHY NOT JUST CALL _ask" -- so a
    wording change to `_ask`'s template needs a matching hand-edit here. The SYSTEM message itself is
    no longer duplicated this way: both callers now build it from the one shared
    `agent.llm.agent_system_message`. A second or third turn is sent as `scenario.turns[i]` verbatim,
    with no coordinate preamble repeated -- that is how a real multi-turn conversation continues.
    """
    return (
        f"The coordinate is longitude {scenario.longitude}, latitude {scenario.latitude} "
        f"(WGS84 decimal degrees). Use the warehouse tools to answer, quote the distances they "
        f"report, and treat any typed refusal as a statement about the lane rather than as an "
        f"absence of data.\n\n{scenario.turns[0]}{site_brief_section}"
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


def _slug(model: str) -> str:
    """A filesystem-safe form of a model id, e.g. `anthropic/claude-haiku-4.5` -> `anthropic__claude-haiku-4.5`."""
    return re.sub(r"[^A-Za-z0-9._-]", "__", model)


def _budget_error(*, scenario_count: int, model_count: int, samples: int, allow_over_budget: bool) -> str | None:
    """The over-budget error `_run_live` refuses with, or None when `scenarios x models x samples` is
    within `MAX_TOTAL_RUNS_WITHOUT_OVERRIDE` (or the caller opted out with `--allow-over-budget`).

    Pulled out of `_run_live` as its own pure function so the budget arithmetic is unit-testable
    without that function's heavy deferred `agri_data_service` imports (CONTRACT-WAVE2.md seam S5).

    `samples < 1` is refused UNCONDITIONALLY, even with `--allow-over-budget`: zero or negative
    samples makes `total_runs <= 0`, which would otherwise slip under the budget ceiling, run nothing,
    and let `all(())` report a vacuous exit code 0 that reads as a passing round.
    """
    if samples < 1:
        return f"error: --samples must be at least 1 (got {samples})"
    total_runs = scenario_count * model_count * samples
    if total_runs <= MAX_TOTAL_RUNS_WITHOUT_OVERRIDE or allow_over_budget:
        return None
    return (
        f"error: {scenario_count} scenario(s) x {model_count} model(s) x {samples} sample(s) "
        f"= {total_runs} run(s), over the {MAX_TOTAL_RUNS_WITHOUT_OVERRIDE}-run budget; pass "
        f"--allow-over-budget to run it anyway"
    )


async def _run_live(scenarios: Sequence[ScenarioExpectation], args: argparse.Namespace) -> int:
    """Run every scenario, against every requested `--model` (default `DEFAULT_MODELS`), `--samples`
    times each, writing results as each conversation completes.

    Budget guard (CONTRACT-WAVE2.md seam S5): refuses to spend more than
    `MAX_TOTAL_RUNS_WITHOUT_OVERRIDE` total conversations (`scenarios x models x samples`) unless
    `--allow-over-budget` is passed, so a typo'd `--samples 50` cannot silently spend an OpenRouter
    budget the caller never intended.
    """
    from agri_data_service.agent import tools as warehouse_tools  # noqa: PLC0415 - see module docstring
    from agri_data_service.agent.graph import (  # noqa: PLC0415 - see module docstring
        AgentRequest,
        brief_literature_context,
        read_site_brief_inputs,
    )
    from agri_data_service.agent.llm import (  # noqa: PLC0415 - see module docstring
        MAX_OUTPUT_TOKENS,
        LlmProviderError,
        OpenAiCompletionsClient,
        agent_system_message,
        tool_schemas,
    )
    from agri_data_service.agent.prompts import build_site_brief_section  # noqa: PLC0415 - see module docstring
    from agri_data_service.agent.site_brief import build_site_brief  # noqa: PLC0415 - see module docstring
    from agri_data_service.agent.strategy_knowledge import StrategyContext  # noqa: PLC0415 - see module docstring

    models = tuple(args.models) if args.models else DEFAULT_MODELS
    budget_error = _budget_error(
        scenario_count=len(scenarios),
        model_count=len(models),
        samples=args.samples,
        allow_over_budget=args.allow_over_budget,
    )
    if budget_error is not None:
        print(budget_error, file=sys.stderr)
        return 1

    max_tokens = args.max_tokens if args.max_tokens is not None else MAX_OUTPUT_TOKENS
    base_client = OpenAiCompletionsClient.from_settings()

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
    # `agent ask`/`converse` gains this system message (seam S5); the eval uses the SAME builder so
    # the two never drift, unlike the hand-duplicated coordinate preamble in `_build_prompt`.
    system_message = agent_system_message(date.today())  # noqa: DTZ011 - the eval's own wall-clock date

    def literature_context(
        scenario: ScenarioExpectation, turn_index: int, brief: dict[str, Any] | None
    ) -> StrategyContext:
        """Every user turn so far; a base run's turn 0 is no typed question, so its brief seeds retrieval."""
        asked = scenario.turns[1 : turn_index + 1] if scenario.base_run else scenario.turns[: turn_index + 1]
        user_question_so_far = "\n".join(asked) or None
        if brief is None:
            return StrategyContext(
                user_question=user_question_so_far,
                longitude=scenario.longitude,
                latitude=scenario.latitude,
                site_facts=None,
            )
        request = AgentRequest(longitude=scenario.longitude, latitude=scenario.latitude, precision="exact")
        return brief_literature_context(request, brief).model_copy(update={"user_question": user_question_so_far})

    async def read_brief(scenario: ScenarioExpectation) -> dict[str, Any] | None:
        """A base run's server-built site brief, read live once per conversation."""
        if not scenario.base_run:
            return None
        request = AgentRequest(longitude=scenario.longitude, latitude=scenario.latitude, precision="exact")
        async with warehouse_tools.run_context():
            return build_site_brief(await read_site_brief_inputs(request))

    async def run_turn(
        client: OpenAiCompletionsClient,
        scenario: ScenarioExpectation,
        transcript_so_far: list[dict[str, Any]],
        turn_index: int,
        brief: dict[str, Any] | None = None,
    ) -> TurnRunResult:
        brief_section = build_site_brief_section(brief) if brief is not None else ""
        user_message = _build_prompt(scenario, brief_section) if turn_index == 0 else scenario.turns[turn_index]
        messages = [*transcript_so_far, {"role": "user", "content": user_message}]
        provider_error: str | None = None
        outcome: dict[str, Any]
        started = time.monotonic()
        try:
            # A FRESH context per user turn (seam S5): the strategy_context seeds server-side
            # retrieval with every user turn asked SO FAR, joined verbatim -- never with site_facts,
            # which this harness leaves to the server's own measured reads.
            async with warehouse_tools.run_context(strategy_context=literature_context(scenario, turn_index, brief)):
                outcome = await client.converse(messages, max_tokens=max_tokens)
        except LlmProviderError as error:
            outcome = {
                "final_text": "",
                "iterations": 0,
                "tool_calls": [],
                "transcript": messages,
                "stopped_because": "provider_error",
            }
            provider_error = str(error)
        latency_seconds = time.monotonic() - started
        final_text = str(outcome.get("final_text", ""))
        turn_transcript = list(outcome.get("transcript") or messages)
        # This turn's OWN new messages -- everything appended since `transcript_so_far`, the
        # conversation as it stood before this turn's user message went in. Scores tool-activity
        # facts against just this slice; see `score_transcript`'s `new_messages` docstring.
        new_messages = turn_transcript[len(transcript_so_far) :]
        context_text = f"{user_message}\n{tool_schema_text}"
        return TurnRunResult(
            turn_index=turn_index,
            user_message=user_message,
            final_text=final_text,
            iterations=int(outcome.get("iterations", 0)),
            stopped_because=str(outcome.get("stopped_because", "")),
            tool_calls=tuple(outcome.get("tool_calls") or ()),
            transcript=tuple(turn_transcript),
            latency_seconds=latency_seconds,
            provider_error=provider_error,
            context_text=context_text,
            score=score_transcript(
                scenario,
                final_text,
                turn_transcript,
                provider_error=provider_error,
                context_text=context_text,
                registry_strategy_ids=registry_strategy_ids,
                new_messages=new_messages,
            ),
            soil_labelling=score_soil_labelling(final_text, _tool_message_contents(new_messages)),
        )

    async def run_conversation(
        client: OpenAiCompletionsClient, scenario: ScenarioExpectation, sample_index: int
    ) -> ScenarioRunResult:
        transcript: list[dict[str, Any]] = [system_message]
        turns: list[TurnRunResult] = []
        brief = await read_brief(scenario)
        for turn_index in range(len(scenario.turns)):
            turn_result = await run_turn(client, scenario, transcript, turn_index, brief)
            turns.append(turn_result)
            transcript = list(turn_result.transcript)
        return ScenarioRunResult(
            scenario=scenario, model=client.credentials.model, sample_index=sample_index, turns=tuple(turns)
        )

    results: list[ScenarioRunResult] = []
    for model in models:
        client = dataclasses.replace(
            base_client, credentials=base_client.credentials.model_copy(update={"model": model})
        )
        for scenario in scenarios:
            for sample_index in range(args.samples):
                result = await run_conversation(client, scenario, sample_index)
                results.append(result)
                _write_scenario_result(args.out, result)
    _write_summary(args.out, results, models=models, strategy_knowledge_url=args.strategy_knowledge_url)
    return 0 if all(result.passed for result in results) else 1


def _write_scenario_result(out: Path, result: ScenarioRunResult) -> None:
    """Persist one (scenario, model, sample) conversation's every turn and its verdict, under
    `out/<model-slug>/sample-<n>/<scenario-id>.json`."""
    payload = {
        "scenario_id": result.scenario.id,
        "model": result.model,
        "sample_index": result.sample_index,
        "longitude": result.scenario.longitude,
        "latitude": result.scenario.latitude,
        "expect_strategy_tool": result.scenario.expect_strategy_tool,
        "expected_family_ids": list(result.scenario.expected_family_ids),
        "forbidden_strategy_ids": list(result.scenario.forbidden_strategy_ids),
        "passed": result.passed,
        "turns": [
            {
                "turn_index": turn.turn_index,
                "user_message": turn.user_message,
                "final_text": turn.final_text,
                "iterations": turn.iterations,
                "stopped_because": turn.stopped_because,
                "latency_seconds": turn.latency_seconds,
                "provider_error": turn.provider_error,
                "context_text": turn.context_text,
                "tool_calls": list(turn.tool_calls),
                "transcript": list(turn.transcript),
                "score": dataclasses.asdict(turn.score),
                "soil_labelling": (
                    None
                    if turn.soil_labelling is None
                    else {**dataclasses.asdict(turn.soil_labelling), "passed": turn.soil_labelling.passed}
                ),
            }
            for turn in result.turns
        ],
    }
    directory = out / _slug(result.model) / f"sample-{result.sample_index}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{result.scenario.id}.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def _write_summary(
    out: Path,
    results: Sequence[ScenarioRunResult],
    *,
    models: Sequence[str],
    strategy_knowledge_url: str,
) -> None:
    """Write `summary.json` (machine-readable) and `summary.md` (one row per turn) into `out`,
    aggregated across every model and sample this run covered."""
    passed = sum(1 for result in results if result.passed)
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "models": list(models),
        "strategy_knowledge_url": strategy_knowledge_url,
        "conversations_run": len(results),
        "conversations_passed": passed,
        "conversations_failed": len(results) - passed,
        "results": [
            {
                "scenario_id": result.scenario.id,
                "model": result.model,
                "sample_index": result.sample_index,
                "passed": result.passed,
                "turns": [{"turn_index": turn.turn_index, **dataclasses.asdict(turn.score)} for turn in result.turns],
            }
            for result in results
        ],
    }
    (out / SUMMARY_JSON_NAME).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / SUMMARY_MARKDOWN_NAME).write_text(_summary_markdown(results), encoding="utf-8")


def _summary_markdown(results: Sequence[ScenarioRunResult]) -> str:
    """One markdown table row per TURN: scenario, model, sample, turn index, and that turn's verdict."""
    lines = [
        "| scenario | model | sample | turn | family_hit | forbidden_hit | unbound_numbers | "
        "direction_dropped | site_promise | passed | reasons |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        for turn in result.turns:
            score = turn.score
            lines.append(
                f"| {result.scenario.id} | {result.model} | {result.sample_index} | {turn.turn_index} | "
                f"{score.family_hit} | {score.forbidden_hit} | {', '.join(score.unbound_numbers) or '-'} | "
                f"{len(score.direction_dropped)} | {len(score.site_promise)} | {score.passed} | "
                f"{'; '.join(score.reasons) or '-'} |"
            )
    return "\n".join(lines) + "\n"


# --- Offline rescoring (E5): re-score stored transcripts with no network --------------


def _iter_stored_scenario_results(directory: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Every stored per-scenario result file under `directory` -- old single-turn shape or new
    multi-turn shape alike. A JSON file with no `scenario_id` (a corpus file, a log, `summary.json`)
    is skipped rather than raising: a research directory mixes result files with other artifacts.
    """
    for path in sorted(directory.rglob("*.json")):
        if path.name in {SUMMARY_JSON_NAME, DRY_RUN_REPORT_NAME, RESCORE_REPORT_NAME}:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and "scenario_id" in payload:
            yield path, payload


def _stored_turns(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The old single-turn result shape (`final_text`/`transcript`/`score` at the top level, from
    before this round's multi-turn rewrite) or the new multi-turn shape (`turns: [...]`), normalised
    to a list of turn-shaped dicts so `_rescore_stored_rows` can rescore either the same way."""
    turns = payload.get("turns")
    return turns if isinstance(turns, list) else [payload]


#: A stored result from before `context_text` was persisted per turn carries no record of the
#: tool-schema boilerplate the model actually saw that turn. Re-fetching the real, current
#: `agent.llm.tool_schemas()` text is not an option here -- the module docstring's "STRATEGY_KNOWLEDGE_
#: URL MUST LAND..." rule means `--rescore` imports NOTHING from `agri_data_service`, and the CURRENT
#: schema text is not even necessarily what an OLDER stored run actually saw. This is a small,
#: hand-written snapshot of the one phrasing `_hallucinated_ids` is known to false-flag as a fabricated
#: id -- the verified case is `.omc/research/agent-evals-20260926/haiku-4.5-r4/control-max-temperature.
#: json`'s "(in YYYY-MM-DD format)" -- not a full replay of the published schemas. A file that already
#: has its own `context_text` never uses this fallback.
_LEGACY_TOOL_SCHEMA_KNOWN_TEXT: Final = "as ISO YYYY-MM-DD such as 2026-03-14, e.g. (YYYY-MM-DD format)"


def _rescore_stored_rows(
    path: Path, payload: dict[str, Any], *, registry_strategy_ids: frozenset[str]
) -> list[dict[str, Any]]:
    """Re-score every turn of one stored result with the CURRENT scorer; one row per turn.

    `turns` for the rebuilt `ScenarioExpectation` comes from each stored turn's OWN `user_message`,
    never from `payload["question"]` -- a NEW-shape multi-turn result (`_write_scenario_result`) never
    writes a top-level `question` field at all, so reading it always answered `""` and silently dropped
    every real user turn from `score_transcript`'s `known_text`, manufacturing hallucination flags out
    of ordinary tool-schema boilerplate the model was legitimately shown (wave-2 fix-stage review).
    """
    stored_turns = _stored_turns(payload)
    turn_user_messages = tuple(str(turn.get("user_message", payload.get("question", ""))) for turn in stored_turns) or (
        str(payload.get("question", "")),
    )
    scenario = ScenarioExpectation(
        id=str(payload.get("scenario_id", "")),
        longitude=float(payload.get("longitude") or 0.0),
        latitude=float(payload.get("latitude") or 0.0),
        question=turn_user_messages[0],
        turns=turn_user_messages,
        expect_strategy_tool=bool(payload.get("expect_strategy_tool", False)),
        expected_family_ids=tuple(payload.get("expected_family_ids", ())),
        forbidden_strategy_ids=tuple(payload.get("forbidden_strategy_ids", ())),
        notes="",
    )
    rows: list[dict[str, Any]] = []
    previous_transcript_length = 0
    for turn_index, turn in enumerate(stored_turns):
        old_score = turn.get("score") or {}
        stored_context_text = turn.get("context_text")
        context_text = (
            stored_context_text
            if isinstance(stored_context_text, str)
            else f"{turn_user_messages[min(turn_index, len(turn_user_messages) - 1)]}\n{_LEGACY_TOOL_SCHEMA_KNOWN_TEXT}"
        )
        turn_transcript = turn.get("transcript") or ()
        new_score = score_transcript(
            scenario,
            str(turn.get("final_text", "")),
            turn_transcript,
            provider_error=turn.get("provider_error"),
            context_text=context_text,
            registry_strategy_ids=registry_strategy_ids,
            new_messages=list(turn_transcript)[previous_transcript_length:],
        )
        previous_transcript_length = len(turn_transcript)
        old_passed = bool(old_score.get("passed"))
        rows.append(
            {
                "source": str(path),
                "scenario_id": scenario.id,
                "turn_index": turn_index,
                "old_passed": old_passed,
                "new_passed": new_score.passed,
                "old_reasons": list(old_score.get("reasons", ())),
                "new_reasons": list(new_score.reasons),
                "verdict_changed": old_passed != new_score.passed,
            }
        )
    return rows


def _rescore_markdown(rows: Sequence[dict[str, Any]]) -> str:
    """One row per rescored turn: its old verdict, its new verdict, and whether it flipped."""
    lines = [
        "| source | scenario | turn | old_passed | new_passed | changed | new_reasons |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| {row['source']} | {row['scenario_id']} | {row['turn_index']} | {row['old_passed']} | "
        f"{row['new_passed']} | {row['verdict_changed']} | {'; '.join(row['new_reasons']) or '-'} |"
        for row in rows
    )
    return "\n".join(lines) + "\n"


def _run_rescore(directory: Path, out: Path, registry_dir: Path) -> int:
    """Offline: re-score every stored per-scenario result under `directory` with the CURRENT scorer --
    no network, no live provider call -- and write an old-verdict vs new-verdict table to `out`.
    """
    try:
        _, registry_strategy_ids = load_registry_ids(registry_dir)
    except FileNotFoundError:
        registry_strategy_ids = frozenset()
    rows: list[dict[str, Any]] = []
    for path, payload in _iter_stored_scenario_results(directory):
        rows.extend(_rescore_stored_rows(path, payload, registry_strategy_ids=registry_strategy_ids))
    changed = sum(1 for row in rows if row["verdict_changed"])
    report = {
        "directory": str(directory),
        "rescored_turns": len(rows),
        "verdicts_changed": changed,
        "rows": rows,
    }
    (out / RESCORE_REPORT_NAME).write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    (out / RESCORE_MARKDOWN_NAME).write_text(_rescore_markdown(rows), encoding="utf-8")
    print(f"rescored {len(rows)} turn(s) from {directory}; {changed} verdict(s) changed")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, then rescore/dry-validate/run every scenario live.

    `--rescore` takes precedence: it is a fully offline mode and must never require
    `STRATEGY_KNOWLEDGE_URL` or import `agri_data_service` at all.
    """
    args = _build_parser().parse_args(argv)
    if args.rescore is not None:
        args.out.mkdir(parents=True, exist_ok=True)
        return _run_rescore(args.rescore, args.out, args.registry_dir or DEFAULT_REGISTRY_DIRECTORY)
    # MUST happen before any agri_data_service import; see the module docstring.
    os.environ["STRATEGY_KNOWLEDGE_URL"] = args.strategy_knowledge_url
    try:
        scenarios = load_scenarios(args.scenarios_file or SCENARIOS_FILE, only=args.scenario_ids or None)
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
