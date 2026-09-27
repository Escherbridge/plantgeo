"""Event-name constants for every first-party log line Wave O introduces.

New names follow `plantgeo_<component>_<noun>_<verb>` (design §1.1); existing names, kept verbatim
where a slice ports an existing call site, are noted inline. This module holds names only -- no
emission logic -- so every slice (o1-o5) imports the same string instead of re-typing it. GL-6's
hold-probe and probation events, and G1's `budget_deferred`, are out of Wave O's scope (folded into
`f1-executor`) and are not listed here; add them there, append-only, when that slice lands.
"""

from __future__ import annotations

from typing import Final

# --- Runner and turn lifecycle (G1's runner emits the first two; listed here so o1's vocabulary
# consumers do not invent their own spelling) -----------------------------------------------------

EVENT_LANE_TURN_STARTED: Final = "plantgeo_lane_turn_started"
EVENT_LANE_TURN_DAY_SETTLED: Final = "plantgeo_lane_turn_day_settled"
EVENT_LANE_TURN_REPORT: Final = "plantgeo_lane_turn_report"

# --- Source-usage audit (o3/o4) -------------------------------------------------------------------

EVENT_SOURCE_REQUEST_RETRY: Final = "plantgeo_source_request_retry"
EVENT_SOURCE_REQUEST_FAILED: Final = "plantgeo_source_request_failed"
EVENT_SOURCE_METER_ERROR: Final = "plantgeo_source_meter_error"
EVENT_TURN_USAGE_OPEN: Final = "plantgeo_turn_usage_open"
EVENT_TURN_USAGE: Final = "plantgeo_turn_usage"
EVENT_SOURCE_USAGE: Final = "plantgeo_source_usage"

# --- Executor lane-turn outcome (o5a) --------------------------------------------------------------

EVENT_JOB_EXECUTOR_LANE_TURN: Final = "plantgeo_job_executor_lane_turn"
EVENT_LANE_REPORT_MISSING: Final = "plantgeo_job_executor_lane_report_missing"
EVENT_LANE_INCOMPLETE_ESCALATED: Final = "plantgeo_job_executor_lane_incomplete_escalated"
EVENT_LANE_INCOMPLETE_CLEARED: Final = "plantgeo_job_executor_lane_incomplete_cleared"

# --- Holds, quarantine and repairs (o2b/o5b) --------------------------------------------------------

EVENT_HOLD_OPENED: Final = "plantgeo_job_executor_hold_opened"
EVENT_HOLD_RECONCILED: Final = "plantgeo_job_executor_hold_reconciled"
EVENT_HOLD_RELEASED: Final = "plantgeo_job_executor_hold_released"
EVENT_HOLD_CHRONIC: Final = "plantgeo_job_executor_hold_chronic"
EVENT_HOLD_FLAPPING: Final = "plantgeo_job_executor_hold_flapping"
EVENT_LANE_BLOCKED: Final = "plantgeo_job_executor_lane_blocked"
EVENT_LANE_UNBLOCKED: Final = "plantgeo_job_executor_lane_unblocked"
EVENT_LANE_QUARANTINED: Final = "plantgeo_job_executor_lane_quarantined"
EVENT_CONFIG_FALLBACK: Final = "plantgeo_job_executor_config_fallback"
EVENT_LANE_PLAN_FAILED: Final = "plantgeo_job_executor_lane_plan_failed"
EVENT_REPAIR_WITHHELD: Final = "plantgeo_job_executor_repair_withheld"
EVENT_REPAIR_BREAKER_OPENED: Final = "plantgeo_job_executor_repair_breaker_opened"
EVENT_REPAIR_BREAKER_RELEASED: Final = "plantgeo_job_executor_repair_breaker_released"
# Existing name, kept: `execution/job_executor_service.py` already logs this on an authoring fault.
EVENT_REPAIR_AUTHORING_FAILED: Final = "repair_authoring_failed"
EVENT_FLEET_CORRELATED: Final = "plantgeo_job_executor_fleet_correlated"
EVENT_INCIDENT_WRITE_FAILED: Final = "plantgeo_job_executor_incident_write_failed"
EVENT_LEASE_LOST_ESCALATED: Final = "plantgeo_job_executor_lease_lost_escalated"
EVENT_TICK_PARTIAL: Final = "plantgeo_job_executor_tick_partial"

# --- Child log router (o1-1b/o5a) -------------------------------------------------------------------

EVENT_CHILD_LOG_TRUNCATED: Final = "plantgeo_child_log_truncated"
EVENT_CHILD_OUTPUT: Final = "plantgeo_child_output"
