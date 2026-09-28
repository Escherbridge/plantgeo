"""GL-5's sweep proof (plan 0W.5): every fault class a lane can hit is classified, recorded on the right
incident, and CONTAINED -- a second lane completes unaffected on every tick. The G1 probe cases (plan 1D,
GL-6 folded) follow at the end: a probe is single-attempt, a lost one is inconclusive, a clean one starts
probation, and the healthy lane still runs every tick.

One flow, parametrised over the plan's fault list. A fault lane that already failed two buckets and a
healthy lane run through two real `run_executor_tick` calls, each turn a REAL child process through the
real `run_scheduled_command` (`soft_failure_fakes.py` fakes only the ledger rows and the worker loop):

1. tick one runs both lanes; the fault lane's turn is stamped with its `exit_class`;
2. tick two, an hour later: a failed third bucket is now operator-held, so its `lane_hold` opens with the
   class read back from that run's final attempt; an exit-0 fault records its own incident instead;
3. on both ticks the healthy lane ran and completed.

Report shapes are the lanes' own `main()` shapes (`test_exit_classes.py` cites the source lines); the
R3 drought case carries the meter's usage line on stdout, which is the only place a wrapped status
survives.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Final

import pytest
import structlog.testing

from agri_data_service.execution import job_executor_service
from agri_data_service.execution.job_executor_service import SoftFailureState
from agri_data_service.execution.lane_ids import (
    CLIMATE_DIRECT_LANE_ID,
    DROUGHT_DIRECT_LANE_ID,
    SENSORS_DIRECT_LANE_ID,
    WATER_GAUGES_DIRECT_LANE_ID,
)
from agri_data_service.execution.lane_incidents import HoldLadders
from agri_data_service.foundation.observability import events
from tests.execution.soft_failure_fakes import (
    COMPLETE_REPORT_SCRIPT,
    HOUR,
    KEY_ERROR_SCRIPT,
    NOW,
    FakeWorld,
    build_lane_spec,
    events_named,
    exit_script,
    report_script,
    states,
    usage_then_report_script,
)
from tests.execution.test_hold_probes import ProbeWorld

HEALTHY: Final = "healthy-probe-lane"
#: A lane with no evidence rules of its own: the explicit-exit and monitor cases run on it.
GENERIC: Final = SENSORS_DIRECT_LANE_ID
DROUGHT_WRAPPER_REPORT: Final = {
    "status": "failed",
    "error": "DirectDroughtError: usdm weekly census fetch failed after 3 attempts",
}
FLOOD_SCRIPT: Final = (
    "import json, sys\n"
    "write = sys.stderr.write\n"
    "for index in range(100_000):\n"
    "    write(f'progress line {index}\\n')\n"
    "sys.stderr.flush()\n"
    'print(json.dumps({"outcome": "complete", "days_unwritten": 0}))\n'
)
SELF_KILL_SCRIPT: Final = "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n"


@dataclass(frozen=True, slots=True)
class FaultCase:
    lane_id: str
    script: str
    exit_class: str
    #: The incident kind open for the fault lane after tick two; `None` when the fault records nothing.
    incident: str | None
    timeout_seconds: int = 60
    raising_read: bool = False
    incomplete_reason: str | None = None


FAULT_CASES: Final = [
    pytest.param(
        FaultCase(
            CLIMATE_DIRECT_LANE_ID,
            report_script(
                {
                    "status": "failed",
                    "error": (
                        "DirectClimateFieldError: nasa-power-precipitation 2026-09-27: NASA POWER answered 503 "
                        "for support-cell-1892 2026-09-27"
                    ),
                },
                exit_code=1,
            ),
            "upstream",
            "lane_hold",
        ),
        id="climate-wrapped-503",
    ),
    pytest.param(
        FaultCase(
            WATER_GAUGES_DIRECT_LANE_ID,
            report_script(
                {
                    "event": "water_gauges_forward_failed",
                    "error_type": "UpstreamHttpError",
                    "detail": "upstream request failed with status 503",
                },
                exit_code=1,
            ),
            "upstream",
            "lane_hold",
        ),
        id="water-gauges-pair",
    ),
    pytest.param(
        FaultCase(
            DROUGHT_DIRECT_LANE_ID,
            usage_then_report_script(last_send_outcome="5xx", report=DROUGHT_WRAPPER_REPORT, exit_code=1),
            "upstream",
            "lane_hold",
        ),
        id="drought-wrapper-after-failed-send",
    ),
    pytest.param(
        FaultCase(
            DROUGHT_DIRECT_LANE_ID,
            usage_then_report_script(last_send_outcome="2xx", report=DROUGHT_WRAPPER_REPORT, exit_code=1),
            "code",
            "lane_hold",
        ),
        id="wrapper-after-200-is-code",
    ),
    pytest.param(FaultCase(GENERIC, KEY_ERROR_SCRIPT, "code", "lane_hold"), id="key-error"),
    pytest.param(FaultCase(GENERIC, exit_script(75), "upstream", "lane_hold"), id="exit-75"),
    pytest.param(FaultCase(GENERIC, exit_script(70), "code", "lane_hold"), id="exit-70"),
    pytest.param(FaultCase(GENERIC, exit_script(78), "config", "lane_hold"), id="exit-78"),
    pytest.param(
        FaultCase(GENERIC, "import time\ntime.sleep(60)\n", "hang", "lane_hold", timeout_seconds=1), id="hang"
    ),
    pytest.param(FaultCase(GENERIC, exit_script(0), "report_missing", "lane_report_missing"), id="exit-0-no-report"),
    pytest.param(
        FaultCase(
            GENERIC,
            report_script({"outcome": "complete", "days_unwritten": 0, "availability_retry_owed": 2}),
            "ok",
            "lane_incomplete",
            incomplete_reason="publication_debt",
        ),
        id="exit-0-publication-debt-is-incomplete",
    ),
    pytest.param(
        FaultCase(
            GENERIC,
            report_script({"outcome": "complete", "days_unwritten": 0, "requests": 0, "rows_written": 0}),
            "ok",
            None,
        ),
        id="zero-fetches",
    ),
    # The default 60 s budget: the router decides admission before redacting, so the ~99,800 lines
    # its ceilings drop cost almost nothing (router.py::_admit; 100k lines ~1.6 s, was ~199 s).
    pytest.param(FaultCase(GENERIC, FLOOD_SCRIPT, "ok", None), id="100k-flood-lines"),
    pytest.param(
        FaultCase(GENERIC, SELF_KILL_SCRIPT, "code", "lane_hold"),
        id="self-sigkill",
        marks=pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL is POSIX-only"),
    ),
    pytest.param(FaultCase(GENERIC, COMPLETE_REPORT_SCRIPT, "ok", None, raising_read=True), id="raising-incident-read"),
]


@pytest.mark.parametrize("case", FAULT_CASES)
async def test_a_fault_is_classified_recorded_and_contained(monkeypatch: pytest.MonkeyPatch, case: FaultCase) -> None:
    world = FakeWorld(
        {
            case.lane_id: build_lane_spec(case.lane_id, case.script, timeout_seconds=case.timeout_seconds),
            HEALTHY: build_lane_spec(HEALTHY, COMPLETE_REPORT_SCRIPT),
        }
    ).install(monkeypatch)
    bucket = NOW.replace(minute=0)
    for back in (2, 1):  # two failed buckets: a third failure in a row holds the lane for an operator
        world.seed_run(case.lane_id, bucket - back * HOUR, "failed", exit_class="code")
    world.incidents.fail_select = case.raising_read
    state = SoftFailureState()

    with structlog.testing.capture_logs() as logs:
        first = await world.tick(soft=state)
        second = await world.tick(now=NOW + HOUR, soft=state)

    # 1. classified: tick one's fault-lane turn carries the case's class.
    assert world.outcomes[case.lane_id][0].metrics["exit_class"] == case.exit_class

    # 2. contained: the healthy lane ran and completed on both ticks.
    assert states(first)[HEALTHY] == states(second)[HEALTHY] == "ran"
    assert [outcome.kind for outcome in world.outcomes[HEALTHY]] == ["completed", "completed"]

    # 3. recorded: the right incident, and nothing else, is open for the fault lane.
    open_kinds = world.incidents.open_kinds(case.lane_id)
    assert open_kinds == (set() if case.incident is None else {case.incident})
    if case.incident == "lane_hold":
        hold = world.incidents.by_fingerprint(f"lane_hold:{case.lane_id}")
        assert hold is not None
        assert hold["detail"]["exit_class"] == case.exit_class  # type: ignore[index]
        assert states(second)[case.lane_id] == "failed", "held: only a recorded supersession releases it"
    if case.incident == "lane_incomplete":
        incomplete = world.incidents.by_fingerprint(f"lane_incomplete:{case.lane_id}")
        assert incomplete is not None
        assert incomplete["detail"]["reason"] == case.incomplete_reason  # type: ignore[index]
    if case.raising_read:
        assert world.incidents.rows == {}
        assert len(events_named(logs, events.EVENT_INCIDENT_WRITE_FAILED)) == 1


# --- G1 probe cases (GL-6 folded into f1-executor, plan 1D): the ladder re-tries a held lane, contained ---

PROBED: Final = "probed-lane"
PROBE_SLEEPER: Final = "import time\ntime.sleep(30)\n"


@dataclass(frozen=True, slots=True)
class ProbeCase:
    script: str
    #: The hold's `detail.state` and rung once the probe has been judged.
    state: str
    rung: int
    fenced_out: bool = False


PROBE_CASES: Final = [
    pytest.param(ProbeCase(exit_script(75), "held", 1), id="single-attempt-probe-fails"),
    pytest.param(ProbeCase(PROBE_SLEEPER, "held:1", 0, fenced_out=True), id="lost-probe-is-inconclusive"),
    # The clean probe starts probation, and that tick's first probation bucket is already one clean bucket.
    pytest.param(ProbeCase(COMPLETE_REPORT_SCRIPT, "probation:1", 0), id="clean-probe-starts-probation"),
]


@pytest.mark.parametrize("case", PROBE_CASES)
async def test_a_probe_is_single_attempt_and_contained(monkeypatch: pytest.MonkeyPatch, case: ProbeCase) -> None:
    monkeypatch.setattr(job_executor_service, "COMMAND_HEARTBEAT_SECONDS", 0.05)
    world = ProbeWorld(
        {PROBED: build_lane_spec(PROBED, case.script), HEALTHY: build_lane_spec(HEALTHY, COMPLETE_REPORT_SCRIPT)}
    ).install(monkeypatch)
    world.seed_hold(PROBED, exit_class="upstream")
    if case.fenced_out:
        world.lease_lost_lanes.add(PROBED)
        world.reap_fenced_out_attempts = True
    state = SoftFailureState(ladders=HoldLadders())

    summaries = [await world.tick(now=NOW + hours * HOUR, soft=state) for hours in (0, 1, 2)]

    opened_by_the_ladder = [run for run in world.runs_of(PROBED) if run.run_id in world.item_attempts]
    assert opened_by_the_ladder, "the probe ran"
    assert {world.item_attempts[run.run_id] for run in opened_by_the_ladder} == {1}, "one attempt, whatever the outcome"
    hold = world.incidents.by_fingerprint(f"lane_hold:{PROBED}")
    assert hold is not None
    assert (hold["detail"]["state"], hold["detail"]["rung"]) == (case.state, case.rung)  # type: ignore[index]
    assert [states(summary)[HEALTHY] for summary in summaries] == ["ran", "ran", "ran"]
    assert [outcome.kind for outcome in world.outcomes[HEALTHY]] == ["completed"] * 3
