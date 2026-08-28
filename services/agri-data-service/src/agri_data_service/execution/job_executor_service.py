"""The single stateful scheduler for PlantGeo ingestion and Parquet publication work."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

import click
import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_pool
from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.ingest.archive_walk import ARCHIVE_WALK_TIME_BUDGET_SECONDS
from agri_data_service.jobs import (
    JobDefinitionRecord,
    JobDefinitionSpec,
    JobHandlerOutcome,
    JobInvocation,
    JobWorkItemSpec,
    RetryPolicy,
    ShutdownSignal,
    job_handler,
    load_job_definition,
    open_job_run,
    run_job_slice,
    shutdown_signal,
)
from agri_data_service.jobs.lease import apply_statement_timeout, canonical_json, fetch_row, required_column
from agri_data_service.jobs.matview_refresh import MATVIEW_REFRESH_TIME_BUDGET_SECONDS
from agri_data_service.jobs.strategy_mv_refresh import STRATEGY_MV_REFRESH_TIME_BUDGET_SECONDS
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRATIONS, LANE_REGISTRY

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

logger = structlog.get_logger(__name__)

EXECUTOR_DEFINITION_PREFIX: Final = "plantgeo.executor."
EXECUTOR_DEFINITION_VERSION: Final = "1"
EXECUTOR_HANDLER_TOKEN: Final = "plantgeo.executor.command.v1"
EXECUTOR_WORK_ITEM_KIND: Final = "scheduled-command"
EXECUTOR_LEADER_LOCK_KEY: Final = "plantgeo:unified-job-executor:v1"
EXECUTOR_REQUESTED_BY: Final = "agri-service ops jobs-executor"

ACTIVE_LANES_VARIABLE: Final = "PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES"
HANDOFF_ACKNOWLEDGEMENTS_VARIABLE: Final = "PLANTGEO_JOB_EXECUTOR_HANDOFF_ACKNOWLEDGEMENTS"
POLL_SECONDS_VARIABLE: Final = "PLANTGEO_JOB_EXECUTOR_POLL_SECONDS"
MAX_LANES_PER_TICK_VARIABLE: Final = "PLANTGEO_JOB_EXECUTOR_MAX_LANES_PER_TICK"

DEFAULT_POLL_SECONDS: Final = 30.0
MIN_LANES_PER_TICK: Final = 2
DEFAULT_MAX_LANES_PER_TICK: Final = MIN_LANES_PER_TICK
MAX_LOOP_BACKOFF_SECONDS: Final = 300.0
COMMAND_HEARTBEAT_SECONDS: Final = 30.0
COMMAND_TIMEOUT_RESERVE_SECONDS: Final = 5.0
COMMAND_CLEANUP_MARGIN_SECONDS: Final = 300
COMMAND_TERMINATE_GRACE_SECONDS: Final = 30
COMMAND_KILL_WAIT_SECONDS: Final = 10
WORKER_ID_MAX_LENGTH: Final = 255

LaneWorkClass = Literal["incremental", "backlog", "source-specific"]
MigrationDisposition = Literal["consolidatable", "source-specific", "snapshot-only"]
LaneTickState = Literal[
    "shadow",
    "source_specific",
    "paused",
    "not_due",
    "deferred_fairness",
    "ran",
    "failed",
]


class ExecutorConfigurationError(ValueError):
    """Raised when ownership activation could overlap or strand a legacy lane."""


class ExecutorLeaderUnlockError(RuntimeError):
    """Raised when the pinned PostgreSQL backend cannot confirm leader-lock release."""


@dataclass(frozen=True, slots=True)
class LaneExecutionSpec:
    """One code-owned scheduling and migration contract."""

    lane_id: str
    legacy_owners: tuple[str, ...]
    required_handoff_acknowledgements: tuple[str, ...]
    conflicts_with: tuple[str, ...]
    work_class: LaneWorkClass
    migration_disposition: MigrationDisposition
    cadence_seconds: int | None
    phase_offset_seconds: int
    schedule: str | None
    publication_lag_days: int | None
    publication_cadence_days: int | None
    publication_lag_source: str
    selection_policy: str
    command: tuple[str, ...] | None
    command_timeout_seconds: int
    description: str
    writer_ceiling: str | None = None

    def __post_init__(self) -> None:
        if not self.lane_id.strip():
            raise ExecutorConfigurationError("lane_id must not be empty")
        if self.cadence_seconds is not None and self.cadence_seconds <= 0:
            raise ExecutorConfigurationError(f"{self.lane_id}: cadence_seconds must be positive")
        if self.phase_offset_seconds < 0:
            raise ExecutorConfigurationError(f"{self.lane_id}: phase_offset_seconds must not be negative")
        if self.command is not None and not self.command:
            raise ExecutorConfigurationError(f"{self.lane_id}: command must not be empty")
        if self.command_timeout_seconds <= 0:
            raise ExecutorConfigurationError(f"{self.lane_id}: command_timeout_seconds must be positive")
        if len(set(self.required_handoff_acknowledgements)) != len(self.required_handoff_acknowledgements):
            raise ExecutorConfigurationError(f"{self.lane_id}: handoff acknowledgements must be unique")

    @property
    def definition_name(self) -> str:
        return f"{EXECUTOR_DEFINITION_PREFIX}{self.lane_id}"

    @property
    def executable(self) -> bool:
        return self.command is not None and self.cadence_seconds is not None

    def definition_spec(self) -> JobDefinitionSpec:
        """Declare the durable outer command run without changing a stored pause switch."""
        return JobDefinitionSpec(
            name=self.definition_name,
            version=EXECUTOR_DEFINITION_VERSION,
            handler=EXECUTOR_HANDLER_TOKEN,
            schedule=self.schedule,
            concurrency_key=f"plantgeo-executor:{self.lane_id}",
            max_attempts=5,
            lease_seconds=self.command_timeout_seconds + 120,
            time_budget_seconds=self.command_timeout_seconds + 30,
            retry_policy=RetryPolicy(
                initial_backoff_seconds=30,
                backoff_multiplier=2,
                maximum_backoff_seconds=3600,
            ),
            parameters={
                "lane_id": self.lane_id,
                "legacy_owners": list(self.legacy_owners),
                "required_handoff_acknowledgements": list(self.required_handoff_acknowledgements),
                "cadence_seconds": self.cadence_seconds,
                "publication_lag_days": self.publication_lag_days,
                "publication_cadence_days": self.publication_cadence_days,
                "publication_lag_source": self.publication_lag_source,
                "selection_policy": self.selection_policy,
                "writer_ceiling": self.writer_ceiling,
            },
        )

    def inventory_row(self, *, active: bool) -> dict[str, object]:
        return {
            "lane_id": self.lane_id,
            "legacy_owners": list(self.legacy_owners),
            "required_handoff_acknowledgements": list(self.required_handoff_acknowledgements),
            "conflicts_with": list(self.conflicts_with),
            "active": active,
            "work_class": self.work_class,
            "migration_disposition": self.migration_disposition,
            "cadence_seconds": self.cadence_seconds,
            "schedule": self.schedule,
            "publication_lag_days": self.publication_lag_days,
            "publication_cadence_days": self.publication_cadence_days,
            "publication_lag_source": self.publication_lag_source,
            "selection_policy": self.selection_policy,
            "executable": self.executable,
            "description": self.description,
            "writer_ceiling": self.writer_ceiling,
            "source_watermark_parity": "not_evaluated",
        }


INGEST_CRON_OWNER: Final = "plantgeo-ingest-cron"
DIRECT_FIRE_OWNER: Final = "plantgeo-fire-detections-forward"
DIRECT_WATER_OWNER: Final = "plantgeo-water-gauges-forward"


def _disabled(owner: str) -> str:
    return f"{owner}:disabled-and-no-run-in-flight"


def _registration(slug: str) -> tuple[int, int, str | None]:
    lane = LANE_REGISTRY[slug]
    ceiling = None if lane.writer_ceiling is None else lane.writer_ceiling.isoformat()
    return lane.publication_lag_days, lane.cadence_days, ceiling


def _spec(  # noqa: PLR0913 - this is the declarative constructor for the code-owned lane table
    lane_id: str,
    *,
    command: tuple[str, ...] | None,
    legacy_owners: tuple[str, ...] = (INGEST_CRON_OWNER,),
    required_handoffs: tuple[str, ...] | None = None,
    conflicts_with: tuple[str, ...] = (),
    work_class: LaneWorkClass = "incremental",
    disposition: MigrationDisposition = "consolidatable",
    cadence_seconds: int | None = 3600,
    phase_offset_seconds: int = 0,
    schedule: str | None = "0 * * * *",
    publication_lag_days: int | None = None,
    publication_cadence_days: int | None = None,
    publication_lag_source: str = "source command contract",
    selection_policy: str = "newest available source receipt first",
    timeout_seconds: int = 900,
    description: str,
    writer_ceiling: str | None = None,
) -> LaneExecutionSpec:
    acknowledgements = (
        tuple(_disabled(owner) for owner in legacy_owners) if required_handoffs is None else required_handoffs
    )
    return LaneExecutionSpec(
        lane_id=lane_id,
        legacy_owners=legacy_owners,
        required_handoff_acknowledgements=acknowledgements,
        conflicts_with=conflicts_with,
        work_class=work_class,
        migration_disposition=disposition,
        cadence_seconds=cadence_seconds,
        phase_offset_seconds=phase_offset_seconds,
        schedule=schedule,
        publication_lag_days=publication_lag_days,
        publication_cadence_days=publication_cadence_days,
        publication_lag_source=publication_lag_source,
        selection_policy=selection_policy,
        command=command,
        command_timeout_seconds=timeout_seconds,
        description=description,
        writer_ceiling=writer_ceiling,
    )


def _postgres_spec(  # noqa: PLR0913 - cadence metadata stays beside each source command
    lane_id: str,
    command_name: str,
    parquet_slug: str,
    *,
    cadence_seconds: int = 3600,
    phase_offset_seconds: int = 0,
    schedule: str = "0 * * * *",
) -> LaneExecutionSpec:
    lag, publication_cadence, _ = _registration(parquet_slug)
    return _spec(
        lane_id,
        command=("agri-service", "data", command_name),
        cadence_seconds=cadence_seconds,
        phase_offset_seconds=phase_offset_seconds,
        schedule=schedule,
        publication_lag_days=lag,
        publication_cadence_days=publication_cadence,
        publication_lag_source=f"pipeline/parquet/lane_registry.py {parquet_slug} contract",
        description=f"Independent PostgreSQL forward ingestion for {parquet_slug}.",
    )


def _parquet_spec(slug: str) -> LaneExecutionSpec:
    lag, publication_cadence, writer_ceiling = _registration(slug)
    handoffs: tuple[str, ...] = (_disabled(INGEST_CRON_OWNER),)
    legacy_owners: tuple[str, ...] = (INGEST_CRON_OWNER,)
    conflicts: tuple[str, ...] = ()
    if slug == "water-gauges":
        handoffs = (_disabled(INGEST_CRON_OWNER), _disabled(DIRECT_WATER_OWNER))
        legacy_owners = (INGEST_CRON_OWNER, DIRECT_WATER_OWNER)
        conflicts = ("water-gauges-direct-forward",)
    return _spec(
        f"parquet-{slug}",
        command=(
            "agri-service",
            "data",
            "parquet-gap-fill",
            "--layer",
            slug,
            "--max-days-per-lane",
            "1",
            "--time-budget-seconds",
            "900",
        ),
        legacy_owners=legacy_owners,
        required_handoffs=handoffs,
        conflicts_with=conflicts,
        work_class="backlog",
        publication_lag_days=lag,
        publication_cadence_days=publication_cadence,
        publication_lag_source=f"pipeline/parquet/lane_registry.py {slug} contract",
        selection_policy="newest missing day first; at most one day per scheduler turn",
        timeout_seconds=1200,
        description=f"Bounded incremental and historical Parquet ownership for {slug}.",
        writer_ceiling=writer_ceiling,
    )


_POSTGRES_SPECS: Final[tuple[LaneExecutionSpec, ...]] = (
    _postgres_spec("postgres-firms", "ingest-firms", "fire-detections"),
    _postgres_spec("postgres-streamflow", "ingest-streamflow", "water-gauges"),
    _postgres_spec("postgres-weather", "ingest-weather", "weather-observations"),
    _postgres_spec("postgres-fire-perimeters", "ingest-fire-perimeters", "fire-perimeters"),
    _postgres_spec(
        "postgres-drought",
        "ingest-drought",
        "drought",
        cadence_seconds=86400,
        phase_offset_seconds=43200,
        schedule="0 12 * * *",
    ),
    _postgres_spec("postgres-vegetation", "ingest-ndvi", "vegetation"),
    _spec(
        "vegetation-catch-up",
        command=("agri-service", "data", "parquet-catch-up-vegetation"),
        work_class="incremental",
        publication_lag_days=_registration("vegetation")[0],
        publication_cadence_days=_registration("vegetation")[1],
        publication_lag_source="pipeline/parquet/lane_registry.py vegetation contract",
        selection_policy="45-day fingerprint revalidation followed by bounded durable pending-day drain",
        description="Exact vegetation publication barrier and pending-queue acknowledgement lane.",
    ),
    _postgres_spec("postgres-sensors", "ingest-sensors", "sensors"),
    _postgres_spec("postgres-evacuation-zones", "ingest-evacuation-zones", "evacuation-zones"),
    _spec(
        "postgres-geometry-repair",
        command=("agri-service", "data", "ingest-geometry-repair"),
        publication_lag_days=0,
        publication_cadence_days=1,
        description="Independent repair of continuously arriving unlinked feature geometry.",
    ),
    _spec(
        "postgres-watersheds",
        command=("agri-service", "data", "ingest-watersheds"),
        legacy_owners=(),
        required_handoffs=(),
        cadence_seconds=86400,
        phase_offset_seconds=7200,
        schedule="0 2 * * *",
        publication_lag_days=_registration("watersheds")[0],
        publication_cadence_days=_registration("watersheds")[1],
        publication_lag_source="pipeline/parquet/lane_registry.py watersheds contract",
        description="Previously unscheduled WBD snapshot refresh, visible without a fabricated legacy owner.",
    ),
)

_JOBS_SPECS: Final[tuple[LaneExecutionSpec, ...]] = (
    *(
        _spec(
            f"jobs-{lane}",
            command=(
                "agri-service",
                "ops",
                "jobs-pulse",
                "--lane",
                lane,
                "--skip-maintenance",
                "--time-budget-seconds",
                "600",
            ),
            work_class="backlog",
            publication_lag_source=f"durable definition {lane}",
            selection_policy="one isolated durable slice per turn",
            timeout_seconds=inner_budget + COMMAND_CLEANUP_MARGIN_SECONDS,
            description=f"Independent durable failure domain for {lane}.",
        )
        for lane, inner_budget in (
            ("matview-refresh", MATVIEW_REFRESH_TIME_BUDGET_SECONDS),
            ("strategy-mv-refresh", STRATEGY_MV_REFRESH_TIME_BUDGET_SECONDS),
            ("firms-archive", ARCHIVE_WALK_TIME_BUDGET_SECONDS),
            ("streamflow-archive", ARCHIVE_WALK_TIME_BUDGET_SECONDS),
        )
    ),
    *(
        _spec(
            f"maintenance-{lane}-{verb}",
            command=("agri-service", "ops", command_name, "--lane", lane, "--apply"),
            work_class="backlog",
            publication_lag_source=f"archive lane {lane}",
            selection_policy="bounded maintenance after independent archive execution",
            description=f"Independent {verb} maintenance for {lane}.",
        )
        for lane in ("firms-archive", "streamflow-archive")
        for verb, command_name in (
            ("reconcile", "jobs-reconcile-lane"),
            ("plan-gaps", "jobs-plan-gaps"),
        )
    ),
    _spec(
        "maintenance-validate-streams",
        command=("agri-service", "ops", "validate-streams"),
        work_class="backlog",
        publication_lag_source="cross-stream validation contracts",
        selection_policy="one isolated validation pass",
        description="Cross-stream validation isolated from every durable worker lane.",
    ),
)

_PARQUET_SPECS: Final[tuple[LaneExecutionSpec, ...]] = tuple(
    _parquet_spec(registration.slug) for registration in LANE_REGISTRATIONS
)

_MIGRATION_INPUT_SPECS: Final[tuple[LaneExecutionSpec, ...]] = (
    _spec(
        "fire-detections-direct-forward",
        command=("python", "-m", "agri_data_service.pipeline.direct.fire_detections"),
        legacy_owners=(DIRECT_FIRE_OWNER,),
        phase_offset_seconds=900,
        schedule="15 * * * *",
        publication_lag_days=_registration("fire-detections")[0],
        publication_cadence_days=_registration("fire-detections")[1],
        publication_lag_source="pipeline/parquet/lane_registry.py fire-detections contract",
        description="Direct FIRMS forward writer; safe beside generic history only because of its writer ceiling.",
    ),
    _spec(
        "water-gauges-direct-forward",
        command=None,
        legacy_owners=(DIRECT_WATER_OWNER, INGEST_CRON_OWNER),
        required_handoffs=(_disabled(DIRECT_WATER_OWNER), _disabled(INGEST_CRON_OWNER)),
        conflicts_with=("parquet-water-gauges",),
        disposition="source-specific",
        publication_lag_days=_registration("water-gauges")[0],
        publication_cadence_days=_registration("water-gauges")[1],
        publication_lag_source="pipeline/parquet/lane_registry.py water-gauges contract",
        description="Direct water writer blocked until a non-overlapping ceiling/cutover contract exists.",
    ),
    _spec(
        "mtbs-forward",
        command=("agri-service", "data", "ingest-mtbs"),
        legacy_owners=("plantgeo-cron-mtbs",),
        cadence_seconds=604800,
        phase_offset_seconds=460500,
        schedule="55 7 * * 2",
        publication_lag_days=_registration("burn-severity")[0],
        publication_cadence_days=_registration("burn-severity")[1],
        publication_lag_source="pipeline/parquet/lane_registry.py burn-severity contract",
        timeout_seconds=1800,
        description="MTBS source ingestion on its established weekly observation cadence.",
    ),
    _spec(
        "soilgrids-cache-warm",
        command=None,
        legacy_owners=("plantgeo-cron-soilgrids",),
        disposition="source-specific",
        phase_offset_seconds=1500,
        schedule="25 * * * *",
        publication_lag_source="static lookup; no temporal publication lag",
        description="Node-only finite SoilGrids cache warmer, unavailable in this Python image.",
    ),
    _spec(
        "soil-moisture-parquet-backfill",
        command=None,
        legacy_owners=("plantgeo-soil-moisture-parquet-load",),
        disposition="snapshot-only",
        cadence_seconds=None,
        schedule=None,
        publication_lag_source="historical one-shot snapshot contract",
        timeout_seconds=3600,
        description="Completed one-shot soil-moisture load, not a recurring forward lane.",
    ),
)

_LANE_SPECS: Final[tuple[LaneExecutionSpec, ...]] = (
    *_POSTGRES_SPECS,
    *_JOBS_SPECS,
    *_PARQUET_SPECS,
    *_MIGRATION_INPUT_SPECS,
)

LANE_SPECS: Final[Mapping[str, LaneExecutionSpec]] = MappingProxyType({spec.lane_id: spec for spec in _LANE_SPECS})

_TRY_LEADER_LOCK: Final = text("SELECT pg_try_advisory_lock(hashtextextended(:lock_key, 0)) AS acquired")
_RELEASE_LEADER_LOCK: Final = text("SELECT pg_advisory_unlock(hashtextextended(:lock_key, 0)) AS released")
_SELECT_DEFINITION_STATE: Final = text(load_query_sql("execution/select_definition_state.sql"))
_INSERT_DEFINITION: Final = text(load_query_sql("execution/insert_definition.sql"))
_SELECT_LATEST_RUN: Final = text(load_query_sql("execution/select_latest_run.sql"))


@dataclass(frozen=True, slots=True)
class ActivationConfig:
    """The explicit lane allow-list and operator-entered handoff acknowledgements."""

    active_lanes: frozenset[str]
    handoff_acknowledgements: Mapping[str, frozenset[str]] = field(default_factory=lambda: MappingProxyType({}))

    def is_active(self, lane_id: str) -> bool:
        return lane_id in self.active_lanes


def _comma_tokens(value: str) -> tuple[str, ...]:
    return tuple(token.strip() for token in value.split(",") if token.strip())


def parse_activation(environment: Mapping[str, str] | None = None) -> ActivationConfig:
    """Parse and validate the two-part cutover gate, defaulting every lane to shadow."""
    source = os.environ if environment is None else environment
    active = frozenset(_comma_tokens(source.get(ACTIVE_LANES_VARIABLE, "")))
    unknown = sorted(active - LANE_SPECS.keys())
    if unknown:
        raise ExecutorConfigurationError(f"unknown active lane(s): {', '.join(unknown)}")

    acknowledgements: dict[str, set[str]] = {}
    for token in _comma_tokens(source.get(HANDOFF_ACKNOWLEDGEMENTS_VARIABLE, "")):
        lane_id, separator, acknowledgement = token.partition("=")
        if not separator or not lane_id.strip() or not acknowledgement.strip():
            raise ExecutorConfigurationError(
                f"{HANDOFF_ACKNOWLEDGEMENTS_VARIABLE} entries must be lane=operator-acknowledgement"
            )
        lane_id = lane_id.strip()
        acknowledgement = acknowledgement.strip()
        lane_acknowledgements = acknowledgements.setdefault(lane_id, set())
        if acknowledgement in lane_acknowledgements:
            raise ExecutorConfigurationError(f"duplicate handoff acknowledgement {acknowledgement!r} for {lane_id!r}")
        lane_acknowledgements.add(acknowledgement)

    unknown_acknowledgements = sorted(acknowledgements.keys() - LANE_SPECS.keys())
    if unknown_acknowledgements:
        raise ExecutorConfigurationError(
            f"handoff acknowledgement names unknown lane(s): {', '.join(unknown_acknowledgements)}"
        )
    inactive_acknowledgements = sorted(acknowledgements.keys() - active)
    if inactive_acknowledgements:
        raise ExecutorConfigurationError(
            f"handoff acknowledgement supplied for inactive lane(s): {', '.join(inactive_acknowledgements)}"
        )

    for lane_id in sorted(active):
        spec = LANE_SPECS[lane_id]
        if not spec.executable:
            raise ExecutorConfigurationError(
                f"lane {lane_id!r} is {spec.migration_disposition} and has no executor command"
            )
        supplied = frozenset(acknowledgements.get(lane_id, set()))
        expected = frozenset(spec.required_handoff_acknowledgements)
        if supplied != expected:
            missing = sorted(expected - supplied)
            extra = sorted(supplied - expected)
            raise ExecutorConfigurationError(
                f"lane {lane_id!r} handoff acknowledgements do not match; missing={missing}, extra={extra}"
            )
        conflicts = sorted(set(spec.conflicts_with) & active)
        if conflicts:
            raise ExecutorConfigurationError(f"lane {lane_id!r} conflicts with active lane(s): {', '.join(conflicts)}")

    _require_atomic_owner_cutovers(active)
    frozen_acknowledgements = MappingProxyType(
        {lane_id: frozenset(values) for lane_id, values in acknowledgements.items()}
    )
    return ActivationConfig(active_lanes=active, handoff_acknowledgements=frozen_acknowledgements)


def _require_atomic_owner_cutovers(active: frozenset[str]) -> None:
    """Keep one multi-role legacy service from being only partly replaced."""
    executable_by_owner: dict[str, set[str]] = {}
    for spec in LANE_SPECS.values():
        if spec.executable:
            for owner in spec.legacy_owners:
                executable_by_owner.setdefault(owner, set()).add(spec.lane_id)
    for owner, owned_lanes in executable_by_owner.items():
        activated = owned_lanes & active
        if activated and activated != owned_lanes:
            missing = ", ".join(sorted(owned_lanes - activated))
            raise ExecutorConfigurationError(
                f"legacy owner {owner!r} must cut over atomically; also activate {missing}"
            )


def scheduled_bucket(spec: LaneExecutionSpec, now: datetime) -> datetime:
    """Return this lane's current cadence bucket using its declared phase offset."""
    if now.utcoffset() is None:
        raise ExecutorConfigurationError("the scheduler clock must include a timezone")
    if spec.cadence_seconds is None:
        raise ExecutorConfigurationError(f"lane {spec.lane_id!r} has no recurring cadence")
    epoch_seconds = int(now.timestamp())
    bucket = (
        (epoch_seconds - spec.phase_offset_seconds) // spec.cadence_seconds
    ) * spec.cadence_seconds + spec.phase_offset_seconds
    return datetime.fromtimestamp(bucket, tz=UTC)


@dataclass(frozen=True, slots=True)
class LatestRun:
    run_id: uuid.UUID
    scheduled_for: datetime
    status: str

    @property
    def open(self) -> bool:
        return self.status in {"queued", "running"}


@dataclass(frozen=True, slots=True)
class DueLane:
    spec: LaneExecutionSpec
    definition: JobDefinitionRecord
    scheduled_for: datetime
    existing_run_id: uuid.UUID | None
    last_scheduled_for: datetime | None


def fair_due_order(candidates: Sequence[DueLane]) -> tuple[DueLane, ...]:
    """Interleave incremental and backlog owners while rotating oldest-served lanes first."""
    oldest = datetime.min.replace(tzinfo=UTC)

    def lane_key(candidate: DueLane) -> tuple[datetime, str]:
        return (candidate.last_scheduled_for or oldest, candidate.spec.lane_id)

    incremental = sorted(
        (candidate for candidate in candidates if candidate.spec.work_class == "incremental"),
        key=lane_key,
    )
    backlog = sorted(
        (candidate for candidate in candidates if candidate.spec.work_class == "backlog"),
        key=lane_key,
    )
    ordered: list[DueLane] = []
    while incremental or backlog:
        if incremental:
            ordered.append(incremental.pop(0))
        if backlog:
            ordered.append(backlog.pop(0))
    return tuple(ordered)


@dataclass(frozen=True, slots=True)
class LaneTickResult:
    lane_id: str
    state: LaneTickState
    scheduled_for: datetime | None = None
    run_id: uuid.UUID | None = None
    run_status: str | None = None
    detail: str | None = None
    slice_summary: Mapping[str, object] | None = None
    command: tuple[str, ...] | None = None
    handoff_blockers: tuple[str, ...] = ()
    due_prediction: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "lane_id": self.lane_id,
            "state": self.state,
            "scheduled_for": None if self.scheduled_for is None else self.scheduled_for.isoformat(),
            "run_id": None if self.run_id is None else str(self.run_id),
            "run_status": self.run_status,
            "detail": self.detail,
            "slice": None if self.slice_summary is None else dict(self.slice_summary),
            "command": None if self.command is None else list(self.command),
            "handoff_blockers": list(self.handoff_blockers),
            "due_prediction": self.due_prediction,
        }


@dataclass(frozen=True, slots=True)
class ExecutorTickSummary:
    observed_at: datetime
    leader: bool
    lanes: tuple[LaneTickResult, ...]

    @property
    def failed(self) -> bool:
        return any(lane.state == "failed" for lane in self.lanes)

    def to_dict(self) -> dict[str, object]:
        return {
            "event": "plantgeo_job_executor_tick",
            "observed_at": self.observed_at.isoformat(),
            "leader": self.leader,
            "failed": self.failed,
            "lanes": [lane.to_dict() for lane in self.lanes],
        }


async def _try_leader_lock(session: AsyncSession) -> bool:
    row = await fetch_row(session, _TRY_LEADER_LOCK, {"lock_key": EXECUTOR_LEADER_LOCK_KEY})
    return False if row is None else required_column(row, "acquired", bool)


async def _release_leader_lock(session: AsyncSession) -> None:
    try:
        row = await fetch_row(session, _RELEASE_LEADER_LOCK, {"lock_key": EXECUTOR_LEADER_LOCK_KEY})
    except Exception as error:
        raise ExecutorLeaderUnlockError("leader advisory unlock query failed") from error
    if row is None or not required_column(row, "released", bool):
        raise ExecutorLeaderUnlockError("the pinned PostgreSQL backend did not release the leader lock")


async def _invalidate_leader_connection(session: AsyncSession) -> None:
    bind = getattr(session, "bind", None)
    if not isinstance(bind, AsyncConnection):
        logger.error("plantgeo_job_executor_leader_connection_not_pinned")
        return
    try:
        await bind.invalidate()
    except BaseException as error:
        logger.error(
            "plantgeo_job_executor_leader_connection_invalidate_failed",
            error_type=type(error).__name__,
        )


def _pinned_connection_invalidated(session: AsyncSession) -> bool:
    """Report whether this tick's externally held connection lost its backend."""
    bind = getattr(session, "bind", None)
    return bool(bind is not None and getattr(bind, "invalidated", False))


async def _definition_state(session: AsyncSession, spec: LaneExecutionSpec) -> tuple[uuid.UUID, bool] | None:
    row = await fetch_row(
        session,
        _SELECT_DEFINITION_STATE,
        {"name": spec.definition_name, "version": EXECUTOR_DEFINITION_VERSION},
    )
    if row is None:
        return None
    return required_column(row, "id", uuid.UUID), required_column(row, "enabled", bool)


async def _load_or_register_definition(
    session: AsyncSession,
    spec: LaneExecutionSpec,
) -> JobDefinitionRecord | None:
    """Create a missing definition once, but never overwrite a stored operator pause."""
    state = await _definition_state(session, spec)
    if state is None:
        definition_spec = spec.definition_spec()
        await fetch_row(
            session,
            _INSERT_DEFINITION,
            {
                "name": definition_spec.name,
                "version": definition_spec.version,
                "handler": definition_spec.handler,
                "queue_name": definition_spec.queue_name,
                "schedule": definition_spec.schedule,
                "schedule_timezone": definition_spec.schedule_timezone,
                "concurrency_key": definition_spec.concurrency_key,
                "max_attempts": definition_spec.max_attempts,
                "lease_seconds": definition_spec.lease_seconds,
                "time_budget_seconds": definition_spec.time_budget_seconds,
                "retry_policy": canonical_json(definition_spec.retry_policy.to_json()),
                "parameters": canonical_json(definition_spec.parameters),
            },
        )
        await session.commit()
        state = await _definition_state(session, spec)
        if state is None:
            raise RuntimeError(f"executor definition {spec.definition_name!r} was neither inserted nor readable")
    _, enabled = state
    if not enabled:
        await session.rollback()
        return None
    definition = await load_job_definition(
        session,
        spec.definition_name,
        version=EXECUTOR_DEFINITION_VERSION,
    )
    await session.rollback()
    return definition


async def _latest_run(session: AsyncSession, definition_id: uuid.UUID) -> LatestRun | None:
    row = await fetch_row(session, _SELECT_LATEST_RUN, {"job_definition_id": definition_id})
    if row is None:
        return None
    return LatestRun(
        run_id=required_column(row, "id", uuid.UUID),
        scheduled_for=required_column(row, "scheduled_for", datetime),
        status=required_column(row, "status", str),
    )


def _work_priority(spec: LaneExecutionSpec) -> int:
    return 100 if spec.work_class == "incremental" else 10


async def _open_scheduled_run(
    session: AsyncSession,
    candidate: DueLane,
) -> uuid.UUID:
    spec = candidate.spec
    scheduled_iso = candidate.scheduled_for.isoformat()
    opened = await open_job_run(
        session,
        candidate.definition,
        logical_run_key=f"{spec.definition_name}:{scheduled_iso}",
        scheduled_for=candidate.scheduled_for,
        requested_by=EXECUTOR_REQUESTED_BY,
        target_partitions={"lane_id": spec.lane_id, "scheduled_for": scheduled_iso},
        work_items=(
            JobWorkItemSpec(
                shard_key=scheduled_iso,
                kind=EXECUTOR_WORK_ITEM_KIND,
                payload={"lane_id": spec.lane_id, "scheduled_for": scheduled_iso},
                priority=_work_priority(spec),
            ),
        ),
    )
    await session.commit()
    return opened.job_run_id


def _worker_id(spec: LaneExecutionSpec) -> str:
    replica = os.environ.get("RAILWAY_REPLICA_ID", "").strip()
    identity = replica or f"{socket.gethostname()}:{os.getpid()}"
    return f"job-executor:{identity}:{spec.lane_id}"[:WORKER_ID_MAX_LENGTH]


async def _execute_due_lane(
    session: AsyncSession,
    candidate: DueLane,
    *,
    stop: ShutdownSignal | None,
) -> LaneTickResult:
    run_id = candidate.existing_run_id or await _open_scheduled_run(session, candidate)
    summary = await run_job_slice(
        session,
        definition_name=candidate.spec.definition_name,
        version=EXECUTOR_DEFINITION_VERSION,
        job_run_id=run_id,
        worker_id=_worker_id(candidate.spec),
        budget_seconds=float(candidate.spec.command_timeout_seconds + 30),
        stop=stop,
    )
    failed = summary.retried > 0 or summary.dead_lettered > 0 or summary.abandoned > 0
    return LaneTickResult(
        lane_id=candidate.spec.lane_id,
        state="failed" if failed else "ran",
        scheduled_for=candidate.scheduled_for,
        run_id=run_id,
        run_status=summary.run_status,
        detail=(
            "work item dead-lettered"
            if summary.dead_lettered
            else "work item abandoned after losing its fenced lease"
            if summary.abandoned
            else "work item entered retry backoff"
            if summary.retried
            else summary.stop_reason
        ),
        slice_summary=summary.to_summary(),
    )


async def _plan_active_lanes(
    session: AsyncSession,
    activation: ActivationConfig,
    now: datetime,
) -> tuple[list[LaneTickResult], list[DueLane]]:
    results: list[LaneTickResult] = []
    due: list[DueLane] = []
    for spec in LANE_SPECS.values():
        if not activation.is_active(spec.lane_id):
            state: LaneTickState = "shadow" if spec.executable else "source_specific"
            current_bucket = (
                scheduled_bucket(spec, now) if spec.executable and spec.cadence_seconds is not None else None
            )
            blockers = ["lane is not in the active allow-list"]
            blockers.extend(
                f"operator handoff acknowledgement required: {acknowledgement}"
                for acknowledgement in spec.required_handoff_acknowledgements
            )
            if not spec.executable:
                blockers.append("no executable command exists in this runtime")
            results.append(
                LaneTickResult(
                    lane_id=spec.lane_id,
                    state=state,
                    scheduled_for=current_bucket,
                    command=spec.command,
                    handoff_blockers=tuple(blockers),
                    due_prediction=(
                        "would_be_due_if_activated; source watermark parity not evaluated"
                        if spec.executable
                        else "not_executable"
                    ),
                    detail=(
                        "shadow schedule prediction only; no ledger or source watermark parity was read"
                        if spec.executable
                        else f"{spec.migration_disposition}: no command in this runtime"
                    ),
                )
            )
            continue

        definition = await _load_or_register_definition(session, spec)
        if definition is None:
            results.append(
                LaneTickResult(lane_id=spec.lane_id, state="paused", detail="job_definition.enabled is false")
            )
            continue
        latest = await _latest_run(session, definition.id)
        await session.rollback()
        bucket = scheduled_bucket(spec, now)
        if latest is not None and latest.status in {"failed", "partial"}:
            results.append(
                LaneTickResult(
                    lane_id=spec.lane_id,
                    state="failed",
                    scheduled_for=latest.scheduled_for,
                    run_id=latest.run_id,
                    run_status=latest.status,
                    detail="latest run remains failed; clear its dead-lettered work before another bucket opens",
                )
            )
            continue
        if latest is not None and latest.open:
            due.append(
                DueLane(
                    spec=spec,
                    definition=definition,
                    scheduled_for=latest.scheduled_for,
                    existing_run_id=latest.run_id,
                    last_scheduled_for=latest.scheduled_for,
                )
            )
            continue
        if latest is not None and latest.scheduled_for >= bucket:
            detail = f"current bucket already settled with status {latest.status}"
            results.append(
                LaneTickResult(
                    lane_id=spec.lane_id,
                    state="not_due",
                    scheduled_for=latest.scheduled_for,
                    run_id=latest.run_id,
                    run_status=latest.status,
                    detail=detail,
                )
            )
            continue
        due.append(
            DueLane(
                spec=spec,
                definition=definition,
                scheduled_for=bucket,
                existing_run_id=None,
                last_scheduled_for=None if latest is None else latest.scheduled_for,
            )
        )
    return results, due


async def run_executor_tick(
    session: AsyncSession,
    *,
    activation: ActivationConfig,
    now: datetime,
    max_lanes_per_tick: int,
    stop: ShutdownSignal | None = None,
) -> ExecutorTickSummary:
    """Run one leader-elected, durable, fairly selected scheduler tick."""
    if max_lanes_per_tick < MIN_LANES_PER_TICK:
        raise ExecutorConfigurationError(
            f"max_lanes_per_tick must be at least {MIN_LANES_PER_TICK} to preserve class fairness"
        )
    logger.info(
        "plantgeo_job_executor_tick_started",
        observed_at=now.isoformat(),
        active_lane_count=len(activation.active_lanes),
    )
    await apply_statement_timeout(session)
    if not await _try_leader_lock(session):
        await session.rollback()
        logger.info("plantgeo_job_executor_leader_not_acquired", observed_at=now.isoformat())
        return ExecutorTickSummary(observed_at=now, leader=False, lanes=())
    logger.info("plantgeo_job_executor_leader_acquired", observed_at=now.isoformat())
    primary_error: BaseException | None = None
    try:
        results, due = await _plan_active_lanes(session, activation, now)
        ordered = fair_due_order(due)
        selected = ordered[:max_lanes_per_tick]
        for candidate in selected:
            try:
                results.append(await _execute_due_lane(session, candidate, stop=stop))
            except Exception as error:  # isolate lane-local faults only while the pinned backend is intact
                await session.rollback()
                if isinstance(error, SQLAlchemyError) or _pinned_connection_invalidated(session):
                    logger.error(
                        "plantgeo_job_executor_pinned_connection_lost",
                        lane_id=candidate.spec.lane_id,
                        error_type=type(error).__name__,
                    )
                    raise
                logger.error(
                    "plantgeo_job_executor_lane_failed",
                    lane_id=candidate.spec.lane_id,
                    error_type=type(error).__name__,
                )
                results.append(
                    LaneTickResult(
                        lane_id=candidate.spec.lane_id,
                        state="failed",
                        scheduled_for=candidate.scheduled_for,
                        run_id=candidate.existing_run_id,
                        detail=f"scheduler lane failed ({type(error).__name__})",
                    )
                )
        for candidate in ordered[max_lanes_per_tick:]:
            results.append(
                LaneTickResult(
                    lane_id=candidate.spec.lane_id,
                    state="deferred_fairness",
                    scheduled_for=candidate.scheduled_for,
                    run_id=candidate.existing_run_id,
                    detail="due; another work class received this bounded tick's turn",
                )
            )
        return ExecutorTickSummary(
            observed_at=now,
            leader=True,
            lanes=tuple(sorted(results, key=lambda result: result.lane_id)),
        )
    except BaseException as error:
        primary_error = error
        raise
    finally:
        unlock_error: BaseException | None = None
        try:
            await session.rollback()
            await _release_leader_lock(session)
            await session.rollback()
        except BaseException as error:
            unlock_error = error
            logger.error(
                "plantgeo_job_executor_leader_unlock_failed",
                error_type=type(error).__name__,
                primary_error_type=None if primary_error is None else type(primary_error).__name__,
            )
            await _invalidate_leader_connection(session)
        if unlock_error is not None and primary_error is None:
            raise unlock_error


async def _stop_process(
    process: asyncio.subprocess.Process,
    wait_task: asyncio.Task[int],
) -> None:
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.terminate()
    try:
        await asyncio.wait_for(asyncio.shield(wait_task), timeout=COMMAND_TERMINATE_GRACE_SECONDS)
    except TimeoutError:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        try:
            await asyncio.wait_for(asyncio.shield(wait_task), timeout=COMMAND_KILL_WAIT_SECONDS)
        except TimeoutError:
            logger.error("plantgeo_job_executor_subprocess_reap_timeout")
            wait_task.cancel()
            with suppress(asyncio.CancelledError):
                await wait_task


CommandMonitorState = Literal["exited", "shutdown", "fence_lost", "timeout"]


async def _monitor_subprocess(
    process: asyncio.subprocess.Process,
    invocation: JobInvocation,
    *,
    timeout: float,
) -> tuple[CommandMonitorState, int | None]:
    """React to exit, shutdown, fence loss, and timeout without serial waits."""
    wait_task = asyncio.create_task(process.wait())
    deadline = time.monotonic() + timeout
    next_heartbeat = time.monotonic() + COMMAND_HEARTBEAT_SECONDS
    try:
        while True:
            if invocation.shutdown_requested():
                await _stop_process(process, wait_task)
                return "shutdown", process.returncode
            now = time.monotonic()
            if now >= deadline:
                await _stop_process(process, wait_task)
                return "timeout", process.returncode
            wait_seconds = min(0.25, deadline - now, max(next_heartbeat - now, 0.0))
            done, _ = await asyncio.wait((wait_task,), timeout=wait_seconds)
            if done:
                return "exited", wait_task.result()
            now = time.monotonic()
            if now >= next_heartbeat:
                if not await invocation.heartbeat():
                    await _stop_process(process, wait_task)
                    return "fence_lost", process.returncode
                next_heartbeat = time.monotonic() + COMMAND_HEARTBEAT_SECONDS
    except BaseException:
        await _stop_process(process, wait_task)
        raise


@job_handler(EXECUTOR_HANDLER_TOKEN)
async def run_scheduled_command(  # noqa: PLR0911, PLR0912 - each terminal state maps to a ledger outcome
    invocation: JobInvocation,
) -> JobHandlerOutcome:
    """Execute one registry-bound command under the outer work item's fence."""
    if invocation.kind != EXECUTOR_WORK_ITEM_KIND:
        return JobHandlerOutcome.failed("unknown_work_item_kind", f"unexpected kind {invocation.kind!r}")
    lane_id = invocation.payload.get("lane_id")
    if not isinstance(lane_id, str) or lane_id not in LANE_SPECS:
        return JobHandlerOutcome.failed("unknown_executor_lane", "work item names no registered executor lane")
    spec = LANE_SPECS[lane_id]
    try:
        activation = parse_activation()
    except ExecutorConfigurationError as error:
        return JobHandlerOutcome.failed("invalid_ownership_activation", str(error))
    if not activation.is_active(lane_id):
        return JobHandlerOutcome.failed(
            "ownership_activation_removed",
            f"lane {lane_id!r} is no longer explicitly activated",
        )
    if spec.command is None:
        return JobHandlerOutcome.failed("source_specific_lane", f"lane {lane_id!r} has no executor command")

    scheduled_for = invocation.payload.get("scheduled_for")
    if invocation.cursor is None:
        return JobHandlerOutcome.progressed(
            {
                "state": "ready",
                "scheduled_for": scheduled_for if isinstance(scheduled_for, str) else invocation.shard_key,
            },
            progress_fraction=0.01,
            metrics={"command_started": False},
        )
    if invocation.cursor.get("state") != "ready":
        return JobHandlerOutcome.failed(
            "invalid_executor_checkpoint",
            f"lane {lane_id!r} cannot resume from its stored command checkpoint",
        )

    timeout = min(
        float(spec.command_timeout_seconds),
        max(invocation.seconds_remaining - COMMAND_TIMEOUT_RESERVE_SECONDS, 0.0),
    )
    if timeout <= 0:
        return JobHandlerOutcome.yielded(reason="no command budget remains in this scheduler slice")

    process = await asyncio.create_subprocess_exec(*spec.command)
    started = time.monotonic()
    monitor_state, return_code = await _monitor_subprocess(process, invocation, timeout=timeout)
    elapsed = round(time.monotonic() - started, 3)
    if monitor_state == "shutdown":
        return JobHandlerOutcome.yielded(
            cursor=invocation.cursor,
            progress_fraction=invocation.progress_fraction,
            reason=f"lane {lane_id!r} stopped for service shutdown before command completion",
            metrics={"elapsed_seconds": elapsed},
        )
    if monitor_state == "fence_lost":
        return JobHandlerOutcome.failed(
            "executor_lease_lost",
            f"lane {lane_id!r} lost its fenced lease while the command was running",
            metrics={"elapsed_seconds": elapsed},
        )
    if monitor_state == "timeout":
        return JobHandlerOutcome.failed(
            "scheduled_command_timeout",
            f"lane {lane_id!r} exceeded its {int(timeout)} second command budget",
            metrics={"elapsed_seconds": elapsed},
        )
    if return_code is None:  # pragma: no cover - exited always carries Process.wait's integer
        return JobHandlerOutcome.failed("scheduled_command_exit", f"lane {lane_id!r} returned no exit status")
    if return_code != 0:
        return JobHandlerOutcome.failed(
            "scheduled_command_exit",
            f"lane {lane_id!r} command exited with status {return_code}",
            metrics={"elapsed_seconds": elapsed, "exit_code": return_code},
        )
    cursor = {
        "state": "completed",
        "scheduled_for": scheduled_for if isinstance(scheduled_for, str) else invocation.shard_key,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    return JobHandlerOutcome.completed(
        cursor=cursor,
        metrics={"elapsed_seconds": elapsed, "exit_code": return_code},
    )


def executor_inventory(activation: ActivationConfig) -> dict[str, object]:
    return {
        "event": "plantgeo_job_executor_inventory",
        "mode": "active" if activation.active_lanes else "shadow",
        "activation_variables": [ACTIVE_LANES_VARIABLE, HANDOFF_ACKNOWLEDGEMENTS_VARIABLE],
        "lanes": [spec.inventory_row(active=activation.is_active(spec.lane_id)) for spec in LANE_SPECS.values()],
    }


async def _service_loop(
    *,
    activation: ActivationConfig,
    poll_seconds: float,
    max_lanes_per_tick: int,
    once: bool,
) -> int:
    failures = 0
    database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_pool(database_url) as loader_pool, shutdown_signal() as stop:
        while not stop.requested:
            try:
                async with (
                    loader_pool.connect() as tick_connection,
                    AsyncSession(
                        bind=tick_connection,
                        expire_on_commit=False,
                    ) as session,
                ):
                    summary = await run_executor_tick(
                        session,
                        activation=activation,
                        now=datetime.now(UTC),
                        max_lanes_per_tick=max_lanes_per_tick,
                        stop=stop,
                    )
                click.echo(json.dumps(summary.to_dict(), sort_keys=True))
                if summary.failed:
                    logger.error(
                        "plantgeo_job_executor_tick_unhealthy",
                        failing_lanes=[lane.lane_id for lane in summary.lanes if lane.state == "failed"],
                    )
                else:
                    logger.info(
                        "plantgeo_job_executor_tick_healthy",
                        leader=summary.leader,
                        lane_count=len(summary.lanes),
                    )
                failures = 0
                if once:
                    return 1 if summary.failed else 0
                await asyncio.sleep(poll_seconds)
            except Exception as error:
                failures += 1
                delay = min(poll_seconds * (2 ** (failures - 1)), MAX_LOOP_BACKOFF_SECONDS)
                logger.error(
                    "plantgeo_job_executor_tick_failed",
                    error_type=type(error).__name__,
                    consecutive_failures=failures,
                    retry_seconds=delay,
                )
                if once:
                    return 1
                await asyncio.sleep(delay)
    return 0


def _environment_float(name: str, fallback: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return fallback
    try:
        value = float(raw)
    except ValueError as error:
        raise ExecutorConfigurationError(f"{name} must be a number") from error
    if value <= 0:
        raise ExecutorConfigurationError(f"{name} must be positive")
    return value


def _environment_int(name: str, fallback: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return fallback
    try:
        value = int(raw)
    except ValueError as error:
        raise ExecutorConfigurationError(f"{name} must be an integer") from error
    if value <= 0:
        raise ExecutorConfigurationError(f"{name} must be positive")
    return value


@click.command("jobs-executor")
@click.option("--once", is_flag=True, help="Run one leader-elected scheduler tick and exit.")
@click.option("--inventory", "inventory_only", is_flag=True, help="Print the code-owned lane inventory and exit.")
def jobs_executor(once: bool, inventory_only: bool) -> None:
    """Run the single continuous PlantGeo ingestion and Parquet job service."""
    try:
        activation = parse_activation()
        inventory = executor_inventory(activation)
        click.echo(json.dumps(inventory, sort_keys=True))
        if inventory_only:
            return
        poll_seconds = _environment_float(POLL_SECONDS_VARIABLE, DEFAULT_POLL_SECONDS)
        max_lanes = _environment_int(MAX_LANES_PER_TICK_VARIABLE, DEFAULT_MAX_LANES_PER_TICK)
        if max_lanes < MIN_LANES_PER_TICK:
            raise ExecutorConfigurationError(
                f"{MAX_LANES_PER_TICK_VARIABLE} must be at least {MIN_LANES_PER_TICK} to preserve class fairness"
            )
        exit_code = asyncio.run(
            _service_loop(
                activation=activation,
                poll_seconds=poll_seconds,
                max_lanes_per_tick=max_lanes,
                once=once,
            )
        )
    except ExecutorConfigurationError as error:
        raise click.ClickException(str(error)) from error
    if exit_code:
        raise click.exceptions.Exit(exit_code)


__all__ = [
    "ACTIVE_LANES_VARIABLE",
    "HANDOFF_ACKNOWLEDGEMENTS_VARIABLE",
    "LANE_SPECS",
    "ActivationConfig",
    "DueLane",
    "ExecutorConfigurationError",
    "ExecutorLeaderUnlockError",
    "ExecutorTickSummary",
    "LaneExecutionSpec",
    "LaneTickResult",
    "executor_inventory",
    "fair_due_order",
    "jobs_executor",
    "parse_activation",
    "run_executor_tick",
    "run_scheduled_command",
    "scheduled_bucket",
]
