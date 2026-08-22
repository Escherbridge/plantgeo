"""Transactional local persistence for accounted-for Open-Meteo ERA5/ERA5-Land archive chunks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from geoalchemy2 import WKTElement
from sqlalchemy import func, select

from agri_data_service.execution.contracts import reject_sensitive_fields
from agri_data_service.execution.historical_writer._release_sets import _finalize_historical_release_set
from agri_data_service.execution.historical_writer._results import (
    HistoricalOpenMeteoWriteResult,
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
from agri_data_service.execution.weather_observations.era5_land import (
    OPEN_METEO_ARCHIVE_SCHEMA_VERSION,
    historical_open_meteo_plan_checksum,
    historical_open_meteo_release_manifest,
    open_meteo_archive_chunk_url,
    require_accounted_open_meteo_result,
)
from agri_data_service.models.historical import CellSourceCrosswalk, SignalCoverageAudit, SignalObservation
from agri_data_service.models.provenance import Artifact, ReleaseValidationState, SourceRelease

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.execution.weather_observations.era5_land import (
        HistoricalOpenMeteoArchivePlan,
        HistoricalOpenMeteoCheckpoint,
        OpenMeteoArchiveChunk,
        OpenMeteoArchiveChunkResult,
    )
    from agri_data_service.models.historical import SpatialCell
    from agri_data_service.models.provenance import DataSource

_RELEASE_CONFLICT_MESSAGE = "historical Open-Meteo source release identity is already governed by different metadata"
_RELEASE_UNVALIDATED_MESSAGE = "historical Open-Meteo source release is valid without a validation timestamp"
_ARTIFACT_CONFLICT_MESSAGE = "historical Open-Meteo source artifact identity is already governed by different content"


async def persist_open_meteo_archive_chunk(
    session: AsyncSession,
    *,
    plan: HistoricalOpenMeteoArchivePlan,
    result: OpenMeteoArchiveChunkResult,
) -> HistoricalOpenMeteoWriteResult:
    """Persist one accounted-for Open-Meteo archive chunk against the existing analysis lattice."""
    require_accounted_open_meteo_result(plan, result)
    chunk = _open_meteo_chunk(plan, result.chunk_key)
    await advisory_lock(session, f"historical-open-meteo:{historical_open_meteo_plan_checksum(plan)}:{chunk.key}")
    source = await _ensure_data_source(
        session,
        plan.source,
        configuration={
            "ingestion_boundary": "local_historical_backfill",
            "source_kind": plan.product.source_kind,
            "provider_role": "intermediary_redistributor",
            "upstream_product": plan.product.upstream_product,
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


async def finalize_open_meteo_release_set(
    session: AsyncSession,
    *,
    plan: HistoricalOpenMeteoArchivePlan,
    checkpoint: HistoricalOpenMeteoCheckpoint,
    validated_at: datetime | None = None,
) -> HistoricalReleaseSetResult:
    """Atomically validate Open-Meteo archive membership only after every planned chunk is durable."""

    async def required_release_ids(source_id: uuid.UUID) -> set[uuid.UUID]:
        releases = await _required_open_meteo_source_releases(session, plan, checkpoint, source_id)
        return {release.id for release in releases}

    return await _finalize_historical_release_set(
        session,
        identity=ReleaseSetIdentity(
            logical_key=plan.release_set_key,
            as_of_time=plan.release_set_as_of,
            description=plan.description,
        ),
        manifest_checksum=historical_open_meteo_release_manifest(plan, checkpoint),
        receipt_times=[receipt.retrieved_at for receipt in checkpoint.receipts],
        source_key=plan.source.key,
        required_release_ids=required_release_ids,
        validated_at=validated_at,
    )


async def _require_open_meteo_spatial_cells(
    session: AsyncSession,
    plan: HistoricalOpenMeteoArchivePlan,
    chunk: OpenMeteoArchiveChunk,
) -> dict[str, SpatialCell]:
    """Require the reviewed analysis lattice to already exist; this lane never mints spatial cells."""
    cells = await _require_spatial_cells(
        session,
        chunk.cells,
        missing_message="Open-Meteo archive persistence requires every reviewed analysis cell in the warehouse",
    )
    wrong_grid = sorted(key for key, cell in cells.items() if cell.grid_name != plan.grid_name)
    if wrong_grid:
        raise ValueError("Open-Meteo archive cell_key resolves to a spatial cell on a different analysis grid")
    return cells


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
        # The host this chunk's bytes really came from, without the paid-tier credential, which is
        # an environment fact and never a stored one. See execution/AGENTS.md §historical_open_meteo.
        "request_url": open_meteo_archive_chunk_url(plan, chunk, base_url=result.request_base_url),
        "model": plan.model,
        "cell_selection": plan.cell_selection,
        "time_zone": plan.time_zone,
        "parameters": plan.parameters,
        "cell_keys": [cell.cell_key for cell in chunk.cells],
    }
    # The export path already refuses a credentialed URL, but it runs long after the INSERT. This is
    # the same check at the moment the value would become permanent.
    reject_sensitive_fields(query_parameters)
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
    release, idempotent = await ensure_source_release(
        session,
        SourceRelease(
            data_source_id=source.id,
            source_version=_open_meteo_source_version(plan, chunk),
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
        ),
        expected={
            "observed_from": observed_from,
            "observed_to": observed_to,
            "payload_bytes": len(result.payload),
            "schema_version": OPEN_METEO_ARCHIVE_SCHEMA_VERSION,
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


async def _ensure_open_meteo_artifact(
    session: AsyncSession,
    plan: HistoricalOpenMeteoArchivePlan,
    source_release: SourceRelease,
    chunk: OpenMeteoArchiveChunk,
    result: OpenMeteoArchiveChunkResult,
) -> tuple[Artifact, bool]:
    # Both the URI namespace and the artifact kind come from the plan's own model, so the two archive
    # models cannot share an artifact identity. Each is byte-identical to the literal it replaced for
    # every already-persisted ERA5-Land release.
    artifact_kind = plan.product.artifact_kind
    uri = (
        f"warehouse://historical-source/{plan.source.key}/{chunk.key}/"
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
                # Empty on purpose: `source_parameter` is the column beside it and
                # `native_grid_name` is already in the release, the crosswalk, and the plan.
                # At 6.8M rows an inline duplicate costs ~690 MB and adds nothing.
                "metadata_json": {},
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


def _open_meteo_chunk(plan: HistoricalOpenMeteoArchivePlan, chunk_key: str) -> OpenMeteoArchiveChunk:
    """Return one reviewed archive chunk or reject an ungoverned source artifact."""
    try:
        return next(chunk for chunk in plan.chunks if chunk.key == chunk_key)
    except StopIteration as exc:
        raise ValueError("historical Open-Meteo chunk is not part of the reviewed plan") from exc


def _open_meteo_source_version(plan: HistoricalOpenMeteoArchivePlan, chunk: OpenMeteoArchiveChunk) -> str:
    """Return the window/grid/chunk-ordinal label for a chunk; NOT on its own a unique identity.

    `chunk_cell_count` is deliberately absent, so two plans that chunk the same grid differently
    both emit `...:cells-0000` for disjoint cell sets. Identity is the composite
    `uq_source_release_identity` (data source, source_version, payload_checksum, transform_version),
    which every lookup here binds; folding the chunk size in would change the label of already
    persisted releases and orphan a finalized release set. See execution/AGENTS.md.
    """
    return (
        f"{OPEN_METEO_ARCHIVE_SCHEMA_VERSION}:{plan.window.start_date:%Y%m%d}-"
        f"{plan.window.end_date:%Y%m%d}:{plan.grid_name}:{chunk.key}"
    )
