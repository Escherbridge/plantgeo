"""The frozen closed vocabulary Wave O's logging, metering and soft-failure slices share.

See `foundation/observability/AGENTS.md` "Vocabulary is frozen" for why this file exists and what
"frozen" means (append-only from GL-2 onward; nothing here is renamed or removed without a fresh
track). Values are pinned to the design record (`observability-wave-design.md` §3.1) and to
`conductor/tracks/config_driven_ingestion_20260926/metadata.json`'s `budget_headline`.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Literal, get_args

# Every `_LITERAL_NAME -> frozenset` pair below is derived with `get_args`, never hand-typed a
# second time: a hand-duplicated frozenset is exactly how `ExitClass` gained `report_missing` (design
# §3.1's "exit 0, no report -> `report_missing`" row) as a `TurnOutcome` member while the mirrored
# `EXIT_CLASSES` set silently stayed one entry behind it (security/code review). Each `Literal` is
# still spelled out by hand -- that is what a type checker reads -- but the RUNTIME membership set
# a caller checks against (`value in EXIT_CLASSES`) can now never drift from it.

# --- Log levels (Railway's four; see AGENTS.md "Railway logging facts") -----------------------

LogLevel = Literal["debug", "info", "warn", "error"]

LOG_LEVELS: Final[frozenset[LogLevel]] = frozenset(get_args(LogLevel))

# --- Exit classes and turn outcomes (design §3.1) ----------------------------------------------
#
# `exit_class` is what `execution/exit_classes.py::classify_exit` stamps (o2a, a later slice); it is
# observational only until GL-6/f1-executor. `infra` is new at Wave O: an upstream failure whose
# evidence names a network/database condition rather than the remote service itself. `report_missing`
# fills BOTH this column and `TurnOutcome` (design §3.1's "exit 0, no report" row, like `interrupted`
# already does for both) -- the earlier revision of this module had it only on `TurnOutcome`.

ExitClass = Literal["ok", "upstream", "infra", "code", "hang", "config", "interrupted", "lease_lost", "report_missing"]

EXIT_CLASSES: Final[frozenset[ExitClass]] = frozenset(get_args(ExitClass))

TurnOutcome = Literal[
    "completed",
    "incomplete",
    "report_missing",
    "upstream_unavailable",
    "infra_unavailable",
    "code_error",
    "timeout",
    "config_error",
    "interrupted",
    "lease_lost",
]

TURN_OUTCOMES: Final[frozenset[TurnOutcome]] = frozenset(get_args(TurnOutcome))

# --- Incident kinds (design §1.4, §3.2A/§3.3; fingerprints are f"{kind}:{lane}") ----------------
#
# `pool_saturated` is deliberately absent: WQ-4 declined the GL-5 pool brake and `POOL_BULK_LANES`
# for this wave (plan 0W.1 GL-1, "no POOL_BULK_LANES and no pool_saturated"). `budget_deferred` and
# `budget_basis_suspect` are likewise absent (G1's admission refusal, out of Wave O's scope). The
# five GL-5/Wave-O kinds design §3.3's table lists beyond o1's original set (`lane_blocked`,
# `lane_quarantined`, `executor_config`, `executor_repair_authoring`, `fleet`) are added here even
# though nothing in o1 opens them yet, so o2b (GL-5, in scope for this Wave) does not have to edit a
# vocabulary this module's own docstring calls frozen (code review).

IncidentKind = Literal[
    "lane_hold",
    "lane_incomplete",
    "lane_report_missing",
    "executor_lease_lost",
    "lane_repair_failing",
    "lane_plan_failed",
    "lane_blocked",
    "lane_quarantined",
    "executor_config",
    "executor_repair_authoring",
    "fleet",
]

INCIDENT_KINDS: Final[frozenset[IncidentKind]] = frozenset(get_args(IncidentKind))

# --- job_attempt.metrics keys (design §2.2) -----------------------------------------------------
#
# Legacy names map onto these at read time (`requests_spent` -> `requests`, `rows` -> `rows_written`,
# `bytes`/`written_bytes` -> `bytes_written`); the map itself lives with the reader (o4), not here,
# since this module stays free of any first-party import.

MetricKey = Literal[
    "turn_id",
    "spawned",
    "exit_class",
    "turn_outcome",
    "report_present",
    "usage_reported",
    "usage_complete",
    "unwritten_known",
    "probe",
    "probe_status",
    "stdout_bytes",
    "stdout_truncated",
    "start_lag_seconds",
    "log_lines_dropped",
    "rss_peak_kib",
    "cpu_seconds",
    "meter_errors",
    "requests",
    "weighted_calls",
    "fetch_attempts",
    "http_requests",
    "weighted_calls_metered",
    "last_send_outcome",
    "rows_written",
    "bytes_written",
    "publication_debt",
]

METRIC_KEYS: Final[frozenset[MetricKey]] = frozenset(get_args(MetricKey))

# --- Provider pool labels (design §2.3; `usage.provider_for_host` resolves a host to one of these) -

PoolLabel = Literal["open-meteo-paid", "open-meteo-free", "firms", "usgs-water-data"]

POOL_LABELS: Final[frozenset[PoolLabel]] = frozenset(get_args(PoolLabel))

# --- Per-lane logical (weighted) caps, from metadata.json's budget_headline --------------------
#
# `legacy_soil_per_run_after_g0` pins soil's hard cap at 1,602 weighted calls per run (G0's fan-out
# cap, independent of `--retry-attempts`). A `suspect`-basis attempt (design §2.2) is charged at its
# lane's entry here, never at the wire worst case. Extend this mapping, never repurpose an entry.

LANE_LOGICAL_CAPS: Final[MappingProxyType[str, int]] = MappingProxyType({"soil": 1602})
