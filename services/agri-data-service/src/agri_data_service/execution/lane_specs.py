"""The code-owned declarative lane table: one `LaneExecutionSpec` per executor lane, plus lane activation.

Split out of `job_executor_service.py` (soft size ceiling, `federation.md` §3) so the scheduler's
declarative data lives apart from the scheduler's planning/execution logic. See execution/AGENTS.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

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
    VEGETATION_NDVI_PROMOTION_LANE_ID,
    WATER_GAUGES_DIRECT_LANE_ID,
    WATERSHEDS_DIRECT_LANE_ID,
    WEATHER_OBSERVATIONS_DIRECT_LANE_ID,
)
from agri_data_service.jobs import JobDefinitionSpec, RetryPolicy
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
    from collections.abc import Mapping

EXECUTOR_DEFINITION_PREFIX: Final = "plantgeo.executor."
EXECUTOR_DEFINITION_VERSION: Final = "2"
EXECUTOR_HANDLER_TOKEN: Final = "plantgeo.executor.command.v1"
EXECUTOR_WORK_ITEM_KIND: Final = "scheduled-command"
EXECUTOR_REQUESTED_BY: Final = "agri-service ops jobs-executor"
ACTIVE_LANES_VARIABLE: Final = "PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES"
#: Margin added to a direct writer's own time budget so the ledger's command timeout always outlives it.
COMMAND_CLEANUP_MARGIN_SECONDS: Final = 300


class ExecutorConfigurationError(ValueError):
    """Raised when executor lane configuration is invalid."""


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
#: How many of a lane's newest terminal runs the checkpoint query inspects for its failure streak. One
#: bounded backward index probe; no policy below ever needs a longer streak than this.
FAILURE_STREAK_PROBE_LIMIT: Final = 3

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


#: How many days behind TODAY the vegetation lane's newest servable day may be before the NDVI
#: promotion turn calls its ceiling stale. A SAFETY BOUND, stated as a literal day count in the unit
#: it is enforced in, and deliberately derived from nothing.
#:
#: It used to be `VEGETATION_PROMOTION_STALE_CEILING_LAG_ALLOWANCES = 2`, multiplied back by the
#: lane's registered `publication_lag_days` (STYLE-REVIEW-W11 S1). Two defects, one of them live:
#:
#: 1. The number a reader met was `2` and the bound the code enforced was `3 x lag = 21` days, because
#:    counting past the declared-lag day spends one whole lag before the counter starts. A constant
#:    whose real value is computed two files away is what `engineering-principles.md` section 2
#:    forbids.
#: 2. `publication_lag_days` is documented in `pipeline/parquet/lane_registry.py`'s vegetation
#:    `floor_basis` as a MEASURED MEDIAN -- a number this repo expects to RE-MEASURE. Re-measuring it
#:    to 10 silently moved this safety bound from 21 days to 30, with a GREEN suite, because the
#:    boundary tests were themselves written as `lag * (allowances + 1)` and tracked the change
#:    instead of catching it. That is the freshness yardstick again: the test compared the code to
#:    itself (`.omc` memory `plantgeo-freshness-yardstick-is-tautological`).
#:
#: So the two questions are now separate constants with separate owners. "How far behind is NORMAL"
#: is `publication_lag_days`, owned by the registry, re-measurable at will, and reported but never
#: multiplied. "How far behind is DEAD" is this literal, owned by this lane, and moved only by
#: editing this line. 21 is the value the bound has had since STYLE-REVIEW-W9 B1 closed -- restated,
#: not re-derived: a healthy lane already sits about 7 days behind today, a routine Oct-Mar PNW
#: overcast fortnight puts it ~16 days behind with nothing wrong, and 21 clears that observed
#: worst case while still catching a writer that stopped.
#:
#: `tests/execution/test_vegetation_partition_promotion.py::test_the_stale_ceiling_bound_is_a_day_count_this_lane_owns`
#: pins this literal and pins the bound the code enforces against it, so a re-measured lag cannot move
#: the bound and cannot pass the suite unnoticed.
VEGETATION_PROMOTION_STALE_CEILING_DAYS: Final = 21

#: How many whole declared-lag medians of slack `VEGETATION_PROMOTION_STALE_CEILING_DAYS` must leave
#: PAST the declared-lag day to still be believable. A FLOOR checked by
#: `stale_ceiling_days_clearing_declared_lag`, which can only REFUSE the bound, never move it -- the
#: one relationship between the two numbers that survives, and it is stated in the direction that
#: cannot silently rescale anything.
#:
#: Two, because the registry calls the 7-day lag a median of a heavy-tailed distribution and W9 B1
#: proved one median of slack is not enough: a 16-day inter-scene gap is routine and sits one median
#: past the declared-lag day. At the registered lag of 7 the floor is 21 and the bound is exactly 21.
VEGETATION_PROMOTION_STALE_CEILING_MINIMUM_SLACK_LAGS: Final = 2


def vegetation_promotion_declared_lag_days() -> int:
    """Return the vegetation lane's REGISTERED `publication_lag_days`, read at call time.

    Named for the registry field it reads and nothing more. It is not the lane's cadence: `vegetation`
    is `daily_series` and `layer-lanes.md` (96831d8b) section 1a allows a cadence above one day only
    to a `release_series`, so `LANE_REGISTRY["vegetation"].cadence_days` is 1 and is deliberately not
    consulted here. It is also not a provider observation: nothing in this call opens a socket -- see
    `execution/AGENTS.md` section Lane activation for what a genuine provider-derived frontier would
    cost and why it is owed to `pipeline/direct/vegetation/source.py` rather than taken here.

    Read from `LANE_REGISTRY`, never copied: the same rule
    `pipeline/direct/vegetation/products.py` states for the floor and the lag it deliberately does
    not duplicate. This number is REPORTED by the promotion turn and is an input to the floor check
    below; it is NOT a factor of the staleness bound, which is
    `VEGETATION_PROMOTION_STALE_CEILING_DAYS` and moves only when that line is edited. Refuses a
    non-positive lag, which is not a lag at all and would let the floor check pass on nothing.
    """
    lag_days = _registration("vegetation")[0]
    if lag_days < 1:
        raise ValueError(
            f"lane 'vegetation' registers publication_lag_days={lag_days}; the NDVI promotion turn "
            f"reports its ceiling's distance past the declared-lag day and cannot do that for a "
            f"non-positive lag"
        )
    return lag_days


def stale_ceiling_days_clearing_declared_lag(*, stale_ceiling_days: int, declared_lag_days: int) -> int:
    """Return `stale_ceiling_days` unchanged, or REFUSE it as too tight for this declared lag.

    The only coupling left between the safety bound and the re-measurable median, and it is
    one-directional by construction: this function returns its first argument or raises. It can never
    compute a bound, so no re-measurement of `declared_lag_days` can move one.

    The floor is `(1 + VEGETATION_PROMOTION_STALE_CEILING_MINIMUM_SLACK_LAGS) * declared_lag_days`.
    The `1 +` is the lag a HEALTHY lane already sits behind today and is written out rather than
    folded into the multiplier -- folding it in is exactly how `= 2` came to mean `3 x lag`.

    Re-measuring the lag DOWNWARD always passes: a bound that is relatively more generous than needed
    detects a dead writer later, which is the safe direction, and the cost is visible in the report's
    `ceiling_declared_lag_slack_days`. Re-measuring it UPWARD far enough that 21 days would start
    refusing lanes that are merely slow raises here instead, which is the point: the bound and the lag
    are then genuinely in conflict and a person has to decide which one is wrong. In a scheduled turn
    that surfaces as `main()`'s `failed` report carrying this message, not as a silent re-scaling.
    """
    minimum_days = (1 + VEGETATION_PROMOTION_STALE_CEILING_MINIMUM_SLACK_LAGS) * declared_lag_days
    if stale_ceiling_days < minimum_days:
        raise ValueError(
            f"the NDVI promotion stale-ceiling bound is {stale_ceiling_days} days behind today, but lane "
            f"'vegetation' now declares publication_lag_days={declared_lag_days}, whose heavy tail needs at "
            f"least {minimum_days} ({VEGETATION_PROMOTION_STALE_CEILING_MINIMUM_SLACK_LAGS} whole declared "
            f"lags of slack past the declared-lag day); raise VEGETATION_PROMOTION_STALE_CEILING_DAYS "
            f"deliberately or explain why this lag may sit inside the bound"
        )
    return stale_ceiling_days


def vegetation_promotion_stale_ceiling_days() -> int:
    """Return the staleness bound in DAYS BEHIND TODAY, after checking it against the registered lag.

    The one accessor the promotion turn calls. What it returns is
    `VEGETATION_PROMOTION_STALE_CEILING_DAYS` and nothing else -- the registry is consulted only to
    REFUSE a bound that has become too tight for a re-measured lag, never to compute one.
    """
    return stale_ceiling_days_clearing_declared_lag(
        stale_ceiling_days=VEGETATION_PROMOTION_STALE_CEILING_DAYS,
        declared_lag_days=vegetation_promotion_declared_lag_days(),
    )


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
        VEGETATION_NDVI_PROMOTION_LANE_ID,
        # REGISTERED, NOT ACTIVE: absent from the deployed PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES
        # allow-list on purpose (execution/AGENTS.md §Lane activation) -- the SAME shadow-by-default
        # mechanism every other lane here already registers under, not a second one. Phase offset
        # trails the vegetation direct writer's own `5 * * * *` so a promotion turn always reads a
        # settled day the forward writer already had a chance to publish first.
        command=("python", "-m", "agri_data_service.execution.vegetation_partition_promotion"),
        disposition="source-specific",
        phase_offset_seconds=1500,
        schedule="25 * * * *",
        publication_lag_days=_registration("vegetation")[0],
        publication_cadence_days=_registration("vegetation")[1],
        publication_lag_source="pipeline/parquet/lane_registry.py vegetation contract",
        selection_policy="newest settled days first, one content-SHA-scoped partition promotion per day",
        timeout_seconds=900,
        description=(
            "Governed-plane promotion for the vegetation NDVI direct-writer stream, keyed per "
            "`layer=vegetation/kind=observed/year=/month=/day=` partition by that partition's own "
            "content SHA (owner decision 2026-09-18), matching the availability index's "
            "`generation=<content-sha>` convention. Hands that partition's own cell values to "
            "`execution/vegetation_ndvi_plane.register_governed_partition_plane`, which touches "
            "`agri.*` only and reads no source table. An unchanged partition re-run is a no-op "
            "against its own promotion receipt; a changed partition re-promotes only itself."
        ),
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
