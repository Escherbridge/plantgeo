"""Candidate admission preserves old bytes and refuses unknown transition, ownership or availability state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import completion_marker_path, partition_path
from agri_data_service.pipeline.parquet import signal_coordinate_correction as correction
from agri_data_service.pipeline.parquet.availability_index import (
    availability_bootstrap_marker_key,
    availability_pointer_key,
)
from agri_data_service.pipeline.parquet.derivation import DERIVED_ROWS_PER_PART
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.parquet.signal_coordinate_correction import (
    LANE_ROOT,
    GuardedSignalBackend,
    SignalMemory,
    SignalRepairDay,
    read_signal_candidate,
    read_signal_scope,
    require_unbootstrapped,
    verify_signal_physical,
    verify_signal_quiescence,
    verify_signal_transition,
    write_signal_ladder,
)
from agri_data_service.pipeline.parquet.signal_coordinate_preview import build_coordinate_candidate, parquet_bytes
from tests.parquet.test_availability_extension import LaneAvailabilityStorage, LoggingBackend
from tests.parquet.test_signal_coordinate_preview import DAY, _dimension
from tests.parquet.test_signal_rewrite import _legacy_table

if TYPE_CHECKING:
    from collections.abc import Mapping

STAMP = datetime(2026, 9, 11, tzinfo=UTC)


def _prepared() -> SignalRepairDay:
    base = _legacy_table(DAY)
    candidate = dict(build_coordinate_candidate(base, _dimension(), day=DAY).tables)[13]
    originals = {
        partition_path("signal", "observed", 13, DAY): parquet_bytes(base),
        completion_marker_path("signal", "observed", 13, DAY): PartitionCompletion(
            part_count=1, row_count=1, completed_at=STAMP, run_id="old"
        ).to_json_bytes(),
    }
    memory = SignalMemory({})
    write_signal_ladder(ObjectStore(memory), candidate, day=DAY, completed_at=STAMP, run_id="prepared")
    return SignalRepairDay(DAY, originals, (), parquet_bytes(candidate), memory.objects)


def _bucket(objects: Mapping[str, bytes]) -> tuple[LoggingBackend, LaneAvailabilityStorage]:
    log: list[str] = []
    backend = LoggingBackend(log)
    backend.objects.update(objects)
    return backend, LaneAvailabilityStorage(backend, log)


def test_ordinary_signal_publication_matches_prepared_bytes_and_all_part_digests() -> None:
    prepared = _prepared()
    backend, storage = _bucket(prepared.originals)
    guarded = GuardedSignalBackend(backend, storage, prepared, lambda: None)
    write_signal_ladder(
        ObjectStore(guarded), read_signal_candidate(prepared), day=DAY, completed_at=STAMP, run_id="prepared"
    )
    verify_signal_physical(prepared, backend.objects)
    assert len(backend.objects) == len({0, 5, 9, 13}) * 2
    base = PartitionCompletion.from_json_bytes(backend.objects[completion_marker_path("signal", "observed", 13, DAY)])
    assert base.parts[0].sha256 == sha256_digest(backend.objects[partition_path("signal", "observed", 13, DAY)])


def test_dense_signal_rungs_keep_every_ordinary_part_and_receipt() -> None:
    template = _legacy_table(DAY)
    seed = template.to_pylist()[0]
    base = pa.Table.from_pylist(
        [{**seed, "signal_name": f"signal_{index:05d}"} for index in range(DERIVED_ROWS_PER_PART + 1)],
        schema=template.schema,
    )
    candidate = dict(build_coordinate_candidate(base, _dimension(), day=DAY).tables)[13]
    memory = SignalMemory({})
    write_signal_ladder(ObjectStore(memory), candidate, day=DAY, completed_at=STAMP, run_id="dense")
    prepared = SignalRepairDay(DAY, {}, (), parquet_bytes(candidate), memory.objects)
    verify_signal_physical(prepared, memory.objects)
    for rung in (0, 5, 9):
        marker = PartitionCompletion.from_json_bytes(
            memory.objects[completion_marker_path("signal", "observed", rung, DAY)]
        )
        assert [part.row_count for part in marker.parts] == [DERIVED_ROWS_PER_PART, 1]


def test_unknown_transition_or_completion_bytes_stop_before_mutation() -> None:
    prepared = _prepared()
    key = completion_marker_path("signal", "observed", 13, DAY)
    current = {**prepared.originals, key: b"foreign completion"}
    backend, storage = _bucket(current)
    guarded = GuardedSignalBackend(backend, storage, prepared, lambda: None)
    with pytest.raises(ValueError, match="unknown signal"):
        verify_signal_transition(prepared, current)
    with pytest.raises(ValueError, match="object changed"):
        guarded.delete(key)
    assert backend.objects == current
    with pytest.raises(ValueError, match="escaped completion"):
        guarded.delete(partition_path("signal", "observed", 13, DAY))


def test_known_partial_transition_can_be_replayed_through_the_same_ordinary_writer() -> None:
    prepared = _prepared()
    key = partition_path("signal", "observed", 13, DAY)
    backend, storage = _bucket({key: prepared.objects[key]})
    verify_signal_transition(prepared, backend.objects)
    write_signal_ladder(
        ObjectStore(GuardedSignalBackend(backend, storage, prepared, lambda: None)),
        read_signal_candidate(prepared),
        day=DAY,
        completed_at=STAMP,
        run_id="prepared",
    )
    verify_signal_physical(prepared, backend.objects)


def test_scope_refuses_unknown_keys_without_fetching_their_bytes() -> None:
    prepared = _prepared()
    unknown = partition_path("signal", "observed", 0, DAY, 99)
    backend, storage = _bucket({**prepared.originals, unknown: b"foreign"})
    with pytest.raises(ValueError, match="unexpected signal object"):
        read_signal_scope(backend, storage, prepared)
    assert unknown not in storage.reads


def test_scope_applies_pinned_per_key_and_aggregate_byte_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    prepared = _prepared()
    key = partition_path("signal", "observed", 13, DAY)
    limit = max(len(prepared.originals[key]), len(prepared.objects[key]))
    backend, storage = _bucket({key: b"x" * (limit + 1)})
    with pytest.raises(AssertionError, match="byte ceiling"):
        read_signal_scope(backend, storage, prepared)
    assert storage.reads == [key]
    storage.reads.clear()
    monkeypatch.setattr(correction, "MAX_DAY_BYTES", 1)
    with pytest.raises(ValueError, match="prepared signal day exceeds byte budget"):
        read_signal_scope(backend, storage, prepared)
    assert storage.reads == []


@pytest.mark.parametrize("key", [availability_pointer_key(LANE_ROOT), availability_bootstrap_marker_key(LANE_ROOT)])
def test_existing_head_or_lost_pointer_marker_is_a_different_recovery_branch(key: str) -> None:
    _, storage = _bucket({key: b"existing availability"})
    with pytest.raises(ValueError, match="separate recovery"):
        require_unbootstrapped(storage)


def test_every_mutation_requires_current_owner_and_exact_prepared_bytes() -> None:
    prepared = _prepared()
    backend, storage = _bucket(prepared.originals)
    key = partition_path("signal", "observed", 13, DAY)

    def lost_owner() -> None:
        raise ValueError("lock lost")

    guarded = GuardedSignalBackend(backend, storage, prepared, lost_owner)
    with pytest.raises(ValueError, match="lock lost"):
        guarded.put(key, prepared.objects[key], content_type="application/vnd.apache.parquet")
    assert backend.objects == prepared.originals
    guarded.guard = lambda: None
    with pytest.raises(ValueError, match="prepared signal bytes"):
        guarded.put(key, b"unexpected data", content_type="application/vnd.apache.parquet")
    assert backend.objects == prepared.originals


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("writers_stopped", False, "proof must cover writers and retry workers"),
        ("no_inflight_retry_workers", False, "proof must cover writers and retry workers"),
        ("request_sha256", "foreign", "proof scope differs"),
        ("operator", " ", "expected a nonempty evidence string"),
    ],
)
def test_quiescence_requires_explicit_current_evidence(field: str, value: object, message: str) -> None:
    proof: dict[str, object] = {
        "schema_version": "signal-correction-quiescence/v1",
        "lane_root": LANE_ROOT,
        "request_sha256": "request",
        "writers_stopped": True,
        "no_inflight_retry_workers": True,
        "operator": "reviewer",
        "evidence": "captured facts",
        "observed_at": STAMP.isoformat(),
        "valid_until": (STAMP + timedelta(minutes=30)).isoformat(),
    }
    verify_signal_quiescence(proof, request_sha="request", now=STAMP)
    with pytest.raises(ValueError, match="expired"):
        verify_signal_quiescence(proof, request_sha="request", now=STAMP + timedelta(hours=1))
    proof[field] = value
    with pytest.raises(ValueError, match=message):
        verify_signal_quiescence(proof, request_sha="request", now=STAMP)
