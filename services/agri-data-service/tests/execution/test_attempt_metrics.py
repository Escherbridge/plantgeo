"""o5a: the usage fold into `job_attempt.metrics` (spec Sec 4.9.2) and `classify_exit` stamping
(spec Sec 4.9.3, observational at GL-3). Pure unit coverage for `_charging_basis`/`_report_usage_fields`
plus real-child integration coverage through `run_scheduled_command` for the fields only a live
attempt can produce (`report_present`, `usage_reported`, `unwritten_known`, `exit_class`).
"""

from __future__ import annotations

import sys
import time
import uuid
from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.execution import job_executor_service
from agri_data_service.execution.job_executor_service import (
    CHARGE_BASIS_VARIABLE,
    EXECUTOR_DEFINITION_PREFIX,
    EXECUTOR_WORK_ITEM_KIND,
    READY_AT_CURSOR_KEY,
    START_LAG_CLOCK_SKEW_SECONDS,
    ActivationConfig,
    LaneExecutionSpec,
    _charging_basis,
    _report_usage_fields,
    run_scheduled_command,
)
from agri_data_service.foundation.observability.vocabulary import LANE_LOGICAL_CAPS
from agri_data_service.jobs import JobDefinitionRecord, JobInvocation

if TYPE_CHECKING:
    from collections.abc import Mapping

PROBE_LANE: Final = "attempt-metrics-probe"
ROWS_WRITTEN: Final = 9
REPORTED_WEIGHTED_CALLS: Final = 12.0
METERED_WEIGHTED_CALLS: Final = 40.0
PUBLICATION_DEBT: Final = 2
#: Generous: the gap between the bootstrap call and the spawn call in one test is milliseconds.
START_LAG_CEILING_SECONDS: Final = 60.0


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


async def _heartbeat() -> bool:
    return True


def _invocation(payload: Mapping[str, object]) -> JobInvocation:
    return JobInvocation(
        shard_key="2026-09-27T06:00:00+00:00",
        kind=EXECUTOR_WORK_ITEM_KIND,
        payload=payload,
        cursor={"state": "ready", "scheduled_for": "2026-09-27T06:00:00+00:00"},
        parameters={},
        attempt_number=1,
        max_attempts=5,
        progress_fraction=0.01,
        seconds_remaining=60.0,
        heartbeat=_heartbeat,
    )


@pytest.fixture
def _pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job_executor_service, "_LANE_TURN_REPORTS", {})


# --- `_report_usage_fields`: the legacy name map ----------------------------------------------------


def test_current_field_names_pass_through_unchanged() -> None:
    report = {"requests": 4, "weighted_calls": 12.0, "fetch_attempts": 5, "rows_written": 9, "bytes_written": 100}
    assert _report_usage_fields(report) == report


def test_legacy_names_map_onto_current_ones() -> None:
    report = {"requests_spent": 4, "rows": 9, "bytes": 100}
    fields = _report_usage_fields(report)
    assert fields == {"requests": 4, "rows_written": 9, "bytes_written": 100}


def test_a_current_name_is_never_overwritten_by_a_stale_legacy_one() -> None:
    report = {"rows_written": ROWS_WRITTEN, "rows": 999}
    assert _report_usage_fields(report)["rows_written"] == ROWS_WRITTEN


def test_none_report_is_an_empty_fold() -> None:
    assert _report_usage_fields(None) == {}


def test_only_typed_values_cross_from_a_child_report_into_unredacted_metrics() -> None:
    """`metrics` is stored unredacted: a counter that is not a finite number, and a `probe_status`
    outside the writers' own vocabulary, never cross -- whatever free text a child printed there."""
    report = {
        "requests": "postgresql://svc:CANARY@db/agri",
        "weighted_calls": float("nan"),
        "fetch_attempts": True,
        "rows": ROWS_WRITTEN,
        "probe_status": "Bearer CANARY-PROBE-TOKEN",
    }
    assert _report_usage_fields(report) == {"rows_written": ROWS_WRITTEN}
    assert _report_usage_fields({"probe_status": "blind"}) == {"probe_status": "blind"}


# --- `_charging_basis`: the per-attempt half of spec Sec 4.9.2's table -----------------------------


def test_not_spawned_is_never_charged_or_suspect() -> None:
    basis = _charging_basis(lane_id="soil", spawned=False, usage_reported=True, report_fields={}, hosts=None)
    assert basis == {"charged_basis": "not_spawned", "charged": 0.0, "suspect": 0.0}


def test_metered_prefers_the_larger_of_metered_and_reported_by_default() -> None:
    hosts = {"a.open-meteo.com": {"weighted_calls_metered": METERED_WEIGHTED_CALLS}}
    basis = _charging_basis(
        lane_id="soil",
        spawned=True,
        usage_reported=True,
        report_fields={"weighted_calls": REPORTED_WEIGHTED_CALLS},
        hosts=hosts,
    )
    assert basis == {"charged_basis": "metered", "charged": METERED_WEIGHTED_CALLS, "suspect": 0.0}


def test_charge_basis_logical_prefers_the_reported_figure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CHARGE_BASIS_VARIABLE, "logical")
    hosts = {"a.open-meteo.com": {"weighted_calls_metered": METERED_WEIGHTED_CALLS}}
    basis = _charging_basis(
        lane_id="soil",
        spawned=True,
        usage_reported=True,
        report_fields={"weighted_calls": REPORTED_WEIGHTED_CALLS},
        hosts=hosts,
    )
    assert basis["charged_basis"] == "metered"
    assert basis["charged"] == REPORTED_WEIGHTED_CALLS


def test_charge_basis_logical_keeps_a_reported_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reported 0 is a real logical figure, not "absent": it must not fall through to the meter."""
    monkeypatch.setenv(CHARGE_BASIS_VARIABLE, "logical")
    hosts = {"a.open-meteo.com": {"weighted_calls_metered": METERED_WEIGHTED_CALLS}}
    basis = _charging_basis(
        lane_id="soil", spawned=True, usage_reported=True, report_fields={"weighted_calls": 0}, hosts=hosts
    )
    assert basis == {"charged_basis": "metered", "charged": 0.0, "suspect": 0.0}


def test_reported_basis_when_spawned_with_a_report_but_no_usage_line() -> None:
    basis = _charging_basis(
        lane_id="soil",
        spawned=True,
        usage_reported=False,
        report_fields={"weighted_calls": REPORTED_WEIGHTED_CALLS},
        hosts=None,
    )
    assert basis == {"charged_basis": "reported", "charged": REPORTED_WEIGHTED_CALLS, "suspect": 0.0}


def test_suspect_basis_charges_the_lane_s_logical_cap_when_spawned_with_neither() -> None:
    basis = _charging_basis(lane_id="soil", spawned=True, usage_reported=False, report_fields={}, hosts=None)
    assert basis == {"charged_basis": "suspect", "charged": 0.0, "suspect": float(LANE_LOGICAL_CAPS["soil"])}


def test_suspect_basis_is_zero_for_a_lane_with_no_declared_logical_cap() -> None:
    basis = _charging_basis(lane_id="drought", spawned=True, usage_reported=False, report_fields={}, hosts=None)
    assert basis == {"charged_basis": "suspect", "charged": 0.0, "suspect": 0.0}


# --- Real-child integration: only a live attempt can produce these -------------------------------


@pytest.mark.usefixtures("_pinned")
async def test_exit_class_is_stamped_from_classify_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """75 and 78 are explicit, evidence-free signals (spec Sec 4.9.3): the stamped `exit_class` must
    equal exactly what `classify_exit` says for that return code, with no lane-specific evidence."""
    specs = {PROBE_LANE: _spec(PROBE_LANE, "import sys\nsys.exit(75)\n")}
    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType(specs))
    monkeypatch.setattr(job_executor_service, "parse_activation", lambda: ActivationConfig(frozenset(specs)))

    upstream = await run_scheduled_command(_invocation({"lane_id": PROBE_LANE}))
    assert upstream.metrics["exit_class"] == "upstream"
    assert upstream.metrics["turn_outcome"] == "upstream_unavailable"

    config_specs = {PROBE_LANE: _spec(PROBE_LANE, "import sys\nsys.exit(78)\n")}
    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType(config_specs))
    config_error = await run_scheduled_command(_invocation({"lane_id": PROBE_LANE}))
    assert config_error.metrics["exit_class"] == "config"
    assert config_error.metrics["turn_outcome"] == "config_error"


@pytest.mark.usefixtures("_pinned")
async def test_a_pre_spawn_failure_is_stamped_config_with_a_failure_class(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType({}))
    monkeypatch.setattr(job_executor_service, "parse_activation", lambda: ActivationConfig(frozenset()))

    outcome = await run_scheduled_command(_invocation({"lane_id": "no-such-lane"}))

    assert outcome.kind == "failed"
    assert outcome.failure_class == "unknown_executor_lane"
    assert outcome.metrics == {
        "turn_id": outcome.metrics["turn_id"],
        "spawned": False,
        "exit_class": "config",
        "turn_outcome": "config_error",
        # Never a NULL basis for GL-4's rollup, even on a row that never spawned.
        "usage": {"hosts": None, "charged_basis": "not_spawned", "charged": 0.0, "suspect": 0.0},
    }
    uuid.UUID(str(outcome.metrics["turn_id"]))


@pytest.mark.usefixtures("_pinned")
async def test_a_no_budget_yield_also_carries_the_not_spawned_usage_block(monkeypatch: pytest.MonkeyPatch) -> None:
    specs = {PROBE_LANE: _spec(PROBE_LANE, "import sys\nsys.exit(0)\n")}
    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType(specs))
    monkeypatch.setattr(job_executor_service, "parse_activation", lambda: ActivationConfig(frozenset(specs)))
    invocation = _invocation({"lane_id": PROBE_LANE})
    no_budget = replace(invocation, seconds_remaining=0.0)

    outcome = await run_scheduled_command(no_budget)

    assert outcome.kind == "yielded"
    assert outcome.metrics["exit_class"] == "interrupted"
    assert outcome.metrics["spawned"] is False
    assert outcome.metrics["usage"] == {"hosts": None, "charged_basis": "not_spawned", "charged": 0.0, "suspect": 0.0}


@pytest.mark.usefixtures("_pinned")
async def test_start_lag_is_wall_clock_from_the_persisted_ready_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bootstrap call stamps the cursor it persists; the spawn call (possibly in another process or
    on another host) measures from it on the wall clock -- a stamp from a clock far ahead of this one
    is unknown, never a negative lag."""
    specs = {PROBE_LANE: _spec(PROBE_LANE, "import sys\nsys.exit(0)\n")}
    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType(specs))
    monkeypatch.setattr(job_executor_service, "parse_activation", lambda: ActivationConfig(frozenset(specs)))
    bootstrap_invocation = _invocation({"lane_id": PROBE_LANE})
    bootstrap = await run_scheduled_command(replace(bootstrap_invocation, cursor=None))
    assert bootstrap.kind == "progressed"
    assert bootstrap.cursor is not None

    spawn_invocation = replace(bootstrap_invocation, cursor=bootstrap.cursor)
    spawned = await run_scheduled_command(spawn_invocation)

    lag = spawned.metrics["start_lag_seconds"]
    assert isinstance(lag, float)
    assert 0.0 <= lag < START_LAG_CEILING_SECONDS

    future_stamp = {**bootstrap.cursor, READY_AT_CURSOR_KEY: time.time() + 10 * START_LAG_CLOCK_SKEW_SECONDS}
    skewed = await run_scheduled_command(replace(bootstrap_invocation, cursor=future_stamp))
    assert skewed.metrics["start_lag_seconds"] is None


@pytest.mark.usefixtures("_pinned")
async def test_report_present_and_unwritten_known_follow_whether_a_report_was_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    specs = {PROBE_LANE: _spec(PROBE_LANE, "import sys\nsys.exit(0)\n")}
    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType(specs))
    monkeypatch.setattr(job_executor_service, "parse_activation", lambda: ActivationConfig(frozenset(specs)))

    no_report = await run_scheduled_command(_invocation({"lane_id": PROBE_LANE}))
    assert no_report.metrics["report_present"] is False
    assert no_report.metrics["unwritten_known"] is False
    assert no_report.metrics["exit_class"] == "report_missing"

    report_script = "import json, sys\nprint(json.dumps({'outcome': 'complete'}))\nsys.exit(0)\n"
    monkeypatch.setattr(
        job_executor_service, "LANE_SPECS", MappingProxyType({PROBE_LANE: _spec(PROBE_LANE, report_script)})
    )
    with_report = await run_scheduled_command(_invocation({"lane_id": PROBE_LANE}))
    assert with_report.metrics["report_present"] is True
    assert with_report.metrics["unwritten_known"] is True
    assert with_report.metrics["exit_class"] == "ok"
    assert with_report.metrics["turn_outcome"] == "completed"


@pytest.mark.usefixtures("_pinned")
async def test_publication_debt_is_kept_alongside_the_new_usage_fold(monkeypatch: pytest.MonkeyPatch) -> None:
    report = {"outcome": "completed", "availability_retry_owed": 2}
    script = f"import json, sys\nprint(json.dumps({report!r}))\nsys.exit(0)\n"
    specs = {PROBE_LANE: _spec(PROBE_LANE, script)}
    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType(specs))
    monkeypatch.setattr(job_executor_service, "parse_activation", lambda: ActivationConfig(frozenset(specs)))

    outcome = await run_scheduled_command(_invocation({"lane_id": PROBE_LANE}))

    assert outcome.metrics["publication_debt"] == PUBLICATION_DEBT
    assert outcome.metrics["exit_class"] == "ok"
    assert outcome.metrics["turn_outcome"] == "incomplete", "publication debt alone still owes work (PD)"
