"""Offline contracts for bounded semantic lineage promotion archives."""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agri_data_service.execution.contracts import canonical_json_bytes
from agri_data_service.execution import promotion
from agri_data_service.execution.promotion import (
    ARCHIVE_FILE_NAMES,
    ArtifactRecord,
    DataSourceRecord,
    ExistingReleaseSet,
    MAX_ARTIFACTS_ARCHIVE_FILE_BYTES,
    MAX_TOTAL_INLINE_ARTIFACT_BYTES,
    PromotionArchive,
    PromotionError,
    PromotionSourceMetadata,
    PromotionTargetPreflight,
    PromotionTargetSnapshot,
    ReleaseSetItemRecord,
    ReleaseSetRecord,
    RestoreStepKind,
    SourceReleaseRecord,
    encode_artifact_content,
    load_promotion_archive,
    plan_semantic_restore,
    promotion_content_checksum,
    write_promotion_archive,
)


def _timestamp() -> datetime:
    return datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def _source_metadata() -> PromotionSourceMetadata:
    return PromotionSourceMetadata(
        source_label="plantgeo-local-warehouse",
        source_service_id="local-warehouse",
        postgres_major=16,
        alembic_revision="20260719_0001",
        extension_versions={
            "postgis": "3.4.0",
            "timescaledb": "2.16.0",
            "vector": "0.7.4",
            "pgcrypto": "1.3",
        },
    )


def _preflight() -> PromotionTargetPreflight:
    return PromotionTargetPreflight(
        target_label="plantgeo-spatiotemporal-db",
        target_service_id="1e166530-9c8a-4d4a-b685-a70c801fc449",
        private_control_plane=True,
        postgres_major=18,
        alembic_revision="20260719_0001",
        extension_versions={
            "postgis": "3.5.0",
            "timescaledb": "2.18.0",
            "vector": "0.8.0",
            "pgcrypto": "1.3",
        },
    )


def _archive() -> PromotionArchive:
    timestamp = _timestamp()
    source_id = uuid.UUID("8657f749-8fa3-4e2f-b069-ca3d81c1d537")
    release_id = uuid.UUID("248974e3-a7e4-414e-b94c-e94dce08d3f1")
    artifact_id = uuid.UUID("eb8c55e9-438f-4bb2-82cc-22782eace63a")
    release_set_id = uuid.UUID("7e5cb7de-ef19-476b-a9e1-07e60bce29e8")
    payload = canonical_json_bytes(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-105.2, 39.7]},
                    "properties": {"brightness": 301.2},
                }
            ],
        }
    )
    checksum = hashlib.sha256(payload).hexdigest()
    source = DataSourceRecord(
        id=source_id,
        key="nasa-firms",
        name="NASA FIRMS",
        owner="NASA",
        purpose="Current fire observations",
        license_name="Public data policy",
        citation="NASA FIRMS",
        refresh_policy={},
        allowed_client_exposure=False,
        review_state="approved",
        reviewed_at=timestamp,
        reviewed_by="data-governance",
        is_active=True,
        configuration={"ingestion_boundary": "local_capture_then_warehouse_publication"},
        created_at=timestamp,
        updated_at=timestamp,
    )
    release = SourceReleaseRecord(
        id=release_id,
        data_source_id=source_id,
        source_version="2026-07-20T00:00:00Z",
        retrieved_at=timestamp,
        data_available_at=timestamp,
        payload_checksum=checksum,
        payload_bytes=len(payload),
        schema_version="firms-geojson-v1",
        license_snapshot="Public data policy",
        query_parameters={},
        quality_summary={"feature_count": 1, "point_count": 1},
        validation_state="valid",
        validated_at=timestamp,
        created_at=timestamp,
    )
    manifest_checksum = hashlib.sha256(
        canonical_json_bytes(
            {
                "source_key": source.key,
                "source_version": release.source_version,
                "payload_checksum": checksum,
                "schema_version": release.schema_version,
            }
        )
    ).hexdigest()
    artifact = ArtifactRecord(
        id=artifact_id,
        source_release_id=release_id,
        kind="source_geojson",
        uri=f"warehouse://source-releases/{source.key}/{release.source_version}/{checksum}",
        media_type="application/geo+json",
        checksum_sha256=checksum,
        size_bytes=len(payload),
        storage_class="database_inline",
        metadata_json=release.quality_summary,
        content_base64=encode_artifact_content(payload),
        created_at=timestamp,
    )
    release_set = ReleaseSetRecord(
        id=release_set_id,
        logical_key="nasa-firms-20260720",
        as_of_time=timestamp,
        manifest_checksum=manifest_checksum,
        state="validated",
        validated_at=timestamp,
        created_at=timestamp,
    )
    item = ReleaseSetItemRecord(
        release_set_id=release_set_id,
        source_release_id=release_id,
        source_role="input",
        added_at=timestamp,
    )
    return PromotionArchive(
        source=_source_metadata(),
        data_sources=[source],
        source_releases=[release],
        artifacts=[artifact],
        release_sets=[release_set],
        release_set_items=[item],
    )


def _exact_target(archive: PromotionArchive) -> PromotionTargetSnapshot:
    release_set = archive.release_sets[0]
    return PromotionTargetSnapshot(
        data_sources=archive.data_sources,
        source_releases=archive.source_releases,
        artifacts=archive.artifacts,
        release_sets=[
            ExistingReleaseSet(
                id=release_set.id,
                logical_key=release_set.logical_key,
                as_of_time=release_set.as_of_time,
                manifest_checksum=release_set.manifest_checksum,
                state="validated",
                description=release_set.description,
                validated_at=release_set.validated_at,
                created_at=release_set.created_at,
            )
        ],
        release_set_items=archive.release_set_items,
    )


def test_semantic_archive_round_trip_is_canonical_and_manifest_has_no_raw_payload(tmp_path: Path) -> None:
    archive = _archive()
    output = tmp_path / "nasa-firms-promotion"

    manifest = write_promotion_archive(output, archive, created_at=_timestamp())
    loaded, loaded_manifest = load_promotion_archive(output)

    assert loaded == archive
    assert loaded_manifest == manifest
    assert manifest.content_checksum == promotion_content_checksum(archive)
    manifest_text = (output / "manifest.json").read_text(encoding="utf-8")
    assert '"FeatureCollection"' not in manifest_text
    assert {item.name for item in output.iterdir()} == set(ARCHIVE_FILE_NAMES.values()) | {"manifest.json"}


def test_semantic_archive_detects_tampered_data_file(tmp_path: Path) -> None:
    output = tmp_path / "nasa-firms-promotion"
    write_promotion_archive(output, _archive(), created_at=_timestamp())
    artifact_file = output / ARCHIVE_FILE_NAMES["artifacts"]
    value = json.loads(artifact_file.read_text(encoding="utf-8"))
    value[0]["content_base64"] = encode_artifact_content(b"tampered")
    artifact_file.write_bytes(canonical_json_bytes(value))

    with pytest.raises(PromotionError, match="checksum mismatch"):
        load_promotion_archive(output)


def test_artifact_file_limit_covers_base64_expansion_and_writer_refuses_overflow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    max_base64_bytes = 4 * ((MAX_TOTAL_INLINE_ARTIFACT_BYTES + 2) // 3)
    assert MAX_ARTIFACTS_ARCHIVE_FILE_BYTES >= max_base64_bytes

    monkeypatch.setattr(promotion, "MAX_ARTIFACTS_ARCHIVE_FILE_BYTES", 1)
    with pytest.raises(PromotionError, match="exceeds its bounded size"):
        write_promotion_archive(tmp_path / "too-large-for-reader", _archive(), created_at=_timestamp())


def test_writer_refuses_a_manifest_larger_than_its_reader_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(promotion, "MAX_MANIFEST_BYTES", 1)

    with pytest.raises(PromotionError, match="manifest exceeds its bounded size"):
        write_promotion_archive(tmp_path / "manifest-too-large", _archive(), created_at=_timestamp())


def test_promotion_reapplies_geojson_custody_policy_to_embedded_artifact_bytes() -> None:
    archive = _archive()
    payload = canonical_json_bytes(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-105.2, 39.7]},
                    "properties": {"accessToken": "must-not-cross-the-promotion-boundary"},
                }
            ],
        }
    )
    checksum = hashlib.sha256(payload).hexdigest()
    source = archive.data_sources[0]
    release = archive.source_releases[0].model_copy(
        update={"payload_checksum": checksum, "payload_bytes": len(payload)}
    )
    artifact = archive.artifacts[0].model_copy(
        update={
            "uri": f"warehouse://source-releases/{source.key}/{release.source_version}/{checksum}",
            "checksum_sha256": checksum,
            "size_bytes": len(payload),
            "content_base64": encode_artifact_content(payload),
        }
    )
    manifest_checksum = hashlib.sha256(
        canonical_json_bytes(
            {
                "source_key": source.key,
                "source_version": release.source_version,
                "payload_checksum": checksum,
                "schema_version": release.schema_version,
            }
        )
    ).hexdigest()
    release_set = archive.release_sets[0].model_copy(update={"manifest_checksum": manifest_checksum})

    with pytest.raises(ValueError, match="sensitive field"):
        PromotionArchive(
            source=archive.source,
            data_sources=[source],
            source_releases=[release],
            artifacts=[artifact],
            release_sets=[release_set],
            release_set_items=archive.release_set_items,
        )


def test_archive_requires_a_closed_source_release_supersession_chain() -> None:
    archive = _archive()
    unclosed_release = archive.source_releases[0].model_copy(update={"supersedes_release_id": uuid.uuid4()})

    with pytest.raises(ValueError, match="supersession must be closed"):
        PromotionArchive(
            source=archive.source,
            data_sources=archive.data_sources,
            source_releases=[unclosed_release],
            artifacts=archive.artifacts,
            release_sets=archive.release_sets,
            release_set_items=archive.release_set_items,
        )


def test_restore_plan_requires_draft_membership_then_validation_and_is_idempotent() -> None:
    archive = _archive()

    plan = plan_semantic_restore(archive, target_preflight=_preflight())

    kinds = [step.kind for step in plan.steps]
    assert kinds[-3:] == [
        RestoreStepKind.CREATE_RELEASE_SET_DRAFT,
        RestoreStepKind.ADD_RELEASE_SET_MEMBERSHIP,
        RestoreStepKind.VALIDATE_RELEASE_SET,
    ]
    assert RestoreStepKind.CREATE_RELEASE_SET_DRAFT in kinds
    assert all("pg_restore" not in step.kind.value for step in plan.steps)

    resumed = plan_semantic_restore(
        archive,
        target_preflight=_preflight(),
        target=_exact_target(archive),
    )

    assert resumed.steps == []


def test_restore_plan_can_resume_a_draft_only_through_membership_then_validation() -> None:
    archive = _archive()
    release_set = archive.release_sets[0]
    target = PromotionTargetSnapshot(
        data_sources=archive.data_sources,
        source_releases=archive.source_releases,
        artifacts=archive.artifacts,
        release_sets=[
            ExistingReleaseSet(
                id=release_set.id,
                logical_key=release_set.logical_key,
                as_of_time=release_set.as_of_time,
                manifest_checksum=release_set.manifest_checksum,
                state="draft",
                description=release_set.description,
                created_at=release_set.created_at,
            )
        ],
    )

    plan = plan_semantic_restore(archive, target_preflight=_preflight(), target=target)

    assert [step.kind for step in plan.steps] == [
        RestoreStepKind.RESUME_RELEASE_SET_DRAFT,
        RestoreStepKind.ADD_RELEASE_SET_MEMBERSHIP,
        RestoreStepKind.VALIDATE_RELEASE_SET,
    ]


def test_restore_refuses_blind_pg_restore_and_conflicting_validated_release_set() -> None:
    archive = _archive()

    with pytest.raises(PromotionError, match="blind pg_restore"):
        plan_semantic_restore(archive, target_preflight=_preflight(), transport="pg_restore")

    target = _exact_target(archive)
    release_set = target.release_sets[0].model_copy(update={"validated_at": _timestamp().replace(hour=13)})
    conflicting = target.model_copy(update={"release_sets": [release_set]})
    with pytest.raises(PromotionError, match="validation evidence"):
        plan_semantic_restore(archive, target_preflight=_preflight(), target=conflicting)
