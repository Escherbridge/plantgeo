"""Focused offline tests for the historical typed-promotion contract."""

import hashlib
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from agri_data_service.execution.contracts import canonical_json_bytes
from agri_data_service.execution.historical_promotion import (
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
    HistoricalPromotionBundle,
    HistoricalPromotionRecord,
    HistoricalReleaseSetRoot,
    HistoricalSourceReleaseIdentity,
    HistoricalSourceReleaseRecord,
    HistoricalUsdmCoverageAuditRecord,
    HistoricalUsdmPolygonRecord,
    build_historical_promotion_bundle,
)

SMALL_CHUNK_BYTES = 1_400


def _time() -> datetime:
    return datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def _geometry(kind: str, coordinates: list[object]) -> str:
    return canonical_json_bytes({"type": kind, "coordinates": coordinates}).decode("utf-8")


def _release(source_key: str, suffix: str) -> HistoricalSourceReleaseIdentity:
    return HistoricalSourceReleaseIdentity(
        source_key=source_key,
        source_version=f"2026-07-20-{suffix}",
        payload_checksum=hashlib.sha256(f"{source_key}-{suffix}".encode()).hexdigest(),
        transform_version=f"{source_key}-transform-v1",
    )


def _artifact(release: HistoricalSourceReleaseIdentity, suffix: str) -> HistoricalArtifactRecord:
    content = f"{release.source_key}-{suffix}".encode()
    assert hashlib.sha256(content).hexdigest() == release.payload_checksum
    return HistoricalArtifactRecord(
        release=release,
        kind="raw_source",
        uri=f"source://{release.source_key}/{release.source_version}",
        media_type="application/octet-stream",
        checksum_sha256=release.payload_checksum,
        size_bytes=len(content),
        metadata={"source_kind": release.source_key},
    )


def _data_source(source_key: str, name: str, purpose: str) -> HistoricalDataSourceRecord:
    return HistoricalDataSourceRecord(
        source_key=source_key,
        name=name,
        owner="Source operator",
        purpose=purpose,
        license_name="Reviewed provider terms",
        citation=name,
        reviewed_at=_time(),
        reviewed_by="data-governance",
        configuration={"source_kind": source_key.replace("-", "_")},
    )


def _source_release(release: HistoricalSourceReleaseIdentity, schema_version: str) -> HistoricalSourceReleaseRecord:
    return HistoricalSourceReleaseRecord(
        release=release,
        retrieved_at=_time(),
        data_available_at=_time(),
        observed_from=_time(),
        observed_to=_time(),
        payload_bytes=len(f"{release.source_key}-{release.source_version.removeprefix('2026-07-20-')}".encode()),
        schema_version=schema_version,
        license_snapshot="Reviewed provider terms",
        validated_at=_time(),
    )


def _records() -> tuple[HistoricalReleaseSetRoot, list[HistoricalPromotionRecord]]:
    nasa = _release("nasa-power-daily", "cell-a")
    era5 = _release("era5-land", "cell-b")
    usdm = _release("usdm-weekly", "20260715")
    root = HistoricalReleaseSetRoot(
        logical_key="four-year-history-20260720",
        as_of_time=_time(),
        release_set_manifest_checksum=hashlib.sha256(b"release-set").hexdigest(),
        members=sorted([nasa, era5, usdm], key=lambda value: value.source_key),
        description="Validated four-year source history",
    )
    polygon = _geometry(
        "MultiPolygon",
        [[[[-105.1, 39.7], [-105.0, 39.7], [-105.0, 39.8], [-105.1, 39.7]]]],
    )
    return root, [
        _data_source("nasa-power-daily", "NASA POWER", "Daily meteorology"),
        _data_source("era5-land", "ERA5-Land", "Daily land reanalysis"),
        _data_source("usdm-weekly", "U.S. Drought Monitor", "Weekly drought polygon snapshots"),
        _source_release(nasa, "nasa-power-daily-v1"),
        _source_release(era5, "era5-land-v1"),
        _source_release(usdm, "usdm-weekly-shapefile-v1"),
        _artifact(nasa, "cell-a"),
        _artifact(era5, "cell-b"),
        _artifact(usdm, "20260715"),
        HistoricalNasaCellRecord(
            cell_key="grid:cell-a",
            grid_name="nasa-power-imerg",
            resolution_m=10_000,
            geometry_json=_geometry("Polygon", [[[-105.1, 39.7], [-105.0, 39.7], [-105.0, 39.8], [-105.1, 39.7]]]),
            centroid_json=_geometry("Point", [-105.05, 39.75]),
            coverage_fraction=1,
        ),
        HistoricalNasaCrosswalkRecord(
            release=nasa,
            cell_key="grid:cell-a",
            native_feature_key="nasa:cell-a",
            native_geometry_json=_geometry("Point", [-105.05, 39.75]),
            native_resolution_m=10_000,
            spatial_support_kind="native_grid_cell",
            mapping_method="native-grid-centroid",
            coverage_fraction=1,
        ),
        HistoricalNasaObservationRecord(
            release=nasa,
            cell_key="grid:cell-a",
            signal_name="temperature_c",
            source_parameter="T2M",
            observed_at=_time(),
            valid_from=_time(),
            valid_to=_time(),
            data_available_at=_time(),
            original_value=21.5,
            original_unit="C",
            normalized_value=21.5,
            normalized_unit="C",
            is_observed=True,
        ),
        HistoricalNasaCoverageAuditRecord(
            release=nasa,
            cell_key="grid:cell-a",
            signal_name="temperature_c",
            source_parameter="T2M",
            window_start=_time(),
            window_end=_time(),
            expected_observation_count=1,
            received_observation_count=1,
            status="complete",
        ),
        HistoricalEra5CellRecord(
            cell_key="grid:cell-b",
            grid_name="era5-land",
            resolution_m=9_000,
            geometry_json=_geometry("Polygon", [[[-105.1, 39.7], [-105.0, 39.7], [-105.0, 39.8], [-105.1, 39.7]]]),
            centroid_json=_geometry("Point", [-105.05, 39.75]),
            coverage_fraction=1,
        ),
        HistoricalEra5CrosswalkRecord(
            release=era5,
            cell_key="grid:cell-b",
            native_feature_key="era5:cell-b",
            native_geometry_json=_geometry("Point", [-105.05, 39.75]),
            native_resolution_m=9_000,
            spatial_support_kind="native_grid_cell",
            mapping_method="native-grid-centroid",
            coverage_fraction=1,
        ),
        HistoricalEra5ObservationRecord(
            release=era5,
            cell_key="grid:cell-b",
            signal_name="soil_moisture",
            source_parameter="swvl1",
            observed_at=_time(),
            valid_from=_time(),
            valid_to=_time(),
            data_available_at=_time(),
            original_value=0.2,
            original_unit="m3/m3",
            normalized_value=0.2,
            normalized_unit="m3/m3",
            is_observed=True,
        ),
        HistoricalEra5CoverageAuditRecord(
            release=era5,
            cell_key="grid:cell-b",
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
            geometry_json=polygon,
            geometry_checksum=hashlib.sha256(polygon.encode()).hexdigest(),
            data_available_at=_time(),
            metadata={"object_id": 7, "shape_area": 12.5},
        ),
        HistoricalUsdmCoverageAuditRecord(
            release=usdm,
            scope_key="contiguous-united-states",
            window_start=_time(),
            window_end=_time(),
            expected_feature_count=1,
            received_feature_count=1,
            status="complete",
            details={"declared_feature_count": 1},
        ),
    ]


def test_builds_deterministic_bounded_root_manifest_for_nasa_era5_and_usdm() -> None:
    root, records = _records()
    first = build_historical_promotion_bundle(
        release_set=root,
        minimum_target_revision="20260720_0003",
        records=records,
        max_chunk_bytes=SMALL_CHUNK_BYTES,
    )
    second = build_historical_promotion_bundle(
        release_set=root,
        minimum_target_revision="20260720_0003",
        records=list(reversed(records)),
        max_chunk_bytes=SMALL_CHUNK_BYTES,
    )

    assert first.manifest.manifest_checksum == second.manifest.manifest_checksum
    assert first.manifest.total_record_count == len(records)
    assert [chunk.descriptor.sequence for chunk in first.chunks] == list(range(1, len(first.chunks) + 1))
    assert all(chunk.descriptor.payload_bytes <= SMALL_CHUNK_BYTES for chunk in first.chunks)
    assert {record.record_type for chunk in first.chunks for record in chunk.records} >= {
        "nasa_cell",
        "nasa_crosswalk",
        "nasa_observation",
        "nasa_coverage",
        "era5_cell",
        "era5_crosswalk",
        "era5_observation",
        "era5_coverage",
        "usdm_polygon",
        "usdm_coverage",
    }
    assert '"source_release_id"' not in first.model_dump_json()
    assert '"release_set_id"' not in first.model_dump_json()


def test_rejects_tampered_chunk_payload_even_when_manifest_is_unchanged() -> None:
    root, records = _records()
    bundle = build_historical_promotion_bundle(
        release_set=root,
        minimum_target_revision="20260720_0003",
        records=records,
    )
    payload = bundle.model_dump(mode="json")
    payload["chunks"][0]["records"].reverse()

    with pytest.raises(ValidationError, match=r"natural key|chunk descriptor"):
        HistoricalPromotionBundle.model_validate(payload)


def test_rejects_credentials_and_local_database_identifiers_in_metadata() -> None:
    with pytest.raises(ValidationError, match="sensitive field"):
        HistoricalDataSourceRecord(
            source_key="nasa-power-daily",
            name="NASA POWER",
            owner="NASA",
            purpose="Daily meteorology",
            license_name="NASA data policy",
            citation="NASA POWER",
            reviewed_at=_time(),
            reviewed_by="data-governance",
            configuration={"api_token": "do-not-export"},
        )

    with pytest.raises(ValidationError, match="local database IDs"):
        HistoricalDataSourceRecord(
            source_key="nasa-power-daily",
            name="NASA POWER",
            owner="NASA",
            purpose="Daily meteorology",
            license_name="NASA data policy",
            citation="NASA POWER",
            reviewed_at=_time(),
            reviewed_by="data-governance",
            configuration={"source_release_id": "local-only"},
        )


def test_rejects_geometry_checksum_mismatch_and_noncanonical_geometry() -> None:
    release = _release("usdm-weekly", "20260715")
    geometry = _geometry("MultiPolygon", [[[[-105.1, 39.7], [-105.0, 39.7], [-105.0, 39.8], [-105.1, 39.7]]]])

    with pytest.raises(ValidationError, match="geometry_checksum"):
        HistoricalUsdmPolygonRecord(
            release=release,
            issue_date=date(2026, 7, 14),
            feature_key="20260714:DM2:example",
            severity_class=2,
            geometry_json=geometry,
            geometry_checksum="0" * 64,
            data_available_at=_time(),
        )

    with pytest.raises(ValidationError, match="canonical JSON"):
        HistoricalNasaCellRecord(
            cell_key="grid:cell-a",
            grid_name="nasa-power-imerg",
            resolution_m=10_000,
            geometry_json='{"type":"Polygon","coordinates":[[[-105.1,39.7],[-105.0,39.7],[-105.0,39.8],[-105.1,39.7]]]}',
            centroid_json=_geometry("Point", [-105.05, 39.75]),
            coverage_fraction=1,
        )


def test_rejects_structurally_invalid_geojson_polygon_coordinates() -> None:
    with pytest.raises(ValidationError, match="linear rings"):
        HistoricalNasaCellRecord(
            cell_key="grid:cell-a",
            grid_name="nasa-power-imerg",
            resolution_m=10_000,
            geometry_json=_geometry("Polygon", [[-105.0, 39.7]]),
            centroid_json=_geometry("Point", [-105.05, 39.75]),
            coverage_fraction=1,
        )


def test_rejects_missing_raw_artifact_and_incomplete_era5_closure() -> None:
    root, records = _records()
    without_era5_artifact = [
        record
        for record in records
        if not (isinstance(record, HistoricalArtifactRecord) and record.release.source_key == "era5-land")
    ]
    with pytest.raises(ValueError, match="matching raw artifact receipt"):
        build_historical_promotion_bundle(
            release_set=root,
            minimum_target_revision="20260720_0003",
            records=without_era5_artifact,
        )

    without_era5_crosswalk = [record for record in records if not isinstance(record, HistoricalEra5CrosswalkRecord)]
    with pytest.raises(ValueError, match="era5 source releases require crosswalk"):
        build_historical_promotion_bundle(
            release_set=root,
            minimum_target_revision="20260720_0003",
            records=without_era5_crosswalk,
        )
