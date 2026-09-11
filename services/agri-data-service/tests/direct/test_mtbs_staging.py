"""Current snapshots stay staged until eligible and preserve evidence across retries."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.pipeline.direct.burn_severity import capture, stage
from agri_data_service.pipeline.direct.burn_severity.current_snapshot import canonical_bytes, digest
from agri_data_service.pipeline.direct.burn_severity.publish_snapshot import _reproduce_stage, publish_stage
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
)
from agri_data_service.pipeline.parquet.derivation import govern_day_absent
from agri_data_service.pipeline.parquet.objectstore import ObjectStore, ParquetWriteError
from tests.direct.test_mtbs_current_snapshot import feature
from tests.parquet.test_availability_extension import LaneAvailabilityStorage, LoggingBackend
from tests.scripts import load_scripts_module

if TYPE_CHECKING:
    from pathlib import Path


def empty_capture(monkeypatch: pytest.MonkeyPatch, path: Path, *, empty: bool = True) -> str:
    original = httpx.Client

    def respond(request: httpx.Request) -> httpx.Response:
        selected = not empty and request.url.params["where"] == "year = 2025"
        if request.url.params.get("returnCountOnly"):
            return httpx.Response(200, json={"count": int(selected)})
        rows = [feature(2025)] if selected else []
        if request.url.params["returnGeometry"] == "false":
            rows = [{"attributes": row["properties"]} for row in rows]
        return httpx.Response(200, json={"features": rows})

    monkeypatch.setattr(
        capture.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)
    )
    return capture.capture_snapshot(path)["manifest_sha256"]


def test_stage_reproduces_empty_ladder_and_does_not_publish(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    identity = empty_capture(monkeypatch, tmp_path / "capture")
    capture.prepare_capture(tmp_path / "capture", identity, tmp_path / "prepared")
    log: list[str] = []
    storage = LaneAvailabilityStorage(LoggingBackend(log), log)
    result = stage.stage_prepared(
        storage, capture=tmp_path / "capture", prepared=tmp_path / "prepared", manifest_sha256=identity
    )
    assert result["status"] == "staged"
    manifest, prepared = stage.load_stage(storage, identity)
    assert all(rung["rows"] == 0 for rung in prepared["rungs"])
    available = date.fromisoformat(manifest["available_day"])
    _, queue = stage.read_queue(storage)
    assert stage.eligible_stage(queue, available - timedelta(days=1)) is None
    assert stage.eligible_stage(queue, available)["manifest_sha256"] == identity
    assert not stage.capture_due(queue, available)
    assert stage.capture_due(queue, available + timedelta(days=6))
    assert all("/zoom=" not in key for key in storage.backend.objects)
    before = set(storage.backend.objects)
    assert stage.record_unchanged_capture(storage, capture=tmp_path / "capture", identity=identity)
    assert set(storage.backend.objects) == before
    assert len(stage.read_queue(storage)[1]["pending"]) == 1


def test_reproduction_refuses_changed_candidate_before_publication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    identity = empty_capture(monkeypatch, tmp_path / "capture")
    capture.prepare_capture(tmp_path / "capture", identity, tmp_path / "prepared")
    log: list[str] = []
    storage = LaneAvailabilityStorage(LoggingBackend(log), log)
    stage.stage_prepared(
        storage, capture=tmp_path / "capture", prepared=tmp_path / "prepared", manifest_sha256=identity
    )
    manifest, prepared = stage.load_stage(storage, identity)
    prepared["rungs"][0]["sha256"] = "a" * 64
    before = dict(storage.backend.objects)
    with pytest.raises(ValueError, match="does not derive"):
        _reproduce_stage(storage, manifest, prepared)
    assert storage.backend.objects == before


@pytest.mark.asyncio
async def test_future_stage_never_touches_session_or_physical_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    day = datetime.now(UTC).date() + timedelta(days=1)
    monkeypatch.setattr(
        "agri_data_service.pipeline.direct.burn_severity.publish_snapshot.load_stage",
        lambda *_args: ({"available_day": day.isoformat()}, {}),
    )
    result = await publish_stage(None, None, None, identity="a" * 64, today=day - timedelta(days=1))
    assert result["status"] == "staged_not_yet_available"


def test_queue_rejects_duplicate_day_and_excessive_pending_entries() -> None:
    log: list[str] = []
    storage = LaneAvailabilityStorage(LoggingBackend(log), log)
    _, queue = stage.read_queue(storage)
    queue["pending"] = [{"manifest_sha256": f"{number:064x}", "available_day": "2026-09-11"} for number in range(2)]
    storage.backend.objects[stage.QUEUE_KEY] = canonical_bytes(queue)
    with pytest.raises(ValueError, match="duplicate"):
        stage.read_queue(storage)
    queue["pending"] *= 5
    storage.backend.objects[stage.QUEUE_KEY] = json.dumps(queue).encode()
    with pytest.raises(ValueError, match="excessive"):
        stage.read_queue(storage)


class PinnedSession:
    def __init__(self) -> None:
        self.held = SimpleNamespace(
            invalidated=False,
            closed=False,
            sync_connection=SimpleNamespace(
                invalidated=False, closed=False, connection=SimpleNamespace(driver_connection=object())
            ),
        )

    async def connection(self) -> Any:
        return self.held

    async def execute(self, _statement: object, _params: object = None) -> Any:
        return SimpleNamespace(scalar=lambda: True, scalar_one=lambda: 123)

    async def rollback(self) -> None:
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize(("empty", "interrupt_base"), [(False, False), (True, False), (False, True)])
async def test_ordinary_publication_indexes_all_rungs_and_refuses_lost_physical_evidence(  # noqa: PLR0915 - real historical bootstrap and resumable publication fixture
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, empty: bool, interrupt_base: bool
) -> None:
    identity = empty_capture(monkeypatch, tmp_path / "capture", empty=empty)
    capture.prepare_capture(tmp_path / "capture", identity, tmp_path / "prepared")
    log: list[str] = []
    storage = LaneAvailabilityStorage(LoggingBackend(log), log)
    store = ObjectStore(storage.backend)
    stage.stage_prepared(
        storage, capture=tmp_path / "capture", prepared=tmp_path / "prepared", manifest_sha256=identity
    )
    manifest, _ = stage.load_stage(storage, identity)
    day = date.fromisoformat(manifest["available_day"])
    now = datetime.combine(day, datetime.min.time(), UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            del tz
            return now

    monkeypatch.setattr("agri_data_service.pipeline.direct.burn_severity.publish_snapshot.datetime", Clock)
    root = "layer=burn-severity/kind=observed"
    source_key = f"{root}/availability/test-inventory.json"
    storage.put_immutable(source_key, b"{}", content_type="application/json")
    source = EvidenceReceipt(key=source_key, sha256=digest(b"{}"))
    index_identity = AvailabilityIdentity(
        lane_root=root,
        lane="burn-severity",
        product="burn-severity",
        nature="release_series",
        required_rungs=(0, 5, 9, 13),
        verified_source_inventory_root=compute_verified_source_inventory_root((source,)),
    )
    inventory = build_bootstrap_inventory_evidence(
        BootstrapInventoryEvidence(identity=index_identity, source_ceiling=day, object_receipts=(source,))
    )
    storage.put_immutable(inventory.receipt.key, inventory.payload, content_type="application/json")
    historical_day = date(2024, 8, 22)
    historical_time = datetime(2024, 8, 22, tzinfo=UTC)
    historical_absence = GovernedAbsence(
        reason="fixture complete historical cohort has no regional fires",
        upstream_response="complete bounded fixture source returned zero fires",
        recorded_at=historical_time,
        run_id="historical-fixture",
    )
    with store.recording_written_objects() as historical_ledger:
        govern_day_absent(store, historical_absence, layer="burn-severity", kind="observed", day=historical_day)
    historical = FinalizedLaneDay(
        terminal_state="governed_absence",
        day=historical_day,
        written=historical_ledger,
        source=LaneDaySource(
            origin="historical-fixture",
            run_id="historical-fixture",
            row_count=0,
            part_count=0,
            exported_at=historical_time,
        ),
        published_at=historical_time,
        source_ceiling=day,
        absence_reason=historical_absence.reason,
    )
    claim = _claim_from_finalized(historical, lane="burn-severity", kind="observed", lane_root=root, day=historical_day)
    assert isinstance(claim, _DayClaim)
    seed = _prepare_day(
        SimpleNamespace(pointer=SimpleNamespace(identity=index_identity, required_rungs=(0, 5, 9, 13)), rows=()), claim
    )
    assert isinstance(seed, _PreparedDay)
    assert seed.source_object_key is not None
    assert seed.source_object_payload is not None
    storage.put_immutable(seed.source_object_key, seed.source_object_payload, content_type="application/json")
    for artifact in seed.artifacts:
        storage.put_immutable(artifact.receipt.key, artifact.payload, content_type="application/json")
    _bootstrap_availability_owned(
        storage,
        BootstrapRequest(
            identity=index_identity,
            source_ceiling=day,
            created_at=now,
            input_receipts=(inventory.receipt,),
            rows=seed.rows,
            input_sha256=digest(b"input"),
        ),
    )
    attempt_key = f"{stage.ROOT}/mtbs-staging/attempts/{identity}.json"
    first_attempt = None
    if interrupt_base:
        original_put = storage.backend.put

        def interrupted_put(key: str, payload: bytes, *, content_type: str) -> None:
            original_put(key, payload, content_type=content_type)
            if "/zoom=13/" in key and key.endswith(".parquet"):
                raise RuntimeError("interrupted after durable base part")

        monkeypatch.setattr(storage.backend, "put", interrupted_put)
        with pytest.raises(ValueError, match="ordinary snapshot writer refused"):
            await publish_stage(PinnedSession(), store, storage, identity=identity, today=day)
        first_attempt = storage.backend.objects[attempt_key]
        parts = [key for key in storage.backend.objects if "/zoom=" in key and key.endswith(".parquet")]
        assert len(parts) == 1
        assert "/zoom=13/" in parts[0]
        assert stage.read_queue(storage)[1]["pending"]
        monkeypatch.setattr(storage.backend, "put", original_put)
    result = await publish_stage(PinnedSession(), store, storage, identity=identity, today=day)
    assert result["status"] == "published"
    if first_attempt is not None:
        assert storage.backend.objects[attempt_key] == first_attempt
    assert stage.read_queue(storage)[1]["pending"] == []
    physical = [
        key
        for key in storage.backend.objects
        if f"/day={day.day:02d}/" in key
        and "/zoom=" in key
        and (key.endswith(".parquet") if not empty else key.endswith("absent.json"))
    ]
    assert physical
    del storage.backend.objects[physical[0]]
    expected_error = ValueError if empty else ParquetWriteError
    with pytest.raises(expected_error, match=r"lost|missing|part|absence"):
        await publish_stage(PinnedSession(), store, storage, identity=identity, today=day)


@pytest.mark.parametrize("missing", [False, True])
def test_adoption_preview_verifies_supplied_candidate_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing: bool
) -> None:
    identity = empty_capture(monkeypatch, tmp_path / "capture")
    prepared = tmp_path / "prepared"
    receipt = capture.prepare_capture(tmp_path / "capture", identity, prepared)
    original_receipt = (prepared / "preparation.json").read_bytes()
    blob = prepared / "blobs" / receipt["rungs"][0]["sha256"]
    if missing:
        blob.unlink()
    else:
        blob.write_bytes(b"changed candidate")
    operator = load_scripts_module("stage_mtbs_current_snapshot.py", "stage_mtbs_current_snapshot_preview_test")
    monkeypatch.setattr(
        operator.threading,
        "Timer",
        lambda *_args, **_kwargs: SimpleNamespace(daemon=False, start=lambda: None, cancel=lambda: None),
    )
    args = SimpleNamespace(stage=False, capture=tmp_path / "capture", prepared=prepared, manifest_sha256=identity)
    expected = FileNotFoundError if missing else ValueError
    with pytest.raises(expected, match=r"No such file|cannot find|candidate bytes"):
        operator._run(args)
    assert (prepared / "preparation.json").read_bytes() == original_receipt
