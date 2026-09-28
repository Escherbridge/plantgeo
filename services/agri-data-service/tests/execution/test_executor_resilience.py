"""A failure never stops the executor, and never reaches another lane (spec Sec 4.9.3 "Isolation").

Flows, each through real `run_executor_tick` calls (or the real `jobs-executor` command) with real
child processes, over the in-memory ledger in `soft_failure_fakes.py`:

- the incident READ fails: every lane still plans and dispatches exactly as HEAD did;
- one incident WRITE fails: only its savepoint rolls back, the rest of the tick's record survives, and
  that lane degrades to HEAD planning;
- the pinned connection dies: today's tick-level re-raise stands, after `tick_partial`;
- one lane's planning (forward or repair) raises: that lane is `plan_failed` and the others run;
- the process starts with every executor variable malformed, and with an unknown allow-list id;
- streaks (plan failures, missing reports, lost leases, blocked runs) survive a restart and escalate once;
- announcements fall back to the process when the row cannot be written;
- three lanes holding for the same reason inside an hour log ONE fleet error;
- `PLANTGEO_JOB_EXECUTOR_SOFT_FAILURE=off` plans exactly as HEAD.
"""

from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from typing import TYPE_CHECKING, Final

import pytest
import structlog.testing
from click.testing import CliRunner
from sqlalchemy.exc import OperationalError

from agri_data_service.execution import job_executor_service
from agri_data_service.execution.gap_repair_contract import REPAIR_LANE_SUFFIX
from agri_data_service.execution.job_executor_service import (
    ACTIVE_LANES_VARIABLE,
    CHARGE_BASIS_VARIABLE,
    DEFAULT_MAX_LANES_PER_TICK,
    DEFAULT_POLL_SECONDS,
    DEFAULT_REPAIR_INTERVAL_SECONDS,
    MAX_LANES_PER_TICK_VARIABLE,
    PLAN_FAILED_DETAIL_PREFIX,
    POLL_SECONDS_VARIABLE,
    PROCESS_START_RELEASE_VARIABLE,
    REPAIR_INTERVAL_VARIABLE,
    SOFT_FAILURE_VARIABLE,
    ActivationConfig,
    RepairAuthoringClock,
    SoftFailureState,
    announce_operator_actions,
    jobs_executor,
)
from agri_data_service.execution.lane_ids import (
    DROUGHT_DIRECT_LANE_ID,
    FIRE_DETECTIONS_DIRECT_LANE_ID,
    SENSORS_DIRECT_LANE_ID,
)
from agri_data_service.foundation.observability import events
from tests.execution.soft_failure_fakes import (
    COMPLETE_REPORT_SCRIPT,
    HOUR,
    NOW,
    FakeWorld,
    build_lane_spec,
    events_named,
    exit_script,
    injected_fault,
    states,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

HELD: Final = DROUGHT_DIRECT_LANE_ID
HELD_REPAIR: Final = f"{HELD}{REPAIR_LANE_SUFFIX}"
HEALTHY: Final = "healthy-probe-lane"
BROKEN: Final = "broken-planning-lane"
OPERATOR_ACTION_EVENT: Final = "plantgeo_job_executor_operator_action_required"
RETIRED_LANE: Final = "retired-lane"


def _held_lane_with_repair(
    monkeypatch: pytest.MonkeyPatch, *, healthy_script: str = COMPLETE_REPORT_SCRIPT
) -> FakeWorld:
    """A held lane with an already-authored repair run, beside a healthy lane: GL-5 would withhold the repair."""
    world = FakeWorld(
        {
            HELD: build_lane_spec(HELD, COMPLETE_REPORT_SCRIPT),
            HEALTHY: build_lane_spec(HEALTHY, healthy_script),
        }
    ).install(monkeypatch)
    world.seed_hold(HELD)
    world.open_repair_run(HELD)
    return world


# --- the incident ledger fails -------------------------------------------------------------------------


async def test_raising_incident_read_leaves_every_lane_dispatching_as_head(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _held_lane_with_repair(monkeypatch)
    world.incidents.fail_select = True
    state = SoftFailureState()

    with structlog.testing.capture_logs() as logs:
        first = await world.tick(soft=state)
        second = await world.tick(now=NOW + HOUR, soft=state)

    assert states(first)[HEALTHY] == states(second)[HEALTHY] == "ran"
    assert states(first)[HELD] == "failed", "the hold is HEAD's own: operator-only"
    assert states(first)[HELD_REPAIR] == "ran", "without its read the tick withholds nothing, exactly as HEAD"
    assert world.incidents.rows == {}
    assert len(events_named(logs, events.EVENT_INCIDENT_WRITE_FAILED)) == 1, "once per transition, not per tick"


async def test_incident_write_failure_rolls_back_to_the_savepoint_only(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _held_lane_with_repair(monkeypatch, healthy_script=exit_script(0))  # exits 0 without a report
    world.incidents.fail_upserts = lambda fingerprint: fingerprint == f"lane_hold:{HELD}"

    summary = await world.tick(soft=SoftFailureState())

    assert world.session.savepoints_rolled_back == 1
    assert world.incidents.by_fingerprint(f"lane_hold:{HELD}") is None
    assert world.incidents.by_fingerprint(f"lane_report_missing:{HEALTHY}") is not None, "the rest of the record stands"
    assert states(summary)[HELD] == "failed"
    assert states(summary)[HELD_REPAIR] == "ran", "the degraded lane repairs as HEAD did"
    degraded = world.incidents.by_fingerprint(f"lane_plan_failed:{HELD}")
    assert degraded is not None
    assert str(degraded["detail"]["reason"]).startswith("incident_write_failed")  # type: ignore[index]


async def test_dead_connection_keeps_the_tick_level_reraise(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _held_lane_with_repair(monkeypatch)
    world.incidents.fail_select = True
    world.fault_kills_connection = True

    with structlog.testing.capture_logs() as logs, pytest.raises(OperationalError):
        await world.tick(soft=SoftFailureState())

    assert events_named(logs, events.EVENT_TICK_PARTIAL), "what the tick settled is logged before the re-raise"
    assert world.outcomes == {}, "nothing runs on a connection that lost its backend"


# --- one lane's planning fails -------------------------------------------------------------------------


async def test_planning_fault_in_one_lane_leaves_the_other_running(monkeypatch: pytest.MonkeyPatch) -> None:
    world = FakeWorld(
        {
            BROKEN: build_lane_spec(BROKEN, COMPLETE_REPORT_SCRIPT),
            HEALTHY: build_lane_spec(HEALTHY, COMPLETE_REPORT_SCRIPT),
        }
    ).install(monkeypatch)
    world.checkpoint_faults[BROKEN] = KeyError("definition_version")
    state = SoftFailureState()

    summary = await world.tick(soft=state)

    broken = next(lane for lane in summary.lanes if lane.lane_id == BROKEN)
    assert broken.state == "failed"
    assert str(broken.detail).startswith(f"{PLAN_FAILED_DETAIL_PREFIX}KeyError")
    assert states(summary)[HEALTHY] == "ran"
    assert state.plan_failed_lanes == (BROKEN,)
    plan_failed = world.incidents.by_fingerprint(f"lane_plan_failed:{BROKEN}")
    assert plan_failed is not None
    assert plan_failed["severity"] == "warning"

    del world.checkpoint_faults[BROKEN]
    recovered = await world.tick(now=NOW + HOUR, soft=state)
    assert states(recovered)[BROKEN] == "ran"
    assert world.incidents.by_fingerprint(f"lane_plan_failed:{BROKEN}") is None, "a clean pass resolves it"


async def test_repair_planning_fault_is_isolated_per_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    other = SENSORS_DIRECT_LANE_ID
    world = FakeWorld(
        {HELD: build_lane_spec(HELD, COMPLETE_REPORT_SCRIPT), other: build_lane_spec(other, COMPLETE_REPORT_SCRIPT)}
    ).install(monkeypatch)
    for lane_id in (HELD, other):
        world.seed_settled(lane_id)
        world.open_repair_run(lane_id)
    world.repair_state_faults[HELD_REPAIR] = RuntimeError("repair definition row is malformed")

    summary = await world.tick(soft=SoftFailureState())

    assert states(summary)[HELD_REPAIR] == "failed"
    assert states(summary)[f"{other}{REPAIR_LANE_SUFFIX}"] == "ran"
    assert world.incidents.by_fingerprint(f"lane_plan_failed:{HELD_REPAIR}") is not None


async def test_leader_fault_logs_partial_tick_before_reraise(monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = "a-planned-lane", "b-ledger-fault-lane"
    world = FakeWorld(
        {first: build_lane_spec(first, COMPLETE_REPORT_SCRIPT), second: build_lane_spec(second, COMPLETE_REPORT_SCRIPT)}
    ).install(monkeypatch)
    # HEAD's own statement fails while opening the second lane's bucket: today's tick-level rule re-raises.
    world.open_run_faults[second] = injected_fault("open_job_run")

    with structlog.testing.capture_logs() as logs, pytest.raises(OperationalError):
        await world.tick(soft=SoftFailureState())

    (partial,) = events_named(logs, events.EVENT_TICK_PARTIAL)
    assert partial["lanes"] == [{"lane_id": first, "state": "ran"}], "the lane that ran is on the record"
    assert partial["error_type"] == "OperationalError"
    assert [outcome.kind for outcome in world.outcomes[first]] == ["completed"]


# --- the process starts whatever the environment says --------------------------------------------------

MALFORMED_ENVIRONMENT: Final[Mapping[str, str]] = {
    ACTIVE_LANES_VARIABLE: f" {RETIRED_LANE}, {FIRE_DETECTIONS_DIRECT_LANE_ID} ,,",
    POLL_SECONDS_VARIABLE: "thirty",
    MAX_LANES_PER_TICK_VARIABLE: "-4",
    REPAIR_INTERVAL_VARIABLE: "every six hours",
    SOFT_FAILURE_VARIABLE: "perhaps",
    PROCESS_START_RELEASE_VARIABLE: "maybe",
    CHARGE_BASIS_VARIABLE: "wire",
    "PLANTGEO_JOB_EXECUTOR_CODE_PROBE_HOURS": "6h,12h",
    "PLANTGEO_LOG_ROUTING": "sometimes",
    "PLANTGEO_UPSTREAM_TELEMETRY": "sure",
    "PLANTGEO_LOG_LEVEL": "loud",
}


def _start_executor(monkeypatch: pytest.MonkeyPatch, environment: Mapping[str, str]) -> dict[str, object]:
    """Invoke the real `jobs-executor --once`; the service loop is the only thing replaced."""
    started: dict[str, object] = {}

    async def service_loop(**kwargs: object) -> int:
        started.update(kwargs)
        return 0

    monkeypatch.setattr(job_executor_service, "_service_loop", service_loop)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    result = CliRunner().invoke(jobs_executor, ["--once"])
    assert result.exit_code == 0, result.output
    return started


def test_executor_starts_with_every_malformed_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    with structlog.testing.capture_logs() as logs:
        started = _start_executor(monkeypatch, MALFORMED_ENVIRONMENT)

    activation = started["activation"]
    soft = started["soft_failure"]
    repair_clock = started["repair_clock"]
    assert isinstance(activation, ActivationConfig)
    assert isinstance(soft, SoftFailureState)
    assert isinstance(repair_clock, RepairAuthoringClock)
    assert started["poll_seconds"] == DEFAULT_POLL_SECONDS
    assert started["max_lanes_per_tick"] == DEFAULT_MAX_LANES_PER_TICK
    assert repair_clock.interval_seconds == DEFAULT_REPAIR_INTERVAL_SECONDS
    assert activation.active_lanes == {FIRE_DETECTIONS_DIRECT_LANE_ID}
    assert activation.quarantined == {RETIRED_LANE}
    assert soft.enabled is False, "a garbled switch is HEAD planning, never a guess"
    fallbacks = {entry["variable"] for entry in events_named(logs, events.EVENT_CONFIG_FALLBACK)}
    assert {POLL_SECONDS_VARIABLE, MAX_LANES_PER_TICK_VARIABLE, REPAIR_INTERVAL_VARIABLE} <= fallbacks

    # The first leader tick records every fault on its own incident row and runs the valid lane.
    world = FakeWorld(
        {FIRE_DETECTIONS_DIRECT_LANE_ID: build_lane_spec(FIRE_DETECTIONS_DIRECT_LANE_ID, COMPLETE_REPORT_SCRIPT)},
        active=activation.active_lanes,
    ).install(monkeypatch)
    world.activation = activation
    summary = asyncio.run(world.tick(soft=soft))

    assert states(summary)[FIRE_DETECTIONS_DIRECT_LANE_ID] == "ran"
    for variable in (POLL_SECONDS_VARIABLE, MAX_LANES_PER_TICK_VARIABLE, REPAIR_INTERVAL_VARIABLE):
        assert world.incidents.by_fingerprint(f"executor_config:{variable}") is not None
    assert world.incidents.by_fingerprint(f"lane_quarantined:{RETIRED_LANE}") is not None


def test_executor_starts_with_an_unknown_lane_in_the_allow_list(monkeypatch: pytest.MonkeyPatch) -> None:
    with structlog.testing.capture_logs() as logs:
        started = _start_executor(
            monkeypatch, {ACTIVE_LANES_VARIABLE: f"{RETIRED_LANE},{FIRE_DETECTIONS_DIRECT_LANE_ID}"}
        )

    quarantined = events_named(logs, events.EVENT_LANE_QUARANTINED)
    assert [(entry["lane_id"], entry["reason"]) for entry in quarantined] == [(RETIRED_LANE, "unknown")]
    activation = started["activation"]
    soft = started["soft_failure"]
    assert isinstance(activation, ActivationConfig)
    assert isinstance(soft, SoftFailureState)

    world = FakeWorld(
        {FIRE_DETECTIONS_DIRECT_LANE_ID: build_lane_spec(FIRE_DETECTIONS_DIRECT_LANE_ID, COMPLETE_REPORT_SCRIPT)}
    ).install(monkeypatch)
    world.activation = activation
    asyncio.run(world.tick(soft=soft))
    assert world.incidents.by_fingerprint(f"lane_quarantined:{RETIRED_LANE}") is not None

    # The next deployment's allow-list is fixed: its first tick resolves the quarantine row.
    world.activation = ActivationConfig(frozenset({FIRE_DETECTIONS_DIRECT_LANE_ID}))
    asyncio.run(world.tick(now=NOW + HOUR, soft=SoftFailureState()))
    assert world.incidents.by_fingerprint(f"lane_quarantined:{RETIRED_LANE}") is None
    assert len(world.incidents.resolved(f"lane_quarantined:{RETIRED_LANE}")) == 1


# --- streaks live on rows, so they survive a restart ---------------------------------------------------


async def test_escalation_survives_an_executor_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    world = FakeWorld({BROKEN: build_lane_spec(BROKEN, COMPLETE_REPORT_SCRIPT)}).install(monkeypatch)
    world.checkpoint_faults[BROKEN] = ValueError("checkpoint row is malformed")
    first_process = SoftFailureState()

    with structlog.testing.capture_logs() as logs:
        await world.tick(soft=first_process)
        await world.tick(now=NOW + timedelta(minutes=1), soft=first_process)
        await world.tick(now=NOW + timedelta(minutes=2), soft=SoftFailureState())  # a restart
        await world.tick(now=NOW + timedelta(minutes=3), soft=SoftFailureState())  # and another

    row = world.incidents.by_fingerprint(f"lane_plan_failed:{BROKEN}")
    assert row is not None
    assert (row["occurrence_count"], row["severity"]) == (4, "error")
    escalations = [
        entry for entry in events_named(logs, events.EVENT_LANE_PLAN_FAILED) if entry["log_level"] == "error"
    ]
    assert [entry["consecutive_ticks"] for entry in escalations] == [3], "escalated once, across both restarts"


async def test_report_missing_streak_survives_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    silent = "silent-writer-lane"
    world = FakeWorld({silent: build_lane_spec(silent, exit_script(0))}).install(monkeypatch)
    first_process = SoftFailureState()

    with structlog.testing.capture_logs() as logs:
        await world.tick(soft=first_process)
        await world.tick(now=NOW + HOUR, soft=first_process)
        await world.tick(now=NOW + 2 * HOUR, soft=SoftFailureState())  # a restart before the third

    row = world.incidents.by_fingerprint(f"lane_report_missing:{silent}")
    assert row is not None
    assert (row["occurrence_count"], row["severity"]) == (3, "error")
    levels = [entry["log_level"] for entry in events_named(logs, events.EVENT_LANE_REPORT_MISSING)]
    assert levels == ["warning", "warning", "error"]

    world.set_script(silent, COMPLETE_REPORT_SCRIPT)
    await world.tick(now=NOW + 3 * HOUR, soft=SoftFailureState())
    assert world.incidents.by_fingerprint(f"lane_report_missing:{silent}") is None, "the next report resolves it"


async def test_blocked_open_run_is_announced_and_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    blocked = "blocked-run-lane"
    world = FakeWorld(
        {
            blocked: build_lane_spec(blocked, COMPLETE_REPORT_SCRIPT),
            HEALTHY: build_lane_spec(HEALTHY, COMPLETE_REPORT_SCRIPT),
        }
    ).install(monkeypatch)
    stuck = world.seed_run(blocked, NOW.replace(minute=0), "queued", has_work_items=False)
    state = SoftFailureState()

    with structlog.testing.capture_logs() as logs:
        first = await world.tick(soft=state)
        second = await world.tick(now=NOW + timedelta(minutes=5), soft=state)

    assert states(first)[blocked] == states(second)[blocked] == "failed"
    assert states(first)[HEALTHY] == "ran"
    assert [entry["lane_id"] for entry in events_named(logs, events.EVENT_LANE_BLOCKED)] == [blocked]
    row = world.incidents.by_fingerprint(f"lane_blocked:{blocked}")
    assert row is not None
    assert row["job_run_id"] == stuck.run_id

    stuck.status = "cancelled"  # an operator cancelled the run with no work items
    with structlog.testing.capture_logs() as logs:
        await world.tick(now=NOW + timedelta(minutes=10), soft=state)
    assert world.incidents.by_fingerprint(f"lane_blocked:{blocked}") is None
    assert [entry["lane_id"] for entry in events_named(logs, events.EVENT_LANE_UNBLOCKED)] == [blocked]


async def test_lease_lost_three_times_opens_one_incident(monkeypatch: pytest.MonkeyPatch) -> None:
    loser = "lease-losing-lane"
    monkeypatch.setattr(job_executor_service, "COMMAND_HEARTBEAT_SECONDS", 0.05)
    world = FakeWorld(
        {
            loser: build_lane_spec(loser, "import time\ntime.sleep(30)\n"),
            HEALTHY: build_lane_spec(HEALTHY, COMPLETE_REPORT_SCRIPT),
        }
    ).install(monkeypatch)
    world.lease_lost_lanes.add(loser)
    state = SoftFailureState()

    with structlog.testing.capture_logs() as logs:
        for hour in range(3):
            summary = await world.tick(now=NOW + hour * HOUR, soft=state)
            assert states(summary)[HEALTHY] == "ran"

    assert [outcome.metrics["exit_class"] for outcome in world.outcomes[loser]] == ["lease_lost"] * 3
    rows = [row for row in world.incidents.rows.values() if row["incident_type"] == "executor_lease_lost"]
    assert [(row["fingerprint"], row["occurrence_count"], row["severity"]) for row in rows] == [
        (f"executor_lease_lost:{loser}", 3, "error")
    ], "one incident, and no fleet row while another lane kept its lease"
    assert len(events_named(logs, events.EVENT_LEASE_LOST_ESCALATED)) == 1


# --- announcements and correlation ---------------------------------------------------------------------


async def test_announcements_fall_back_to_a_process_set_when_row_write_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _held_lane_with_repair(monkeypatch)
    world.incidents.fail_upserts = lambda fingerprint: fingerprint.startswith("lane_hold:")
    state = SoftFailureState()

    with structlog.testing.capture_logs() as logs:
        for minutes in (0, 5, 10):
            summary = await world.tick(now=NOW + timedelta(minutes=minutes), soft=state)
            announce_operator_actions(summary, state.announced, durable=state.durable_announcements)

    announced = events_named(logs, OPERATOR_ACTION_EVENT)
    assert [entry["lane_id"] for entry in announced] == [HELD], "once, from the process set, not every tick"
    write_failures = [
        entry
        for entry in events_named(logs, events.EVENT_INCIDENT_WRITE_FAILED)
        if entry["statement"] == f"lane_hold:{HELD}"
    ]
    assert len(write_failures) == 1


async def test_fleet_correlation_logs_one_error(monkeypatch: pytest.MonkeyPatch) -> None:
    members = ("fleet-member-a", "fleet-member-b", "fleet-member-c")
    world = FakeWorld(
        {
            **{lane_id: build_lane_spec(lane_id, COMPLETE_REPORT_SCRIPT) for lane_id in members},
            HEALTHY: build_lane_spec(HEALTHY, COMPLETE_REPORT_SCRIPT),
        }
    ).install(monkeypatch)
    held_runs = [world.seed_hold(lane_id, exit_class="code") for lane_id in members]
    state = SoftFailureState()

    with structlog.testing.capture_logs() as logs:
        first = await world.tick(soft=state)
        await world.tick(now=NOW + timedelta(minutes=10), soft=state)

    correlated = events_named(logs, events.EVENT_FLEET_CORRELATED)
    assert [(entry["exit_class"], entry["members"]) for entry in correlated] == [("code", list(members))]
    assert states(first)[HEALTHY] == "ran"
    fleet = world.incidents.by_fingerprint("fleet:code")
    assert fleet is not None
    assert fleet["severity"] == "error"

    for run in held_runs:
        world.supersede(run)
    await world.tick(now=NOW + timedelta(minutes=20), soft=state)
    assert world.incidents.by_fingerprint("fleet:code") is None, "every member resolved, so the fleet row did"


# --- the switch ----------------------------------------------------------------------------------------


async def test_soft_failure_off_restores_head_planning(monkeypatch: pytest.MonkeyPatch) -> None:
    async def plan_with(soft: SoftFailureState | None) -> tuple[set[tuple[str, str]], str, FakeWorld]:
        world = _held_lane_with_repair(monkeypatch)
        summary = await world.tick(soft=soft)
        # Every field of every lane result, with each world's own random run ids scrubbed.
        every_field = re.sub(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<uuid>", repr(summary.lanes)
        )
        return {(lane.lane_id, lane.state) for lane in summary.lanes}, every_field, world

    head, head_fields, _ = await plan_with(None)
    switched_off, off_fields, off_world = await plan_with(SoftFailureState(enabled=False))
    switched_on, _, _ = await plan_with(SoftFailureState(enabled=True))

    assert switched_off == head
    assert off_fields == head_fields, "switched off, every lane result is HEAD's field for field"
    assert (HELD_REPAIR, "ran") in head
    assert (HELD_REPAIR, "paused") in switched_on
    assert off_world.incidents.by_fingerprint(f"lane_hold:{HELD}") is not None, "incidents are still written"


@pytest.mark.parametrize(
    ("raw", "enabled"),
    [
        (None, True),
        ("", True),
        (" ON ", True),
        ("enabled", True),
        ("0", False),
        ("none", False),
        ("perhaps", False),
    ],
)
def test_soft_failure_switch_reads_garbled_as_off(raw: str | None, enabled: bool) -> None:
    environment = {} if raw is None else {SOFT_FAILURE_VARIABLE: raw}
    assert job_executor_service.soft_failure_enabled(environment) is enabled
