"""Tests for disk-spooled historical promotion without a database or network dependency."""

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from agri_data_service.execution.contracts import canonical_json_bytes
from agri_data_service.execution.historical_export import (
    HistoricalPromotionArtifactUpload,
    HistoricalPromotionError,
    HistoricalPromotionSpoolWriter,
    LocalHistoricalPromotionExporter,
    load_historical_promotion_spool,
)
from agri_data_service.execution.historical_promotion import (
    ERA5_LAND_SOURCE_KEY,
    MAX_HISTORICAL_ARTIFACT_BYTES,
    MAX_HISTORICAL_PROMOTION_CHUNK_BYTES,
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
    HistoricalReleaseSetRoot,
    HistoricalSourceReleaseIdentity,
    HistoricalSourceReleaseRecord,
    HistoricalUsdmCoverageAuditRecord,
    HistoricalUsdmPolygonRecord,
    historical_record_key,
    historical_source_release_token,
)

_TEST_CHUNK_BYTES = 1_500
_PROMOTION_BOUND_BYTES = 8_000_000
_OBSERVED_USDM_GEOMETRY_BYTES = 5_175_875


def _time() -> datetime:
    return datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def _geometry(kind: str, coordinates: list[object]) -> str:
    return canonical_json_bytes({"type": kind, "coordinates": coordinates}).decode()


def _release(source_key: str) -> HistoricalSourceReleaseIdentity:
    payload = f"{source_key}-payload".encode()
    return HistoricalSourceReleaseIdentity(
        source_key=source_key,
        source_version="2026-07-20",
        payload_checksum=hashlib.sha256(payload).hexdigest(),
        transform_version=f"{source_key}-v1",
    )


def _core_records() -> tuple[HistoricalReleaseSetRoot, list[object]]:
    nasa = _release(NASA_POWER_SOURCE_KEY)
    era5 = _release(ERA5_LAND_SOURCE_KEY)
    usdm = _release(USDM_SOURCE_KEY)
    root = HistoricalReleaseSetRoot(
        logical_key="north-america-four-year-history",
        as_of_time=_time(),
        release_set_manifest_checksum=hashlib.sha256(b"root").hexdigest(),
        members=sorted(
            [nasa, era5, usdm],
            key=lambda value: (
                value.source_key,
                value.source_version,
                value.payload_checksum,
                value.transform_version,
            ),
        ),
    )
    polygon = _geometry("Polygon", [[[-105.1, 39.7], [-105.0, 39.7], [-105.0, 39.8], [-105.1, 39.7]]])
    multipolygon = _geometry("MultiPolygon", [[[[-105.1, 39.7], [-105.0, 39.7], [-105.0, 39.8], [-105.1, 39.7]]]])
    records: list[object] = [
        HistoricalDataSourceRecord(
            source_key=source_key,
            name=source_key,
            owner="Reviewed source operator",
            purpose="Historical environmental evidence",
            license_name="Reviewed terms",
            citation=source_key,
            reviewed_at=_time(),
            reviewed_by="data-governance",
        )
        for source_key in (NASA_POWER_SOURCE_KEY, ERA5_LAND_SOURCE_KEY, USDM_SOURCE_KEY)
    ]
    for release in (nasa, era5, usdm):
        records.extend(
            (
                HistoricalSourceReleaseRecord(
                    release=release,
                    retrieved_at=_time(),
                    data_available_at=_time(),
                    observed_from=_time(),
                    observed_to=_time(),
                    payload_bytes=len(f"{release.source_key}-payload"),
                    schema_version="source-v1",
                    license_snapshot="Reviewed terms",
                    validated_at=_time(),
                ),
                HistoricalArtifactRecord(
                    release=release,
                    uri=f"source://{release.source_key}/2026-07-20",
                    media_type="application/octet-stream",
                    checksum_sha256=release.payload_checksum,
                    size_bytes=len(f"{release.source_key}-payload"),
                ),
            )
        )
    records.extend(
        (
            HistoricalNasaCellRecord(
                cell_key="nasa:cell-a",
                grid_name="nasa-power-0.5-degree",
                resolution_m=55_660,
                geometry_json=polygon,
                centroid_json=_geometry("Point", [-105.05, 39.75]),
                coverage_fraction=1,
            ),
            HistoricalNasaCrosswalkRecord(
                release=nasa,
                cell_key="nasa:cell-a",
                native_feature_key="nasa:cell-a",
                native_geometry_json=_geometry("Point", [-105.05, 39.75]),
                native_resolution_m=55_660,
                spatial_support_kind="native_grid_cell",
                mapping_method="source-grid-centroid",
                coverage_fraction=1,
            ),
            HistoricalNasaObservationRecord(
                release=nasa,
                cell_key="nasa:cell-a",
                signal_name="temperature_c",
                source_parameter="T2M",
                observed_at=_time(),
                data_available_at=_time(),
                original_value=20,
                original_unit="C",
                normalized_value=20,
                normalized_unit="C",
                is_observed=True,
            ),
            HistoricalNasaCoverageAuditRecord(
                release=nasa,
                cell_key="nasa:cell-a",
                signal_name="temperature_c",
                source_parameter="T2M",
                window_start=_time(),
                window_end=_time(),
                expected_observation_count=1,
                received_observation_count=1,
                status="complete",
            ),
            HistoricalEra5CellRecord(
                cell_key="era5:cell-a",
                grid_name="era5-land-0.1-degree",
                resolution_m=9_000,
                geometry_json=polygon,
                centroid_json=_geometry("Point", [-105.05, 39.75]),
                coverage_fraction=1,
            ),
            HistoricalEra5CrosswalkRecord(
                release=era5,
                cell_key="era5:cell-a",
                native_feature_key="era5:cell-a",
                native_geometry_json=_geometry("Point", [-105.05, 39.75]),
                native_resolution_m=9_000,
                spatial_support_kind="native_grid_cell",
                mapping_method="source-grid-centroid",
                coverage_fraction=1,
            ),
            HistoricalEra5ObservationRecord(
                release=era5,
                cell_key="era5:cell-a",
                signal_name="soil_moisture",
                source_parameter="swvl1",
                observed_at=_time(),
                data_available_at=_time(),
                original_value=0.2,
                original_unit="m3/m3",
                normalized_value=0.2,
                normalized_unit="m3/m3",
                is_observed=True,
            ),
            HistoricalEra5CoverageAuditRecord(
                release=era5,
                cell_key="era5:cell-a",
                signal_name="soil_moisture",
                source_parameter="swvl1",
                window_start=_time(),
                window_end=_time(),
                expected_observation_count=1,
                received_observation_count=1,
                status="complete",
            ),
            HistoricalUsdmPolygonRecord(
                release=usdm,
                issue_date=date(2026, 7, 14),
                feature_key="20260714:DM2:example",
                severity_class=2,
                geometry_json=multipolygon,
                geometry_checksum=hashlib.sha256(multipolygon.encode()).hexdigest(),
                data_available_at=_time(),
            ),
            HistoricalUsdmCoverageAuditRecord(
                release=usdm,
                scope_key="united-states-national-native-vector",
                window_start=_time(),
                window_end=_time(),
                expected_feature_count=1,
                received_feature_count=1,
                status="complete",
            ),
        )
    )
    return root, records


def test_spools_sorted_bounded_chunks_and_a_recoverable_checkpoint(tmp_path: Path) -> None:
    root, records = _core_records()
    writer = HistoricalPromotionSpoolWriter(
        root_directory=tmp_path,
        release_set=root,
        minimum_target_revision="20260720_0004",
        max_chunk_bytes=_TEST_CHUNK_BYTES,
    )
    for record in sorted(records, key=historical_record_key):
        writer.append(record)  # type: ignore[arg-type]
    spool = writer.finish(
        HistoricalPromotionArtifactUpload(release=member, token=historical_source_release_token(member))
        for member in root.members
    )

    recovered = load_historical_promotion_spool(spool.directory)

    assert recovered.manifest == spool.manifest
    assert len(recovered.manifest.chunks) > 1
    assert all(descriptor.payload_bytes <= _TEST_CHUNK_BYTES for descriptor in recovered.manifest.chunks)
    assert recovered.checkpoint_path.exists()
    assert [upload.token for upload in recovered.artifacts] == sorted(upload.token for upload in recovered.artifacts)


def test_refuses_to_spool_an_incomplete_required_source_set(tmp_path: Path) -> None:
    nasa = _release(NASA_POWER_SOURCE_KEY)
    root = HistoricalReleaseSetRoot(
        logical_key="partial-history",
        as_of_time=_time(),
        release_set_manifest_checksum=hashlib.sha256(b"partial").hexdigest(),
        members=[nasa],
    )
    writer = HistoricalPromotionSpoolWriter(
        root_directory=tmp_path,
        release_set=root,
        minimum_target_revision="20260720_0004",
    )

    with pytest.raises(HistoricalPromotionError, match="complete NASA POWER, ERA5-Land, and USDM membership"):
        writer.finish([])


def test_transport_bounds_accommodate_the_observed_usdm_geometry_and_source_artifact_limit() -> None:
    assert MAX_HISTORICAL_PROMOTION_CHUNK_BYTES == _PROMOTION_BOUND_BYTES
    assert MAX_HISTORICAL_ARTIFACT_BYTES == _PROMOTION_BOUND_BYTES
    assert MAX_HISTORICAL_PROMOTION_CHUNK_BYTES > _OBSERVED_USDM_GEOMETRY_BYTES


async def test_local_exporter_preserves_global_record_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, raw_records = _core_records()
    records = sorted(raw_records, key=historical_record_key)
    data_sources = {record.source_key: record for record in records if isinstance(record, HistoricalDataSourceRecord)}
    source_releases = {
        record.release.source_key: record for record in records if isinstance(record, HistoricalSourceReleaseRecord)
    }
    artifacts = {
        record.release.source_key: record for record in records if isinstance(record, HistoricalArtifactRecord)
    }
    nasa_records = {record.record_type: record for record in records if record.record_type.startswith("nasa_")}
    era5_records = {record.record_type: record for record in records if record.record_type.startswith("era5_")}
    usdm_records = {record.record_type: record for record in records if record.record_type.startswith("usdm_")}
    members = tuple(
        SimpleNamespace(
            source=SimpleNamespace(key=release.source_key),
            source_release_id=uuid4(),
            identity=release,
        )
        for release in root.members
    )
    local_root = SimpleNamespace(
        logical_key=root.logical_key,
        as_of_time=root.as_of_time,
        manifest_checksum=root.release_set_manifest_checksum,
        description=None,
    )
    artifact_receipts = {member.source_release_id: SimpleNamespace() for member in members}
    exporter = LocalHistoricalPromotionExporter(spool_root=tmp_path)

    async def load_root(_session: Any, _release_set_key: str) -> tuple[Any, tuple[Any, ...], dict[Any, Any]]:
        return local_root, members, artifact_receipts

    async def emit_cells(
        _session: Any,
        writer: HistoricalPromotionSpoolWriter,
        _members: Any,
        source_key: str,
    ) -> None:
        records_by_type = nasa_records if source_key == NASA_POWER_SOURCE_KEY else era5_records
        writer.append(records_by_type["nasa_cell" if source_key == NASA_POWER_SOURCE_KEY else "era5_cell"])

    async def emit_crosswalk(_session: Any, writer: HistoricalPromotionSpoolWriter, member: Any) -> None:
        records_by_type = nasa_records if member.source.key == NASA_POWER_SOURCE_KEY else era5_records
        writer.append(
            records_by_type["nasa_crosswalk" if member.source.key == NASA_POWER_SOURCE_KEY else "era5_crosswalk"]
        )

    async def emit_observation(_session: Any, writer: HistoricalPromotionSpoolWriter, member: Any) -> None:
        records_by_type = nasa_records if member.source.key == NASA_POWER_SOURCE_KEY else era5_records
        writer.append(
            records_by_type["nasa_observation" if member.source.key == NASA_POWER_SOURCE_KEY else "era5_observation"]
        )

    async def emit_coverage(_session: Any, writer: HistoricalPromotionSpoolWriter, member: Any) -> None:
        records_by_type = nasa_records if member.source.key == NASA_POWER_SOURCE_KEY else era5_records
        writer.append(
            records_by_type["nasa_coverage" if member.source.key == NASA_POWER_SOURCE_KEY else "era5_coverage"]
        )

    async def emit_usdm_polygons(_session: Any, writer: HistoricalPromotionSpoolWriter, _member: Any) -> None:
        writer.append(usdm_records["usdm_polygon"])

    async def emit_usdm_coverage(_session: Any, writer: HistoricalPromotionSpoolWriter, _member: Any) -> None:
        writer.append(usdm_records["usdm_coverage"])

    monkeypatch.setattr(exporter, "_load_root", load_root)
    monkeypatch.setattr(exporter, "_data_source_record", lambda source: data_sources[source.key])
    monkeypatch.setattr(exporter, "_source_release_record", lambda member: source_releases[member.source.key])
    monkeypatch.setattr(exporter, "_artifact_record", lambda member, _artifact: artifacts[member.source.key])
    monkeypatch.setattr(exporter, "_emit_grid_cells", emit_cells)
    monkeypatch.setattr(exporter, "_emit_grid_crosswalks", emit_crosswalk)
    monkeypatch.setattr(exporter, "_emit_grid_observations", emit_observation)
    monkeypatch.setattr(exporter, "_emit_grid_coverage", emit_coverage)
    monkeypatch.setattr(exporter, "_emit_usdm_polygons", emit_usdm_polygons)
    monkeypatch.setattr(exporter, "_emit_usdm_coverage", emit_usdm_coverage)

    spool = await exporter.spool(None, release_set_key=root.logical_key)

    assert spool.manifest.total_record_count == len(records)
