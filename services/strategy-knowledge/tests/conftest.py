"""Shared fixtures: a deterministic hashing embedder (no model download) and a seeded corpus store."""

import hashlib
import math
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import pytest

from strategy_knowledge.corpus import FAMILIES_FILE, REGISTRY_FILE, CorpusStore, read_json, split_raw_lines
from strategy_knowledge.index import Indexer
from strategy_knowledge.ingest import import_chunked, resolve_slice_id
from strategy_knowledge.knowledge_base import KnowledgeBase
from strategy_knowledge.lexical import tokenize

FIXTURES: Final = Path(__file__).parent / "fixtures"
CHUNKED_FIXTURE: Final = FIXTURES / "chunked_fixture.json"
SOURCE_ID: Final = "fixture-post-fire-guide"
SOURCE_URL: Final = "https://example.org/guides/post-fire-land-care.pdf"
HASHING_DIMENSIONS: Final = 64


class HashingEmbedder:
    """Feature hashing (Weinberger et al. 2009) of word tokens into a unit vector; stable across processes."""

    model_name = "test-hashing-embedder"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One L2-normalised bag-of-words vector per text."""
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        vector = [0.0] * HASHING_DIMENSIONS
        for token in tokenize(text):
            bucket = int(hashlib.md5(token.encode(), usedforsecurity=False).hexdigest(), 16) % HASHING_DIMENSIONS
            vector[bucket] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            vector[0] = 1.0
            return vector
        return [value / norm for value in vector]


class OtherModelEmbedder(HashingEmbedder):
    """Same vectors, different model name: an index built with one must be refused by the other."""

    model_name = "some-other-model"


def copy_fixture_raw(store: CorpusStore, *, trailing_newline: bool = False) -> None:
    """Put the fixture raw file into a cache, optionally ending in a newline (as raw files 24 and 25 do)."""
    store.raw_path(SOURCE_ID).parent.mkdir(parents=True, exist_ok=True)
    text = (FIXTURES / "raw" / f"{SOURCE_ID}.txt").read_text(encoding="utf-8")
    store.raw_path(SOURCE_ID).write_text(text + ("\n" if trailing_newline else ""), encoding="utf-8", newline="\n")


def copy_fixture_registry(store: CorpusStore) -> None:
    """Put the fixture registry and families into a cache."""
    for relative, name in ((REGISTRY_FILE, "strategy_registry.json"), (FAMILIES_FILE, "families.json")):
        store.path(relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / name, store.path(relative))


def seed_store(root: Path, *, trailing_newline: bool = False) -> CorpusStore:
    """A cache with the fixture raw file, registry and families, and the fixture chunk plan imported."""
    store = CorpusStore(root)
    copy_fixture_raw(store, trailing_newline=trailing_newline)
    copy_fixture_registry(store)
    document = read_json(CHUNKED_FIXTURE)
    outcome = import_chunked(store, document, resolve_slice_id(document, CHUNKED_FIXTURE))
    assert not outcome.rejected, outcome.rejected
    return store


@pytest.fixture
def fixture_store(tmp_path: Path) -> CorpusStore:
    """A seeded corpus store in a fresh temporary cache."""
    return seed_store(tmp_path / "cache")


@pytest.fixture
def embedder() -> HashingEmbedder:
    """The deterministic test embedder."""
    return HashingEmbedder()


@pytest.fixture
def knowledge_base(fixture_store: CorpusStore, embedder: HashingEmbedder) -> KnowledgeBase:
    """A knowledge base over a freshly built index of the fixture corpus."""
    Indexer(fixture_store, embedder).rebuild_all()
    return KnowledgeBase.open(fixture_store, embedder)


@pytest.fixture
def fixture_raw_lines() -> list[str]:
    """The fixture raw file's lines, numbered as the Read tool numbers them."""
    return split_raw_lines((FIXTURES / "raw" / f"{SOURCE_ID}.txt").read_text(encoding="utf-8"))


@pytest.fixture
def fixture_document() -> dict[str, Any]:
    """The fixture DESIGN.md section 5 document."""
    return read_json(CHUNKED_FIXTURE)
