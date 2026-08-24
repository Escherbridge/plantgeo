-- signal_plane_day_export
-- Purpose: produce ONE calendar day of the signal plane at the exported grain
--          (support_key, signal_name, normalized_unit, cell_id, observed_day), for writing to
--          `layer=signal/kind=observed/year=/month=/day=/part-N.parquet`.
-- Loaded by: agri_data_service.pipeline.lanes.signal
-- Params: observed_day (date -- the one UTC calendar day being exported),
--         cell_ids (uuid[] -- the cell batch; NEVER empty, see the batching note below)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md -- SQLAlchemy's text() scans comments too, and a colon-prefixed word here would
-- mint a phantom bind parameter no caller supplies.
--
-- THIS IS A TRANSCRIPTION, NOT A NEW QUERY. The `governed` CTE below is lifted verbatim from
-- `drizzle/0029_pre_aggregation_layer.sql:534-614`, the defining query of `geo.mv_signal_cell_daily`
-- -- the rollup the map, the agent and ops all read before it was dropped (6,349 MB). Exporting a
-- different population than the rollup served would silently change what every downstream reader
-- sees, so the gates are reproduced exactly and the differences are enumerated here:
--
--   * SCOPED TO ONE DAY AND ONE CELL BATCH. The rollup covered all of history at once.
--   * THREE COLUMNS DROPPED -- `min_value`, `max_value`, `avg_value`. RUNBOOK section 0.22.3
--     measured them equal to `normalized_value` on 100% of 701,257 rows, and they cost 3.81x in
--     file size. Readers that want a spread re-aggregate from `normalized_value`, which is
--     provably identical output. DO NOT RE-ADD THEM.
--
-- WHY THE CELL BATCH IS A PARAMETER RATHER THAN A DAY RANGE:
--   `agri.signal_observation` is an 11 GB heap with NO index leading on `observed_at`. A
--   day-scoped export across all cells is a FULL HEAP SCAN -- `min(observed_at)` alone does not
--   complete in 90 s. Batching by `cell_id` rides `ix_signal_observation_cell_time_signal`
--   (2,915 MB, 87,609 scans). RUNBOOK section 0.22.5. Passing an empty array here would scan
--   nothing and return zero rows, which the writer would then refuse as an empty partition -- the
--   caller must never do it, and `signal.py` asserts so.
--
-- THE GOVERNED CONTRACT IS (signal_name, normalized_unit, lane), NOT signal_name alone. A rollup
-- that required only `normalized_unit IS NOT NULL` would emit TWO rows per (cell, day) the moment
-- one off-contract unit lands. `required_source_key` is NULL for the ERA5-Land rows because
-- migrations 0016/0019 gate on the unit pair alone -- NULL means "no lane gate applies", never
-- "unknown". `surface` is a generic support the ERA5-Land writer also emits, so `support_key`
-- cannot substitute for the lane gate on the three shared names (`precipitation`, `wind_speed`,
-- `relative_humidity`).
--
-- The dedup below is a RELEASE dedup, not a measurement aggregate: the winner is the newest
-- source release for that grain key. `observation_count` is therefore how many times an archive
-- republished the cell-day, NOT how many readings it stands for -- no reader may weight by it.
-- It runs ~1.001 on current data; the 1.85x collapse is a historical-backfill artifact
-- (RUNBOOK section 0.22.7).
WITH governed AS (
    SELECT
        observation.support_key::text AS support_key,
        observation.signal_name::text AS signal_name,
        observation.normalized_unit::text AS normalized_unit,
        observation.cell_id,
        (observation.observed_at AT TIME ZONE 'UTC')::date AS observed_day,
        observation.observed_at,
        observation.normalized_value,
        observation.coverage_fraction,
        observation.id AS observation_id,
        release.retrieved_at AS release_retrieved_at,
        source.allowed_client_exposure
    FROM agri.signal_observation AS observation
    JOIN agri.source_release AS release ON release.id = observation.source_release_id
    JOIN agri.data_source AS source ON source.id = release.data_source_id
    JOIN (
        VALUES
            -- nasa-power-daily, support `surface`, grid `nasa-power-0.5-degree`, from 2022-08-06.
            ('air_temperature_mean'::text,  'C'::text,                'nasa-power-daily'::text),
            ('air_temperature_max',         'C',                      'nasa-power-daily'),
            ('air_temperature_min',         'C',                      'nasa-power-daily'),
            ('dew_point_temperature',       'C',                      'nasa-power-daily'),
            ('precipitation',               'mm/day',                 'nasa-power-daily'),
            ('relative_humidity',           '%',                      'nasa-power-daily'),
            ('surface_shortwave_radiation', 'MJ/m^2/day',             'nasa-power-daily'),
            ('wind_speed',                  'm/s',                    'nasa-power-daily'),
            -- nasa-power-daily soil-wetness pilot, same lane, opens 2022-08-06.
            ('soil_wetness_surface',        'fraction_of_saturation', 'nasa-power-daily'),
            ('soil_wetness_root_zone',      'fraction_of_saturation', 'nasa-power-daily'),
            ('soil_wetness_profile',        'fraction_of_saturation', 'nasa-power-daily'),
            -- open-meteo-era5-land-archive, support `era5-land-0.1deg`, from 2022-04-30.
            ('soil_water_content_layer_1',  'm^3/m^3',                NULL),
            ('soil_water_content_layer_2',  'm^3/m^3',                NULL),
            ('soil_water_content_layer_3',  'm^3/m^3',                NULL),
            ('soil_temperature_level_1',    'C',                      NULL),
            ('soil_temperature_level_2',    'C',                      NULL),
            ('soil_temperature_level_3',    'C',                      NULL),
            ('soil_temperature_level_4',    'C',                      NULL),
            ('vapor_pressure_deficit',      'kPa',                    NULL)
    ) AS governed(signal_name, normalized_unit, required_source_key)
        ON governed.signal_name = observation.signal_name
       AND governed.normalized_unit = observation.normalized_unit
       AND (governed.required_source_key IS NULL OR governed.required_source_key = source.key)
    WHERE observation.cell_id = ANY(:cell_ids)
      AND observation.observed_at >= (:observed_day)::timestamp AT TIME ZONE 'UTC'
      AND observation.observed_at < ((:observed_day)::date + 1)::timestamp AT TIME ZONE 'UTC'
      AND observation.is_observed
      AND observation.quality_flag = 'accepted'
      AND observation.normalized_value IS NOT NULL
      AND observation.normalized_unit IS NOT NULL
),
-- THE CELL'S POSITION IS RESOLVED AFTER THE AGGREGATE, NOT CARRIED THROUGH IT, and that is a
-- measured requirement rather than a preference. Carrying `cell.centroid` down the `governed` CTE
-- put a PostGIS geometry on EVERY observation row and then pushed it through `array_agg`, which
-- took one production day from comfortably inside the 120 s statement timeout to CANCELLED at 151 s
-- (measured 2026-08-24). Below, `agri.spatial_cell` is joined to the already-grouped rows, so the
-- geometry is touched once per exported row -- a few thousand -- instead of once per observation.
--
-- INNER is safe: `observation.cell_id` is a foreign key to `spatial_cell(id)`, so a cell that fails
-- to resolve is a broken invariant, not a legitimate row to carry through with empty coordinates.
aggregated AS (
SELECT
    support_key,
    signal_name,
    normalized_unit,
    cell_id,
    observed_day,
    (array_agg(normalized_value ORDER BY release_retrieved_at DESC, observation_id DESC))[1]
        AS normalized_value,
    COUNT(*)::bigint AS observation_count,
    MAX(observed_at) AS newest_observed_at,
    (array_agg(coverage_fraction ORDER BY release_retrieved_at DESC, observation_id DESC))[1]
        AS coverage_fraction,
    (array_agg(allowed_client_exposure ORDER BY release_retrieved_at DESC, observation_id DESC))[1]
        AS allowed_client_exposure
FROM governed
GROUP BY support_key, signal_name, normalized_unit, cell_id, observed_day
)
SELECT
    aggregated.support_key,
    aggregated.signal_name,
    aggregated.normalized_unit,
    aggregated.cell_id::text AS cell_id,
    aggregated.observed_day,
    aggregated.normalized_value,
    aggregated.observation_count,
    aggregated.newest_observed_at,
    aggregated.coverage_fraction,
    aggregated.allowed_client_exposure,
    -- The representative point of the CELL, never the location of any individual observation. A
    -- reader must not treat these as where a measurement was taken.
    ST_X(cell.centroid) AS cell_longitude,
    ST_Y(cell.centroid) AS cell_latitude
FROM aggregated
INNER JOIN agri.spatial_cell AS cell ON cell.id = aggregated.cell_id
