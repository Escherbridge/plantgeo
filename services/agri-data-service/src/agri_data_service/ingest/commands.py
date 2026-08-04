"""The `ingest-*` CLI verbs: each prints one JSON summary per job and exits non-zero when one failed."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING

import click
import structlog

from agri_data_service.db.engine import ingest_session
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
from agri_data_service.ingest.firms import FIRMS_SOURCE, run_fire_ingestion_job
from agri_data_service.ingest.ndvi import NDVI_SOURCE, run_vegetation_ingestion_job
from agri_data_service.ingest.open_meteo import OPEN_METEO_SOURCE, run_weather_ingestion_job
from agri_data_service.ingest.realtime import RealtimePublisher
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
from agri_data_service.ingest.usgs_nwis import USGS_STREAMFLOW_SOURCE, run_water_ingestion_job
from agri_data_service.ingest.vegetation import build_vegetation_source
from agri_data_service.ingest.wfigs import WFIGS_SOURCE, run_fire_perimeters_ingestion_job
from agri_data_service.ingest.writer import bind_feature_writer

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from agri_data_service.ingest.results import IngestionJobResult
    from agri_data_service.ingest.source import IngestionSource
    from agri_data_service.ingest.usdm_history import HistoryBackfillPlan
    from agri_data_service.ingest.writer import FeatureWriter

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
    results = [
        asyncio.run(
            _run_with_feature_writer(
                NDVI_SOURCE,
                lambda write_features: run_vegetation_ingestion_job(write_features, bbox=bbox),
            )
        )
    ]
    finish(context, results)


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


def _build_backfillable_sources() -> Mapping[str, IngestionSource]:
    """The sources that declare a usable HistoryCapability, keyed by the token `--source` takes.

    Built on demand rather than at import: `nws_sensor_source` stamps its own `earliest` from the
    run clock, so a module-level instance would freeze the NWS retention window at import time.
    """
    sources = (nws_sensor_source(), build_vegetation_source())
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
def ingest_backfill(  # noqa: PLR0913 - one parameter per click option, as cli.py's own verbs are
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
    async with ingest_session() as session, RealtimePublisher() as publisher:
        write_features = bind_feature_writer(session, publisher)
        chunks: list[IngestionJobResult] = []

        async def walk() -> IngestionJobResult:
            chunks.extend(await run_source_backfill(source, write_features, plan))
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


INGEST_COMMANDS: tuple[click.Command, ...] = (
    ingest_firms,
    ingest_streamflow,
    ingest_weather,
    ingest_fire_perimeters,
    ingest_drought,
    ingest_ndvi,
    ingest_sensors,
    ingest_evacuation_zones,
    ingest_backfill,
    ingest_geometry_repair,
    ingest_drought_history,
    ingest_all,
)


def register_ingest_commands(group: click.Group) -> None:
    """Attach every `ingest-*` verb to the CLI group."""
    for command in INGEST_COMMANDS:
        group.add_command(command)
