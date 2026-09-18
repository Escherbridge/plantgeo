"""After a stall nobody is at the keyboard: a new process releases a breaker once, and the leader authors repairs.

No database and no object store. The planner's ledger reads are pinned as in `test_lane_cadence.py`; the
supersession recorder and the gap-repair authoring functions are replaced by recording fakes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import OperationalError

from agri_data_service.execution import gap_repair, job_executor_service, job_run_supersession
from agri_data_service.execution.job_executor_service import (
    CLOCK_RELEASE_STREAK_LIMIT,
    DEFAULT_REPAIR_INTERVAL_SECONDS,
    EXECUTOR_DEFINITION_PREFIX,
    LANE_SPECS,
    PROCESS_START_RELEASE_OPERATOR,
    ActivationConfig,
    ExecutorConfigurationError,
    LatestRun,
    ProcessStartRelease,
    RepairAuthoringClock,
    scheduled_bucket,
)
from agri_data_service.execution.lane_ids import DROUGHT_DIRECT_LANE_ID
from agri_data_service.jobs import JobDefinitionRecord

if TYPE_CHECKING:
    from agri_data_service.execution.job_executor_service import LaneExecutionSpec

ACTIVE = ActivationConfig(frozenset({DROUGHT_DIRECT_LANE_ID}))
PROCESS_STARTED = datetime(2026, 9, 18, 6, 0, tzinfo=UTC)
NOW = datetime(2026, 9, 18, 6, 46, tzinfo=UTC)
HELD_RUN = uuid.UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
BREAKER_STREAK = CLOCK_RELEASE_STREAK_LIMIT[LANE_SPECS[DROUGHT_DIRECT_LANE_ID].catch_up_policy]


def _definition(spec: LaneExecutionSpec) -> JobDefinitionRecord:
    return JobDefinitionRecord(
        id=uuid.uuid4(),
        name=f"{EXECUTOR_DEFINITION_PREFIX}{spec.lane_id}",
        version="2",
        handler="plantgeo.executor.command.v1",
        queue_name="default",
        concurrency_key=None,
        max_attempts=5,
        lease_seconds=spec.command_timeout_seconds + 120,
        time_budget_seconds=spec.command_timeout_seconds + 30,
        retry_policy={},
        parameters={},
    )


def _held(scheduled_for: datetime, *, run_id: uuid.UUID = HELD_RUN) -> LatestRun:
    return LatestRun(
        run_id=run_id,
        scheduled_for=scheduled_for,
        status="failed",
        work_claimable=False,
        consecutive_failures=BREAKER_STREAK,
    )


@pytest.fixture
def ledger(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """`markers` is the durable (deployment, lane) set: it survives a simulated restart, unlike the release memo."""
    held: dict[str, object] = {"latest": None, "recorded": [], "committed": 0, "markers": set()}

    async def claim(
        _session: object, *, lane_id: str, deployment: str, operator: str, now: datetime, detail: object
    ) -> bool:
        del operator, now, detail
        markers = held["markers"]
        assert isinstance(markers, set)
        if (deployment, lane_id) in markers:
            return False
        markers.add((deployment, lane_id))
        return True

    monkeypatch.setattr(job_run_supersession, "claim_process_start_release", claim)

    async def load_or_register(_session: object, spec: LaneExecutionSpec) -> JobDefinitionRecord:
        return _definition(spec)

    async def checkpoint(_session: object, _spec: LaneExecutionSpec) -> LatestRun | None:
        return held["latest"]  # type: ignore[return-value]

    async def rollback(_session: object) -> None:
        return None

    async def commit(_session: object) -> None:
        held["committed"] = int(held["committed"]) + 1  # type: ignore[call-overload]

    async def supersede(  # noqa: PLR0913 - mirrors the real signature
        _session: object,
        spec: LaneExecutionSpec,
        run_id: uuid.UUID,
        *,
        ledger: str,
        evidence: str,
        operator: str,
        now: datetime,
        apply: bool,
    ) -> object:
        recorded = held["recorded"]
        assert isinstance(recorded, list)
        recorded.append((spec.lane_id, run_id, ledger, evidence, operator, now, apply))
        return type("Receipt", (), {"outcome": "recorded"})()

    monkeypatch.setattr(job_executor_service, "_load_or_register_definition", load_or_register)
    monkeypatch.setattr(job_executor_service, "read_lane_checkpoint", checkpoint)
    monkeypatch.setattr(job_executor_service, "_rollback_planning_transaction", rollback)
    monkeypatch.setattr(job_executor_service, "_commit_planning_transaction", commit)
    monkeypatch.setattr(job_executor_service, "_ledger_label", lambda: "ledger.test/plantgeo")
    monkeypatch.setattr(job_run_supersession, "supersede_failed_run", supersede)
    only_drought = MappingProxyType({DROUGHT_DIRECT_LANE_ID: LANE_SPECS[DROUGHT_DIRECT_LANE_ID]})
    monkeypatch.setattr(job_executor_service, "LANE_SPECS", only_drought)
    return held


async def _plan(release: ProcessStartRelease | None) -> tuple[list[object], list[object]]:
    results, due = await job_executor_service._plan_active_lanes(
        object(),  # type: ignore[arg-type]
        ACTIVE,
        NOW,
        breaker_release=release,
    )
    return list(results), list(due)


# --- breaker release on process start -------------------------------------------------------------


async def test_a_new_process_releases_a_breaker_held_run_once_by_recording_a_supersession(
    ledger: dict[str, object],
) -> None:
    spec = LANE_SPECS[DROUGHT_DIRECT_LANE_ID]
    failed_bucket = scheduled_bucket(spec, PROCESS_STARTED - timedelta(hours=2))
    ledger["latest"] = _held(failed_bucket)
    release = ProcessStartRelease(started_at=PROCESS_STARTED, deployment="deploy-abc")

    results, due = await _plan(release)

    assert results == []
    assert [candidate.scheduled_for for candidate in due] == [scheduled_bucket(spec, NOW)]  # type: ignore[attr-defined]
    assert due[0].supersession == "operator"  # type: ignore[attr-defined]
    assert due[0].superseded_run_id == HELD_RUN  # type: ignore[attr-defined]
    recorded = ledger["recorded"]
    assert isinstance(recorded, list)
    (lane_id, run_id, ledger_name, evidence, operator, now, apply) = recorded[0]
    assert (lane_id, run_id, ledger_name, operator, now, apply) == (
        DROUGHT_DIRECT_LANE_ID,
        HELD_RUN,
        "ledger.test/plantgeo",
        PROCESS_START_RELEASE_OPERATOR,
        NOW,
        True,
    )
    assert "deploy-abc" in evidence
    assert str(BREAKER_STREAK) in evidence
    assert ledger["committed"] == 1, "the recording is committed before the bucket is opened"
    assert release.released == {HELD_RUN}
    assert ledger["markers"] == {("deploy-abc", DROUGHT_DIRECT_LANE_ID)}

    # The same process never releases the same run twice: if it failed again it is held for an operator.
    results, due = await _plan(release)
    assert due == []
    assert [result.state for result in results] == ["failed"]  # type: ignore[attr-defined]
    assert results[0].operator_action is not None  # type: ignore[attr-defined]
    assert len(recorded) == 1


async def test_a_second_failure_in_the_same_process_is_held_even_though_its_bucket_is_older(
    ledger: dict[str, object],
) -> None:
    """The granted bucket fails: its run settled under this process and this deployment, so nothing releases it."""
    spec = LANE_SPECS[DROUGHT_DIRECT_LANE_ID]
    ledger["latest"] = _held(scheduled_bucket(spec, PROCESS_STARTED - timedelta(hours=2)))
    release = ProcessStartRelease(started_at=PROCESS_STARTED, deployment="deploy-abc")
    await _plan(release)
    # The granted bucket (the process's own start bucket) failed and is now the checkpoint.
    second_run = uuid.uuid4()
    ledger["latest"] = _held(scheduled_bucket(spec, PROCESS_STARTED), run_id=second_run)

    results, due = await _plan(release)

    assert due == []
    assert [result.state for result in results] == ["failed"]  # type: ignore[attr-defined]
    assert len(ledger["recorded"]) == 1  # type: ignore[arg-type]
    assert second_run not in release.released, "never even attempted: the bucket is not before the start bucket"


async def test_a_restart_under_the_same_deployment_does_not_release_again(ledger: dict[str, object]) -> None:
    """A container kill after a child OOM is a lane fault; the durable marker keeps it from earning a bucket."""
    spec = LANE_SPECS[DROUGHT_DIRECT_LANE_ID]
    ledger["latest"] = _held(scheduled_bucket(spec, PROCESS_STARTED - timedelta(hours=2)))
    await _plan(ProcessStartRelease(started_at=PROCESS_STARTED, deployment="deploy-abc"))
    # The granted bucket (the first process's own start bucket) failed and settled under this deployment.
    later_run = uuid.uuid4()
    ledger["latest"] = _held(scheduled_bucket(spec, PROCESS_STARTED), run_id=later_run)
    restarted = ProcessStartRelease(started_at=PROCESS_STARTED + timedelta(hours=3), deployment="deploy-abc")

    results, due = await _plan(restarted)

    assert due == []
    assert [result.state for result in results] == ["failed"]  # type: ignore[attr-defined]
    assert len(ledger["recorded"]) == 1, "the marker refused before any supersession was written"  # type: ignore[arg-type]
    assert restarted.released == {later_run}, "remembered as spent, so the next tick does not probe again"

    # A NEW deployment is a human having looked: it earns one bucket again.
    redeployed = ProcessStartRelease(started_at=PROCESS_STARTED + timedelta(hours=4), deployment="deploy-def")
    results, due = await _plan(redeployed)
    assert len(due) == 1
    assert len(ledger["recorded"]) == 2  # noqa: PLR2004 - one per deployment  # type: ignore[arg-type]


async def test_a_ledger_fault_during_release_rolls_back_and_holds_without_aborting_the_tick(
    ledger: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def explode(*_args: object, **_kwargs: object) -> object:
        raise OperationalError("insert", {}, Exception("connection reset"))

    monkeypatch.setattr(job_run_supersession, "supersede_failed_run", explode)
    spec = LANE_SPECS[DROUGHT_DIRECT_LANE_ID]
    ledger["latest"] = _held(scheduled_bucket(spec, PROCESS_STARTED - timedelta(hours=2)))
    release = ProcessStartRelease(started_at=PROCESS_STARTED, deployment="deploy-abc")

    results, due = await _plan(release)

    assert due == []
    assert [result.state for result in results] == ["failed"]  # type: ignore[attr-defined]
    assert ledger["committed"] == 0
    assert release.released == set(), "a transient fault is retried on a later tick, not remembered as done"


async def test_with_no_release_policy_the_hold_is_exactly_what_it_was(ledger: dict[str, object]) -> None:
    spec = LANE_SPECS[DROUGHT_DIRECT_LANE_ID]
    ledger["latest"] = _held(scheduled_bucket(spec, PROCESS_STARTED - timedelta(hours=2)))

    results, due = await _plan(None)

    assert due == []
    assert [result.state for result in results] == ["failed"]  # type: ignore[attr-defined]
    assert ledger["recorded"] == []


async def test_a_refused_recording_leaves_the_lane_held(
    ledger: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(*_args: object, **_kwargs: object) -> object:
        raise job_run_supersession.SupersessionRefusal("paused in the ledger")

    monkeypatch.setattr(job_run_supersession, "supersede_failed_run", refuse)
    spec = LANE_SPECS[DROUGHT_DIRECT_LANE_ID]
    ledger["latest"] = _held(scheduled_bucket(spec, PROCESS_STARTED - timedelta(hours=2)))

    results, due = await _plan(ProcessStartRelease(started_at=PROCESS_STARTED, deployment="d"))

    assert due == []
    assert [result.state for result in results] == ["failed"]  # type: ignore[attr-defined]
    assert ledger["committed"] == 0


async def test_a_refusal_is_remembered_so_the_ledger_is_not_probed_every_tick(
    ledger: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    probes = 0

    async def refuse(*_args: object, **_kwargs: object) -> object:
        nonlocal probes
        probes += 1
        raise job_run_supersession.SupersessionRefusal("paused in the ledger")

    monkeypatch.setattr(job_run_supersession, "supersede_failed_run", refuse)
    spec = LANE_SPECS[DROUGHT_DIRECT_LANE_ID]
    ledger["latest"] = _held(scheduled_bucket(spec, PROCESS_STARTED - timedelta(hours=2)))
    release = ProcessStartRelease(started_at=PROCESS_STARTED, deployment="d")

    await _plan(release)
    await _plan(release)

    assert probes == 1
    assert release.released == {HELD_RUN}


def test_the_release_policy_is_opt_in_and_reads_its_deployment_from_the_environment() -> None:
    assert ProcessStartRelease.from_environment(now=NOW, environment={}) is None, "OFF by default (owner 2026-09-18)"
    off = {"PLANTGEO_JOB_EXECUTOR_PROCESS_START_RELEASES_BREAKER": "0"}
    assert ProcessStartRelease.from_environment(now=NOW, environment=off) is None
    on = {"PLANTGEO_JOB_EXECUTOR_PROCESS_START_RELEASES_BREAKER": "1"}
    assert ProcessStartRelease.from_environment(now=NOW, environment=on) == ProcessStartRelease(
        started_at=NOW, deployment="local"
    )
    built = ProcessStartRelease.from_environment(now=NOW, environment={**on, "RAILWAY_DEPLOYMENT_ID": "dep-1"})
    assert built is not None
    assert built.deployment == "dep-1"


# --- repair authoring on the leader's clock --------------------------------------------------------


async def _author(session: object, clock: RepairAuthoringClock) -> dict[str, object] | None:
    return await job_executor_service._author_due_repairs(
        session,  # type: ignore[arg-type]
        ACTIVE,
        now=NOW,
        clock=clock,
    )


def test_the_rotation_sits_an_authored_layer_out_for_the_window_and_the_pass_records_what_it_authored() -> None:
    clock = RepairAuthoringClock(interval_seconds=3600, rotation_seconds=100.0)
    clock.remember(["a", "b"], 1000.0)
    assert clock.excluded(1050.0) == {"a", "b"}
    assert clock.excluded(1100.0) == frozenset(), "outside the window the layer is eligible again"


async def test_an_authored_layer_is_excluded_from_the_next_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_exclusions: list[object] = []
    plan = type("Plan", (), {"authorized": (), "candidates": ()})()
    receipt = type("Receipt", (), {"layer": "vegetation", "to_dict": lambda _self: {}})()

    def plan_repairs(*_args: object, recently_authored: object, **_kwargs: object) -> object:
        seen_exclusions.append(recently_authored)
        return plan

    async def author(*_args: object, **_kwargs: object) -> tuple:
        return (receipt,)

    async def commit(_session: object) -> None:
        return None

    def read_coverage(*, now: datetime) -> object:
        del now
        return object()

    monkeypatch.setattr(gap_repair, "read_parquet_coverage", read_coverage)
    monkeypatch.setattr(gap_repair, "plan_gap_repairs", plan_repairs)
    monkeypatch.setattr(gap_repair, "author_gap_repairs", author)
    monkeypatch.setattr(job_executor_service, "_commit_planning_transaction", commit)
    clock = RepairAuthoringClock(interval_seconds=0.0)

    await _author(object(), clock)
    await _author(object(), clock)

    assert seen_exclusions == [frozenset(), frozenset({"vegetation"})]


def test_the_repair_clock_is_due_once_per_interval_and_off_at_zero() -> None:
    clock = RepairAuthoringClock.from_environment({})
    assert clock == RepairAuthoringClock(interval_seconds=DEFAULT_REPAIR_INTERVAL_SECONDS)
    assert clock.due(1000.0)
    clock.mark(1000.0)
    assert not clock.due(1000.0 + DEFAULT_REPAIR_INTERVAL_SECONDS - 1)
    assert clock.due(1000.0 + DEFAULT_REPAIR_INTERVAL_SECONDS)
    assert RepairAuthoringClock.from_environment({"PLANTGEO_JOB_EXECUTOR_REPAIR_INTERVAL_SECONDS": "0"}) is None
    with pytest.raises(ExecutorConfigurationError):
        RepairAuthoringClock.from_environment({"PLANTGEO_JOB_EXECUTOR_REPAIR_INTERVAL_SECONDS": "soon"})
    with pytest.raises(ExecutorConfigurationError):
        RepairAuthoringClock.from_environment({"PLANTGEO_JOB_EXECUTOR_REPAIR_INTERVAL_SECONDS": "-1"})


async def test_a_due_clock_reads_coverage_once_plans_and_commits_an_applied_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []
    coverage = object()
    plan = type("Plan", (), {"authorized": (), "candidates": ()})()

    def read_coverage(*, now: datetime) -> object:
        calls.append(("read", now))
        return coverage

    def plan_repairs(
        seen: object, *, activation: ActivationConfig, now: datetime, budget: object, recently_authored: object
    ) -> object:
        calls.append(("plan", (seen, activation, now, type(budget).__name__, recently_authored)))
        return plan

    async def author(session: object, *, plan: object, now: datetime, apply: bool) -> tuple:
        calls.append(("author", (session, plan, now, apply)))
        return ()

    async def commit(_session: object) -> None:
        calls.append(("commit", None))

    monkeypatch.setattr(gap_repair, "read_parquet_coverage", read_coverage)
    monkeypatch.setattr(gap_repair, "plan_gap_repairs", plan_repairs)
    monkeypatch.setattr(gap_repair, "author_gap_repairs", author)
    monkeypatch.setattr(job_executor_service, "_commit_planning_transaction", commit)
    clock = RepairAuthoringClock(interval_seconds=3600)
    session = object()

    summary = await _author(session, clock)

    assert summary == {"authorized": [], "verdicts": {}, "receipts": []}
    assert [name for name, _ in calls] == ["read", "plan", "author", "commit"]
    assert calls[1][1] == (coverage, ACTIVE, NOW, "RepairBudget", frozenset())
    assert calls[2][1] == (session, plan, NOW, True), "an applied pass, with nobody at the keyboard"
    assert clock.last_authored is not None


async def test_a_failed_authoring_pass_rolls_back_and_still_advances_the_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rolled_back: list[object] = []

    def explode(*, now: datetime) -> object:
        del now
        raise OSError("bucket unreachable")

    async def rollback(session: object) -> None:
        rolled_back.append(session)

    monkeypatch.setattr(gap_repair, "read_parquet_coverage", explode)
    monkeypatch.setattr(job_executor_service, "_rollback_planning_transaction", rollback)
    clock = RepairAuthoringClock(interval_seconds=3600)

    summary = await _author(object(), clock)

    assert summary is None
    assert rolled_back == [rolled_back[0]]
    assert clock.last_authored is not None, "a broken store is not probed again every thirty seconds"
