"""The S15 work queue (spec S15/S16, FR-7): a session per lane, no awaiting, a slot limit, never twice.

The leader tick is the real `run_executor_tick` over `soft_failure_fakes.FakeWorld`'s ledger, and every lane
turn is a REAL child process through the real handler. The lane sessions are fakes at the one place a lane
session touches PostgreSQL by itself: the per-definition advisory lock (`jobs/lease.py`), held in a shared
table so a second "process" can hold it too.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Final

import pytest
import structlog.testing
from sqlalchemy.exc import OperationalError

from agri_data_service.execution import job_executor_service
from agri_data_service.execution.job_executor_service import (
    BREAKER_MODE_VARIABLE,
    DISPATCH_VARIABLE,
    MAX_CONCURRENT_LANES_VARIABLE,
    SOFT_FAILURE_VARIABLE,
    DailyMaintenance,
    ExecutorLeaderUnlockError,
    ExecutorSettings,
    ExecutorTickSummary,
    LaneDispatcher,
    SoftFailureState,
    run_executor_tick,
)
from agri_data_service.foundation.observability import events
from agri_data_service.jobs.lease import definition_lock_key
from agri_data_service.jobs.worker import ShutdownSignal
from tests.execution.soft_failure_fakes import (
    COMPLETE_REPORT_SCRIPT,
    NOW,
    FakeWorld,
    build_lane_spec,
    events_named,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping
    from contextlib import AbstractAsyncContextManager
    from datetime import datetime

MINUTE: Final = 60.0
SLOW: Final = "a-slow-lane"
FAST: Final = "b-fast-lane"
#: Exits 0 with no report after a second: slow enough that the tick returns first, and `report_missing`.
SLOW_WITHOUT_REPORT: Final = "import time\ntime.sleep(1)\n"
SLOW_COMPLETE: Final = (
    "import json, time\ntime.sleep(1)\nprint(json.dumps({'outcome': 'complete', 'days_unwritten': 0}))\n"
)
LONG: Final = "import time\ntime.sleep(30)\n"


class _Scalar:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar(self) -> object:
        return self._value


class LaneLocks:
    """The advisory lock table every fake lane session shares, as one PostgreSQL cluster would."""

    def __init__(self) -> None:
        self.held: dict[str, object] = {}
        self.sessions: list[LaneSession] = []


class LaneSession:
    """Just enough of a lane's `AsyncSession`: the statement timeout, its definition lock, commit, rollback."""

    def __init__(self, locks: LaneLocks) -> None:
        self.locks = locks

    async def execute(self, statement: object, parameters: Mapping[str, object] | None = None) -> _Scalar:
        del parameters
        rendered = str(statement)
        if "pg_try_advisory_lock" in rendered or "pg_advisory_unlock" in rendered:
            key = next(value for value in statement.compile().params.values() if isinstance(value, str))  # type: ignore[attr-defined]
            if "pg_try_advisory_lock" in rendered:
                if key in self.locks.held and self.locks.held[key] is not self:
                    return _Scalar(value=False)
                self.locks.held[key] = self
                return _Scalar(value=True)
            return _Scalar(value=self.locks.held.pop(key, None) is self)
        return _Scalar(value=None)  # the transaction-local statement timeout

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _lane_sessions(locks: LaneLocks) -> Callable[[], AbstractAsyncContextManager[LaneSession]]:
    @asynccontextmanager
    async def open_lane_session() -> AsyncIterator[LaneSession]:
        session = LaneSession(locks)
        locks.sessions.append(session)
        yield session

    return open_lane_session


async def _queued_tick(
    world: FakeWorld, dispatcher: LaneDispatcher, at: datetime, state: SoftFailureState | None = None
) -> ExecutorTickSummary:
    return await run_executor_tick(
        world.session,  # type: ignore[arg-type]
        activation=world.activation,
        now=at,
        max_lanes_per_tick=6,
        soft_failure=state,
        dispatcher=dispatcher,
    )


async def _until_idle(dispatcher: LaneDispatcher, *, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while dispatcher.in_flight:
        assert time.monotonic() < deadline, "a dispatched lane never finished"
        await asyncio.sleep(0.05)


def _lane_states(summary: ExecutorTickSummary, lane_id: str) -> list[str]:
    return [lane.state for lane in summary.lanes if lane.lane_id == lane_id]


async def test_a_queued_lane_runs_on_its_own_session_and_the_next_tick_folds_its_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = FakeWorld({SLOW: build_lane_spec(SLOW, SLOW_WITHOUT_REPORT)}).install(monkeypatch)
    locks = LaneLocks()
    dispatcher = LaneDispatcher(open_lane_session=_lane_sessions(locks))  # type: ignore[arg-type]
    state = SoftFailureState()

    dispatched = await _queued_tick(world, dispatcher, NOW, state)

    assert _lane_states(dispatched, SLOW) == ["dispatched"]
    assert SLOW not in world.outcomes, "the tick returned before the lane's child finished"
    assert dispatcher.in_flight == (f"{job_executor_service.EXECUTOR_DEFINITION_PREFIX}{SLOW}",)
    await _until_idle(dispatcher)
    assert len(locks.sessions) == 1, "the turn ran on its own lane session, not the leader's"
    assert locks.held == {}, "the lane released its definition lock"

    with structlog.testing.capture_logs() as logs:
        folded = await _queued_tick(world, dispatcher, NOW.replace(minute=6), state)

    assert _lane_states(folded, SLOW)[0] == "ran", "the finished turn lands in the next tick"
    assert world.incidents.by_fingerprint(f"lane_report_missing:{SLOW}") is not None, "its verdict was folded"
    assert len(events_named(logs, events.EVENT_LANE_REPORT_MISSING)) == 1


async def test_the_slot_limit_holds_and_a_running_lane_is_never_dispatched_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = FakeWorld(
        {SLOW: build_lane_spec(SLOW, SLOW_COMPLETE), FAST: build_lane_spec(FAST, COMPLETE_REPORT_SCRIPT)}
    ).install(monkeypatch)
    dispatcher = LaneDispatcher(open_lane_session=_lane_sessions(LaneLocks()), max_concurrent_lanes=1)  # type: ignore[arg-type]

    first = await _queued_tick(world, dispatcher, NOW)
    second = await _queued_tick(world, dispatcher, NOW.replace(minute=6))

    assert (_lane_states(first, SLOW), _lane_states(first, FAST)) == (["dispatched"], ["deferred_fairness"])
    assert (_lane_states(second, SLOW), _lane_states(second, FAST)) == (["running"], ["deferred_fairness"])
    assert len(world.runs_of(SLOW)) == 1, "a running lane is never opened or dispatched again"
    await _until_idle(dispatcher)

    third = await _queued_tick(world, dispatcher, NOW.replace(minute=7))
    await _until_idle(dispatcher)

    assert _lane_states(third, SLOW) == ["ran", "not_due"]
    assert _lane_states(third, FAST) == ["dispatched"], "the freed slot goes to the lane that waited"
    assert [outcome.kind for outcome in world.outcomes[FAST]] == ["completed"]


async def test_a_lane_whose_definition_lock_is_held_elsewhere_is_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    world = FakeWorld({FAST: build_lane_spec(FAST, COMPLETE_REPORT_SCRIPT)}).install(monkeypatch)
    locks = LaneLocks()
    another_process = object()
    locks.held[definition_lock_key(f"{job_executor_service.EXECUTOR_DEFINITION_PREFIX}{FAST}")] = another_process
    dispatcher = LaneDispatcher(open_lane_session=_lane_sessions(locks))  # type: ignore[arg-type]

    await _queued_tick(world, dispatcher, NOW)
    await _until_idle(dispatcher)
    (turn,) = dispatcher.collect()

    assert turn.result.state == "not_due"
    assert FAST not in world.outcomes, "no child was spawned: the lane never runs twice across processes"
    assert next(iter(locks.held.values())) is another_process, "the other holder's lock is untouched"


class _FakeConnection:
    """What `AsyncSession(bind=...)` needs from a connection that is never used (the tick is replaced)."""

    sync_engine = None


class _FakePool:
    @asynccontextmanager
    async def connect(self) -> AsyncIterator[_FakeConnection]:
        yield _FakeConnection()


async def test_leader_loss_cancels_lane_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    world = FakeWorld({SLOW: build_lane_spec(SLOW, LONG, timeout_seconds=120)}).install(monkeypatch)
    locks = LaneLocks()
    dispatcher = LaneDispatcher(open_lane_session=_lane_sessions(locks))  # type: ignore[arg-type]
    await _queued_tick(world, dispatcher, NOW)
    deadline = time.monotonic() + 10
    while not locks.held:  # the lane task has taken its lock
        assert time.monotonic() < deadline
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.5)  # and has spawned its child, so the cancellation lands in the monitor
    started = time.monotonic()

    async def follower_tick(_session: object, **_keywords: object) -> ExecutorTickSummary:
        return ExecutorTickSummary(observed_at=NOW, leader=False, lanes=())

    monkeypatch.setattr(job_executor_service, "run_executor_tick", follower_tick)
    exit_code = await job_executor_service._run_service_ticks(
        loader_pool=_FakePool(),  # type: ignore[arg-type]
        stop=ShutdownSignal(),
        dispatcher=dispatcher,
        activation=world.activation,
        poll_seconds=MINUTE,
        max_lanes_per_tick=2,
        once=True,
        repair_clock=None,
        soft_failure=SoftFailureState(),
    )

    assert exit_code == 0
    assert dispatcher.in_flight == (), "another leader owns the lanes now: nothing of ours is still running"
    assert locks.held == {}, "the cancelled lane released its definition lock"
    assert time.monotonic() - started < 25, "the 30 s child was stopped, not waited out"  # noqa: PLR2004
    assert SLOW not in world.outcomes, "a cancelled turn records no outcome"


@pytest.mark.parametrize(
    ("fault", "cancels"),
    [
        (OperationalError("SELECT 1", {}, Exception("canceling statement due to statement timeout")), False),
        (ExecutorLeaderUnlockError("the pinned PostgreSQL backend did not release the leader lock"), True),
    ],
    ids=["planning-statement-timeout", "leader-unlock-lost"],
)
async def test_only_an_unproven_leader_lock_cancels_the_running_lanes(
    monkeypatch: pytest.MonkeyPatch, fault: Exception, cancels: bool
) -> None:
    """H4: a planning SQL fault leaves an in-flight lane running on its own session and lock; a lost unlock stops it."""
    world = FakeWorld({SLOW: build_lane_spec(SLOW, LONG, timeout_seconds=120)}).install(monkeypatch)
    locks = LaneLocks()
    dispatcher = LaneDispatcher(open_lane_session=_lane_sessions(locks))  # type: ignore[arg-type]
    await _queued_tick(world, dispatcher, NOW)
    deadline = time.monotonic() + 10
    while not locks.held:  # the lane task has taken its lock
        assert time.monotonic() < deadline
        await asyncio.sleep(0.05)

    async def failing_tick(_session: object, **_keywords: object) -> ExecutorTickSummary:
        raise fault

    monkeypatch.setattr(job_executor_service, "run_executor_tick", failing_tick)
    try:
        exit_code = await job_executor_service._run_service_ticks(
            loader_pool=_FakePool(),  # type: ignore[arg-type]
            stop=ShutdownSignal(),
            dispatcher=dispatcher,
            activation=world.activation,
            poll_seconds=MINUTE,
            max_lanes_per_tick=2,
            once=True,
            repair_clock=None,
            soft_failure=SoftFailureState(),
        )

        assert exit_code == 1
        assert dispatcher.in_flight == (() if cancels else (f"plantgeo.executor.{SLOW}",))
    finally:
        await dispatcher.cancel_all(reason="test_teardown")


async def test_one_slot_alternates_classes_so_a_due_repair_is_not_starved_by_forwards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PH1(a): at concurrency 1 the queue takes only the head, so the head class alternates between dispatches."""
    backlog = "c-backlog-lane"
    world = FakeWorld(
        {
            FAST: build_lane_spec(FAST, SLOW_COMPLETE),
            SLOW: build_lane_spec(SLOW, SLOW_COMPLETE),
            backlog: replace(build_lane_spec(backlog, SLOW_COMPLETE), work_class="backlog"),
        }
    ).install(monkeypatch)
    dispatcher = LaneDispatcher(open_lane_session=_lane_sessions(LaneLocks()))  # type: ignore[arg-type]
    dispatched: list[str] = []
    try:
        for minute in range(3):
            summary = await _queued_tick(world, dispatcher, NOW + timedelta(minutes=minute))
            dispatched += [lane.lane_id for lane in summary.lanes if lane.state == "dispatched"]
            await _until_idle(dispatcher)
    finally:
        await dispatcher.cancel_all(reason="test_teardown")

    assert dispatched[:2] == [SLOW, backlog], "the backlog lane is second, not after every forward"
    assert set(dispatched) == {FAST, SLOW, backlog}


async def test_daily_job_log_maintenance_waits_for_a_tick_with_no_lane_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PH1(c): the pass's ACCESS EXCLUSIVE partition lock would stall a running lane's event writes."""
    world = FakeWorld({SLOW: build_lane_spec(SLOW, LONG, timeout_seconds=120)}).install(monkeypatch)
    maintained: list[datetime] = []

    async def maintain(database_url: str, now: datetime) -> dict[str, str | int]:
        del database_url
        maintained.append(now)
        return {}

    monkeypatch.setattr(job_executor_service, "_maintain_job_events", maintain)
    maintenance = DailyMaintenance(database_url="postgresql://maintenance.test/agri", receipt_store_factory=None)
    dispatcher = LaneDispatcher(open_lane_session=_lane_sessions(LaneLocks()))  # type: ignore[arg-type]

    async def tick(at: datetime) -> None:
        await run_executor_tick(
            world.session,  # type: ignore[arg-type]
            activation=world.activation,
            now=at,
            max_lanes_per_tick=6,
            dispatcher=dispatcher,
            maintenance=maintenance,
        )

    try:
        await _queued_tick(world, dispatcher, NOW)
        busy = NOW + timedelta(minutes=1)
        await tick(busy)
        during_the_lane = list(maintained)
        await dispatcher.cancel_all(reason="test")
        idle = NOW + timedelta(minutes=2)
        await tick(idle)
    finally:
        await dispatcher.cancel_all(reason="test_teardown")

    assert during_the_lane == []
    assert maintained == [idle]


async def test_serial_dispatch_is_heads_in_tick_await(monkeypatch: pytest.MonkeyPatch) -> None:
    world = FakeWorld({FAST: build_lane_spec(FAST, COMPLETE_REPORT_SCRIPT)}).install(monkeypatch)

    summary = await world.tick(soft=None)  # `run_executor_tick(dispatcher=None)`: S16 `serial`

    assert _lane_states(summary, FAST) == ["ran"], "the tick awaited the lane"
    assert [outcome.kind for outcome in world.outcomes[FAST]] == ["completed"]


@pytest.mark.parametrize(
    ("environment", "dispatch", "breaker", "lanes", "soft_failure", "laddered"),
    [
        pytest.param({}, "queue", "split", 1, True, True, id="unset-is-the-g1-default"),
        pytest.param({DISPATCH_VARIABLE: " Serial "}, "serial", "split", 1, True, True, id="serial-restores-head"),
        pytest.param(
            {DISPATCH_VARIABLE: "parallel"}, "serial", "split", 1, True, True, id="garbled-dispatch-is-serial"
        ),
        pytest.param({BREAKER_MODE_VARIABLE: "legacy"}, "queue", "legacy", 1, True, False, id="legacy-breaker-is-gl5"),
        pytest.param({BREAKER_MODE_VARIABLE: "on"}, "queue", "legacy", 1, True, False, id="garbled-breaker-is-legacy"),
        pytest.param(
            {BREAKER_MODE_VARIABLE: "legacy", SOFT_FAILURE_VARIABLE: "off"},
            "queue",
            "legacy",
            1,
            False,
            False,
            id="legacy-and-soft-failure-off-is-pre-wave-o",
        ),
        pytest.param({MAX_CONCURRENT_LANES_VARIABLE: "2"}, "queue", "split", 2, True, True, id="two-slots"),
        pytest.param({MAX_CONCURRENT_LANES_VARIABLE: "-1"}, "queue", "split", 1, True, True, id="garbled-slots-is-1"),
    ],
)
def test_the_s16_switches_fall_back_to_heads_behaviour(  # noqa: PLR0913 - one parametrised fact per argument
    environment: Mapping[str, str], dispatch: str, breaker: str, lanes: int, soft_failure: bool, laddered: bool
) -> None:
    settings_ = ExecutorSettings.from_environment(environment)

    assert (settings_.dispatch, settings_.breaker, settings_.max_concurrent_lanes) == (dispatch, breaker, lanes)
    assert settings_.soft_failure_enabled is soft_failure, "each shared change rolls back on its own switch (PH2)"
    assert (settings_.hold_ladders is not None) is laddered
