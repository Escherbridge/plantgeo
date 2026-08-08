"""Transactional local persistence for validated ERA5-Land monthly source releases."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from geoalchemy2 import WKTElement
from sqlalchemy import func, select

from agri_data_service.execution.historical_era5 import (
    ERA5_LAND_DAILY_SCHEMA_VERSION,
    historical_era5_plan_checksum,
    historical_era5_release_manifest,
    require_complete_era5_result,
)
from agri_data_service.execution.historical_writer._release_sets import _finalize_historical_release_set
from agri_data_service.execution.historical_writer._results import (
    HistoricalEra5WriteResult,
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
    _point_wkt,
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

    from agri_data_service.execution.historical_era5 import (
        Era5LandMonthlyResult,
        Era5LandPeriod,
        HistoricalEra5Checkpoint,
        HistoricalEra5LandBackfillPlan,
    )
    from agri_data_service.models.historical import SpatialCell
    from agri_data_service.models.provenance import DataSource

_ARTIFACT_KIND = "source_era5_land_daily_netcdf_zip"
_RELEASE_CONFLICT_MESSAGE = "historical ERA5 source release identity is already governed by different metadata"
_RELEASE_UNVALIDATED_MESSAGE = "historical ERA5 source release is valid without a validation timestamp"
_ARTIFACT_CONFLICT_MESSAGE = "historical ERA5 source artifact identity is already governed by different content"


async def persist_era5_land_month(
    session: AsyncSession,
    *,
    plan: HistoricalEra5LandBackfillPlan,
    result: Era5LandMonthlyResult,
) -> HistoricalEra5WriteResult:
    """Persist one complete cache-backed ERA5 month without retaining its raw ZIP in PostgreSQL."""
    require_complete_era5_result(plan, result)
    period = _era5_period(plan, result.period_key)
    await advisory_lock(session, f"historical-era5:{historical_era5_plan_checksum(plan)}:{period.key}")
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


async def finalize_era5_release_set(
    session: AsyncSession,
    *,
    plan: HistoricalEra5LandBackfillPlan,
    checkpoint: HistoricalEra5Checkpoint,
    validated_at: datetime | None = None,
) -> HistoricalReleaseSetResult:
    """Atomically validate ERA5-Land membership only after every monthly ZIP is durable."""

    async def required_release_ids(source_id: uuid.UUID) -> set[uuid.UUID]:
        releases = await _required_era5_source_releases(session, plan, checkpoint, source_id)
        return {release.id for release in releases}

    return await _finalize_historical_release_set(
        session,
        identity=ReleaseSetIdentity(
            logical_key=plan.release_set_key,
            as_of_time=plan.release_set_as_of,
            description=plan.description,
        ),
        manifest_checksum=historical_era5_release_manifest(plan, checkpoint),
        receipt_times=[receipt.retrieved_at for receipt in checkpoint.receipts],
        source_key=plan.source.key,
        required_release_ids=required_release_ids,
        validated_at=validated_at,
    )


async def _require_era5_spatial_cells(
    session: AsyncSession,
    plan: HistoricalEra5LandBackfillPlan,
) -> dict[str, SpatialCell]:
    """Require the matching NASA-established sampling lattice before adding ERA5 facts."""
    return await _require_spatial_cells(
        session,
        plan.cells,
        missing_message="ERA5 persistence requires the complete matching NASA sampling lattice in the warehouse",
    )


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
    release, idempotent = await ensure_source_release(
        session,
        SourceRelease(
            data_source_id=source.id,
            source_version=_era5_source_version(period),
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
        ),
        expected={
            "observed_from": observed_from,
            "observed_to": observed_to,
            "payload_bytes": len(result.payload),
            "schema_version": ERA5_LAND_DAILY_SCHEMA_VERSION,
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
    return await ensure_artifact(
        session,
        Artifact(
            source_release_id=source_release.id,
            kind=_ARTIFACT_KIND,
            uri=uri,
            media_type="application/zip",
            checksum_sha256=result.payload_checksum,
            size_bytes=len(result.payload),
            # This lane alone keeps its ZIP on local disk, so the warehouse row must stay blob-free.
            storage_class="local_raw_cache",
            metadata_json=metadata,
            content_bytes=None,
        ),
        expected={
            "source_release_id": source_release.id,
            "kind": _ARTIFACT_KIND,
            "media_type": "application/zip",
            "size_bytes": len(result.payload),
            "storage_class": "local_raw_cache",
            "metadata_json": metadata,
            "content_bytes": None,
        },
        # The absence of the blob is itself part of this lane's contract, so it has to be read back.
        defer_content_bytes=False,
        conflict_message=_ARTIFACT_CONFLICT_MESSAGE,
    )


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


def _era5_period(plan: HistoricalEra5LandBackfillPlan, period_key: str) -> Era5LandPeriod:
    """Return one reviewed ERA5 calendar month or reject an ungoverned source artifact."""
    try:
        return next(period for period in plan.periods if period.key == period_key)
    except StopIteration as exc:
        raise ValueError("historical ERA5 period is not part of the reviewed plan") from exc


def _era5_source_version(period: Era5LandPeriod) -> str:
    """Return the stable source version for one immutable ERA5 calendar-month ZIP."""
    return f"{ERA5_LAND_DAILY_SCHEMA_VERSION}:{period.key}"
