"""Receipt-preserving recovery of terminal days after interrupted publication."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, cast

import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import absence_marker_path
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.parquet.availability_extension import (
    AvailabilityExtensionTally,
    retry_pending_availability,
)
from agri_data_service.pipeline.parquet.availability_index import read_latest_availability
from agri_data_service.pipeline.parquet.derivation import govern_day_absent, write_absence_ladder
from agri_data_service.pipeline.parquet.gap_fill import (
    build_lane_census,
    fill_one_lane_day,
    repair_one_lane_day,
    unlocked_lane_day,
    zero_row_absence,
)
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY, LaneRunResult
from agri_data_service.pipeline.parquet.objectstore import (
    GovernedAbsenceConflictError,
    ObjectStore,
    availability_retry_path,
)
from tests.parquet.test_absence_ladder import RefusingBackend
from tests.parquet.test_availability_extension import (
    CEILING,
    DAY,
    LANE,
    LANE_ROOT,
    NOW,
    REPAIRED_AT,
    RUN_ID,
    bootstrap_lane,
    granted_barrier,
    new_lane,
    write_published_day,
)
from tests.parquet.test_gap_fill import RecordingSession
from tests.parquet.test_objectstore_writer import BASE_TIER

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.gap_fill import LadderRepairOutcome
    from tests.parquet.test_availability_extension import LaneAvailabilityStorage


def source_absence() -> GovernedAbsence:
    return GovernedAbsence(
        reason="complete settled source response contains no observations for this day",
        upstream_response='{"http_status":200,"source_sha256":"original-source-receipt"}',
        recorded_at=NOW,
        run_id="source-original",
    )


async def repair_absence(store: ObjectStore, storage: LaneAvailabilityStorage | None = None) -> LadderRepairOutcome:
    return await repair_one_lane_day(
        cast("AsyncSession", RecordingSession()),
        store,
        LANE_REGISTRY[LANE],
        day=DAY,
        today=CEILING,
        run_id=RUN_ID,
        now=lambda: REPAIRED_AT,
        lane_day_lock=unlocked_lane_day,
        availability_storage=storage,
    )


def test_a_same_reason_recheck_retains_original_bytes_and_records_every_receipt() -> None:
    backend, store, _storage, log = new_lane()
    original = source_absence()
    govern_day_absent(store, original, layer=LANE, kind="observed", day=DAY)
    before = dict(backend.objects)
    log.clear()

    with store.recording_written_objects() as ledger:
        govern_day_absent(
            store,
            replace(
                original,
                upstream_response="later matching source answer",
                recorded_at=NOW + timedelta(days=1),
                run_id="recheck",
            ),
            layer=LANE,
            kind="observed",
            day=DAY,
        )

    assert backend.objects == before
    assert log == []
    assert len(ledger.absences) == len(ZOOM_TIERS)
    assert {receipt.reason for receipt in ledger.absences.values()} == {original.reason}


def test_conflicting_absence_evidence_is_refused_before_any_rung_changes() -> None:
    backend, store, _storage, log = new_lane()
    original = source_absence()
    store.write_absence(original, layer=LANE, kind="observed", zoom=BASE_TIER, day=DAY)
    store.write_absence(
        replace(original, reason="a different source claim"), layer=LANE, kind="observed", zoom=ZOOM_TIERS[1], day=DAY
    )
    before = dict(backend.objects)
    log.clear()

    with pytest.raises(GovernedAbsenceConflictError, match="different governed-absence evidence"):
        write_absence_ladder(store, original, layer=LANE, kind="observed", day=DAY)

    assert backend.objects == before
    assert log == []


@pytest.mark.asyncio
async def test_partial_absence_ladder_is_scheduled_repaired_and_reindexed_from_original_proof() -> None:
    backend, store, storage, log = new_lane()
    bootstrap_lane(store, storage)
    original = source_absence()
    store.write_absence(original, layer=LANE, kind="observed", zoom=BASE_TIER, day=DAY)
    original_bytes = backend.objects[absence_marker_path(LANE, "observed", BASE_TIER, DAY)]
    lane = replace(LANE_REGISTRY[LANE], history_floor=DAY, publication_lag_days=0, writer_ceiling=None)

    census = build_lane_census(lane, store, today=DAY)
    assert census.missing_days == ()
    assert census.ladder_repair_days == (DAY,)
    log.clear()
    result = await repair_absence(store, storage)

    assert result.outcome == "absent"
    assert result.availability is not None
    assert result.availability.state == "retry_owed"
    assert log[0] == f"lane-put:{availability_retry_path(LANE, 'observed', DAY)}"
    for rung in ZOOM_TIERS:
        assert backend.objects[absence_marker_path(LANE, "observed", rung, DAY)] == original_bytes
    assert build_lane_census(lane, store, today=DAY).ladder_repair_days == ()
    log.clear()
    assert (await repair_absence(store, storage)).outcome == "skipped_unchanged"
    assert log == []

    outcomes = await retry_pending_availability(
        cast("AsyncSession", RecordingSession()),
        store,
        lane=LANE,
        kind="observed",
        availability=storage,
        now=lambda: REPAIRED_AT,
        publication_barrier=granted_barrier,
    )
    assert [outcome.state for outcome in outcomes] == ["extended"]
    rows = [row for row in read_latest_availability(storage, lane_root=LANE_ROOT).rows if row.day == DAY]
    assert {row.absence_reason for row in rows} == {original.reason}
    assert len(rows) == len(ZOOM_TIERS)
    assert availability_retry_path(LANE, "observed", DAY) not in backend.objects


@pytest.mark.asyncio
async def test_partial_absence_repair_rollback_keeps_the_original_source_receipts() -> None:
    backend = RefusingBackend()
    store = ObjectStore(backend)
    original = source_absence()
    for rung in (BASE_TIER, ZOOM_TIERS[0]):
        store.write_absence(original, layer=LANE, kind="observed", zoom=rung, day=DAY)
    before = dict(backend.objects)
    backend.refuses_put_of.add(absence_marker_path(LANE, "observed", ZOOM_TIERS[2], DAY))

    result = await repair_absence(store)

    assert result.outcome == "raised"
    assert backend.objects == before


@pytest.mark.asyncio
async def test_a_failed_repair_claim_leaves_the_absence_ladder_selectable(monkeypatch: pytest.MonkeyPatch) -> None:
    backend, store, storage, log = new_lane()
    bootstrap_lane(store, storage)
    store.write_absence(source_absence(), layer=LANE, kind="observed", zoom=BASE_TIER, day=DAY)
    before = dict(backend.objects)
    original_put = backend.put
    log.clear()

    def refuse_claim(key: str, payload: bytes, *, content_type: str) -> None:
        if key == availability_retry_path(LANE, "observed", DAY):
            raise OSError("retry ledger unavailable")
        original_put(key, payload, content_type=content_type)

    monkeypatch.setattr(backend, "put", refuse_claim)
    result = await repair_absence(store, storage)

    assert result.outcome == "raised"
    assert result.availability is not None
    assert result.availability.state == "retry_claim_failed"
    assert backend.objects == before
    assert log == []
    lane = replace(LANE_REGISTRY[LANE], history_floor=DAY, publication_lag_days=0, writer_ceiling=None)
    assert build_lane_census(lane, store, today=DAY).ladder_repair_days == (DAY,)


@pytest.mark.asyncio
async def test_an_interrupted_repair_intent_cannot_publish_before_physical_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    store.write_absence(source_absence(), layer=LANE, kind="observed", zoom=BASE_TIER, day=DAY)
    original_put = backend.put

    def refuse_rung(key: str, payload: bytes, *, content_type: str) -> None:
        if key == absence_marker_path(LANE, "observed", ZOOM_TIERS[2], DAY):
            raise OSError("rung upload interrupted")
        original_put(key, payload, content_type=content_type)

    monkeypatch.setattr(backend, "put", refuse_rung)
    result = await repair_absence(store, storage)
    assert result.outcome == "raised"
    assert availability_retry_path(LANE, "observed", DAY) in backend.objects
    outcomes = await retry_pending_availability(
        cast("AsyncSession", RecordingSession()),
        store,
        lane=LANE,
        kind="observed",
        availability=storage,
        now=lambda: REPAIRED_AT,
        publication_barrier=granted_barrier,
    )
    assert [outcome.state for outcome in outcomes] == ["retry_owed"]
    assert DAY not in read_latest_availability(storage, lane_root=LANE_ROOT).selectable_days()
    monkeypatch.setattr(backend, "put", original_put)
    assert (await repair_absence(store, storage)).outcome == "absent"
    outcomes = await retry_pending_availability(
        cast("AsyncSession", RecordingSession()),
        store,
        lane=LANE,
        kind="observed",
        availability=storage,
        now=lambda: REPAIRED_AT,
        publication_barrier=granted_barrier,
    )
    assert [outcome.state for outcome in outcomes] == ["extended"]


@pytest.mark.asyncio
async def test_a_historical_query_zero_marker_is_not_copied_as_source_proof() -> None:
    backend, store, _storage, log = new_lane()
    original = zero_row_absence(LANE, zoom=BASE_TIER, day=DAY, run_id=RUN_ID, observed="zero rows", recorded_at=NOW)
    store.write_absence(original, layer=LANE, kind="observed", zoom=BASE_TIER, day=DAY)
    before = dict(backend.objects)
    log.clear()

    result = await repair_absence(store)

    assert result.outcome == "blocked"
    assert "not source absence" in (result.detail or "")
    assert backend.objects == before
    assert log == []


@pytest.mark.asyncio
async def test_absence_repair_refuses_a_published_empty_rung_without_mutating_it() -> None:
    backend, store, _storage, log = new_lane()
    store.write_absence(source_absence(), layer=LANE, kind="observed", zoom=BASE_TIER, day=DAY)
    store.write_completion_marker(
        PartitionCompletion(part_count=0, row_count=0, completed_at=NOW, run_id=RUN_ID, derived_empty=True),
        layer=LANE,
        kind="observed",
        zoom=ZOOM_TIERS[1],
        day=DAY,
    )
    before = dict(backend.objects)
    log.clear()

    result = await repair_absence(store)

    assert result.outcome == "blocked"
    assert "published-empty completion" in (result.detail or "")
    assert backend.objects == before
    assert log == []


@pytest.mark.asyncio
async def test_a_repair_rechecks_the_ladder_after_acquiring_its_lock() -> None:
    backend, store, _storage, log = new_lane()
    expected: dict[str, bytes] = {}

    @asynccontextmanager
    async def completed_while_waiting(_session: AsyncSession, _key: str) -> AsyncIterator[bool]:
        write_published_day(store, day=DAY)
        expected.update(backend.objects)
        log.clear()
        yield True

    result = await repair_one_lane_day(
        cast("AsyncSession", RecordingSession()),
        store,
        LANE_REGISTRY[LANE],
        day=DAY,
        today=CEILING,
        run_id=RUN_ID,
        now=lambda: REPAIRED_AT,
        lane_day_lock=completed_while_waiting,
    )

    assert result.outcome == "skipped_unchanged"
    assert result.parts == result.rows == result.written_bytes == 0
    assert backend.objects == expected
    assert log == []


@pytest.mark.asyncio
async def test_absence_publication_keeps_a_retry_claim_when_marker_reads_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    backend, store, storage, _log = new_lane()
    bootstrap_lane(store, storage)
    storage.cas_succeeds = False
    original_get = backend.get
    original = source_absence()

    def unreadable(_key: str) -> bytes | None:
        raise OSError("object GET unavailable after terminal publication")

    async def adapter(_session: AsyncSession, store: ObjectStore, *, day: date, run_id: str) -> LaneRunResult:
        assert day == DAY
        assert run_id == RUN_ID
        receipt = govern_day_absent(store, original, layer=LANE, kind="observed", day=DAY)
        monkeypatch.setattr(backend, "get", unreadable)
        return LaneRunResult(part_count=0, row_count=0, byte_count=receipt.byte_count, absence_recorded=True)

    tally = AvailabilityExtensionTally()
    result = await fill_one_lane_day(
        cast("AsyncSession", RecordingSession()),
        store,
        replace(LANE_REGISTRY[LANE], adapter=adapter),
        day=DAY,
        today=CEILING,
        run_id=RUN_ID,
        now=lambda: NOW,
        lane_day_lock=unlocked_lane_day,
        availability_storage=storage,
        availability_tally=tally,
    )

    assert result[0] == "absent"
    assert tally.retry_owed == 1
    claim = json.loads(backend.objects[availability_retry_path(LANE, "observed", DAY)])
    assert claim["absence_reason"] == original.reason
    terminal_bytes = {absence_marker_path(LANE, "observed", rung, DAY): original.to_json_bytes() for rung in ZOOM_TIERS}
    monkeypatch.setattr(backend, "get", original_get)
    storage.cas_succeeds = True
    outcomes = await retry_pending_availability(
        cast("AsyncSession", RecordingSession()),
        store,
        lane=LANE,
        kind="observed",
        availability=storage,
        now=lambda: REPAIRED_AT,
        publication_barrier=granted_barrier,
    )
    assert [outcome.state for outcome in outcomes] == ["extended"]
    assert {key: backend.objects[key] for key in terminal_bytes} == terminal_bytes
