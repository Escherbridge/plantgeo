"""The S5 turn report (the last stdout line, event `plantgeo_lane_turn_report`, no `level`) and the turn's bounded log.

`TurnReportBuilder.to_payload` refuses to serialise until the turn has said what it left unwritten
(or declared that unknown); `__main__` writes the line from `finally`. See
`pipeline/runner/AGENTS.md` "The S5 report".
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.observability import events, redaction
from agri_data_service.foundation.observability import usage as usage_meter
from agri_data_service.foundation.observability.logging import get_logger
from agri_data_service.pipeline.runner.exits import (
    EXIT_COMPLETED,
    EXIT_CONFIGURATION_ERROR,
    EXIT_INTERNAL_ERROR,
    EXIT_UPSTREAM_UNAVAILABLE,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from agri_data_service.pipeline.runner.contract import TurnMode, UnwrittenReason

REPORT_VERSION: Final = 1
#: The report must fit the executor's 64 KiB stdout tail, so entries are capped and counted.
REPORT_UNWRITTEN_ENTRY_LIMIT: Final = 100
REPORT_DETAIL_CHARS: Final = 200
#: FR-38: at most this many info (and warn) lines per turn; errors are never dropped.
TURN_LOG_LINE_LIMIT: Final = 200
_METER_COUNTERS: Final = (
    "http_requests",
    "http_2xx",
    "http_3xx",
    "http_4xx",
    "http_429",
    "http_5xx",
    "transport_failures",
    "bytes_in",
    "backoff_seconds",
    "weighted_calls_metered",
)
_LOGGER_NAME: Final = "agri_data_service.pipeline.runner"


class ReportIncompleteError(RuntimeError):
    """The report was asked to serialise before the turn said what it left unwritten."""


@dataclass(frozen=True, slots=True)
class UnwrittenEntry:
    """One owed stream-day the turn did not write, and why (S5)."""

    day: date
    reason: UnwrittenReason
    detail: str = ""
    stream: str | None = None
    #: The alarm counts only days behind the provider edge (S5).
    behind_edge: bool = True

    def to_dict(self) -> dict[str, object]:
        """The report's entry shape."""
        return {
            "day": self.day.isoformat(),
            "stream": self.stream,
            "reason": self.reason,
            "detail": self.detail[:REPORT_DETAIL_CHARS],
            "behind_edge": self.behind_edge,
        }


def meter_snapshot() -> dict[str, float]:
    """Sum this process's per-host meter (`ingest/http.py` hooks) into the report's `http_*` counters. Fails open."""
    totals = dict.fromkeys(_METER_COUNTERS, 0.0)
    try:
        hosts = getattr(usage_meter, "_host_counters", {})
        for entry in list(hosts.values()):
            for name in _METER_COUNTERS:
                value = entry.get(name)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    totals[name] += float(value)
    except Exception:  # the meter is fail-open everywhere; a report never breaks on it
        return dict.fromkeys(_METER_COUNTERS, 0.0)
    return totals


@dataclass(slots=True)
class TurnLog:
    """The turn's structured log: lane-scoped, bounded, counted into the report's `log_lines_*`."""

    lane: str
    mode: str
    limit: int = TURN_LOG_LINE_LIMIT
    counts: dict[str, int] = field(default_factory=lambda: {"debug": 0, "info": 0, "warn": 0, "error": 0})
    suppressed: int = 0

    def debug(self, event: str, **fields: object) -> None:
        """A debug line (day-level progress)."""
        self._emit("debug", event, fields)

    def info(self, event: str, **fields: object) -> None:
        """An info line; past the per-turn limit it is counted, not written."""
        self._emit("info", event, fields)

    def warn(self, event: str, **fields: object) -> None:
        """A warn line; past the per-turn limit it is counted, not written."""
        self._emit("warn", event, fields)

    def error(self, event: str, **fields: object) -> None:
        """An error line; never suppressed."""
        self._emit("error", event, fields)

    def _emit(self, level: str, event: str, fields: Mapping[str, object]) -> None:
        if level in {"debug", "info", "warn"} and self.counts[level] >= self.limit:
            self.suppressed += 1
            return
        self.counts[level] += 1
        logger = get_logger(_LOGGER_NAME)
        method = {"debug": logger.debug, "info": logger.info, "warn": logger.warning, "error": logger.error}[level]
        method(event, lane=self.lane, mode=self.mode, executor="config", **fields)


@dataclass(slots=True)
class TurnReportBuilder:
    """Accumulates one turn's S5 facts; nothing reaches stdout until `to_payload` agrees it is whole."""

    lane: str
    mode: TurnMode | str
    run_id: str
    turn_id: str | None = None
    strategy: str | None = None
    compare: bool = False
    republish_current: bool = False
    facts: dict[str, object] = field(default_factory=dict)
    #: Entries the turn has decided so far; `close_unwritten` or `mark_unwritten_unknown` freezes them.
    pending: list[UnwrittenEntry] = field(default_factory=list)
    unwritten: list[UnwrittenEntry] | None = None
    unwritten_known: bool = True
    meter_at_start: dict[str, float] = field(default_factory=meter_snapshot)

    def add_unwritten(self, entry: UnwrittenEntry) -> None:
        """Record one owed stream-day the turn will not write."""
        self.pending.append(entry)

    def close_unwritten(self) -> None:
        """The turn finished deciding: `unwritten` is the whole, known list. Required before serialising."""
        self.unwritten = list(self.pending)
        self.unwritten_known = True

    def mark_unwritten_unknown(self) -> None:
        """A turn that stopped early cannot vouch for its list: what it decided, and `unwritten_known = false`."""
        self.unwritten = list(self.pending)
        self.unwritten_known = False

    def set(self, **facts: object) -> None:
        """Record report facts by name."""
        self.facts.update(facts)

    def to_payload(self, *, exit_code: int, log: TurnLog | None = None) -> dict[str, object]:
        """The report object; raises `ReportIncompleteError` when `unwritten` was never stated."""
        if self.unwritten is None:
            raise ReportIncompleteError(
                f"lane {self.lane!r}: the report may not serialise before the turn states what it left unwritten"
            )
        ordered = sorted(self.unwritten, key=lambda entry: (not entry.behind_edge, entry.day, entry.stream or ""))
        by_reason: dict[str, int] = {}
        for entry in ordered:
            by_reason[entry.reason] = by_reason.get(entry.reason, 0) + 1
        behind = sorted(
            {(entry.day, entry.stream) for entry in ordered if entry.behind_edge},
            key=lambda identity: (identity[0], identity[1] is not None, identity[1] or ""),
        )
        meter_now = meter_snapshot()
        meter = {name: meter_now[name] - self.meter_at_start.get(name, 0.0) for name in _METER_COUNTERS}
        payload: dict[str, object] = {
            "event": events.EVENT_LANE_TURN_REPORT,
            "report_version": REPORT_VERSION,
            "lane": self.lane,
            "mode": self.mode,
            "executor": "config",
            "strategy": self.strategy,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "compare": self.compare,
            "republish_current": self.republish_current,
            "exit_code": exit_code,
            **self.facts,
            "days_unwritten": len({day for day, _ in behind}),
            "days_unwritten_total": len({entry.day for entry in ordered}),
            "unwritten": [entry.to_dict() for entry in ordered[:REPORT_UNWRITTEN_ENTRY_LIMIT]],
            "unwritten_truncated": len(ordered) > REPORT_UNWRITTEN_ENTRY_LIMIT,
            "unwritten_by_reason": by_reason,
            "unwritten_known": self.unwritten_known,
            "http_requests": int(meter["http_requests"]),
            "http_2xx": int(meter["http_2xx"]),
            "http_3xx": int(meter["http_3xx"]),
            "http_4xx": int(meter["http_4xx"]),
            "http_429": int(meter["http_429"]),
            "http_5xx": int(meter["http_5xx"]),
            "transport_failures": int(meter["transport_failures"]),
            "bytes_in": int(meter["bytes_in"]),
            "backoff_seconds": round(meter["backoff_seconds"], 3),
            "weighted_calls_metered": round(meter["weighted_calls_metered"], 3),
        }
        payload.setdefault("status", "completed" if exit_code == EXIT_COMPLETED else "failed")
        payload.setdefault("outcome", _outcome(exit_code, days_unwritten=len({day for day, _ in behind})))
        if log is not None:
            payload.update(
                {
                    "log_lines_debug": log.counts["debug"],
                    "log_lines_info": log.counts["info"],
                    "log_lines_warn": log.counts["warn"],
                    "log_lines_error": log.counts["error"],
                    "log_lines_suppressed": log.suppressed,
                }
            )
        return payload


#: S4 exit code -> the `vocabulary.TurnOutcome` word the report states.
_OUTCOME_BY_EXIT_CODE: Final[Mapping[int, str]] = {
    EXIT_UPSTREAM_UNAVAILABLE: "upstream_unavailable",
    EXIT_INTERNAL_ERROR: "code_error",
    EXIT_CONFIGURATION_ERROR: "config_error",
}


def _outcome(exit_code: int, *, days_unwritten: int) -> str:
    """`completed` or `incomplete` for exit 0 (S5: only days behind the edge count), else the exit's own word."""
    if exit_code == EXIT_COMPLETED:
        return "incomplete" if days_unwritten else "completed"
    return _OUTCOME_BY_EXIT_CODE.get(exit_code, "code_error")


def write_report_line(payload: Mapping[str, object]) -> None:
    """Write the report as one redacted JSON line to stdout, resolved now, and flush it."""
    redacted = redaction.redact_value(dict(payload))
    line = json.dumps(redacted, sort_keys=True, default=str)
    stream = sys.stdout
    stream.write(line + "\n")
    stream.flush()


__all__ = [
    "REPORT_UNWRITTEN_ENTRY_LIMIT",
    "REPORT_VERSION",
    "TURN_LOG_LINE_LIMIT",
    "ReportIncompleteError",
    "TurnLog",
    "TurnReportBuilder",
    "UnwrittenEntry",
    "meter_snapshot",
    "write_report_line",
]
