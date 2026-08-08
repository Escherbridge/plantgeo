"""Transactional local persistence for validated USDM weekly drought vector releases."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Text, bindparam, func, select, text
from sqlalchemy.dialects.postgresql import ARRAY, insert

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.execution.historical_usdm import (
    historical_usdm_plan_checksum,
    historical_usdm_release_manifest,
    require_complete_usdm_result,
    usdm_shapefile_url,
)
from agri_data_service.execution.historical_writer._release_sets import _finalize_historical_release_set
from agri_data_service.execution.historical_writer._results import (
    HistoricalReleaseSetResult,
    HistoricalUsdmWriteResult,
    ReleaseSetIdentity,
)
from agri_data_service.execution.historical_writer._shared import _ensure_data_source
from agri_data_service.execution.provenance import (
    advisory_lock,
    ensure_artifact,
    ensure_source_release,
    require_validation_timestamp,
)
from agri_data_service.models.historical import DroughtPolygonSnapshot, SourceCoverageAudit
from agri_data_service.models.provenance import Artifact, ReleaseValidationState, SourceRelease

if TYPE_CHECKING:
    import uuid
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.execution.historical_usdm import (
        HistoricalUsdmBackfillPlan,
        HistoricalUsdmCheckpoint,
        UsdmShapefileResult,
    )
    from agri_data_service.models.provenance import DataSource

# A national D0 multipolygon's GeoJSON is megabytes, so the statement is bounded by polygon count, not by
# the bind-parameter ceiling every other batch here is bounded by.
HISTORICAL_USDM_POLYGON_INSERT_BATCH_SIZE = 500

_INSERT_USDM_POLYGONS = text(load_query_sql("execution/insert_usdm_polygons.sql")).bindparams(
    bindparam("geometry_jsons", type_=ARRAY(Text)),
    bindparam("geometry_checksums", type_=ARRAY(Text)),
    bindparam("severity_classes", type_=ARRAY(Text)),
    bindparam("metadata_jsons", type_=ARRAY(Text)),
)

_SCHEMA_VERSION = "usdm-shapefile-v1"
_ARTIFACT_KIND = "source_usdm_weekly_shapefile_zip"
_RELEASE_CONFLICT_MESSAGE = "historical USDM source release identity is already governed by different metadata"
_RELEASE_UNVALIDATED_MESSAGE = "historical USDM source release is valid without a validation timestamp"
_ARTIFACT_CONFLICT_MESSAGE = "historical USDM source artifact identity is already governed by different content"


async def persist_usdm_shapefile(
    session: AsyncSession,
    *,
    plan: HistoricalUsdmBackfillPlan,
    result: UsdmShapefileResult,
) -> HistoricalUsdmWriteResult:
    """Persist one complete USDM weekly source package in the caller-owned transaction."""
    require_complete_usdm_result(plan, result)
    await advisory_lock(session, f"historical-usdm:{historical_usdm_plan_checksum(plan)}:{result.issue_date:%Y%m%d}")
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


async def finalize_usdm_release_set(
    session: AsyncSession,
    *,
    plan: HistoricalUsdmBackfillPlan,
    checkpoint: HistoricalUsdmCheckpoint,
    validated_at: datetime | None = None,
) -> HistoricalReleaseSetResult:
    """Atomically validate USDM membership only after every planned weekly receipt is durable."""

    async def required_release_ids(source_id: uuid.UUID) -> set[uuid.UUID]:
        releases = await _required_usdm_source_releases(session, plan, checkpoint, source_id)
        return {release.id for release in releases}

    return await _finalize_historical_release_set(
        session,
        identity=ReleaseSetIdentity(
            logical_key=plan.release_set_key,
            as_of_time=plan.release_set_as_of,
            description=plan.description,
        ),
        manifest_checksum=historical_usdm_release_manifest(plan, checkpoint),
        receipt_times=[receipt.retrieved_at for receipt in checkpoint.receipts],
        source_key=plan.source.key,
        required_release_ids=required_release_ids,
        validated_at=validated_at,
    )


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
            # Pinned to this lane rather than read off the plan: the USDM shapefile contract is a
            # property of the lane's parser, not of a per-run plan field.
            schema_version=_SCHEMA_VERSION,
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
            "schema_version": _SCHEMA_VERSION,
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
    return await ensure_artifact(
        session,
        Artifact(
            source_release_id=source_release.id,
            kind=_ARTIFACT_KIND,
            uri=uri,
            media_type="application/zip",
            checksum_sha256=result.payload_checksum,
            size_bytes=len(result.payload),
            storage_class="database_inline",
            metadata_json=metadata,
            content_bytes=result.payload,
        ),
        expected={
            "source_release_id": source_release.id,
            "kind": _ARTIFACT_KIND,
            "media_type": "application/zip",
            "size_bytes": len(result.payload),
            "storage_class": "database_inline",
            "metadata_json": metadata,
        },
        defer_content_bytes=True,
        conflict_message=_ARTIFACT_CONFLICT_MESSAGE,
    )


async def _insert_usdm_polygons(
    session: AsyncSession,
    source_release: SourceRelease,
    result: UsdmShapefileResult,
) -> None:
    """Repair, validate and insert one release's polygons per bounded batch, evaluating ST_MakeValid once each."""
    polygons = list(result.polygons)
    for start in range(0, len(polygons), HISTORICAL_USDM_POLYGON_INSERT_BATCH_SIZE):
        batch = polygons[start : start + HISTORICAL_USDM_POLYGON_INSERT_BATCH_SIZE]
        accepted_count = (
            await session.execute(
                _INSERT_USDM_POLYGONS,
                {
                    # Scalar binds stay native Python objects; only the unnested text[] columns are cast.
                    "source_release_id": source_release.id,
                    "issue_date": result.issue_date,
                    "data_available_at": result.retrieved_at,
                    "geometry_jsons": [polygon.geometry_json for polygon in batch],
                    "geometry_checksums": [polygon.geometry_checksum for polygon in batch],
                    "severity_classes": [str(polygon.severity_class) for polygon in batch],
                    "metadata_jsons": [
                        json.dumps(
                            {
                                "feature_key": polygon.feature_key,
                                "raw_geometry_checksum": polygon.geometry_checksum,
                                "geometry_transform": "postgis-makevalid-v1",
                                **polygon.metadata,
                            },
                            allow_nan=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        for polygon in batch
                    ],
                },
            )
        ).scalar_one()
        # Fail closed exactly as the per-polygon validation SELECT did: a polygon the repair chain could not
        # turn into a valid WGS84 MULTIPOLYGON is never silently dropped from the release.
        if accepted_count != len(batch):
            raise ValueError("USDM polygon must be valid WGS84 MULTIPOLYGON geometry")


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


def _usdm_source_version(issue_date: date) -> str:
    return f"{_SCHEMA_VERSION}:{issue_date:%Y%m%d}"
