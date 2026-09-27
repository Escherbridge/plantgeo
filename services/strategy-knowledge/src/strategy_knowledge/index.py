"""Build and incrementally update the three Chroma collections of DESIGN.md section 8.

Every Chroma call here was checked against the installed chromadb 1.5.9 source; see AGENTS.md section
"Chroma" for the verified API and why the service passes its own vectors, and section "Indexing" for the
preflight, the stale-source rule and the full/partial version stamp.
"""

import ctypes
import logging
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection
from chromadb.config import Settings as ChromaSettings

from strategy_knowledge.corpus import CorpusStore
from strategy_knowledge.embedding import TextEmbedder
from strategy_knowledge.materialize import materialize_plan, source_usability
from strategy_knowledge.metadata import (
    RECORD_HASH_KEY,
    Metadata,
    facet_document,
    finding_document,
    finding_metadata,
    passage_document,
    passage_metadata,
    sealed,
    strategy_facet_metadata,
)
from strategy_knowledge.models import Family, FindingsFile, RegistryStrategy
from strategy_knowledge.validate import plan_coverage_problems
from strategy_knowledge.vocabulary import FACETS, SCHEMA_VERSION

if sys.platform != "win32":
    import resource

logger = logging.getLogger(__name__)

STRATEGY_FACETS: Final = "strategy_facets"
FINDINGS: Final = "findings"
PASSAGES: Final = "passages"
COLLECTION_NAMES: Final = (STRATEGY_FACETS, FINDINGS, PASSAGES)
#: Chroma 1.x collection configuration for cosine distance (the pre-1.0 form was metadata {"hnsw:space": ...}).
COSINE_CONFIGURATION: Final = {"hnsw": {"space": "cosine"}}
MAXIMUM_ADD_BATCH: Final = 500
#: The key each collection's incremental update groups records by.
GROUP_KEYS: Final = {STRATEGY_FACETS: "strategy_id", FINDINGS: "source_id", PASSAGES: "source_id"}
SUMMARY_FALLBACK_FACET: Final = "overview"
#: Collection metadata keys; `corpus_version` is only stamped by a full update, `partial_since` marks the first
#: incremental update after it ("" = none), so `index_is_stale` stays true until the next full update.
CORPUS_VERSION_METADATA: Final = "corpus_version"
PARTIAL_SINCE_METADATA: Final = "partial_since"
NEVER_FULLY_BUILT: Final = ""
#: Chroma sizes its HNSW index pool as file-handle limit // 5, split over 64 shards (AGENTS.md "Embedding and Chroma").
HANDLES_PER_HNSW_INDEX: Final = 5
HNSW_POOL_SHARDS: Final = 64
#: Indexes one shard keeps before evicting: two sets of the three collections; Windows' CRT allows at most 6.
HNSW_POOL_SLOTS_PER_SHARD: Final = 6


class IndexMismatchError(RuntimeError):
    """Raised when an index was built with another embedding model or schema than this service expects."""


class IndexMissingError(RuntimeError):
    """Raised when a collection the server needs does not exist yet."""


class IncompleteCoverageError(RuntimeError):
    """Raised before any index change when a source's chunks and skips do not cover its whole raw file."""


@dataclass(frozen=True, slots=True)
class IndexRecord:
    """One Chroma document: id, text to embed, scalar metadata."""

    identifier: str
    document: str
    metadata: Metadata


@dataclass
class IndexSelection:
    """What one index run will touch, decided before any collection changes."""

    source_id: str | None
    usable: list[str]
    excluded: dict[str, list[str]]
    partial_coverage: dict[str, list[str]] = field(default_factory=dict)

    @property
    def full(self) -> bool:
        """True for an all-sources run that left no planned source out: only then is corpus_version stamped."""
        return self.source_id is None and not self.excluded


@dataclass(frozen=True, slots=True)
class IndexStamp:
    """What a local index's collections say about the corpus they were built from (AGENTS.md "Indexing")."""

    corpus_version: str
    partial_since: str
    missing_collections: tuple[str, ...]

    def stale_against(self, corpus_version: str) -> list[str]:
        """Why this index does not reflect `corpus_version` exactly; empty when it does."""
        reasons = []
        if self.missing_collections:
            reasons.append(f"collections {list(self.missing_collections)} are missing")
        if self.partial_since:
            reasons.append(f"partial index updates since {self.partial_since}; run a full `strategy-kb index`")
        if self.corpus_version != corpus_version:
            built_from = self.corpus_version or "(never fully built, or its collections disagree)"
            reasons.append(f"the index was built from corpus {built_from}, the local corpus is {corpus_version}")
        return reasons


def widen_hnsw_pool() -> None:
    """Raise (or make finite) the file-handle limit Chroma sizes its HNSW pool from, so no shard evicts a live index.

    Chroma reads the limit when a cache path's client is first created, so this must run before that.
    See AGENTS.md "Embedding and Chroma" for the eviction that made queries fail "Nothing found on disk".
    """
    wanted = HANDLES_PER_HNSW_INDEX * HNSW_POOL_SHARDS * HNSW_POOL_SLOTS_PER_SHARD
    if sys.platform == "win32":
        runtime = ctypes.windll.msvcrt  # the C runtime whose stdio limit chromadb/api/rust.py reads
        if runtime._getmaxstdio() < wanted and runtime._setmaxstdio(wanted) < 0:
            logger.warning("could not raise the C runtime stdio limit to %d; Chroma may evict HNSW indexes", wanted)
        return
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    # An unlimited soft limit is not "enough": CPython reports it as -1 on Linux and Chroma's pool would be -1 // 5.
    if soft != resource.RLIM_INFINITY and soft >= wanted:
        return
    raised = wanted if hard == resource.RLIM_INFINITY else min(wanted, hard)
    if raised < wanted:
        logger.warning("the hard open-file limit %d is below %d; Chroma may evict HNSW indexes", hard, wanted)
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (raised, hard))
    except (ValueError, OSError) as error:
        logger.warning("could not raise the open-file limit to %d (%s); Chroma may evict HNSW indexes", raised, error)


def open_client(path: str) -> ClientAPI:
    """A persistent local Chroma client with telemetry off, created after its HNSW pool is widened."""
    widen_hnsw_pool()
    return chromadb.PersistentClient(path=path, settings=ChromaSettings(anonymized_telemetry=False))


def read_index_stamp(client: ClientAPI) -> IndexStamp:
    """The corpus_version the three collections agree on ("" when they disagree) and any partial-update mark."""
    existing = {collection.name for collection in client.list_collections()}
    versions: set[str] = set()
    partial_since = ""
    for name in COLLECTION_NAMES:
        if name in existing:
            metadata = client.get_collection(name, embedding_function=None).metadata or {}
            versions.add(str(metadata.get(CORPUS_VERSION_METADATA) or ""))
            partial_since = partial_since or str(metadata.get(PARTIAL_SINCE_METADATA) or "")
    return IndexStamp(
        corpus_version=versions.pop() if len(versions) == 1 else NEVER_FULLY_BUILT,
        partial_since=partial_since,
        missing_collections=tuple(name for name in COLLECTION_NAMES if name not in existing),
    )


def collection_metadata(embedding_model: str, corpus_version: str, partial_since: str = "") -> dict[str, str]:
    """The metadata every collection carries and the server compares on open."""
    return {
        "embedding_model": embedding_model,
        CORPUS_VERSION_METADATA: corpus_version,
        "schema_version": SCHEMA_VERSION,
        "indexed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        PARTIAL_SINCE_METADATA: partial_since,
    }


def stamped_metadata(
    previous: Mapping[str, Any] | None,
    embedding_model: str,
    corpus_version: str,
    *,
    full: bool,
) -> dict[str, str]:
    """A full update stamps the corpus version and clears `partial_since`; any other keeps the old stamp."""
    if full:
        return collection_metadata(embedding_model, corpus_version)
    found = dict(previous or {})
    return collection_metadata(
        embedding_model,
        str(found.get(CORPUS_VERSION_METADATA) or NEVER_FULLY_BUILT),
        str(found.get(PARTIAL_SINCE_METADATA) or datetime.now(UTC).isoformat(timespec="seconds")),
    )


def check_compatible(name: str, metadata: Mapping[str, Any] | None, embedding_model: str) -> None:
    """Refuse an index whose embedding model or schema differs from this service's configuration."""
    found = dict(metadata or {})
    expected = {"embedding_model": embedding_model, "schema_version": SCHEMA_VERSION}
    mismatched = {key: found.get(key) for key, value in expected.items() if found.get(key) != value}
    if mismatched:
        raise IndexMismatchError(
            f"collection {name!r} was built with {mismatched}, this service expects {expected}; "
            "rebuild with `strategy-kb index --all`",
        )


def strategy_records(
    strategies: Iterable[RegistryStrategy],
    families: Mapping[str, Family] | None = None,
) -> dict[str, list[IndexRecord]]:
    """strategy_id -> its facet documents; a strategy without authored facets indexes its summary as overview.

    Each facet's BM25 `search_terms` carries its family's name and description (AGENTS.md "Metadata").
    """
    grouped: dict[str, list[IndexRecord]] = {}
    for strategy in strategies:
        family = (families or {}).get(strategy.family_id or "")
        facets = {facet: getattr(strategy.facets, facet) for facet in FACETS if getattr(strategy.facets, facet)}
        origin = "authored"
        if not facets and strategy.summary:
            facets, origin = {SUMMARY_FALLBACK_FACET: strategy.summary}, "summary_fallback"
        records = []
        for facet, text in facets.items():
            metadata = {**strategy_facet_metadata(strategy, facet, text, family), "facet_origin": origin}
            document = facet_document(strategy.name, facet, text)
            records.append(IndexRecord(f"{strategy.strategy_id}::{facet}", document, metadata))
        grouped[strategy.strategy_id] = records
    return grouped


def _canonical(strategy_ids: Iterable[str], aliases: Mapping[str, str]) -> list[str]:
    """Linked ids with merge aliases resolved, so a scope filter by the canonical id finds them."""
    return list(dict.fromkeys(aliases.get(strategy_id, strategy_id) for strategy_id in strategy_ids))


def finding_records(findings: FindingsFile, aliases: Mapping[str, str] | None = None) -> list[IndexRecord]:
    """One document per finding."""
    resolved = [
        finding.model_copy(update={"linked_strategy_ids": _canonical(finding.linked_strategy_ids, aliases or {})})
        for finding in findings.findings
    ]
    return [
        IndexRecord(finding.finding_id, finding_document(finding), finding_metadata(finding, findings.source_id))
        for finding in resolved
    ]


def passage_records(store: CorpusStore, source_id: str, aliases: Mapping[str, str] | None = None) -> list[IndexRecord]:
    """One document per chunk window of a usable source."""
    plan = store.load_plan(source_id)
    if plan is None:
        return []
    chunks = {
        chunk.chunk_id: chunk.model_copy(
            update={"linked_strategy_ids": _canonical(chunk.linked_strategy_ids, aliases or {})},
        )
        for chunk in plan.chunks
    }
    return [
        IndexRecord(
            window.passage_id,
            passage_document(chunks[window.chunk_id].section_path, window.text),
            passage_metadata(chunks[window.chunk_id], window),
        )
        for window in materialize_plan(plan, store.raw_lines(source_id))
    ]


def _sealed_records(records: Iterable[IndexRecord], corpus_version: str) -> list[IndexRecord]:
    """Stamp version and content hash; drop duplicate ids (first wins) with a warning."""
    unique: dict[str, IndexRecord] = {}
    for record in records:
        if record.identifier in unique:
            logger.warning("duplicate index id %s dropped", record.identifier)
            continue
        unique[record.identifier] = IndexRecord(
            record.identifier,
            record.document,
            sealed(record.document, record.metadata, corpus_version),
        )
    return list(unique.values())


def coverage_gaps(store: CorpusStore, source_ids: Iterable[str]) -> dict[str, list[str]]:
    """Source -> whole-file coverage problems, for every source whose plan does not cover lines 1..N once."""
    gaps: dict[str, list[str]] = {}
    for source_id in source_ids:
        plan = store.load_plan(source_id)
        if plan is None:
            continue
        problems = plan_coverage_problems(plan, len(store.raw_lines(source_id)))
        if problems:
            gaps[source_id] = problems
    return gaps


class Indexer:
    """Owns one Chroma client over the store's chroma directory and keeps it in step with the corpus."""

    def __init__(self, store: CorpusStore, embedder: TextEmbedder, client: ClientAPI | None = None) -> None:
        self.store = store
        self.embedder = embedder
        self.client = client or open_client(str(store.chroma_path))

    def select(self, source_id: str | None = None, *, allow_partial: bool = False) -> IndexSelection:
        """Decide what a run touches, refusing incomplete coverage before anything changes."""
        usable, excluded = source_usability(self.store, source_id)
        gaps = coverage_gaps(self.store, usable)
        if gaps and not allow_partial:
            detail = "; ".join(f"{gap_source}: {' / '.join(problems)}" for gap_source, problems in gaps.items())
            raise IncompleteCoverageError(
                f"{len(gaps)} source(s) are not fully chunked: {detail}. Import the missing slices, or pass "
                "--allow-partial to index what is there",
            )
        return IndexSelection(source_id=source_id, usable=usable, excluded=excluded, partial_coverage=gaps)

    def rebuild_all(self, *, allow_partial: bool = False) -> dict[str, Any]:
        """Drop and recreate every collection from the whole corpus (after the preflight passed)."""
        selection = self.select(None, allow_partial=allow_partial)
        existing = {collection.name for collection in self.client.list_collections()}
        for name in COLLECTION_NAMES:
            if name in existing:
                self.client.delete_collection(name)
        return self.apply(selection)

    def update(self, source_id: str | None = None, *, allow_partial: bool = False) -> dict[str, Any]:
        """Bring one source (or every source) plus the whole registry up to date; unchanged records are no-ops."""
        return self.apply(self.select(source_id, allow_partial=allow_partial))

    def apply(self, selection: IndexSelection) -> dict[str, Any]:
        """Write a selection: registry facets, usable sources' findings and passages; drop excluded sources."""
        corpus_version = self.store.corpus_version()
        collections = {name: self._collection(name) for name in COLLECTION_NAMES}
        report: dict[str, Any] = {
            "corpus_version": corpus_version,
            "scope": selection.source_id or "all",
            "excluded_sources": selection.excluded,
            "partial_coverage_sources": sorted(selection.partial_coverage),
            "collections": {},
        }
        strategies = self.store.load_registry()
        aliases = self.store.strategy_aliases(strategies)
        families = {family.family_id: family for family in self.store.load_families()}
        report["collections"][STRATEGY_FACETS] = self._sync(
            collections[STRATEGY_FACETS],
            strategy_records(strategies, families),
            corpus_version,
            drop=None,
        )
        findings_groups = {
            usable: finding_records(findings, aliases) if (findings := self.store.load_findings(usable)) else []
            for usable in selection.usable
        }
        passage_groups = {usable: passage_records(self.store, usable, aliases) for usable in selection.usable}
        # A full run drops every source group it did not write (stale, unplanned, deleted); a single-source
        # run drops only that source when it is excluded.
        drop = None if selection.source_id is None else set(selection.excluded)
        for name, groups in ((FINDINGS, findings_groups), (PASSAGES, passage_groups)):
            report["collections"][name] = self._sync(collections[name], groups, corpus_version, drop=drop)
        for name, collection in collections.items():
            collection.modify(
                metadata=stamped_metadata(
                    collection.metadata,
                    self.embedder.model_name,
                    corpus_version,
                    full=selection.full,
                ),
            )
            report["collections"][name]["records"] = collection.count()
        report["index_stamped_full"] = selection.full
        return report

    def _collection(self, name: str) -> Collection:
        """Get or create a cosine collection, refusing one built for another model or schema."""
        existing = {collection.name for collection in self.client.list_collections()}
        if name in existing:
            collection = self.client.get_collection(name, embedding_function=None)
            check_compatible(name, collection.metadata, self.embedder.model_name)
            return collection
        return self.client.create_collection(
            name,
            configuration=COSINE_CONFIGURATION,  # type: ignore[arg-type]
            metadata=collection_metadata(self.embedder.model_name, NEVER_FULLY_BUILT),
            embedding_function=None,
        )

    def _sync(
        self,
        collection: Collection,
        groups: Mapping[str, Sequence[IndexRecord]],
        corpus_version: str,
        drop: set[str] | None,
    ) -> dict[str, int]:
        """Per group: embed changed records first, then delete stale ids, then add; then drop groups.

        `drop=None` drops every existing group not in `groups`; a set drops exactly those groups.
        """
        key = GROUP_KEYS[collection.name]
        existing = _existing_hashes(collection, key)
        counts = {"unchanged": 0, "replaced": 0, "added": 0, "removed": 0, "records_written": 0, "records_deleted": 0}
        for group, records in groups.items():
            desired = _sealed_records(records, corpus_version)
            current = existing.get(group, {})
            changed = [
                record for record in desired if current.get(record.identifier) != record.metadata[RECORD_HASH_KEY]
            ]
            wanted = {record.identifier for record in desired}
            obsolete = [identifier for identifier in current if identifier not in wanted]
            if not changed and not obsolete:
                counts["unchanged"] += 1
                continue
            # Embed first: a failing model download must not leave a group already deleted.
            vectors = self.embedder.embed([record.document for record in changed])
            rewritten = [record.identifier for record in changed if record.identifier in current]
            self._delete(collection, [*obsolete, *rewritten])
            self._add(collection, changed, vectors)
            counts["replaced" if current else "added"] += 1
            counts["records_written"] += len(changed)
            counts["records_deleted"] += len(obsolete)
        dropped = set(existing) - set(groups) if drop is None else set(drop)
        for group in sorted(dropped & set(existing)):
            collection.delete(where={key: {"$eq": group}})
            counts["removed"] += 1
            counts["records_deleted"] += len(existing[group])
        return counts

    def _batch_size(self) -> int:
        return min(MAXIMUM_ADD_BATCH, self.client.get_max_batch_size())

    def _delete(self, collection: Collection, identifiers: Sequence[str]) -> None:
        """Delete records by id in batches no larger than the client allows."""
        batch_size = self._batch_size()
        for first in range(0, len(identifiers), batch_size):
            collection.delete(ids=list(identifiers[first : first + batch_size]))

    def _add(self, collection: Collection, records: Sequence[IndexRecord], vectors: Sequence[Sequence[float]]) -> None:
        """Add already-embedded records in batches no larger than the client allows."""
        batch_size = self._batch_size()
        for first in range(0, len(records), batch_size):
            batch = records[first : first + batch_size]
            collection.add(
                ids=[record.identifier for record in batch],
                embeddings=[list(vector) for vector in vectors[first : first + batch_size]],  # type: ignore[arg-type]
                documents=[record.document for record in batch],
                metadatas=[record.metadata for record in batch],  # type: ignore[misc]
            )


def _existing_hashes(collection: Collection, key: str) -> dict[str, dict[str, str]]:
    """group value -> {record id: record hash} for everything already in the collection."""
    result = collection.get(include=["metadatas"])
    grouped: dict[str, dict[str, str]] = defaultdict(dict)
    for identifier, metadata in zip(result["ids"], result["metadatas"] or [], strict=False):
        if metadata and key in metadata:
            grouped[str(metadata[key])][identifier] = str(metadata.get(RECORD_HASH_KEY, ""))
    return dict(grouped)
