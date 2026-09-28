"""o5a: `run_scheduled_command` tees every child chunk through `ChildLogRouter` in front of the sinks.

Real child processes (this interpreter), no database. The router's OWN sinks
(`foundation.observability.router::_default_stdout_sink`/`_default_stderr_sink`) are the mirror an
operator actually reads on Railway; the bounded RAW tail (never touched by the router) is what
`_command_failure_reason`/`parse_terminal_report` read. See execution/AGENTS.md, "Command stderr
reaches the ledger".
"""

from __future__ import annotations

import sys
import uuid
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import pytest
import structlog.testing

from agri_data_service.execution import job_executor_service
from agri_data_service.execution.job_executor_service import (
    EXECUTOR_DEFINITION_PREFIX,
    EXECUTOR_WORK_ITEM_KIND,
    ActivationConfig,
    LaneExecutionSpec,
    run_scheduled_command,
)
from agri_data_service.foundation.observability import router as observability_router
from agri_data_service.jobs import JobDefinitionRecord, JobInvocation

if TYPE_CHECKING:
    from collections.abc import Mapping

PROBE_LANE: Final = "router-wiring-probe"
SECRET_SCRIPT: Final = (
    "import sys\n"
    "sys.stderr.write('dsn=postgresql://svc:CANARY-DSN-SECRET@db.example:5432/agri\\n')\n"
    "sys.stderr.write('Authorization: Bearer CANARY-BEARER-SECRET\\n')\n"
    "sys.exit(1)\n"
)
JSON_STDOUT_SCRIPT: Final = "import json, sys\nprint(json.dumps({'event': 'probe_started', 'n': 1}))\nsys.exit(0)\n"
NO_TRAILING_NEWLINE_SCRIPT: Final = "import sys\nsys.stdout.write('partial-line-no-newline')\nsys.exit(0)\n"
#: Past the router's 64 KiB reassembly cap with no newline -- a GDAL-style `\r` progress bar -- which
#: sends the router down its (unguarded) stub path.
PROGRESS_BAR_BYTES: Final = 70 * 1024
BROKEN_STREAM_REPORT: Final = {
    "outcome": "incomplete",
    "days_unwritten": 2,
    "unwritten": [{"day": "2026-09-27", "outcome": "conflict", "detail": "probe"}],
}
STDERR_PARTIAL_LINE: Final = "ValueError: the real cause, with no trailing newline"
#: The progress bar, then the one terminal report, then a stderr partial line only `flush()` reaches.
BROKEN_STREAM_SCRIPT: Final = (
    "import json, sys\n"
    f"sys.stdout.write('\\r' + '#' * {PROGRESS_BAR_BYTES} + '\\n')\n"
    f"print(json.dumps({BROKEN_STREAM_REPORT!r}))\n"
    f"sys.stderr.write({STDERR_PARTIAL_LINE!r})\n"
    "sys.exit(0)\n"
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


def _ready_cursor() -> dict[str, object]:
    return {"state": "ready", "scheduled_for": "2026-09-27T06:00:00+00:00"}


@pytest.fixture
def routed_stderr() -> list[dict[str, object]]:
    return []


@pytest.fixture
def routed_stdout() -> list[dict[str, object]]:
    return []


@pytest.fixture
def _pinned(
    monkeypatch: pytest.MonkeyPatch,
    routed_stderr: list[dict[str, object]],
    routed_stdout: list[dict[str, object]],
) -> None:
    specs = {PROBE_LANE: _spec(PROBE_LANE, SECRET_SCRIPT)}
    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType(specs))
    monkeypatch.setattr(job_executor_service, "parse_activation", lambda: ActivationConfig(frozenset(specs)))
    monkeypatch.setattr(observability_router, "_default_stderr_sink", routed_stderr.append)
    monkeypatch.setattr(observability_router, "_default_stdout_sink", routed_stdout.append)
    monkeypatch.setattr(job_executor_service, "_LANE_TURN_REPORTS", {})


def _pin_script(monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    monkeypatch.setattr(
        job_executor_service,
        "LANE_SPECS",
        MappingProxyType({PROBE_LANE: _spec(PROBE_LANE, script)}),
    )


def _rendered_text(records: list[dict[str, object]]) -> str:
    parts: list[str] = []
    for payload in records:
        for key in ("line", "preview"):
            value = payload.get(key)
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts)


@pytest.mark.usefixtures("_pinned")
async def test_a_secret_reaching_stderr_is_redacted_in_the_routed_mirror(
    routed_stderr: list[dict[str, object]],
) -> None:
    """The DSN and bearer token the child prints reach the routed mirror only in redacted form --
    the RAW tail (read separately, never asserted here) is what the ledger's failure reason uses."""
    outcome = await run_scheduled_command(_invocation({"lane_id": PROBE_LANE}, cursor=_ready_cursor()))

    assert outcome.kind == "failed"
    rendered = _rendered_text(routed_stderr)
    assert "CANARY-DSN-SECRET" not in rendered
    assert "CANARY-BEARER-SECRET" not in rendered
    assert "[redacted]" in rendered, "the DSN userinfo and Bearer token both leave a redaction marker"


@pytest.mark.usefixtures("_pinned")
async def test_a_json_stdout_line_is_leveled_and_routed(
    monkeypatch: pytest.MonkeyPatch, routed_stdout: list[dict[str, object]]
) -> None:
    _pin_script(monkeypatch, JSON_STDOUT_SCRIPT)

    outcome = await run_scheduled_command(_invocation({"lane_id": PROBE_LANE}, cursor=_ready_cursor()))

    assert outcome.kind == "completed"
    assert any(payload.get("event") == "probe_started" for payload in routed_stdout)
    routed = next(payload for payload in routed_stdout if payload.get("event") == "probe_started")
    assert routed["level"] in {"info", "debug", "warn", "error"}, "the router never leaves a line unleveled"
    assert "timestamp" in routed
    assert "service" in routed
    # The child never stamped this line itself: the turn context comes from THIS turn, not from the
    # executor process's own environment (which never carries the turn keys).
    assert routed["turn_id"] == outcome.metrics["turn_id"]
    assert routed["lane"] == PROBE_LANE
    assert routed["mode"] == "forward"


def test_the_executor_s_turn_field_table_matches_the_router_s() -> None:
    """`_ROUTER_TURN_FIELDS` is a hand-kept copy (the sibling-copy rule); this pins it against drift."""
    assert job_executor_service._ROUTER_TURN_FIELDS == observability_router._TURN_ENV_TO_FIELD


@pytest.mark.usefixtures("_pinned")
async def test_a_raising_router_leaves_the_raw_tail_and_report_intact(monkeypatch: pytest.MonkeyPatch) -> None:
    """The log stream is broken (every routed write raises) while the child prints a 70 KiB `\\r`
    progress bar and ends on a partial stderr line -- the two router paths that are NOT guarded inside
    the router itself. The raw tails still hold every byte, the report still parses, the turn still
    completes (no child blocked on an unread pipe, no fault out of `run_scheduled_command`), and the
    fault is counted and warned about exactly once."""

    def broken_log_stream(_payload: dict[str, object]) -> None:
        raise BrokenPipeError("the log stream is gone")

    monkeypatch.setattr(observability_router, "_default_stdout_sink", broken_log_stream)
    monkeypatch.setattr(observability_router, "_default_stderr_sink", broken_log_stream)
    _pin_script(monkeypatch, BROKEN_STREAM_SCRIPT)

    with structlog.testing.capture_logs() as logs:
        outcome = await run_scheduled_command(_invocation({"lane_id": PROBE_LANE}, cursor=_ready_cursor()))

    assert outcome.kind == "completed"
    assert outcome.metrics["report_present"] is True
    assert outcome.metrics["days_unwritten"] == BROKEN_STREAM_REPORT["days_unwritten"]
    assert outcome.metrics["turn_outcome"] == "incomplete"
    assert outcome.metrics["stdout_bytes"] > PROGRESS_BAR_BYTES, "every stdout byte reached the raw tail"
    assert outcome.metrics["stderr_bytes"] == len(STDERR_PARTIAL_LINE), "the partial stderr line too"
    assert outcome.metrics["log_router_faults"] >= 1
    warnings = [entry for entry in logs if entry["event"] == "plantgeo_job_executor_log_router_failed"]
    assert len(warnings) == 1, "one bounded warning per turn, however many faults"
    lane_turns = [entry for entry in logs if entry["event"] == "plantgeo_job_executor_lane_turn"]
    assert [entry["turn_id"] for entry in lane_turns] == [outcome.metrics["turn_id"]]


@pytest.mark.usefixtures("_pinned")
async def test_a_trailing_partial_line_is_flushed_as_a_stub_after_the_child_exits(
    monkeypatch: pytest.MonkeyPatch, routed_stdout: list[dict[str, object]]
) -> None:
    """No trailing newline: the router buffers it until `run_scheduled_command` calls `.flush()`
    after the drain finishes, so the content still reaches the mirror rather than being lost."""
    _pin_script(monkeypatch, NO_TRAILING_NEWLINE_SCRIPT)

    outcome = await run_scheduled_command(_invocation({"lane_id": PROBE_LANE}, cursor=_ready_cursor()))

    assert outcome.kind == "completed"
    assert any(
        payload.get("event") == "plantgeo_child_output" and "partial-line-no-newline" in str(payload.get("preview"))
        for payload in routed_stdout
    )


async def test_spawned_marker_distinguishes_pre_spawn_from_spawned(monkeypatch: pytest.MonkeyPatch) -> None:
    """`metrics.spawned` is `False` on the bootstrap `progressed` call (no cursor yet, nothing has
    run) and `True` once `create_subprocess_exec` has actually returned -- the one call later, with a
    `ready` cursor, that spawns the real child."""
    specs = {PROBE_LANE: _spec(PROBE_LANE, "import sys\nsys.exit(0)\n")}
    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType(specs))
    monkeypatch.setattr(job_executor_service, "parse_activation", lambda: ActivationConfig(frozenset(specs)))
    monkeypatch.setattr(job_executor_service, "_LANE_TURN_REPORTS", {})

    pre_spawn = await run_scheduled_command(_invocation({"lane_id": PROBE_LANE}, cursor=None))
    assert pre_spawn.kind == "progressed"
    assert pre_spawn.metrics["spawned"] is False
    assert isinstance(pre_spawn.metrics["turn_id"], str)
    uuid.UUID(str(pre_spawn.metrics["turn_id"]))  # a real uuid4, not a placeholder

    spawned = await run_scheduled_command(_invocation({"lane_id": PROBE_LANE}, cursor=_ready_cursor()))
    assert spawned.kind == "completed"
    assert spawned.metrics["spawned"] is True
    # Each CALL to `run_scheduled_command` mints its own turn_id (design Sec 1.2): the bootstrap
    # step and the real spawn are two separate calls and never share one.
    assert spawned.metrics["turn_id"] != pre_spawn.metrics["turn_id"]
