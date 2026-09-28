"""Soft-failure incidents: one deduplicated `agri.job_incident` row per streak (spec Sec 4.9.3;
design record Sec 3.2A/3.4; GL-5, plan `config_driven_ingestion_20260926` 0W.5).

This module is the `o2b-incidents` slice: the upsert/resolve/select helpers a lane hold (and every
other soft-failure incident kind) is recorded and read through, the `INCIDENT_SEVERITY` vocabulary
mapping, the PURE `reconcile` state machine that decides what a hold does each tick, and the repair
breaker's 1/2/4/7-day cooldown ladder. It owns no wiring into `job_executor_service.py` -- that is
`o5b`'s slice, landing in the same GL-5 push -- so every function here is either a self-contained
database call or a function that touches no I/O at all, callable from a fake in a unit test exactly
as it will be called from the executor.

**Fingerprints are never reused while an episode is open.** `resolve_lane_incident.sql` renames a
resolved row's fingerprint (`lane_hold:<lane>` becomes `lane_hold:<lane>:resolved:<id>`) the instant
it closes, so `upsert_lane_incident`'s `ON CONFLICT (fingerprint)` branch can only ever mean "this
still-open episode happened again" -- never "reopen a closed one under the same identity". A fresh
episode after a resolve opens a brand new row under the same base fingerprint, exactly as `reconcile`
expects when it later needs to tell "the SAME still-open hold" from "a new episode of the same lane".

**Every write lives in its own savepoint** (design Sec 3.3, SOF2-03): `upsert_lane_incident`,
`resolve_lane_incident`, `select_lane_incidents` and `select_run_final_attempt` each run their one
statement inside `session.begin_nested()`. None of them catches the `SQLAlchemyError` a broken
statement raises -- that is deliberately `o5b`'s job, once per lane, so ONE lane's incident write (or
read) failing degrades only that lane to HEAD planning instead of losing the whole tick
(`test_raising_incident_read_leaves_every_lane_dispatching_as_head`,
`test_incident_write_failure_rolls_back_to_the_savepoint_only`, both `o5b`). This module supplies the
savepoint boundary; the surrounding catch-and-continue is the executor's.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.foundation.observability.vocabulary import EXIT_CLASSES, LOG_LEVELS, ExitClass, LogLevel
from agri_data_service.jobs.lease import (
    JobLedgerRowError,
    canonical_json,
    fetch_row,
    fetch_rows,
    optional_column,
    required_column,
)
from agri_data_service.models.jobs import EventSeverity, IncidentState

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession

_UPSERT_LANE_INCIDENT: Final = text(load_query_sql("execution/upsert_lane_incident.sql"))
_RESOLVE_LANE_INCIDENT: Final = text(load_query_sql("execution/resolve_lane_incident.sql"))
_SELECT_LANE_INCIDENTS: Final = text(load_query_sql("execution/select_lane_incidents.sql"))
_SELECT_RUN_FINAL_ATTEMPT: Final = text(load_query_sql("execution/select_run_final_attempt.sql"))

_EMPTY_DETAIL: Final[Mapping[str, object]] = MappingProxyType({})

# --- INCIDENT_SEVERITY: the one place a log level becomes an EventSeverity ------------------------
#
# `foundation` may not import `models` (see `foundation/observability/AGENTS.md`), so the vocabulary
# a caller reasons in (`LogLevel`, `foundation/observability/vocabulary.py`, the same four words the
# spec's incident tables use: "warn", "error", ...) and the enum `agri.job_incident.severity` is
# actually typed as (`EventSeverity`, `models/jobs.py`) cannot live next to each other in either
# module. This mapping is that seam, and it lives here because `execution` may import both.
INCIDENT_SEVERITY: Final[Mapping[LogLevel, EventSeverity]] = MappingProxyType(
    {
        "debug": EventSeverity.DEBUG,
        "info": EventSeverity.INFO,
        "warn": EventSeverity.WARNING,
        "error": EventSeverity.ERROR,
    }
)

assert set(INCIDENT_SEVERITY) == LOG_LEVELS, "INCIDENT_SEVERITY must cover every LogLevel exactly once"


# --- Upsert / resolve / select: one savepoint-wrapped statement each -------------------------------


@dataclass(frozen=True, slots=True)
class UpsertedIncident:
    """What `upsert_lane_incident.sql` handed back: the ledger's own count and timestamps, not the caller's."""

    incident_id: uuid.UUID
    fingerprint: str
    status: IncidentState
    occurrence_count: int
    first_seen_at: datetime
    last_seen_at: datetime


async def upsert_lane_incident(  # noqa: PLR0913 - one incident fact per argument, matching the SQL file's params
    session: AsyncSession,
    *,
    fingerprint: str,
    incident_type: str,
    severity: EventSeverity,
    summary: str,
    now: datetime,
    job_run_id: uuid.UUID | None = None,
    job_work_item_id: uuid.UUID | None = None,
    detail: Mapping[str, object] = _EMPTY_DETAIL,
) -> UpsertedIncident:
    """Open a new incident, or bump an already-open one under the same fingerprint, in its own savepoint.

    `severity` is written exactly as given -- this function never compares it to what the ledger
    already holds, so an escalating caller (the chronic/flapping rules, say) passes the ALREADY
    escalated value; `upsert_lane_incident.sql`'s own header explains why that decision belongs to
    the caller and not to the statement.
    """
    async with session.begin_nested():
        row = await fetch_row(
            session,
            _UPSERT_LANE_INCIDENT,
            {
                "fingerprint": fingerprint,
                "incident_type": incident_type,
                "severity": severity.value,
                "job_run_id": job_run_id,
                "job_work_item_id": job_work_item_id,
                "summary": summary,
                "now": now,
                "detail": canonical_json(detail),
            },
        )
    if row is None:  # pragma: no cover - defensive: an INSERT ... ON CONFLICT DO UPDATE always RETURNS a row
        raise JobLedgerRowError(f"upsert_lane_incident.sql returned no row for fingerprint {fingerprint!r}")
    return UpsertedIncident(
        incident_id=required_column(row, "id", uuid.UUID),
        fingerprint=required_column(row, "fingerprint", str),
        status=IncidentState(required_column(row, "status", str)),
        occurrence_count=required_column(row, "occurrence_count", int),
        first_seen_at=required_column(row, "first_seen_at", datetime),
        last_seen_at=required_column(row, "last_seen_at", datetime),
    )


@dataclass(frozen=True, slots=True)
class ResolvedIncident:
    """The renamed identity a resolved incident now carries; `fingerprint` is never the one it opened under."""

    incident_id: uuid.UUID
    fingerprint: str
    resolved_at: datetime


async def resolve_lane_incident(
    session: AsyncSession,
    *,
    fingerprint: str,
    now: datetime,
    detail_patch: Mapping[str, object] = _EMPTY_DETAIL,
) -> ResolvedIncident | None:
    """Close one open incident by its CURRENT fingerprint, in its own savepoint.

    `None` means nothing was open under that fingerprint -- already resolved by an earlier call, or
    never opened -- which is idempotent-safe to read as "nothing to do", the same convention
    `insert_run_supersession_incident.sql`'s own no-row conflict path already uses.
    """
    async with session.begin_nested():
        row = await fetch_row(
            session,
            _RESOLVE_LANE_INCIDENT,
            {"fingerprint": fingerprint, "now": now, "detail_patch": canonical_json(detail_patch)},
        )
    if row is None:
        return None
    return ResolvedIncident(
        incident_id=required_column(row, "id", uuid.UUID),
        fingerprint=required_column(row, "fingerprint", str),
        resolved_at=required_column(row, "resolved_at", datetime),
    )


@dataclass(frozen=True, slots=True)
class LaneIncidentRow:
    """One row of `select_lane_incidents.sql`: every open incident, plus `lane_hold`'s last-7-days episodes.

    The four `detail`-derived fields are read exactly as `->>` returns them -- text, uninterpreted --
    because most incident kinds this row can represent carry none of them and would otherwise force a
    parse failure on a NULL; a caller that needs `rung` or `chain_first_seen_at` as a number or a
    timestamp parses it itself, the same division of labour `select_open_incidents.sql` already
    established for the usage report.
    """

    incident_id: uuid.UUID
    fingerprint: str
    incident_type: str
    severity: EventSeverity
    status: IncidentState
    job_run_id: uuid.UUID | None
    job_work_item_id: uuid.UUID | None
    summary: str
    occurrence_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    cooldown_until: datetime | None
    resolved_at: datetime | None
    state: str | None
    rung: str | None
    chain_first_seen_at: str | None
    episodes_7d: str | None


async def select_lane_incidents(session: AsyncSession, *, now: datetime) -> tuple[LaneIncidentRow, ...]:
    """The executor's one incident read per tick, in its own savepoint (design Sec 3.2A)."""
    async with session.begin_nested():
        rows = await fetch_rows(session, _SELECT_LANE_INCIDENTS, {"now": now})
    return tuple(
        LaneIncidentRow(
            incident_id=required_column(row, "id", uuid.UUID),
            fingerprint=required_column(row, "fingerprint", str),
            incident_type=required_column(row, "incident_type", str),
            severity=EventSeverity(required_column(row, "severity", str)),
            status=IncidentState(required_column(row, "status", str)),
            job_run_id=optional_column(row, "job_run_id", uuid.UUID),
            job_work_item_id=optional_column(row, "job_work_item_id", uuid.UUID),
            summary=required_column(row, "summary", str),
            occurrence_count=required_column(row, "occurrence_count", int),
            first_seen_at=required_column(row, "first_seen_at", datetime),
            last_seen_at=required_column(row, "last_seen_at", datetime),
            cooldown_until=optional_column(row, "cooldown_until", datetime),
            resolved_at=optional_column(row, "resolved_at", datetime),
            state=optional_column(row, "state", str),
            rung=optional_column(row, "rung", str),
            chain_first_seen_at=optional_column(row, "chain_first_seen_at", str),
            episodes_7d=optional_column(row, "episodes_7d", str),
        )
        for row in rows
    )


@dataclass(frozen=True, slots=True)
class FinalAttempt:
    """One `select_run_final_attempt.sql` row: what actually happened last on a held checkpoint run."""

    status: str
    failure_class: str | None
    exit_class: str | None
    error_summary: str | None
    finished_at: datetime | None


async def select_run_final_attempt(session: AsyncSession, *, job_run_id: uuid.UUID) -> FinalAttempt | None:
    """Read the run's deciding attempt, in its own savepoint. `None` means the run never opened one."""
    async with session.begin_nested():
        row = await fetch_row(session, _SELECT_RUN_FINAL_ATTEMPT, {"job_run_id": job_run_id})
    if row is None:
        return None
    return FinalAttempt(
        status=required_column(row, "status", str),
        failure_class=optional_column(row, "failure_class", str),
        exit_class=optional_column(row, "exit_class", str),
        error_summary=optional_column(row, "error_summary", str),
        finished_at=optional_column(row, "finished_at", datetime),
    )


# --- The exit class a newly-opened hold carries -----------------------------------------------------

#: `class_source` values `final_attempt_exit_class` may return; every one names WHY the class is what
#: it is, so a hold's `detail.class_source` is self-explaining without a second query.
CLASS_SOURCE_STAMPED: Final = "final_attempt.metrics.exit_class"
CLASS_SOURCE_MISSING_ATTEMPT: Final = "no_final_attempt"
CLASS_SOURCE_LOST_ATTEMPT: Final = "final_attempt.status_lost"
CLASS_SOURCE_UNRECOGNISED: Final = "final_attempt.metrics.exit_class_unrecognised"
CLASS_SOURCE_NOT_A_HOLD_CLASS: Final = "final_attempt.metrics.exit_class_not_a_failure_class"
#: The classes a hold may carry (spec Sec 4.9.3's hold table). A stamped `ok`, `interrupted`, `lease_lost`
#: or `report_missing` on a held run's final attempt says nothing about WHY it failed, so it reads as `code`.
HOLD_EXIT_CLASSES: Final[frozenset[ExitClass]] = frozenset({"upstream", "infra", "code", "hang", "config"})


def final_attempt_exit_class(attempt: FinalAttempt | None) -> tuple[ExitClass, str]:
    """The `exit_class` a NEW `lane_hold` opens with, and why (design Sec 3.2A: "missing, unknown or lost
    means `code`, and `class_source` records why").

    Pure: reads only the `FinalAttempt` `select_run_final_attempt` already fetched. A `lost` status is
    checked before the stamped class is even read, because a lost attempt's `metrics` predates the
    kill that lost it and cannot be trusted to describe how the run actually ended.
    """
    if attempt is None:
        return "code", CLASS_SOURCE_MISSING_ATTEMPT
    if attempt.status == "lost":
        return "code", CLASS_SOURCE_LOST_ATTEMPT
    if attempt.exit_class is not None and attempt.exit_class in HOLD_EXIT_CLASSES:
        return attempt.exit_class, CLASS_SOURCE_STAMPED
    if attempt.exit_class is not None and attempt.exit_class in EXIT_CLASSES:
        return "code", CLASS_SOURCE_NOT_A_HOLD_CLASS
    return "code", CLASS_SOURCE_UNRECOGNISED


# --- reconcile: the pure per-tick hold state machine (design Sec 3.2A) -----------------------------

HoldState = Literal["held", "paused"]
ReleasedBy = Literal["operator", "reconciled"]
PauseReason = Literal["disabled", "inactive"]
ReconcileAction = Literal["none", "resolve", "pause", "resume"]

#: How long an unbroken `held`/`paused` chain may run before its severity rises to error and
#: `hold_chronic` logs once (design Sec 3.2A; spec Sec 4.9.3's incidents table).
CHRONIC_CHAIN_AGE: Final = timedelta(hours=72)
#: How many episodes within `FLAPPING_WINDOW` before `hold_flapping` logs once (design Sec 3.2A).
FLAPPING_EPISODE_THRESHOLD: Final = 3
FLAPPING_WINDOW: Final = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class HoldVerdict:
    """What the caller (`o5b`, every tick) knows right now about the lane one `lane_hold` incident names.

    Pure input to `reconcile`; nothing here is read from the database by this module.
    `probed_run_ids` exists only so this shape does not need to change again for GL-6's probe ladder
    (design Sec 3.2A's `superseded_run_id ∉ probes[].superseded_run_id` discriminator) -- at GL-5 it
    is always empty, because no probe has ever run.
    """

    still_held: bool
    definition_enabled: bool
    lane_active: bool
    lane_quarantined: bool
    superseded_by_operator: bool
    superseded_run_id: uuid.UUID | None
    probed_run_ids: frozenset[uuid.UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class ReconcileOutcome:
    """What `reconcile` decided to do with one hold this tick."""

    action: ReconcileAction
    state: HoldState
    released_by: ReleasedBy | None = None
    pause_reason: PauseReason | None = None


def reconcile(*, state: HoldState, verdict: HoldVerdict) -> ReconcileOutcome:
    """Apply spec Sec 4.9.3 / design Sec 3.2A's reconcile rules to one hold. Pure, DB-free, every tick.

    Rule order mirrors the design's own bullet list, and it matters: a verdict that is no longer held
    resolves the hold NO MATTER its current state (held or paused), because a lane can stop being held
    while it happens to be paused too (the clock or an operator releasing it under an inactive lane,
    say) -- checking `still_held` first means that case is never mistaken for "stay paused". Only once
    the verdict says the hold is genuinely still held do the pause/resume transitions apply, and they
    only ever move a hold BETWEEN held and paused: neither fires once the incident has resolved,
    because a resolved row is renamed by `resolve_lane_incident.sql` and `select_lane_incidents.sql`
    never hands a caller a resolved row to reconcile again in the first place (only the open, or the
    last-7-days-resolved, ones -- and this function is never called on the latter).
    """
    if not verdict.still_held:
        released_by: ReleasedBy = (
            "operator"
            if verdict.superseded_by_operator and verdict.superseded_run_id not in verdict.probed_run_ids
            else "reconciled"
        )
        return ReconcileOutcome(action="resolve", state="held", released_by=released_by)
    action: ReconcileAction = "none" if state == "paused" else "pause"
    if not verdict.definition_enabled:
        return ReconcileOutcome(action=action, state="paused", pause_reason="disabled")
    if verdict.lane_quarantined or not verdict.lane_active:
        return ReconcileOutcome(action=action, state="paused", pause_reason="inactive")
    if state == "paused":
        return ReconcileOutcome(action="resume", state="held")
    return ReconcileOutcome(action="none", state="held")


def is_chain_chronic(chain_first_seen_at: datetime, *, now: datetime) -> bool:
    """True once an unbroken `held`/`paused` chain has run `CHRONIC_CHAIN_AGE` (72 h) or more.

    Excludes probation on purpose (design Sec 3.2A: "Probation is excluded [from chronic]") -- but
    probation does not exist until GL-6, so at GL-5 every hold this is asked about is already
    `held` or `paused` and the exclusion has nothing to exclude yet.
    """
    return now - chain_first_seen_at >= CHRONIC_CHAIN_AGE


def is_chain_flapping(episodes_in_window: int) -> bool:
    """True once a chain has reopened `FLAPPING_EPISODE_THRESHOLD` (3) or more times within 7 days."""
    return episodes_in_window >= FLAPPING_EPISODE_THRESHOLD


# --- The repair breaker: 2 consecutive code/hang/config failures trip a 1/2/4/7-day cooldown ladder --

#: The whole-day cooldown at the breaker's 1st, 2nd, 3rd and every later trip; the ladder holds at its
#: last rung forever after (design Sec 3.4: "1, 2, 4, then 7 days, then allow one run").
REPAIR_BREAKER_COOLDOWN_LADDER_DAYS: Final[tuple[int, ...]] = (1, 2, 4, 7)
#: Consecutive qualifying failures that trip the breaker (design Sec 3.4: "2 consecutive").
REPAIR_BREAKER_TRIP_THRESHOLD: Final = 2
#: The only exit classes that count toward a trip; `invalid_repair_request` (a malformed candidate,
#: never a run failure) and every ok/upstream/infra class never do (design Sec 3.4).
REPAIR_BREAKER_TRIPPING_CLASSES: Final[frozenset[str]] = frozenset({"code", "hang", "config"})


def repair_breaker_cooldown_days(trip_count: int) -> int:
    """The whole days a repair lane is withheld after its `trip_count`-th trip (1-indexed).

    The ladder holds at its last rung (7 days) forever once `trip_count` exceeds its length, so a
    lane that never recovers is checked roughly weekly rather than escalating without bound.
    """
    if trip_count < 1:
        raise ValueError(f"trip_count must be at least 1, got {trip_count}")
    index = min(trip_count, len(REPAIR_BREAKER_COOLDOWN_LADDER_DAYS)) - 1
    return REPAIR_BREAKER_COOLDOWN_LADDER_DAYS[index]


@dataclass(frozen=True, slots=True)
class RepairBreakerState:
    """One lane's repair breaker: how many qualifying failures in a row, how many times it has tripped,
    and when its current cooldown lifts (`None` when it has never tripped, or a success just reset it)."""

    consecutive_failures: int = 0
    trip_count: int = 0
    cooldown_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class RepairBreakerVerdict:
    """The state after folding in one repair run's outcome, and whether THAT trip withholds the lane."""

    state: RepairBreakerState
    tripped_this_turn: bool


def evaluate_repair_breaker(state: RepairBreakerState, *, exit_class: str, now: datetime) -> RepairBreakerVerdict:
    """Fold one repair run's `exit_class` into the breaker (design Sec 3.4).

    The caller filters out `invalid_repair_request` before calling this (it "never counts for the
    breaker" and never reaches here at all). Any class outside `REPAIR_BREAKER_TRIPPING_CLASSES`
    -- in practice `ok` -- is a success and resets the breaker completely, matching "a success resolves
    it": the ladder does not remember a lane's past trips once it has proven it can run clean.
    """
    if exit_class not in REPAIR_BREAKER_TRIPPING_CLASSES:
        return RepairBreakerVerdict(state=RepairBreakerState(), tripped_this_turn=False)
    consecutive = state.consecutive_failures + 1
    if consecutive < REPAIR_BREAKER_TRIP_THRESHOLD:
        return RepairBreakerVerdict(
            state=RepairBreakerState(
                consecutive_failures=consecutive,
                trip_count=state.trip_count,
                cooldown_until=state.cooldown_until,
            ),
            tripped_this_turn=False,
        )
    trip_count = state.trip_count + 1
    cooldown_until = now + timedelta(days=repair_breaker_cooldown_days(trip_count))
    return RepairBreakerVerdict(
        state=RepairBreakerState(consecutive_failures=0, trip_count=trip_count, cooldown_until=cooldown_until),
        tripped_this_turn=True,
    )


def repair_breaker_admits(state: RepairBreakerState, *, now: datetime) -> bool:
    """Whether `plan_gap_repairs` may plan this lane's repair THIS tick (design Sec 3.4).

    Never tripped, or the cooldown has elapsed (the ladder's one allowed probe run): admits. A probe
    run that then fails re-trips the breaker at the next rung through `evaluate_repair_breaker`; one
    that succeeds resets it entirely rather than merely re-arming the same rung.
    """
    return state.cooldown_until is None or now >= state.cooldown_until


__all__ = [
    "CHRONIC_CHAIN_AGE",
    "CLASS_SOURCE_LOST_ATTEMPT",
    "CLASS_SOURCE_MISSING_ATTEMPT",
    "CLASS_SOURCE_NOT_A_HOLD_CLASS",
    "CLASS_SOURCE_STAMPED",
    "CLASS_SOURCE_UNRECOGNISED",
    "FLAPPING_EPISODE_THRESHOLD",
    "FLAPPING_WINDOW",
    "INCIDENT_SEVERITY",
    "REPAIR_BREAKER_COOLDOWN_LADDER_DAYS",
    "REPAIR_BREAKER_TRIPPING_CLASSES",
    "REPAIR_BREAKER_TRIP_THRESHOLD",
    "FinalAttempt",
    "HoldState",
    "HoldVerdict",
    "LaneIncidentRow",
    "PauseReason",
    "ReconcileAction",
    "ReconcileOutcome",
    "ReleasedBy",
    "RepairBreakerState",
    "RepairBreakerVerdict",
    "ResolvedIncident",
    "UpsertedIncident",
    "evaluate_repair_breaker",
    "final_attempt_exit_class",
    "is_chain_chronic",
    "is_chain_flapping",
    "reconcile",
    "repair_breaker_admits",
    "repair_breaker_cooldown_days",
    "resolve_lane_incident",
    "select_lane_incidents",
    "select_run_final_attempt",
    "upsert_lane_incident",
]
