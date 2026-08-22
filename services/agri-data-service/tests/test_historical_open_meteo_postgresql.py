"""Disposable-PostgreSQL contract for the Open-Meteo ERA5-Land archive warehouse path.

Gated on ``AGRI_TEST_DATABASE_URL`` through the shared ``tests/conftest.py`` fixture, which
verifies the Alembic head and refuses the persistent ``plantgeo`` warehouse. Everything runs
inside one transaction that is rolled back, so the database is left exactly as it was found.

The plan uses its own ``grid_name`` and ``cell_key`` prefix so it can never resolve a real
analysis cell, while reusing the exact reviewed source definition so ``_ensure_data_source``
matches a already-registered ``open-meteo-era5-land-archive`` row instead of rejecting it.
"""

# ruff: noqa: PLR2004

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from geoalchemy2 import WKTElement
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from agri_data_service.execution.contracts import canonical_json_bytes
from agri_data_service.execution.historical_writer import (
    finalize_open_meteo_release_set,
    persist_open_meteo_archive_chunk,
)
from agri_data_service.execution.weather_observations.era5_land import (
    OPEN_METEO_ARCHIVE_SOIL_MOISTURE_PARAMETERS,
    HistoricalOpenMeteoArchivePlan,
    OpenMeteoArchiveCapture,
    OpenMeteoArchiveChunk,
    OpenMeteoArchiveChunkResult,
    initialize_historical_open_meteo_checkpoint,
    parse_open_meteo_archive_payload,
    record_historical_open_meteo_result,
)
from agri_data_service.models.historical import (
    CellSourceCrosswalk,
    SignalCoverageAudit,
    SignalObservation,
    SpatialCell,
)
from agri_data_service.models.provenance import ReleaseSet, ReleaseSetItem, ReleaseSetState

if TYPE_CHECKING:
    import uuid

WINDOW_START = date(2022, 4, 30)
WINDOW_END = date(2026, 4, 30)
DAY_COUNT = 1462
RETRIEVED_AT = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
STALE_AS_OF = "2026-08-05T23:59:59Z"
LATE_AS_OF = "2027-12-31T23:59:59Z"

# Deliberately not a real grid or lattice: this test must never bind a governed analysis cell.
TEST_GRID_NAME = "agri-test-open-meteo-0p25deg"
TEST_CELL_LATITUDES = (43.125, 43.375)
TEST_CELL_LONGITUDE = -116.375

PARAMETERS = sorted(OPEN_METEO_ARCHIVE_SOIL_MOISTURE_PARAMETERS)

# The exact reviewed registration from plans/author_pnw_soil_moisture_plans.py, so a database that
# already carries this source key matches rather than raising on governed-metadata drift.
SOURCE_DEFINITION: dict[str, object] = {
    "key": "open-meteo-era5-land-archive",
    "name": "Open-Meteo ERA5-Land archive (redistributed ECMWF reanalysis)",
    "owner": "Open-Meteo",
    "purpose": (
        "Native 0.1-degree ERA5-Land soil-state covariates for the Sentinel-2 NDVI analysis "
        "lattice, which the 1.0-degree CDS output-grid contract cannot address."
    ),
    "base_url": "https://archive-api.open-meteo.com/v1/archive",
    "license_name": "CC-BY 4.0 (Open-Meteo) over Copernicus/ECMWF ERA5-Land",
    "license_url": "https://open-meteo.com/en/license",
    "citation": (
        "Zippenfenig, P. (2023). Open-Meteo.com Weather API. Generated using Copernicus Climate "
        "Change Service information: Munoz Sabater, J. (2019): ERA5-Land hourly data from 1950 to "
        "present, Copernicus Climate Change Service (C3S) Climate Data Store (CDS). Open-Meteo is "
        "an INTERMEDIARY redistributor: these values were not retrieved from ECMWF or the CDS, so "
        "this provenance is weaker than a first-party CDS receipt and must not be presented as one."
    ),
    "retention_days": None,
    "reviewed_at": "2026-08-05T00:00:00Z",
    "reviewed_by": "local-data-operator",
}


def _cell_key(latitude: float) -> str:
    return f"{TEST_GRID_NAME}:{latitude:.4f}:{TEST_CELL_LONGITUDE:.4f}"


def _plan(*, release_set_key: str, release_set_as_of: str) -> HistoricalOpenMeteoArchivePlan:
    """One chunk per cell, so an incomplete checkpoint and a complete one are both reachable."""
    return HistoricalOpenMeteoArchivePlan.model_validate(
        {
            "source": SOURCE_DEFINITION,
            "window": {"start_date": WINDOW_START.isoformat(), "end_date": WINDOW_END.isoformat()},
            "grid_name": TEST_GRID_NAME,
            "grid_resolution_m": 27830,
            "native_grid_degrees": 0.1,
            "native_grid_resolution_m": 9000,
            "cells": [
                {"cell_key": _cell_key(latitude), "latitude": latitude, "longitude": TEST_CELL_LONGITUDE}
                for latitude in TEST_CELL_LATITUDES
            ],
            "chunk_cell_count": 1,
            "parameters": PARAMETERS,
            "transform_version": "open-meteo-era5-land-archive-daily-mean-normalization-v1",
            "release_set_key": release_set_key,
            "release_set_as_of": release_set_as_of,
            "description": "disposable-PostgreSQL contract plan",
        }
    )


def _window_days() -> list[str]:
    days: list[str] = []
    current = WINDOW_START
    while current <= WINDOW_END:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _result(
    plan: HistoricalOpenMeteoArchivePlan,
    chunk: OpenMeteoArchiveChunk,
    values: dict[str, list[float | None]],
) -> OpenMeteoArchiveChunkResult:
    """Parse one synthetic single-cell chunk document into the accounted-for result the writer takes."""
    days = _window_days()
    cell = chunk.cells[0]
    daily: dict[str, object] = {"time": days}
    for parameter in plan.parameters:
        daily[parameter] = values.get(parameter, [0.25] * len(days))
    payload = canonical_json_bytes(
        [
            {
                # The provider answers from the nearest 0.1-degree node, 0.025 from a `.125` centroid.
                "latitude": round(round(cell.latitude / 0.1) * 0.1, 6),
                "longitude": round(round(cell.longitude / 0.1) * 0.1, 6),
                "utc_offset_seconds": 0,
                "timezone": "GMT",
                "timezone_abbreviation": "GMT",
                "elevation": 923.0,
                "daily": daily,
            }
        ]
    )
    return parse_open_meteo_archive_payload(
        plan,
        chunk,
        payload,
        OpenMeteoArchiveCapture(
            retrieved_at=RETRIEVED_AT,
            wire_payload_bytes=len(payload),
            wire_payload_checksum=hashlib.sha256(payload).hexdigest(),
        ),
    )


async def _seed_spatial_cells(session: AsyncSession, plan: HistoricalOpenMeteoArchivePlan) -> None:
    """This lane mints no spatial cells, so the test establishes its own isolated ones."""
    half = 0.125
    for cell in plan.cells:
        west, east = cell.longitude - half, cell.longitude + half
        south, north = cell.latitude - half, cell.latitude + half
        session.add(
            SpatialCell(
                cell_key=cell.cell_key,
                grid_name=plan.grid_name,
                resolution_m=plan.grid_resolution_m,
                geometry=WKTElement(
                    f"POLYGON(({west} {south}, {east} {south}, {east} {north}, {west} {north}, {west} {south}))",
                    srid=4326,
                ),
                centroid=WKTElement(f"POINT({cell.longitude} {cell.latitude})", srid=4326),
                coverage_fraction=1,
            )
        )
    await session.flush()


async def _count(session: AsyncSession, model: Any, release_id: uuid.UUID) -> int:
    statement = select(func.count()).select_from(model).where(model.source_release_id == release_id)
    return int((await session.execute(statement)).scalar_one())


@pytest.mark.asyncio
async def test_open_meteo_chunk_persistence_is_accounted_idempotent_and_finalization_gated(
    agri_db_async_dsn: str,
) -> None:
    """The whole warehouse path: no_data, partial, ON CONFLICT idempotency, and both blocked finalizations."""
    stale_plan = _plan(release_set_key="agri-test-open-meteo-archive-stale", release_set_as_of=STALE_AS_OF)
    late_plan = _plan(release_set_key="agri-test-open-meteo-archive-late", release_set_as_of=LATE_AS_OF)
    gapped: list[float | None] = [0.25] * DAY_COUNT
    gapped[5] = None
    no_data_result = _result(stale_plan, stale_plan.chunks[0], {PARAMETERS[2]: [None] * DAY_COUNT})
    partial_result = _result(stale_plan, stale_plan.chunks[1], {PARAMETERS[0]: gapped})

    engine = create_async_engine(agri_db_async_dsn)
    try:
        async with AsyncSession(bind=engine, expire_on_commit=False) as session:
            transaction = await session.begin()
            try:
                await _seed_spatial_cells(session, stale_plan)
                await _assert_chunk_rows(session, stale_plan, no_data_result, partial_result)
                await _assert_finalization_is_gated(session, stale_plan, late_plan, no_data_result, partial_result)
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def _assert_chunk_rows(
    session: AsyncSession,
    plan: HistoricalOpenMeteoArchivePlan,
    no_data_result: OpenMeteoArchiveChunkResult,
    partial_result: OpenMeteoArchiveChunkResult,
) -> None:
    """Persist both chunks, then prove the rows, the gap records, and re-running the writer."""
    first = await persist_open_meteo_archive_chunk(session, plan=plan, result=no_data_result)
    assert first.observation_count == 2 * DAY_COUNT
    assert first.observed_value_count == 2 * DAY_COUNT
    assert first.coverage_count == len(PARAMETERS)
    assert first.no_data_series_count == 1
    assert first.crosswalk_count == 1
    assert not first.idempotent

    release_id = first.source_release_id
    assert await _count(session, SignalObservation, release_id) == 2 * DAY_COUNT
    assert await _count(session, SignalCoverageAudit, release_id) == len(PARAMETERS)
    assert await _count(session, CellSourceCrosswalk, release_id) == 1

    statuses = (
        await session.execute(
            select(SignalCoverageAudit.source_parameter, SignalCoverageAudit.status).where(
                SignalCoverageAudit.source_release_id == release_id
            )
        )
    ).all()
    assert dict(statuses)[PARAMETERS[2]] == "no_data"
    # A no_data series writes one honest gap record, never 1,462 is_observed=false rows.
    assert (
        await session.execute(
            select(func.count())
            .select_from(SignalObservation)
            .where(
                SignalObservation.source_release_id == release_id,
                SignalObservation.source_parameter == PARAMETERS[2],
            )
        )
    ).scalar_one() == 0

    stored = (
        await session.execute(
            select(SignalObservation.support_key, SignalObservation.metadata_json)
            .where(SignalObservation.source_release_id == release_id)
            .limit(1)
        )
    ).one()
    assert stored[0] == "era5-land-0.1deg"
    # metadata_json duplicated source_parameter and native_grid_name; both are already stored.
    assert stored[1] == {}

    replayed = await persist_open_meteo_archive_chunk(session, plan=plan, result=no_data_result)
    assert replayed.source_release_id == release_id
    assert replayed.idempotent
    assert await _count(session, SignalObservation, release_id) == 2 * DAY_COUNT
    assert await _count(session, SignalCoverageAudit, release_id) == len(PARAMETERS)
    assert await _count(session, CellSourceCrosswalk, release_id) == 1

    second = await persist_open_meteo_archive_chunk(session, plan=plan, result=partial_result)
    assert second.source_release_id != release_id
    assert second.observation_count == len(PARAMETERS) * DAY_COUNT
    assert second.observed_value_count == len(PARAMETERS) * DAY_COUNT - 1
    assert second.no_data_series_count == 0

    missing = (
        await session.execute(
            select(SignalObservation.quality_flag, SignalObservation.normalized_value).where(
                SignalObservation.source_release_id == second.source_release_id,
                SignalObservation.is_observed.is_(False),
            )
        )
    ).all()
    assert [tuple(row) for row in missing] == [("source_missing", None)]


async def _assert_finalization_is_gated(
    session: AsyncSession,
    stale_plan: HistoricalOpenMeteoArchivePlan,
    late_plan: HistoricalOpenMeteoArchivePlan,
    no_data_result: OpenMeteoArchiveChunkResult,
    partial_result: OpenMeteoArchiveChunkResult,
) -> None:
    """Incomplete coverage and a stale as-of are different refusals; only a governed set finalizes."""
    partial_checkpoint = record_historical_open_meteo_result(
        stale_plan,
        initialize_historical_open_meteo_checkpoint(stale_plan, updated_at=RETRIEVED_AT),
        no_data_result,
        updated_at=RETRIEVED_AT,
    )
    assert partial_checkpoint.state == "running"
    with pytest.raises(ValueError, match="complete validated checkpoint is required"):
        await finalize_open_meteo_release_set(session, plan=stale_plan, checkpoint=partial_checkpoint)

    stale_checkpoint = record_historical_open_meteo_result(
        stale_plan, partial_checkpoint, partial_result, updated_at=RETRIEVED_AT
    )
    assert stale_checkpoint.state == "validated"
    # Coverage is complete; the only defect is an as-of time the run overran.
    with pytest.raises(ValueError, match="must not precede a persisted source receipt"):
        await finalize_open_meteo_release_set(session, plan=stale_plan, checkpoint=stale_checkpoint)
    assert await _release_set_id(session, stale_plan.release_set_key) is None

    late_checkpoint = initialize_historical_open_meteo_checkpoint(late_plan, updated_at=RETRIEVED_AT)
    for result in (no_data_result, partial_result):
        late_checkpoint = record_historical_open_meteo_result(
            late_plan, late_checkpoint, result, updated_at=RETRIEVED_AT
        )
    finalized = await finalize_open_meteo_release_set(
        session, plan=late_plan, checkpoint=late_checkpoint, validated_at=RETRIEVED_AT
    )
    assert finalized.source_release_count == 2
    assert not finalized.idempotent
    release_set = (
        await session.execute(select(ReleaseSet).where(ReleaseSet.id == finalized.release_set_id))
    ).scalar_one()
    assert release_set.state == ReleaseSetState.VALIDATED
    assert release_set.manifest_checksum == finalized.manifest_checksum
    assert (
        await session.execute(
            select(func.count()).select_from(ReleaseSetItem).where(ReleaseSetItem.release_set_id == release_set.id)
        )
    ).scalar_one() == 2


async def _release_set_id(session: AsyncSession, logical_key: str) -> uuid.UUID | None:
    statement = select(ReleaseSet.id).where(ReleaseSet.logical_key == logical_key)
    return (await session.execute(statement)).scalar_one_or_none()
