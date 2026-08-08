"""Transactional local persistence for validated NASA POWER historical source cells."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from geoalchemy2 import WKTElement
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from agri_data_service.execution.historical_backfill import (
    historical_nasa_plan_checksum,
    historical_nasa_release_manifest,
    nasa_power_daily_url,
    require_complete_nasa_result,
)
from agri_data_service.execution.historical_writer._release_sets import _finalize_historical_release_set
from agri_data_service.execution.historical_writer._results import (
    HistoricalNasaWriteResult,
    HistoricalReleaseSetResult,
    ReleaseSetIdentity,
)
from agri_data_service.execution.historical_writer._shared import (
    HISTORICAL_NASA_OBSERVATION_INSERT_BATCH_SIZE,
    _cell_polygon_wkt,
    _ensure_data_source,
    _point_wkt,
)
from agri_data_service.execution.provenance import (
    advisory_lock,
    ensure_artifact,
    ensure_source_release,
    require_validation_timestamp,
)
from agri_data_service.models.historical import (
    CellSourceCrosswalk,
    SignalCoverageAudit,
    SignalObservation,
    SpatialCell,
)
from agri_data_service.models.provenance import (
    Artifact,
    ReleaseValidationState,
    SourceRelease,
)

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.execution.historical_backfill import (
        AnalysisGridCell,
        HistoricalNasaBackfillPlan,
        HistoricalNasaCheckpoint,
        NasaPowerDailyResult,
    )
    from agri_data_service.models.provenance import DataSource

_ARTIFACT_KIND = "source_nasa_power_daily_json"
_RELEASE_CONFLICT_MESSAGE = "historical source release identity is already governed by different metadata"
_RELEASE_UNVALIDATED_MESSAGE = "historical source release is valid without a validation timestamp"
_ARTIFACT_CONFLICT_MESSAGE = "historical source artifact identity is already governed by different content"


async def persist_nasa_power_cell(
    session: AsyncSession,
    *,
    plan: HistoricalNasaBackfillPlan,
    result: NasaPowerDailyResult,
) -> HistoricalNasaWriteResult:
    """Persist one complete source cell inside the caller-owned transaction without committing."""
    require_complete_nasa_result(plan.nasa, result)
    cell = _plan_cell(plan, result.cell_key)
    await advisory_lock(session, f"historical-nasa:{historical_nasa_plan_checksum(plan)}:{cell.cell_key}")
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


async def finalize_nasa_release_set(
    session: AsyncSession,
    *,
    plan: HistoricalNasaBackfillPlan,
    checkpoint: HistoricalNasaCheckpoint,
    validated_at: datetime | None = None,
) -> HistoricalReleaseSetResult:
    """Atomically validate the release set only after every reviewed source cell is durable."""

    async def required_release_ids(source_id: uuid.UUID) -> set[uuid.UUID]:
        releases = await _required_source_releases(session, plan, checkpoint, source_id)
        return {release.id for release in releases}

    return await _finalize_historical_release_set(
        session,
        identity=ReleaseSetIdentity(
            logical_key=plan.release_set_key,
            as_of_time=plan.release_set_as_of,
            description=plan.description,
        ),
        manifest_checksum=historical_nasa_release_manifest(plan, checkpoint),
        receipt_times=[receipt.retrieved_at for receipt in checkpoint.receipts],
        source_key=plan.source.key,
        required_release_ids=required_release_ids,
        validated_at=validated_at,
    )


async def _ensure_spatial_cell(
    session: AsyncSession,
    plan: HistoricalNasaBackfillPlan,
    cell: AnalysisGridCell,
) -> tuple[SpatialCell, bool]:
    """Mint or re-confirm the one analysis cell this lane owns; every other lane requires it to exist."""
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
    observed_from = datetime.combine(plan.nasa.window.start_date, datetime.min.time(), tzinfo=UTC)
    observed_to = datetime.combine(plan.nasa.window.end_date, datetime.max.time(), tzinfo=UTC)
    release, idempotent = await ensure_source_release(
        session,
        SourceRelease(
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
        ),
        expected={
            "observed_from": observed_from,
            "observed_to": observed_to,
            "payload_bytes": len(result.payload),
            "schema_version": plan.nasa.schema_version,
            "transform_version": plan.transform_version,
            "license_snapshot": plan.source.license_name,
            "query_parameters": query_parameters,
            "quality_summary": quality_summary,
            "validation_state": ReleaseValidationState.VALID,
        },
        conflict_message=_RELEASE_CONFLICT_MESSAGE,
    )
    if idempotent:
        require_validation_timestamp(release, message=_RELEASE_UNVALIDATED_MESSAGE)
    return release, idempotent


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
    return await ensure_artifact(
        session,
        Artifact(
            source_release_id=source_release.id,
            kind=_ARTIFACT_KIND,
            uri=uri,
            media_type="application/json",
            checksum_sha256=result.payload_checksum,
            size_bytes=len(result.payload),
            storage_class="database_inline",
            metadata_json=metadata,
            content_bytes=result.payload,
        ),
        expected={
            "source_release_id": source_release.id,
            "kind": _ARTIFACT_KIND,
            "media_type": "application/json",
            "size_bytes": len(result.payload),
            "storage_class": "database_inline",
            "metadata_json": metadata,
        },
        defer_content_bytes=True,
        conflict_message=_ARTIFACT_CONFLICT_MESSAGE,
    )


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
