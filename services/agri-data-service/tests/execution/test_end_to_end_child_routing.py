"""o5a's sweep proof (plan 0W.3): a REAL child, run with `python -m`, through the full
`run_scheduled_command` wiring -- armed, metered, routed and redacted -- asserting on the actual
captured file descriptors (`capfd`), not a monkeypatched sink. See execution/AGENTS.md, "Command
stderr reaches the ledger".
"""

from __future__ import annotations

import json
import os
import sys
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import pytest
import structlog.testing

from agri_data_service.execution import job_executor_service
from agri_data_service.execution.job_executor_service import (
    EXECUTOR_WORK_ITEM_KIND,
    ActivationConfig,
    LaneExecutionSpec,
    run_scheduled_command,
)
from agri_data_service.jobs import JobInvocation

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

PROBE_LANE: Final = "end-to-end-probe"
MODULE_NAME: Final = "plantgeo_end_to_end_probe_child"
METER_FAULT_MODULE_NAME: Final = "plantgeo_end_to_end_meter_fault_child"
PARENT_MODULE_NAME: Final = "plantgeo_end_to_end_two_process_parent"
GRANDCHILD_MODULE_NAME: Final = "plantgeo_end_to_end_two_process_grandchild"
GRANDCHILD_HOST: Final = "grandchild.example.test"

DSN_CANARY: Final = "CANARY-DSN-PASSWORD-9f3a"
BEARER_CANARY: Final = "CANARY-BEARER-TOKEN-7c2e"
#: The DSN line and the Bearer line each leave their own marker; the repr'd header may add a third.
MIN_REDACTED_SECRET_LINES: Final = 2

#: A REAL child module (design "the child is a temporary module run with `python -m`"): imports the
#: package root (arms as an executor child on `PLANTGEO_TURN_ID`), prints a legacy success line, a
#: DSN, a bearer token and a repr'd header on stderr, drives the meter through `transport=` (no real
#: network), prints an UNFLUSHED terminal report, then raises with the same secret values still in
#: its locals.
_CHILD_MODULE_SOURCE: Final = f'''
import json
import sys

import httpx

import agri_data_service  # noqa: F401 - triggers foundation.observability.bootstrap.arm_from_environment
from agri_data_service.ingest.http import upstream_sync_client


def _mock_upstream(request: httpx.Request) -> httpx.Response:
    return httpx.Response(503, request=request)


def main() -> None:
    sys.stderr.write("legacy_lane_complete\\n")
    sys.stderr.write("dsn=postgresql://svc:{DSN_CANARY}@db.example:5432/agri\\n")
    sys.stderr.write("Authorization: Bearer {BEARER_CANARY}\\n")
    sys.stderr.write(repr({{"Authorization": "Bearer {BEARER_CANARY}"}}) + "\\n")

    transport = httpx.MockTransport(_mock_upstream)
    with upstream_sync_client(transport=transport) as client:
        client.get("https://example.open-meteo.com/v1/forecast?latitude=1&hourly=temperature_2m")

    report = {{
        "outcome": "incomplete",
        "days_unwritten": 1,
        "unwritten": [{{"day": "2026-09-27", "outcome": "conflict", "detail": "probe"}}],
    }}
    print(json.dumps(report))  # UNFLUSHED: the atexit usage writer's flush-first write covers this

    canary_dsn_password = "{DSN_CANARY}"  # noqa: F841 - deliberately kept in locals
    canary_bearer_token = "{BEARER_CANARY}"  # noqa: F841
    raise RuntimeError("boom")


if __name__ == "__main__":
    main()
'''

#: A child whose METER faults (the Open-Meteo weight table raises) while its send succeeds: the fault
#: is counted by `usage.record_meter_error`, and the send outcome still reaches the usage line.
_METER_FAULT_CHILD_SOURCE: Final = """
import json

import httpx

import agri_data_service  # noqa: F401 - arms the child on PLANTGEO_TURN_ID
from agri_data_service.foundation.observability import usage
from agri_data_service.ingest.http import upstream_sync_client


def _broken_weight_table(url: str) -> float:
    raise RuntimeError("weight table unavailable")


def _ok_upstream(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, request=request, content=b"{}")


def main() -> None:
    usage.open_meteo_weight_for_url = _broken_weight_table
    with upstream_sync_client(transport=httpx.MockTransport(_ok_upstream)) as client:
        client.get("https://archive-api.open-meteo.com/v1/archive?latitude=1&hourly=temperature_2m")
    print(json.dumps({"outcome": "complete"}))


if __name__ == "__main__":
    main()
"""

#: A turn that is two processes, the shape `burn_severity/daily.py`'s spawn child has: the grandchild
#: inherits `PLANTGEO_TURN_ID`, arms itself and does the only fetching; the parent (armed too) writes
#: its own EMPTY-hosts usage line last, at exit.
_PARENT_SOURCE: Final = f"""
import json
import subprocess
import sys

import agri_data_service  # noqa: F401 - arms the parent too


def main() -> None:
    sys.stdout.flush()
    subprocess.run(
        [sys.executable, "-m", "{GRANDCHILD_MODULE_NAME}"], stdout=sys.stdout, stderr=sys.stderr, check=True
    )
    print(json.dumps({{"outcome": "complete"}}))


if __name__ == "__main__":
    main()
"""
_GRANDCHILD_SOURCE: Final = f"""
import httpx

import agri_data_service  # noqa: F401 - arms the grandchild on the inherited PLANTGEO_TURN_ID
from agri_data_service.ingest.http import upstream_sync_client


def _ok_upstream(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, request=request, content=b"{{}}")


if __name__ == "__main__":
    with upstream_sync_client(transport=httpx.MockTransport(_ok_upstream)) as client:
        client.get("https://{GRANDCHILD_HOST}/data")
"""


def _parse_json_lines(text: str) -> list[dict[str, object]]:
    """Every line of a captured stream that parses as a JSON object; a stray non-JSON line (a third-
    party warning, say) is skipped rather than crashing the assertion that reads these back."""
    parsed: list[dict[str, object]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            candidate = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(candidate, dict):
            parsed.append(candidate)
    return parsed


def _spec(command: tuple[str, ...]) -> LaneExecutionSpec:
    return LaneExecutionSpec(
        lane_id=PROBE_LANE,
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
        command=command,
        command_timeout_seconds=60,
        description="end-to-end probe",
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
def _child_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, ...]:
    """Write the temp module and put it on the CHILD's PYTHONPATH (never this test process's own
    `sys.path` -- the module is only ever imported by the spawned `python -m` child)."""
    for name, source in (
        (MODULE_NAME, _CHILD_MODULE_SOURCE),
        (METER_FAULT_MODULE_NAME, _METER_FAULT_CHILD_SOURCE),
        (PARENT_MODULE_NAME, _PARENT_SOURCE),
        (GRANDCHILD_MODULE_NAME, _GRANDCHILD_SOURCE),
    ):
        (tmp_path / f"{name}.py").write_text(source, encoding="utf-8")
    existing = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", f"{tmp_path}{os.pathsep}{existing}" if existing else str(tmp_path))
    return (sys.executable, "-m", MODULE_NAME)


def _pin_module(monkeypatch: pytest.MonkeyPatch, module_name: str) -> None:
    monkeypatch.setattr(
        job_executor_service,
        "LANE_SPECS",
        MappingProxyType({PROBE_LANE: _spec((sys.executable, "-m", module_name))}),
    )


@pytest.fixture
def _pinned(monkeypatch: pytest.MonkeyPatch, _child_module: tuple[str, ...]) -> None:
    specs = {PROBE_LANE: _spec(_child_module)}
    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType(specs))
    monkeypatch.setattr(job_executor_service, "parse_activation", lambda: ActivationConfig(frozenset(specs)))
    monkeypatch.setattr(job_executor_service, "_LANE_TURN_REPORTS", {})


@pytest.mark.usefixtures("_pinned")
async def test_a_real_child_through_run_scheduled_command_routes_redacts_and_meters(
    capfd: pytest.CaptureFixture[str],
) -> None:
    # `_emit_lane_turn` logs through structlog (unconfigured here, so it never renders through capfd
    # the way the router's OWN raw `sys.stdout`/`sys.stderr` writes do); `capture_logs()` is the seam
    # that reads it back regardless of configuration. Both capture layers cover the SAME call.
    with structlog.testing.capture_logs() as logs:
        outcome = await run_scheduled_command(_invocation({"lane_id": PROBE_LANE}))
    captured = capfd.readouterr()

    # Neither canary EVER reaches either real stream, the stored metrics, the ledger's failure reason
    # or a first-party log line, whatever shape it was printed in.
    for surface in (captured.out, captured.err, json.dumps(outcome.metrics, default=str), outcome.reason, str(logs)):
        assert DSN_CANARY not in str(surface)
        assert BEARER_CANARY not in str(surface)

    # stderr carries this attempt's routed lines (the router's non-JSON-line payloads carry this
    # attempt's turn_id in their own `attempt` field); the legacy success line and the two redacted
    # secret lines are all `level=error` (the router's own default for an un-leveled stderr line --
    # none of them matches the third-party `Warning: ` prefix that would route as warn instead).
    turn_id = outcome.metrics["turn_id"]
    stderr_payloads = _parse_json_lines(captured.err)
    this_attempt = [payload for payload in stderr_payloads if payload.get("attempt") == turn_id]
    assert this_attempt, "the router's non-JSON-line payloads carry this attempt's turn_id"
    legacy_line = next(p for p in this_attempt if "legacy_lane_complete" in str(p.get("line", "")))
    redacted_lines = [p for p in this_attempt if "[redacted]" in str(p.get("line", ""))]
    assert legacy_line["level"] == "error"
    assert len(redacted_lines) >= MIN_REDACTED_SECRET_LINES, "the DSN and the Bearer token each leave a marker"
    assert all(p["level"] == "error" for p in redacted_lines)

    # The report is intact end to end, despite being printed unflushed right before the raise.
    assert outcome.metrics["report_present"] is True
    assert outcome.metrics["days_unwritten"] == 1
    assert job_executor_service._LANE_TURN_REPORTS[PROBE_LANE].days_unwritten == 1

    # The meter, driven purely through `transport=` (no real network), reached the usage fold.
    hosts = outcome.metrics["usage"]["hosts"]
    assert isinstance(hosts, dict)
    assert any("open-meteo.com" in host for host in hosts)
    assert outcome.metrics["usage_reported"] is True
    # The child's own usage line now carries its latest send outcome (never a hardcoded null), and the
    # router's winning outcome is what reaches the attempt.
    assert outcome.metrics["last_send_outcome"] == "5xx"
    assert outcome.metrics["meter_errors"] == 0

    # Exactly one `lane_turn`, and it is `code` (a legacy non-zero exit whose report carries no R1-R4
    # evidence at all -- the report's own shape is `{outcome, days_unwritten, unwritten}`, never
    # `error`/`error_type`/`detail`).
    lane_turn_entries = [entry for entry in logs if entry["event"] == "plantgeo_job_executor_lane_turn"]
    assert len(lane_turn_entries) == 1
    assert lane_turn_entries[0]["exit_class"] == "code"
    assert lane_turn_entries[0]["turn_id"] == turn_id
    assert outcome.metrics["exit_class"] == "code"
    assert outcome.metrics["turn_outcome"] == "code_error"


@pytest.mark.usefixtures("_pinned")
async def test_report_intact_with_an_armed_python_m_child(capfd: pytest.CaptureFixture[str]) -> None:
    """Narrower than the sweep proof above: only the flush-before-atexit-usage-write guarantee
    (BUI2-01) -- an unflushed `print()` report survives even though the SAME process also raises."""
    outcome = await run_scheduled_command(_invocation({"lane_id": PROBE_LANE}))
    capfd.readouterr()  # drain; this test only cares about the parsed metrics, not the raw streams

    assert outcome.metrics["report_present"] is True
    assert outcome.metrics["days_unwritten"] == 1
    assert outcome.metrics["publication_debt"] == 0


@pytest.mark.usefixtures("_pinned")
async def test_a_metering_fault_in_the_child_reaches_the_attempt_metrics(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A fail-open metering fault never costs the send, and is no longer invisible: the child's usage
    line counts it (`usage.record_meter_error`) and the attempt's metrics carry it."""
    _pin_module(monkeypatch, METER_FAULT_MODULE_NAME)

    outcome = await run_scheduled_command(_invocation({"lane_id": PROBE_LANE}))
    capfd.readouterr()

    assert outcome.kind == "completed"
    assert outcome.metrics["meter_errors"] == 1
    assert outcome.metrics["last_send_outcome"] == "2xx", "the send itself still succeeded and was recorded"
    host = outcome.metrics["usage"]["hosts"]["archive-api.open-meteo.com"]
    assert host["http_requests"] == 1
    assert host["http_2xx"] == 1


@pytest.mark.usefixtures("_pinned")
async def test_a_two_process_turn_folds_every_pid_s_usage_line(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """The grandchild does all the fetching; the parent's own empty-hosts usage line is written LAST.
    The fold keeps both pids' lines and sums them, so the turn is charged for the grandchild's send."""
    _pin_module(monkeypatch, PARENT_MODULE_NAME)

    outcome = await run_scheduled_command(_invocation({"lane_id": PROBE_LANE}))
    capfd.readouterr()

    assert outcome.kind == "completed"
    assert outcome.metrics["usage_reported"] is True
    assert outcome.metrics["usage_complete"] is True, "both pids opened and closed their usage lines"
    hosts = outcome.metrics["usage"]["hosts"]
    assert hosts[GRANDCHILD_HOST]["http_requests"] == 1
    assert hosts[GRANDCHILD_HOST]["last_send_outcome"] == "2xx"
    assert outcome.metrics["usage"]["charged_basis"] == "metered"
