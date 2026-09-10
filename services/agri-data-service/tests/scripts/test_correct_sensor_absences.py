"""Run the governed operator against one shared fake bucket and a pinned fake SQL session."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.pipeline.parquet.availability_extension import (
    FinalizedLaneDay,
    LaneDaySource,
    _claim_from_finalized,
    _DayClaim,
    _prepare_day,
    _PreparedDay,
)
from agri_data_service.pipeline.parquet.availability_index import (
    AvailabilityIdentity,
    BootstrapInventoryEvidence,
    BootstrapRequest,
    EvidenceReceipt,
    _bootstrap_availability_owned,
    build_bootstrap_inventory_evidence,
    compute_verified_source_inventory_root,
    read_latest_availability,
)
from agri_data_service.pipeline.parquet.gap_fill import zero_row_absence
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.parquet.sensor_absence_correction import (
    DAYS,
    LANE_ROOT,
    RUNGS,
    encode,
    verify_generation_binding,
    verify_index_row,
)
from tests.parquet.test_availability_extension import LaneAvailabilityStorage, LoggingBackend
from tests.parquet.test_sensor_absence_correction import _candidate
from tests.scripts import load_scripts_module

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from agri_data_service.pipeline.parquet.availability_index import AvailabilityIndex

OPERATOR: Any = load_scripts_module("correct_sensor_absences.py", "correct_sensor_absences")


class FakeSession:
    """Every SQL call is recorded locally; no database engine exists."""

    def __init__(self) -> None:
        self.driver = object()
        self.held = SimpleNamespace(
            invalidated=False,
            closed=False,
            sync_connection=SimpleNamespace(connection=SimpleNamespace(driver_connection=self.driver)),
        )

    async def connection(self) -> Any:
        return self.held

    async def execute(self, statement: object, params: object = None) -> Any:
        del statement, params
        return SimpleNamespace(scalar=lambda: True, scalar_one=lambda: 123)

    async def rollback(self) -> None:
        return None


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, Path, Path, str]:  # noqa: PLR0915
    log: list[str] = []
    backend = LoggingBackend(log)
    bucket: Any = backend
    bucket.bucket = "fixture-bucket"
    storage = LaneAvailabilityStorage(backend, log)
    store = ObjectStore(backend)
    old_time = datetime(2026, 9, 7, tzinfo=UTC)
    inventory_key = f"{LANE_ROOT}/availability/original-input.json"
    storage.put_immutable(inventory_key, b"original inventory", content_type="application/json")
    inventory_receipt = EvidenceReceipt(key=inventory_key, sha256=sha256_digest(b"original inventory"))
    identity = AvailabilityIdentity(
        lane_root=LANE_ROOT,
        lane="sensors",
        product="sensors",
        nature="daily_series",
        required_rungs=RUNGS,
        verified_source_inventory_root=compute_verified_source_inventory_root((inventory_receipt,)),
    )
    empty_index = cast(
        "AvailabilityIndex", SimpleNamespace(pointer=SimpleNamespace(identity=identity, required_rungs=RUNGS), rows=())
    )
    rows = []
    captured = []
    for day in DAYS:
        with store.recording_written_objects() as ledger:
            for rung in RUNGS:
                store.write_absence(
                    zero_row_absence(
                        "sensors",
                        zoom=rung,
                        day=day,
                        run_id="old",
                        observed="empty obsolete export",
                        recorded_at=old_time,
                    ),
                    layer="sensors",
                    kind="observed",
                    zoom=rung,
                    day=day,
                )
        for receipt in ledger.absences.values():
            held = storage.read(receipt.relative_path, max_bytes=100_000)
            assert held is not None
            captured.append(
                {
                    "key": receipt.relative_path,
                    "present": True,
                    "sha256": receipt.sha256,
                    "etag": held.etag,
                    "version_id": None,
                }
            )
        absence = store.read_absence("sensors", "observed", 13, day)
        assert absence is not None
        outcome = FinalizedLaneDay(
            terminal_state="governed_absence",
            day=day,
            written=ledger,
            source=LaneDaySource(origin="old", run_id="old", row_count=0, part_count=0, exported_at=old_time),
            published_at=old_time,
            source_ceiling=DAYS[1],
            absence_reason=absence.reason,
        )
        claim = _claim_from_finalized(outcome, lane="sensors", kind="observed", lane_root=LANE_ROOT, day=day)
        assert isinstance(claim, _DayClaim)
        prepared = _prepare_day(empty_index, claim)
        assert isinstance(prepared, _PreparedDay)
        assert prepared.source_object_key is not None
        assert prepared.source_object_payload is not None
        storage.put_immutable(
            prepared.source_object_key, prepared.source_object_payload, content_type="application/json"
        )
        for artifact in prepared.artifacts:
            storage.put_immutable(artifact.receipt.key, artifact.payload, content_type="application/json")
        rows.extend(prepared.rows)
    inventory = build_bootstrap_inventory_evidence(
        BootstrapInventoryEvidence(identity=identity, source_ceiling=DAYS[1], object_receipts=(inventory_receipt,))
    )
    storage.put_immutable(inventory.receipt.key, inventory.payload, content_type="application/json")
    _bootstrap_availability_owned(
        storage,
        BootstrapRequest(
            identity=identity,
            source_ceiling=DAYS[1],
            created_at=old_time,
            input_receipts=(inventory.receipt,),
            rows=tuple(rows),
            input_sha256=sha256_digest(b"input"),
        ),
    )
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    artifacts = {}
    for day in DAYS:
        data = _candidate(day)
        name = f"sensors-{day}-positive-candidate.parquet"
        (candidate / name).write_bytes(data)
        artifacts[name] = {"sha256": sha256_digest(data), "bytes": len(data)}
    archive = tmp_path / "source.tar.gz"
    archive.write_bytes(b"pinned source fixture")
    archive_sha = sha256_digest(archive.read_bytes())
    manifest = {
        "source_complete": False,
        "archive_sha256": archive_sha,
        "artifacts": artifacts,
        "original_publication_objects": captured,
    }
    manifest_path = candidate / "candidate-manifest.json"
    manifest_path.write_bytes(encode(manifest))
    monkeypatch.setattr(OPERATOR, "CANDIDATE_SHA", sha256_digest(manifest_path.read_bytes()))
    monkeypatch.setattr(OPERATOR, "ARCHIVE_SHA", archive_sha)
    monkeypatch.setattr(OPERATOR, "connect", lambda: (backend, storage, ""))

    @asynccontextmanager
    async def session() -> AsyncIterator[FakeSession]:
        yield FakeSession()

    monkeypatch.setattr(OPERATOR, "ingest_session", session)
    out = tmp_path / "prepared"
    prepared_report = OPERATOR.prepare(manifest_path, archive, out)
    request = out / "request.json"
    proof = tmp_path / "quiescence.json"
    now = datetime.now(UTC)
    proof.write_bytes(
        encode(
            {
                "schema_version": "sensors-correction-quiescence/v1",
                "request_sha256": prepared_report["sha256"],
                "lane_root": LANE_ROOT,
                "writers_stopped": True,
                "no_inflight_retry_workers": True,
                "operator": "fixture",
                "evidence": ["all workers stopped"],
                "observed_at": now.isoformat(),
                "valid_until": (now + timedelta(minutes=30)).isoformat(),
            }
        )
    )
    return backend, storage, request, proof, prepared_report["sha256"]


async def test_apply_runs_real_finalizer_and_extension_then_exact_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, storage, request, proof, digest = _setup(tmp_path, monkeypatch)
    original = dict(backend.objects)
    result = await OPERATOR.apply(request, digest, proof, sha256_digest(proof.read_bytes()))
    assert set(result["days"].values()) == {"indexed"}
    plan = json.loads(request.read_bytes())
    index = read_latest_availability(storage, lane_root=LANE_ROOT)
    for row in index.rows:
        verify_index_row(row, OPERATOR.finalized(plan, row.day, digest), index.pointer.identity)
    first = index.rows[0]
    wrong_terminal = replace(
        first,
        terminal_receipt=EvidenceReceipt(
            key=f"{LANE_ROOT}/availability/evidence/wrong.json", sha256=first.terminal_receipt.sha256
        ),
    )
    with pytest.raises(ValueError, match="terminal wrapper differs"):
        verify_index_row(wrong_terminal, OPERATOR.finalized(plan, first.day, digest), index.pointer.identity)
    for key, payload in original.items():
        if key.endswith("absent.json"):
            assert key not in backend.objects
            assert payload in backend.objects.values()
    repeated = await OPERATOR.apply(request, digest, proof, sha256_digest(proof.read_bytes()))
    assert repeated["days"] == result["days"]


@pytest.mark.parametrize("crash_kind", ["delete", "part", "after_index"])
async def test_resume_after_each_durable_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_kind: str
) -> None:
    backend, storage, request, proof, digest = _setup(tmp_path, monkeypatch)
    fired = False
    original_delete, original_put, original_cas = backend.delete, backend.put, storage.compare_and_swap

    def delete(key: str) -> None:
        nonlocal fired
        original_delete(key)
        if crash_kind == "delete" and key.endswith("absent.json") and not fired:
            fired = True
            raise RuntimeError("simulated crash")

    def put(key: str, payload: bytes, *, content_type: str) -> None:
        nonlocal fired
        original_put(key, payload, content_type=content_type)
        if crash_kind == "part" and key.endswith(".parquet") and not fired:
            fired = True
            raise RuntimeError("simulated crash")

    def cas(key: str, payload: bytes, *, expected_etag: str | None, content_type: str) -> bool:
        nonlocal fired
        if crash_kind == "after_index" and key.endswith("journal.json") and b'"indexed"' in payload and not fired:
            fired = True
            raise RuntimeError("simulated crash")
        return original_cas(key, payload, expected_etag=expected_etag, content_type=content_type)

    monkeypatch.setattr(backend, "delete", delete)
    monkeypatch.setattr(backend, "put", put)
    monkeypatch.setattr(storage, "compare_and_swap", cas)
    with pytest.raises((RuntimeError, ValueError), match=r"simulated crash|ordinary finalizer failed"):
        await OPERATOR.apply(request, digest, proof, sha256_digest(proof.read_bytes()))
    assert fired
    result = await OPERATOR.apply(request, digest, proof, sha256_digest(proof.read_bytes()))
    assert set(result["days"].values()) == {"indexed"}


async def test_invalid_quiescence_causes_no_bucket_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _, request, proof, digest = _setup(tmp_path, monkeypatch)
    before = dict(backend.objects)
    value = json.loads(proof.read_bytes())
    value["no_inflight_retry_workers"] = False
    proof.write_bytes(encode(value))
    with pytest.raises(ValueError, match="quiescence"):
        await OPERATOR.apply(request, digest, proof, sha256_digest(proof.read_bytes()))
    assert backend.objects == before


async def test_changed_original_before_apply_is_refused_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, _, request, proof, digest = _setup(tmp_path, monkeypatch)
    key = next(key for key in backend.objects if key.endswith("absent.json"))
    backend.objects[key] += b"changed"
    before = dict(backend.objects)
    with pytest.raises(ValueError, match="original identity changed"):
        await OPERATOR.apply(request, digest, proof, sha256_digest(proof.read_bytes()))
    assert backend.objects == before


@pytest.mark.parametrize("change", ["ceiling", "old_absence", "extra_proof", "future_clock"])
def test_rehashed_tampered_plan_still_fails_scope_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    _, _, request, _, _ = _setup(tmp_path, monkeypatch)
    plan = json.loads(request.read_bytes())
    source = OPERATOR.load_objects(request.parent, plan["proof"])
    originals = OPERATOR.load_objects(
        request.parent, {key: value["sha256"] for key, value in plan["originals"].items()}
    )
    if change == "ceiling":
        plan["source_ceiling"] = "2099-01-01"
    elif change == "old_absence":
        key = next(key for key in originals if key.endswith("absent.json"))
        originals[key] = b"different absence"
    elif change == "extra_proof":
        source["extra"] = b"unscoped"
    else:
        plan["published_at"] = "2099-01-01T00:00:00+00:00"
    assert sha256_digest(encode(plan))
    with pytest.raises(
        ValueError,
        match=r"source ceiling changed|original absence no longer matches|proof population changed|publication clock",
    ):
        OPERATOR.validate_plan(plan, source, originals, now=datetime.now(UTC))


def test_mutation_backend_checks_ownership_before_any_put_or_delete() -> None:
    log: list[str] = []
    backend = LoggingBackend(log)
    storage = LaneAvailabilityStorage(backend, log)
    key = f"{LANE_ROOT}/zoom=13/year=2026/month=09/day=05/part-0.parquet"

    def lost() -> None:
        raise ValueError("lock connection lost")

    guard = OPERATOR.GuardedBackend(backend, storage, "", {key: b"candidate"}, {}, lost, {})
    with pytest.raises(ValueError, match="lock connection lost"):
        guard.put(key, b"candidate", content_type="application/octet-stream")
    with pytest.raises(ValueError, match="lock connection lost"):
        guard.delete(key)
    assert not log


@pytest.mark.parametrize("changed", ["identity", "bootstrap"])
def test_same_rows_do_not_authorize_a_different_lane_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    _, storage, _, _, _ = _setup(tmp_path, monkeypatch)
    original = read_latest_availability(storage, lane_root=LANE_ROOT)
    if changed == "identity":
        pointer = replace(
            original.pointer, identity=replace(original.pointer.identity, verified_source_inventory_root="b" * 64)
        )
    else:
        pointer = replace(
            original.pointer, bootstrap_receipt=replace(original.pointer.bootstrap_receipt, sha256="b" * 64)
        )
    current = replace(original, pointer=pointer)
    assert current.rows == original.rows
    with pytest.raises(ValueError, match=r"identity changed|bootstrap binding changed"):
        verify_generation_binding(current, original)
