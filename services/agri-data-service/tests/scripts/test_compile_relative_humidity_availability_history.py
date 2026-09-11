"""Focused contract tests for the offline relative-humidity history candidate."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.pipeline.parquet.availability_index import (
    SYSTEM_BOOTSTRAP_SCHEMA_VERSION,
    AvailabilityIdentity,
    AvailabilityIndex,
    AvailabilityPointer,
    AvailabilityRow,
    EvidenceReceipt,
    SourceEvidence,
    StoredAvailabilityObject,
    TerminalEvidence,
    availability_pointer_key,
    build_source_evidence,
    build_terminal_evidence,
    load_publication_request,
)
from agri_data_service.warehouse.schemas.availability_index import (
    AVAILABILITY_REQUIRED_RUNGS,
    AVAILABILITY_SCHEMA_VERSION,
)
from tests.scripts import load_scripts_module

COMPILER: Any = load_scripts_module(
    "compile_relative_humidity_availability_history.py",
    "compile_relative_humidity_availability_history",
)

LANE_ROOT = "layer=climate-field-relative-humidity/kind=observed"
CREATED_AT = datetime(2026, 9, 11, 12, tzinfo=UTC)
SOURCE_CEILING = date(2026, 9, 10)
EXPECTED_READER_CALLS = 2


class ReadOnlyStore:
    """Fake current-availability store that makes any accidental mutation fail."""

    def __init__(self, objects: dict[str, StoredAvailabilityObject]) -> None:
        self.objects = objects
        self.reads: list[str] = []

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        self.reads.append(key)
        value = self.objects.get(key)
        if value is not None:
            assert len(value.payload) <= max_bytes
        return value

    def put_immutable(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline constructor must not upload")

    def compare_and_swap(self, *_args: object, **_kwargs: object) -> bool:
        raise AssertionError("offline constructor must not publish")


def _identity() -> AvailabilityIdentity:
    return AvailabilityIdentity(
        lane_root=LANE_ROOT,
        lane=COMPILER.LANE,
        product=COMPILER.LANE,
        nature="daily_series",
        required_rungs=AVAILABILITY_REQUIRED_RUNGS,
        verified_source_inventory_root="1" * 64,
    )


def _rows(
    identity: AvailabilityIdentity,
    day: date,
    *,
    rungs: tuple[int, ...] = AVAILABILITY_REQUIRED_RUNGS,
) -> tuple[AvailabilityRow, ...]:
    return tuple(
        AvailabilityRow(
            lane=identity.lane,
            product=identity.product,
            nature=identity.nature,
            day=day,
            rung=rung,
            terminal_state="published",
            row_count=1,
            source_receipt=EvidenceReceipt(key="source/marker.json", sha256="2" * 64),
            terminal_receipt=EvidenceReceipt(key=f"terminal/z{rung}.json", sha256="3" * 64),
            data_receipts=(
                EvidenceReceipt(
                    key=(
                        f"{LANE_ROOT}/zoom={rung:02d}/year={day.year:04d}/"
                        f"month={day.month:02d}/day={day.day:02d}/part-0.parquet"
                    ),
                    sha256="4" * 64,
                ),
            ),
            completion_receipt=EvidenceReceipt(
                key=(
                    f"{LANE_ROOT}/zoom={rung:02d}/year={day.year:04d}/"
                    f"month={day.month:02d}/day={day.day:02d}/_complete.json"
                ),
                sha256="5" * 64,
            ),
            absence_reason=None,
            source_ceiling=SOURCE_CEILING,
            published_at=CREATED_AT,
        )
        for rung in rungs
    )


def _pointer(
    identity: AvailabilityIdentity, rows: tuple[AvailabilityRow, ...], bootstrap: EvidenceReceipt
) -> AvailabilityPointer:
    generation_sha256 = "6" * 64
    return AvailabilityPointer(
        schema_version=AVAILABILITY_SCHEMA_VERSION,
        identity=identity,
        required_rungs=identity.required_rungs,
        generation_key=f"{LANE_ROOT}/availability/generation={generation_sha256}/availability.parquet",
        generation_sha256=generation_sha256,
        generation_receipt_sha256="7" * 64,
        generation_bytes=1234,
        rows=len(rows),
        earliest_terminal_day=min(row.day for row in rows),
        latest_terminal_day=max(row.day for row in rows),
        source_ceiling=SOURCE_CEILING,
        prior_generation_key=None,
        prior_generation_sha256=None,
        created_at=CREATED_AT,
        bootstrap_receipt=bootstrap,
    )


def _bootstrap_payload(identity: AvailabilityIdentity) -> bytes:
    return canonical_json(
        {
            "bootstrap_input_sha256": "b" * 64,
            "created_at": "2026-09-11T12:00:00.000000Z",
            "input_receipts": [
                {
                    "key": f"{LANE_ROOT}/availability/evidence/bootstrap-input={'c' * 64}.json",
                    "sha256": "c" * 64,
                }
            ],
            "lane": identity.lane,
            "lane_root": identity.lane_root,
            "nature": identity.nature,
            "outcome_sha256": "d" * 64,
            "product": identity.product,
            "provenance": {
                "digested": {"earliest_day": "2018-01-01", "latest_day": "2018-01-01", "row_count": 4},
                "manifest_trusted": {"earliest_day": None, "latest_day": None, "row_count": 0},
            },
            "required_rungs": list(identity.required_rungs),
            "row_count": 4,
            "schema_version": SYSTEM_BOOTSTRAP_SCHEMA_VERSION,
            "source_ceiling": SOURCE_CEILING.isoformat(),
            "verified_source_inventory_root": identity.verified_source_inventory_root,
        }
    ).encode()


def _current(day: date = date(2018, 1, 1)) -> COMPILER.CurrentAvailability:
    identity = _identity()
    rows = _rows(identity, day)
    bootstrap_payload = _bootstrap_payload(identity)
    bootstrap = EvidenceReceipt(
        key=f"{LANE_ROOT}/availability/bootstrap/receipt={sha256_digest(bootstrap_payload)}.json",
        sha256=sha256_digest(bootstrap_payload),
    )
    pointer = _pointer(identity, rows, bootstrap)
    pointer_payload = canonical_json(pointer.to_wire()).encode()
    return COMPILER.CurrentAvailability(
        index=AvailabilityIndex(pointer=pointer, rows=rows),
        pointer=StoredAvailabilityObject(payload=pointer_payload, etag='"pointer"', version_id="version-1"),
        pointer_sha256=sha256_digest(pointer_payload),
        bootstrap=StoredAvailabilityObject(payload=bootstrap_payload, etag='"bootstrap"', version_id="version-2"),
    )


def _candidate(current: COMPILER.CurrentAvailability, day: date) -> COMPILER.Candidate:
    identity = current.index.pointer.identity
    source = build_source_evidence(
        SourceEvidence(
            identity=identity,
            day=day,
            source_ceiling=SOURCE_CEILING,
            object_receipts=(EvidenceReceipt(key="physical/source-marker.json", sha256="8" * 64),),
        )
    )
    artifacts = [COMPILER.bootstrap.EvidenceArtifactFile(key=source.receipt.key, payload=source.payload)]
    rows: list[AvailabilityRow] = []
    for rung in AVAILABILITY_REQUIRED_RUNGS:
        physical_prefix = f"{LANE_ROOT}/zoom={rung:02d}/year={day.year:04d}/month={day.month:02d}/day={day.day:02d}"
        terminal = TerminalEvidence(
            identity=identity,
            day=day,
            rung=rung,
            terminal_state="published",
            row_count=1,
            source_ceiling=SOURCE_CEILING,
            published_at=CREATED_AT,
            source_receipt=source.receipt,
            data_receipts=(EvidenceReceipt(key=f"{physical_prefix}/part-0.parquet", sha256="9" * 64),),
            completion_receipt=EvidenceReceipt(key=f"{physical_prefix}/_complete.json", sha256="a" * 64),
            absence_receipt=None,
            absence_reason=None,
            provenance="digested",
        )
        artifact = build_terminal_evidence(terminal)
        artifacts.append(COMPILER.bootstrap.EvidenceArtifactFile(key=artifact.receipt.key, payload=artifact.payload))
        rows.append(COMPILER.availability_row_from_terminal_evidence(terminal, terminal_receipt=artifact.receipt))
    compilation = COMPILER.bootstrap.LaneCompilation(
        lane=SimpleNamespace(layer=COMPILER.LANE),
        lane_root=LANE_ROOT,
        source_ceiling=SOURCE_CEILING,
        created_at=CREATED_AT,
        identity=identity,
        rows=tuple(rows),
        artifacts=tuple(artifacts),
        hashed_part_count=len(rows),
        hashed_part_bytes=400,
        marker_read_count=len(rows),
    )
    return COMPILER.Candidate(
        rows=tuple(rows),
        artifacts=tuple(artifacts),
        compilation=compilation,
        target_days=1,
        already_indexed_days=0,
    )


def test_current_identity_reads_pointer_generation_and_bootstrap_without_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _current()
    pointer = current.index.pointer
    store = ReadOnlyStore(
        {
            availability_pointer_key(LANE_ROOT): current.pointer,
            pointer.bootstrap_receipt.key: current.bootstrap,
        }
    )
    monkeypatch.setattr(COMPILER, "read_latest_availability", lambda *_args, **_kwargs: current.index)

    reread = COMPILER._read_current(store, lane_root=LANE_ROOT)

    assert reread == current
    assert store.reads == [availability_pointer_key(LANE_ROOT), pointer.bootstrap_receipt.key]


def test_missing_window_omits_complete_days_and_refuses_partial_days(monkeypatch: pytest.MonkeyPatch) -> None:
    first = date(1981, 1, 1)
    last = date(1981, 1, 2)
    monkeypatch.setattr(COMPILER, "FIRST_DAY", first)
    monkeypatch.setattr(COMPILER, "LAST_DAY", last)
    monkeypatch.setattr(COMPILER, "EXPECTED_TARGET_DAYS", 2)
    current = _current(first)

    assert COMPILER._missing_days(current.index) == ((last,), 1)

    partial_index = replace(current.index, rows=current.index.rows[:1])
    with pytest.raises(COMPILER.HistoryCompilationError, match="partially covers"):
        COMPILER._missing_days(partial_index)


def test_candidate_round_trips_offline_and_refuses_local_overwrite(tmp_path: Path) -> None:
    current = _current()
    candidate = _candidate(current, date(1981, 1, 1))
    chunks = COMPILER._publication_chunks(candidate, current=current)
    out = tmp_path / "candidate"

    receipt = COMPILER._write_candidate(
        out,
        current=current,
        candidate=candidate,
        publication_chunks=chunks,
        timings={"bind": 1.25},
        peak_memory_bytes=2048,
        workers=4,
    )
    request = load_publication_request(
        out / COMPILER.INPUT_FILE_NAME,
        expected_sha256=chunks[0].sha256,
        expected_row_count=len(candidate.rows),
    )

    assert receipt["apply_authorized"] is False
    assert {row.provenance for row in request.rows} == {"digested"}
    assert len(tuple((out / COMPILER.EVIDENCE_DIRECTORY_NAME).rglob("*.json"))) == len(candidate.artifacts)
    assert COMPILER._require_local_evidence(out, request.rows)[0] == len(candidate.artifacts)
    assert len(tuple((out / COMPILER.PRIOR_DIRECTORY_NAME).glob("pointer=*.json"))) == 1
    COMPILER._write_receipt(out, receipt)
    assert len(tuple(out.glob("receipt=*.json"))) == 1
    with pytest.raises(FileExistsError):
        COMPILER._write_candidate(
            out,
            current=current,
            candidate=candidate,
            publication_chunks=chunks,
            timings={},
            peak_memory_bytes=0,
            workers=4,
        )


def test_publication_chunks_keep_complete_days_under_the_input_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _current()
    one_day = _candidate(current, date(1981, 1, 1))
    second_day = _candidate(current, date(1981, 1, 2))
    candidate = replace(
        one_day,
        rows=(*one_day.rows, *second_day.rows),
        target_days=2,
    )
    monkeypatch.setattr(COMPILER, "MAX_DAYS_PER_INPUT", 1)
    monkeypatch.setattr(COMPILER, "EXPECTED_TARGET_DAYS", 2)

    chunks = COMPILER._publication_chunks(candidate, current=current)
    raw_payloads = COMPILER._raw_publication_payloads(COMPILER._publication_document(candidate, current=current))

    assert [len(chunk.rows) for chunk in chunks] == [4, 4]
    assert [chunk.rows[0].day for chunk in chunks] == [date(1981, 1, 1), date(1981, 1, 2)]
    assert all(len(chunk.payload) <= COMPILER.MAX_INPUT_BYTES for chunk in chunks)
    assert raw_payloads == tuple(chunk.payload for chunk in chunks)


def test_resume_oversized_revalidates_and_records_immutable_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery_day = date(1981, 1, 1)
    monkeypatch.setattr(COMPILER, "FIRST_DAY", recovery_day)
    monkeypatch.setattr(COMPILER, "LAST_DAY", recovery_day)
    monkeypatch.setattr(COMPILER, "EXPECTED_TARGET_DAYS", 1)
    monkeypatch.setattr(COMPILER, "MAX_DAYS_PER_INPUT", 1)
    current = _current()
    candidate = _candidate(current, recovery_day)
    source = tmp_path / "oversized"
    COMPILER._write_candidate(
        source,
        current=current,
        candidate=candidate,
        publication_chunks=COMPILER._publication_chunks(candidate, current=current),
        timings={},
        peak_memory_bytes=0,
        workers=1,
    )
    oversized = canonical_json(COMPILER._publication_document(candidate, current=current)).encode()
    (source / COMPILER.OVERSIZED_INPUT_FILE_NAME).write_bytes(oversized)
    monkeypatch.setattr(COMPILER, "_read_current", lambda *_args, **_kwargs: current)
    out = tmp_path / "resumed"

    receipt = COMPILER._resume_oversized(
        source,
        out,
        store=cast("COMPILER.BotoAvailabilityStorage", object()),
        workers=2,
    )

    bootstrap = current.index.pointer.bootstrap_receipt
    assert receipt["apply_authorized"] is False
    assert receipt["bootstrap_receipt"]["key"] == bootstrap.key
    assert receipt["bootstrap_receipt"]["sha256"] == bootstrap.sha256
    assert receipt["publication_identity"]["verified_source_inventory_root"] == "1" * 64
    assert receipt["candidate"]["publication_row_count"] == len(candidate.rows)
    assert len(tuple((out / COMPILER.PRIOR_DIRECTORY_NAME).glob("bootstrap-receipt=*.json"))) == 1
    assert (
        load_publication_request(
            out / COMPILER.INPUT_FILE_NAME,
            expected_sha256=receipt["candidate"]["inputs"][0]["sha256"],
            expected_row_count=len(candidate.rows),
        ).rows
        == candidate.rows
    )


def test_compatible_forward_pointer_movement_rebases_without_rehashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery_day = date(1981, 1, 1)
    monkeypatch.setattr(COMPILER, "FIRST_DAY", recovery_day)
    monkeypatch.setattr(COMPILER, "LAST_DAY", recovery_day)
    monkeypatch.setattr(COMPILER, "EXPECTED_TARGET_DAYS", 1)
    compiled_against = _current()
    candidate = _candidate(compiled_against, recovery_day)
    identity = compiled_against.index.pointer.identity
    latest_rows = (*compiled_against.index.rows, *_rows(identity, date(2018, 1, 2)))
    generation_sha256 = "e" * 64
    latest_pointer = replace(
        compiled_against.index.pointer,
        generation_key=f"{LANE_ROOT}/availability/generation={generation_sha256}/availability.parquet",
        generation_sha256=generation_sha256,
        generation_receipt_sha256="f" * 64,
        rows=len(latest_rows),
        latest_terminal_day=date(2018, 1, 2),
    )
    latest_payload = canonical_json(latest_pointer.to_wire()).encode()
    latest = replace(
        compiled_against,
        index=AvailabilityIndex(pointer=latest_pointer, rows=latest_rows),
        pointer=StoredAvailabilityObject(
            payload=latest_payload,
            etag='"new-pointer"',
            version_id="version-3",
        ),
        pointer_sha256=sha256_digest(latest_payload),
    )
    monkeypatch.setattr(COMPILER, "_read_current", lambda *_args, **_kwargs: latest)

    refreshed, rebased = COMPILER._refresh_compatible_current(
        object(),
        lane_root=LANE_ROOT,
        compiled_against=compiled_against,
        candidate=candidate,
    )

    assert rebased is True
    assert refreshed == latest

    incompatible = replace(
        latest,
        index=replace(
            latest.index,
            pointer=replace(latest_pointer, source_ceiling=date(2026, 9, 11)),
        ),
    )
    monkeypatch.setattr(COMPILER, "_read_current", lambda *_args, **_kwargs: incompatible)
    with pytest.raises(COMPILER.HistoryCompilationError, match="changed incompatibly"):
        COMPILER._refresh_compatible_current(
            object(),
            lane_root=LANE_ROOT,
            compiled_against=compiled_against,
            candidate=candidate,
        )


def test_reader_retries_transient_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class FlakyReader:
        prefix = "warehouse"

        def read(self, _relative_path: str) -> bytes:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("transient response failure")
            return b"verified"

    monkeypatch.setattr(COMPILER.time, "sleep", lambda _seconds: None)
    reader = COMPILER.RetryingBucketReader(
        cast("COMPILER.bootstrap.BucketReader", FlakyReader()),
        attempts=2,
    )

    assert reader.read("part.parquet") == b"verified"
    assert reader.retry_count == 1
    assert calls == EXPECTED_READER_CALLS


def test_arguments_bound_concurrency_and_require_a_fresh_output_path(tmp_path: Path) -> None:
    fresh = tmp_path / "fresh"

    assert (
        COMPILER._arguments(["--out", str(fresh), "--workers", str(COMPILER.MAX_WORKERS)]).workers
        == COMPILER.MAX_WORKERS
    )
    with pytest.raises(SystemExit):
        COMPILER._arguments(["--out", str(fresh), "--workers", "25"])
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(SystemExit):
        COMPILER._arguments(["--out", str(existing)])
    with pytest.raises(SystemExit):
        COMPILER._arguments(["--out", str(tmp_path / "other"), "--resume-from-oversized", str(tmp_path / "missing")])
    resume_source = tmp_path / "resume-source"
    resume_source.mkdir()
    with pytest.raises(SystemExit):
        COMPILER._arguments(
            [
                "--out",
                str(resume_source / COMPILER.PRIOR_DIRECTORY_NAME / "nested"),
                "--resume-from-oversized",
                str(resume_source),
            ]
        )
