"""The signal operator archives first, resumes exact partial work, and never publishes availability."""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.pipeline.parquet.availability_index import (
    availability_bootstrap_marker_key,
    availability_pointer_key,
)
from agri_data_service.pipeline.parquet.objectstore import availability_retry_path, availability_retry_quarantine_path
from agri_data_service.pipeline.parquet.signal_coordinate_correction import (
    LANE_ROOT,
    SignalRepair,
    encode,
    verify_signal_physical,
)
from tests.parquet.test_signal_coordinate_correction import DAY, STAMP, _bucket, _prepared
from tests.scripts import load_scripts_module
from tests.scripts.test_correct_sensor_absences import FakeSession

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

OPERATOR: Any = load_scripts_module("correct_signal_coordinates.py", "correct_signal_coordinates")


def _setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, owner: FakeSession | None = None
) -> tuple[Any, Any, Any, Path, Path, Path, str, str]:
    prepared = _prepared()
    backend, storage = _bucket(prepared.originals)
    configured_backend: Any = backend
    configured_backend.bucket = "fixture-signal"
    identities = []
    for key, payload in prepared.originals.items():
        held = storage.read(key, max_bytes=100_000)
        assert held is not None
        identities.append(
            {"key": key, "sha256": sha256_digest(payload), "etag": held.etag, "version_id": held.version_id}
        )
    prepared = replace(prepared, identities=tuple(identities))
    request_payload = encode({"bucket": "fixture-signal", "completed_at": STAMP.isoformat()})
    repair = SignalRepair(request_payload, (prepared,), STAMP, "prepared")
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"preserved archive")
    request = tmp_path / "request.json"
    request.write_bytes(request_payload)
    request_sha = sha256_digest(request_payload)
    proof = tmp_path / "proof.json"
    now = datetime.now(UTC)
    proof.write_bytes(
        encode(
            {
                "schema_version": "signal-correction-quiescence/v1",
                "lane_root": LANE_ROOT,
                "request_sha256": request_sha,
                "writers_stopped": True,
                "no_inflight_retry_workers": True,
                "operator": "reviewer",
                "evidence": "fixture observations",
                "observed_at": (now - timedelta(minutes=1)).isoformat(),
                "valid_until": (now + timedelta(minutes=10)).isoformat(),
            }
        )
    )
    monkeypatch.setattr(OPERATOR, "connect", lambda: (backend, storage))
    monkeypatch.setattr(OPERATOR, "build_signal_repair", lambda *_args, **_kwargs: repair)
    monkeypatch.setattr(OPERATOR, "ARCHIVE_PIN", SimpleNamespace(sha256=sha256_digest(archive.read_bytes())))
    session = FakeSession() if owner is None else owner

    @asynccontextmanager
    async def held_session() -> AsyncIterator[FakeSession]:
        yield session

    @asynccontextmanager
    async def held_lock(_session: object, _key: str) -> AsyncIterator[bool]:
        yield True

    monkeypatch.setattr(OPERATOR, "ingest_session", held_session)
    monkeypatch.setattr(OPERATOR, "postgres_lane_publication_barrier", held_lock)
    monkeypatch.setattr(OPERATOR, "postgres_lane_day_lock", held_lock)
    return backend, storage, prepared, request, archive, proof, request_sha, sha256_digest(proof.read_bytes())


async def test_apply_archives_source_and_candidates_before_mutation_and_resume_verifies_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, storage, day, request, archive, proof, request_sha, proof_sha = _setup(tmp_path, monkeypatch)
    original_put = backend.put
    root = f"{LANE_ROOT}/availability/repairs/{request_sha}"
    fail_key = partition_path("signal", "observed", 5, DAY)

    def interrupted(key: str, payload: bytes, *, content_type: str) -> None:
        if key.startswith(f"{LANE_ROOT}/zoom="):
            assert backend.objects[f"{root}/source-archive.tar.gz"] == archive.read_bytes()
            journal = json.loads(backend.objects[f"{root}/journal.json"])
            assert journal["days"][DAY.isoformat()] == "mutating"
        if key == fail_key:
            raise RuntimeError("interrupted physical write")
        original_put(key, payload, content_type=content_type)

    monkeypatch.setattr(backend, "put", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        await OPERATOR.apply(request, archive, request_sha, proof, proof_sha, max_days=1)
    monkeypatch.setattr(backend, "put", original_put)
    result = await OPERATOR.apply(request, archive, request_sha, proof, proof_sha, max_days=1)
    assert result["physical_complete"] is True
    assert result["availability_publication"] is False
    assert availability_pointer_key(LANE_ROOT) not in backend.objects
    physical = {key: payload for key, payload in backend.objects.items() if key.startswith(f"{LANE_ROOT}/zoom=")}
    verify_signal_physical(day, physical)
    repeated = await OPERATOR.apply(request, archive, request_sha, proof, proof_sha, max_days=1)
    assert repeated["days_admitted_this_run"] == 0
    backend.objects[fail_key] = b"foreign replacement"
    with pytest.raises(ValueError, match="physical ladder differs"):
        await OPERATOR.apply(request, archive, request_sha, proof, proof_sha, max_days=1)
    assert storage.read(f"{root}/journal.json", max_bytes=100_000) is not None


async def test_apply_never_reinterprets_a_new_availability_head_as_a_fresh_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, _storage, day, request, archive, proof, request_sha, proof_sha = _setup(tmp_path, monkeypatch)
    backend.objects[availability_pointer_key(LANE_ROOT)] = b"new generation"
    before = dict(backend.objects)
    with pytest.raises(ValueError, match="separate recovery"):
        await OPERATOR.apply(request, archive, request_sha, proof, proof_sha, max_days=1)
    assert backend.objects == before
    assert all(backend.objects[key] == value for key, value in day.originals.items())


@pytest.mark.parametrize("lost_backend", [False, True])
async def test_apply_pings_pinned_backend_after_first_write_even_when_wrappers_stay_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, lost_backend: bool
) -> None:
    owner = FakeSession()
    backend, _storage, _day, request, archive, proof, request_sha, proof_sha = _setup(
        tmp_path, monkeypatch, owner=owner
    )
    owning_thread = threading.get_ident()
    owner_lost = False
    after_loss: list[str] = []
    written: list[str] = []
    original_put, original_delete = backend.put, backend.delete

    async def execute(statement: object, params: object = None) -> Any:
        del statement, params
        assert threading.get_ident() == owning_thread
        if owner_lost and lost_backend:
            raise ConnectionError("pinned backend died")
        return SimpleNamespace(scalar_one=lambda: 124 if owner_lost else 123)

    def put(key: str, payload: bytes, *, content_type: str) -> None:
        nonlocal owner_lost
        if owner_lost:
            after_loss.append(key)
        original_put(key, payload, content_type=content_type)
        if key.startswith(f"{LANE_ROOT}/zoom="):
            written.append(key)
            owner_lost = True

    def delete(key: str) -> None:
        if owner_lost:
            after_loss.append(key)
        original_delete(key)

    monkeypatch.setattr(owner, "execute", execute)
    monkeypatch.setattr(backend, "put", put)
    monkeypatch.setattr(backend, "delete", delete)
    with pytest.raises((ConnectionError, ValueError), match=r"backend died|backend changed"):
        await OPERATOR.apply(request, archive, request_sha, proof, proof_sha, max_days=1)
    assert written == [partition_path("signal", "observed", 13, DAY)]
    assert after_loss == []
    assert not owner.held.closed
    assert not owner.held.invalidated
    assert owner.held.sync_connection.connection.driver_connection is owner.driver
    root = f"{LANE_ROOT}/availability/repairs/{request_sha}"
    assert json.loads(backend.objects[f"{root}/journal.json"])["days"][DAY.isoformat()] == "mutating"


@pytest.mark.parametrize(
    "drift_key",
    [
        availability_pointer_key(LANE_ROOT),
        availability_bootstrap_marker_key(LANE_ROOT),
        availability_retry_path("signal", "observed", DAY),
        availability_retry_quarantine_path("signal", "observed", DAY),
    ],
)
async def test_apply_stops_subsequent_mutations_when_head_bootstrap_or_retry_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift_key: str
) -> None:
    backend, _storage, _day, request, archive, proof, request_sha, proof_sha = _setup(tmp_path, monkeypatch)
    original_put, original_delete = backend.put, backend.delete
    written: list[str] = []
    after_drift: list[str] = []

    def put(key: str, payload: bytes, *, content_type: str) -> None:
        if drift_key in backend.objects:
            after_drift.append(key)
        original_put(key, payload, content_type=content_type)
        if key.startswith(f"{LANE_ROOT}/zoom="):
            written.append(key)
            backend.objects[drift_key] = b"independent state change"

    def delete(key: str) -> None:
        if drift_key in backend.objects:
            after_drift.append(key)
        original_delete(key)

    monkeypatch.setattr(backend, "put", put)
    monkeypatch.setattr(backend, "delete", delete)
    with pytest.raises(ValueError, match=r"separate recovery|retry/quarantine"):
        await OPERATOR.apply(request, archive, request_sha, proof, proof_sha, max_days=1)
    assert written == [partition_path("signal", "observed", 13, DAY)]
    assert after_drift == []
    assert backend.objects[drift_key] == b"independent state change"


async def test_cancel_disarms_the_worker_before_releasing_owner_contexts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = FakeSession()
    backend, _storage, _day, request, archive, proof, request_sha, proof_sha = _setup(
        tmp_path, monkeypatch, owner=owner
    )
    started, released, finished = threading.Event(), threading.Event(), threading.Event()
    original_put, original_delete, original_writer = backend.put, backend.delete, OPERATOR.write_signal_ladder
    written: list[str] = []
    after_cancel: list[str] = []

    def put(key: str, payload: bytes, *, content_type: str) -> None:
        if released.is_set():
            after_cancel.append(key)
        original_put(key, payload, content_type=content_type)
        if key.startswith(f"{LANE_ROOT}/zoom="):
            written.append(key)
            started.set()
            assert released.wait(timeout=5)

    def delete(key: str) -> None:
        if released.is_set():
            after_cancel.append(key)
        original_delete(key)

    def writer(*args: Any, **kwargs: Any) -> None:
        try:
            original_writer(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(backend, "put", put)
    monkeypatch.setattr(backend, "delete", delete)
    monkeypatch.setattr(OPERATOR, "write_signal_ladder", writer)
    task = asyncio.create_task(OPERATOR.apply(request, archive, request_sha, proof, proof_sha, max_days=1))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        released.set()
    assert await asyncio.to_thread(finished.wait, 5)
    assert written == [partition_path("signal", "observed", 13, DAY)]
    assert after_cancel == []
    assert not owner.held.closed
    assert not owner.held.invalidated
