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


_STATEMENT_TIMEOUT: Final = text(
    f"-- statement_timeout\nSET LOCAL statement_timeout = '{LEASE_STATEMENT_TIMEOUT_SECONDS}s'"
)

# One statement, because every constraint on these tables is NOT DEFERRABLE and fails at statement
# execution rather than at COMMIT. `status`, `lease_owner`, `lease_expires_at` and `fencing_token` must
# therefore move together (ck_job_work_item_complete_lease_pair and
# ck_job_work_item_active_item_has_fenced_lease), and `attempt_count` with them so the attempt row
# below can use it as its attempt_number.
#
# `attempt_count < max_attempts` is not optional. Without it an exhausted item that is still in a
# claimable state makes `attempt_count + 1` violate ck_job_work_item_attempt_count_within_limit, which
# aborts the whole claim: the worker gets an error where it should have got "no work".
#
# The expired-lease arm is what makes a killed container resumable with no filesystem state -- the next
# tick claims its own abandoned lease straight out of the ledger.
_CLAIM_WORK_ITEM: Final = text("""
-- claim_work_item
WITH candidate AS (
    SELECT item.id
    FROM agri.job_work_item AS item
    WHERE item.job_run_id = :job_run_id
      AND item.attempt_count < item.max_attempts
      AND item.available_at <= now()
      AND (
            (
                item.status IN ('queued', 'retry_wait', 'deferred')
                AND (item.next_attempt_at IS NULL OR item.next_attempt_at <= now())
            )
            OR (
                item.status IN ('leased', 'running')
                AND item.lease_expires_at IS NOT NULL
                AND item.lease_expires_at <= now()
            )
      )
    ORDER BY item.priority DESC, item.available_at, item.id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE agri.job_work_item AS item
SET status = 'leased',
    fencing_token = item.fencing_token + 1,
    attempt_count = item.attempt_count + 1,
    lease_owner = :worker_id,
    lease_expires_at = now() + make_interval(secs => :lease_seconds),
    heartbeat_at = now(),
    next_attempt_at = NULL,
    started_at = COALESCE(item.started_at, now())
FROM candidate
WHERE item.id = candidate.id
RETURNING item.id,
          item.job_run_id,
          item.shard_key,
          item.kind,
          item.payload,
          item.fencing_token,
          item.attempt_count,
          item.max_attempts,
          item.checkpoint_sequence,
          item.progress_fraction,
          item.lease_expires_at
""")

_LATEST_CHECKPOINT_CURSOR: Final = text("""
-- latest_checkpoint_cursor
SELECT checkpoint.cursor
FROM agri.job_checkpoint AS checkpoint
WHERE checkpoint.job_work_item_id = :work_item_id
ORDER BY checkpoint.sequence DESC
LIMIT 1
""")

# attempt_number equals the item's post-increment attempt_count so uq_job_attempt_item_number and
# job_work_item.attempt_count can never diverge, and fencing_token equals the item's post-increment
# token so uq_job_attempt_item_fence is satisfied by construction -- the token only ever grows, is
# never reset on completion, and is therefore never reusable.
_OPEN_ATTEMPT: Final = text("""
-- open_attempt
INSERT INTO agri.job_attempt (job_work_item_id, attempt_number, fencing_token, status, worker_id, started_at)
VALUES (:work_item_id, :attempt_number, :fencing_token, 'running', :worker_id, now())
RETURNING id
""")

_MARK_WORK_ITEM_RUNNING: Final = text("""
-- mark_work_item_running
UPDATE agri.job_work_item
SET status = 'running',
    lease_expires_at = now() + make_interval(secs => :lease_seconds),
    heartbeat_at = now()
WHERE id = :work_item_id
  AND fencing_token = :fencing_token
  AND lease_owner = :lease_owner
  AND status IN ('leased', 'running')
RETURNING id
""")

# `lease_owner` is deliberately left untouched so ck_job_work_item_complete_lease_pair still holds.
# Zero rows back means the fence moved: another worker owns this shard now and this one must stop.
_EXTEND_WORK_ITEM_LEASE: Final = text("""
-- extend_work_item_lease
UPDATE agri.job_work_item
SET heartbeat_at = now(),
    lease_expires_at = now() + make_interval(secs => :lease_seconds)
WHERE id = :work_item_id
  AND fencing_token = :fencing_token
  AND lease_owner = :lease_owner
  AND status IN ('leased', 'running')
RETURNING id
""")

_EXTEND_ATTEMPT_HEARTBEAT: Final = text("""
-- extend_attempt_heartbeat
UPDATE agri.job_attempt
SET heartbeat_at = now()
WHERE id = :attempt_id AND status = 'running'
RETURNING id
""")

# The sequence is taken from the item row under its own lock, never from `SELECT max(sequence)`: two
# writers reading a max derive the same N and one dies on uq_job_checkpoint_item_sequence. This UPDATE
# must precede the INSERT for the same reason.
#
# GREATEST holds the item's progress monotonic. A parked outcome may carry a cursor and still default
# its fraction to zero -- a deferral or a budget yield states where it resumes, not how far it got -- and
# assigning that raw would rewind the shard's progress every time it waits. The checkpoint row keeps the
# raw per-step value; the item column is the high-water mark.
_ADVANCE_CHECKPOINT_SEQUENCE: Final = text("""
-- advance_checkpoint_sequence
UPDATE agri.job_work_item
SET checkpoint_sequence = checkpoint_sequence + 1,
    progress_fraction = GREATEST(progress_fraction, CAST(:progress_fraction AS double precision)),
    heartbeat_at = now(),
    lease_expires_at = now() + make_interval(secs => :lease_seconds)
WHERE id = :work_item_id
  AND fencing_token = :fencing_token
  AND lease_owner = :lease_owner
  AND status IN ('leased', 'running')
RETURNING checkpoint_sequence
""")

_APPEND_CHECKPOINT: Final = text("""
-- append_checkpoint
INSERT INTO agri.job_checkpoint (
    job_work_item_id, job_attempt_id, sequence, fencing_token, cursor, cursor_checksum, progress_fraction
)
VALUES (
    :work_item_id,
    :attempt_id,
    :sequence,
    :fencing_token,
    CAST(:cursor AS jsonb),
    :cursor_checksum,
    :progress_fraction
)
RETURNING id
""")

# THE FENCE LIVES ON THE ITEM, NOT ON THE ATTEMPT, so this locks the item row and closes the attempt only
# while the claim still owns it. Read the CTE before changing any of the three `close_attempt_*` writes
# below, because the obvious cheaper spelling does not work:
#
# `AND job_attempt.fencing_token = :fencing_token` is a TAUTOLOGY here. `_OPEN_ATTEMPT` stamps the attempt
# row with exactly `claim.fencing_token` and nothing ever updates that column, so the predicate matches its
# own row forever and fences nothing. The only column that moves when another worker takes the shard is
# `job_work_item.fencing_token`, so that is the column every fenced write must compare against.
#
# `FOR UPDATE` and not a plain join. Under READ COMMITTED each statement takes its own snapshot, so a bare
# `UPDATE ... FROM job_work_item` could read the item as it was before a competing claim committed and
# close the attempt anyway. Locking the row makes PostgreSQL block on the competing claim, re-evaluate this
# WHERE against the row it committed, and match zero -- and the item UPDATE that follows wants that same
# lock one statement later regardless, so this moves the wait rather than adding one. The transaction-local
# `statement_timeout` bounds the block.
#
# Callers still roll back on a False/None return; see jobs/AGENTS.md "The fence guards the attempt too".
_CLOSE_ATTEMPT_SUCCEEDED: Final = text("""
-- close_attempt_succeeded
WITH fence AS (
    SELECT item.id
    FROM agri.job_work_item AS item
    WHERE item.id = :work_item_id
      AND item.fencing_token = :fencing_token
      AND item.lease_owner = :lease_owner
      AND item.status IN ('leased', 'running')
    FOR UPDATE
)
UPDATE agri.job_attempt AS attempt
SET status = 'succeeded', finished_at = now(), metrics = CAST(:metrics AS jsonb)
FROM fence
WHERE attempt.id = :attempt_id
  AND attempt.job_work_item_id = fence.id
  AND attempt.status = 'running'
RETURNING attempt.id
""")

_COMPLETE_WORK_ITEM: Final = text("""
-- complete_work_item
UPDATE agri.job_work_item
SET status = 'succeeded',
    completed_at = now(),
    progress_fraction = 1,
    lease_owner = NULL,
    lease_expires_at = NULL,
    next_attempt_at = NULL,
    heartbeat_at = now()
WHERE id = :work_item_id
  AND fencing_token = :fencing_token
  AND lease_owner = :lease_owner
  AND status IN ('leased', 'running')
RETURNING id
""")

# GREATEST/LEAST rather than a bare +1. ck_job_run_work_item_counts_within_total is IMMEDIATE, so a
# counter bump past `total_work_items` aborts the completion transaction and loses a shard that
# actually succeeded. Clamping never decreases the counter and never crosses the total; the slice's
# closing rollup recomputes the true value from the work items themselves.
_INCREMENT_RUN_SUCCEEDED: Final = text("""
-- increment_run_succeeded
UPDATE agri.job_run
SET succeeded_work_items = GREATEST(
        succeeded_work_items,
        LEAST(succeeded_work_items + 1, total_work_items - failed_work_items)
    )
WHERE id = :job_run_id
RETURNING succeeded_work_items
""")

# `metrics` travels with the failure. What a dead-lettered window actually managed -- chunks walked,
# records seen, records written -- is exactly what an operator needs to tell "upstream had nothing" from
# "we never reached upstream", and dropping it on the failure path leaves that question unanswerable
# from the ledger alone. The column is NOT NULL DEFAULT '{}', so an empty object is always legal.
#
# Fenced through the item exactly as `_CLOSE_ATTEMPT_SUCCEEDED` is, and for the same reason: 'failed' is a
# terminal attempt state, so writing it on a shard another worker owns records a verdict this worker had
# no authority to reach. See that statement's comment for why the item row and not the attempt row.
_CLOSE_ATTEMPT_FAILED: Final = text("""
-- close_attempt_failed
WITH fence AS (
    SELECT item.id
    FROM agri.job_work_item AS item
    WHERE item.id = :work_item_id
      AND item.fencing_token = :fencing_token
      AND item.lease_owner = :lease_owner
      AND item.status IN ('leased', 'running')
    FOR UPDATE
)
UPDATE agri.job_attempt AS attempt
SET status = 'failed',
    finished_at = now(),
    failure_class = :failure_class,
    error_summary = :error_summary,
    metrics = CAST(:metrics AS jsonb)
FROM fence
WHERE attempt.id = :attempt_id
  AND attempt.job_work_item_id = fence.id
  AND attempt.status = 'running'
RETURNING attempt.id
""")

_RETRY_WORK_ITEM: Final = text("""
-- retry_work_item
UPDATE agri.job_work_item
SET status = 'retry_wait',
    next_attempt_at = now() + make_interval(secs => :backoff_seconds),
    lease_owner = NULL,
    lease_expires_at = NULL,
    heartbeat_at = now(),
    last_error_class = :failure_class,
    last_error_summary = :error_summary
WHERE id = :work_item_id
  AND fencing_token = :fencing_token
  AND lease_owner = :lease_owner
  AND status IN ('leased', 'running')
RETURNING id
""")

_DEAD_LETTER_WORK_ITEM: Final = text("""
-- dead_letter_work_item
UPDATE agri.job_work_item
SET status = 'dead_letter',
    completed_at = now(),
    next_attempt_at = NULL,
    lease_owner = NULL,
    lease_expires_at = NULL,
    heartbeat_at = now(),
    last_error_class = :failure_class,
    last_error_summary = :error_summary
WHERE id = :work_item_id
  AND fencing_token = :fencing_token
  AND lease_owner = :lease_owner
  AND status IN ('leased', 'running')
RETURNING id
""")

_INCREMENT_RUN_FAILED: Final = text("""
-- increment_run_failed
UPDATE agri.job_run
SET failed_work_items = GREATEST(
        failed_work_items,
        LEAST(failed_work_items + :increment, total_work_items - succeeded_work_items)
    )
WHERE id = :job_run_id
RETURNING failed_work_items
""")

# A deferral is not a failure, so its reason goes in the attempt's free-form `metrics` rather than in
# `error_summary`, which an operator scanning for real failures reads.
#
# Fenced through the item exactly as `_CLOSE_ATTEMPT_SUCCEEDED` is. 'deferred' is terminal for the attempt
# even though it is not terminal for the item, so a fenced-out worker parking someone else's shard still
# writes a verdict it had no authority to reach.
_CLOSE_ATTEMPT_DEFERRED: Final = text("""
-- close_attempt_deferred
WITH fence AS (
    SELECT item.id
    FROM agri.job_work_item AS item
    WHERE item.id = :work_item_id
      AND item.fencing_token = :fencing_token
      AND item.lease_owner = :lease_owner
      AND item.status IN ('leased', 'running')
    FOR UPDATE
)
UPDATE agri.job_attempt AS attempt
SET status = 'deferred', finished_at = now(), metrics = CAST(:metrics AS jsonb)
FROM fence
WHERE attempt.id = :attempt_id
  AND attempt.job_work_item_id = fence.id
  AND attempt.status = 'running'
RETURNING attempt.id
""")

# `max_attempts + 1` is the load-bearing part. The claim already spent an attempt on this item, and a
# deferral means upstream had nothing to give -- charging the item's failure budget for that would
# dead-letter a weekly source polled hourly after five hours of perfectly correct waiting. Raising the
# ceiling is legal (ck_job_work_item_positive_work_item_max_attempts and attempt_count_within_limit
# are both satisfied) and it keeps attempt_number dense-unique, which decrementing attempt_count would
# not. See jobs/AGENTS.md "A deferral must not spend the retry budget".
_DEFER_WORK_ITEM: Final = text("""
-- defer_work_item
UPDATE agri.job_work_item
SET status = 'deferred',
    next_attempt_at = COALESCE(CAST(:resume_at AS timestamptz), now()),
    max_attempts = max_attempts + 1,
    lease_owner = NULL,
    lease_expires_at = NULL,
    heartbeat_at = now()
WHERE id = :work_item_id
  AND fencing_token = :fencing_token
  AND lease_owner = :lease_owner
  AND status IN ('leased', 'running')
RETURNING id
""")

# Nothing in the schema reaps an abandoned attempt: without this, a fenced-out worker's attempt row
# sits in 'running' forever and every incident query that counts running attempts lies.
#
# THE ONE ATTEMPT CLOSE THAT MUST NOT CARRY A FENCE, and the exception is the whole point: this statement
# runs precisely when the fence has already moved, so fencing it the way the three closes above are fenced
# would match zero rows and leave the dangling 'running' attempt it exists to reap. It is safe unfenced
# because 'lost' is the only verdict a superseded worker is ever entitled to reach about its own attempt,
# and `id = :attempt_id` addresses that attempt and no other.
_CLOSE_ATTEMPT_LOST: Final = text("""
-- close_attempt_lost
UPDATE agri.job_attempt
SET status = 'lost',
    finished_at = now(),
    failure_class = :failure_class,
    error_summary = :error_summary
WHERE id = :attempt_id AND status = 'running'
RETURNING id
""")

# The reaper runs without a fence, because there is no fence to hold: every candidate row's owner is
# by definition gone. It deliberately does NOT bump `fencing_token` -- the token is bumped only by the
# next successful claim, so a zombie's token is already permanently stale, and bumping here would burn
# a token with no attempt row behind it and break the fk_job_checkpoint_attempt_fence invariant.
# It reclaims to 'retry_wait' rather than 'queued' because 'queued' with a stale lease pair passes
# every CHECK and produces a row that lies about who owns it.
_RECLAIM_EXPIRED_LEASES: Final = text("""
-- reclaim_expired_leases
WITH expired AS (
    SELECT item.id
    FROM agri.job_work_item AS item
    WHERE item.status IN ('leased', 'running')
      AND item.lease_expires_at IS NOT NULL
      AND item.lease_expires_at < now()
      AND (CAST(:job_run_id AS uuid) IS NULL OR item.job_run_id = CAST(:job_run_id AS uuid))
    FOR UPDATE SKIP LOCKED
), reclaimed AS (
    UPDATE agri.job_work_item AS item
    SET status = CASE
            WHEN item.attempt_count >= item.max_attempts THEN 'dead_letter'
            ELSE 'retry_wait'
        END,
        next_attempt_at = CASE
            WHEN item.attempt_count >= item.max_attempts THEN NULL
            ELSE now() + make_interval(secs => :backoff_seconds)
        END,
        completed_at = CASE
            WHEN item.attempt_count >= item.max_attempts THEN COALESCE(item.completed_at, now())
            ELSE item.completed_at
        END,
        lease_owner = NULL,
        lease_expires_at = NULL,
        last_error_class = :failure_class,
        last_error_summary = :error_summary
    FROM expired
    WHERE item.id = expired.id
    RETURNING item.id, item.job_run_id, item.status
)
SELECT id, job_run_id, status FROM reclaimed
""")

_CLOSE_LOST_ATTEMPTS: Final = text("""
-- close_lost_attempts
UPDATE agri.job_attempt
SET status = 'lost',
    finished_at = now(),
    failure_class = :failure_class,
    error_summary = :error_summary
WHERE status = 'running'
  AND job_work_item_id = ANY(CAST(:work_item_ids AS uuid[]))
RETURNING id
""")


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


def failure_summary(error: BaseException) -> str:
    """Describe a failure without echoing a statement, a payload, a DSN, or an API-keyed URL."""
    if isinstance(error, SQLAlchemyError):
        # The SQLAlchemy message carries the whole statement and its bound parameters.
        return f"job step failed ({error.__class__.__name__})"
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
        {**claim.fence_predicate(), "resume_at": resume_at},
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
) -> ReclaimSummary:
    """Return every expired lease to the queue (or dead-letter an exhausted one) and close its dangling attempt."""
    rows = await fetch_rows(
        session,
        _RECLAIM_EXPIRED_LEASES,
        {
            "job_run_id": job_run_id,
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
