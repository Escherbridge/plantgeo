"""GL-5 records and reconciles a lane hold; only an operator releases it until G1 (spec Sec 4.9.3 "Holds").

Flows, each through one real `run_executor_tick` with real child processes (`soft_failure_fakes.py`):

- a lane that failed three buckets opens ONE `lane_hold` row whose class comes from the held run's final
  attempt, announces the release command once, and a restarted process does not announce it again;
- an operator's recorded supersession resolves the hold on the next tick and the lane runs at once;
- disabling the lane pauses the hold, and re-enabling it returns it to `held`;
- a lane taken out of the allow-list pauses its hold and never escalates it; back in, it escalates once;
- a hold row whose lane is no longer held (or no longer exists) is reconciled, not left open forever.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Final

import pytest
import structlog.testing

from agri_data_service.execution.job_executor_service import SoftFailureState, announce_operator_actions
from agri_data_service.execution.lane_incidents import (
    CLASS_SOURCE_LOST_ATTEMPT,
    CLASS_SOURCE_STAMPED,
    CLASS_SOURCE_UNRECOGNISED,
)
from agri_data_service.foundation.observability import events
from tests.execution.soft_failure_fakes import (
    COMPLETE_REPORT_SCRIPT,
    NOW,
    FakeWorld,
    build_lane_spec,
    events_named,
    states,
)

if TYPE_CHECKING:
    from datetime import datetime

HELD: Final = "held-probe-lane"
HEALTHY: Final = "healthy-probe-lane"
OPERATOR_ACTION_EVENT: Final = "plantgeo_job_executor_operator_action_required"


def _world(monkeypatch: pytest.MonkeyPatch) -> FakeWorld:
    return FakeWorld(
        {
            HELD: build_lane_spec(HELD, COMPLETE_REPORT_SCRIPT),
            HEALTHY: build_lane_spec(HEALTHY, COMPLETE_REPORT_SCRIPT),
        }
    ).install(monkeypatch)


def _hold(world: FakeWorld, lane_id: str = HELD) -> dict[str, object]:
    row = world.incidents.by_fingerprint(f"lane_hold:{lane_id}")
    assert row is not None, f"no open lane_hold for {lane_id}"
    return row


@pytest.mark.parametrize(
    ("attempt_exit_class", "attempt_status", "expected_class", "expected_source", "expected_severity"),
    [
        pytest.param("upstream", "failed", "upstream", CLASS_SOURCE_STAMPED, "warning", id="stamped-upstream"),
        pytest.param("upstream", "lost", "code", CLASS_SOURCE_LOST_ATTEMPT, "error", id="lost-attempt-is-code"),
        pytest.param(None, "failed", "code", CLASS_SOURCE_UNRECOGNISED, "error", id="unstamped-is-code"),
    ],
)
async def test_hold_opens_with_class_from_the_final_attempt(  # noqa: PLR0913 - one parametrised fact per argument
    monkeypatch: pytest.MonkeyPatch,
    attempt_exit_class: str | None,
    attempt_status: str,
    expected_class: str,
    expected_source: str,
    expected_severity: str,
) -> None:
    world = _world(monkeypatch)
    held_run = world.seed_hold(HELD, exit_class=attempt_exit_class)
    assert held_run.final_attempt is not None
    held_run.final_attempt["status"] = attempt_status
    state = SoftFailureState()

    with structlog.testing.capture_logs() as logs:
        summary = await world.tick(soft=state)
        announce_operator_actions(summary, state.announced, durable=state.durable_announcements)

    assert states(summary)[HELD] == "failed", "the hold itself is HEAD's: nothing runs on the lane"
    assert states(summary)[HEALTHY] == "ran"
    hold = _hold(world)
    assert hold["job_run_id"] == held_run.run_id
    assert hold["severity"] == expected_severity
    assert hold["detail"] == {
        "lane_id": HELD,
        "run_id": str(held_run.run_id),
        "state": "held",
        "exit_class": expected_class,
        "class_source": expected_source,
        "rung": 0,
        "chain_first_seen_at": NOW.isoformat(),
        "episodes_7d": 1,
        "probes": [],
        "clean_buckets": 0,
        "release": "operator",
        "pause_reason": None,
    }
    announced = events_named(logs, OPERATOR_ACTION_EVENT)
    assert [(entry["lane_id"], entry["exit_class"]) for entry in announced] == [(HELD, expected_class)]
    assert "jobs-supersede-run" in str(announced[0]["command"])

    # A restarted executor reads the same open row: no second announcement and no second episode.
    restarted = SoftFailureState()
    with structlog.testing.capture_logs() as later:
        summary = await world.tick(now=NOW + timedelta(minutes=30), soft=restarted)
        announce_operator_actions(summary, restarted.announced, durable=restarted.durable_announcements)

    assert events_named(later, OPERATOR_ACTION_EVENT) == []
    assert _hold(world)["occurrence_count"] == 1
    assert len(world.incidents.rows) == 1


async def test_operator_supersession_resolves_next_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(monkeypatch)
    held_run = world.seed_hold(HELD)
    state = SoftFailureState()
    await world.tick(soft=state)
    world.supersede(held_run)

    with structlog.testing.capture_logs() as logs:
        summary = await world.tick(now=NOW + timedelta(minutes=1), soft=state)

    assert world.incidents.by_fingerprint(f"lane_hold:{HELD}") is None
    (resolved,) = world.incidents.resolved(f"lane_hold:{HELD}")
    assert resolved["detail"]["released_by"] == "operator"  # type: ignore[index]
    assert str(resolved["fingerprint"]).startswith(f"lane_hold:{HELD}:resolved:"), "the fingerprint is free again"
    assert states(summary)[HELD] == "ran", "the released lane runs its current bucket on the same tick"
    released = events_named(logs, events.EVENT_HOLD_RELEASED)
    assert [(entry["lane_id"], entry["released_by"]) for entry in released] == [(HELD, "operator")]


async def test_enable_after_disable_returns_to_held(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(monkeypatch)
    world.seed_hold(HELD)
    state = SoftFailureState()
    await world.tick(soft=state)

    world.disabled.add(HELD)
    paused = await world.tick(now=NOW + timedelta(minutes=10), soft=state)
    assert states(paused)[HELD] == "paused"
    assert _hold(world)["detail"]["state"] == "paused"  # type: ignore[index]
    assert _hold(world)["detail"]["pause_reason"] == "disabled"  # type: ignore[index]

    world.disabled.discard(HELD)
    with structlog.testing.capture_logs() as logs:
        held = await world.tick(now=NOW + timedelta(minutes=20), soft=state)

    assert states(held)[HELD] == "failed"
    assert _hold(world)["detail"]["state"] == "held"  # type: ignore[index]
    assert [entry["action"] for entry in events_named(logs, events.EVENT_HOLD_RECONCILED)] == ["resume"]
    assert len(world.incidents.resolved(f"lane_hold:{HELD}")) == 0, "one episode throughout, never re-opened"


async def test_inactive_lane_hold_is_paused_not_escalated(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(monkeypatch)
    world.seed_hold(HELD, exit_class="upstream")  # opens at warn, so an escalation would be visible
    state = SoftFailureState()
    await world.tick(soft=state)
    world.set_active(frozenset({HEALTHY}))

    three_days_on: datetime = NOW + timedelta(hours=80)
    with structlog.testing.capture_logs() as logs:
        summary = await world.tick(now=three_days_on, soft=state)

    hold = _hold(world)
    assert hold["detail"]["state"] == "paused"  # type: ignore[index]
    assert hold["detail"]["pause_reason"] == "inactive"  # type: ignore[index]
    assert hold["severity"] == "warning", "a paused hold never escalates"
    assert events_named(logs, events.EVENT_HOLD_CHRONIC) == []
    assert states(summary)[HELD] == "shadow"
    assert state.chronic_lanes == ()

    # Back in the allow-list the 80-hour chain is chronic: escalated once, listed in the heartbeat.
    world.set_active(frozenset({HELD, HEALTHY}))
    with structlog.testing.capture_logs() as logs:
        await world.tick(now=three_days_on + timedelta(hours=1), soft=state)
        await world.tick(now=three_days_on + timedelta(hours=2), soft=SoftFailureState())
    assert _hold(world)["severity"] == "error"
    assert [entry["lane_id"] for entry in events_named(logs, events.EVENT_HOLD_CHRONIC)] == [HELD]
    assert state.chronic_lanes == (HELD,)


async def test_orphan_hold_incident_is_reconciled(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(monkeypatch)
    world.seed_settled(HELD)  # an operator released the lane while no executor ran, and it has since succeeded
    world.incidents.seed(
        fingerprint=f"lane_hold:{HELD}",
        incident_type="lane_hold",
        first_seen_at=NOW - timedelta(hours=2),
        job_run_id=uuid.uuid4(),
        detail={"state": "held", "rung": 0, "chain_first_seen_at": (NOW - timedelta(hours=2)).isoformat()},
    )
    world.incidents.seed(
        fingerprint="lane_hold:retired-lane",
        incident_type="lane_hold",
        first_seen_at=NOW - timedelta(hours=2),
        detail={"state": "held", "rung": 0},
    )

    await world.tick(soft=SoftFailureState())

    (orphan,) = world.incidents.resolved(f"lane_hold:{HELD}")
    assert orphan["detail"]["released_by"] == "reconciled"  # type: ignore[index]
    retired = _hold(world, "retired-lane")
    assert retired["detail"]["state"] == "paused", "a lane nobody schedules can never be released: paused"  # type: ignore[index]
    assert retired["detail"]["pause_reason"] == "inactive"  # type: ignore[index]


async def test_hold_opened_while_disabled_announces_on_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hold that opens paused announces nothing; its first held tick is where the command is logged,
    once, and the hold row then keeps a restarted process from logging it again."""
    world = _world(monkeypatch)
    held_run = world.seed_hold(HELD)
    world.disabled.add(HELD)
    state = SoftFailureState()

    with structlog.testing.capture_logs() as paused_logs:
        summary = await world.tick(soft=state)
        announce_operator_actions(summary, state.announced, durable=state.durable_announcements)
    assert _hold(world)["detail"]["state"] == "paused"  # type: ignore[index]
    assert events_named(paused_logs, OPERATOR_ACTION_EVENT) == []

    world.disabled.discard(HELD)
    with structlog.testing.capture_logs() as resumed_logs:
        for minutes in (10, 20):
            summary = await world.tick(now=NOW + timedelta(minutes=minutes), soft=state)
            announce_operator_actions(summary, state.announced, durable=state.durable_announcements)

    announced = events_named(resumed_logs, OPERATOR_ACTION_EVENT)
    assert [(entry["lane_id"], entry["run_id"]) for entry in announced] == [(HELD, str(held_run.run_id))]
    assert "jobs-supersede-run" in str(announced[0]["command"])

    restarted = SoftFailureState()
    with structlog.testing.capture_logs() as later:
        summary = await world.tick(now=NOW + timedelta(minutes=30), soft=restarted)
        announce_operator_actions(summary, restarted.announced, durable=restarted.durable_announcements)
    assert events_named(later, OPERATOR_ACTION_EVENT) == []
