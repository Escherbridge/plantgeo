"""`router.py::ChildLogRouter`. Not wired to any real child yet (GL-3 does that); see its AGENTS.md
entry for the bound table and the judgment calls this module makes where the design record
(§1.6) leaves a gap. "Revision 1's eleven" (plan 0W.1, design §6.1) names could not be recovered
verbatim -- the design record's own §6.1 for `test_router.py` says only "revision 1's eleven tests"
without listing them, and no earlier revision of the file survives anywhere in the repo or the
research directory (checked: only revision 2 of `observability-wave-design.md` and its
`.result.json` exist). The eleven below are freshly authored against §1.6's prose instead, one per
described behaviour; see this file's authoring report for that gap.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from agri_data_service.foundation.observability import events, router
from agri_data_service.foundation.observability.router import ChildLogRouter

if TYPE_CHECKING:
    import pytest


def _make(attempt_id: str = "attempt-1") -> ChildLogRouter:
    return ChildLogRouter(attempt_id=attempt_id)


def _line(obj: dict[str, object]) -> bytes:
    return (json.dumps(obj) + "\n").encode("utf-8")


# --- Freshly authored (see module docstring): core JSON/non-JSON/redaction/bound behaviour --------


def test_json_line_with_a_level_is_kept_as_is() -> None:
    child = _make()
    child.route_line("stdout", _line({"event": "x", "level": "warn"}))
    assert child.routed[0].level == "warn"


def test_json_line_without_a_level_gets_the_stream_default() -> None:
    child = _make()
    child.route_line("stdout", _line({"event": "x"}))
    child.route_line("stderr", _line({"event": "y"}))
    assert child.routed[0].level == "info"
    assert child.routed[1].level == "error"


def test_legacy_event_name_gets_its_override_level() -> None:
    child = _make()
    child.route_line("stdout", _line({"event": "tick_unhealthy"}))
    assert child.routed[0].level == "warn"


def test_legacy_lane_lines_are_levelled_by_outcome_not_by_stream() -> None:
    """The shapes the first live GL-3 read (2026-09-28) saw reach Railway as errors."""
    child = _make()
    child.route_line("stderr", _line({"event": "soil_forward_complete", "requests_spent": 1}))
    child.route_line("stderr", _line({"event": "drought_forward_started", "selected_weeks": []}))
    child.route_line("stderr", _line({"event": "climate_forward_quota_pause", "pause": 1}))
    child.route_line("stdout", _line({"event": "water_gauges_forward_failed", "error_type": "UpstreamHttpError"}))
    child.route_line("stdout", _line({"status": "failed", "error": "UpstreamHttpError: 503"}))
    assert [line.level for line in child.routed] == ["info", "info", "warn", "error", "error"]


def test_missing_turn_context_is_filled_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANTGEO_LANE_ID", "soil")
    child = _make()
    child.route_line("stdout", _line({"event": "x", "level": "info"}))
    assert child.routed[0].payload["lane"] == "soil"


def test_present_turn_context_is_never_overwritten(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANTGEO_LANE_ID", "soil")
    child = _make()
    child.route_line("stdout", _line({"event": "x", "level": "info", "lane": "climate"}))
    assert child.routed[0].payload["lane"] == "climate"


def test_secret_leaf_inside_a_json_line_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_METEO_API_KEY", "supersecretvalue123")
    child = _make()
    child.route_line("stdout", _line({"event": "x", "level": "info", "detail": "used supersecretvalue123 today"}))
    assert "supersecretvalue123" not in json.dumps(child.routed[0].payload)


def test_non_json_stdout_line_is_wrapped_as_child_output() -> None:
    child = _make()
    # `route_line` takes an already newline-delimited line (the newline stripped, exactly as
    # `feed()` hands it off) -- never a trailing "\n" itself.
    child.route_line("stdout", b"plain text from a legacy print")
    assert child.routed[0].payload["event"] == events.EVENT_CHILD_OUTPUT
    assert child.routed[0].payload["line"] == "plain text from a legacy print"


def test_non_json_stderr_line_stays_on_stderr_and_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_METEO_API_KEY", "supersecretvalue123")
    child = _make()
    child.route_line("stderr", b"boom: supersecretvalue123")
    assert child.routed[0].stream == "stderr"
    assert "supersecretvalue123" not in child.routed[0].payload["line"]


def test_non_json_line_over_16_kib_is_truncated_without_leaking_a_secret_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_METEO_API_KEY", "supersecretvalue123")
    filler = "x" * (16 * 1024)
    line = (filler + " supersecretvalue123" + " tail").encode("utf-8")
    child = _make()
    child.route_line("stdout", line)
    payload_text = json.dumps(child.routed[0].payload)
    assert "supersecretvalue123" not in payload_text
    assert len(child.routed[0].payload["line"].encode("utf-8")) <= 16 * 1024 + len("…[truncated]".encode())


def test_log_routing_off_disables_releveling_but_redaction_stays_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANTGEO_LOG_ROUTING", "off")
    monkeypatch.setenv("OPEN_METEO_API_KEY", "supersecretvalue123")
    monkeypatch.setenv("PLANTGEO_LANE_ID", "soil")
    child = _make()
    child.route_line("stdout", _line({"event": "tick_unhealthy", "detail": "supersecretvalue123"}))
    payload = child.routed[0].payload
    # Re-leveling is off: the legacy override is not applied, so the line keeps the stream default.
    assert payload["level"] == "info"
    # Turn-context rewriting is off too.
    assert "lane" not in payload
    # Redaction is never disabled.
    assert "supersecretvalue123" not in payload["detail"]


def test_a_structured_path_fault_falls_back_to_line_wise_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_METEO_API_KEY", "supersecretvalue123")

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    child = _make()
    monkeypatch.setattr(child, "_handle_json_object", _raise)
    child.route_line("stdout", _line({"event": "x", "level": "info", "detail": "supersecretvalue123"}))
    assert child.log_lines_dropped == 0
    assert "supersecretvalue123" not in json.dumps(child.routed[0].payload)


def test_a_fault_in_the_fallback_itself_drops_the_line_and_counts_it(monkeypatch: pytest.MonkeyPatch) -> None:
    child = _make()

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(child, "_handle_json_object", _raise)
    monkeypatch.setattr(router.redaction, "redact_for_log", _raise)
    child.route_line("stdout", _line({"event": "x", "level": "info"}))
    assert child.log_lines_dropped == 1
    assert child.routed == []


# --- Named in plan 0W.1 / design §6.1 --------------------------------------------------------------


def test_rate_bucket_drops_info_keeps_error_report_lane_turn() -> None:
    clock = {"t": 0.0}
    child = ChildLogRouter(attempt_id="attempt-1", clock=lambda: clock["t"])
    # Burst capacity is 200; exhaust it with plain info lines at t=0, then everything else at the
    # same instant (no time passes, so the sustained-rate refill is zero) must be dropped except the
    # exempt kinds.
    for _ in range(200):
        child.route_line("stdout", _line({"event": "plantgeo_child_output", "level": "info"}))
    dropped_before = child.log_lines_dropped

    child.route_line("stdout", _line({"event": "plantgeo_child_output", "level": "info"}))
    assert child.log_lines_dropped == dropped_before + 1

    child.route_line("stderr", _line({"event": "something_bad", "level": "error"}))
    child.route_line("stdout", _line({"event": events.EVENT_LANE_TURN_REPORT, "level": "info"}))
    child.route_line("stdout", _line({"event": events.EVENT_JOB_EXECUTOR_LANE_TURN, "level": "info"}))

    routed_events = [line.payload.get("event") for line in child.routed]
    assert "something_bad" in routed_events
    assert events.EVENT_LANE_TURN_REPORT in routed_events
    assert events.EVENT_JOB_EXECUTOR_LANE_TURN in routed_events


def test_unterminated_line_over_64_kib_becomes_a_stub() -> None:
    child = _make()
    # No trailing newline, ever: feed exceeds the 64 KiB reassembly cap while still mid-line.
    child.feed("stdout", b"{" + b"a" * (64 * 1024 + 1))
    assert len(child.routed) == 1
    payload = child.routed[0].payload
    assert payload["event"] == events.EVENT_CHILD_OUTPUT
    assert payload["bytes"] == 64 * 1024 + 2
    assert payload["stream"] == "stdout"
    assert len(payload["preview"]) <= 1024 + len("…[truncated]")


def test_third_party_warning_prefix_routes_as_warn() -> None:
    child = _make()
    child.route_line("stderr", b"UserWarning: something noisy from a dependency")
    assert child.routed[0].level == "warn"


def test_unclosed_usage_pid_flags_usage_incomplete() -> None:
    child = _make()
    child.route_line("stdout", _line({"event": events.EVENT_TURN_USAGE_OPEN, "level": "debug", "pid": 42}))
    summary = child.usage_summary()
    assert summary["usage_complete"] is False
    assert summary["usage_incomplete_pids"] == [42]


def test_latest_last_send_outcome_wins_across_usage_lines() -> None:
    child = _make()
    child.route_line(
        "stdout",
        _line(
            {
                "event": events.EVENT_TURN_USAGE,
                "level": "debug",
                "pid": 1,
                "last_send_outcome": "2xx",
                "last_send_at": 100.0,
            }
        ),
    )
    child.route_line(
        "stdout",
        _line(
            {
                "event": events.EVENT_TURN_USAGE,
                "level": "debug",
                "pid": 2,
                "last_send_outcome": "429",
                "last_send_at": 200.0,
            }
        ),
    )
    summary = child.usage_summary()
    assert summary["last_send_outcome"] == "429"
    assert summary["usage_complete"] is True
