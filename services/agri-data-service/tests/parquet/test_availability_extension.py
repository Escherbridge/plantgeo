"""The availability step every terminal lane-day passes through, and what it does when it cannot finish.

The fixtures here are deliberately REAL objects in one bucket: parts written through
`ObjectStore.write_partition`, completion and absence markers through its own writers, and an
availability storage adapter over the SAME backend -- because availability verification opens those
exact keys and re-hashes those exact bytes. A memory storage holding its own copy would let a test
pass over receipts that agree with nothing.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import pytest

from agri_data_service.db.vegetation_publication import unlocked_vegetation_publication_barrier
from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import COMPLETION_FILE_NAME, completion_marker_path
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.parquet.availability_extension import (
    DERIVED_TO_ZERO_ROWS,
    POSTGRES_DAY_EXPORT_ORIGIN,
    AvailabilityExtensionOutcome,
    AvailabilityExtensionTally,
    FinalizedLaneDay,
    LaneDaySource,
    extend_availability_for_lane_day,
    retry_pending_availability,
)
from agri_data_service.pipeline.parquet.availability_index import (
    MAX_PUBLICATION_ATTEMPTS,
    AvailabilityIdentity,
    AvailabilityRow,
    BootstrapInventoryEvidence,
    BootstrapRequest,
    EvidenceReceipt,
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
from agri_data_service.pipeline.parquet.derivation import DerivationResult, DerivedTierReport
from agri_data_service.pipeline.parquet.gap_fill import (
    GAP_FILL_PARTITION_KIND,
    GAP_FILL_ZOOM_TIER,
    fill_one_lane_day,
    repair_one_lane_day,
    unlocked_lane_day,
    zero_row_absence,
    zero_row_absence_reason,
)
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY, LaneRegistration, LaneRunResult
from agri_data_service.pipeline.parquet.objectstore import (
    ObjectStore,
    WrittenObjectLedger,
    availability_retry_path,
    availability_retry_quarantine_path,
)
from agri_data_service.warehouse.schemas.availability_index import AVAILABILITY_REQUIRED_RUNGS
from tests.parquet.test_gap_fill import RecordingSession
from tests.parquet.test_objectstore_writer import WHOLE_WORLD_TIER, RecordingBackend, signal_rows

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.gap_fill import LadderRepairOutcome

LANE = "signal"
LANE_ROOT = "layer=signal/kind=observed"
BOOTSTRAP_DAY = date(2026, 8, 19)
DAY = date(2026, 8, 20)
RUN_ID = "availability-extension:test"
NOW = datetime(2026, 8, 21, 12, tzinfo=UTC)
ROWS_PER_RUNG = 3
#: The LANE's declared horizon, deliberately AHEAD of the day being written: the whole point of
#: `FinalizedLaneDay.source_ceiling` is that a lane's horizon is not its newest published day.
CEILING = date(2026, 8, 21)
#: When a LADDER REPAIR publishes, strictly after the export it corrects.
REPAIRED_AT = NOW + timedelta(hours=1)


class LoggingBackend(RecordingBackend):
    """A `RecordingBackend` that appends every mutation to a log shared with the availability storage."""

    def __init__(self, log: list[str]) -> None:
        super().__init__()
        self.log = log

    def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        self.log.append(f"lane-put:{key}")
        super().put(key, payload, content_type=content_type)

    def delete(self, key: str) -> None:
        self.log.append(f"lane-delete:{key}")
        super().delete(key)


@dataclass
class LaneAvailabilityStorage:
    """`AvailabilityStorage` over the SAME bucket the lane writes, with digest-derived comparison tokens."""

    backend: LoggingBackend
    log: list[str]
    cas_calls: int = 0
    cas_succeeds: bool = True
    cas_hook: Callable[[], None] | None = None
    reads: list[str] = field(default_factory=list)

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        self.reads.append(key)
        payload = self.backend.objects.get(key)
        if payload is None:
            return None
        if len(payload) > max_bytes:
            raise AssertionError(f"{key} exceeded its {max_bytes}-byte ceiling in a fixture")
        return StoredAvailabilityObject(payload=payload, etag=_etag(payload), version_id=None)

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        existing = self.backend.objects.get(key)
        if existing is not None:
            if existing != payload:
                raise AssertionError(f"immutable {key} would change bytes")
            return
        self.log.append(f"availability-put:{key}")
        self.backend.objects[key] = payload
        self.backend.content_types[key] = content_type
        self.backend.last_modified[key] = None

    def compare_and_swap(
        self,
        key: str,
        payload: bytes,
        *,
        expected_etag: str | None,
        content_type: str,
    ) -> bool:
        self.cas_calls += 1
        if self.cas_hook is not None:
            hook, self.cas_hook = self.cas_hook, None
            hook()
        if not self.cas_succeeds:
            return False
        existing = self.backend.objects.get(key)
        if expected_etag is None:
            if existing is not None:
                return False
        elif existing is None or _etag(existing) != expected_etag:
            return False
        self.log.append(f"availability-put:{key}")
        self.backend.objects[key] = payload
        self.backend.content_types[key] = content_type
        self.backend.last_modified[key] = None
        return True


def _etag(payload: bytes) -> str:
    """Derive a strong comparison token from the bytes themselves, as a versionless store would."""
    return f'"{sha256_digest(payload)[:32]}"'


@asynccontextmanager
async def granted_barrier(session: object, lane_root: str) -> AsyncIterator[bool]:
    """The lane publication barrier, canned as granted: contention has its own tests upstream."""
    del session
    assert lane_root == LANE_ROOT
    yield True


@asynccontextmanager
async def contended_barrier(session: object, lane_root: str) -> AsyncIterator[bool]:
    """A barrier another writer already owns."""
    del session, lane_root
    yield False


def new_lane() -> tuple[LoggingBackend, ObjectStore, LaneAvailabilityStorage, list[str]]:
    """Open one empty bucket shared by the lane writer and the availability contract."""
    log: list[str] = []
    backend = LoggingBackend(log)
    return backend, ObjectStore(backend), LaneAvailabilityStorage(backend=backend, log=log), log


def _lane_part_objects(backend: LoggingBackend) -> dict[str, bytes]:
    """Return the lane's DATA parts alone.

    An availability GENERATION is a Parquet object too, and a retry legitimately publishes a new
    content-addressed one -- so a bare `.parquet` filter reads that as the lane having re-exported.
    """
    return {
        key: value for key, value in backend.objects.items() if key.endswith(".parquet") and "/availability/" not in key
    }


def write_published_day(store: ObjectStore, *, day: date, completed_at: datetime = NOW) -> WrittenObjectLedger:
    """Write one whole published ladder -- parts and completion marker at every rung -- and keep its receipts."""
    with store.recording_written_objects() as ledger:
        for tier in ZOOM_TIERS:
            store.write_partition(
                signal_rows(),
                layer=LANE,
                kind=GAP_FILL_PARTITION_KIND,
                zoom=tier,
                day=day,
            )
            store.write_completion_marker(
                PartitionCompletion(
                    part_count=1,
                    row_count=ROWS_PER_RUNG,
                    completed_at=completed_at,
                    run_id=RUN_ID,
                ),
                layer=LANE,
                kind=GAP_FILL_PARTITION_KIND,
                zoom=tier,
                day=day,
            )
    return ledger


#: ELEVEN, not ten. `foundation/parquet/paths.py::partition_path` mints UNPADDED part names, so
#: numeric and lexicographic key order agree all the way through `part-9.parquet` and disagree the
#: moment `part-10.parquet` exists. Every other fixture in this file writes one part per rung and
#: therefore cannot tell the two orders apart.
PARTS_PAST_THE_UNPADDED_BREAK = 11


def write_published_day_split_across_parts(
    store: ObjectStore,
    *,
    day: date,
    base_parts: int = PARTS_PAST_THE_UNPADDED_BREAK,
) -> WrittenObjectLedger:
    """Write one published ladder whose BASE rung is split across `base_parts` real part files.

    One row per part, so the marker's `row_count` is the part count and every part is a genuine
    Parquet object the availability contract will open and re-hash.
    """
    with store.recording_written_objects() as ledger:
        for index in range(base_parts):
            store.write_partition(
                signal_rows(cell_ids=(f"base-{index}",)),
                layer=LANE,
                kind=GAP_FILL_PARTITION_KIND,
                zoom=GAP_FILL_ZOOM_TIER,
                day=day,
                part_index=index,
            )
        store.write_completion_marker(
            PartitionCompletion(part_count=base_parts, row_count=base_parts, completed_at=NOW, run_id=RUN_ID),
            layer=LANE,
            kind=GAP_FILL_PARTITION_KIND,
            zoom=GAP_FILL_ZOOM_TIER,
            day=day,
        )
        for tier in ZOOM_TIERS:
            if tier == GAP_FILL_ZOOM_TIER:
                continue
            store.write_partition(signal_rows(), layer=LANE, kind=GAP_FILL_PARTITION_KIND, zoom=tier, day=day)
            store.write_completion_marker(
                PartitionCompletion(part_count=1, row_count=ROWS_PER_RUNG, completed_at=NOW, run_id=RUN_ID),
                layer=LANE,
                kind=GAP_FILL_PARTITION_KIND,
                zoom=tier,
                day=day,
            )
    return ledger


def write_absent_day(store: ObjectStore, *, day: date, recorded_at: datetime = NOW) -> WrittenObjectLedger:
    """Write one governed-absence ladder, coarse rungs first and the censused base rung last."""
    with store.recording_written_objects() as ledger:
        for tier in (*(rung for rung in ZOOM_TIERS if rung != GAP_FILL_ZOOM_TIER), GAP_FILL_ZOOM_TIER):
            store.write_absence(
                zero_row_absence(
                    LANE,
                    zoom=tier,
                    day=day,
                    run_id=RUN_ID,
                    observed="the fixture export returned 0 rows",
                    recorded_at=recorded_at,
                ),
                layer=LANE,
                kind=GAP_FILL_PARTITION_KIND,
                zoom=tier,
                day=day,
            )
    return ledger


def bootstrap_lane(
    store: ObjectStore,
    storage: LaneAvailabilityStorage,
    *,
    day: date = BOOTSTRAP_DAY,
    published_at: datetime = NOW - timedelta(days=1),
) -> AvailabilityIdentity:
    """Give the lane a generation zero built from one REAL published day, as the offline bootstrap does."""
    ledger = write_published_day(store, day=day, completed_at=published_at)
    manifest_key = f"{LANE_ROOT}/availability/manifest/bootstrap.json"
    storage.put_immutable(manifest_key, b"verified bootstrap manifest", content_type="application/json")
    manifest = EvidenceReceipt(key=manifest_key, sha256=sha256_digest(b"verified bootstrap manifest"))
    identity = AvailabilityIdentity(
        lane_root=LANE_ROOT,
        lane=LANE,
        product=LANE,
        nature="daily_series",
        required_rungs=AVAILABILITY_REQUIRED_RUNGS,
        verified_source_inventory_root=compute_verified_source_inventory_root((manifest,)),
    )
    rows = seed_day_rows(storage, identity, ledger, day=day, published_at=published_at)
    inventory = build_bootstrap_inventory_evidence(
        BootstrapInventoryEvidence(identity=identity, source_ceiling=day, object_receipts=(manifest,))
    )
    storage.put_immutable(inventory.receipt.key, inventory.payload, content_type="application/json")
    bootstrap_availability(
        storage,
        BootstrapRequest(
            identity=identity,
            source_ceiling=day,
            created_at=published_at,
            input_receipts=(inventory.receipt,),
            rows=rows,
            input_sha256=sha256_digest(b"bootstrap input"),
        ),
    )
    return identity


def seed_day_rows(
    storage: LaneAvailabilityStorage,
    identity: AvailabilityIdentity,
    ledger: WrittenObjectLedger,
    *,
    day: date,
    published_at: datetime,
) -> tuple[AvailabilityRow, ...]:
    """Build and seed one already-written day's evidence, the way an offline input document would."""
    source_key = f"{LANE_ROOT}/availability/source/day={day.isoformat()}/bootstrap.json"
    source_payload = f"bootstrap source for {day.isoformat()}".encode()
    storage.put_immutable(source_key, source_payload, content_type="application/json")
    source_evidence = build_source_evidence(
        SourceEvidence(
            identity=identity,
            day=day,
            source_ceiling=day,
            object_receipts=(EvidenceReceipt(key=source_key, sha256=sha256_digest(source_payload)),),
        )
    )
    storage.put_immutable(source_evidence.receipt.key, source_evidence.payload, content_type="application/json")
    rows: list[AvailabilityRow] = []
    for rung in identity.required_rungs:
        tier = cast("ZoomTier", rung)
        parts = ledger.parts_for(kind=GAP_FILL_PARTITION_KIND, zoom=tier, day=day)
        completion = ledger.completion_for(kind=GAP_FILL_PARTITION_KIND, zoom=tier, day=day)
        assert completion is not None
        terminal = build_terminal_evidence(
            TerminalEvidence(
                identity=identity,
                day=day,
                rung=rung,
                terminal_state="published",
                row_count=completion.row_count,
                source_ceiling=day,
                published_at=published_at,
                source_receipt=source_evidence.receipt,
                data_receipts=tuple(EvidenceReceipt(key=part.relative_path, sha256=part.sha256) for part in parts),
                completion_receipt=EvidenceReceipt(key=completion.relative_path, sha256=completion.sha256),
                absence_receipt=None,
                absence_reason=None,
            )
        )
        storage.put_immutable(terminal.receipt.key, terminal.payload, content_type="application/json")
        rows.append(
            AvailabilityRow(
                lane=identity.lane,
                product=identity.product,
                nature=identity.nature,
                day=day,
                rung=rung,
                terminal_state="published",
                row_count=completion.row_count,
                source_receipt=source_evidence.receipt,
                terminal_receipt=terminal.receipt,
                data_receipts=tuple(EvidenceReceipt(key=part.relative_path, sha256=part.sha256) for part in parts),
                completion_receipt=EvidenceReceipt(key=completion.relative_path, sha256=completion.sha256),
                absence_reason=None,
                source_ceiling=day,
                published_at=published_at,
            )
        )
    return tuple(rows)


def published_outcome(
    ledger: WrittenObjectLedger,
    *,
    day: date = DAY,
    source_ceiling: date = CEILING,
) -> FinalizedLaneDay:
    """The finalized day a written export hands to the availability step."""
    return FinalizedLaneDay(
        terminal_state="published",
        day=day,
        written=ledger,
        source=LaneDaySource(
            origin=POSTGRES_DAY_EXPORT_ORIGIN,
            run_id=RUN_ID,
            row_count=ROWS_PER_RUNG,
            part_count=1,
            exported_at=NOW,
            detail="signal daily_series day export",
        ),
        published_at=NOW,
        source_ceiling=source_ceiling,
    )


def absent_outcome(
    ledger: WrittenObjectLedger,
    *,
    day: date = DAY,
    source_ceiling: date = CEILING,
) -> FinalizedLaneDay:
    """The finalized day a governed absence hands to the availability step."""
    return FinalizedLaneDay(
        terminal_state="governed_absence",
        day=day,
        written=ledger,
        source=LaneDaySource(
            origin=POSTGRES_DAY_EXPORT_ORIGIN,
            run_id=RUN_ID,
            row_count=0,
            part_count=0,
            exported_at=NOW,
        ),
        published_at=NOW,
        source_ceiling=source_ceiling,
        absence_reason=zero_row_absence_reason(LANE, day),
    )


async def extend(
    store: ObjectStore,
    storage: LaneAvailabilityStorage | None,
    outcome: FinalizedLaneDay,
    *,
    day: date = DAY,
) -> AvailabilityExtensionOutcome:
    """Run the availability step for one finalized day against the canned granted barrier."""
    return await extend_availability_for_lane_day(
        cast("AsyncSession", object()),
        store,
        lane=LANE,
        kind=GAP_FILL_PARTITION_KIND,
        day=day,
        outcome=outcome,
        availability=storage,
        now=lambda: NOW,
        publication_barrier=granted_barrier,
    )


@pytest.mark.asyncio
async def test_a_published_day_joins_the_generation_at_every_required_rung() -> None:
    """One terminal day becomes exactly one row per required rung, each bound to the objects it wrote."""
    backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    ledger = write_published_day(store, day=DAY)

    outcome = await extend(store, storage, published_outcome(ledger))

    assert outcome.state == "extended"
    index = read_latest_availability(storage, lane_root=LANE_ROOT)
    added = tuple(row for row in index.rows if row.day == DAY)
    assert tuple(row.rung for row in added) == AVAILABILITY_REQUIRED_RUNGS
    assert {row.terminal_state for row in added} == {"published"}
    assert {row.row_count for row in added} == {ROWS_PER_RUNG}
    assert len({row.source_receipt for row in added}) == 1
    assert DAY in index.selectable_days()
    for row in added:
        assert row.completion_receipt is not None
        assert row.completion_receipt.key == completion_marker_path(
            LANE, GAP_FILL_PARTITION_KIND, cast("ZoomTier", row.rung), DAY
        )
        assert backend.objects[row.data_receipts[0].key]
        assert sha256_digest(backend.objects[row.data_receipts[0].key]) == row.data_receipts[0].sha256


@pytest.mark.asyncio
async def test_a_governed_absence_joins_the_generation_with_one_reason_across_the_ladder() -> None:
    """An absent day is absent at every resolution of itself, and the ladder must agree on why."""
    _backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    ledger = write_absent_day(store, day=DAY)

    outcome = await extend(store, storage, absent_outcome(ledger))

    assert outcome.state == "extended"
    index = read_latest_availability(storage, lane_root=LANE_ROOT)
    added = tuple(row for row in index.rows if row.day == DAY)
    assert tuple(row.rung for row in added) == AVAILABILITY_REQUIRED_RUNGS
    assert {row.terminal_state for row in added} == {"governed_absence"}
    assert {row.row_count for row in added} == {0}
    assert {row.absence_reason for row in added} == {zero_row_absence_reason(LANE, DAY)}
    assert all(row.completion_receipt is None and not row.data_receipts for row in added)
    assert DAY in index.selectable_days()


@pytest.mark.asyncio
async def test_a_lane_with_no_bootstrap_is_reported_and_writes_nothing() -> None:
    """Production bootstrap is separately authorized: until it runs, a terminal day stays terminal."""
    backend, store, storage, _log = new_lane()
    ledger = write_published_day(store, day=DAY)
    before = dict(backend.objects)

    outcome = await extend(store, storage, published_outcome(ledger))

    assert outcome.state == "not_bootstrapped"
    assert outcome.error_kind == "availability_not_bootstrapped"
    assert backend.objects == before
    assert availability_pointer_key(LANE_ROOT) not in backend.objects


@pytest.mark.asyncio
async def test_an_unwired_availability_storage_is_reported_without_touching_the_bucket() -> None:
    """A run with no conditional storage configured must be inert, never a failed lane-day."""
    backend, store, _storage, _log = new_lane()
    ledger = write_published_day(store, day=DAY)
    before = dict(backend.objects)

    outcome = await extend(store, None, published_outcome(ledger))

    assert outcome.state == "not_bootstrapped"
    assert outcome.error_kind == "availability_not_wired"
    assert backend.objects == before


@pytest.mark.asyncio
async def test_a_pointer_that_stays_contended_leaves_a_retry_marker_and_the_prior_generation() -> None:
    """A failed availability step owes an idempotent retry -- never a re-export of the data day."""
    backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    head = read_latest_availability(storage, lane_root=LANE_ROOT).pointer
    ledger = write_published_day(store, day=DAY)
    # The bootstrap advanced the pointer once. Counting from zero here is what makes the
    # assertion below a statement about THIS publication's retry budget rather than about
    # every swap the fixture has ever made.
    storage.cas_calls = 0
    storage.cas_succeeds = False

    outcome = await extend(store, storage, published_outcome(ledger))

    assert outcome.state == "retry_owed"
    assert outcome.error_kind == "publication_failed"
    assert storage.cas_calls == MAX_PUBLICATION_ATTEMPTS
    assert read_latest_availability(storage, lane_root=LANE_ROOT).pointer == head
    marker = availability_retry_path(LANE, GAP_FILL_PARTITION_KIND, DAY)
    assert outcome.retry_marker == marker
    claim = json.loads(backend.objects[marker].decode("utf-8"))
    assert claim["day"] == DAY.isoformat()
    assert claim["lane_root"] == LANE_ROOT
    assert claim["source_ceiling"] == CEILING.isoformat()
    assert claim["terminal_state"] == "published"
    assert [entry["rung"] for entry in claim["rungs"]] == list(AVAILABILITY_REQUIRED_RUNGS)
    # The claim names the PHYSICAL objects, not the typed evidence wrappers: those keys derive from
    # the pointer identity, which is exactly the read a claim has to survive.
    for entry in claim["rungs"]:
        assert entry["completion_receipt"]["key"].endswith(COMPLETION_FILE_NAME)
        assert entry["data_receipts"], "a published rung claims the parts a retry rebuilds from"


@pytest.mark.asyncio
async def test_the_next_turn_consumes_the_retry_marker_and_clears_it() -> None:
    """The retry publishes the SAME receipts the failed turn wrote, then drops the claim it satisfied."""
    backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    ledger = write_published_day(store, day=DAY)
    storage.cas_succeeds = False
    await extend(store, storage, published_outcome(ledger))
    storage.cas_succeeds = True
    parts_before = _lane_part_objects(backend)

    outcomes = await retry_pending_availability(
        cast("AsyncSession", object()),
        store,
        lane=LANE,
        kind=GAP_FILL_PARTITION_KIND,
        availability=storage,
        now=lambda: NOW + timedelta(hours=1),
        publication_barrier=granted_barrier,
    )

    assert [item.state for item in outcomes] == ["extended"]
    assert availability_retry_path(LANE, GAP_FILL_PARTITION_KIND, DAY) not in backend.objects
    index = read_latest_availability(storage, lane_root=LANE_ROOT)
    assert tuple(row.rung for row in index.rows if row.day == DAY) == AVAILABILITY_REQUIRED_RUNGS
    # The retry republished; it never re-exported. Every part file is byte-identical to before.
    assert _lane_part_objects(backend) == parts_before


@pytest.mark.asyncio
async def test_an_identical_second_extension_is_skipped_unchanged() -> None:
    """The same day with the same receipts is already indexed, so nothing is published a second time."""
    _backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    ledger = write_published_day(store, day=DAY)
    first = await extend(store, storage, published_outcome(ledger))
    cas_after_first = storage.cas_calls

    second = await extend(store, storage, published_outcome(ledger))

    assert first.state == "extended"
    assert second.state == "skipped_unchanged"
    assert storage.cas_calls == cas_after_first
    assert second.generation_key == first.generation_key


@pytest.mark.asyncio
async def test_a_rung_that_wrote_nothing_leaves_the_day_terminal_and_names_the_gap() -> None:
    """A rung that derived to no rows carries no marker, so the required ladder cannot be formed."""
    backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    with store.recording_written_objects() as ledger:
        for tier in ZOOM_TIERS:
            if tier == WHOLE_WORLD_TIER:
                continue
            store.write_partition(signal_rows(), layer=LANE, kind=GAP_FILL_PARTITION_KIND, zoom=tier, day=DAY)
            store.write_completion_marker(
                PartitionCompletion(part_count=1, row_count=ROWS_PER_RUNG, completed_at=NOW, run_id=RUN_ID),
                layer=LANE,
                kind=GAP_FILL_PARTITION_KIND,
                zoom=tier,
                day=DAY,
            )
    head = read_latest_availability(storage, lane_root=LANE_ROOT).pointer

    outcome = await extend(store, storage, published_outcome(ledger))

    assert outcome.state == "ladder_incomplete"
    assert "z0" in outcome.reason
    assert read_latest_availability(storage, lane_root=LANE_ROOT).pointer == head
    assert availability_retry_path(LANE, GAP_FILL_PARTITION_KIND, DAY) not in backend.objects


@pytest.mark.asyncio
async def test_a_completion_marker_that_disagrees_with_its_parts_is_refused_before_publication() -> None:
    """No availability row may bind a marker and a part population that describe different days' work."""
    _backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    with store.recording_written_objects() as ledger:
        for tier in ZOOM_TIERS:
            store.write_partition(signal_rows(), layer=LANE, kind=GAP_FILL_PARTITION_KIND, zoom=tier, day=DAY)
            store.write_completion_marker(
                PartitionCompletion(part_count=2, row_count=ROWS_PER_RUNG, completed_at=NOW, run_id=RUN_ID),
                layer=LANE,
                kind=GAP_FILL_PARTITION_KIND,
                zoom=tier,
                day=DAY,
            )

    outcome = await extend(store, storage, published_outcome(ledger))

    assert outcome.state == "ladder_incomplete"
    assert "completion marker claims" in outcome.reason


@pytest.mark.asyncio
async def test_every_availability_object_is_written_after_the_base_completion_marker() -> None:
    """The ordering guarantee, proved through the driver: the day is terminal BEFORE the index moves."""
    backend, store, storage, log = new_lane()
    bootstrap_lane(store, storage)
    log.clear()

    result = await fill_one_lane_day(
        cast("AsyncSession", RecordingSession()),
        store,
        _writing_lane(),
        day=DAY,
        run_id=RUN_ID,
        now=lambda: NOW,
        today=DAY,
        lane_day_lock=unlocked_lane_day,
        derive_tiers=_coarse_rung_writer,
        availability_storage=storage,
    )

    assert result[0] == "written"
    base_marker = f"lane-put:{completion_marker_path(LANE, GAP_FILL_PARTITION_KIND, GAP_FILL_ZOOM_TIER, DAY)}"
    availability_writes = [index for index, entry in enumerate(log) if entry.startswith("availability-put:")]
    assert availability_writes, "the extension published nothing, so this proves no ordering"
    assert min(availability_writes) > log.index(base_marker)
    assert DAY in read_latest_availability(storage, lane_root=LANE_ROOT).selectable_days()
    assert backend.objects[availability_pointer_key(LANE_ROOT)]


@pytest.mark.asyncio
async def test_the_driver_can_be_asked_not_to_extend_availability_at_all() -> None:
    """The seam a test disables; the day still completes and the pointer never moves."""
    _backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    head = read_latest_availability(storage, lane_root=LANE_ROOT).pointer

    result = await fill_one_lane_day(
        cast("AsyncSession", RecordingSession()),
        store,
        _writing_lane(),
        day=DAY,
        run_id=RUN_ID,
        now=lambda: NOW,
        today=DAY,
        lane_day_lock=unlocked_lane_day,
        derive_tiers=_coarse_rung_writer,
        extend_availability=False,
        availability_storage=storage,
    )

    assert result[0] == "written"
    assert result[4] is None or "availability" not in result[4]
    assert read_latest_availability(storage, lane_root=LANE_ROOT).pointer == head


@pytest.mark.asyncio
async def test_a_contended_publication_barrier_owes_a_retry_rather_than_failing_the_day() -> None:
    """Another writer holding the lane barrier is contention, not a data failure."""
    backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    ledger = write_published_day(store, day=DAY)

    outcome = await extend_availability_for_lane_day(
        cast("AsyncSession", object()),
        store,
        lane=LANE,
        kind=GAP_FILL_PARTITION_KIND,
        day=DAY,
        outcome=published_outcome(ledger),
        availability=storage,
        now=lambda: NOW,
        publication_barrier=contended_barrier,
    )

    assert outcome.state == "retry_owed"
    assert outcome.error_kind == "publication_failed"
    assert availability_retry_path(LANE, GAP_FILL_PARTITION_KIND, DAY) in backend.objects


@pytest.mark.asyncio
async def test_the_published_ceiling_is_the_lane_s_horizon_and_not_the_day_just_written() -> None:
    """A ceiling defaulted to the day itself pinned every lane's horizon to its newest row."""
    _backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    today = DAY + timedelta(days=3)

    result = await fill_one_lane_day(
        cast("AsyncSession", RecordingSession()),
        store,
        _writing_lane(),
        day=DAY,
        run_id=RUN_ID,
        now=lambda: NOW,
        today=today,
        lane_day_lock=unlocked_lane_day,
        derive_tiers=_coarse_rung_writer,
        availability_storage=storage,
    )

    assert result[0] == "written"
    pointer = read_latest_availability(storage, lane_root=LANE_ROOT).pointer
    assert pointer.source_ceiling == today, "the lane declares today minus its lag, never the day it wrote"
    assert pointer.latest_terminal_day == DAY


@pytest.mark.asyncio
async def test_an_unreadable_head_still_claims_the_day_and_the_next_turn_indexes_it() -> None:
    """The census never revisits a base-complete day, so an unindexed one must leave a claim behind."""
    backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    ledger = write_published_day(store, day=DAY)
    pointer_key = availability_pointer_key(LANE_ROOT)
    intact_pointer = backend.objects[pointer_key]
    backend.objects[pointer_key] = b"{"  # a transiently unreadable head, not a missing one

    owed = await extend(store, storage, published_outcome(ledger))

    assert owed.state == "retry_owed"
    assert owed.error_kind == "availability_unreadable"
    marker = availability_retry_path(LANE, GAP_FILL_PARTITION_KIND, DAY)
    assert owed.retry_marker == marker
    assert marker in backend.objects, "a day the index never heard of must be claimed before the head is read"

    backend.objects[pointer_key] = intact_pointer
    parts_before = _lane_part_objects(backend)
    outcomes = await retry_pending_availability(
        cast("AsyncSession", object()),
        store,
        lane=LANE,
        kind=GAP_FILL_PARTITION_KIND,
        availability=storage,
        now=lambda: NOW + timedelta(hours=1),
        publication_barrier=granted_barrier,
    )

    assert [item.state for item in outcomes] == ["extended"]
    assert marker not in backend.objects
    index = read_latest_availability(storage, lane_root=LANE_ROOT)
    assert tuple(row.rung for row in index.rows if row.day == DAY) == AVAILABILITY_REQUIRED_RUNGS
    assert DAY in index.selectable_days()
    assert _lane_part_objects(backend) == parts_before, "the retry republished; it never re-exported"


@pytest.mark.asyncio
async def test_an_unparseable_claim_is_quarantined_out_of_the_oldest_first_ledger() -> None:
    """DO NOT DELETE. A claim that cannot be parsed cannot be retried, and left in place it STARVES.

    `_claim_from_marker` is pure, so the next turn parses the identical bytes into the identical
    refusal -- forever. The ledger is drained oldest-first and bounded at `DEFAULT_MAX_RETRIES_PER_LANE`
    days per tick, so one unparseable day permanently occupies one of those slots and can hold back
    every genuinely replayable day behind it. It leaves the ledger, its bytes stay readable beside
    it, and its own day is counted as the permanent loss it is.
    """
    backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    marker = availability_retry_path(LANE, GAP_FILL_PARTITION_KIND, DAY)
    backend.objects[store.key_for(marker)] = b'{"schema_version": "availability-retry-v0"}'

    outcomes = await retry_pending_availability(
        cast("AsyncSession", object()),
        store,
        lane=LANE,
        kind=GAP_FILL_PARTITION_KIND,
        availability=storage,
        now=lambda: NOW + timedelta(hours=1),
        publication_barrier=granted_barrier,
    )

    assert [item.state for item in outcomes] == ["retry_claim_failed"]
    assert outcomes[0].error_kind == "retry_marker_malformed"
    quarantined = availability_retry_quarantine_path(LANE, GAP_FILL_PARTITION_KIND, DAY)
    assert outcomes[0].retry_marker == quarantined
    assert store.key_for(marker) not in backend.objects, "the unparseable claim must leave the ledger"
    assert backend.objects[store.key_for(quarantined)], "and its bytes must stay readable for an operator"
    assert store.list_availability_retry_days(LANE, GAP_FILL_PARTITION_KIND) == (), (
        "a quarantined key must not be listed as owed, or it still occupies a retry slot"
    )
    tally = AvailabilityExtensionTally()
    tally.record(outcomes[0])
    assert tally.to_summary()["availability_retry_claim_failed"] == 1


@pytest.mark.asyncio
async def test_a_day_whose_claim_cannot_be_recorded_is_its_own_counted_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`retry_claim_failed` is the loss no retry can recover, so it may never hide inside `retry_owed`."""
    _backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    ledger = write_published_day(store, day=DAY)

    def refuse(*_args: object, **_kwargs: object) -> str:
        raise OSError("the bucket refused the claim")

    monkeypatch.setattr(store, "write_availability_retry", refuse)

    outcome = await extend(store, storage, published_outcome(ledger))

    assert outcome.state == "retry_claim_failed"
    assert outcome.error_kind == "retry_claim_unwritable"
    tally = AvailabilityExtensionTally()
    tally.record(outcome)
    assert tally.retry_claim_failed == 1
    assert tally.to_summary()["availability_retry_claim_failed"] == 1


@pytest.mark.asyncio
async def test_an_emptied_rung_names_its_own_gap_kind_and_is_counted_as_a_lost_day() -> None:
    """A rung retracted for deriving to zero rows is not a broken export, and the summary says which."""
    _backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    with store.recording_written_objects() as ledger:
        for tier in ZOOM_TIERS:
            if tier == WHOLE_WORLD_TIER:
                continue
            store.write_partition(signal_rows(), layer=LANE, kind=GAP_FILL_PARTITION_KIND, zoom=tier, day=DAY)
            store.write_completion_marker(
                PartitionCompletion(part_count=1, row_count=ROWS_PER_RUNG, completed_at=NOW, run_id=RUN_ID),
                layer=LANE,
                kind=GAP_FILL_PARTITION_KIND,
                zoom=tier,
                day=DAY,
            )

    outcome = await extend(store, storage, published_outcome(ledger))

    assert outcome.state == "ladder_incomplete"
    assert outcome.error_kind == DERIVED_TO_ZERO_ROWS
    tally = AvailabilityExtensionTally()
    tally.record(outcome)
    assert tally.to_summary()["availability_ladder_incomplete"] == 1


@pytest.mark.asyncio
async def test_an_emptied_rung_carrying_its_receipt_closes_the_ladder_and_stays_selectable() -> None:
    """DO NOT DELETE. This is the whole point of the derived-empty receipt.

    Every base row of this day fell below z0's floor, so `derivation._retract_tier` emptied the rung
    and marked it. The day is `published` at EVERY rung -- one terminal state, so
    `_validate_generation_day` is satisfied -- and z0 holds a row with `row_count=0` and no data
    receipts. Before the receipt this exact day was `ladder_incomplete` forever, on a green tick.
    """
    _backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    with store.recording_written_objects() as ledger:
        for tier in ZOOM_TIERS:
            if tier == WHOLE_WORLD_TIER:
                store.write_completion_marker(
                    PartitionCompletion(part_count=0, row_count=0, completed_at=NOW, run_id=RUN_ID, derived_empty=True),
                    layer=LANE,
                    kind=GAP_FILL_PARTITION_KIND,
                    zoom=tier,
                    day=DAY,
                )
                continue
            store.write_partition(signal_rows(), layer=LANE, kind=GAP_FILL_PARTITION_KIND, zoom=tier, day=DAY)
            store.write_completion_marker(
                PartitionCompletion(part_count=1, row_count=ROWS_PER_RUNG, completed_at=NOW, run_id=RUN_ID),
                layer=LANE,
                kind=GAP_FILL_PARTITION_KIND,
                zoom=tier,
                day=DAY,
            )

    outcome = await extend(store, storage, published_outcome(ledger))

    assert outcome.state == "extended"
    index = read_latest_availability(storage, lane_root=LANE_ROOT)
    assert tuple(row.rung for row in index.rows if row.day == DAY) == AVAILABILITY_REQUIRED_RUNGS
    assert DAY in index.selectable_days(), "an emptied rung must not cost the day its whole ladder"
    empty_rung = next(row for row in index.rows if row.day == DAY and row.rung == WHOLE_WORLD_TIER)
    assert (empty_rung.terminal_state, empty_rung.row_count, empty_rung.data_receipts) == ("published", 0, ())
    assert empty_rung.completion_receipt is not None, "the receipt is what makes the empty rung a CLAIM"


@pytest.mark.asyncio
async def test_parked_claims_are_counted_in_the_walk_the_retry_pass_already_pays_for() -> None:
    """A quarantined claim is a terminal day outside the index that nothing retries; it must be a NUMBER.

    `quarantine_availability_retry` deliberately hides these from the oldest-first ledger so one
    unreadable day cannot starve a lane's eight retries -- and nothing swept or counted them, so a
    lane writing claims it could not read back accumulated silent permanent losses.
    """
    backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    parked_days = (DAY, DAY + timedelta(days=1))
    for day in parked_days:
        store.quarantine_availability_retry(
            b'{"schema_version": "availability-retry-v0"}',
            layer=LANE,
            kind=GAP_FILL_PARTITION_KIND,
            day=day,
        )

    outcomes = await retry_pending_availability(
        cast("AsyncSession", object()),
        store,
        lane=LANE,
        kind=GAP_FILL_PARTITION_KIND,
        availability=storage,
        now=lambda: NOW + timedelta(hours=1),
        publication_barrier=granted_barrier,
    )

    assert [item.state for item in outcomes] == ["quarantined"]
    swept = outcomes[0]
    assert swept.day is None, "the sweep is a lane-wide statement, not a per-day verdict"
    assert swept.counted_days == len(parked_days)
    assert all(day.isoformat() in swept.reason for day in parked_days)
    tally = AvailabilityExtensionTally()
    tally.record(swept)
    assert tally.to_summary()["availability_quarantined_standing"] == len(parked_days), (
        "the parked set is a STANDING gauge: each sweep restates it whole rather than incrementing"
    )
    for day in parked_days:
        key = store.key_for(availability_retry_quarantine_path(LANE, GAP_FILL_PARTITION_KIND, day))
        assert key in backend.objects, "nothing is deleted: reading a malformed claim is an admin's call"


def test_the_tally_folds_lane_totals_without_losing_a_verdict() -> None:
    """A tick-wide count is the sum of its lanes; a fold that dropped one would under-report a loss."""
    one = AvailabilityExtensionTally(extended=2, ladder_incomplete=1)
    other = AvailabilityExtensionTally(extended=3, retry_claim_failed=1)

    one.add(other)

    assert one.to_summary() == {
        "availability_extended": 5,
        "availability_skipped_unchanged": 0,
        "availability_not_bootstrapped": 0,
        "availability_ladder_incomplete": 1,
        "availability_retry_owed": 0,
        "availability_retry_claim_failed": 1,
        "availability_quarantined_standing": 0,
        "availability_reindex_owed": 0,
    }


def _writing_lane() -> LaneRegistration:
    """A lane whose adapter really writes its base rung, so the ladder below it is real Parquet."""

    async def adapter(session: Any, store: ObjectStore, *, day: date, run_id: str) -> LaneRunResult:  # noqa: ARG001
        receipt = store.write_partition(
            signal_rows(),
            layer=LANE,
            kind=GAP_FILL_PARTITION_KIND,
            zoom=GAP_FILL_ZOOM_TIER,
            day=day,
        )
        return LaneRunResult(
            part_count=1,
            row_count=receipt.row_count,
            byte_count=receipt.byte_count,
            absence_recorded=False,
        )

    return LaneRegistration(
        slug=LANE,
        adapter=adapter,
        history_floor=BOOTSTRAP_DAY,
        publication_lag_days=0,
        nature="daily_series",
        floor_basis="test fixture for the availability extension",
        watermark=None,
    )


def _coarse_rung_writer(  # noqa: PLR0913 - the signature IS the seam; it must match what it replaces
    store: ObjectStore,
    *,
    layer: str,
    kind: PartitionKind,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
) -> DerivationResult:
    """A tier deriver that writes the real coarse objects without asking Polars to generalise anything."""
    reports: list[DerivedTierReport] = []
    for tier in ZOOM_TIERS:
        if tier == GAP_FILL_ZOOM_TIER:
            continue
        receipt = store.write_partition(signal_rows(), layer=layer, kind=kind, zoom=tier, day=day)
        store.write_completion_marker(
            PartitionCompletion(
                part_count=1,
                row_count=receipt.row_count,
                completed_at=now(),
                run_id=run_id,
            ),
            layer=layer,
            kind=kind,
            zoom=tier,
            day=day,
        )
        reports.append(
            DerivedTierReport(
                tier=tier,
                part_count=1,
                row_count=receipt.row_count,
                byte_count=receipt.byte_count,
            )
        )
    return DerivationResult(tiers=tuple(reports), notes=())


# --- A repaired day joins the index -------------------------------------------------------------
#
# A ladder repair rewrites three of the day's four rungs, so their receipts change. Until the claim
# existed nothing told the index: the day was complete at all four rungs, `derived_rung_completions`
# never selected it again, the generation still bound receipts of objects that no longer existed,
# and the tick reported `repaired: 1`. The loss was permanent and it was green.

DERIVED_RUNGS: tuple[ZoomTier, ...] = tuple(tier for tier in ZOOM_TIERS if tier != GAP_FILL_ZOOM_TIER)


def rewriting_deriver(*, cell_ids: tuple[str, ...]) -> Callable[..., DerivationResult]:
    """A deriver that really writes each coarse rung, with content the previous derivation did not hold.

    Real objects, because the availability contract opens every receipt it is handed and re-hashes
    the bytes -- a deriver that only returned counts would let this test pass over receipts that
    point at nothing. Different `cell_ids` are what make the new receipts DIFFER from the old, which
    is the whole subject: identical bytes would resolve as `skipped_unchanged` and prove nothing.
    """

    def derive(  # noqa: PLR0913 - the signature IS the seam; it must match what it replaces
        store: ObjectStore,
        *,
        layer: str,
        kind: PartitionKind,
        day: date,
        run_id: str,
        now: Callable[[], datetime],
        connection: object = None,
        base_table: object = None,
    ) -> DerivationResult:
        del connection, base_table
        reports: list[DerivedTierReport] = []
        for tier in DERIVED_RUNGS:
            receipt = store.write_partition(signal_rows(cell_ids=cell_ids), layer=layer, kind=kind, zoom=tier, day=day)
            store.write_completion_marker(
                PartitionCompletion(part_count=1, row_count=len(cell_ids), completed_at=now(), run_id=run_id),
                layer=layer,
                kind=kind,
                zoom=tier,
                day=day,
            )
            reports.append(
                DerivedTierReport(tier=tier, part_count=1, row_count=len(cell_ids), byte_count=receipt.byte_count)
            )
        return DerivationResult(tiers=tuple(reports), notes=())

    return derive


async def repair(
    store: ObjectStore,
    storage: LaneAvailabilityStorage | None,
    *,
    day: date = DAY,
    cell_ids: tuple[str, ...] = ("c9", "c8"),
) -> LadderRepairOutcome:
    """Re-derive one published day's coarse rungs through the real driver, lock seam granted.

    `REPAIRED_AT` is deliberately LATER than the export's `published_at`: a correction whose rows do
    not postdate the ones they replace is refused as a stale publication, which is the contract doing
    its job -- an out-of-order retry must never overwrite a newer generation.
    """
    return await repair_one_lane_day(
        cast("AsyncSession", RecordingSession()),
        store,
        LANE_REGISTRY[LANE],
        day=day,
        run_id=RUN_ID,
        now=lambda: REPAIRED_AT,
        today=CEILING,
        lane_day_lock=unlocked_lane_day,
        vegetation_publication_barrier=unlocked_vegetation_publication_barrier,
        derive_tiers=rewriting_deriver(cell_ids=cell_ids),
        availability_storage=storage,
    )


@pytest.mark.asyncio
async def test_a_repaired_day_writes_the_claim_that_brings_it_into_the_index() -> None:
    """DO NOT DELETE. Without the claim a repaired day is complete, unindexed and unreachable.

    Every rung is marked, so no census re-selects it; `_extend_availability_for_result` is gated on
    an EXPORT outcome and never runs; `_LadderGap` writes no claim. The day would sit outside the
    generation for good while the tick reported `repaired: 1`.
    """
    _backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    write_published_day(store, day=DAY)

    outcome = await repair(store, storage)

    assert outcome.outcome == "written"
    assert outcome.availability is not None
    assert outcome.availability.state == "retry_owed"
    assert store.read_availability_retry(LANE, GAP_FILL_PARTITION_KIND, DAY) is not None
    index = read_latest_availability(storage, lane_root=LANE_ROOT)
    assert not [row for row in index.rows if row.day == DAY], "the claim indexes on the NEXT turn, not this one"


@pytest.mark.asyncio
async def test_the_next_turn_indexes_the_repaired_day_without_re_exporting_a_row() -> None:
    """The claim names every physical receipt, so the drain publishes from it and touches no lane part."""
    backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    write_published_day(store, day=DAY)
    await repair(store, storage)
    parts_before = dict(_lane_part_objects(backend))

    outcomes = await retry_pending_availability(
        cast("AsyncSession", object()),
        store,
        lane=LANE,
        kind=GAP_FILL_PARTITION_KIND,
        availability=storage,
        now=lambda: NOW,
        publication_barrier=granted_barrier,
    )

    assert [outcome.state for outcome in outcomes] == ["extended"]
    assert _lane_part_objects(backend) == parts_before, "the availability step re-exported lane data"
    assert store.read_availability_retry(LANE, GAP_FILL_PARTITION_KIND, DAY) is None
    index = read_latest_availability(storage, lane_root=LANE_ROOT)
    added = tuple(row for row in index.rows if row.day == DAY)
    assert tuple(row.rung for row in added) == AVAILABILITY_REQUIRED_RUNGS
    assert DAY in index.selectable_days()
    for row in added:
        for receipt in row.data_receipts:
            assert sha256_digest(backend.objects[receipt.key]) == receipt.sha256


@pytest.mark.asyncio
async def test_a_repaired_day_of_eleven_base_parts_still_reaches_the_index() -> None:
    """DO NOT DELETE. Unpadded part names make numeric and lexicographic order disagree past `part-9`.

    `ObjectStore.read_partition_with_receipts` hands the base rung's parts back in NUMERIC
    `part_index` order -- it must, because that is the order their rows are concatenated in -- while
    `availability_index._validate_data_receipt_collection` demands LEXICOGRAPHIC object-key order.
    Below eleven parts the two orders are identical and every other fixture here passes over the
    difference. At eleven, numeric order puts `part-10.parquet` after `part-9.parquet` and the claim
    is no longer sorted, so `TerminalEvidence` raised inside `_prepare_day` -- where
    `_index_claimed_day` catches `ValueError`, DELETES the claim and reports `evidence_unbuildable`.
    The repaired day then left the index permanently and silently, complete at every rung so no
    census would ever select it again: the exact loss the claim mechanism exists to prevent, arriving
    through the mechanism itself. `DERIVED_ROWS_PER_PART` is 10,000, so any lane day past ~90k rows
    is in this range.
    """
    backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    write_published_day_split_across_parts(store, day=DAY)

    repaired = await repair(store, storage)
    outcomes = await retry_pending_availability(
        cast("AsyncSession", object()),
        store,
        lane=LANE,
        kind=GAP_FILL_PARTITION_KIND,
        availability=storage,
        now=lambda: NOW,
        publication_barrier=granted_barrier,
    )

    assert repaired.availability is not None
    assert repaired.availability.state == "retry_owed", repaired.availability.reason
    assert [outcome.state for outcome in outcomes] == ["extended"], [outcome.reason for outcome in outcomes]
    index = read_latest_availability(storage, lane_root=LANE_ROOT)
    base = next(row for row in index.rows if row.day == DAY and row.rung == GAP_FILL_ZOOM_TIER)
    keys = tuple(receipt.key for receipt in base.data_receipts)
    assert len(keys) == PARTS_PAST_THE_UNPADDED_BREAK
    assert any(key.endswith("part-10.parquet") for key in keys), "the fixture never crossed the unpadded break"
    assert keys == tuple(sorted(keys)), "the claim cited its parts in upload order rather than object-key order"
    for receipt in base.data_receipts:
        assert sha256_digest(backend.objects[receipt.key]) == receipt.sha256
    assert DAY in index.selectable_days()


@pytest.mark.asyncio
async def test_a_re_derivation_after_a_cleared_marker_publishes_a_correction_generation() -> None:
    """DO NOT DELETE. This is the `write_partition` half of the same hole.

    `write_partition` clears the completion marker at `part_index == 0`, so a re-derivation that dies
    right after that leaves an ALREADY-INDEXED day whose next repair writes new parts and new SHAs
    while the generation still binds the old receipts. Nothing re-selects the day -- it is complete
    at every rung once the repair finishes -- so without the claim the index and the bucket diverge
    permanently and silently. The claim turns the changed receipts into a correction generation.
    """
    backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    ledger = write_published_day(store, day=DAY)
    assert (await extend(store, storage, published_outcome(ledger))).state == "extended"
    before = read_latest_availability(storage, lane_root=LANE_ROOT)
    old_receipts = {
        row.rung: tuple(receipt.sha256 for receipt in row.data_receipts) for row in before.rows if row.day == DAY
    }

    # The container died after `part-0` cleared z9's claim: the rung holds parts and no marker.
    store.clear_completion_marker(LANE, GAP_FILL_PARTITION_KIND, cast("ZoomTier", 9), DAY)
    await repair(store, storage, cell_ids=("c7", "c6", "c5", "c4"))
    outcomes = await retry_pending_availability(
        cast("AsyncSession", object()),
        store,
        lane=LANE,
        kind=GAP_FILL_PARTITION_KIND,
        availability=storage,
        now=lambda: NOW,
        publication_barrier=granted_barrier,
    )

    assert [outcome.state for outcome in outcomes] == ["extended"]
    after = read_latest_availability(storage, lane_root=LANE_ROOT)
    assert after.pointer.generation_key != before.pointer.generation_key, "the correction never published"
    assert after.pointer.prior_generation_key == before.pointer.generation_key
    corrected = {
        row.rung: tuple(receipt.sha256 for receipt in row.data_receipts) for row in after.rows if row.day == DAY
    }
    for rung in DERIVED_RUNGS:
        assert corrected[rung] != old_receipts[rung], f"z{rung} still binds the receipts of replaced objects"
        for receipt in next(row for row in after.rows if row.day == DAY and row.rung == rung).data_receipts:
            assert sha256_digest(backend.objects[receipt.key]) == receipt.sha256
    assert corrected[GAP_FILL_ZOOM_TIER] == old_receipts[GAP_FILL_ZOOM_TIER], "the repair rewrote the base rung"


@pytest.mark.asyncio
async def test_a_repair_reuses_the_source_evidence_of_a_day_the_generation_already_holds() -> None:
    """No export happened, so no second export-source document is minted for one that never ran."""
    _backend, store, storage, log = new_lane()
    bootstrap_lane(store, storage)
    ledger = write_published_day(store, day=DAY)
    await extend(store, storage, published_outcome(ledger))
    before = read_latest_availability(storage, lane_root=LANE_ROOT)
    held = {row.source_receipt for row in before.rows if row.day == DAY}
    assert len(held) == 1

    await repair(store, storage, cell_ids=("c7", "c6"))
    log.clear()
    outcomes = await retry_pending_availability(
        cast("AsyncSession", object()),
        store,
        lane=LANE,
        kind=GAP_FILL_PARTITION_KIND,
        availability=storage,
        now=lambda: NOW,
        publication_barrier=granted_barrier,
    )

    # Asserted FIRST: a refused correction leaves the held rows in place, so every assertion below
    # this line passes vacuously on a retry that never published.
    assert [outcome.state for outcome in outcomes] == ["extended"]
    after = read_latest_availability(storage, lane_root=LANE_ROOT)
    assert {row.source_receipt for row in after.rows if row.day == DAY} == held
    assert not [line for line in log if "/availability/source/" in line], (
        "a repair minted a new export-source object for a fetch that never happened"
    )
