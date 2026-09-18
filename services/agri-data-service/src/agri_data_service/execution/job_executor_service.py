"""The single stateful scheduler for PlantGeo ingestion and Parquet publication work."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
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
from agri_data_service.execution.gap_repair_contract import (
    EXECUTOR_REPAIR_WORK_ITEM_KIND,
    REPAIR_LANE_IDS,
    REPAIR_LANE_SUFFIX,
    RepairRequest,
    RepairRequestError,
)
from agri_data_service.execution.lane_ids import (
    BURN_SEVERITY_DIRECT_LANE_ID,
    CLIMATE_DIRECT_LANE_ID,
    DROUGHT_DIRECT_LANE_ID,
    EVACUATION_ZONES_DIRECT_LANE_ID,
    FIRE_DETECTIONS_DIRECT_LANE_ID,
    FIRE_PERIMETERS_DIRECT_LANE_ID,
    MTBS_FORWARD_LANE_ID,
    SENSORS_DIRECT_LANE_ID,
    SOIL_DIRECT_LANE_ID,
    VEGETATION_DIRECT_LANE_ID,
    WATER_GAUGES_DIRECT_LANE_ID,
    WATERSHEDS_DIRECT_LANE_ID,
    WEATHER_OBSERVATIONS_DIRECT_LANE_ID,
)
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
    read_lane_pause_state,
    run_job_slice,
    shutdown_signal,
)
from agri_data_service.jobs.lease import (
    FAILURE_SUMMARY_MAX_LENGTH,
    apply_statement_timeout,
    canonical_json,
    fetch_row,
    redact_text,
    required_column,
)
from agri_data_service.pipeline.constants import (
    FIRE_DETECTIONS_DIRECT_WRITER_START_DAY,
    WATER_GAUGES_DIRECT_WRITER_START_DAY,
)
from agri_data_service.pipeline.direct.burn_severity.forward import BURN_SEVERITY_MAX_TIME_BUDGET_SECONDS
from agri_data_service.pipeline.direct.climate.products import (
    CLIMATE_DEFAULT_TIME_BUDGET_SECONDS,
    CLIMATE_FIELD_PRODUCTS,
    CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS,
)
from agri_data_service.pipeline.direct.drought.forward import DROUGHT_DEFAULT_TIME_BUDGET_SECONDS
from agri_data_service.pipeline.direct.evacuation_zones.forward import EVACUATION_ZONES_DEFAULT_TIME_BUDGET_SECONDS
from agri_data_service.pipeline.direct.fire_perimeters.forward import FIRE_PERIMETERS_DEFAULT_TIME_BUDGET_SECONDS
from agri_data_service.pipeline.direct.sensors.forward import SENSORS_DEFAULT_TIME_BUDGET_SECONDS
from agri_data_service.pipeline.direct.soil.products import (
    ERA5_LAND_ARCHIVE_PUBLICATION_LAG_DAYS,
    SOIL_DEFAULT_TIME_BUDGET_SECONDS,
    SOIL_FIELD_PRODUCTS,
)
from agri_data_service.pipeline.direct.vegetation.forward import VEGETATION_DEFAULT_TIME_BUDGET_SECONDS
from agri_data_service.pipeline.direct.vegetation.products import VEGETATION_DIRECT_WRITER_START_DAY
from agri_data_service.pipeline.direct.weather_observations.forward import (
    WEATHER_OBSERVATIONS_DEFAULT_TIME_BUDGET_SECONDS,
)
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from collections.abc import Set as AbstractSet

logger = structlog.get_logger(__name__)

EXECUTOR_DEFINITION_PREFIX: Final = "plantgeo.executor."
EXECUTOR_DEFINITION_VERSION: Final = "2"
EXECUTOR_HANDLER_TOKEN: Final = "plantgeo.executor.command.v1"
EXECUTOR_WORK_ITEM_KIND: Final = "scheduled-command"
#: The two work item kinds the one handler runs: a cadence bucket's command, or a bounded repair turn of the
#: same command. See execution/AGENTS.md, "Bounded gap repair".
EXECUTOR_WORK_ITEM_KINDS: Final[frozenset[str]] = frozenset({EXECUTOR_WORK_ITEM_KIND, EXECUTOR_REPAIR_WORK_ITEM_KIND})
EXECUTOR_LEADER_LOCK_KEY: Final = "plantgeo:unified-job-executor:v1"
EXECUTOR_REQUESTED_BY: Final = "agri-service ops jobs-executor"

#: A failed or partial checkpoint run an operator has superseded is one resolved `agri.job_incident` row
#: keyed by this prefix plus the run id; `select_latest_run.sql` reads it as `superseded_by_operator`.
#: See execution/AGENTS.md, "Failed checkpoints are superseded by the clock or by an operator".
RUN_SUPERSESSION_INCIDENT_TYPE: Final = "plantgeo.executor.run_superseded"
RUN_SUPERSESSION_FINGERPRINT_PREFIX: Final = "plantgeo.executor.run-superseded:"
SUPERSEDE_RUN_COMMAND: Final = "agri-service ops jobs-supersede-run"
#: The two run statuses that settle a checkpoint without success and block the lane behind it.
SETTLED_WITHOUT_SUCCESS: Final[frozenset[str]] = frozenset({"failed", "partial"})
#: How many of a lane's newest terminal runs the checkpoint query inspects for its failure streak. One
#: bounded backward index probe; no policy below ever needs a longer streak than this.
FAILURE_STREAK_PROBE_LIMIT: Final = 3

ACTIVE_LANES_VARIABLE: Final = "PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES"
POLL_SECONDS_VARIABLE: Final = "PLANTGEO_JOB_EXECUTOR_POLL_SECONDS"
MAX_LANES_PER_TICK_VARIABLE: Final = "PLANTGEO_JOB_EXECUTOR_MAX_LANES_PER_TICK"
#: How often the leader re-reads Parquet coverage and authors bounded repair turns; `0` disables authoring.
REPAIR_INTERVAL_VARIABLE: Final = "PLANTGEO_JOB_EXECUTOR_REPAIR_INTERVAL_SECONDS"
DEFAULT_REPAIR_INTERVAL_SECONDS: Final = 6 * 3600.0
#: How long an authored layer sits out of the pass budget so the other layers get their turn.
DEFAULT_REPAIR_ROTATION_SECONDS: Final = 24 * 3600.0
#: Whether a NEW executor process releases a breaker-held lane once. `0` keeps every hold for an operator.
PROCESS_START_RELEASE_VARIABLE: Final = "PLANTGEO_JOB_EXECUTOR_PROCESS_START_RELEASES_BREAKER"
PROCESS_START_RELEASE_OPERATOR: Final = "executor:process-start"
DEPLOYMENT_ID_VARIABLE: Final = "RAILWAY_DEPLOYMENT_ID"

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
#: How much of a child's stderr the wrapper keeps for the ledger. The TAIL, because a Python traceback ends
#: with the exception that matters and a bounded head would keep only the warnings that preceded it.
COMMAND_STDERR_TAIL_BYTES: Final = 4096
COMMAND_STDERR_READ_BYTES: Final = 4096
#: How long the wrapper waits for the child's stderr pipe to reach EOF once the child has exited or been killed.
COMMAND_STDERR_DRAIN_SECONDS: Final = 5.0
#: Characters of that tail allowed into `last_error_summary`, leaving the headline room inside the ledger's clamp.
COMMAND_STDERR_SUMMARY_CHARS: Final = FAILURE_SUMMARY_MAX_LENGTH - 120
#: How much of a child's stdout the wrapper keeps: enough for the ONE terminal JSON report every direct writer
#: prints last, whose `unwritten` list is what the ledger must not lose at exit 0.
COMMAND_STDOUT_TAIL_BYTES: Final = 64 * 1024
#: How many unwritten days one checkpoint records verbatim, and how long each day's detail may be.
TURN_REPORT_UNWRITTEN_MAX: Final = 12
TURN_REPORT_DETAIL_CHARS: Final = 200
#: The blocker string prefix `_held_checkpoint_result` writes; the typed `operator_action` carries the same command.
OPERATOR_SUPERSESSION_BLOCKER_PREFIX: Final = "operator supersession required: "

LaneWorkClass = Literal["incremental", "backlog"]
MigrationDisposition = Literal["consolidatable", "source-specific", "snapshot-only"]
CatchUpPolicy = Literal["coalesce_latest", "replay_oldest"]
ReleaseMechanism = Literal["clock", "operator"]
#: The failure streak at which the clock stops releasing a lane and an operator must record a supersession.
#: A coalesce_latest lane tolerates two transient failed buckets (the third in a row is a broken lane); a
#: replay_oldest lane tolerates none, because every one of its buckets is owed.
CLOCK_RELEASE_STREAK_LIMIT: Final[Mapping[CatchUpPolicy, int]] = MappingProxyType(
    {"coalesce_latest": 3, "replay_oldest": 1}
)
LaneTickState = Literal[
    "shadow",
    "source_specific",
    "paused",
    "not_due",
    "deferred_fairness",
    "deferred_shutdown",
    "ran",
    "failed",
]


class ExecutorConfigurationError(ValueError):
    """Raised when executor lane configuration is invalid."""


class ExecutorLeaderUnlockError(RuntimeError):
    """Raised when the pinned PostgreSQL backend cannot confirm leader-lock release."""


if max(CLOCK_RELEASE_STREAK_LIMIT.values()) > FAILURE_STREAK_PROBE_LIMIT:
    # The query caps the streak at the probe limit, so a limit above it could never be reached and the
    # breaker would be silently disarmed: a broken lane would mint one dead letter per bucket forever.
    raise ExecutorConfigurationError("FAILURE_STREAK_PROBE_LIMIT must cover every CLOCK_RELEASE_STREAK_LIMIT")


@dataclass(frozen=True, slots=True)
class LaneExecutionSpec:
    """One code-owned scheduling contract."""

    lane_id: str
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
    catch_up_policy: CatchUpPolicy
    command: tuple[str, ...] | None
    command_timeout_seconds: int
    description: str
    writer_floor: str | None = None
    writer_ceiling: str | None = None

    def __post_init__(self) -> None:
        if not self.lane_id.strip():
            raise ExecutorConfigurationError("lane_id must not be empty")
        if self.cadence_seconds is not None and self.cadence_seconds <= 0:
            raise ExecutorConfigurationError(f"{self.lane_id}: cadence_seconds must be positive")
        if self.phase_offset_seconds < 0:
            raise ExecutorConfigurationError(f"{self.lane_id}: phase_offset_seconds must not be negative")
        if self.cadence_seconds is not None and self.phase_offset_seconds >= self.cadence_seconds:
            raise ExecutorConfigurationError(
                f"{self.lane_id}: phase_offset_seconds must be smaller than cadence_seconds"
            )
        if self.command is not None and not self.command:
            raise ExecutorConfigurationError(f"{self.lane_id}: command must not be empty")
        if self.command_timeout_seconds <= 0:
            raise ExecutorConfigurationError(f"{self.lane_id}: command_timeout_seconds must be positive")

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
                "work_class": self.work_class,
                "migration_disposition": self.migration_disposition,
                "cadence_seconds": self.cadence_seconds,
                "phase_offset_seconds": self.phase_offset_seconds,
                "publication_lag_days": self.publication_lag_days,
                "publication_cadence_days": self.publication_cadence_days,
                "publication_lag_source": self.publication_lag_source,
                "selection_policy": self.selection_policy,
                "catch_up_policy": self.catch_up_policy,
                "writer_floor": self.writer_floor,
                "writer_ceiling": self.writer_ceiling,
            },
        )

    def inventory_row(self, *, active: bool) -> dict[str, object]:
        return {
            "lane_id": self.lane_id,
            "conflicts_with": list(self.conflicts_with),
            "active": active,
            "work_class": self.work_class,
            "migration_disposition": self.migration_disposition,
            "cadence_seconds": self.cadence_seconds,
            "phase_offset_seconds": self.phase_offset_seconds,
            "schedule": self.schedule,
            "publication_lag_days": self.publication_lag_days,
            "publication_cadence_days": self.publication_cadence_days,
            "publication_lag_source": self.publication_lag_source,
            "selection_policy": self.selection_policy,
            "catch_up_policy": self.catch_up_policy,
            "command": None if self.command is None else list(self.command),
            "command_timeout_seconds": self.command_timeout_seconds,
            "checkpoint": "agri.job_work_item.cursor",
            "lease_seconds": self.command_timeout_seconds + 120,
            "max_attempts": 5,
            "retry_policy": {
                "initial_backoff_seconds": 30,
                "backoff_multiplier": 2,
                "maximum_backoff_seconds": 3600,
            },
            "dead_letter_visibility": "agri.job_work_item status=dead_letter and agri.job_event",
            "rollback": f"remove lane from {ACTIVE_LANES_VARIABLE}",
            "executable": self.executable,
            "description": self.description,
            "writer_floor": self.writer_floor,
            "writer_ceiling": self.writer_ceiling,
            "source_watermark_parity": "not_evaluated",
        }


def _registration(slug: str) -> tuple[int, int, str | None]:
    lane = LANE_REGISTRY[slug]
    ceiling = None if lane.writer_ceiling is None else lane.writer_ceiling.isoformat()
    return lane.publication_lag_days, lane.cadence_days, ceiling


def _spec(  # noqa: PLR0913 - this is the declarative constructor for the code-owned lane table
    lane_id: str,
    *,
    command: tuple[str, ...] | None,
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
    catch_up_policy: CatchUpPolicy | None = None,
    timeout_seconds: int = 900,
    description: str,
    writer_floor: str | None = None,
    writer_ceiling: str | None = None,
) -> LaneExecutionSpec:
    return LaneExecutionSpec(
        lane_id=lane_id,
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
        catch_up_policy=("replay_oldest" if work_class == "backlog" else "coalesce_latest")
        if catch_up_policy is None
        else catch_up_policy,
        command=command,
        command_timeout_seconds=timeout_seconds,
        description=description,
        writer_floor=writer_floor,
        writer_ceiling=writer_ceiling,
    )


# Environmental source schedules are explicit below. Generic `parquet-*` gap-fill and drain
# schedules were removed from the executor catalog on 2026-09-12; they were the last reactivation
# surface for a PostgreSQL-backed environmental export. Direct source packages own acquisition and
# publication, and an unadmitted product remains unavailable until its Parquet contract is published.
# Bounded gap repair of what those writers publish is NOT a generic lane either: it is a per-lane repair
# definition that runs the owning writer's own command (`repair_lane_spec`, `gap_repair_contract.py`).
# The lane identifiers themselves live in `execution/lane_ids.py`, a leaf, so the repair contract can name
# a lane without importing this scheduler.

# PostgreSQL materialized-view refresh jobs were retired with the forecast/source cutover.
_DURABLE_JOB_SCHEDULES: Final[tuple[tuple[str, int, int, int, str, CatchUpPolicy], ...]] = ()


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
            cadence_seconds=cadence_seconds,
            phase_offset_seconds=phase_offset_seconds,
            schedule=schedule,
            catch_up_policy=catch_up_policy,
            publication_lag_source=f"durable definition {lane}",
            selection_policy="one isolated durable slice per turn",
            timeout_seconds=inner_budget + COMMAND_CLEANUP_MARGIN_SECONDS,
            description=f"Independent durable failure domain for {lane}.",
        )
        for lane, inner_budget, cadence_seconds, phase_offset_seconds, schedule, catch_up_policy in (
            _DURABLE_JOB_SCHEDULES
        )
    ),
)

_MIGRATION_INPUT_SPECS: Final[tuple[LaneExecutionSpec, ...]] = (
    _spec(
        FIRE_DETECTIONS_DIRECT_LANE_ID,
        command=("python", "-m", "agri_data_service.pipeline.direct.fire_detections"),
        phase_offset_seconds=900,
        schedule="15 * * * *",
        publication_lag_days=_registration("fire-detections")[0],
        publication_cadence_days=_registration("fire-detections")[1],
        publication_lag_source="pipeline/parquet/lane_registry.py fire-detections contract",
        description="Direct FIRMS forward writer for the admitted Parquet stream.",
        writer_floor=FIRE_DETECTIONS_DIRECT_WRITER_START_DAY.isoformat(),
    ),
    _spec(
        WATER_GAUGES_DIRECT_LANE_ID,
        command=("python", "-m", "agri_data_service.pipeline.parquet.water_gauges_forward"),
        disposition="source-specific",
        phase_offset_seconds=900,
        schedule="15 * * * *",
        publication_lag_days=_registration("water-gauges")[0],
        publication_cadence_days=_registration("water-gauges")[1],
        publication_lag_source="pipeline/parquet/lane_registry.py water-gauges contract",
        timeout_seconds=1800,
        description="Bounded direct water forward writer for the admitted Parquet stream.",
        writer_floor=WATER_GAUGES_DIRECT_WRITER_START_DAY.isoformat(),
    ),
    _spec(
        MTBS_FORWARD_LANE_ID,
        command=("python", "-m", "agri_data_service.pipeline.direct.burn_severity"),
        cadence_seconds=604800,
        phase_offset_seconds=460500,
        schedule="55 7 * * 2",
        publication_lag_days=_registration("burn-severity")[0],
        publication_cadence_days=_registration("burn-severity")[1],
        publication_lag_source="pipeline/parquet/lane_registry.py burn-severity contract",
        timeout_seconds=1800,
        description="Source-direct MTBS capture and governed Parquet publication.",
    ),
    _spec(
        CLIMATE_DIRECT_LANE_ID,
        command=("python", "-m", "agri_data_service.pipeline.direct.climate"),
        # The larger of the two source lags and the earliest of the eight floors cover the shared
        # direct writer contract.
        disposition="source-specific",
        phase_offset_seconds=2400,
        schedule="40 * * * *",
        publication_lag_days=CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS,
        publication_cadence_days=1,
        publication_lag_source="pipeline/parquet/lane_registry.py climate-field-* contracts",
        selection_policy="newest settled unfilled day first, one product-day per lane-day lock",
        timeout_seconds=int(CLIMATE_DEFAULT_TIME_BUDGET_SECONDS) + COMMAND_CLEANUP_MARGIN_SECONDS,
        description=(
            "Direct NASA POWER forward writer for the eight climate-field streams and the three "
            "soil-wetness depths. ACTIVE in production: the ledger opens and runs one bucket per hour "
            "(measured 2026-09-15, buckets 00:40, 01:40, 12:40, 13:40, 23:40 each `ran`/`succeeded`)."
        ),
        writer_floor=min(product.history_floor for product in CLIMATE_FIELD_PRODUCTS).isoformat(),
    ),
    _spec(
        SOIL_DIRECT_LANE_ID,
        command=("python", "-m", "agri_data_service.pipeline.direct.soil"),
        # One shared lag applies: unlike the climate writer's two publication clocks, all eight
        # ERA5-Land streams come off one model on one release schedule. The phase offset is
        # its own so the two direct writers never open their fan-outs in the same minute -- they
        # share no lane, but they do share this container's CPU and egress.
        disposition="source-specific",
        phase_offset_seconds=3000,
        schedule="50 * * * *",
        publication_lag_days=ERA5_LAND_ARCHIVE_PUBLICATION_LAG_DAYS,
        publication_cadence_days=1,
        publication_lag_source="pipeline/parquet/lane_registry.py soil-field-*/soil-temperature-* contracts",
        selection_policy="newest settled unfilled day first, one product-day per lane-day lock",
        timeout_seconds=int(SOIL_DEFAULT_TIME_BUDGET_SECONDS) + COMMAND_CLEANUP_MARGIN_SECONDS,
        description=(
            "Direct Open-Meteo ERA5-Land forward writer for the three moisture, four temperature and "
            "one VPD streams. ACTIVE in production, one bucket per hour at :50 (measured 2026-09-15)."
        ),
        writer_floor=min(product.history_floor for product in SOIL_FIELD_PRODUCTS).isoformat(),
    ),
    _spec(
        VEGETATION_DIRECT_LANE_ID,
        command=("python", "-m", "agri_data_service.pipeline.direct.vegetation"),
        # This is the sole scheduled owner of the source-direct stream.
        disposition="source-specific",
        phase_offset_seconds=300,
        schedule="5 * * * *",
        publication_lag_days=_registration("vegetation")[0],
        publication_cadence_days=_registration("vegetation")[1],
        publication_lag_source="pipeline/parquet/lane_registry.py vegetation contract",
        selection_policy="newest unfilled settled day first, one product-day per lane-day lock",
        timeout_seconds=int(VEGETATION_DEFAULT_TIME_BUDGET_SECONDS) + COMMAND_CLEANUP_MARGIN_SECONDS,
        description=(
            "Direct Sentinel-2 NDVI forward writer for the admitted Parquet stream. Historical repair "
            "and parity are manual source-direct operations, never a scheduled database export."
        ),
        # forward.py::history_floor() == max(registered floor, START_DAY + 1 day): the boundary day
        # itself belongs to backfill.py, not to this lane. See VEGETATION_PLANE_STREAM's writer_ceiling
        # in pipeline/parquet/lane_registry.py for the other side of the same boundary.
        writer_floor=(VEGETATION_DIRECT_WRITER_START_DAY + timedelta(days=1)).isoformat(),
    ),
    _spec(
        WEATHER_OBSERVATIONS_DIRECT_LANE_ID,
        command=("python", "-m", "agri_data_service.pipeline.direct.weather_observations"),
        disposition="source-specific",
        phase_offset_seconds=1800,
        schedule="30 * * * *",
        publication_lag_days=_registration("weather-observations")[0],
        publication_cadence_days=_registration("weather-observations")[1],
        publication_lag_source="pipeline/parquet/lane_registry.py weather-observations contract",
        selection_policy="one current-conditions poll merged into the (at most two) day buckets it touched",
        timeout_seconds=int(WEATHER_OBSERVATIONS_DEFAULT_TIME_BUDGET_SECONDS) + COMMAND_CLEANUP_MARGIN_SECONDS,
        description=(
            "Direct Open-Meteo current-conditions forward writer for weather-observations. Unlike "
            "vegetation/fire-detections/water-gauges, no cited ownership-boundary constant exists yet "
            "in pipeline/direct/weather_observations/ (no backfill.py, no *_DIRECT_WRITER_START_DAY), "
            "so no writer_ceiling/writer_floor was guessed here without one to cite. ACTIVE since 2026-09-07, "
            "and LANE_REGISTRY['weather-observations'].adapter became a source-direct refusal in the same "
            "wave: a boundary day is only worth having while TWO writers share the window, and they no "
            "longer do. There is still no cited boundary; there is no longer anything for it to divide."
        ),
    ),
    _spec(
        DROUGHT_DIRECT_LANE_ID,
        command=("python", "-m", "agri_data_service.pipeline.direct.drought"),
        disposition="source-specific",
        phase_offset_seconds=2700,
        schedule="45 * * * *",
        publication_lag_days=_registration("drought")[0],
        publication_cadence_days=_registration("drought")[1],
        publication_lag_source="pipeline/parquet/lane_registry.py drought contract",
        selection_policy="newest settled USDM release Tuesday first, one release per lane-day lock",
        timeout_seconds=int(DROUGHT_DEFAULT_TIME_BUDGET_SECONDS) + COMMAND_CLEANUP_MARGIN_SECONDS,
        description=(
            "Direct USDM forward writer for the admitted Parquet stream. Forward and historical repair "
            "are source-direct and no database export lane is registered."
        ),
        # The direct writer's own floor, not a boundary: `forward.py:125` scans from
        # `max(lane.history_floor, ...)` and `backfill.py:72` walks `release_weeks(lane.history_floor,
        # ...)`, so both halves start at exactly this registration's floor.
        writer_floor=LANE_REGISTRY["drought"].history_floor.isoformat(),
    ),
    # --- Source-direct writers: one scheduled owner per stream -------------------------
    _spec(
        FIRE_PERIMETERS_DIRECT_LANE_ID,
        command=("python", "-m", "agri_data_service.pipeline.direct.fire_perimeters"),
        disposition="source-specific",
        phase_offset_seconds=600,
        schedule="10 * * * *",
        publication_lag_days=_registration("fire-perimeters")[0],
        publication_cadence_days=_registration("fire-perimeters")[1],
        publication_lag_source="pipeline/parquet/lane_registry.py fire-perimeters contract",
        selection_policy="one source-watermark resolution per turn; at most one version published, or none at all",
        timeout_seconds=int(FIRE_PERIMETERS_DEFAULT_TIME_BUDGET_SECONDS) + COMMAND_CLEANUP_MARGIN_SECONDS,
        description=(
            "Direct WFIGS _Current forward writer for fire-perimeters, a static_lookup lane. The "
            "direct writer owns both its source capture and version clock. NO "
            "writer_ceiling is declared on the registration and none is possible: "
            "LaneRegistration.__post_init__ refuses a ceiling on a version-stamped lane. There is no "
            "backfill module and there cannot be one -- WFIGS _Current retains nothing, so no past "
            "version is re-fetchable and the 45 days the retired daily_series shape wrote are all the "
            "history this lane will ever have. Hourly at :10, the cadence of the _Current poller it "
            "replaces. Shadow until activated; the adapter AND watermark swap belongs in the same push "
            "that drops geo.features."
        ),
    ),
    _spec(
        SENSORS_DIRECT_LANE_ID,
        command=("python", "-m", "agri_data_service.pipeline.direct.sensors"),
        disposition="source-specific",
        phase_offset_seconds=1200,
        schedule="20 * * * *",
        publication_lag_days=_registration("sensors")[0],
        publication_cadence_days=_registration("sensors")[1],
        publication_lag_source="pipeline/parquet/lane_registry.py sensors contract",
        selection_policy="one rolling-window poll, merge-published into every day bucket it touched, newest first",
        timeout_seconds=int(SENSORS_DEFAULT_TIME_BUDGET_SECONDS) + COMMAND_CLEANUP_MARGIN_SECONDS,
        description=(
            "Direct NOAA NWS forward writer for sensors. One poll fetches the FULL rolling window every "
            "run (SENSORS_MAX_DAYS = NWS_OBSERVATION_RETENTION.days + 1, seven buckets), which "
            "self-heals across a missed tick at no extra HTTP cost: observation_url issues one request "
            "per station whether a window is asked for or not. HOURLY AT :20 IS A DECIDED TRADE, not a "
            "default -- roughly hourly is NWS's own publication cadence, so a :15/:30-style sub-hourly "
            "slot would re-transfer the same mostly-unchanged six days several times an hour for no new "
            "readings, while a slower slot risks a day ageing out of NWS's rolling retention unseen, "
            "which loses it from the source forever. Like weather-observations, this package ships no "
            "*_DIRECT_WRITER_START_DAY and no backfill.py, so no writer_floor was guessed without a boundary "
            "day to cite. ACTIVE since 2026-09-07; the adapter became a source-direct refusal once "
            "postgres-sensors was deleted, because a FROZEN geo.features is not a deeper archive -- it "
            "is a fixed set of past days, so no database export is registered for this lane."
        ),
    ),
    _spec(
        WATERSHEDS_DIRECT_LANE_ID,
        command=("python", "-m", "agri_data_service.pipeline.direct.watersheds"),
        disposition="source-specific",
        cadence_seconds=86400,
        phase_offset_seconds=10800,
        schedule="0 3 * * *",
        publication_lag_days=_registration("watersheds")[0],
        publication_cadence_days=_registration("watersheds")[1],
        publication_lag_source="pipeline/parquet/lane_registry.py watersheds contract",
        selection_policy="one source-derived version per turn, published only when NHDPlus_HR's own loaddate has moved",
        # `pipeline/direct/watersheds/forward.py` exposes no `--time-budget-seconds` -- its CLI is
        # `--max-days/--bbox/--run-id` -- so this command timeout is the ONLY bound on a turn, and it
        # is sized for the whole walk rather than copied from the 300 s siblings that move small JSON.
        timeout_seconds=1800,
        description=(
            "Direct NHDPlus_HR WBDHU12 forward writer for watersheds. The adapter-and-watermark swap "
            "this description used to say activation still owed LANDED 2026-09-06: "
            "LANE_REGISTRY['watersheds'] now carries a source-direct refusal and a watermark reading "
            "NHDPlus_HR's own loaddate, so neither half depends on geo.features any longer and the "
            "census cannot freeze when postgres-watersheds stops -- and it HAS stopped: that lane, its "
            "ingest-watersheds verb and the Postgres exporter behind it were deleted on 2026-09-06, so "
            "this is now the ONLY writer of the watersheds stream. DAILY at 03:00, one hour after the "
            "02:00 slot the deleted postgres-watersheds lane held, a phase kept so the parity receipt "
            "and any operator re-run never open the same fetch minute: one turn is the same "
            "~9,400-basin, ~47-request NHDPlus_HR walk the export itself "
            "performs, against a national reference layer measured to hold exactly ONE load day in its "
            "whole history, so an hourly slot would pay that walk 24 times a day to detect a change "
            "that has happened once. Shadow until activated."
        ),
    ),
    _spec(
        EVACUATION_ZONES_DIRECT_LANE_ID,
        command=("python", "-m", "agri_data_service.pipeline.direct.evacuation_zones"),
        disposition="source-specific",
        phase_offset_seconds=2100,
        schedule="35 * * * *",
        publication_lag_days=_registration("evacuation-zones")[0],
        publication_cadence_days=_registration("evacuation-zones")[1],
        publication_lag_source="pipeline/parquet/lane_registry.py evacuation-zones contract",
        selection_policy=(
            "one statewide capture per turn, published only when its content digest differs from the "
            "newest already-published version"
        ),
        timeout_seconds=int(EVACUATION_ZONES_DEFAULT_TIME_BUDGET_SECONDS) + COMMAND_CLEANUP_MARGIN_SECONDS,
        description=(
            "Direct Oregon OEM forward writer for evacuation-zones, a static_lookup lane whose change "
            "test moved INTO the writer: it digests the captured population (rows.content_digest) and "
            "publishes only when that differs from the newest version already published, so a tick that "
            "finds nothing changed writes nothing and a skipped tick costs nothing. No writer_ceiling is "
            "possible here either -- a version-stamped lane has no calendar window to divide -- so "
            "The direct writer is the only scheduled owner of this source-direct stream. The watermark "
            "replacement this description used to say was still owed "
            "LANDED 2026-09-06: _evacuation_zones_watermark no longer reads geo.features or geo.geometry, "
            "it runs the SAME content digest the writer publishes on, so census and writer cannot "
            "disagree and neither survives a last_edited_date re-stamp on an unchanged area. Hourly at "
            ":35, the cadence of the postgres-evacuation-zones poller it replaced -- that lane, its "
            "ingest-evacuation-zones verb and the job behind it were deleted on 2026-09-07, so this is "
            "now the ONLY writer of the evacuation-zones stream. Shadow until activated."
        ),
    ),
    _spec(
        BURN_SEVERITY_DIRECT_LANE_ID,
        command=(
            "python",
            "-m",
            "agri_data_service.pipeline.direct.burn_severity",
            "--current-snapshots",
            "--time-budget-seconds",
            "1800",
        ),
        disposition="source-specific",
        cadence_seconds=86400,
        phase_offset_seconds=32100,
        schedule="55 8 * * *",
        publication_lag_days=None,
        publication_cadence_days=None,
        publication_lag_source="mixed: historical registered cohorts; current capture D+1 UTC with no extra lag",
        selection_policy=(
            "daily earliest eligible staged snapshot; weekly bounded current capture; five historical cohorts retained"
        ),
        timeout_seconds=int(BURN_SEVERITY_MAX_TIME_BUDGET_SECONDS) + COMMAND_CLEANUP_MARGIN_SECONDS,
        description=(
            "Daily 08:55 UTC eligible-stage check and weekly bounded current MTBS capture. "
            "Current snapshots become eligible the UTC day after capture and replace the current "
            "2018-2026 footprint; 2023-2026 mapping remains partial. Identical source content records "
            "a check without another release. Historical five-cohort repair retains its registered "
            "release dates and lag. The fixed year horizon refuses 2027 until reviewed."
        ),
        # The direct writer's own floor, not a boundary: `products.governed_release_days()` opens at
        # 2020-11-24, which is exactly the day this registration's floor cites (measured 2026-09-06,
        # pinned by `tests/test_job_executor_service.py`).
        writer_floor=LANE_REGISTRY["burn-severity"].history_floor.isoformat(),
    ),
)

_LANE_SPECS: Final[tuple[LaneExecutionSpec, ...]] = (
    *_JOBS_SPECS,
    *_MIGRATION_INPUT_SPECS,
)

LANE_SPECS: Final[Mapping[str, LaneExecutionSpec]] = MappingProxyType({spec.lane_id: spec for spec in _LANE_SPECS})

# Every conflict must name a lane that exists. `parse_activation` intersects `conflicts_with` with the
# ACTIVE set, so a misspelled or renamed target is not merely unenforced there -- it is invisible: the
# intersection is empty and the pairing activates. Direct source lanes currently have no conflicts;
# this assertion remains for any future control-plane mutual exclusion.
assert not {target for spec in _LANE_SPECS for target in spec.conflicts_with} - LANE_SPECS.keys(), (
    "a lane declares conflicts_with against a lane id that is not in LANE_SPECS"
)


_TRY_LEADER_LOCK: Final = text("SELECT pg_try_advisory_lock(hashtextextended(:lock_key, 0)) AS acquired")
_RELEASE_LEADER_LOCK: Final = text("SELECT pg_advisory_unlock(hashtextextended(:lock_key, 0)) AS released")
_SELECT_DEFINITION_STATE: Final = text(load_query_sql("execution/select_definition_state.sql"))
_INSERT_DEFINITION: Final = text(load_query_sql("execution/insert_definition.sql"))
_SELECT_LATEST_RUN: Final = text(load_query_sql("execution/select_latest_run.sql"))


@dataclass(frozen=True, slots=True)
class ActivationConfig:
    """The explicit lane allow-list."""

    active_lanes: frozenset[str]

    def is_active(self, lane_id: str) -> bool:
        return lane_id in self.active_lanes


def _comma_tokens(value: str) -> tuple[str, ...]:
    return tuple(token.strip() for token in value.split(",") if token.strip())


def parse_activation(environment: Mapping[str, str] | None = None) -> ActivationConfig:
    """Parse and validate the active lane allow-list, defaulting every lane to shadow."""
    source = os.environ if environment is None else environment
    active = frozenset(_comma_tokens(source.get(ACTIVE_LANES_VARIABLE, "")))
    unknown = sorted(active - LANE_SPECS.keys())
    if unknown:
        raise ExecutorConfigurationError(f"unknown active lane(s): {', '.join(unknown)}")

    for lane_id in sorted(active):
        conflicts = sorted(set(LANE_SPECS[lane_id].conflicts_with) & active)
        if conflicts:
            raise ExecutorConfigurationError(f"lane {lane_id!r} conflicts with active lane(s): {', '.join(conflicts)}")

    for lane_id in sorted(active):
        spec = LANE_SPECS[lane_id]
        if not spec.executable:
            raise ExecutorConfigurationError(
                f"lane {lane_id!r} is {spec.migration_disposition} and has no executor command"
            )
    return ActivationConfig(active_lanes=active)


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


def next_scheduled_bucket(
    spec: LaneExecutionSpec,
    now: datetime,
    latest_scheduled_for: datetime | None,
) -> datetime:
    """Choose the next logical bucket under the lane's restart catch-up contract."""
    current = scheduled_bucket(spec, now)
    if latest_scheduled_for is None or latest_scheduled_for >= current:
        return current
    if spec.catch_up_policy == "coalesce_latest":
        return current
    return min(bucket_after(spec, latest_scheduled_for), current)


def bucket_after(spec: LaneExecutionSpec, scheduled_for: datetime) -> datetime:
    """Return the cadence bucket immediately after `scheduled_for`: the one a replayed lane opens next."""
    if spec.cadence_seconds is None:
        raise ExecutorConfigurationError(f"lane {spec.lane_id!r} has no recurring cadence")
    return datetime.fromtimestamp(int(scheduled_for.timestamp()) + spec.cadence_seconds, tz=UTC)


@dataclass(frozen=True, slots=True)
class LatestRun:
    run_id: uuid.UUID
    scheduled_for: datetime
    status: str
    work_claimable: bool
    has_work_items: bool = True
    terminal_items_need_rollup: bool = False
    definition_id: uuid.UUID | None = None
    definition_version: str = EXECUTOR_DEFINITION_VERSION
    definition_enabled: bool = True
    superseded_by_operator: bool = False
    consecutive_failures: int = 0

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
    #: The failed or partial checkpoint this bucket supersedes, and what released it; None for an ordinary bucket.
    superseded_run_id: uuid.UUID | None = None
    supersession: ReleaseMechanism | None = None


def fair_due_order(candidates: Sequence[DueLane]) -> tuple[DueLane, ...]:
    """Interleave work classes while ordering eligible lanes by their oldest cadence checkpoint."""
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
    blockers: tuple[str, ...] = ()
    due_prediction: str | None = None
    #: The exact `jobs-supersede-run` invocation that releases this lane, when only an operator can. Typed so
    #: a consumer never parses it back out of `blockers`; see execution/AGENTS.md, "Operator action surface".
    operator_action: str | None = None
    #: What the lane's own terminal report said this bucket, when the command ran in this process.
    turn_report: TurnReport | None = None

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
            "blockers": list(self.blockers),
            "due_prediction": self.due_prediction,
            "operator_action": self.operator_action,
            "turn_report": None if self.turn_report is None else self.turn_report.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class TurnReport:
    """The bounded facts kept from a writer's terminal stdout report: did the turn leave days unwritten?

    A direct lane exits 0 when at least one day wrote and reports `outcome=incomplete` with an `unwritten`
    list; before this nothing consumed that list, so a day stuck refusing re-refused every bucket silently.
    `consecutive_incomplete_buckets` is held per DEFINITION in THIS process (`_LANE_TURN_REPORTS`, a repair
    definition counts separately from its owning lane) and is honest about that: a restart resets it to
    the buckets seen since. See execution/AGENTS.md, "Turn reports".
    """

    outcome: str | None
    days_unwritten: int
    unwritten: tuple[Mapping[str, object], ...]
    unwritten_truncated: bool
    consecutive_incomplete_buckets: int = 0

    @property
    def incomplete(self) -> bool:
        return self.days_unwritten > 0

    def to_dict(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "days_unwritten": self.days_unwritten,
            "unwritten": [dict(entry) for entry in self.unwritten],
            "unwritten_truncated": self.unwritten_truncated,
            "consecutive_incomplete_buckets": self.consecutive_incomplete_buckets,
        }


#: Per owning lane, the newest turn report this process ran and its incomplete-bucket streak.
_LANE_TURN_REPORTS: dict[str, TurnReport] = {}


def parse_terminal_report(stdout_tail: bytes) -> Mapping[str, object] | None:
    """Return the LAST stdout line that is a JSON object -- the one terminal report a writer prints -- or `None`."""
    for raw in reversed(stdout_tail.decode("utf-8", errors="replace").splitlines()):
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _unwritten_entries(report: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Collect `unwritten` entries from the report and from any per-product `results` it fans out into."""
    found: list[Mapping[str, object]] = []
    own = report.get("unwritten")
    if isinstance(own, list):
        found.extend(entry for entry in own if isinstance(entry, dict))
    results = report.get("results")
    if isinstance(results, list):
        for product in results:
            if isinstance(product, dict):
                found.extend(_unwritten_entries(product))
    return found


def summarize_turn_report(report: Mapping[str, object] | None, *, previous: TurnReport | None) -> TurnReport | None:
    """Bound one parsed report to what a checkpoint may hold, continuing the lane's incomplete-bucket streak."""
    if report is None:
        return None
    entries = _unwritten_entries(report)
    declared = report.get("days_unwritten")
    days_unwritten = declared if isinstance(declared, int) and not isinstance(declared, bool) else len(entries)
    # Redacted HERE because the cursor path (`record_checkpoint`) canonicalises but never redacts; a child
    # that echoed a keyed URL into a day's detail must not put it on a durable row.
    kept = tuple(
        {
            "day": entry.get("day"),
            "outcome": entry.get("outcome"),
            "detail": redact_text(str(entry.get("detail", "")))[:TURN_REPORT_DETAIL_CHARS],
        }
        for entry in entries[:TURN_REPORT_UNWRITTEN_MAX]
    )
    outcome = report.get("outcome", report.get("status"))
    streak = (previous.consecutive_incomplete_buckets if previous is not None else 0) + 1 if days_unwritten else 0
    return TurnReport(
        outcome=str(outcome) if outcome is not None else None,
        days_unwritten=days_unwritten,
        unwritten=kept,
        unwritten_truncated=len(entries) > len(kept),
        consecutive_incomplete_buckets=streak,
    )


def record_turn_report(lane_id: str, report: Mapping[str, object] | None) -> TurnReport | None:
    """Fold one bucket's report into the process-held streak for its owning lane and return what was kept."""
    kept = summarize_turn_report(report, previous=_LANE_TURN_REPORTS.get(lane_id))
    if kept is not None:
        _LANE_TURN_REPORTS[lane_id] = kept
    return kept


@dataclass(frozen=True, slots=True)
class OperatorAction:
    """One held lane and the single command that releases it."""

    lane_id: str
    run_id: uuid.UUID | None
    command: str

    def to_dict(self) -> dict[str, object]:
        return {
            "lane_id": self.lane_id,
            "run_id": None if self.run_id is None else str(self.run_id),
            "command": self.command,
        }


@dataclass(frozen=True, slots=True)
class ExecutorTickSummary:
    observed_at: datetime
    leader: bool
    lanes: tuple[LaneTickResult, ...]

    @property
    def failed(self) -> bool:
        return any(lane.state == "failed" for lane in self.lanes)

    @property
    def operator_actions(self) -> tuple[OperatorAction, ...]:
        """Every lane this tick found held behind a recorded-supersession requirement, with its release command."""
        return tuple(
            OperatorAction(lane_id=lane.lane_id, run_id=lane.run_id, command=lane.operator_action)
            for lane in self.lanes
            if lane.operator_action is not None
        )

    @property
    def incomplete_lanes(self) -> tuple[LaneTickResult, ...]:
        """Every lane that ran this tick, exited 0, and still reported days it could not write."""
        return tuple(lane for lane in self.lanes if lane.turn_report is not None and lane.turn_report.incomplete)

    def to_dict(self) -> dict[str, object]:
        return {
            "event": "plantgeo_job_executor_tick",
            "observed_at": self.observed_at.isoformat(),
            "leader": self.leader,
            "failed": self.failed,
            "operator_actions": [action.to_dict() for action in self.operator_actions],
            "incomplete_lanes": [
                {
                    "lane_id": lane.lane_id,
                    "days_unwritten": lane.turn_report.days_unwritten,
                    "consecutive_incomplete_buckets": lane.turn_report.consecutive_incomplete_buckets,
                }
                for lane in self.incomplete_lanes
                if lane.turn_report is not None
            ],
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


async def _commit_planning_transaction(session: AsyncSession) -> None:
    """Start the next planning transaction with its transaction-local timeout restored."""
    await session.commit()
    await apply_statement_timeout(session)


async def _rollback_planning_transaction(session: AsyncSession) -> None:
    """Start the next planning transaction with its transaction-local timeout restored."""
    await session.rollback()
    await apply_statement_timeout(session)


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
    """Register a missing version fail-closed and preserve the lane-wide operator pause."""
    pause_state = await read_lane_pause_state(session, spec.definition_name)
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
                "enabled": not pause_state.registered,
                "concurrency_key": definition_spec.concurrency_key,
                "max_attempts": definition_spec.max_attempts,
                "lease_seconds": definition_spec.lease_seconds,
                "time_budget_seconds": definition_spec.time_budget_seconds,
                "retry_policy": canonical_json(definition_spec.retry_policy.to_json()),
                "parameters": canonical_json(definition_spec.parameters),
            },
        )
        await _commit_planning_transaction(session)
        state = await _definition_state(session, spec)
        if state is None:
            raise RuntimeError(f"executor definition {spec.definition_name!r} was neither inserted nor readable")
    _, enabled = state
    if pause_state.paused or not enabled:
        await _rollback_planning_transaction(session)
        return None
    definition = await load_job_definition(
        session,
        spec.definition_name,
        version=EXECUTOR_DEFINITION_VERSION,
    )
    await _rollback_planning_transaction(session)
    return definition


async def read_lane_checkpoint(session: AsyncSession, spec: LaneExecutionSpec) -> LatestRun | None:
    """Read the lane's scheduler checkpoint: the run a tick plans from, with its supersession and failure streak."""
    row = await fetch_row(
        session,
        _SELECT_LATEST_RUN,
        {
            "name": spec.definition_name,
            "current_version": EXECUTOR_DEFINITION_VERSION,
            "supersession_fingerprint_prefix": RUN_SUPERSESSION_FINGERPRINT_PREFIX,
            "failure_streak_limit": FAILURE_STREAK_PROBE_LIMIT,
        },
    )
    if row is None:
        return None
    return LatestRun(
        run_id=required_column(row, "id", uuid.UUID),
        scheduled_for=required_column(row, "scheduled_for", datetime),
        status=required_column(row, "status", str),
        work_claimable=required_column(row, "work_claimable", bool),
        has_work_items=required_column(row, "has_work_items", bool),
        terminal_items_need_rollup=required_column(row, "terminal_items_need_rollup", bool),
        definition_id=required_column(row, "job_definition_id", uuid.UUID),
        definition_version=required_column(row, "definition_version", str),
        definition_enabled=required_column(row, "definition_enabled", bool),
        superseded_by_operator=required_column(row, "superseded_by_operator", bool),
        consecutive_failures=required_column(row, "consecutive_failures", int),
    )


@dataclass(frozen=True, slots=True)
class CheckpointVerdict:
    """The planner's ruling on a checkpoint that settled without success; the operator verb rules by it too."""

    #: The bucket that opens once the checkpoint is released. Never the failed bucket itself.
    next_bucket: datetime
    #: Whether `next_bucket` is already reachable on the clock, or is the bucket after a failed current one.
    newer_bucket_exists: bool
    #: What releases the lane: the clock (the next bucket supersedes the failure) or a recorded supersession.
    release: ReleaseMechanism
    #: Whether the planner opens `next_bucket` on this tick.
    released: bool
    #: The unbroken run of settled-without-success checkpoints ending in this one, at least 1.
    consecutive_failures: int


def judge_failed_checkpoint(spec: LaneExecutionSpec, latest: LatestRun, now: datetime) -> CheckpointVerdict:
    """Rule on a failed or partial checkpoint: the bucket that opens next, what releases it, and whether that is now.

    The clock releases a lane while its failure streak is below its policy's limit. A coalesce_latest lane
    declares a missed bucket not owed, so a transient failure is superseded by the next bucket -- but three
    in a row is a broken lane, and the breaker holds it until an operator records a supersession; the
    a former maintenance loop minted 200 dead letters for want of exactly that. A replay_oldest lane owes every
    bucket, so its limit is one and only a recorded supersession ever releases it.

    Whatever releases it, the lane resumes at the CURRENT bucket, never at the buckets the hold cost: a
    coalesce_latest lane never owed them, and an operator's supersession forgives them for a replay_oldest
    lane -- its command re-censuses its own backlog, and `fair_due_order` favours the oldest checkpoint, so
    replaying a day of buckets would hand one lane the backlog class's single turn for hours. Nothing
    reopens the failed bucket itself: its logical run key is spent and its dead letter stays as the record.
    See execution/AGENTS.md, "Failed checkpoints are superseded by the clock or by an operator".
    """
    current = scheduled_bucket(spec, now)
    newer_bucket_exists = current > latest.scheduled_for
    next_bucket = current if newer_bucket_exists else bucket_after(spec, latest.scheduled_for)
    streak = max(latest.consecutive_failures, 1)
    release: ReleaseMechanism = "clock" if streak < CLOCK_RELEASE_STREAK_LIMIT[spec.catch_up_policy] else "operator"
    released = newer_bucket_exists and (release == "clock" or latest.superseded_by_operator)
    return CheckpointVerdict(
        next_bucket=next_bucket,
        newer_bucket_exists=newer_bucket_exists,
        release=release,
        released=released,
        consecutive_failures=streak,
    )


def supersession_command(spec: LaneExecutionSpec, run_id: uuid.UUID) -> str:
    """The exact operator invocation that records this checkpoint's supersession."""
    return f"{SUPERSEDE_RUN_COMMAND} --lane {spec.lane_id} --run-id {run_id}"


def _held_checkpoint_result(spec: LaneExecutionSpec, latest: LatestRun, verdict: CheckpointVerdict) -> LaneTickResult:
    """Report a checkpoint that settled without success and still holds its lane, naming what releases it."""
    needs_operator = verdict.release == "operator" and not latest.superseded_by_operator
    if not verdict.newer_bucket_exists:
        opens = "only after a recorded supersession" if needs_operator else "by itself"
        detail = (
            f"current bucket settled {latest.status}; its logical run is spent, and bucket "
            f"{verdict.next_bucket.isoformat()} opens {opens}"
        )
    else:
        detail = (
            f"{verdict.consecutive_failures} consecutive bucket(s) settled without success; the clock no longer "
            f"releases this {spec.catch_up_policy} lane, so bucket {verdict.next_bucket.isoformat()} waits for a "
            "recorded operator supersession"
        )
    command = supersession_command(spec, latest.run_id) if needs_operator else None
    return LaneTickResult(
        lane_id=spec.lane_id,
        state="failed",
        scheduled_for=latest.scheduled_for,
        run_id=latest.run_id,
        run_status=latest.status,
        detail=detail,
        blockers=(f"{OPERATOR_SUPERSESSION_BLOCKER_PREFIX}{command}",) if command is not None else (),
        operator_action=command,
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
    await _commit_planning_transaction(session)
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
    if stop is not None and stop.requested:
        return _deferred_shutdown_result(candidate)
    run_id = candidate.existing_run_id or await _open_scheduled_run(session, candidate)
    if candidate.superseded_run_id is not None:
        logger.info(
            "plantgeo_job_executor_failed_run_superseded",
            lane_id=candidate.spec.lane_id,
            superseded_run_id=str(candidate.superseded_run_id),
            release=candidate.supersession,
            bucket=candidate.scheduled_for.isoformat(),
            run_id=str(run_id),
        )
    summary = await run_job_slice(
        session,
        definition_name=candidate.spec.definition_name,
        version=candidate.definition.version,
        job_run_id=run_id,
        worker_id=_worker_id(candidate.spec),
        budget_seconds=float(candidate.definition.time_budget_seconds),
        stop=stop,
    )
    failed = (
        summary.retried > 0
        or summary.dead_lettered > 0
        or summary.abandoned > 0
        or summary.run_status in SETTLED_WITHOUT_SUCCESS
    )
    detail: str = (
        "work item dead-lettered"
        if summary.dead_lettered
        else "work item abandoned after losing its fenced lease"
        if summary.abandoned
        else "work item entered retry backoff"
        if summary.retried
        else summary.stop_reason
    )
    if candidate.superseded_run_id is not None:
        detail = f"supersedes run {candidate.superseded_run_id} by {candidate.supersession}; {detail}"
    turn_report = _LANE_TURN_REPORTS.get(candidate.spec.lane_id) if summary.claimed else None
    if turn_report is not None and turn_report.incomplete:
        detail = (
            f"{detail}; left {turn_report.days_unwritten} day(s) unwritten for "
            f"{turn_report.consecutive_incomplete_buckets} consecutive bucket(s) in this process"
        )
    return LaneTickResult(
        lane_id=candidate.spec.lane_id,
        state="failed" if failed else "ran",
        scheduled_for=candidate.scheduled_for,
        run_id=run_id,
        run_status=summary.run_status,
        detail=detail,
        slice_summary=summary.to_summary(),
        turn_report=turn_report,
    )


def _deferred_shutdown_result(candidate: DueLane) -> LaneTickResult:
    return LaneTickResult(
        lane_id=candidate.spec.lane_id,
        state="deferred_shutdown",
        scheduled_for=candidate.scheduled_for,
        run_id=candidate.existing_run_id,
        detail="shutdown requested before this lane was opened",
    )


def _blocked_open_run_result(
    spec: LaneExecutionSpec,
    latest: LatestRun,
    *,
    prior_version: bool,
) -> LaneTickResult | None:
    version_detail = (
        f"prior definition version {latest.definition_version!r}" if prior_version else "current definition"
    )
    if not latest.has_work_items:
        return LaneTickResult(
            lane_id=spec.lane_id,
            state="failed",
            scheduled_for=latest.scheduled_for,
            run_id=latest.run_id,
            run_status=latest.status,
            detail=f"{version_detail} has a nonterminal run with no work items; explicitly repair or cancel it",
        )
    if latest.work_claimable or latest.terminal_items_need_rollup:
        return None
    return LaneTickResult(
        lane_id=spec.lane_id,
        state="not_due",
        scheduled_for=latest.scheduled_for,
        run_id=latest.run_id,
        run_status=latest.status,
        detail=f"{version_detail} has no currently claimable work; retry, defer, or live lease wait remains",
    )


async def _plan_prior_version_run(
    session: AsyncSession,
    spec: LaneExecutionSpec,
    latest: LatestRun | None,
) -> tuple[LaneTickResult | None, DueLane | None]:
    """Resume or refuse prior-version work before current-version scheduling."""
    if latest is None or not latest.open or latest.definition_version == EXECUTOR_DEFINITION_VERSION:
        return None, None
    if not latest.definition_enabled:
        return (
            LaneTickResult(
                lane_id=spec.lane_id,
                state="failed",
                scheduled_for=latest.scheduled_for,
                run_id=latest.run_id,
                run_status=latest.status,
                detail=(
                    f"prior definition version {latest.definition_version!r} has nonterminal work but is "
                    "disabled; explicitly resume or cancel that durable run before current-version work"
                ),
            ),
            None,
        )
    blocked = _blocked_open_run_result(spec, latest, prior_version=True)
    if blocked is not None:
        return blocked, None
    definition = await load_job_definition(
        session,
        spec.definition_name,
        version=latest.definition_version,
    )
    if definition.handler != EXECUTOR_HANDLER_TOKEN:
        return (
            LaneTickResult(
                lane_id=spec.lane_id,
                state="failed",
                scheduled_for=latest.scheduled_for,
                run_id=latest.run_id,
                run_status=latest.status,
                detail=(
                    f"prior definition version {latest.definition_version!r} uses incompatible handler "
                    f"{definition.handler!r}; reconcile it before current-version work"
                ),
            ),
            None,
        )
    return (
        None,
        DueLane(
            spec=spec,
            definition=definition,
            scheduled_for=latest.scheduled_for,
            existing_run_id=latest.run_id,
            last_scheduled_for=latest.scheduled_for,
        ),
    )


@dataclass(slots=True)
class ProcessStartRelease:
    """OPT-IN: a new DEPLOYMENT releases each breaker-held lane once, by recording a real supersession.

    A breaker hold means "this code failed three buckets running; a human must look". A deploy IS the human
    having looked, so under `PLANTGEO_JOB_EXECUTOR_PROCESS_START_RELEASES_BREAKER=1` the lane gets exactly
    one bucket per deployment; if that fails, the streak is longer than before and the hold returns until
    the NEXT deployment. Off by default (owner decision 2026-09-18: the sensors release is an explicit CLI
    supersession until the fix has proven itself). Three bounds, in order of strength: only a run whose
    bucket lies strictly before this process's start bucket qualifies, so a bucket this process opened is
    never its own release; the ledger marker `claim_process_start_release` is one row per (deployment,
    lane), so a container restart under the same deployment re-releases nothing; `released` is the
    process-local memo that keeps a refused or failed attempt from being retried every tick.
    """

    started_at: datetime
    deployment: str
    released: set[uuid.UUID] = field(default_factory=set)

    @classmethod
    def from_environment(
        cls, *, now: datetime, environment: Mapping[str, str] | None = None
    ) -> ProcessStartRelease | None:
        source = os.environ if environment is None else environment
        if source.get(PROCESS_START_RELEASE_VARIABLE, "0").strip().lower() not in {"1", "true", "yes", "on"}:
            return None
        return cls(started_at=now, deployment=source.get(DEPLOYMENT_ID_VARIABLE, "").strip() or "local")

    def qualifies(self, spec: LaneExecutionSpec, latest: LatestRun) -> bool:
        """True for a held run whose BUCKET settled strictly before the bucket this process started in."""
        return latest.scheduled_for < scheduled_bucket(spec, self.started_at) and latest.run_id not in self.released

    def evidence(self, latest: LatestRun, verdict: CheckpointVerdict) -> str:
        return (
            f"released once by executor process start at {self.started_at.isoformat()} (deployment "
            f"{self.deployment}); the breaker held run {latest.run_id} after {verdict.consecutive_failures} "
            "consecutive failed bucket(s) settled before this process existed, and a fresh process earns one bucket"
        )


def _ledger_label() -> str:
    """Name the ledger for a receipt without ever echoing its credential; `unknown` when no DSN resolves."""
    from agri_data_service.execution.job_run_supersession import ledger_target  # noqa: PLC0415 - import cycle

    try:
        return ledger_target(settings.require_local_source_loader_database_url())
    except Exception:  # a label, never a gate: the recording itself proves the ledger answered
        return "unknown"


async def _release_by_process_start(  # noqa: PLR0913 - the held run, its verdict, the clock and the policy
    session: AsyncSession,
    spec: LaneExecutionSpec,
    latest: LatestRun,
    verdict: CheckpointVerdict,
    *,
    now: datetime,
    release: ProcessStartRelease,
) -> LatestRun | None:
    """Record this deployment's one supersession of a breaker-held run; `None` when it was not recorded.

    Marker first, supersession second, one commit: the marker's `ON CONFLICT DO NOTHING` refuses a second
    release under the same deployment before any supersession is written. Every refusal and every ledger
    fault rolls back, logs and returns `None` -- a planning tick must never abort because a release could
    not be recorded -- and `released` is only extended once the ledger has answered, so a transient fault
    is retried on a later tick rather than remembered as done.
    """
    from agri_data_service.execution.job_run_supersession import (  # noqa: PLC0415 - import cycle
        SupersessionRefusal,
        claim_process_start_release,
        supersede_failed_run,
    )

    try:
        claimed = await claim_process_start_release(
            session,
            lane_id=spec.lane_id,
            deployment=release.deployment,
            operator=PROCESS_START_RELEASE_OPERATOR,
            now=now,
            detail={
                "lane_id": spec.lane_id,
                "deployment": release.deployment,
                "run_id": str(latest.run_id),
                "process_started_at": release.started_at.isoformat(),
            },
        )
        if not claimed:
            await _rollback_planning_transaction(session)
            release.released.add(latest.run_id)
            logger.info(
                "plantgeo_job_executor_breaker_release_already_spent",
                lane_id=spec.lane_id,
                run_id=str(latest.run_id),
                deployment=release.deployment,
                detail="this deployment already released this lane once; the hold waits for an operator",
            )
            return None
        receipt = await supersede_failed_run(
            session,
            spec,
            latest.run_id,
            ledger=_ledger_label(),
            evidence=release.evidence(latest, verdict),
            operator=PROCESS_START_RELEASE_OPERATOR,
            now=now,
            apply=True,
        )
        if receipt.outcome not in {"recorded", "already_superseded"}:
            await _rollback_planning_transaction(session)
            return None
        await _commit_planning_transaction(session)
    except SupersessionRefusal as refusal:
        await _rollback_planning_transaction(session)
        release.released.add(latest.run_id)
        logger.warning(
            "plantgeo_job_executor_breaker_release_refused",
            lane_id=spec.lane_id,
            run_id=str(latest.run_id),
            reason=str(refusal),
        )
        return None
    except SQLAlchemyError as error:
        await _rollback_planning_transaction(session)
        logger.error(
            "plantgeo_job_executor_breaker_release_failed",
            lane_id=spec.lane_id,
            run_id=str(latest.run_id),
            error_type=type(error).__name__,
        )
        return None
    release.released.add(latest.run_id)
    logger.error(
        "plantgeo_job_executor_breaker_released_by_process_start",
        lane_id=spec.lane_id,
        run_id=str(latest.run_id),
        consecutive_failures=verdict.consecutive_failures,
        deployment=release.deployment,
        detail="one bucket is granted; a failure now re-holds the lane for an operator or the next process start",
    )
    return replace(latest, superseded_by_operator=True)


async def _plan_active_lanes(  # noqa: PLR0912 - one branch per lane state the planner can find
    session: AsyncSession,
    activation: ActivationConfig,
    now: datetime,
    *,
    breaker_release: ProcessStartRelease | None = None,
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
            if not spec.executable:
                blockers.append("no executable command exists in this runtime")
            results.append(
                LaneTickResult(
                    lane_id=spec.lane_id,
                    state=state,
                    scheduled_for=current_bucket,
                    command=spec.command,
                    blockers=tuple(blockers),
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
        latest = await read_lane_checkpoint(session, spec)
        prior_result, prior_due = await _plan_prior_version_run(session, spec, latest)
        await _rollback_planning_transaction(session)
        if prior_result is not None:
            results.append(prior_result)
            continue
        if prior_due is not None:
            due.append(prior_due)
            continue
        if definition is None:
            results.append(
                LaneTickResult(
                    lane_id=spec.lane_id,
                    state="paused",
                    detail="the lane-wide job_definition pause or current-version pause is active",
                )
            )
            continue
        current_bucket = scheduled_bucket(spec, now)
        if latest is not None and latest.status in SETTLED_WITHOUT_SUCCESS:
            verdict = judge_failed_checkpoint(spec, latest, now)
            if (
                not verdict.released
                and verdict.release == "operator"
                and not latest.superseded_by_operator
                and breaker_release is not None
                and breaker_release.qualifies(spec, latest)
            ):
                released = await _release_by_process_start(
                    session, spec, latest, verdict, now=now, release=breaker_release
                )
                if released is not None:
                    latest = released
                    verdict = judge_failed_checkpoint(spec, latest, now)
            if not verdict.released:
                results.append(_held_checkpoint_result(spec, latest, verdict))
                continue
            due.append(
                DueLane(
                    spec=spec,
                    definition=definition,
                    scheduled_for=verdict.next_bucket,
                    existing_run_id=None,
                    last_scheduled_for=latest.scheduled_for,
                    superseded_run_id=latest.run_id,
                    supersession=verdict.release,
                )
            )
            continue
        if latest is not None and latest.open:
            blocked = _blocked_open_run_result(spec, latest, prior_version=False)
            if blocked is not None:
                results.append(blocked)
                continue
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
        if latest is not None and latest.scheduled_for >= current_bucket:
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
        bucket = next_scheduled_bucket(
            spec,
            now,
            None if latest is None else latest.scheduled_for,
        )
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


def repair_lane_spec(spec: LaneExecutionSpec) -> LaneExecutionSpec:
    """Derive the definition a lane's bounded gap repairs run under: the same command and timeout, its own ledger name.

    A SEPARATE definition, not a second work item on the cadence definition: `select_latest_run.sql` reads a
    lane's newest terminal run as its cadence checkpoint, so a repair run filed under the forward definition
    would settle a bucket the forward writer never ran. Backlog class, so `fair_due_order` never lets a repair
    take the incremental turn its owning lane's hourly bucket needs.
    """
    return replace(
        spec,
        lane_id=f"{spec.lane_id}{REPAIR_LANE_SUFFIX}",
        conflicts_with=(),
        work_class="backlog",
        schedule=None,
        catch_up_policy="coalesce_latest",
        selection_policy="operator-authored bounded repair turns; the writer selects the days",
        description=(
            f"Bounded gap repair for {spec.lane_id}: the same source-direct writer with a --max-days bound, "
            "authored from Parquet coverage by `agri-service ops jobs-plan-gap-repair`, never scheduled."
        ),
    )


async def ensure_lane_definition(session: AsyncSession, spec: LaneExecutionSpec) -> JobDefinitionRecord | None:
    """Register the lane's durable definition if absent and return it, or `None` while its pause switch is set."""
    return await _load_or_register_definition(session, spec)


async def _plan_repair_runs(
    session: AsyncSession,
    activation: ActivationConfig,
    *,
    forward_due: AbstractSet[str],
) -> tuple[list[LaneTickResult], list[DueLane]]:
    """Drive the repair runs an operator already authored. Never authors one, registers nothing, lists nothing.

    A repair definition that does not exist in the ledger costs one probe and is skipped: the tick must not
    create definitions for work nobody asked for. A repair run that settled is history; the authoring verb
    opens the next one under a new logical key. `forward_due` keeps a lane's forward bucket and its repair
    out of the same tick, so one turn never doubles that lane's egress.
    """
    results: list[LaneTickResult] = []
    due: list[DueLane] = []
    for lane_id in sorted(REPAIR_LANE_IDS):
        spec = LANE_SPECS.get(lane_id)
        if spec is None or not activation.is_active(lane_id):
            continue
        repair = repair_lane_spec(spec)
        state = await _definition_state(session, repair)
        if state is None:
            await _rollback_planning_transaction(session)
            continue
        definition = await _load_or_register_definition(session, repair)
        latest = await read_lane_checkpoint(session, repair)
        await _rollback_planning_transaction(session)
        if definition is None:
            results.append(
                LaneTickResult(
                    lane_id=repair.lane_id,
                    state="paused",
                    detail="the repair definition's pause switch is set",
                )
            )
            continue
        if latest is None or not latest.open:
            continue
        if lane_id in forward_due:
            results.append(
                LaneTickResult(
                    lane_id=repair.lane_id,
                    state="deferred_fairness",
                    scheduled_for=latest.scheduled_for,
                    run_id=latest.run_id,
                    detail="the owning lane's forward bucket is due this tick; its repair waits for the next one",
                )
            )
            continue
        blocked = _blocked_open_run_result(repair, latest, prior_version=False)
        if blocked is not None:
            results.append(blocked)
            continue
        due.append(
            DueLane(
                spec=repair,
                definition=definition,
                scheduled_for=latest.scheduled_for,
                existing_run_id=latest.run_id,
                last_scheduled_for=latest.scheduled_for,
            )
        )
    return results, due


@dataclass(slots=True)
class RepairAuthoringClock:
    """When the leader last authored repairs, and how long it waits before reading coverage again.

    Coverage is one pointer GET per lane, so it is read on this interval and never per tick. A failed
    authoring pass advances the clock too: a broken object store must not be probed every 30 seconds.
    """

    interval_seconds: float
    last_authored: float | None = None
    #: Layer -> monotonic instant it was last authored for, in THIS process. A layer inside
    #: `rotation_seconds` is excluded from the next pass so two persistently unfillable layers cannot take
    #: the pass budget every interval and starve the rest (`deferred_by_rotation`). Process-held on purpose:
    #: a restart forgets the rotation, which costs at most one pass of the old ordering.
    recently_authored: dict[str, float] = field(default_factory=dict)
    rotation_seconds: float = DEFAULT_REPAIR_ROTATION_SECONDS

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> RepairAuthoringClock | None:
        source = os.environ if environment is None else environment
        raw = source.get(REPAIR_INTERVAL_VARIABLE, "").strip()
        if not raw:
            return cls(interval_seconds=DEFAULT_REPAIR_INTERVAL_SECONDS)
        try:
            interval = float(raw)
        except ValueError as error:
            raise ExecutorConfigurationError(f"{REPAIR_INTERVAL_VARIABLE} must be a number") from error
        if interval < 0:
            raise ExecutorConfigurationError(f"{REPAIR_INTERVAL_VARIABLE} must not be negative")
        return None if interval == 0 else cls(interval_seconds=interval)

    def due(self, monotonic_now: float) -> bool:
        return self.last_authored is None or monotonic_now - self.last_authored >= self.interval_seconds

    def mark(self, monotonic_now: float) -> None:
        self.last_authored = monotonic_now

    def excluded(self, monotonic_now: float) -> frozenset[str]:
        """Layers authored within the rotation window; the next pass looks past them."""
        return frozenset(
            layer for layer, when in self.recently_authored.items() if monotonic_now - when < self.rotation_seconds
        )

    def remember(self, layers: Iterable[str], monotonic_now: float) -> None:
        for layer in layers:
            self.recently_authored[layer] = monotonic_now


async def _author_due_repairs(
    session: AsyncSession,
    activation: ActivationConfig,
    *,
    now: datetime,
    clock: RepairAuthoringClock,
) -> dict[str, object] | None:
    """Author the bounded repair turns the measured gaps call for, once per interval, with no hand on the ledger.

    The self-healing half of gap repair: `gap_repair.py`'s verb does exactly this by hand, and after a stall
    nobody is at the keyboard. One coverage read (availability authority, never a listing), one plan, one
    committed pass; the tick then drives whatever was opened through `_plan_repair_runs` like any other run.
    """
    from agri_data_service.execution import gap_repair  # noqa: PLC0415 - import cycle
    from agri_data_service.execution.gap_repair_contract import RepairBudget  # noqa: PLC0415 - import cycle

    started = time.monotonic()
    clock.mark(started)
    try:
        coverage = await asyncio.to_thread(gap_repair.read_parquet_coverage, now=now)
        plan = gap_repair.plan_gap_repairs(
            coverage,
            activation=activation,
            now=now,
            budget=RepairBudget(),
            recently_authored=clock.excluded(started),
        )
        receipts = await gap_repair.author_gap_repairs(session, plan=plan, now=now, apply=True)
        await _commit_planning_transaction(session)
    except Exception as error:  # authoring is best-effort; the forward lanes never wait on it
        await _rollback_planning_transaction(session)
        logger.error("plantgeo_job_executor_repair_authoring_failed", error_type=type(error).__name__)
        return None
    clock.remember((receipt.layer for receipt in receipts), started)
    summary: dict[str, object] = {
        "authorized": [candidate.layer for candidate in plan.authorized],
        "verdicts": {candidate.layer: candidate.verdict for candidate in plan.candidates},
        "receipts": [receipt.to_dict() for receipt in receipts],
    }
    logger.info("plantgeo_job_executor_repairs_authored", **summary)
    return summary


async def run_executor_tick(  # noqa: PLR0913 - one operator-tunable knob of the tick per argument
    session: AsyncSession,
    *,
    activation: ActivationConfig,
    now: datetime,
    max_lanes_per_tick: int,
    stop: ShutdownSignal | None = None,
    breaker_release: ProcessStartRelease | None = None,
    repair_clock: RepairAuthoringClock | None = None,
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
        results, due = await _plan_active_lanes(session, activation, now, breaker_release=breaker_release)
        if repair_clock is not None and repair_clock.due(time.monotonic()):
            await _author_due_repairs(session, activation, now=now, clock=repair_clock)
        repair_results, repair_due = await _plan_repair_runs(
            session,
            activation,
            forward_due={candidate.spec.lane_id for candidate in due},
        )
        results.extend(repair_results)
        due.extend(repair_due)
        ordered = fair_due_order(due)
        selected = ordered[:max_lanes_per_tick]
        for index, candidate in enumerate(selected):
            if stop is not None and stop.requested:
                results.extend(_deferred_shutdown_result(deferred) for deferred in selected[index:])
                break
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
                await apply_statement_timeout(session)
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
            await _rollback_planning_transaction(session)
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


def _write_through(stream: object, chunk: bytes) -> None:
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        buffer.write(chunk)
        buffer.flush()
        return
    stream.write(chunk.decode("utf-8", errors="replace"))  # type: ignore[attr-defined]
    stream.flush()  # type: ignore[attr-defined]


def _default_stderr_sink(chunk: bytes) -> None:
    """Re-emit one chunk of a child's stderr on this process's stderr, so the Railway log stream still carries it."""
    _write_through(sys.stderr, chunk)


def _default_stdout_sink(chunk: bytes) -> None:
    """Re-emit one chunk of a child's stdout on this process's stdout: the log stream keeps the JSON report."""
    _write_through(sys.stdout, chunk)


class CommandOutputTail:
    """Tee one of a child's output streams through to this process while keeping only its bounded TAIL.

    stderr: before this the child inherited it outright, its traceback reached the log stream and nothing
    else, and `agri.job_attempt.last_error_summary` read only `command exited with status 1`. The tail is
    what `run_scheduled_command` folds into every failure reason; `jobs.lease.fail_work_item` then redacts
    and clamps it like any other summary, so a secret printed by a child still never reaches the ledger.

    stdout: the one terminal JSON report a writer prints last is parsed out of the tail at exit 0, so an
    `outcome=incomplete` turn is persisted rather than lost with the stream.
    """

    def __init__(
        self,
        *,
        limit: int = COMMAND_STDERR_TAIL_BYTES,
        sink: Callable[[bytes], None] | None = None,
    ) -> None:
        self._limit = limit
        self._sink = _default_stderr_sink if sink is None else sink
        self._tail = bytearray()
        self.bytes_seen = 0

    @property
    def tail(self) -> bytes:
        """The bytes kept, at most `limit` of them and always the newest."""
        return bytes(self._tail)

    @property
    def truncated(self) -> bool:
        """True when the child wrote more than the tail holds, so the summary is the END of its output."""
        return self.bytes_seen > len(self._tail)

    def feed(self, chunk: bytes) -> None:
        """Forward one chunk to the sink and fold it into the bounded tail."""
        if not chunk:
            return
        self.bytes_seen += len(chunk)
        self._sink(chunk)
        self._tail.extend(chunk)
        if len(self._tail) > self._limit:
            del self._tail[: len(self._tail) - self._limit]

    def summary(self, *, max_chars: int = COMMAND_STDERR_SUMMARY_CHARS) -> str | None:
        """One line: the tail's non-blank lines joined with ` | `, cut from the FRONT so the last line survives."""
        text = self._tail.decode("utf-8", errors="replace")
        lines = (" ".join(line.split()) for line in text.splitlines())
        joined = " | ".join(line for line in lines if line)
        if not joined:
            return None
        if len(joined) <= max_chars:
            return joined
        return "..." + joined[-(max_chars - 3) :]

    def metrics(self) -> dict[str, object]:
        """Counts only, never content: `metrics` is stored unredacted, so the tail itself goes through `reason`."""
        return {"stderr_bytes": self.bytes_seen, "stderr_truncated": self.truncated}


#: The stderr-flavoured name the first tests were written against; one class serves both streams.
CommandStderrTail = CommandOutputTail


async def _drain_stream(stream: asyncio.StreamReader, tail: CommandOutputTail) -> None:
    """Read one child stream to EOF; running concurrently keeps a chatty child from blocking on a full pipe."""
    while True:
        chunk = await stream.read(COMMAND_STDERR_READ_BYTES)
        if not chunk:
            return
        tail.feed(chunk)


async def _drain_both(
    process: asyncio.subprocess.Process, *, stdout: CommandOutputTail, stderr: CommandOutputTail
) -> None:
    if process.stdout is None or process.stderr is None:  # pragma: no cover - PIPE always yields readers
        raise RuntimeError("the child's output pipes were not created")
    await asyncio.gather(_drain_stream(process.stdout, stdout), _drain_stream(process.stderr, stderr))


async def _finish_drain(drain: asyncio.Task[None]) -> None:
    """Wait a bounded moment for EOF after exit or kill; a grandchild holding the pipe must not hold the attempt."""
    try:
        await asyncio.wait_for(asyncio.shield(drain), timeout=COMMAND_STDERR_DRAIN_SECONDS)
    except TimeoutError:
        drain.cancel()
        with suppress(asyncio.CancelledError):
            await drain
    except Exception:  # a broken pipe reader must not mask the child's own exit status
        logger.exception("plantgeo_job_executor_stderr_drain_failed")


def _command_failure_reason(headline: str, tail: CommandOutputTail) -> str:
    """Attach the bounded stderr tail to a failure headline; the ledger's own clamp bounds the whole."""
    summary = tail.summary()
    if summary is None:
        return f"{headline}; stderr: nothing captured"
    marker = " tail" if tail.truncated else ""
    return f"{headline}; stderr{marker}: {summary}"


def _resolve_command(spec: LaneExecutionSpec, invocation: JobInvocation) -> tuple[str, ...]:
    """Return the exact argv this work item runs: the lane's own command, plus a repair's bounded knobs.

    A repair item stores its REQUEST, never a command: the argv is rebuilt here from the registered spec and
    a payload `RepairRequest.from_payload` has already refused to accept out of bounds, so a stored payload
    cannot smuggle an argument the writer's contract does not expose.
    """
    if spec.command is None:
        raise RepairRequestError(f"lane {spec.lane_id!r} has no executor command")
    if invocation.kind == EXECUTOR_WORK_ITEM_KIND:
        return spec.command
    request = RepairRequest.from_payload(invocation.payload)
    if request.lane_id != spec.lane_id:
        raise RepairRequestError(f"repair request names lane {request.lane_id!r}, work item names {spec.lane_id!r}")
    return (*spec.command, *request.command_arguments())


@job_handler(EXECUTOR_HANDLER_TOKEN)
async def run_scheduled_command(  # noqa: PLR0911, PLR0912 - each terminal state maps to a ledger outcome
    invocation: JobInvocation,
) -> JobHandlerOutcome:
    """Execute one registry-bound command under the outer work item's fence."""
    if invocation.kind not in EXECUTOR_WORK_ITEM_KINDS:
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
    try:
        command = _resolve_command(spec, invocation)
    except RepairRequestError as error:
        return JobHandlerOutcome.failed("invalid_repair_request", f"lane {lane_id!r}: {error}")

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

    tail = CommandOutputTail()
    stdout = CommandOutputTail(limit=COMMAND_STDOUT_TAIL_BYTES, sink=_default_stdout_sink)
    # Both streams are piped and teed back through this process chunk by chunk, so the Railway log stream
    # carries exactly what it did before. stderr's tail is folded into the failure reason; stdout's tail is
    # where the writer's one terminal JSON report is parsed from, whatever the exit status.
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    drain = asyncio.create_task(_drain_both(process, stdout=stdout, stderr=tail))
    started = time.monotonic()
    try:
        monitor_state, return_code = await _monitor_subprocess(process, invocation, timeout=timeout)
    finally:
        await _finish_drain(drain)
    elapsed = round(time.monotonic() - started, 3)
    # Keyed by the DEFINITION that ran, not the owning lane: a `--max-days 5` repair turn is legitimately
    # partial and must not count against the hourly lane's incomplete-bucket streak.
    report_key = lane_id if invocation.kind == EXECUTOR_WORK_ITEM_KIND else f"{lane_id}{REPAIR_LANE_SUFFIX}"
    turn_report = record_turn_report(report_key, parse_terminal_report(stdout.tail))
    metrics: dict[str, object] = {
        "elapsed_seconds": elapsed,
        **tail.metrics(),
        "days_unwritten": None if turn_report is None else turn_report.days_unwritten,
    }
    if monitor_state == "shutdown":
        return JobHandlerOutcome.yielded(
            cursor=invocation.cursor,
            progress_fraction=invocation.progress_fraction,
            reason=f"lane {lane_id!r} stopped for service shutdown before command completion",
            metrics=metrics,
        )
    if monitor_state == "fence_lost":
        return JobHandlerOutcome.failed(
            "executor_lease_lost",
            _command_failure_reason(f"lane {lane_id!r} lost its fenced lease while the command was running", tail),
            metrics=metrics,
        )
    if monitor_state == "timeout":
        return JobHandlerOutcome.failed(
            "scheduled_command_timeout",
            _command_failure_reason(f"lane {lane_id!r} exceeded its {int(timeout)} second command budget", tail),
            metrics=metrics,
        )
    if return_code is None:  # pragma: no cover - exited always carries Process.wait's integer
        return JobHandlerOutcome.failed("scheduled_command_exit", f"lane {lane_id!r} returned no exit status")
    if return_code != 0:
        return JobHandlerOutcome.failed(
            "scheduled_command_exit",
            _command_failure_reason(f"lane {lane_id!r} command exited with status {return_code}", tail),
            metrics={**metrics, "exit_code": return_code},
        )
    cursor = {
        "state": "completed",
        "scheduled_for": scheduled_for if isinstance(scheduled_for, str) else invocation.shard_key,
        "completed_at": datetime.now(UTC).isoformat(),
        # The checkpoint row is where an exit-0-but-incomplete turn survives the log stream's retention.
        "turn_report": None if turn_report is None else turn_report.to_dict(),
    }
    return JobHandlerOutcome.completed(
        cursor=cursor,
        metrics={**metrics, "exit_code": return_code},
    )


def executor_inventory(activation: ActivationConfig) -> dict[str, object]:
    return {
        "event": "plantgeo_job_executor_inventory",
        "mode": "active" if activation.active_lanes else "shadow",
        "activation_variables": [ACTIVE_LANES_VARIABLE],
        "lanes": [spec.inventory_row(active=activation.is_active(spec.lane_id)) for spec in LANE_SPECS.values()],
    }


async def _wait_for_shutdown(stop: ShutdownSignal, delay_seconds: float) -> bool:
    """Wait for either the next service tick or an event-backed shutdown request."""
    if stop.requested:
        return True
    try:
        await asyncio.wait_for(stop.wait_requested(), timeout=delay_seconds)
    except TimeoutError:
        return False
    return True


def announce_operator_actions(summary: ExecutorTickSummary, announced: set[tuple[str, str | None]]) -> None:
    """Log each held lane's release command ONCE per process while it holds, and once more when it clears.

    The tick already printed the same command inside `blockers` every thirty seconds for a week with nothing
    consuming it; a flood is as invisible as silence. One `error`-severity event per held run, at top level
    with the exact verb to run, is what a log-based alert or a human skim can actually see. `announced` is
    the caller's per-process memory; a restart re-announces, which is the right side to err on.
    """
    if not summary.leader:
        # A follower sees no lanes at all; treating that as "cleared" would re-announce on every leadership flip.
        return
    current = {
        (action.lane_id, None if action.run_id is None else str(action.run_id)): action
        for action in summary.operator_actions
    }
    for key, action in current.items():
        if key in announced:
            continue
        announced.add(key)
        logger.error(
            "plantgeo_job_executor_operator_action_required",
            lane_id=action.lane_id,
            run_id=key[1],
            command=action.command,
            detail="the clock no longer releases this lane; nothing runs on it until this command is recorded",
        )
    for key in [key for key in announced if key not in current]:
        announced.discard(key)
        logger.info("plantgeo_job_executor_operator_action_cleared", lane_id=key[0], run_id=key[1])


async def _service_loop(
    *,
    activation: ActivationConfig,
    poll_seconds: float,
    max_lanes_per_tick: int,
    once: bool,
) -> int:
    failures = 0
    announced: set[tuple[str, str | None]] = set()
    breaker_release = ProcessStartRelease.from_environment(now=datetime.now(UTC))
    repair_clock = RepairAuthoringClock.from_environment()
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
                        breaker_release=breaker_release,
                        repair_clock=repair_clock,
                    )
                click.echo(json.dumps(summary.to_dict(), sort_keys=True))
                announce_operator_actions(summary, announced)
                for lane in summary.incomplete_lanes:
                    if lane.turn_report is not None:
                        logger.warning(
                            "plantgeo_job_executor_lane_incomplete",
                            lane_id=lane.lane_id,
                            days_unwritten=lane.turn_report.days_unwritten,
                            consecutive_incomplete_buckets=lane.turn_report.consecutive_incomplete_buckets,
                            unwritten=[dict(entry) for entry in lane.turn_report.unwritten],
                        )
                if summary.failed:
                    logger.error(
                        "plantgeo_job_executor_tick_unhealthy",
                        failing_lanes=[lane.lane_id for lane in summary.lanes if lane.state == "failed"],
                        incomplete_lanes=[lane.lane_id for lane in summary.incomplete_lanes],
                        operator_actions=[action.command for action in summary.operator_actions],
                    )
                else:
                    logger.info(
                        "plantgeo_job_executor_tick_healthy",
                        leader=summary.leader,
                        lane_count=len(summary.lanes),
                        incomplete_lanes=[lane.lane_id for lane in summary.incomplete_lanes],
                    )
                failures = 0
                if once:
                    return 1 if summary.failed else 0
                if await _wait_for_shutdown(stop, poll_seconds):
                    break
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
                if await _wait_for_shutdown(stop, delay):
                    break
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
    "CLOCK_RELEASE_STREAK_LIMIT",
    "COMMAND_STDERR_SUMMARY_CHARS",
    "COMMAND_STDERR_TAIL_BYTES",
    "EXECUTOR_WORK_ITEM_KINDS",
    "FAILURE_STREAK_PROBE_LIMIT",
    "LANE_SPECS",
    "OPERATOR_SUPERSESSION_BLOCKER_PREFIX",
    "RUN_SUPERSESSION_FINGERPRINT_PREFIX",
    "RUN_SUPERSESSION_INCIDENT_TYPE",
    "SETTLED_WITHOUT_SUCCESS",
    "SUPERSEDE_RUN_COMMAND",
    "ActivationConfig",
    "CheckpointVerdict",
    "CommandOutputTail",
    "CommandStderrTail",
    "DueLane",
    "ExecutorConfigurationError",
    "ExecutorLeaderUnlockError",
    "ExecutorTickSummary",
    "LaneExecutionSpec",
    "LaneTickResult",
    "LatestRun",
    "OperatorAction",
    "ProcessStartRelease",
    "RepairAuthoringClock",
    "TurnReport",
    "announce_operator_actions",
    "bucket_after",
    "ensure_lane_definition",
    "executor_inventory",
    "fair_due_order",
    "jobs_executor",
    "judge_failed_checkpoint",
    "next_scheduled_bucket",
    "parse_activation",
    "parse_terminal_report",
    "read_lane_checkpoint",
    "record_turn_report",
    "repair_lane_spec",
    "run_executor_tick",
    "run_scheduled_command",
    "scheduled_bucket",
    "summarize_turn_report",
    "supersession_command",
]
