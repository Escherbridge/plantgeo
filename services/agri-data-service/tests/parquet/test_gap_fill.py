"""The gap-fill driver: newest-first ordering, round-robin fairness, budgets, and per-lane isolation.

Every test here runs against the in-memory `RecordingBackend` and a session fake that executes no
SQL -- the lane exports themselves are pinned by their own lane tests. What is pinned here is the
DRIVER's behaviour: which days it picks, in what order, what it does when one raises, and that a
`--dry-run` census writes nothing at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner

from agri_data_service.cli import cli
from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.paths import absence_marker_path, partition_path
from agri_data_service.pipeline.parquet.gap_fill import (
    GapFillSummary,
    build_gap_census,
    gap_census_report,
    lane_window,
    run_gap_fill,
)
from agri_data_service.pipeline.parquet.lane_registry import (
    LaneRegistration,
    LaneRunResult,
    LaneWindowKind,
)
from agri_data_service.pipeline.parquet.objectstore import (
    ABSENCE_CONTENT_TYPE,
    PARQUET_CONTENT_TYPE,
    EmptyPartitionError,
    ObjectStore,
)
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator

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


class RecordingSession:
    """Records the statement-timeout pins and rollbacks the driver issues; executes no real SQL."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.bound: list[dict[str, Any] | None] = []
        self.rollbacks = 0

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> None:
        self.statements.append(str(statement))
        self.bound.append(params)

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

    def list_keys(self, prefix: str) -> Iterator[str]:
        raise OSError(f"bucket unreachable while listing {prefix}")


def stub_lane(  # noqa: PLR0913 - one knob per behaviour a driver test needs to provoke
    slug: str,
    calls: list[LaneCall],
    *,
    floor: date = FLOOR,
    lag: int = 0,
    window_kind: LaneWindowKind = "daily_series",
    raises_on: Collection[date] = (),
    empty_on: Collection[date] = (),
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

    return LaneRegistration(
        slug=slug,
        adapter=adapter,
        history_floor=floor,
        publication_lag_days=lag,
        window_kind=window_kind,
        floor_basis=f"test fixture for {slug}",
    )


def seed_partition(backend: RecordingBackend, slug: str, day: date) -> None:
    """Land a part file for one stream-day, at exactly the key the writer's layout would use."""
    backend.put(partition_path(slug, "observed", day), b"parquet", content_type=PARQUET_CONTENT_TYPE)


def seed_absence(backend: RecordingBackend, slug: str, day: date) -> None:
    """Land a governed-absence marker for one stream-day, exactly as `write_absence` would."""
    marker = GovernedAbsence(reason="seeded", upstream_response="seeded", recorded_at=FROZEN_NOW, run_id="seed")
    backend.put(absence_marker_path(slug, "observed", day), marker.to_json_bytes(), content_type=ABSENCE_CONTENT_TYPE)


async def drive(  # noqa: PLR0913 - one knob per driver parameter a test needs to vary
    lanes: list[LaneRegistration],
    store: ObjectStore,
    *,
    session: RecordingSession | None = None,
    time_budget_seconds: float = UNLIMITED_BUDGET_SECONDS,
    max_days_per_lane: int | None = None,
    monotonic: FakeClock | None = None,
) -> GapFillSummary:
    """Run one tick against the frozen clock, day and run id every test in this module shares."""
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
    seed_absence(backend, "signal", second)

    summary = await drive([stub_lane("signal", calls)], ObjectStore(backend))

    assert [call.day for call in calls] == days_newest_first(WINDOW_DAYS)[2:]
    assert calls[0].day == third
    assert summary.lanes[0].considered == WINDOW_DAYS - 2


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

    marker_key = absence_marker_path("signal", "observed", newest)
    assert backend.content_types[marker_key] == ABSENCE_CONTENT_TYPE
    absence = GovernedAbsence.from_json_bytes(backend.objects[marker_key])
    assert absence.run_id == RUN_ID
    assert absence.recorded_at == FROZEN_NOW
    assert newest.isoformat() in absence.reason
    assert "returned 0 rows" in absence.upstream_response
    assert "DID NOT CONTACT THE UPSTREAM SOURCE SYSTEM" in absence.upstream_response
    assert summary.lanes[0].absent == 1
    assert summary.lanes[0].written == WINDOW_DAYS - 1
    assert not summary.failed


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
    assert absence_marker_path("signal", "observed", newest) in backend.objects
    assert newest not in [call.day for call in second_calls]
    assert [call.day for call in second_calls] == days_newest_first(WINDOW_DAYS)[1:]


@pytest.mark.asyncio
async def test_the_session_is_rolled_back_after_every_day_and_the_timeout_re_pinned() -> None:
    """`SET LOCAL` dies with each rollback, and holding one read snapshot across a tick pins xmin."""
    calls: list[LaneCall] = []
    session = RecordingSession()

    await drive([stub_lane("signal", calls)], ObjectStore(RecordingBackend()), session=session)

    assert session.rollbacks == WINDOW_DAYS
    assert len(session.statements) == WINDOW_DAYS
    assert all("statement_timeout" in statement for statement in session.statements)


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
async def test_a_current_snapshot_lane_only_ever_fills_the_newest_settled_day() -> None:
    """Its export broadcasts the day onto every row, so filling a past day would fabricate history."""
    calls: list[LaneCall] = []
    store = ObjectStore(RecordingBackend())
    lane = stub_lane("watersheds", calls, floor=date(2026, 8, 7), window_kind="current_snapshot")

    summary = await drive([lane], store)

    assert pairs(calls) == [("watersheds", TODAY)]
    assert lane_window(lane, today=TODAY) == (TODAY, TODAY)
    assert summary.lanes[0].considered == 1


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

    summary = await drive(
        [stub_lane("signal", calls)], ObjectStore(RecordingBackend()), max_days_per_lane=capped_days
    )

    assert [call.day for call in calls] == days_newest_first(capped_days)
    assert summary.lanes[0].considered == capped_days
    assert summary.lanes[0].remaining == 0


def test_the_census_counts_data_absent_and_missing_days_without_opening_a_file() -> None:
    """Gap detection is a listing, never a scan: `layer-lanes.md` section 4."""
    calls: list[LaneCall] = []
    backend = RecordingBackend()
    newest, second, third = days_newest_first(3)
    seed_partition(backend, "signal", newest)
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

    result = CliRunner().invoke(cli, ["parquet-gap-fill", "--layer", "water-gauges", "--dry-run"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["lane_count"] == 1
    assert report["lanes"][0]["lane"] == "water-gauges"
    assert report["lanes"][0]["history_floor"] == "2026-05-24"
    assert "docs/lanes/water-gauges.md" in report["lanes"][0]["floor_basis"]
    assert backend.objects == {}, "--dry-run must not write a single object"


def test_an_unknown_layer_is_refused_before_anything_is_listed() -> None:
    result = CliRunner().invoke(cli, ["parquet-gap-fill", "--layer", "interventions", "--dry-run"])

    assert result.exit_code != 0
    assert "interventions" in result.output
