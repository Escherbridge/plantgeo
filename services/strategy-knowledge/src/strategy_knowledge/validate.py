"""Corpus validation: line coverage, overlaps, closed enums, id references and verbatim excerpts.

Ported from the research directory's `verify_extractions.py` and `validate_chunked.py`; see AGENTS.md
section "Validation" for what changed in the port and why.
"""

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cache
from typing import Any, Final

from strategy_knowledge.corpus import (
    FAMILIES_FILE,
    REGISTRY_FILE,
    SOURCES_FILE,
    CorpusStore,
    build_alias_map,
    is_valid_source_id,
    read_json,
    records_from,
)
from strategy_knowledge.models import ChunkPlan
from strategy_knowledge.vocabulary import (
    AI_SYNTHESIS_SOURCE_PREFIX,
    CONTRACT_MINIMUM_EXCERPT_WORDS,
    FACETS,
    GOAL_STATES,
    GOALS,
    MAXIMUM_EXCERPT_WORDS,
    MINIMUM_EXCERPT_WORDS,
    VARIABLE_ROLES,
    VOCABULARIES,
)

MAXIMUM_CHUNK_WORDS: Final = 700
MINIMUM_CHUNK_WORDS: Final = 60
MAXIMUM_FACET_WORDS: Final = 170
SMALL_CHUNK_EXEMPT_TYPES: Final = frozenset({"noise", "front_matter", "references"})
#: Lines of context either side of a finding's range when locating its excerpt (reference: -3 / +2).
EXCERPT_WINDOW_BEFORE: Final = 3
EXCERPT_WINDOW_AFTER: Final = 2
REPORTED_LINE_SAMPLE: Final = 6
#: A number as a magnitude states it: digits with optional decimal or thousands separators ("1.5", "1,000").
NUMBER_TOKEN: Final = re.compile(r"\d+(?:[.,]\d+)*")

REQUIRED_STRATEGY_FIELDS: Final = (
    "strategy_id",
    "name",
    "summary",
    "category",
    "land_use",
    "region",
    "evidence_strength",
    "sources",
)
#: Missing only warns: a strategy grounded in a research abstract may have no operational steps to cite.
SOFT_STRATEGY_FIELDS: Final = ("actions",)
LIST_ENUM_FIELDS: Final = {
    "secondary_categories": "category",
    "fire_phase": "fire_phase",
    "land_use": "land_use",
    "region": "region",
    "soil_conditions": "soil_conditions",
    "scale": "scale",
}
SCALAR_ENUM_FIELDS: Final = {
    "category": "category",
    "cost_level": "level",
    "labor_intensity": "level",
    "evidence_strength": "evidence_strength",
}
QUOTE_AND_DASH_TABLE: Final = str.maketrans(
    {
        "\u2019": "'",
        "\u2018": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u00a0": " ",
    },
)

NormalisedForms = tuple[str, str]
RawFormsLookup = Callable[[str], NormalisedForms | None]
LineRange = tuple[int, int]


@dataclass
class ValidationReport:
    """Problems (each one fails validation), warnings (reported, never failing) and counters."""

    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: Counter[str] = field(default_factory=Counter)

    @property
    def ok(self) -> bool:
        """True when no problem was found."""
        return not self.problems

    def merge(self, other: "ValidationReport") -> None:
        """Fold another report into this one."""
        self.problems.extend(other.problems)
        self.warnings.extend(other.warnings)
        self.stats.update(other.stats)


@dataclass(frozen=True)
class StrategyIdSpace:
    """Which strategy ids `matches_existing` (existing) and `linked_strategy_ids` (linkable) may name.

    Before the registry exists: the known-id file. Once it exists: registry ids + `merged_from` aliases, and for
    links also candidate ids whose `matches_existing` resolved into the registry (AGENTS.md "Validation"). A
    block's own candidates are linkable while it awaits the registry step (at import, or with no registry yet).
    """

    existing: frozenset[str]
    linkable: frozenset[str]
    registry_present: bool
    block_candidates_linkable: bool = True

    @classmethod
    def of_known(cls, known_strategy_ids: Iterable[str]) -> "StrategyIdSpace":
        """A space from a plain known-id list (a `strategy_ids.txt`), as before any registry exists."""
        known = frozenset(known_strategy_ids)
        return cls(existing=known, linkable=known, registry_present=False)


def strategy_id_space(
    registry_rows: Sequence[Mapping[str, Any]],
    candidate_matches: Iterable[tuple[str, str | None]] = (),
    extra_known_strategy_ids: Iterable[str] = (),
) -> StrategyIdSpace:
    """The id space a block or the stored corpus is checked against."""
    if not registry_rows:
        return StrategyIdSpace.of_known(extra_known_strategy_ids)
    merged = {
        str(row.get("strategy_id")): [str(alias) for alias in row.get("merged_from") or []] for row in registry_rows
    }
    aliases = build_alias_map(merged, candidate_matches)
    existing = frozenset(merged) | frozenset(alias for absorbed in merged.values() for alias in absorbed)
    return StrategyIdSpace(existing=existing, linkable=existing | frozenset(aliases), registry_present=True)


def normalise(text: str, *, rejoin_hyphens: bool) -> str:
    """Lower-case, unify quotes/dashes, collapse whitespace; a hyphen at a line wrap is dropped or kept."""
    text = re.sub(r"-\s*\n\s*", "" if rejoin_hyphens else "-", text)
    text = text.translate(QUOTE_AND_DASH_TABLE)
    if not rejoin_hyphens:
        text = re.sub(r"-\s+", "-", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def normalised_forms(text: str) -> NormalisedForms:
    """Both normalisations of a text: line-wrap hyphens rejoined, and kept."""
    return normalise(text, rejoin_hyphens=True), normalise(text, rejoin_hyphens=False)


def excerpt_found(excerpt: str, forms: NormalisedForms) -> bool:
    """A PDF line-wrap hyphen may be a word break ('signifi-cant') or a real hyphen ('tow- behind')."""
    if not excerpt.strip():
        return False
    return any(normalise(excerpt, rejoin_hyphens=mode) in forms[index] for index, mode in enumerate((True, False)))


def finding_window(raw_lines: Sequence[str], line_start: int, line_end: int) -> str:
    """The raw text of a finding's claimed lines plus a few lines of context either side."""
    return "\n".join(raw_lines[max(0, line_start - EXCERPT_WINDOW_BEFORE) : line_end + EXCERPT_WINDOW_AFTER])


def excerpt_in_lines(excerpt: str, raw_lines: Sequence[str], line_start: int, line_end: int) -> bool:
    """Whether the excerpt occurs within a few lines of its claimed range."""
    return excerpt_found(excerpt, normalised_forms(finding_window(raw_lines, line_start, line_end)))


def magnitude_numbers_missing(magnitude: str | None, window: str) -> list[str]:
    """Number tokens of a magnitude that the raw window does not print (a magnitude's numbers are verbatim)."""
    printed = set(NUMBER_TOKEN.findall(window))
    return list(dict.fromkeys(token for token in NUMBER_TOKEN.findall(magnitude or "") if token not in printed))


def check_excerpt_length(report: ValidationReport, where: str, excerpt: str) -> bool:
    """Under 6 or over 25 words is a problem, 6-7 a warning (the contract asks 8-25); False on a problem."""
    words = len(excerpt.split())
    if words < MINIMUM_EXCERPT_WORDS or words > MAXIMUM_EXCERPT_WORDS:
        report.problems.append(
            f"{where}: excerpt {words} words (allowed {MINIMUM_EXCERPT_WORDS}-{MAXIMUM_EXCERPT_WORDS})",
        )
        return False
    if words < CONTRACT_MINIMUM_EXCERPT_WORDS:
        report.warnings.append(f"{where}: excerpt {words} words (contract asks {CONTRACT_MINIMUM_EXCERPT_WORDS}+)")
    return True


def merge_ranges(ranges: Iterable[Sequence[int]]) -> list[LineRange]:
    """Sort and coalesce inclusive line ranges that touch or overlap."""
    merged: list[list[int]] = []
    for start, end in sorted((int(pair[0]), int(pair[1])) for pair in ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def coverage_problems(
    label: str,
    assigned: Iterable[Sequence[int]],
    covered: Iterable[Sequence[int]],
    outside_label: str = "outside assignment",
) -> list[str]:
    """Every assigned line must fall in exactly one covered range: report overlaps, gaps and strays."""
    counts: Counter[int] = Counter()
    for start, end in covered:
        counts.update(range(int(start), int(end) + 1))
    assigned_lines = {line for start, end in assigned for line in range(int(start), int(end) + 1)}
    overlapping = sorted(line for line, count in counts.items() if count > 1)
    uncovered = sorted(assigned_lines - counts.keys())
    outside = sorted(counts.keys() - assigned_lines)
    return [
        f"{label}: {len(lines)} {kind} lines, first {lines[:REPORTED_LINE_SAMPLE]}"
        for kind, lines in (("overlapping", overlapping), ("uncovered", uncovered), (outside_label, outside))
        if lines
    ]


def file_coverage_problems(
    label: str,
    line_count: int,
    covered: Iterable[Sequence[int]],
    assigned_ranges: Iterable[Sequence[int]],
) -> list[str]:
    """Whole-file rule: lines 1..line_count each in exactly one chunk or skipped range; unassigned lines named."""
    problems = coverage_problems(label, [(1, line_count)], covered, outside_label="beyond the raw file")
    assigned_lines = {line for start, end in assigned_ranges for line in range(int(start), int(end) + 1)}
    unassigned = [line for line in range(1, line_count + 1) if line not in assigned_lines]
    if unassigned:
        problems.append(
            f"{label}: {len(unassigned)} lines lie in no imported slice's assignment, first "
            f"{unassigned[:REPORTED_LINE_SAMPLE]}; import the slice that owns them",
        )
    return problems


def plan_coverage_problems(plan: ChunkPlan, line_count: int) -> list[str]:
    """`file_coverage_problems` for a parsed chunk plan."""
    covered = [(chunk.line_start, chunk.line_end) for chunk in plan.chunks]
    covered.extend((skip.line_start, skip.line_end) for skip in plan.skipped)
    return file_coverage_problems(plan.source_id, line_count, covered, plan.assigned_ranges)


def check_goals(problems: list[str], where: str, goals: Any) -> None:
    """Goals must be an object of known goal -> stated|inferred."""
    if goals is None:
        return
    if not isinstance(goals, dict):
        problems.append(f"{where}: goals is not an object")
        return
    problems.extend(
        f"{where}: bad goal {goal}={state}"
        for goal, state in goals.items()
        if goal not in GOALS or state not in GOAL_STATES
    )


def check_enum(problems: list[str], where: str, field_name: str, value: Any, vocabulary: str) -> None:
    """Every value (scalar or list) must belong to the named closed vocabulary; null is allowed."""
    if value is None:
        return
    values = value if isinstance(value, list) else [value]
    allowed = VOCABULARIES[vocabulary]
    problems.extend(f"{where}: {field_name}={item!r} not in {vocabulary}" for item in values if item not in allowed)


def check_citations(
    report: ValidationReport,
    where: str,
    citations: Any,
    raw_forms_for: RawFormsLookup,
    *,
    ai_synthesis_is_error: bool = False,
) -> bool:
    """Check each citation's excerpt verbatim; return whether any primary (non-AI) citation exists.

    A registry strategy may carry an AI-synthesis citation beside a primary one (strategy_schema.md rule 3); a
    candidate may not cite it at all, as in the reference validator.
    """
    cited_primary = False
    for citation in citations or []:
        source_id = str(citation.get("source_id", ""))
        excerpt = str(citation.get("excerpt", ""))
        if source_id.startswith(AI_SYNTHESIS_SOURCE_PREFIX):
            if ai_synthesis_is_error:
                report.problems.append(f"{where}: cites the AI-synthesis source {source_id}")
            continue
        cited_primary = True
        forms = raw_forms_for(source_id)
        if forms is None:
            report.problems.append(f"{where}: cites unknown source_id {source_id}")
            continue
        if check_excerpt_length(report, f"{where} citation of {source_id}", excerpt) and not excerpt_found(
            excerpt,
            forms,
        ):
            report.problems.append(f"{where}: excerpt NOT FOUND in {source_id}: {excerpt[:90]!r}")
    return cited_primary


@dataclass(frozen=True)
class BlockContext:
    """What validating one source block needs besides the block itself."""

    source_id: str
    raw_lines: Sequence[str]
    line_count: int
    known_strategy_ids: frozenset[str]
    linkable_strategy_ids: frozenset[str]
    finding_ids: frozenset[str]
    raw_forms_for: RawFormsLookup


def _is_line_range(start: Any, end: Any) -> bool:
    """Whether a chunk's bounds form a usable 1-based inclusive range."""
    return isinstance(start, int) and isinstance(end, int) and 1 <= start <= end


def covered_ranges(block: Mapping[str, Any]) -> list[LineRange]:
    """Line ranges of a block's well-formed chunks plus its skipped ranges."""
    covered = [
        (chunk["line_start"], chunk["line_end"])
        for chunk in block.get("chunks") or []
        if _is_line_range(chunk.get("line_start"), chunk.get("line_end"))
    ]
    covered.extend((int(skip["line_start"]), int(skip["line_end"])) for skip in block.get("skipped") or [])
    return covered


def _check_chunk(report: ValidationReport, chunk: dict[str, Any], context: BlockContext) -> None:
    """Validate one chunk: id, range, enums, goals and id references."""
    problems = report.problems
    where = str(chunk.get("chunk_id", "?"))
    start, end = chunk.get("line_start"), chunk.get("line_end")
    if chunk.get("chunk_id") != f"{context.source_id}#L{start}-{end}":
        problems.append(f"{where}: chunk_id does not match {context.source_id}#L{start}-{end}")
    if not _is_line_range(start, end):
        problems.append(f"{where}: bad line range")
        return
    if end > context.line_count:
        problems.append(f"{where}: line_end {end} beyond the raw file's {context.line_count} lines")
    words = len(" ".join(context.raw_lines[start - 1 : end]).split())
    report.stats["chunks"] += 1
    report.stats["chunk_words"] += words
    content_type = chunk.get("content_type")
    if words > MAXIMUM_CHUNK_WORDS:
        report.stats["chunks_over_700_words"] += 1
    if words < MINIMUM_CHUNK_WORDS and content_type not in SMALL_CHUNK_EXEMPT_TYPES:
        report.stats["chunks_under_60_words"] += 1
    check_enum(problems, where, "content_type", content_type, "content_type")
    check_enum(problems, where, "relevance", chunk.get("relevance"), "relevance")
    if content_type is None or chunk.get("relevance") is None:
        problems.append(f"{where}: content_type and relevance are required")
    report.stats[f"content_type:{content_type}"] += 1
    check_goals(problems, where, chunk.get("goals", {}))
    for field_name in ("land_use", "region", "soil_conditions"):
        check_enum(problems, where, field_name, chunk.get(field_name) or [], field_name)
    problems.extend(
        f"{where}: unknown linked strategy {strategy_id}"
        for strategy_id in chunk.get("linked_strategy_ids") or []
        if strategy_id not in context.linkable_strategy_ids
    )
    problems.extend(
        f"{where}: unknown linked finding {finding_id}"
        for finding_id in chunk.get("linked_finding_ids") or []
        if finding_id not in context.finding_ids
    )


def _check_finding(report: ValidationReport, finding: dict[str, Any], context: BlockContext) -> None:
    """Validate one finding, including its verbatim excerpt at its claimed lines."""
    problems = report.problems
    where = str(finding.get("finding_id", "?"))
    report.stats["findings"] += 1
    if not where.startswith(f"{context.source_id}#F"):
        problems.append(f"{where}: finding_id prefix")
    for field_name in ("study_type", "direction", "evidence_strength"):
        if finding.get(field_name) not in VOCABULARIES[field_name]:
            problems.append(f"{where}: {field_name} {finding.get(field_name)!r}")
    check_goals(problems, where, finding.get("goals", {}))
    for field_name in ("land_use", "region"):
        check_enum(problems, where, field_name, finding.get(field_name) or [], field_name)
    for variable in finding.get("variables") or []:
        if variable.get("role") not in VARIABLE_ROLES:
            problems.append(f"{where}: variable role {variable.get('role')!r}")
    excerpt = str(finding.get("excerpt", ""))
    check_excerpt_length(report, where, excerpt)
    line_start, line_end = int(finding.get("line_start") or 0), int(finding.get("line_end") or 0)
    if excerpt_in_lines(excerpt, context.raw_lines, line_start, line_end):
        report.stats["findings_verified"] += 1
    else:
        problems.append(f"{where}: excerpt not at its lines: {excerpt[:80]!r}")
    window = finding_window(context.raw_lines, line_start, line_end)
    missing_numbers = magnitude_numbers_missing(str(finding.get("magnitude") or ""), window)
    if missing_numbers:
        report.warnings.append(
            f"{where}: magnitude number(s) {missing_numbers} not printed near lines {line_start}-{line_end}; "
            "a magnitude's numbers must be verbatim from the source",
        )
    problems.extend(
        f"{where}: unknown linked strategy {strategy_id}"
        for strategy_id in finding.get("linked_strategy_ids") or []
        if strategy_id not in context.linkable_strategy_ids
    )


def _check_candidate(report: ValidationReport, candidate: dict[str, Any], context: BlockContext) -> None:
    """Validate one candidate strategy: enums, goals, the match reference and its citations."""
    problems = report.problems
    where = f"candidate {candidate.get('strategy_id')}"
    report.stats["candidates"] += 1
    report.stats["candidates_matching_existing"] += bool(candidate.get("matches_existing"))
    if candidate.get("matches_existing") and candidate["matches_existing"] not in context.known_strategy_ids:
        problems.append(f"{where}: matches_existing unknown {candidate['matches_existing']}")
    if candidate.get("category") not in VOCABULARIES["category"]:
        problems.append(f"{where}: category {candidate.get('category')!r}")
    for field_name, vocabulary in LIST_ENUM_FIELDS.items():
        check_enum(problems, where, field_name, candidate.get(field_name), vocabulary)
    check_goals(problems, where, candidate.get("goals", {}))
    if candidate.get("fire_phase") and "wildfire_resilience" not in (candidate.get("goals") or {}):
        report.stats["candidates_fire_phase_without_wildfire_goal"] += 1
    check_citations(report, where, candidate.get("sources"), context.raw_forms_for, ai_synthesis_is_error=True)


def validate_source_block(
    block: dict[str, Any],
    raw_lines: Sequence[str],
    assigned_ranges: Iterable[Sequence[int]] | None,
    strategy_ids: StrategyIdSpace | Iterable[str],
    raw_forms_for: RawFormsLookup,
) -> ValidationReport:
    """Validate one DESIGN.md section 5 source block against its raw lines and assigned ranges.

    `strategy_ids` says what `matches_existing` and links may name (a plain id list means "no registry yet").
    `assigned_ranges=None` leaves coverage to the caller (the stored-corpus check applies the whole-file rule).
    """
    report = ValidationReport()
    source_id = str(block.get("source_id"))
    space = strategy_ids if isinstance(strategy_ids, StrategyIdSpace) else StrategyIdSpace.of_known(strategy_ids)
    candidate_ids = frozenset(str(c.get("strategy_id")) for c in block.get("candidate_strategies") or [])
    findings = block.get("findings") or []
    context = BlockContext(
        source_id=source_id,
        raw_lines=raw_lines,
        line_count=len(raw_lines),
        known_strategy_ids=space.existing,
        linkable_strategy_ids=space.linkable | candidate_ids if space.block_candidates_linkable else space.linkable,
        finding_ids=frozenset(str(finding.get("finding_id")) for finding in findings),
        raw_forms_for=raw_forms_for,
    )
    for chunk in block.get("chunks") or []:
        _check_chunk(report, chunk, context)
    if assigned_ranges is not None:
        report.problems.extend(coverage_problems(source_id, assigned_ranges, covered_ranges(block)))
    report.problems.extend(_duplicate_problems(source_id, "chunk_id", block.get("chunks") or []))
    report.problems.extend(_duplicate_problems(source_id, "finding_id", findings))
    report.problems.extend(_conflicting_match_problems(source_id, block.get("candidate_strategies") or []))
    for finding in findings:
        _check_finding(report, finding, context)
    for candidate in block.get("candidate_strategies") or []:
        _check_candidate(report, candidate, context)
    return report


def _duplicate_problems(label: str, key: str, records: Iterable[dict[str, Any]]) -> list[str]:
    """Ids that occur more than once."""
    counts = Counter(str(record.get(key)) for record in records)
    return [f"{label}: duplicate {key} {identifier}" for identifier, count in counts.items() if count > 1]


def _conflicting_match_problems(label: str, candidates: Iterable[dict[str, Any]]) -> list[str]:
    """A candidate one slice repeats must name one `matches_existing` target: two cannot be folded into one."""
    targets: dict[tuple[str, str], set[str]] = {}
    for candidate in candidates:
        if candidate.get("matches_existing"):
            key = (str(candidate.get("origin_slice") or ""), str(candidate.get("strategy_id")))
            targets.setdefault(key, set()).add(str(candidate["matches_existing"]))
    return [
        f"{label}: candidate {strategy_id} is repeated with conflicting matches_existing {sorted(named)}"
        for (_origin_slice, strategy_id), named in targets.items()
        if len(named) > 1
    ]


def validate_strategy(
    record: dict[str, Any],
    raw_forms_for: RawFormsLookup,
    family_ids: frozenset[str] | None,
) -> ValidationReport:
    """Validate one registry strategy: required fields, enums, goals, facets, family and citations."""
    report = ValidationReport()
    problems = report.problems
    where = f"registry/{record.get('strategy_id')}"
    problems.extend(
        f"{where}: missing {field_name}"
        for field_name in REQUIRED_STRATEGY_FIELDS
        if record.get(field_name) in (None, "", [])
    )
    report.warnings.extend(
        f"{where}: no {field_name} (source gives no operational steps)"
        for field_name in SOFT_STRATEGY_FIELDS
        if record.get(field_name) in (None, "", [])
    )
    for field_name, vocabulary in LIST_ENUM_FIELDS.items():
        check_enum(problems, where, field_name, record.get(field_name), vocabulary)
    for field_name, vocabulary in SCALAR_ENUM_FIELDS.items():
        check_enum(problems, where, field_name, record.get(field_name), vocabulary)
    goals = record.get("goals") or {}
    check_goals(problems, where, goals)
    if record.get("fire_phase") and "wildfire_resilience" not in goals:
        problems.append(f"{where}: fire_phase set without the wildfire_resilience goal (DESIGN.md section 3)")
    for facet, text in (record.get("facets") or {}).items():
        if facet in FACETS and text and len(str(text).split()) > MAXIMUM_FACET_WORDS:
            problems.append(f"{where}: facet {facet} is {len(str(text).split())} words (>170)")
    family_id = record.get("family_id")
    if family_ids is not None and family_id and family_id not in family_ids:
        problems.append(f"{where}: family_id {family_id} not in families.json")
    if not check_citations(report, where, record.get("sources"), raw_forms_for):
        problems.append(f"{where}: no primary (non-AI) citation")
    return report


def validate_source_entry(entry: dict[str, Any]) -> list[str]:
    """Validate the id and enum fields of one `sources.json` row."""
    problems: list[str] = []
    where = f"sources/{entry.get('source_id')}"
    if not is_valid_source_id(str(entry.get("source_id", ""))):
        problems.append(f"{where}: source_id is not a valid id (lower-case letters, digits and hyphens, 3-121)")
    check_enum(problems, where, "source_type", entry.get("source_type"), "source_type")
    check_enum(problems, where, "wildfire_relevance", entry.get("wildfire_relevance"), "wildfire_relevance")
    check_enum(problems, where, "region_focus", entry.get("region_focus"), "region")
    check_goals(problems, where, entry.get("goals"))
    return problems


def raw_forms_lookup(store: CorpusStore) -> RawFormsLookup:
    """A cached `source_id -> normalised raw text forms` lookup over the store's raw files."""

    @cache
    def lookup(source_id: str) -> NormalisedForms | None:
        if not is_valid_source_id(source_id):
            return None
        path = store.raw_path(source_id)
        return normalised_forms(path.read_text(encoding="utf-8")) if path.is_file() else None

    return lookup


def validate_corpus(
    store: CorpusStore,
    source_id: str | None = None,
    extra_known_strategy_ids: Iterable[str] = (),
) -> ValidationReport:
    """Validate the stored plans, findings, sources and registry (one source, or all of them)."""
    report = ValidationReport()
    raw_forms_for = raw_forms_lookup(store)
    registry_rows = _registry_rows(store)
    id_space = strategy_id_space(registry_rows, store.candidate_matches(), extra_known_strategy_ids)
    sources_path = store.path(SOURCES_FILE)
    source_rows = records_from(read_json(sources_path), "sources") if sources_path.is_file() else []
    sources = {str(row.get("source_id")): row for row in source_rows}
    selected = [source_id] if source_id else sorted(set(store.plan_source_ids()) | set(sources))
    for row in source_rows:
        if source_id is None or row.get("source_id") == source_id:
            report.problems.extend(validate_source_entry(row))
    for selected_id in selected:
        if is_valid_source_id(selected_id):
            report.merge(_validate_stored_source(store, selected_id, sources, id_space, raw_forms_for))
        elif selected_id not in sources:  # an invalid id already in sources.json is reported by its entry check
            report.problems.append(f"{selected_id!r}: not a valid source_id")
    report.merge(_validate_registry(store, registry_rows, source_id, raw_forms_for))
    return report


def _validate_stored_source(
    store: CorpusStore,
    source_id: str,
    sources: Mapping[str, dict[str, Any]],
    id_space: StrategyIdSpace,
    raw_forms_for: RawFormsLookup,
) -> ValidationReport:
    """Freshness, whole-file coverage and block validation for one stored source."""
    report = ValidationReport()
    entry = sources.get(source_id)
    if entry is None:
        report.problems.append(f"{source_id}: has a chunk plan but no sources.json entry")
        return report
    freshness = store.freshness(source_id, entry.get("sha256"))
    report.problems.extend(f"{source_id}: {reason}" for reason in freshness.problems())
    if not freshness.raw_present:
        return report
    plan_path, findings_path = store.plan_path(source_id), store.findings_path(source_id)
    if not plan_path.is_file():
        report.stats["sources_awaiting_chunk_plan"] += 1
        return report
    plan = read_json(plan_path)
    block = {
        "source_id": source_id,
        "chunks": plan.get("chunks") or [],
        "skipped": plan.get("skipped") or [],
        "candidate_strategies": plan.get("candidate_strategies") or [],
        "findings": (read_json(findings_path).get("findings") or []) if findings_path.is_file() else [],
    }
    raw_lines = store.raw_lines(source_id)
    # Stored candidates were meant for the registry step; once a registry exists a link must resolve into it.
    stored_space = replace(id_space, block_candidates_linkable=not id_space.registry_present)
    report.merge(validate_source_block(block, raw_lines, None, stored_space, raw_forms_for))
    report.problems.extend(
        file_coverage_problems(
            source_id,
            len(raw_lines),
            covered_ranges(block),
            plan.get("assigned_ranges") or [],
        ),
    )
    return report


def _registry_rows(store: CorpusStore) -> list[dict[str, Any]]:
    """Registry strategies as raw dicts, so invalid values are reported instead of raised."""
    path = store.path(REGISTRY_FILE)
    return records_from(read_json(path), "strategies") if path.is_file() else []


def _validate_registry(
    store: CorpusStore,
    rows: list[dict[str, Any]],
    source_id: str | None,
    raw_forms_for: RawFormsLookup,
) -> ValidationReport:
    """Validate registry strategies (only those citing `source_id` when given)."""
    report = ValidationReport()
    families_path = store.path(FAMILIES_FILE)
    family_ids = (
        frozenset(str(row.get("family_id")) for row in records_from(read_json(families_path), "families"))
        if families_path.is_file()
        else None
    )
    report.problems.extend(_duplicate_problems("registry", "strategy_id", rows))
    for row in rows:
        cited = {str(citation.get("source_id")) for citation in row.get("sources") or []}
        if source_id is not None and source_id not in cited:
            continue
        report.stats["registry_strategies"] += 1
        report.merge(validate_strategy(row, raw_forms_for, family_ids))
    return report


def summarise(report: ValidationReport) -> str:
    """One status line per report, then every problem and every warning."""
    stats = report.stats
    mean_words = stats["chunk_words"] // max(1, stats["chunks"])
    lines = [
        (
            f"chunks {stats['chunks']} (mean {mean_words} words, >700: {stats['chunks_over_700_words']}, "
            f"<60: {stats['chunks_under_60_words']}); findings {stats['findings_verified']}/{stats['findings']} "
            f"verified; candidates {stats['candidates']} ({stats['candidates_matching_existing']} match existing); "
            f"registry strategies checked {stats['registry_strategies']}; {len(report.problems)} problems, "
            f"{len(report.warnings)} warnings"
        ),
    ]
    lines.extend(f"  - {problem}" for problem in report.problems)
    lines.extend(f"  ~ {warning}" for warning in report.warnings)
    return "\n".join(lines)
