-- Registers the Boise Trekker Rim NASA POWER WS2M forecast series.
-- Idempotent: safe to rerun; a second run inserts nothing once the series
-- exists. Mirrors the Denver fixture's row shape (first-metric-forecast.sql
-- lines 140-162) with Boise-local entity_key/spatial_cell_id/
-- source_variant_key/metadata_json. Requires a validated/published
-- nasa-power-daily release set for the Boise cell to already exist (run
-- historical-nasa-backfill/-finalize first); zero rows insert otherwise.
-- Run with the owner credential against the disposable database, e.g.:
--   psql ... -f register-boise-trekker-rim-series.sql

\set ON_ERROR_STOP on
\pset pager off

BEGIN;
SET LOCAL statement_timeout = '120s';

INSERT INTO agri.forecast_series(
    series_key, source_variant_key, input_adapter, data_source_id,
    signal_name, source_parameter, support_key, source_transform_version,
    entity_type, entity_key, metric_name, metric_unit,
    spatial_cell_id, representation_kind, spatial_support_kind,
    source_spatial_resolution_m, output_spatial_resolution_m,
    source_temporal_support, output_temporal_support, metadata_json
)
SELECT
    'nasa-power-ws2m-boise-trekker-rim-v1',
    source_release.source_version,
    'signal_observation',
    data_source.id,
    'wind_speed',
    'WS2M',
    'surface',
    source_release.transform_version,
    'nasa_power_point',
    'boise-local:trekker-rim:p43.556:m116.132',
    'wind_speed',
    'm/s',
    spatial_cell.id,
    'raw_native',
    'point_sample',
    55660,
    55660,
    interval '1 day',
    interval '1 day',
    jsonb_build_object(
        'source_release_id', source_release.id,
        'release_set_id', release_set.id,
        'release_manifest_checksum', release_set.manifest_checksum,
        'parcel_apn', 'R0541500060',
        'parcel_name', 'Trekker Rim',
        'jurisdiction', 'City of Boise Parks & Rec',
        'acres', 5.232
    )
FROM agri.data_source AS data_source
JOIN agri.source_release AS source_release
    ON source_release.data_source_id = data_source.id
JOIN agri.spatial_cell AS spatial_cell
    ON spatial_cell.cell_key = 'boise-local:trekker-rim:p43.556:m116.132'
JOIN agri.release_set_item AS item ON item.source_release_id = source_release.id
JOIN agri.release_set AS release_set ON release_set.id = item.release_set_id
WHERE data_source.key = 'nasa-power-daily'
  AND source_release.source_version =
      'nasa-power-daily-v1:20220723-20260723:boise-local:trekker-rim:p43.556:m116.132'
  AND source_release.transform_version = 'nasa-power-point-sample-normalization-v2'
  AND release_set.state IN ('validated', 'published')
ON CONFLICT (series_key) DO NOTHING;

\echo 'Boise Trekker Rim forecast series (present only once the Boise NASA release set validates)'
SELECT id, series_key, source_variant_key, spatial_cell_id, metadata_json
FROM agri.forecast_series
WHERE series_key = 'nasa-power-ws2m-boise-trekker-rim-v1';

COMMIT;
