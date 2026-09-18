"""A lane command's real exception reaches the ledger: the bounded stderr tail rides every failure reason.

Real child processes (this interpreter), no database: `LANE_SPECS` and `parse_activation` are pinned on
the module so the handler runs a probe command under the real wrapper, drain and monitor.
"""

# ruff: noqa: PLR2004 - assertion literals are the measured facts under test

from __future__ import annotations

import sys
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.execution import job_executor_service
from agri_data_service.execution.gap_repair_contract import EXECUTOR_REPAIR_WORK_ITEM_KIND, RepairRequest
from agri_data_service.execution.job_executor_service import (
    COMMAND_STDERR_SUMMARY_CHARS,
    EXECUTOR_WORK_ITEM_KIND,
    TURN_REPORT_DETAIL_CHARS,
    ActivationConfig,
    CommandStderrTail,
    LaneExecutionSpec,
    parse_terminal_report,
    run_scheduled_command,
    summarize_turn_report,
)
from agri_data_service.execution.lane_ids import VEGETATION_DIRECT_LANE_ID
from agri_data_service.jobs import JobInvocation
from agri_data_service.jobs.lease import FAILURE_SUMMARY_MAX_LENGTH, clamp_summary

if TYPE_CHECKING:
    from collections.abc import Mapping

PROBE_LANE: Final = "stderr-probe"
NOISE_LINES: Final = 200
EXIT_STATUS: Final = 3
#: A child that buries the cause under a wall of warnings, as a real traceback does.
FAILING_SCRIPT: Final = (
    "import sys\n"
    f"sys.stderr.write('warning: noise before the cause\\n' * {NOISE_LINES})\n"
    "sys.stderr.write('Traceback (most recent call last):\\n  File \"lane.py\", line 1\\n')\n"
    "sys.stderr.write('ValueError: the real cause\\n')\n"
    f"sys.exit({EXIT_STATUS})\n"
)
CHATTY_SUCCESS_SCRIPT: Final = "import sys\nsys.stderr.write('just a warning\\n')\nsys.exit(0)\n"
ECHO_ARGV_SCRIPT: Final = "import sys\nsys.stderr.write(' '.join(sys.argv[1:]))\nsys.exit(0)\n"
#: A1's exit-0-but-incomplete shape: progress lines first, then the one terminal JSON report, last.
INCOMPLETE_REPORT: Final = {
    "event": "sensors_forward_complete",
    "outcome": "incomplete",
    "days_unwritten": 2,
    "unwritten": [
        {"day": "2026-09-12", "outcome": "conflict", "detail": "z13 holds a data/absence conflict " + "x" * 400},
        {"day": "2026-09-13", "outcome": "contention", "detail": "lane-day lock held"},
    ],
}
INCOMPLETE_SCRIPT: Final = (
    "import json, sys\n"
    "print(json.dumps({'event': 'sensors_forward_started'}))\n"
    f"print(json.dumps({INCOMPLETE_REPORT!r}))\n"
    "sys.exit(0)\n"
)
COMPLETE_SCRIPT: Final = "import json, sys\nprint(json.dumps({'outcome': 'complete', 'unwritten': []}))\nsys.exit(0)\n"


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


def _invocation(kind: str, payload: Mapping[str, object]) -> JobInvocation:
    return JobInvocation(
        shard_key="2026-09-15T06:00:00+00:00",
        kind=kind,
        payload=payload,
        cursor={"state": "ready", "scheduled_for": "2026-09-15T06:00:00+00:00"},
        parameters={},
        attempt_number=1,
        max_attempts=5,
        progress_fraction=0.01,
        seconds_remaining=60.0,
        heartbeat=_heartbeat,
    )


@pytest.fixture
def teed() -> list[bytes]:
    return []


@pytest.fixture
def teed_stdout() -> list[bytes]:
    return []


@pytest.fixture
def _pinned(monkeypatch: pytest.MonkeyPatch, teed: list[bytes], teed_stdout: list[bytes]) -> None:
    specs = {
        PROBE_LANE: _spec(PROBE_LANE, FAILING_SCRIPT),
        VEGETATION_DIRECT_LANE_ID: _spec(VEGETATION_DIRECT_LANE_ID, ECHO_ARGV_SCRIPT),
    }
    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType(specs))
    monkeypatch.setattr(job_executor_service, "parse_activation", lambda: ActivationConfig(frozenset(specs)))
    monkeypatch.setattr(job_executor_service, "_default_stderr_sink", teed.append)
    monkeypatch.setattr(job_executor_service, "_default_stdout_sink", teed_stdout.append)
    monkeypatch.setattr(job_executor_service, "_LANE_TURN_REPORTS", {})


def _pin_script(monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    monkeypatch.setattr(
        job_executor_service,
        "LANE_SPECS",
        MappingProxyType({**job_executor_service.LANE_SPECS, PROBE_LANE: _spec(PROBE_LANE, script)}),
    )


def test_the_tail_keeps_the_end_and_tees_everything() -> None:
    seen: list[bytes] = []
    tail = CommandStderrTail(limit=16, sink=seen.append)

    tail.feed(b"first line\n")
    tail.feed(b"second line\n")
    tail.feed(b"")

    assert seen == [b"first line\n", b"second line\n"], "every chunk reaches the sink, the empty one is a no-op"
    assert tail.bytes_seen == 23
    assert tail.truncated is True
    assert tail.summary() == "ine | second line", "the TAIL survives, the head is what was dropped"
    assert tail.metrics() == {"stderr_bytes": 23, "stderr_truncated": True}


def test_the_summary_is_one_line_cut_from_the_front() -> None:
    tail = CommandStderrTail(limit=4096, sink=lambda _chunk: None)
    tail.feed(b"  spaced   out  \n\n\nlast\n")
    assert tail.summary() == "spaced out | last"
    assert tail.summary(max_chars=9) == "...| last"
    assert CommandStderrTail(sink=lambda _chunk: None).summary() is None


@pytest.mark.usefixtures("_pinned")
async def test_a_failing_command_s_real_exception_reaches_the_failure_reason(teed: list[bytes]) -> None:
    outcome = await run_scheduled_command(_invocation(EXECUTOR_WORK_ITEM_KIND, {"lane_id": PROBE_LANE}))

    assert outcome.kind == "failed"
    assert outcome.failure_class == "scheduled_command_exit"
    assert outcome.reason is not None
    assert outcome.reason.startswith(f"lane {PROBE_LANE!r} command exited with status {EXIT_STATUS}; stderr tail: ")
    assert outcome.reason.endswith("ValueError: the real cause"), "the last line the child wrote is the one kept"
    # What the ledger will actually store, after the same redaction and clamp `fail_work_item` applies.
    stored = clamp_summary(outcome.reason)
    assert len(stored) <= FAILURE_SUMMARY_MAX_LENGTH
    assert "ValueError: the real cause" in stored
    assert outcome.metrics["exit_code"] == EXIT_STATUS
    assert outcome.metrics["stderr_truncated"] is True
    assert outcome.metrics["stderr_bytes"] == len(b"".join(teed)), "counts only in metrics, never content"
    assert b"warning: noise before the cause" in b"".join(teed), "the log stream still receives the whole output"
    assert b"ValueError: the real cause" in b"".join(teed)


@pytest.mark.usefixtures("_pinned")
async def test_a_chatty_success_completes_and_counts_its_stderr(
    monkeypatch: pytest.MonkeyPatch, teed: list[bytes]
) -> None:
    _pin_script(monkeypatch, CHATTY_SUCCESS_SCRIPT)

    outcome = await run_scheduled_command(_invocation(EXECUTOR_WORK_ITEM_KIND, {"lane_id": PROBE_LANE}))

    assert outcome.kind == "completed"
    assert outcome.metrics["exit_code"] == 0
    # Byte-for-byte what the child wrote, whatever line ending its platform's text mode chose.
    assert outcome.metrics["stderr_bytes"] == len(b"".join(teed))
    assert b"just a warning" in b"".join(teed)
    assert outcome.metrics["stderr_truncated"] is False
    assert outcome.metrics["days_unwritten"] is None, "no terminal report on stdout, nothing claimed"
    assert outcome.cursor is not None
    assert outcome.cursor["turn_report"] is None


@pytest.mark.usefixtures("_pinned")
async def test_an_exit_zero_incomplete_turn_is_persisted_on_the_checkpoint_and_its_streak_counted(
    monkeypatch: pytest.MonkeyPatch, teed_stdout: list[bytes]
) -> None:
    _pin_script(monkeypatch, INCOMPLETE_SCRIPT)
    invocation = _invocation(EXECUTOR_WORK_ITEM_KIND, {"lane_id": PROBE_LANE})

    first = await run_scheduled_command(invocation)
    second = await run_scheduled_command(invocation)

    assert first.kind == second.kind == "completed"
    assert first.metrics["days_unwritten"] == 2
    assert first.cursor is not None
    report = first.cursor["turn_report"]
    assert isinstance(report, dict)
    assert report["outcome"] == "incomplete"
    assert report["days_unwritten"] == 2
    assert report["unwritten_truncated"] is False
    assert [entry["day"] for entry in report["unwritten"]] == ["2026-09-12", "2026-09-13"]
    assert len(report["unwritten"][0]["detail"]) == TURN_REPORT_DETAIL_CHARS, "detail is bounded"
    assert report["consecutive_incomplete_buckets"] == 1
    assert second.cursor is not None
    assert second.cursor["turn_report"]["consecutive_incomplete_buckets"] == 2  # type: ignore[index]
    assert job_executor_service._LANE_TURN_REPORTS[PROBE_LANE].consecutive_incomplete_buckets == 2
    # The log stream still received every stdout byte the child wrote, including the progress line.
    assert b"sensors_forward_started" in b"".join(teed_stdout)

    _pin_script(monkeypatch, COMPLETE_SCRIPT)
    clean = await run_scheduled_command(invocation)
    assert clean.cursor is not None
    assert clean.cursor["turn_report"]["consecutive_incomplete_buckets"] == 0  # type: ignore[index]
    assert clean.metrics["days_unwritten"] == 0


def test_the_terminal_report_is_the_last_json_object_line_and_a_fan_out_report_is_folded() -> None:
    tail = (
        b'{"event":"started"}\nprogress text\n{"outcome":"complete","results":[{"unwritten":[{"day":"2026-09-01"}]}]}\n'
    )
    parsed = parse_terminal_report(tail)
    assert parsed is not None
    kept = summarize_turn_report(parsed, previous=None)
    assert kept is not None
    assert kept.days_unwritten == 1, "an `unwritten` list nested under per-product results still counts"
    assert kept.outcome == "complete"
    assert parse_terminal_report(b"not json at all\n") is None
    assert summarize_turn_report(None, previous=None) is None
    many = {"unwritten": [{"day": f"2026-08-{day:02d}", "outcome": "conflict"} for day in range(1, 31)]}
    bounded = summarize_turn_report(many, previous=None)
    assert bounded is not None
    assert (bounded.days_unwritten, len(bounded.unwritten), bounded.unwritten_truncated) == (30, 12, True)


@pytest.mark.usefixtures("_pinned")
async def test_a_repair_work_item_runs_the_lane_s_own_command_with_only_the_bounded_knobs(teed: list[bytes]) -> None:
    request = RepairRequest(
        lane_id=VEGETATION_DIRECT_LANE_ID,
        layer="vegetation",
        max_days=3,
        gap_first_day=datetime(2026, 9, 1, tzinfo=UTC).date(),
        gap_last_day=datetime(2026, 9, 5, tzinfo=UTC).date(),
        gap_day_count=5,
        source_ceiling_day=None,
        expected_horizon_day=None,
        authored_at=datetime(2026, 9, 15, 6, tzinfo=UTC),
    )

    outcome = await run_scheduled_command(_invocation(EXECUTOR_REPAIR_WORK_ITEM_KIND, request.to_payload()))

    assert outcome.kind == "completed"
    assert b"".join(teed) == b"--max-days 3", "argv is the registered command plus exactly the request's knobs"


@pytest.mark.usefixtures("_pinned")
async def test_a_partial_repair_turn_counts_against_its_own_definition_not_the_hourly_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        job_executor_service,
        "LANE_SPECS",
        MappingProxyType(
            {
                **job_executor_service.LANE_SPECS,
                VEGETATION_DIRECT_LANE_ID: _spec(VEGETATION_DIRECT_LANE_ID, INCOMPLETE_SCRIPT),
            }
        ),
    )
    request = RepairRequest(
        lane_id=VEGETATION_DIRECT_LANE_ID,
        layer="vegetation",
        max_days=3,
        gap_first_day=datetime(2026, 9, 1, tzinfo=UTC).date(),
        gap_last_day=datetime(2026, 9, 5, tzinfo=UTC).date(),
        gap_day_count=5,
        source_ceiling_day=None,
        expected_horizon_day=None,
        authored_at=datetime(2026, 9, 15, 6, tzinfo=UTC),
    )

    outcome = await run_scheduled_command(_invocation(EXECUTOR_REPAIR_WORK_ITEM_KIND, request.to_payload()))

    assert outcome.kind == "completed"
    reports = job_executor_service._LANE_TURN_REPORTS
    assert f"{VEGETATION_DIRECT_LANE_ID}:gap-repair" in reports
    assert VEGETATION_DIRECT_LANE_ID not in reports, "the hourly lane's streak is untouched by a bounded repair"


def test_an_unwritten_day_s_detail_is_redacted_before_it_reaches_a_checkpoint() -> None:
    report = {"unwritten": [{"day": "2026-09-01", "detail": "GET https://api.example/v1?key=SECRET timed out"}]}
    kept = summarize_turn_report(report, previous=None)
    assert kept is not None
    assert "SECRET" not in str(kept.unwritten[0]["detail"])


@pytest.mark.usefixtures("_pinned")
async def test_a_malformed_repair_payload_is_refused_before_any_process_starts(teed: list[bytes]) -> None:
    payload = {"lane_id": VEGETATION_DIRECT_LANE_ID, "layer": "vegetation", "max_days": 99, "repair_payload_version": 1}

    outcome = await run_scheduled_command(_invocation(EXECUTOR_REPAIR_WORK_ITEM_KIND, payload))

    assert outcome.kind == "failed"
    assert outcome.failure_class == "invalid_repair_request"
    assert teed == [], "nothing ran"


@pytest.mark.usefixtures("_pinned")
async def test_an_unknown_work_item_kind_is_still_refused() -> None:
    outcome = await run_scheduled_command(_invocation("something-else", {"lane_id": PROBE_LANE}))
    assert outcome.kind == "failed"
    assert outcome.failure_class == "unknown_work_item_kind"


def test_the_tail_budget_leaves_room_for_the_headline_inside_the_ledger_clamp() -> None:
    assert 0 < COMMAND_STDERR_SUMMARY_CHARS < FAILURE_SUMMARY_MAX_LENGTH
