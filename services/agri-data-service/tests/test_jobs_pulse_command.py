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
    MaintenanceStepContractError,
    MaintenanceStepReport,
    PlannedDispatchableLane,
    PlannedDurableDefinition,
    PlannedMaintenanceStep,
    PulseLaneResult,
    PulsePlan,
    PulseSummary,
    _parse_lane_filter,
    _plan_maintenance_steps,
    _run_jobs_pulse_process,
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
from agri_data_service.jobs.worker import JobSliceSummary, ShutdownSignal

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

_RUN_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
_FAKE_HANDLER_TOKEN = "tests.jobs_pulse_fake_handler"


class _FakeResult:
    """The two-call shape `jobs.lease.fetch_row` drives: `.mappings().first()`."""

    def __init__(self, row: Mapping[str, object] | None) -> None:
        self._row = row

    def mappings(self) -> _FakeResult:
        return self

    def first(self) -> Mapping[str, object] | None:
        return self._row


class _Ledger:
    """The ledger state the fake session answers from: how much work each definition has buried.

    Keyed by `agri.job_definition.name` -- which for a dispatchable lane IS its `lane_id`, and for a
    durable lane is `PlannedDurableDefinition.definition_name`. A definition absent from the mapping has
    buried nothing, which is the healthy default every existing test in this module relies on.
    """

    def __init__(self) -> None:
        self.dead_lettered_by_definition: dict[str, int] = {}
        self.census_calls: list[str] = []
        self.census_error: Exception | None = None

    def bury(self, definition_name: str, count: int) -> None:
        """Put `count` work items of this definition into 'dead_letter', as an earlier tick would have."""
        self.dead_lettered_by_definition[definition_name] = count

    def census_row(self, definition_name: str) -> Mapping[str, object]:
        """The one row `sql/jobs/count_dead_lettered_work_items.sql` returns for this definition."""
        self.census_calls.append(definition_name)
        if self.census_error is not None:
            raise self.census_error
        buried = self.dead_lettered_by_definition.get(definition_name, 0)
        return {
            "dead_lettered_work_items": buried,
            "first_dead_lettered_shard_key": f"{definition_name}:shard-0" if buried else None,
            "last_dead_lettered_at": None,
        }


class _FakeSession:
    """The narrow slice of `AsyncSession` this verb touches: a statement sink and two boundaries.

    `execute` answers the dead-letter census for real, because the standing-failure signal is issued
    through this session by the pulse's own code path -- so a test that wants a lane to be carrying
    buried work states that on the ledger and lets `_fold_in_standing_dead_letters` find it, rather than
    hand-building the classifier's output.
    """

    def __init__(self, ledger: _Ledger) -> None:
        self.commits = 0
        self.rollbacks = 0
        self._ledger = ledger

    async def execute(self, statement: object, parameters: Mapping[str, object] | None = None) -> _FakeResult:
        if parameters and "definition_name" in parameters:
            return _FakeResult(self._ledger.census_row(str(parameters["definition_name"])))
        del statement
        return _FakeResult(None)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture
def ledger() -> _Ledger:
    """The ledger every fake session in one test answers from; empty (nothing buried) by default."""
    return _Ledger()


@pytest.fixture(autouse=True)
def _patch_ingest_session(monkeypatch: pytest.MonkeyPatch, ledger: _Ledger) -> list[_FakeSession]:
    """Hand every call a fresh recorded session, exactly as `test_ingest_commands_jobs.py` does."""
    sessions: list[_FakeSession] = []

    @asynccontextmanager
    async def _fake_ingest_session() -> AsyncIterator[_FakeSession]:
        session = _FakeSession(ledger)
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


def _maintenance(
    step: Any,
    *,
    lane_token: str | None = None,
    enabled: bool = True,
) -> PlannedMaintenanceStep:
    return PlannedMaintenanceStep(step=step, lane_token=lane_token, enabled=enabled)


def _plan(
    dispatchable: Sequence[PlannedDispatchableLane] = (),
    durable: Sequence[PlannedDurableDefinition] = (),
    maintenance: Sequence[PlannedMaintenanceStep] = (),
) -> PulsePlan:
    return PulsePlan(
        dispatchable=tuple(dispatchable),
        durable=tuple(durable),
        maintenance=tuple(maintenance),
    )


def _patch_plan(monkeypatch: pytest.MonkeyPatch, plan: PulsePlan) -> None:
    async def _fake_discover(
        session: object,
        *,
        lane_filter: object,
        registry: object = None,
        include_maintenance: bool = True,
    ) -> PulsePlan:
        del session, lane_filter, registry
        return plan if include_maintenance else _plan(plan.dispatchable, plan.durable)

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


class _RecordingLogger:
    """The three structlog levels this module emits, captured as `(level, event, fields)` triples."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def info(self, event: str, **fields: Any) -> None:
        self.calls.append(("info", event, fields))

    def warning(self, event: str, **fields: Any) -> None:
        self.calls.append(("warning", event, fields))

    def error(self, event: str, **fields: Any) -> None:
        self.calls.append(("error", event, fields))


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
    group = click.Group("agri-service")
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


async def test_process_shutdown_signal_reaches_dispatchable_and_archive_slices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = ShutdownSignal()
    _patch_plan(
        monkeypatch,
        _plan(dispatchable=[_dispatchable("matview-refresh")], durable=[_durable("firms-archive")]),
    )
    observed: list[object] = []

    async def _fake_dispatch(
        session: object,
        lane_id: str,
        *,
        requested_by: str,
        **kwargs: object,
    ) -> DispatchOutcome:
        observed.append(kwargs["stop"])
        return DispatchOutcome(lane_id=lane_id, state="dispatched", summary=_slice_summary(lane_id))

    async def _fake_slice(
        *,
        definition_name: str,
        worker_id: str,
        budget_seconds: float | None,
        stop: object,
    ) -> JobSliceSummary:
        observed.append(stop)
        return _slice_summary(definition_name)

    monkeypatch.setattr(jobs_pulse_command, "dispatch_lane", _fake_dispatch)
    monkeypatch.setattr(jobs_pulse_command, "run_archive_definition_slice", _fake_slice)

    await run_jobs_pulse(
        lane_filter=None,
        time_budget_seconds=600,
        worker_id="w",
        include_maintenance=False,
        stop=stop,
    )

    assert observed == [stop, stop]


async def test_jobs_pulse_process_installs_exactly_one_shared_shutdown_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = ShutdownSignal()
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def _signal() -> AsyncIterator[object]:
        yield stop

    async def _pulse(**kwargs: object) -> PulseSummary:
        captured.update(kwargs)
        return PulseSummary(lanes=())

    monkeypatch.setattr(jobs_pulse_command, "shutdown_signal", _signal)
    monkeypatch.setattr(jobs_pulse_command, "run_jobs_pulse", _pulse)

    await _run_jobs_pulse_process(
        lane_filter=frozenset({"matview-refresh"}),
        time_budget_seconds=600,
        worker_id="w",
        include_maintenance=False,
    )

    assert captured["stop"] is stop


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
    # NOT `ran`. A tick that buried a work item must not report the outcome a clean tick reports; that
    # ambiguity is what let production show SUCCESS hourly for ~24h with two lanes fully dead-lettering.
    assert by_lane["firms-like-lane"].outcome == "dead_lettered"
    assert by_lane["firms-like-lane"].dead_lettered == 1
    assert "dead-lettered 1 of 1 claimed" in (by_lane["firms-like-lane"].detail or "")
    assert by_lane["streamflow-archive"].outcome == "ran"
    assert summary.failed is True


# --- run_jobs_pulse: the STANDING failure signal ---------------------------------------------------
#
# What the runtime actually produces on the ticks after a burial, and why these tests are shaped this
# way. `sql/jobs/select_open_job_run.sql` selects a run only while its status is 'queued'/'running'.
# Once `refresh_job_run_rollup.sql` rolls a run up to 'failed'/'partial' it is no longer selectable, so
# `run_job_slice` takes its `run_id is None` branch and returns `stop_reason='no_open_run'` with
# `run_status` left at its `None` default. Every one of these tests therefore hands the pulse THAT
# shape -- the shape the runtime can really emit after burial -- and states the buried work on the
# ledger, where the real signal is read from.


def _post_burial_summary(definition_name: str) -> JobSliceSummary:
    """The summary `run_job_slice` really returns on every tick after a lane's work has been buried."""
    return _slice_summary(
        definition_name,
        job_run_id=None,
        stop_reason="no_open_run",
        claimed=0,
        succeeded=0,
        dead_lettered=0,
        run_status=None,
    )


async def test_a_lane_carrying_buried_work_stays_red_on_tick_2_and_3_not_only_the_tick_that_buried_it(
    monkeypatch: pytest.MonkeyPatch,
    ledger: _Ledger,
) -> None:
    # THE 24h-GREEN BUG, exercised on the real path. The tick that did the burying was already loud
    # through `dead_lettered > 0`; the hole was every tick AFTER it. The old signal read
    # `JobSliceSummary.run_status`, which on these ticks is `None` because no run is selectable any
    # more -- so it classified them `ran`. Three consecutive ticks must all be red.
    definition_name = "agri.ingest.archive_walk.firms-archive"
    _patch_plan(monkeypatch, _plan(durable=[_durable("firms-archive")]))
    ledger.bury(definition_name, 3)

    async def _fake_slice(*, definition_name: str, worker_id: str, budget_seconds: float | None) -> JobSliceSummary:
        return _post_burial_summary(definition_name)

    monkeypatch.setattr(jobs_pulse_command, "run_archive_definition_slice", _fake_slice)

    for tick in (1, 2, 3):
        summary = await run_jobs_pulse(lane_filter=None, time_budget_seconds=600.0, worker_id="w")
        lane = summary.lanes[0]
        assert lane.outcome == "standing_dead_letters", f"tick {tick} read as {lane.outcome}"
        assert lane.dead_lettered == 0, "nothing was buried THIS tick; the burial was earlier"
        assert lane.standing_dead_letters == 3
        assert "3 work item(s) standing dead-lettered" in (lane.detail or "")
        assert summary.failed is True, f"tick {tick} exited green with three work items buried"

    # And the census really was issued once per lane per tick, not derived from the slice summary.
    assert ledger.census_calls == [definition_name] * 3


async def test_an_operator_cancellation_never_reds_the_tick(
    monkeypatch: pytest.MonkeyPatch,
    ledger: _Ledger,
) -> None:
    # THE SAFETY PROPERTY. Cancellation is recorded on the WORK ITEM as 'cancelled', and
    # `refresh_job_run_rollup.sql` counts a cancelled item as `failed`, so a cancelled lane's run rolls
    # up to 'partial' (or 'failed') -- there is no 'cancelled' branch in that CASE and nothing anywhere
    # writes 'cancelled' to `job_run.status`. Reading that rollup is what made "cancel it deliberately"
    # an instruction that turned the hourly cron red forever with no state left to clear. Nothing is in
    # 'dead_letter' here, so the tick must be GREEN.
    definition_name = "agri.ingest.archive_walk.firms-archive"
    _patch_plan(monkeypatch, _plan(durable=[_durable("firms-archive")]))
    assert definition_name not in ledger.dead_lettered_by_definition

    async def _fake_slice(*, definition_name: str, worker_id: str, budget_seconds: float | None) -> JobSliceSummary:
        return _slice_summary(definition_name, claimed=0, succeeded=0, run_status="partial")

    monkeypatch.setattr(jobs_pulse_command, "run_archive_definition_slice", _fake_slice)

    summary = await run_jobs_pulse(lane_filter=None, time_budget_seconds=600.0, worker_id="w")

    assert summary.lanes[0].outcome == "ran"
    assert summary.lanes[0].standing_dead_letters == 0
    assert summary.failed is False


@pytest.mark.parametrize("run_status", ["queued", "running", "succeeded", "partial", "failed", None])
async def test_no_writable_run_status_decides_the_tick_on_its_own(
    monkeypatch: pytest.MonkeyPatch,
    run_status: str | None,
) -> None:
    # Those six ARE the whole writable vocabulary of `agri.job_run.status`: `insert_job_run.sql` writes
    # 'queued', `refresh_job_run_rollup.sql`'s CASE writes the other four, and `None` is what a tick
    # that selected no run reports. ('dead_letter' and 'cancelled' are in the table's CHECK constraint
    # but no statement in this service ever writes them.) With nothing buried, every one of them is a
    # green tick -- the verdict comes from the work items, not from the run.
    _patch_plan(monkeypatch, _plan(durable=[_durable("firms-archive")]))

    async def _fake_slice(*, definition_name: str, worker_id: str, budget_seconds: float | None) -> JobSliceSummary:
        return _slice_summary(definition_name, claimed=0, succeeded=0, run_status=run_status)

    monkeypatch.setattr(jobs_pulse_command, "run_archive_definition_slice", _fake_slice)

    summary = await run_jobs_pulse(lane_filter=None, time_budget_seconds=600.0, worker_id="w")

    assert summary.lanes[0].outcome == "ran"
    assert summary.failed is False


async def test_the_burial_census_is_issued_even_for_a_lane_whose_own_dispatch_raised(
    monkeypatch: pytest.MonkeyPatch,
    ledger: _Ledger,
) -> None:
    # UNCONDITIONAL means unconditional. A census only issued on the healthy paths would be exactly the
    # conditional signal this replaced -- and a lane that raises is the likeliest one to be carrying
    # buried work. The `raised` outcome wins the label (it is this tick's own evidence), but the count
    # rides along and `failing_lanes` triggers on it independently.
    _patch_plan(monkeypatch, _plan(dispatchable=[_dispatchable("matview-refresh")]))
    ledger.bury("matview-refresh", 4)

    async def _fake_dispatch(session: object, lane_id: str, *, requested_by: str, **kwargs: object) -> DispatchOutcome:
        raise RuntimeError("the dispatcher blew up")

    monkeypatch.setattr(jobs_pulse_command, "dispatch_lane", _fake_dispatch)

    summary = await run_jobs_pulse(lane_filter=None, time_budget_seconds=600.0, worker_id="w")

    assert ledger.census_calls == ["matview-refresh"]
    lane = summary.lanes[0]
    assert lane.outcome == "raised"
    assert lane.standing_dead_letters == 4
    assert "the dispatcher blew up" in (lane.detail or "")
    assert "4 work item(s) standing dead-lettered" in (lane.detail or "")
    assert summary.failed is True


async def test_a_census_that_cannot_be_read_fails_closed_rather_than_reporting_a_clean_lane(
    monkeypatch: pytest.MonkeyPatch,
    ledger: _Ledger,
) -> None:
    # "We do not know whether this lane is carrying buried work" is not the same claim as "it is not".
    _patch_plan(monkeypatch, _plan(durable=[_durable("firms-archive")]))
    ledger.census_error = OperationalError("SELECT ...", {"dsn": "postgresql://user:secret@host"}, Exception())

    async def _fake_slice(*, definition_name: str, worker_id: str, budget_seconds: float | None) -> JobSliceSummary:
        return _slice_summary(definition_name)

    monkeypatch.setattr(jobs_pulse_command, "run_archive_definition_slice", _fake_slice)

    summary = await run_jobs_pulse(lane_filter=None, time_budget_seconds=600.0, worker_id="w")

    assert summary.lanes[0].outcome == "raised"
    assert "dead-letter census failed" in (summary.lanes[0].detail or "")
    assert "secret" not in (summary.lanes[0].detail or "")
    assert summary.failed is True


async def test_a_paused_lane_is_never_censused_because_it_was_never_run(
    monkeypatch: pytest.MonkeyPatch,
    ledger: _Ledger,
) -> None:
    # A paused lane is skipped in every pass, so it gets no verdict of any kind. Pausing is itself an
    # operator decision and, like cancellation, must not page anyone hourly for having been made.
    _patch_plan(monkeypatch, _plan(durable=[_durable("firms-archive", enabled=False)]))
    ledger.bury("agri.ingest.archive_walk.firms-archive", 9)

    summary = await run_jobs_pulse(lane_filter=None, time_budget_seconds=600.0, worker_id="w")

    assert ledger.census_calls == []
    assert summary.lanes[0].outcome == "paused"
    assert summary.failed is False


async def test_a_failing_tick_logs_at_error_and_a_healthy_one_does_not(monkeypatch: pytest.MonkeyPatch) -> None:
    # The whole point of Task 2's logging half: a log scrape for ERROR over a fully dead lane found
    # nothing, because `jobs/dispatch.py`'s `lane_dispatched` INFO line was the only line emitted.
    recorded = _RecordingLogger()
    monkeypatch.setattr(jobs_pulse_command, "logger", recorded)
    _patch_plan(monkeypatch, _plan(durable=[_durable("firms-archive")]))

    async def _buried(*, definition_name: str, worker_id: str, budget_seconds: float | None) -> JobSliceSummary:
        return _slice_summary(definition_name, claimed=2, succeeded=0, dead_lettered=2, run_status="failed")

    monkeypatch.setattr(jobs_pulse_command, "run_archive_definition_slice", _buried)
    await run_jobs_pulse(lane_filter=None, time_budget_seconds=600.0, worker_id="w")

    assert [event for level, event, _ in recorded.calls if level == "error"] == ["jobs_pulse_tick_failed"]
    assert "jobs_pulse_tick_healthy" not in [event for _, event, _ in recorded.calls]
    failed_call = next(fields for level, event, fields in recorded.calls if event == "jobs_pulse_tick_failed")
    assert failed_call["failing_lane_count"] == 1
    assert failed_call["failing_lanes"][0]["outcome"] == "dead_lettered"

    recorded.calls.clear()

    async def _clean(*, definition_name: str, worker_id: str, budget_seconds: float | None) -> JobSliceSummary:
        return _slice_summary(definition_name)

    monkeypatch.setattr(jobs_pulse_command, "run_archive_definition_slice", _clean)
    await run_jobs_pulse(lane_filter=None, time_budget_seconds=600.0, worker_id="w")

    assert [event for level, event, _ in recorded.calls if level == "error"] == []
    assert "jobs_pulse_tick_healthy" in [event for _, event, _ in recorded.calls]


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


def test_exit_code_1_when_a_lane_is_carrying_buried_work_from_an_earlier_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Nothing dead-lettered THIS tick, so the `dead_lettered > 0` trigger cannot fire -- the standing
    # count is the only evidence, and it must be enough on its own. The outer executor turns this
    # into bounded retry/dead-letter state rather than a process restart loop.
    async def _fake_run_jobs_pulse(**kwargs: object) -> PulseSummary:
        return PulseSummary(
            lanes=(
                PulseLaneResult(
                    lane="matview-refresh",
                    kind="dispatchable",
                    outcome="standing_dead_letters",
                    seconds=1.0,
                    records=0,
                    standing_dead_letters=2,
                    detail="2 work item(s) standing dead-lettered for this definition",
                ),
            )
        )

    monkeypatch.setattr(jobs_pulse_command, "run_jobs_pulse", _fake_run_jobs_pulse)

    result = _invoke("jobs-pulse")

    assert result.exit_code == FAILED_PULSE_EXIT_CODE
    payload = _last_json_line(result.output)
    assert payload["standing_dead_letters"] == 1
    assert payload["standing_dead_letter_lanes"] == 1
    assert payload["failed"] is True
    assert payload["failing_lanes"] == ["matview-refresh"]
    # The healthy counter must NOT have absorbed it: a failing lane counted as `ran` is the whole bug.
    assert payload["ran"] == 0


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


# --- the data-quality maintenance pass ---------------------------------------------------------
#
# This pass restored on 2026-08-14 what the cron consolidation had left unscheduled. The tests below
# assert the three properties that make it worth having: it is DERIVED from the ledger's own lane set
# (so a new lane is maintained with no second list to update), it runs in an order where reconcile
# settles before gap-planning measures, and it carries `validate-streams`' exit rule through
# unchanged -- `invalid` fails the tick, `incomplete` does not.


def _fake_maintenance(monkeypatch: pytest.MonkeyPatch, **by_step: object) -> list[str]:
    """Record the order steps ran in, answering each with a scripted report or raising its exception."""
    order: list[str] = []

    # `**_step_context` is tolerated rather than required: `_execute_maintenance_step` takes only the
    # planned step today (the `monotonic`/`census_probes` pair went away with the shard census probe),
    # and these cases assert step ORDER and exit rules rather than any step's own arguments.
    async def _fake_execute(planned: PlannedMaintenanceStep, **_step_context: object) -> object:
        order.append(planned.step_id)
        answer = by_step.get(planned.step, MaintenanceStepReport(outcome="ran", records=0, detail=None))
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(jobs_pulse_command, "_execute_maintenance_step", _fake_execute)
    return order


async def _pulse(monkeypatch: pytest.MonkeyPatch, plan: PulsePlan, *, budget: float) -> PulseSummary:
    _patch_plan(monkeypatch, plan)
    return await run_jobs_pulse(
        lane_filter=None,
        time_budget_seconds=budget,
        worker_id="test-worker",
        monotonic=_ScriptedClock(),
    )


def test_maintenance_is_planned_from_the_durable_lane_set_not_a_second_list() -> None:
    steps = _plan_maintenance_steps(
        (_durable("firms-archive"), _durable("streamflow-archive", enabled=False)),
        lane_filter=None,
    )

    assert [step.step_id for step in steps] == [
        "firms-archive:reconcile",
        "firms-archive:plan-gaps",
        "streamflow-archive:reconcile",
        "streamflow-archive:plan-gaps",
        "validate-streams",
    ]
    # The pause switch is inherited from the lane's own ledger row rather than read a second time.
    assert [step.enabled for step in steps] == [True, True, False, False, True]


def test_reconcile_precedes_plan_gaps_for_each_lane_and_validation_runs_last() -> None:
    steps = _plan_maintenance_steps((_durable("firms-archive"),), lane_filter=None)
    ordering = [step.step for step in steps]
    assert ordering.index("reconcile") < ordering.index("plan-gaps") < ordering.index("validate-streams")


def test_a_lane_filtered_tick_plans_no_global_stream_validation() -> None:
    steps = _plan_maintenance_steps((_durable("firms-archive"),), lane_filter=frozenset({"firms-archive"}))
    assert [step.step for step in steps] == ["reconcile", "plan-gaps"]


def test_a_per_lane_step_planned_with_no_lane_refuses_rather_than_maintaining_nothing() -> None:
    with pytest.raises(MaintenanceStepContractError):
        _maintenance("reconcile").require_lane_token()


@pytest.mark.asyncio
async def test_a_paused_lane_is_never_maintained(monkeypatch: pytest.MonkeyPatch) -> None:
    order = _fake_maintenance(monkeypatch)

    summary = await _pulse(
        monkeypatch,
        _plan(maintenance=[_maintenance("reconcile", lane_token="firms-archive", enabled=False)]),
        budget=DEFAULT_PULSE_TIME_BUDGET_SECONDS,
    )

    assert order == []
    assert [lane.outcome for lane in summary.lanes] == ["paused"]
    assert not summary.failed


@pytest.mark.asyncio
async def test_an_invalid_stream_fails_the_tick_and_is_reported_apart_from_a_raised_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_maintenance(
        monkeypatch,
        **{"validate-streams": MaintenanceStepReport(outcome="invalid", records=7, detail="invalid=1")},
    )

    summary = await _pulse(
        monkeypatch,
        _plan(maintenance=[_maintenance("validate-streams")]),
        budget=DEFAULT_PULSE_TIME_BUDGET_SECONDS,
    )

    assert summary.failed
    report = summary.to_summary()
    assert report["invalid"] == 1
    # `raised` stays zero: the check ran perfectly, it just found rows that are wrong.
    assert report["raised"] == 0


@pytest.mark.asyncio
async def test_an_incomplete_only_validation_keeps_the_tick_green(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_maintenance(
        monkeypatch,
        **{"validate-streams": MaintenanceStepReport(outcome="ran", records=7, detail="incomplete=3")},
    )

    summary = await _pulse(
        monkeypatch,
        _plan(maintenance=[_maintenance("validate-streams")]),
        budget=DEFAULT_PULSE_TIME_BUDGET_SECONDS,
    )

    assert not summary.failed


@pytest.mark.asyncio
async def test_one_maintenance_step_raising_never_stops_the_next(monkeypatch: pytest.MonkeyPatch) -> None:
    order = _fake_maintenance(monkeypatch, reconcile=OperationalError("boom", None, Exception("boom")))

    summary = await _pulse(
        monkeypatch,
        _plan(
            maintenance=[
                _maintenance("reconcile", lane_token="firms-archive"),
                _maintenance("plan-gaps", lane_token="firms-archive"),
            ]
        ),
        budget=DEFAULT_PULSE_TIME_BUDGET_SECONDS,
    )

    assert order == ["firms-archive:reconcile", "firms-archive:plan-gaps"]
    assert [lane.outcome for lane in summary.lanes] == ["raised", "ran"]
    assert summary.failed


@pytest.mark.asyncio
async def test_maintenance_runs_after_both_lane_passes_so_a_spent_budget_drops_checking_not_walking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = _fake_maintenance(monkeypatch)

    async def _fake_dispatch(session: object, lane_id: str, *, requested_by: str, **kwargs: object) -> DispatchOutcome:
        del session, requested_by, kwargs
        order.append(f"dispatch:{lane_id}")
        return DispatchOutcome(lane_id=lane_id, state="dispatched", summary=_slice_summary(lane_id))

    async def _fake_slice(*, definition_name: str, worker_id: str, budget_seconds: float | None) -> JobSliceSummary:
        del worker_id, budget_seconds
        order.append(f"slice:{definition_name}")
        return _slice_summary(definition_name)

    monkeypatch.setattr(jobs_pulse_command, "dispatch_lane", _fake_dispatch)
    monkeypatch.setattr(jobs_pulse_command, "run_archive_definition_slice", _fake_slice)

    await _pulse(
        monkeypatch,
        _plan(
            dispatchable=[_dispatchable("strategy-mv-refresh")],
            durable=[_durable("firms-archive")],
            maintenance=[_maintenance("validate-streams")],
        ),
        budget=DEFAULT_PULSE_TIME_BUDGET_SECONDS,
    )

    assert order == [
        "dispatch:strategy-mv-refresh",
        "slice:agri.ingest.archive_walk.firms-archive",
        "validate-streams",
    ]


@pytest.mark.asyncio
async def test_an_exhausted_budget_skips_a_maintenance_step_rather_than_starting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = _fake_maintenance(monkeypatch)

    summary = await _pulse(monkeypatch, _plan(maintenance=[_maintenance("validate-streams")]), budget=0.0)

    assert order == []
    assert [lane.outcome for lane in summary.lanes] == ["skipped_budget"]
    assert not summary.failed


def test_skip_maintenance_reaches_run_jobs_pulse_and_defaults_to_running_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def _fake_run_jobs_pulse(**kwargs: object) -> PulseSummary:
        captured.update(kwargs)
        return PulseSummary(lanes=())

    monkeypatch.setattr(jobs_pulse_command, "run_jobs_pulse", _fake_run_jobs_pulse)

    _invoke("jobs-pulse")
    assert captured["include_maintenance"] is True

    _invoke("jobs-pulse", "--skip-maintenance")
    assert captured["include_maintenance"] is False


def test_the_dry_run_report_lists_the_maintenance_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_plan(
        monkeypatch,
        _plan(
            durable=[_durable("firms-archive")],
            maintenance=[_maintenance("reconcile", lane_token="firms-archive"), _maintenance("validate-streams")],
        ),
    )

    result = _invoke("jobs-pulse", "--dry-run")

    assert result.exit_code == 0
    maintenance = _last_json_line(result.output)["maintenance"]
    assert isinstance(maintenance, list)
    assert [entry["lane"] for entry in maintenance] == ["firms-archive:reconcile", "validate-streams"]
    assert all(entry["would_run"] for entry in maintenance)
