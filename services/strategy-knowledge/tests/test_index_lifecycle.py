"""Index lifecycle: stale sources, re-chunking, the full/partial stamp, the coverage gate, embed-before-delete."""

import copy
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    SOURCE_ID,
    SOURCE_URL,
    HashingEmbedder,
    copy_fixture_raw,
    copy_fixture_registry,
    seed_store,
)

from strategy_knowledge import cli
from strategy_knowledge.corpus import REGISTRY_FILE, CorpusStore, read_json, write_json
from strategy_knowledge.fetch import FetchedDocument
from strategy_knowledge.index import (
    COSINE_CONFIGURATION,
    FINDINGS,
    HNSW_POOL_SHARDS,
    HNSW_POOL_SLOTS_PER_SHARD,
    PARTIAL_SINCE_METADATA,
    PASSAGES,
    STRATEGY_FACETS,
    IncompleteCoverageError,
    Indexer,
    open_client,
)
from strategy_knowledge.ingest import ImportOptions, add_source, import_chunked
from strategy_knowledge.knowledge_base import KnowledgeBase
from strategy_knowledge.validate import validate_corpus

STRAW = "post-fire-straw-mulching"
#: Cache paths the pool regression probes; each is a separate Chroma system with its own pool.
POOL_PROBE_PATHS = 10


class FailingEmbedder(HashingEmbedder):
    """Same model name as the index, but every embed call fails (a model download that breaks mid-run)."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Always fail."""
        raise RuntimeError(f"embedding {len(texts)} texts failed")


def _ids_for_source(indexer: Indexer, collection_name: str, source_id: str) -> list[str]:
    collection = indexer.client.get_collection(collection_name, embedding_function=None)
    return sorted(collection.get(where={"source_id": {"$eq": source_id}})["ids"])


def _changed_upstream(store: CorpusStore) -> None:
    """add-source on the registered URL returns a different body: the raw file and its hash change."""
    body = "\n".join(store.raw_lines(SOURCE_ID)[6:]).replace("1-2 tons/acre", "2-3 tons/acre")

    def fetcher(url: str) -> FetchedDocument:
        return FetchedDocument(url, url, "pdf", "Fixture guide", body, datetime.now(UTC).isoformat())

    outcome = add_source(store, SOURCE_URL, fetcher=fetcher)
    assert outcome.status == "changed"


def test_a_source_changed_upstream_is_removed_from_the_index(
    fixture_store: CorpusStore,
    embedder: HashingEmbedder,
) -> None:
    indexer = Indexer(fixture_store, embedder)
    indexer.rebuild_all()
    assert _ids_for_source(indexer, PASSAGES, SOURCE_ID)
    _changed_upstream(fixture_store)
    problems = validate_corpus(fixture_store, SOURCE_ID).problems
    assert any("chunk plan was made against a different raw file" in problem for problem in problems)
    report = indexer.update(SOURCE_ID)
    assert SOURCE_ID in report["excluded_sources"]
    assert _ids_for_source(indexer, PASSAGES, SOURCE_ID) == []
    assert _ids_for_source(indexer, FINDINGS, SOURCE_ID) == []
    full = indexer.update()
    assert full["index_stamped_full"] is False
    assert KnowledgeBase.open(fixture_store, embedder).index_is_stale


def test_index_source_on_a_stale_source_exits_non_zero(
    fixture_store: CorpusStore,
    embedder: HashingEmbedder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Indexer(fixture_store, embedder).rebuild_all()
    _changed_upstream(fixture_store)
    monkeypatch.setenv("STRATEGY_KB_CACHE_DIR", str(fixture_store.root))
    monkeypatch.setattr(cli, "embedder_for", lambda _model: embedder)
    assert cli.main(["index", "--source", SOURCE_ID]) == cli.EXIT_PROBLEMS


def test_a_full_index_run_that_leaves_out_a_planned_source_exits_non_zero(
    fixture_store: CorpusStore,
    embedder: HashingEmbedder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Indexer(fixture_store, embedder).rebuild_all()
    _changed_upstream(fixture_store)
    monkeypatch.setenv("STRATEGY_KB_CACHE_DIR", str(fixture_store.root))
    monkeypatch.setattr(cli, "embedder_for", lambda _model: embedder)
    assert cli.main(["index"]) == cli.EXIT_PROBLEMS


def test_a_rechunked_source_leaves_no_old_ids(
    fixture_store: CorpusStore,
    embedder: HashingEmbedder,
    fixture_document: dict[str, Any],
) -> None:
    indexer = Indexer(fixture_store, embedder)
    indexer.rebuild_all()
    assert f"{SOURCE_ID}#L27-31#W0" in _ids_for_source(indexer, PASSAGES, SOURCE_ID)
    block = copy.deepcopy(fixture_document["sources"][0])
    cover = next(chunk for chunk in block["chunks"] if chunk["line_start"] == 27)
    references = next(chunk for chunk in block["chunks"] if chunk["line_start"] == 32)
    rechunked = {
        "slice_id": "T2",
        "sources": [
            {
                "source_id": SOURCE_ID,
                "chunks": [
                    {**cover, "chunk_id": f"{SOURCE_ID}#L27-29", "line_end": 29, "linked_finding_ids": []},
                    {**cover, "chunk_id": f"{SOURCE_ID}#L30-31", "line_start": 30},
                    references,
                ],
                "skipped": [],
                "findings": [finding for finding in block["findings"] if finding["line_start"] >= 27],
                "candidate_strategies": [],
            },
        ],
    }
    assigned = ImportOptions(assignments={SOURCE_ID: [[27, 33]]})
    assert import_chunked(fixture_store, rechunked, "T2", assigned).rejected == {}
    report = indexer.update(SOURCE_ID)
    ids = _ids_for_source(indexer, PASSAGES, SOURCE_ID)
    assert f"{SOURCE_ID}#L27-31#W0" not in ids
    assert {f"{SOURCE_ID}#L27-29#W0", f"{SOURCE_ID}#L30-31#W0"} <= set(ids)
    assert report["collections"][PASSAGES]["records_deleted"] >= 1


def test_only_a_full_update_stamps_the_corpus_version(fixture_store: CorpusStore, embedder: HashingEmbedder) -> None:
    indexer = Indexer(fixture_store, embedder)
    built = indexer.rebuild_all()
    assert not KnowledgeBase.open(fixture_store, embedder).index_is_stale
    indexer.update(SOURCE_ID)
    collection = indexer.client.get_collection(STRATEGY_FACETS, embedding_function=None)
    assert collection.metadata["corpus_version"] == built["corpus_version"]
    assert collection.metadata[PARTIAL_SINCE_METADATA]
    assert KnowledgeBase.open(fixture_store, embedder).index_is_stale
    indexer.update()
    collection = indexer.client.get_collection(STRATEGY_FACETS, embedding_function=None)
    assert collection.metadata[PARTIAL_SINCE_METADATA] == ""
    assert not KnowledgeBase.open(fixture_store, embedder).index_is_stale


@pytest.fixture
def half_chunked_store(tmp_path: Path, fixture_document: dict[str, Any]) -> CorpusStore:
    """The fixture source with only lines 1-20 imported (one slice of two)."""
    store = CorpusStore(tmp_path / "half")
    copy_fixture_raw(store)
    copy_fixture_registry(store)
    block = copy.deepcopy(fixture_document["sources"][0])
    block["chunks"] = [chunk for chunk in block["chunks"] if chunk["line_end"] <= 20]
    block["findings"] = [finding for finding in block["findings"] if finding["line_start"] <= 20]
    block["candidate_strategies"] = []
    assigned = ImportOptions(assignments={SOURCE_ID: [[1, 20]]})
    outcome = import_chunked(store, {"slice_id": "H1", "sources": [block]}, "H1", assigned)
    assert outcome.rejected == {}
    return store


def test_validation_names_lines_no_slice_covers(half_chunked_store: CorpusStore) -> None:
    joined = "\n".join(validate_corpus(half_chunked_store).problems)
    assert f"{SOURCE_ID}: 13 uncovered lines, first [21, 22, 23, 24, 25, 26]" in joined
    assert "13 lines lie in no imported slice's assignment" in joined


def test_index_refuses_incomplete_coverage_unless_allowed(
    half_chunked_store: CorpusStore,
    embedder: HashingEmbedder,
) -> None:
    indexer = Indexer(half_chunked_store, embedder)
    with pytest.raises(IncompleteCoverageError, match=SOURCE_ID):
        indexer.update()
    assert indexer.client.list_collections() == []
    report = indexer.update(allow_partial=True)
    assert report["partial_coverage_sources"] == [SOURCE_ID]
    assert report["collections"][PASSAGES]["records"] == 2


def test_embeddings_are_computed_before_anything_is_deleted(
    fixture_store: CorpusStore,
    embedder: HashingEmbedder,
) -> None:
    Indexer(fixture_store, embedder).rebuild_all()
    registry = read_json(fixture_store.path(REGISTRY_FILE))
    for row in registry["strategies"]:
        if row["strategy_id"] == STRAW:
            row["facets"]["how_to"] = "Post-fire straw mulching, rewritten: spread straw at a new rate."
    write_json(fixture_store.path(REGISTRY_FILE), registry)
    failing = Indexer(fixture_store, FailingEmbedder())
    with pytest.raises(RuntimeError, match="embedding"):
        failing.update()
    collection = failing.client.get_collection(STRATEGY_FACETS, embedding_function=None)
    assert len(collection.get(where={"strategy_id": {"$eq": STRAW}})["ids"]) == 4


def test_a_raw_file_ending_in_a_newline_has_no_phantom_line(tmp_path: Path, embedder: HashingEmbedder) -> None:
    store = seed_store(tmp_path / "newline", trailing_newline=True)
    assert store.raw_path(SOURCE_ID).read_text(encoding="utf-8").endswith("\n")
    assert len(store.raw_lines(SOURCE_ID)) == 33
    assert store.load_sources()[SOURCE_ID].line_count == 33
    assert validate_corpus(store).problems == []
    report = Indexer(store, embedder).update()
    assert report["partial_coverage_sources"] == []
    assert report["collections"][PASSAGES]["records"] == 5


def test_the_client_hnsw_pool_gives_every_shard_its_slots(tmp_path: Path) -> None:
    """Regression: Chroma's own sizing left one slot per shard on Windows (AGENTS.md "Embedding and Chroma")."""
    pool_size = open_client(str(tmp_path / "chroma"))._server.hnsw_cache_size  # type: ignore[attr-defined]
    assert pool_size // HNSW_POOL_SHARDS >= HNSW_POOL_SLOTS_PER_SHARD


def test_a_full_shard_of_hnsw_indexes_stays_queryable(tmp_path: Path) -> None:
    """Regression for "Error creating hnsw segment reader: Nothing found on disk" (AGENTS.md "Embedding and Chroma").

    Each cache path gets as many indexes as one shard holds, each read before its first vector (as
    `Indexer._sync` does), so no shard can evict; with one slot per shard most runs of this test fail.
    """
    for path_number in range(POOL_PROBE_PATHS):
        client = open_client(str(tmp_path / f"chroma-{path_number}"))
        collections = []
        for number in range(HNSW_POOL_SLOTS_PER_SHARD):
            collection = client.create_collection(
                f"pool-{number}",
                configuration=COSINE_CONFIGURATION,  # type: ignore[arg-type]
                embedding_function=None,
            )
            collection.get(include=["metadatas"])
            collection.add(ids=["east", "north"], embeddings=[[1.0, 0.0], [0.0, 1.0]])  # type: ignore[arg-type]
            collections.append(collection)
        for collection in collections:
            nearest = collection.query(query_embeddings=[[1.0, 0.1]], n_results=1)  # type: ignore[arg-type]
            assert nearest["ids"] == [["east"]], (path_number, collection.name)
