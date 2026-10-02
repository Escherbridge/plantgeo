"""The S5 report's own rules (FR-38): never serialised without `unwritten`, no `level`, bounded, and alarm-honest."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from agri_data_service.pipeline.runner.report import (
    REPORT_UNWRITTEN_ENTRY_LIMIT,
    TURN_LOG_LINE_LIMIT,
    ReportIncompleteError,
    TurnLog,
    TurnReportBuilder,
    UnwrittenEntry,
)

DAY = date(2026, 9, 1)
OVERFLOW_LINES = 7


def _builder() -> TurnReportBuilder:
    return TurnReportBuilder(lane="fixture-lane", mode="forward", run_id="report-test")


def test_a_report_that_never_stated_its_unwritten_days_refuses_to_serialise() -> None:
    """The executor must never read "nothing owed" from a turn that did not say so."""
    with pytest.raises(ReportIncompleteError):
        _builder().to_payload(exit_code=0)


def test_only_days_behind_the_provider_edge_make_a_turn_incomplete() -> None:
    """S5: a day the probe showed unpublished is reported, but counted by no alarm."""
    ahead = _builder()
    ahead.add_unwritten(UnwrittenEntry(day=DAY, reason="unsettled", detail="newer_than_probed_edge", behind_edge=False))
    ahead.close_unwritten()
    behind = _builder()
    behind.add_unwritten(UnwrittenEntry(day=DAY, reason="deferred_budget"))
    behind.close_unwritten()

    ahead_payload = ahead.to_payload(exit_code=0)
    behind_payload = behind.to_payload(exit_code=0)

    assert (ahead_payload["outcome"], ahead_payload["days_unwritten"], ahead_payload["days_unwritten_total"]) == (
        "completed",
        0,
        1,
    )
    assert (behind_payload["outcome"], behind_payload["days_unwritten"]) == ("incomplete", 1)
    assert "level" not in ahead_payload


def test_the_unwritten_list_is_bounded_behind_edge_first_and_says_it_was_cut() -> None:
    """The report must fit the executor's stdout tail; the entries an operator acts on come first."""
    builder = _builder()
    for offset in range(REPORT_UNWRITTEN_ENTRY_LIMIT + 5):
        builder.add_unwritten(UnwrittenEntry(day=DAY + timedelta(days=offset), reason="unsettled", behind_edge=False))
    builder.add_unwritten(UnwrittenEntry(day=DAY, reason="upstream_unavailable"))
    builder.close_unwritten()

    payload = builder.to_payload(exit_code=0)

    entries = payload["unwritten"]
    assert isinstance(entries, list)
    assert len(entries) == REPORT_UNWRITTEN_ENTRY_LIMIT
    assert entries[0]["reason"] == "upstream_unavailable"
    assert payload["unwritten_truncated"] is True
    assert payload["unwritten_by_reason"] == {"unsettled": REPORT_UNWRITTEN_ENTRY_LIMIT + 5, "upstream_unavailable": 1}


@pytest.mark.parametrize("reverse", [False, True])
def test_a_day_with_lane_and_stream_debt_keeps_both_reasons_in_a_stable_report(reverse: bool) -> None:
    """A tile outage and a stream coverage refusal can name the same day with different scopes."""
    entries = [
        UnwrittenEntry(day=DAY, reason="upstream_unavailable", detail="one tile stayed unavailable"),
        UnwrittenEntry(day=DAY, stream="water-gauges-daily", reason="refused_partial", detail="coverage_lost"),
    ]
    builder = _builder()
    for entry in reversed(entries) if reverse else entries:
        builder.add_unwritten(entry)
    builder.close_unwritten()

    payload = builder.to_payload(exit_code=0)

    assert payload["unwritten"] == [entry.to_dict() for entry in entries]
    assert payload["unwritten_by_reason"] == {"upstream_unavailable": 1, "refused_partial": 1}
    assert payload["days_unwritten"] == payload["days_unwritten_total"] == 1
    assert payload["outcome"] == "incomplete"
    assert payload["unwritten_truncated"] is False


def test_the_turn_log_caps_info_lines_and_counts_what_it_dropped_but_never_drops_an_error() -> None:
    """FR-38: at most 200 info lines per turn; the report's `log_lines_*` say what happened."""
    log = TurnLog(lane="fixture-lane", mode="forward")
    for _ in range(TURN_LOG_LINE_LIMIT + OVERFLOW_LINES):
        log.info("plantgeo_lane_turn_progress")
    log.error("plantgeo_lane_turn_failed")
    builder = _builder()
    builder.close_unwritten()

    payload = builder.to_payload(exit_code=70, log=log)

    assert payload["log_lines_info"] == TURN_LOG_LINE_LIMIT
    assert payload["log_lines_suppressed"] == OVERFLOW_LINES
    assert payload["log_lines_error"] == 1
    assert payload["outcome"] == "code_error"
