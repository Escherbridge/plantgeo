"""The write side: the S11 rewrite rules through real turns, and the production writer over a real object store.

S11 is proven three ways (plan 1B): a same-count digest change on a settled day, a partial
`write_and_recheck` day that gains units, and a transform input change (`test_conformance.py`).
"""

from __future__ import annotations

import contextlib
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.derivation import DerivationResult, DerivedTierReport
from agri_data_service.pipeline.parquet.gap_fill import no_derived_tiers, unlocked_lane_day
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.runner.contract import Absent, Written
from agri_data_service.pipeline.runner.reader import ObjectStoreLaneReader
from agri_data_service.pipeline.runner.receipts import DayReceipt, TurnReceipts
from agri_data_service.pipeline.runner.writer import (
    CompareModeWriteError,
    LaneDayContendedError,
    ObjectStoreLaneWriter,
    WritePermit,
    decide_rewrite,
)
from tests.parquet.availability_documents import MemoryAvailabilityStorage
from tests.parquet.test_gap_fill import RecordingSession
from tests.parquet.test_objectstore_writer import JULY_FOURTH, RecordingBackend, signal_rows
from tests.runner.fakes import (
    TODAY,
    ManualClock,
    MemoryLaneStore,
    ScriptedUpstream,
    days_between,
    grid_lane,
    point_lane,
    ports_for,
    run,
    spec_for,
)
from tests.runner.fixtures.grid_refuse import CELLS, GRID_STREAM, GridRefuseStrategy, GridWorld
from tests.runner.fixtures.point_recheck import POINT_STREAM, STATIONS, PointRecheckStrategy, PointWorld

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from agri_data_service.foundation.parquet.paths import PartitionDayStatus, PartitionKind

SIGNAL = "signal"
DAY = date(2026, 9, 1)
FULL = DayReceipt(
    stream="s", day=DAY, lane="l", outcome="written", source_digest="old", present_units=4, expected_units=4
)
PARTIAL = DayReceipt(
    stream="s", day=DAY, lane="l", outcome="written", source_digest="old", present_units=2, expected_units=4
)


@pytest.mark.parametrize(
    ("status", "receipt", "settlement", "digest", "policy", "reason"),
    [
        ("missing", None, Written(4, 4), "new", "refuse", "new"),
        ("incomplete", None, Written(4, 4), "new", "refuse", "new"),
        ("absent", None, Written(4, 4), "new", "refuse", "retract_disproven_absence"),
        ("data", None, Written(4, 4), "new", "refuse", "no_receipt"),
        ("data", FULL, Written(4, 4), "old", "refuse", "digest_unchanged"),
        ("data", FULL, Written(4, 4), "revised", "refuse", "digest_changed"),
        ("data", PARTIAL, Written(4, 3), "revised", "write_and_recheck", "more_units"),
        ("data", PARTIAL, Written(4, 2), "revised", "write_and_recheck", "no_more_units"),
        ("data", FULL, Written(4, 3), "revised", "write_and_recheck", "fewer_units"),
        ("data", FULL, Written(4, 4), "revised", "write_and_recheck", "digest_changed"),
        ("data", FULL, Absent("no_values", "later day published"), "x", "refuse", "data_never_retracted_by_absence"),
        ("missing", None, Absent("no_values", "later day published"), "x", "refuse", "absent"),
        ("absent", None, Absent("no_values", "later day published"), "x", "refuse", "absence_unchanged"),
    ],
)
def test_the_s11_rewrite_rules(  # noqa: PLR0913 - one parameter per column of the S11 table
    status: PartitionDayStatus, receipt: DayReceipt | None, settlement: Any, digest: str, policy: Any, reason: str
) -> None:
    """One row per S11 case: settled days rewrite on a digest change, partial days only on more units."""
    decision = decide_rewrite(
        status=status, receipt=receipt, settlement=settlement, source_digest=digest, partial_day=policy
    )

    assert decision.reason == reason


async def test_a_settled_day_is_rewritten_when_its_source_digest_changes_at_the_same_unit_count() -> None:
    """An ERA5T revision: the probe-gated fan-out re-reads the window for free and rewrites only the revised day."""
    edge = TODAY - timedelta(days=5)
    revised = edge - timedelta(days=5)
    store, world = MemoryLaneStore(), GridWorld()
    world.publish(days_between(edge - timedelta(days=13), edge - timedelta(days=1)))
    strategy = GridRefuseStrategy(span_days=14)
    await run(spec_for(grid_lane()), ports_for(strategy, store, upstream=ScriptedUpstream(world.answer)))
    before = store.tables[(GRID_STREAM, revised)]
    world.publish([edge])
    world.publish([revised], value=9.0)

    exit_code, report = await run(
        spec_for(grid_lane()), ports_for(strategy, store, upstream=ScriptedUpstream(world.answer))
    )

    assert exit_code == 0
    assert report["rewrite_reasons"] == {"digest_changed": 1, "digest_unchanged": 12, "new": 1}
    assert report["days_rechecked"] == len(days_between(edge - timedelta(days=13), edge - timedelta(days=1)))
    assert store.tables[(GRID_STREAM, revised)].column("value").to_pylist() == [
        9.0 + index for index in range(len(CELLS))
    ]
    assert store.tables[(GRID_STREAM, revised)].num_rows == before.num_rows
    assert store.status(GRID_STREAM, edge) == "data"


async def test_a_partial_recheck_day_is_rewritten_only_when_more_units_arrive() -> None:
    """`write_and_recheck`: a third station rewrites its day; a changed value at the same count does not."""
    yesterday = TODAY - timedelta(days=1)
    window = days_between(yesterday - timedelta(days=2), yesterday)
    world = PointWorld(readings={(day, station): 1.0 for day in window for station in ("s1", "s2")})
    store = MemoryLaneStore()
    await run(spec_for(point_lane()), ports_for(PointRecheckStrategy(), store, upstream=ScriptedUpstream(world.answer)))
    grows, drifts = window[1], window[0]
    world.readings[(grows, "s3")] = 1.0
    world.readings[(drifts, "s1")] = 7.0

    exit_code, report = await run(
        spec_for(point_lane()), ports_for(PointRecheckStrategy(), store, upstream=ScriptedUpstream(world.answer))
    )

    assert exit_code == 0
    assert store.tables[(POINT_STREAM, grows)].num_rows == len(STATIONS)
    assert store.tables[(POINT_STREAM, drifts)].column("value").to_pylist() == [1.0, 1.0]
    assert report["rewrite_reasons"] == {"more_units": 1, "no_more_units": 2}


def _coarse_rungs(  # noqa: PLR0913 - the signature IS the tier-deriver seam it replaces
    store: ObjectStore,
    *,
    layer: str,
    kind: PartitionKind,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
    **_: object,
) -> DerivationResult:
    """A tier deriver that writes real coarse objects without asking Polars to generalise the fixture rows."""
    reports: list[DerivedTierReport] = []
    for tier in ZOOM_TIERS:
        if tier == LANE_BASE_ZOOM_TIER:
            continue
        receipt = store.write_partition(signal_rows(), layer=layer, kind=kind, zoom=tier, day=day)
        store.write_completion_marker(
            PartitionCompletion(part_count=1, row_count=receipt.row_count, completed_at=now(), run_id=run_id),
            layer=layer,
            kind=kind,
            zoom=tier,
            day=day,
        )
        reports.append(
            DerivedTierReport(tier=tier, part_count=1, row_count=receipt.row_count, byte_count=receipt.byte_count)
        )
    return DerivationResult(tiers=tuple(reports), notes=())


@contextlib.asynccontextmanager
async def _held_elsewhere(session: object, key: str) -> AsyncIterator[bool]:  # noqa: ARG001 - the LaneDayLock shape
    """A lane-day lock another run holds."""
    yield False


def _production_writer(
    store: ObjectStore, availability: MemoryAvailabilityStorage, **overrides: Any
) -> ObjectStoreLaneWriter:
    options: dict[str, Any] = {"lane_day_lock": unlocked_lane_day, "derive_tiers": _coarse_rungs, **overrides}
    return ObjectStoreLaneWriter(
        permit=WritePermit.for_turn(compare=False),
        session=RecordingSession(),  # type: ignore[arg-type]
        store=store,
        availability_storage=availability,
        receipts=TurnReceipts(availability),
        run_id="runner-test",
        clock=ManualClock(),
        **options,
    )


async def test_the_production_writer_publishes_the_whole_ladder_and_its_turn_receipt() -> None:
    """Through `fill_one_lane_day`: every rung and marker, a census that reads `data`, the receipt, equal digests."""
    store, availability = ObjectStore(RecordingBackend()), MemoryAvailabilityStorage()
    writer = _production_writer(store, availability)
    reader = ObjectStoreLaneReader(store=store, receipts=TurnReceipts(availability))
    table = signal_rows()
    receipt = DayReceipt(
        stream=SIGNAL,
        day=JULY_FOURTH,
        lane="fixture",
        outcome="written",
        source_digest="digest-1",
        present_units=3,
        expected_units=3,
    )

    result = await writer.write_day(SIGNAL, JULY_FOURTH, table, receipt, availability=True)

    census = reader.census([SIGNAL], JULY_FOURTH, JULY_FOURTH)
    assert census.status(SIGNAL, JULY_FOURTH) == "data"
    assert result.rows == table.num_rows
    stored = reader.receipt(SIGNAL, JULY_FOURTH)
    assert stored is not None
    assert stored.source_digest == "digest-1"
    published = reader.read_published(SIGNAL, JULY_FOURTH)
    assert published is not None
    assert reader.canonical_digest(SIGNAL, published) == reader.canonical_digest(SIGNAL, table)
    assert reader.newest_data_day(SIGNAL) == JULY_FOURTH


async def test_a_pruned_stream_day_is_retracted_at_every_rung() -> None:
    """§4.6 prune: after the transform supersedes a provisional day, no rung of it is left serving."""
    store, availability = ObjectStore(RecordingBackend()), MemoryAvailabilityStorage()
    writer = _production_writer(store, availability)
    receipt = DayReceipt(stream=SIGNAL, day=JULY_FOURTH, lane="fixture", outcome="written")
    await writer.write_day(SIGNAL, JULY_FOURTH, signal_rows(), receipt, availability=False)

    result = await writer.prune(SIGNAL, JULY_FOURTH)

    reader = ObjectStoreLaneReader(store=store, receipts=TurnReceipts(availability))
    assert reader.census([SIGNAL], JULY_FOURTH, JULY_FOURTH).status(SIGNAL, JULY_FOURTH) == "missing"
    assert result.partitions == len(ZOOM_TIERS)
    assert reader.read_published(SIGNAL, JULY_FOURTH) is None


def test_a_compare_turn_cannot_construct_a_writer() -> None:
    """`--compare` holds a refusing permit, so the production writer cannot even be built for it."""
    availability = MemoryAvailabilityStorage()

    with pytest.raises(CompareModeWriteError):
        ObjectStoreLaneWriter(
            permit=WritePermit.for_turn(compare=True),
            session=RecordingSession(),  # type: ignore[arg-type]
            store=ObjectStore(RecordingBackend()),
            availability_storage=availability,
            receipts=TurnReceipts(availability),
            run_id="runner-test",
            clock=ManualClock(),
        )


async def test_the_production_writer_derives_a_coarse_blind_day_from_its_published_base() -> None:
    """H1 through `repair_one_lane_day`: a base-only day reads `incomplete`, and `data` after the repair."""
    store, availability = ObjectStore(RecordingBackend()), MemoryAvailabilityStorage()
    receipt = DayReceipt(stream=SIGNAL, day=JULY_FOURTH, lane="fixture", outcome="written")
    base_only = _production_writer(store, availability, derive_tiers=no_derived_tiers)
    await base_only.write_day(SIGNAL, JULY_FOURTH, signal_rows(), receipt, availability=False)
    reader = ObjectStoreLaneReader(store=store, receipts=TurnReceipts(availability))
    before = reader.census([SIGNAL], JULY_FOURTH, JULY_FOURTH).status(SIGNAL, JULY_FOURTH)

    result = await _production_writer(store, availability).repair_ladder(SIGNAL, JULY_FOURTH)

    assert before == "incomplete"
    assert reader.census([SIGNAL], JULY_FOURTH, JULY_FOURTH).status(SIGNAL, JULY_FOURTH) == "data"
    assert result.partitions == len(ZOOM_TIERS) - 1


async def test_a_lane_day_another_run_holds_is_refused_as_contended_and_nothing_is_written() -> None:
    """M4: the lock's refusal is the typed `contended` error the turn records, never exit 70."""
    store, availability = ObjectStore(RecordingBackend()), MemoryAvailabilityStorage()
    writer = _production_writer(store, availability, lane_day_lock=_held_elsewhere)
    receipt = DayReceipt(stream=SIGNAL, day=JULY_FOURTH, lane="fixture", outcome="written")

    with pytest.raises(LaneDayContendedError):
        await writer.write_day(SIGNAL, JULY_FOURTH, signal_rows(), receipt, availability=True)

    reader = ObjectStoreLaneReader(store=store, receipts=TurnReceipts(availability))
    assert reader.census([SIGNAL], JULY_FOURTH, JULY_FOURTH).status(SIGNAL, JULY_FOURTH) == "missing"
    assert reader.receipt(SIGNAL, JULY_FOURTH) is None
