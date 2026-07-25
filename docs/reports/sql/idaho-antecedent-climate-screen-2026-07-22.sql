\set ON_ERROR_STOP on
\set nasa_release_ids 'd4a3cc03-cb49-4a96-b470-f114c3d23089,946a4adc-74cc-4a3e-bc56-a1ee4e031770,e864def1-ff57-4711-85af-38b35f7ba9ae,f5101439-a88b-41e4-b80c-7d3dfb33fb8d,6f27d1d7-ce76-45db-ad81-60c0f8512c7b,e2648a60-62c8-469d-90c9-5a4523b50a5e,12c16a6d-b527-4948-8424-89c09ab2600a,fcdaa0b4-dafb-4eca-8c20-0cf5a0b3351d,0c000f1a-452a-4457-96d0-2906b1bc4198,e92c0783-db67-4703-aa46-e21aa7933ea2,b6621349-c37d-42a3-882e-ffd9010983a9,a7f5da15-1be5-4993-8ca1-380fc26d75e7,bf9490ec-8a78-4008-ba3c-8e7afeaec440,5c8c832c-c4be-45e1-9b4d-dc4ab9c57130,a81a7cc0-d9de-4c61-953d-55188dce924a,b6b03ce9-2b2f-4c1a-852b-af0feb72259f,58caf6ed-74dc-41d3-938f-407938e6cee8,e8d626f8-5fd3-48a0-b736-a357ff4878c5,d3f7d2ff-f9c8-43bf-8a8a-6ccc7e7e072d,68c8e3b7-c0cb-4980-9ae1-bfbc3525c357,0c28818e-533d-4e6f-bbaf-bd84c075889f,523d929b-3f76-4f57-a5be-b05e44775a68,c7d3d425-1548-41d6-b811-3f1010af3bef,ec5aa864-1fd4-42d4-a6f0-addc7f3d4b31,109b9c3c-7836-439f-ad33-472d1f99a6e4,9f0ac062-2488-4c0f-9f5e-18a0b6fcefa1,335a1bdb-8b51-46fe-b9b5-f5a2a4ede203,52c5d521-4e7a-4233-9700-0f59c8ff134f,0322d69b-98f5-4f87-a8a5-2f791a0c3f2f,88b5703b-45e0-40ec-80d6-646523a90ecf,09b29003-40f8-49fa-9295-3e9de36c3e7a'

BEGIN READ ONLY;
SET LOCAL statement_timeout = '120s';

-- Coverage and missingness: expected result is 31 cells, 1,462 dates,
-- eight signals, 362,576 accepted rows, and zero null normalized values.
-- The polygon is an analytical proxy, not an authoritative state boundary.
WITH parameters AS (
    SELECT ST_GeomFromText(
        'POLYGON((-117.25 42,-111.04 42,-111.04 44.5,-112.7 45,-114.5 46.7,-116.05 49,-117.05 49,-117.05 46,-116.5 45,-116.8 44,-117.25 43,-117.25 42))',
        4326
    ) AS proxy
),
report_idaho_cells AS (
    SELECT
        cell.id AS cell_id,
        cell.cell_key,
        ST_Y(cell.centroid) AS latitude,
        CASE
            WHEN ST_Y(cell.centroid) < 44.0 THEN 'south'
            WHEN ST_Y(cell.centroid) < 46.5 THEN 'central'
            ELSE 'north'
        END AS region
    FROM agri.spatial_cell AS cell
    CROSS JOIN parameters
    WHERE cell.resolution_m = 55660
      AND ST_Covers(parameters.proxy, cell.centroid)
),
nasa_releases AS (
    SELECT release.id
    FROM agri.source_release AS release
    JOIN agri.data_source AS source
      ON source.id = release.data_source_id
    WHERE source.key = 'nasa-power-daily'
      AND release.id = ANY(string_to_array(:'nasa_release_ids', ',')::uuid[])
),
selected AS (
    SELECT observation.*
    FROM report_idaho_cells AS cell
    JOIN agri.signal_observation AS observation
      ON observation.cell_id = cell.cell_id
    JOIN nasa_releases AS release
      ON release.id = observation.source_release_id
    WHERE observation.quality_flag = 'accepted'
      AND observation.is_observed
      AND observation.observed_at >= TIMESTAMPTZ '2022-04-30 00:00:00+00'
      AND observation.observed_at < TIMESTAMPTZ '2026-05-01 00:00:00+00'
)
SELECT
    (SELECT count(*) FROM report_idaho_cells) AS cell_count,
    count(DISTINCT observed_at::date) AS date_count,
    count(DISTINCT source_parameter) AS signal_count,
    count(*) AS accepted_observation_count,
    count(*) FILTER (WHERE normalized_value IS NULL) AS null_normalized_value_count,
    min(observed_at)::date AS first_date,
    max(observed_at)::date AS last_date
FROM selected;

-- Every selected cell/release/signal receipt must be complete before interpreting
-- the aggregates. The expected result is 248 complete receipts and no others.
WITH parameters AS (
    SELECT ST_GeomFromText(
        'POLYGON((-117.25 42,-111.04 42,-111.04 44.5,-112.7 45,-114.5 46.7,-116.05 49,-117.05 49,-117.05 46,-116.5 45,-116.8 44,-117.25 43,-117.25 42))',
        4326
    ) AS proxy
),
report_idaho_cells AS (
    SELECT cell.id AS cell_id
    FROM agri.spatial_cell AS cell
    CROSS JOIN parameters
    WHERE cell.resolution_m = 55660
      AND ST_Covers(parameters.proxy, cell.centroid)
),
nasa_releases AS (
    SELECT release.id
    FROM agri.source_release AS release
    JOIN agri.data_source AS source
      ON source.id = release.data_source_id
    WHERE source.key = 'nasa-power-daily'
      AND release.id = ANY(string_to_array(:'nasa_release_ids', ',')::uuid[])
)
SELECT
    audit.status,
    count(*) AS receipt_count,
    sum(audit.expected_observation_count) AS expected_observations,
    sum(audit.received_observation_count) AS received_observations
FROM report_idaho_cells AS cell
JOIN agri.signal_coverage_audit AS audit
  ON audit.cell_id = cell.cell_id
JOIN nasa_releases AS release
  ON release.id = audit.source_release_id
GROUP BY audit.status
ORDER BY audit.status;

-- PRECTOTCORR is summed within cell/year; T2M, RH2M, and WS2M are averaged.
-- Cells then receive equal weight within each region/year. The 2026 value is
-- compared with the arithmetic mean of the corresponding 2023-2025 values.
WITH parameters AS (
    SELECT ST_GeomFromText(
        'POLYGON((-117.25 42,-111.04 42,-111.04 44.5,-112.7 45,-114.5 46.7,-116.05 49,-117.05 49,-117.05 46,-116.5 45,-116.8 44,-117.25 43,-117.25 42))',
        4326
    ) AS proxy
),
report_idaho_cells AS (
    SELECT
        cell.id AS cell_id,
        CASE
            WHEN ST_Y(cell.centroid) < 44.0 THEN 'south'
            WHEN ST_Y(cell.centroid) < 46.5 THEN 'central'
            ELSE 'north'
        END AS region
    FROM agri.spatial_cell AS cell
    CROSS JOIN parameters
    WHERE cell.resolution_m = 55660
      AND ST_Covers(parameters.proxy, cell.centroid)
),
nasa_releases AS (
    SELECT release.id
    FROM agri.source_release AS release
    JOIN agri.data_source AS source
      ON source.id = release.data_source_id
    WHERE source.key = 'nasa-power-daily'
      AND release.id = ANY(string_to_array(:'nasa_release_ids', ',')::uuid[])
),
observations AS (
    SELECT
        cell.region,
        cell.cell_id,
        extract(year FROM observation.observed_at)::integer AS observation_year,
        observation.source_parameter,
        observation.normalized_value
    FROM report_idaho_cells AS cell
    JOIN agri.signal_observation AS observation
      ON observation.cell_id = cell.cell_id
    JOIN nasa_releases AS release
      ON release.id = observation.source_release_id
    WHERE observation.quality_flag = 'accepted'
      AND observation.is_observed
      AND observation.normalized_value IS NOT NULL
      AND observation.source_parameter IN ('PRECTOTCORR', 'T2M', 'RH2M', 'WS2M')
      AND observation.observed_at >= TIMESTAMPTZ '2023-01-01 00:00:00+00'
      AND observation.observed_at < TIMESTAMPTZ '2026-05-01 00:00:00+00'
      AND extract(month FROM observation.observed_at) BETWEEN 1 AND 4
),
cell_year AS (
    SELECT
        region,
        cell_id,
        observation_year,
        source_parameter,
        CASE
            WHEN source_parameter = 'PRECTOTCORR' THEN sum(normalized_value)
            ELSE avg(normalized_value)
        END AS metric_value
    FROM observations
    GROUP BY region, cell_id, observation_year, source_parameter
),
region_year AS (
    SELECT
        region,
        observation_year,
        source_parameter,
        avg(metric_value) AS metric_value,
        count(*) AS contributing_cells
    FROM cell_year
    GROUP BY region, observation_year, source_parameter
),
comparison AS (
    SELECT
        region,
        source_parameter,
        max(metric_value) FILTER (WHERE observation_year = 2026) AS value_2026,
        avg(metric_value) FILTER (WHERE observation_year BETWEEN 2023 AND 2025) AS baseline_2023_2025,
        min(contributing_cells) AS minimum_contributing_cells
    FROM region_year
    GROUP BY region, source_parameter
)
SELECT
    region,
    source_parameter,
    round(value_2026::numeric, 6) AS value_2026,
    round(baseline_2023_2025::numeric, 6) AS baseline_2023_2025,
    round(
        CASE
            WHEN source_parameter = 'PRECTOTCORR'
                THEN 100 * (value_2026 / NULLIF(baseline_2023_2025, 0) - 1)
            ELSE value_2026 - baseline_2023_2025
        END::numeric,
        6
    ) AS reported_change,
    CASE
        WHEN source_parameter = 'PRECTOTCORR' THEN 'percent'
        WHEN source_parameter = 'T2M' THEN 'degrees_C'
        WHEN source_parameter = 'RH2M' THEN 'percentage_points'
        ELSE 'meters_per_second'
    END AS change_unit,
    minimum_contributing_cells
FROM comparison
ORDER BY
    CASE region WHEN 'south' THEN 1 WHEN 'central' THEN 2 ELSE 3 END,
    source_parameter;

ROLLBACK;
