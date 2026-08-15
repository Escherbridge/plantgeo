"""`jobs-pulse`: pause skip, per-lane failure isolation, the global time budget, and its exit rule.

Every async entry point below `run_jobs_pulse` itself is mocked -- no database, no HTTP, matching
`test_ingest_commands_jobs.py`'s own convention (its module docstring: "no database, fakes for both
seams"). Real-database proof that `discover_pulse_plan`'s SQL join actually matches ledger rows, and
that a real dispatchable lane and a real durable definition are BOTH reached from one pulse, is
`test_jobs_pulse_agri_db.py`.
"""

# ruff: noqa: PLR2004, ARG001

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import click
import pytest
from click.testing import CliRunner
from sqlalchemy.exc import OperationalError

from agri_data_service.execution import jobs_pulse_command
from agri_data_service.execution.jobs_pulse_command import (
    DEFAULT_PULSE_TIME_BUDGET_SECONDS,
    FAILED_PULSE_EXIT_CODE,
    PlannedDispatchableLane,
    PlannedDurableDefinition,
    PulseLaneResult,
    PulsePlan,
    PulseSummary,
    _parse_lane_filter,
    jobs_pulse,
    known_lane_tokens,
    run_jobs_pulse,
)
from agri_data_service.ingest.lanes import BACKFILL_LANES
from agri_data_service.jobs.dispatch import (
    DispatchOutcome,
    LaneDispatchRegistry,
    LanePauseState,
    register_dispatchable_lane,
)
from agri_data_service.jobs.worker import JobSliceSummary

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

_RUN_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
_FAKE_HANDLER_TOKEN = "tests.jobs_pulse_fake_handler"


class _FakeSession:
    """The narrow slice of `AsyncSession` this verb touches: a statement sink and two boundaries."""

    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement: object, parameters: Mapping[str, object] | None = None) -> None:
        del statement, parameters

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture(autouse=True)
def _patch_ingest_session(monkeypatch: pytest.MonkeyPatch) -> list[_FakeSession]:
    """Hand every call a fresh recorded session, exactly as `test_ingest_commands_jobs.py` does."""
    sessions: list[_FakeSession] = []

    @asynccontextmanager
    async def _fake_ingest_session() -> AsyncIterator[_FakeSession]:
        session = _FakeSession()
        sessions.append(session)
        yield session

    monkeypatch.setattr(jobs_pulse_command, "ingest_session", _fake_ingest_session)
    return sessions


def _dispatchable(lane_id: str, *, paused: bool = False, registered: bool = True) -> PlannedDispatchableLane:
    return PlannedDispatchableLane(
        lane_id=lane_id,
        handler_token=_FAKE_HANDLER_TOKEN,
        pause_state=LanePauseState(registered=registered, paused=paused),
    )


def _durable(lane_token: str, *, definition_name: str | None = None, enabled: bool = True) -> PlannedDurableDefinition:
    return PlannedDurableDefinition(
        definition_name=definition_name or f"agri.ingest.archive_walk.{lane_token}",
        lane_token=lane_token,
        enabled=enabled,
    )


def _plan(
    dispatchable: Sequence[PlannedDispatchableLane] = (),
    durable: Sequence[PlannedDurableDefinition] = (),
) -> PulsePlan:
    return PulsePlan(dispatchable=tuple(dispatchable), durable=tuple(durable))


def _patch_plan(monkeypatch: pytest.MonkeyPatch, plan: PulsePlan) -> None:
    async def _fake_discover(session: object, *, lane_filter: object, registry: object = None) -> PulsePlan:
        del session, lane_filter, registry
        return plan

    monkeypatch.setattr(jobs_pulse_command, "discover_pulse_plan", _fake_discover)


def _slice_summary(definition_name: str, **overrides: object) -> JobSliceSummary:
    fields: dict[str, object] = {
        "definition_name": definition_name,
        "worker_id": "test-worker",
        "job_run_id": _RUN_ID,
        "stop_reason": "no_claimable_work",
        "claimed": 1,
        "succeeded": 1,
        "run_status": "succeeded",
    }
    fields.update(overrides)
    return JobSliceSummary(**fields)


class _ScriptedClock:
    """A `monotonic()` stand-in stepping by a fixed amount per call, from a fixed start.

    Deterministic budget-boundary tests must never depend on real wall-clock timing: two real
    `time.monotonic()` calls close together can (in principle, on some platforms) read equal, and a
    test that assumed otherwise would be flaky by construction.
    """

    def __init__(self, start: float = 0.0, step: float = 1.0) -> None:
        self._next = start
        self._step = step

    def __call__(self) -> float:
        value = self._next
        self._next += self._step
        return value


def _group() -> click.Group:
    group = click.Group("agri-cli")
    group.add_command(jobs_pulse)
    return group


def _invoke(*arguments: str) -> Any:
    return CliRunner().invoke(_group(), list(arguments))


def _last_json_line(output: str) -> dict[str, object]:
    lines = [line for line in output.splitlines() if line.startswith("{")]
    assert lines, f"no JSON line in output: {output!r}"
    return json.loads(lines[-1])


# --- discover_pulse_plan's filter helper -------------------------------------------------------


def test_known_lane_tokens_unions_the_archive_registry_and_the_dispatch_registry() -> None:
    registry = LaneDispatchRegistry()

    async def _trigger(session: object, *, requested_by: str) -> JobSliceSummary:  # pragma: no cover
        raise AssertionError("never driven")

    register_dispatchable_lane(
        lane_id="a-fake-dispatchable-lane",
        handler_token=_FAKE_HANDLER_TOKEN,
        trigger=_trigger,
        description="a test lane",
        registry=registry,
    )

    known = known_lane_tokens(registry)

    assert "a-fake-dispatchable-lane" in known
    assert set(BACKFILL_LANES) <= known


def test_an_empty_lane_filter_means_no_filter() -> None:
    assert _parse_lane_filter(()) is None


def test_a_valid_lane_filter_is_accepted_as_a_frozenset() -> None:
    lane_name = next(iter(BACKFILL_LANES))
    assert _parse_lane_filter((lane_name,)) == frozenset({lane_name})


def test_an_unknown_lane_filter_is_refused_by_name() -> None:
    with pytest.raises(click.BadParameter) as raised:
        _parse_lane_filter(("not-a-real-lane",))
    assert "not-a-real-lane" in str(raised.value)


# --- run_jobs_pulse: pause skip -------------------------------------------------------------------


async def test_a_paused_dispatchable_lane_is_skipped_and_never_dispatched(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan(dispatchable=[_dispatchable("paused-lane", paused=True)])
    _patch_plan(monkeypatch, plan)

    async def _never(*args: object, **kwargs: object) -> DispatchOutcome:
        raise AssertionError("a paused lane's dispatch_lane must never be called")

    monkeypatch.setattr(jobs_pulse_command, "dispatch_lane", _never)

    summary = await run_jobs_pulse(lane_filter=None, time_budget_seconds=600.0, worker_id="w")

    assert len(summary.lanes) == 1
    assert summary.lanes[0].outcome == "paused"
    assert summary.lanes[0].records == 0
    assert summary.failed is False


async def test_a_paused_durable_definition_is_skipped_and_no_slice_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan(durable=[_durable("firms-archive", enabled=False)])
    _patch_plan(monkeypatch, plan)

    async def _never(**kwargs: object) -> JobSliceSummary:
        raise AssertionError("a paused definition's slice must never run")

    monkeypatch.setattr(jobs_pulse_command, "run_archive_definition_slice", _never)

    summary = await run_jobs_pulse(lane_filter=None, time_budget_seconds=600.0, worker_id="w")

    assert len(summary.lanes) == 1
    assert summary.lanes[0].outcome == "paused"
    assert summary.failed is False


# --- run_jobs_pulse: per-lane failure isolation ----------------------------------------------------


async def test_one_dispatchable_lane_raising_does_not_stop_the_next_dispatchable_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(dispatchable=[_dispatchable("bad-lane"), _dispatchable("good-lane")])
    _patch_plan(monkeypatch, plan)
    calls: list[str] = []

    async def _fake_dispatch(session: object, lane_id: str, *, requested_by: str, **kwargs: object) -> DispatchOutcome:
        calls.append(lane_id)
        if lane_id == "bad-lane":
            raise RuntimeError("the dispatcher blew up")
        return DispatchOutcome(lane_id=lane_id, state="dispatched", summary=_slice_summary(lane_id))

    monkeypatch.setattr(jobs_pulse_command, "dispatch_lane", _fake_dispatch)

    summary = await run_jobs_pulse(lane_filter=None, time_budget_seconds=600.0, worker_id="w")

    assert calls == ["bad-lane", "good-lane"]
    by_lane = {lane.lane: lane for lane in summary.lanes}
    assert by_lane["bad-lane"].outcome == "raised"
    assert "the dispatcher blew up" in (by_lane["bad-lane"].detail or "")
    assert by_lane["good-lane"].outcome == "ran"
    assert summary.failed is True


async def test_one_durable_definition_raising_does_not_stop_the_next_durable_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(durable=[_durable("firms-archive"), _durable("streamflow-archive")])
    _patch_plan(monkeypatch, plan)
    calls: list[str] = []

    async def _fake_slice(*, definition_name: str, worker_id: str, budget_seconds: float | None) -> JobSliceSummary:
        calls.append(definition_name)
        if "firms" in definition_name:
            raise RuntimeError("archive walk exploded")
        return _slice_summary(definition_name, claimed=2)

    monkeypatch.setattr(jobs_pulse_command, "run_archive_definition_slice", _fake_slice)

    summary = await run_jobs_pulse(lane_filter=None, time_budget_seconds=600.0, worker_id="w")

    assert len(calls) == 2
    by_lane = {lane.lane: lane for lane in summary.lanes}
    assert by_lane["firms-archive"].outcome == "raised"
    assert by_lane["streamflow-archive"].outcome == "ran"
    assert by_lane["streamflow-archive"].records == 2
    assert summary.failed is True


async def test_a_sqlalchemy_error_never_leaks_its_bound_parameters_into_the_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(dispatchable=[_dispatchable("leaky-lane")])
    _patch_plan(monkeypatch, plan)

    async def _fake_dispatch(session: object, lane_id: str, *, requested_by: str, **kwargs: object) -> DispatchOutcome:
        raise OperationalError("SELECT ...", {"dsn": "postgresql://user:secret@host"}, Exception())

    monkeypatch.setattr(jobs_pulse_command, "dispatch_lane", _fake_dispatch)

    summary = await run_jobs_pulse(lane_filter=None, time_budget_seconds=600.0, worker_id="w")

    assert summary.lanes[0].outcome == "raised"
    assert "secret" not in (summary.lanes[0].detail or "")


async def test_a_dead_lettered_shard_fails_the_tick_without_stopping_the_next_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(dispatchable=[_dispatchable("firms-like-lane")], durable=[_durable("streamflow-archive")])
    _patch_plan(monkeypatch, plan)

    async def _fake_dispatch(session: object, lane_id: str, *, requested_by: str, **kwargs: object) -> DispatchOutcome:
        return DispatchOutcome(
            lane_id=lane_id,
            state="dispatched",
            summary=_slice_summary(lane_id, dead_lettered=1, claimed=1, succeeded=0),
        )

    async def _fake_slice(*, definition_name: str, worker_id: str, budget_seconds: float | None) -> JobSliceSummary:
        return _slice_summary(definition_name)

    monkeypatch.setattr(jobs_pulse_command, "dispatch_lane", _fake_dispatch)
    monkeypatch.setattr(jobs_pulse_command, "run_archive_definition_slice", _fake_slice)

    summary = await run_jobs_pulse(lane_filter=None, time_budget_seconds=600.0, worker_id="w")

    by_lane = {lane.lane: lane for lane in summary.lanes}
    assert by_lane["firms-like-lane"].outcome == "ran"
    assert by_lane["firms-like-lane"].dead_lettered == 1
    assert by_lane["streamflow-archive"].outcome == "ran"
    assert summary.failed is True


# --- run_jobs_pulse: the global time budget --------------------------------------------------------


async def test_a_lane_already_running_is_never_killed_by_the_budget_but_the_next_lane_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(dispatchable=[_dispatchable("first-lane"), _dispatchable("second-lane")])
    _patch_plan(monkeypatch, plan)
    calls: list[str] = []

    async def _fake_dispatch(session: object, lane_id: str, *, requested_by: str, **kwargs: object) -> DispatchOutcome:
        calls.append(lane_id)
        return DispatchOutcome(lane_id=lane_id, state="dispatched", summary=_slice_summary(lane_id))

    monkeypatch.setattr(jobs_pulse_command, "dispatch_lane", _fake_dispatch)
    # start=0.0: `started` reads 0.0, deadline = 1.5. The first lane's own pre-check reads 1.0 (< 1.5,
    # runs); whatever it costs internally, the SECOND lane's pre-check is at least the third call and
    # therefore >= 2.0, past the deadline regardless of exactly how many calls the first lane made.
    clock = _ScriptedClock(start=0.0, step=1.0)

    summary = await run_jobs_pulse(
        lane_filter=None,
        time_budget_seconds=1.5,
        worker_id="w",
        monotonic=clock,
    )

    assert calls == ["first-lane"]
    by_lane = {lane.lane: lane for lane in summary.lanes}
    assert by_lane["first-lane"].outcome == "ran"
    assert by_lane["second-lane"].outcome == "skipped_budget"
    assert by_lane["second-lane"].records == 0
    assert summary.failed is False


async def test_a_zero_budget_skips_every_lane_and_dispatches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan(dispatchable=[_dispatchable("a")], durable=[_durable("firms-archive")])
    _patch_plan(monkeypatch, plan)

    async def _never_dispatch(*args: object, **kwargs: object) -> DispatchOutcome:
        raise AssertionError("no lane may start once the budget is already spent")

    async def _never_slice(**kwargs: object) -> JobSliceSummary:
        raise AssertionError("no lane may start once the budget is already spent")

    monkeypatch.setattr(jobs_pulse_command, "dispatch_lane", _never_dispatch)
    monkeypatch.setattr(jobs_pulse_command, "run_archive_definition_slice", _never_slice)
    # A clock that never advances: every pre-check reads exactly the deadline, so every lane sees the
    # budget as already spent -- deterministic, unlike racing real wall-clock time against a 0.0 budget.
    still_clock = _ScriptedClock(start=0.0, step=0.0)

    summary = await run_jobs_pulse(
        lane_filter=None,
        time_budget_seconds=0.0,
        worker_id="w",
        monotonic=still_clock,
    )

    assert {lane.outcome for lane in summary.lanes} == {"skipped_budget"}
    assert summary.failed is False


# --- The CLI verb: exit codes, --dry-run, --lane -----------------------------------------------------


def test_jobs_pulse_is_registered_and_states_its_exit_rule_in_help() -> None:
    result = _invoke("--help")
    assert result.exit_code == 0
    assert "jobs-pulse" in result.output

    help_result = _invoke("jobs-pulse", "--help")
    assert help_result.exit_code == 0
    assert "--time-budget-seconds" in help_result.output
    assert "--lane" in help_result.output
    assert "--dry-run" in help_result.output
    assert "dead" in help_result.output.lower()


def test_exit_code_0_when_nothing_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_run_jobs_pulse(**kwargs: object) -> PulseSummary:
        return PulseSummary(lanes=())

    monkeypatch.setattr(jobs_pulse_command, "run_jobs_pulse", _fake_run_jobs_pulse)

    result = _invoke("jobs-pulse")

    assert result.exit_code == 0
    assert _last_json_line(result.output)["lane_count"] == 0


def test_exit_code_1_when_a_lane_raised_but_the_summary_is_still_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_run_jobs_pulse(**kwargs: object) -> PulseSummary:
        return PulseSummary(
            lanes=(
                PulseLaneResult(
                    lane="bad-lane", kind="dispatchable", outcome="raised", seconds=0.1, records=0, detail="boom"
                ),
            )
        )

    monkeypatch.setattr(jobs_pulse_command, "run_jobs_pulse", _fake_run_jobs_pulse)

    result = _invoke("jobs-pulse")

    assert result.exit_code == FAILED_PULSE_EXIT_CODE
    payload = _last_json_line(result.output)
    assert payload["raised"] == 1


def test_exit_code_1_when_a_lane_dead_lettered(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_run_jobs_pulse(**kwargs: object) -> PulseSummary:
        return PulseSummary(
            lanes=(
                PulseLaneResult(
                    lane="firms-archive", kind="durable", outcome="ran", seconds=1.0, records=5, dead_lettered=1
                ),
            )
        )

    monkeypatch.setattr(jobs_pulse_command, "run_jobs_pulse", _fake_run_jobs_pulse)

    result = _invoke("jobs-pulse")

    assert result.exit_code == FAILED_PULSE_EXIT_CODE
    assert _last_json_line(result.output)["dead_lettered_lanes"] == 1


def test_exit_code_0_when_only_paused_and_skipped_budget_lanes_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_run_jobs_pulse(**kwargs: object) -> PulseSummary:
        return PulseSummary(
            lanes=(
                PulseLaneResult(lane="a", kind="dispatchable", outcome="paused", seconds=0.0, records=0),
                PulseLaneResult(lane="b", kind="durable", outcome="skipped_budget", seconds=0.0, records=0),
            )
        )

    monkeypatch.setattr(jobs_pulse_command, "run_jobs_pulse", _fake_run_jobs_pulse)

    result = _invoke("jobs-pulse")

    assert result.exit_code == 0


def test_dry_run_prints_a_plan_and_never_calls_run_jobs_pulse(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan(
        dispatchable=[_dispatchable("a")],
        durable=[_durable("firms-archive", enabled=False)],
    )
    _patch_plan(monkeypatch, plan)

    async def _never(**kwargs: object) -> PulseSummary:
        raise AssertionError("--dry-run must never execute a lane")

    monkeypatch.setattr(jobs_pulse_command, "run_jobs_pulse", _never)

    result = _invoke("jobs-pulse", "--dry-run")

    assert result.exit_code == 0
    payload = _last_json_line(result.output)
    assert payload["dispatchable"][0]["lane"] == "a"
    assert payload["dispatchable"][0]["would_run"] is True
    assert payload["durable"][0]["lane"] == "firms-archive"
    assert payload["durable"][0]["would_run"] is False
    assert payload["durable"][0]["paused"] is True


def test_an_unknown_lane_filter_is_refused_before_anything_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _never(**kwargs: object) -> PulseSummary:
        raise AssertionError("a refused --lane must never reach run_jobs_pulse")

    monkeypatch.setattr(jobs_pulse_command, "run_jobs_pulse", _never)

    result = _invoke("jobs-pulse", "--lane", "not-a-real-lane")

    assert result.exit_code != 0
    assert "not-a-real-lane" in result.output


def test_a_known_lane_filter_reaches_run_jobs_pulse_as_a_frozenset(monkeypatch: pytest.MonkeyPatch) -> None:
    lane_name = next(iter(BACKFILL_LANES))
    captured: dict[str, object] = {}

    async def _fake_run_jobs_pulse(**kwargs: object) -> PulseSummary:
        captured.update(kwargs)
        return PulseSummary(lanes=())

    monkeypatch.setattr(jobs_pulse_command, "run_jobs_pulse", _fake_run_jobs_pulse)

    result = _invoke("jobs-pulse", "--lane", lane_name)

    assert result.exit_code == 0
    assert captured["lane_filter"] == frozenset({lane_name})


def test_a_negative_time_budget_is_refused_by_click() -> None:
    result = _invoke("jobs-pulse", "--time-budget-seconds", "-5")
    assert result.exit_code != 0


def test_the_time_budget_default_and_an_override_both_reach_run_jobs_pulse(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_run_jobs_pulse(**kwargs: object) -> PulseSummary:
        captured.update(kwargs)
        return PulseSummary(lanes=())

    monkeypatch.setattr(jobs_pulse_command, "run_jobs_pulse", _fake_run_jobs_pulse)

    _invoke("jobs-pulse")
    assert captured["time_budget_seconds"] == DEFAULT_PULSE_TIME_BUDGET_SECONDS

    _invoke("jobs-pulse", "--time-budget-seconds", "42")
    assert captured["time_budget_seconds"] == 42.0
