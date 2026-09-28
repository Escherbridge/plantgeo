"""The G1 hold ladder end to end (Wave O GL-6 folded into f1-executor; spec §4.9.3; FR-8, FR-36).

Every flow runs the real `run_executor_tick`, the real planner, the real `_open_scheduled_run` and
`jobs/worker.py::open_job_run`, the real `supersede_failed_run` and the real handler spawning a REAL child
process. Only the ledger is faked, at its SQL edge: `ProbeWorld` extends `soft_failure_fakes.FakeWorld` with
the statements the ladder adds (a run and its work item row, a supersession marker with its owner) and a
checkpoint whose failure streak reads markers the way `select_latest_run.sql` does (executor F4).

The ladder under test is the production default (`HoldLadders()`: upstream 1, 2, 4, 8, 16, 24 h; code
6, 12, 24 h). Every lane is hourly at phase 0, so `NOW` (06:05) sits in bucket 06:00 and `seed_hold` leaves
buckets 03:00-05:00 failed.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final

import pytest
import structlog.testing

from agri_data_service.execution import job_executor_service, job_run_supersession
from agri_data_service.execution.job_executor_service import (
    DEPLOYMENT_ID_VARIABLE,
    EVENT_HOLD_PROBE_FAILED,
    PROBE_OPERATOR,
    PROCESS_START_RELEASE_VARIABLE,
    ExecutorTickSummary,
    ProcessStartRelease,
    SoftFailureState,
    run_executor_tick,
)
from agri_data_service.execution.job_run_supersession import PROCESS_START_RELEASE_FINGERPRINT_PREFIX
from agri_data_service.execution.lane_ids import SOIL_DIRECT_LANE_ID
from agri_data_service.execution.lane_incidents import HoldLadders
from agri_data_service.foundation.observability import events
from agri_data_service.jobs import worker
from tests.execution.soft_failure_fakes import (
    COMPLETE_REPORT_SCRIPT,
    HOUR,
    NOW,
    FakeRun,
    FakeWorld,
    build_lane_spec,
    events_named,
    exit_script,
    report_script,
    states,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.execution.lane_specs import LaneExecutionSpec
    from agri_data_service.jobs import JobDefinitionRecord, JobInvocation
    from agri_data_service.jobs.worker import JobSliceSummary

FLAKY: Final = "flaky-upstream-lane"
#: What every ordinary bucket's work item opens with; a probe and a probation bucket open with 1.
DEFINITION_ATTEMPTS: Final = 5
#: Passes ONLY when the executor told the child it is a probe (`PLANTGEO_TURN_PROBE=1`); otherwise exits 75.
PROBE_ONLY_PASSES: Final = (
    "import json, os, sys\n"
    "if os.environ.get('PLANTGEO_TURN_PROBE') != '1':\n"
    "    sys.exit(75)\n"
    'print(json.dumps({"outcome": "complete", "days_unwritten": 0}))\n'
)
#: A child that outlives its fence: its lease is lost at the first (fast) heartbeat.
SLEEPER: Final = "import time\ntime.sleep(30)\n"
#: A soil turn that completes but proves nothing: its edge probe did not answer `ok` (`PROGRESS_EVIDENCE`).
UNPROVEN_SOIL_REPORT: Final = report_script(
    {"status": "completed", "days_unwritten": 0, "probe": {"status": "unavailable"}, "results": []}
)
_REAL_OPEN_SCHEDULED_RUN: Final = job_executor_service._open_scheduled_run


class ProbeWorld(FakeWorld):
    """`FakeWorld` plus the ledger rows the ladder touches, answered with their SQL semantics."""

    def __init__(self, specs: Mapping[str, LaneExecutionSpec], *, active: frozenset[str] | None = None) -> None:
        super().__init__(specs, active=active)
        #: Run id -> the `owner` of its supersession marker (a person, or `executor:*`).
        self.markers: dict[uuid.UUID, str] = {}
        #: Run id -> its work item's `max_attempts`, as `insert_job_work_items.sql` stored it.
        self.item_attempts: dict[uuid.UUID, int] = {}
        #: The real worker abandons a fenced-out attempt (`lost`) and the reaper then dead-letters a
        #: single-attempt item, so the probe run settles failed with a lost final attempt.
        self.reap_fenced_out_attempts = False
        self._opening: dict[str, tuple[uuid.UUID, datetime]] = {}
        self._process_start_markers: set[str] = set()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> ProbeWorld:
        super().install(monkeypatch)
        monkeypatch.setattr(job_executor_service, "_open_scheduled_run", _REAL_OPEN_SCHEDULED_RUN)
        monkeypatch.setattr(job_run_supersession, "read_lane_checkpoint", self._checkpoint)
        return self

    def supersede(self, run: FakeRun) -> None:
        """A PERSON records `jobs-supersede-run` for `run`."""
        run.superseded_by_operator = True
        self.markers[run.run_id] = "operator-on-call"

    def probe_runs(self, lane_id: str) -> list[FakeRun]:
        return [run for run in self.runs_of(lane_id) if self.item_attempts.get(run.run_id) == 1]

    async def _load_definition(self, _session: object, spec: LaneExecutionSpec) -> JobDefinitionRecord | None:
        definition = await super()._load_definition(_session, spec)
        return None if definition is None else replace(definition, max_attempts=DEFINITION_ATTEMPTS)

    async def _checkpoint(self, _session: object, spec: LaneExecutionSpec) -> job_executor_service.LatestRun | None:
        latest = await super()._checkpoint(_session, spec)
        if latest is None:
            return None
        streak = 0
        for run in reversed(self.runs_of(spec.lane_id)):
            released_by_person = run.superseded_by_operator and not self.markers.get(run.run_id, "").startswith(
                "executor:"
            )
            if run.status not in {"failed", "partial"} or released_by_person:
                break
            streak += 1
        return replace(latest, consecutive_failures=min(streak, 3))

    def _invocation(self, run: FakeRun, *, cursor: Mapping[str, object] | None, budget: float) -> JobInvocation:
        invocation = super()._invocation(run, cursor=cursor, budget=budget)
        return replace(invocation, max_attempts=self.item_attempts.get(run.run_id, DEFINITION_ATTEMPTS))

    async def _run_slice(self, _session: object, **keywords: object) -> JobSliceSummary:
        summary = await super()._run_slice(_session, **keywords)  # type: ignore[arg-type]
        run = next(run for run in self.runs if run.run_id == keywords["job_run_id"])
        if self.reap_fenced_out_attempts and self.outcomes[run.lane_id][-1].metrics.get("exit_class") == "lease_lost":
            run.status = "failed"
            run.final_attempt = {**(run.final_attempt or {}), "status": "lost"}
        return summary

    def answer(self, statement: object, params: dict[str, object]) -> list[dict[str, object]]:  # noqa: PLR0911
        if statement is worker._INSERT_JOB_RUN:
            run_id = uuid.uuid4()
            scheduled_for = params["scheduled_for"]
            assert isinstance(scheduled_for, datetime)
            self._opening[str(params["logical_run_key"])] = (run_id, scheduled_for)
            return [{"id": run_id}]
        if statement is worker._INSERT_JOB_WORK_ITEMS:
            run_id = params["job_run_id"]
            assert isinstance(run_id, uuid.UUID)
            (item,) = json.loads(str(params["items"]))
            payload = json.loads(item["payload"])
            scheduled_for = next(when for opened, when in self._opening.values() if opened == run_id)
            self.runs.append(
                FakeRun(
                    run_id=run_id,
                    lane_id=str(payload["lane_id"]),
                    scheduled_for=scheduled_for,
                    status="queued",
                    kind=str(item["kind"]),
                    payload=payload,
                )
            )
            self.item_attempts[run_id] = int(str(params["max_attempts"]))
            return [{"id": uuid.uuid4()}]
        if statement is worker._REFRESH_JOB_RUN_ROLLUP:
            return [{"status": "queued", "total_work_items": 1, "succeeded_work_items": 0, "failed_work_items": 0}]
        if statement is job_run_supersession._INSERT_SUPERSESSION_INCIDENT:
            fingerprint = str(params["fingerprint"])
            if fingerprint.startswith(PROCESS_START_RELEASE_FINGERPRINT_PREFIX):
                if fingerprint in self._process_start_markers:
                    return []
                self._process_start_markers.add(fingerprint)
                return [{"id": uuid.uuid4()}]
            run = next(run for run in self.runs if run.run_id == params["job_run_id"])
            if run.superseded_by_operator:
                return []  # ON CONFLICT (fingerprint) DO NOTHING
            run.superseded_by_operator = True
            self.markers[run.run_id] = str(params["owner"])
            return [{"id": uuid.uuid4()}]
        return super().answer(statement, params)


def _world(monkeypatch: pytest.MonkeyPatch, script: str, *, lane_id: str = FLAKY) -> ProbeWorld:
    return ProbeWorld({lane_id: build_lane_spec(lane_id, script)}).install(monkeypatch)


def _laddered() -> SoftFailureState:
    """The production default: the split breaker's ladder on (`SoftFailureState.for_process` under `split`)."""
    return SoftFailureState(ladders=HoldLadders())


def _hold(world: ProbeWorld, lane_id: str = FLAKY) -> dict[str, object]:
    row = world.incidents.by_fingerprint(f"lane_hold:{lane_id}")
    assert row is not None, "the hold row is open"
    return row


def _detail(world: ProbeWorld, lane_id: str = FLAKY) -> dict[str, object]:
    detail = _hold(world, lane_id)["detail"]
    assert isinstance(detail, dict)
    return detail


async def _tick(world: ProbeWorld, state: SoftFailureState, at: datetime) -> ExecutorTickSummary:
    return await world.tick(now=at, soft=state)


async def test_probe_work_item_has_one_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(monkeypatch, PROBE_ONLY_PASSES)
    held_run = world.seed_hold(FLAKY, exit_class="upstream")
    state = _laddered()

    opened = await _tick(world, state, NOW)
    assert states(opened)[FLAKY] == "failed", "the tick that opens a hold never probes it"
    assert world.probe_runs(FLAKY) == []

    probed = await _tick(world, state, NOW + HOUR)  # rung 0 of the upstream ladder: one hour

    (probe,) = world.probe_runs(FLAKY)
    assert states(probed)[FLAKY] == "ran"
    assert world.item_attempts[probe.run_id] == 1, "the probe's work item row is single-attempt"
    outcome = world.last_outcome(FLAKY)
    assert outcome.kind == "completed", "the child exits 75 unless it was told PLANTGEO_TURN_PROBE=1"
    assert outcome.metrics["probe"] is True
    assert world.markers[held_run.run_id] == PROBE_OPERATOR, "the held run was superseded by the probe"
    detail = _detail(world)
    assert detail["state"] == "probing"
    assert [probe_record["superseded_run_id"] for probe_record in detail["probes"]] == [str(held_run.run_id)]  # type: ignore[union-attr]

    # The probe passed, so the next bucket is a probation bucket: single-attempt too.
    await _tick(world, state, NOW + 2 * HOUR)
    assert _detail(world)["state"] == "probation"
    assert world.item_attempts[world.runs_of(FLAKY)[-1].run_id] == 1


async def test_failed_probe_advances_the_rung_on_one_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(monkeypatch, exit_script(75))
    world.seed_hold(FLAKY, exit_class="upstream")
    state = _laddered()
    await _tick(world, state, NOW)
    await _tick(world, state, NOW + HOUR)  # probe 1 fails (exit 75)

    with structlog.testing.capture_logs() as logs:
        judged = await _tick(world, state, NOW + 2 * HOUR)

    assert states(judged)[FLAKY] == "failed", "a failed probe leaves the lane held"
    detail = _detail(world)
    assert (detail["state"], detail["rung"], detail["exit_class"]) == ("held", 1, "upstream")
    assert world.incidents.resolved(f"lane_hold:{FLAKY}") == [], "one fingerprint, one episode"
    assert [entry["rung"] for entry in events_named(logs, EVENT_HOLD_PROBE_FAILED)] == [1]

    # Rung 1 waits two hours from the failure, not one.
    await _tick(world, state, NOW + 3 * HOUR)
    assert len(world.probe_runs(FLAKY)) == 1
    await _tick(world, state, NOW + 4 * HOUR)
    assert len(world.probe_runs(FLAKY)) == 2  # noqa: PLR2004 - the second probe, on rung 1


async def test_lost_probe_does_not_advance_the_rung_or_flip_the_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job_executor_service, "COMMAND_HEARTBEAT_SECONDS", 0.05)
    world = _world(monkeypatch, SLEEPER)
    held_run = world.seed_hold(FLAKY, exit_class="upstream")
    world.lease_lost_lanes.add(FLAKY)
    world.reap_fenced_out_attempts = True
    state = _laddered()
    await _tick(world, state, NOW)
    await _tick(world, state, NOW + HOUR)  # the probe loses its fence; the reaper closes its attempt `lost`

    with structlog.testing.capture_logs() as logs:
        await _tick(world, state, NOW + 2 * HOUR)

    detail = _detail(world)
    assert (detail["state"], detail["rung"]) == ("held:1", 0), "same rung, one inconclusive probe counted"
    assert detail["exit_class"] == "upstream", "a lost attempt reads as `code`, yet the hold keeps its class"
    assert _hold(world)["job_run_id"] == held_run.run_id
    assert len(events_named(logs, job_executor_service.EVENT_HOLD_PROBE_INCONCLUSIVE)) == 1

    # Still on the upstream ladder: the next probe comes an hour later, not six.
    await _tick(world, state, NOW + 3 * HOUR)
    assert len(world.probe_runs(FLAKY)) == 2  # noqa: PLR2004 - the re-probe at rung 0


async def test_fence_lost_on_a_probe_parks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job_executor_service, "COMMAND_HEARTBEAT_SECONDS", 0.05)
    world = _world(monkeypatch, SLEEPER)
    world.seed_hold(FLAKY, exit_class="upstream")
    world.lease_lost_lanes.add(FLAKY)
    state = _laddered()
    await _tick(world, state, NOW)

    await _tick(world, state, NOW + HOUR)

    outcome = world.last_outcome(FLAKY)
    assert (outcome.kind, outcome.metrics["exit_class"]) == ("yielded", "lease_lost"), "parked, never failed"
    (probe,) = world.probe_runs(FLAKY)
    assert probe.status == "queued", "the parked probe's run is still open"
    await _tick(world, state, NOW + 2 * HOUR)
    detail = _detail(world)
    assert (detail["state"], detail["rung"]) == ("probing", 0), "a parked probe is still in flight: no rung moved"
    assert len(world.probe_runs(FLAKY)) == 1, "the parked probe is re-driven, never duplicated"


async def test_three_inconclusive_probes_count_as_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job_executor_service, "COMMAND_HEARTBEAT_SECONDS", 0.05)
    world = _world(monkeypatch, SLEEPER)
    world.seed_hold(FLAKY, exit_class="upstream")
    world.lease_lost_lanes.add(FLAKY)
    world.reap_fenced_out_attempts = True
    state = _laddered()
    await _tick(world, state, NOW)
    positions: list[tuple[object, object]] = []

    with structlog.testing.capture_logs() as logs:
        for probe_number in range(4):
            await _tick(world, state, NOW + (2 * probe_number + 1) * HOUR)  # a probe, lost
            await _tick(world, state, NOW + (2 * probe_number + 2) * HOUR)  # judged inconclusive
            detail = _detail(world)
            positions.append((detail["state"], detail["rung"]))

    assert positions == [("held:1", 0), ("held:2", 0), ("held:3", 0), ("held", 1)]
    (failed,) = events_named(logs, EVENT_HOLD_PROBE_FAILED)
    assert failed["reason"] == "lost_repeatedly"
    assert _detail(world)["exit_class"] == "code", "a lane that keeps losing its executor is itself the fault"


async def test_daily_lane_never_probes_more_often_than_its_cadence(monkeypatch: pytest.MonkeyPatch) -> None:
    daily = "daily-upstream-lane"
    spec = replace(build_lane_spec(daily, exit_script(75)), cadence_seconds=86_400, schedule="0 0 * * *")
    world = ProbeWorld({daily: spec}).install(monkeypatch)
    today = NOW.replace(hour=0, minute=0)
    for days_back in (3, 2, 1):
        world.seed_run(daily, today - timedelta(days=days_back), "failed", exit_class="upstream")
    state = _laddered()
    await _tick(world, state, NOW)  # the hold opens; today's bucket is newer than yesterday's failure

    probes_by_tick = []
    for hours in range(1, 19):  # 07:05 today .. 00:05 tomorrow, one tick an hour
        await _tick(world, state, NOW + hours * HOUR)
        probes_by_tick.append(len(world.probe_runs(daily)))

    assert probes_by_tick[0] == 1, "rung 0: the first probe an hour after the hold opened"
    assert probes_by_tick[-2] == 1, "the 1 h and 2 h rungs came and went with no newer daily bucket to run"
    assert probes_by_tick[-1] == 2, "tomorrow's bucket is the next probe"  # noqa: PLR2004


async def test_inconclusive_probation_expiry_opens_lane_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(monkeypatch, UNPROVEN_SOIL_REPORT, lane_id=SOIL_DIRECT_LANE_ID)
    world.seed_hold(SOIL_DIRECT_LANE_ID, exit_class="upstream")
    state = _laddered()
    await _tick(world, state, NOW)
    await _tick(world, state, NOW + HOUR)  # the probe exits 0
    await _tick(world, state, NOW + 2 * HOUR)  # probation starts; its first bucket proves nothing
    assert _detail(world, SOIL_DIRECT_LANE_ID)["state"] == "probation"

    await _tick(world, state, NOW + 50 * HOUR)

    assert world.incidents.by_fingerprint(f"lane_hold:{SOIL_DIRECT_LANE_ID}") is None
    (resolved,) = world.incidents.resolved(f"lane_hold:{SOIL_DIRECT_LANE_ID}")
    assert resolved["detail"]["released_by"] == "probe"  # type: ignore[index]
    assert resolved["detail"]["resolution_reason"] == "probation_expired"  # type: ignore[index]
    incomplete = world.incidents.by_fingerprint(f"lane_incomplete:{SOIL_DIRECT_LANE_ID}")
    assert incomplete is not None, "still open after that tick's own unproven turn completed"
    assert incomplete["detail"]["reason"] == "inconclusive"  # type: ignore[index]


@pytest.mark.parametrize(("phase", "chronic"), [("probation", False), ("held", True)])
async def test_probation_is_excluded_from_chronic(monkeypatch: pytest.MonkeyPatch, phase: str, chronic: bool) -> None:
    world = _world(monkeypatch, COMPLETE_REPORT_SCRIPT)
    if phase == "held":
        held_run = world.seed_hold(FLAKY, exit_class="upstream")
    else:
        held_run = world.seed_run(FLAKY, NOW.replace(minute=0) - HOUR, "succeeded", exit_class="ok")
    chain_start = NOW - 80 * HOUR
    world.incidents.seed(
        fingerprint=f"lane_hold:{FLAKY}",
        incident_type="lane_hold",
        first_seen_at=chain_start,
        last_seen_at=NOW - HOUR,
        job_run_id=held_run.run_id,
        detail={"state": phase, "rung": 2, "chain_first_seen_at": chain_start.isoformat(), "episodes_7d": 1},
    )
    state = _laddered()

    with structlog.testing.capture_logs() as logs:
        await _tick(world, state, NOW)

    assert len(events_named(logs, events.EVENT_HOLD_CHRONIC)) == int(chronic)
    assert state.chronic_lanes == ((FLAKY,) if chronic else ())


async def test_watch_window_opens_two_attempt_buckets(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(monkeypatch, COMPLETE_REPORT_SCRIPT)
    held_run = world.seed_hold(FLAKY, exit_class="code")
    state = _laddered()
    await _tick(world, state, NOW)
    world.supersede(held_run)
    await _tick(world, state, NOW + timedelta(minutes=1))  # released; the hold resolves as `operator`
    (resolved,) = world.incidents.resolved(f"lane_hold:{FLAKY}")
    assert resolved["detail"]["released_by"] == "operator"  # type: ignore[index]

    await _tick(world, state, NOW + HOUR)
    watched = world.runs_of(FLAKY)[-1]
    await _tick(world, state, NOW + 25 * HOUR)
    after_the_watch = world.runs_of(FLAKY)[-1]

    assert world.item_attempts[watched.run_id] == 2  # noqa: PLR2004 - the watch's two attempts
    assert world.item_attempts[after_the_watch.run_id] == DEFINITION_ATTEMPTS


@pytest.mark.parametrize(("resolved_days_ago", "inherits"), [(1, True), (8, False)])
async def test_reopen_within_7_days_inherits_rung_and_chain(
    monkeypatch: pytest.MonkeyPatch, resolved_days_ago: int, inherits: bool
) -> None:
    world = _world(monkeypatch, exit_script(75))
    resolved_at = NOW - timedelta(days=resolved_days_ago)
    chain_start = resolved_at - timedelta(days=1)
    earlier = world.incidents.seed(
        fingerprint=f"lane_hold:{FLAKY}:resolved:earlier",
        incident_type="lane_hold",
        status="resolved",
        first_seen_at=chain_start,
        resolved_at=resolved_at,
        detail={"state": "probation:1", "rung": 3, "chain_first_seen_at": chain_start.isoformat(), "episodes_7d": 1},
    )
    assert earlier["status"] == "resolved"
    world.seed_hold(FLAKY, exit_class="upstream")

    await _tick(world, _laddered(), NOW)

    detail = _detail(world)
    if inherits:
        assert (detail["rung"], detail["chain_first_seen_at"], detail["episodes_7d"]) == (3, chain_start.isoformat(), 2)
    else:
        assert (detail["rung"], detail["chain_first_seen_at"], detail["episodes_7d"]) == (0, NOW.isoformat(), 1)


async def test_three_episodes_in_7_days_log_hold_flapping_once(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(monkeypatch, exit_script(75))
    for days_ago, episodes in ((2, 1), (1, 2)):
        world.incidents.seed(
            fingerprint=f"lane_hold:{FLAKY}:resolved:{days_ago}",
            incident_type="lane_hold",
            status="resolved",
            first_seen_at=NOW - timedelta(days=days_ago, hours=5),
            resolved_at=NOW - timedelta(days=days_ago),
            detail={"state": "held", "rung": 0, "chain_first_seen_at": None, "episodes_7d": episodes},
        )
    third = world.seed_hold(FLAKY, exit_class="upstream")
    state = _laddered()

    with structlog.testing.capture_logs() as logs:
        await _tick(world, state, NOW)
        assert _hold(world)["severity"] == "error", "a flapping hold opens at error"
        world.supersede(third)
        await _tick(world, state, NOW + timedelta(minutes=1))  # released: the third episode ends
        world.seed_hold(FLAKY, now=NOW + 5 * HOUR, exit_class="upstream")
        await _tick(world, state, NOW + 5 * HOUR)  # a fourth episode opens

    assert _detail(world)["episodes_7d"] == 4  # noqa: PLR2004
    assert [entry["episodes_7d"] for entry in events_named(logs, events.EVENT_HOLD_FLAPPING)] == [3]


async def test_operator_supersession_is_told_apart_from_a_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(monkeypatch, exit_script(75))
    world.seed_hold(FLAKY, exit_class="upstream")
    state = _laddered()
    await _tick(world, state, NOW)
    await _tick(world, state, NOW + HOUR)  # the probe supersedes the held run, then fails
    await _tick(world, state, NOW + 2 * HOUR)

    assert world.incidents.resolved(f"lane_hold:{FLAKY}") == [], "the probe's own marker never resolves the hold"
    assert _detail(world)["rung"] == 1
    failed_probe = world.probe_runs(FLAKY)[-1]
    world.supersede(failed_probe)  # a person records `jobs-supersede-run`

    await _tick(world, state, NOW + 2 * HOUR + timedelta(minutes=30))

    (resolved,) = world.incidents.resolved(f"lane_hold:{FLAKY}")
    assert resolved["detail"]["released_by"] == "operator"  # type: ignore[index]


async def test_deploy_probe_is_off_by_default_and_folds_when_on(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ProcessStartRelease.from_environment(now=NOW, environment={}) is None, "no deploy probe (WQ-7)"
    world = _world(monkeypatch, PROBE_ONLY_PASSES)
    held_run = world.seed_hold(FLAKY, exit_class="code")
    state = _laddered()
    await _tick(world, state, NOW)
    deployed_at = NOW + timedelta(minutes=10)
    release = ProcessStartRelease.from_environment(
        now=deployed_at, environment={PROCESS_START_RELEASE_VARIABLE: "1", DEPLOYMENT_ID_VARIABLE: "deploy-b"}
    )
    assert release is not None

    summary = await run_executor_tick(
        world.session,  # type: ignore[arg-type]
        activation=world.activation,
        now=deployed_at,
        max_lanes_per_tick=6,
        breaker_release=release,
        soft_failure=state,
    )

    assert states(summary)[FLAKY] == "ran", "the deployment granted one bucket, well before the 6 h code rung"
    assert world.markers[held_run.run_id] == "executor:process-start"
    (probe,) = world.probe_runs(FLAKY)
    assert world.item_attempts[probe.run_id] == 1
    assert world.last_outcome(FLAKY).kind == "completed", "it ran as a probe: PLANTGEO_TURN_PROBE=1"
    detail = _detail(world)
    assert detail["state"] == "probing"
    assert [record["via"] for record in detail["probes"]] == ["deploy"]  # type: ignore[union-attr]
    await _tick(world, state, NOW + HOUR)
    assert _detail(world)["state"] == "probation", "then probation, like any clean probe"
