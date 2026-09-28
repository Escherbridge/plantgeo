"""Repairs never run for a ledger-held lane, and a repair that keeps failing backs off (spec Sec 4.9.3).

Flows, each through real `run_executor_tick` calls whose repair turns run real child processes:

- a held lane's repairs are withheld (authoring and driving both) and resume once an operator releases it;
- two code-class repair failures trip the breaker; the lane then waits 1 day, gets one run, and a failed
  run re-trips it for 2 days; a success resolves it;
- a malformed repair request is refused before spawning and never counts against the breaker;
- an authoring pass that raises opens `executor_repair_authoring`, escalates at the second interval and
  resolves on the next good pass, while forward lanes keep running.

The design's two pool-brake tests are not written: WQ-4 declined the pool brake.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final

import structlog.testing

from agri_data_service.execution import gap_repair
from agri_data_service.execution.gap_repair_contract import REPAIR_LANE_SUFFIX
from agri_data_service.execution.job_executor_service import RepairAuthoringClock, SoftFailureState
from agri_data_service.execution.lane_ids import DROUGHT_DIRECT_LANE_ID
from agri_data_service.foundation.observability import events
from tests.execution.soft_failure_fakes import (
    COMPLETE_REPORT_SCRIPT,
    NOW,
    FakeWorld,
    build_lane_spec,
    events_named,
    exit_script,
    states,
)

if TYPE_CHECKING:
    import pytest

LANE: Final = DROUGHT_DIRECT_LANE_ID
REPAIR: Final = f"{LANE}{REPAIR_LANE_SUFFIX}"
HEALTHY: Final = "healthy-probe-lane"
BREAKER: Final = f"lane_repair_failing:{LANE}"


def _empty_plan() -> object:
    return type("Plan", (), {"authorized": (), "candidates": ()})()


def _record_authoring(
    monkeypatch: pytest.MonkeyPatch, *, coverage_fails: list[bool] | None = None
) -> list[frozenset[str]]:
    """Replace the object-store coverage read and the ledger authoring; keep the planner's activation."""
    seen: list[frozenset[str]] = []
    failures = coverage_fails if coverage_fails is not None else []

    def read_coverage(*, now: datetime) -> object:
        del now
        if failures and failures.pop(0):
            raise OSError("bucket unreachable")
        return object()

    def plan_repairs(_coverage: object, *, activation: object, **_kwargs: object) -> object:
        seen.append(frozenset(activation.active_lanes))  # type: ignore[attr-defined]
        return _empty_plan()

    async def author(*_args: object, **_kwargs: object) -> tuple[()]:
        return ()

    monkeypatch.setattr(gap_repair, "read_parquet_coverage", read_coverage)
    monkeypatch.setattr(gap_repair, "plan_gap_repairs", plan_repairs)
    monkeypatch.setattr(gap_repair, "author_gap_repairs", author)
    return seen


async def test_held_lane_authors_no_repair_and_resumes_when_released(monkeypatch: pytest.MonkeyPatch) -> None:
    world = FakeWorld(
        {LANE: build_lane_spec(LANE, COMPLETE_REPORT_SCRIPT), HEALTHY: build_lane_spec(HEALTHY, COMPLETE_REPORT_SCRIPT)}
    ).install(monkeypatch)
    held_run = world.seed_hold(LANE)
    repair_run = world.open_repair_run(LANE)
    authored_for = _record_authoring(monkeypatch)
    clock = RepairAuthoringClock(interval_seconds=0.0)
    state = SoftFailureState()

    with structlog.testing.capture_logs() as logs:
        held = await world.tick(soft=state, repair_clock=clock)

    assert states(held)[REPAIR] == "paused"
    assert repair_run.status == "queued", "the already-authored repair run is skipped, not failed"
    assert authored_for == [frozenset({HEALTHY})], "authoring never sees the held lane"
    assert [entry["lane_id"] for entry in events_named(logs, events.EVENT_REPAIR_WITHHELD)] == [LANE]

    world.supersede(held_run)
    released = await world.tick(now=NOW + timedelta(minutes=1), soft=state, repair_clock=clock)
    assert states(released)[LANE] == "ran"
    assert states(released)[REPAIR] == "deferred_fairness", "HEAD's rule: never a forward bucket and its repair"
    assert LANE in authored_for[-1]

    resumed = await world.tick(now=NOW + timedelta(minutes=2), soft=state, repair_clock=clock)
    assert states(resumed)[REPAIR] == "ran"
    assert repair_run.status == "succeeded"


async def test_repair_breaker_withholds_after_two_code_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    world = FakeWorld({LANE: build_lane_spec(LANE, exit_script(70))}).install(monkeypatch)
    state = SoftFailureState()

    async def repair_turn(at: datetime) -> dict[str, str]:
        world.seed_settled(LANE, now=at)  # the forward bucket is done: only the repair is due
        world.open_repair_run(LANE, now=at)
        return states(await world.tick(now=at, soft=state))

    with structlog.testing.capture_logs() as logs:
        assert (await repair_turn(NOW))[REPAIR] == "failed"
        first = world.incidents.by_fingerprint(BREAKER)
        assert first is not None
        assert (first["severity"], first["detail"]["state"]) == ("warning", "counting:1")  # type: ignore[index]

        tripped_at = NOW + timedelta(minutes=2)
        assert (await repair_turn(tripped_at))[REPAIR] == "failed"
        tripped = world.incidents.by_fingerprint(BREAKER)
        assert tripped is not None
        assert (tripped["severity"], tripped["detail"]["state"], tripped["detail"]["rung"]) == ("error", "cooldown", 1)  # type: ignore[index]

        cooling = await repair_turn(NOW + timedelta(hours=3))
        assert cooling[REPAIR] == "paused", "withheld for a day"
        waiting = world.latest(REPAIR)
        assert waiting is not None
        assert waiting.status == "queued"

    opened = events_named(logs, events.EVENT_REPAIR_BREAKER_OPENED)
    assert [(entry["trip_count"], entry["cooldown_days"]) for entry in opened] == [(1, 1)]

    # The cooldown lifts: ONE run is allowed, and its code-class failure re-trips at the next rung.
    one_day_on = tripped_at + timedelta(days=1, minutes=5)
    assert (await repair_turn(one_day_on))[REPAIR] == "failed"
    retripped = world.incidents.by_fingerprint(BREAKER)
    assert retripped is not None
    detail = retripped["detail"]
    assert isinstance(detail, dict)
    assert (detail["rung"], detail["cooldown_until"]) == (2, (one_day_on + timedelta(days=2)).isoformat())

    # A success after the two-day cooldown resolves the breaker entirely.
    world.set_script(LANE, COMPLETE_REPORT_SCRIPT)
    with structlog.testing.capture_logs() as logs:
        assert (await repair_turn(one_day_on + timedelta(days=2, minutes=5)))[REPAIR] == "ran"
    assert world.incidents.by_fingerprint(BREAKER) is None
    assert [entry["lane_id"] for entry in events_named(logs, events.EVENT_REPAIR_BREAKER_RELEASED)] == [LANE]


async def test_invalid_repair_request_does_not_trip_the_repair_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    world = FakeWorld({LANE: build_lane_spec(LANE, COMPLETE_REPORT_SCRIPT)}).install(monkeypatch)
    state = SoftFailureState()

    for minutes in (0, 2, 4):
        at = NOW + timedelta(minutes=minutes)
        world.seed_settled(LANE, now=at)
        world.open_repair_run(LANE, now=at, max_days=9_999)  # outside the writer's `--max-days` cap
        assert states(await world.tick(now=at, soft=state))[REPAIR] == "failed"
        assert world.last_outcome(REPAIR).failure_class == "invalid_repair_request"

    assert world.incidents.by_fingerprint(BREAKER) is None

    later = NOW + timedelta(minutes=6)
    world.seed_settled(LANE, now=later)
    world.open_repair_run(LANE, now=later)
    assert states(await world.tick(now=later, soft=state))[REPAIR] == "ran", "never withheld by refusals"


async def test_repair_authoring_failure_opens_incident_and_escalates(monkeypatch: pytest.MonkeyPatch) -> None:
    world = FakeWorld({HEALTHY: build_lane_spec(HEALTHY, COMPLETE_REPORT_SCRIPT)}).install(monkeypatch)
    _record_authoring(monkeypatch, coverage_fails=[True, True, False])
    clock = RepairAuthoringClock(interval_seconds=0.0)
    state = SoftFailureState()

    with structlog.testing.capture_logs() as logs:
        first = await world.tick(soft=state, repair_clock=clock)
        opened = world.incidents.by_fingerprint("executor_repair_authoring")
        assert opened is not None
        assert opened["severity"] == "warning"
        second = await world.tick(now=NOW + timedelta(hours=1), soft=state, repair_clock=clock)
        escalated = world.incidents.by_fingerprint("executor_repair_authoring")
        assert escalated is not None
        assert escalated["severity"] == "error"
        third = await world.tick(now=NOW + timedelta(hours=2), soft=state, repair_clock=clock)

    assert world.incidents.by_fingerprint("executor_repair_authoring") is None
    assert len(world.incidents.resolved("executor_repair_authoring")) == 1
    escalations = [
        entry for entry in events_named(logs, events.EVENT_REPAIR_AUTHORING_FAILED) if entry.get("escalated")
    ]
    assert len(escalations) == 1
    assert [states(summary)[HEALTHY] for summary in (first, second, third)] == ["ran", "ran", "ran"]


async def test_lane_rows_retire_when_the_lane_leaves_the_allow_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rows that only a planned lane or a turn can clear retire as `lane_inactive` once the lane is out of
    the allow-list; a hold is paused instead (it stays operator-only), and fleet-wide rows are untouched."""
    world = FakeWorld(
        {
            LANE: build_lane_spec(LANE, COMPLETE_REPORT_SCRIPT),
            HEALTHY: build_lane_spec(HEALTHY, COMPLETE_REPORT_SCRIPT),
        },
        active=frozenset({HEALTHY}),
    ).install(monkeypatch)
    kinds = ("lane_incomplete", "lane_report_missing", "executor_lease_lost", "lane_blocked", "lane_repair_failing")
    for kind in kinds:
        world.incidents.seed(fingerprint=f"{kind}:{LANE}", incident_type=kind, first_seen_at=NOW - timedelta(hours=1))
    world.incidents.seed(
        fingerprint="executor_lease_lost:fleet",
        incident_type="executor_lease_lost",
        first_seen_at=NOW - timedelta(hours=1),
    )

    summary = await world.tick(soft=SoftFailureState())

    assert states(summary)[HEALTHY] == "ran"
    assert world.incidents.open_kinds(LANE) == set()
    for kind in kinds:
        (retired,) = world.incidents.resolved(f"{kind}:{LANE}")
        assert retired["detail"]["resolution_reason"] == "lane_inactive"  # type: ignore[index]
    assert world.incidents.by_fingerprint("executor_lease_lost:fleet") is not None


async def test_quiet_repair_breaker_retires_but_a_cooling_one_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    """A breaker with no settled repair failure for a week past the point it admits runs again retires
    (the gaps closed, so no repair will ever come to clear it); one still inside its cooldown stays."""
    world = FakeWorld({LANE: build_lane_spec(LANE, COMPLETE_REPORT_SCRIPT)}).install(monkeypatch)
    world.seed_settled(LANE)
    tripped_at = NOW - timedelta(days=9)  # rung 1: a one-day cooldown that lifted eight days ago
    world.incidents.seed(
        fingerprint=BREAKER,
        incident_type="lane_repair_failing",
        first_seen_at=tripped_at,
        detail={"state": "cooldown", "rung": 1},
    )

    await world.tick(soft=SoftFailureState())

    assert world.incidents.by_fingerprint(BREAKER) is None
    (retired,) = world.incidents.resolved(BREAKER)
    assert retired["detail"]["resolution_reason"] == "repair_quiet"  # type: ignore[index]

    world.incidents.seed(
        fingerprint=BREAKER,
        incident_type="lane_repair_failing",
        first_seen_at=NOW - timedelta(days=3),
        detail={"state": "cooldown", "rung": 4},  # a seven-day cooldown, four days still to run
    )
    await world.tick(now=NOW + timedelta(minutes=1), soft=SoftFailureState())
    assert world.incidents.by_fingerprint(BREAKER) is not None


async def test_authoring_streak_retires_when_authoring_is_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    world = FakeWorld({HEALTHY: build_lane_spec(HEALTHY, COMPLETE_REPORT_SCRIPT)}).install(monkeypatch)
    world.incidents.seed(
        fingerprint="executor_repair_authoring",
        incident_type="executor_repair_authoring",
        first_seen_at=NOW - timedelta(hours=2),
        severity="error",
    )

    summary = await world.tick(soft=SoftFailureState(), repair_clock=None)

    assert states(summary)[HEALTHY] == "ran"
    assert world.incidents.by_fingerprint("executor_repair_authoring") is None
    (retired,) = world.incidents.resolved("executor_repair_authoring")
    assert retired["detail"]["resolution_reason"] == "authoring_disabled"  # type: ignore[index]
