"""o5a: tick volume (design Sec 1.4) -- `tick_started`/`leader_*`/`tick_healthy` drop to debug; the
tick summary prints on a (lane, state, run) change and hourly; exactly one
`plantgeo_job_executor_lane_turn` per terminal handler outcome, pre-spawn included.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import pytest
import structlog.testing

from agri_data_service.execution import job_executor_service
from agri_data_service.execution.job_executor_service import (
    EXECUTOR_WORK_ITEM_KIND,
    ActivationConfig,
    ExecutorTickSummary,
    LaneExecutionSpec,
    LaneTickResult,
    _tick_signature,
    _UnhealthyEdge,
    announce_operator_actions,
    run_executor_tick,
    run_scheduled_command,
)
from agri_data_service.foundation.observability.logging import configure_logging
from agri_data_service.jobs import JobInvocation

if TYPE_CHECKING:
    from collections.abc import Mapping

NOW: Final = datetime(2026, 9, 27, 6, tzinfo=UTC)
PROBE_LANE: Final = "tick-volume-probe"
LANE_TURN_EVENT: Final = "plantgeo_job_executor_lane_turn"
TICK_UNHEALTHY_EVENT: Final = "plantgeo_job_executor_tick_unhealthy"
BEARER_CANARY: Final = "CANARY-LANE-TURN-BEARER-5d1b"
#: A failing child: a secret on stderr, then the real cause as its LAST line.
FAILING_WITH_SECRET_SCRIPT: Final = (
    "import sys\n"
    f"sys.stderr.write('Authorization: Bearer {BEARER_CANARY}\\n')\n"
    "sys.stderr.write('ValueError: the real cause\\n')\n"
    "sys.exit(1)\n"
)


def _spec(lane_id: str, script: str) -> LaneExecutionSpec:
    return LaneExecutionSpec(
        lane_id=lane_id,
        conflicts_with=(),
        work_class="incremental",
        migration_disposition="source-specific",
        cadence_seconds=3600,
        phase_offset_seconds=0,
        schedule="0 * * * *",
        publication_lag_days=None,
        publication_cadence_days=None,
        publication_lag_source="test",
        selection_policy="test",
        catch_up_policy="coalesce_latest",
        command=(sys.executable, "-c", script),
        command_timeout_seconds=60,
        description="probe",
    )


async def _heartbeat() -> bool:
    return True


def _invocation(payload: Mapping[str, object], *, cursor: Mapping[str, object] | None) -> JobInvocation:
    return JobInvocation(
        shard_key="2026-09-27T06:00:00+00:00",
        kind=EXECUTOR_WORK_ITEM_KIND,
        payload=payload,
        cursor=cursor,
        parameters={},
        attempt_number=1,
        max_attempts=5,
        progress_fraction=0.01,
        seconds_remaining=60.0,
        heartbeat=_heartbeat,
    )


class _FakeSession:
    """Just enough of `AsyncSession`'s surface for the leader-not-acquired early return."""

    async def rollback(self) -> None:
        return None


async def test_leader_not_acquired_and_tick_started_log_at_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_lock(_session: object) -> bool:
        return False

    async def _noop_timeout(_session: object) -> None:
        return None

    monkeypatch.setattr(job_executor_service, "_try_leader_lock", _no_lock)
    monkeypatch.setattr(job_executor_service, "apply_statement_timeout", _noop_timeout)

    with structlog.testing.capture_logs() as logs:
        summary = await run_executor_tick(
            _FakeSession(),  # type: ignore[arg-type]
            activation=ActivationConfig(frozenset()),
            now=NOW,
            max_lanes_per_tick=2,
        )

    assert summary.leader is False
    events = [(entry["event"], entry["log_level"]) for entry in logs]
    assert ("plantgeo_job_executor_tick_started", "debug") in events
    assert ("plantgeo_job_executor_leader_not_acquired", "debug") in events
    assert not any(level == "info" for _, level in events), "no tick-volume line stays at info any more"


def test_tick_signature_is_stable_across_reruns_of_the_same_result() -> None:
    lane = LaneTickResult(lane_id="soil", state="ran", run_id=uuid.UUID(int=1))
    summary_a = ExecutorTickSummary(observed_at=NOW, leader=True, lanes=(lane,))
    summary_b = ExecutorTickSummary(observed_at=NOW, leader=True, lanes=(lane,))
    assert _tick_signature(summary_a) == _tick_signature(summary_b)


def test_tick_signature_changes_with_lane_state() -> None:
    ran = LaneTickResult(lane_id="soil", state="ran", run_id=uuid.UUID(int=1))
    failed = LaneTickResult(lane_id="soil", state="failed", run_id=uuid.UUID(int=1))
    before = ExecutorTickSummary(observed_at=NOW, leader=True, lanes=(ran,))
    after = ExecutorTickSummary(observed_at=NOW, leader=True, lanes=(failed,))
    assert _tick_signature(before) != _tick_signature(after)


def test_announce_operator_actions_carries_the_process_local_exit_class(monkeypatch: pytest.MonkeyPatch) -> None:
    """o5a: `plantgeo_job_executor_operator_action_required` gains `exit_class`, read from
    `_LANE_EXIT_CLASSES` -- `None` until this process has classified a turn for that lane itself."""
    monkeypatch.setattr(job_executor_service, "_LANE_EXIT_CLASSES", {"drought-direct-forward": "upstream"})
    run_id = uuid.uuid4()
    held = LaneTickResult(
        lane_id="drought-direct-forward",
        state="failed",
        run_id=run_id,
        operator_action="agri-service ops jobs-supersede-run --lane drought-direct-forward",
    )
    summary = ExecutorTickSummary(observed_at=NOW, leader=True, lanes=(held,))
    announced: set[tuple[str, str | None]] = set()

    with structlog.testing.capture_logs() as logs:
        announce_operator_actions(summary, announced)

    required = next(entry for entry in logs if entry["event"] == "plantgeo_job_executor_operator_action_required")
    assert required["exit_class"] == "upstream"


def test_announce_operator_actions_names_no_class_for_a_lane_this_process_never_classified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `_LANE_EXIT_CLASSES` is process-global (like `_LANE_TURN_REPORTS`); cleared here so an earlier
    # test's own classification of this lane id can never leak into this one's "never classified" claim.
    monkeypatch.setattr(job_executor_service, "_LANE_EXIT_CLASSES", {})
    run_id = uuid.uuid4()
    held = LaneTickResult(
        lane_id="fire-perimeters-direct-forward", state="failed", run_id=run_id, operator_action="cmd"
    )
    summary = ExecutorTickSummary(observed_at=NOW, leader=True, lanes=(held,))

    with structlog.testing.capture_logs() as logs:
        announce_operator_actions(summary, set())

    required = next(entry for entry in logs if entry["event"] == "plantgeo_job_executor_operator_action_required")
    assert required["exit_class"] is None


@pytest.mark.parametrize(
    ("script", "expected_exit_class"),
    [
        pytest.param("import sys\nsys.exit(0)\n", "report_missing", id="completed-no-report"),
        pytest.param("import sys\nsys.exit(1)\n", "code", id="legacy-failure-no-evidence"),
    ],
)
async def test_turn_id_on_every_lane_turn_including_pre_spawn(
    monkeypatch: pytest.MonkeyPatch, script: str, expected_exit_class: str
) -> None:
    specs = {PROBE_LANE: _spec(PROBE_LANE, script)}
    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType(specs))
    monkeypatch.setattr(job_executor_service, "parse_activation", lambda: ActivationConfig(frozenset(specs)))
    monkeypatch.setattr(job_executor_service, "_LANE_TURN_REPORTS", {})

    # Pre-spawn: the bootstrap `progressed` call. NOT terminal -- no `lane_turn` line, per design's
    # "exactly one per TERMINAL handler outcome" (progressed is not one; see execution/AGENTS.md).
    with structlog.testing.capture_logs() as pre_spawn_logs:
        pre_spawn = await run_scheduled_command(_invocation({"lane_id": PROBE_LANE}, cursor=None))
    assert not any(entry["event"] == "plantgeo_job_executor_lane_turn" for entry in pre_spawn_logs)
    assert isinstance(pre_spawn.metrics["turn_id"], str)

    # The spawn call IS terminal: exactly one `lane_turn`, carrying this call's OWN turn_id.
    with structlog.testing.capture_logs() as logs:
        outcome = await run_scheduled_command(_invocation({"lane_id": PROBE_LANE}, cursor={"state": "ready"}))
    turns = [entry for entry in logs if entry["event"] == "plantgeo_job_executor_lane_turn"]
    assert len(turns) == 1
    assert turns[0]["turn_id"] == outcome.metrics["turn_id"]
    assert turns[0]["turn_id"] != pre_spawn.metrics["turn_id"], "each call mints its own turn_id"
    assert turns[0]["lane_id"] == PROBE_LANE
    assert turns[0]["spawned"] is True
    assert turns[0]["exit_class"] == expected_exit_class


async def test_a_pre_spawn_refusal_is_also_exactly_one_lane_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refusal before `create_subprocess_exec` (an unknown lane, here) IS terminal -- `failed`, not
    `progressed` -- so it still gets its one `lane_turn` line, `spawned=False`."""
    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType({}))
    monkeypatch.setattr(job_executor_service, "parse_activation", lambda: ActivationConfig(frozenset()))

    with structlog.testing.capture_logs() as logs:
        outcome = await run_scheduled_command(_invocation({"lane_id": "unregistered"}, cursor=None))

    assert outcome.kind == "failed"
    turns = [entry for entry in logs if entry["event"] == "plantgeo_job_executor_lane_turn"]
    assert len(turns) == 1
    assert turns[0]["spawned"] is False
    assert turns[0]["exit_class"] == "config"


async def test_a_lane_turn_after_configure_logging_is_json_with_a_level(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The production order: `agri-service ops jobs-executor` imports this module (as this test module
    already has) BEFORE the CLI root calls `configure_logging("tool")`. The `lane_turn` line must still
    go through the configured pipeline -- one JSON object with the `level` Railway reads and the
    `service` envelope -- and, for a failed turn, say WHY with its redacted stderr tail."""
    specs = {PROBE_LANE: _spec(PROBE_LANE, FAILING_WITH_SECRET_SCRIPT)}
    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType(specs))
    monkeypatch.setattr(job_executor_service, "parse_activation", lambda: ActivationConfig(frozenset(specs)))
    monkeypatch.setattr(job_executor_service, "_LANE_TURN_REPORTS", {})
    configure_logging("tool")

    outcome = await run_scheduled_command(_invocation({"lane_id": PROBE_LANE}, cursor={"state": "ready"}))
    captured = capsys.readouterr()

    lane_turns = []
    for line in captured.err.splitlines():
        stripped = line.strip()
        if stripped.startswith("{") and LANE_TURN_EVENT in stripped:
            lane_turns.append(json.loads(stripped))
    assert len(lane_turns) == 1, "exactly one lane_turn, and it rendered as JSON"
    lane_turn = lane_turns[0]
    assert lane_turn["event"] == LANE_TURN_EVENT
    assert lane_turn["level"] == "error", "a `code` turn is an error Railway can filter on"
    assert "service" in lane_turn
    assert lane_turn["turn_id"] == outcome.metrics["turn_id"]
    assert lane_turn["exit_class"] == "code"
    assert str(lane_turn["stderr_tail"]).endswith("ValueError: the real cause")
    assert BEARER_CANARY not in captured.err
    assert BEARER_CANARY not in captured.out


def _tick(*lanes: LaneTickResult) -> ExecutorTickSummary:
    return ExecutorTickSummary(observed_at=NOW, leader=True, lanes=lanes)


def test_tick_unhealthy_prints_when_the_unhealthy_set_changes_not_every_tick() -> None:
    """A standing hold prints once; a NEW failure behind it prints again (it must never be hidden by
    the one before it); a healthy tick resets the edge so the next failure prints."""
    held = LaneTickResult(lane_id="drought-direct-forward", state="failed", run_id=uuid.UUID(int=1))
    second = LaneTickResult(lane_id="sensors-direct-forward", state="failed", run_id=uuid.UUID(int=2))
    healthy = LaneTickResult(lane_id="drought-direct-forward", state="ran", run_id=uuid.UUID(int=3))
    edge = _UnhealthyEdge()

    with structlog.testing.capture_logs() as logs:
        for summary in (
            _tick(held),
            _tick(held),
            _tick(held, second),
            _tick(held, second),
            _tick(healthy),
            _tick(held),
        ):
            edge.report(summary)

    unhealthy = [entry["failing_lanes"] for entry in logs if entry["event"] == TICK_UNHEALTHY_EVENT]
    assert unhealthy == [
        ["drought-direct-forward"],
        ["drought-direct-forward", "sensors-direct-forward"],
        ["drought-direct-forward"],
    ]
