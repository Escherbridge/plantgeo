"""Append pipeline (DESIGN.md section 13): register a fetched source, import an agent's chunk plan, bootstrap.

See AGENTS.md section "Append pipeline" for the runbook these functions implement.
"""

import logging
import math
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from strategy_knowledge.config import SERVICE_ROOT
from strategy_knowledge.corpus import (
    FAMILIES_FILE,
    REGISTRY_FILE,
    CorpusStore,
    checked_source_id,
    is_valid_source_id,
    read_json,
    read_raw_lines,
    sha256_bytes,
    sha256_file,
    split_raw_lines,
    write_json,
)
from strategy_knowledge.fetch import (
    FetchedDocument,
    build_source_id,
    disambiguate,
    fetch_document,
    parse_header,
    raw_body,
    raw_file_content,
)
from strategy_knowledge.models import (
    CandidateStrategy,
    Chunk,
    ChunkPlan,
    Finding,
    FindingsFile,
    SkippedRange,
    SourceEntry,
)
from strategy_knowledge.validate import (
    LineRange,
    RawFormsLookup,
    merge_ranges,
    raw_forms_lookup,
    strategy_id_space,
    validate_source_block,
)
from strategy_knowledge.vocabulary import EVIDENCE_RANK, STATED_GOAL_STATE

logger = logging.getLogger(__name__)

BRIEFS_DIRECTORY: Final = SERVICE_ROOT / "briefs"
CHUNK_BRIEF_PATH: Final = BRIEFS_DIRECTORY / "chunk_source.md"
REGISTRY_BRIEF_PATH: Final = BRIEFS_DIRECTORY / "registry.md"
FACETS_BRIEF_PATH: Final = BRIEFS_DIRECTORY / "facets.md"
STRATEGY_IDS_FILE: Final = "strategy_ids.txt"
SLICE_ASSIGNMENTS_FILE: Final = "slice_assignments.json"
#: One chunking agent reads at most about this many raw lines (AGENTS.md "Append pipeline").
LINES_PER_CHUNKING_AGENT: Final = 1500
#: `add-source` names an appended source's agent slices S1..Sn.
AGENT_SLICE_PREFIX: Final = "S"


class RawFileMissingError(RuntimeError):
    """Raised when a registered source's raw file is absent locally: its text lives in the bucket."""


class CandidateConflictError(ValueError):
    """Raised when one slice repeats a candidate with two different `matches_existing` targets."""


@dataclass
class AddSourceOutcome:
    """What `add-source` did and what the operator runs next."""

    source_id: str
    status: str
    line_count: int
    next_steps: list[str] = field(default_factory=list)


@dataclass
class ImportOutcome:
    """Per-source result of importing one DESIGN.md section 5 document."""

    imported: dict[str, dict[str, int]] = field(default_factory=dict)
    rejected: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ImportOptions:
    """How one chunked document is imported: its slice's assignment, extra known ids, and problem tolerance.

    `assignments=None` means every block's assignment is its source's whole raw file.
    """

    assignments: Mapping[str, Sequence[Sequence[int]]] | None = None
    known_strategy_ids: frozenset[str] = field(default_factory=frozenset)
    accept_problems: bool = False


@dataclass(frozen=True, slots=True)
class BlockScope:
    """Where one imported block lands: its source and raw hash, its slice, and the lines that slice owns."""

    source_id: str
    raw_sha256: str
    slice_id: str
    assigned: Sequence[LineRange]
    whole_file: bool = False


def read_known_strategy_ids(path: Path) -> set[str]:
    """Ids from a `strategy_ids.txt` (`id | name | category` per line)."""
    return {line.split(" | ")[0].strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def resolve_slice_id(document: Mapping[str, Any], path: Path) -> str:
    """A chunked document's slice id: its own `slice_id`, else its file's stem (the CLI and bootstrap agree)."""
    return str(document.get("slice_id") or path.stem)


def _line_count(path: Path) -> int:
    return len(read_raw_lines(path))


def _blank_line_at_or_before(raw_lines: Sequence[str], line: int, floor: int) -> int | None:
    """The last blank line in `floor..line` (1-based), where a slice can end without cutting a paragraph."""
    return next((number for number in range(line, floor - 1, -1) if not raw_lines[number - 1].strip()), None)


def agent_slices(raw_lines: Sequence[str], lines_per_agent: int = LINES_PER_CHUNKING_AGENT) -> list[LineRange]:
    """Near-equal consecutive line ranges of about `lines_per_agent` lines, each ending on a nearby blank line."""
    line_count = len(raw_lines)
    count = math.ceil(line_count / lines_per_agent)
    target = math.ceil(line_count / count) if count else 0
    ranges: list[LineRange] = []
    start = 1
    for index in range(1, count + 1):
        end = line_count
        if index < count:
            boundary = start + target - 1
            end = _blank_line_at_or_before(raw_lines, boundary, start + target // 2) or boundary
        ranges.append((start, end))
        start = end + 1
    return ranges


def agent_slice_ids(slices: Sequence[LineRange]) -> list[str]:
    """S1..Sn, one per agent slice."""
    return [f"{AGENT_SLICE_PREFIX}{index}" for index in range(1, len(slices) + 1)]


def _write_work_files(store: CorpusStore, source_id: str, slices: Sequence[LineRange]) -> Path:
    """The agent step's inputs: the registry's strategy ids and a slice_assignments.json of S1..Sn."""
    work = store.work_path(source_id)
    work.mkdir(parents=True, exist_ok=True)
    lines = [f"{s.strategy_id} | {s.name} | {s.category}" for s in store.load_registry()]
    (work / STRATEGY_IDS_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    assignments = {
        slice_id: [{"source_id": source_id, "ranges": [[start, end]]}]
        for slice_id, (start, end) in zip(agent_slice_ids(slices), slices, strict=True)
    }
    write_json(work / SLICE_ASSIGNMENTS_FILE, assignments)
    return work


def next_steps(store: CorpusStore, source_id: str, slices: Sequence[LineRange]) -> list[str]:
    """The append runbook for one registered source (the briefs are the research wave's, read with this map)."""
    work = store.work_path(source_id)
    assignments_path = work / SLICE_ASSIGNMENTS_FILE
    slice_ids = agent_slice_ids(slices)
    slice_map = "; ".join(
        f"{slice_id} = lines {start}-{end} -> {work / f'{slice_id}.json'}"
        for slice_id, (start, end) in zip(slice_ids, slices, strict=True)
    )
    return [
        (
            f"1. Agent step, one agent per slice ({len(slices)}, run in parallel): chunk {store.raw_path(source_id)} "
            f"following {CHUNK_BRIEF_PATH}. Slices: {slice_map}. For each agent the brief's 'assigned line ranges' "
            f"are its slice's lines, `briefs/strategy_ids.txt` is {work / STRATEGY_IDS_FILE}, and "
            f'`chunked/<slice_id>.json` is {work}/<slice_id>.json with "slice_id" set to the slice\'s name; '
            "DESIGN.md / strategy_schema.md are restated in services/strategy-knowledge/AGENTS.md and vocabulary.py."
        ),
        *(
            f"2. strategy-kb import-chunked {work / f'{slice_id}.json'} --assignments {assignments_path}"
            for slice_id in slice_ids
        ),
        f"3. strategy-kb validate --source {source_id}",
        (
            f"4. Registry step ({REGISTRY_BRIEF_PATH}): reconcile its candidate strategies into "
            "corpus/strategies/strategy_registry.json; new strategies get facets per "
            f"{FACETS_BRIEF_PATH}."
        ),
        f"5. strategy-kb index --source {source_id}",
        "6. strategy-kb sync push",
    ]


def add_source(
    store: CorpusStore,
    url: str,
    fetcher: Callable[[str], FetchedDocument] = fetch_document,
    now: datetime | None = None,
) -> AddSourceOutcome:
    """Fetch, extract, assign a source_id, hash and register a source; unchanged re-runs are a no-op.

    A URL that is registered but whose raw file is not in the local cache is refused before any fetch: the
    stored text (and the line numbers its plans use) lives in the bucket, and re-fetching would replace it.
    """
    sources = store.load_sources()
    existing = next((entry for entry in sources.values() if entry.url == url), None)
    if existing is not None and not store.raw_path(existing.source_id).is_file():
        raise RawFileMissingError(
            f"{url} is registered as {existing.source_id} but its raw file is not in {store.root}; "
            "run `strategy-kb sync pull` first",
        )
    document = fetcher(url)
    content = raw_file_content(document)
    if existing is not None:
        raw_path = store.raw_path(existing.source_id)
        if raw_body(raw_path.read_text(encoding="utf-8")) == raw_body(content):
            line_count = _line_count(raw_path)
            return AddSourceOutcome(existing.source_id, "unchanged", line_count, [])
        source_id, status = existing.source_id, "changed"
    else:
        source_id = build_source_id(url, now or datetime.now(UTC), document.title)
        if source_id in sources:
            source_id = disambiguate(source_id, url)
        checked_source_id(source_id)
        status = "added"
    raw_path = store.raw_path(source_id)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(content, encoding="utf-8", newline="\n")
    raw_lines = read_raw_lines(raw_path)
    entry = existing or SourceEntry(source_id=source_id, url=url)
    sources[source_id] = entry.model_copy(
        update={
            "final_url": document.final_url,
            "title": entry.title or document.title,
            "sha256": sha256_file(raw_path),
            "line_count": len(raw_lines),
            "fetched_at": document.fetched_at,
            "kind": document.kind,
        },
    )
    store.save_sources(sources.values())
    slices = agent_slices(raw_lines)
    _write_work_files(store, source_id, slices)
    if status == "changed":
        logger.warning("%s changed upstream: its chunk plan and findings no longer hold and must be redone", source_id)
    return AddSourceOutcome(source_id, status, len(raw_lines), next_steps(store, source_id, slices))


def _intersects(start: int, end: int, ranges: Sequence[LineRange]) -> bool:
    return any(start <= range_end and range_start <= end for range_start, range_end in ranges)


#: Ordered unions when a slice repeats a candidate (AGENTS.md "Append pipeline", candidate folding).
MERGED_LIST_FIELDS: Final = (
    "secondary_categories",
    "fire_phase",
    "land_use",
    "region",
    "materials",
    "actions",
    "soil_conditions",
    "scale",
    "equipment",
    "benefits",
    "risks_limitations",
)
#: Single-value fields: the first copy's value wins and a later copy only fills a gap (`category` is required,
#: so the first copy's always wins).
FIRST_NON_EMPTY_FIELDS: Final = (
    "name",
    "summary",
    "timing",
    "slope_guidance",
    "cost_level",
    "labor_intensity",
    "time_to_effect",
    "nrcs_practice_code",
    "proposed_family",
)
#: Free-text fields that keep every distinct value, joined.
JOINED_TEXT_FIELDS: Final = ("application_rate", "notes")
JOIN_SEPARATOR: Final = " | "


def stronger_goals(first: Mapping[str, str], second: Mapping[str, str]) -> dict[str, str]:
    """Union of two goal maps where, per goal, `stated` beats `inferred`."""
    merged = dict(first)
    for goal, state in second.items():
        if merged.get(goal) != STATED_GOAL_STATE:
            merged[goal] = state
    return merged


def _joined_distinct(*values: str | None) -> str | None:
    """Every distinct non-empty part of already-joined texts, joined once."""
    parts = (part.strip() for value in values if value for part in value.split(JOIN_SEPARATOR))
    return JOIN_SEPARATOR.join(dict.fromkeys(part for part in parts if part)) or None


def _merged_match(first: CandidateStrategy, second: CandidateStrategy) -> str | None:
    """The one `matches_existing` two copies agree on; two different non-null targets cannot be folded."""
    if first.matches_existing and second.matches_existing and first.matches_existing != second.matches_existing:
        raise CandidateConflictError(
            f"candidate {first.strategy_id} is repeated with conflicting matches_existing "
            f"{first.matches_existing!r} and {second.matches_existing!r}; one slice must name one match",
        )
    return first.matches_existing or second.matches_existing


def merge_candidates(first: CandidateStrategy, second: CandidateStrategy) -> CandidateStrategy:
    """Fold a slice's repeated candidate for one strategy into one, never weakening what either copy says.

    Citations and list fields are ordered unions, goals keep the stronger state per goal, evidence_strength keeps
    the higher rank, single-value fields keep the first non-empty value, rates and notes keep every distinct
    text, and conflicting `matches_existing` targets raise `CandidateConflictError`.
    """
    seen = {(citation.source_id, citation.excerpt) for citation in first.sources}
    sources = list(first.sources)
    for citation in second.sources:
        if (citation.source_id, citation.excerpt) not in seen:
            seen.add((citation.source_id, citation.excerpt))
            sources.append(citation)
    updates: dict[str, Any] = {
        "sources": sources,
        "goals": stronger_goals(first.goals, second.goals),
        "evidence_strength": max(first.evidence_strength, second.evidence_strength, key=EVIDENCE_RANK.__getitem__),
        "matches_existing": _merged_match(first, second),
    }
    for field_name in MERGED_LIST_FIELDS:
        updates[field_name] = list(dict.fromkeys([*getattr(first, field_name), *getattr(second, field_name)]))
    for field_name in FIRST_NON_EMPTY_FIELDS:
        updates[field_name] = getattr(first, field_name) or getattr(second, field_name)
    for field_name in JOINED_TEXT_FIELDS:
        updates[field_name] = _joined_distinct(getattr(first, field_name), getattr(second, field_name))
    return first.model_copy(update=updates)


def fold_candidates(
    rows: Sequence[Mapping[str, Any]],
    origin_slice: str,
) -> dict[tuple[str | None, str], CandidateStrategy]:
    """One slice's candidate rows keyed by (origin slice, strategy_id), repeats folded by `merge_candidates`."""
    folded: dict[tuple[str | None, str], CandidateStrategy] = {}
    for row in rows:
        candidate = CandidateStrategy.model_validate({**row, "origin_slice": origin_slice})
        key = (origin_slice, candidate.strategy_id)
        folded[key] = merge_candidates(folded[key], candidate) if key in folded else candidate
    return folded


def merge_plan(existing: ChunkPlan | None, scope: BlockScope, block: Mapping[str, Any]) -> ChunkPlan:
    """Replace whatever the existing plan had inside the newly assigned ranges; keep the rest.

    Candidates are keyed by (origin slice, strategy_id): several slices of one source may each propose the same
    strategy, and a re-import replaces only its own slice's candidates - unless the slice owns the whole file,
    when it replaces every stored candidate of the source (a re-chunk under a new slice id leaves none behind).
    """
    if existing is not None and existing.raw_sha256 != scope.raw_sha256:
        logger.warning("%s: existing plan was made against another raw file; discarding it", scope.source_id)
        existing = None
    base = existing or ChunkPlan(source_id=scope.source_id, raw_sha256=scope.raw_sha256)
    chunks = [chunk for chunk in base.chunks if not _intersects(chunk.line_start, chunk.line_end, scope.assigned)]
    skipped = [skip for skip in base.skipped if not _intersects(skip.line_start, skip.line_end, scope.assigned)]
    kept = [] if scope.whole_file else [c for c in base.candidate_strategies if c.origin_slice != scope.slice_id]
    candidates = {(candidate.origin_slice, candidate.strategy_id): candidate for candidate in kept}
    candidates.update(fold_candidates(block.get("candidate_strategies") or [], scope.slice_id))
    return ChunkPlan(
        source_id=scope.source_id,
        raw_sha256=scope.raw_sha256,
        assigned_ranges=merge_ranges([*base.assigned_ranges, *scope.assigned]),
        chunks=[*chunks, *(Chunk.model_validate(row) for row in block.get("chunks") or [])],
        skipped=[*skipped, *(SkippedRange.model_validate(row) for row in block.get("skipped") or [])],
        candidate_strategies=list(candidates.values()),
    )


def merge_findings(existing: FindingsFile | None, scope: BlockScope, block: Mapping[str, Any]) -> FindingsFile:
    """Replace findings located inside the newly assigned ranges; keep the rest."""
    kept = []
    if existing is not None and existing.raw_sha256 == scope.raw_sha256:
        kept = [f for f in existing.findings if not _intersects(f.line_start, f.line_start, scope.assigned)]
    incoming = [Finding.model_validate(row) for row in block.get("findings") or []]
    return FindingsFile(source_id=scope.source_id, raw_sha256=scope.raw_sha256, findings=[*kept, *incoming])


def _register_source_record(
    store: CorpusStore,
    sources: dict[str, SourceEntry],
    source_id: str,
    record: Mapping[str, Any] | None,
) -> list[str]:
    """Create or complete a source's sources.json entry from a block's `source_record`."""
    raw_path = store.raw_path(source_id)
    if not raw_path.is_file():
        return [f"{source_id}: no raw file in the cache; run add-source or sync pull first"]
    existing = sources.get(source_id)
    fields = dict(record or {})
    fields.pop("source_id", None)
    try:
        incoming = SourceEntry.model_validate({"source_id": source_id, **fields})
    except ValidationError as error:
        return [f"{source_id}: source_record invalid: {error}"]
    if existing is None:
        header = parse_header(raw_path.read_text(encoding="utf-8"))
        existing = SourceEntry(source_id=source_id, url=header.get("source_url"), kind=header.get("kind"))
    completed = {
        name: value
        for name, value in incoming.model_dump().items()
        if value not in (None, [], {}) and getattr(existing, name) in (None, [], {})
    }
    sources[source_id] = existing.model_copy(
        update={**completed, "sha256": existing.sha256 or sha256_file(raw_path), "line_count": _line_count(raw_path)},
    )
    return []


def import_chunked(
    store: CorpusStore,
    document: Mapping[str, Any],
    slice_id: str,
    options: ImportOptions | None = None,
) -> ImportOutcome:
    """Validate each source block of a chunked document and split it into plan and findings files.

    `slice_id` is the RESOLVED slice id (`resolve_slice_id`): candidates are stored under it, so the caller
    decides it once, and a document without its own `slice_id` still never stores a candidate under None.
    Nothing is written for a rejected block: its source registration is staged on a copy of `sources.json`
    and committed only with the block. With `options.assignments`, a block for a source the slice was not
    assigned is rejected, and so is every assigned source the document left out (the reference validator's check).
    """
    if not slice_id:
        raise ValueError("import_chunked needs the resolved slice id (see resolve_slice_id)")
    chosen = options or ImportOptions()
    outcome = ImportOutcome()
    context = _ImportContext(
        store=store,
        sources=store.load_sources(),
        registry_rows=[strategy.model_dump(mode="json") for strategy in store.load_registry()],
        stored_matches=store.candidate_matches(),
        extra_known_strategy_ids=chosen.known_strategy_ids,
        raw_forms_for=raw_forms_lookup(store),
        slice_id=slice_id,
    )
    assignments = chosen.assignments
    blocks = list(document.get("sources") or [])
    imported_any = False
    for block in blocks:
        source_id = str(block.get("source_id"))
        if not is_valid_source_id(source_id):
            outcome.rejected[source_id] = [f"{source_id!r}: not a valid source_id (lower-case, digits, hyphens)"]
        elif assignments is not None and source_id not in assignments:
            outcome.rejected[source_id] = [f"{source_id}: not assigned to this slice"]
        else:
            ranges = assignments[source_id] if assignments is not None else None
            staged = _stage_block(context, block, ranges, accept_problems=chosen.accept_problems)
            if isinstance(staged, list):
                outcome.rejected[source_id] = staged
                continue
            context.sources[source_id] = staged.entry
            context.store.save_plan(staged.plan)
            context.store.save_findings(staged.findings)
            outcome.imported[source_id] = staged.counts
            imported_any = True
    if assignments is not None:
        present = {str(block.get("source_id")) for block in blocks}
        for missing in sorted(set(assignments) - present):
            outcome.rejected[missing] = [f"assigned source missing from output: {missing}"]
    if imported_any:
        store.save_sources(context.sources.values())
    return outcome


@dataclass
class _ImportContext:
    """What every block of one import is checked against; `sources` gains a block's entry only on success."""

    store: CorpusStore
    sources: dict[str, SourceEntry]
    registry_rows: list[dict[str, Any]]
    stored_matches: list[tuple[str, str | None]]
    extra_known_strategy_ids: frozenset[str]
    raw_forms_for: RawFormsLookup
    slice_id: str


@dataclass
class _StagedBlock:
    """A validated block, merged in memory and not yet written."""

    entry: SourceEntry
    plan: ChunkPlan
    findings: FindingsFile
    counts: dict[str, int]


def _stage_block(
    context: _ImportContext,
    block: Mapping[str, Any],
    ranges: Sequence[Sequence[int]] | None,
    *,
    accept_problems: bool,
) -> _StagedBlock | list[str]:
    """Register (on a copy of sources.json), validate and merge one block; the problems when it is rejected."""
    source_id = str(block.get("source_id"))
    staged_sources = dict(context.sources)
    problems = _register_source_record(context.store, staged_sources, source_id, block.get("source_record"))
    if problems:
        return problems
    raw_lines = context.store.raw_lines(source_id)
    whole_file_range = [(1, len(raw_lines))]
    assigned = merge_ranges(ranges if ranges is not None else whole_file_range)
    block_matches = [
        (str(row.get("strategy_id")), str(row["matches_existing"]) if row.get("matches_existing") else None)
        for row in block.get("candidate_strategies") or []
    ]
    id_space = strategy_id_space(
        context.registry_rows,
        [*context.stored_matches, *block_matches],
        context.extra_known_strategy_ids,
    )
    report = validate_source_block(dict(block), raw_lines, assigned, id_space, context.raw_forms_for)
    if report.problems and not accept_problems:
        return report.problems
    entry = staged_sources[source_id]
    whole_file = assigned == whole_file_range
    scope = BlockScope(source_id, entry.sha256 or "", context.slice_id, assigned, whole_file=whole_file)
    try:
        plan = merge_plan(context.store.load_plan(source_id), scope, block)
        findings = merge_findings(context.store.load_findings(source_id), scope, block)
    except CandidateConflictError as error:
        return [f"{source_id}: {error}"]
    except ValidationError as error:
        return [f"{source_id}: records do not parse: {error}"]
    counts = {
        "chunks": len(block.get("chunks") or []),
        "findings": len(block.get("findings") or []),
        "candidates": len(block.get("candidate_strategies") or []),
        "problems_accepted": len(report.problems),
        "warnings": len(report.warnings),
    }
    return _StagedBlock(entry=entry, plan=plan, findings=findings, counts=counts)


def read_slice_assignments(path: Path) -> dict[str, dict[str, list[list[int]]]]:
    """Every slice's `source_id -> ranges` from a `slice_assignments.json` (any slice id, not only P/C/S/W/U/D)."""
    return {
        str(slice_id): {row["source_id"]: row["ranges"] for row in rows or []}
        for slice_id, rows in read_json(path).items()
    }


def slice_assignments(path: Path, slice_id: str) -> dict[str, list[list[int]]] | None:
    """One slice's `source_id -> ranges`, or None when the file does not list the slice."""
    return read_slice_assignments(path).get(slice_id)


def slices_without_output(assignments_path: Path, chunked_directory: Path) -> list[str]:
    """Slices the assignments file lists that have no `<chunked_directory>/<slice_id>.json` yet."""
    return sorted(
        slice_id
        for slice_id in read_slice_assignments(assignments_path)
        if not (chunked_directory / f"{slice_id}.json").is_file()
    )


def _copy_if_changed(source: Path, target: Path) -> bool:
    """Copy bytes (so hashes survive) unless the target is already identical."""
    if target.is_file() and sha256_file(target) == sha256_file(source):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return True


def _source_entry_from_raw(store: CorpusStore, source_id: str, record: Mapping[str, Any] | None) -> SourceEntry:
    """A sources.json entry from an extracted SourceRecord (when present) plus the raw header and hash."""
    raw_path = store.raw_path(source_id)
    content = raw_path.read_bytes()
    header = parse_header(content.decode("utf-8"))
    base = {"source_id": source_id, "url": header.get("source_url"), "title": header.get("title")}
    try:
        entry = SourceEntry.model_validate({**base, **(record or {})})
    except ValidationError as error:
        logger.warning("%s: extracted SourceRecord invalid (%s); registering header facts only", source_id, error)
        entry = SourceEntry.model_validate(base)
    return entry.model_copy(
        update={
            "final_url": header.get("final_url") or entry.final_url,
            "fetched_at": header.get("fetched_at"),
            "kind": header.get("kind"),
            "sha256": sha256_bytes(content),
            "line_count": len(split_raw_lines(content.decode("utf-8"))),
        },
    )


def bootstrap(store: CorpusStore, research_directory: Path, *, accept_problems: bool = False) -> dict[str, Any]:
    """Seed the cache from the research directory: raw files, SourceRecords, chunked slices, the registry.

    Every `chunked/*.json` is imported whatever its slice id; with `briefs/slice_assignments.json` present, a
    slice it does not list is rejected (a missing assignment must not silently become the whole file), and
    listed slices without an output file are reported in `slices_without_output`.
    """
    all_raw = sorted((research_directory / "raw").glob("*.txt"))
    invalid_raw = [path.name for path in all_raw if not is_valid_source_id(path.stem)]
    research_raw = [path for path in all_raw if is_valid_source_id(path.stem)]
    copied = [path.stem for path in research_raw if _copy_if_changed(path, store.raw_path(path.stem))]
    records: dict[str, dict[str, Any]] = {}
    for path in sorted((research_directory / "extracted").glob("*.json")):
        for row in read_json(path).get("sources") or []:
            records.setdefault(str(row.get("source_id")), row)
    sources = store.load_sources()
    for path in research_raw:
        sources[path.stem] = _source_entry_from_raw(store, path.stem, records.get(path.stem))
    store.save_sources(sources.values())
    briefs = research_directory / "briefs"
    known_ids_path = briefs / "strategy_ids.txt"
    known_ids = frozenset(read_known_strategy_ids(known_ids_path)) if known_ids_path.is_file() else frozenset()
    assignments_path = briefs / SLICE_ASSIGNMENTS_FILE
    all_assignments = read_slice_assignments(assignments_path) if assignments_path.is_file() else None
    chunked_directory = research_directory / "chunked"
    slices: dict[str, Any] = {}
    for path in sorted(chunked_directory.glob("*.json")):
        document = read_json(path)
        slice_id = resolve_slice_id(document, path)
        if all_assignments is not None and slice_id not in all_assignments:
            slices[slice_id] = {"imported": {}, "rejected": {"*": [f"slice {slice_id} is not in {assignments_path}"]}}
            continue
        options = ImportOptions(
            assignments=all_assignments[slice_id] if all_assignments is not None else None,
            known_strategy_ids=known_ids,
            accept_problems=accept_problems,
        )
        result = import_chunked(store, document, slice_id, options)
        slices[slice_id] = {"imported": result.imported, "rejected": result.rejected}
    missing_slices = slices_without_output(assignments_path, chunked_directory) if assignments_path.is_file() else []
    if missing_slices:
        logger.warning("slices with no chunked output yet: %s", ", ".join(missing_slices))
    registry_copied = [
        relative
        for relative, name in ((REGISTRY_FILE, "strategy_registry.json"), (FAMILIES_FILE, "families.json"))
        if (research_directory / "registry" / name).is_file()
        and _copy_if_changed(research_directory / "registry" / name, store.path(relative))
    ]
    return {
        "raw_files_copied": copied,
        "raw_files_with_invalid_ids": invalid_raw,
        "sources_registered": len(sources),
        "slices": slices,
        "slices_without_output": missing_slices,
        "registry_files_copied": registry_copied,
        "corpus_version": store.corpus_version(),
    }
