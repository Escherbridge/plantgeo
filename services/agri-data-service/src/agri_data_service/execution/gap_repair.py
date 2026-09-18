"""`agri-service ops jobs-plan-gap-repair`: author bounded repair turns for the lanes' own writers from coverage.

Reads coverage under the `availability` authority only -- one pointer GET and one bounded generation GET
per lane, never an object listing -- judges every lane through `gap_repair_contract.select_repair_candidates`,
and with `--apply` opens one repair run per authorized lane under that lane's repair definition. The tick
then drives the run through `run_scheduled_command`, which rebuilds the writer's own argv from the stored
request. Nothing here reads or writes an environmental table. See `execution/AGENTS.md`, "Bounded gap repair".
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Literal

import click
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from agri_data_service.config import settings
from agri_data_service.db.engine import ingest_session
from agri_data_service.execution.gap_repair_contract import (
    EXECUTOR_REPAIR_WORK_ITEM_KIND,
    REPAIR_BINDINGS,
    REPAIR_DEFAULT_MAX_CANDIDATES,
    REPAIR_DEFAULT_MAX_DAYS_PER_TURN,
    REPAIR_LANE_IDS,
    RepairBudget,
    RepairPlan,
    RepairRequestError,
    select_repair_candidates,
)
from agri_data_service.execution.job_executor_service import (
    LANE_SPECS,
    ActivationConfig,
    ExecutorConfigurationError,
    ensure_lane_definition,
    parse_activation,
    repair_lane_spec,
)
from agri_data_service.execution.job_run_supersession import ledger_target
from agri_data_service.jobs import JobWorkItemSpec, open_job_run
from agri_data_service.jobs.lease import apply_statement_timeout
from agri_data_service.parquet_ops.availability_coverage import (
    AvailabilityCoverageReader,
    merge_direct_lane_rows,
    resolve_availability_lanes,
)
from agri_data_service.parquet_ops.coverage import registered_census_lanes
from agri_data_service.parquet_ops.freshness import render_freshness_report
from agri_data_service.parquet_ops.wire import WarehouseCoverage, render_instant
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence
    from collections.abc import Set as AbstractSet

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.execution.gap_repair_contract import RepairRequest
    from agri_data_service.execution.job_executor_service import LaneExecutionSpec

PLAN_GAP_REPAIR_COMMAND: Final = "agri-service ops jobs-plan-gap-repair"
#: Backlog priority, matching `job_executor_service._work_priority` for a backlog lane.
REPAIR_WORK_ITEM_PRIORITY: Final = 10

#: `write_failed` is the one outcome the process wrapper assigns: the inserts ran but COMMIT did not return.
RepairAuthoringOutcome = Literal["dry_run", "authored", "already_authored", "write_failed"]


class RepairAuthoringRefusal(click.ClickException):
    """A refusal the operator must read; the ledger is untouched."""


@dataclass(frozen=True, slots=True)
class RepairAuthoringReceipt:
    """One authorized lane: the run it was (or would be) filed under, and whether this call created the work."""

    lane_id: str
    repair_lane_id: str
    layer: str
    logical_run_key: str
    shard_key: str
    request: RepairRequest
    outcome: RepairAuthoringOutcome
    run_id: uuid.UUID | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "lane_id": self.lane_id,
            "repair_lane_id": self.repair_lane_id,
            "layer": self.layer,
            "logical_run_key": self.logical_run_key,
            "shard_key": self.shard_key,
            "command_arguments": list(self.request.command_arguments()),
            "request": self.request.to_payload(),
            "outcome": self.outcome,
            "run_id": None if self.run_id is None else str(self.run_id),
        }


@dataclass(frozen=True, slots=True)
class RepairAuthoringReport:
    """What the verb read, judged and (with `--apply`) wrote, as one JSON line."""

    generated_at: datetime
    ledger: str | None
    plan: RepairPlan
    receipts: tuple[RepairAuthoringReceipt, ...]
    freshness: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "event": "plantgeo_job_executor_gap_repair_plan",
            "generated_at": render_instant(self.generated_at),
            "ledger": self.ledger,
            "plan": self.plan.to_dict(),
            "receipts": [receipt.to_dict() for receipt in self.receipts],
            "freshness": self.freshness,
            "ledger_untouched": (
                "only agri.job_run and agri.job_work_item rows under a lane's repair definition are written, "
                "and only with --apply; no Parquet object and no environmental table is touched here"
            ),
        }


def read_parquet_coverage(*, now: datetime) -> WarehouseCoverage:
    """Read every registered lane's coverage from its availability index. Availability authority ONLY, no listing.

    The policy is pinned rather than read from settings: a planner that fell back to the listing census
    would be the request-time historical scan RUNBOOK R2 forbids, and a lane with no index authors no work
    anyway -- it comes back withheld, and a withheld lane's verdict is `coverage_withheld`.
    """
    reader = AvailabilityCoverageReader(BotoAvailabilityStorage.from_settings())
    lanes = registered_census_lanes()
    resolution = resolve_availability_lanes(
        reader,
        lanes=lanes,
        policy="availability",
        now=now,
        rollup=reader.read_rollup(),
    )
    return WarehouseCoverage(
        generated_at=now,
        evaluated_through_day=now.astimezone(UTC).date(),
        lanes=merge_direct_lane_rows(lanes=lanes, resolution=resolution, census_rows=()),
    )


def plan_gap_repairs(  # noqa: PLR0913 - the coverage, the activation, the clock, the budget and two filters
    coverage: WarehouseCoverage,
    *,
    activation: ActivationConfig,
    now: datetime,
    budget: RepairBudget,
    lane_ids: AbstractSet[str] | None = None,
    recently_authored: AbstractSet[str] = frozenset(),
) -> RepairPlan:
    """Judge the coverage rows, optionally only those whose repair binding names one of `lane_ids`."""
    rows = coverage.lanes
    if lane_ids is not None:
        rows = tuple(
            row for row in rows if row.layer in REPAIR_BINDINGS and REPAIR_BINDINGS[row.layer].lane_id in lane_ids
        )
    return select_repair_candidates(
        rows,
        active_lanes=activation.active_lanes,
        now=now,
        budget=budget,
        recently_authored=recently_authored,
    )


def repair_logical_run_key(repair: LaneExecutionSpec, *, now: datetime) -> str:
    """One logical run per repair definition per UTC day: re-running the verb the same day adds shards, not runs."""
    return f"{repair.definition_name}:{now.astimezone(UTC).date().isoformat()}"


async def author_gap_repairs(
    session: AsyncSession | None,
    *,
    plan: RepairPlan,
    now: datetime,
    apply: bool,
) -> tuple[RepairAuthoringReceipt, ...]:
    """Describe, or with `apply` open, one repair run per authorized lane on the caller's session.

    A dry run needs no session at all and touches no ledger. An applied pass never commits: the caller
    commits it. `open_job_run` is idempotent on both the logical run key and the shard key, so a second
    applied pass on the same day reports `already_authored`.
    """
    if apply and session is None:
        raise RepairRequestError("an applied pass needs a ledger session")
    receipts: list[RepairAuthoringReceipt] = []
    for candidate in plan.authorized:
        request = candidate.request
        if request is None:  # pragma: no cover - an authorized candidate always carries its request
            raise RepairRequestError(f"{candidate.layer} is authorized but carries no request")
        spec = LANE_SPECS[request.lane_id]
        repair = repair_lane_spec(spec)
        receipt = RepairAuthoringReceipt(
            lane_id=spec.lane_id,
            repair_lane_id=repair.lane_id,
            layer=request.layer,
            logical_run_key=repair_logical_run_key(repair, now=now),
            shard_key=request.shard_key(),
            request=request,
            outcome="dry_run",
        )
        if not apply or session is None:
            receipts.append(receipt)
            continue
        definition = await ensure_lane_definition(session, repair)
        if definition is None:
            raise RepairAuthoringRefusal(
                f"{repair.lane_id} is paused in the ledger (agri.job_definition.enabled = false); resume it before "
                "authoring repair work it would never run"
            )
        opened = await open_job_run(
            session,
            definition,
            logical_run_key=receipt.logical_run_key,
            scheduled_for=now,
            requested_by=PLAN_GAP_REPAIR_COMMAND,
            target_partitions={"lane_id": spec.lane_id, "layer": request.layer, "repair": True},
            work_items=(
                JobWorkItemSpec(
                    shard_key=receipt.shard_key,
                    kind=EXECUTOR_REPAIR_WORK_ITEM_KIND,
                    payload=request.to_payload(),
                    priority=REPAIR_WORK_ITEM_PRIORITY,
                ),
            ),
        )
        receipts.append(
            replace(
                receipt,
                outcome="authored" if opened.added_work_items else "already_authored",
                run_id=opened.job_run_id,
            )
        )
    return tuple(receipts)


def _resolve_ledger() -> str:
    try:
        return ledger_target(settings.require_local_source_loader_database_url())
    except ValueError as error:
        raise click.ClickException(f"no ledger DSN resolved: {error}") from error
    except ArgumentError as error:
        raise click.ClickException("the ledger DSN could not be parsed; nothing was written") from error


async def _plan_process(
    *,
    lane_ids: AbstractSet[str] | None,
    budget: RepairBudget,
    apply: bool,
) -> RepairAuthoringReport:
    """One coverage read, one plan, and -- only with `apply` -- one transaction that opens the authorized runs."""
    now = datetime.now(UTC)
    activation = parse_activation()
    coverage = await asyncio.to_thread(read_parquet_coverage, now=now)
    plan = plan_gap_repairs(coverage, activation=activation, now=now, budget=budget, lane_ids=lane_ids)
    freshness = render_freshness_report(coverage)
    if not plan.authorized:
        return RepairAuthoringReport(generated_at=now, ledger=None, plan=plan, receipts=(), freshness=freshness)
    if not apply:
        # A dry run opens no ledger connection: the plan is a function of coverage and activation alone.
        described = await author_gap_repairs(None, plan=plan, now=now, apply=False)
        return RepairAuthoringReport(generated_at=now, ledger=None, plan=plan, receipts=described, freshness=freshness)
    ledger = _resolve_ledger()
    receipts: tuple[RepairAuthoringReceipt, ...] = ()
    try:
        async with ingest_session() as session:
            await apply_statement_timeout(session)
            receipts = await author_gap_repairs(session, plan=plan, now=now, apply=True)
            committed = receipts
            receipts = tuple(replace(receipt, outcome="write_failed", run_id=None) for receipt in receipts)
            await session.commit()
            receipts = committed
    except SQLAlchemyError as error:
        if not receipts:
            raise click.ClickException(
                f"ledger access failed ({type(error).__name__}) before any receipt; nothing was written"
            ) from error
        click.echo(
            json.dumps(
                RepairAuthoringReport(
                    generated_at=now, ledger=ledger, plan=plan, receipts=receipts, freshness=freshness
                ).to_dict(),
                sort_keys=True,
            )
        )
        raise click.ClickException(
            f"the ledger failed ({type(error).__name__}) after the receipts above were reached; a write_failed "
            "outcome is NOT durable, re-run with --apply once the ledger answers"
        ) from error
    return RepairAuthoringReport(generated_at=now, ledger=ledger, plan=plan, receipts=receipts, freshness=freshness)


def _validate_lane_ids(lane_ids: Sequence[str]) -> frozenset[str] | None:
    if not lane_ids:
        return None
    unknown = sorted(set(lane_ids) - REPAIR_LANE_IDS)
    if unknown:
        bound = ", ".join(sorted(REPAIR_LANE_IDS))
        raise click.ClickException(f"no repair binding names lane(s) {', '.join(unknown)}; lanes with one: {bound}")
    return frozenset(lane_ids)


@click.command("jobs-plan-gap-repair")
@click.option(
    "--lane",
    "lane_ids",
    multiple=True,
    help="Only lanes named here (repeatable). Default: every lane with a repair binding.",
)
@click.option(
    "--max-days",
    type=int,
    default=REPAIR_DEFAULT_MAX_DAYS_PER_TURN,
    show_default=True,
    help="--max-days handed to each writer, capped at that writer's own ceiling.",
)
@click.option(
    "--max-candidates",
    type=int,
    default=REPAIR_DEFAULT_MAX_CANDIDATES,
    show_default=True,
    help="How many lanes this pass may author work for; the rest are reported deferred_by_budget.",
)
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    help="Open the repair runs. Without it, the plan is printed only.",
)
def jobs_plan_gap_repair(lane_ids: tuple[str, ...], max_days: int, max_candidates: int, apply_changes: bool) -> None:
    """Author bounded gap-repair turns from the published Parquet coverage, for the lanes' own writers.

    Reads each lane's availability index (never a listing), judges every measured gap, and with --apply opens
    one run per authorized lane under `<lane>:gap-repair`; the executor tick drives it with the same writer
    command plus `--max-days` (and `--product` where the writer fans out). Nothing environmental is read
    from or written to PostgreSQL. A lane that is not active, whose coverage is withheld, or whose gaps lie
    beyond its writer's backlog scan is reported with the reason and no work is authored for it.
    """
    try:
        budget = RepairBudget(max_days_per_turn=max_days, max_candidates=max_candidates)
    except RepairRequestError as error:
        raise click.ClickException(str(error)) from error
    selected = _validate_lane_ids(lane_ids)
    try:
        report = asyncio.run(_plan_process(lane_ids=selected, budget=budget, apply=apply_changes))
    except ExecutorConfigurationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(report.to_dict(), sort_keys=True))


__all__ = [
    "PLAN_GAP_REPAIR_COMMAND",
    "REPAIR_WORK_ITEM_PRIORITY",
    "RepairAuthoringOutcome",
    "RepairAuthoringReceipt",
    "RepairAuthoringRefusal",
    "RepairAuthoringReport",
    "author_gap_repairs",
    "jobs_plan_gap_repair",
    "plan_gap_repairs",
    "read_parquet_coverage",
    "repair_logical_run_key",
]
