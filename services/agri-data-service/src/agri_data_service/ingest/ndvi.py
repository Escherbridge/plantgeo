"""NDVI ingestion: Sentinel-2 L2A sampled onto the fixed warehouse grid, or a skip carrying the real reason."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.ingest.http import upstream_client
from agri_data_service.ingest.policy import (
    UNCONFIGURED_BBOX_REASON,
    resolve_bounded_bbox,
    resolve_max_source_records,
)
from agri_data_service.ingest.results import IngestionJobResult, skipped_result
from agri_data_service.ingest.source import FetchRequest, select_writes
from agri_data_service.ingest.vegetation import (
    COG_BOUNDS,
    CURRENT_OBSERVATION_WINDOW,
    VEGETATION_SOURCE,
    GridSampleWindow,
    build_vegetation_source,
    collect_ndvi_grid_records,
)

if TYPE_CHECKING:
    import httpx

    from agri_data_service.ingest.writer import FeatureWriter

# Kept under its historical name so the runner and the `ingest-ndvi` verb keep importing one token.
NDVI_SOURCE: Final = VEGETATION_SOURCE

NO_CLEAR_SCENE_REASON: Final = "No cloud-free Sentinel-2 scene covered the coverage box in the observation window"


async def _run_vegetation_job(
    write_features: FeatureWriter,
    area: str,
    client: httpx.AsyncClient,
    now: datetime | None,
) -> IngestionJobResult:
    """Sample the grid from the clearest recent scenes and write the cells those scenes actually filled."""
    end = now if now is not None else datetime.now(UTC)
    request = FetchRequest(bbox=area, max_records=resolve_max_source_records(), now=now, client=client)
    outcome = await collect_ndvi_grid_records(
        client,
        GridSampleWindow(
            bbox=area,
            start=end - CURRENT_OBSERVATION_WINDOW,
            end=end,
            max_cells=request.max_records,
        ),
    )
    if not outcome.records:
        return skipped_result(VEGETATION_SOURCE, NO_CLEAR_SCENE_REASON)

    selection = select_writes(build_vegetation_source(), outcome.records, request)
    return IngestionJobResult(
        source=VEGETATION_SOURCE,
        status="ingested",
        records_seen=len(outcome.records),
        records_written=await write_features(selection.writes),
        truncated=outcome.truncated or selection.truncated,
        details={"cells": outcome.cells_requested, "rejected": selection.rejected},
    )


async def run_vegetation_ingestion_job(
    write_features: FeatureWriter,
    *,
    bbox: str | None = None,
    client: httpx.AsyncClient | None = None,
    now: datetime | None = None,
) -> IngestionJobResult:
    """Sample Sentinel-2 L2A NDVI onto the fixed warehouse grid and write only the cells a scene really filled.

    This replaces the stub whose skip reason was "No versioned warehouse-backed NDVI adapter is
    configured". That reason was true of the app's only NDVI source: the vegetation tile route proxies
    NASA GIBS' MODIS_Terra_NDVI_8Day WMTS layer, which returns pre-rendered colour-ramped PNG tiles with
    no raw values recoverable from the pixels. The GIBS proxy stays exactly as it is for display; this
    job reads a different, quantitative source for the warehouse: Sentinel-2 L2A Cloud-Optimized GeoTIFFs
    on AWS Open Data, catalogued by Element84's Earth Search STAC API. Both are keyless and open.

    Every value is measured, never modelled. A cell is written only when at least MIN_VALID_SUBSAMPLES of
    its 5x5 lattice points fall on pixels the scene classification layer marks as usable surface, and its
    observed_at is the acquisition instant of the scene that supplied those pixels -- read from the STAC
    item's own `properties.datetime`, never from the run clock. Because a bbox-wide pass is assembled from
    many acquisitions, two cells written by the same run legitimately carry different observation times,
    and that is preserved rather than collapsed onto one date for the run.

    When nothing is measurable the job skips and writes nothing: an unset INGEST_BBOX, a window in which
    no scene under MAX_SCENE_CLOUD_COVER_PERCENT covered the box, or a cell whose lattice was entirely
    cloud, snow, shadow or scene nodata. An empty cell is an honest absence of observation, so no value is
    interpolated, carried forward or invented to fill it.

    `write_features` is required: the runner and the `ingest-ndvi` verb both bind one, and an unbound
    call must fail loudly rather than skip quietly under a reason that hides the missing wiring.
    """
    area = resolve_bounded_bbox(bbox)
    if area is None:
        return skipped_result(VEGETATION_SOURCE, UNCONFIGURED_BBOX_REASON)
    if client is not None:
        return await _run_vegetation_job(write_features, area, client, now)
    async with upstream_client(COG_BOUNDS) as owned_client:
        return await _run_vegetation_job(write_features, area, owned_client, now)
