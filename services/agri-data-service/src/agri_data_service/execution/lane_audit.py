"""`agri-service ops lane-audit`: one read-only walk of every lane, from declared source limits to served coverage.

Joins evidence that already exists and adds only the judgement (execution/AGENTS.md, "Lane audit"):

1. **Declared limits** -- the legacy `LaneExecutionSpec` table and the config lane TOMLs (with their
   provider file), both through `lane_catalogue.current_lane_catalogue`, so a lane is audited on the
   path it actually dispatches on.
2. **Coverage and freshness** -- `gap_repair.read_parquet_coverage`, the same availability read
   `jobs-plan-gap-repair` prints, folded per layer by `gap_repair_contract.measure_lane_gaps`.
3. **Ledger** -- `usage_report`'s three loaders (row-level usage feed, month-to-date per pool, open
   incidents) inside its read-only, timeout-bounded transaction; the last forward turn per lane is read
   off the same usage feed, so no SQL file is loaded a second time.

Every flag is a `LaneFlag` with a severity; a lane's status is its worst flag, else `ok` -- but only when
every read that could have raised a worse flag answered (`_status`). The evidence reads are
fault-isolated: a dead ledger still prints the coverage flags, and the reverse.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, cast

import click

from agri_data_service.db.engine import ingest_session
from agri_data_service.execution import usage_report
from agri_data_service.execution.cron_schedule import cron_expression, latest_fire_at_or_before, next_fire_after
from agri_data_service.execution.gap_repair import read_parquet_coverage
from agri_data_service.execution.gap_repair_contract import REPAIR_BINDINGS, LaneGapFacts, measure_lane_gaps
from agri_data_service.execution.lane_catalogue import (
    GAP_FILL_SUFFIX,
    ConfigLane,
    current_lane_catalogue,
    load_config_lanes,
    owning_lane_id,
)
from agri_data_service.execution.lane_ids import (
    BURN_SEVERITY_DIRECT_LANE_ID,
    CROP_COVER_MAINTENANCE_LANE_ID,
    EVACUATION_ZONES_DIRECT_LANE_ID,
    FIRE_DETECTIONS_DIRECT_LANE_ID,
    FIRE_PERIMETERS_DIRECT_LANE_ID,
    LAND_CONTEXT_BACKFILL_LANE_ID,
    LAND_CONTEXT_FORWARD_LANE_ID,
    LAND_CONTEXT_RECONCILE_LANE_ID,
    MTBS_FORWARD_LANE_ID,
    VEGETATION_DIRECT_LANE_ID,
    WATER_GAUGES_DIRECT_LANE_ID,
    WATERSHEDS_DIRECT_LANE_ID,
)
from agri_data_service.execution.lane_specs import (
    EXECUTOR_DEFINITION_PREFIX,
    LANE_SPECS,
    LaneExecutionSpec,
    parse_activation,
)
from agri_data_service.foundation.observability.vocabulary import POOL_LABELS
from agri_data_service.foundation.parquet.lane_contract import nature_has_time_axis
from agri_data_service.parquet_ops.wire import render_day, render_instant

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from agri_data_service.execution.lane_catalogue import LaneCatalogue
    from agri_data_service.foundation.lane_config import ProviderConfig
    from agri_data_service.foundation.parquet.lane_contract import LaneNature
    from agri_data_service.parquet_ops.wire import LaneCoverage, LaneRefreshPolicy

Severity = Literal["critical", "warn", "info"]
SEVERITY_RANK: Final[Mapping[str, int]] = MappingProxyType({"critical": 3, "warn": 2, "info": 1})
STATUS_OK: Final = "ok"
#: A flag-free lane whose coverage or ledger could not be read: "ok" would be a claim nothing proved.
STATUS_UNKNOWN: Final = "unknown"
FORMAT_CHOICES: Final = ("table", "json")
LANE_AUDIT_COMMAND: Final = "agri-service ops lane-audit"

# --- Thresholds: every number a flag compares against, named once ---------------------------------

#: The trailing ledger window. Seven days covers twice the slowest DAILY cadence with room to spare; a
#: weekly lane (mtbs-forward) needs `--days 14` before its absence is judged rather than reported.
DEFAULT_AUDIT_WINDOW_DAYS: Final = 7
#: A forward lane whose newest turn is older than this many cadence periods has stopped turning.
STALE_TURN_CADENCE_MULTIPLE: Final = 2
#: Share of one host's HTTP requests (429 + 5xx + transport failures) above which a lane is flagged.
ERROR_RATE_WARN_FRACTION: Final = 0.05
#: At or above this share the host is failing more often than it answers: critical.
ERROR_RATE_CRITICAL_FRACTION: Final = 0.50
#: Fewer requests than this on a host is too small a sample for a rate to mean anything.
MIN_REQUESTS_FOR_ERROR_RATE: Final = 20
#: Bytes received per metered run above which a small-row lane is flagged (2026-10-03: sensors
#: measured 183 MB/run over 600 requests/run; every other point lane sat far below this).
BYTES_PER_RUN_CEILING: Final = 50_000_000
#: Lanes whose turns legitimately move polygons, rasters or whole-snapshot captures: bytes scale with
#: scene or geometry size, not rows, so the small-row byte ceiling does not judge them.
GEOMETRY_CAPTURE_LANES: Final[frozenset[str]] = frozenset(
    {
        VEGETATION_DIRECT_LANE_ID,
        CROP_COVER_MAINTENANCE_LANE_ID,
        BURN_SEVERITY_DIRECT_LANE_ID,
        MTBS_FORWARD_LANE_ID,
        WATERSHEDS_DIRECT_LANE_ID,
        EVACUATION_ZONES_DIRECT_LANE_ID,
        FIRE_PERIMETERS_DIRECT_LANE_ID,
        LAND_CONTEXT_FORWARD_LANE_ID,
        LAND_CONTEXT_RECONCILE_LANE_ID,
        LAND_CONTEXT_BACKFILL_LANE_ID,
    }
)
#: A lane behind its expected horizon by more than this many days is critical rather than a warning.
BEHIND_CRITICAL_STALENESS_DAYS: Final = 14
#: Withholdings that mean the published index itself is broken, not merely late.
WITHHELD_CRITICAL_REASONS: Final[frozenset[str]] = frozenset(
    {"availability_malformed", "availability_checksum_invalid"}
)
#: Exit classes a retry cannot fix: the lane's own code or configuration failed.
FAILED_EXIT_CLASSES: Final[frozenset[str]] = frozenset({"code", "config"})
#: Exit classes of a turn that did not finish for a reason outside the lane's code.
DEGRADED_EXIT_CLASSES: Final[frozenset[str]] = frozenset(
    {"upstream", "infra", "hang", "lease_lost", "report_missing", "interrupted"}
)
FAILED_ATTEMPT_STATUSES: Final[frozenset[str]] = frozenset({"failed", "lost"})
#: How many gap ranges each coverage row renders, newest first; the count is always stated in full.
MAX_RENDERED_GAP_RANGES: Final = 5
_MEGABYTE: Final = 1_000_000
_SECONDS_PER_HOUR: Final = 3600

# --- Flag codes ---------------------------------------------------------------------------------

BEHIND_HORIZON: Final = "behind_horizon"
COVERAGE_GAPS: Final = "coverage_gaps"
COVERAGE_WITHHELD: Final = "coverage_withheld"
NEVER_WRITTEN: Final = "never_written"
FRESHNESS_METADATA_MISSING: Final = "freshness_metadata_missing"
LAST_TURN_FAILED: Final = "last_turn_failed"
LAST_TURN_INCOMPLETE: Final = "last_turn_incomplete"
FORWARD_TURN_STALE: Final = "forward_turn_stale"
NO_TURN_IN_WINDOW: Final = "no_turn_in_window"
PROVIDER_ERROR_RATE: Final = "provider_error_rate"
BYTES_PER_RUN_ANOMALY: Final = "bytes_per_run_anomaly"
OPEN_INCIDENT: Final = "open_incident"
POOL_BUDGET_PRESSURE: Final = "pool_budget_pressure"

# --- Evidence: the reads a status depends on --------------------------------------------------------

USAGE_SECTION: Final = "usage_rows"
MONTH_TO_DATE_SECTION: Final = "month_to_date"
INCIDENTS_SECTION: Final = "open_incidents"
#: The ledger's three savepoint-isolated sections (`_read_ledger`); any one can fail alone.
LEDGER_SECTIONS: Final = (USAGE_SECTION, MONTH_TO_DATE_SECTION, INCIDENTS_SECTION)
COVERAGE_EVIDENCE: Final = "coverage"
#: Not a read but a judgement: staleness is judged only for a lane active in this process's env.
TURN_STALENESS_EVIDENCE: Final = "turn_staleness"
#: The worst flag each missing read could have raised; a status below it claims what nothing proved.
_WORST_FLAG_IF_READ: Final[Mapping[str, int]] = MappingProxyType(
    {
        COVERAGE_EVIDENCE: SEVERITY_RANK["critical"],
        USAGE_SECTION: SEVERITY_RANK["critical"],
        MONTH_TO_DATE_SECTION: SEVERITY_RANK["critical"],
        INCIDENTS_SECTION: SEVERITY_RANK["critical"],
        TURN_STALENESS_EVIDENCE: SEVERITY_RANK["warn"],
    }
)

#: Layers written by a legacy direct writer that no repair binding names. Every repair-bound layer
#: (`REPAIR_BINDINGS`) and every config `[[streams]]` slug joins its lane by derivation instead.
_DIRECT_WRITER_LAYERS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "fire-detections": (FIRE_DETECTIONS_DIRECT_LANE_ID,),
        "water-gauges": (WATER_GAUGES_DIRECT_LANE_ID,),
        "burn-severity": (BURN_SEVERITY_DIRECT_LANE_ID, MTBS_FORWARD_LANE_ID),
        "fire-perimeters": (FIRE_PERIMETERS_DIRECT_LANE_ID,),
        "evacuation-zones": (EVACUATION_ZONES_DIRECT_LANE_ID,),
        "watersheds": (WATERSHEDS_DIRECT_LANE_ID,),
        "land-context-boundaries": (LAND_CONTEXT_FORWARD_LANE_ID,),
        "land-context-offices": (LAND_CONTEXT_FORWARD_LANE_ID,),
        "land-context-contacts": (LAND_CONTEXT_FORWARD_LANE_ID,),
        "crop-cover": (CROP_COVER_MAINTENANCE_LANE_ID,),
    }
)


@dataclass(frozen=True, slots=True)
class LaneFlag:
    """One thing an operator should look at, with how urgently."""

    code: str
    severity: Severity
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "severity": self.severity, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class LaneUnderAudit:
    """One forward definition the executor knows, on the path it dispatches on, with its declared limits."""

    lane_id: str
    path: Literal["legacy", "config"]
    active: bool
    definition_name: str
    #: The forward period: a legacy cadence, or the gap between a config cron's consecutive fires.
    cadence_seconds: int | None
    declared: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class LayerCoverage:
    """One census layer folded across its rungs, plus the two row facts the fold does not carry."""

    facts: LaneGapFacts
    nature: LaneNature
    refresh_policy: LaneRefreshPolicy | None

    def to_dict(self) -> dict[str, object]:
        facts = self.facts
        policy = self.refresh_policy
        return {
            "layer": facts.layer,
            "kind": facts.kind,
            "nature": self.nature,
            "latest_recorded_day": _day(facts.latest_recorded_day),
            "source_ceiling_day": _day(facts.source_ceiling_day),
            "expected_horizon_day": _day(facts.expected_horizon_day),
            "staleness_days": facts.staleness_days,
            "behind_provider": facts.behind_provider,
            "gap_day_count": facts.gap_day_count,
            "gap_range_count": len(facts.gap_ranges),
            "gap_ranges_newest": [entry.to_wire() for entry in facts.gap_ranges[::-1][:MAX_RENDERED_GAP_RANGES]],
            "withheld_reason": facts.withheld_reason,
            "freshness": None
            if policy is None
            else {
                "publication_lag_days": policy.publication_lag_days,
                "source_cadence_days": policy.source_cadence_days,
                "refresh_interval_seconds": policy.refresh_interval_seconds,
            },
        }


@dataclass(frozen=True, slots=True)
class AuditEvidence:
    """Everything the per-lane judgement reads, gathered once for the whole fleet."""

    now: datetime
    window: usage_report.ReportWindow
    coverage_by_lane: Mapping[str, Sequence[LayerCoverage]]
    rows_by_lane: Mapping[str, Sequence[Mapping[str, object]]]
    incidents_by_lane: Mapping[str, Sequence[Mapping[str, object]]]
    pool_flags: Mapping[str, Sequence[LaneFlag]]
    #: Ledger sections (`LEDGER_SECTIONS`) that did not answer; a dead connection fails all three.
    failed_ledger_sections: frozenset[str]
    coverage_read: bool
    #: Lanes active in THIS process's env, over the WHOLE catalogue (never narrowed by `--lane`): the
    #: "is this the right host at all" signal `missing_evidence` reads. Zero means turn staleness could
    #: not be judged for ANYONE here; a deliberately inactive lane on an otherwise normal host is a
    #: different fact and must not read the same way (`missing_evidence`).
    active_lane_count: int

    @property
    def usage_read(self) -> bool:
        """True when the usage feed answered, so a lane's turns (and their absence) can be judged."""
        return USAGE_SECTION not in self.failed_ledger_sections


def _day(value: date | None) -> str | None:
    return None if value is None else render_day(value)


def _worst(flags: Iterable[LaneFlag]) -> str:
    ranked = max((SEVERITY_RANK[flag.severity] for flag in flags), default=0)
    return next((name for name, rank in SEVERITY_RANK.items() if rank == ranked), STATUS_OK)


def missing_evidence(lane: LaneUnderAudit, evidence: AuditEvidence, *, pools: Sequence[str]) -> list[str]:
    """The reads that did not answer FOR THIS LANE: a failed pool read only matters to a lane spending a pool.

    Turn staleness is missing only when the read it needs failed (`not evidence.usage_read`) or this
    process's env proves NOTHING is active here (`active_lane_count == 0`, the wrong-host signal) --
    never merely because THIS lane happens to be inactive on an otherwise normal host. `turn_flags`
    already never raises a staleness flag for an inactive lane (there is nothing to judge), so before
    2026-10-03's review a deliberately-inactive lane on a healthy host could never read `ok` either: its
    real silence was indistinguishable from a read that failed, and nine inactive prod lanes read
    `unknown` every audit, drowning any lane with a genuine unknown in the same noise.
    """
    missing = [] if evidence.coverage_read else [COVERAGE_EVIDENCE]
    missing.extend(
        section
        for section in LEDGER_SECTIONS
        if section in evidence.failed_ledger_sections and (section != MONTH_TO_DATE_SECTION or pools)
    )
    if not evidence.usage_read or evidence.active_lane_count == 0 or lane.cadence_seconds is None:
        missing.append(TURN_STALENESS_EVIDENCE)
    return missing


def _status(flags: Sequence[LaneFlag], missing: Sequence[str]) -> str:
    """The worst flag, unless a read that could have raised a WORSE one did not answer: then `unknown`."""
    worst = _worst(flags)
    raised = SEVERITY_RANK.get(worst, 0)
    could_have_raised = max((_WORST_FLAG_IF_READ[name] for name in missing), default=0)
    return worst if raised >= could_have_raised else STATUS_UNKNOWN


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


# --- 1. The lane inventory and its declared limits ---------------------------------------------------


def cron_period_seconds(cron: str, *, now: datetime) -> int:
    """The gap between a cron's latest fire at or before `now` and the fire after it: its current period."""
    expression = cron_expression(cron)
    latest = latest_fire_at_or_before(expression, now)
    return int((next_fire_after(expression, latest) - latest).total_seconds())


def _legacy_lane(spec: LaneExecutionSpec, *, active: bool) -> LaneUnderAudit:
    return LaneUnderAudit(
        lane_id=spec.lane_id,
        path="legacy",
        active=active,
        definition_name=spec.definition_name,
        cadence_seconds=spec.cadence_seconds,
        declared={
            "schedule": spec.schedule,
            "cadence_seconds": spec.cadence_seconds,
            "command_timeout_seconds": spec.command_timeout_seconds,
            "publication_lag_days": spec.publication_lag_days,
            "publication_cadence_days": spec.publication_cadence_days,
            "publication_lag_source": spec.publication_lag_source,
            "catch_up_policy": spec.catch_up_policy,
            "writer_floor": spec.writer_floor,
            "writer_ceiling": spec.writer_ceiling,
            "source_limits": "code-owned: lane_specs.py and the writer package's own constants",
        },
    )


def _provider_limits(provider: ProviderConfig | None, *, endpoint: str | None) -> dict[str, object] | None:
    """A provider file's hosts, key wiring and budget. The key is checked for PRESENCE only, never read out."""
    if provider is None:
        return None
    hosts = sorted(
        {host for entry in provider.endpoints.values() for host in (entry.host, entry.customer_host) if host}
    )
    key_name = provider.api_key_env
    return {
        "id": provider.id,
        "endpoint": endpoint,
        "hosts": hosts,
        "weighted": provider.weighted,
        "time_standard": provider.time_standard,
        "api_key_env": key_name,
        "api_key_configured": None if key_name is None else bool(os.environ.get(key_name, "").strip()),
        "budget": None if provider.budget is None else provider.budget.model_dump(mode="json"),
    }


def _config_lane(lane: ConfigLane, *, provider: ProviderConfig | None, active: bool, now: datetime) -> LaneUnderAudit:
    config = lane.config
    schedule = config.schedule
    forward = lane.forward
    return LaneUnderAudit(
        lane_id=config.id,
        path="config",
        active=active,
        definition_name=forward.definition_name,
        cadence_seconds=cron_period_seconds(schedule.forward_cron, now=now),
        declared={
            "enabled": config.enabled,
            "forward_cron": schedule.forward_cron,
            "gap_fill_cron": schedule.gap_fill_cron,
            "gap_fill_enabled": schedule.gap_fill_enabled,
            "catch_up_policy": schedule.catch_up,
            "command_timeout_seconds": forward.command_timeout_seconds,
            "days": None if config.days is None else config.days.model_dump(mode="json"),
            "budget": config.budget.model_dump(mode="json"),
            "provider": _provider_limits(provider, endpoint=None if config.source is None else config.source.endpoint),
            "source_limits": f"lanes/{config.id}.toml and its lanes/_providers file",
        },
    )


def lane_inventory(*, now: datetime) -> tuple[list[LaneUnderAudit], LaneCatalogue]:
    """Every forward definition the catalogue dispatches, legacy then config, with activation read from env."""
    catalogue = current_lane_catalogue(LANE_SPECS)
    active = catalogue.dispatch_activation(parse_activation()).active_lanes
    lanes = [_legacy_lane(spec, active=spec.lane_id in active) for spec in catalogue.legacy_specs.values()]
    configs = None if catalogue.load_error is not None else load_config_lanes()
    for config_lane in catalogue.config_lanes.values():
        provider = None if configs is None else configs.provider_of(config_lane.config)
        lanes.append(
            _config_lane(config_lane, provider=provider, active=config_lane.forward.lane_id in active, now=now)
        )
    return lanes, catalogue


def layer_owners(catalogue: LaneCatalogue) -> dict[str, tuple[str, ...]]:
    """Census layer -> the lanes that write it: repair bindings, config streams, then the direct writers."""
    owners: dict[str, list[str]] = defaultdict(list)
    for layer, binding in REPAIR_BINDINGS.items():
        owners[layer].append(binding.lane_id)
    for lane_id, config_lane in catalogue.config_lanes.items():
        for stream in config_lane.config.streams:
            owners[stream.slug].append(lane_id)
    for layer, lane_ids in _DIRECT_WRITER_LAYERS.items():
        owners[layer].extend(lane_ids)
    return {layer: tuple(dict.fromkeys(lane_ids)) for layer, lane_ids in owners.items()}


# --- 2. Coverage ---------------------------------------------------------------------------------------


def fold_coverage(rows: Sequence[LaneCoverage]) -> list[LayerCoverage]:
    """One `LayerCoverage` per (layer, kind), folded across rungs exactly as the repair planner folds them."""
    grouped: dict[tuple[str, str], list[LaneCoverage]] = defaultdict(list)
    for row in rows:
        grouped[(row.layer, row.kind)].append(row)
    return [
        LayerCoverage(
            facts=measure_lane_gaps(layer_rows),
            nature=layer_rows[0].nature,
            refresh_policy=next((row.refresh_policy for row in layer_rows if row.refresh_policy is not None), None),
        )
        for layer_rows in grouped.values()
    ]


def _missing_freshness_fields(policy: LaneRefreshPolicy | None) -> list[str]:
    if policy is None:
        return ["publication_lag_days", "source_cadence_days", "refresh_interval_seconds"]
    fields = {
        "publication_lag_days": policy.publication_lag_days,
        "source_cadence_days": policy.source_cadence_days,
        "refresh_interval_seconds": policy.refresh_interval_seconds,
    }
    return [name for name, value in fields.items() if value is None]


def coverage_flags(layer: LayerCoverage) -> list[LaneFlag]:
    """What the census says is wrong with one layer: withheld, behind, holed, empty, or undescribed."""
    facts = layer.facts
    flags: list[LaneFlag] = []
    if facts.withheld_reason is not None:
        severity: Severity = "critical" if facts.withheld_reason in WITHHELD_CRITICAL_REASONS else "warn"
        flags.append(
            LaneFlag(
                COVERAGE_WITHHELD,
                severity,
                f"{facts.layer}: coverage withheld ({facts.withheld_reason}); the slider shows no axis for it",
            )
        )
    if facts.behind_provider:
        staleness = facts.staleness_days or 0
        severity = "critical" if staleness > BEHIND_CRITICAL_STALENESS_DAYS else "warn"
        flags.append(
            LaneFlag(
                BEHIND_HORIZON,
                severity,
                f"{facts.layer}: latest recorded {_day(facts.latest_recorded_day)} is {staleness} day(s) behind "
                f"its expected horizon {_day(facts.expected_horizon_day)} (registered lag plus cadence and grace)",
            )
        )
    if facts.gap_day_count:
        flags.append(
            LaneFlag(
                COVERAGE_GAPS,
                "warn",
                f"{facts.layer}: {facts.gap_day_count} unwritten day(s) in {len(facts.gap_ranges)} range(s), "
                f"{_day(facts.oldest_gap_day)}..{_day(facts.newest_gap_day)}",
            )
        )
    time_axis = nature_has_time_axis(layer.nature)
    if time_axis and facts.latest_recorded_day is None and facts.withheld_reason is None:
        flags.append(LaneFlag(NEVER_WRITTEN, "warn", f"{facts.layer}: no day has ever been recorded"))
    missing = _missing_freshness_fields(layer.refresh_policy) if time_axis else []
    if missing:
        flags.append(
            LaneFlag(
                FRESHNESS_METADATA_MISSING,
                "info",
                f"{facts.layer}: freshness {', '.join(missing)} unset; the web slider omits that timing label",
            )
        )
    return flags


# --- 3. Ledger: last turn, usage, incidents, pools -------------------------------------------------


def _owned_rows(rows: Sequence[Mapping[str, object]]) -> dict[str, list[Mapping[str, object]]]:
    """Usage rows keyed by OWNING lane: a `:gap-fill` definition rolls up as `:gap-repair` already does.

    The SQL's `lane_id` is the executor definition name (`plantgeo.executor.<lane>`), not the bare lane id.
    """
    by_lane: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        lane_id = owning_lane_id(str(row.get("lane_id")).removeprefix(EXECUTOR_DEFINITION_PREFIX))
        by_lane[lane_id].append({**row, "lane_id": lane_id})
    return by_lane


def last_forward_turn(lane: LaneUnderAudit, rows: Sequence[Mapping[str, object]]) -> Mapping[str, object] | None:
    """The newest settled attempt of the lane's FORWARD definition in the window; repair/gap-fill turns excluded."""
    forward = [row for row in rows if row.get("definition_name") == lane.definition_name]
    return max(forward, key=lambda row: cast("datetime", row["started_at"]), default=None)


def render_turn(turn: Mapping[str, object] | None, *, now: datetime) -> dict[str, object] | None:
    if turn is None:
        return None
    settled = cast("datetime | None", turn.get("finished_at")) or cast("datetime", turn["started_at"])
    return {
        "attempt_id": str(turn.get("attempt_id")),
        "started_at": render_instant(cast("datetime", turn["started_at"])),
        "finished_at": None if turn.get("finished_at") is None else render_instant(settled),
        "age_seconds": int((now - settled).total_seconds()),
        "attempt_status": turn.get("attempt_status"),
        "exit_class": turn.get("exit_class"),
        "turn_outcome": turn.get("turn_outcome"),
        "probe_status": turn.get("probe_status"),
        # `_number` is the usage report's own jsonb-numeric coercion, reused rather than copied.
        "rows_written": usage_report._number(turn.get("rows_written")),
        "publication_debt": usage_report._number(turn.get("publication_debt")),
    }


def turn_flags(lane: LaneUnderAudit, turn: Mapping[str, object] | None, evidence: AuditEvidence) -> list[LaneFlag]:
    """Failed, incomplete, or stale forward turns. Staleness is judged only for a lane activation runs."""
    if turn is None:
        return _absent_turn_flags(lane, evidence)
    flags: list[LaneFlag] = []
    exit_class = turn.get("exit_class")
    status = turn.get("attempt_status")
    outcome = turn.get("turn_outcome")
    describe = f"exit_class={exit_class} turn_outcome={outcome} attempt_status={status}"
    if exit_class in FAILED_EXIT_CLASSES:
        flags.append(LaneFlag(LAST_TURN_FAILED, "critical", f"last forward turn failed in its own code: {describe}"))
    elif exit_class in DEGRADED_EXIT_CLASSES or status in FAILED_ATTEMPT_STATUSES:
        flags.append(LaneFlag(LAST_TURN_FAILED, "warn", f"last forward turn did not finish: {describe}"))
    elif outcome == "incomplete" or (usage_report._number(turn.get("publication_debt")) or 0) > 0:
        flags.append(
            LaneFlag(
                LAST_TURN_INCOMPLETE,
                "warn",
                f"last forward turn left work owed: {describe} publication_debt={turn.get('publication_debt')}",
            )
        )
    if lane.active and lane.cadence_seconds is not None:
        settled = cast("datetime | None", turn.get("finished_at")) or cast("datetime", turn["started_at"])
        age = evidence.now - settled
        stale_after = timedelta(seconds=lane.cadence_seconds * STALE_TURN_CADENCE_MULTIPLE)
        if age > stale_after:
            flags.append(
                LaneFlag(
                    FORWARD_TURN_STALE,
                    "warn",
                    f"newest forward turn settled {age.total_seconds() / _SECONDS_PER_HOUR:.1f} h ago; cadence is "
                    f"{lane.cadence_seconds / _SECONDS_PER_HOUR:g} h, stale after {STALE_TURN_CADENCE_MULTIPLE}x",
                )
            )
    return flags


def _absent_turn_flags(lane: LaneUnderAudit, evidence: AuditEvidence) -> list[LaneFlag]:
    if not evidence.usage_read or not lane.active or lane.cadence_seconds is None:
        return []
    window_span = evidence.window.until - evidence.window.since
    stale_after = timedelta(seconds=lane.cadence_seconds * STALE_TURN_CADENCE_MULTIPLE)
    if window_span >= stale_after:
        return [
            LaneFlag(
                FORWARD_TURN_STALE,
                "warn",
                f"no settled forward turn in the {evidence.window.label} window, though the lane is active on a "
                f"{lane.cadence_seconds / _SECONDS_PER_HOUR:g} h cadence",
            )
        ]
    return [
        LaneFlag(
            NO_TURN_IN_WINDOW,
            "info",
            f"no forward turn in the {evidence.window.label} window, shorter than {STALE_TURN_CADENCE_MULTIPLE}x its "
            "cadence; widen --days to judge it",
        )
    ]


def lane_usage(rows: Sequence[Mapping[str, object]]) -> dict[str, object] | None:
    """The lane's usage over the window and per host, rolled up by `usage_report.group_usage_rows`."""
    if not rows:
        return None
    summary = usage_report.group_usage_rows(rows, by="lane")[0]
    host_rows = [row for row in rows if row.get("host") is not None]
    providers = {str(row["host"]): row.get("provider") for row in host_rows}
    hosts = [
        {
            "host": bucket["host"],
            "provider": providers.get(str(bucket["host"])),
            "http_requests": bucket["http_requests"],
            "http_429_rate": bucket["http_429_rate"],
            "http_5xx_rate": bucket["http_5xx_rate"],
            "transport_failure_rate": bucket["transport_failure_rate"],
            "bytes_in": bucket["bytes_in"],
        }
        for bucket in usage_report.group_usage_rows(host_rows, by="host")
    ]
    metered_runs = len({row["attempt_id"] for row in host_rows})
    http_requests = cast("float", summary["http_requests"])
    bytes_in = cast("float", summary["bytes_in"])
    return {
        "attempts": summary["attempts"],
        "metered_runs": metered_runs,
        "http_requests": http_requests,
        "http_requests_per_run": http_requests / metered_runs if metered_runs else None,
        "bytes_in": bytes_in,
        "bytes_in_per_run": bytes_in / metered_runs if metered_runs else None,
        "http_429_rate": summary["http_429_rate"],
        "http_5xx_rate": summary["http_5xx_rate"],
        "transport_failure_rate": summary["transport_failure_rate"],
        "turn_outcome_counts": summary["turn_outcome_counts"],
        "exit_class_counts": summary["exit_class_counts"],
        "pools": sorted({str(row["pool"]) for row in host_rows if row.get("pool") is not None}),
        "hosts": hosts,
    }


def _host_error_rate(host: Mapping[str, object]) -> tuple[float, str] | None:
    """(combined failure share, the rates that make it up) for one host with enough requests to judge."""
    requests = cast("float", host["http_requests"]) or 0
    if requests < MIN_REQUESTS_FOR_ERROR_RATE:
        return None
    rates = {
        "429": host["http_429_rate"],
        "5xx": host["http_5xx_rate"],
        "transport": host["transport_failure_rate"],
    }
    present = {name: cast("float", rate) for name, rate in rates.items() if rate}
    combined = sum(present.values())
    breakdown = ", ".join(f"{name} {_percent(rate)}" for name, rate in present.items())
    return combined, f"{breakdown} of {requests:.0f} requests"


def usage_flags(lane: LaneUnderAudit, usage: Mapping[str, object] | None) -> list[LaneFlag]:
    """Per-host failure rates over the window, and bytes per run against the small-row ceiling."""
    if usage is None:
        return []
    flags: list[LaneFlag] = []
    for host in cast("list[Mapping[str, object]]", usage["hosts"]):
        judged = _host_error_rate(host)
        if judged is None or judged[0] <= ERROR_RATE_WARN_FRACTION:
            continue
        combined, breakdown = judged
        severity: Severity = "critical" if combined >= ERROR_RATE_CRITICAL_FRACTION else "warn"
        flags.append(
            LaneFlag(PROVIDER_ERROR_RATE, severity, f"{host['host']}: {_percent(combined)} failed ({breakdown})")
        )
    per_run = cast("float | None", usage["bytes_in_per_run"])
    if lane.lane_id not in GEOMETRY_CAPTURE_LANES and per_run is not None and per_run > BYTES_PER_RUN_CEILING:
        requests_per_run = cast("float", usage["http_requests_per_run"])
        flags.append(
            LaneFlag(
                BYTES_PER_RUN_ANOMALY,
                "warn",
                f"{per_run / _MEGABYTE:.0f} MB in per run ({requests_per_run:.0f} requests/run over "
                f"{usage['metered_runs']} run(s)); ceiling {BYTES_PER_RUN_CEILING / _MEGABYTE:.0f} MB",
            )
        )
    return flags


def incident_lane(incident: Mapping[str, object]) -> str | None:
    """The lane an incident names: fingerprints are `<kind>:<lane>`, a suffixed definition rolls up."""
    _, _, rest = str(incident.get("fingerprint") or "").partition(":")
    return owning_lane_id(rest) if rest else None


def incident_flag(incident: Mapping[str, object]) -> LaneFlag:
    """A lane hold stops the lane, so it is critical whatever the row says; otherwise the row's own severity."""
    recorded = str(incident.get("severity"))
    severity: Severity
    if incident.get("incident_type") == "lane_hold" or recorded in {"critical", "error"}:
        severity = "critical"
    elif recorded == "warning":
        severity = "warn"
    else:
        severity = "info"
    return LaneFlag(
        OPEN_INCIDENT,
        severity,
        f"{incident.get('incident_type')} ({recorded}, x{incident.get('occurrence_count')}, since "
        f"{incident.get('first_seen_at')}): {incident.get('summary')}",
    )


def pool_flags(pool: str, month_to_date: object) -> list[LaneFlag]:
    """A metered pool past its gap-fill ceiling (warn) or its forward stop (critical), from its own `[budget]`."""
    if not isinstance(month_to_date, dict) or not isinstance(month_to_date.get("budget"), dict):
        return []
    budget = cast("dict[str, object]", month_to_date["budget"])
    charged = cast("float", month_to_date.get("charged") or 0)
    forward_stop = cast("float | None", budget.get("forward_stop"))
    ceiling = cast("float | None", budget.get("gap_fill_ceiling"))
    if forward_stop and charged >= forward_stop:
        return [LaneFlag(POOL_BUDGET_PRESSURE, "critical", f"{pool}: charged {charged:.0f} >= forward stop")]
    if ceiling and charged >= ceiling:
        return [LaneFlag(POOL_BUDGET_PRESSURE, "warn", f"{pool}: charged {charged:.0f} >= gap-fill ceiling")]
    return []


# --- Assembly --------------------------------------------------------------------------------------


def audit_lane(lane: LaneUnderAudit, evidence: AuditEvidence) -> dict[str, object]:
    """One lane's declared limits, evidence and flags, with its status the worst flag it raised."""
    layers = evidence.coverage_by_lane.get(lane.lane_id, ())
    rows = evidence.rows_by_lane.get(lane.lane_id, ())
    incidents = evidence.incidents_by_lane.get(lane.lane_id, ())
    turn = last_forward_turn(lane, rows)
    usage = lane_usage(rows)
    flags = [flag for layer in layers for flag in coverage_flags(layer)]
    flags.extend(turn_flags(lane, turn, evidence))
    flags.extend(usage_flags(lane, usage))
    flags.extend(incident_flag(incident) for incident in incidents)
    pools = cast("list[str]", (usage or {}).get("pools", []))
    for pool in pools:
        flags.extend(evidence.pool_flags.get(pool, ()))
    flags.sort(key=lambda flag: -SEVERITY_RANK[flag.severity])
    missing = missing_evidence(lane, evidence, pools=pools)
    return {
        "lane_id": lane.lane_id,
        "path": lane.path,
        "active": lane.active,
        "status": _status(flags, missing),
        "evidence_missing": missing,
        "declared": dict(lane.declared),
        "coverage": [layer.to_dict() for layer in layers],
        "last_turn": render_turn(turn, now=evidence.now),
        "usage": usage,
        "open_incidents": [dict(incident) for incident in incidents],
        "flags": [flag.to_dict() for flag in flags],
    }


async def _read_coverage(now: datetime) -> list[LayerCoverage] | str:
    try:
        coverage = await asyncio.to_thread(read_parquet_coverage, now=now)
    except Exception as error:  # deliberately broad: a dead object store must not hide the ledger's flags
        return f"{type(error).__name__}: {error}"
    return fold_coverage(coverage.lanes)


async def _read_ledger(
    window: usage_report.ReportWindow, *, lane_ids: Sequence[str] | None, now: datetime
) -> dict[str, object]:
    """The usage feed, every pool's month to date and every open incident, each in its own savepoint."""
    # The usage report's own per-section savepoint isolation, reused rather than re-implemented.
    run_section = usage_report._run_section
    async with ingest_session() as session:
        await usage_report.apply_report_bounds(session)

        async def usage_rows() -> list[Mapping[str, object]]:
            return list(
                await usage_report.provider_usage_rows(
                    session, since=window.since, until=window.until, lane_ids=lane_ids, pool=None
                )
            )

        async def pools() -> dict[str, object]:
            return {pool: await usage_report.month_to_date(session, pool=pool, now=now) for pool in sorted(POOL_LABELS)}

        async def incidents() -> list[dict[str, object]]:
            return await usage_report.open_incidents(session)

        evidence = {
            USAGE_SECTION: await run_section(session, usage_rows),
            MONTH_TO_DATE_SECTION: await run_section(session, pools),
            INCIDENTS_SECTION: await run_section(session, incidents),
        }
        await session.rollback()
    return evidence


def _ledger_filter(lane_ids: Sequence[str] | None, lanes: Sequence[LaneUnderAudit]) -> list[str] | None:
    """The usage feed's lane filter in definition-name terms; a config lane's `:gap-fill` rows are named too."""
    if lane_ids is None:
        return None
    config = {lane.lane_id for lane in lanes if lane.path == "config"}
    named = [*lane_ids, *(f"{lane_id}{GAP_FILL_SUFFIX}" for lane_id in lane_ids if lane_id in config)]
    return [f"{EXECUTOR_DEFINITION_PREFIX}{lane_id}" for lane_id in named]


def _section_list(value: object) -> list[Mapping[str, object]]:
    return cast("list[Mapping[str, object]]", value) if isinstance(value, list) else []


def failed_sections(ledger: Mapping[str, object]) -> frozenset[str]:
    """Ledger sections that did not answer: an `{"error": ...}` section, a missing one, or a wrong shape."""
    expected_shape: Mapping[str, type] = {USAGE_SECTION: list, MONTH_TO_DATE_SECTION: dict, INCIDENTS_SECTION: list}
    failed: set[str] = set()
    for section, shape in expected_shape.items():
        value = ledger.get(section)
        if not isinstance(value, shape) or (isinstance(value, dict) and "error" in value):
            failed.add(section)
    return frozenset(failed)


def _errors(coverage: object, ledger: Mapping[str, object]) -> dict[str, str]:
    errors = {"coverage": coverage} if isinstance(coverage, str) else {}
    errors.update(
        {
            name: str(cast("dict[str, object]", section)["error"])
            for name, section in ledger.items()
            if isinstance(section, dict) and "error" in section
        }
    )
    return errors


async def build_lane_audit(
    *, days: int, lane_ids: Sequence[str] | None, now: datetime | None = None
) -> dict[str, object]:
    """Walk every lane (or the named ones) and return the whole audit as one JSON-ready dict."""
    now = now or datetime.now(UTC)
    window = usage_report.resolve_window(days=days, since=None, until=None, now=now)
    lanes, catalogue = lane_inventory(now=now)
    # Computed over the WHOLE catalogue, before any `--lane` filter narrows `lanes`: this is "is this
    # process's env the right host at all", and must not read differently just because an operator
    # named one inactive lane.
    active_lane_count = sum(1 for lane in lanes if lane.active)
    if lane_ids:
        lanes = [lane for lane in lanes if lane.lane_id in set(lane_ids)]
    coverage = await _read_coverage(now)
    ledger: dict[str, object]
    try:
        ledger = await _read_ledger(window, lane_ids=_ledger_filter(lane_ids, lanes), now=now)
    except Exception as error:  # deliberately broad: no ledger DSN or a dead database still prints coverage
        ledger = {"connection": {"error": f"{type(error).__name__}: {error}"}}
    owners = layer_owners(catalogue)
    audited = {lane.lane_id for lane in lanes}
    coverage_by_lane: dict[str, list[LayerCoverage]] = defaultdict(list)
    unowned: list[LayerCoverage] = []
    for layer in coverage if isinstance(coverage, list) else []:
        owning = [lane_id for lane_id in owners.get(layer.facts.layer, ()) if lane_id in audited]
        for lane_id in owning:
            coverage_by_lane[lane_id].append(layer)
        if not owners.get(layer.facts.layer):
            unowned.append(layer)
    incidents_by_lane: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    unattributed: list[Mapping[str, object]] = []
    for incident in _section_list(ledger.get(INCIDENTS_SECTION)):
        incident_owner = incident_lane(incident)
        if incident_owner is not None and incident_owner in audited:
            incidents_by_lane[incident_owner].append(incident)
        else:
            unattributed.append(incident)
    failed = failed_sections(ledger)
    month_to_date = ledger.get(MONTH_TO_DATE_SECTION)
    pools = (
        cast("dict[str, object]", month_to_date)
        if isinstance(month_to_date, dict) and MONTH_TO_DATE_SECTION not in failed
        else {}
    )
    flagged_pools = {pool: pool_flags(pool, value) for pool, value in pools.items()}
    evidence = AuditEvidence(
        now=now,
        window=window,
        coverage_by_lane=coverage_by_lane,
        rows_by_lane=_owned_rows(_section_list(ledger.get(USAGE_SECTION))),
        incidents_by_lane=incidents_by_lane,
        pool_flags=flagged_pools,
        failed_ledger_sections=failed,
        coverage_read=isinstance(coverage, list),
        active_lane_count=active_lane_count,
    )
    lane_reports = [audit_lane(lane, evidence) for lane in lanes]
    statuses = [str(report["status"]) for report in lane_reports]
    return {
        "event": "plantgeo_lane_audit",
        "generated_at": render_instant(now),
        "window": {"since": render_instant(window.since), "until": render_instant(window.until), "label": window.label},
        "lane_filter": list(lane_ids) if lane_ids else None,
        "activation": {
            "active_lane_count": active_lane_count,
            "note": None
            if active_lane_count
            else "no lane is active in this process's environment, so turn staleness was not judged and no "
            "flag-free lane reads ok; run the verb on plantgeo-job-executor",
        },
        "summary": {
            "lanes": len(lane_reports),
            **{name: statuses.count(name) for name in (*SEVERITY_RANK, STATUS_OK, STATUS_UNKNOWN)},
        },
        "lanes": lane_reports,
        "pools": {
            pool: {"month_to_date": value, "flags": [flag.to_dict() for flag in flagged_pools.get(pool, [])]}
            for pool, value in pools.items()
        },
        "unowned_layers": [
            {**layer.to_dict(), "flags": [flag.to_dict() for flag in coverage_flags(layer)]}
            for layer in (unowned if not lane_ids else [])
        ],
        "unattributed_incidents": [dict(incident) for incident in unattributed],
        "section_errors": _errors(coverage, ledger),
    }


# --- Rendering and the verb --------------------------------------------------------------------------


def _worst_layer(coverage: Sequence[Mapping[str, object]]) -> str:
    timed = [layer for layer in coverage if layer.get("expected_horizon_day") is not None]
    if not timed:
        return "-"
    worst = max(timed, key=lambda layer: cast("int", layer.get("staleness_days") or 0))
    return f"{worst['latest_recorded_day']}/{worst['expected_horizon_day']}"


def _turn_cell(turn: Mapping[str, object] | None) -> str:
    if turn is None:
        return "-"
    hours = cast("int", turn["age_seconds"]) / _SECONDS_PER_HOUR
    return f"{hours:.1f}h {turn.get('exit_class') or turn.get('attempt_status')}/{turn.get('turn_outcome') or '-'}"


def _usage_cells(usage: Mapping[str, object] | None) -> tuple[str, str, str]:
    if usage is None:
        return "-", "-", "-"
    requests = cast("float | None", usage["http_requests_per_run"])
    per_run = cast("float | None", usage["bytes_in_per_run"])
    rates = [_host_error_rate(host) for host in cast("list[Mapping[str, object]]", usage["hosts"])]
    worst = max((rate[0] for rate in rates if rate is not None), default=None)
    return (
        "-" if requests is None else f"{requests:.0f}",
        "-" if per_run is None else f"{per_run / _MEGABYTE:.1f}",
        "-" if worst is None else _percent(worst),
    )


def render_table(report: Mapping[str, object]) -> str:
    """One row per lane, then every flag in full, then pools, unowned layers and section errors."""
    summary = cast("Mapping[str, object]", report["summary"])
    window = cast("Mapping[str, object]", report["window"])
    lines = [
        f"lane-audit  window={window['label']}  generated={report['generated_at']}  "
        + "  ".join(f"{key}={value}" for key, value in summary.items()),
        f"{'LANE':44} {'PATH':6} {'ACT':3} {'STATUS':8} {'LATEST/EXPECTED':23} {'LAST TURN':26} "
        f"{'REQ/RUN':>7} {'MB/RUN':>7} {'ERR%':>6}  FLAGS",
    ]
    lane_reports = cast("list[Mapping[str, object]]", report["lanes"])
    for lane in lane_reports:
        flags = cast("list[Mapping[str, object]]", lane["flags"])
        requests, megabytes, error_rate = _usage_cells(cast("Mapping[str, object] | None", lane["usage"]))
        lines.append(
            f"{lane['lane_id']!s:44} {lane['path']!s:6} {'yes' if lane['active'] else 'no':3} {lane['status']!s:8} "
            f"{_worst_layer(cast('list[Mapping[str, object]]', lane['coverage'])):23} "
            f"{_turn_cell(cast('Mapping[str, object] | None', lane['last_turn'])):26} "
            f"{requests:>7} {megabytes:>7} {error_rate:>6}  {','.join(sorted({str(flag['code']) for flag in flags}))}"
        )
    lines.append("\n== flags")
    for lane in lane_reports:
        lines.extend(
            f"  {lane['lane_id']}: [{flag['severity']}] {flag['code']} -- {flag['detail']}"
            for flag in cast("list[Mapping[str, object]]", lane["flags"])
        )
        if lane["status"] == STATUS_UNKNOWN:
            missing = ", ".join(cast("list[str]", lane["evidence_missing"]))
            lines.append(f"  {lane['lane_id']}: [unknown] not judged, evidence missing: {missing}")
    lines.append("\n== pools")
    for pool, entry in cast("Mapping[str, Mapping[str, object]]", report["pools"]).items():
        lines.append(f"  {pool}: {json.dumps(entry, sort_keys=True, default=str)}")
    lines.append("\n== unowned layers")
    for layer in cast("list[Mapping[str, object]]", report["unowned_layers"]):
        codes = ",".join(str(flag["code"]) for flag in cast("list[Mapping[str, object]]", layer["flags"]))
        lines.append(f"  {layer['layer']}: latest={layer['latest_recorded_day']} flags={codes or '-'}")
    lines.append("\n== unattributed incidents")
    lines.extend(
        f"  {incident.get('incident_type')}: {incident.get('summary')}"
        for incident in cast("list[Mapping[str, object]]", report["unattributed_incidents"])
    )
    errors = cast("Mapping[str, str]", report["section_errors"])
    if errors:
        lines.append("\n== section errors")
        lines.extend(f"  {name}: {message}" for name, message in errors.items())
    return "\n".join(lines)


def _validate_lane_ids(lane_ids: Sequence[str]) -> None:
    if not lane_ids:
        return
    lanes, _ = lane_inventory(now=datetime.now(UTC))
    known = {lane.lane_id for lane in lanes}
    unknown = sorted(set(lane_ids) - known)
    if unknown:
        raise click.ClickException(f"unknown lane(s) {', '.join(unknown)}; known: {', '.join(sorted(known))}")


@click.command("lane-audit")
@click.option(
    "--days",
    type=click.IntRange(min=1),
    default=DEFAULT_AUDIT_WINDOW_DAYS,
    show_default=True,
    help="Trailing ledger window for turns, usage and error rates.",
)
@click.option("--lane", "lanes", multiple=True, help="Audit only these lane ids (repeatable). Default: every lane.")
@click.option("--format", "output_format", type=click.Choice(FORMAT_CHOICES), default="table", show_default=True)
def lane_audit(days: int, lanes: tuple[str, ...], output_format: str) -> None:
    """Audit every lane: declared source limits, census coverage and freshness, last turn, usage, incidents.

    Read-only. Coverage comes from the availability indexes (never a listing); the ledger is read in one
    read-only, timeout-bounded transaction. Each lane gets flags with a severity; see execution/AGENTS.md,
    "Lane audit", and docs/lane-audit/BASE_PROMPT.md for what to do about each flag.
    """
    _validate_lane_ids(lanes)
    report = asyncio.run(build_lane_audit(days=days, lane_ids=lanes or None))
    if output_format == "json":
        click.echo(json.dumps(report, sort_keys=True, default=str))
    else:
        click.echo(render_table(report))


__all__ = [
    "BEHIND_CRITICAL_STALENESS_DAYS",
    "BYTES_PER_RUN_CEILING",
    "DEFAULT_AUDIT_WINDOW_DAYS",
    "ERROR_RATE_CRITICAL_FRACTION",
    "ERROR_RATE_WARN_FRACTION",
    "GEOMETRY_CAPTURE_LANES",
    "MIN_REQUESTS_FOR_ERROR_RATE",
    "STALE_TURN_CADENCE_MULTIPLE",
    "LaneFlag",
    "LaneUnderAudit",
    "LayerCoverage",
    "build_lane_audit",
    "coverage_flags",
    "failed_sections",
    "lane_audit",
    "lane_inventory",
    "layer_owners",
    "missing_evidence",
    "render_table",
]
