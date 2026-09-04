"""`agri-service ops jobs-supersede-run`: release a lane the executor holds behind a failed checkpoint run.

The executor holds a lane whose latest checkpoint run settled `failed` or `partial` until its catch-up
policy's clock releases it (a `coalesce_latest` lane's next bucket supersedes a transient failure) or, once
the failure streak reaches the policy's limit (three for `coalesce_latest`, one for `replay_oldest`, whose
every bucket is owed), until an operator records a supersession with evidence. This verb records exactly
that: one resolved `agri.job_incident` row keyed by the run id. The run, its dead-lettered work item and
its attempts are never written, so the failure record stays whole. It rules by the planner's own
`judge_failed_checkpoint`, so what it refuses and the bucket it names are what the scheduler will do.
See execution/AGENTS.md, "Failed checkpoints are superseded by the clock or by an operator".
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Literal

import click
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from agri_data_service.config import settings
from agri_data_service.db.engine import ingest_session
from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.execution.job_executor_service import (
    CLOCK_RELEASE_STREAK_LIMIT,
    LANE_SPECS,
    RUN_SUPERSESSION_FINGERPRINT_PREFIX,
    RUN_SUPERSESSION_INCIDENT_TYPE,
    SETTLED_WITHOUT_SUCCESS,
    SUPERSEDE_RUN_COMMAND,
    ActivationConfig,
    CheckpointVerdict,
    ExecutorConfigurationError,
    LaneExecutionSpec,
    LatestRun,
    judge_failed_checkpoint,
    parse_activation,
    read_lane_checkpoint,
)
from agri_data_service.jobs import read_lane_pause_state
from agri_data_service.jobs.lease import (
    apply_statement_timeout,
    canonical_json,
    fetch_row,
    fetch_rows,
    optional_column,
    required_column,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession

_SELECT_RUN_WORK_ITEMS: Final = text(load_query_sql("execution/select_executor_run_work_items.sql"))
_SELECT_SUPERSESSION_INCIDENT: Final = text(load_query_sql("execution/select_run_supersession_incident.sql"))
_INSERT_SUPERSESSION_INCIDENT: Final = text(load_query_sql("execution/insert_run_supersession_incident.sql"))

#: `agri.job_incident.summary` is unbounded text; the cap keeps a receipt readable, not the column safe.
EVIDENCE_MAX_LENGTH: Final = 2000
#: `agri.job_incident.owner` and `acknowledged_by` are varchar(255).
OPERATOR_MAX_LENGTH: Final = 255
#: The receipt keys copied verbatim into the incident's `detail`, so the row explains itself without a join.
_RECORDED_DETAIL_KEYS: Final[tuple[str, ...]] = (
    "lane_id",
    "catch_up_policy",
    "scheduled_for",
    "run_status",
    "consecutive_failures",
    "opens_no_earlier_than",
    "work_items",
)

#: `write_failed` is the one outcome the process wrapper assigns: the insert ran but its COMMIT did not
#: return, so nothing is durable and the receipt must not say `recorded`.
SupersessionOutcome = Literal["dry_run", "already_superseded", "recorded", "write_failed"]


class SupersessionRefusal(click.ClickException):
    """A refusal the operator must read; the ledger is untouched."""


@dataclass(frozen=True, slots=True)
class RunWorkItem:
    """One shard of the checkpoint run and how its final attempt ended."""

    work_item_id: uuid.UUID
    shard_key: str
    status: str
    attempt_count: int
    max_attempts: int
    completed_at: datetime | None
    final_attempt_number: int | None
    failure_class: str | None
    error_summary: str | None
    finished_at: datetime | None

    def to_dict(self) -> dict[str, object]:
        return {
            "work_item_id": str(self.work_item_id),
            "shard_key": self.shard_key,
            "status": self.status,
            "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts,
            "completed_at": None if self.completed_at is None else self.completed_at.isoformat(),
            "final_attempt_number": self.final_attempt_number,
            "failure_class": self.failure_class,
            "error_summary": self.error_summary,
            "finished_at": None if self.finished_at is None else self.finished_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SupersessionReceipt:
    """What the verb read, the one outcome it reached, and the earliest bucket the scheduler may open.

    `evidence` and `operator` are what the ledger holds: the caller's own text for a dry run or a
    recording, the FIRST recording's text when the run was already superseded. `next_bucket` is the
    earliest bucket a release can open; a tick that runs later opens its own current bucket instead.
    """

    ledger: str
    lane_id: str
    catch_up_policy: str
    run_id: uuid.UUID
    scheduled_for: datetime
    run_status: str
    consecutive_failures: int
    next_bucket: datetime
    work_items: tuple[RunWorkItem, ...]
    evidence: str
    operator: str
    fingerprint: str
    outcome: SupersessionOutcome
    incident_id: uuid.UUID | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "event": "plantgeo_job_executor_run_supersession",
            "ledger": self.ledger,
            "lane_id": self.lane_id,
            "catch_up_policy": self.catch_up_policy,
            "run_id": str(self.run_id),
            "scheduled_for": self.scheduled_for.isoformat(),
            "run_status": self.run_status,
            "consecutive_failures": self.consecutive_failures,
            "opens_no_earlier_than": self.next_bucket.isoformat(),
            "work_items": [item.to_dict() for item in self.work_items],
            "evidence": self.evidence,
            "operator": self.operator,
            "fingerprint": self.fingerprint,
            "outcome": self.outcome,
            "incident_id": None if self.incident_id is None else str(self.incident_id),
            "ledger_untouched": (
                "the run, its work items and its attempts are never written; only one resolved "
                "agri.job_incident row is recorded"
            ),
        }


class LedgerFailedAfterReceipt(click.ClickException):
    """The ledger failed after a receipt was reached; the receipt printed above says exactly what is durable."""

    def __init__(self, receipt: SupersessionReceipt, error: SQLAlchemyError) -> None:
        if receipt.outcome == "write_failed":
            message = (
                f"the ledger COMMIT failed ({type(error).__name__}); the recording is NOT durable and the receipt "
                "above says write_failed -- re-run with --apply once the ledger answers"
            )
        else:
            message = (
                f"the ledger connection failed while closing ({type(error).__name__}); the receipt above was "
                "reached and its outcome is durable, so re-run without --apply to confirm before recording again"
            )
        super().__init__(message)
        self.receipt = receipt


def supersession_fingerprint(run_id: uuid.UUID) -> str:
    """The unique incident fingerprint `select_latest_run.sql` probes for this run."""
    return f"{RUN_SUPERSESSION_FINGERPRINT_PREFIX}{run_id}"


def ledger_target(database_url: str) -> str:
    """Name the ledger a DSN reaches as host:port/database, never its credentials."""
    url = make_url(database_url)
    port = "" if url.port is None else f":{url.port}"
    return f"{url.host or '?'}{port}/{url.database or '?'}"


def resolve_executor_lane(lane_id: str, activation: ActivationConfig) -> LaneExecutionSpec:
    """Return the executor lane this verb may act on: known, executable, and in the active allow-list."""
    spec = LANE_SPECS.get(lane_id)
    if spec is None:
        raise SupersessionRefusal(f"unknown lane {lane_id!r}; `agri-service ops jobs-executor --inventory` lists them")
    if not spec.executable:
        raise SupersessionRefusal(f"lane {lane_id!r} is {spec.migration_disposition} and never opens executor buckets")
    if not activation.is_active(lane_id):
        raise SupersessionRefusal(
            f"lane {lane_id!r} is not in the executor's active allow-list, so the scheduler never plans it and a "
            "recording would never be read; activate the lane first"
        )
    return spec


def _work_item_from_row(row: Mapping[str, object]) -> RunWorkItem:
    return RunWorkItem(
        work_item_id=required_column(row, "work_item_id", uuid.UUID),
        shard_key=required_column(row, "shard_key", str),
        status=required_column(row, "status", str),
        attempt_count=required_column(row, "attempt_count", int),
        max_attempts=required_column(row, "max_attempts", int),
        completed_at=optional_column(row, "completed_at", datetime),
        final_attempt_number=optional_column(row, "attempt_number", int),
        failure_class=optional_column(row, "failure_class", str),
        error_summary=optional_column(row, "error_summary", str),
        finished_at=optional_column(row, "finished_at", datetime),
    )


def _refuse_unless_held(
    spec: LaneExecutionSpec,
    latest: LatestRun | None,
    run_id: uuid.UUID,
    now: datetime,
) -> tuple[LatestRun, CheckpointVerdict]:
    """Admit only the one run that holds this lane and that the clock will not release by itself."""
    if latest is None:
        raise SupersessionRefusal(f"lane {spec.lane_id!r} has no checkpoint run; nothing holds it")
    if latest.run_id != run_id:
        raise SupersessionRefusal(
            f"run {run_id} is not the checkpoint of lane {spec.lane_id!r}; its checkpoint is run {latest.run_id} "
            f"(bucket {latest.scheduled_for.isoformat()}, status {latest.status}), and only that run can hold it"
        )
    if latest.open:
        raise SupersessionRefusal(
            f"run {run_id} is still {latest.status}; wait for it to settle or cancel its work -- nothing to supersede"
        )
    if latest.status not in SETTLED_WITHOUT_SUCCESS:
        raise SupersessionRefusal(f"run {run_id} settled {latest.status}; lane {spec.lane_id!r} is not held")
    verdict = judge_failed_checkpoint(spec, latest, now)
    if verdict.release == "clock":
        when = "on the next tick" if verdict.newer_bucket_exists else "when the clock reaches it"
        raise SupersessionRefusal(
            f"the clock releases lane {spec.lane_id!r} by itself: bucket {verdict.next_bucket.isoformat()} opens "
            f"{when} ({verdict.consecutive_failures} consecutive failure(s), below this {spec.catch_up_policy} lane's "
            f"limit of {CLOCK_RELEASE_STREAK_LIMIT[spec.catch_up_policy]}); nothing to record"
        )
    return latest, verdict


async def _recorded_receipt(session: AsyncSession, receipt: SupersessionReceipt) -> SupersessionReceipt:
    """Report an existing recording with the evidence and operator the ledger holds, not the ones supplied."""
    row = await fetch_row(session, _SELECT_SUPERSESSION_INCIDENT, {"fingerprint": receipt.fingerprint})
    if row is None:
        # The probe or the conflict clause said a recording exists, but this statement's snapshot cannot see
        # it (it was deleted between statements). Refuse rather than assert an outcome nobody can read.
        raise SupersessionRefusal(
            f"a recording for run {receipt.run_id} was detected but could not be read back; nothing was written "
            "by this call -- re-run without --apply to confirm the ledger's state"
        )
    return replace(
        receipt,
        outcome="already_superseded",
        incident_id=required_column(row, "id", uuid.UUID),
        evidence=required_column(row, "summary", str),
        operator=optional_column(row, "owner", str) or "",
    )


async def supersede_failed_run(  # noqa: PLR0913 - one parameter per fact the receipt records
    session: AsyncSession,
    spec: LaneExecutionSpec,
    run_id: uuid.UUID,
    *,
    ledger: str,
    evidence: str,
    operator: str,
    now: datetime,
    apply: bool,
) -> SupersessionReceipt:
    """Describe, or with `apply` record, the supersession of the checkpoint run holding this lane.

    Reads and writes on the caller's session inside the caller's transaction and never commits or rolls
    back: the caller commits a `recorded` outcome and discards everything else.
    """
    pause = await read_lane_pause_state(session, spec.definition_name)
    if pause.paused:
        raise SupersessionRefusal(
            f"lane {spec.lane_id!r} is paused in the ledger (agri.job_definition.enabled = false), so the scheduler "
            "never plans it and a recording would never be read; resume the lane first"
        )
    latest, verdict = _refuse_unless_held(spec, await read_lane_checkpoint(session, spec), run_id, now)
    rows = await fetch_rows(session, _SELECT_RUN_WORK_ITEMS, {"job_run_id": run_id})
    work_items = tuple(_work_item_from_row(row) for row in rows)
    receipt = SupersessionReceipt(
        ledger=ledger,
        lane_id=spec.lane_id,
        catch_up_policy=spec.catch_up_policy,
        run_id=run_id,
        scheduled_for=latest.scheduled_for,
        run_status=latest.status,
        consecutive_failures=verdict.consecutive_failures,
        next_bucket=verdict.next_bucket,
        work_items=work_items,
        evidence=evidence,
        operator=operator,
        fingerprint=supersession_fingerprint(run_id),
        outcome="dry_run",
    )
    if latest.superseded_by_operator:
        return await _recorded_receipt(session, receipt)
    if not apply:
        return receipt
    dead_letters = [item for item in work_items if item.status == "dead_letter"]
    receipt_fields = receipt.to_dict()
    detail = {key: receipt_fields[key] for key in _RECORDED_DETAIL_KEYS} | {
        "recorded_at": now.isoformat(),
        "recorded_by": SUPERSEDE_RUN_COMMAND,
    }
    row = await fetch_row(
        session,
        _INSERT_SUPERSESSION_INCIDENT,
        {
            "fingerprint": receipt.fingerprint,
            "incident_type": RUN_SUPERSESSION_INCIDENT_TYPE,
            "job_run_id": run_id,
            "job_work_item_id": dead_letters[0].work_item_id if dead_letters else None,
            "summary": evidence,
            "owner": operator,
            "acknowledged_by": operator,
            "detail": canonical_json(detail),
        },
    )
    if row is None:
        # Somebody recorded it between our probe and our insert. PostgreSQL's ON CONFLICT DO NOTHING waits
        # for a concurrent speculative insert to commit or abort before deciding, so the winner is durable
        # and readable by the statement that follows.
        return await _recorded_receipt(session, receipt)
    return replace(receipt, outcome="recorded", incident_id=required_column(row, "id", uuid.UUID))


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _resolve_ledger() -> str:
    """Name the ledger the session will open, refusing in one line when no DSN resolves or it cannot be parsed."""
    try:
        return ledger_target(settings.require_local_source_loader_database_url())
    except ValueError as error:
        raise click.ClickException(f"no ledger DSN resolved: {error}") from error
    except ArgumentError as error:
        # Never echo the DSN: it carries the credential.
        raise click.ClickException("the ledger DSN could not be parsed; nothing was recorded") from error


async def _supersede_process(
    spec: LaneExecutionSpec,
    run_id: uuid.UUID,
    *,
    evidence: str,
    operator: str,
    apply: bool,
) -> SupersessionReceipt:
    """One session, one transaction: commit only a recorded outcome, discard every other read.

    A receipt never claims more than the ledger did: `recorded` is assigned only after COMMIT returned,
    a COMMIT that raised prints `write_failed`, and a failure after that prints the receipt as reached.
    """
    ledger = _resolve_ledger()
    receipt: SupersessionReceipt | None = None
    try:
        async with ingest_session() as session:
            await apply_statement_timeout(session)
            described = await supersede_failed_run(
                session,
                spec,
                run_id,
                ledger=ledger,
                evidence=evidence,
                operator=operator,
                now=_utc_now(),
                apply=apply,
            )
            if described.outcome != "recorded":
                receipt = described
                await session.rollback()
            else:
                receipt = replace(described, outcome="write_failed", incident_id=None)
                await session.commit()
                receipt = described
    except SQLAlchemyError as error:
        if receipt is None:
            raise click.ClickException(
                f"ledger access failed ({type(error).__name__}) before any receipt; nothing was recorded"
            ) from error
        raise LedgerFailedAfterReceipt(receipt, error) from error
    return receipt


def _bounded_text(value: str, *, name: str, limit: int) -> str:
    stripped = value.strip()
    if not stripped:
        raise click.BadParameter(f"--{name} must not be empty")
    if len(stripped) > limit:
        raise click.BadParameter(f"--{name} must be at most {limit} characters")
    return stripped


@click.command("jobs-supersede-run")
@click.option(
    "--lane",
    "lane_id",
    required=True,
    help="Executor lane id whose failed checkpoint run is being superseded.",
)
@click.option(
    "--run-id",
    "run_id",
    required=True,
    type=click.UUID,
    help="The agri.job_run id that settled failed or partial; it must be the lane's latest checkpoint.",
)
@click.option(
    "--evidence",
    required=True,
    help="Why the failure is understood and the next bucket may open (cause, fix commit, or reference); "
    "recorded verbatim as the incident summary.",
)
@click.option("--operator", required=True, help="Who is recording the supersession; recorded as the incident owner.")
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    default=False,
    help="Record the supersession; the default is a dry run that writes nothing.",
)
def jobs_supersede_run(lane_id: str, run_id: uuid.UUID, evidence: str, operator: str, apply_changes: bool) -> None:
    """Release a lane held behind its failed checkpoint run without touching the dead letter.

    Records one resolved agri.job_incident row keyed by the run id and carrying EVIDENCE; the scheduler then
    resumes the lane at its current bucket. Refuses a lane that is not active or is paused, a run that is
    not the lane's checkpoint, is still open, or did not fail, and a lane the clock will release by itself
    (a coalesce_latest lane below its three-failure breaker). Prints one JSON receipt naming the ledger
    written and the dead letters left standing; nothing else is written to stdout.
    """
    bounded_evidence = _bounded_text(evidence, name="evidence", limit=EVIDENCE_MAX_LENGTH)
    bounded_operator = _bounded_text(operator, name="operator", limit=OPERATOR_MAX_LENGTH)
    try:
        activation = parse_activation()
    except ExecutorConfigurationError as error:
        raise click.ClickException(f"executor activation is invalid: {error}") from error
    spec = resolve_executor_lane(lane_id, activation)
    try:
        receipt = asyncio.run(
            _supersede_process(
                spec,
                run_id,
                evidence=bounded_evidence,
                operator=bounded_operator,
                apply=apply_changes,
            )
        )
    except LedgerFailedAfterReceipt as failed:
        click.echo(json.dumps(failed.receipt.to_dict(), sort_keys=True))
        raise
    click.echo(json.dumps(receipt.to_dict(), sort_keys=True))


__all__ = [
    "EVIDENCE_MAX_LENGTH",
    "OPERATOR_MAX_LENGTH",
    "LedgerFailedAfterReceipt",
    "RunWorkItem",
    "SupersessionOutcome",
    "SupersessionReceipt",
    "SupersessionRefusal",
    "jobs_supersede_run",
    "ledger_target",
    "resolve_executor_lane",
    "supersede_failed_run",
    "supersession_fingerprint",
]
