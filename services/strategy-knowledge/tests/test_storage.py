"""Three-way bucket sync against a last-synced manifest, refusal of unsafe keys, and index publishing rules."""

import hashlib
import io
import tarfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import SOURCE_ID, HashingEmbedder

from strategy_knowledge.config import Settings
from strategy_knowledge.corpus import CorpusStore
from strategy_knowledge.index import Indexer
from strategy_knowledge.storage import INDEX_POINTER_KEY, BucketSync, KeyState, classify

PREFIX = "strategy-knowledge/"
KEY = "corpus/sources.json"
REMOTE_VERSION = "f" * 64


class FakeBucket:
    """The S3 calls BucketSync makes, over a dict; ETags are MD5s like a single-part PUT's (boto3's argument names)."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_keys: list[str] = []
        self.fail_on: str | None = None

    def put(self, key: str, body: bytes) -> None:
        """Change an object behind the sync's back (another machine pushing)."""
        self.objects[f"{PREFIX}{key}"] = body

    def get_paginator(self, _operation: str) -> "FakeBucket":
        return self

    def paginate(
        self,
        *,
        Bucket: str,  # noqa: N803, ARG002
        Prefix: str,  # noqa: N803
    ) -> Iterator[dict[str, Any]]:
        contents = [
            {"Key": key, "ETag": f'"{hashlib.md5(body, usedforsecurity=False).hexdigest()}"'}
            for key, body in sorted(self.objects.items())
            if key.startswith(Prefix)
        ]
        yield {"Contents": contents}

    def download_file(self, _bucket: str, key: str, filename: str) -> None:
        Path(filename).write_bytes(self.objects[key])

    def get_object(
        self,
        *,
        Bucket: str,  # noqa: N803, ARG002
        Key: str,  # noqa: N803
    ) -> dict[str, Any]:
        body = self.objects[Key]
        return {"Body": io.BytesIO(body), "ETag": f'"{hashlib.md5(body, usedforsecurity=False).hexdigest()}"'}

    def put_object(
        self,
        *,
        Bucket: str,  # noqa: N803, ARG002
        Key: str,  # noqa: N803
        Body: bytes,  # noqa: N803
        ContentType: str,  # noqa: N803, ARG002
    ) -> dict[str, str]:
        if self.fail_on is not None and Key == f"{PREFIX}{self.fail_on}":
            raise RuntimeError(f"upload of {Key} failed")
        self.objects[Key] = Body
        self.put_keys.append(Key.removeprefix(PREFIX))
        return {"ETag": f'"{hashlib.md5(Body, usedforsecurity=False).hexdigest()}"'}

    def index_keys(self) -> list[str]:
        """Every object under index/, prefix-relative."""
        return sorted(key.removeprefix(PREFIX) for key in self.objects if key.startswith(f"{PREFIX}index/"))


@pytest.fixture
def bucket() -> FakeBucket:
    """An empty fake bucket."""
    return FakeBucket()


def _sync(root: Path, bucket: FakeBucket) -> BucketSync:
    settings = Settings(
        cache_dir=root,
        prefix=PREFIX,
        embedding_model="test",
        object_store_values={"OBJECT_STORE_BUCKET": "bucket"},
    )
    return BucketSync(settings, CorpusStore(root), client=bucket)


def _write(root: Path, key: str, body: bytes) -> None:
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def test_classify_is_a_three_way_comparison() -> None:
    base = {"etag": "a", "md5": "a"}
    assert classify("a", "a", None) is KeyState.SAME
    assert classify("b", None, base) is KeyState.LOCAL_ONLY
    assert classify(None, "b", base) is KeyState.REMOTE_ONLY
    assert classify("b", "a", base) is KeyState.LOCAL_CHANGED
    assert classify("a", "b", base) is KeyState.REMOTE_CHANGED
    assert classify("b", "c", base) is KeyState.CONFLICT
    assert classify("b", "c", None) is KeyState.CONFLICT


def test_push_then_pull_round_trip_records_the_manifest(tmp_path: Path, bucket: FakeBucket) -> None:
    _write(tmp_path / "a", KEY, b"v1")
    first = _sync(tmp_path / "a", bucket).push()
    assert first.transferred == [KEY]
    assert _sync(tmp_path / "a", bucket).push().unchanged == 1
    pulled = _sync(tmp_path / "b", bucket).pull()
    assert pulled.transferred == [KEY]
    assert (tmp_path / "b" / KEY).read_bytes() == b"v1"
    assert (tmp_path / "b" / "sync_manifest.json").is_file()


def test_pull_never_overwrites_an_unpushed_local_change(tmp_path: Path, bucket: FakeBucket) -> None:
    _write(tmp_path, KEY, b"v1")
    sync = _sync(tmp_path, bucket)
    sync.push()
    _write(tmp_path, KEY, b"local edit")
    report = sync.pull()
    assert report.kept_local_changes == [KEY]
    assert (tmp_path / KEY).read_bytes() == b"local edit"
    assert not report.failed
    assert sync.push().transferred == [KEY]
    assert bucket.objects[f"{PREFIX}{KEY}"] == b"local edit"


def test_push_never_overwrites_a_remote_change_made_since_the_last_sync(tmp_path: Path, bucket: FakeBucket) -> None:
    _write(tmp_path, KEY, b"v1")
    sync = _sync(tmp_path, bucket)
    sync.push()
    bucket.put(KEY, b"remote edit")
    report = sync.push()
    assert report.kept_remote_changes == [KEY]
    assert bucket.objects[f"{PREFIX}{KEY}"] == b"remote edit"
    assert sync.pull().transferred == [KEY]
    assert (tmp_path / KEY).read_bytes() == b"remote edit"


def test_a_conflict_is_reported_until_a_side_is_chosen(tmp_path: Path, bucket: FakeBucket) -> None:
    _write(tmp_path, KEY, b"v1")
    sync = _sync(tmp_path, bucket)
    sync.push()
    bucket.put(KEY, b"remote edit")
    _write(tmp_path, KEY, b"local edit")
    pulled = sync.pull()
    pushed = sync.push()
    assert pulled.conflicts == pushed.conflicts == [KEY]
    assert pulled.failed
    assert pushed.failed
    assert (tmp_path / KEY).read_bytes() == b"local edit"
    assert bucket.objects[f"{PREFIX}{KEY}"] == b"remote edit"
    forced = sync.push(on_conflict="local")
    assert forced.resolved_conflicts == {KEY: "local"}
    assert bucket.objects[f"{PREFIX}{KEY}"] == b"local edit"


def test_pull_can_let_the_remote_win_a_conflict(tmp_path: Path, bucket: FakeBucket) -> None:
    _write(tmp_path, KEY, b"v1")
    sync = _sync(tmp_path, bucket)
    sync.push()
    bucket.put(KEY, b"remote edit")
    _write(tmp_path, KEY, b"local edit")
    report = sync.pull(on_conflict="remote")
    assert report.resolved_conflicts == {KEY: "remote"}
    assert (tmp_path / KEY).read_bytes() == b"remote edit"


def test_keeping_local_on_pull_lets_the_next_push_upload_it(tmp_path: Path, bucket: FakeBucket) -> None:
    _write(tmp_path, KEY, b"v1")
    sync = _sync(tmp_path, bucket)
    sync.push()
    bucket.put(KEY, b"remote edit")
    _write(tmp_path, KEY, b"local edit")
    assert sync.pull(on_conflict="local").resolved_conflicts == {KEY: "local"}
    pushed = sync.push()
    assert pushed.conflicts == []
    assert bucket.objects[f"{PREFIX}{KEY}"] == b"local edit"


def test_unsafe_remote_keys_are_refused(tmp_path: Path, bucket: FakeBucket) -> None:
    bucket.put("raw/", b"")
    bucket.put("raw/../../escaped.txt", b"outside")
    bucket.put("raw/fine.txt", b"inside")
    report = _sync(tmp_path / "cache", bucket).pull()
    assert sorted(report.rejected_keys) == ["raw/", "raw/../../escaped.txt"]
    assert report.failed
    assert not (tmp_path / "escaped.txt").exists()
    assert (tmp_path / "cache" / "raw" / "fine.txt").read_bytes() == b"inside"


def test_a_conflict_anywhere_stops_the_whole_transfer(tmp_path: Path, bucket: FakeBucket) -> None:
    _write(tmp_path, KEY, b"v1")
    _write(tmp_path, "raw/a.txt", b"v1")
    sync = _sync(tmp_path, bucket)
    sync.push()
    bucket.put(KEY, b"remote edit")
    bucket.put("raw/b.txt", b"remote only")
    _write(tmp_path, KEY, b"local edit")
    _write(tmp_path, "raw/a.txt", b"local change")
    pushed = sync.push()
    assert pushed.conflicts == [KEY]
    assert pushed.transferred == []
    assert bucket.objects[f"{PREFIX}raw/a.txt"] == b"v1"
    pulled = sync.pull()
    assert pulled.conflicts == [KEY]
    assert pulled.transferred == []
    assert not (tmp_path / "raw" / "b.txt").exists()


def test_push_publishes_the_registries_last(tmp_path: Path, bucket: FakeBucket) -> None:
    for key in (
        KEY,
        "corpus/strategies/strategy_registry.json",
        "corpus/chunk_plans/new-source.json",
        "corpus/findings/new-source.json",
        "raw/new-source.txt",
    ):
        _write(tmp_path, key, key.encode())
    bucket.fail_on = "corpus/strategies/strategy_registry.json"
    with pytest.raises(RuntimeError, match="failed"):
        _sync(tmp_path, bucket).push()
    assert f"{PREFIX}{KEY}" not in bucket.objects
    assert bucket.put_keys == [
        "raw/new-source.txt",
        "corpus/chunk_plans/new-source.json",
        "corpus/findings/new-source.json",
    ]
    bucket.fail_on = None
    _sync(tmp_path, bucket).push()
    assert bucket.put_keys[-2:] == ["corpus/strategies/strategy_registry.json", KEY]


@pytest.fixture
def indexed_store(fixture_store: CorpusStore, embedder: HashingEmbedder) -> tuple[CorpusStore, str]:
    """The fixture corpus with a full index built over it, and that index's stamped corpus_version."""
    built = Indexer(fixture_store, embedder).rebuild_all()
    return fixture_store, built["corpus_version"]


def test_the_index_archive_is_labelled_with_the_index_stamp(indexed_store: tuple[CorpusStore, str]) -> None:
    store, version = indexed_store
    bucket = FakeBucket()
    report = _sync(store.root, bucket).push(with_index=True)
    assert report.index_refusals == []
    assert set(bucket.index_keys()) == {INDEX_POINTER_KEY, f"index/{version}/chroma.tar.gz"}
    assert bucket.objects[f"{PREFIX}{INDEX_POINTER_KEY}"] == f"{version}\n".encode()
    assert bucket.put_keys[-1] == INDEX_POINTER_KEY


def test_a_partial_index_is_never_pushed(indexed_store: tuple[CorpusStore, str], embedder: HashingEmbedder) -> None:
    store, _version = indexed_store
    Indexer(store, embedder).update(SOURCE_ID)
    bucket = FakeBucket()
    report = _sync(store.root, bucket).push(with_index=True)
    assert report.failed
    assert any("partial index updates" in reason for reason in report.index_refusals)
    assert bucket.index_keys() == []
    assert f"{PREFIX}{KEY}" in bucket.objects


def test_the_index_is_not_pushed_when_the_corpus_push_kept_remote_changes(
    indexed_store: tuple[CorpusStore, str],
) -> None:
    store, _version = indexed_store
    bucket = FakeBucket()
    sync = _sync(store.root, bucket)
    sync.push()
    bucket.put(KEY, b"another machine's sources.json")
    report = sync.push(with_index=True)
    assert report.kept_remote_changes == [KEY]
    assert any("corpus push" in reason for reason in report.index_refusals)
    assert bucket.index_keys() == []


def test_a_latest_changed_remotely_since_the_last_sync_is_not_overwritten(
    indexed_store: tuple[CorpusStore, str],
) -> None:
    store, _version = indexed_store
    bucket = FakeBucket()
    sync = _sync(store.root, bucket)
    assert sync.push(with_index=True).index_refusals == []
    bucket.put(INDEX_POINTER_KEY, f"{REMOTE_VERSION}\n".encode())
    report = sync.push(with_index=True)
    assert report.failed
    assert any("changed in the bucket" in reason for reason in report.index_refusals)
    assert bucket.objects[f"{PREFIX}{INDEX_POINTER_KEY}"] == f"{REMOTE_VERSION}\n".encode()


def _archive(members: dict[str, bytes], links: dict[str, str] | None = None) -> bytes:
    """A gzipped tar of regular files (name -> bytes) and symbolic links (name -> target), names as given."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, body in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            archive.addfile(info)
    return buffer.getvalue()


def _publish(bucket: FakeBucket, archive: bytes) -> str:
    """Put an archive and a LATEST naming it into the bucket; return the archive key."""
    archive_key = f"index/{REMOTE_VERSION}/chroma.tar.gz"
    bucket.put(archive_key, archive)
    bucket.put(INDEX_POINTER_KEY, f"{REMOTE_VERSION}\n".encode())
    return archive_key


def _cache_with_an_index(root: Path) -> None:
    _write(root, "chroma/marker.txt", b"local index")
    _write(root, KEY, b"local sources")


@pytest.mark.parametrize(
    ("members", "links"),
    [
        ({"chroma/ok.bin": b"ok", "corpus/sources.json": b"hijacked"}, None),
        ({"chroma/ok.bin": b"ok", "sync_manifest.json": b"{}"}, None),
        ({"chroma/ok.bin": b"ok", "../escaped.txt": b"outside"}, None),
        ({"chroma/ok.bin": b"ok", "chroma/../../escaped.txt": b"outside"}, None),
        ({"chroma/ok.bin": b"ok", "/absolute.txt": b"outside"}, None),
        ({"chroma/ok.bin": b"ok"}, {"chroma/link": "../corpus/sources.json"}),
    ],
)
def test_an_index_archive_with_any_unsafe_member_is_refused_whole(
    tmp_path: Path,
    bucket: FakeBucket,
    members: dict[str, bytes],
    links: dict[str, str] | None,
) -> None:
    root = tmp_path / "cache"
    _cache_with_an_index(root)
    archive_key = _publish(bucket, _archive(members, links))
    report = _sync(root, bucket).pull(with_index=True)
    assert report.rejected_keys == [archive_key]
    assert (root / "chroma" / "marker.txt").read_bytes() == b"local index"
    assert not (root / "chroma" / "ok.bin").exists()
    assert (root / KEY).read_bytes() == b"local sources"
    assert not (tmp_path / "escaped.txt").exists()
    assert not (root / "escaped.txt").exists()
    assert not list(root.glob(".index-pull-*"))


def test_pulling_an_index_from_a_bucket_that_never_had_one_keeps_the_local_index(
    tmp_path: Path,
    bucket: FakeBucket,
) -> None:
    root = tmp_path / "cache"
    _cache_with_an_index(root)
    report = _sync(root, bucket).pull(with_index=True)
    assert not report.failed
    assert report.transferred == []
    assert (root / "chroma" / "marker.txt").read_bytes() == b"local index"
    assert INDEX_POINTER_KEY not in (root / "sync_manifest.json").read_text(encoding="utf-8")


def test_a_latest_naming_a_missing_archive_is_rejected_not_raised(tmp_path: Path, bucket: FakeBucket) -> None:
    root = tmp_path / "cache"
    _cache_with_an_index(root)
    bucket.put(INDEX_POINTER_KEY, f"{REMOTE_VERSION}\n".encode())
    report = _sync(root, bucket).pull(with_index=True)
    assert report.rejected_keys == [f"index/{REMOTE_VERSION}/chroma.tar.gz"]
    assert (root / "chroma" / "marker.txt").read_bytes() == b"local index"


def test_a_safe_index_archive_replaces_only_chroma(tmp_path: Path, bucket: FakeBucket) -> None:
    root = tmp_path / "cache"
    _cache_with_an_index(root)
    archive_key = _publish(bucket, _archive({"chroma/new.bin": b"new index"}))
    report = _sync(root, bucket).pull(with_index=True)
    assert report.rejected_keys == []
    assert report.transferred == [archive_key]
    assert (root / "chroma" / "new.bin").read_bytes() == b"new index"
    assert not (root / "chroma" / "marker.txt").exists()
    assert (root / KEY).read_bytes() == b"local sources"
    assert INDEX_POINTER_KEY in (root / "sync_manifest.json").read_text(encoding="utf-8")
    assert not list(root.glob(".index-pull-*"))
    assert not list(root.glob(".chroma-retired-*"))
