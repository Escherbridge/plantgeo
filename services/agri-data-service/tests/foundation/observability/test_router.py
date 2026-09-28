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
import time
from collections import Counter
from typing import TYPE_CHECKING

import pytest

from agri_data_service.foundation.observability import events, redaction, router
from agri_data_service.foundation.observability.router import ChildLogRouter

if TYPE_CHECKING:
    from collections.abc import Callable


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


# --- Admission before redaction: a dropped line costs no redaction (AGENTS.md "Child log router") --

FLOOD_LINES = 100_000
FLOOD_ERROR_EVERY = 1_000
# Measured at 1.6 s for this flood on a Windows dev machine; the redact-then-cap order took 199 s.
FLOOD_BUDGET_SECONDS = 10.0
SECRET = "supersecretvalue123"


def _ticking_clock() -> Callable[[], float]:
    """One second later at every read, so the rate bucket always refills and only the ceilings bite."""
    now = [0.0]

    def tick() -> float:
        now[0] += 1.0
        return now[0]

    return tick


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Every payload the router hands either sink, in order."""
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(router, "_default_stdout_sink", sent.append)
    monkeypatch.setattr(router, "_default_stderr_sink", sent.append)
    return sent


def _child_output_lines(child: ChildLogRouter) -> list[object]:
    return [line.payload.get("line") for line in child.routed if line.payload.get("event") == events.EVENT_CHILD_OUTPUT]


def _exhaust_rate_bucket(child: ChildLogRouter, *, leave: int = 0) -> None:
    for index in range(int(router._BURST_CAPACITY) - leave):
        child.route_line("stdout", f"filler {index}".encode())


def _raise(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("boom")


def test_a_100k_line_flood_is_fast_emits_only_the_ceiling_and_keeps_every_error_line(
    monkeypatch: pytest.MonkeyPatch, dispatched: list[dict[str, object]]
) -> None:
    monkeypatch.setenv("OPEN_METEO_API_KEY", SECRET)
    child = ChildLogRouter(attempt_id="flood", clock=_ticking_clock())
    error_indexes = list(range(0, FLOOD_LINES, FLOOD_ERROR_EVERY))

    started = time.perf_counter()
    for index in range(FLOOD_LINES):
        if index % FLOOD_ERROR_EVERY == 0:
            child.route_line("stderr", _line({"event": "flood_step_failed", "level": "error", "index": index}))
        elif index % 2:
            child.route_line("stdout", _line({"event": "flood_progress", "index": index}))
        else:
            child.route_line("stdout", f"progress {index} of the flood".encode())
    elapsed = time.perf_counter() - started

    assert elapsed < FLOOD_BUDGET_SECONDS, f"the router took {elapsed:.1f}s over {FLOOD_LINES} lines"
    levels = Counter(line.level for line in child.routed)
    assert levels["info"] == router._MAX_NON_ERROR_LINES_PER_ATTEMPT
    assert [payload["index"] for payload in dispatched if payload.get("level") == "error"] == error_indexes
    truncations = [payload for payload in dispatched if payload["event"] == events.EVENT_CHILD_LOG_TRUNCATED]
    assert len(truncations) == 1
    assert len(dispatched) == len(child.routed) == levels["info"] + len(error_indexes) + 1
    assert child.log_lines_dropped == FLOOD_LINES - levels["info"] - len(error_indexes)


def test_a_secret_on_a_line_past_the_ceiling_is_never_redacted_or_emitted(
    monkeypatch: pytest.MonkeyPatch, dispatched: list[dict[str, object]]
) -> None:
    monkeypatch.setenv("OPEN_METEO_API_KEY", SECRET)
    child = ChildLogRouter(attempt_id="attempt-1", clock=_ticking_clock())
    for index in range(router._MAX_NON_ERROR_LINES_PER_ATTEMPT):
        child.route_line("stdout", f"filler {index}".encode())
    for index in range(router._MAX_ERROR_LINES_PER_ATTEMPT):
        child.route_line("stderr", f"error filler {index}".encode())
    assert child.log_lines_dropped == 0

    redaction_calls: list[str] = []
    for name in ("exact_value_scrub", "redact_for_log", "redact_value"):
        original = getattr(redaction, name)

        def spy(
            *args: object, _name: str = name, _original: Callable[..., object] = original, **kwargs: object
        ) -> object:
            redaction_calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(redaction, name, spy)

    over_the_ceiling: list[tuple[router.Stream, bytes]] = [
        ("stdout", f"plain leak {SECRET}".encode()),
        ("stdout", _line({"event": "x", "level": "info", "detail": SECRET, SECRET: 1})),
        ("stderr", f"Traceback leak {SECRET}".encode()),
        ("stderr", _line({"event": "y", "level": "error", "url": f"https://h/?apikey={SECRET}"})),
    ]
    for stream, raw in over_the_ceiling:
        child.route_line(stream, raw)

    assert child.log_lines_dropped == len(over_the_ceiling)
    assert redaction_calls == [], "a line the ceiling drops is never redacted"
    assert SECRET not in json.dumps([line.payload for line in child.routed]) + json.dumps(dispatched)
    assert [payload["event"] for payload in dispatched].count(events.EVENT_CHILD_LOG_TRUNCATED) == 1


def test_an_sql_block_carried_across_dropped_lines_still_cuts_the_next_surviving_line(
    dispatched: list[dict[str, object]],
) -> None:
    clock = [0.0]
    child = ChildLogRouter(attempt_id="attempt-1", clock=lambda: clock[0])
    _exhaust_rate_bucket(child, leave=1)
    child.route_line("stdout", b"psycopg.errors.DataError: bad value [SQL: SELECT * FROM t WHERE k = %(k)s")
    dropped_continuations = [b"  AND owner = 'DROPPED-SQL-BODY-1'", b"  AND api_token = 'DROPPED-SQL-BODY-2'"]
    for raw in dropped_continuations:
        child.route_line("stdout", raw)
    assert child.log_lines_dropped == len(dropped_continuations)
    clock[0] += 10.0  # the bucket refills; the block is still open
    child.route_line("stdout", b"  AND password = 'SURVIVING-SQL-BODY-3'")
    child.route_line("stdout", b"Traceback (most recent call last):")
    child.route_line("stdout", b"after the block")

    opened, continued, closed, after = _child_output_lines(child)[-4:]
    assert opened == f"psycopg.errors.DataError: bad value {redaction.SQL_REDACTED_PLACEHOLDER}"
    assert continued == redaction.SQL_REDACTED_PLACEHOLDER
    assert (closed, after) == ("Traceback (most recent call last):", "after the block")
    rendered = json.dumps(dispatched)
    for marker in ("DROPPED-SQL-BODY-1", "DROPPED-SQL-BODY-2", "SURVIVING-SQL-BODY-3", "SELECT"):
        assert marker not in rendered


def test_an_sql_block_opened_on_a_dropped_line_still_cuts_the_next_surviving_line(
    dispatched: list[dict[str, object]],
) -> None:
    clock = [0.0]
    child = ChildLogRouter(attempt_id="attempt-1", clock=lambda: clock[0])
    _exhaust_rate_bucket(child)
    child.route_line("stdout", b"psycopg.errors.UniqueViolation: duplicate key [SQL: INSERT INTO t VALUES (%(v)s)")
    assert child.log_lines_dropped == 1
    clock[0] += 10.0
    child.route_line("stdout", b"  VALUES ('SURVIVING-AFTER-A-DROPPED-OPEN')")

    assert _child_output_lines(child)[-1] == redaction.SQL_REDACTED_PLACEHOLDER
    assert "SURVIVING-AFTER-A-DROPPED-OPEN" not in json.dumps(dispatched)


def test_the_byte_ceiling_is_charged_with_the_redacted_size_not_the_raw_one(
    monkeypatch: pytest.MonkeyPatch, dispatched: list[dict[str, object]]
) -> None:
    """Redaction can GROW a line -- an 8-character secret becomes `[redacted:OPEN_METEO_API_KEY]` --
    so the pre-redaction size only pre-checks the ceiling; what is charged is what is emitted."""
    short_secret = "s3cretv1"
    monkeypatch.setenv("OPEN_METEO_API_KEY", short_secret)
    child = ChildLogRouter(attempt_id="attempt-1", clock=_ticking_clock())
    repeated = " ".join([short_secret] * 4)
    raw_lines = [
        _line({"event": "grows", "level": "info", **{f"k{k}": repeated for k in range(300)}}) for _ in range(60)
    ]
    assert sum(len(raw) for raw in raw_lines) < router._MAX_TOTAL_BYTES_PER_ATTEMPT, "the raw sizes alone would all fit"

    for raw in raw_lines:
        child.route_line("stdout", raw)

    emitted_sizes = [
        len(json.dumps(payload, default=str).encode("utf-8")) for payload in dispatched if payload["event"] == "grows"
    ]
    assert sum(emitted_sizes) <= router._MAX_TOTAL_BYTES_PER_ATTEMPT
    assert sum(emitted_sizes) > router._MAX_TOTAL_BYTES_PER_ATTEMPT - max(emitted_sizes), "filled, not undershot"
    assert child.log_lines_dropped == len(raw_lines) - len(emitted_sizes)
    assert short_secret not in json.dumps(dispatched)


def test_a_redaction_fault_after_admission_falls_back_without_charging_the_line_twice(
    monkeypatch: pytest.MonkeyPatch, dispatched: list[dict[str, object]]
) -> None:
    monkeypatch.setenv("OPEN_METEO_API_KEY", SECRET)
    monkeypatch.setattr(router.redaction, "redact_value", _raise)
    child = ChildLogRouter(attempt_id="attempt-1", clock=_ticking_clock())
    for index in range(router._MAX_NON_ERROR_LINES_PER_ATTEMPT + 1):
        child.route_line("stdout", _line({"event": "x", "level": "info", "index": index, "detail": SECRET}))

    fallen_back = [payload for payload in dispatched if "line" in payload]
    assert len(fallen_back) == router._MAX_NON_ERROR_LINES_PER_ATTEMPT, "each line took exactly one ceiling slot"
    assert child.log_lines_dropped == 1
    assert SECRET not in json.dumps(dispatched)


def test_a_fault_inside_an_open_sql_block_falls_back_to_the_cut_not_the_raw_line(
    monkeypatch: pytest.MonkeyPatch, dispatched: list[dict[str, object]]
) -> None:
    child = _make()
    child.route_line("stderr", b"psycopg.errors.DataError: bad value [SQL: SELECT 1")
    monkeypatch.setattr(router.redaction, "truncate_leaf", _raise)
    child.route_line("stderr", b"  WHERE owner = 'INSIDE-THE-BLOCK'")

    assert child.routed[-1].payload["line"] == redaction.SQL_REDACTED_PLACEHOLDER
    assert "INSIDE-THE-BLOCK" not in json.dumps(dispatched)
