-- Purpose: one row per data stream this platform loads, with its coverage, its INTERIOR
--          gaps and how stale it is, so /ops/backfill shows the state of EVERY load rather
--          than only the lanes that happen to run through the agri.job_* ledger.
-- Loaded by: agri_data_service.routes.ops
-- Params: none.
--
-- Why this exists beside the lane query: the ledger only knows about work that was enqueued
-- as job_work_item rows. The Open-Meteo plan lanes, the SoilGrids warm walk and the forward
-- ingest crons all write their results straight to the warehouse without ever touching the
-- ledger, so a dashboard built on the ledger alone reports them as absent when they are in
-- fact healthy. This query asks the opposite question -- not "what work is queued" but
-- "what data actually landed, and where are the holes" -- and the two together are the
-- whole picture.
--
-- Why interior gaps and not just a date range: a first and last day describe the ENVELOPE of
-- a stream, not its contents. A walk that wrote 2022 and 2026 and nothing between reports the
-- same from_day and to_day as one that wrote every single day, and reads as complete on any
-- dashboard that shows only the range. `missing_days` and `largest_gap_days` are what tell
-- those two apart, and they are the reason this query counts distinct days rather than rows:
-- row counts cannot distinguish a lane that wrote half its cells for every day from one that
-- wrote every cell for half its days.
--
-- What this returns: one row per stream across three stores, unioned into a single shape:
--   * warehouse signal -- a governed time series in agri.signal_observation
--   * map layer        -- a published feature collection in geo.features
--   * point cache      -- an on-demand upstream cache (SoilGrids topsoil chemistry)
-- `coverage` means a different unit per kind, which is why `coverage_label` travels with it.
-- The day columns are NULL for a stream with no observation day at all: the SoilGrids cache
-- is a static raster sample and reference layers carry no date, and NULL says "this stream
-- has no time axis" where a zero would falsely claim a complete one.
--
-- How this query works, clause by clause:
--
--   WITH signal_days AS (... GROUP BY signal_name, observed_at::date)
--     Collapses ~16M observations to one row per (stream, day) -- a few thousand rows. Every
--     gap calculation below runs over THAT, not over the raw table, which is what keeps the
--     window functions affordable.
--
--   day - lag(day) OVER (PARTITION BY stream ORDER BY day)
--     `lag` reads the previous row's value within each stream. The difference is the step
--     between consecutive PRESENT days, so a step of 1 means no gap and a step of 5 means
--     four days are missing. The first day of each stream has no previous row, so its step
--     is NULL and is excluded from max() automatically.
--
--   max(step) - 1
--     Converts the largest step into the largest RUN OF MISSING DAYS. Subtracting one is
--     what makes it a count of absent days rather than the distance between present ones;
--     without it a perfectly dense stream would report a gap of 1.
--
--   (to_day - from_day + 1) - observed_days
--     Total missing days inside the envelope. The `+ 1` makes the span inclusive of both
--     endpoints, so a stream holding exactly one day reports span 1 and missing 0.
--
--   the median_step_days column
--     The typical spacing between consecutive days, which is this stream's OBSERVED refresh
--     cadence -- daily reads 1, weekly reads 7. It is the median rather than the mean
--     because one long outage would drag a mean far away from the cadence the stream
--     actually keeps, and cadence is the thing an operator wants to compare against.
--     `percentile_cont` interpolates, so an even sample can return a fractional day.
--
--   LEFT JOIN ... ON layer shape
--     A LEFT join so a registered layer with zero features, or one whose features carry no
--     date, still produces a row. An INNER join would silently drop exactly the layers most
--     worth seeing on an operations page.
--
--   feature.status = 'published'
--     Only published features are servable, so only published features are counted. A layer
--     holding nothing but pending_review rows is empty from the map's point of view.
--
--   feature.geometry_id is not null
--     Only rows linked into the geometry dimension. This mirrors the canonical census this
--     query otherwise duplicates -- sql/ingest/observed_days.sql, loaded at both its layer scopes
--     by validation/queries.py -- which gates on this same predicate for the same
--     reason: an unlinked row has no shape the time-aware serving path can place, so the map's day
--     slider can never reach it. Without this predicate the census here counts days the slider
--     cannot actually offer, and /ops/backfill over-reports coverage relative to what a person
--     scrubbing the map will see.

with signal_days as (
    select
        observation.signal_name as stream,
        observation.observed_at::date as day
    from agri.signal_observation as observation
    group by observation.signal_name, observation.observed_at::date
),

signal_steps as (
    select
        stream,
        day,
        day - lag(day) over (partition by stream order by day) as step
    from signal_days
),

signal_shape as (
    select
        stream,
        count(*) as observed_days,
        min(day) as from_day,
        max(day) as to_day,
        coalesce(max(step) - 1, 0) as largest_gap_days,
        percentile_cont(0.5) within group (order by step) as median_step_days
    from signal_steps
    group by stream
),

signal_volume as (
    select
        observation.signal_name as stream,
        count(*) as rows,
        count(distinct observation.cell_id) as coverage
    from agri.signal_observation as observation
    group by observation.signal_name
),

layer_days as (
    select
        layer.name as stream,
        geo.feature_observation_day(feature.properties) as day
    from geo.layers as layer
    inner join geo.features as feature
        on feature.layer_id = layer.id
        and feature.status = 'published'
        and feature.geometry_id is not null
    where geo.feature_observation_day(feature.properties) is not null
    group by layer.name, geo.feature_observation_day(feature.properties)
),

layer_steps as (
    select
        stream,
        day,
        day - lag(day) over (partition by stream order by day) as step
    from layer_days
),

layer_shape as (
    select
        stream,
        count(*) as observed_days,
        min(day) as from_day,
        max(day) as to_day,
        coalesce(max(step) - 1, 0) as largest_gap_days,
        percentile_cont(0.5) within group (order by step) as median_step_days
    from layer_steps
    group by stream
),

layer_volume as (
    select
        layer.name as stream,
        count(feature.id) as rows
    from geo.layers as layer
    left join geo.features as feature
        on feature.layer_id = layer.id
        and feature.status = 'published'
    group by layer.name
)

select
    'warehouse signal' as kind,
    volume.stream,
    volume.rows,
    volume.coverage,
    'cells' as coverage_label,
    shape.from_day,
    shape.to_day,
    shape.observed_days,
    (shape.to_day - shape.from_day + 1) as span_days,
    (shape.to_day - shape.from_day + 1) - shape.observed_days as missing_days,
    shape.largest_gap_days,
    shape.median_step_days,
    (current_date - shape.to_day) as stale_days
from signal_volume as volume
inner join signal_shape as shape on shape.stream = volume.stream

union all

select
    'map layer' as kind,
    volume.stream,
    volume.rows,
    volume.rows as coverage,
    'features' as coverage_label,
    shape.from_day,
    shape.to_day,
    shape.observed_days,
    (shape.to_day - shape.from_day + 1) as span_days,
    (shape.to_day - shape.from_day + 1) - shape.observed_days as missing_days,
    shape.largest_gap_days,
    shape.median_step_days,
    (current_date - shape.to_day) as stale_days
from layer_volume as volume
left join layer_shape as shape on shape.stream = volume.stream

union all

select
    'point cache' as kind,
    'soilgrids-topsoil' as stream,
    count(*) as rows,
    count(*) filter (where cache.complete) as coverage,
    'cells' as coverage_label,
    null::date as from_day,
    null::date as to_day,
    null::bigint as observed_days,
    null::integer as span_days,
    null::integer as missing_days,
    null::integer as largest_gap_days,
    null::double precision as median_step_days,
    null::integer as stale_days
from public.soil_grid_cache as cache

order by kind, stream
