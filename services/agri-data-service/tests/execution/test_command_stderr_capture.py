"""A lane command's real exception reaches the ledger: the bounded stderr tail rides every failure reason.

Real child processes (this interpreter), no database: `LANE_SPECS` and `parse_activation` are pinned on
the module so the handler runs a probe command under the real wrapper, drain and monitor.
"""

# ruff: noqa: PLR2004 - assertion literals are the measured facts under test

from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.execution import job_executor_service
from agri_data_service.execution.gap_repair_contract import EXECUTOR_REPAIR_WORK_ITEM_KIND, RepairRequest
from agri_data_service.execution.job_executor_service import (
    COMMAND_STDERR_SUMMARY_CHARS,
    EXECUTOR_DEFINITION_PREFIX,
    EXECUTOR_WORK_ITEM_KIND,
    PUBLICATION_DEBT_COUNTERS,
    TURN_REPORT_DETAIL_CHARS,
    ActivationConfig,
    CommandStderrTail,
    DueLane,
    ExecutorTickSummary,
    LaneExecutionSpec,
    LaneTickResult,
    TurnReport,
    _describe_turn_report_debt,
    _execute_due_lane,
    parse_terminal_report,
    run_scheduled_command,
    summarize_turn_report,
)
from agri_data_service.execution.lane_ids import VEGETATION_DIRECT_LANE_ID
from agri_data_service.foundation.observability import router as observability_router
from agri_data_service.jobs import JobDefinitionRecord, JobInvocation, JobSliceSummary
from agri_data_service.jobs.lease import FAILURE_SUMMARY_MAX_LENGTH, clamp_summary
from agri_data_service.pipeline.parquet.availability_extension import AvailabilityExtensionTally

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
#: A2's quiet failure: every day wrote (`days_unwritten` absent), exit 0, yet availability owes a retry.
DEBT_ONLY_REPORT: Final = {"outcome": "completed", "availability_extended": 1, "availability_retry_owed": 2}
DEBT_SCRIPT: Final = f"import json, sys\nprint(json.dumps({DEBT_ONLY_REPORT!r}))\nsys.exit(0)\n"


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


#: `run_scheduled_command` (o5a) now tees every child chunk through `ChildLogRouter` in front of the
#: real sinks (execution/AGENTS.md, "Command stderr reaches the ledger"): the mirror Railway actually
#: sees is the router's own `_default_stdout_sink`/`_default_stderr_sink`, one JSON payload per routed
#: line, not job_executor_service's raw byte passthrough. `teed`/`teed_stdout` capture THOSE payloads;
#: the RAW bounded tail (`_command_failure_reason`, `parse_terminal_report`) is untouched by any of
#: this and is asserted separately, directly off `outcome`.
def _rendered_text(records: list[dict[str, object]]) -> str:
    """Every text-bearing field a routed payload can carry, joined for a substring check."""
    parts: list[str] = []
    for payload in records:
        for key in ("line", "preview", "event"):
            value = payload.get(key)
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts)


@pytest.fixture
def teed() -> list[dict[str, object]]:
    return []


@pytest.fixture
def teed_stdout() -> list[dict[str, object]]:
    return []


@pytest.fixture
def _pinned(
    monkeypatch: pytest.MonkeyPatch, teed: list[dict[str, object]], teed_stdout: list[dict[str, object]]
) -> None:
    specs = {
        PROBE_LANE: _spec(PROBE_LANE, FAILING_SCRIPT),
        VEGETATION_DIRECT_LANE_ID: _spec(VEGETATION_DIRECT_LANE_ID, ECHO_ARGV_SCRIPT),
    }
    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType(specs))
    monkeypatch.setattr(job_executor_service, "parse_activation", lambda: ActivationConfig(frozenset(specs)))
    monkeypatch.setattr(observability_router, "_default_stderr_sink", teed.append)
    monkeypatch.setattr(observability_router, "_default_stdout_sink", teed_stdout.append)
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


def test_a_raising_sink_never_costs_the_tail_or_raises() -> None:
    def broken_log_stream(_chunk: bytes) -> None:
        raise BrokenPipeError("the log stream is gone")

    tail = CommandStderrTail(limit=4096, sink=broken_log_stream)

    tail.feed(b"Traceback (most recent call last):\n")
    tail.feed(b"ValueError: the real cause\n")

    assert tail.summary() == "Traceback (most recent call last): | ValueError: the real cause"
    assert tail.sink_failures == 2, "every failed forward is counted; the drain keeps reading"


def test_the_summary_redacts_each_line_before_the_front_cut() -> None:
    tail = CommandStderrTail(limit=4096, sink=lambda _chunk: None)
    tail.feed(b"Authorization: Bearer CANARY-SUMMARY-TOKEN\n")
    tail.feed(b"dsn=postgresql://svc:CANARY-SUMMARY-PASSWORD@db.example:5432/agri\n")
    tail.feed(b"ValueError: the real cause\n")

    summary = tail.summary()

    assert summary is not None
    assert "CANARY-SUMMARY-TOKEN" not in summary
    assert "CANARY-SUMMARY-PASSWORD" not in summary
    assert summary.endswith("ValueError: the real cause")


def test_the_summary_is_one_line_cut_from_the_front() -> None:
    tail = CommandStderrTail(limit=4096, sink=lambda _chunk: None)
    tail.feed(b"  spaced   out  \n\n\nlast\n")
    assert tail.summary() == "spaced out | last"
    assert tail.summary(max_chars=9) == "...| last"
    assert CommandStderrTail(sink=lambda _chunk: None).summary() is None


@pytest.mark.usefixtures("_pinned")
async def test_a_failing_command_s_real_exception_reaches_the_failure_reason(teed: list[dict[str, object]]) -> None:
    """The RAW bounded tail (never the routed mirror) is what protects the ledger's failure reason:
    `NOISE_LINES` (200) is chosen to exactly fill the router's own per-attempt error-line ceiling
    (`_MAX_ERROR_LINES_PER_ATTEMPT`), so the routed mirror below legitimately drops the traceback and
    the real exception -- and the ledger reason still carries it, because that reads the tail, not the
    mirror. See execution/AGENTS.md, "Command stderr reaches the ledger"."""
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
    assert outcome.metrics["exit_class"] == "code"
    assert outcome.metrics["turn_outcome"] == "code_error"
    assert "warning: noise before the cause" in _rendered_text(teed), "the routed mirror still carries the noise"
    assert any(payload.get("event") == "plantgeo_child_log_truncated" for payload in teed), (
        "the flood past the per-attempt error ceiling is counted and announced, not silently lost"
    )


@pytest.mark.usefixtures("_pinned")
async def test_a_chatty_success_completes_and_counts_its_stderr(
    monkeypatch: pytest.MonkeyPatch, teed: list[dict[str, object]]
) -> None:
    _pin_script(monkeypatch, CHATTY_SUCCESS_SCRIPT)

    outcome = await run_scheduled_command(_invocation(EXECUTOR_WORK_ITEM_KIND, {"lane_id": PROBE_LANE}))

    assert outcome.kind == "completed"
    assert outcome.metrics["exit_code"] == 0
    assert outcome.metrics["exit_class"] == "report_missing", "exit 0, no terminal JSON report on stdout"
    # Counts only in metrics, never content: the RAW tail's own byte counter, independent of routing.
    # The child's text-mode stderr writes the platform line ending (`\r\n` on Windows).
    assert outcome.metrics["stderr_bytes"] == len(("just a warning" + os.linesep).encode())
    assert "just a warning" in _rendered_text(teed), "well under any ceiling, so the mirror still carries it"
    assert outcome.metrics["stderr_truncated"] is False
    assert outcome.metrics["days_unwritten"] is None, "no terminal report on stdout, nothing claimed"
    assert outcome.cursor is not None
    assert outcome.cursor["turn_report"] is None


@pytest.mark.usefixtures("_pinned")
async def test_an_exit_zero_incomplete_turn_is_persisted_on_the_checkpoint_and_its_streak_counted(
    monkeypatch: pytest.MonkeyPatch, teed_stdout: list[dict[str, object]]
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
    # The routed mirror still carries the progress line's event name (JSON content survives routing).
    assert "sensors_forward_started" in _rendered_text(teed_stdout)

    _pin_script(monkeypatch, COMPLETE_SCRIPT)
    clean = await run_scheduled_command(invocation)
    assert clean.cursor is not None
    assert clean.cursor["turn_report"]["consecutive_incomplete_buckets"] == 0  # type: ignore[index]
    assert clean.metrics["days_unwritten"] == 0


@pytest.mark.usefixtures("_pinned")
async def test_an_exit_zero_debt_only_turn_is_persisted_on_the_checkpoint_and_its_streak_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A2's quiet failure end to end: `days_unwritten == 0` must still reach `metrics` and the checkpoint,
    not just the in-process `TurnReport` that `summarize_turn_report`'s unit tests already cover."""
    _pin_script(monkeypatch, DEBT_SCRIPT)
    invocation = _invocation(EXECUTOR_WORK_ITEM_KIND, {"lane_id": PROBE_LANE})

    first = await run_scheduled_command(invocation)
    second = await run_scheduled_command(invocation)

    assert first.kind == second.kind == "completed"
    assert first.metrics["days_unwritten"] == 0
    assert first.metrics["publication_debt"] == 2
    assert first.cursor is not None
    report = first.cursor["turn_report"]
    assert isinstance(report, dict)
    assert report["outcome"] == "completed"
    assert report["days_unwritten"] == 0
    assert report["publication_debt"] == 2
    assert report["publication_debt_counts"] == {"availability_retry_owed": 2}
    assert report["consecutive_incomplete_buckets"] == 1
    assert second.cursor is not None
    assert second.cursor["turn_report"]["publication_debt"] == 2  # type: ignore[index]
    assert second.cursor["turn_report"]["consecutive_incomplete_buckets"] == 2  # type: ignore[index]

    _pin_script(monkeypatch, COMPLETE_SCRIPT)
    clean = await run_scheduled_command(invocation)
    assert clean.metrics["publication_debt"] == 0
    assert clean.cursor is not None
    assert clean.cursor["turn_report"]["publication_debt"] == 0  # type: ignore[index]
    assert clean.cursor["turn_report"]["consecutive_incomplete_buckets"] == 0  # type: ignore[index]


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
async def test_a_repair_work_item_runs_the_lane_s_own_command_with_only_the_bounded_knobs(
    teed: list[dict[str, object]],
) -> None:
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
    # No trailing newline (the script's `stderr.write` never emits one), so the router never sees a
    # complete line until `flush()` forces the buffered partial out as a `plantgeo_child_output` stub.
    assert "--max-days 3" in _rendered_text(teed), "argv is the registered command plus exactly the request's knobs"


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
async def test_a_malformed_repair_payload_is_refused_before_any_process_starts(
    teed: list[dict[str, object]],
) -> None:
    payload = {"lane_id": VEGETATION_DIRECT_LANE_ID, "layer": "vegetation", "max_days": 99, "repair_payload_version": 1}

    outcome = await run_scheduled_command(_invocation(EXECUTOR_REPAIR_WORK_ITEM_KIND, payload))

    assert outcome.kind == "failed"
    assert outcome.failure_class == "invalid_repair_request"
    assert outcome.metrics["spawned"] is False
    assert outcome.metrics["exit_class"] == "config"
    assert teed == [], "nothing ran, so no router was ever fed"


@pytest.mark.usefixtures("_pinned")
async def test_an_unknown_work_item_kind_is_still_refused() -> None:
    outcome = await run_scheduled_command(_invocation("something-else", {"lane_id": PROBE_LANE}))
    assert outcome.kind == "failed"
    assert outcome.failure_class == "unknown_work_item_kind"


def test_the_tail_budget_leaves_room_for_the_headline_inside_the_ledger_clamp() -> None:
    assert 0 < COMMAND_STDERR_SUMMARY_CHARS < FAILURE_SUMMARY_MAX_LENGTH


def test_publication_debt_counters_are_every_tally_field_except_the_settled_pair() -> None:
    """Drift guard for the hand-copied list: a name added to `_TALLY_FIELDS` without a matching change
    here would silently count as zero debt, which is the exact quiet failure this whole feature exists
    to surface. See execution/AGENTS.md, "Publication debt is the second, quieter half of an incomplete
    turn"."""
    settled = {"availability_extended", "availability_skipped_unchanged"}
    all_fields = set(AvailabilityExtensionTally().to_summary())

    assert not settled & set(PUBLICATION_DEBT_COUNTERS), "the settled pair must never read as owed work"
    assert set(PUBLICATION_DEBT_COUNTERS) | settled == all_fields, (
        "every AvailabilityExtensionTally field must be classified as settled or owed, with none dropped"
    )


def test_a_turn_that_wrote_every_day_but_owes_availability_is_still_incomplete() -> None:
    """The quiet failure: four rungs in R2, `outcome=completed`, exit 0, and nothing serving them."""
    report = {
        "outcome": "completed",
        "days_unwritten": 0,
        "availability_extended": 3,
        "availability_skipped_unchanged": 1,
        "availability_retry_owed": 2,
        "availability_quarantined_standing": 1,
    }

    kept = summarize_turn_report(report, previous=None)

    assert kept is not None
    assert kept.days_unwritten == 0
    assert kept.publication_debt == 3
    assert kept.publication_debt_counts == {
        "availability_retry_owed": 2,
        "availability_quarantined_standing": 1,
    }
    assert kept.incomplete
    assert kept.consecutive_incomplete_buckets == 1


def test_a_settled_availability_summary_is_never_read_as_debt() -> None:
    report = {"outcome": "completed", "availability_extended": 12, "availability_skipped_unchanged": 40}

    kept = summarize_turn_report(report, previous=None)

    assert kept is not None
    assert (kept.publication_debt, kept.publication_debt_counts, kept.incomplete) == (0, {}, False)


def test_publication_debt_is_folded_out_of_nested_per_product_results() -> None:
    report = {
        "outcome": "completed",
        "results": [
            {"layer": "climate-a", "availability_ladder_incomplete": 1},
            {"layer": "climate-b", "availability_ladder_incomplete": 2, "availability_not_bootstrapped": 1},
        ],
    }

    kept = summarize_turn_report(report, previous=None)

    assert kept is not None
    assert kept.publication_debt == 4
    assert kept.publication_debt_counts["availability_ladder_incomplete"] == 3


def test_owed_publication_continues_the_incomplete_streak_and_a_clean_turn_clears_it() -> None:
    first = summarize_turn_report({"availability_retry_owed": 1}, previous=None)
    assert first is not None
    second = summarize_turn_report({"availability_retry_owed": 1}, previous=first)
    assert second is not None
    cleared = summarize_turn_report({"availability_extended": 1}, previous=second)

    assert (first.consecutive_incomplete_buckets, second.consecutive_incomplete_buckets) == (1, 2)
    assert cleared is not None
    assert cleared.consecutive_incomplete_buckets == 0


def test_a_malformed_debt_counter_is_ignored_rather_than_guessed_at() -> None:
    report = {"availability_retry_owed": "two", "availability_reindex_owed": True, "availability_ladder_incomplete": -1}

    kept = summarize_turn_report(report, previous=None)

    assert kept is not None
    assert (kept.publication_debt, kept.publication_debt_counts) == (0, {})


def test_debt_only_incomplete_lanes_carry_their_publication_debt_not_a_false_zero() -> None:
    """`days_unwritten == 0` here must not read as clean: `incomplete_lanes` exists to surface this."""
    debt_only = summarize_turn_report({"availability_retry_owed": 2}, previous=None)
    assert debt_only is not None
    assert debt_only.days_unwritten == 0
    lane = LaneTickResult(lane_id="climate-direct", state="ran", turn_report=debt_only)
    summary = ExecutorTickSummary(observed_at=datetime(2026, 9, 27, tzinfo=UTC), leader=True, lanes=(lane,))

    assert summary.incomplete_lanes == (lane,)
    # The owner-decided boundary this whole subsection exists to hold: standing debt is reporting-only.
    # It must never flip `state`, and `ExecutorTickSummary.failed` (so the `--once` exit code) must not see it.
    assert lane.state == "ran"
    assert summary.failed is False
    rendered = summary.to_dict()["incomplete_lanes"]
    assert rendered == [
        {
            "lane_id": "climate-direct",
            "days_unwritten": 0,
            "consecutive_incomplete_buckets": 1,
            "publication_debt": 2,
        }
    ]


def test_the_blocker_detail_is_worded_for_debt_when_no_day_went_unwritten() -> None:
    """The bug this guards: a debt-only turn must not read as `left 0 day(s) unwritten`."""
    debt_only = TurnReport(
        outcome="completed",
        days_unwritten=0,
        unwritten=(),
        unwritten_truncated=False,
        consecutive_incomplete_buckets=3,
        publication_debt=2,
        publication_debt_counts={"availability_retry_owed": 2},
    )

    detail = _describe_turn_report_debt(debt_only)

    assert "0 day(s) unwritten" not in detail
    assert detail == (
        "owes availability publication for 3 consecutive bucket(s) in this process (availability_retry_owed=2)"
    )


def test_the_blocker_detail_still_names_unwritten_days_when_that_is_also_owed() -> None:
    mixed = TurnReport(
        outcome="incomplete",
        days_unwritten=2,
        unwritten=(),
        unwritten_truncated=False,
        consecutive_incomplete_buckets=1,
        publication_debt=1,
        publication_debt_counts={"availability_reindex_owed": 1},
    )

    detail = _describe_turn_report_debt(mixed)

    assert detail == (
        "left 2 day(s) unwritten for 1 consecutive bucket(s) in this process"
        "; owes availability publication (availability_reindex_owed=1)"
    )


@pytest.mark.usefixtures("_pinned")
async def test_execute_due_lane_reports_standing_publication_debt_without_failing_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real regression site for `test_debt_only_incomplete_lanes_carry_their_publication_debt_not_a_false_zero`
    above: that test only ever passed `lane.state` in by hand, so nothing exercised the `failed` computation
    in `_execute_due_lane` itself that actually decides it. A claimed, non-retried run whose lane owes
    publication debt must still land as `state="ran"`."""
    debt_only = TurnReport(
        outcome="completed",
        days_unwritten=0,
        unwritten=(),
        unwritten_truncated=False,
        consecutive_incomplete_buckets=1,
        publication_debt=2,
        publication_debt_counts={"availability_retry_owed": 2},
    )
    job_executor_service._LANE_TURN_REPORTS[PROBE_LANE] = debt_only
    run_id = uuid.uuid4()

    async def _claimed_zero_retry_summary(*_args: object, **_kwargs: object) -> JobSliceSummary:
        return JobSliceSummary(
            definition_name=PROBE_LANE,
            worker_id="test-worker",
            job_run_id=run_id,
            stop_reason="time_budget_exhausted",
            claimed=1,
            succeeded=1,
            run_status="succeeded",
        )

    monkeypatch.setattr(job_executor_service, "run_job_slice", _claimed_zero_retry_summary)
    spec = job_executor_service.LANE_SPECS[PROBE_LANE]
    candidate = DueLane(
        spec=spec,
        definition=_definition(spec),
        scheduled_for=datetime(2026, 9, 27, tzinfo=UTC),
        existing_run_id=run_id,
        last_scheduled_for=None,
    )

    result = await _execute_due_lane(None, candidate, stop=None)  # type: ignore[arg-type]

    assert result.state == "ran", "standing publication debt alone must never flip a claimed run to failed"
    assert "owes availability publication" in result.detail
