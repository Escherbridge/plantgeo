"""Durable executor pause/resume with an atomic audit; see execution/AGENTS.md."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Final

import click
from sqlalchemy import text
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from agri_data_service.config import settings
from agri_data_service.db.engine import ingest_session
from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.execution.job_executor_service import EXECUTOR_DEFINITION_VERSION, LANE_SPECS
from agri_data_service.execution.job_run_supersession import ledger_target
from agri_data_service.jobs.lease import apply_statement_timeout, canonical_json, fetch_row, fetch_rows, required_column

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

_SELECT: Final = text(load_query_sql("execution/select_executor_lane_definitions.sql"))
_LOCK: Final = text(load_query_sql("execution/lock_executor_lane_definitions.sql"))
_UPDATE: Final = text(load_query_sql("execution/set_executor_lane_enabled.sql"))
_AUDIT: Final = text(load_query_sql("execution/insert_executor_lane_control_incident.sql"))
MAX_VERSIONS: Final = 32
MAX_REASON: Final = 2000
MAX_OPERATOR: Final = 255


class LaneControlRefusal(click.ClickException):
    """Refuse an uncertain identity or readback without committing the transaction."""


@dataclass(frozen=True, slots=True)
class DefinitionState:
    """One exact registered definition version."""

    id: str
    name: str
    version: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class LaneControlRequest:
    """One bounded, attributable requested enable state."""

    definition: str
    enabled: bool
    operator: str
    reason: str
    apply: bool
    control_id: uuid.UUID


def resolve_definition(name: str) -> str:
    """Require the full name of exactly one executable, code-registered executor lane."""
    matches = [spec for spec in LANE_SPECS.values() if spec.definition_name == name and spec.executable]
    if len(matches) != 1:
        raise LaneControlRefusal("--definition must exactly name one registered executable executor definition")
    return matches[0].definition_name


def _bounded(value: str, label: str, limit: int) -> str:
    value = value.strip()
    if not value or len(value) > limit:
        raise click.BadParameter(f"{label} must contain 1 through {limit} characters")
    return value


def _states(rows: Sequence[Mapping[str, object]], name: str) -> tuple[DefinitionState, ...]:
    states = tuple(
        DefinitionState(
            str(required_column(row, "id", uuid.UUID)),
            required_column(row, "name", str),
            required_column(row, "version", str),
            required_column(row, "enabled", bool),
        )
        for row in rows
    )
    if not states or len(states) > MAX_VERSIONS:
        raise LaneControlRefusal("definition is missing or exceeds the bounded version inventory")
    if (
        any(state.name != name for state in states)
        or len({state.id for state in states}) != len(states)
        or len({state.version for state in states}) != len(states)
        or EXECUTOR_DEFINITION_VERSION not in {state.version for state in states}
    ):
        raise LaneControlRefusal("definition identities are ambiguous or lack the current executor version")
    return states


async def control_lane(
    session: AsyncSession,
    request: LaneControlRequest,
) -> dict[str, object]:
    """Read, optionally toggle every version, verify and audit within the caller's transaction."""
    definition = resolve_definition(request.definition)
    operator = _bounded(request.operator, "operator", MAX_OPERATOR)
    reason = _bounded(request.reason, "reason", MAX_REASON)
    enabled, apply, control_id = request.enabled, request.apply, request.control_id
    parameters = {"name": definition, "limit": MAX_VERSIONS + 1}
    before = _states(await fetch_rows(session, _LOCK if apply else _SELECT, parameters), definition)
    changed = sum(state.enabled != enabled for state in before)
    receipt: dict[str, object] = {
        "event": "plantgeo_executor_lane_control",
        "control_id": str(control_id),
        "definition": definition,
        "requested_enabled": enabled,
        "operator": operator,
        "reason": reason,
        "before": [asdict(state) for state in before],
        "after": None,
        "versions_changed": changed,
        "outcome": "dry_run",
        "incident_id": None,
        "quiescence_proven": False,
        "held_runs_released": False,
        "scope": "Definition enable state only; existing runs, work items, attempts and leases are untouched.",
    }
    if not apply:
        return receipt
    updated = await fetch_rows(session, _UPDATE, {"name": definition, "enabled": enabled})
    if {str(required_column(row, "id", uuid.UUID)) for row in updated} != {
        state.id for state in before if state.enabled != enabled
    }:
        raise LaneControlRefusal("updated definition identities differ from the locked inventory")
    after = _states(await fetch_rows(session, _SELECT, parameters), definition)
    if {(state.id, state.name, state.version) for state in before} != {
        (state.id, state.name, state.version) for state in after
    } or any(state.enabled != enabled for state in after):
        raise LaneControlRefusal("definition readback differs; transaction must roll back")
    receipt["after"] = [asdict(state) for state in after]
    receipt["outcome"] = "changed" if changed else "already_set"
    audit = await fetch_row(
        session,
        _AUDIT,
        {
            "fingerprint": f"executor-lane-control:{control_id}",
            "summary": reason,
            "owner": operator,
            "acknowledged_by": operator,
            "detail": canonical_json(receipt),
        },
    )
    if audit is None:
        raise LaneControlRefusal("lane control audit was not returned; transaction must roll back")
    receipt["incident_id"] = str(required_column(audit, "id", uuid.UUID))
    return receipt


async def _process(definition: str, *, enabled: bool, operator: str, reason: str, apply: bool) -> dict[str, object]:
    control_id = uuid.uuid4()
    try:
        target = ledger_target(settings.require_local_source_loader_database_url())
    except (ValueError, ArgumentError) as error:
        raise click.ClickException("an operational ledger DSN could not be resolved; nothing was changed") from error
    try:
        async with ingest_session() as session:
            await apply_statement_timeout(session)
            receipt = await control_lane(
                session,
                LaneControlRequest(
                    definition=definition,
                    enabled=enabled,
                    operator=operator,
                    reason=reason,
                    apply=apply,
                    control_id=control_id,
                ),
            )
            receipt["ledger"] = target
            if apply:
                await session.commit()
            else:
                await session.rollback()
    except SQLAlchemyError as error:
        click.echo(
            json.dumps(
                {
                    "event": "plantgeo_executor_lane_control",
                    "control_id": str(control_id),
                    "definition": definition,
                    "outcome": "write_unconfirmed" if apply else "read_failed",
                    "ledger": target,
                    "quiescence_proven": False,
                    "held_runs_released": False,
                },
                sort_keys=True,
            )
        )
        raise click.ClickException(
            "ledger operation failed; inspect control_id audit and current state before retry"
        ) from error
    return receipt


@click.command("jobs-set-lane-enabled")
@click.option("--definition", required=True, help="Exact registered plantgeo.executor.* definition name.")
@click.option(
    "--enabled/--disabled", default=None, help="Required desired state; pause does not stop existing workers."
)
@click.option("--operator", required=True)
@click.option("--reason", required=True)
@click.option("--apply", "apply_changes", is_flag=True, default=False)
def jobs_set_lane_enabled(
    definition: str, enabled: bool | None, operator: str, reason: str, apply_changes: bool
) -> None:
    """Preview or audit a durable all-version executor pause/resume; see execution/AGENTS.md."""
    definition = resolve_definition(definition)
    if enabled is None:
        raise click.BadParameter("choose --enabled or --disabled")
    operator = _bounded(operator, "operator", MAX_OPERATOR)
    reason = _bounded(reason, "reason", MAX_REASON)
    result = asyncio.run(
        _process(
            definition,
            enabled=enabled,
            operator=operator,
            reason=reason,
            apply=apply_changes,
        )
    )
    click.echo(json.dumps(result, sort_keys=True))
