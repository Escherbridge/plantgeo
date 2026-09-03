"""The gap-fill driver: newest-first ordering, round-robin fairness, budgets, and per-lane isolation.

Every test here runs against the in-memory `RecordingBackend` and a session fake that executes no
SQL -- the lane exports themselves are pinned by their own lane tests. What is pinned here is the
DRIVER's behaviour: which days it picks, in what order, what it does when one raises, and that a
`--dry-run` census writes nothing at all.

The driver writes and censuses ONE tier -- `GAP_FILL_ZOOM_TIER`, the base rung its lane adapters
export -- so every seed below lands there. The tests that seed another rung are the interesting
ones: a day present at `zoom=00` is not coverage of the base tier, and a census that thought it was
would report the lane complete and never fill the day.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.lane_contract import LaneNature, SourceWatermark
from agri_data_service.foundation.parquet.paths import (
    absence_marker_path,
    completion_marker_path,
    partition_path,
    try_parse_absence_marker_path,
    try_parse_partition_path,
)
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.interface.cli import cli
from agri_data_service.pipeline.parquet.derivation import DerivationResult
from agri_data_service.pipeline.parquet.gap_fill import (
    GAP_FILL_ZOOM_TIER,
    GapFillContractError,
    GapFillSummary,
    LaneGapCensus,
    LaneWatermarkReading,
    build_gap_census,
    gap_census_report,
    lane_window,
    no_derived_tiers,
    resolve_lane_watermarks,
    run_gap_fill,
    zero_row_absence_reason,
)
from agri_data_service.pipeline.parquet.lane_registry import (
    LaneRegistration,
    LaneRunResult,
    resolve_lanes,
)
from agri_data_service.pipeline.parquet.objectstore import (
    ABSENCE_CONTENT_TYPE,
    COMPLETION_CONTENT_TYPE,
    PARQUET_CONTENT_TYPE,
    EmptyPartitionError,
    ListedObject,
    ObjectStore,
)
from tests.parquet.test_objectstore_writer import BASE_TIER, WHOLE_WORLD_TIER, RecordingBackend

if TYPE_CHECKING:
    # A TYPE_CHECKING-only alias in `gap_fill`, exactly like `LaneDayLock` beside it, so it
    # must be imported the same way here rather than at runtime.
    from collections.abc import Collection, Iterator, Sequence

    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.gap_fill import TierDeriver

TODAY = date(2026, 8, 22)
RUN_ID = "parquet-gap-fill:test"
FROZEN_NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
UNLIMITED_BUDGET_SECONDS = 3600.0

WINDOW_DAYS = 5
FLOOR = date(2026, 8, 18)  # TODAY - 4, so a lag-0 lane has exactly WINDOW_DAYS days in its window


@dataclass(frozen=True, slots=True)
class LaneCall:
    """One adapter invocation the driver made, with everything it was handed."""

    slug: str
    day: date
    run_id: str
    store: ObjectStore
    session: object


def days_newest_first(count: int, *, last: date = TODAY) -> list[date]:
    """The days a lag-0 lane should attempt, newest first, counting back from `last`."""
    return [date.fromordinal(last.toordinal() - offset) for offset in range(count)]


def pairs(calls: list[LaneCall]) -> list[tuple[str, date]]:
    """Flatten recorded calls to `(slug, day)` in attempt order -- what most of these tests assert on."""
    return [(call.slug, call.day) for call in calls]


class GrantedLock:
    """The one-column result `pg_try_advisory_lock` returns, canned as granted."""

    def __init__(self, granted: bool = True) -> None:
        self.granted = granted

    def scalar(self) -> bool:
        return self.granted


class RecordingSession:
    """Records the statement-timeout pins and rollbacks the driver issues; executes no real SQL.

    `execute` answers every statement with a GRANTED advisory lock, because the driver now takes one
    per lane-day and these tests are not about contention. Answering here rather than making every
    test pass `lane_day_lock=` keeps the fake faithful to the real session -- an uncontended Postgres
    grants the lock -- and leaves the injectable seam for the tests that ARE about serialisation,
    which inject a lock yielding `False`.
    """

    def __init__(self, lock_granted: bool = True) -> None:
        self.statements: list[str] = []
        self.bound: list[dict[str, Any] | None] = []
        self.rollbacks = 0
        self.lock_granted = lock_granted

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> GrantedLock:
        self.statements.append(str(statement))
        self.bound.append(params)
        return GrantedLock(self.lock_granted)

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeClock:
    """A monotonic clock advancing `step` seconds per read, so the budget test is deterministic."""

    def __init__(self, step: float = 1.0) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


class RaisingBackend(RecordingBackend):
    """A backend whose listing fails, standing in for a bucket this tick cannot read."""

    def list_objects(self, prefix: str) -> Iterator[ListedObject]:
        raise OSError(f"bucket unreachable while listing {prefix}")


def stub_lane(  # noqa: PLR0913 - one knob per behaviour a driver test needs to provoke
    slug: str,
    calls: list[LaneCall],
    *,
    floor: date = FLOOR,
    lag: int = 0,
    nature: LaneNature = "daily_series",
    raises_on: Collection[date] = (),
    empty_on: Collection[date] = (),
    watermark_day: date | None = None,
    watermark_raises: bool = False,
) -> LaneRegistration:
    """A registration whose adapter records everything it was handed, then behaves as scripted."""

    async def adapter(
        session: Any,
        store: ObjectStore,
        *,
        day: date,
        run_id: str,
    ) -> LaneRunResult:
        calls.append(LaneCall(slug=slug, day=day, run_id=run_id, store=store, session=session))
        if day in empty_on:
            raise EmptyPartitionError(f"refusing to write a zero-row {slug!r} observed partition for {day}")
        if day in raises_on:
            raise RuntimeError(f"{slug} export blew up on {day}")
        return LaneRunResult(part_count=1, row_count=5, byte_count=50, absence_recorded=False)

    async def watermark(_session: Any, _store: ObjectStore, *, today: date) -> SourceWatermark:  # noqa: ARG001
        if watermark_raises:
            raise OSError(f"{slug} watermark query blew up")
        return SourceWatermark(day=watermark_day, basis=f"test fixture watermark for {slug}")

    is_static = nature == "static_lookup"
    return LaneRegistration(
        slug=slug,
        adapter=adapter,
        history_floor=floor,
        publication_lag_days=0 if is_static else lag,
        nature=nature,
        floor_basis=f"test fixture for {slug}",
        watermark=watermark if is_static else None,
    )


ZONES = "evacuation-zones"
VERSION_DAY = date(2026, 8, 20)
# Four instants on ONE UTC day: a version stamp cannot tell them apart, which is the whole point.
BE_READY_AT = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
GO_NOW_AT = datetime(2026, 8, 20, 14, 0, tzinfo=UTC)
AFTER_GO_NOW = datetime(2026, 8, 20, 15, 0, tzinfo=UTC)
LATER_CHANGE = datetime(2026, 8, 20, 16, 0, tzinfo=UTC)
LATEST_CHANGE = datetime(2026, 8, 20, 17, 0, tzinfo=UTC)
ZONES_BASIS = "evacuation-zones: GREATEST(max(updated_at)) over 12 published rows"


def seed_parts(backend: RecordingBackend, store: ObjectStore, *, count: int, at: datetime, day: date) -> None:
    """Land `count` part files over one stream-day, all stamped as one export at `at`."""
    for part_index in range(count):
        key = store.key_for(partition_path(ZONES, "observed", BASE_TIER, day, part_index))
        backend.put(key, b"parquet", content_type=PARQUET_CONTENT_TYPE)
        backend.set_last_modified(key, at)


def held_parts(store: ObjectStore) -> tuple[str, ...]:
    """Every PART FILE the zones stream currently holds, as relative paths -- orphans included.

    The listing returns all three object kinds, so absence and completion markers are filtered out
    here: this helper answers "which parts are published", and a prune's whole subject is parts.
    """
    return tuple(
        key
        for key in store.list_partition_keys(ZONES, "observed", BASE_TIER)
        if try_parse_partition_path(key) is not None
    )


def static_lane(  # noqa: PLR0913 - one knob per behaviour the static driver path needs to provoke
    backend: RecordingBackend,
    *,
    part_counts: Sequence[int],
    export_instants: Sequence[datetime],
    watermark_instants: Sequence[datetime | None] = (),
    version_day: date = VERSION_DAY,
    exports: list[int] | None = None,
) -> LaneRegistration:
    """A `static_lookup` lane that REALLY writes part files, so a shrinking re-export can be watched.

    Each element of `part_counts` is one export's whole population and `export_instants` stamps it,
    standing in for the store's own `LastModified`. `watermark_instants` is consumed one READ at a
    time, so "the source changed during this export's window" is expressed as two different values
    inside a single export's bracket. Both sequences hold their last value once exhausted.
    """
    reads = [0]
    written = exports if exports is not None else []

    def at(values: Sequence[Any], index: int) -> Any:
        return values[min(index, len(values) - 1)]

    async def adapter(session: Any, store: ObjectStore, *, day: date, run_id: str) -> LaneRunResult:  # noqa: ARG001
        count = at(part_counts, len(written))
        written.append(count)
        seed_parts(backend, store, count=count, at=at(export_instants, len(written) - 1), day=day)
        return LaneRunResult(part_count=count, row_count=count * 5, byte_count=count * 50, absence_recorded=False)

    async def watermark(_session: Any, _store: ObjectStore, *, today: date) -> SourceWatermark:  # noqa: ARG001
        instant = at(watermark_instants, reads[0]) if watermark_instants else None
        reads[0] += 1
        return SourceWatermark(day=version_day, instant=instant, basis=ZONES_BASIS)

    return LaneRegistration(
        slug=ZONES,
        adapter=adapter,
        history_floor=version_day,
        publication_lag_days=0,
        nature="static_lookup",
        floor_basis=f"test fixture for {ZONES}",
        watermark=watermark,
    )


def seed_partition(backend: RecordingBackend, slug: str, day: date, *, zoom: ZoomTier = BASE_TIER) -> None:
    """Land a part file for one stream-day at one tier, at exactly the key the writer's layout would use.

    The default is the tier the driver itself fills; a test naming another one is asserting that the
    driver does NOT read it as coverage.
    """
    backend.put(partition_path(slug, "observed", zoom, day), b"parquet", content_type=PARQUET_CONTENT_TYPE)


def seed_absence(backend: RecordingBackend, slug: str, day: date) -> None:
    """Land a governed-absence marker for one stream-day, exactly as `write_absence` would."""
    marker = GovernedAbsence(reason="seeded", upstream_response="seeded", recorded_at=FROZEN_NOW, run_id="seed")
    backend.put(
        absence_marker_path(slug, "observed", BASE_TIER, day), marker.to_json_bytes(), content_type=ABSENCE_CONTENT_TYPE
    )


def seed_completion(
    backend: RecordingBackend, slug: str, day: date, *, part_count: int = 1, zoom: ZoomTier = BASE_TIER
) -> None:
    """Land a completion marker for one stream-day, asserting that an export finished.

    Days seeded by `seed_partition` or `seed_parts` now need this marker to count as `data` rather
    than `incomplete`. The driver writes it automatically; manual seeds do not.
    """
    marker = PartitionCompletion(
        part_count=part_count, row_count=part_count * 5, completed_at=FROZEN_NOW, run_id="seed"
    )
    backend.put(
        completion_marker_path(slug, "observed", zoom, day),
        marker.to_json_bytes(),
        content_type=COMPLETION_CONTENT_TYPE,
    )


@dataclass
class EmptyAvailabilityStorage:
    """An availability storage holding NOTHING: every read misses, and a write would be a bug.

    It is the cheapest honest probe of the driver's wiring. A lane with no generation has nothing to
    extend, so the extension must read the pointer exactly once per finalized day, write nothing at
    all, and say so on the day rather than failing it.
    """

    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)

    def read(self, key: str, *, max_bytes: int) -> None:
        del max_bytes
        self.reads.append(key)

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        del payload, content_type
        self.writes.append(key)

    def compare_and_swap(
        self,
        key: str,
        payload: bytes,
        *,
        expected_etag: str | None,
        content_type: str,
    ) -> bool:
        del payload, expected_etag, content_type
        self.writes.append(key)
        return True


async def drive(  # noqa: PLR0913 - one knob per driver parameter a test needs to vary
    lanes: list[LaneRegistration],
    store: ObjectStore,
    *,
    session: RecordingSession | None = None,
    time_budget_seconds: float = UNLIMITED_BUDGET_SECONDS,
    max_days_per_lane: int | None = None,
    monotonic: FakeClock | None = None,
    derive_tiers: TierDeriver = no_derived_tiers,
    extend_availability: bool = True,
    availability_storage: EmptyAvailabilityStorage | None = None,
) -> GapFillSummary:
    """Run one tick against the frozen clock, day and run id every test in this module shares.

    THE COARSE RUNGS ARE STUBBED OUT BY DEFAULT, the same way this module never fakes a real
    advisory lock. Deriving them reads the base rung BACK from the store, so with the real deriver
    every stub lane here would first have to write a schema-conforming Parquet part before its day
    could close -- and each of these tests would become a test about Parquet schemas rather than
    about budgets, watermarks and per-lane isolation. `tests/parquet/test_tier_derivation.py` owns
    the ladder; a test here that wants it passes the real one.
    """
    return await run_gap_fill(
        session if session is not None else RecordingSession(),  # type: ignore[arg-type]
        store,
        lanes=lanes,
        today=TODAY,
        run_id=RUN_ID,
        time_budget_seconds=time_budget_seconds,
        max_days_per_lane=max_days_per_lane,
        monotonic=monotonic if monotonic is not None else FakeClock(step=0.0),
        now=lambda: FROZEN_NOW,
        derive_tiers=derive_tiers,
        extend_availability=extend_availability,
        availability_storage=availability_storage,
    )


@pytest.mark.asyncio
async def test_missing_days_are_attempted_newest_first() -> None:
    """The design: a newly published day IS the newest missing day, so one ordering serves both jobs."""
    calls: list[LaneCall] = []
    store = ObjectStore(RecordingBackend())

    summary = await drive([stub_lane("signal", calls)], store)

    assert [call.day for call in calls] == days_newest_first(WINDOW_DAYS)
    assert {call.run_id for call in calls} == {RUN_ID}
    assert summary.lanes[0].written == WINDOW_DAYS
    assert summary.lanes[0].outcome == "filled"
    assert not summary.failed


@pytest.mark.asyncio
async def test_a_day_carrying_an_absence_marker_is_not_re_attempted() -> None:
    """Otherwise a day the source truly has nothing for is re-queried on every tick, forever."""
    calls: list[LaneCall] = []
    backend = RecordingBackend()
    newest, second, third = days_newest_first(3)
    seed_partition(backend, "signal", newest)
    seed_completion(backend, "signal", newest)
    seed_absence(backend, "signal", second)

    summary = await drive([stub_lane("signal", calls)], ObjectStore(backend))

    assert [call.day for call in calls] == days_newest_first(WINDOW_DAYS)[2:]
    assert calls[0].day == third
    assert summary.lanes[0].considered == WINDOW_DAYS - 2


@pytest.mark.asyncio
async def test_a_day_published_at_another_tier_is_not_coverage_of_the_tier_being_filled() -> None:
    """THE BLENDING TRAP at driver scale: a z0 part file says nothing about whether the base tier exists.

    The day is seeded at the whole-world rung only. Counted as coverage, the newest day would be
    dropped from the walk and the base tier would stay empty for as long as the coarse object
    survives -- a gap the census would keep reporting as filled. Contrast
    `test_a_day_carrying_an_absence_marker_is_not_re_attempted`, where coverage at the SAME tier
    correctly removes the day.
    """
    calls: list[LaneCall] = []
    backend = RecordingBackend()
    newest = days_newest_first(1)[0]
    seed_partition(backend, "signal", newest, zoom=WHOLE_WORLD_TIER)

    summary = await drive([stub_lane("signal", calls)], ObjectStore(backend))

    assert [call.day for call in calls] == days_newest_first(WINDOW_DAYS)
    assert summary.lanes[0].considered == WINDOW_DAYS


@pytest.mark.asyncio
async def test_lanes_are_walked_round_robin_so_a_deep_lane_cannot_starve_a_shallow_one() -> None:
    """Sequential order would let fire-detections' ~9,400-day window eat a tick before signal wrote."""
    calls: list[LaneCall] = []
    store = ObjectStore(RecordingBackend())
    lanes = [
        stub_lane("signal", calls, floor=date(2026, 8, 20)),
        stub_lane("water-gauges", calls, floor=date(2026, 8, 20)),
    ]

    await drive(lanes, store)

    newest, second, third = days_newest_first(3)
    assert pairs(calls) == [
        ("signal", newest),
        ("water-gauges", newest),
        ("signal", second),
        ("water-gauges", second),
        ("signal", third),
        ("water-gauges", third),
    ]


@pytest.mark.asyncio
async def test_the_time_budget_stops_the_walk_and_reports_what_remains() -> None:
    """A partially drained backlog is the expected steady state of a multi-tick driver, not a failure."""
    calls: list[LaneCall] = []
    store = ObjectStore(RecordingBackend())
    lane = stub_lane("signal", calls, floor=date(2026, 8, 1))
    attempted_before_the_budget_ran_out = 2

    summary = await drive([lane], store, time_budget_seconds=5.0, monotonic=FakeClock(step=1.0))

    verdict = summary.lanes[0]
    assert len(calls) == attempted_before_the_budget_ran_out
    assert verdict.outcome == "budget_exhausted"
    assert verdict.written == attempted_before_the_budget_ran_out
    assert verdict.remaining == verdict.considered - attempted_before_the_budget_ran_out
    assert verdict.remaining > 0
    assert not summary.failed, "a remaining backlog must not red the cron run"


@pytest.mark.asyncio
async def test_one_lane_raising_does_not_abort_the_others() -> None:
    """Per-lane isolation is the whole point: an unrelated fault must not starve every other stream."""
    calls: list[LaneCall] = []
    store = ObjectStore(RecordingBackend())
    newest = days_newest_first(1)[0]
    lanes = [
        stub_lane("signal", calls, raises_on={newest}),
        stub_lane("water-gauges", calls),
    ]

    summary = await drive(lanes, store)

    signal, water_gauges = summary.lanes
    assert signal.outcome == "raised"
    assert signal.detail is not None
    assert newest.isoformat() in signal.detail
    assert "RuntimeError" in signal.detail
    assert water_gauges.outcome == "filled"
    assert water_gauges.written == WINDOW_DAYS
    assert summary.failed
    assert summary.to_summary()["failing_lanes"] == ["signal"]


@pytest.mark.asyncio
async def test_a_raised_lane_stops_taking_turns_rather_than_burning_the_tick() -> None:
    """Its next day would fail identically; rediscovering that costs every other lane its rounds."""
    calls: list[LaneCall] = []
    store = ObjectStore(RecordingBackend())
    newest = days_newest_first(1)[0]

    summary = await drive([stub_lane("signal", calls, raises_on={newest})], store)

    assert pairs(calls) == [("signal", newest)]
    assert summary.lanes[0].remaining == WINDOW_DAYS - 1


@pytest.mark.asyncio
async def test_a_zero_row_day_becomes_an_absence_that_never_claims_the_upstream_was_asked() -> None:
    calls: list[LaneCall] = []
    backend = RecordingBackend()
    newest = days_newest_first(1)[0]

    summary = await drive([stub_lane("signal", calls, empty_on={newest})], ObjectStore(backend))

    marker_key = absence_marker_path("signal", "observed", BASE_TIER, newest)
    assert backend.content_types[marker_key] == ABSENCE_CONTENT_TYPE
    absence = GovernedAbsence.from_json_bytes(backend.objects[marker_key])
    assert absence.run_id == RUN_ID
    assert absence.recorded_at == FROZEN_NOW
    assert newest.isoformat() in absence.reason
    assert "returned 0 rows" in absence.upstream_response
    # The key already carries the tier; the evidence repeats it, so a marker read on its own still
    # settles one rung rather than reading as a claim about the whole ladder. The REASON does not
    # repeat it: every rung of one absent day must agree on why, or no availability ladder can bind
    # them together as one outcome.
    assert f"zoom tier {GAP_FILL_ZOOM_TIER}" in absence.upstream_response
    assert f"z{GAP_FILL_ZOOM_TIER}" not in absence.reason
    assert "DID NOT CONTACT THE UPSTREAM SOURCE SYSTEM" in absence.upstream_response
    assert summary.lanes[0].absent == 1
    assert summary.lanes[0].written == WINDOW_DAYS - 1
    assert not summary.failed


@pytest.mark.asyncio
async def test_an_absent_day_is_marked_absent_at_every_rung_with_the_censused_one_last() -> None:
    """An empty day is empty at every RESOLUTION of itself, and a z9 reader must be told so.

    The base rung goes LAST for the reason `_finalize_written_day` derives before it marks: the
    census reads the base tier alone, so a run that died after the base marker would leave a day
    that is covered, never revisited, and silent at every rung above it.
    """
    calls: list[LaneCall] = []
    backend = RecordingBackend()
    newest = days_newest_first(1)[0]

    summary = await drive([stub_lane("signal", calls, empty_on={newest})], ObjectStore(backend))

    written_order = [key for key in backend.objects if try_parse_absence_marker_path(key) is not None]
    assert written_order == [
        absence_marker_path("signal", "observed", tier, newest)
        for tier in (*(rung for rung in ZOOM_TIERS if rung != BASE_TIER), BASE_TIER)
    ]
    reasons = {GovernedAbsence.from_json_bytes(backend.objects[key]).reason for key in written_order}
    assert reasons == {zero_row_absence_reason("signal", newest)}
    assert summary.lanes[0].absent == 1


@pytest.mark.asyncio
async def test_every_terminal_day_reaches_the_availability_step_exactly_once() -> None:
    """Written days and absent days alike: each is offered to the index once, and neither fails over it.

    THE TALLY IS THE OBSERVABLE HERE, NOT THE POINTER READ. This module stubs the coarse rungs out
    by default (`drive`), so a written day holds only its base rung and the extension refuses it
    from the LEDGER ALONE -- before it ever reads the pointer -- as `ladder_incomplete`. That
    refusal is the availability step, and counting it is the only way the loss is reported as a
    number instead of being buried in one day's detail string. The absent day marks every rung, so
    its ladder is complete and it alone gets as far as the pointer.
    """
    calls: list[LaneCall] = []
    backend = RecordingBackend()
    newest = days_newest_first(1)[0]
    availability = EmptyAvailabilityStorage()

    summary = await drive(
        [stub_lane("signal", calls, empty_on={newest})],
        ObjectStore(backend),
        availability_storage=availability,
    )

    tally = summary.availability
    assert sum(tally.to_summary().values()) == WINDOW_DAYS, "each terminal day is counted once, and only once"
    assert tally.not_bootstrapped == 1
    assert tally.ladder_incomplete == WINDOW_DAYS - 1
    pointer_reads = [key for key in availability.reads if key.endswith("/availability/_LATEST.json")]
    assert pointer_reads == ["layer=signal/kind=observed/availability/_LATEST.json"]
    # A lane with no generation has nothing to extend, so nothing may be written into one.
    assert availability.writes == []
    assert summary.lanes[0].absent == 1
    assert summary.lanes[0].written == WINDOW_DAYS - 1
    assert not summary.failed
    assert "availability ladder_incomplete" in (summary.lanes[0].detail or "")


@pytest.mark.asyncio
async def test_the_availability_step_can_be_switched_off_for_one_run() -> None:
    """The seam a caller disables: the day still completes and the index is never even read."""
    calls: list[LaneCall] = []
    availability = EmptyAvailabilityStorage()

    summary = await drive(
        [stub_lane("signal", calls)],
        ObjectStore(RecordingBackend()),
        extend_availability=False,
        availability_storage=availability,
    )

    assert availability.reads == []
    assert summary.lanes[0].written == WINDOW_DAYS
    assert summary.lanes[0].detail is None


@pytest.mark.asyncio
async def test_a_recorded_absence_is_covered_on_the_next_tick() -> None:
    """The marker is the memory: without it the same empty day is re-exported on every tick."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    newest = days_newest_first(1)[0]
    first_calls: list[LaneCall] = []
    await drive([stub_lane("signal", first_calls, empty_on={newest})], store)

    second_calls: list[LaneCall] = []
    await drive([stub_lane("signal", second_calls, empty_on={newest})], store)

    # The stub writes no part file, so the other four days stay missing on purpose -- what this pins
    # is that the ONE day the driver marked absent is now covered and is never handed to a lane again.
    assert newest in [call.day for call in first_calls]
    assert absence_marker_path("signal", "observed", BASE_TIER, newest) in backend.objects
    assert newest not in [call.day for call in second_calls]
    assert [call.day for call in second_calls] == days_newest_first(WINDOW_DAYS)[1:]


@pytest.mark.asyncio
async def test_the_session_is_rolled_back_after_every_day_and_the_timeout_re_pinned() -> None:
    """`SET LOCAL` dies with each rollback, and holding one read snapshot across a tick pins xmin."""
    calls: list[LaneCall] = []
    session = RecordingSession()

    await drive([stub_lane("signal", calls)], ObjectStore(RecordingBackend()), session=session)

    assert session.rollbacks == WINDOW_DAYS
    # Counted by KIND, not in total: each lane-day now also takes and releases its advisory lock, so
    # a bare length assertion would break every time the driver learns another per-day statement
    # while saying nothing about the property this test is actually about.
    pins = [statement for statement in session.statements if "statement_timeout" in statement]
    assert len(pins) == WINDOW_DAYS, "the timeout must be re-pinned once per day; SET LOCAL dies with each rollback"
    lane_locks = [statement for statement in session.statements if "pg_try_advisory_lock_shared" in statement]
    day_locks = [statement for statement in session.statements if "pg_try_advisory_lock(" in statement]
    lane_unlocks = [statement for statement in session.statements if "pg_advisory_unlock_shared" in statement]
    day_unlocks = [statement for statement in session.statements if "pg_advisory_unlock(" in statement]
    assert len(lane_locks) == WINDOW_DAYS, "every writer enters its lane barrier exactly once"
    assert len(day_locks) == WINDOW_DAYS, "every lane-day is claimed exactly once"
    assert len(lane_unlocks) == WINDOW_DAYS, "every lane barrier is released"
    assert len(day_unlocks) == WINDOW_DAYS, "every day lock is released"


@pytest.mark.asyncio
async def test_a_listing_failure_fails_that_lane_and_only_that_lane() -> None:
    """Not reading the bucket is a different claim from finding no gaps, and must not read as the latter."""
    calls: list[LaneCall] = []

    summary = await drive([stub_lane("signal", calls)], ObjectStore(RaisingBackend()))

    assert calls == []
    assert summary.lanes[0].outcome == "raised"
    assert summary.lanes[0].detail is not None
    assert "OSError" in summary.lanes[0].detail
    assert summary.failed


@pytest.mark.asyncio
async def test_a_static_lane_writes_one_snapshot_dated_at_its_watermark_not_at_the_run_date() -> None:
    """A HUC12 boundary is a reference fact with a version; the partition day is that version stamp."""
    calls: list[LaneCall] = []
    store = ObjectStore(RecordingBackend())
    changed_on = date(2026, 8, 7)
    lane = stub_lane("watersheds", calls, nature="static_lookup", watermark_day=changed_on)

    summary = await drive([lane], store)

    assert pairs(calls) == [("watersheds", changed_on)]
    assert changed_on != TODAY, "the point of the model is that the run date does not date the snapshot"
    assert summary.lanes[0].considered == 1
    assert summary.lanes[0].written == 1


@pytest.mark.asyncio
async def test_a_static_lane_at_its_watermark_is_current_and_writes_nothing() -> None:
    """Not a gap, not an absence, just current -- and reported with its own outcome, never `complete`."""
    calls: list[LaneCall] = []
    backend = RecordingBackend()
    changed_on = date(2026, 8, 7)
    seed_partition(backend, "watersheds", changed_on)
    seed_completion(backend, "watersheds", changed_on)
    lane = stub_lane("watersheds", calls, nature="static_lookup", watermark_day=changed_on)

    summary = await drive([lane], ObjectStore(backend))

    assert calls == [], "a current reference set must not be re-exported on every tick"
    verdict = summary.lanes[0]
    assert verdict.outcome == "current"
    assert verdict.detail is not None
    assert "is current" in verdict.detail
    assert not summary.failed


@pytest.mark.asyncio
async def test_a_version_written_after_the_watermark_still_counts_as_current() -> None:
    """The rule is "at or after", so the legacy daily re-snapshots already in the bucket satisfy it."""
    calls: list[LaneCall] = []
    backend = RecordingBackend()
    seed_partition(backend, "watersheds", TODAY)
    seed_completion(backend, "watersheds", TODAY)
    lane = stub_lane("watersheds", calls, nature="static_lookup", watermark_day=date(2026, 8, 7))

    summary = await drive([lane], ObjectStore(backend))

    assert calls == []
    assert summary.lanes[0].outcome == "current"


@pytest.mark.asyncio
async def test_a_static_lane_whose_source_changed_again_writes_the_new_version() -> None:
    """The old version stays; a NEW partition lands at the new watermark day. Nothing is overwritten."""
    calls: list[LaneCall] = []
    backend = RecordingBackend()
    seed_partition(backend, "watersheds", date(2026, 8, 7))
    changed_again = date(2026, 8, 20)
    lane = stub_lane("watersheds", calls, nature="static_lookup", watermark_day=changed_again)

    summary = await drive([lane], ObjectStore(backend))

    assert pairs(calls) == [("watersheds", changed_again)]
    assert summary.lanes[0].outcome == "filled"


@pytest.mark.asyncio
async def test_a_static_lane_with_no_published_rows_is_source_empty_not_a_gap() -> None:
    """An empty source has no version to stamp, which is honest rather than a day owed."""
    calls: list[LaneCall] = []
    lane = stub_lane("soil-survey", calls, nature="static_lookup", watermark_day=None)

    summary = await drive([lane], ObjectStore(RecordingBackend()))

    assert calls == []
    assert summary.lanes[0].outcome == "no_window"
    assert summary.lanes[0].detail is not None
    assert "nothing to snapshot" in summary.lanes[0].detail
    assert not summary.failed


@pytest.mark.asyncio
async def test_an_unreadable_watermark_fails_that_lane_rather_than_reading_as_current() -> None:
    """ "We could not ask the source" must never render identically to "the source says we are current"."""
    calls: list[LaneCall] = []
    lane = stub_lane("watersheds", calls, nature="static_lookup", watermark_raises=True)

    summary = await drive([lane], ObjectStore(RecordingBackend()))

    assert calls == []
    assert summary.lanes[0].outcome == "raised"
    assert summary.lanes[0].detail is not None
    assert "OSError" in summary.lanes[0].detail
    assert summary.failed


@pytest.mark.asyncio
async def test_a_watermark_dated_after_today_is_refused_as_a_clock_disagreement() -> None:
    """Writing an observed partition dated in the future is never right, whatever the source claims."""
    calls: list[LaneCall] = []
    lane = stub_lane("watersheds", calls, nature="static_lookup", watermark_day=TODAY + timedelta(days=1))

    summary = await drive([lane], ObjectStore(RecordingBackend()))

    assert calls == []
    assert summary.lanes[0].outcome == "raised"
    assert summary.lanes[0].detail is not None
    assert "later than" in summary.lanes[0].detail
    assert summary.failed


def test_a_census_with_no_watermark_reports_unread_rather_than_zero_gaps() -> None:
    """A listing-only census did not look; saying so is the difference between honest and plausible."""
    calls: list[LaneCall] = []
    lane = stub_lane("watersheds", calls, nature="static_lookup", watermark_day=date(2026, 8, 7))

    census = build_gap_census([lane], ObjectStore(RecordingBackend()), today=TODAY)

    entry = census[0]
    assert entry.static_state == "watermark_unread"
    assert entry.missing_days == ()
    assert entry.source_watermark is None
    report = gap_census_report(census)
    assert report["static_lanes_unread"] == ["watersheds"]
    assert report["static_lanes_current"] == []
    assert report["lanes_with_gaps"] == []


def test_every_census_row_names_the_tier_it_was_taken_at() -> None:
    """A coverage row that cannot name its tier is not coverage: three of the four rungs went unexamined.

    The driver fills the BASE rung -- the ungeneralized population a lane's own export produces --
    and the coarser rungs are derived from those objects rather than from a day-scoped query, so
    this census is an answer about one rung and says which.
    """
    calls: list[LaneCall] = []
    lanes = [
        stub_lane("signal", calls),
        stub_lane("watersheds", calls, nature="static_lookup", watermark_day=date(2026, 8, 7)),
    ]

    census = build_gap_census(lanes, ObjectStore(RecordingBackend()), today=TODAY)

    assert GAP_FILL_ZOOM_TIER == BASE_TIER, "the driver fills the most detailed rung, the one nothing generalized"
    assert [entry.zoom for entry in census] == [GAP_FILL_ZOOM_TIER, GAP_FILL_ZOOM_TIER]
    rows = gap_census_report(census)["lanes"]
    assert [row["zoom"] for row in rows] == [GAP_FILL_ZOOM_TIER, GAP_FILL_ZOOM_TIER]  # type: ignore[union-attr]


def test_the_census_reports_nature_and_forecastability_for_every_lane() -> None:
    """An operator must be able to see at a glance which streams are static and which are series."""
    calls: list[LaneCall] = []
    lanes = [
        stub_lane("signal", calls),
        stub_lane("drought", calls, nature="release_series"),
        stub_lane("watersheds", calls, nature="static_lookup", watermark_day=date(2026, 8, 7)),
    ]

    report = gap_census_report(build_gap_census(lanes, ObjectStore(RecordingBackend()), today=TODAY))

    rows = {row["lane"]: row for row in report["lanes"]}  # type: ignore[union-attr]
    assert rows["signal"]["nature"] == "daily_series"
    assert rows["drought"]["nature"] == "release_series"
    assert rows["watersheds"]["nature"] == "static_lookup"
    assert all(row["forecastable"] is False for row in rows.values()), "stub lanes ship no forecaster"


def test_a_static_lane_is_refused_a_settled_window_rather_than_handed_a_plausible_one() -> None:
    """`current_snapshot` collapsed to "the newest settled day", which is what re-snapshotted daily."""
    calls: list[LaneCall] = []
    lane = stub_lane("watersheds", calls, nature="static_lookup", watermark_day=date(2026, 8, 7))

    with pytest.raises(GapFillContractError, match="has no settled window"):
        lane_window(lane, today=TODAY)


@pytest.mark.asyncio
async def test_only_static_lanes_are_asked_for_a_watermark() -> None:
    """A series lane is driven by its publication lag; two clocks on one lane would disagree."""
    calls: list[LaneCall] = []
    lanes = [
        stub_lane("signal", calls),
        stub_lane("watersheds", calls, nature="static_lookup", watermark_day=date(2026, 8, 7)),
    ]

    readings = await resolve_lane_watermarks(
        RecordingSession(),  # type: ignore[arg-type]
        ObjectStore(RecordingBackend()),
        lanes=lanes,
        today=TODAY,
    )

    assert set(readings) == {"watersheds"}
    assert readings["watersheds"] == LaneWatermarkReading(
        watermark=SourceWatermark(day=date(2026, 8, 7), basis="test fixture watermark for watersheds")
    )


@pytest.mark.asyncio
async def test_a_lane_whose_floor_has_not_settled_yet_is_reported_not_attempted() -> None:
    calls: list[LaneCall] = []
    lane = stub_lane("sensors", calls, floor=date(2026, 8, 21), lag=9)

    summary = await drive([lane], ObjectStore(RecordingBackend()))

    assert calls == []
    assert summary.lanes[0].outcome == "no_window"
    assert not summary.failed


@pytest.mark.asyncio
async def test_max_days_per_lane_caps_the_walk() -> None:
    calls: list[LaneCall] = []
    capped_days = 2

    summary = await drive([stub_lane("signal", calls)], ObjectStore(RecordingBackend()), max_days_per_lane=capped_days)

    assert [call.day for call in calls] == days_newest_first(capped_days)
    assert summary.lanes[0].considered == capped_days
    assert summary.lanes[0].remaining == 0


def test_the_census_counts_data_absent_and_missing_days_without_opening_a_file() -> None:
    """Gap detection is a listing, never a scan: `layer-lanes.md` section 4."""
    calls: list[LaneCall] = []
    backend = RecordingBackend()
    newest, second, third = days_newest_first(3)
    seed_partition(backend, "signal", newest)
    seed_completion(backend, "signal", newest)
    seed_absence(backend, "signal", second)

    census = build_gap_census([stub_lane("signal", calls)], ObjectStore(backend), today=TODAY)

    entry = census[0]
    assert entry.data_days == 1
    assert entry.absent_days == 1
    assert entry.conflict_days == 0
    assert entry.missing_days[0] == third
    assert entry.window_days == WINDOW_DAYS
    assert entry.floor_basis == "test fixture for signal"
    report = gap_census_report(census)
    assert report["missing_days"] == WINDOW_DAYS - 2
    assert report["lanes_with_gaps"] == ["signal"]
    assert report["lanes_with_errors"] == []


def test_dry_run_reports_the_census_and_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """This is how the cron is audited: a full gap census with not one object put."""
    backend = RecordingBackend()
    store = ObjectStore(backend)

    def _stub_from_settings(_cls: type[ObjectStore], _source: object = None) -> ObjectStore:
        return store

    monkeypatch.setattr(ObjectStore, "from_settings", classmethod(_stub_from_settings))

    result = CliRunner().invoke(cli, ["data", "parquet-gap-fill", "--layer", "water-gauges", "--dry-run"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["lane_count"] == 1
    assert report["lanes"][0]["lane"] == "water-gauges"
    assert report["lanes"][0]["history_floor"] == "2026-05-24"
    assert "docs/lanes/water-gauges.md" in report["lanes"][0]["floor_basis"]
    assert backend.objects == {}, "--dry-run must not write a single object"


def test_skip_watermarks_keeps_a_static_lane_dry_run_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `--dry-run` that silently opens the loader DSN is a footgun; this is the escape hatch.

    The loader DSN falls back to `DATABASE_URL`, which in this repo's own `.env` is PRODUCTION, so an
    operator auditing the census must be able to keep the verb entirely offline.
    """
    store = ObjectStore(RecordingBackend())

    def _stub_from_settings(_cls: type[ObjectStore], _source: object = None) -> ObjectStore:
        return store

    def _refuse_session(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("--skip-watermarks must not open a database session")

    monkeypatch.setattr(ObjectStore, "from_settings", classmethod(_stub_from_settings))
    monkeypatch.setattr("agri_data_service.interface.cli.commands.local_source_loader_session", _refuse_session)

    result = CliRunner().invoke(
        cli,
        ["data", "parquet-gap-fill", "--layer", "watersheds", "--dry-run", "--skip-watermarks"],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["static_lanes_unread"] == ["watersheds"]
    assert report["lanes"][0]["nature"] == "static_lookup"
    assert report["lanes"][0]["source_watermark"] is None
    assert report["lanes"][0]["missing_days"] == 0


def test_a_default_dry_run_over_a_static_lane_resolves_no_dsn_and_opens_no_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE PIN: a plain `--dry-run` must be offline. A dry run is safe by default or it is not a dry run.

    The loader DSN falls back to `DATABASE_URL`, which in this repo's own `.env` is the PRODUCTION
    Railway host, and this repo declares no `LOCAL_SOURCE_LOADER_DATABASE_URL`. `--skip-watermarks`
    alone did not close that, because an opt-in mitigation leaves the DEFAULT the prod-touching one.
    Both refusals below are load-bearing: `_read_gap_fill_watermarks` resolves the DSN before it ever
    opens a session, so refusing only the session would still let a DSN resolution slip through.
    """
    store = ObjectStore(RecordingBackend())

    def _stub_from_settings(_cls: type[ObjectStore], _source: object = None) -> ObjectStore:
        return store

    def _refuse_watermark_read(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a default --dry-run must resolve no loader DSN")

    def _refuse_session(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a default --dry-run must not open a database session")

    monkeypatch.setattr(ObjectStore, "from_settings", classmethod(_stub_from_settings))
    monkeypatch.setattr("agri_data_service.interface.cli.commands._read_gap_fill_watermarks", _refuse_watermark_read)
    monkeypatch.setattr("agri_data_service.interface.cli.commands.local_source_loader_session", _refuse_session)

    result = CliRunner().invoke(cli, ["data", "parquet-gap-fill", "--layer", "watersheds", "--dry-run"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["static_lanes_unread"] == ["watersheds"], "an unread lane must say so, never zero gaps"
    assert report["lanes"][0]["nature"] == "static_lookup"
    assert report["lanes"][0]["source_watermark"] is None


def test_read_watermarks_opts_back_in_and_still_prints_when_the_warehouse_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the DEFAULT moved: the opt-in still reads, and a census that cannot read still PRINTS."""
    store = ObjectStore(RecordingBackend())
    reads: list[str] = []

    def _stub_from_settings(_cls: type[ObjectStore], _source: object = None) -> ObjectStore:
        return store

    def _record_then_fail(*_args: object, **_kwargs: object) -> object:
        reads.append("attempted")
        raise ValueError("set LOCAL_SOURCE_LOADER_DATABASE_URL or DATABASE_URL")

    monkeypatch.setattr(ObjectStore, "from_settings", classmethod(_stub_from_settings))
    monkeypatch.setattr("agri_data_service.interface.cli.commands._read_gap_fill_watermarks", _record_then_fail)

    result = CliRunner().invoke(
        cli,
        ["data", "parquet-gap-fill", "--layer", "watersheds", "--dry-run", "--read-watermarks"],
    )

    assert reads == ["attempted"], "--read-watermarks must still reach the watermark read"
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["static_lanes_unread"] == ["watersheds"]
    assert "no source watermark was read" in report["lanes"][0]["error"]


def test_an_unknown_layer_is_refused_before_anything_is_listed() -> None:
    result = CliRunner().invoke(cli, ["data", "parquet-gap-fill", "--layer", "interventions", "--dry-run"])

    assert result.exit_code != 0
    assert "interventions" in result.output


def zones_census(store: ObjectStore, lane: LaneRegistration, *, instant: datetime | None) -> LaneGapCensus:
    """Census the zones lane against a watermark pinned at `instant`, through the REAL census path."""
    return build_gap_census(
        [lane],
        store,
        today=TODAY,
        watermarks={
            ZONES: LaneWatermarkReading(watermark=SourceWatermark(day=VERSION_DAY, instant=instant, basis=ZONES_BASIS))
        },
    )[0]


def test_the_census_feeds_the_stores_real_export_instant_into_the_resolver() -> None:
    """THE WIRING, not the resolver. Replacing `newest_data_instant` with `None` must fail here.

    The sub-day comparison was asserted only on the pure resolver with hand-built arguments, so the
    census could have passed the literal `None` and every test still passed. This drives the census
    against a store whose part file was stamped BEFORE the source changed, and the only way to reach
    `stale` is for the listing's instant to arrive at the resolver.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend)
    lane = static_lane(backend, part_counts=[1], export_instants=[BE_READY_AT])
    seed_parts(backend, store, count=1, at=BE_READY_AT, day=VERSION_DAY)
    seed_completion(backend, ZONES, VERSION_DAY, part_count=1)

    stale = zones_census(store, lane, instant=GO_NOW_AT)

    assert stale.static_state == "stale"
    assert stale.missing_days == (VERSION_DAY,)
    assert "BEFORE the source changed" in (stale.static_detail or "")
    # And the converse, so the wiring cannot pass by always answering `stale` either.
    assert zones_census(store, lane, instant=BE_READY_AT).static_state == "current"


def test_a_census_with_no_export_instant_still_falls_back_to_day_resolution() -> None:
    """The unstamped listing is the honest unknown, and must not be mistaken for a stale export."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    lane = static_lane(backend, part_counts=[1], export_instants=[BE_READY_AT])
    backend.put(
        store.key_for(partition_path(ZONES, "observed", BASE_TIER, VERSION_DAY, 0)),
        b"x",
        content_type=PARQUET_CONTENT_TYPE,
    )
    seed_completion(backend, ZONES, VERSION_DAY, part_count=1)

    census = zones_census(store, lane, instant=GO_NOW_AT)

    assert census.static_state == "current"
    assert "DAY-RESOLUTION" in (census.static_detail or "")


@pytest.mark.asyncio
async def test_a_shrinking_re_export_leaves_no_orphan_and_the_lane_converges_to_current() -> None:
    """THE BLOCKER, end to end through the driver: stale -> re-export -> the SAME day -> current.

    Day D holds four parts exported before the source changed, so the lane is stale. The re-export's
    population has shrunk to two. Without the prune, `part-2` and `part-3` survive holding the
    SUPERSEDED evacuation levels, `oldest_export_instant` keeps answering the OLD instant, and the
    lane is judged stale again on every tick forever -- re-exporting the whole population each time.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend, prefix="sandbox")
    exports: list[int] = []
    lane = static_lane(
        backend,
        part_counts=[2],
        export_instants=[AFTER_GO_NOW],
        watermark_instants=[GO_NOW_AT],
        exports=exports,
    )
    seed_parts(backend, store, count=4, at=BE_READY_AT, day=VERSION_DAY)
    assert zones_census(store, lane, instant=GO_NOW_AT).static_state == "stale"

    summary = await drive([lane], store)

    assert exports == [2], "the stale lane must re-export exactly once"
    assert summary.lanes[0].outcome == "filled"
    assert held_parts(store) == (
        partition_path(ZONES, "observed", BASE_TIER, VERSION_DAY, 0),
        partition_path(ZONES, "observed", BASE_TIER, VERSION_DAY, 1),
    ), "the two parts this smaller export did not write must be gone, not published beside it"
    # The latch is gone with them: the day's oldest and newest part now agree on the new export.
    assert zones_census(store, lane, instant=GO_NOW_AT).static_state == "current"


@pytest.mark.asyncio
async def test_the_post_export_prune_never_reaches_another_tiers_parts_of_the_same_day() -> None:
    """A coarser rung of this version is a different RESOLUTION of it, never an older export of it.

    The base tier shrinks from four parts to two, so `part-2` and `part-3` are surplus there. The z0
    object at the same day and the same index was written by the derivation step, which this export
    never replaced -- deleting it would silently drop the tier that serves whole-world viewports and
    leave nothing to say it had gone.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend, prefix="sandbox")
    lane = static_lane(backend, part_counts=[2], export_instants=[AFTER_GO_NOW], watermark_instants=[GO_NOW_AT])
    seed_parts(backend, store, count=4, at=BE_READY_AT, day=VERSION_DAY)
    derived = partition_path(ZONES, "observed", WHOLE_WORLD_TIER, VERSION_DAY, 3)
    backend.put(store.key_for(derived), b"parquet", content_type=PARQUET_CONTENT_TYPE)

    summary = await drive([lane], store)

    assert summary.lanes[0].outcome == "filled"
    assert held_parts(store) == (
        partition_path(ZONES, "observed", BASE_TIER, VERSION_DAY, 0),
        partition_path(ZONES, "observed", BASE_TIER, VERSION_DAY, 1),
    )
    assert backend.deleted == [
        store.key_for(partition_path(ZONES, "observed", BASE_TIER, VERSION_DAY, 2)),
        store.key_for(partition_path(ZONES, "observed", BASE_TIER, VERSION_DAY, 3)),
    ]
    assert store.key_for(derived) in backend.objects


@pytest.mark.asyncio
async def test_a_source_that_moves_during_the_export_window_forces_a_re_export() -> None:
    """The export instant is PUT time, so a change between the select and the upload reads as captured.

    The watermark is read on each side of the export. Two different values mean the source moved
    inside the window and the snapshot's vintage is unknown, so the day is exported again rather
    than published as current on the strength of an upload instant that proves nothing.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend)
    exports: list[int] = []
    lane = static_lane(
        backend,
        part_counts=[2],
        export_instants=[AFTER_GO_NOW],
        # Read 0 is the census's own. Reads 1 and 2 bracket export 1 and DIFFER, so it raced; reads
        # 3 and 4 bracket export 2 and agree, so it settles. The sequence holds its last value.
        watermark_instants=[GO_NOW_AT, GO_NOW_AT, LATER_CHANGE],
        exports=exports,
    )
    seed_parts(backend, store, count=1, at=BE_READY_AT, day=VERSION_DAY)

    summary = await drive([lane], store)

    assert exports == [2, 2], "the raced export must be redone, and the quiet retry must settle it"
    verdict = summary.lanes[0]
    assert verdict.outcome == "filled"
    assert "DURING attempt 1" in (verdict.detail or "")


@pytest.mark.asyncio
async def test_a_source_racing_every_attempt_is_reported_rather_than_latched_current() -> None:
    """A bounded retry that gives up LOUDLY is the correct failure mode; silence is the one being fixed."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    exports: list[int] = []
    lane = static_lane(
        backend,
        part_counts=[2],
        export_instants=[AFTER_GO_NOW],
        # Both brackets straddle a change: reads 1/2 differ, and so do reads 3/4.
        watermark_instants=[GO_NOW_AT, GO_NOW_AT, LATER_CHANGE, LATER_CHANGE, LATEST_CHANGE],
        exports=exports,
    )
    seed_parts(backend, store, count=1, at=BE_READY_AT, day=VERSION_DAY)

    summary = await drive([lane], store)

    assert exports == [2, 2], "bounded: it must not retry forever"
    detail = summary.lanes[0].detail or ""
    assert "every one of 2 export attempts" in detail
    assert "re-export it" in detail


@pytest.mark.asyncio
async def test_a_quiet_source_exports_once_and_says_nothing_about_racing() -> None:
    """The bracket must be silent on the ordinary path, or every static export would read as suspect."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    exports: list[int] = []
    lane = static_lane(
        backend,
        part_counts=[2],
        export_instants=[AFTER_GO_NOW],
        watermark_instants=[GO_NOW_AT],
        exports=exports,
    )
    seed_parts(backend, store, count=1, at=BE_READY_AT, day=VERSION_DAY)

    summary = await drive([lane], store)

    assert exports == [2]
    assert summary.lanes[0].detail is None


@pytest.mark.asyncio
async def test_a_prune_that_cannot_remove_an_orphan_withholds_the_mark_and_reports_it() -> None:
    """The rows are written and correct, so this may not fail the export -- but it may not vanish either."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    lane = static_lane(backend, part_counts=[1], export_instants=[AFTER_GO_NOW], watermark_instants=[GO_NOW_AT])
    seed_parts(backend, store, count=2, at=BE_READY_AT, day=VERSION_DAY)
    orphan = partition_path(ZONES, "observed", BASE_TIER, VERSION_DAY, 1)
    backend.refuses_delete_of.add(store.key_for(orphan))

    summary = await drive([lane], store)

    verdict = summary.lanes[0]
    assert verdict.outcome == "filled", "a prune failure must never fail the export"
    assert verdict.written == 1
    assert orphan in (verdict.detail or "")
    assert "still published beside this export" in (verdict.detail or "")
    # AND THE DAY IS NOT MARKED COMPLETE. Without this the test passes whether or not the mark is
    # withheld, so the behaviour RUNBOOK 0.35.2 records as the fix would be entirely unguarded: a
    # completion claim over a surviving orphan is exactly the two-generation mixture the marker
    # exists to make impossible.
    assert completion_marker_path(ZONES, "observed", BASE_TIER, VERSION_DAY) not in backend.objects, (
        "a day whose surplus parts survived may not claim it finished"
    )
    assert "NOT being marked complete" in (verdict.detail or "")


class WatermarkRow:
    """One aggregated watermark row: `row_count` is a count, every other column is the timestamp."""

    def __init__(self, *, watermark_at: datetime | None, row_count: int) -> None:
        self.watermark_at = watermark_at
        self.row_count = row_count

    def __getitem__(self, column: str) -> object:
        return self.row_count if column == "row_count" else self.watermark_at


class WatermarkSession:
    """A session whose one statement returns a single canned watermark row; executes no real SQL."""

    def __init__(self, row: WatermarkRow) -> None:
        self.row = row

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:  # noqa: ARG002
        return self

    def scalar(self) -> bool:
        """Every advisory-lock probe this session sees is granted; contention is not its subject."""
        return True

    def mappings(self) -> list[WatermarkRow]:
        return [self.row]

    async def rollback(self) -> None:
        return None


@pytest.mark.asyncio
async def test_the_real_zones_watermark_resolver_carries_the_instant_beside_the_day() -> None:
    """THE PRODUCER END of the wiring. Dropping `instant=` from the reader must fail here.

    This runs the REGISTERED `evacuation-zones` resolver, not a fixture, so the day/instant pair is
    asserted where production actually builds it. Without the instant the whole sub-day comparison
    silently degrades to the day-resolution fallback, which is what the day already could not decide.
    """
    (lane,) = resolve_lanes([ZONES])
    assert lane.watermark is not None
    session = WatermarkSession(WatermarkRow(watermark_at=GO_NOW_AT, row_count=12))

    watermark = await lane.watermark(session, ObjectStore(RecordingBackend()), today=TODAY)  # type: ignore[arg-type]

    assert watermark.instant == GO_NOW_AT
    assert watermark.day == VERSION_DAY
    assert "12 published rows" in watermark.basis


@pytest.mark.asyncio
async def test_a_source_with_no_rows_carries_neither_a_day_nor_an_instant() -> None:
    """An empty population has nothing that changed, and the contract refuses an instant without a day."""
    (lane,) = resolve_lanes([ZONES])
    assert lane.watermark is not None
    session = WatermarkSession(WatermarkRow(watermark_at=None, row_count=0))

    watermark = await lane.watermark(session, ObjectStore(RecordingBackend()), today=TODAY)  # type: ignore[arg-type]

    assert (watermark.day, watermark.instant) == (None, None)


# --- The ladder queue: published days that are invisible below z13 -------------------------------


class DerivedRungListingFails(RecordingBackend):
    """A backend whose DERIVED-rung listings fail while the base rung still lists cleanly.

    The half-readable bucket is the case worth pinning: an empty repair set means "every rung is
    whole", which is the one thing an unreadable listing cannot say.
    """

    def list_objects(self, prefix: str) -> Iterator[ListedObject]:
        if f"zoom={GAP_FILL_ZOOM_TIER}" not in prefix and "availability/" not in prefix:
            raise OSError(f"bucket unreachable while listing {prefix}")
        return super().list_objects(prefix)


def recording_deriver(derived: list[tuple[str, date]]) -> TierDeriver:
    """A tier deriver that records the lane-days it was asked for and writes nothing."""

    def derive(store: ObjectStore, **kwargs: Any) -> DerivationResult:  # noqa: ARG001
        derived.append((str(kwargs["layer"]), kwargs["day"]))
        return DerivationResult(tiers=(), notes=())

    return derive


def one_day_lane(calls: list[LaneCall], day: date) -> LaneRegistration:
    """A lane whose settled window is exactly `day`, so its only work is whatever that day owes."""
    return stub_lane("signal", calls, floor=day, lag=(TODAY - day).days)


def test_a_base_complete_day_missing_a_coarse_rung_owes_a_repair_and_not_an_export() -> None:
    """DO NOT DELETE. Censusing the base rung alone hid 1,040 published lane-days below z13.

    The day has parts and a completion marker at z13, so no export is owed and the old census called
    it finished. Every zoom under 13 held nothing for it, forever, on a green tick.
    """
    calls: list[LaneCall] = []
    backend = RecordingBackend()
    newest = days_newest_first(1)[0]
    seed_partition(backend, "signal", newest)
    seed_completion(backend, "signal", newest)

    entry = build_gap_census([one_day_lane(calls, newest)], ObjectStore(backend), today=TODAY)[0]

    assert entry.missing_days == (), "the base rung is published, so nothing is exported"
    assert entry.ladder_repair_days == (newest,)
    assert entry.ladder_error is None
    report = gap_census_report([entry])
    assert report["ladder_repair_days"] == 1
    assert report["lanes_with_ladder_repairs"] == ["signal"]


def test_a_day_marked_at_every_rung_owes_nothing_at_all() -> None:
    calls: list[LaneCall] = []
    backend = RecordingBackend()
    newest = days_newest_first(1)[0]
    seed_partition(backend, "signal", newest)
    for tier in ZOOM_TIERS:
        seed_completion(backend, "signal", newest, zoom=tier)

    entry = build_gap_census([one_day_lane(calls, newest)], ObjectStore(backend), today=TODAY)[0]

    assert (entry.missing_days, entry.ladder_repair_days) == ((), ())


def test_a_rung_marked_without_parts_still_counts_as_finished() -> None:
    """A rung that generalised every base row away holds NO parts and a derived-empty receipt.

    Demanding parts at a derived rung would re-select such a day on every tick forever, which is the
    loop `derivation._retract_tier`'s receipt exists to break.
    """
    calls: list[LaneCall] = []
    backend = RecordingBackend()
    newest = days_newest_first(1)[0]
    seed_partition(backend, "signal", newest)
    seed_completion(backend, "signal", newest)
    for tier in ZOOM_TIERS:
        if tier != GAP_FILL_ZOOM_TIER:
            backend.put(
                completion_marker_path("signal", "observed", tier, newest),
                PartitionCompletion(
                    part_count=0, row_count=0, completed_at=FROZEN_NOW, run_id="seed", derived_empty=True
                ).to_json_bytes(),
                content_type=COMPLETION_CONTENT_TYPE,
            )

    entry = build_gap_census([one_day_lane(calls, newest)], ObjectStore(backend), today=TODAY)[0]

    assert entry.ladder_repair_days == ()


def test_an_unreadable_rung_listing_is_reported_rather_than_read_as_a_whole_ladder() -> None:
    calls: list[LaneCall] = []
    backend = DerivedRungListingFails()
    newest = days_newest_first(1)[0]
    seed_partition(backend, "signal", newest)
    seed_completion(backend, "signal", newest)

    entry = build_gap_census([one_day_lane(calls, newest)], ObjectStore(backend), today=TODAY)[0]

    assert entry.error is None, "the base census is still good"
    assert entry.ladder_repair_days == ()
    assert entry.ladder_error is not None
    assert "derived rungs" in entry.ladder_error
    assert gap_census_report([entry])["lanes_with_ladder_errors"] == ["signal"]


@pytest.mark.asyncio
async def test_a_repair_re_derives_the_day_without_ever_calling_the_lane_adapter() -> None:
    """The base rows are already correct; re-exporting them would cost a source query for nothing."""
    calls: list[LaneCall] = []
    derived: list[tuple[str, date]] = []
    backend = RecordingBackend()
    newest = days_newest_first(1)[0]
    seed_partition(backend, "signal", newest)
    seed_completion(backend, "signal", newest)

    summary = await drive([one_day_lane(calls, newest)], ObjectStore(backend), derive_tiers=recording_deriver(derived))

    assert calls == [], "a ladder repair opens no source query at all"
    assert derived == [("signal", newest)]
    verdict = summary.lanes[0]
    assert (verdict.repaired, verdict.written, verdict.ladder_remaining) == (1, 0, 0)
    assert verdict.outcome == "filled", "a tick that repaired is not a tick with nothing to do"
    assert summary.to_summary()["repaired"] == 1


@pytest.mark.asyncio
async def test_exports_are_taken_before_repairs() -> None:
    """A missing day is absent from the map at every zoom; a ladder gap is a published day gone coarse."""
    calls: list[LaneCall] = []
    derived: list[tuple[str, date]] = []
    backend = RecordingBackend()
    newest, second = days_newest_first(2)
    seed_partition(backend, "signal", second)
    seed_completion(backend, "signal", second)
    lane = stub_lane("signal", calls, floor=second, lag=0)

    await drive([lane], ObjectStore(backend), derive_tiers=recording_deriver(derived))

    assert [call.day for call in calls] == [newest], "only the missing day reaches the lane adapter"
    # The first derivation is the EXPORT's own ladder, written before its base marker; the second is
    # the repair. Their order is the claim: a published-but-coarse day never delays a missing one.
    assert [day for _lane, day in derived] == [newest, second]


@pytest.mark.asyncio
async def test_a_repair_that_raises_leaves_the_lane_working() -> None:
    """A derivation failure is a property of ONE published day, not of the lane's source or schema."""
    calls: list[LaneCall] = []
    backend = RecordingBackend()
    newest = days_newest_first(1)[0]
    seed_partition(backend, "signal", newest)
    seed_completion(backend, "signal", newest)

    def refuse(*_args: object, **_kwargs: object) -> DerivationResult:
        raise ValueError("the base table does not carry cell_longitude")

    summary = await drive([one_day_lane(calls, newest)], ObjectStore(backend), derive_tiers=refuse)

    verdict = summary.lanes[0]
    assert verdict.outcome != "raised", "one poisoned day must not hide every other lane's ladder gap"
    assert verdict.repaired == 0
    assert "retracting and re-exporting" in (verdict.detail or "")


@pytest.mark.asyncio
async def test_a_current_static_lane_still_repairs_its_ladder() -> None:
    """DO NOT DELETE. The three `static_lookup` lanes are `current` almost every tick.

    `current` says the reference set matches its source, which is a statement about EXPORTS. Gating
    the repair queue on it would mean no hourly tick ever derived a coarse rung for `watersheds`,
    `soil-survey` or `evacuation-zones` -- the exact silence the ladder census exists to end.
    """
    calls: list[LaneCall] = []
    derived: list[tuple[str, date]] = []
    backend = RecordingBackend()
    version_day = date(2026, 8, 7)
    seed_partition(backend, "watersheds", version_day)
    seed_completion(backend, "watersheds", version_day)
    lane = stub_lane("watersheds", calls, nature="static_lookup", watermark_day=version_day)

    summary = await drive([lane], ObjectStore(backend), derive_tiers=recording_deriver(derived))

    verdict = summary.lanes[0]
    assert verdict.outcome == "current", "repairing must not reword what the lane's EXPORT verdict was"
    assert calls == [], "a current reference set is never re-exported"
    assert derived == [("watersheds", version_day)]
    assert verdict.repaired == 1


@pytest.mark.asyncio
async def test_a_lane_whose_export_raised_drops_its_ladder_repairs_too() -> None:
    """Something about that lane is wrong; re-deriving its published days is the driver guessing."""
    calls: list[LaneCall] = []
    derived: list[tuple[str, date]] = []
    backend = RecordingBackend()
    newest, second = days_newest_first(2)
    seed_partition(backend, "signal", second)
    seed_completion(backend, "signal", second)
    lane = stub_lane("signal", calls, floor=second, lag=0, raises_on={newest})

    summary = await drive([lane], ObjectStore(backend), derive_tiers=recording_deriver(derived))

    assert summary.lanes[0].outcome == "raised"
    assert derived == [], "the next tick's census re-selects every one of them"
    assert summary.lanes[0].ladder_remaining == 0, "a dropped queue is not a deferred one"
