"""Transactional local persistence for accounted-for Open-Meteo CAMS air-quality chunks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from geoalchemy2 import WKTElement
from sqlalchemy import func, select

from agri_data_service.execution.contracts import reject_sensitive_fields
from agri_data_service.execution.historical_cams import (
    CAMS_CHECKPOINT_SCHEMA_VERSION,
    cams_air_quality_chunk_url,
    historical_cams_plan_checksum,
    historical_cams_release_manifest,
    require_accounted_cams_result,
)
from agri_data_service.execution.historical_writer._release_sets import _finalize_historical_release_set
from agri_data_service.execution.historical_writer._results import (
    HistoricalCamsWriteResult,
    HistoricalReleaseSetResult,
    ReleaseSetIdentity,
)
from agri_data_service.execution.historical_writer._shared import (
    HISTORICAL_SIGNAL_INSERT_BATCH_SIZE,
    WGS84_SRID,
    _ensure_data_source,
    _insert_cell_crosswalk_batch,
    _insert_signal_coverage_batch,
    _insert_signal_observation_batch,
    _require_spatial_cells,
)
from agri_data_service.execution.provenance import (
    advisory_lock,
    ensure_artifact,
    ensure_source_release,
    require_validation_timestamp,
)
from agri_data_service.models.historical import CellSourceCrosswalk, SignalCoverageAudit, SignalObservation
from agri_data_service.models.provenance import Artifact, ReleaseValidationState, SourceRelease

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.execution.historical_cams import (
        CamsAirQualityChunk,
        CamsAirQualityChunkResult,
        HistoricalCamsAirQualityPlan,
        HistoricalCamsCheckpoint,
    )
    from agri_data_service.models.historical import SpatialCell
    from agri_data_service.models.provenance import DataSource

_RELEASE_CONFLICT_MESSAGE = "historical CAMS source release identity is already governed by different metadata"
_RELEASE_UNVALIDATED_MESSAGE = "historical CAMS source release is valid without a validation timestamp"
_ARTIFACT_CONFLICT_MESSAGE = "historical CAMS source artifact identity is already governed by different content"


async def persist_cams_air_quality_chunk(
    session: AsyncSession,
    *,
    plan: HistoricalCamsAirQualityPlan,
    result: CamsAirQualityChunkResult,
) -> HistoricalCamsWriteResult:
    """Persist one accounted-for CAMS air-quality chunk against the existing analysis lattice."""
    require_accounted_cams_result(plan, result)
    chunk = _cams_chunk(plan, result.chunk_key)
    await advisory_lock(session, f"historical-cams:{historical_cams_plan_checksum(plan)}:{chunk.key}")
    source = await _ensure_data_source(
        session,
        plan.source,
        configuration={
            "ingestion_boundary": "local_historical_backfill",
            "source_kind": "model_reanalysis",
            "provider_role": "intermediary_redistributor",
            "upstream_product": "cams_air_quality",
            "domain": plan.domain,
            "native_grid_degrees": str(plan.native_grid_degrees),
            "native_grid_resolution_m": str(plan.native_grid_resolution_m),
        },
    )
    spatial_cells = await _require_cams_spatial_cells(session, plan, chunk)
    source_release, release_idempotent = await _ensure_cams_source_release(session, plan, source, chunk, result)
    artifact, artifact_idempotent = await _ensure_cams_artifact(session, plan, source_release, chunk, result)
    await _insert_cams_crosswalks(session, source_release, plan, spatial_cells, result)
    await _insert_cams_observations(session, source_release, plan, spatial_cells, result)
    await _insert_cams_coverage(session, source_release, plan, spatial_cells, result)
    await _verify_persisted_cams_release(session, source_release, chunk, result)
    return HistoricalCamsWriteResult(
        source_release_id=source_release.id,
        artifact_id=artifact.id,
        observation_count=len(result.observations),
        observed_value_count=sum(1 for item in result.observations if item.is_observed),
        insufficient_hour_day_count=result.insufficient_hour_day_count,
        coverage_count=len(result.coverage),
        no_data_series_count=sum(1 for item in result.coverage if item.status == "no_data"),
        crosswalk_count=len(chunk.cells),
        idempotent=release_idempotent and artifact_idempotent,
    )


async def finalize_cams_release_set(
    session: AsyncSession,
    *,
    plan: HistoricalCamsAirQualityPlan,
    checkpoint: HistoricalCamsCheckpoint,
    validated_at: datetime | None = None,
) -> HistoricalReleaseSetResult:
    """Atomically validate CAMS air-quality membership only after every planned chunk is durable."""

    async def required_release_ids(source_id: uuid.UUID) -> set[uuid.UUID]:
        releases = await _required_cams_source_releases(session, plan, checkpoint, source_id)
        return {release.id for release in releases}

    return await _finalize_historical_release_set(
        session,
        identity=ReleaseSetIdentity(
            logical_key=plan.release_set_key,
            as_of_time=plan.release_set_as_of,
            description=plan.description,
        ),
        manifest_checksum=historical_cams_release_manifest(plan, checkpoint),
        receipt_times=[receipt.retrieved_at for receipt in checkpoint.receipts],
        source_key=plan.source.key,
        required_release_ids=required_release_ids,
        validated_at=validated_at,
    )


async def _require_cams_spatial_cells(
    session: AsyncSession,
    plan: HistoricalCamsAirQualityPlan,
    chunk: CamsAirQualityChunk,
) -> dict[str, SpatialCell]:
    cells = await _require_spatial_cells(
        session,
        chunk.cells,
        missing_message="CAMS air-quality persistence requires every reviewed analysis cell in the warehouse",
    )
    wrong_grid = sorted(key for key, cell in cells.items() if cell.grid_name != plan.grid_name)
    if wrong_grid:
        raise ValueError("CAMS air-quality cell_key resolves to a spatial cell on a different analysis grid")
    return cells


async def _ensure_cams_source_release(
    session: AsyncSession,
    plan: HistoricalCamsAirQualityPlan,
    source: DataSource,
    chunk: CamsAirQualityChunk,
    result: CamsAirQualityChunkResult,
) -> tuple[SourceRelease, bool]:
    observed_from = datetime.combine(chunk.start_date, datetime.min.time(), tzinfo=UTC)
    observed_to = datetime.combine(chunk.end_date, datetime.max.time(), tzinfo=UTC)
    query_parameters = {
        "request_url": cams_air_quality_chunk_url(plan, chunk, base_url=result.request_base_url),
        "domain": plan.domain,
        "cell_selection": plan.cell_selection,
        "time_zone": plan.time_zone,
        "parameters": plan.parameters,
        "cell_keys": [cell.cell_key for cell in chunk.cells],
    }
    reject_sensitive_fields(query_parameters)
    quality_summary = {
        "requested_series_count": len(chunk.cells) * len(plan.parameters),
        "expected_daily_rows": len(result.observations),
        "observed_value_count": sum(1 for item in result.observations if item.is_observed),
        "insufficient_hour_day_count": result.insufficient_hour_day_count,
        "coverage_count": len(result.coverage),
        "no_data_series_count": sum(1 for item in result.coverage if item.status == "no_data"),
        "partial_series_count": sum(1 for item in result.coverage if item.status == "partial"),
        "spatial_support_kind": "point_sample",
        "wire_payload_checksum": result.wire_payload_checksum,
        "wire_payload_bytes": result.wire_payload_bytes,
        "provider_role": "intermediary_redistributor",
    }
    release, idempotent = await ensure_source_release(
        session,
        SourceRelease(
            data_source_id=source.id,
            source_version=_cams_source_version(plan, chunk),
            retrieved_at=result.retrieved_at,
            data_available_at=result.retrieved_at,
            observed_from=observed_from,
            observed_to=observed_to,
            payload_checksum=result.payload_checksum,
            payload_bytes=len(result.payload),
            schema_version=CAMS_CHECKPOINT_SCHEMA_VERSION,
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
            "schema_version": CAMS_CHECKPOINT_SCHEMA_VERSION,
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


async def _ensure_cams_artifact(
    session: AsyncSession,
    plan: HistoricalCamsAirQualityPlan,
    source_release: SourceRelease,
    chunk: CamsAirQualityChunk,
    result: CamsAirQualityChunkResult,
) -> tuple[Artifact, bool]:
    artifact_kind = "cams_air_quality_json"
    uri = (
        f"warehouse://historical-source/{plan.source.key}/{chunk.key}/"
        f"{plan.transform_version}/{result.payload_checksum}.json"
    )
    metadata = {
        "chunk_key": chunk.key,
        "plan_checksum": historical_cams_plan_checksum(plan),
        "transform_version": plan.transform_version,
        "wire_payload_checksum": result.wire_payload_checksum,
        "wire_payload_bytes": result.wire_payload_bytes,
    }
    return await ensure_artifact(
        session,
        Artifact(
            source_release_id=source_release.id,
            kind=artifact_kind,
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
            "kind": artifact_kind,
            "media_type": "application/json",
            "size_bytes": len(result.payload),
            "storage_class": "database_inline",
            "metadata_json": metadata,
        },
        defer_content_bytes=True,
        conflict_message=_ARTIFACT_CONFLICT_MESSAGE,
    )


async def _insert_cams_crosswalks(
    session: AsyncSession,
    source_release: SourceRelease,
    plan: HistoricalCamsAirQualityPlan,
    spatial_cells: dict[str, SpatialCell],
    result: CamsAirQualityChunkResult,
) -> None:
    rows: list[dict[str, object]] = []
    for cell_key, latitude, longitude in result.grid_points:
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
                },
            }
        )
        if len(rows) == HISTORICAL_SIGNAL_INSERT_BATCH_SIZE:
            await _insert_cell_crosswalk_batch(session, rows)
            rows = []
    if rows:
        await _insert_cell_crosswalk_batch(session, rows)


async def _insert_cams_observations(
    session: AsyncSession,
    source_release: SourceRelease,
    plan: HistoricalCamsAirQualityPlan,
    spatial_cells: dict[str, SpatialCell],
    result: CamsAirQualityChunkResult,
) -> None:
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
                "metadata_json": {},
            }
        )
        if len(rows) == HISTORICAL_SIGNAL_INSERT_BATCH_SIZE:
            await _insert_signal_observation_batch(session, rows)
            rows = []
    if rows:
        await _insert_signal_observation_batch(session, rows)


async def _insert_cams_coverage(
    session: AsyncSession,
    source_release: SourceRelease,
    plan: HistoricalCamsAirQualityPlan,
    spatial_cells: dict[str, SpatialCell],
    result: CamsAirQualityChunkResult,
) -> None:
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
                    "no_data_means": "the provider modelled no value here",
                },
            }
        )
        if len(rows) == HISTORICAL_SIGNAL_INSERT_BATCH_SIZE:
            await _insert_signal_coverage_batch(session, rows)
            rows = []
    if rows:
        await _insert_signal_coverage_batch(session, rows)


async def _verify_persisted_cams_release(
    session: AsyncSession,
    source_release: SourceRelease,
    chunk: CamsAirQualityChunk,
    result: CamsAirQualityChunkResult,
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
        raise ValueError("historical CAMS source release did not retain every accounted-for row")


async def _required_cams_source_releases(
    session: AsyncSession,
    plan: HistoricalCamsAirQualityPlan,
    checkpoint: HistoricalCamsCheckpoint,
    source_id: uuid.UUID,
) -> list[SourceRelease]:
    releases: list[SourceRelease] = []
    for receipt in checkpoint.receipts:
        release = (
            await session.execute(
                select(SourceRelease).where(
                    SourceRelease.data_source_id == source_id,
                    SourceRelease.source_version == _cams_source_version(plan, _cams_chunk(plan, receipt.chunk_key)),
                    SourceRelease.payload_checksum == receipt.payload_checksum,
                    SourceRelease.transform_version == plan.transform_version,
                    SourceRelease.validation_state == ReleaseValidationState.VALID,
                )
            )
        ).scalar_one_or_none()
        if release is None:
            raise ValueError(f"historical CAMS receipt {receipt.chunk_key!r} is not persisted and valid")
        releases.append(release)
    return releases


def _cams_chunk(plan: HistoricalCamsAirQualityPlan, chunk_key: str) -> CamsAirQualityChunk:
    try:
        return next(chunk for chunk in plan.chunks if chunk.key == chunk_key)
    except StopIteration as exc:
        raise ValueError("historical CAMS chunk is not part of the reviewed plan") from exc


def _cams_source_version(plan: HistoricalCamsAirQualityPlan, chunk: CamsAirQualityChunk) -> str:
    return (
        f"{CAMS_CHECKPOINT_SCHEMA_VERSION}:{chunk.start_date:%Y%m%d}-"
        f"{chunk.end_date:%Y%m%d}:{plan.grid_name}:{chunk.key}"
    )
