"""`o2b-incidents`, GL-5 (spec Sec 4.9.3; design Sec 3.2A/3.4): the pure parts of
`execution/lane_incidents.py` -- `INCIDENT_SEVERITY`, `reconcile`'s state machine, the chronic/
flapping predicates, `final_attempt_exit_class`, and the repair breaker's 1/2/4/7-day cooldown
ladder. No database, no subprocess: every function under test here is DB-free by construction."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest

from agri_data_service.execution.lane_incidents import (
    CLASS_SOURCE_LOST_ATTEMPT,
    CLASS_SOURCE_MISSING_ATTEMPT,
    CLASS_SOURCE_NOT_A_HOLD_CLASS,
    CLASS_SOURCE_STAMPED,
    CLASS_SOURCE_UNRECOGNISED,
    INCIDENT_SEVERITY,
    REPAIR_BREAKER_COOLDOWN_LADDER_DAYS,
    FinalAttempt,
    HoldVerdict,
    RepairBreakerState,
    evaluate_repair_breaker,
    final_attempt_exit_class,
    is_chain_chronic,
    is_chain_flapping,
    reconcile,
    repair_breaker_admits,
    repair_breaker_cooldown_days,
)
from agri_data_service.foundation.observability.vocabulary import LOG_LEVELS
from agri_data_service.models.jobs import EventSeverity

NOW: Final = datetime(2026, 9, 28, 12, tzinfo=UTC)
RUN_A: Final = uuid.uuid4()


def _verdict(**overrides: object) -> HoldVerdict:
    base = {
        "still_held": True,
        "definition_enabled": True,
        "lane_active": True,
        "lane_quarantined": False,
        "superseded_by_operator": False,
        "superseded_run_id": None,
    }
    base.update(overrides)
    return HoldVerdict(**base)  # type: ignore[arg-type]


# --- INCIDENT_SEVERITY -------------------------------------------------------------------------


def test_every_incident_severity_maps_to_event_severity() -> None:
    assert set(INCIDENT_SEVERITY) == LOG_LEVELS
    for level, severity in INCIDENT_SEVERITY.items():
        assert isinstance(severity, EventSeverity), f"{level!r} does not map to an EventSeverity member"
    # The two levels the incidents table actually cites map to the exact members it names.
    assert INCIDENT_SEVERITY["warn"] == EventSeverity.WARNING
    assert INCIDENT_SEVERITY["error"] == EventSeverity.ERROR


# --- reconcile: the rules table (design Sec 3.2A) -----------------------------------------------


def test_reconcile_rules_table() -> None:
    # A verdict that is no longer held resolves the hold, whatever state it was in, with the right
    # released_by: an operator supersession the ladder never probed reads "operator"...
    resolved_by_operator = reconcile(
        state="held",
        verdict=_verdict(still_held=False, superseded_by_operator=True, superseded_run_id=RUN_A),
    )
    assert resolved_by_operator.action == "resolve"
    assert resolved_by_operator.released_by == "operator"

    # ...but a run the probe ladder itself superseded (GL-6 forward-compat) reads "reconciled", not
    # "operator" -- the whole point of `probed_run_ids` existing on `HoldVerdict` at all.
    resolved_by_clock = reconcile(
        state="held",
        verdict=_verdict(
            still_held=False, superseded_by_operator=True, superseded_run_id=RUN_A, probed_run_ids=frozenset({RUN_A})
        ),
    )
    assert resolved_by_clock.released_by == "reconciled"

    # No operator supersession at all is also "reconciled" (the clock released it by itself).
    resolved_by_reconciliation = reconcile(state="held", verdict=_verdict(still_held=False))
    assert resolved_by_reconciliation.action == "resolve"
    assert resolved_by_reconciliation.released_by == "reconciled"

    # A verdict that is still held resolves nothing, even while paused.
    still_held_while_paused = reconcile(state="paused", verdict=_verdict(still_held=False))
    assert still_held_while_paused.action == "resolve"

    # The definition being disabled pauses a held incident, reason "disabled".
    disabled = reconcile(state="held", verdict=_verdict(definition_enabled=False))
    assert disabled.action == "pause"
    assert disabled.state == "paused"
    assert disabled.pause_reason == "disabled"

    # Already paused for the same reason: no further action (idempotent).
    still_disabled = reconcile(state="paused", verdict=_verdict(definition_enabled=False))
    assert still_disabled.action == "none"
    assert still_disabled.state == "paused"

    # An inactive lane pauses too, reason "inactive".
    inactive = reconcile(state="held", verdict=_verdict(lane_active=False))
    assert inactive.action == "pause"
    assert inactive.pause_reason == "inactive"

    # A quarantined lane pauses the same way.
    quarantined = reconcile(state="held", verdict=_verdict(lane_quarantined=True))
    assert quarantined.action == "pause"
    assert quarantined.pause_reason == "inactive"

    # Re-enabling a disabled definition (and reactivating an inactive lane) resumes a paused hold.
    resumed = reconcile(state="paused", verdict=_verdict())
    assert resumed.action == "resume"
    assert resumed.state == "held"

    # Steady state: still held, definition enabled, lane active -- nothing to do.
    steady = reconcile(state="held", verdict=_verdict())
    assert steady.action == "none"
    assert steady.state == "held"


def test_is_chain_chronic_boundary_is_exactly_72_hours() -> None:
    chain_start = NOW - timedelta(hours=72)
    assert is_chain_chronic(chain_start, now=NOW)
    assert not is_chain_chronic(chain_start + timedelta(seconds=1), now=NOW)


def test_is_chain_flapping_boundary_is_exactly_three_episodes() -> None:
    assert not is_chain_flapping(2)
    assert is_chain_flapping(3)
    assert is_chain_flapping(4)


# --- final_attempt_exit_class ---------------------------------------------------------------------


def test_final_attempt_exit_class_reads_the_stamped_class() -> None:
    attempt = FinalAttempt(
        status="failed", failure_class="upstream", exit_class="upstream", error_summary=None, finished_at=NOW
    )
    exit_class, source = final_attempt_exit_class(attempt)
    assert exit_class == "upstream"
    assert source == CLASS_SOURCE_STAMPED


def test_final_attempt_exit_class_is_code_with_no_attempt() -> None:
    exit_class, source = final_attempt_exit_class(None)
    assert exit_class == "code"
    assert source == CLASS_SOURCE_MISSING_ATTEMPT


def test_final_attempt_exit_class_is_code_for_a_lost_attempt_even_with_a_stamped_class() -> None:
    """A `lost` attempt's `metrics` predates the kill that lost it and cannot be trusted."""
    attempt = FinalAttempt(
        status="lost", failure_class=None, exit_class="upstream", error_summary=None, finished_at=None
    )
    exit_class, source = final_attempt_exit_class(attempt)
    assert exit_class == "code"
    assert source == CLASS_SOURCE_LOST_ATTEMPT


@pytest.mark.parametrize("stamped", ["ok", "interrupted", "lease_lost", "report_missing"])
def test_final_attempt_exit_class_is_code_for_a_stamp_that_is_not_a_failure_class(stamped: str) -> None:
    """A held run whose final attempt says `interrupted` (say) still needs a failure class: `code`."""
    attempt = FinalAttempt(status="failed", failure_class=None, exit_class=stamped, error_summary=None, finished_at=NOW)
    exit_class, source = final_attempt_exit_class(attempt)
    assert exit_class == "code"
    assert source == CLASS_SOURCE_NOT_A_HOLD_CLASS


def test_final_attempt_exit_class_is_code_for_an_unrecognised_stamp() -> None:
    attempt = FinalAttempt(
        status="failed", failure_class=None, exit_class="not_a_real_class", error_summary=None, finished_at=NOW
    )
    exit_class, source = final_attempt_exit_class(attempt)
    assert exit_class == "code"
    assert source == CLASS_SOURCE_UNRECOGNISED


# --- The repair breaker: 1/2/4/7-day cooldown ladder ---------------------------------------------


def test_repair_breaker_ladder_1_2_4_7_days() -> None:
    assert REPAIR_BREAKER_COOLDOWN_LADDER_DAYS == (1, 2, 4, 7)
    assert [repair_breaker_cooldown_days(trip) for trip in (1, 2, 3, 4, 5, 6)] == [1, 2, 4, 7, 7, 7]
    with pytest.raises(ValueError, match="trip_count"):
        repair_breaker_cooldown_days(0)


def test_repair_breaker_trips_after_two_consecutive_qualifying_failures() -> None:
    state = RepairBreakerState()
    first = evaluate_repair_breaker(state, exit_class="code", now=NOW)
    assert not first.tripped_this_turn
    assert first.state.consecutive_failures == 1
    assert first.state.cooldown_until is None
    assert repair_breaker_admits(first.state, now=NOW)

    second = evaluate_repair_breaker(first.state, exit_class="hang", now=NOW)
    assert second.tripped_this_turn
    assert second.state.trip_count == 1
    assert second.state.cooldown_until == NOW + timedelta(days=1)
    assert not repair_breaker_admits(second.state, now=NOW)
    assert repair_breaker_admits(second.state, now=NOW + timedelta(days=1))


def test_repair_breaker_never_trips_on_invalid_repair_request_or_ok() -> None:
    """The caller filters `invalid_repair_request` out before calling; anything else outside the
    tripping classes (in practice `ok`) resets the breaker to a clean slate."""
    tripped = RepairBreakerState(consecutive_failures=1, trip_count=2, cooldown_until=NOW + timedelta(days=4))
    reset = evaluate_repair_breaker(tripped, exit_class="ok", now=NOW)
    assert reset.state == RepairBreakerState()
    assert not reset.tripped_this_turn


def test_repair_breaker_success_resets_the_ladder_rather_than_re_arming_the_same_rung() -> None:
    """A lane that has tripped twice (rung 2) and then runs clean does NOT resume at rung 3 on its
    next failure streak -- design Sec 3.4's "a success resolves it" means the whole ladder restarts."""
    state = RepairBreakerState(consecutive_failures=0, trip_count=2, cooldown_until=None)
    reset = evaluate_repair_breaker(state, exit_class="ok", now=NOW).state
    first = evaluate_repair_breaker(reset, exit_class="code", now=NOW)
    second = evaluate_repair_breaker(first.state, exit_class="code", now=NOW)
    assert second.state.trip_count == 1
    assert second.state.cooldown_until == NOW + timedelta(days=1)
