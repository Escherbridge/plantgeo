"""The `ingest-all` orchestrator: seven sources then the geometry repair, isolated per job, exit code out."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agri_data_service.ingest.backfill import GEOMETRY_REPAIR_SOURCE, run_geometry_repair
from agri_data_service.ingest.evacuation_zones import EVACUATION_ZONES_SOURCE, run_evacuation_zones_ingestion_job
from agri_data_service.ingest.firms import FIRMS_SOURCE, run_fire_ingestion_job
from agri_data_service.ingest.open_meteo import OPEN_METEO_SOURCE, run_weather_ingestion_job
from agri_data_service.ingest.results import IngestionJobResult, run_isolated_job
from agri_data_service.ingest.sensors import NWS_SENSOR_SOURCE, run_sensor_ingestion_job
from agri_data_service.ingest.usdm import USDM_SOURCE, PostgresDroughtStore, run_drought_ingestion_job
from agri_data_service.ingest.usgs_nwis import USGS_STREAMFLOW_SOURCE, run_water_ingestion_job
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
    """Run the seven sources in turn, then repair orphaned geometry links, isolating each failure.

    The repair runs LAST and on every tick, not by hand. `geo.features.geometry_id` is what the
    slider's observation window and `getMetricAtDate` both join on, so an unlinked row is invisible
    to the whole time axis while the map still draws it. Orphans regrow continuously because the
    `/api/ingest/*` push routes still write through `services/ingest.ts`, which sets no
    `geometry_id` at all -- so a repair that only ever runs from an operator's laptop means the
    slider silently loses depth between deploys. Running it here bounds that to one cron interval.
    It is the last job because it should claim anything this run's own sources failed to link.
    """
    write_features = bind_feature_writer(session, publisher)
    jobs: list[tuple[str, Callable[[], Awaitable[IngestionJobResult]]]] = [
        (FIRMS_SOURCE, lambda: run_fire_ingestion_job(write_features, bbox=bbox)),
        (USGS_STREAMFLOW_SOURCE, lambda: run_water_ingestion_job(write_features, bbox=bbox)),
        (OPEN_METEO_SOURCE, lambda: run_weather_ingestion_job(write_features, bbox=bbox)),
        (WFIGS_SOURCE, lambda: run_fire_perimeters_ingestion_job(write_features, bbox=bbox)),
        (USDM_SOURCE, lambda: run_drought_ingestion_job(PostgresDroughtStore(session))),
        (NWS_SENSOR_SOURCE, lambda: run_sensor_ingestion_job(write_features, bbox=bbox)),
        (EVACUATION_ZONES_SOURCE, lambda: run_evacuation_zones_ingestion_job(write_features, bbox=bbox)),
        (GEOMETRY_REPAIR_SOURCE, lambda: run_geometry_repair(session)),
    ]
    return [await run_isolated_job(source, run) for source, run in jobs]
