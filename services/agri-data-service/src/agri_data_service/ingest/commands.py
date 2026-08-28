"""The `ingest-*` and `jobs-*` CLI verbs: each prints one JSON summary per job and states its own exit rule."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import click
import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from agri_data_service.db.engine import ingest_session

# Importing this module is also what REGISTERS the archive-walk handler: `@job_handler` binds
# `ingest.archive_walk` into `JOB_HANDLERS` at import time, and `jobs-run` resolves a stored
# `job_definition.handler` token through that registry. Every name below is used, so no tidy-up can drop
# the import by accident, but the side effect is the reason a slice can run at all.
from agri_data_service.ingest.archive_walk import (
    ArchiveWalkContext,
    archive_lane_definition_name,
    archive_source,
    archive_walk_context,
    plan_archive_lane,
)
from agri_data_service.ingest.backfill import (
    DEFAULT_HISTORY_CHUNK,
    DEFAULT_HISTORY_YEARS,
    DEFAULT_REPAIR_BATCH_SIZE,
    GEOMETRY_REPAIR_SOURCE,
    BackfillPlan,
    merge_backfill_results,
    run_geometry_repair,
    run_source_backfill,
    subtract_years,
)
from agri_data_service.ingest.evacuation_zones import EVACUATION_ZONES_SOURCE, run_evacuation_zones_ingestion_job
from agri_data_service.ingest.firms import FIRMS_SOURCE, firms_archive_source, run_fire_ingestion_job
from agri_data_service.ingest.http import upstream_client
from agri_data_service.ingest.lanes import LaneSpecificationError, UnknownBackfillLaneError, resolve_lane
from agri_data_service.ingest.mtbs import MTBS_SOURCE, run_mtbs_ingestion_job
from agri_data_service.ingest.ndvi import NDVI_SOURCE, run_vegetation_ingestion_job
from agri_data_service.ingest.open_meteo import OPEN_METEO_SOURCE, run_weather_ingestion_job
from agri_data_service.ingest.realtime import RealtimePublisher
from agri_data_service.ingest.reconcile import ReconciliationError, plan_lane_gaps, reconcile_lane
from agri_data_service.ingest.results import any_job_failed, run_isolated_job
from agri_data_service.ingest.runner import run_all_ingestion_jobs
from agri_data_service.ingest.sensors import NWS_SENSOR_SOURCE, nws_sensor_source, run_sensor_ingestion_job
from agri_data_service.ingest.source import HistoryWindow
from agri_data_service.ingest.usdm import USDM_SOURCE, PostgresDroughtStore, run_drought_ingestion_job
from agri_data_service.ingest.usdm_history import (
    USDM_HISTORY_SOURCE,
    PostgresStoredReleaseIndex,
    default_history_plan,
    merge_week_outcomes,
    run_usdm_history_backfill,
)
from agri_data_service.ingest.usgs_nwis import (
    USGS_STREAMFLOW_SOURCE,
    run_water_ingestion_job,
    usgs_streamflow_archive_source,
)
from agri_data_service.ingest.validation import (
    ObservedDayScanTooLargeError,
    ValidationRowError,
    build_validation_report,
)
from agri_data_service.ingest.vegetation import COG_BOUNDS, build_vegetation_source
from agri_data_service.ingest.watersheds import WATERSHEDS_SOURCE, run_watersheds_ingestion_job
from agri_data_service.ingest.wfigs import WFIGS_SOURCE, run_fire_perimeters_ingestion_job
from agri_data_service.ingest.writer import MissingIngestionLayerError, bind_feature_writer
from agri_data_service.jobs import (
    JobDefinitionNotFoundError,
    JobLedgerRowError,
    JobRunError,
    JobSpecificationError,
    UnknownJobHandlerError,
    run_job_slice,
    shutdown_signal,
)
from agri_data_service.jobs.lease import apply_statement_timeout, fetch_rows, optional_column, required_column
from agri_data_service.pipeline.parquet.vegetation_forward import bind_vegetation_forward_writer

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from agri_data_service.ingest.lanes import BackfillLane
    from agri_data_service.ingest.reconcile import LaneGapPlan, LaneReconciliation
    from agri_data_service.ingest.results import IngestionJobResult
    from agri_data_service.ingest.source import IngestionSource
    from agri_data_service.ingest.usdm_history import HistoryBackfillPlan
    from agri_data_service.ingest.validation import ValidationReport
    from agri_data_service.ingest.writer import FeatureWriter
    from agri_data_service.jobs import JobSliceSummary

# Operational telemetry is bound to stderr, never stdout; see ingest/AGENTS.md "results.py, runner.py and commands.py".
logger = structlog.wrap_logger(structlog.PrintLogger(file=sys.stderr))

FAILED_JOB_EXIT_CODE = 1


def emit(results: Sequence[IngestionJobResult]) -> None:
    """Print one JSON line per job so a cron log records what each source did."""
    for result in results:
        click.echo(json.dumps(result.to_summary(), sort_keys=True))


def finish(context: click.Context, results: Sequence[IngestionJobResult]) -> None:
    """Print the summaries, then turn a failed job into a failed cron run."""
    emit(results)
    if any_job_failed(results):
        context.exit(FAILED_JOB_EXIT_CODE)


async def _run_with_feature_writer(
    source: str,
    build: Callable[[FeatureWriter], Awaitable[IngestionJobResult]],
) -> IngestionJobResult:
    """Open one ingest session and publisher, run a single feature-writing job, and isolate its failure."""
    async with ingest_session() as session, RealtimePublisher() as publisher:
        write_features = bind_feature_writer(session, publisher)
        return await run_isolated_job(source, lambda: build(write_features))


@click.command("ingest-firms")
@click.option("--bbox", default=None, help="Override INGEST_BBOX as west,south,east,north.")
@click.pass_context
def ingest_firms(context: click.Context, bbox: str | None) -> None:
    """Ingest bounded NASA FIRMS active-fire detections."""
    results = [
        asyncio.run(
            _run_with_feature_writer(
                FIRMS_SOURCE,
                lambda write_features: run_fire_ingestion_job(write_features, bbox=bbox),
            )
        )
    ]
    finish(context, results)


@click.command("ingest-streamflow")
@click.option("--bbox", default=None, help="Override INGEST_BBOX as west,south,east,north.")
@click.pass_context
def ingest_streamflow(context: click.Context, bbox: str | None) -> None:
    """Ingest bounded USGS NWIS streamflow gauges."""
    results = [
        asyncio.run(
            _run_with_feature_writer(
                USGS_STREAMFLOW_SOURCE,
                lambda write_features: run_water_ingestion_job(write_features, bbox=bbox),
            )
        )
    ]
    finish(context, results)


@click.command("ingest-weather")
@click.option("--bbox", default=None, help="Override INGEST_BBOX as west,south,east,north.")
@click.pass_context
def ingest_weather(context: click.Context, bbox: str | None) -> None:
    """Ingest current Open-Meteo conditions across the bounded sample grid."""
    results = [
        asyncio.run(
            _run_with_feature_writer(
                OPEN_METEO_SOURCE,
                lambda write_features: run_weather_ingestion_job(write_features, bbox=bbox),
            )
        )
    ]
    finish(context, results)


@click.command("ingest-fire-perimeters")
@click.option("--bbox", default=None, help="Override INGEST_BBOX as west,south,east,north.")
@click.pass_context
def ingest_fire_perimeters(context: click.Context, bbox: str | None) -> None:
    """Ingest bounded WFIGS interagency fire perimeters."""
    results = [
        asyncio.run(
            _run_with_feature_writer(
                WFIGS_SOURCE,
                lambda write_features: run_fire_perimeters_ingestion_job(write_features, bbox=bbox),
            )
        )
    ]
    finish(context, results)


@click.command("ingest-drought")
@click.option("--valid-date", default=None, help="An explicit USDM Tuesday; omit for the newest published release.")
@click.option("--replace", is_flag=True, default=False, help="Overwrite a release that is already stored.")
@click.pass_context
def ingest_drought(context: click.Context, valid_date: str | None, replace: bool) -> None:
    """Ingest the newest published US Drought Monitor release, then prune old releases."""
    results = [asyncio.run(_run_drought(valid_date=valid_date, replace=replace))]
    finish(context, results)


async def _run_drought(valid_date: str | None, replace: bool) -> IngestionJobResult:
    """Open one ingest session for the drought store and isolate the job's failure."""
    async with ingest_session() as session:
        store = PostgresDroughtStore(session)
        return await run_isolated_job(
            USDM_SOURCE,
            lambda: run_drought_ingestion_job(store, valid_date=valid_date, replace=replace),
        )


@click.command("ingest-ndvi")
@click.option("--bbox", default=None, help="Override INGEST_BBOX as west,south,east,north.")
@click.pass_context
def ingest_ndvi(context: click.Context, bbox: str | None) -> None:
    """Ingest Sentinel-2 L2A NDVI sampled onto the bounded warehouse grid."""
    results = [asyncio.run(_run_ndvi(bbox))]
    finish(context, results)


async def _run_ndvi(bbox: str | None) -> IngestionJobResult:
    """Keep raw persistence and governed Parquet publication on the same isolated job boundary."""
    async with ingest_session() as session, RealtimePublisher() as publisher:
        write_features = bind_feature_writer(session, publisher)
        forward_vegetation = bind_vegetation_forward_writer(session)
        return await run_isolated_job(
            NDVI_SOURCE,
            lambda: run_vegetation_ingestion_job(
                write_features,
                bbox=bbox,
                on_persisted=forward_vegetation,
            ),
        )


@click.command("ingest-sensors")
@click.option("--bbox", default=None, help="Override INGEST_BBOX as west,south,east,north.")
@click.pass_context
def ingest_sensors(context: click.Context, bbox: str | None) -> None:
    """Ingest the latest NOAA NWS ground-station observations inside the coverage box."""
    results = [
        asyncio.run(
            _run_with_feature_writer(
                NWS_SENSOR_SOURCE,
                lambda write_features: run_sensor_ingestion_job(write_features, bbox=bbox),
            )
        )
    ]
    finish(context, results)


@click.command("ingest-evacuation-zones")
@click.option("--bbox", default=None, help="Override INGEST_BBOX as west,south,east,north.")
@click.pass_context
def ingest_evacuation_zones(context: click.Context, bbox: str | None) -> None:
    """Ingest bounded Oregon OEM fire evacuation areas."""
    results = [
        asyncio.run(
            _run_with_feature_writer(
                EVACUATION_ZONES_SOURCE,
                lambda write_features: run_evacuation_zones_ingestion_job(write_features, bbox=bbox),
            )
        )
    ]
    finish(context, results)


@click.command("ingest-watersheds")
@click.option("--bbox", default=None, help="Override INGEST_BBOX as west,south,east,north.")
@click.pass_context
def ingest_watersheds(context: click.Context, bbox: str | None) -> None:
    """Ingest USGS WBD HUC12 watershed boundaries for the configured extent.

    Run once, then only when USGS republishes the WBD. Boundaries are a snapshot keyed by the HUC12
    code itself, so a re-run refreshes rows in place rather than accumulating versions -- there is no
    backfill verb for this source because there is no series to walk.
    """
    results = [
        asyncio.run(
            _run_with_feature_writer(
                WATERSHEDS_SOURCE,
                lambda write_features: run_watersheds_ingestion_job(write_features, bbox=bbox),
            )
        )
    ]
    finish(context, results)


@click.command("ingest-mtbs")
@click.option("--bbox", default=None, help="Override INGEST_BBOX as west,south,east,north.")
@click.option(
    "--release-year",
    "release_years",
    type=int,
    multiple=True,
    help="An MTBS fire year to ingest; repeatable. Omit for every year with an established release date.",
)
@click.pass_context
def ingest_mtbs(context: click.Context, bbox: str | None, release_years: tuple[int, ...]) -> None:
    """Ingest MTBS burned-area boundaries, one published release cohort at a time.

    Unlike the other verbs this one is not hourly-shaped: MTBS publishes quarterly and a fire year
    accretes over two to four years, so a run re-reads cohorts that almost never move. That is
    intentional and cheap -- the writer's diff rejects an unchanged payload and the geometry adapter
    confirms an unchanged shape -- and it is why `ingest-all` does not include this source.

    A fire year with no established release publication date fails the run rather than borrowing an
    ignition date, a run clock, or an assumed mapping lag for `observedAt`.
    """
    results = [
        asyncio.run(
            _run_with_feature_writer(
                MTBS_SOURCE,
                lambda write_features: run_mtbs_ingestion_job(
                    write_features,
                    bbox=bbox,
                    release_years=list(release_years) or None,
                ),
            )
        )
    ]
    finish(context, results)


def _build_backfillable_sources() -> Mapping[str, IngestionSource]:
    """The sources that declare a usable HistoryCapability, keyed by the token `--source` takes.

    Built on demand rather than at import: `nws_sensor_source` stamps its own `earliest` from the
    run clock, so a module-level instance would freeze the NWS retention window at import time.

    `nasa-firms-archive` is a second source token over the same producer, layer and identity contract
    as `nasa-firms`, not a second producer: FIRMS' archive is the same endpoint with a start date, and
    which product answers for a past day is read from the live availability table per chunk. It is a
    separate token so `ingest-backfill` cannot ask the forward job for a past window, nor this walk for
    the current one -- only `ingest-firms` reports a partial-constellation outage as a reason.

    `usgs-streamflow-archive` is the same arrangement over USGS NWIS, and for a sharper reason: it reads
    the DAILY-values service while `ingest-streamflow` reads the instantaneous one. The instantaneous
    feed retains roughly 120 days and answers an older window with a well-formed, empty response rather
    than an error, so a walk pointed at it would report years of successful empty chunks.
    """
    sources = (
        nws_sensor_source(),
        build_vegetation_source(),
        firms_archive_source(),
        usgs_streamflow_archive_source(),
    )
    return MappingProxyType({source.source_name: source for source in sources})


def _window_bound(value: str | None, fallback: datetime) -> datetime:
    """Parse a `--since`/`--until` bound as an ISO-8601 instant in UTC, refusing a naive local guess."""
    if value is None:
        return fallback
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as error:
        raise click.BadParameter(f"{value!r} is not an ISO-8601 date or instant") from error
    # A bare YYYY-MM-DD is read as UTC midnight; anything else must say which zone it means, because
    # the container's local zone would silently walk a different window than the operator asked for.
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


@click.command("ingest-backfill")
@click.option("--source", "source_name", required=True, help="Source token; see the error text for the list.")
@click.option("--since", default=None, help="Window start, ISO-8601. Defaults to --years back from --until.")
@click.option("--until", default=None, help="Window end, ISO-8601. Defaults to now.")
@click.option("--years", type=int, default=DEFAULT_HISTORY_YEARS, help="Years back when --since is omitted.")
@click.option("--chunk-days", type=int, default=DEFAULT_HISTORY_CHUNK.days, help="Days per fetched chunk.")
@click.option("--bbox", default=None, help="Override INGEST_BBOX as west,south,east,north.")
@click.pass_context
def ingest_backfill(  # noqa: PLR0913 - one parameter per click option, as interface/cli/commands.py's own verbs are
    context: click.Context,
    source_name: str,
    since: str | None,
    until: str | None,
    years: int,
    chunk_days: int,
    bbox: str | None,
) -> None:
    """Walk one source over a past date range in bounded chunks, through the forward write path."""
    sources = _build_backfillable_sources()
    source = sources.get(source_name)
    if source is None:
        raise click.BadParameter(f"--source must be one of: {', '.join(sorted(sources))}")
    if chunk_days <= 0:
        raise click.BadParameter("--chunk-days must be a positive number of days")
    end = _window_bound(until, datetime.now(UTC))
    start = _window_bound(since, subtract_years(end, years))
    if start >= end:
        raise click.BadParameter("--since must precede --until")
    plan = BackfillPlan(
        window=HistoryWindow(start=start, end=end),
        chunk=timedelta(days=chunk_days),
        bbox=bbox,
    )
    # The per-chunk results are emitted verbatim rather than folded: a resumed run starts from the
    # last chunk label, and a typed history refusal must stay visible as a skip, not average away.
    finish(context, asyncio.run(_run_backfill(source, plan)))


async def _run_backfill(source: IngestionSource, plan: BackfillPlan) -> list[IngestionJobResult]:
    """Open one ingest session and publisher for the whole walk, isolating the source's failure.

    Emits every chunk that completed and then the fold. A walk that dies half way still prints the
    chunks it wrote, which is what an operator resumes `--since` from; `run_isolated_job` turns the
    death itself into one failed summary and a non-zero exit rather than an unhandled traceback.
    """
    # One upstream client for the whole walk, not one per chunk: a raster source reads many byte
    # ranges per scene, so pooling across chunks is the difference between reusing a connection and
    # renegotiating TLS hundreds of times. A source that would rather own its own still may -- the
    # plan's client is an offer, and only a source that reads `request.client` takes it.
    async with (
        ingest_session() as session,
        RealtimePublisher() as publisher,
        upstream_client(COG_BOUNDS) as client,
    ):
        write_features = bind_feature_writer(session, publisher)
        walked = replace(plan, client=client)
        chunks: list[IngestionJobResult] = []

        async def walk() -> IngestionJobResult:
            chunks.extend(await run_source_backfill(source, write_features, walked))
            return merge_backfill_results(source.source_name, chunks)

        # Bound to a name first: a list display evaluates `*chunks` before the await, so inlining
        # the call would splat the list while it is still empty.
        merged = await run_isolated_job(source.source_name, walk)
        return [*chunks, merged]


@click.command("ingest-geometry-repair")
@click.option("--batch-size", type=int, default=DEFAULT_REPAIR_BATCH_SIZE, help="Features per repair transaction.")
@click.option("--max-features", type=int, default=None, help="Stop after this many rows; omit to repair all.")
@click.pass_context
def ingest_geometry_repair(context: click.Context, batch_size: int, max_features: int | None) -> None:
    """Version and link every geo.features row left without a geometry_id."""
    if batch_size <= 0:
        raise click.BadParameter("--batch-size must be positive")
    if max_features is not None and max_features <= 0:
        raise click.BadParameter("--max-features must be positive")
    results = [asyncio.run(_run_geometry_repair(batch_size, max_features))]
    finish(context, results)


async def _run_geometry_repair(batch_size: int, max_features: int | None) -> IngestionJobResult:
    """Open one ingest session for the repair pass and isolate its failure."""
    async with ingest_session() as session:
        return await run_isolated_job(
            GEOMETRY_REPAIR_SOURCE,
            lambda: run_geometry_repair(session, batch_size=batch_size, max_features=max_features),
        )


@click.command("ingest-drought-history")
@click.option("--years", type=int, default=DEFAULT_HISTORY_YEARS, help="Years of USDM release Tuesdays to walk.")
@click.option("--replace", is_flag=True, default=False, help="Rewrite a release week that is already stored.")
@click.pass_context
def ingest_drought_history(context: click.Context, years: int, replace: bool) -> None:
    """Walk the USDM archive week by week into geo.drought_areas, recording every unpublished week as a gap."""
    if years <= 0:
        raise click.BadParameter("--years must be positive")
    results = [asyncio.run(_run_drought_history(years, replace))]
    finish(context, results)


async def _run_drought_history(years: int, replace: bool) -> IngestionJobResult:
    """Open one ingest session for the archive walk and fold its per-week ledger into one summary."""
    async with ingest_session() as session:
        store = PostgresDroughtStore(session)
        stored = PostgresStoredReleaseIndex(session)
        plan = default_history_plan(years=years, replace=replace)
        return await run_isolated_job(
            USDM_HISTORY_SOURCE,
            lambda: _fold_drought_history(store, plan, stored),
        )


async def _fold_drought_history(
    store: PostgresDroughtStore,
    plan: HistoryBackfillPlan,
    stored: PostgresStoredReleaseIndex,
) -> IngestionJobResult:
    """Run the archive walk and fold its per-week outcomes, so the verb prints one summary like every other."""
    return merge_week_outcomes(await run_usdm_history_backfill(store, plan, stored))


@click.command("ingest-all")
@click.option("--bbox", default=None, help="Override INGEST_BBOX as west,south,east,north.")
@click.pass_context
def ingest_all(context: click.Context, bbox: str | None) -> None:
    """Run every ingestion source in turn and exit non-zero when any of them failed."""
    finish(context, asyncio.run(_run_all(bbox)))


async def _run_all(bbox: str | None) -> list[IngestionJobResult]:
    """Open one session and one publisher for the whole run, then execute the eight sources sequentially."""
    async with ingest_session() as session, RealtimePublisher() as publisher:
        results = await run_all_ingestion_jobs(session, publisher, bbox)
        logger.info("realtime_publish_totals", delivered=publisher.delivered, dropped=publisher.dropped)
    return results


# ---------------------------------------------------------------------------------------------------------------
# The `jobs-*` verbs: the durable archive lanes. See docs/runbooks/durable-backfill-lanes.md for the
# operator's view and jobs/AGENTS.md for why the ledger is the substrate.
#
# These verbs deliberately do NOT go through `emit`/`finish`. Those two exist to fold a list of
# `IngestionJobResult`s and fail the run when any source failed, and that rule is wrong here: a slice that
# ends with work remaining is a healthy multi-tick backfill, not a failed job. Each verb below prints one
# JSON line the same way and states its own exit rule in its docstring.
# ---------------------------------------------------------------------------------------------------------------

# A stream whose verdict is `invalid` fails `validate-streams`. `incomplete` deliberately does not.
INVALID_STREAM_EXIT_CODE: Final = 1

# Railway sets this per running container instance and it is the only stable, unique handle a one-shot cron
# container has. The uuid4 fallback is not a lesser option, it is the correct one off Railway -- `worker_id`
# only has to be unique per concurrent claimer, and two ticks sharing one would each read the other's lease
# as their own and heartbeat a shard they do not hold.
WORKER_ID_VARIABLE: Final = "RAILWAY_REPLICA_ID"

# `job_work_item.lease_owner` and `job_attempt.worker_id` are both VARCHAR(255). An over-long value does not
# truncate, it aborts the claim, so the id is clamped here rather than discovered as a constraint violation.
WORKER_ID_MAX_LENGTH: Final = 255

# One row per (definition, run, work-item status). Eight statuses times a handful of runs per lane; a lane
# mints one extra run each time its floor is lowered, which is a handful over the service's whole life.
MAX_JOB_STATUS_ROWS: Final = 5_000

# Dead-lettered shard keys are LISTED, not just counted, because the shard key is the actionable identity --
# it is what an operator requeues. The listing is bounded; the count beside it always comes from the status
# aggregate, so a truncated listing still reports the true number it omitted.
MAX_DEAD_LETTER_ROWS: Final = 1_000
MAX_REPORTED_DEAD_LETTER_WINDOWS: Final = 50

# A work item in one of these states is settled; anything else is a window the lane still owes. Spelled the
# same way `validation.SETTLED_WORK_ITEM_STATES` spells it, because they answer the same question.
SETTLED_WORK_ITEM_STATES: Final[frozenset[str]] = frozenset({"succeeded", "cancelled"})

DEAD_LETTER_WORK_ITEM_STATE: Final = "dead_letter"

_JOB_WORK_ITEM_STATES: Final = text("""
-- job_work_item_states
SELECT definitions.name     AS definition,
       runs.logical_run_key AS run_key,
       items.status         AS status,
       count(*)             AS window_count,
       min(items.shard_key) AS oldest_shard_key
FROM agri.job_definition AS definitions
JOIN agri.job_run AS runs
  ON runs.job_definition_id = definitions.id
JOIN agri.job_work_item AS items
  ON items.job_run_id = runs.id
WHERE (CAST(:definition AS text) IS NULL OR definitions.name = CAST(:definition AS text))
GROUP BY definitions.name, runs.logical_run_key, items.status
ORDER BY definitions.name, runs.logical_run_key, items.status
LIMIT :row_limit
""")

# `last_error_summary` is deliberately not selected. It is already redacted at the chokepoint
# (jobs/AGENTS.md "Redaction") but it is up to 500 characters per row, and this verb's whole purpose is to
# be a line an operator can read. The failure CLASS is the part that tells them what to do next.
_JOB_DEAD_LETTERED_WINDOWS: Final = text("""
-- job_dead_lettered_windows
SELECT definitions.name       AS definition,
       items.shard_key        AS shard_key,
       items.last_error_class AS last_error_class,
       items.attempt_count    AS attempt_count
FROM agri.job_definition AS definitions
JOIN agri.job_run AS runs
  ON runs.job_definition_id = definitions.id
JOIN agri.job_work_item AS items
  ON items.job_run_id = runs.id
WHERE items.status = 'dead_letter'
  AND (CAST(:definition AS text) IS NULL OR definitions.name = CAST(:definition AS text))
ORDER BY definitions.name, items.shard_key
LIMIT :row_limit
""")

# The clock columns `jobs-status` had none of. Every one of these already existed and was unused, so five
# of an operator's six questions -- is the cron alive, is this lane stalled, how fast is it going, how long
# is left, when did this window last move -- were unanswerable from the tool built to answer them.
#
# `max(attempts.started_at)` is the per-lane heartbeat of last resort and reads "dead" for a lane that is
# merely finished; the `job_event` row `jobs-run` now writes on every tick is the honest one. Grouping is
# per RUN because a lane whose floor was lowered has two, and the aggregates are min/max only, so the
# LEFT JOIN's fan-out over attempts cannot distort them the way a count(*) would.
#
# `observed_at` rides this statement rather than a `SELECT now()` of its own. It is the DATABASE's clock and
# not the operator's laptop, for the same reason every lease timestamp is -- a staleness figure is only
# trustworthy when the "now" it is subtracted from came from the same clock as the instants themselves --
# and it needs no separate round trip, because a definition with no rows here has no rows in the state pass
# either and is therefore never printed at all.
_JOB_LANE_TIMESTAMPS: Final = text("""
-- job_lane_timestamps
SELECT definitions.name     AS definition,
       runs.logical_run_key AS run_key,
       now()                AS observed_at,
       max(attempts.started_at)  AS last_attempt_started_at,
       max(attempts.finished_at) AS last_attempt_finished_at,
       max(items.completed_at) FILTER (WHERE items.status = 'succeeded') AS last_succeeded_at,
       min(items.created_at) FILTER (WHERE items.status = 'queued')      AS oldest_queued_created_at,
       min(items.lease_expires_at)                                       AS next_lease_expiry
FROM agri.job_definition AS definitions
JOIN agri.job_run AS runs
  ON runs.job_definition_id = definitions.id
JOIN agri.job_work_item AS items
  ON items.job_run_id = runs.id
LEFT JOIN agri.job_attempt AS attempts
  ON attempts.job_work_item_id = items.id
WHERE (CAST(:definition AS text) IS NULL OR definitions.name = CAST(:definition AS text))
GROUP BY definitions.name, runs.logical_run_key
ORDER BY definitions.name, runs.logical_run_key
LIMIT :row_limit
""")


def _lane_day(value: str, option_name: str) -> datetime:
    """Parse a lane boundary as a plain UTC calendar day, refusing a time the floor-anchored grid would drop."""
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError as error:
        raise click.BadParameter(f"{value!r} is not a YYYY-MM-DD calendar day", param_hint=option_name) from error
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC)


def _lane_from_token(lane_name: str, floor: str | None) -> BackfillLane:
    """Resolve a `--lane` token, applying a `--floor` override that mints its own run rather than reopening one."""
    try:
        lane = resolve_lane(lane_name)
    except UnknownBackfillLaneError as error:
        raise click.BadParameter(str(error), param_hint="--lane") from error
    if floor is None:
        return lane
    override = _lane_day(floor, "--floor")
    # A source that refuses history below a day would spend eight attempts per window discovering that, and
    # a lane floored below its source's own `earliest` plans thousands of windows every one of which
    # dead-letters. The capability already states the boundary, so it is read rather than re-declared.
    earliest = archive_source(lane).history_capability().earliest
    if earliest is not None and override < earliest:
        raise click.BadParameter(
            f"{lane.name} serves no history before {earliest.date().isoformat()}",
            param_hint="--floor",
        )
    try:
        return replace(lane, floor=override)
    except LaneSpecificationError as error:
        raise click.BadParameter(str(error), param_hint="--floor") from error


def _definition_filter(definition_name: str | None, lane_name: str | None) -> str | None:
    """Turn `--definition`/`--lane` into the one definition name to act on, refusing two answers at once.

    `--lane` is the spelling a deployment should use. `archive_lane_definition_name` is the ONLY producer
    of a `job_definition.name`, so resolving the token here means an `infra/cron-*/railway.json` never
    carries a second hard-coded copy of it -- and a second copy is not a cosmetic duplicate, it joins to
    nothing the day the naming changes, and a slice that joins to nothing silently claims no work.
    """
    if definition_name is not None and lane_name is not None:
        raise click.BadParameter("pass --definition or --lane, not both; a lane names exactly one definition")
    if lane_name is not None:
        return archive_lane_definition_name(_lane_from_token(lane_name, None))
    return definition_name


def _required_definition(definition_name: str | None, lane_name: str | None) -> str:
    """Resolve the one definition a slice runs, refusing "neither" rather than claiming across every lane."""
    definition = _definition_filter(definition_name, lane_name)
    if definition is None:
        raise click.BadParameter("name the work to run with --lane (preferred) or --definition")
    return definition


def _resolve_worker_id(worker_id: str | None) -> str:
    """Name this tick's lease owner, preferring an operator's own label then Railway's per-container id."""
    explicit = (worker_id or "").strip()
    if explicit:
        return explicit[:WORKER_ID_MAX_LENGTH]
    replica = os.environ.get(WORKER_ID_VARIABLE, "").strip()
    return f"jobs-run:{replica or uuid.uuid4()}"[:WORKER_ID_MAX_LENGTH]


def _ledger_failure(exc: Exception, action: str) -> click.ClickException:
    """Degrade a ledger failure to something safe to print, keeping the repo's SQLAlchemy-message rule."""
    # A SQLAlchemyError message carries the whole statement and every bound parameter, which is how a DSN
    # password reaches a cron log. Same degradation interface/cli/commands.py applies to every other direct-SQL verb.
    reason = f"{action} failed ({exc.__class__.__name__})" if isinstance(exc, SQLAlchemyError) else str(exc)
    return click.ClickException(reason)


@click.command("jobs-plan-lane")
@click.option("--lane", "lane_name", required=True, help="Backfill lane token; the error names the registered set.")
@click.option("--floor", default=None, help="Walk deeper than the lane declares, as YYYY-MM-DD. Mints its own run.")
@click.option("--until", default=None, help="Plan whole windows below this YYYY-MM-DD day; defaults to today.")
def jobs_plan_lane(lane_name: str, floor: str | None, until: str | None) -> None:
    """Declare one archive lane and fan its windows out as durable work items, idempotently.

    Safe to re-run on every tick of every day. `open_job_run` inserts the run `ON CONFLICT
    (logical_run_key) DO NOTHING` and each window `ON CONFLICT (job_run_id, shard_key) DO NOTHING`, and
    `lane_windows` anchors its grid at the lane's FLOOR rather than at today -- so a replan produces
    byte-identical shard keys and adds, at most once per `window_days`, one genuinely new window.

    `--floor` does not edit the existing run. The floor is part of `logical_run_key`, so a deeper floor
    opens a SECOND run with its own grid and its own counters, and the shallower one stays exactly as
    complete as it was. That is the durable form of the thing `firms-archive-full.sh` had to delete a
    sentinel file to achieve.

    EXIT CODES -- always 0 unless the plan itself could not be written. Planning is a declaration, and a
    lane that owes 1,900 windows the moment it is planned is the normal, healthy state.
    """
    lane = _lane_from_token(lane_name, floor)
    end = None if until is None else _lane_day(until, "--until")
    try:
        summary = asyncio.run(_plan_archive_lane(lane, end))
    except (SQLAlchemyError, JobRunError, JobSpecificationError, JobLedgerRowError) as exc:
        raise _ledger_failure(exc, f"planning lane {lane.name}") from exc
    click.echo(json.dumps(summary, sort_keys=True))


async def _plan_archive_lane(lane: BackfillLane, end: datetime | None) -> dict[str, object]:
    """Open one session for the definition upsert and the fan-out, and commit them together."""
    async with ingest_session() as session:
        await apply_statement_timeout(session)
        opened = await plan_archive_lane(session, lane, end=end, requested_by="agri-service ops jobs-plan-lane")
        await session.commit()
    return {
        "lane": lane.name,
        "definition": archive_lane_definition_name(lane),
        "run_key": opened.logical_run_key,
        "job_run_id": str(opened.job_run_id),
        "created": opened.created,
        "added_work_items": opened.added_work_items,
        "total_work_items": opened.total_work_items,
        "run_status": opened.status,
        "floor_day": lane.floor_day,
        "window_days": lane.window_days,
        "chunk_days": lane.chunk_days,
        "planned_through": None if end is None else end.date().isoformat(),
    }


@click.command("jobs-run")
@click.option("--lane", "lane_name", default=None, help="Backfill lane token; the deployment's preferred spelling.")
@click.option("--definition", "definition_name", default=None, help="An explicit agri.job_definition.name.")
@click.option("--budget-seconds", type=float, default=None, help="Override the definition's own slice budget.")
@click.option("--worker-id", default=None, help="Label this lease owner; defaults to the Railway replica id.")
@click.pass_context
def jobs_run(
    context: click.Context,
    lane_name: str | None,
    definition_name: str | None,
    budget_seconds: float | None,
    worker_id: str | None,
) -> None:
    """Run ONE bounded slice of a durable definition: claim, work, checkpoint, exit. This is what a cron tick runs.

    Name the work with `--lane`, not `--definition`. A definition name has exactly one producer
    (`archive_lane_definition_name`), and a `railway.json` that spelled the token itself would carry a
    second copy that joins to nothing the day the naming changes -- and a slice that joins to nothing
    claims no work while still exiting 0, which is the silence this whole port exists to remove.

    EXIT CODES, and they are the point of this verb:

      0 -- the slice ran. Work may remain, the time budget may be spent, nothing may have been claimable:
           all three are a healthy multi-tick backfill and none of them is an incident. A backfill that
           spans weeks is INCOMPLETE by definition, and exiting non-zero merely because work remains would
           page someone at 3am for a system working exactly as designed.
      1 -- a work item DEAD-LETTERED during this slice, or the slice itself raised.

    A dead letter is the one thing that genuinely needs a human: the window spent all eight attempts and is
    now missing from the archive until someone requeues it. Everything else the runtime does with a shard is
    a park it will pick up again -- `retried`, `deferred` and `yielded` are all "come back next tick".
    `abandoned` is not a failure either: the fence moved, another worker owns that window, and its work is
    theirs.
    """
    definition = _required_definition(definition_name, lane_name)
    try:
        summary = asyncio.run(
            run_archive_definition_slice(
                definition_name=definition,
                worker_id=_resolve_worker_id(worker_id),
                budget_seconds=budget_seconds,
            )
        )
    except (
        JobDefinitionNotFoundError,
        JobLedgerRowError,
        JobRunError,
        JobSpecificationError,
        UnknownJobHandlerError,
        SQLAlchemyError,
    ) as exc:
        raise _ledger_failure(exc, f"job slice for {definition}") from exc
    click.echo(json.dumps(summary.to_summary(), sort_keys=True))
    if summary.dead_lettered:
        context.exit(FAILED_JOB_EXIT_CODE)


async def run_archive_definition_slice(
    *,
    definition_name: str,
    worker_id: str,
    budget_seconds: float | None,
) -> JobSliceSummary:
    """Open one session, publisher and write path for the whole tick, then drive one bounded slice through them.

    The shared core behind `jobs-run` AND `jobs-pulse` (`execution/jobs_pulse_command.py`) -- both name
    a durable archive definition and want exactly this: one session, one bounded slice, one summary.
    `jobs-run` targets one definition per invocation via `--lane`/`--definition`; `jobs-pulse` calls this
    once per definition the ledger's own namespace still owes a tick, in one cron container. Neither
    caller's behaviour changed when this was extracted -- this is a pure rename of `_run_archive_slice`.

    ONE `ingest_session()` for the slice and never one per work item: `db/engine.ingest_session` builds a
    new engine per `async with` and disposes it in its `finally`, so a per-shard binding would be a full
    TCP+TLS+auth handshake against the Railway proxy for every window. jobs/AGENTS.md states the same rule,
    and `archive_walk_context` exists precisely so the handler can never open its own.

    `shutdown_signal()` is installed HERE and not inside the runtime, because this is the process boundary:
    it is the only scope that knows this is a one-shot container rather than a library call, and handlers
    installed for the length of one slice are restored when the slice ends. Without it a Railway SIGTERM --
    a redeploy, an eviction, a manual restart -- ends the container mid-shard and strands that window
    behind a lease no living process owns. See jobs/AGENTS.md "Shutdown and heartbeat semantics".
    """
    async with ingest_session() as session, RealtimePublisher() as publisher, shutdown_signal() as stop:
        write_features = bind_feature_writer(session, publisher)
        # No shared httpx client is offered, deliberately. Each archive source opens its own for the length
        # of one chunk under its OWN measured bounds -- FIRMS 15s/16MB, NWIS 90s/32MB -- and a client handed
        # down from here would impose one lane's timeout on the other's chunk. What that gives up is small:
        # a source already scopes one client across a whole chunk's product and tile requests, so the saving
        # would only be across chunks, and http.py's transport retry plus its 10-connection ceiling are what
        # actually answer the `ConnectError`/`getaddrinfo failed` exhaustion the bash driver hit.
        walk_context = ArchiveWalkContext(write_features=write_features)
        async with archive_walk_context(walk_context):
            summary = await run_job_slice(
                session,
                definition_name=definition_name,
                worker_id=worker_id,
                budget_seconds=budget_seconds,
                stop=stop,
            )
        logger.info("realtime_publish_totals", delivered=publisher.delivered, dropped=publisher.dropped)
    return summary


@click.command("jobs-status")
@click.option("--definition", "definition_name", default=None, help="Restrict to one agri.job_definition.name.")
@click.option("--lane", "lane_name", default=None, help="Restrict to one lane, resolved to its definition name.")
def jobs_status(definition_name: str | None, lane_name: str | None) -> None:
    """Report each definition's work items by state, its oldest outstanding window, and its dead-lettered shards.

    This replaces grepping a log file. The bash driver's progress was a cursor file and its failures were a
    ledger file that the next successful run deleted; both are rows here, so "which windows are still
    missing" is a query rather than an archaeology exercise.

    Counts are grouped per RUN as well as aggregated, because a lane whose floor was lowered has two runs
    over overlapping calendar days. The aggregate counts LEDGER ROWS, not calendar days -- read the per-run
    breakdown when a lane has more than one run.

    Every line carries the database's `observed_at` and five instants read against it: when this lane last
    claimed (`last_attempt_started_at`), last closed an attempt, last landed a window, how long its oldest
    still-queued window has been waiting, and when its next lease expires. A `last_attempt_started_at` far
    behind `observed_at` is a stalled lane; a `next_lease_expiry` already in the past is a shard the next
    tick's reaper owes. None of these judge -- see `jobs-run`'s exit codes for the thing that does.

    EXIT CODES -- always 0. This verb answers a question; it does not judge. A dead letter is reported, and
    `jobs-run` is what turns one into a failed cron run at the moment it happens.
    """
    definition = _definition_filter(definition_name, lane_name)
    try:
        definitions = asyncio.run(_read_job_status(definition))
    except (SQLAlchemyError, JobLedgerRowError) as exc:
        raise _ledger_failure(exc, "reading job status") from exc
    for entry in definitions:
        click.echo(json.dumps(entry, sort_keys=True))


async def _read_job_status(definition: str | None) -> list[dict[str, object]]:
    """Read the ledger passes in one transaction and fold them into one printable object per definition."""
    async with ingest_session() as session:
        await apply_statement_timeout(session)
        state_rows = await fetch_rows(
            session,
            _JOB_WORK_ITEM_STATES,
            {"definition": definition, "row_limit": MAX_JOB_STATUS_ROWS},
        )
        dead_letter_rows = await fetch_rows(
            session,
            _JOB_DEAD_LETTERED_WINDOWS,
            {"definition": definition, "row_limit": MAX_DEAD_LETTER_ROWS},
        )
        timestamp_rows = await fetch_rows(
            session,
            _JOB_LANE_TIMESTAMPS,
            {"definition": definition, "row_limit": MAX_JOB_STATUS_ROWS},
        )
    return _fold_job_status(state_rows, dead_letter_rows, timestamp_rows)


def _instant(value: datetime | None) -> str | None:
    """Render one ledger timestamp for a JSON line, keeping NULL distinguishable from the epoch."""
    return None if value is None else value.isoformat()


def _newest(left: datetime | None, right: datetime | None) -> datetime | None:
    """The later of two optional instants, treating absence as "no evidence" rather than as long ago."""
    if left is None or right is None:
        return left or right
    return max(left, right)


def _oldest(left: datetime | None, right: datetime | None) -> datetime | None:
    """The earlier of two optional instants, treating absence as "no evidence" rather than as just now."""
    if left is None or right is None:
        return left or right
    return min(left, right)


@dataclass(frozen=True, slots=True)
class _RunTimestamps:
    """When a run last moved and when it next expects to: the clock this report had no column for.

    Read against the `observed_at` on the same line. `last_attempt_started_at` far behind it means nothing
    has claimed in this lane; a `next_lease_expiry` in the past means a shard is stranded and the next
    tick's reaper owes it; `oldest_queued_created_at` is how long the lane's frontier has been waiting.
    """

    last_attempt_started_at: datetime | None = None
    last_attempt_finished_at: datetime | None = None
    last_succeeded_at: datetime | None = None
    oldest_queued_created_at: datetime | None = None
    next_lease_expiry: datetime | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> _RunTimestamps:
        """Read one grouped timestamp row, refusing a column that came back as something other than an instant."""
        return cls(
            last_attempt_started_at=optional_column(row, "last_attempt_started_at", datetime),
            last_attempt_finished_at=optional_column(row, "last_attempt_finished_at", datetime),
            last_succeeded_at=optional_column(row, "last_succeeded_at", datetime),
            oldest_queued_created_at=optional_column(row, "oldest_queued_created_at", datetime),
            next_lease_expiry=optional_column(row, "next_lease_expiry", datetime),
        )

    def merge(self, other: _RunTimestamps) -> _RunTimestamps:
        """Fold a sibling run's clock into this one: newest for what has happened, oldest for what is owed."""
        return _RunTimestamps(
            last_attempt_started_at=_newest(self.last_attempt_started_at, other.last_attempt_started_at),
            last_attempt_finished_at=_newest(self.last_attempt_finished_at, other.last_attempt_finished_at),
            last_succeeded_at=_newest(self.last_succeeded_at, other.last_succeeded_at),
            oldest_queued_created_at=_oldest(self.oldest_queued_created_at, other.oldest_queued_created_at),
            next_lease_expiry=_oldest(self.next_lease_expiry, other.next_lease_expiry),
        )

    def to_summary(self) -> dict[str, object]:
        """Render the five instants as ISO-8601 strings beside the counts they explain."""
        return {
            "last_attempt_started_at": _instant(self.last_attempt_started_at),
            "last_attempt_finished_at": _instant(self.last_attempt_finished_at),
            "last_succeeded_at": _instant(self.last_succeeded_at),
            "oldest_queued_created_at": _instant(self.oldest_queued_created_at),
            "next_lease_expiry": _instant(self.next_lease_expiry),
        }


@dataclass(slots=True)
class _RunTally:
    """One run's per-status window counts, and the oldest window it still owes. Mutable, like worker._SliceTally."""

    run_key: str
    states: dict[str, int] = field(default_factory=dict)
    oldest_outstanding_window: str | None = None
    timestamps: _RunTimestamps = field(default_factory=_RunTimestamps)

    def record(self, status: str, window_count: int, oldest_shard_key: str | None) -> None:
        """Fold in one grouped (status, count) row, tracking the oldest shard key among the unsettled statuses."""
        self.states[status] = self.states.get(status, 0) + window_count
        if oldest_shard_key is None or status in SETTLED_WORK_ITEM_STATES:
            return
        # Shard keys are `<lane>:<YYYY-MM-DD>..<YYYY-MM-DD>`, so within one lane the lexicographic minimum
        # IS the chronologically oldest window. That is a property of the key format, not a coincidence.
        if self.oldest_outstanding_window is None or oldest_shard_key < self.oldest_outstanding_window:
            self.oldest_outstanding_window = oldest_shard_key

    @property
    def total_windows(self) -> int:
        """Every window this run holds, in whatever state."""
        return sum(self.states.values())

    @property
    def outstanding_windows(self) -> int:
        """Windows this run still owes: everything that has not succeeded and was not cancelled."""
        return sum(count for status, count in self.states.items() if status not in SETTLED_WORK_ITEM_STATES)

    def to_summary(self) -> dict[str, object]:
        """Render one run's row inside a definition's line."""
        return {
            "run_key": self.run_key,
            "states": dict(sorted(self.states.items())),
            "total_windows": self.total_windows,
            "outstanding_windows": self.outstanding_windows,
            "oldest_outstanding_window": self.oldest_outstanding_window,
            **self.timestamps.to_summary(),
        }


def _fold_job_status(
    state_rows: Sequence[Mapping[str, object]],
    dead_letter_rows: Sequence[Mapping[str, object]],
    timestamp_rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Fold the grouped rows into one object per definition, with the per-run breakdown nested inside it."""
    # Every timestamp row carries the same `now()` from the one statement that produced them all, so the
    # first is as good as any: one clock for the whole report, not one per line.
    observed_at = None if not timestamp_rows else optional_column(timestamp_rows[0], "observed_at", datetime)
    runs: dict[tuple[str, str], _RunTally] = {}
    for row in state_rows:
        definition = required_column(row, "definition", str)
        run_key = required_column(row, "run_key", str)
        tally = runs.setdefault((definition, run_key), _RunTally(run_key=run_key))
        tally.record(
            required_column(row, "status", str),
            required_column(row, "window_count", int),
            optional_column(row, "oldest_shard_key", str),
        )

    for row in timestamp_rows:
        # Keyed the same way the state rows are, so a run that both passes report lands on one tally. The
        # two statements are read in the same transaction, so they cannot disagree about which runs exist.
        key = (required_column(row, "definition", str), required_column(row, "run_key", str))
        timed = runs.setdefault(key, _RunTally(run_key=key[1]))
        timed.timestamps = _RunTimestamps.from_row(row)

    listed_dead_letters: dict[str, list[dict[str, object]]] = {}
    for row in dead_letter_rows:
        listed_dead_letters.setdefault(required_column(row, "definition", str), []).append(
            {
                "shard_key": required_column(row, "shard_key", str),
                "attempt_count": required_column(row, "attempt_count", int),
                "last_error_class": optional_column(row, "last_error_class", str),
            }
        )

    definitions: dict[str, list[_RunTally]] = {}
    for (definition, _run_key), tally in runs.items():
        definitions.setdefault(definition, []).append(tally)
    return [
        _definition_entry(definition, tallies, listed_dead_letters.get(definition, ()), observed_at)
        for definition, tallies in sorted(definitions.items())
    ]


def _definition_entry(
    definition: str,
    tallies: Sequence[_RunTally],
    dead_letters: Sequence[Mapping[str, object]],
    observed_at: datetime | None,
) -> dict[str, object]:
    """Aggregate one definition's runs into the single line an operator reads, keeping the runs beside it."""
    states: dict[str, int] = {}
    for tally in tallies:
        for status, count in tally.states.items():
            states[status] = states.get(status, 0) + count
    oldest = [tally.oldest_outstanding_window for tally in tallies if tally.oldest_outstanding_window is not None]
    dead_letter_count = states.get(DEAD_LETTER_WORK_ITEM_STATE, 0)
    shown = list(dead_letters[:MAX_REPORTED_DEAD_LETTER_WINDOWS])
    clock = _RunTimestamps()
    for tally in tallies:
        clock = clock.merge(tally.timestamps)
    return {
        "definition": definition,
        # The database's clock, on the same line as the instants it is read against, so staleness is a
        # subtraction an operator can do by eye rather than one that needs their laptop to agree.
        "observed_at": _instant(observed_at),
        **clock.to_summary(),
        "run_count": len(tallies),
        "states": dict(sorted(states.items())),
        "total_windows": sum(states.values()),
        "outstanding_windows": sum(count for status, count in states.items() if status not in SETTLED_WORK_ITEM_STATES),
        "oldest_outstanding_window": min(oldest) if oldest else None,
        "dead_lettered": dead_letter_count,
        "dead_letter_windows": shown,
        # Taken from the status aggregate rather than from the listing's length, so a listing truncated by
        # either cap still reports the true number of dead letters it did not print.
        "omitted_dead_letter_windows": max(0, dead_letter_count - len(shown)),
        "runs": [tally.to_summary() for tally in sorted(tallies, key=lambda tally: tally.run_key)],
    }


@click.command("validate-streams")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["json", "markdown"], case_sensitive=True),
    default="json",
    show_default=True,
    help="Machine-readable JSON, or the Markdown form for docs/reports/.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path, dir_okay=False, writable=True),
    default=None,
    help="Write the report here; stdout then carries only a one-line receipt.",
)
@click.option("--bbox", default=None, help="Override INGEST_BBOX as west,south,east,north.")
@click.pass_context
def validate_streams(
    context: click.Context,
    output_format: str,
    output_path: Path | None,
    bbox: str | None,
) -> None:
    """Run the cross-stream completeness and validity report over the warehouse and the job ledger.

    EXIT CODES:

      0 -- every stream is `complete` or `incomplete`.
      1 -- at least one stream is `invalid`.

    `incomplete` MUST NOT fail the run and that is not a leniency. A backfill in flight is incomplete by
    definition: the fire-detections stream is incomplete from the moment its 1,900-window lane is planned
    until the last window lands, which is weeks of correct operation. A daily cron that went red for all of
    it would be ignored by the time it mattered. `invalid` is different -- it means rows that ARE there are
    wrong (null geometry, unlinked geometry, a duplicate identity, a `-999999` sentinel served as a
    measurement), and no amount of further walking fixes those.

    A dead-lettered window can never produce a `complete` verdict, so a lost window still shows up here even
    when every validity check reads zero.
    """
    try:
        report = asyncio.run(build_stream_validation_report(bbox))
    except (SQLAlchemyError, ObservedDayScanTooLargeError, ValidationRowError, ValueError) as exc:
        raise _ledger_failure(exc, "cross-stream validation") from exc
    document = report.to_json(indent=2) if output_format == "json" else report.to_markdown()
    if output_path is None:
        # `indent=None` for the stdout JSON form: a cron log line is parsed, not read.
        click.echo(report.to_json() if output_format == "json" else document)
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(document, encoding="utf-8", newline="\n")
        click.echo(
            json.dumps(
                {
                    "state": "written",
                    "output": str(output_path),
                    "format": output_format,
                    "verdicts": dict(report.verdict_counts),
                },
                sort_keys=True,
            )
        )
    if any(stream.verdict == "invalid" for stream in report.streams):
        context.exit(INVALID_STREAM_EXIT_CODE)


async def build_stream_validation_report(bbox: str | None) -> ValidationReport:
    """Open one read-only session for the whole report; `build_validation_report` pins the snapshot itself.

    Public because `execution/jobs_pulse_command.py`'s maintenance pass runs this exact measurement on
    the hourly tick. It reuses this rather than opening its own session, for the same reason it reuses
    `run_archive_definition_slice`: one spelling of "how this check is run", not two that can drift.
    """
    async with ingest_session() as session:
        return await build_validation_report(session, bbox=bbox)


@click.command("jobs-reconcile-lane")
@click.option("--lane", "lane_name", required=True, help="Backfill lane token; the error names the registered set.")
@click.option("--floor", default=None, help="Reconcile the run planned at this YYYY-MM-DD floor, not the declared one.")
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    default=False,
    help="Perform the settlement; the default is a dry run that writes nothing.",
)
def jobs_reconcile_lane(lane_name: str, floor: str | None, apply_changes: bool) -> None:
    """Settle the planned windows a lane's layer already serves, so a fresh plan re-walks nothing that landed.

    DRY RUN BY DEFAULT. Without `--apply` this opens no write transaction at all and prints what it would
    settle: how many windows, over which calendar span, and a sample of the windows themselves with the
    observed-versus-expected day counts that decided each one. Read the span first -- it is the single fact
    that tells you whether the reconciliation is measuring the era you think it is.

    Coverage is derived from `geo.feature_observation_day` over the lane's own layer, NEVER from the bash
    cursor files. The cursor recorded where the walk had REACHED; it did not record what had LANDED, and on
    the first full pass those differed by 169 of 298 FIRMS windows. Importing the cursor would import
    exactly that error. It also means the cursor and failure files need no migration when the bash drivers
    are retired -- they can be abandoned where they lie.

    Only fully-covered windows are settled. A partially-covered window stays queued: partial is not landed,
    and the days it is missing are the days the walk still owes. Windows another worker holds under a live
    lease are left alone, and a dead-lettered window is never converted -- a dead letter is the evidence
    that eight attempts failed, and erasing it is the failure this whole ledger exists to prevent.

    EXIT CODES -- always 0. A lane with nothing to reconcile is the normal state, and a lane with hundreds
    of covered windows is a successful measurement, not an error.
    """
    lane = _lane_from_token(lane_name, floor)
    try:
        reconciliation = asyncio.run(reconcile_archive_lane(lane, apply_changes=apply_changes))
    except (SQLAlchemyError, MissingIngestionLayerError, ReconciliationError, JobLedgerRowError, JobRunError) as exc:
        raise _ledger_failure(exc, f"reconciling lane {lane.name}") from exc
    click.echo(json.dumps(reconciliation.to_summary(), sort_keys=True))


async def reconcile_archive_lane(lane: BackfillLane, *, apply_changes: bool) -> LaneReconciliation:
    """Open one session for the measurement, and commit only when `--apply` actually settled something.

    Public for `execution/jobs_pulse_command.py`'s maintenance pass; see `build_stream_validation_report`.
    """
    async with ingest_session() as session:
        reconciliation = await reconcile_lane(session, lane, apply_changes=apply_changes)
        if apply_changes:
            await session.commit()
        else:
            # An explicit rollback rather than trusting the session's close. A dry run must leave nothing
            # behind even if a future edit to `reconcile_lane` starts writing on a path it does not today.
            await session.rollback()
    return reconciliation


@click.command("jobs-plan-gaps")
@click.option("--lane", "lane_name", required=True, help="Backfill lane token; the error names the registered set.")
@click.option("--floor", default=None, help="Plan into the run planned at this YYYY-MM-DD floor, not the declared one.")
@click.option("--until", default=None, help="Measure gaps below this YYYY-MM-DD day; defaults to today.")
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    default=False,
    help="Perform the planning; the default is a dry run that writes nothing.",
)
def jobs_plan_gaps(lane_name: str, floor: str | None, until: str | None, apply_changes: bool) -> None:
    """Turn the days a lane's layer is MISSING into claimable windows, reopening one that succeeded over nothing.

    The inverse of `jobs-reconcile-lane`, and the half of the loop that was absent. `validate-streams` finds
    a gap and exits 0 on it by design; `jobs-reconcile-lane` only ever REMOVES work. Nothing could turn "the
    report says 2024-03-11 is missing" into "a claimable window exists for 2024-03-11", and `jobs-plan-lane`
    could not help: it appends whole windows below today, so a hole inside an already-succeeded run had no
    verb at all. This is that verb.

    DRY RUN BY DEFAULT. Without `--apply` this opens no write transaction and prints what it would plan: how
    many days are missing, over which calendar span, which windows of the lane's own grid own them, and a
    sample of those windows with the days that decided each one. Read the span first.

    The grid is the lane's, never a second one. Each missing day is mapped back onto the floor-anchored
    window that `jobs-plan-lane` would have given it, so a shard key is byte-identical whichever verb minted
    it and re-running this is a set of `DO NOTHING` inserts. No trailing partial window is ever planned --
    the forward hourly cron owns the present, and a partial would re-key itself every day.

    Only a `succeeded` window is reopened. A queued, retry-waiting or deferred window is already claimable;
    a leased or running one is held by a live worker's fence; a dead letter is the evidence that every
    attempt failed and is never converted; a cancelled window is an operator's decision. All four are
    reported instead, which is the point -- "we found a gap and can do nothing about it" must never have to
    be inferred from silence.

    EXIT CODES -- always 0 unless the plan itself could not be written. A lane with nothing to plan is the
    normal, healthy state, exactly as it is for `jobs-plan-lane` and `jobs-reconcile-lane`.
    """
    lane = _lane_from_token(lane_name, floor)
    end = None if until is None else _lane_day(until, "--until")
    try:
        plan = asyncio.run(plan_archive_lane_gaps(lane, end=end, apply_changes=apply_changes))
    except (
        SQLAlchemyError,
        MissingIngestionLayerError,
        ReconciliationError,
        JobLedgerRowError,
        JobRunError,
        JobSpecificationError,
    ) as exc:
        raise _ledger_failure(exc, f"planning gaps for lane {lane.name}") from exc
    click.echo(json.dumps(plan.to_summary(), sort_keys=True))


async def plan_archive_lane_gaps(
    lane: BackfillLane,
    *,
    end: datetime | None,
    apply_changes: bool,
) -> LaneGapPlan:
    """Open one session for the measurement, and commit only when `--apply` actually planned something.

    Public for `execution/jobs_pulse_command.py`'s maintenance pass; see `build_stream_validation_report`.
    """
    async with ingest_session() as session:
        plan = await plan_lane_gaps(session, lane, apply_changes=apply_changes, end=end)
        if apply_changes:
            await session.commit()
        else:
            # An explicit rollback rather than trusting the session's close, for the same reason
            # `reconcile_archive_lane` does it: a dry run must leave nothing behind even if a future edit
            # starts writing on a path it does not today.
            await session.rollback()
    return plan


INGEST_COMMANDS: tuple[click.Command, ...] = (
    ingest_firms,
    ingest_streamflow,
    ingest_weather,
    ingest_fire_perimeters,
    ingest_drought,
    ingest_ndvi,
    ingest_sensors,
    ingest_evacuation_zones,
    ingest_watersheds,
    ingest_mtbs,
    ingest_backfill,
    ingest_geometry_repair,
    ingest_drought_history,
    ingest_all,
    jobs_plan_lane,
    jobs_plan_gaps,
    jobs_run,
    jobs_status,
    jobs_reconcile_lane,
    validate_streams,
)


def register_ingest_commands(group: click.Group) -> None:
    """Attach every `ingest-*` and `jobs-*` verb to the CLI group."""
    for command in INGEST_COMMANDS:
        group.add_command(command)
