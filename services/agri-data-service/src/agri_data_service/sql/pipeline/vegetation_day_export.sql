-- vegetation_day_export
-- Purpose: produce ONE calendar day of the governed vegetation (Sentinel-2 L2A NDVI) plane at the
--          exported grain (cell_id, observed_day), for writing to
--          `layer=vegetation/kind=observed/year=/month=/day=/part-N.parquet`.
-- Loaded by: agri_data_service.pipeline.lanes.vegetation
-- Params: observed_day (date -- the one UTC calendar day being exported),
--         cell_ids (uuid[] -- the spatial-cell batch; NEVER empty, see the batching note below)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md -- SQLAlchemy's text() scans comments too, and a colon-prefixed word here would
-- mint a phantom bind parameter no caller supplies.
--
-- WHICH TABLE THIS READS AND WHY: this lane's governed plane is `agri.forecast_observation`, never
-- `agri.signal_observation` -- checking the wrong table has already once falsely reported this
-- lane dead (`docs/lanes/vegetation.md` section 4, "do not conflate them"). It is reached through
-- `agri.forecast_series`, which registers exactly one series per spatial cell for this lane's
-- fixed `metric_name = 'ndvi'` and `source_transform_version = 'sentinel2-ndvi-daily-cell-mean-v1'`
-- (`execution/vegetation_ndvi_plane.py:40,49-52`), so grouping by cell and day below recovers the
-- lane's true one-row-per-cell-day grain without needing signal_name/unit disambiguators the way
-- the signal plane does.
--
-- WHY THE DEDUP: `register_governed_plane` mints a NEW `agri.source_release` every time the raw
-- corpus digest changes (`execution/vegetation_ndvi_plane.py:372-423`; `insert_source_release.sql`
-- is idempotent only on `(data_source_id, source_version, payload_checksum, transform_version)`,
-- never on the observation itself), then re-materialises the FULL history up to the new cutoff for
-- whichever cells that run selects (`sql/execution/load_observations.sql:133-148` scans every raw
-- feature up to `cutoff_day`, not just days new since the last run). `load_observations.sql`'s own
-- write-time idempotency is scoped to `(source_release_id, series_id, source_event_key)`
-- (`db/agri/tables/forecast_observation.sql:41-43`) -- scoped PER RELEASE -- so a second
-- registration run over overlapping cells inserts a SECOND `forecast_observation` row for the same
-- `(series_id, observed_day)` under the new release rather than upserting the old one. Left
-- undeduplicated this silently double-counts a cell-day the moment two registration runs ever
-- overlap, so the winner is the newest source release -- the identical rule and reason
-- `signal_plane_day_export.sql` applies to `agri.signal_observation`.
--
-- WHY THE CELL BATCH IS A PARAMETER: bounds each statement's array parameter and result set,
-- reusing the batch size this lane's own registration path already settled on
-- (`execution/vegetation_ndvi_plane.py:57`, `CELL_BATCH_SIZE = 200`) rather than inventing a new
-- number. `ix_forecast_observation_series_time` leads on `(series_id, observed_at)`
-- (`db/agri/tables/forecast_observation.sql:49`), so scoping to one cell batch's series lets the
-- `observed_at` range condition ride that index per series instead of a plan that has to consider
-- every series in the table.
--
-- How this query works, clause by clause:
--
--   WITH governed AS (...)
--     A CTE ("common table expression") -- a named subquery written up front and referenced below
--     like a table. This one walks from the requested cell batch down to every observation of them
--     on the requested day, carrying along the release lineage the dedup below needs.
--
--   FROM agri.forecast_series AS series
--   INNER JOIN agri.spatial_cell AS cell ON cell.id = series.spatial_cell_id
--   INNER JOIN agri.forecast_observation AS observation ON observation.series_id = series.id
--   INNER JOIN agri.source_release AS release ON release.id = observation.source_release_id
--   INNER JOIN agri.data_source AS source ON source.id = release.data_source_id
--     Walks the lineage from the requested cells to their series, to those series' observations,
--     to the release each observation belongs to, to the data source that release came from. Every
--     join is INNER: a cell with no registered series, a series with no observation on this day, or
--     an observation whose release or source somehow failed to resolve all produce no row rather
--     than a row with empty columns -- the safe direction, because a downstream forecast is about
--     to be pinned to whatever comes back.
--
--   WHERE series.spatial_cell_id = ANY(CAST(:cell_ids AS uuid[]))
--     ANY means "equals any element of this array" -- the set-membership form of an equality test,
--     which is how one statement covers a whole cell batch. The cast pins the parameter's type: a
--     bare bind parameter carries none of its own, and the database will not guess which kind of
--     array it was handed.
--
--   AND series.metric_name = 'ndvi'
--   AND series.source_transform_version = 'sentinel2-ndvi-daily-cell-mean-v1'
--   AND source.key = 'sentinel2-ndvi-l2a'
--     Three literals, not parameters, because they are properties of this lane rather than of a
--     request -- this file only ever reads the one metric, the one transform, and the one data
--     source `docs/lanes/vegetation.md` section 1 scopes it to. Restricting on all three (rather
--     than trusting the cell-to-series join alone) is the same defensive posture
--     `signal_plane_day_export.sql` documents: the governed contract is the combination, not any
--     one column of it.
--
--   AND observation.observed_at >= (:observed_day)::timestamp AT TIME ZONE 'UTC'
--   AND observation.observed_at < ((:observed_day)::date + 1)::timestamp AT TIME ZONE 'UTC'
--     The half-open UTC day window: from midnight on the requested day up to, but not including,
--     midnight on the next. This is what confines the export to exactly one calendar day no matter
--     how the underlying timestamp is stored.
--
--   AND observation.quality_flag = 'accepted'
--     Every row this lane's own writer produces is quality_flag = 'accepted'
--     (`sql/execution/load_observations.sql:162`) today, but filtering explicitly keeps this query
--     correct if a future quarantine path ever writes a different flag, rather than depending on
--     that being true forever.
--
--   (observation.observed_at AT TIME ZONE 'UTC')::date AS observed_day
--     Recovers the calendar day from the stored instant. `AT TIME ZONE 'UTC'` first anchors the
--     interpretation to UTC so the subsequent cast to `date` cannot shift the day under a
--     session-local time zone.
--
--   array_agg(... ORDER BY release_retrieved_at DESC, observation_id DESC))[1]
--     Builds an array of the column's value across every release-duplicate row for one cell-day,
--     ordered newest release first with the observation id as a stable tiebreaker, then takes the
--     first element -- the newest release's own value. This is the dedup: whichever value the most
--     recently retrieved release recorded wins, and it is applied identically to every carried
--     column so a row's value, checksum, availability time and exposure flag all come from the same
--     winning release rather than being mixed across releases.
--
--   COUNT(*)::bigint AS release_count
--     How many release-duplicate rows existed for this cell-day, not how many raw Sentinel-2 scenes
--     contributed to the mean -- a reader must not read this as observation density. It runs 1 on
--     current data unless two registration passes have ever overlapped for that cell-day.
--
--   GROUP BY cell_id, grid_name, metric_name, metric_unit, observed_day
--     Collapses the release-duplicate rows for one cell-day into the one exported row. The four
--     descriptive columns are functionally constant per cell (a cell has exactly one grid and this
--     lane has exactly one metric), so grouping by them alongside `cell_id`/`observed_day` is safe
--     and keeps every selected column either grouped-by or aggregated, which every SQL engine here
--     requires.
WITH governed AS (
    SELECT
        cell.id AS cell_id,
        cell.grid_name AS grid_name,
        series.metric_name AS metric_name,
        series.metric_unit AS metric_unit,
        (observation.observed_at AT TIME ZONE 'UTC')::date AS observed_day,
        observation.metric_value,
        observation.observation_checksum,
        observation.data_available_at,
        observation.id AS observation_id,
        release.retrieved_at AS release_retrieved_at,
        source.allowed_client_exposure
    FROM agri.forecast_series AS series
    INNER JOIN agri.spatial_cell AS cell ON cell.id = series.spatial_cell_id
    INNER JOIN agri.forecast_observation AS observation ON observation.series_id = series.id
    INNER JOIN agri.source_release AS release ON release.id = observation.source_release_id
    INNER JOIN agri.data_source AS source ON source.id = release.data_source_id
    WHERE series.spatial_cell_id = ANY(CAST(:cell_ids AS uuid[]))
      AND series.metric_name = 'ndvi'
      AND series.source_transform_version = 'sentinel2-ndvi-daily-cell-mean-v1'
      AND source.key = 'sentinel2-ndvi-l2a'
      AND observation.observed_at >= (:observed_day)::timestamp AT TIME ZONE 'UTC'
      AND observation.observed_at < ((:observed_day)::date + 1)::timestamp AT TIME ZONE 'UTC'
      AND observation.quality_flag = 'accepted'
),
aggregated AS (
SELECT
    cell_id,
    grid_name,
    metric_name,
    metric_unit,
    observed_day,
    (array_agg(metric_value ORDER BY release_retrieved_at DESC, observation_id DESC))[1]
        AS metric_value,
    (array_agg(observation_checksum ORDER BY release_retrieved_at DESC, observation_id DESC))[1]
        AS observation_checksum,
    (array_agg(data_available_at ORDER BY release_retrieved_at DESC, observation_id DESC))[1]
        AS data_available_at,
    COUNT(*)::bigint AS release_count,
    (array_agg(allowed_client_exposure ORDER BY release_retrieved_at DESC, observation_id DESC))[1]
        AS allowed_client_exposure
FROM governed
GROUP BY cell_id, grid_name, metric_name, metric_unit, observed_day
)
-- THE CELL'S POSITION IS RESOLVED AFTER THE AGGREGATE, NOT CARRIED THROUGH IT. Carrying
-- `cell.centroid` down the per-observation CTE puts a PostGIS geometry on every row and then pushes
-- it through `array_agg`; on the sibling `signal` lane that measured as the difference between
-- finishing inside the 120 s statement timeout and being CANCELLED at 151 s (2026-08-24). This lane
-- is far smaller, but the shape of the query should not differ from its sibling's for no reason.
--
-- INNER is safe for the same reason the CTE's own joins are: `spatial_cell_id` is a foreign key, so
-- a cell that fails to resolve is a broken invariant rather than a row to carry with no position.
SELECT
    aggregated.cell_id::text AS cell_id,
    aggregated.grid_name,
    aggregated.metric_name,
    aggregated.metric_unit,
    aggregated.observed_day,
    aggregated.metric_value,
    aggregated.observation_checksum,
    aggregated.data_available_at,
    aggregated.release_count,
    aggregated.allowed_client_exposure,
    -- The representative point of the CELL, never any individual observation's location.
    ST_X(cell.centroid) AS cell_longitude,
    ST_Y(cell.centroid) AS cell_latitude
FROM aggregated
INNER JOIN agri.spatial_cell AS cell ON cell.id = aggregated.cell_id
