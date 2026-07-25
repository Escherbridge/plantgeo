"""Optional disposable PostgreSQL 16+ proof for revision 0009 geometry guards."""

import os

import psycopg2
import pytest

DATABASE_URL = os.getenv("GEOSPATIAL_EVIDENCE_DATABASE_URL")
DATABASE_PREFIX = "plantgeo_geospatial_test_"
MIN_SUPPORTED_SERVER_VERSION_NUM = 160000
MAX_SUPPORTED_SERVER_VERSION_NUM = 190000


def _connection() -> psycopg2.extensions.connection:
    assert DATABASE_URL is not None
    connection = psycopg2.connect(DATABASE_URL)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_database(), current_setting('server_version_num')::integer, "
            "(SELECT version_num FROM public.alembic_version)"
        )
        database_name, server_version_num, revision = cursor.fetchone()
    assert database_name.startswith(DATABASE_PREFIX)
    assert MIN_SUPPORTED_SERVER_VERSION_NUM <= server_version_num < MAX_SUPPORTED_SERVER_VERSION_NUM
    assert revision == "20260723_0009"
    return connection


def _expect_geometry_rejection(
    cursor: psycopg2.extensions.cursor,
    geometry_ewkt: str,
    feature_key: str,
) -> None:
    cursor.execute("SAVEPOINT rejected_geometry")
    with pytest.raises(
        (
            psycopg2.errors.CheckViolation,
            psycopg2.errors.InvalidParameterValue,
        )
    ):
        cursor.execute(
            """
            INSERT INTO agri.normalized_source_feature(
                source_release_id, artifact_id, feature_key, feature_kind, geometry,
                geometry_checksum, data_available_at, spatial_support_kind, native_scale,
                maximum_inference_scale, confidence_basis, method_key, method_version,
                feature_checksum
            ) VALUES (
                '10000000-0000-4000-8000-000000000002',
                '10000000-0000-4000-8000-000000000003',
                %s, 'test_geometry', ST_GeomFromEWKT(%s), repeat('4', 64),
                '2026-01-01T00:00:00Z', 'native_polygon', 'test fixture',
                'parcel', 'unassessed', 'fixture', 'v1', repeat('5', 64)
            )
            """,
            (feature_key, geometry_ewkt),
        )
    cursor.execute("ROLLBACK TO SAVEPOINT rejected_geometry")


@pytest.mark.skipif(
    not DATABASE_URL,
    reason="set a guarded disposable GEOSPATIAL_EVIDENCE_DATABASE_URL",
)
def test_revision_0009_rejects_invalid_empty_3d_and_nonpolygon_subject_geometry() -> None:
    connection = _connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO agri.data_source(
                    id, key, name, owner, purpose, license_name, citation
                ) VALUES (
                    '10000000-0000-4000-8000-000000000001', 'geometry-contract',
                    'Geometry contract', 'PlantGeo test', 'Disposable constraint proof',
                    'test-only', 'test-only'
                );
                INSERT INTO agri.source_release(
                    id, data_source_id, source_version, retrieved_at, data_available_at,
                    payload_checksum, schema_version, license_snapshot,
                    validation_state, validated_at
                ) VALUES (
                    '10000000-0000-4000-8000-000000000002',
                    '10000000-0000-4000-8000-000000000001', 'v1',
                    '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z',
                    repeat('2', 64), 'v1', 'test-only',
                    'valid', '2026-01-01T00:00:01Z'
                );
                INSERT INTO agri.artifact(
                    id, source_release_id, kind, uri, checksum_sha256, size_bytes
                ) VALUES (
                    '10000000-0000-4000-8000-000000000003',
                    '10000000-0000-4000-8000-000000000002',
                    'test', 'test://geometry-contract', repeat('3', 64), 0
                );
                """
            )
            cursor.execute("SAVEPOINT rejected_inline_digest")
            with pytest.raises(psycopg2.errors.CheckViolation):
                cursor.execute(
                    """
                    INSERT INTO agri.artifact(
                        source_release_id, kind, uri, checksum_sha256, size_bytes,
                        storage_class, content_bytes
                    ) VALUES (
                        '10000000-0000-4000-8000-000000000002',
                        'test', 'test://wrong-inline-digest', repeat('0', 64), 1,
                        'database_inline', decode('01', 'hex')
                    )
                    """
                )
            cursor.execute("ROLLBACK TO SAVEPOINT rejected_inline_digest")

            _expect_geometry_rejection(
                cursor,
                "SRID=4326;POLYGON((0 0,1 1,1 0,0 1,0 0))",
                "invalid-polygon",
            )
            _expect_geometry_rejection(cursor, "SRID=4326;GEOMETRYCOLLECTION EMPTY", "empty")
            _expect_geometry_rejection(cursor, "SRID=4326;POINT Z (0 0 1)", "three-dimensional")

            cursor.execute(
                """
                INSERT INTO agri.normalized_source_feature(
                    id, source_release_id, artifact_id, feature_key, feature_kind,
                    geometry, geometry_checksum, data_available_at, spatial_support_kind,
                    native_scale, maximum_inference_scale, confidence_basis, method_key,
                    method_version, feature_checksum
                ) VALUES (
                    '10000000-0000-4000-8000-000000000004',
                    '10000000-0000-4000-8000-000000000002',
                    '10000000-0000-4000-8000-000000000003', 'valid-point',
                    'test_geometry', ST_GeomFromText('POINT(0 0)', 4326),
                    repeat('4', 64), '2026-01-01T00:00:00Z', 'point_sample',
                    'test fixture', 'parcel', 'unassessed', 'fixture', 'v1',
                    repeat('5', 64)
                );
                SAVEPOINT rejected_subject;
                """
            )
            with pytest.raises(psycopg2.errors.CheckViolation):
                cursor.execute(
                    """
                    INSERT INTO agri.analysis_subject(
                        subject_key, subject_version, subject_kind, source_release_id,
                        artifact_id, source_feature_id, display_name, country_code,
                        geometry, geometry_checksum, spatial_support_kind, native_scale,
                        maximum_inference_scale, confidence_basis, method_key, method_version
                    ) VALUES (
                        'test:parcel', 'v1', 'parcel',
                        '10000000-0000-4000-8000-000000000002',
                        '10000000-0000-4000-8000-000000000003',
                        '10000000-0000-4000-8000-000000000004', 'Test parcel', 'US',
                        ST_GeomFromText('POINT(0 0)', 4326), repeat('6', 64),
                        'parcel_boundary', 'test fixture', 'parcel', 'unassessed',
                        'fixture', 'v1'
                    )
                    """
                )
            cursor.execute("ROLLBACK TO SAVEPOINT rejected_subject")

            cursor.execute("SAVEPOINT rejected_mutation")
            with pytest.raises(psycopg2.errors.RaiseException, match="immutable"):
                cursor.execute(
                    """
                    UPDATE agri.normalized_source_feature
                    SET feature_kind = 'changed'
                    WHERE id = '10000000-0000-4000-8000-000000000004'
                    """
                )
            cursor.execute("ROLLBACK TO SAVEPOINT rejected_mutation")
    finally:
        connection.rollback()
        connection.close()
