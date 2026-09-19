"""What a serving plane may reach: a read-only facade, size-checked reads, and a sealed session."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import duckdb
import pytest

from plantgeo_ml_service.pipeline.duckdb_session import (
    DuckDbSessionError,
    seal_serving_configuration,
    sql_literal,
)
from plantgeo_ml_service.pipeline.object_store import (
    JSON_CONTENT_TYPE,
    InMemoryObjectStoreBackend,
    ListedObject,
    ObjectStore,
    ObjectTooLargeError,
    ReadOnlyObjectStoreView,
)
from plantgeo_ml_service.planes import refusals
from plantgeo_ml_service.planes.artifact_reads import (
    MAX_LISTED_ARTIFACTS,
    MAX_LISTING_BYTES,
    list_artifacts,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

ARTIFACT_KIND: Final = "fire-risk"
ARTIFACT_PREFIX: Final = f"ml/artifacts/{ARTIFACT_KIND}/"

#: One artifact body, small enough that the count cap is what a listing test exercises.
ARTIFACT_BODY: Final = b'{"schema_version": 1}'


class _TimedBackend(InMemoryObjectStoreBackend):
    """An in-memory bucket that also remembers WHEN each object was written."""

    def __init__(self) -> None:
        super().__init__()
        self.written_at: dict[str, datetime] = {}

    def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        """Store the object and stamp it, so a listing can be ordered by age like a real one."""
        super().put(key, payload, content_type=content_type)
        self.written_at.setdefault(key, datetime(2026, 1, 1, tzinfo=UTC))

    def stamp(self, key: str, moment: datetime) -> None:
        """Declare when one object was written."""
        self.written_at[key] = moment

    def list_objects(self, prefix: str, *, max_keys: int = 500_000) -> Iterator[ListedObject]:
        """Walk the prefix, attaching each object's recorded write time."""
        for entry in super().list_objects(prefix, max_keys=max_keys):
            yield ListedObject(key=entry.key, last_modified=self.written_at.get(entry.key))


def _store() -> tuple[ObjectStore, _TimedBackend]:
    """Return a store over a timestamp-keeping in-memory bucket."""
    backend = _TimedBackend()
    return ObjectStore(backend=backend), backend


def test_the_facade_a_plane_receives_has_no_way_to_write_or_delete() -> None:
    """M4: a read cannot write because the object it holds cannot, not because it chooses not to."""
    store, _ = _store()

    facade = store.read_only()

    assert isinstance(facade, ReadOnlyObjectStoreView)
    assert not hasattr(facade, "put")
    assert not hasattr(facade, "delete")
    assert not hasattr(facade, "write_partition")
    assert not hasattr(facade, "put_immutable")


def test_an_oversize_object_is_refused_before_its_body_is_ever_fetched() -> None:
    """M2: a ceiling applied after `Body.read()` has already paid the download it was meant to stop."""
    store, backend = _store()
    store.backend.put("ml/big.json", b"x" * 64, content_type=JSON_CONTENT_TYPE)
    fetched: list[str] = []
    original_get = backend.get
    backend.get = lambda key: (fetched.append(key), original_get(key))[1]  # type: ignore[method-assign]

    with pytest.raises(ObjectTooLargeError):
        store.read_only().read_object("ml/big.json", max_bytes=16)

    assert fetched == []


def test_an_object_inside_its_ceiling_is_read_whole() -> None:
    store, _ = _store()
    store.backend.put("ml/small.json", ARTIFACT_BODY, content_type=JSON_CONTENT_TYPE)

    assert store.read_only().read_object("ml/small.json", max_bytes=len(ARTIFACT_BODY)) == ARTIFACT_BODY
    assert store.read_only().object_size("ml/small.json") == len(ARTIFACT_BODY)
    assert store.read_only().object_size("ml/absent.json") is None


def test_an_artifact_listing_answers_the_newest_first_and_says_when_it_was_cut_short() -> None:
    """M3: an answer of 25 that might be 25 of 25 or 25 of 900 is not a count."""
    store, backend = _store()
    total = MAX_LISTED_ARTIFACTS + 5
    for index in range(total):
        key = f"{ARTIFACT_PREFIX}{index:064d}.json"
        store.backend.put(key, ARTIFACT_BODY, content_type=JSON_CONTENT_TYPE)
        backend.stamp(key, datetime(2026, 1, 1, tzinfo=UTC).replace(minute=index))

    listing = list_artifacts(store.read_only(), model_kind=ARTIFACT_KIND)

    assert len(listing.artifacts) == MAX_LISTED_ARTIFACTS
    assert listing.listing_truncated is True
    assert listing.to_wire()["listing_truncated"] is True
    assert listing.artifacts[0].key == f"{ARTIFACT_PREFIX}{total - 1:064d}.json"
    assert listing.claim.artifact_sha256 == listing.artifacts[0].sha256


def test_a_listing_whose_artifacts_pass_the_request_budget_is_refused_by_name() -> None:
    """A per-object ceiling bounds each GET and says nothing about what the request costs."""
    store, _ = _store()
    body = b'{"schema_version": 1, "padding": "' + b"p" * (MAX_LISTING_BYTES // 2) + b'"}'
    for index in range(3):
        store.backend.put(f"{ARTIFACT_PREFIX}{index:064d}.json", body, content_type=JSON_CONTENT_TYPE)

    with pytest.raises(refusals.MachineLearningRefusalError) as raised:
        list_artifacts(store.read_only(), model_kind=ARTIFACT_KIND)

    assert raised.value.code == refusals.READ_OVER_BUDGET


def test_a_refused_artifact_listing_never_puts_the_object_key_in_its_message() -> None:
    store, _ = _store()
    store.backend.put(f"{ARTIFACT_PREFIX}{'a' * 64}.json", b"not json", content_type=JSON_CONTENT_TYPE)

    with pytest.raises(refusals.MachineLearningRefusalError) as raised:
        list_artifacts(store.read_only(), model_kind=ARTIFACT_KIND)

    assert raised.value.code == refusals.ARTIFACT_UNREADABLE
    assert ARTIFACT_PREFIX not in raised.value.message


def test_the_object_store_unconfigured_refusal_names_no_environment_variable() -> None:
    """A caller learns this deployment is not wired; how it is wired is an operator fact."""
    error = refusals.object_store_unconfigured()

    assert error.code == refusals.OBJECT_STORE_UNCONFIGURED
    assert "OBJECT_STORE_" not in error.message


def test_a_sealed_session_refuses_to_read_the_local_filesystem(tmp_path: Path) -> None:
    """M4: a `read_csv` of a local path reachable from a serving query is a file read nobody offered."""
    readable = tmp_path / "rows.csv"
    readable.write_text("value\n1\n", encoding="utf-8")
    connection = duckdb.connect()
    try:
        # The same query answers BEFORE the seal, so the refusal below is the seal and not the path.
        assert connection.execute(f"SELECT count(*) FROM read_csv({sql_literal(readable.as_posix())})").fetchall()

        seal_serving_configuration(connection)

        with pytest.raises(duckdb.Error):
            connection.execute(f"SELECT count(*) FROM read_csv({sql_literal(readable.as_posix())})")
        # The seal is one-way: nothing a query reaches afterwards can put the filesystem back.
        with pytest.raises(duckdb.Error):
            connection.execute("SET disabled_filesystems=''")
    finally:
        connection.close()


def test_sealing_a_closed_connection_is_a_typed_session_error() -> None:
    """Every DuckDB failure on this path leaves as `DuckDbSessionError`, never a raw engine type."""
    connection = duckdb.connect()
    connection.close()

    with pytest.raises(DuckDbSessionError):
        seal_serving_configuration(connection)
