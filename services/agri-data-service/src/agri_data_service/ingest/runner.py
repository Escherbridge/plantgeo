"""The `ingest-all` orchestrator: one surviving source then the geometry repair, isolated per job, exit code out."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agri_data_service.ingest.backfill import GEOMETRY_REPAIR_SOURCE, run_geometry_repair
from agri_data_service.ingest.results import IngestionJobResult, run_isolated_job
from agri_data_service.ingest.wfigs import WFIGS_SOURCE, run_fire_perimeters_ingestion_job
from agri_data_service.ingest.writer import bind_feature_writer

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.ingest.realtime import RealtimePublisher


async def run_all_ingestion_jobs(
    session: AsyncSession,
    publisher: RealtimePublisher | None = None,
    bbox: str | None = None,
) -> list[IngestionJobResult]:
    """Run the one surviving source, then repair orphaned geometry links, isolating each failure.

    SEVEN SOURCES WERE DELETED FROM THIS LIST, NOT DISABLED. `nasa-firms`, `usgs-streamflow`,
    `open-meteo`, `usdm-drought` and `sentinel2-ndvi` went on 2026-09-06; `nws-sensors` and
    `evacuation-zones` went on 2026-09-07. The rule each removal answered to is the same one, and it
    is about who reads `geo.features`, not about who has a Parquet writer: a PostgreSQL producer may
    go once NOTHING still exports the rows it writes. For all seven, the direct-to-Parquet writer in
    `pipeline/direct/` is ACTIVE and the generic `parquet-*` exporter that used to read `geo.features`
    for that layer is retired from the active set, so the producer was feeding a reader that no
    longer exists.

    WFIGS is the one source that rule still keeps, and keeps for a stated reason rather than for want
    of a writer: `fire-perimeters-direct-forward` EXISTS but is SHADOW, because it refuses to publish
    while any upstream WFIGS perimeter carries invalid geometry -- an open owner decision. So
    `parquet-fire-perimeters` is still the ACTIVE writer for that object stream, it still reads
    `geo.features`, and deleting this producer would stop the fire-perimeters layer rather than
    finish its cutover.

    The repair runs LAST and on every tick, not by hand. `geo.features.geometry_id` is what the
    slider's observation window and `getMetricAtDate` both join on, so an unlinked row is invisible
    to the whole time axis while the map still draws it. Orphans regrow continuously because the
    `/api/ingest/*` push routes still write through `services/ingest.ts`, which sets no
    `geometry_id` at all -- so a repair that only ever runs from an operator's laptop means the
    slider silently loses depth between deploys. Running it here bounds that to one cron interval.
    It is the last job because it should claim anything this run's own source failed to link, and
    WFIGS is still writing rows that need linking.
    """
    write_features = bind_feature_writer(session, publisher)
    jobs: list[tuple[str, Callable[[], Awaitable[IngestionJobResult]]]] = [
        (WFIGS_SOURCE, lambda: run_fire_perimeters_ingestion_job(write_features, bbox=bbox)),
        (GEOMETRY_REPAIR_SOURCE, lambda: run_geometry_repair(session)),
    ]
    return [await run_isolated_job(source, run) for source, run in jobs]
