-- Vetted copy of C:\tmp\plantgeo-retain-ghisaconus.sql, 2026-08-14.
-- SHA-256 of the original file: 077a4b8acba29b9e824bc14ebac17e354937e0048c9e45827d144696b330e97d
-- Byte-identical to the original -- schema vetting found NO column/type/constraint
-- drift against prod (alembic head 20260808_0019) that needed fixing. This script
-- is BLOCKED for a different reason, unrelated to schema shape: see
-- lineage-blocked-2026-08-14.md in this directory. It was NOT run against prod.

BEGIN;

DO $$
DECLARE
    source_id uuid;
    release_id uuid;
    release_set_id uuid;
BEGIN
    INSERT INTO agri.data_source (
        id, key, name, owner, purpose, base_url, license_name, license_url,
        citation, refresh_policy, retention_days, allowed_client_exposure,
        review_state, reviewed_at, reviewed_by, is_active, configuration,
        created_at, updated_at
    ) VALUES (
        gen_random_uuid(),
        'kaggle-ghisaconus-mirror',
        'Hyperspectral Library of Agricultural Crops (USGS)',
        'Bill Basener / Kaggle listing',
        'Evaluation-only historical crop-spectrum classification benchmark.',
        'https://www.kaggle.com/datasets/billbasener/hyperspectral-library-of-agricultural-crops-usgs',
        'U.S. Government Works',
        'https://www.kaggle.com/terms',
        'Kaggle GHISACONUS Version 1 listing; USGS/NASA lineage is retained as a provenance caveat.',
        '{"mode":"manual","append_only":true}'::jsonb,
        3650, false, 'approved', '2026-07-26T13:49:09Z'::timestamptz,
        'local-evaluation-operator', true,
        '{"benchmark_scope":"evaluation_only","causal_role":"none","source_kind":"public_kaggle_csv"}'::jsonb,
        now(), now()
    ) RETURNING id INTO source_id;

    INSERT INTO agri.source_release (
        id, data_source_id, source_version, retrieved_at, data_available_at,
        observed_from, observed_to, payload_checksum, payload_bytes,
        schema_version, transform_version, license_snapshot, query_parameters,
        quality_summary, validation_state, validated_at, created_at
    ) VALUES (
        gen_random_uuid(), source_id, '1', '2026-07-26T13:49:30Z'::timestamptz,
        '2026-07-26T13:49:30Z'::timestamptz,
        '2008-01-01T00:00:00Z'::timestamptz,
        '2015-12-31T23:59:59Z'::timestamptz,
        'e2f5a21b24fac00e930520ba959ab54cc8a3f8c56368f8e0a1868bbf3e3377d5',
        11540638, 'ghisaconus_csv_v1', 'source-native',
        'Kaggle listing: U.S. Government Works; terms https://www.kaggle.com/terms',
        '{"dataset_ref":"billbasener/hyperspectral-library-of-agricultural-crops-usgs","version":1}'::jsonb,
        '{"row_count":6988,"column_count":142,"spectral_column_count":131,"source_image_count":99,"missing_metadata_cells":0,"missing_spectral_cells":0,"synthetic_data_detected":false}'::jsonb,
        'valid', now(), now()
    ) RETURNING id INTO release_id;

    INSERT INTO agri.release_set (
        id, logical_key, as_of_time, manifest_checksum, state, description,
        validated_at, created_at
    ) VALUES (
        gen_random_uuid(), 'ghisaconus-v1-public-benchmark-20260726',
        '2026-07-26T13:49:30Z'::timestamptz,
        '535fa6a2f412b627e735689d756b2ce29f9e288f9e790611719b0868d9920f0b',
        'draft',
        'Evaluation-only public GHISACONUS crop-spectrum benchmark; not a causal or operational release.',
        NULL, now()
    ) RETURNING id INTO release_set_id;

    INSERT INTO agri.release_set_item (release_set_id, source_release_id, source_role, added_at)
    VALUES (release_set_id, release_id, 'benchmark_input', now());

    UPDATE agri.release_set
    SET state = 'validated', validated_at = now()
    WHERE id = release_set_id;

    INSERT INTO agri.artifact (
        id, source_release_id, kind, uri, media_type, checksum_sha256, size_bytes,
        storage_class, metadata_json, content_bytes, created_at
    ) VALUES
        (gen_random_uuid(), release_id, 'source_csv',
         'warehouse://public-benchmarks/ghisaconus/v1/csv/e2f5a21b24fac00e930520ba959ab54cc8a3f8c56368f8e0a1868bbf3e3377d5',
         'text/csv', 'e2f5a21b24fac00e930520ba959ab54cc8a3f8c56368f8e0a1868bbf3e3377d5',
         11540638, 'database_inline',
         '{"retention_role":"primary_payload","source_release_payload":true}'::jsonb,
         pg_read_binary_file('/tmp/GHISACONUS_2008_001_speclib.csv'), now()),
        (gen_random_uuid(), release_id, 'source_archive',
         'warehouse://public-benchmarks/ghisaconus/v1/archive/3bb701ab61eb2069c09ae7cc9cb66fbd0995165f127478af2b6afb9d406abac0',
         'application/zip', '3bb701ab61eb2069c09ae7cc9cb66fbd0995165f127478af2b6afb9d406abac0',
         5225446, 'database_inline',
         '{"retention_role":"distribution_archive","source_release_payload":false}'::jsonb,
         pg_read_binary_file('/tmp/plantgeo-ghisaconus-v1.zip'), now()),
        (gen_random_uuid(), release_id, 'source_metadata',
         'warehouse://public-benchmarks/ghisaconus/v1/metadata/42480207bdeed7b40753637f4b24b07d44232ba4ed6f8455fabdecd4c1b6b220',
         'application/json', '42480207bdeed7b40753637f4b24b07d44232ba4ed6f8455fabdecd4c1b6b220',
         8877, 'database_inline',
         '{"retention_role":"source_metadata","source_release_payload":false}'::jsonb,
         pg_read_binary_file('/tmp/plantgeo-ghisaconus-metadata.json'), now());
END $$;

COMMIT;
