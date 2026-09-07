"""The warehouse coverage rollup: one object, proven per lane against that lane's own pointer.

The rollup exists because a cold coverage answer was measured spending ~5.2 s of CPU re-verifying
96,012 availability rows it had already verified -- 11.5 MB downloaded and re-hashed on every build,
against the app's 8 s coverage timeout. These tests hold it to the only terms on which that shortcut
is allowed: the SAME resolved rows, a fallback that still answers when the cache is gone, and a stale
entry that is detected rather than served. A rollup that could quietly lag would be worse than the
slow path it replaces, because withheld-vs-absent must stay distinguishable.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.parquet_ops.availability_coverage import (
    AvailabilityCoverageReader,
    lane_coverage_from_index,
    lane_coverage_from_rollup_entry,
    lane_root,
    resolve_availability_lanes,
)
from agri_data_service.parquet_ops.coverage import CensusLane
from agri_data_service.pipeline.parquet.availability_index import (
    AvailabilityIndex,
    AvailabilityUnavailableError,
    PublicationResult,
    StoredAvailabilityObject,
    _refresh_coverage_rollup,
)
from agri_data_service.pipeline.parquet.coverage_rollup import (
    COVERAGE_ROLLUP_KEY,
    CoverageRollup,
    CoverageRollupMalformedError,
    entry_from_index,
    fold_days,
    pointer_digest,
    read_coverage_rollup,
    refresh_coverage_rollup_entry,
)
from tests.parquet_ops.test_availability_coverage import (
    CEILING,
    DIGEST,
    OTHER_DIGEST,
    whole_ladder,
)

if TYPE_CHECKING:
    from agri_data_service.parquet_ops.wire import LaneCoverage
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityPointer

NOW: Final = datetime(2026, 8, 25, 4, tzinfo=UTC)

VEGETATION: Final = CensusLane(layer="vegetation", nature="daily_series", kind="observed")
WATER_GAUGES: Final = CensusLane(layer="water-gauges", nature="daily_series", kind="observed")
DROUGHT: Final = CensusLane(
    layer="drought", nature="release_series", kind="observed", cadence_days=7, publication_lag_days=4
)

PUBLISHED_DAYS: Final = (date(2026, 8, 1), date(2026, 8, 2), date(2026, 8, 3), date(2026, 8, 5))
ABSENT_DAYS: Final = (date(2026, 8, 4),)

#: A rollup merge that races is retried, so a losing attempt must be OBSERVED, not assumed.
EXPECTED_RACING_PUBLISHERS: Final = 2


class VersionedStore:
    """An `AvailabilityStorage` with real conditional-write semantics over a dictionary.

    The point of the fake is the ETag: `compare_and_swap` accepts a write only while the caller's
    observed version is still current, which is the property the whole concurrency argument rests
    on. A fake that always accepted would let a lost update pass every test in this file.
    """

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.rejected_swaps = 0
        self.accepted_swaps = 0
        self._lock = threading.Lock()
        self._version = 0

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        """Return the held bytes and the token a conditional write must present."""
        del max_bytes
        with self._lock:
            held = self.objects.get(key)
        return None if held is None else StoredAvailabilityObject(payload=held[0], etag=held[1])

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        """Not exercised here; the rollup is never written immutably."""
        del payload, content_type
        raise AssertionError(f"the rollup must not be written immutably: {key!r}")

    def compare_and_swap(
        self,
        key: str,
        payload: bytes,
        *,
        expected_etag: str | None,
        content_type: str,
    ) -> bool:
        """Accept the write only while the caller's observed version is still the current one."""
        del content_type
        with self._lock:
            held = self.objects.get(key)
            current = None if held is None else held[1]
            if current != expected_etag:
                self.rejected_swaps += 1
                return False
            self._version += 1
            self.objects[key] = (payload, f'"v{self._version}"')
            self.accepted_swaps += 1
            return True


class ExplodingStore(VersionedStore):
    """A store whose every read fails. A cache that cannot be read must never fail an answer."""

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        """Raise, as an unreachable object store does."""
        del key, max_bytes
        raise OSError("the object store is unreachable")


class RacingStore(VersionedStore):
    """A `VersionedStore` that makes two publishers observe the SAME version before either writes.

    The interleaving is forced rather than hoped for: each thread's FIRST read blocks until every
    publisher has read, so both observe the empty rollup before either can compare-and-swap. That is
    exactly the lost-update window, produced deterministically instead of by timing luck.
    """

    def __init__(self, expected: int = EXPECTED_RACING_PUBLISHERS) -> None:
        super().__init__()
        # NOT a threading.Barrier. A Barrier deadlocks-then-breaks if the expected party count is
        # ever wrong -- if a publisher retries into a second first-read, or reaches its CAS by a
        # path that never calls read() -- and a BrokenBarrierError says nothing about the property
        # under test. This gate releases when everyone who is GOING to arrive has arrived, and
        # otherwise releases on its own timeout, so a mis-set expectation degrades into the real
        # assertions below (`rejected_swaps >= 1` proves the race actually happened) instead of
        # into a 10-second hang and an error about threading.
        self._expected = expected
        self._all_arrived = threading.Event()
        self._arrived: set[str] = set()
        self._arrival_lock = threading.Lock()

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        """Read, holding each thread's FIRST read until every publisher has observed one version."""
        stored = super().read(key, max_bytes=max_bytes)
        name = threading.current_thread().name
        with self._arrival_lock:
            first_read = name not in self._arrived
            self._arrived.add(name)
            if len(self._arrived) >= self._expected:
                self._all_arrived.set()
        if first_read:
            self._all_arrived.wait(timeout=10)
        return stored


class RollupScriptedReader(AvailabilityCoverageReader):
    """A reader whose per-lane index and pointer answers are scripted, counting which path was taken.

    `index_reads` is the expensive path -- in production it is the generation GET plus the per-row
    verification the rollup exists to avoid -- so counting it is how these tests state "this lane was
    answered from the rollup" as a fact rather than a hope.
    """

    def __init__(
        self,
        indexes: dict[str, AvailabilityIndex],
        *,
        pointers: dict[str, AvailabilityIndex] | None = None,
        pointer_faults: dict[str, Exception] | None = None,
    ) -> None:
        super().__init__(VersionedStore())
        self._indexes = indexes
        self._pointer_source = indexes if pointers is None else pointers
        self._pointer_faults = {} if pointer_faults is None else pointer_faults
        self.index_reads: list[str] = []
        self.pointer_reads: list[str] = []

    def read(self, lane: CensusLane, *, now: datetime) -> AvailabilityIndex:
        """Answer the scripted index, recording that the expensive path was taken."""
        del now
        self.index_reads.append(lane.layer)
        held = self._indexes.get(lane.layer)
        if held is None:
            raise AvailabilityUnavailableError("availability_missing", f"no index scripted for {lane.layer!r}")
        return held

    def read_pointer(self, lane: CensusLane, *, now: datetime) -> AvailabilityPointer:
        """Answer the scripted pointer, recording the cheap probe."""
        del now
        self.pointer_reads.append(lane.layer)
        fault = self._pointer_faults.get(lane.layer)
        if fault is not None:
            raise fault
        held = self._pointer_source.get(lane.layer)
        if held is None:
            raise AvailabilityUnavailableError("availability_missing", f"no pointer scripted for {lane.layer!r}")
        return held.pointer


def index_for(lane: CensusLane, *, generation_sha256: str = DIGEST) -> AvailabilityIndex:
    """One verified index with a published run, a governed absence and a real rung ladder."""
    built = whole_ladder(lane, published=PUBLISHED_DAYS, absent=ABSENT_DAYS, source_ceiling=CEILING)
    if generation_sha256 == DIGEST:
        return built
    return AvailabilityIndex(
        pointer=replace(
            built.pointer,
            generation_key=built.pointer.generation_key.replace(DIGEST, generation_sha256),
            generation_sha256=generation_sha256,
        ),
        rows=built.rows,
    )


def rollup_of(*pairs: tuple[CensusLane, AvailabilityIndex]) -> CoverageRollup:
    """Build a rollup holding exactly the given lanes, derived the way a publisher derives it."""
    return CoverageRollup(entries={lane_root(lane): entry_from_index(index, updated_at=NOW) for lane, index in pairs})


def wire(rows: tuple[LaneCoverage, ...]) -> list[dict[str, object]]:
    """Render rows to the shape the route publishes, which is what equality must hold over."""
    return [row.to_wire() for row in rows]


def test_a_rollup_entry_answers_exactly_what_its_generation_answers() -> None:
    """The whole premise. Equality of the RESOLVED rows, not of bytes -- the rollup stores day sets,
    the generation stores rows, and the two are only allowed to agree about the answer."""
    index = index_for(VEGETATION)
    served = lane_coverage_from_rollup_entry(entry_from_index(index, updated_at=NOW), lane=VEGETATION, now=NOW)
    proven = lane_coverage_from_index(index, lane=VEGETATION, now=NOW)
    assert wire(served) == wire(proven)


@pytest.mark.parametrize("lane", [VEGETATION, DROUGHT])
def test_a_whole_resolution_is_unchanged_by_serving_it_from_the_rollup(lane: CensusLane) -> None:
    """Through the real resolver, for a daily series and for the release lane whose carry moves.

    Drought is here on purpose: its axis is closed against TODAY, so an entry that had frozen the
    closed ROWS instead of the proven DAYS would answer yesterday's axis and this would fail.
    """
    index = index_for(lane)
    reader = RollupScriptedReader({lane.layer: index})
    without = resolve_availability_lanes(reader, lanes=[lane], policy="availability", now=NOW)
    with_rollup = resolve_availability_lanes(
        RollupScriptedReader({lane.layer: index}),
        lanes=[lane],
        policy="availability",
        now=NOW,
        rollup=rollup_of((lane, index)),
    )
    assert wire(with_rollup.lanes) == wire(without.lanes)
    assert with_rollup.withheld == without.withheld
    assert with_rollup.census_lanes == without.census_lanes


def test_a_rollup_hit_never_reads_the_generation_and_a_miss_always_does() -> None:
    """The saving, stated as a fact about which path ran rather than as a timing claim."""
    index = index_for(VEGETATION)
    hit = RollupScriptedReader({VEGETATION.layer: index})
    resolve_availability_lanes(
        hit, lanes=[VEGETATION], policy="availability", now=NOW, rollup=rollup_of((VEGETATION, index))
    )
    assert hit.index_reads == []
    assert hit.pointer_reads == [VEGETATION.layer]

    miss = RollupScriptedReader({VEGETATION.layer: index})
    resolve_availability_lanes(miss, lanes=[VEGETATION], policy="availability", now=NOW)
    assert miss.index_reads == [VEGETATION.layer]
    assert miss.pointer_reads == []


@pytest.mark.parametrize("absent_rollup", [None, CoverageRollup.empty()])
def test_a_missing_rollup_falls_back_and_still_answers(absent_rollup: CoverageRollup | None) -> None:
    """Never fail closed on a cache: no rollup and an empty rollup both answer the full truth."""
    index = index_for(VEGETATION)
    reader = RollupScriptedReader({VEGETATION.layer: index})
    resolved = resolve_availability_lanes(
        reader, lanes=[VEGETATION], policy="availability", now=NOW, rollup=absent_rollup
    )
    assert wire(resolved.lanes) == wire(lane_coverage_from_index(index, lane=VEGETATION, now=NOW))
    assert reader.index_reads == [VEGETATION.layer]


def test_a_stale_rollup_entry_is_detected_and_never_served() -> None:
    """A rollup entry bound to a generation the pointer no longer names must not answer for it.

    The lane's pointer has moved on to a generation whose day set is LARGER. Serving the held entry
    would silently hide a published day, which is precisely the failure mode that makes a stale
    coverage answer worse than a slow one.
    """
    stale_index = index_for(VEGETATION, generation_sha256=DIGEST)
    current = AvailabilityIndex(
        pointer=replace(
            stale_index.pointer,
            generation_key=stale_index.pointer.generation_key.replace(DIGEST, OTHER_DIGEST),
            generation_sha256=OTHER_DIGEST,
        ),
        rows=whole_ladder(
            VEGETATION,
            published=(*PUBLISHED_DAYS, date(2026, 8, 6)),
            absent=ABSENT_DAYS,
            source_ceiling=CEILING,
        ).rows,
    )
    reader = RollupScriptedReader({VEGETATION.layer: current})
    resolved = resolve_availability_lanes(
        reader,
        lanes=[VEGETATION],
        policy="availability",
        now=NOW,
        rollup=rollup_of((VEGETATION, stale_index)),
    )
    assert reader.index_reads == [VEGETATION.layer], "a stale entry must be repaired by a full read"
    assert wire(resolved.lanes) == wire(lane_coverage_from_index(current, lane=VEGETATION, now=NOW))
    assert resolved.lanes[0].latest_day == date(2026, 8, 6)
    assert resolved.lanes[0].availability_generation_sha256 == OTHER_DIGEST


def test_the_freshness_key_covers_every_pointer_field_not_just_the_generation() -> None:
    """A pointer that moved in ANY field makes the entry stale, so no drift can hide in one nobody compared."""
    index = index_for(VEGETATION)
    entry = entry_from_index(index, updated_at=NOW)
    assert entry.answers(index.pointer)
    moved_ceiling = replace(index.pointer, source_ceiling=date(2026, 8, 9))
    assert not entry.answers(moved_ceiling)
    moved_rows = replace(index.pointer, rows=index.pointer.rows + 1)
    assert not entry.answers(moved_rows)
    assert pointer_digest(moved_ceiling) != pointer_digest(index.pointer)


def test_a_lane_absent_from_the_rollup_is_read_in_full_and_never_reported_empty() -> None:
    """The negative control. A cold cache must cost a read, never a lane that looks like it has no days."""
    index = index_for(WATER_GAUGES)
    reader = RollupScriptedReader({WATER_GAUGES.layer: index, VEGETATION.layer: index_for(VEGETATION)})
    resolved = resolve_availability_lanes(
        reader,
        lanes=[VEGETATION, WATER_GAUGES],
        policy="availability",
        now=NOW,
        # Only vegetation is in the rollup; water-gauges has never been merged.
        rollup=rollup_of((VEGETATION, index_for(VEGETATION))),
    )
    assert reader.index_reads == [WATER_GAUGES.layer]
    rows = [row for row in resolved.lanes if row.layer == WATER_GAUGES.layer]
    assert rows, "a lane the rollup has not heard of must still appear in the answer"
    assert all(row.earliest_day is not None for row in rows)
    assert all(row.published_ranges for row in rows)
    assert all(row.withheld_reason is None for row in rows)


def test_a_pointer_the_probe_cannot_read_defers_to_the_full_path_rather_than_deciding() -> None:
    """One lane, one verdict: the cheap probe never publishes a withholding of its own."""
    index = index_for(VEGETATION)
    reader = RollupScriptedReader(
        {VEGETATION.layer: index},
        pointer_faults={VEGETATION.layer: OSError("the object store is unreachable")},
    )
    resolved = resolve_availability_lanes(
        reader,
        lanes=[VEGETATION],
        policy="availability",
        now=NOW,
        rollup=rollup_of((VEGETATION, index)),
    )
    assert reader.index_reads == [VEGETATION.layer]
    assert wire(resolved.lanes) == wire(lane_coverage_from_index(index, lane=VEGETATION, now=NOW))


def test_two_concurrent_publishes_both_survive_the_rollup_merge() -> None:
    """Two lanes advancing at the same instant must both be in the rollup afterwards.

    The interleaving is forced by a barrier so both publishers read the SAME empty object before
    either writes; the assertion is about the OUTCOME -- both entries present, one swap rejected --
    and not about any lock having been taken.
    """
    store = RacingStore()
    entries = [
        entry_from_index(index_for(VEGETATION), updated_at=NOW),
        entry_from_index(index_for(WATER_GAUGES), updated_at=NOW),
    ]
    outcomes: list[bool] = []
    outcome_lock = threading.Lock()

    def publish(entry_index: int) -> None:
        landed = refresh_coverage_rollup_entry(store, entry=entries[entry_index])
        with outcome_lock:
            outcomes.append(landed)

    threads = [
        threading.Thread(target=publish, args=(index,), name=f"publisher-{index}")
        for index in range(EXPECTED_RACING_PUBLISHERS)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert outcomes == [True, True], "both publishers must report their entry landed"
    assert store.rejected_swaps >= 1, "the race must actually have happened, not merely been possible"
    held = read_coverage_rollup(store)
    assert held is not None
    assert set(held.entries) == {lane_root(VEGETATION), lane_root(WATER_GAUGES)}
    for entry in entries:
        assert held.entries[entry.lane_root].to_wire() == entry.to_wire()


def test_a_merge_that_changes_nothing_writes_nothing() -> None:
    """A replayed publication must not churn the shared object every other publisher is racing on."""
    store = VersionedStore()
    entry = entry_from_index(index_for(VEGETATION), updated_at=NOW)
    assert refresh_coverage_rollup_entry(store, entry=entry)
    assert store.accepted_swaps == 1
    assert refresh_coverage_rollup_entry(store, entry=entry)
    assert store.accepted_swaps == 1


def test_a_malformed_rollup_object_is_replaced_rather_than_left_to_strand_every_lane() -> None:
    """An unparseable rollup is worse than none, so a publisher rewrites it with its own lane."""
    store = VersionedStore()
    store.objects[COVERAGE_ROLLUP_KEY] = (b'{"schema_version": "wrong"}', '"v0"')
    entry = entry_from_index(index_for(VEGETATION), updated_at=NOW)
    assert refresh_coverage_rollup_entry(store, entry=entry)
    held = read_coverage_rollup(store)
    assert held is not None
    assert set(held.entries) == {lane_root(VEGETATION)}


@pytest.mark.parametrize(
    "payload",
    [
        b"not json at all",
        b'{"schema_version":"availability-coverage-rollup-v1"}',
        b'{"lanes":[],"schema_version":"availability-coverage-rollup-v2"}',
        b'{"lanes":{},"schema_version":"availability-coverage-rollup-v1"}',
    ],
)
def test_every_shape_fault_refuses_as_one_named_error(payload: bytes) -> None:
    """One error class, so the reader's fallback has exactly one thing to catch about content."""
    with pytest.raises(CoverageRollupMalformedError):
        CoverageRollup.parse(payload)


def test_a_reader_answers_with_an_empty_rollup_when_the_object_store_is_unreachable() -> None:
    """A transport fault reading a CACHE must not fail the coverage answer that cache serves."""
    reader = AvailabilityCoverageReader(ExplodingStore())
    assert reader.read_rollup().entries == {}


def test_a_reader_answers_with_an_empty_rollup_when_the_object_is_malformed() -> None:
    """Content faults fall back exactly as transport faults do; neither is a coverage verdict."""
    store = VersionedStore()
    store.objects[COVERAGE_ROLLUP_KEY] = (b"{", '"v0"')
    assert AvailabilityCoverageReader(store).read_rollup().entries == {}


def test_the_document_round_trips_through_its_canonical_bytes() -> None:
    """What a publisher writes is what a reader parses, field for field and day for day."""
    built = rollup_of((VEGETATION, index_for(VEGETATION)), (DROUGHT, index_for(DROUGHT)))
    parsed = CoverageRollup.parse(built.to_bytes())
    assert parsed.entries.keys() == built.entries.keys()
    for root, entry in built.entries.items():
        assert parsed.entries[root].to_wire() == entry.to_wire()
        assert parsed.entries[root].published_days() == entry.published_days()
        assert parsed.entries[root].absent_days() == entry.absent_days()
    assert parsed.to_bytes() == built.to_bytes()


def test_the_rollup_key_sits_outside_every_lane_prefix() -> None:
    """A listing census walks `layer=`; the shared object must be invisible to all of them."""
    assert not COVERAGE_ROLLUP_KEY.startswith("layer=")
    assert "kind=" not in COVERAGE_ROLLUP_KEY


def test_folding_days_is_exact_and_disjoint() -> None:
    """The compaction is the whole reason one object can hold a four-year warehouse."""
    days = {date(2026, 8, 1), date(2026, 8, 2), date(2026, 8, 5)}
    assert fold_days(days) == ((date(2026, 8, 1), date(2026, 8, 2)), (date(2026, 8, 5), date(2026, 8, 5)))
    assert fold_days(()) == ()


def test_a_publication_survives_a_rollup_refresh_that_cannot_write() -> None:
    """A cache failure must never become a publication failure: the result passes through untouched."""

    class RefusingStore(VersionedStore):
        def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
            del key, max_bytes
            raise OSError("the object store is unreachable")

    index = index_for(VEGETATION)
    result = PublicationResult(pointer=index.pointer, advanced=True, attempts=1, rows=index.rows)
    assert _refresh_coverage_rollup(RefusingStore(), result) is result


def test_a_publication_that_advanced_nothing_leaves_the_rollup_alone() -> None:
    """An exact replay names the same generation the rollup already holds; rewriting it is churn."""
    store = VersionedStore()
    index = index_for(VEGETATION)
    replayed = PublicationResult(pointer=index.pointer, advanced=False, attempts=1)
    assert _refresh_coverage_rollup(store, replayed) is replayed
    assert store.objects == {}


def test_an_advancing_publication_merges_its_own_entry() -> None:
    """The chokepoint: the statement that made the generation visible is the one that refreshes the cache."""
    store = VersionedStore()
    index = index_for(VEGETATION)
    result = PublicationResult(pointer=index.pointer, advanced=True, attempts=1, rows=index.rows)
    _refresh_coverage_rollup(store, result)
    held = read_coverage_rollup(store)
    assert held is not None
    entry = held.entry_for(lane_root(VEGETATION))
    assert entry is not None
    assert entry.answers(index.pointer)
    assert wire(lane_coverage_from_rollup_entry(entry, lane=VEGETATION, now=NOW)) == wire(
        lane_coverage_from_index(index, lane=VEGETATION, now=NOW)
    )
