"""The fenced-lease protocol over agri.job_work_item, agri.job_attempt and agri.job_checkpoint."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Final, Literal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.jobs.registry import EMPTY_JSON_OBJECT

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.engine.row import RowMapping
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import TextClause

    from agri_data_service.jobs.registry import RetryPolicy

# `conductor/code_styleguides/python.md` "Algorithmic excellence": every direct SQL caller pins a
# transaction-local statement timeout. 120s is the repo-wide CLI/procedure convention. A claim that
# blocks past this aborts its own transaction instead of holding a row lock for the whole cron tick.
LEASE_STATEMENT_TIMEOUT_SECONDS: Final = 120

# `job_work_item.last_error_class` and `job_attempt.failure_class` are VARCHAR(255); a longer class
# aborts the UPDATE rather than truncating, which would turn "this shard failed" into "this slice
# crashed". The summary column is TEXT, but an unbounded upstream error body in a shared operator log
# is its own problem, so it is clamped too.
FAILURE_CLASS_MAX_LENGTH: Final = 255
FAILURE_SUMMARY_MAX_LENGTH: Final = 500

UNKNOWN_FAILURE_SUMMARY: Final = "unknown job failure"
LEASE_EXPIRED_FAILURE_CLASS: Final = "lease_expired"
LEASE_EXPIRED_SUMMARY: Final = "lease expired before the worker reported back"
FENCE_LOST_FAILURE_CLASS: Final = "fence_lost"
FENCE_LOST_SUMMARY: Final = "another worker claimed this shard while this attempt was running"
SUPERSEDED_SUMMARY: Final = "this attempt's lease expired and another worker claimed the shard"

# How many consecutive parks a shard may serve before the runtime stops protecting its retry budget.
# A park is normally correct -- `defer_work_item` raises `max_attempts` precisely so that waiting on a
# weekly source polled hourly never dead-letters it -- but nothing bounded it, so a window that parks on
# every tick forever climbed `max_attempts` without limit while `jobs-status` reported it as a healthy
# `deferred`. `jobs-run --budget-seconds` set under a handler's own next-chunk estimate is the concrete
# way to mint one. 24 is half a day of the deployed 30-minute archive cadence with no checkpoint to show
# for it; past that the ceiling stops rising, the retry budget starts closing, and the shard eventually
# dead-letters into a report that says it is missing rather than parking silently for ever.
#
# The count is CONSECUTIVE and resets on progress: only parked attempts whose fencing token is newer than
# the newest checkpoint are counted, so a window that walks a chunk and then yields for the clock -- the
# normal shape of a multi-tick window -- starts again from zero every tick.
MAX_CONSECUTIVE_PARKS: Final = 24

REDACTED_PLACEHOLDER: Final = "[redacted]"

# A URL can carry an API key (FIRMS embeds MAP_KEY in the path, not the query) and a DSN carries a
# password; either one reaching `last_error_summary` publishes it to every operator who reads the
# ledger. Each alternative substitutes a whole whitespace-delimited token rather than parsing it,
# because a partial match leaves the secret in the half that survived: scheme-shaped, user@host-shaped,
# and a bare query tail for a message that names a key without naming its scheme.
#
# Kept deliberately identical to ingest/results.py::_SECRET_SHAPED and NOT shared with it: `jobs` is the
# reusable primitive `ingest` builds on, so importing back the other way would invert the layering.
# Change both together.
_SECRET_SHAPED = re.compile(r"[a-z][a-z0-9+.\-]*://\S+|\S+@\S+|\?\S+", re.IGNORECASE)

FailureDisposition = Literal["retry_wait", "dead_letter"]


class JobLedgerRowError(RuntimeError):
    """Raised when a ledger column comes back in a shape the schema does not declare."""


class JobCursorError(TypeError):
    """Raised when a checkpoint cursor holds a value that cannot be stored as deterministic JSON."""


# Stays inline rather than moving to sql/jobs/: `SET LOCAL` cannot take a bind parameter, so the
# seconds must be interpolated, and the whole statement is one line already visible at its call site.
_STATEMENT_TIMEOUT: Final = text(
    f"-- statement_timeout\nSET LOCAL statement_timeout = '{LEASE_STATEMENT_TIMEOUT_SECONDS}s'"
)

_CLAIM_WORK_ITEM: Final = text(load_query_sql("jobs/claim_work_item.sql"))

_CLOSE_SUPERSEDED_ATTEMPTS: Final = text(load_query_sql("jobs/close_superseded_attempts.sql"))

_LATEST_CHECKPOINT_CURSOR: Final = text(load_query_sql("jobs/latest_checkpoint_cursor.sql"))

_OPEN_ATTEMPT: Final = text(load_query_sql("jobs/open_attempt.sql"))

_MARK_WORK_ITEM_RUNNING: Final = text(load_query_sql("jobs/mark_work_item_running.sql"))

_EXTEND_WORK_ITEM_LEASE: Final = text(load_query_sql("jobs/extend_work_item_lease.sql"))

_EXTEND_ATTEMPT_HEARTBEAT: Final = text(load_query_sql("jobs/extend_attempt_heartbeat.sql"))

_ADVANCE_CHECKPOINT_SEQUENCE: Final = text(load_query_sql("jobs/advance_checkpoint_sequence.sql"))

_APPEND_CHECKPOINT: Final = text(load_query_sql("jobs/append_checkpoint.sql"))

_CLOSE_ATTEMPT_SUCCEEDED: Final = text(load_query_sql("jobs/close_attempt_succeeded.sql"))

_COMPLETE_WORK_ITEM: Final = text(load_query_sql("jobs/complete_work_item.sql"))

_INCREMENT_RUN_SUCCEEDED: Final = text(load_query_sql("jobs/increment_run_succeeded.sql"))

_CLOSE_ATTEMPT_FAILED: Final = text(load_query_sql("jobs/close_attempt_failed.sql"))

_RETRY_WORK_ITEM: Final = text(load_query_sql("jobs/retry_work_item.sql"))

_DEAD_LETTER_WORK_ITEM: Final = text(load_query_sql("jobs/dead_letter_work_item.sql"))

_INCREMENT_RUN_FAILED: Final = text(load_query_sql("jobs/increment_run_failed.sql"))

_CLOSE_ATTEMPT_DEFERRED: Final = text(load_query_sql("jobs/close_attempt_deferred.sql"))

_DEFER_WORK_ITEM: Final = text(load_query_sql("jobs/defer_work_item.sql"))

_CLOSE_ATTEMPT_LOST: Final = text(load_query_sql("jobs/close_attempt_lost.sql"))

_RECLAIM_EXPIRED_LEASES: Final = text(load_query_sql("jobs/reclaim_expired_leases.sql"))

_CLOSE_LOST_ATTEMPTS: Final = text(load_query_sql("jobs/close_lost_attempts.sql"))

_CHECK_RELATIONS_EXIST: Final = text(load_query_sql("jobs/check_relations_exist.sql"))


def clamp_text(value: str, limit: int) -> str:
    """Cut a free-text field to the width its column declares, because a long value aborts the write."""
    stripped = value.strip()
    return stripped if len(stripped) <= limit else stripped[:limit]


def redact_text(value: str) -> str:
    """Substitute every URL-shaped, user@host-shaped and query-shaped token, whole, before it is stored."""
    return _SECRET_SHAPED.sub(REDACTED_PLACEHOLDER, value)


def clamp_summary(value: str) -> str:
    """Redact then clamp one operator-facing summary; redaction runs first so clamping cannot spare a secret."""
    return clamp_text(redact_text(value), FAILURE_SUMMARY_MAX_LENGTH) or UNKNOWN_FAILURE_SUMMARY


def failure_condition_name(error: BaseException) -> str:
    """Name the real driver condition under a SQLAlchemy wrapper, never a statement or a parameter.

    Ported from `routes/ops.py::_panel_error_summary`, which discovered this in production: prod's
    `agri.job_attempt.error_summary` read the tautological literal `'job step failed
    (ProgrammingError)'` for an `UndefinedTable` on a named relation, because `failure_summary` used
    to stop at the outer wrapper's class alone. `routes/ops.py` is not owned by this package and
    still carries its own copy pending a follow-up repoint to this one -- see jobs/AGENTS.md
    "Redaction" for the sibling precedent (`ingest/results.py::failure_reason`) this mirrors.

    WHAT THIS FUNCTION MAY EVALUATE, WHICH IS THE ENTIRE SECURITY ARGUMENT. Every expression below
    is `type(x).__name__`. A Python class name is an identifier -- letters, digits and underscores --
    so nothing this returns can carry a statement, a parameter, a row or a message, whatever it is
    handed. It never reads `str()`, `repr()`, `.statement`, `.params`, `.detail`, `.args` or
    `__context__`. Any future edit that reads a VALUE off an exception rather than its type breaks
    that argument and needs the same scrutiny this function got.

    WHY IT UNWRAPS EXACTLY ONE LEVEL. `orig` is SQLAlchemy's handle on the driver exception, but for
    asyncpg it is the DIALECT's own wrapper rather than the condition: a missing relation arrives as
    ProgrammingError whose `orig` is asyncpg's own ProgrammingError, so the pair reads
    "ProgrammingError: ProgrammingError" -- a tautology saying nothing `type(error).__name__` alone
    did not. The real condition, e.g. UndefinedTableError, hangs off that wrapper's `__cause__`. One
    level is unwrapped and no more: one level is what the dialect adds, and a loop would chase a
    chain of unknown depth for no gain. An error raised outside the driver has no `orig` at all and
    reports its own class alone.
    """
    origin = getattr(error, "orig", None)
    if origin is None:
        return type(error).__name__
    condition = getattr(origin, "__cause__", None) or origin
    return f"{type(error).__name__}: {type(condition).__name__}"


def failure_summary(error: BaseException) -> str:
    """Describe a failure without echoing a statement, a payload, a DSN, or an API-keyed URL."""
    if isinstance(error, SQLAlchemyError):
        # The SQLAlchemy message carries the whole statement and its bound parameters; the class
        # pair from failure_condition_name recovers the real driver condition instead of the outer
        # wrapper's name alone -- "ProgrammingError: UndefinedTable" rather than the tautological
        # "ProgrammingError" a bare error.__class__.__name__ produced.
        #
        # `clamp_summary` here is belt-and-braces, not a live leak fix: every part this branch
        # interpolates is a `type(...).__name__`, which is an identifier and can carry neither a
        # statement nor a parameter. It is applied anyway so that EVERY return of this function has
        # passed redaction and clamping -- an invariant a reader can check locally, rather than one
        # that holds only as long as `failure_condition_name` keeps its own promise.
        return clamp_summary(f"job step failed ({failure_condition_name(error)})")
    return clamp_summary(str(error))


def _json_default(value: object) -> str:
    """Render the few non-JSON values a cursor legitimately carries, and refuse the rest by name."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    raise JobCursorError(f"a job cursor may not carry a {type(value).__name__}")


def canonical_json(payload: Mapping[str, object]) -> str:
    """Render a JSONB payload with sorted keys and no incidental whitespace, so its digest is reproducible."""
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_json_default)


def cursor_checksum(cursor: Mapping[str, object]) -> str:
    """The sha256 hex digest of a cursor's canonical JSON; job_checkpoint has no CHECK on this shape."""
    return hashlib.sha256(canonical_json(cursor).encode("utf-8")).hexdigest()


def _named_columns(row: RowMapping) -> dict[str, object]:
    """Normalise one driver row to a plain name-keyed mapping; a RowMapping's key type is not `str`."""
    return {str(column): value for column, value in row.items()}


async def fetch_row(
    session: AsyncSession,
    statement: TextClause,
    parameters: Mapping[str, object],
) -> Mapping[str, object] | None:
    """Execute one statement and return its first row as a mapping, or None when it matched nothing."""
    result = await session.execute(statement, parameters)
    row = result.mappings().first()
    return None if row is None else _named_columns(row)


async def fetch_rows(
    session: AsyncSession,
    statement: TextClause,
    parameters: Mapping[str, object],
) -> Sequence[Mapping[str, object]]:
    """Execute one statement and return every row as a mapping."""
    result = await session.execute(statement, parameters)
    return [_named_columns(row) for row in result.mappings().all()]


def required_column[ValueT](row: Mapping[str, object], column: str, expected: type[ValueT]) -> ValueT:
    """Read one column as the type the ledger declares, refusing a driver surprise in typed terms."""
    if column not in row:
        raise JobLedgerRowError(f"the ledger returned no {column!r} column")
    value = row[column]
    if not isinstance(value, expected):
        raise JobLedgerRowError(f"{column} came back as {type(value).__name__}, not {expected.__name__}")
    return value


def optional_column[ValueT](row: Mapping[str, object], column: str, expected: type[ValueT]) -> ValueT | None:
    """Read one nullable column, distinguishing a NULL from a wrongly-typed value."""
    if row.get(column) is None:
        return None
    return required_column(row, column, expected)


def required_number(row: Mapping[str, object], column: str) -> float:
    """Read one numeric column as a float, since a double precision zero may arrive as an int."""
    value = row.get(column)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise JobLedgerRowError(f"{column} came back as {type(value).__name__}, not a number")
    return float(value)


async def apply_statement_timeout(session: AsyncSession) -> None:
    """Pin the transaction-local statement timeout; call this once at the start of every transaction."""
    await session.execute(_STATEMENT_TIMEOUT)


@dataclass(frozen=True, slots=True)
class ClaimedWorkItem:
    """One shard this worker holds under a fencing token, plus the cursor its last attempt left behind."""

    work_item_id: uuid.UUID
    job_run_id: uuid.UUID
    attempt_id: uuid.UUID
    worker_id: str
    shard_key: str
    kind: str
    payload: Mapping[str, object]
    cursor: Mapping[str, object] | None
    fencing_token: int
    attempt_number: int
    max_attempts: int
    checkpoint_sequence: int
    progress_fraction: float
    lease_expires_at: datetime

    @property
    def is_final_attempt(self) -> bool:
        """True when a failure on this attempt dead-letters the shard instead of scheduling a retry."""
        return self.attempt_number >= self.max_attempts

    def fence_predicate(self) -> dict[str, object]:
        """The three-column predicate every fenced write repeats; this triple is the worker's whole authority."""
        return {
            "work_item_id": self.work_item_id,
            "fencing_token": self.fencing_token,
            "lease_owner": self.worker_id,
        }


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    """One durable cursor append: where the shard resumes from and how far along it is."""

    sequence: int
    cursor_checksum: str
    progress_fraction: float


@dataclass(frozen=True, slots=True)
class WorkItemFailure:
    """How a failed attempt landed: waiting for another try, or dead-lettered with its budget spent."""

    disposition: FailureDisposition
    attempt_number: int
    max_attempts: int
    failure_class: str
    error_summary: str
    backoff_seconds: float | None = None

    @property
    def is_dead_letter(self) -> bool:
        """True when this shard is done failing and will never be claimed again without operator action."""
        return self.disposition == "dead_letter"


@dataclass(frozen=True, slots=True)
class ReclaimSummary:
    """What the reaper did to leases whose owner never came back."""

    requeued: int = 0
    dead_lettered: int = 0
    attempts_closed: int = 0
    work_item_ids: tuple[uuid.UUID, ...] = field(default=())

    @property
    def total(self) -> int:
        """Every expired lease this pass touched."""
        return self.requeued + self.dead_lettered


async def claim_work_item(
    session: AsyncSession,
    *,
    job_run_id: uuid.UUID,
    worker_id: str,
    lease_seconds: int,
) -> ClaimedWorkItem | None:
    """Claim one eligible shard under a fresh fencing token and open its attempt, or return None for no work."""
    row = await fetch_row(
        session,
        _CLAIM_WORK_ITEM,
        {"job_run_id": job_run_id, "worker_id": worker_id, "lease_seconds": float(lease_seconds)},
    )
    if row is None:
        return None
    work_item_id = required_column(row, "id", uuid.UUID)
    attempt_number = required_column(row, "attempt_count", int)
    fencing_token = required_column(row, "fencing_token", int)
    # Before opening this claim's attempt, close whatever the previous owner left behind. The claim's
    # expired-lease arm is the only path that can supersede a live attempt without the superseded worker
    # participating, and it holds the item's row lock in this very transaction, so this is the one cheap
    # place the orphan can be reaped at all. On a first claim it matches nothing.
    await fetch_rows(
        session,
        _CLOSE_SUPERSEDED_ATTEMPTS,
        {
            "work_item_id": work_item_id,
            "fencing_token": fencing_token,
            "failure_class": LEASE_EXPIRED_FAILURE_CLASS,
            "error_summary": SUPERSEDED_SUMMARY,
        },
    )
    cursor_row = await fetch_row(session, _LATEST_CHECKPOINT_CURSOR, {"work_item_id": work_item_id})
    attempt_row = await fetch_row(
        session,
        _OPEN_ATTEMPT,
        {
            "work_item_id": work_item_id,
            "attempt_number": attempt_number,
            "fencing_token": fencing_token,
            "worker_id": worker_id,
        },
    )
    if attempt_row is None:
        raise JobLedgerRowError("the attempt insert returned no row, so the claim has no fenced identity")
    return ClaimedWorkItem(
        work_item_id=work_item_id,
        job_run_id=required_column(row, "job_run_id", uuid.UUID),
        attempt_id=required_column(attempt_row, "id", uuid.UUID),
        worker_id=worker_id,
        shard_key=required_column(row, "shard_key", str),
        kind=required_column(row, "kind", str),
        payload=required_column(row, "payload", dict),
        cursor=None if cursor_row is None else required_column(cursor_row, "cursor", dict),
        fencing_token=fencing_token,
        attempt_number=attempt_number,
        max_attempts=required_column(row, "max_attempts", int),
        checkpoint_sequence=required_column(row, "checkpoint_sequence", int),
        progress_fraction=required_number(row, "progress_fraction"),
        lease_expires_at=required_column(row, "lease_expires_at", datetime),
    )


async def mark_work_item_running(session: AsyncSession, claim: ClaimedWorkItem, *, lease_seconds: int) -> bool:
    """Move a claimed shard from leased to running, so a crash tells you whether the handler ever started."""
    row = await fetch_row(
        session,
        _MARK_WORK_ITEM_RUNNING,
        {**claim.fence_predicate(), "lease_seconds": float(lease_seconds)},
    )
    return row is not None


async def extend_lease(session: AsyncSession, claim: ClaimedWorkItem, *, lease_seconds: int) -> bool:
    """Push the lease out and stamp a heartbeat; False means the fence moved and the worker must stop."""
    row = await fetch_row(
        session,
        _EXTEND_WORK_ITEM_LEASE,
        {**claim.fence_predicate(), "lease_seconds": float(lease_seconds)},
    )
    if row is None:
        return False
    await fetch_row(session, _EXTEND_ATTEMPT_HEARTBEAT, {"attempt_id": claim.attempt_id})
    return True


async def record_checkpoint(
    session: AsyncSession,
    claim: ClaimedWorkItem,
    *,
    cursor: Mapping[str, object],
    progress_fraction: float,
    lease_seconds: int,
) -> CheckpointRecord | None:
    """Append the next durable cursor under this fence, returning None when the fence has already moved."""
    sequence_row = await fetch_row(
        session,
        _ADVANCE_CHECKPOINT_SEQUENCE,
        {
            **claim.fence_predicate(),
            "lease_seconds": float(lease_seconds),
            "progress_fraction": progress_fraction,
        },
    )
    if sequence_row is None:
        return None
    sequence = required_column(sequence_row, "checkpoint_sequence", int)
    checksum = cursor_checksum(cursor)
    await fetch_row(
        session,
        _APPEND_CHECKPOINT,
        {
            "work_item_id": claim.work_item_id,
            "attempt_id": claim.attempt_id,
            "sequence": sequence,
            "fencing_token": claim.fencing_token,
            "cursor": canonical_json(cursor),
            "cursor_checksum": checksum,
            "progress_fraction": progress_fraction,
        },
    )
    return CheckpointRecord(sequence=sequence, cursor_checksum=checksum, progress_fraction=progress_fraction)


async def complete_work_item(
    session: AsyncSession,
    claim: ClaimedWorkItem,
    *,
    metrics: Mapping[str, object] = EMPTY_JSON_OBJECT,
) -> bool:
    """Close the attempt, the shard and the run counter in that order; False means the fence moved."""
    await fetch_row(
        session,
        _CLOSE_ATTEMPT_SUCCEEDED,
        {**claim.fence_predicate(), "attempt_id": claim.attempt_id, "metrics": canonical_json(metrics)},
    )
    item_row = await fetch_row(session, _COMPLETE_WORK_ITEM, claim.fence_predicate())
    if item_row is None:
        # Attempt-first ordering means a crash between these two statements leaves the shard leased and
        # re-drivable, rather than a completed shard with an attempt still marked running.
        #
        # The item UPDATE stays the authority for this return value even though the attempt close now
        # carries the same fence: the two predicates are identical, so a fence loss matches zero rows in
        # both, and reading the verdict off the row whose status actually decides the shard keeps one
        # answer rather than two that must be argued equal.
        return False
    await fetch_row(session, _INCREMENT_RUN_SUCCEEDED, {"job_run_id": claim.job_run_id})
    return True


async def fail_work_item(  # noqa: PLR0913 - the claim, its policy, its metrics and both failure fields
    session: AsyncSession,
    claim: ClaimedWorkItem,
    *,
    failure_class: str,
    reason: str,
    retry_policy: RetryPolicy,
    metrics: Mapping[str, object] = EMPTY_JSON_OBJECT,
) -> WorkItemFailure | None:
    """Record the failure, then schedule a retry or dead-letter the shard; None means the fence moved."""
    # Redaction happens HERE and not only in the caller. `reason` reaches this function from two places
    # -- an exception, already through failure_summary, and a handler outcome's free-text string, which
    # has been through nothing at all -- and this is the single point both funnel into before the value
    # becomes a durable last_error_summary. See jobs/AGENTS.md "Redaction".
    bounded_class = clamp_text(redact_text(failure_class), FAILURE_CLASS_MAX_LENGTH) or "unknown"
    bounded_summary = clamp_summary(reason)
    await fetch_row(
        session,
        _CLOSE_ATTEMPT_FAILED,
        {
            **claim.fence_predicate(),
            "attempt_id": claim.attempt_id,
            "failure_class": bounded_class,
            "error_summary": bounded_summary,
            "metrics": canonical_json(metrics),
        },
    )
    error_columns = {"failure_class": bounded_class, "error_summary": bounded_summary}
    if claim.is_final_attempt:
        # Exhaustion is dead_letter, never a quiet success: the shard stays visibly unfinished so a
        # completeness report over shard_key reports it as missing rather than as done.
        row = await fetch_row(
            session,
            _DEAD_LETTER_WORK_ITEM,
            {**claim.fence_predicate(), **error_columns},
        )
        if row is None:
            return None
        await fetch_row(session, _INCREMENT_RUN_FAILED, {"job_run_id": claim.job_run_id, "increment": 1})
        return WorkItemFailure(
            disposition="dead_letter",
            attempt_number=claim.attempt_number,
            max_attempts=claim.max_attempts,
            failure_class=bounded_class,
            error_summary=bounded_summary,
        )
    backoff_seconds = retry_policy.backoff_seconds(claim.attempt_number)
    row = await fetch_row(
        session,
        _RETRY_WORK_ITEM,
        {**claim.fence_predicate(), **error_columns, "backoff_seconds": backoff_seconds},
    )
    if row is None:
        return None
    return WorkItemFailure(
        disposition="retry_wait",
        attempt_number=claim.attempt_number,
        max_attempts=claim.max_attempts,
        failure_class=bounded_class,
        error_summary=bounded_summary,
        backoff_seconds=backoff_seconds,
    )


async def defer_work_item(
    session: AsyncSession,
    claim: ClaimedWorkItem,
    *,
    resume_at: datetime | None,
    reason: str,
    metrics: Mapping[str, object] = EMPTY_JSON_OBJECT,
) -> bool:
    """Park the shard until `resume_at` (None means the next tick) without spending its retry budget."""
    # `deferral_reason` is written last so a handler metric of that name cannot shadow the runtime's,
    # and it is redacted here for the same reason fail_work_item redacts: the reason is free text from
    # a handler, and this is the chokepoint it funnels into before it becomes durable.
    parked_metrics = {**metrics, "deferral_reason": clamp_summary(reason)}
    await fetch_row(
        session,
        _CLOSE_ATTEMPT_DEFERRED,
        {**claim.fence_predicate(), "attempt_id": claim.attempt_id, "metrics": canonical_json(parked_metrics)},
    )
    row = await fetch_row(
        session,
        _DEFER_WORK_ITEM,
        {
            **claim.fence_predicate(),
            "resume_at": resume_at,
            "max_consecutive_parks": MAX_CONSECUTIVE_PARKS,
        },
    )
    return row is not None


async def release_lost_attempt(session: AsyncSession, claim: ClaimedWorkItem) -> None:
    """Close this worker's own attempt as lost after it was fenced out, so it stops looking like live work."""
    await fetch_row(
        session,
        _CLOSE_ATTEMPT_LOST,
        {
            "attempt_id": claim.attempt_id,
            "failure_class": FENCE_LOST_FAILURE_CLASS,
            "error_summary": FENCE_LOST_SUMMARY,
        },
    )


async def reclaim_expired_leases(
    session: AsyncSession,
    *,
    backoff_seconds: float,
    job_run_id: uuid.UUID | None = None,
    job_definition_id: uuid.UUID | None = None,
) -> ReclaimSummary:
    """Return every expired lease to the queue (or dead-letter an exhausted one) and close its dangling attempt.

    Both scopes are optional and are ANDed. Pass `job_definition_id` to cover every non-terminal run of a
    lane, which is what a slice wants: it drives only the oldest open run, so a run-scoped reaper leaves an
    expired lease in a sibling run unreclaimable by any tick.
    """
    rows = await fetch_rows(
        session,
        _RECLAIM_EXPIRED_LEASES,
        {
            "job_run_id": job_run_id,
            "job_definition_id": job_definition_id,
            "backoff_seconds": backoff_seconds,
            "failure_class": LEASE_EXPIRED_FAILURE_CLASS,
            "error_summary": LEASE_EXPIRED_SUMMARY,
        },
    )
    if not rows:
        return ReclaimSummary()
    work_item_ids = tuple(required_column(row, "id", uuid.UUID) for row in rows)
    failed_by_run: dict[uuid.UUID, int] = {}
    for row in rows:
        if required_column(row, "status", str) == "dead_letter":
            run_id = required_column(row, "job_run_id", uuid.UUID)
            failed_by_run[run_id] = failed_by_run.get(run_id, 0) + 1
    dead_lettered = sum(failed_by_run.values())
    closed = await fetch_rows(
        session,
        _CLOSE_LOST_ATTEMPTS,
        {
            "work_item_ids": [str(work_item_id) for work_item_id in work_item_ids],
            "failure_class": LEASE_EXPIRED_FAILURE_CLASS,
            "error_summary": LEASE_EXPIRED_SUMMARY,
        },
    )
    for run_id, increment in failed_by_run.items():
        await fetch_row(session, _INCREMENT_RUN_FAILED, {"job_run_id": run_id, "increment": increment})
    return ReclaimSummary(
        requeued=len(rows) - dead_lettered,
        dead_lettered=dead_lettered,
        attempts_closed=len(closed),
        work_item_ids=work_item_ids,
    )


async def find_missing_relations(session: AsyncSession, qualified_names: Sequence[str]) -> tuple[str, ...]:
    """Return every relation in `qualified_names` that does not exist right now, in the input's order.

    The preflight primitive: a lane calls this once, before it opens a run or mints a work item, over
    the relations its handler cannot function without. See jobs/AGENTS.md "Preflight" -- a matview
    lane failing this check with the relation still missing is worse than never trying; it must
    refuse loudly rather than attempt a step certain to raise.
    """
    if not qualified_names:
        return ()
    rows = await fetch_rows(session, _CHECK_RELATIONS_EXIST, {"qualified_names": list(qualified_names)})
    existing = {required_column(row, "qualified_name", str) for row in rows if bool(row.get("relation_exists"))}
    return tuple(name for name in qualified_names if name not in existing)
