"""The local cache directory: a mirror of the bucket prefix of DESIGN.md section 12, plus derived state."""

import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel

from strategy_knowledge.models import (
    ChunkPlan,
    Family,
    FindingsFile,
    RegistryStrategy,
    SourceEntry,
    dump_record,
)
from strategy_knowledge.vocabulary import SCHEMA_VERSION

logger = logging.getLogger(__name__)

RAW_DIRECTORY: Final = "raw"
CORPUS_DIRECTORY: Final = "corpus"
SOURCES_FILE: Final = "corpus/sources.json"
CHUNK_PLANS_DIRECTORY: Final = "corpus/chunk_plans"
FINDINGS_DIRECTORY: Final = "corpus/findings"
STRATEGIES_DIRECTORY: Final = "corpus/strategies"
REGISTRY_FILE: Final = "corpus/strategies/strategy_registry.json"
FAMILIES_FILE: Final = "corpus/strategies/families.json"
INDEX_DIRECTORY: Final = "index"
#: Local-only directories and files, never mirrored to the bucket.
CHROMA_DIRECTORY: Final = "chroma"
MATERIALIZED_DIRECTORY: Final = "materialized"
WORK_DIRECTORY: Final = "work"
SYNC_MANIFEST_FILE: Final = "sync_manifest.json"
#: DESIGN.md section 2 source ids (the existing 25 stems and `<yyyymmdd>-<host>-<slug>`); it doubles as a
#: path-safety check, because every id becomes a file name under the cache.
SOURCE_ID_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9-]{2,120}$")


class CorpusFileError(ValueError):
    """Raised when a corpus file exists but cannot be parsed into its model."""


class InvalidSourceIdError(ValueError):
    """Raised when a source id does not match `SOURCE_ID_PATTERN` (and so cannot name a file safely)."""


def is_valid_source_id(source_id: str) -> bool:
    """Whether a source id matches `SOURCE_ID_PATTERN`."""
    return bool(SOURCE_ID_PATTERN.fullmatch(source_id))


def checked_source_id(source_id: str) -> str:
    """The source id itself, or `InvalidSourceIdError` when it could not name a file safely."""
    if not is_valid_source_id(source_id):
        raise InvalidSourceIdError(f"source_id {source_id!r} does not match {SOURCE_ID_PATTERN.pattern}")
    return source_id


def build_alias_map(
    merged_from: Mapping[str, Iterable[str]],
    candidate_matches: Iterable[tuple[str, str | None]],
) -> dict[str, str]:
    """Superseded strategy id -> canonical registry id (AGENTS.md section "Strategy aliases").

    `merged_from` (registry id -> ids it absorbed) comes first; then a chunking candidate whose `matches_existing`
    names a registry id or one of its aliases becomes an alias of that registry id, unless it is canonical itself.
    """
    canonical = set(merged_from)
    aliases = {alias: owner for owner, absorbed in merged_from.items() for alias in absorbed if alias not in canonical}
    for candidate_id, matched in candidate_matches:
        if not matched or candidate_id in canonical or candidate_id in aliases:
            continue
        target = matched if matched in canonical else aliases.get(matched)
        if target is not None:
            aliases[candidate_id] = target
    return aliases


def alias_groups(aliases: Mapping[str, str]) -> dict[str, list[str]]:
    """Canonical id -> every alias that resolves to it (the inverse of `build_alias_map`)."""
    groups: dict[str, list[str]] = {}
    for alias, canonical in sorted(aliases.items()):
        groups.setdefault(canonical, []).append(alias)
    return groups


def sha256_bytes(payload: bytes) -> str:
    """Hex SHA-256 of a byte string."""
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file's bytes, read in 1 MiB blocks."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def split_raw_lines(text: str) -> list[str]:
    """`str.splitlines()`: index `n - 1` is the 1-based line `n` the Read tool prints (AGENTS.md "Corpus layout").

    Never `split("\\n")`: for a file ending in a newline that yields a phantom empty last line, and coverage would
    then report line N + 1 as uncovered.
    """
    return text.splitlines()


def read_raw_lines(path: Path) -> list[str]:
    """A raw file's lines, numbered as the Read tool numbers them; `len()` of the result is its line count."""
    return split_raw_lines(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    """Write indented UTF-8 JSON with LF endings, so a file's hash is the same on every platform."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def read_json(path: Path) -> Any:
    """Read a UTF-8 JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def records_from(payload: Any, key: str) -> list[dict[str, Any]]:
    """Accept either a bare list or an object wrapping the list under `key`."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get(key), list):
        return payload[key]
    raise CorpusFileError(f"expected a list or an object with a {key!r} list")


@dataclass(frozen=True, slots=True)
class SourceFreshness:
    """Whether a source's raw file and plans still agree with `sources.json` (DESIGN.md section 12)."""

    source_id: str
    raw_present: bool
    raw_matches_registry: bool
    plan_matches_raw: bool
    findings_match_raw: bool

    @property
    def usable(self) -> bool:
        """True when line numbers in the plan and findings still hold for the raw file."""
        return self.raw_present and self.raw_matches_registry and self.plan_matches_raw and self.findings_match_raw

    def problems(self) -> list[str]:
        """Human-readable reasons the source is not usable."""
        reasons = []
        if not self.raw_present:
            reasons.append("raw file missing")
        elif not self.raw_matches_registry:
            reasons.append("raw file sha256 differs from sources.json")
        if not self.plan_matches_raw:
            reasons.append("chunk plan was made against a different raw file; re-chunk this source")
        if not self.findings_match_raw:
            reasons.append("findings were made against a different raw file; re-chunk this source")
        return reasons


class CorpusStore:
    """Read and write the corpus files under one cache directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, relative: str) -> Path:
        """Absolute path of a prefix-relative corpus path."""
        return self.root / relative

    def raw_path(self, source_id: str) -> Path:
        """Where a source's verbatim extracted text lives."""
        return self.root / RAW_DIRECTORY / f"{checked_source_id(source_id)}.txt"

    def plan_path(self, source_id: str) -> Path:
        """Where a source's chunk plan lives."""
        return self.root / CHUNK_PLANS_DIRECTORY / f"{checked_source_id(source_id)}.json"

    def findings_path(self, source_id: str) -> Path:
        """Where a source's findings live."""
        return self.root / FINDINGS_DIRECTORY / f"{checked_source_id(source_id)}.json"

    @property
    def sync_manifest_path(self) -> Path:
        """The local record of what each synced key looked like at the last pull or push."""
        return self.root / SYNC_MANIFEST_FILE

    @property
    def chroma_path(self) -> Path:
        """The local persistent Chroma directory."""
        return self.root / CHROMA_DIRECTORY

    @property
    def materialized_path(self) -> Path:
        """Where `materialize` writes passage windows for inspection."""
        return self.root / MATERIALIZED_DIRECTORY

    def work_path(self, source_id: str) -> Path:
        """Scratch directory of one appended source's agent step."""
        return self.root / WORK_DIRECTORY / checked_source_id(source_id)

    def load_sources(self) -> dict[str, SourceEntry]:
        """Every registered source, by source_id; empty when `sources.json` does not exist yet."""
        path = self.path(SOURCES_FILE)
        if not path.is_file():
            return {}
        entries = [_parse(SourceEntry, row, path) for row in records_from(read_json(path), "sources")]
        return {entry.source_id: entry for entry in entries}

    def save_sources(self, sources: Iterable[SourceEntry]) -> None:
        """Write `sources.json`, sorted by source_id so the file hash is order-independent."""
        rows = [dump_record(entry) for entry in sorted(sources, key=lambda entry: entry.source_id)]
        write_json(self.path(SOURCES_FILE), {"schema_version": SCHEMA_VERSION, "sources": rows})

    def plan_source_ids(self) -> list[str]:
        """Source ids that have a chunk plan; a plan file whose name is no valid source id is logged and left out."""
        stems = sorted(path.stem for path in (self.root / CHUNK_PLANS_DIRECTORY).glob("*.json"))
        for invalid in (stem for stem in stems if not is_valid_source_id(stem)):
            logger.warning("chunk plan %r ignored: not a valid source_id", invalid)
        return [stem for stem in stems if is_valid_source_id(stem)]

    def candidate_matches(self) -> list[tuple[str, str | None]]:
        """(candidate id, matches_existing) of every stored plan's candidate strategies, read without models."""
        pairs: list[tuple[str, str | None]] = []
        for source_id in self.plan_source_ids():
            payload = read_json(self.plan_path(source_id))
            rows = payload.get("candidate_strategies") if isinstance(payload, dict) else None
            for row in rows or []:
                if isinstance(row, dict) and row.get("strategy_id"):
                    matched = row.get("matches_existing")
                    pairs.append((str(row["strategy_id"]), str(matched) if matched else None))
        return pairs

    def strategy_aliases(self, strategies: Iterable[RegistryStrategy] | None = None) -> dict[str, str]:
        """Superseded id -> canonical id over the registry's merges and the stored candidates' matches."""
        registry = self.load_registry() if strategies is None else list(strategies)
        merged = {strategy.strategy_id: list(strategy.merged_from) for strategy in registry}
        return build_alias_map(merged, self.candidate_matches())

    def load_plan(self, source_id: str) -> ChunkPlan | None:
        """A source's chunk plan, or None."""
        path = self.plan_path(source_id)
        return _parse(ChunkPlan, read_json(path), path) if path.is_file() else None

    def save_plan(self, plan: ChunkPlan) -> None:
        """Write a chunk plan, chunks sorted by line."""
        plan.chunks.sort(key=lambda chunk: chunk.line_start)
        plan.skipped.sort(key=lambda skipped: skipped.line_start)
        write_json(self.plan_path(plan.source_id), dump_record(plan))

    def load_findings(self, source_id: str) -> FindingsFile | None:
        """A source's findings, or None."""
        path = self.findings_path(source_id)
        return _parse(FindingsFile, read_json(path), path) if path.is_file() else None

    def save_findings(self, findings: FindingsFile) -> None:
        """Write a source's findings, sorted by line."""
        findings.findings.sort(key=lambda finding: (finding.line_start, finding.finding_id))
        write_json(self.findings_path(findings.source_id), dump_record(findings))

    def load_registry(self) -> list[RegistryStrategy]:
        """Canonical strategies; empty (with a warning) until the reconcile step has written them."""
        path = self.path(REGISTRY_FILE)
        if not path.is_file():
            logger.warning("strategy registry absent at %s; strategy tools will return nothing", path)
            return []
        return [_parse(RegistryStrategy, row, path) for row in records_from(read_json(path), "strategies")]

    def load_families(self) -> list[Family]:
        """Registry families; empty when `families.json` does not exist yet."""
        path = self.path(FAMILIES_FILE)
        if not path.is_file():
            return []
        return [_parse(Family, row, path) for row in records_from(read_json(path), "families")]

    def raw_lines(self, source_id: str) -> list[str]:
        """A source's raw text as 1-based-addressable lines."""
        return read_raw_lines(self.raw_path(source_id))

    def freshness(self, source_id: str, registered_sha256: str | None) -> SourceFreshness:
        """Compare the raw file, its sources.json hash and the hashes the plan and findings were made against."""
        raw_path = self.raw_path(source_id)
        raw_present = raw_path.is_file()
        raw_hash = sha256_file(raw_path) if raw_present else None
        plan_hash = _recorded_raw_hash(self.plan_path(source_id))
        findings_hash = _recorded_raw_hash(self.findings_path(source_id))
        return SourceFreshness(
            source_id=source_id,
            raw_present=raw_present,
            raw_matches_registry=raw_hash is not None and raw_hash == registered_sha256,
            plan_matches_raw=plan_hash is _ABSENT or plan_hash == registered_sha256,
            findings_match_raw=findings_hash is _ABSENT or findings_hash == registered_sha256,
        )

    def versioned_files(self) -> list[Path]:
        """The files `corpus_version` covers: sources.json and every plan, findings and registry file."""
        files = [self.path(SOURCES_FILE)]
        for directory in (CHUNK_PLANS_DIRECTORY, FINDINGS_DIRECTORY, STRATEGIES_DIRECTORY):
            files.extend(sorted((self.root / directory).glob("*.json")))
        return [path for path in files if path.is_file()]

    def corpus_version(self) -> str:
        """SHA-256 over each covered file's relative path and content hash, in sorted path order."""
        digest = hashlib.sha256()
        for path in sorted(self.versioned_files(), key=lambda path: path.relative_to(self.root).as_posix()):
            relative = path.relative_to(self.root).as_posix()
            digest.update(f"{relative}\0{sha256_file(path)}\n".encode())
        return digest.hexdigest()


_ABSENT: Final = object()


def _recorded_raw_hash(path: Path) -> object:
    """The `raw_sha256` a plan or findings file was made against, or `_ABSENT` when there is no file."""
    if not path.is_file():
        return _ABSENT
    payload = read_json(path)
    return payload.get("raw_sha256") if isinstance(payload, dict) else None


def _parse[ModelT: BaseModel](model: type[ModelT], payload: Any, path: Path) -> ModelT:
    """Validate one record, naming the file (and record id when present) on failure."""
    try:
        return model.model_validate(payload)
    except ValueError as error:
        identifier = (payload.get("strategy_id") or payload.get("source_id")) if isinstance(payload, dict) else None
        raise CorpusFileError(f"{path}: record {identifier!r} is invalid: {error}") from error
