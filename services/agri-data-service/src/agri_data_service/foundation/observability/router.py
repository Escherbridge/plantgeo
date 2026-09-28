"""`ChildLogRouter`: reassembles, bounds, levels, redacts and routes one child process's raw output.

See `foundation/observability/AGENTS.md` "Child log router" for the bound table and the residual
judgment calls this module makes where the design record leaves a gap. **This module is not wired to
any real child process yet** -- `o5a-executor-observability` (GL-3) does that; it exists now so its
shape is settled and independently testable first (plan 0W.1 GL-1). Pure: stdlib plus
`foundation.observability` only, matching `redaction.py`'s own promise
(`tests/test_layer_import_contract.py::LAYER_FORBIDDEN_IMPORTS["foundation"]`).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Literal, NamedTuple

from agri_data_service.foundation.observability import events, redaction

if TYPE_CHECKING:
    from collections.abc import Callable

Stream = Literal["stdout", "stderr"]

# --- Volume bounds (design §1.6, §1.8) -------------------------------------------------------------

_REASSEMBLY_CAP_BYTES: Final = 64 * 1024
_JSON_LINE_MAX_BYTES: Final = 64 * 1024
_NON_JSON_LINE_TRUNCATE_BYTES: Final = 16 * 1024
_STUB_PREVIEW_BYTES: Final = 1024
_SUSTAINED_RATE_PER_SECOND: Final = 50.0
_BURST_CAPACITY: Final = 200.0

# Per-attempt ceilings, independent of the rate bucket above (design §1.6 point 4/§1.8; security
# review): the rate bucket alone let every non-JSON `error`-defaulted stderr line -- a progress bar,
# GDAL chatter, a multi-line traceback with no `level` of its own -- bypass throttling entirely,
# because `level == "error"` is exempt from the bucket. These ceilings still let the exempt kinds
# (a genuine `error` line, the report, `lane_turn`) through up to their own cap, then drop.
_MAX_NON_ERROR_LINES_PER_ATTEMPT: Final = 1000
_MAX_ERROR_LINES_PER_ATTEMPT: Final = 200
_MAX_DEBUG_LINES_PER_ATTEMPT: Final = 500
_MAX_TOTAL_BYTES_PER_ATTEMPT: Final = 1024 * 1024

# The stateful SQL-block cut's markers (security review; see `_apply_sql_block_state`).
_SQL_BLOCK_MARKER_TEXT: Final = "[SQL: "
_PARAMETERS_LINE_PREFIX: Final = "[parameters:"

# Events the rate bucket never drops, alongside any `level == "error"` line (design §1.6 point 4:
# "Error lines, the report and `lane_turn` are never dropped").
_NEVER_DROPPED_EVENTS: Final = frozenset({events.EVENT_LANE_TURN_REPORT, events.EVENT_JOB_EXECUTOR_LANE_TURN})

# A small, explicit table for legacy lines that carry no `level` field of their own (design §1.6
# point 2, "a small `LEGACY_LEVEL_OVERRIDES` table"), sourced from design §1.4's "Existing executor
# events" list. Extend it, never repurpose an entry -- the same append-only rule `vocabulary.py`
# documents for its own tables.
LEGACY_LEVEL_OVERRIDES: Final[dict[str, str]] = {
    "tick_started": "debug",
    "leader_acquired": "debug",
    "leader_not_acquired": "debug",
    "tick_healthy": "debug",
    "tick_unhealthy": "warn",
    events.EVENT_REPAIR_AUTHORING_FAILED: "error",
}

# The suffix rule (design §1.6 point 2) for legacy lane events that carry no `level`. Checked after
# `LEGACY_LEVEL_OVERRIDES`, before the stream default. Without it, a lane's success line printed to
# stderr (`soil_forward_complete`, `drought_forward_started`) reaches Railway as an error; that was
# the first live GL-3 read, 2026-09-28. Longest-first order is not needed: no suffix ends another.
_LEVEL_BY_EVENT_SUFFIX: Final[tuple[tuple[str, str], ...]] = (
    ("_failed", "error"),
    ("_error", "error"),
    ("_pause", "warn"),
    ("_deferred", "warn"),
    ("_refused", "warn"),
    ("_retry", "warn"),
    ("_complete", "info"),
    ("_completed", "info"),
    ("_started", "info"),
    ("_finished", "info"),
    ("_published", "info"),
    ("_settled", "info"),
    ("_skipped", "info"),
)

_THIRD_PARTY_WARNING_PREFIX: Final = re.compile(r"^[\w.]+Warning: |^Warning \d+: ")
_ROUTING_ENV_VAR: Final = "PLANTGEO_LOG_ROUTING"

# Kept in sync with `logging.py::_TURN_ENV_TO_FIELD` by hand -- duplicated rather than imported.
# Both modules are peer siblings inside `foundation.observability` (a same-package import would be
# legal even under the layer-import test), but reaching across into a sibling module's private
# constant is a worse coupling than five duplicate lines; see this module's AGENTS.md entry.
_TURN_ENV_TO_FIELD: Final[dict[str, str]] = {
    "PLANTGEO_TURN_ID": "turn_id",
    "PLANTGEO_LANE_ID": "lane",
    "PLANTGEO_TURN_MODE": "mode",
    "PLANTGEO_ATTEMPT": "attempt",
    "PLANTGEO_TURN_BUCKET": "shard_key",
    "PLANTGEO_TURN_PROBE": "probe",
}


def _routing_enabled_from_environment() -> bool:
    """`PLANTGEO_LOG_ROUTING=off` disables re-leveling and turn-context rewriting only; redaction stays on."""
    return os.environ.get(_ROUTING_ENV_VAR, "").strip().casefold() != "off"


def _legacy_level(parsed: dict[str, object]) -> str | None:
    """The level a legacy, unlevelled child line earns: override table, suffix rule, `status: failed`."""
    event = parsed.get("event")
    if isinstance(event, str):
        if event in LEGACY_LEVEL_OVERRIDES:
            return LEGACY_LEVEL_OVERRIDES[event]
        for suffix, level in _LEVEL_BY_EVENT_SUFFIX:
            if event.endswith(suffix):
                return level
    # Every direct lane's `main()` prints `{"status": "failed", "error": ...}` with no event (R1).
    if parsed.get("status") == "failed":
        return "error"
    return None


def _default_level_for_stream(stream: Stream) -> str:
    """Railway's own unlevelled-line rule (AGENTS.md "Railway logging facts"), applied before Railway sees it."""
    return "error" if stream == "stderr" else "info"


# Duplicated from `logging.py` rather than imported -- the same sibling-module rule
# `_TURN_ENV_TO_FIELD` above already documents (security review: every routed line should carry the
# same `service`/`deploy` envelope a first-party structlog line does, per FR-30).
def _service_name() -> str:
    return os.environ.get("RAILWAY_SERVICE_NAME", "agri-data-service")


def _iso_timestamp() -> str:
    return datetime.now(UTC).isoformat()


# --- Sinks, looked up by name when called (mirrors `logging.py::StreamSink`'s late binding) --------


def _default_stdout_sink(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()


def _default_stderr_sink(payload: dict[str, object]) -> None:
    sys.stderr.write(json.dumps(payload, default=str) + "\n")
    sys.stderr.flush()


def _dispatch_to_stream(stream: Stream, payload: dict[str, object]) -> None:
    # Module-level lookup at call time, not a bound reference captured at import -- so a test's
    # `monkeypatch.setattr(router, "_default_stdout_sink", ...)` is honoured on the next line.
    if stream == "stdout":
        _default_stdout_sink(payload)
    else:
        _default_stderr_sink(payload)


class RoutedLine(NamedTuple):
    """One decision the router made about a raw line: where it went, and at what level."""

    stream: Stream
    level: str
    payload: dict[str, object]


class _Admission(NamedTuple):
    """One line's verdict, reached from pre-redaction facts before any redaction runs."""

    admitted: bool
    level: str
    # False for the never-dropped kinds and the stubs: they skip every ceiling, the byte one included.
    capped: bool


_ALWAYS_ADMITTED_WARN: Final = _Admission(admitted=True, level="warn", capped=False)


class _LineInFlight:
    """What `route_line` has already decided about one line, for `_fallback_redact` to reuse."""

    __slots__ = ("admission", "raw_size", "text")

    def __init__(self, raw_size: int) -> None:
        self.raw_size = raw_size
        self.admission: _Admission | None = None
        # The non-JSON line after `_apply_sql_block_state`, once computed.
        self.text: str | None = None


def _ceiling_bucket(level: str) -> str:
    if level == "debug":
        return "debug"
    return "error" if level == "error" else "non_error"


def _encoded_length(payload: dict[str, object]) -> int:
    try:
        return len(json.dumps(payload, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


class _UsagePidState:
    """One pid's usage-open/usage-close pairing (design §1.6 point 2, last sentence)."""

    __slots__ = ("closed", "last_send_at", "last_send_outcome", "opened")

    def __init__(self) -> None:
        self.opened = False
        self.closed = False
        self.last_send_outcome: object = None
        self.last_send_at: float | None = None


class _RateBucket:
    """Token bucket: 50 lines/s sustained, burst 200, per attempt (design §1.6 point 4)."""

    def __init__(self, *, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._tokens = _BURST_CAPACITY
        self._last = clock()

    def allow(self) -> bool:
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(_BURST_CAPACITY, self._tokens + elapsed * _SUSTAINED_RATE_PER_SECOND)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class ChildLogRouter:
    """Bounds, levels, redacts and routes one attempt's child stdout/stderr, byte-reassembled.

    Test seam: `route_line(stream, raw)` processes exactly one already-newline-delimited line, for
    tests that do not care about byte-chunk reassembly; `feed(stream, chunk)` is the real entry point
    a real child's `CommandOutputTail` tee (GL-3) will call with arbitrary read-sized chunks.
    """

    def __init__(self, *, attempt_id: str, clock: Callable[[], float] | None = None) -> None:
        self._attempt_id = attempt_id
        self._clock = clock or time.monotonic
        self._buffers: dict[Stream, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
        self._rate = _RateBucket(clock=self._clock)
        self._usage_by_pid: dict[int, _UsagePidState] = {}
        self._drop_notice_emitted = False
        self.log_lines_dropped = 0
        self.routing_enabled = _routing_enabled_from_environment()
        self.routed: list[RoutedLine] = []
        # Per-attempt ceilings' running counters, and the in-progress SQL-block cut state per stream
        # (design §1.6/§1.8; security review -- see `_admit`, `_emit` and `_apply_sql_block_state`).
        self._level_line_counts: dict[str, int] = {"debug": 0, "error": 0, "non_error": 0}
        self._total_bytes = 0
        self._sql_block_open: dict[Stream, bool] = {"stdout": False, "stderr": False}

    # --- Byte reassembly (design §1.6 point 1) -----------------------------------------------------

    def feed(self, stream: Stream, chunk: bytes) -> None:
        buffer = self._buffers[stream]
        buffer.extend(chunk)
        while True:
            newline_index = buffer.find(b"\n")
            if newline_index == -1:
                if len(buffer) > _REASSEMBLY_CAP_BYTES:
                    raw = bytes(buffer)
                    del buffer[:]
                    self._emit_output_stub(stream, raw)
                break
            raw = bytes(buffer[:newline_index])
            del buffer[: newline_index + 1]
            self.route_line(stream, raw)

    def flush(self) -> None:
        """Force out any buffered partial line as a stub, at end of stream."""
        for stream in ("stdout", "stderr"):
            buffer = self._buffers[stream]
            if buffer:
                raw = bytes(buffer)
                del buffer[:]
                self._emit_output_stub(stream, raw)

    def route_line(self, stream: Stream, raw: bytes) -> None:
        """Process exactly one already-delimited line (the test seam; `feed` calls it per line)."""
        if not raw.strip():
            return
        line = _LineInFlight(raw_size=len(raw))
        try:
            self._route_line_unguarded(stream, raw, line)
        except Exception:  # a structured-path fault must never drop redaction
            self._fallback_redact(stream, raw, line)

    def _route_line_unguarded(self, stream: Stream, raw: bytes, line: _LineInFlight) -> None:
        if len(raw) > _JSON_LINE_MAX_BYTES:
            self._emit_output_stub(stream, raw)
            return
        text = raw.decode("utf-8", errors="replace")
        parsed = self._try_parse_json(text)
        if isinstance(parsed, dict):
            self._handle_json_object(stream, parsed, line)
            return
        self._handle_non_json_line(stream, text, line)

    def _fallback_redact(self, stream: Stream, raw: bytes, line: _LineInFlight) -> None:
        try:
            # Reuse a verdict the structured path already charged, so a fault after admission is
            # never counted twice; admit here only when the fault came first.
            admission = line.admission
            if admission is None:
                size_estimate = min(line.raw_size, _NON_JSON_LINE_TRUNCATE_BYTES)
                admission = self._admit(
                    _default_level_for_stream(stream), never_dropped=False, size_estimate=size_estimate
                )
            if not admission.admitted:
                return
            if line.text is not None:  # the SQL-block cut already ran for this line: honour it
                bounded = line.text.encode("utf-8")[:_NON_JSON_LINE_TRUNCATE_BYTES]
            else:
                bounded = raw[:_NON_JSON_LINE_TRUNCATE_BYTES]
            redacted = redaction.redact_for_log(bounded.decode("utf-8", errors="replace"))
            payload: dict[str, object] = {
                "event": events.EVENT_CHILD_OUTPUT,
                "level": admission.level,
                "attempt": self._attempt_id,
                "stream": stream,
                "line": redacted,
            }
            self._emit(stream, admission, payload)
        except Exception:  # the absolute last resort: drop it, counted
            self.log_lines_dropped += 1

    @staticmethod
    def _try_parse_json(text: str) -> object | None:
        try:
            parsed: object = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
        return parsed

    # --- JSON objects (design §1.6 point 2) --------------------------------------------------------

    def _handle_json_object(self, stream: Stream, parsed: dict[str, object], line: _LineInFlight) -> None:
        event = parsed.get("event")
        if event in (events.EVENT_TURN_USAGE_OPEN, events.EVENT_TURN_USAGE):
            self._consume_usage_line(parsed)
            return
        if self.routing_enabled:
            self._fill_turn_context(parsed)
            self._assign_level(parsed, stream)
        else:
            # `PLANTGEO_LOG_ROUTING=off` disables re-leveling (the `LEGACY_LEVEL_OVERRIDES` table)
            # and turn-context rewriting only -- it must not leave the line with NO `level` at all,
            # which broke FR-30's "every line has event/level/timestamp/service" for every routed
            # line while routing was off (the one failing sweep test; security review agreed this is
            # a code fix, not a test fix: an audit line missing its own level is a correctness bug
            # independent of what any test happens to assert).
            parsed.setdefault("level", _default_level_for_stream(stream))
        level = str(parsed.get("level") or _default_level_for_stream(stream))
        never_dropped = isinstance(event, str) and event in _NEVER_DROPPED_EVENTS
        admission = line.admission = self._admit(level, never_dropped=never_dropped, size_estimate=line.raw_size)
        if not admission.admitted:
            return
        # `leaf_limit` bounds each JSON string LEAF to the same 8 KiB the per-leaf structlog pipeline
        # uses, before the regex scrub -- previously only the whole 64 KiB LINE was bounded, so a
        # single oversized string leaf still reached the regex scrubs unbounded (security review).
        redacted = redaction.redact_value(parsed, leaf_limit=redaction.LEAF_TRUNCATE_BYTES)
        if not isinstance(redacted, dict):  # pragma: no cover - redact_value(dict) always returns dict
            redacted = parsed
        redacted.setdefault("event", event if event is not None else events.EVENT_CHILD_OUTPUT)
        self._emit(stream, admission, redacted)

    def _fill_turn_context(self, parsed: dict[str, object]) -> None:
        for env_name, field in _TURN_ENV_TO_FIELD.items():
            if field in parsed:
                continue
            value = os.environ.get(env_name)
            if value:
                parsed[field] = value

    def _assign_level(self, parsed: dict[str, object], stream: Stream) -> None:
        if parsed.get("level"):
            return
        parsed["level"] = _legacy_level(parsed) or _default_level_for_stream(stream)

    def _consume_usage_line(self, parsed: dict[str, object]) -> None:
        pid = parsed.get("pid")
        if not isinstance(pid, int):
            return
        state = self._usage_by_pid.setdefault(pid, _UsagePidState())
        if parsed.get("event") == events.EVENT_TURN_USAGE_OPEN:
            state.opened = True
            return
        state.closed = True
        # A raw child usage line is otherwise never redacted before it flows into the parent's
        # attempt report (security review): `outcome` is a free-text description of a send result
        # and can carry a keyed URL (an httpx error message, say).
        outcome = redaction.redact_value(parsed.get("last_send_outcome"))
        sent_at = parsed.get("last_send_at")
        sent_at_value = sent_at if isinstance(sent_at, (int, float)) else 0.0
        if outcome is not None and (state.last_send_at is None or sent_at_value >= state.last_send_at):
            state.last_send_outcome = outcome
            state.last_send_at = sent_at_value

    def usage_summary(self) -> dict[str, object]:
        """The attempt-level rollup a report writer reads: `usage_complete` and the winning outcome."""
        unclosed = [pid for pid, state in self._usage_by_pid.items() if state.opened and not state.closed]
        winning_outcome: object = None
        winning_at: float | None = None
        for state in self._usage_by_pid.values():
            is_later = winning_at is None or (state.last_send_at or 0.0) >= winning_at
            if state.last_send_outcome is not None and is_later:
                winning_outcome = state.last_send_outcome
                winning_at = state.last_send_at
        return {
            "usage_complete": not unclosed,
            "usage_incomplete_pids": unclosed,
            "last_send_outcome": winning_outcome,
        }

    # --- Non-JSON lines (design §1.6 point 3) ------------------------------------------------------

    def _apply_sql_block_state(self, stream: Stream, text: str) -> str:
        """Carry the `[SQL: ` cut across lines: `feed` splits on '\\n' before `redact_for_log` ever
        sees the text, so a multi-line traceback's `[SQL: ...]`/`[parameters: ...]` body -- each on
        its own line -- previously only had the FIRST of those lines redacted (security review:
        verified a bound-parameter dict on the line after `[SQL: ` survived verbatim). Once a line
        carries the marker, every following line of the SAME stream is replaced outright until a
        blank line or the start of a new record/traceback ends the block. A `[parameters:` line is
        always replaced even outside an open block, since the driver sometimes emits it standalone.
        """
        stripped = text.strip()
        if self._sql_block_open[stream]:
            ends_block = not stripped or stripped.startswith(("Traceback", "{", "(Background on this error"))
            if ends_block:
                self._sql_block_open[stream] = False
            else:
                return redaction.SQL_REDACTED_PLACEHOLDER
        if _SQL_BLOCK_MARKER_TEXT in text:
            self._sql_block_open[stream] = True
        if stripped.startswith(_PARAMETERS_LINE_PREFIX):
            return redaction.SQL_REDACTED_PLACEHOLDER
        return text

    def _handle_non_json_line(self, stream: Stream, text: str, line: _LineInFlight) -> None:
        # Every line, dropped or not, passes through the SQL-block state first and in order, so a
        # dropped line can neither leak into nor end the cut for a later surviving one.
        text = line.text = self._apply_sql_block_state(stream, text)
        # Levelled from the unredacted text: the prefix is anchored at the start of the line, and
        # `endpos` holds the match to the same 16 KiB bound the emitted line is cut to.
        if self.routing_enabled and _THIRD_PARTY_WARNING_PREFIX.match(text, 0, _NON_JSON_LINE_TRUNCATE_BYTES):
            level = "warn"
        else:
            level = _default_level_for_stream(stream)
        size_estimate = min(len(text), _NON_JSON_LINE_TRUNCATE_BYTES)
        admission = line.admission = self._admit(level, never_dropped=False, size_estimate=size_estimate)
        if not admission.admitted:
            return
        # Pinned order (design §1.6 point 1): exact-value scrub over the whole line first, so a
        # secret that straddles the 16 KiB cut is already gone before the cut can leave its prefix
        # exposed; then the token-safe truncation; then the remaining regex-based scrubs.
        scrubbed = redaction.exact_value_scrub(text)
        truncated = redaction.truncate_leaf(scrubbed, limit=_NON_JSON_LINE_TRUNCATE_BYTES)
        redacted = redaction.redact_for_log(truncated)
        # Both streams now carry the same shape (`event`, `stream`) -- a forwarded stderr line
        # previously had no `event` at all, breaking FR-30's envelope (security review).
        payload: dict[str, object] = {
            "event": events.EVENT_CHILD_OUTPUT,
            "level": level,
            "attempt": self._attempt_id,
            "stream": stream,
            "line": redacted,
        }
        self._emit(stream, admission, payload)

    def _emit_output_stub(self, stream: Stream, raw: bytes) -> None:
        # Same pinned order as `_handle_non_json_line`: scrub the whole decoded line before slicing
        # to the 1 KiB preview, so a secret straddling the slice point never leaks its prefix.
        text = raw.decode("utf-8", errors="replace")
        scrubbed = redaction.exact_value_scrub(text)
        preview_source = redaction.truncate_leaf(scrubbed, limit=_STUB_PREVIEW_BYTES)
        preview = redaction.redact_for_log(preview_source)
        payload = {
            "event": events.EVENT_CHILD_OUTPUT,
            "level": "warn",
            "attempt": self._attempt_id,
            "bytes": len(raw),
            "stream": stream,
            "preview": preview,
        }
        self._emit(stream, _ALWAYS_ADMITTED_WARN, payload)

    # --- Admission, per-attempt ceilings and dispatch (design §1.6 point 4/5; §1.8) -----------------
    # Admission runs BEFORE redaction, so a dropped line costs no redaction at all; see AGENTS.md
    # "Child log router", "Admission before redaction".

    def _admit(self, level: str, *, never_dropped: bool, size_estimate: int) -> _Admission:
        """Charge the per-attempt ceilings and the rate bucket from pre-redaction facts only.

        Order: the byte ceiling (a pre-check -- the bytes actually emitted so far plus this line's
        pre-redaction size, charged later in `_emit` with the real size), then the per-level line
        ceiling (charged), then the rate bucket (charged; `error` is exempt from it). The ceilings are
        independent of, and stricter than, the rate bucket: an `error`-level line skips the bucket but
        is still capped at `_MAX_ERROR_LINES_PER_ATTEMPT` (security review: unthrottled
        `error`-defaulted stderr chatter was the exact gap the bucket's own error exemption opened).
        """
        if never_dropped:
            return _Admission(admitted=True, level=level, capped=False)
        admitted = (
            self._total_bytes + size_estimate <= _MAX_TOTAL_BYTES_PER_ATTEMPT
            and self._charge_line_ceiling(level)
            and (level == "error" or self._rate.allow())
        )
        if not admitted:
            self._count_drop()
        return _Admission(admitted=admitted, level=level, capped=True)

    def _charge_line_ceiling(self, level: str) -> bool:
        bucket = _ceiling_bucket(level)
        limit = {
            "debug": _MAX_DEBUG_LINES_PER_ATTEMPT,
            "error": _MAX_ERROR_LINES_PER_ATTEMPT,
            "non_error": _MAX_NON_ERROR_LINES_PER_ATTEMPT,
        }[bucket]
        if self._level_line_counts[bucket] >= limit:
            return False
        self._level_line_counts[bucket] += 1
        return True

    def _emit(self, stream: Stream, admission: _Admission, payload: dict[str, object]) -> None:
        """Stamp the envelope, enforce the byte ceiling on the REDACTED size, and dispatch."""
        payload.setdefault("timestamp", _iso_timestamp())
        payload.setdefault("service", _service_name())
        payload.setdefault("deploy", os.environ.get("RAILWAY_DEPLOYMENT_ID"))
        if admission.capped:
            encoded_len = _encoded_length(payload)
            if self._total_bytes + encoded_len > _MAX_TOTAL_BYTES_PER_ATTEMPT:
                self._count_drop()
                return
            self._total_bytes += encoded_len
        self.routed.append(RoutedLine(stream=stream, level=admission.level, payload=payload))
        _dispatch_to_stream(stream, payload)

    def _count_drop(self) -> None:
        self.log_lines_dropped += 1
        self._maybe_emit_drop_notice()

    def _maybe_emit_drop_notice(self) -> None:
        if self._drop_notice_emitted:
            return
        self._drop_notice_emitted = True
        notice = {
            "event": events.EVENT_CHILD_LOG_TRUNCATED,
            "level": "warn",
            "attempt": self._attempt_id,
            "dropped": self.log_lines_dropped,
            "timestamp": _iso_timestamp(),
            "service": _service_name(),
            "deploy": os.environ.get("RAILWAY_DEPLOYMENT_ID"),
        }
        self.routed.append(RoutedLine(stream="stderr", level="warn", payload=notice))
        _dispatch_to_stream("stderr", notice)
