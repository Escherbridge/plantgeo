"""Private, resumable receiver for verified normalized historical data."""

import hashlib
import json as json_parser
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any, NoReturn

from sanic import Blueprint, Request, json
from sanic.response import HTTPResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agri_data_service.config import settings
from agri_data_service.db.engine import receiver_writer_session
from agri_data_service.execution.historical_promotion import (
    ERA5_LAND_SOURCE_KEY,
    NASA_POWER_SOURCE_KEY,
    USDM_SOURCE_KEY,
    HistoricalArtifactRecord,
    HistoricalDataSourceRecord,
    HistoricalEra5CellRecord,
    HistoricalEra5CoverageAuditRecord,
    HistoricalEra5CrosswalkRecord,
    HistoricalEra5ObservationRecord,
    HistoricalNasaCellRecord,
    HistoricalNasaCoverageAuditRecord,
    HistoricalNasaCrosswalkRecord,
    HistoricalNasaObservationRecord,
    HistoricalPromotionChunk,
    HistoricalPromotionManifest,
    HistoricalPromotionRecord,
    HistoricalReleaseSetRoot,
    HistoricalSourceReleaseIdentity,
    HistoricalSourceReleaseRecord,
    HistoricalUsdmCoverageAuditRecord,
    HistoricalUsdmPolygonRecord,
    historical_source_release_key,
    historical_source_release_token,
)
from agri_data_service.models.historical import (
    CellSourceCrosswalk,
    DroughtPolygonSnapshot,
    SignalCoverageAudit,
    SignalObservation,
    SourceCoverageAudit,
    SpatialCell,
)
from agri_data_service.models.historical_promotion import (
    HistoricalPromotionArtifactReceipt,
    HistoricalPromotionBundle,
    HistoricalPromotionChunkReceipt,
    HistoricalPromotionDataSourceReceipt,
    HistoricalPromotionSourceReleaseReceipt,
    HistoricalPromotionState,
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

historical_promotion_bp = Blueprint("historical_promotion", url_prefix="/historical-promotions")
WGS84_SRID = 4326

type GridCellRecord = HistoricalNasaCellRecord | HistoricalEra5CellRecord
type GridCrosswalkRecord = HistoricalNasaCrosswalkRecord | HistoricalEra5CrosswalkRecord
type GridObservationRecord = HistoricalNasaObservationRecord | HistoricalEra5ObservationRecord
type GridCoverageRecord = HistoricalNasaCoverageAuditRecord | HistoricalEra5CoverageAuditRecord


class _PromotionAbortError(Exception):
    def __init__(self, code: str, status: int, **detail: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail


@historical_promotion_bp.post("/bundles")
async def stage_bundle(request: Request) -> HTTPResponse:  # noqa: PLR0911
    """Create the target draft release set for one immutable manifest."""
    unauthorized = _authorize(request)
    if unauthorized:
        return unauthorized
    if len(request.body) > settings.historical_promotion_max_manifest_bytes:
        return _error("manifest_too_large", 413)
    try:
        manifest = HistoricalPromotionManifest.model_validate(json_parser.loads(request.body))
    except (TypeError, ValueError):
        return _error("invalid_historical_manifest", 422)
    manifest_json = manifest.model_dump(mode="json")
    if (
        len(json_parser.dumps(manifest_json, separators=(",", ":")).encode())
        > settings.historical_promotion_max_manifest_bytes
    ):
        return _error("manifest_too_large", 413)

    try:
        async with receiver_writer_session() as session, session.begin():
            await _require_target_revision(session, manifest.minimum_target_revision)
            existing_bundle = (
                await session.execute(
                    select(HistoricalPromotionBundle)
                    .where(HistoricalPromotionBundle.manifest_checksum == manifest.manifest_checksum)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if existing_bundle is not None:
                if existing_bundle.manifest_json != manifest_json:
                    _abort("manifest_checksum_conflict", 409)
                return _bundle_response(existing_bundle, status=200)

            release_set = (
                await session.execute(
                    select(ReleaseSet)
                    .where(ReleaseSet.logical_key == manifest.release_set.logical_key)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if release_set is None:
                release_set = ReleaseSet(
                    logical_key=manifest.release_set.logical_key,
                    as_of_time=manifest.release_set.as_of_time,
                    manifest_checksum=manifest.release_set.release_set_manifest_checksum,
                    state=ReleaseSetState.DRAFT,
                    description=manifest.release_set.description,
                )
                session.add(release_set)
                await session.flush()
            elif not _draft_release_set_matches(release_set, manifest.release_set):
                _abort("release_set_conflict", 409)

            bundle = HistoricalPromotionBundle(
                manifest_checksum=manifest.manifest_checksum,
                manifest_json=manifest_json,
                release_set_id=release_set.id,
                state=HistoricalPromotionState.RECEIVING,
                expected_chunk_count=len(manifest.chunks),
                expected_record_count=manifest.total_record_count,
                received_by=_promotion_actor(),
            )
            session.add(bundle)
            await session.flush()
            return _bundle_response(bundle, status=201)
    except _PromotionAbortError as exc:
        return _abort_response(exc)
    except (IntegrityError, ValueError):
        return _error("historical_stage_conflict", 409)


@historical_promotion_bp.put("/bundles/<bundle_id:uuid>/chunks/<sequence:int>")
async def receive_chunk(  # noqa: PLR0911
    request: Request, bundle_id: uuid.UUID, sequence: int
) -> HTTPResponse:
    """Validate and atomically import one independently checksummed typed chunk."""
    unauthorized = _authorize(request)
    if unauthorized:
        return unauthorized
    if (
        len(request.body)
        > settings.historical_promotion_max_chunk_bytes + settings.historical_promotion_request_overhead_bytes
    ):
        return _error("chunk_too_large", 413)
    try:
        chunk = HistoricalPromotionChunk.model_validate(json_parser.loads(request.body))
    except (TypeError, ValueError):
        return _error("invalid_historical_chunk", 422)
    if (
        chunk.descriptor.sequence != sequence
        or chunk.descriptor.payload_bytes > settings.historical_promotion_max_chunk_bytes
    ):
        return _error("chunk_descriptor_mismatch", 422)

    try:
        async with receiver_writer_session() as session, session.begin():
            bundle = await _locked_bundle(session, bundle_id)
            manifest = _manifest(bundle)
            if sequence > len(manifest.chunks) or chunk.descriptor != manifest.chunks[sequence - 1]:
                _abort("chunk_descriptor_mismatch", 422)
            existing = await session.get(HistoricalPromotionChunkReceipt, (bundle_id, sequence))
            if existing is not None:
                if _chunk_receipt_matches(existing, chunk):
                    return _chunk_response(bundle, chunk, status=200)
                _abort("chunk_receipt_conflict", 409)
            if sequence > 1 and await session.get(HistoricalPromotionChunkReceipt, (bundle_id, sequence - 1)) is None:
                _abort("chunk_sequence_gap", 409)
            _validate_chunk_membership(manifest.release_set, chunk.records)
            for record in chunk.records:
                await _import_record(session, bundle, record)
            session.add(
                HistoricalPromotionChunkReceipt(
                    bundle_id=bundle.id,
                    sequence=sequence,
                    payload_sha256=chunk.descriptor.payload_sha256,
                    payload_bytes=chunk.descriptor.payload_bytes,
                    record_count=chunk.descriptor.record_count,
                )
            )
        return _chunk_response(bundle, chunk, status=201)
    except _PromotionAbortError as exc:
        return _abort_response(exc)
    except (IntegrityError, ValueError):
        return _error("historical_chunk_conflict", 409)


@historical_promotion_bp.put("/bundles/<bundle_id:uuid>/artifacts/<identity_token:str>")
async def receive_artifact(request: Request, bundle_id: uuid.UUID, identity_token: str) -> HTTPResponse:
    """Store one raw source package after its typed descriptor has been received."""
    unauthorized = _authorize(request)
    if unauthorized:
        return unauthorized
    if len(request.body) > settings.historical_promotion_max_artifact_bytes:
        return _error("artifact_too_large", 413)
    try:
        async with receiver_writer_session() as session, session.begin():
            bundle = await _locked_bundle(session, bundle_id)
            receipt = await _artifact_receipt_for_token(session, bundle.id, identity_token)
            if receipt is None:
                _abort("unknown_artifact_identity", 404)
            content = bytes(request.body)
            if len(content) != receipt.size_bytes or hashlib.sha256(content).hexdigest() != receipt.checksum_sha256:
                _abort("artifact_checksum_mismatch", 422)
            existing = await session.get(Artifact, receipt.artifact_id) if receipt.artifact_id else None
            if existing is not None:
                if existing.content_bytes != content or existing.source_release_id != receipt.source_release_id:
                    _abort("artifact_receipt_conflict", 409)
                return _artifact_response(bundle, receipt, status=200)
            artifact = await _find_or_create_artifact(session, receipt, content)
            receipt.artifact_id = artifact.id
            receipt.uploaded_at = datetime.now(UTC)
        return _artifact_response(bundle, receipt, status=201)
    except _PromotionAbortError as exc:
        return _abort_response(exc)
    except (IntegrityError, ValueError):
        return _error("historical_artifact_conflict", 409)


@historical_promotion_bp.post("/bundles/<bundle_id:uuid>/finalize")
async def finalize_bundle(request: Request, bundle_id: uuid.UUID) -> HTTPResponse:
    """Verify receipts and warehouse closure before validating the target release set."""
    unauthorized = _authorize(request)
    if unauthorized:
        return unauthorized
    if request.body:
        return _error("unexpected_finalize_payload", 422)
    try:
        async with receiver_writer_session() as session, session.begin():
            bundle = await _locked_bundle(session, bundle_id, allow_finalized=True)
            if bundle.state == HistoricalPromotionState.FINALIZED:
                return _bundle_response(bundle, status=200)
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": bundle.manifest_checksum}
            )
            manifest = _manifest(bundle)
            release_set = await session.get(ReleaseSet, bundle.release_set_id, with_for_update=True)
            if release_set is None or release_set.state != ReleaseSetState.DRAFT:
                _abort("release_set_not_finalizable", 409)
            release_ids = await _release_ids_for_root(session, manifest.release_set)
            await _require_complete_receipts(session, bundle, manifest, manifest.release_set, release_ids)
            await _validate_target_closure(session, release_ids)
            existing_member_count = await session.scalar(
                select(func.count()).select_from(ReleaseSetItem).where(ReleaseSetItem.release_set_id == release_set.id)
            )
            if existing_member_count:
                _abort("release_set_membership_conflict", 409)
            session.add_all(
                [
                    ReleaseSetItem(release_set_id=release_set.id, source_release_id=release_id, source_role="input")
                    for release_id in release_ids
                ]
            )
            await session.flush()
            now = datetime.now(UTC)
            release_set.state = ReleaseSetState.VALIDATED
            release_set.validated_at = now
            bundle.state = HistoricalPromotionState.FINALIZED
            bundle.finalized_at = now
        return _bundle_response(bundle, status=200)
    except _PromotionAbortError as exc:
        return _abort_response(exc)
    except (IntegrityError, ValueError):
        return _error("historical_finalize_conflict", 409)


def _authorize(request: Request) -> HTTPResponse | None:
    configured = settings.historical_promotion_token
    if (
        not settings.historical_promotion_receiver_enabled
        or configured is None
        or settings.historical_promotion_actor is None
    ):
        return _error("historical_promotion_disabled", 503)
    authorization = request.headers.get("authorization", "")
    prefix = "Bearer "
    if not authorization.startswith(prefix) or not secrets.compare_digest(
        authorization[len(prefix) :], configured.get_secret_value()
    ):
        return _error("unauthorized", 401)
    return None


def _promotion_actor() -> str:
    actor = settings.historical_promotion_actor
    if actor is None:
        raise ValueError("historical promotion actor is not configured")
    return actor


async def _locked_bundle(
    session: AsyncSession, bundle_id: uuid.UUID, *, allow_finalized: bool = False
) -> HistoricalPromotionBundle:
    bundle = (
        await session.execute(
            select(HistoricalPromotionBundle).where(HistoricalPromotionBundle.id == bundle_id).with_for_update()
        )
    ).scalar_one_or_none()
    if bundle is None:
        _abort("unknown_historical_bundle", 404)
    if bundle.state != HistoricalPromotionState.RECEIVING and not (
        allow_finalized and bundle.state == HistoricalPromotionState.FINALIZED
    ):
        _abort("historical_bundle_not_receiving", 409)
    return bundle


def _manifest(bundle: HistoricalPromotionBundle) -> HistoricalPromotionManifest:
    try:
        return HistoricalPromotionManifest.model_validate(bundle.manifest_json)
    except ValueError:
        _abort("stored_historical_manifest_invalid", 409)


async def _require_target_revision(session: AsyncSession, expected: str) -> None:
    observed = await session.scalar(text("SELECT version_num FROM public.alembic_version"))
    if observed != expected:
        _abort("target_revision_mismatch", 409)


def _draft_release_set_matches(release_set: ReleaseSet, root: HistoricalReleaseSetRoot) -> bool:
    return (
        release_set.state == ReleaseSetState.DRAFT
        and release_set.as_of_time == root.as_of_time
        and release_set.manifest_checksum == root.release_set_manifest_checksum
        and release_set.description == root.description
    )


def _validate_chunk_membership(root: HistoricalReleaseSetRoot, records: list[HistoricalPromotionRecord]) -> None:
    member_keys = {historical_source_release_key(member) for member in root.members}
    source_keys = {member.source_key for member in root.members}
    for record in records:
        if isinstance(record, HistoricalDataSourceRecord):
            if record.source_key not in source_keys:
                _abort("record_outside_release_set", 422)
        elif isinstance(record, (HistoricalNasaCellRecord, HistoricalEra5CellRecord)):
            if record.record_type == "nasa_cell" and NASA_POWER_SOURCE_KEY not in source_keys:
                _abort("record_outside_release_set", 422)
            if record.record_type == "era5_cell" and ERA5_LAND_SOURCE_KEY not in source_keys:
                _abort("record_outside_release_set", 422)
        else:
            release = _record_release(record)
            if release is None or historical_source_release_key(release) not in member_keys:
                _abort("record_outside_release_set", 422)
            expected_source = _expected_source_key(record)
            if expected_source is not None and release.source_key != expected_source:
                _abort("record_source_mismatch", 422)


def _record_release(record: HistoricalPromotionRecord) -> HistoricalSourceReleaseIdentity | None:
    if isinstance(
        record,
        (
            HistoricalSourceReleaseRecord,
            HistoricalArtifactRecord,
            HistoricalNasaCrosswalkRecord,
            HistoricalNasaObservationRecord,
            HistoricalNasaCoverageAuditRecord,
            HistoricalEra5CrosswalkRecord,
            HistoricalEra5ObservationRecord,
            HistoricalEra5CoverageAuditRecord,
            HistoricalUsdmPolygonRecord,
            HistoricalUsdmCoverageAuditRecord,
        ),
    ):
        return record.release
    return None


def _expected_source_key(record: HistoricalPromotionRecord) -> str | None:
    if isinstance(
        record,
        (
            HistoricalNasaCrosswalkRecord,
            HistoricalNasaObservationRecord,
            HistoricalNasaCoverageAuditRecord,
            HistoricalEra5CrosswalkRecord,
            HistoricalEra5ObservationRecord,
            HistoricalEra5CoverageAuditRecord,
        ),
    ):
        return ERA5_LAND_SOURCE_KEY if record.record_type.startswith("era5_") else NASA_POWER_SOURCE_KEY
    if isinstance(record, (HistoricalUsdmPolygonRecord, HistoricalUsdmCoverageAuditRecord)):
        return USDM_SOURCE_KEY
    return None


async def _import_record(
    session: AsyncSession, bundle: HistoricalPromotionBundle, record: HistoricalPromotionRecord
) -> None:
    if isinstance(record, HistoricalDataSourceRecord):
        await _ensure_data_source(session, bundle, record)
    elif isinstance(record, HistoricalSourceReleaseRecord):
        await _ensure_source_release(session, bundle, record)
    elif isinstance(record, HistoricalArtifactRecord):
        await _ensure_artifact_receipt(session, bundle, record)
    elif isinstance(record, (HistoricalNasaCellRecord, HistoricalEra5CellRecord)):
        await _ensure_cell(session, record)
    elif isinstance(record, (HistoricalNasaCrosswalkRecord, HistoricalEra5CrosswalkRecord)):
        await _ensure_crosswalk(session, record)
    elif isinstance(record, (HistoricalNasaObservationRecord, HistoricalEra5ObservationRecord)):
        await _ensure_observation(session, record)
    elif isinstance(record, (HistoricalNasaCoverageAuditRecord, HistoricalEra5CoverageAuditRecord)):
        await _ensure_grid_coverage(session, record)
    elif isinstance(record, HistoricalUsdmPolygonRecord):
        await _ensure_usdm_polygon(session, record)
    elif isinstance(record, HistoricalUsdmCoverageAuditRecord):
        await _ensure_usdm_coverage(session, record)
    else:  # pragma: no cover - discriminated union exhaustiveness guard
        raise ValueError(f"unsupported historical record type {record.record_type}")


async def _ensure_data_source(
    session: AsyncSession, bundle: HistoricalPromotionBundle, record: HistoricalDataSourceRecord
) -> DataSource:
    source = (await session.execute(select(DataSource).where(DataSource.key == record.source_key))).scalar_one_or_none()
    expected = {
        "name": record.name,
        "owner": record.owner,
        "purpose": record.purpose,
        "base_url": record.base_url,
        "license_name": record.license_name,
        "license_url": record.license_url,
        "citation": record.citation,
        "retention_days": record.retention_days,
        "reviewed_at": record.reviewed_at,
        "reviewed_by": record.reviewed_by,
        "configuration": record.configuration,
    }
    if source is None:
        source = DataSource(
            key=record.source_key,
            **expected,
            allowed_client_exposure=False,
            review_state=SourceReviewState.APPROVED,
            is_active=True,
        )
        session.add(source)
        await session.flush()
    elif (
        source.review_state != SourceReviewState.APPROVED
        or not source.is_active
        or any(getattr(source, field) != value for field, value in expected.items())
    ):
        _abort("data_source_conflict", 409)
    receipt = await session.get(HistoricalPromotionDataSourceReceipt, (bundle.id, record.source_key))
    if receipt is None:
        session.add(HistoricalPromotionDataSourceReceipt(bundle_id=bundle.id, source_key=record.source_key))
    return source


async def _ensure_source_release(
    session: AsyncSession, bundle: HistoricalPromotionBundle, record: HistoricalSourceReleaseRecord
) -> SourceRelease:
    source = (
        await session.execute(select(DataSource).where(DataSource.key == record.release.source_key))
    ).scalar_one_or_none()
    if source is None:
        _abort("source_release_before_data_source", 409)
    release = (
        await session.execute(
            select(SourceRelease).where(
                SourceRelease.data_source_id == source.id,
                SourceRelease.source_version == record.release.source_version,
                SourceRelease.payload_checksum == record.release.payload_checksum,
                SourceRelease.transform_version == record.release.transform_version,
            )
        )
    ).scalar_one_or_none()
    expected = {
        "retrieved_at": record.retrieved_at,
        "data_available_at": record.data_available_at,
        "observed_from": record.observed_from,
        "observed_to": record.observed_to,
        "payload_bytes": record.payload_bytes,
        "schema_version": record.schema_version,
        "license_snapshot": record.license_snapshot,
        "query_parameters": record.query_parameters,
        "quality_summary": record.quality_summary,
        "validation_state": ReleaseValidationState.VALID,
        "validated_at": record.validated_at,
    }
    if release is None:
        release = SourceRelease(
            data_source_id=source.id,
            source_version=record.release.source_version,
            payload_checksum=record.release.payload_checksum,
            transform_version=record.release.transform_version,
            **expected,
        )
        session.add(release)
        await session.flush()
    elif any(getattr(release, field) != value for field, value in expected.items()):
        _abort("source_release_conflict", 409)
    receipt = await session.get(HistoricalPromotionSourceReleaseReceipt, (bundle.id, release.id))
    if receipt is None:
        session.add(HistoricalPromotionSourceReleaseReceipt(bundle_id=bundle.id, source_release_id=release.id))
    return release


async def _source_release(session: AsyncSession, identity: HistoricalSourceReleaseIdentity) -> SourceRelease:
    release = (
        await session.execute(
            select(SourceRelease)
            .join(DataSource, DataSource.id == SourceRelease.data_source_id)
            .where(
                DataSource.key == identity.source_key,
                SourceRelease.source_version == identity.source_version,
                SourceRelease.payload_checksum == identity.payload_checksum,
                SourceRelease.transform_version == identity.transform_version,
            )
        )
    ).scalar_one_or_none()
    if release is None:
        _abort("referenced_source_release_missing", 409)
    return release


async def _ensure_artifact_receipt(
    session: AsyncSession, bundle: HistoricalPromotionBundle, record: HistoricalArtifactRecord
) -> None:
    release = await _source_release(session, record.release)
    if record.checksum_sha256 != release.payload_checksum or record.size_bytes != release.payload_bytes:
        _abort("artifact_source_payload_mismatch", 422)
    receipt = (
        await session.execute(
            select(HistoricalPromotionArtifactReceipt).where(
                HistoricalPromotionArtifactReceipt.bundle_id == bundle.id,
                HistoricalPromotionArtifactReceipt.source_release_id == release.id,
            )
        )
    ).scalar_one_or_none()
    expected = {
        "uri": record.uri,
        "media_type": record.media_type,
        "checksum_sha256": record.checksum_sha256,
        "size_bytes": record.size_bytes,
        "metadata_json": record.metadata,
    }
    if receipt is None:
        session.add(HistoricalPromotionArtifactReceipt(bundle_id=bundle.id, source_release_id=release.id, **expected))
    elif any(getattr(receipt, field) != value for field, value in expected.items()):
        _abort("artifact_descriptor_conflict", 409)


async def _ensure_cell(session: AsyncSession, record: GridCellRecord) -> SpatialCell:
    await _require_valid_geometry(session, record.geometry_json, "POLYGON")
    await _require_valid_geometry(session, record.centroid_json, "POINT")
    cell = (
        await session.execute(select(SpatialCell).where(SpatialCell.cell_key == record.cell_key))
    ).scalar_one_or_none()
    if cell is None:
        cell = SpatialCell(
            cell_key=record.cell_key,
            grid_name=record.grid_name,
            resolution_m=record.resolution_m,
            geometry=_geojson_geometry(record.geometry_json),
            centroid=_geojson_geometry(record.centroid_json),
            coverage_fraction=record.coverage_fraction,
        )
        session.add(cell)
        await session.flush()
    elif (
        cell.grid_name != record.grid_name
        or cell.resolution_m != record.resolution_m
        or cell.coverage_fraction != record.coverage_fraction
        or not await _geometry_matches(session, SpatialCell.geometry, cell.id, record.geometry_json)
        or not await _geometry_matches(session, SpatialCell.centroid, cell.id, record.centroid_json)
    ):
        _abort("spatial_cell_conflict", 409)
    return cell


async def _ensure_crosswalk(session: AsyncSession, record: GridCrosswalkRecord) -> None:
    release = await _source_release(session, record.release)
    cell = (
        await session.execute(select(SpatialCell).where(SpatialCell.cell_key == record.cell_key))
    ).scalar_one_or_none()
    if cell is None:
        _abort("crosswalk_before_cell", 409)
    await _require_valid_geometry(session, record.native_geometry_json, "POINT")
    existing = (
        await session.execute(
            select(CellSourceCrosswalk).where(
                CellSourceCrosswalk.source_release_id == release.id,
                CellSourceCrosswalk.native_feature_key == record.native_feature_key,
                CellSourceCrosswalk.cell_id == cell.id,
            )
        )
    ).scalar_one_or_none()
    expected = {
        "native_resolution_m": record.native_resolution_m,
        "spatial_support_kind": record.spatial_support_kind,
        "mapping_method": record.mapping_method,
        "coverage_fraction": record.coverage_fraction,
        "metadata_json": record.metadata,
    }
    if existing is None:
        session.add(
            CellSourceCrosswalk(
                source_release_id=release.id,
                cell_id=cell.id,
                native_feature_key=record.native_feature_key,
                native_geometry=_geojson_geometry(record.native_geometry_json),
                **expected,
            )
        )
    elif any(getattr(existing, field) != value for field, value in expected.items()) or not await _geometry_matches(
        session, CellSourceCrosswalk.native_geometry, existing.id, record.native_geometry_json
    ):
        _abort("crosswalk_conflict", 409)


async def _ensure_observation(session: AsyncSession, record: GridObservationRecord) -> None:
    release = await _source_release(session, record.release)
    cell = (
        await session.execute(select(SpatialCell).where(SpatialCell.cell_key == record.cell_key))
    ).scalar_one_or_none()
    if cell is None:
        _abort("observation_before_cell", 409)
    filters = (
        SignalObservation.source_release_id == release.id,
        SignalObservation.cell_id == cell.id,
        SignalObservation.signal_name == record.signal_name,
        SignalObservation.source_parameter == record.source_parameter,
        SignalObservation.support_key == record.support_key,
        SignalObservation.observed_at == record.observed_at,
    )
    existing = (await session.execute(select(SignalObservation).where(*filters))).scalar_one_or_none()
    expected = {
        "valid_from": record.valid_from,
        "valid_to": record.valid_to,
        "data_available_at": record.data_available_at,
        "original_value": record.original_value,
        "original_unit": record.original_unit,
        "normalized_value": record.normalized_value,
        "normalized_unit": record.normalized_unit,
        "quality_flag": record.quality_flag,
        "coverage_fraction": record.coverage_fraction,
        "is_observed": record.is_observed,
        "metadata_json": record.metadata,
    }
    if existing is None:
        session.add(
            SignalObservation(
                source_release_id=release.id,
                cell_id=cell.id,
                signal_name=record.signal_name,
                source_parameter=record.source_parameter,
                support_key=record.support_key,
                observed_at=record.observed_at,
                **expected,
            )
        )
    elif any(getattr(existing, field) != value for field, value in expected.items()):
        _abort("observation_conflict", 409)


async def _ensure_grid_coverage(session: AsyncSession, record: GridCoverageRecord) -> None:
    release = await _source_release(session, record.release)
    cell = (
        await session.execute(select(SpatialCell).where(SpatialCell.cell_key == record.cell_key))
    ).scalar_one_or_none()
    if cell is None:
        _abort("coverage_before_cell", 409)
    filters = (
        SignalCoverageAudit.source_release_id == release.id,
        SignalCoverageAudit.cell_id == cell.id,
        SignalCoverageAudit.signal_name == record.signal_name,
        SignalCoverageAudit.source_parameter == record.source_parameter,
        SignalCoverageAudit.support_key == record.support_key,
        SignalCoverageAudit.window_start == record.window_start,
        SignalCoverageAudit.window_end == record.window_end,
    )
    existing = (await session.execute(select(SignalCoverageAudit).where(*filters))).scalar_one_or_none()
    expected = {
        "expected_observation_count": record.expected_observation_count,
        "received_observation_count": record.received_observation_count,
        "status": record.status,
        "details": record.details,
    }
    if existing is None:
        session.add(
            SignalCoverageAudit(
                source_release_id=release.id,
                cell_id=cell.id,
                signal_name=record.signal_name,
                source_parameter=record.source_parameter,
                support_key=record.support_key,
                window_start=record.window_start,
                window_end=record.window_end,
                **expected,
            )
        )
    elif any(getattr(existing, field) != value for field, value in expected.items()):
        _abort("grid_coverage_conflict", 409)


async def _ensure_usdm_polygon(session: AsyncSession, record: HistoricalUsdmPolygonRecord) -> None:
    release = await _source_release(session, record.release)
    await _require_valid_geometry(session, record.geometry_json, "MULTIPOLYGON")
    existing = (
        await session.execute(
            select(DroughtPolygonSnapshot).where(
                DroughtPolygonSnapshot.source_release_id == release.id,
                DroughtPolygonSnapshot.severity_class == record.severity_class,
                DroughtPolygonSnapshot.impact_type == record.impact_type,
                DroughtPolygonSnapshot.geometry_checksum == record.geometry_checksum,
            )
        )
    ).scalar_one_or_none()
    expected = {
        "issue_date": record.issue_date,
        "data_available_at": record.data_available_at,
        "metadata_json": {**record.metadata, "feature_key": record.feature_key},
    }
    if existing is None:
        session.add(
            DroughtPolygonSnapshot(
                source_release_id=release.id,
                severity_class=record.severity_class,
                impact_type=record.impact_type,
                geometry_checksum=record.geometry_checksum,
                geometry=_geojson_geometry(record.geometry_json),
                **expected,
            )
        )
    elif any(getattr(existing, field) != value for field, value in expected.items()) or not await _geometry_matches(
        session, DroughtPolygonSnapshot.geometry, existing.id, record.geometry_json
    ):
        _abort("usdm_polygon_conflict", 409)


async def _ensure_usdm_coverage(session: AsyncSession, record: HistoricalUsdmCoverageAuditRecord) -> None:
    release = await _source_release(session, record.release)
    filters = (
        SourceCoverageAudit.source_release_id == release.id,
        SourceCoverageAudit.scope_key == record.scope_key,
        SourceCoverageAudit.window_start == record.window_start,
        SourceCoverageAudit.window_end == record.window_end,
    )
    existing = (await session.execute(select(SourceCoverageAudit).where(*filters))).scalar_one_or_none()
    expected = {
        "expected_feature_count": record.expected_feature_count,
        "received_feature_count": record.received_feature_count,
        "status": record.status,
        "details": record.details,
    }
    if existing is None:
        session.add(
            SourceCoverageAudit(
                source_release_id=release.id,
                scope_key=record.scope_key,
                window_start=record.window_start,
                window_end=record.window_end,
                **expected,
            )
        )
    elif any(getattr(existing, field) != value for field, value in expected.items()):
        _abort("usdm_coverage_conflict", 409)


def _geojson_geometry(value: str) -> Any:
    return func.ST_SetSRID(func.ST_GeomFromGeoJSON(value), 4326)


async def _require_valid_geometry(session: AsyncSession, geometry_json: str, expected_type: str) -> None:
    result = await session.execute(
        text(
            """
            SELECT ST_IsValid(ST_SetSRID(ST_GeomFromGeoJSON(CAST(:geometry AS json)), 4326)) AS valid,
                   ST_SRID(ST_SetSRID(ST_GeomFromGeoJSON(CAST(:geometry AS json)), 4326)) AS srid,
                   ST_GeometryType(ST_SetSRID(ST_GeomFromGeoJSON(CAST(:geometry AS json)), 4326)) AS geometry_type
            """
        ),
        {"geometry": geometry_json},
    )
    valid, srid, geometry_type = result.one()
    expected_geometry_type = {
        "POINT": "ST_Point",
        "POLYGON": "ST_Polygon",
        "MULTIPOLYGON": "ST_MultiPolygon",
    }[expected_type]
    if not valid or srid != WGS84_SRID or geometry_type != expected_geometry_type:
        _abort("invalid_promoted_geometry", 422)


async def _geometry_matches(session: AsyncSession, column: Any, row_id: Any, geometry_json: str) -> bool:
    return bool(
        await session.scalar(
            select(func.ST_Equals(column, _geojson_geometry(geometry_json))).where(column.table.c.id == row_id)
        )
    )


def _chunk_receipt_matches(receipt: HistoricalPromotionChunkReceipt, chunk: HistoricalPromotionChunk) -> bool:
    descriptor = chunk.descriptor
    return (
        receipt.payload_sha256 == descriptor.payload_sha256
        and receipt.payload_bytes == descriptor.payload_bytes
        and receipt.record_count == descriptor.record_count
    )


async def _artifact_receipt_for_token(
    session: AsyncSession, bundle_id: uuid.UUID, token: str
) -> HistoricalPromotionArtifactReceipt | None:
    rows = await session.execute(
        select(HistoricalPromotionArtifactReceipt, SourceRelease, DataSource)
        .join(SourceRelease, SourceRelease.id == HistoricalPromotionArtifactReceipt.source_release_id)
        .join(DataSource, DataSource.id == SourceRelease.data_source_id)
        .where(HistoricalPromotionArtifactReceipt.bundle_id == bundle_id)
    )
    for receipt, release, source in rows.tuples():
        identity = HistoricalSourceReleaseIdentity(
            source_key=source.key,
            source_version=release.source_version,
            payload_checksum=release.payload_checksum,
            transform_version=release.transform_version,
        )
        if secrets.compare_digest(token, historical_source_release_token(identity)):
            return receipt
    return None


async def _find_or_create_artifact(
    session: AsyncSession, receipt: HistoricalPromotionArtifactReceipt, content: bytes
) -> Artifact:
    existing = (
        await session.execute(
            select(Artifact).where(Artifact.uri == receipt.uri, Artifact.checksum_sha256 == receipt.checksum_sha256)
        )
    ).scalar_one_or_none()
    if existing is None:
        artifact = Artifact(
            source_release_id=receipt.source_release_id,
            kind="raw_source",
            uri=receipt.uri,
            media_type=receipt.media_type,
            checksum_sha256=receipt.checksum_sha256,
            size_bytes=receipt.size_bytes,
            storage_class="database_inline",
            metadata_json=receipt.metadata_json,
            content_bytes=content,
        )
        session.add(artifact)
        await session.flush()
        return artifact
    if (
        existing.source_release_id != receipt.source_release_id
        or existing.size_bytes != receipt.size_bytes
        or existing.metadata_json != receipt.metadata_json
        or existing.content_bytes != content
    ):
        _abort("artifact_content_address_conflict", 409)
    return existing


async def _require_complete_receipts(
    session: AsyncSession,
    bundle: HistoricalPromotionBundle,
    manifest: HistoricalPromotionManifest,
    root: HistoricalReleaseSetRoot,
    release_ids: list[uuid.UUID],
) -> None:
    receipts = (
        (
            await session.execute(
                select(HistoricalPromotionChunkReceipt).where(HistoricalPromotionChunkReceipt.bundle_id == bundle.id)
            )
        )
        .scalars()
        .all()
    )
    expected = {descriptor.sequence: descriptor for descriptor in manifest.chunks}
    if len(receipts) != len(expected) or any(
        receipt.sequence not in expected
        or receipt.payload_sha256 != expected[receipt.sequence].payload_sha256
        or receipt.payload_bytes != expected[receipt.sequence].payload_bytes
        or receipt.record_count != expected[receipt.sequence].record_count
        for receipt in receipts
    ):
        _abort("incomplete_chunk_receipts", 409)
    if sum(receipt.record_count for receipt in receipts) != manifest.total_record_count:
        _abort("historical_record_count_mismatch", 409)
    data_source_receipts = set(
        (
            await session.execute(
                select(HistoricalPromotionDataSourceReceipt.source_key).where(
                    HistoricalPromotionDataSourceReceipt.bundle_id == bundle.id
                )
            )
        ).scalars()
    )
    if data_source_receipts != {identity.source_key for identity in root.members}:
        _abort("incomplete_data_source_receipts", 409)
    source_release_receipts = set(
        (
            await session.execute(
                select(HistoricalPromotionSourceReleaseReceipt.source_release_id).where(
                    HistoricalPromotionSourceReleaseReceipt.bundle_id == bundle.id
                )
            )
        ).scalars()
    )
    if source_release_receipts != set(release_ids):
        _abort("incomplete_source_release_receipts", 409)
    artifacts = (
        (
            await session.execute(
                select(HistoricalPromotionArtifactReceipt, Artifact)
                .outerjoin(Artifact, Artifact.id == HistoricalPromotionArtifactReceipt.artifact_id)
                .where(HistoricalPromotionArtifactReceipt.bundle_id == bundle.id)
            )
        )
        .tuples()
        .all()
    )
    if len(artifacts) != len(release_ids) or {receipt.source_release_id for receipt, _ in artifacts} != set(
        release_ids
    ):
        _abort("incomplete_artifact_receipts", 409)
    if any(
        artifact is None
        or receipt.uploaded_at is None
        or artifact.source_release_id != receipt.source_release_id
        or artifact.kind != "raw_source"
        or artifact.checksum_sha256 != receipt.checksum_sha256
        or artifact.size_bytes != receipt.size_bytes
        or artifact.content_bytes is None
        or len(artifact.content_bytes) != receipt.size_bytes
        or hashlib.sha256(artifact.content_bytes).hexdigest() != receipt.checksum_sha256
        for receipt, artifact in artifacts
    ):
        _abort("incomplete_artifact_receipts", 409)


async def _release_ids_for_root(session: AsyncSession, root: HistoricalReleaseSetRoot) -> list[uuid.UUID]:
    return [(await _source_release(session, identity)).id for identity in root.members]


async def _validate_target_closure(session: AsyncSession, release_ids: list[uuid.UUID]) -> None:
    grid_release_ids = (
        (
            await session.execute(
                select(SourceRelease.id)
                .join(DataSource, DataSource.id == SourceRelease.data_source_id)
                .where(
                    SourceRelease.id.in_(release_ids), DataSource.key.in_([NASA_POWER_SOURCE_KEY, ERA5_LAND_SOURCE_KEY])
                )
            )
        )
        .scalars()
        .all()
    )
    if grid_release_ids:
        missing_crosswalk = await session.scalar(
            select(SourceRelease.id)
            .where(SourceRelease.id.in_(grid_release_ids))
            .where(
                ~SourceRelease.id.in_(
                    select(CellSourceCrosswalk.source_release_id).where(
                        CellSourceCrosswalk.source_release_id.in_(grid_release_ids)
                    )
                )
            )
            .limit(1)
        )
        if missing_crosswalk is not None:
            _abort("incomplete_grid_crosswalk", 409)
        coverage_rows = (
            (
                await session.execute(
                    select(SignalCoverageAudit).where(SignalCoverageAudit.source_release_id.in_(grid_release_ids))
                )
            )
            .scalars()
            .all()
        )
        if (
            not coverage_rows
            or {row.source_release_id for row in coverage_rows} != set(grid_release_ids)
            or any(row.status != "complete" for row in coverage_rows)
        ):
            _abort("incomplete_grid_coverage", 409)
        for coverage in coverage_rows:
            received = await session.scalar(
                select(func.count())
                .select_from(SignalObservation)
                .where(
                    SignalObservation.source_release_id == coverage.source_release_id,
                    SignalObservation.cell_id == coverage.cell_id,
                    SignalObservation.signal_name == coverage.signal_name,
                    SignalObservation.source_parameter == coverage.source_parameter,
                    SignalObservation.support_key == coverage.support_key,
                    SignalObservation.is_observed.is_(True),
                    SignalObservation.observed_at >= coverage.window_start,
                    SignalObservation.observed_at <= coverage.window_end,
                )
            )
            if received != coverage.received_observation_count:
                _abort("grid_coverage_count_mismatch", 409)
    usdm_release_ids = (
        (
            await session.execute(
                select(SourceRelease.id)
                .join(DataSource, DataSource.id == SourceRelease.data_source_id)
                .where(SourceRelease.id.in_(release_ids), DataSource.key == USDM_SOURCE_KEY)
            )
        )
        .scalars()
        .all()
    )
    for release_id in usdm_release_ids:
        usdm_coverage_rows = (
            (
                await session.execute(
                    select(SourceCoverageAudit).where(SourceCoverageAudit.source_release_id == release_id)
                )
            )
            .scalars()
            .all()
        )
        if len(usdm_coverage_rows) != 1 or usdm_coverage_rows[0].status != "complete":
            _abort("incomplete_usdm_coverage", 409)
        polygons = await session.scalar(
            select(func.count())
            .select_from(DroughtPolygonSnapshot)
            .where(DroughtPolygonSnapshot.source_release_id == release_id)
        )
        if polygons != usdm_coverage_rows[0].received_feature_count:
            _abort("usdm_coverage_count_mismatch", 409)


def _bundle_response(bundle: HistoricalPromotionBundle, *, status: int) -> HTTPResponse:
    return json(
        {
            "bundle_id": str(bundle.id),
            "manifest_checksum": bundle.manifest_checksum,
            "release_set_id": str(bundle.release_set_id),
            "state": bundle.state,
            "expected_chunk_count": bundle.expected_chunk_count,
            "expected_record_count": bundle.expected_record_count,
        },
        status=status,
    )


def _chunk_response(bundle: HistoricalPromotionBundle, chunk: HistoricalPromotionChunk, *, status: int) -> HTTPResponse:
    return json(
        {
            "bundle_id": str(bundle.id),
            "sequence": chunk.descriptor.sequence,
            "payload_sha256": chunk.descriptor.payload_sha256,
            "received": True,
        },
        status=status,
    )


def _artifact_response(
    bundle: HistoricalPromotionBundle, receipt: HistoricalPromotionArtifactReceipt, *, status: int
) -> HTTPResponse:
    return json(
        {
            "bundle_id": str(bundle.id),
            "artifact_checksum": receipt.checksum_sha256,
            "uploaded": receipt.uploaded_at is not None,
        },
        status=status,
    )


def _abort(code: str, status: int, **detail: Any) -> NoReturn:
    raise _PromotionAbortError(code, status, **detail)


def _abort_response(exc: _PromotionAbortError) -> HTTPResponse:
    return json({"error": exc.code, **exc.detail}, status=exc.status)


def _error(code: str, status: int) -> HTTPResponse:
    return json({"error": code}, status=status)
