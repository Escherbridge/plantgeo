from __future__ import annotations

import asyncio
import io
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.pipeline.parquet import availability_index as availability_module
from agri_data_service.pipeline.parquet.availability_index import (
    GENERATION_MAX_BYTES,
    MAX_AVAILABILITY_ROWS,
    AvailabilityChecksumError,
    AvailabilityConfig,
    AvailabilityConflictError,
    AvailabilityIdentity,
    AvailabilityMalformedError,
    AvailabilityRow,
    AvailabilityUnavailableError,
    BootstrapInventoryEvidence,
    BootstrapRequest,
    EvidenceReceipt,
    PublicationRequest,
    SourceEvidence,
    StoredAvailabilityObject,
    TerminalEvidence,
    availability_pointer_key,
    build_bootstrap_inventory_evidence,
    build_source_evidence,
    build_terminal_evidence,
    compute_verified_source_inventory_root,
    read_latest_availability,
)
from agri_data_service.pipeline.parquet.availability_index import (
    _bootstrap_availability_owned as bootstrap_availability,
)
from agri_data_service.pipeline.parquet.availability_index import (
    _publish_availability_owned as publish_availability,
)
from agri_data_service.pipeline.parquet.availability_index import (
    _rollback_availability_owned as rollback_availability,
)
from agri_data_service.pipeline.parquet.availability_index import (
    bootstrap_availability as guarded_bootstrap_availability,
)
from agri_data_service.pipeline.parquet.availability_index import (
    publish_availability as guarded_publish_availability,
)
from agri_data_service.pipeline.parquet.availability_index import (
    rollback_availability as guarded_rollback_availability,
)
from agri_data_service.warehouse.schemas.availability_index import AVAILABILITY_REQUIRED_RUNGS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

_LANE_ROOT = "layer=test-lane/kind=observed"
_START = datetime(2026, 9, 1, 12, tzinfo=UTC)


@dataclass
class MemoryAvailabilityStorage:
    objects: dict[str, StoredAvailabilityObject] = field(default_factory=dict)
    read_log: list[str] = field(default_factory=list)
    cas_hook: Callable[[], None] | None = None
    cas_calls: int = 0
    _version: int = 0

    def seed(self, key: str, payload: bytes) -> EvidenceReceipt:
        self._version += 1
        self.objects[key] = StoredAvailabilityObject(
            payload=payload,
            etag=f'"{self._version}"',
            version_id=f"version-{self._version}",
        )
        return EvidenceReceipt(key=key, sha256=sha256_digest(payload))

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        self.read_log.append(key)
        stored = self.objects.get(key)
        if stored is not None and len(stored.payload) > max_bytes:
            raise AvailabilityUnavailableError("availability_oversized", key)
        return stored

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        del content_type
        existing = self.objects.get(key)
        if existing is not None:
            if existing.payload != payload:
                raise AvailabilityConflictError("immutable conflict")
            return
        self.seed(key, payload)

    def compare_and_swap(
        self,
        key: str,
        payload: bytes,
        *,
        expected_etag: str | None,
        content_type: str,
    ) -> bool:
        del content_type
        self.cas_calls += 1
        hook = self.cas_hook
        if hook is not None:
            self.cas_hook = None
            hook()
        existing = self.objects.get(key)
        if expected_etag is None:
            if existing is not None:
                return False
        elif existing is None or existing.etag != expected_etag:
            return False
        self.seed(key, payload)
        return True


@dataclass
class CorruptingGenerationStorage(MemoryAvailabilityStorage):
    corrupt_next_generation_read: bool = True

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        stored = super().read(key, max_bytes=max_bytes)
        if self.corrupt_next_generation_read and stored is not None and "/generation=" in key:
            self.corrupt_next_generation_read = False
            return StoredAvailabilityObject(payload=stored.payload + b"corrupt", etag=stored.etag)
        return stored


@dataclass
class MutatingEvidenceStorage(MemoryAvailabilityStorage):
    mutation_enabled: bool = False
    mutation_armed: bool = False
    mutate_key: str | None = None

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        super().put_immutable(key, payload, content_type=content_type)
        if self.mutation_enabled and "/generation=" in key:
            self.mutation_armed = True

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        if self.mutation_armed and key == self.mutate_key:
            held = self.objects[key]
            self.seed(key, held.payload)
            self.mutation_armed = False
        return super().read(key, max_bytes=max_bytes)


@dataclass
class PublicationBarrierProbe:
    """Deterministic shared/exclusive ownership probe for the public async boundary."""

    held: bool = False
    entries: int = 0

    @asynccontextmanager
    async def exclusive(self, session: object, lane_root: str) -> AsyncIterator[bool]:
        del session
        assert lane_root == _LANE_ROOT
        assert self.held is False
        self.held = True
        self.entries += 1
        try:
            yield True
        finally:
            self.held = False

    def writer_granted(self) -> bool:
        """Model the shared writer acquire: it must refuse while exclusive ownership is held."""
        return not self.held


@dataclass
class BarrierObservingStorage(MemoryAvailabilityStorage):
    barrier_probe: PublicationBarrierProbe = field(default_factory=PublicationBarrierProbe)
    cas_barrier_states: list[bool] = field(default_factory=list)

    def compare_and_swap(
        self,
        key: str,
        payload: bytes,
        *,
        expected_etag: str | None,
        content_type: str,
    ) -> bool:
        self.cas_barrier_states.append(self.barrier_probe.held)
        return super().compare_and_swap(
            key,
            payload,
            expected_etag=expected_etag,
            content_type=content_type,
        )


@asynccontextmanager
async def _contended_publication_barrier(session: object, lane_root: str) -> AsyncIterator[bool]:
    del session
    assert lane_root == _LANE_ROOT
    yield False


async def test_guarded_bootstrap_performs_no_object_io_when_lane_barrier_is_contended() -> None:
    store = MemoryAvailabilityStorage()
    request = _bootstrap_request(store, days=(date(2026, 8, 30),))
    objects_before = dict(store.objects)
    store.read_log.clear()

    with pytest.raises(AvailabilityConflictError, match="publication barrier is contended"):
        await guarded_bootstrap_availability(
            cast("AsyncSession", object()),
            store,
            request,
            publication_barrier=_contended_publication_barrier,
        )

    assert store.read_log == []
    assert store.cas_calls == 0
    assert store.objects == objects_before


async def test_guarded_publish_and_rollback_perform_no_object_io_when_barrier_is_contended() -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 29),))
    pointer = bootstrap_availability(store, bootstrap).pointer
    request = PublicationRequest(
        config=AvailabilityConfig(
            identity=bootstrap.identity,
            source_ceiling=date(2026, 8, 30),
            bootstrap_receipt=pointer.bootstrap_receipt,
        ),
        created_at=_START + timedelta(hours=1),
        rows=_day_rows(
            store,
            bootstrap.identity,
            date(2026, 8, 30),
            published_at=_START + timedelta(hours=1),
        ),
        input_sha256="c" * 64,
    )
    objects_before = dict(store.objects)
    cas_before = store.cas_calls
    store.read_log.clear()
    session = cast("AsyncSession", object())

    with pytest.raises(AvailabilityConflictError, match="publication barrier is contended"):
        await guarded_publish_availability(
            session,
            store,
            request,
            publication_barrier=_contended_publication_barrier,
        )
    with pytest.raises(AvailabilityConflictError, match="publication barrier is contended"):
        await guarded_rollback_availability(
            session,
            store,
            lane_root=_LANE_ROOT,
            target_generation_key=pointer.generation_key,
            created_at=_START + timedelta(hours=2),
            publication_barrier=_contended_publication_barrier,
        )

    assert store.read_log == []
    assert store.cas_calls == cas_before
    assert store.objects == objects_before


async def test_guarded_publish_keeps_barrier_until_cancelled_core_finishes_cas() -> None:
    store = BarrierObservingStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 29),))
    pointer = bootstrap_availability(store, bootstrap).pointer
    store.cas_barrier_states.clear()
    probe = store.barrier_probe
    events: list[tuple[str, bool]] = []
    owner_task = asyncio.current_task()
    assert owner_task is not None

    @asynccontextmanager
    async def recording_barrier(session: object, lane_root: str) -> AsyncIterator[bool]:
        del session
        assert lane_root == _LANE_ROOT
        assert probe.held is False
        probe.held = True
        try:
            yield True
        finally:
            events.append(("barrier_exit", probe.held))
            probe.held = False

    def cancel_at_cas() -> None:
        events.append(("cas", probe.held))
        owner_task.cancel()

    request = PublicationRequest(
        config=AvailabilityConfig(
            identity=bootstrap.identity,
            source_ceiling=date(2026, 8, 30),
            bootstrap_receipt=pointer.bootstrap_receipt,
        ),
        created_at=_START + timedelta(hours=1),
        rows=_day_rows(
            store,
            bootstrap.identity,
            date(2026, 8, 30),
            published_at=_START + timedelta(hours=1),
        ),
        input_sha256="d" * 64,
    )
    pointer_key = availability_pointer_key(_LANE_ROOT)
    pointer_before = store.objects[pointer_key]
    store.cas_hook = cancel_at_cas

    async def publish_then_checkpoint() -> None:
        await guarded_publish_availability(
            cast("AsyncSession", object()),
            store,
            request,
            publication_barrier=recording_barrier,
        )
        await asyncio.sleep(0)

    with pytest.raises(asyncio.CancelledError):
        await publish_then_checkpoint()

    assert events == [("cas", True), ("barrier_exit", True)]
    assert store.cas_barrier_states == [True]
    assert store.objects[pointer_key] != pointer_before
    assert probe.held is False


async def test_guarded_publication_excludes_writer_through_cas_conflict_and_rebase() -> None:
    store = BarrierObservingStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 29),))
    first = bootstrap_availability(store, bootstrap)
    store.cas_barrier_states.clear()
    identity = bootstrap.identity
    loser = PublicationRequest(
        config=AvailabilityConfig(
            identity=identity,
            source_ceiling=date(2026, 8, 30),
            bootstrap_receipt=first.pointer.bootstrap_receipt,
        ),
        created_at=_START + timedelta(hours=1),
        rows=_day_rows(store, identity, date(2026, 8, 30), published_at=_START + timedelta(hours=1)),
        input_sha256="a" * 64,
    )
    winner = PublicationRequest(
        config=AvailabilityConfig(
            identity=identity,
            source_ceiling=date(2026, 8, 31),
            bootstrap_receipt=first.pointer.bootstrap_receipt,
        ),
        created_at=_START + timedelta(hours=2),
        rows=_day_rows(store, identity, date(2026, 8, 31), published_at=_START + timedelta(hours=1)),
        input_sha256="b" * 64,
    )
    writer_attempts: list[bool] = []

    def race_at_cas() -> None:
        writer_attempts.append(store.barrier_probe.writer_granted())
        publish_availability(store, winner)

    store.cas_hook = race_at_cas
    result = await guarded_publish_availability(
        cast("AsyncSession", object()),
        store,
        loser,
        publication_barrier=store.barrier_probe.exclusive,
    )

    expected_attempts = 2
    expected_cas_calls = 3
    assert result.attempts == expected_attempts
    assert writer_attempts == [False]
    assert len(store.cas_barrier_states) >= expected_cas_calls
    assert all(store.cas_barrier_states)
    assert store.barrier_probe.entries == 1
    assert store.barrier_probe.held is False


def test_bootstrap_is_idempotent_without_revalidating_historical_inputs() -> None:
    store = MemoryAvailabilityStorage()
    request = _bootstrap_request(store, days=(date(2026, 8, 30),))

    first = bootstrap_availability(store, request)
    successor_rows = _day_rows(
        store,
        request.identity,
        date(2026, 8, 31),
        published_at=_START + timedelta(hours=1),
    )
    successor = publish_availability(
        store,
        PublicationRequest(
            config=AvailabilityConfig(
                identity=request.identity,
                source_ceiling=date(2026, 8, 31),
                bootstrap_receipt=first.pointer.bootstrap_receipt,
            ),
            created_at=_START + timedelta(hours=2),
            rows=successor_rows,
            input_sha256="9" * 64,
        ),
    )
    store.read_log.clear()
    second = bootstrap_availability(store, request)

    assert first.advanced is True
    assert second.advanced is False
    assert second.pointer == successor.pointer
    evidence_keys = {
        receipt.key
        for receipt in (
            *request.input_receipts,
            *(receipt for row in request.rows for receipt in row.evidence_receipts()),
        )
    }
    assert not evidence_keys.intersection(store.read_log)
    assert store.read_log == [availability_pointer_key(_LANE_ROOT), successor.pointer.generation_key]


@pytest.mark.parametrize(
    ("mutation", "expected_match"),
    [
        ("lane", "identity"),
        ("product", "identity"),
        ("required_rungs", "required_rungs"),
        ("inventory_root", "identity"),
        ("input_receipts", "invalid bootstrap input key"),
        ("source_ceiling", "source ceiling"),
    ],
)
def test_system_bootstrap_receipt_rejects_mismatched_cross_bindings(
    mutation: str,
    expected_match: str,
) -> None:
    store = MemoryAvailabilityStorage()
    request = _bootstrap_request(store, days=(date(2026, 8, 30),))
    head = bootstrap_availability(store, request).pointer
    document = json.loads(store.objects[head.bootstrap_receipt.key].payload)
    if mutation == "lane":
        document["lane"] = "other-lane"
    elif mutation == "product":
        document["product"] = "other-product"
    elif mutation == "required_rungs":
        document["required_rungs"] = list(reversed(AVAILABILITY_REQUIRED_RUNGS))
    elif mutation == "inventory_root":
        document["verified_source_inventory_root"] = "0" * 64
    elif mutation == "input_receipts":
        input_receipt = document["input_receipts"][0]
        input_receipt["key"] = f"{_LANE_ROOT}/availability/evidence/source={input_receipt['sha256']}.json"
    elif mutation == "source_ceiling":
        document["source_ceiling"] = "2026-09-01"
    else:
        raise AssertionError(f"unhandled mutation {mutation!r}")
    payload = canonical_json(document).encode()
    digest = sha256_digest(payload)
    receipt = store.seed(
        f"{_LANE_ROOT}/availability/bootstrap/receipt={digest}.json",
        payload,
    )

    with pytest.raises(
        (AvailabilityConflictError, AvailabilityMalformedError, ValueError),
        match=expected_match,
    ):
        availability_module._verify_system_bootstrap_receipt(
            store,
            receipt,
            expected_identity=request.identity,
            maximum_row_count=len(request.rows),
            maximum_source_ceiling=request.source_ceiling,
        )


def test_bootstrap_refuses_an_unverified_inventory_root() -> None:
    store = MemoryAvailabilityStorage()
    valid = _bootstrap_request(store, days=(date(2026, 8, 30),))
    wrong_identity = AvailabilityIdentity(
        lane_root=valid.identity.lane_root,
        lane=valid.identity.lane,
        product=valid.identity.product,
        nature=valid.identity.nature,
        required_rungs=valid.identity.required_rungs,
        verified_source_inventory_root="0" * 64,
    )
    manifest = EvidenceReceipt(
        key="evidence/bootstrap-manifest.json",
        sha256=sha256_digest(b"verified manifest"),
    )
    wrong_input = build_bootstrap_inventory_evidence(
        BootstrapInventoryEvidence(
            identity=wrong_identity,
            source_ceiling=valid.source_ceiling,
            object_receipts=(manifest,),
        )
    )
    store.seed(wrong_input.receipt.key, wrong_input.payload)
    request = BootstrapRequest(
        identity=wrong_identity,
        source_ceiling=valid.source_ceiling,
        created_at=valid.created_at,
        input_receipts=(wrong_input.receipt,),
        rows=valid.rows,
        input_sha256=valid.input_sha256,
    )

    with pytest.raises(AvailabilityChecksumError, match="inventory_root"):
        bootstrap_availability(store, request)


def test_bootstrap_inventory_root_is_derived_from_aggregate_wrapper_objects() -> None:
    store = MemoryAvailabilityStorage()
    first = store.seed("evidence/manifest-a.json", b"manifest-a")
    second = store.seed("evidence/checkpoint-b.json", b"checkpoint-b")
    identity = AvailabilityIdentity(
        lane_root=_LANE_ROOT,
        lane="test-lane",
        product="test-product",
        nature="daily_series",
        required_rungs=AVAILABILITY_REQUIRED_RUNGS,
        verified_source_inventory_root=compute_verified_source_inventory_root((first, second)),
    )
    day = date(2026, 8, 30)
    wrappers = tuple(
        build_bootstrap_inventory_evidence(
            BootstrapInventoryEvidence(
                identity=identity,
                source_ceiling=day,
                object_receipts=(receipt,),
            )
        )
        for receipt in (first, second)
    )
    for wrapper in wrappers:
        store.seed(wrapper.receipt.key, wrapper.payload)
    input_receipts = tuple(sorted((wrapper.receipt for wrapper in wrappers), key=lambda item: item.key))
    rows = _day_rows(store, identity, day, published_at=_START - timedelta(hours=1))

    result = bootstrap_availability(
        store,
        BootstrapRequest(
            identity=identity,
            source_ceiling=day,
            created_at=_START,
            input_receipts=input_receipts,
            rows=rows,
            input_sha256="6" * 64,
        ),
    )

    assert result.advanced is True


def test_malformed_bootstrap_cannot_create_availability_objects() -> None:
    store = MemoryAvailabilityStorage()
    valid = _bootstrap_request(store, days=(date(2026, 8, 30),))
    objects_before = dict(store.objects)
    malformed = BootstrapRequest(
        identity=valid.identity,
        source_ceiling=valid.source_ceiling,
        created_at=valid.created_at,
        input_receipts=valid.input_receipts,
        rows=valid.rows[:-1],
        input_sha256=valid.input_sha256,
    )

    with pytest.raises(ValueError, match="exact required_rungs"):
        bootstrap_availability(store, malformed)

    assert availability_pointer_key(_LANE_ROOT) not in store.objects
    assert store.objects == objects_before


def test_pointer_race_retries_from_the_winning_generation() -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 29),))
    first = bootstrap_availability(store, bootstrap)
    identity = bootstrap.identity
    losing_config = AvailabilityConfig(
        identity=identity,
        source_ceiling=date(2026, 8, 30),
        bootstrap_receipt=first.pointer.bootstrap_receipt,
    )
    winning_config = AvailabilityConfig(
        identity=identity,
        source_ceiling=date(2026, 8, 31),
        bootstrap_receipt=first.pointer.bootstrap_receipt,
    )
    losing_rows = _day_rows(store, identity, date(2026, 8, 30), published_at=_START + timedelta(hours=1))
    winning_rows = _day_rows(store, identity, date(2026, 8, 31), published_at=_START + timedelta(hours=1))
    losing = PublicationRequest(
        config=losing_config,
        created_at=_START + timedelta(hours=1),
        rows=losing_rows,
        input_sha256="a" * 64,
    )
    winner = PublicationRequest(
        config=winning_config,
        created_at=_START + timedelta(hours=2),
        rows=winning_rows,
        input_sha256="b" * 64,
    )
    store.cas_hook = lambda: publish_availability(store, winner)

    result = publish_availability(store, losing)
    index = read_latest_availability(store, lane_root=_LANE_ROOT)

    expected_attempts = 2
    assert result.attempts == expected_attempts
    assert index.selectable_days() == (date(2026, 8, 29), date(2026, 8, 30), date(2026, 8, 31))
    assert result.pointer.prior_generation_key is not None
    assert result.pointer.prior_generation_sha256 is not None
    assert result.pointer.source_ceiling == date(2026, 8, 31)
    assert result.pointer.created_at == winner.created_at + timedelta(microseconds=1)


def test_correction_creates_a_generation_and_rollback_republishes_the_retained_rows() -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 30),))
    generation_zero = bootstrap_availability(store, bootstrap).pointer
    corrected_rows = _day_rows(
        store,
        bootstrap.identity,
        date(2026, 8, 30),
        published_at=_START + timedelta(hours=1),
        row_count=9,
    )
    correction = publish_availability(
        store,
        PublicationRequest(
            config=AvailabilityConfig(
                identity=bootstrap.identity,
                source_ceiling=bootstrap.source_ceiling,
                bootstrap_receipt=generation_zero.bootstrap_receipt,
            ),
            created_at=_START + timedelta(hours=2),
            rows=corrected_rows,
            input_sha256="c" * 64,
        ),
    ).pointer
    later_rows = _day_rows(
        store,
        bootstrap.identity,
        date(2026, 8, 31),
        published_at=_START + timedelta(hours=2),
    )
    later = publish_availability(
        store,
        PublicationRequest(
            config=AvailabilityConfig(
                identity=bootstrap.identity,
                source_ceiling=date(2026, 8, 31),
                bootstrap_receipt=generation_zero.bootstrap_receipt,
            ),
            created_at=_START + timedelta(hours=3),
            rows=later_rows,
            input_sha256="4" * 64,
        ),
    ).pointer

    rollback = rollback_availability(
        store,
        lane_root=_LANE_ROOT,
        target_generation_key=correction.generation_key,
        created_at=_START + timedelta(hours=4),
    ).pointer
    restored = read_latest_availability(store, lane_root=_LANE_ROOT)

    assert correction.generation_key != generation_zero.generation_key
    assert correction.prior_generation_key == generation_zero.generation_key
    assert correction.prior_generation_sha256 == generation_zero.generation_sha256
    assert rollback.generation_key not in {generation_zero.generation_key, correction.generation_key}
    assert rollback.prior_generation_key == later.generation_key
    assert rollback.prior_generation_sha256 == later.generation_sha256
    assert rollback.source_ceiling == correction.source_ceiling
    assert rollback.latest_terminal_day == correction.latest_terminal_day
    assert restored.rows == corrected_rows
    assert restored.selectable_days() == (date(2026, 8, 30),)
    assert generation_zero.generation_key in store.objects
    assert correction.generation_key in store.objects


def test_exact_replay_after_a_later_ceiling_returns_the_winning_pointer() -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 30),))
    generation_zero = bootstrap_availability(store, bootstrap).pointer
    successor = publish_availability(
        store,
        PublicationRequest(
            config=AvailabilityConfig(
                identity=bootstrap.identity,
                source_ceiling=date(2026, 8, 31),
                bootstrap_receipt=generation_zero.bootstrap_receipt,
            ),
            created_at=_START + timedelta(hours=2),
            rows=_day_rows(
                store,
                bootstrap.identity,
                date(2026, 8, 31),
                published_at=_START + timedelta(hours=1),
            ),
            input_sha256="5" * 64,
        ),
    ).pointer

    replay = publish_availability(
        store,
        PublicationRequest(
            config=AvailabilityConfig(
                identity=bootstrap.identity,
                source_ceiling=bootstrap.source_ceiling,
                bootstrap_receipt=generation_zero.bootstrap_receipt,
            ),
            created_at=bootstrap.created_at,
            rows=bootstrap.rows,
            input_sha256=bootstrap.input_sha256,
        ),
    )

    assert replay.advanced is False
    assert replay.pointer == successor


@pytest.mark.parametrize("row_slice", [slice(0, 0), slice(0, -1)])
def test_empty_or_partial_exact_replay_is_refused_without_advancing_pointer(row_slice: slice) -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 30),))
    head = bootstrap_availability(store, bootstrap).pointer
    pointer_key = availability_pointer_key(_LANE_ROOT)
    pointer_before = store.objects[pointer_key]

    with pytest.raises(ValueError, match=r"must contain|exact required_rungs"):
        publish_availability(
            store,
            PublicationRequest(
                config=AvailabilityConfig(
                    identity=bootstrap.identity,
                    source_ceiling=bootstrap.source_ceiling,
                    bootstrap_receipt=head.bootstrap_receipt,
                ),
                created_at=_START + timedelta(hours=1),
                rows=bootstrap.rows[row_slice],
                input_sha256="0" * 64,
            ),
        )

    assert store.objects[pointer_key] == pointer_before


def test_newer_same_grain_correction_survives_an_unrelated_ceiling_advance() -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 30),))
    generation_zero = bootstrap_availability(store, bootstrap).pointer
    publish_availability(
        store,
        PublicationRequest(
            config=AvailabilityConfig(
                identity=bootstrap.identity,
                source_ceiling=date(2026, 8, 31),
                bootstrap_receipt=generation_zero.bootstrap_receipt,
            ),
            created_at=_START + timedelta(hours=2),
            rows=_day_rows(
                store,
                bootstrap.identity,
                date(2026, 8, 31),
                published_at=_START + timedelta(hours=1),
            ),
            input_sha256="a" * 64,
        ),
    )
    corrected_rows = _day_rows(
        store,
        bootstrap.identity,
        date(2026, 8, 30),
        published_at=_START + timedelta(hours=3),
        row_count=9,
    )

    corrected = publish_availability(
        store,
        PublicationRequest(
            config=AvailabilityConfig(
                identity=bootstrap.identity,
                source_ceiling=bootstrap.source_ceiling,
                bootstrap_receipt=generation_zero.bootstrap_receipt,
            ),
            created_at=_START + timedelta(hours=4),
            rows=corrected_rows,
            input_sha256="b" * 64,
        ),
    )
    index = read_latest_availability(store, lane_root=_LANE_ROOT)

    assert corrected.pointer.source_ceiling == date(2026, 8, 31)
    assert {row.row_count for row in index.rows if row.day == date(2026, 8, 30)} == {9}


def test_generation_source_ceiling_cannot_advance_without_a_row_witness() -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 30),))
    head = bootstrap_availability(store, bootstrap).pointer
    pointer_before = store.objects[availability_pointer_key(_LANE_ROOT)]

    with pytest.raises(ValueError, match="maximum receipt-bound row source ceiling"):
        publish_availability(
            store,
            PublicationRequest(
                config=AvailabilityConfig(
                    identity=bootstrap.identity,
                    source_ceiling=date(2026, 8, 31),
                    bootstrap_receipt=head.bootstrap_receipt,
                ),
                created_at=_START + timedelta(hours=1),
                rows=bootstrap.rows,
                input_sha256="6" * 64,
            ),
        )

    assert store.objects[availability_pointer_key(_LANE_ROOT)] == pointer_before


@pytest.mark.parametrize("mode", ["missing", "sha_invalid"])
def test_rollback_refuses_invalid_target_receipts_without_advancing_pointer(mode: str) -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 30),))
    target = bootstrap_availability(store, bootstrap).pointer
    publish_availability(
        store,
        PublicationRequest(
            config=AvailabilityConfig(
                identity=bootstrap.identity,
                source_ceiling=date(2026, 8, 31),
                bootstrap_receipt=target.bootstrap_receipt,
            ),
            created_at=_START + timedelta(hours=2),
            rows=_day_rows(
                store,
                bootstrap.identity,
                date(2026, 8, 31),
                published_at=_START + timedelta(hours=1),
            ),
            input_sha256="7" * 64,
        ),
    )
    pointer_key = availability_pointer_key(_LANE_ROOT)
    pointer_before = store.objects[pointer_key]
    receipt = bootstrap.rows[0].data_receipts[0]
    if mode == "missing":
        del store.objects[receipt.key]
        expected_error: type[Exception] = AvailabilityUnavailableError
    else:
        store.seed(receipt.key, b"different bytes")
        expected_error = AvailabilityChecksumError

    with pytest.raises(expected_error):
        rollback_availability(
            store,
            lane_root=_LANE_ROOT,
            target_generation_key=target.generation_key,
            created_at=_START + timedelta(hours=3),
        )

    assert store.objects[pointer_key] == pointer_before


def test_generation_reread_corruption_prevents_pointer_creation() -> None:
    store = CorruptingGenerationStorage()
    request = _bootstrap_request(store, days=(date(2026, 8, 30),))

    with pytest.raises(AvailabilityChecksumError):
        bootstrap_availability(store, request)

    assert availability_pointer_key(_LANE_ROOT) not in store.objects


@pytest.mark.parametrize("mode", ["missing", "sha_invalid"])
def test_new_evidence_refusal_preserves_the_winning_pointer(mode: str) -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 30),))
    head = bootstrap_availability(store, bootstrap).pointer
    rows = _day_rows(
        store,
        bootstrap.identity,
        date(2026, 8, 31),
        published_at=_START + timedelta(hours=1),
    )
    receipt = rows[0].data_receipts[0]
    if mode == "missing":
        del store.objects[receipt.key]
        expected_error: type[Exception] = AvailabilityUnavailableError
    else:
        store.seed(receipt.key, b"different bytes")
        expected_error = AvailabilityChecksumError
    pointer_key = availability_pointer_key(_LANE_ROOT)
    pointer_before = store.objects[pointer_key]

    with pytest.raises(expected_error):
        publish_availability(
            store,
            PublicationRequest(
                config=AvailabilityConfig(
                    identity=bootstrap.identity,
                    source_ceiling=date(2026, 8, 31),
                    bootstrap_receipt=head.bootstrap_receipt,
                ),
                created_at=_START + timedelta(hours=2),
                rows=rows,
                input_sha256="8" * 64,
            ),
        )

    assert store.objects[pointer_key] == pointer_before


@pytest.mark.parametrize(
    ("field_name", "wrong_value"),
    [
        ("lane", "other-lane"),
        ("product", "other-product"),
        ("day", "2026-08-30"),
        ("rung", 5),
        ("row_count", 3),
        ("terminal_state", "governed_absence"),
        ("source_ceiling", "2026-08-30"),
    ],
)
def test_terminal_evidence_cross_binding_mismatches_preserve_pointer(
    field_name: str,
    wrong_value: object,
) -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 30),))
    head = bootstrap_availability(store, bootstrap).pointer
    rows = _day_rows(
        store,
        bootstrap.identity,
        date(2026, 8, 31),
        published_at=_START + timedelta(hours=1),
    )
    original = rows[0]
    document = json.loads(store.objects[original.terminal_receipt.key].payload)
    document[field_name] = wrong_value
    payload = canonical_json(document).encode()
    digest = sha256_digest(payload)
    receipt = store.seed(
        f"{_LANE_ROOT}/availability/evidence/terminal={digest}.json",
        payload,
    )
    changed_rows = (replace(original, terminal_receipt=receipt), *rows[1:])
    pointer_key = availability_pointer_key(_LANE_ROOT)
    pointer_before = store.objects[pointer_key]

    with pytest.raises(
        (AvailabilityConflictError, AvailabilityMalformedError, ValueError),
        match="terminal",
    ):
        publish_availability(
            store,
            PublicationRequest(
                config=AvailabilityConfig(
                    identity=bootstrap.identity,
                    source_ceiling=date(2026, 8, 31),
                    bootstrap_receipt=head.bootstrap_receipt,
                ),
                created_at=_START + timedelta(hours=2),
                rows=changed_rows,
                input_sha256="1" * 64,
            ),
        )

    assert store.objects[pointer_key] == pointer_before


@pytest.mark.parametrize("mode", ["swapped_purpose", "opaque", "extra_field", "unknown_schema"])
def test_unknown_or_noncanonical_terminal_receipts_preserve_pointer(mode: str) -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 30),))
    head = bootstrap_availability(store, bootstrap).pointer
    rows = _day_rows(
        store,
        bootstrap.identity,
        date(2026, 8, 31),
        published_at=_START + timedelta(hours=1),
    )
    if mode == "swapped_purpose":
        changed_rows = tuple(replace(row, terminal_receipt=row.source_receipt) for row in rows)
    else:
        document = json.loads(store.objects[rows[0].terminal_receipt.key].payload)
        if mode == "opaque":
            payload = b"opaque receipt"
        else:
            if mode == "extra_field":
                document["extra"] = True
            else:
                document["schema_version"] = "unknown"
            payload = canonical_json(document).encode()
        digest = sha256_digest(payload)
        receipt = store.seed(
            f"{_LANE_ROOT}/availability/evidence/terminal={digest}.json",
            payload,
        )
        changed_rows = (replace(rows[0], terminal_receipt=receipt), *rows[1:])
    pointer_key = availability_pointer_key(_LANE_ROOT)
    pointer_before = store.objects[pointer_key]

    with pytest.raises(
        (AvailabilityConflictError, AvailabilityMalformedError, ValueError),
        match=r"terminal|Expecting value",
    ):
        publish_availability(
            store,
            PublicationRequest(
                config=AvailabilityConfig(
                    identity=bootstrap.identity,
                    source_ceiling=date(2026, 8, 31),
                    bootstrap_receipt=head.bootstrap_receipt,
                ),
                created_at=_START + timedelta(hours=2),
                rows=changed_rows,
                input_sha256="2" * 64,
            ),
        )

    assert store.objects[pointer_key] == pointer_before


@pytest.mark.parametrize(
    ("mode", "wrong_value"),
    [
        ("lane", "other-lane"),
        ("product", "other-product"),
        ("day", "2026-08-30"),
        ("source_ceiling", "2026-08-30"),
        ("opaque", None),
        ("unknown_schema", None),
        ("wrong_purpose", None),
    ],
)
def test_source_evidence_mismatch_or_unknown_wrapper_preserves_pointer(
    mode: str,
    wrong_value: object,
) -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 30),))
    head = bootstrap_availability(store, bootstrap).pointer
    rows = _day_rows(
        store,
        bootstrap.identity,
        date(2026, 8, 31),
        published_at=_START + timedelta(hours=1),
    )
    document = json.loads(store.objects[rows[0].source_receipt.key].payload)
    if mode in {"lane", "product", "day", "source_ceiling"}:
        document[mode] = wrong_value
        payload = canonical_json(document).encode()
    elif mode == "unknown_schema":
        document["schema_version"] = "unknown"
        payload = canonical_json(document).encode()
    else:
        payload = b"opaque source receipt"
    digest = sha256_digest(payload)
    purpose = "terminal" if mode == "wrong_purpose" else "source"
    source = store.seed(
        f"{_LANE_ROOT}/availability/evidence/{purpose}={digest}.json",
        payload,
    )
    changed_rows = tuple(replace(row, source_receipt=source) for row in rows)
    pointer_key = availability_pointer_key(_LANE_ROOT)
    pointer_before = store.objects[pointer_key]

    with pytest.raises(
        (AvailabilityConflictError, AvailabilityMalformedError, ValueError),
        match=r"source|Expecting value",
    ):
        publish_availability(
            store,
            PublicationRequest(
                config=AvailabilityConfig(
                    identity=bootstrap.identity,
                    source_ceiling=date(2026, 8, 31),
                    bootstrap_receipt=head.bootstrap_receipt,
                ),
                created_at=_START + timedelta(hours=2),
                rows=changed_rows,
                input_sha256="5" * 64,
            ),
        )

    assert store.objects[pointer_key] == pointer_before


@pytest.mark.parametrize("mutation_target", ["data", "completion"])
def test_same_bytes_new_etag_after_generation_write_aborts_before_pointer_cas(
    mutation_target: str,
) -> None:
    store = MutatingEvidenceStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 30),))
    head = bootstrap_availability(store, bootstrap).pointer
    rows = _day_rows(
        store,
        bootstrap.identity,
        date(2026, 8, 31),
        published_at=_START + timedelta(hours=1),
    )
    completion = rows[0].completion_receipt
    assert completion is not None
    store.mutate_key = rows[0].data_receipts[0].key if mutation_target == "data" else completion.key
    store.mutation_enabled = True
    pointer_key = availability_pointer_key(_LANE_ROOT)
    pointer_before = store.objects[pointer_key]
    cas_calls_before = store.cas_calls

    with pytest.raises(AvailabilityConflictError, match="changed before pointer publication"):
        publish_availability(
            store,
            PublicationRequest(
                config=AvailabilityConfig(
                    identity=bootstrap.identity,
                    source_ceiling=date(2026, 8, 31),
                    bootstrap_receipt=head.bootstrap_receipt,
                ),
                created_at=_START + timedelta(hours=2),
                rows=rows,
                input_sha256="3" * 64,
            ),
        )

    assert store.objects[pointer_key] == pointer_before
    assert store.cas_calls == cas_calls_before


def test_published_ladder_accepts_lexicographic_receipts_for_twelve_numeric_parts() -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 30),))
    head = bootstrap_availability(store, bootstrap).pointer
    day = date(2026, 8, 31)
    rows = _day_rows(
        store,
        bootstrap.identity,
        day,
        published_at=_START + timedelta(hours=1),
    )
    original = rows[0]
    part_count = 12
    data_receipts = tuple(
        sorted(
            (
                store.seed(_partition_part_key(day, original.rung, index), _parquet_payload(1))
                for index in range(part_count)
            ),
            key=lambda receipt: receipt.key,
        )
    )
    assert data_receipts[2].key.endswith("part-10.parquet")
    completion = store.seed(
        _completion_key(day, original.rung),
        PartitionCompletion(
            part_count=part_count,
            row_count=part_count,
            completed_at=original.published_at,
            run_id="twelve-parts",
        ).to_json_bytes(),
    )
    terminal_artifact = build_terminal_evidence(
        TerminalEvidence(
            identity=bootstrap.identity,
            day=day,
            rung=original.rung,
            terminal_state="published",
            row_count=part_count,
            source_ceiling=day,
            published_at=original.published_at,
            source_receipt=original.source_receipt,
            data_receipts=data_receipts,
            completion_receipt=completion,
            absence_receipt=None,
            absence_reason=None,
        )
    )
    terminal = store.seed(terminal_artifact.receipt.key, terminal_artifact.payload)
    changed_rows = (
        replace(
            original,
            row_count=part_count,
            data_receipts=data_receipts,
            completion_receipt=completion,
            terminal_receipt=terminal,
        ),
        *rows[1:],
    )

    result = publish_availability(
        store,
        PublicationRequest(
            config=AvailabilityConfig(
                identity=bootstrap.identity,
                source_ceiling=day,
                bootstrap_receipt=head.bootstrap_receipt,
            ),
            created_at=_START + timedelta(hours=2),
            rows=changed_rows,
            input_sha256="a" * 64,
        ),
    )

    assert result.advanced is True


def test_published_observation_count_above_availability_index_cap_is_accepted() -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 30),))
    head = bootstrap_availability(store, bootstrap).pointer
    day = date(2026, 8, 31)
    rows = _day_rows(
        store,
        bootstrap.identity,
        day,
        published_at=_START + timedelta(hours=1),
    )
    original = rows[0]
    observation_count = MAX_AVAILABILITY_ROWS + 1
    data = store.seed(
        original.data_receipts[0].key,
        _parquet_payload(observation_count),
    )
    completion = store.seed(
        _completion_key(day, original.rung),
        PartitionCompletion(
            part_count=1,
            row_count=observation_count,
            completed_at=original.published_at,
            run_id="large-observation-part",
        ).to_json_bytes(),
    )
    terminal_artifact = build_terminal_evidence(
        TerminalEvidence(
            identity=bootstrap.identity,
            day=day,
            rung=original.rung,
            terminal_state="published",
            row_count=observation_count,
            source_ceiling=day,
            published_at=original.published_at,
            source_receipt=original.source_receipt,
            data_receipts=(data,),
            completion_receipt=completion,
            absence_receipt=None,
            absence_reason=None,
        )
    )
    terminal = store.seed(terminal_artifact.receipt.key, terminal_artifact.payload)
    changed_rows = (
        replace(
            original,
            row_count=observation_count,
            data_receipts=(data,),
            completion_receipt=completion,
            terminal_receipt=terminal,
        ),
        *rows[1:],
    )

    result = publish_availability(
        store,
        PublicationRequest(
            config=AvailabilityConfig(
                identity=bootstrap.identity,
                source_ceiling=day,
                bootstrap_receipt=head.bootstrap_receipt,
            ),
            created_at=_START + timedelta(hours=2),
            rows=changed_rows,
            input_sha256="b" * 64,
        ),
    )

    assert result.advanced is True


def test_physical_parquet_row_count_mismatch_preserves_pointer() -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 30),))
    head = bootstrap_availability(store, bootstrap).pointer
    rows = _day_rows(
        store,
        bootstrap.identity,
        date(2026, 8, 31),
        published_at=_START + timedelta(hours=1),
    )
    original = rows[0]
    changed_data = store.seed(original.data_receipts[0].key, _parquet_payload(3))
    document = json.loads(store.objects[original.terminal_receipt.key].payload)
    document["data_receipts"] = [changed_data.to_wire()]
    payload = canonical_json(document).encode()
    digest = sha256_digest(payload)
    terminal = store.seed(
        f"{_LANE_ROOT}/availability/evidence/terminal={digest}.json",
        payload,
    )
    changed_rows = (
        replace(original, data_receipts=(changed_data,), terminal_receipt=terminal),
        *rows[1:],
    )
    pointer_key = availability_pointer_key(_LANE_ROOT)
    pointer_before = store.objects[pointer_key]

    with pytest.raises(AvailabilityConflictError, match="Parquet row counts"):
        publish_availability(
            store,
            PublicationRequest(
                config=AvailabilityConfig(
                    identity=bootstrap.identity,
                    source_ceiling=date(2026, 8, 31),
                    bootstrap_receipt=head.bootstrap_receipt,
                ),
                created_at=_START + timedelta(hours=2),
                rows=changed_rows,
                input_sha256="4" * 64,
            ),
        )

    assert store.objects[pointer_key] == pointer_before


@pytest.mark.parametrize(
    "field_name",
    ["part_count", "row_count", "completed_at"],
)
def test_completion_marker_mismatch_preserves_pointer(field_name: str) -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 30),))
    head = bootstrap_availability(store, bootstrap).pointer
    rows = _day_rows(
        store,
        bootstrap.identity,
        date(2026, 8, 31),
        published_at=_START + timedelta(hours=1),
    )
    original = rows[0]
    completion = original.completion_receipt
    assert completion is not None
    changed_completion = store.seed(
        completion.key,
        PartitionCompletion(
            part_count=2 if field_name == "part_count" else 1,
            row_count=3 if field_name == "row_count" else original.row_count,
            completed_at=(
                datetime(2026, 9, 1, 15, tzinfo=UTC) if field_name == "completed_at" else original.published_at
            ),
            run_id="changed-completion",
        ).to_json_bytes(),
    )
    terminal_document = json.loads(store.objects[original.terminal_receipt.key].payload)
    terminal_document["completion_receipt"] = changed_completion.to_wire()
    terminal_payload = canonical_json(terminal_document).encode()
    terminal_digest = sha256_digest(terminal_payload)
    changed_terminal = store.seed(
        f"{_LANE_ROOT}/availability/evidence/terminal={terminal_digest}.json",
        terminal_payload,
    )
    changed_rows = (
        replace(
            original,
            completion_receipt=changed_completion,
            terminal_receipt=changed_terminal,
        ),
        *rows[1:],
    )
    pointer_key = availability_pointer_key(_LANE_ROOT)
    pointer_before = store.objects[pointer_key]

    with pytest.raises(AvailabilityConflictError, match="completion"):
        publish_availability(
            store,
            PublicationRequest(
                config=AvailabilityConfig(
                    identity=bootstrap.identity,
                    source_ceiling=date(2026, 8, 31),
                    bootstrap_receipt=head.bootstrap_receipt,
                ),
                created_at=_START + timedelta(hours=2),
                rows=changed_rows,
                input_sha256="7" * 64,
            ),
        )

    assert store.objects[pointer_key] == pointer_before


def test_governed_absence_marker_reason_mismatch_preserves_pointer() -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 30),))
    head = bootstrap_availability(store, bootstrap).pointer
    day = date(2026, 8, 31)
    rows = tuple(
        _absence_row(
            store,
            bootstrap.identity,
            day,
            rung,
            _START + timedelta(hours=1),
        )
        for rung in AVAILABILITY_REQUIRED_RUNGS
    )
    original = rows[0]
    terminal_document = json.loads(store.objects[original.terminal_receipt.key].payload)
    absence_wire = terminal_document["absence_receipt"]
    assert isinstance(absence_wire, dict)
    absence_key = absence_wire["key"]
    assert isinstance(absence_key, str)
    changed_absence = store.seed(
        absence_key,
        GovernedAbsence(
            reason="different_reason",
            upstream_response="verified empty test response",
            recorded_at=original.published_at,
            run_id="changed-absence",
        ).to_json_bytes(),
    )
    terminal_document["absence_receipt"] = changed_absence.to_wire()
    terminal_payload = canonical_json(terminal_document).encode()
    terminal_digest = sha256_digest(terminal_payload)
    changed_terminal = store.seed(
        f"{_LANE_ROOT}/availability/evidence/terminal={terminal_digest}.json",
        terminal_payload,
    )
    changed_rows = (replace(original, terminal_receipt=changed_terminal), *rows[1:])
    pointer_key = availability_pointer_key(_LANE_ROOT)
    pointer_before = store.objects[pointer_key]

    with pytest.raises(AvailabilityConflictError, match="absence marker reason"):
        publish_availability(
            store,
            PublicationRequest(
                config=AvailabilityConfig(
                    identity=bootstrap.identity,
                    source_ceiling=day,
                    bootstrap_receipt=head.bootstrap_receipt,
                ),
                created_at=_START + timedelta(hours=2),
                rows=changed_rows,
                input_sha256="8" * 64,
            ),
        )

    assert store.objects[pointer_key] == pointer_before


def test_oversized_pointer_declared_generation_is_refused_before_generation_get() -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 30),))
    head = bootstrap_availability(store, bootstrap).pointer
    pointer_key = availability_pointer_key(_LANE_ROOT)
    document = json.loads(store.objects[pointer_key].payload)
    document["generation_bytes"] = GENERATION_MAX_BYTES + 1
    store.seed(pointer_key, canonical_json(document).encode())
    store.read_log.clear()

    with pytest.raises(AvailabilityMalformedError):
        read_latest_availability(store, lane_root=_LANE_ROOT)

    assert store.read_log == [pointer_key]
    assert head.generation_key not in store.read_log


def test_oversized_pointer_declared_rows_is_refused_before_generation_get() -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 30),))
    head = bootstrap_availability(store, bootstrap).pointer
    pointer_key = availability_pointer_key(_LANE_ROOT)
    document = json.loads(store.objects[pointer_key].payload)
    document["rows"] = MAX_AVAILABILITY_ROWS + 1
    store.seed(pointer_key, canonical_json(document).encode())
    store.read_log.clear()

    with pytest.raises(AvailabilityMalformedError):
        read_latest_availability(store, lane_root=_LANE_ROOT)

    assert store.read_log == [pointer_key]
    assert head.generation_key not in store.read_log


def test_generation_physical_row_cap_is_checked_from_metadata_before_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 30),))
    bootstrap_availability(store, bootstrap)
    pointer_key = availability_pointer_key(_LANE_ROOT)
    document = json.loads(store.objects[pointer_key].payload)
    payload = _parquet_payload(MAX_AVAILABILITY_ROWS + 1)
    digest = sha256_digest(payload)
    generation_key = f"{_LANE_ROOT}/availability/generation={digest}/availability.parquet"
    store.seed(generation_key, payload)
    document["generation_bytes"] = len(payload)
    document["generation_key"] = generation_key
    document["generation_sha256"] = digest
    document["rows"] = 1
    store.seed(pointer_key, canonical_json(document).encode())

    materialized = False

    def fail_materialization(_: object) -> pa.Table:
        nonlocal materialized
        materialized = True
        raise AssertionError("oversized generation must not be materialized")

    monkeypatch.setattr(
        availability_module,
        "_materialize_generation",
        fail_materialization,
    )

    with pytest.raises(AvailabilityMalformedError, match="too many physical rows"):
        read_latest_availability(store, lane_root=_LANE_ROOT)
    assert materialized is False


def test_publication_refuses_partial_mixed_and_stale_conflicting_ladders() -> None:
    store = MemoryAvailabilityStorage()
    bootstrap = _bootstrap_request(store, days=(date(2026, 8, 30),))
    pointer = bootstrap_availability(store, bootstrap).pointer
    full_rows = _day_rows(store, bootstrap.identity, date(2026, 8, 31), published_at=_START)
    config = AvailabilityConfig(
        identity=bootstrap.identity,
        source_ceiling=date(2026, 8, 31),
        bootstrap_receipt=pointer.bootstrap_receipt,
    )

    with pytest.raises(ValueError, match="exact required_rungs"):
        publish_availability(
            store,
            PublicationRequest(
                config=config, created_at=_START + timedelta(hours=1), rows=full_rows[:-1], input_sha256="d" * 64
            ),
        )

    mixed = list(full_rows)
    mixed[-1] = _absence_row(store, bootstrap.identity, date(2026, 8, 31), mixed[-1].rung, _START)
    with pytest.raises(ValueError, match="mixes terminal states"):
        publish_availability(
            store,
            PublicationRequest(
                config=config, created_at=_START + timedelta(hours=1), rows=tuple(mixed), input_sha256="e" * 64
            ),
        )

    stale_correction = _day_rows(
        store,
        bootstrap.identity,
        date(2026, 8, 30),
        published_at=_START - timedelta(hours=1),
        row_count=9,
    )
    with pytest.raises(AvailabilityConflictError, match="stale publication conflicts"):
        publish_availability(
            store,
            PublicationRequest(
                config=AvailabilityConfig(
                    identity=bootstrap.identity,
                    source_ceiling=pointer.source_ceiling,
                    bootstrap_receipt=pointer.bootstrap_receipt,
                ),
                created_at=pointer.created_at,
                rows=stale_correction,
                input_sha256="f" * 64,
            ),
        )


def _bootstrap_request(store: MemoryAvailabilityStorage, *, days: tuple[date, ...]) -> BootstrapRequest:
    manifest = store.seed("evidence/bootstrap-manifest.json", b"verified manifest")
    identity = AvailabilityIdentity(
        lane_root=_LANE_ROOT,
        lane="test-lane",
        product="test-product",
        nature="daily_series",
        required_rungs=AVAILABILITY_REQUIRED_RUNGS,
        verified_source_inventory_root=compute_verified_source_inventory_root((manifest,)),
    )
    rows = tuple(
        row for day in days for row in _day_rows(store, identity, day, published_at=_START - timedelta(hours=1))
    )
    bootstrap_input = build_bootstrap_inventory_evidence(
        BootstrapInventoryEvidence(
            identity=identity,
            source_ceiling=max(days),
            object_receipts=(manifest,),
        )
    )
    store.seed(bootstrap_input.receipt.key, bootstrap_input.payload)
    return BootstrapRequest(
        identity=identity,
        source_ceiling=max(days),
        created_at=_START,
        input_receipts=(bootstrap_input.receipt,),
        rows=rows,
        input_sha256=sha256_digest(b"bootstrap input"),
    )


def _day_rows(
    store: MemoryAvailabilityStorage,
    identity: AvailabilityIdentity,
    day: date,
    *,
    published_at: datetime,
    row_count: int = 4,
) -> tuple[AvailabilityRow, ...]:
    source_object = store.seed(f"source/{day}/response.bin", f"source:{day}".encode())
    source_evidence = build_source_evidence(
        SourceEvidence(
            identity=identity,
            day=day,
            source_ceiling=day,
            object_receipts=(source_object,),
        )
    )
    source = store.seed(source_evidence.receipt.key, source_evidence.payload)
    rows = []
    for rung in AVAILABILITY_REQUIRED_RUNGS:
        data = store.seed(_partition_key(day, rung), _parquet_payload(row_count))
        completion_payload = PartitionCompletion(
            part_count=1,
            row_count=row_count,
            completed_at=published_at,
            run_id=f"test-{day}-z{rung}",
        ).to_json_bytes()
        completion = store.seed(_completion_key(day, rung), completion_payload)
        terminal_evidence = build_terminal_evidence(
            TerminalEvidence(
                identity=identity,
                day=day,
                rung=rung,
                terminal_state="published",
                row_count=row_count,
                source_ceiling=day,
                published_at=published_at,
                source_receipt=source,
                data_receipts=(data,),
                completion_receipt=completion,
                absence_receipt=None,
                absence_reason=None,
            )
        )
        terminal = store.seed(terminal_evidence.receipt.key, terminal_evidence.payload)
        rows.append(
            AvailabilityRow(
                lane=identity.lane,
                product=identity.product,
                nature=identity.nature,
                day=day,
                rung=rung,
                terminal_state="published",
                row_count=row_count,
                source_receipt=source,
                terminal_receipt=terminal,
                data_receipts=(data,),
                completion_receipt=completion,
                absence_reason=None,
                source_ceiling=day,
                published_at=published_at,
            )
        )
    return tuple(rows)


def _absence_row(
    store: MemoryAvailabilityStorage,
    identity: AvailabilityIdentity,
    day: date,
    rung: int,
    published_at: datetime,
) -> AvailabilityRow:
    source_object = store.seed(f"source/{day}/response.bin", f"source:{day}".encode())
    source_evidence = build_source_evidence(
        SourceEvidence(
            identity=identity,
            day=day,
            source_ceiling=day,
            object_receipts=(source_object,),
        )
    )
    source = store.seed(source_evidence.receipt.key, source_evidence.payload)
    reason = "source_verified_empty"
    absence = store.seed(
        _absence_key(day, rung),
        GovernedAbsence(
            reason=reason,
            upstream_response="verified empty test response",
            recorded_at=published_at,
            run_id=f"test-{day}-z{rung}",
        ).to_json_bytes(),
    )
    terminal_evidence = build_terminal_evidence(
        TerminalEvidence(
            identity=identity,
            day=day,
            rung=rung,
            terminal_state="governed_absence",
            row_count=0,
            source_ceiling=day,
            published_at=published_at,
            source_receipt=source,
            data_receipts=(),
            completion_receipt=None,
            absence_receipt=absence,
            absence_reason=reason,
        )
    )
    terminal = store.seed(terminal_evidence.receipt.key, terminal_evidence.payload)
    return AvailabilityRow(
        lane=identity.lane,
        product=identity.product,
        nature=identity.nature,
        day=day,
        rung=rung,
        terminal_state="governed_absence",
        row_count=0,
        source_receipt=source,
        terminal_receipt=terminal,
        data_receipts=(),
        completion_receipt=None,
        absence_reason=reason,
        source_ceiling=day,
        published_at=published_at,
    )


def _partition_prefix(day: date, rung: int) -> str:
    return f"{_LANE_ROOT}/zoom={rung:02d}/year={day.year:04d}/month={day.month:02d}/day={day.day:02d}"


def _partition_key(day: date, rung: int) -> str:
    return _partition_part_key(day, rung, 0)


def _partition_part_key(day: date, rung: int, part_index: int) -> str:
    return f"{_partition_prefix(day, rung)}/part-{part_index}.parquet"


def _completion_key(day: date, rung: int) -> str:
    return f"{_partition_prefix(day, rung)}/_complete.json"


def _absence_key(day: date, rung: int) -> str:
    return f"{_partition_prefix(day, rung)}/absent.json"


def _parquet_payload(row_count: int) -> bytes:
    sink = io.BytesIO()
    pq.write_table(pa.table({"value": pa.nulls(row_count, type=pa.int8())}), sink)
    return sink.getvalue()
