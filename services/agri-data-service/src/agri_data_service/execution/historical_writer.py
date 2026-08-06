"""Transactional local persistence for validated NASA POWER historical source cells."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from geoalchemy2 import WKTElement
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from agri_data_service.execution.historical_backfill import (
    AnalysisGridCell,
    HistoricalNasaBackfillPlan,
    HistoricalNasaCheckpoint,
    NasaPowerDailyResult,
    historical_nasa_plan_checksum,
    historical_nasa_release_manifest,
    nasa_power_daily_url,
    require_complete_nasa_result,
)
from agri_data_service.execution.historical_era5 import (
    ERA5_LAND_DAILY_SCHEMA_VERSION,
    Era5LandMonthlyResult,
    Era5LandPeriod,
    HistoricalEra5Checkpoint,
    HistoricalEra5LandBackfillPlan,
    historical_era5_plan_checksum,
    historical_era5_release_manifest,
    require_complete_era5_result,
)
from agri_data_service.execution.historical_open_meteo import (
    OPEN_METEO_ARCHIVE_SCHEMA_VERSION,
    HistoricalOpenMeteoArchivePlan,
    HistoricalOpenMeteoCheckpoint,
    OpenMeteoArchiveChunk,
    OpenMeteoArchiveChunkResult,
    historical_open_meteo_plan_checksum,
    historical_open_meteo_release_manifest,
    open_meteo_archive_chunk_url,
    require_accounted_open_meteo_result,
)
from agri_data_service.execution.historical_usdm import (
    HistoricalUsdmBackfillPlan,
    HistoricalUsdmCheckpoint,
    UsdmShapefileResult,
    historical_usdm_plan_checksum,
    historical_usdm_release_manifest,
    require_complete_usdm_result,
    usdm_shapefile_url,
)
from agri_data_service.models.historical import (
    CellSourceCrosswalk,
    DroughtPolygonSnapshot,
    SignalCoverageAudit,
    SignalObservation,
    SourceCoverageAudit,
    SpatialCell,
)
from agri_data_service.models.provenance import (
    Artifact,
    DataSource,
    ReleaseSet,
    ReleaseSetItem,
    ReleaseSetState,
    ReleaseValidationState,
    SourceRelease,
    SourceReviewState,
)

WGS84_SRID = 4326
HISTORICAL_NASA_OBSERVATION_INSERT_BATCH_SIZE = 1_000
HISTORICAL_SIGNAL_INSERT_BATCH_SIZE = 1_000

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.execution.source_ingestion import SourceDefinition


@dataclass(frozen=True)
class HistoricalNasaWriteResult:
    """Identifiers and row counts from one persisted NASA POWER source cell."""

    source_release_id: uuid.UUID
    cell_id: uuid.UUID
    artifact_id: uuid.UUID
    observation_count: int
    coverage_count: int
    idempotent: bool


@dataclass(frozen=True)
class HistoricalReleaseSetResult:
    """One finalized immutable release set covering every checkpoint receipt."""

    release_set_id: uuid.UUID
    manifest_checksum: str
    source_release_count: int
    idempotent: bool


@dataclass(frozen=True)
class HistoricalUsdmWriteResult:
    """Identifiers and row counts from one persisted USDM weekly vector release."""

    source_release_id: uuid.UUID
    artifact_id: uuid.UUID
    polygon_count: int
    idempotent: bool


@dataclass(frozen=True)
class HistoricalEra5WriteResult:
    """Identifiers and row counts from one persisted ERA5-Land monthly source release."""

    source_release_id: uuid.UUID
    artifact_id: uuid.UUID
    observation_count: int
    coverage_count: int
    crosswalk_count: int
    idempotent: bool


@dataclass(frozen=True)
class HistoricalOpenMeteoWriteResult:
    """Identifiers and row counts from one persisted Open-Meteo ERA5-Land archive chunk."""

    source_release_id: uuid.UUID
    artifact_id: uuid.UUID
    observation_count: int
    observed_value_count: int
    coverage_count: int
    no_data_series_count: int
    crosswalk_count: int
    idempotent: bool


async def persist_nasa_power_cell(
    session: AsyncSession,
    *,
    plan: HistoricalNasaBackfillPlan,
    result: NasaPowerDailyResult,
) -> HistoricalNasaWriteResult:
    """Persist one complete source cell inside the caller-owned transaction without committing."""
    require_complete_nasa_result(plan.nasa, result)
    cell = _plan_cell(plan, result.cell_key)
    await _advisory_lock(session, f"historical-nasa:{historical_nasa_plan_checksum(plan)}:{cell.cell_key}")
    source = await _ensure_data_source(
        session,
        plan.source,
        configuration={"ingestion_boundary": "local_historical_backfill", "source_kind": "nasa_power_daily"},
    )
    spatial_cell, spatial_idempotent = await _ensure_spatial_cell(session, plan, cell)
    source_release, release_idempotent = await _ensure_source_release(session, plan, source, cell, result)
    artifact, artifact_idempotent = await _ensure_artifact(session, plan, source_release, cell, result)
    await _ensure_cell_crosswalk(session, source_release, spatial_cell, cell, plan)
    await _insert_observations(session, source_release, spatial_cell, result)
    await _insert_coverage(session, source_release, spatial_cell, result)
    await _verify_persisted_cell(session, source_release, result)
    return HistoricalNasaWriteResult(
        source_release_id=source_release.id,
        cell_id=spatial_cell.id,
        artifact_id=artifact.id,
        observation_count=len(result.observations),
        coverage_count=len(result.coverage),
        idempotent=spatial_idempotent and release_idempotent and artifact_idempotent,
    )


async def persist_usdm_shapefile(
    session: AsyncSession,
    *,
    plan: HistoricalUsdmBackfillPlan,
    result: UsdmShapefileResult,
) -> HistoricalUsdmWriteResult:
    """Persist one complete USDM weekly source package in the caller-owned transaction."""
    require_complete_usdm_result(plan, result)
    await _advisory_lock(session, f"historical-usdm:{historical_usdm_plan_checksum(plan)}:{result.issue_date:%Y%m%d}")
    source = await _ensure_data_source(
        session,
        plan.source,
        configuration={
            "ingestion_boundary": "local_historical_backfill",
            "native_product_scope": plan.native_product_scope,
            "source_kind": "usdm_weekly_vector",
        },
    )
    source_release, release_idempotent = await _ensure_usdm_source_release(session, plan, source, result)
    artifact, artifact_idempotent = await _ensure_usdm_artifact(session, plan, source_release, result)
    await _insert_usdm_polygons(session, source_release, result)
    await _insert_usdm_coverage(session, plan, source_release, result)
    await _verify_persisted_usdm_release(session, source_release, result)
    return HistoricalUsdmWriteResult(
        source_release_id=source_release.id,
        artifact_id=artifact.id,
        polygon_count=len(result.polygons),
        idempotent=release_idempotent and artifact_idempotent,
    )


async def persist_era5_land_month(
    session: AsyncSession,
    *,
    plan: HistoricalEra5LandBackfillPlan,
    result: Era5LandMonthlyResult,
) -> HistoricalEra5WriteResult:
    """Persist one complete cache-backed ERA5 month without retaining its raw ZIP in PostgreSQL."""
    require_complete_era5_result(plan, result)
    period = _era5_period(plan, result.period_key)
    await _advisory_lock(session, f"historical-era5:{historical_era5_plan_checksum(plan)}:{period.key}")
    source = await _ensure_data_source(
        session,
        plan.source,
        configuration={
            "ingestion_boundary": "local_historical_backfill",
            "source_kind": "era5_land_daily",
            "requested_grid_degrees": str(plan.requested_grid_degrees),
            "native_grid_resolution_m": str(plan.native_grid_resolution_m),
        },
    )
    spatial_cells = await _require_era5_spatial_cells(session, plan)
    source_release, release_idempotent = await _ensure_era5_source_release(session, plan, source, period, result)
    artifact, artifact_idempotent = await _ensure_era5_artifact(session, plan, source_release, period, result)
    await _insert_era5_crosswalks(session, source_release, plan, spatial_cells)
    await _insert_era5_observations(session, source_release, spatial_cells, result)
    await _insert_era5_coverage(session, source_release, spatial_cells, result)
    await _verify_persisted_era5_release(session, source_release, plan, result)
    return HistoricalEra5WriteResult(
        source_release_id=source_release.id,
        artifact_id=artifact.id,
        observation_count=len(result.observations),
        coverage_count=len(result.coverage),
        crosswalk_count=len(plan.cells),
        idempotent=release_idempotent and artifact_idempotent,
    )


async def persist_open_meteo_archive_chunk(
    session: AsyncSession,
    *,
    plan: HistoricalOpenMeteoArchivePlan,
    result: OpenMeteoArchiveChunkResult,
) -> HistoricalOpenMeteoWriteResult:
    """Persist one accounted-for Open-Meteo ERA5-Land archive chunk against the existing analysis lattice."""
    require_accounted_open_meteo_result(plan, result)
    chunk = _open_meteo_chunk(plan, result.chunk_key)
    await _advisory_lock(session, f"historical-open-meteo:{historical_open_meteo_plan_checksum(plan)}:{chunk.key}")
    source = await _ensure_data_source(
        session,
        plan.source,
        configuration={
            "ingestion_boundary": "local_historical_backfill",
            "source_kind": "open_meteo_era5_land_archive_daily",
            "provider_role": "intermediary_redistributor",
            "upstream_product": "ECMWF ERA5-Land via Copernicus Climate Change Service",
            "model": plan.model,
            "native_grid_degrees": str(plan.native_grid_degrees),
            "native_grid_resolution_m": str(plan.native_grid_resolution_m),
        },
    )
    spatial_cells = await _require_open_meteo_spatial_cells(session, plan, chunk)
    source_release, release_idempotent = await _ensure_open_meteo_source_release(session, plan, source, chunk, result)
    artifact, artifact_idempotent = await _ensure_open_meteo_artifact(session, plan, source_release, chunk, result)
    await _insert_open_meteo_crosswalks(session, source_release, plan, spatial_cells, result)
    await _insert_open_meteo_observations(session, source_release, plan, spatial_cells, result)
    await _insert_open_meteo_coverage(session, source_release, plan, spatial_cells, result)
    await _verify_persisted_open_meteo_release(session, source_release, chunk, result)
    return HistoricalOpenMeteoWriteResult(
        source_release_id=source_release.id,
        artifact_id=artifact.id,
        observation_count=len(result.observations),
        observed_value_count=sum(1 for item in result.observations if item.is_observed),
        coverage_count=len(result.coverage),
        no_data_series_count=sum(1 for item in result.coverage if item.status == "no_data"),
        crosswalk_count=len(chunk.cells),
        idempotent=release_idempotent and artifact_idempotent,
    )


async def finalize_nasa_release_set(
    session: AsyncSession,
    *,
    plan: HistoricalNasaBackfillPlan,
    checkpoint: HistoricalNasaCheckpoint,
    validated_at: datetime | None = None,
) -> HistoricalReleaseSetResult:
    """Atomically validate the release set only after every reviewed source cell is durable."""
    manifest_checksum = historical_nasa_release_manifest(plan, checkpoint)
    if any(receipt.retrieved_at > plan.release_set_as_of for receipt in checkpoint.receipts):
        raise ValueError("release_set_as_of must not precede a persisted source receipt")
    await _advisory_lock(session, f"historical-release-set:{plan.release_set_key}")
    source = await _find_active_source(session, plan.source.key)
    source_releases = await _required_source_releases(session, plan, checkpoint, source.id)
    expected_ids = {release.id for release in source_releases}
    return await _finalize_release_set(
        session,
        release_set_key=plan.release_set_key,
        as_of_time=plan.release_set_as_of,
        description=plan.description,
        manifest_checksum=manifest_checksum,
        expected_ids=expected_ids,
        validated_at=validated_at,
    )


async def finalize_era5_release_set(
    session: AsyncSession,
    *,
    plan: HistoricalEra5LandBackfillPlan,
    checkpoint: HistoricalEra5Checkpoint,
    validated_at: datetime | None = None,
) -> HistoricalReleaseSetResult:
    """Atomically validate ERA5-Land membership only after every monthly ZIP is durable."""
    manifest_checksum = historical_era5_release_manifest(plan, checkpoint)
    if any(receipt.retrieved_at > plan.release_set_as_of for receipt in checkpoint.receipts):
        raise ValueError("release_set_as_of must not precede a persisted source receipt")
    await _advisory_lock(session, f"historical-release-set:{plan.release_set_key}")
    source = await _find_active_source(session, plan.source.key)
    source_releases = await _required_era5_source_releases(session, plan, checkpoint, source.id)
    expected_ids = {release.id for release in source_releases}
    return await _finalize_release_set(
        session,
        release_set_key=plan.release_set_key,
        as_of_time=plan.release_set_as_of,
        description=plan.description,
        manifest_checksum=manifest_checksum,
        expected_ids=expected_ids,
        validated_at=validated_at,
    )


async def finalize_usdm_release_set(
    session: AsyncSession,
    *,
    plan: HistoricalUsdmBackfillPlan,
    checkpoint: HistoricalUsdmCheckpoint,
    validated_at: datetime | None = None,
) -> HistoricalReleaseSetResult:
    """Atomically validate USDM membership only after every planned weekly receipt is durable."""
    manifest_checksum = historical_usdm_release_manifest(plan, checkpoint)
    if any(receipt.retrieved_at > plan.release_set_as_of for receipt in checkpoint.receipts):
        raise ValueError("release_set_as_of must not precede a persisted source receipt")
    await _advisory_lock(session, f"historical-release-set:{plan.release_set_key}")
    source = await _find_active_source(session, plan.source.key)
    source_releases = await _required_usdm_source_releases(session, plan, checkpoint, source.id)
    expected_ids = {release.id for release in source_releases}
    return await _finalize_release_set(
        session,
        release_set_key=plan.release_set_key,
        as_of_time=plan.release_set_as_of,
        description=plan.description,
        manifest_checksum=manifest_checksum,
        expected_ids=expected_ids,
        validated_at=validated_at,
    )


async def finalize_open_meteo_release_set(
    session: AsyncSession,
    *,
    plan: HistoricalOpenMeteoArchivePlan,
    checkpoint: HistoricalOpenMeteoCheckpoint,
    validated_at: datetime | None = None,
) -> HistoricalReleaseSetResult:
    """Atomically validate Open-Meteo archive membership only after every planned chunk is durable."""
    manifest_checksum = historical_open_meteo_release_manifest(plan, checkpoint)
    if any(receipt.retrieved_at > plan.release_set_as_of for receipt in checkpoint.receipts):
        raise ValueError("release_set_as_of must not precede a persisted source receipt")
    await _advisory_lock(session, f"historical-release-set:{plan.release_set_key}")
    source = await _find_active_source(session, plan.source.key)
    source_releases = await _required_open_meteo_source_releases(session, plan, checkpoint, source.id)
    expected_ids = {release.id for release in source_releases}
    return await _finalize_release_set(
        session,
        release_set_key=plan.release_set_key,
        as_of_time=plan.release_set_as_of,
        description=plan.description,
        manifest_checksum=manifest_checksum,
        expected_ids=expected_ids,
        validated_at=validated_at,
    )


async def _finalize_release_set(
    session: AsyncSession,
    *,
    release_set_key: str,
    as_of_time: datetime,
    description: str | None,
    manifest_checksum: str,
    expected_ids: set[uuid.UUID],
    validated_at: datetime | None,
) -> HistoricalReleaseSetResult:
    """Atomically validate one release set's membership; the warehouse trigger freezes it afterwards."""
    existing = (
        (
            await session.execute(
                select(ReleaseSet).where(
                    (ReleaseSet.logical_key == release_set_key)
                    | (ReleaseSet.manifest_checksum == manifest_checksum)
                )
            )
        )
        .scalars()
        .all()
    )
    if len(existing) > 1:
        raise ValueError("release set key and manifest checksum identify different historical release sets")
    release_set = existing[0] if existing else None
    if release_set is None:
        release_set = ReleaseSet(
            logical_key=release_set_key,
            as_of_time=as_of_time,
            manifest_checksum=manifest_checksum,
            state=ReleaseSetState.DRAFT,
            description=description,
        )
        session.add(release_set)
        await session.flush()
        idempotent = False
    elif (
        release_set.logical_key != release_set_key
        or release_set.manifest_checksum != manifest_checksum
        or release_set.as_of_time != as_of_time
        or release_set.description != description
    ):
        raise ValueError("historical release set identity is already governed by different content")
    elif release_set.state in {ReleaseSetState.VALIDATED, ReleaseSetState.PUBLISHED}:
        members = await _release_set_member_ids(session, release_set.id)
        if members != expected_ids:
            raise ValueError("finalized historical release set has different source membership")
        return HistoricalReleaseSetResult(
            release_set_id=release_set.id,
            manifest_checksum=manifest_checksum,
            source_release_count=len(expected_ids),
            idempotent=True,
        )
    elif release_set.state != ReleaseSetState.DRAFT:
        raise ValueError("historical release set is not in a mutable draft state")
    else:
        idempotent = False

    existing_members = await _release_set_member_ids(session, release_set.id)
    unexpected_members = existing_members.difference(expected_ids)
    if unexpected_members:
        raise ValueError("historical draft release set contains unexpected source releases")
    for source_release_id in sorted(expected_ids.difference(existing_members), key=str):
        session.add(ReleaseSetItem(release_set_id=release_set.id, source_release_id=source_release_id))
    await session.flush()
    members = await _release_set_member_ids(session, release_set.id)
    if members != expected_ids:
        raise ValueError("historical release set did not retain complete source membership")
    release_set.state = ReleaseSetState.VALIDATED
    release_set.validated_at = _utc_now_or_value(validated_at)
    await session.flush()
    return HistoricalReleaseSetResult(
        release_set_id=release_set.id,
        manifest_checksum=manifest_checksum,
        source_release_count=len(expected_ids),
        idempotent=idempotent,
    )


async def _ensure_data_source(
    session: AsyncSession,
    source_definition: SourceDefinition,
    *,
    configuration: dict[str, str],
) -> DataSource:
    source = await _find_source(session, source_definition.key)
    if source is None:
        source = DataSource(
            key=source_definition.key,
            name=source_definition.name,
            owner=source_definition.owner,
            purpose=source_definition.purpose,
            base_url=source_definition.base_url,
            license_name=source_definition.license_name,
            license_url=source_definition.license_url,
            citation=source_definition.citation,
            retention_days=source_definition.retention_days,
            allowed_client_exposure=False,
            review_state=SourceReviewState.APPROVED,
            reviewed_at=source_definition.reviewed_at,
            reviewed_by=source_definition.reviewed_by,
            configuration=configuration,
        )
        session.add(source)
        await session.flush()
        return source
    if source.review_state != SourceReviewState.APPROVED or not source.is_active:
        raise ValueError("historical source is not approved and active")
    expected = {
        "name": source_definition.name,
        "owner": source_definition.owner,
        "purpose": source_definition.purpose,
        "base_url": source_definition.base_url,
        "license_name": source_definition.license_name,
        "license_url": source_definition.license_url,
        "citation": source_definition.citation,
        "retention_days": source_definition.retention_days,
        "reviewed_at": source_definition.reviewed_at,
        "reviewed_by": source_definition.reviewed_by,
        "configuration": configuration,
    }
    if any(getattr(source, field) != value for field, value in expected.items()):
        raise ValueError("historical source key is already governed by different metadata")
    return source


async def _ensure_spatial_cell(
    session: AsyncSession,
    plan: HistoricalNasaBackfillPlan,
    cell: AnalysisGridCell,
) -> tuple[SpatialCell, bool]:
    geometry = WKTElement(_cell_polygon_wkt(cell, plan.nasa.cell_half_span_degrees), srid=4326)
    centroid = WKTElement(_point_wkt(cell), srid=4326)
    existing = (
        await session.execute(select(SpatialCell).where(SpatialCell.cell_key == cell.cell_key))
    ).scalar_one_or_none()
    if existing is None:
        spatial_cell = SpatialCell(
            cell_key=cell.cell_key,
            grid_name=plan.nasa.grid_name,
            resolution_m=plan.nasa.grid_resolution_m,
            geometry=geometry,
            centroid=centroid,
            coverage_fraction=1,
        )
        session.add(spatial_cell)
        await session.flush()
        return spatial_cell, False
    if existing.grid_name != plan.nasa.grid_name or existing.resolution_m != plan.nasa.grid_resolution_m:
        raise ValueError("historical spatial cell key is already governed by a different grid")
    geometry_matches = (
        await session.execute(
            select(func.ST_Equals(SpatialCell.geometry, geometry)).where(SpatialCell.id == existing.id)
        )
    ).scalar_one()
    centroid_matches = (
        await session.execute(
            select(func.ST_Equals(SpatialCell.centroid, centroid)).where(SpatialCell.id == existing.id)
        )
    ).scalar_one()
    if not geometry_matches or not centroid_matches:
        raise ValueError("historical spatial cell key is already governed by different geometry")
    return existing, True


async def _ensure_source_release(
    session: AsyncSession,
    plan: HistoricalNasaBackfillPlan,
    source: DataSource,
    cell: AnalysisGridCell,
    result: NasaPowerDailyResult,
) -> tuple[SourceRelease, bool]:
    source_version = _source_version(plan, cell)
    query_parameters = {
        "request_url": str(nasa_power_daily_url(plan.nasa, cell)),
        "cell_key": cell.cell_key,
        "grid_name": plan.nasa.grid_name,
        "time_standard": plan.nasa.time_standard,
    }
    quality_summary = {
        "expected_observation_count": len(result.observations),
        "coverage_count": len(result.coverage),
        "coverage_status": "complete",
    }
    existing = (
        await session.execute(
            select(SourceRelease).where(
                SourceRelease.data_source_id == source.id,
                SourceRelease.source_version == source_version,
                SourceRelease.payload_checksum == result.payload_checksum,
                SourceRelease.transform_version == plan.transform_version,
            )
        )
    ).scalar_one_or_none()
    observed_from = datetime.combine(plan.nasa.window.start_date, datetime.min.time(), tzinfo=UTC)
    observed_to = datetime.combine(plan.nasa.window.end_date, datetime.max.time(), tzinfo=UTC)
    if existing is None:
        release = SourceRelease(
            data_source_id=source.id,
            source_version=source_version,
            retrieved_at=result.retrieved_at,
            data_available_at=result.retrieved_at,
            observed_from=observed_from,
            observed_to=observed_to,
            payload_checksum=result.payload_checksum,
            payload_bytes=len(result.payload),
            schema_version=plan.nasa.schema_version,
            transform_version=plan.transform_version,
            license_snapshot=plan.source.license_name,
            query_parameters=query_parameters,
            quality_summary=quality_summary,
            validation_state=ReleaseValidationState.VALID,
            validated_at=result.retrieved_at,
        )
        session.add(release)
        await session.flush()
        return release, False
    expected = {
        "observed_from": observed_from,
        "observed_to": observed_to,
        "payload_bytes": len(result.payload),
        "schema_version": plan.nasa.schema_version,
        "transform_version": plan.transform_version,
        "license_snapshot": plan.source.license_name,
        "query_parameters": query_parameters,
        "quality_summary": quality_summary,
        "validation_state": ReleaseValidationState.VALID,
    }
    if any(getattr(existing, field) != value for field, value in expected.items()):
        raise ValueError("historical source release identity is already governed by different metadata")
    if existing.validated_at is None:
        raise ValueError("historical source release is valid without a validation timestamp")
    return existing, True


async def _ensure_usdm_source_release(
    session: AsyncSession,
    plan: HistoricalUsdmBackfillPlan,
    source: DataSource,
    result: UsdmShapefileResult,
) -> tuple[SourceRelease, bool]:
    source_version = _usdm_source_version(result.issue_date)
    observed_from = datetime.combine(result.issue_date, datetime.min.time(), tzinfo=UTC)
    observed_to = datetime.combine(result.issue_date, datetime.max.time(), tzinfo=UTC)
    query_parameters = {
        "request_url": str(usdm_shapefile_url(result.issue_date)),
        "issue_date": result.issue_date.isoformat(),
        "native_product_scope": plan.native_product_scope,
    }
    quality_summary = {
        "expected_feature_count": result.declared_feature_count,
        "received_feature_count": len(result.polygons),
        "coverage_status": "complete",
        "present_severity_classes": sorted({polygon.severity_class for polygon in result.polygons}),
    }
    existing = (
        await session.execute(
            select(SourceRelease).where(
                SourceRelease.data_source_id == source.id,
                SourceRelease.source_version == source_version,
                SourceRelease.payload_checksum == result.payload_checksum,
                SourceRelease.transform_version == plan.transform_version,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        release = SourceRelease(
            data_source_id=source.id,
            source_version=source_version,
            retrieved_at=result.retrieved_at,
            data_available_at=result.retrieved_at,
            observed_from=observed_from,
            observed_to=observed_to,
            payload_checksum=result.payload_checksum,
            payload_bytes=len(result.payload),
            schema_version="usdm-shapefile-v1",
            transform_version=plan.transform_version,
            license_snapshot=plan.source.license_name,
            query_parameters=query_parameters,
            quality_summary=quality_summary,
            validation_state=ReleaseValidationState.VALID,
            validated_at=result.retrieved_at,
        )
        session.add(release)
        await session.flush()
        return release, False
    expected = {
        "observed_from": observed_from,
        "observed_to": observed_to,
        "payload_bytes": len(result.payload),
        "schema_version": "usdm-shapefile-v1",
        "transform_version": plan.transform_version,
        "license_snapshot": plan.source.license_name,
        "query_parameters": query_parameters,
        "quality_summary": quality_summary,
        "validation_state": ReleaseValidationState.VALID,
    }
    if any(getattr(existing, field) != value for field, value in expected.items()):
        raise ValueError("historical USDM source release identity is already governed by different metadata")
    if existing.validated_at is None:
        raise ValueError("historical USDM source release is valid without a validation timestamp")
    return existing, True


async def _ensure_era5_source_release(
    session: AsyncSession,
    plan: HistoricalEra5LandBackfillPlan,
    source: DataSource,
    period: Era5LandPeriod,
    result: Era5LandMonthlyResult,
) -> tuple[SourceRelease, bool]:
    observed_from = datetime.combine(period.start_date, datetime.min.time(), tzinfo=UTC)
    observed_to = datetime.combine(period.end_date, datetime.max.time(), tzinfo=UTC)
    query_parameters = {
        "dataset": plan.dataset,
        "year": period.year,
        "month": period.month,
        "days": period.days,
        "parameters": plan.parameters,
        "daily_statistic": plan.daily_statistic,
        "frequency": plan.frequency,
        "time_zone": plan.time_zone,
        "requested_area": plan.requested_area.cds_value,
        "requested_grid_degrees": plan.requested_grid_degrees,
    }
    quality_summary = {
        "expected_observation_count": len(result.observations),
        "coverage_count": len(result.coverage),
        "coverage_status": "complete",
        "spatial_support_kind": "point_sample",
    }
    source_version = _era5_source_version(period)
    existing = (
        await session.execute(
            select(SourceRelease).where(
                SourceRelease.data_source_id == source.id,
                SourceRelease.source_version == source_version,
                SourceRelease.payload_checksum == result.payload_checksum,
                SourceRelease.transform_version == plan.transform_version,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        release = SourceRelease(
            data_source_id=source.id,
            source_version=source_version,
            retrieved_at=result.retrieved_at,
            data_available_at=result.retrieved_at,
            observed_from=observed_from,
            observed_to=observed_to,
            payload_checksum=result.payload_checksum,
            payload_bytes=len(result.payload),
            schema_version=ERA5_LAND_DAILY_SCHEMA_VERSION,
            transform_version=plan.transform_version,
            license_snapshot=plan.source.license_name,
            query_parameters=query_parameters,
            quality_summary=quality_summary,
            validation_state=ReleaseValidationState.VALID,
            validated_at=result.retrieved_at,
        )
        session.add(release)
        await session.flush()
        return release, False
    expected = {
        "observed_from": observed_from,
        "observed_to": observed_to,
        "payload_bytes": len(result.payload),
        "schema_version": ERA5_LAND_DAILY_SCHEMA_VERSION,
        "transform_version": plan.transform_version,
        "license_snapshot": plan.source.license_name,
        "query_parameters": query_parameters,
        "quality_summary": quality_summary,
        "validation_state": ReleaseValidationState.VALID,
    }
    if any(getattr(existing, field) != value for field, value in expected.items()):
        raise ValueError("historical ERA5 source release identity is already governed by different metadata")
    if existing.validated_at is None:
        raise ValueError("historical ERA5 source release is valid without a validation timestamp")
    return existing, True


async def _ensure_artifact(
    session: AsyncSession,
    plan: HistoricalNasaBackfillPlan,
    source_release: SourceRelease,
    cell: AnalysisGridCell,
    result: NasaPowerDailyResult,
) -> tuple[Artifact, bool]:
    uri = (
        "warehouse://historical-source/nasa-power-daily/"
        f"{plan.nasa.window.start_date:%Y%m%d}-{plan.nasa.window.end_date:%Y%m%d}/"
        f"{plan.transform_version}/{cell.cell_key}/{result.payload_checksum}.json"
    )
    metadata = {
        "cell_key": cell.cell_key,
        "plan_checksum": historical_nasa_plan_checksum(plan),
        "transform_version": plan.transform_version,
    }
    existing = (
        await session.execute(
            select(Artifact).where(Artifact.uri == uri, Artifact.checksum_sha256 == result.payload_checksum)
        )
    ).scalar_one_or_none()
    if existing is None:
        artifact = Artifact(
            source_release_id=source_release.id,
            kind="source_nasa_power_daily_json",
            uri=uri,
            media_type="application/json",
            checksum_sha256=result.payload_checksum,
            size_bytes=len(result.payload),
            storage_class="database_inline",
            metadata_json=metadata,
            content_bytes=result.payload,
        )
        session.add(artifact)
        await session.flush()
        return artifact, False
    if (
        existing.source_release_id != source_release.id
        or existing.kind != "source_nasa_power_daily_json"
        or existing.media_type != "application/json"
        or existing.size_bytes != len(result.payload)
        or existing.storage_class != "database_inline"
        or existing.metadata_json != metadata
        or existing.content_bytes != result.payload
    ):
        raise ValueError("historical source artifact identity is already governed by different content")
    return existing, True


async def _ensure_usdm_artifact(
    session: AsyncSession,
    plan: HistoricalUsdmBackfillPlan,
    source_release: SourceRelease,
    result: UsdmShapefileResult,
) -> tuple[Artifact, bool]:
    uri = (
        "warehouse://historical-source/usdm-weekly/"
        f"{result.issue_date:%Y%m%d}/{plan.transform_version}/{result.payload_checksum}.zip"
    )
    metadata = {
        "issue_date": result.issue_date.isoformat(),
        "plan_checksum": historical_usdm_plan_checksum(plan),
        "native_product_scope": plan.native_product_scope,
        "transform_version": plan.transform_version,
    }
    existing = (
        await session.execute(
            select(Artifact).where(Artifact.uri == uri, Artifact.checksum_sha256 == result.payload_checksum)
        )
    ).scalar_one_or_none()
    if existing is None:
        artifact = Artifact(
            source_release_id=source_release.id,
            kind="source_usdm_weekly_shapefile_zip",
            uri=uri,
            media_type="application/zip",
            checksum_sha256=result.payload_checksum,
            size_bytes=len(result.payload),
            storage_class="database_inline",
            metadata_json=metadata,
            content_bytes=result.payload,
        )
        session.add(artifact)
        await session.flush()
        return artifact, False
    if (
        existing.source_release_id != source_release.id
        or existing.kind != "source_usdm_weekly_shapefile_zip"
        or existing.media_type != "application/zip"
        or existing.size_bytes != len(result.payload)
        or existing.storage_class != "database_inline"
        or existing.metadata_json != metadata
        or existing.content_bytes != result.payload
    ):
        raise ValueError("historical USDM source artifact identity is already governed by different content")
    return existing, True


async def _ensure_era5_artifact(
    session: AsyncSession,
    plan: HistoricalEra5LandBackfillPlan,
    source_release: SourceRelease,
    period: Era5LandPeriod,
    result: Era5LandMonthlyResult,
) -> tuple[Artifact, bool]:
    uri = f"warehouse://historical-source/era5-land/{period.key}/{plan.transform_version}/{result.payload_checksum}.zip"
    metadata = {
        "period_key": period.key,
        "plan_checksum": historical_era5_plan_checksum(plan),
        "raw_cache_required": True,
        "transform_version": plan.transform_version,
    }
    existing = (
        await session.execute(
            select(Artifact).where(Artifact.uri == uri, Artifact.checksum_sha256 == result.payload_checksum)
        )
    ).scalar_one_or_none()
    if existing is None:
        artifact = Artifact(
            source_release_id=source_release.id,
            kind="source_era5_land_daily_netcdf_zip",
            uri=uri,
            media_type="application/zip",
            checksum_sha256=result.payload_checksum,
            size_bytes=len(result.payload),
            storage_class="local_raw_cache",
            metadata_json=metadata,
            content_bytes=None,
        )
        session.add(artifact)
        await session.flush()
        return artifact, False
    if (
        existing.source_release_id != source_release.id
        or existing.kind != "source_era5_land_daily_netcdf_zip"
        or existing.media_type != "application/zip"
        or existing.size_bytes != len(result.payload)
        or existing.storage_class != "local_raw_cache"
        or existing.metadata_json != metadata
        or existing.content_bytes is not None
    ):
        raise ValueError("historical ERA5 source artifact identity is already governed by different content")
    return existing, True


async def _ensure_open_meteo_source_release(
    session: AsyncSession,
    plan: HistoricalOpenMeteoArchivePlan,
    source: DataSource,
    chunk: OpenMeteoArchiveChunk,
    result: OpenMeteoArchiveChunkResult,
) -> tuple[SourceRelease, bool]:
    observed_from = datetime.combine(plan.window.start_date, datetime.min.time(), tzinfo=UTC)
    observed_to = datetime.combine(plan.window.end_date, datetime.max.time(), tzinfo=UTC)
    query_parameters = {
        "request_url": open_meteo_archive_chunk_url(plan, chunk),
        "model": plan.model,
        "cell_selection": plan.cell_selection,
        "time_zone": plan.time_zone,
        "parameters": plan.parameters,
        "cell_keys": [cell.cell_key for cell in chunk.cells],
    }
    quality_summary = {
        "requested_series_count": len(chunk.cells) * len(plan.parameters),
        "expected_daily_rows": len(result.observations),
        "observed_value_count": sum(1 for item in result.observations if item.is_observed),
        "coverage_count": len(result.coverage),
        "no_data_series_count": sum(1 for item in result.coverage if item.status == "no_data"),
        "partial_series_count": sum(1 for item in result.coverage if item.status == "partial"),
        "spatial_support_kind": "point_sample",
        # The warehouse stores a canonical document with the provider's per-request timing metric
        # removed; this is the digest of the exact bytes that came off the wire.
        "wire_payload_checksum": result.wire_payload_checksum,
        "wire_payload_bytes": result.wire_payload_bytes,
        "provider_role": "intermediary_redistributor",
    }
    source_version = _open_meteo_source_version(plan, chunk)
    existing = (
        await session.execute(
            select(SourceRelease).where(
                SourceRelease.data_source_id == source.id,
                SourceRelease.source_version == source_version,
                SourceRelease.payload_checksum == result.payload_checksum,
                SourceRelease.transform_version == plan.transform_version,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        release = SourceRelease(
            data_source_id=source.id,
            source_version=source_version,
            retrieved_at=result.retrieved_at,
            data_available_at=result.retrieved_at,
            observed_from=observed_from,
            observed_to=observed_to,
            payload_checksum=result.payload_checksum,
            payload_bytes=len(result.payload),
            schema_version=OPEN_METEO_ARCHIVE_SCHEMA_VERSION,
            transform_version=plan.transform_version,
            license_snapshot=plan.source.license_name,
            query_parameters=query_parameters,
            quality_summary=quality_summary,
            validation_state=ReleaseValidationState.VALID,
            validated_at=result.retrieved_at,
        )
        session.add(release)
        await session.flush()
        return release, False
    expected = {
        "observed_from": observed_from,
        "observed_to": observed_to,
        "payload_bytes": len(result.payload),
        "schema_version": OPEN_METEO_ARCHIVE_SCHEMA_VERSION,
        "transform_version": plan.transform_version,
        "license_snapshot": plan.source.license_name,
        "query_parameters": query_parameters,
        "quality_summary": quality_summary,
        "validation_state": ReleaseValidationState.VALID,
    }
    if any(getattr(existing, field) != value for field, value in expected.items()):
        raise ValueError("historical Open-Meteo source release identity is already governed by different metadata")
    if existing.validated_at is None:
        raise ValueError("historical Open-Meteo source release is valid without a validation timestamp")
    return existing, True


async def _ensure_open_meteo_artifact(
    session: AsyncSession,
    plan: HistoricalOpenMeteoArchivePlan,
    source_release: SourceRelease,
    chunk: OpenMeteoArchiveChunk,
    result: OpenMeteoArchiveChunkResult,
) -> tuple[Artifact, bool]:
    uri = (
        f"warehouse://historical-source/open-meteo-era5-land-archive/{chunk.key}/"
        f"{plan.transform_version}/{result.payload_checksum}.json"
    )
    metadata = {
        "chunk_key": chunk.key,
        "plan_checksum": historical_open_meteo_plan_checksum(plan),
        "transform_version": plan.transform_version,
        "canonicalization": "generationtime_ms removed; keys sorted; UTF-8 compact separators",
        "wire_payload_checksum": result.wire_payload_checksum,
        "wire_payload_bytes": result.wire_payload_bytes,
    }
    existing = (
        await session.execute(
            select(Artifact).where(Artifact.uri == uri, Artifact.checksum_sha256 == result.payload_checksum)
        )
    ).scalar_one_or_none()
    if existing is None:
        artifact = Artifact(
            source_release_id=source_release.id,
            kind="source_open_meteo_era5_land_archive_daily_json",
            uri=uri,
            media_type="application/json",
            checksum_sha256=result.payload_checksum,
            size_bytes=len(result.payload),
            storage_class="database_inline",
            metadata_json=metadata,
            content_bytes=result.payload,
        )
        session.add(artifact)
        await session.flush()
        return artifact, False
    if (
        existing.source_release_id != source_release.id
        or existing.kind != "source_open_meteo_era5_land_archive_daily_json"
        or existing.media_type != "application/json"
        or existing.size_bytes != len(result.payload)
        or existing.storage_class != "database_inline"
        or existing.metadata_json != metadata
        or existing.content_bytes != result.payload
    ):
        raise ValueError("historical Open-Meteo source artifact identity is already governed by different content")
    return existing, True


async def _require_open_meteo_spatial_cells(
    session: AsyncSession,
    plan: HistoricalOpenMeteoArchivePlan,
    chunk: OpenMeteoArchiveChunk,
) -> dict[str, SpatialCell]:
    """Require the reviewed analysis lattice to already exist; this lane never mints spatial cells."""
    cell_keys = [cell.cell_key for cell in chunk.cells]
    existing = (await session.execute(select(SpatialCell).where(SpatialCell.cell_key.in_(cell_keys)))).scalars()
    cells = {cell.cell_key: cell for cell in existing}
    missing = [cell.cell_key for cell in chunk.cells if cell.cell_key not in cells]
    if missing:
        raise ValueError("Open-Meteo archive persistence requires every reviewed analysis cell in the warehouse")
    wrong_grid = sorted(key for key, cell in cells.items() if cell.grid_name != plan.grid_name)
    if wrong_grid:
        raise ValueError("Open-Meteo archive cell_key resolves to a spatial cell on a different analysis grid")
    return cells


async def _insert_open_meteo_crosswalks(
    session: AsyncSession,
    source_release: SourceRelease,
    plan: HistoricalOpenMeteoArchivePlan,
    spatial_cells: dict[str, SpatialCell],
    result: OpenMeteoArchiveChunkResult,
) -> None:
    """Record the native grid point the provider actually answered with, not the requested centroid."""
    rows: list[dict[str, object]] = []
    for cell_key, latitude, longitude, elevation in result.grid_points:
        rows.append(
            {
                "source_release_id": source_release.id,
                "cell_id": spatial_cells[cell_key].id,
                "native_feature_key": cell_key,
                "native_geometry": WKTElement(f"POINT({longitude:.8f} {latitude:.8f})", srid=WGS84_SRID),
                "native_resolution_m": plan.native_grid_resolution_m,
                "spatial_support_kind": "point_sample",
                "mapping_method": "provider_nearest_native_grid_point",
                "coverage_fraction": 1,
                "metadata_json": {
                    "native_grid_name": plan.native_grid_name,
                    "native_grid_degrees": plan.native_grid_degrees,
                    "provider_response_geometry": "point",
                    "provider_reported_elevation_m": elevation,
                    # The analysis cell is ~0.25 degrees; one native grid box is sampled inside it.
                    "analysis_cell_geometry": "sampling_area",
                    "native_resolution_is_context_only": True,
                },
            }
        )
        if len(rows) == HISTORICAL_SIGNAL_INSERT_BATCH_SIZE:
            await _insert_cell_crosswalk_batch(session, rows)
            rows = []
    if rows:
        await _insert_cell_crosswalk_batch(session, rows)


async def _insert_open_meteo_observations(
    session: AsyncSession,
    source_release: SourceRelease,
    plan: HistoricalOpenMeteoArchivePlan,
    spatial_cells: dict[str, SpatialCell],
    result: OpenMeteoArchiveChunkResult,
) -> None:
    """Insert one chunk's daily facts in fixed-size batches under this lane's own spatial support key."""
    rows: list[dict[str, object]] = []
    for observation in result.observations:
        rows.append(
            {
                "source_release_id": source_release.id,
                "cell_id": spatial_cells[observation.cell_key].id,
                "signal_name": observation.signal_name,
                "source_parameter": observation.source_parameter,
                "support_key": plan.support_key,
                "observed_at": observation.observed_at,
                "valid_from": observation.observed_at,
                "valid_to": observation.observed_at,
                "data_available_at": result.retrieved_at,
                "original_value": observation.original_value,
                "original_unit": observation.original_unit,
                "normalized_value": observation.normalized_value,
                "normalized_unit": observation.normalized_unit,
                "quality_flag": observation.quality_flag,
                "coverage_fraction": 1,
                "is_observed": observation.is_observed,
                "metadata_json": {
                    "source_parameter": observation.source_parameter,
                    "native_grid_name": plan.native_grid_name,
                },
            }
        )
        if len(rows) == HISTORICAL_SIGNAL_INSERT_BATCH_SIZE:
            await _insert_signal_observation_batch(session, rows)
            rows = []
    if rows:
        await _insert_signal_observation_batch(session, rows)


async def _insert_open_meteo_coverage(
    session: AsyncSession,
    source_release: SourceRelease,
    plan: HistoricalOpenMeteoArchivePlan,
    spatial_cells: dict[str, SpatialCell],
    result: OpenMeteoArchiveChunkResult,
) -> None:
    """Insert per-cell/signal coverage evidence, preserving `no_data` and `partial` as themselves."""
    rows: list[dict[str, object]] = []
    for coverage in result.coverage:
        rows.append(
            {
                "source_release_id": source_release.id,
                "cell_id": spatial_cells[coverage.cell_key].id,
                "signal_name": coverage.signal_name,
                "source_parameter": coverage.source_parameter,
                "support_key": plan.support_key,
                "window_start": coverage.window_start,
                "window_end": coverage.window_end,
                "expected_observation_count": coverage.expected_observation_count,
                "received_observation_count": coverage.received_observation_count,
                "status": coverage.status,
                "details": {
                    "source_parameter": coverage.source_parameter,
                    "no_data_means": "the provider modelled no value here; it is not a zero measurement",
                },
            }
        )
        if len(rows) == HISTORICAL_SIGNAL_INSERT_BATCH_SIZE:
            await _insert_signal_coverage_batch(session, rows)
            rows = []
    if rows:
        await _insert_signal_coverage_batch(session, rows)


async def _verify_persisted_open_meteo_release(
    session: AsyncSession,
    source_release: SourceRelease,
    chunk: OpenMeteoArchiveChunk,
    result: OpenMeteoArchiveChunkResult,
) -> None:
    """Prove the chunk retained every fact, audit, and point mapping it claimed to write."""
    observation_count = (
        await session.execute(
            select(func.count())
            .select_from(SignalObservation)
            .where(SignalObservation.source_release_id == source_release.id)
        )
    ).scalar_one()
    coverage_count = (
        await session.execute(
            select(func.count())
            .select_from(SignalCoverageAudit)
            .where(SignalCoverageAudit.source_release_id == source_release.id)
        )
    ).scalar_one()
    crosswalk_count = (
        await session.execute(
            select(func.count())
            .select_from(CellSourceCrosswalk)
            .where(CellSourceCrosswalk.source_release_id == source_release.id)
        )
    ).scalar_one()
    if (
        observation_count != len(result.observations)
        or coverage_count != len(result.coverage)
        or crosswalk_count != len(chunk.cells)
    ):
        raise ValueError("historical Open-Meteo source release did not retain every accounted-for row")


async def _required_open_meteo_source_releases(
    session: AsyncSession,
    plan: HistoricalOpenMeteoArchivePlan,
    checkpoint: HistoricalOpenMeteoCheckpoint,
    source_id: uuid.UUID,
) -> list[SourceRelease]:
    """Resolve every validated chunk receipt into its immutable local source release."""
    releases: list[SourceRelease] = []
    for receipt in checkpoint.receipts:
        release = (
            await session.execute(
                select(SourceRelease).where(
                    SourceRelease.data_source_id == source_id,
                    SourceRelease.source_version
                    == _open_meteo_source_version(plan, _open_meteo_chunk(plan, receipt.chunk_key)),
                    SourceRelease.payload_checksum == receipt.payload_checksum,
                    SourceRelease.transform_version == plan.transform_version,
                    SourceRelease.validation_state == ReleaseValidationState.VALID,
                )
            )
        ).scalar_one_or_none()
        if release is None:
            raise ValueError(f"historical Open-Meteo receipt {receipt.chunk_key!r} is not persisted and valid")
        releases.append(release)
    return releases


async def _ensure_cell_crosswalk(
    session: AsyncSession,
    source_release: SourceRelease,
    spatial_cell: SpatialCell,
    cell: AnalysisGridCell,
    plan: HistoricalNasaBackfillPlan,
) -> None:
    native_geometry = WKTElement(_point_wkt(cell), srid=4326)
    await session.execute(
        insert(CellSourceCrosswalk)
        .values(
            source_release_id=source_release.id,
            cell_id=spatial_cell.id,
            native_feature_key=cell.cell_key,
            native_geometry=native_geometry,
            native_resolution_m=plan.nasa.grid_resolution_m,
            spatial_support_kind="point_sample",
            mapping_method="requested_api_point",
            coverage_fraction=1,
            metadata_json={
                "grid_name": plan.nasa.grid_name,
                "provider_response_geometry": "point",
                "analysis_cell_geometry": "sampling_area",
            },
        )
        .on_conflict_do_nothing(constraint="uq_cell_source_crosswalk_release_feature_cell")
    )


async def _require_era5_spatial_cells(
    session: AsyncSession,
    plan: HistoricalEra5LandBackfillPlan,
) -> dict[str, SpatialCell]:
    """Require the matching NASA-established sampling lattice before adding ERA5 facts."""
    cell_keys = [cell.cell_key for cell in plan.cells]
    existing = (await session.execute(select(SpatialCell).where(SpatialCell.cell_key.in_(cell_keys)))).scalars()
    cells = {cell.cell_key: cell for cell in existing}
    missing = [cell.cell_key for cell in plan.cells if cell.cell_key not in cells]
    if missing:
        raise ValueError("ERA5 persistence requires the complete matching NASA sampling lattice in the warehouse")
    return cells


async def _insert_era5_crosswalks(
    session: AsyncSession,
    source_release: SourceRelease,
    plan: HistoricalEra5LandBackfillPlan,
    spatial_cells: dict[str, SpatialCell],
) -> None:
    """Bind each requested ERA5 point sample to the pre-existing analysis lattice in bounded batches."""
    rows: list[dict[str, object]] = []
    for cell in plan.cells:
        rows.append(
            {
                "source_release_id": source_release.id,
                "cell_id": spatial_cells[cell.cell_key].id,
                "native_feature_key": cell.cell_key,
                "native_geometry": WKTElement(_point_wkt(cell), srid=WGS84_SRID),
                "native_resolution_m": plan.native_grid_resolution_m,
                "spatial_support_kind": "point_sample",
                "mapping_method": "requested_cds_output_point",
                "coverage_fraction": 1,
                "metadata_json": {
                    "native_grid_name": plan.native_grid_name,
                    "requested_grid_degrees": plan.requested_grid_degrees,
                    "provider_response_geometry": "point",
                    "native_resolution_is_context_only": True,
                },
            }
        )
        if len(rows) == HISTORICAL_SIGNAL_INSERT_BATCH_SIZE:
            await _insert_cell_crosswalk_batch(session, rows)
            rows = []
    if rows:
        await _insert_cell_crosswalk_batch(session, rows)


async def _insert_cell_crosswalk_batch(session: AsyncSession, rows: list[dict[str, object]]) -> None:
    """Insert one bounded idempotent cell crosswalk batch."""
    await session.execute(
        insert(CellSourceCrosswalk)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_cell_source_crosswalk_release_feature_cell")
    )


async def _insert_era5_observations(
    session: AsyncSession,
    source_release: SourceRelease,
    spatial_cells: dict[str, SpatialCell],
    result: Era5LandMonthlyResult,
) -> None:
    """Insert one monthly ERA5 fact set in fixed-size batches rather than materializing all rows twice."""
    rows: list[dict[str, object]] = []
    for observation in result.observations:
        rows.append(
            {
                "source_release_id": source_release.id,
                "cell_id": spatial_cells[observation.cell_key].id,
                "signal_name": observation.signal_name,
                "source_parameter": observation.source_parameter,
                "support_key": "surface",
                "observed_at": observation.observed_at,
                "valid_from": observation.observed_at,
                "valid_to": observation.observed_at,
                "data_available_at": result.retrieved_at,
                "original_value": observation.original_value,
                "original_unit": observation.original_unit,
                "normalized_value": observation.normalized_value,
                "normalized_unit": observation.normalized_unit,
                "quality_flag": observation.quality_flag,
                "coverage_fraction": 1,
                "is_observed": observation.is_observed,
                "metadata_json": {"source_parameter": observation.source_parameter},
            }
        )
        if len(rows) == HISTORICAL_SIGNAL_INSERT_BATCH_SIZE:
            await _insert_signal_observation_batch(session, rows)
            rows = []
    if rows:
        await _insert_signal_observation_batch(session, rows)


async def _insert_signal_observation_batch(session: AsyncSession, rows: list[dict[str, object]]) -> None:
    """Insert one bounded idempotent signal observation batch."""
    await session.execute(
        insert(SignalObservation)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_signal_observation_release_cell_signal_time")
    )


async def _insert_era5_coverage(
    session: AsyncSession,
    source_release: SourceRelease,
    spatial_cells: dict[str, SpatialCell],
    result: Era5LandMonthlyResult,
) -> None:
    """Insert per-cell/signal ERA5 coverage evidence in bounded batches."""
    rows: list[dict[str, object]] = []
    for coverage in result.coverage:
        rows.append(
            {
                "source_release_id": source_release.id,
                "cell_id": spatial_cells[coverage.cell_key].id,
                "signal_name": coverage.signal_name,
                "source_parameter": coverage.source_parameter,
                "support_key": "surface",
                "window_start": coverage.window_start,
                "window_end": coverage.window_end,
                "expected_observation_count": coverage.expected_observation_count,
                "received_observation_count": coverage.received_observation_count,
                "status": coverage.status,
                "details": {"source_parameter": coverage.source_parameter},
            }
        )
        if len(rows) == HISTORICAL_SIGNAL_INSERT_BATCH_SIZE:
            await _insert_signal_coverage_batch(session, rows)
            rows = []
    if rows:
        await _insert_signal_coverage_batch(session, rows)


async def _insert_signal_coverage_batch(session: AsyncSession, rows: list[dict[str, object]]) -> None:
    """Insert one bounded idempotent signal coverage batch."""
    await session.execute(
        insert(SignalCoverageAudit)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_signal_coverage_release_cell_signal_parameter_window")
    )


async def _insert_observations(
    session: AsyncSession,
    source_release: SourceRelease,
    spatial_cell: SpatialCell,
    result: NasaPowerDailyResult,
) -> None:
    rows = [
        {
            "source_release_id": source_release.id,
            "cell_id": spatial_cell.id,
            "signal_name": observation.signal_name,
            "source_parameter": observation.source_parameter,
            "support_key": "surface",
            "observed_at": observation.observed_at,
            "valid_from": observation.observed_at,
            "valid_to": observation.observed_at,
            "data_available_at": result.retrieved_at,
            "original_value": observation.original_value,
            "original_unit": observation.original_unit,
            "normalized_value": observation.normalized_value,
            "normalized_unit": observation.normalized_unit,
            "quality_flag": observation.quality_flag,
            "coverage_fraction": 1,
            "is_observed": observation.is_observed,
            "metadata_json": {"source_parameter": observation.source_parameter},
        }
        for observation in result.observations
    ]
    for start in range(0, len(rows), HISTORICAL_NASA_OBSERVATION_INSERT_BATCH_SIZE):
        await session.execute(
            insert(SignalObservation)
            .values(rows[start : start + HISTORICAL_NASA_OBSERVATION_INSERT_BATCH_SIZE])
            .on_conflict_do_nothing(constraint="uq_signal_observation_release_cell_signal_time")
        )


async def _insert_coverage(
    session: AsyncSession,
    source_release: SourceRelease,
    spatial_cell: SpatialCell,
    result: NasaPowerDailyResult,
) -> None:
    for coverage in result.coverage:
        await session.execute(
            insert(SignalCoverageAudit)
            .values(
                source_release_id=source_release.id,
                cell_id=spatial_cell.id,
                signal_name=coverage.signal_name,
                source_parameter=coverage.source_parameter,
                support_key="surface",
                window_start=coverage.window_start,
                window_end=coverage.window_end,
                expected_observation_count=coverage.expected_observation_count,
                received_observation_count=coverage.received_observation_count,
                status=coverage.status,
                details={"source_parameter": coverage.source_parameter},
            )
            .on_conflict_do_nothing(constraint="uq_signal_coverage_release_cell_signal_parameter_window")
        )


async def _insert_usdm_polygons(
    session: AsyncSession,
    source_release: SourceRelease,
    result: UsdmShapefileResult,
) -> None:
    for polygon in result.polygons:
        source_geometry = func.ST_SetSRID(func.ST_GeomFromGeoJSON(polygon.geometry_json), WGS84_SRID)
        geometry = func.ST_Multi(func.ST_CollectionExtract(func.ST_MakeValid(source_geometry), 3))
        geometry_state = (
            await session.execute(
                select(
                    func.ST_IsValid(geometry),
                    func.ST_IsEmpty(geometry),
                    func.ST_SRID(geometry),
                    func.GeometryType(geometry),
                )
            )
        ).one()
        is_valid, is_empty, srid, geometry_type = geometry_state
        if not is_valid or is_empty or srid != WGS84_SRID or geometry_type != "MULTIPOLYGON":
            raise ValueError("USDM polygon must be valid WGS84 MULTIPOLYGON geometry")
        await session.execute(
            insert(DroughtPolygonSnapshot)
            .values(
                source_release_id=source_release.id,
                issue_date=result.issue_date,
                severity_class=polygon.severity_class,
                impact_type="none",
                geometry=geometry,
                geometry_checksum=polygon.geometry_checksum,
                data_available_at=result.retrieved_at,
                metadata_json={
                    "feature_key": polygon.feature_key,
                    "raw_geometry_checksum": polygon.geometry_checksum,
                    "geometry_transform": "postgis-makevalid-v1",
                    **polygon.metadata,
                },
            )
            .on_conflict_do_nothing(constraint="uq_drought_polygon_release_identity")
        )


async def _insert_usdm_coverage(
    session: AsyncSession,
    plan: HistoricalUsdmBackfillPlan,
    source_release: SourceRelease,
    result: UsdmShapefileResult,
) -> None:
    window_start = datetime.combine(result.issue_date, datetime.min.time(), tzinfo=UTC)
    window_end = datetime.combine(result.issue_date, datetime.max.time(), tzinfo=UTC)
    await session.execute(
        insert(SourceCoverageAudit)
        .values(
            source_release_id=source_release.id,
            scope_key=plan.native_product_scope,
            window_start=window_start,
            window_end=window_end,
            expected_feature_count=result.declared_feature_count,
            received_feature_count=len(result.polygons),
            status="complete",
            details={
                "issue_date": result.issue_date.isoformat(),
                "declared_feature_count": result.declared_feature_count,
                "present_severity_classes": sorted({polygon.severity_class for polygon in result.polygons}),
            },
        )
        .on_conflict_do_nothing(constraint="uq_source_coverage_release_scope_window")
    )


async def _verify_persisted_cell(
    session: AsyncSession,
    source_release: SourceRelease,
    result: NasaPowerDailyResult,
) -> None:
    observation_count = (
        await session.execute(
            select(func.count())
            .select_from(SignalObservation)
            .where(SignalObservation.source_release_id == source_release.id)
        )
    ).scalar_one()
    coverage_count = (
        await session.execute(
            select(func.count())
            .select_from(SignalCoverageAudit)
            .where(
                SignalCoverageAudit.source_release_id == source_release.id,
                SignalCoverageAudit.status == "complete",
            )
        )
    ).scalar_one()
    if observation_count != len(result.observations) or coverage_count != len(result.coverage):
        raise ValueError("historical source release did not retain complete normalized coverage")


async def _verify_persisted_era5_release(
    session: AsyncSession,
    source_release: SourceRelease,
    plan: HistoricalEra5LandBackfillPlan,
    result: Era5LandMonthlyResult,
) -> None:
    """Prove each ERA5 monthly release retained every requested fact, audit, and point mapping."""
    observation_count = (
        await session.execute(
            select(func.count())
            .select_from(SignalObservation)
            .where(SignalObservation.source_release_id == source_release.id)
        )
    ).scalar_one()
    coverage_count = (
        await session.execute(
            select(func.count())
            .select_from(SignalCoverageAudit)
            .where(
                SignalCoverageAudit.source_release_id == source_release.id,
                SignalCoverageAudit.status == "complete",
            )
        )
    ).scalar_one()
    crosswalk_count = (
        await session.execute(
            select(func.count())
            .select_from(CellSourceCrosswalk)
            .where(CellSourceCrosswalk.source_release_id == source_release.id)
        )
    ).scalar_one()
    if (
        observation_count != len(result.observations)
        or coverage_count != len(result.coverage)
        or crosswalk_count != len(plan.cells)
    ):
        raise ValueError("historical ERA5 source release did not retain complete normalized coverage")


async def _verify_persisted_usdm_release(
    session: AsyncSession,
    source_release: SourceRelease,
    result: UsdmShapefileResult,
) -> None:
    polygon_count = (
        await session.execute(
            select(func.count())
            .select_from(DroughtPolygonSnapshot)
            .where(DroughtPolygonSnapshot.source_release_id == source_release.id)
        )
    ).scalar_one()
    complete_coverage_count = (
        await session.execute(
            select(func.count())
            .select_from(SourceCoverageAudit)
            .where(
                SourceCoverageAudit.source_release_id == source_release.id,
                SourceCoverageAudit.status == "complete",
            )
        )
    ).scalar_one()
    if polygon_count != result.declared_feature_count or complete_coverage_count != 1:
        raise ValueError("historical USDM source release did not retain complete native coverage")


async def _required_source_releases(
    session: AsyncSession,
    plan: HistoricalNasaBackfillPlan,
    checkpoint: HistoricalNasaCheckpoint,
    source_id: uuid.UUID,
) -> list[SourceRelease]:
    releases: list[SourceRelease] = []
    for receipt in checkpoint.receipts:
        release = (
            await session.execute(
                select(SourceRelease).where(
                    SourceRelease.data_source_id == source_id,
                    SourceRelease.source_version == _source_version(plan, _plan_cell(plan, receipt.cell_key)),
                    SourceRelease.payload_checksum == receipt.payload_checksum,
                    SourceRelease.transform_version == plan.transform_version,
                    SourceRelease.validation_state == ReleaseValidationState.VALID,
                )
            )
        ).scalar_one_or_none()
        if release is None:
            raise ValueError(f"historical source receipt {receipt.cell_key!r} is not persisted and valid")
        releases.append(release)
    return releases


async def _required_usdm_source_releases(
    session: AsyncSession,
    plan: HistoricalUsdmBackfillPlan,
    checkpoint: HistoricalUsdmCheckpoint,
    source_id: uuid.UUID,
) -> list[SourceRelease]:
    releases: list[SourceRelease] = []
    for receipt in checkpoint.receipts:
        release = (
            await session.execute(
                select(SourceRelease).where(
                    SourceRelease.data_source_id == source_id,
                    SourceRelease.source_version == _usdm_source_version(receipt.issue_date),
                    SourceRelease.payload_checksum == receipt.payload_checksum,
                    SourceRelease.transform_version == plan.transform_version,
                    SourceRelease.validation_state == ReleaseValidationState.VALID,
                )
            )
        ).scalar_one_or_none()
        if release is None:
            raise ValueError(f"historical USDM receipt {receipt.issue_date.isoformat()!r} is not persisted and valid")
        releases.append(release)
    return releases


async def _required_era5_source_releases(
    session: AsyncSession,
    plan: HistoricalEra5LandBackfillPlan,
    checkpoint: HistoricalEra5Checkpoint,
    source_id: uuid.UUID,
) -> list[SourceRelease]:
    """Resolve every validated ERA5 monthly artifact into its immutable local source release."""
    releases: list[SourceRelease] = []
    for receipt in checkpoint.receipts:
        release = (
            await session.execute(
                select(SourceRelease).where(
                    SourceRelease.data_source_id == source_id,
                    SourceRelease.source_version == _era5_source_version(_era5_period(plan, receipt.period_key)),
                    SourceRelease.payload_checksum == receipt.payload_checksum,
                    SourceRelease.transform_version == plan.transform_version,
                    SourceRelease.validation_state == ReleaseValidationState.VALID,
                )
            )
        ).scalar_one_or_none()
        if release is None:
            raise ValueError(f"historical ERA5 receipt {receipt.period_key!r} is not persisted and valid")
        releases.append(release)
    return releases


async def _release_set_member_ids(session: AsyncSession, release_set_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        (
            await session.execute(
                select(ReleaseSetItem.source_release_id).where(ReleaseSetItem.release_set_id == release_set_id)
            )
        ).scalars()
    )


async def _find_source(session: AsyncSession, source_key: str) -> DataSource | None:
    return (await session.execute(select(DataSource).where(DataSource.key == source_key))).scalar_one_or_none()


async def _find_active_source(session: AsyncSession, source_key: str) -> DataSource:
    source = await _find_source(session, source_key)
    if source is None or source.review_state != SourceReviewState.APPROVED or not source.is_active:
        raise ValueError("historical source is not approved and active")
    return source


async def _advisory_lock(session: AsyncSession, key: str) -> None:
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))


def _plan_cell(plan: HistoricalNasaBackfillPlan, cell_key: str) -> AnalysisGridCell:
    try:
        return next(cell for cell in plan.nasa.cells if cell.cell_key == cell_key)
    except StopIteration as exc:
        raise ValueError("historical cell is not part of the reviewed plan") from exc


def _source_version(plan: HistoricalNasaBackfillPlan, cell: AnalysisGridCell) -> str:
    return (
        f"{plan.nasa.schema_version}:{plan.nasa.window.start_date:%Y%m%d}-"
        f"{plan.nasa.window.end_date:%Y%m%d}:{cell.cell_key}"
    )


def _era5_period(plan: HistoricalEra5LandBackfillPlan, period_key: str) -> Era5LandPeriod:
    """Return one reviewed ERA5 calendar month or reject an ungoverned source artifact."""
    try:
        return next(period for period in plan.periods if period.key == period_key)
    except StopIteration as exc:
        raise ValueError("historical ERA5 period is not part of the reviewed plan") from exc


def _era5_source_version(period: Era5LandPeriod) -> str:
    """Return the stable source version for one immutable ERA5 calendar-month ZIP."""
    return f"{ERA5_LAND_DAILY_SCHEMA_VERSION}:{period.key}"


def _usdm_source_version(issue_date: date) -> str:
    return f"usdm-shapefile-v1:{issue_date:%Y%m%d}"


def _open_meteo_chunk(plan: HistoricalOpenMeteoArchivePlan, chunk_key: str) -> OpenMeteoArchiveChunk:
    """Return one reviewed archive chunk or reject an ungoverned source artifact."""
    try:
        return next(chunk for chunk in plan.chunks if chunk.key == chunk_key)
    except StopIteration as exc:
        raise ValueError("historical Open-Meteo chunk is not part of the reviewed plan") from exc


def _open_meteo_source_version(plan: HistoricalOpenMeteoArchivePlan, chunk: OpenMeteoArchiveChunk) -> str:
    """Return the stable source version for one immutable archive chunk."""
    return (
        f"{OPEN_METEO_ARCHIVE_SCHEMA_VERSION}:{plan.window.start_date:%Y%m%d}-"
        f"{plan.window.end_date:%Y%m%d}:{plan.grid_name}:{chunk.key}"
    )


def _cell_polygon_wkt(cell: AnalysisGridCell, half_span: float) -> str:
    west = cell.longitude - half_span
    east = cell.longitude + half_span
    south = cell.latitude - half_span
    north = cell.latitude + half_span
    return (
        "POLYGON(("
        f"{west:.8f} {south:.8f}, {east:.8f} {south:.8f}, {east:.8f} {north:.8f}, "
        f"{west:.8f} {north:.8f}, {west:.8f} {south:.8f}"
        "))"
    )


def _point_wkt(cell: AnalysisGridCell) -> str:
    return f"POINT({cell.longitude:.8f} {cell.latitude:.8f})"


def _utc_now_or_value(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("validated_at must include a timezone")
    return timestamp.astimezone(UTC)
