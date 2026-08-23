-- watersheds_day_export
-- Purpose: export the current USGS WBD HUC12 boundary snapshot (geo.features, layer
--          'watersheds') to Parquet at (huc12) grain, for one release day.
-- Loaded by: agri_data_service.pipeline.lanes.watersheds
-- Params: release_day (date -- the day THIS EXPORT represents, bound into every row; it is
--         NOT a per-row filter, since the source carries no daily series to slice, see below)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md -- SQLAlchemy's text() scans comments too, and a colon-prefixed word here would
-- mint a phantom bind parameter no caller supplies.
--
-- THIS LANE IS STATIC, horizon: none (docs/lanes/watersheds.md section 7). Unlike the signal
-- plane's day-scoped, cell-batched export (signal_plane_day_export.sql), this query carries NO
-- date predicate: a HUC12 boundary is a snapshot re-keyed in place, not a daily-resampled
-- series -- "one row per HUC12, refreshed in place... a re-run must land on the same row rather
-- than minting a new version of an unchanged polygon" (ingest/watersheds.py:10-14). The measured
-- production reality is that the whole persisted set landed on exactly one load day
-- (conductor/RUNBOOK.md:872, "13 days static, exactly ONE load day -- all 9,396 rows on
-- 08-07"), which is why `release_day` is a caller-supplied constant broadcast onto every row
-- rather than something derived from a per-row column.
--
-- Reads the BASE layer only (geo.features, layer 'watersheds'), never geo.watershed_rollup.
-- The rollup is a cartographic aggregate this repo computes from the base layer (ST_Union +
-- simplify, drizzle/0023_watershed_zoom_generalization.sql:21-93), not upstream USGS data --
-- out of scope for this lane per docs/lanes/watersheds.md section 7.
--
-- Column source: `build_watershed_write` (ingest/watersheds.py:151-168) writes huc12, name,
-- areasqkm, tohuc, states, hutype, source and (when the WBD loaddate parsed) observedAt into
-- `properties`, plus the polygon into the native `geom` column. Field names below match those
-- exact lowercase GeoJSON spellings, which src/lib/map/hover-fields.ts also reads verbatim.
--
-- How this query works, clause by clause:
--
--   JOIN geo.layers l ON f.layer_id = l.id WHERE l.name = 'watersheds'
--     Scopes to this one layer's rows. The literal name matches the tile function's own literal
--     (drizzle/0017_watershed_persistence.sql:69) rather than resolving the WATERSHEDS_LAYER_ID
--     override env var (ingest/watersheds.py:44-51) -- that variable only ever renames the
--     destination row a live ingest run writes to, never this Parquet stream's fixed slug.
--
--   AND f.status = 'published' AND f.geom IS NOT NULL
--     The same two gates the tile function applies (drizzle/0017:70-73): a row awaiting review,
--     or one with no geometry at all, is not part of the served boundary set.
--
--   AND f.properties ->> 'huc12' IS NOT NULL
--     Defensive, not expected to ever exclude a row: every write the ingest job produces
--     carries a huc12 (build_watershed_identity raises otherwise, ingest/watersheds.py:120-129).
--     The export still never manufactures a grain key for a row that somehow lacks one.
--
--   CASE WHEN jsonb_typeof(f.properties -> 'areasqkm') = 'number' THEN (...)::double precision END
--     The same numeric-extraction guard the tile function uses (drizzle/0017:60-62) -- a
--     malformed or absent areasqkm becomes NULL rather than aborting the whole export with a
--     cast error on one bad row.
--
--   CASE WHEN ... AND pg_input_is_valid(..., 'timestamptz') THEN (...)::timestamptz END AS observed_at
--     The same validity-before-cast guard geo.feature_observation_day uses
--     (drizzle/0015_tile_observation_day.sql:41-45), applied here to the WBD loaddate vintage
--     this repo writes as `observedAt`. A basin the writer left undated (build_watershed_write
--     only sets observedAt when the load date parsed, ingest/watersheds.py:143-149) reports
--     NULL here too, never a fabricated instant.
--
--   ST_AsBinary(f.geom) AS geom
--     The polygon as well-known binary rather than GeoJSON text -- this lane's chosen wire
--     format for geometry, and roughly half the size of the GeoJSON copy this same row also
--     carries in properties->'geometry' (measured on one row: 21,572 B WKB vs 56,780 B GeoJSON,
--     conductor/RUNBOOK.md:1783-1785).
--
--   ORDER BY huc12
--     The lane's grain: "one row = one nationally-keyed HUC12 basin polygon"
--     (docs/lanes/watersheds.md section 4). Sorting here, ahead of the sort `write_partition`
--     also applies per part, keeps row order stable across the batches
--     `pipeline.lanes.watersheds` slices into separate part files.
SELECT
    f.properties ->> 'huc12' AS huc12,
    f.properties ->> 'name' AS name,
    CASE
        WHEN jsonb_typeof(f.properties -> 'areasqkm') = 'number'
            THEN (f.properties ->> 'areasqkm')::double precision
    END AS areasqkm,
    f.properties ->> 'tohuc' AS tohuc,
    f.properties ->> 'states' AS states,
    f.properties ->> 'hutype' AS hutype,
    f.properties ->> 'source' AS source,
    CASE
        WHEN f.properties ->> 'observedAt' IS NOT NULL
         AND pg_input_is_valid(f.properties ->> 'observedAt', 'timestamptz')
            THEN (f.properties ->> 'observedAt')::timestamptz
    END AS observed_at,
    f.data_available_at,
    (:release_day)::date AS release_day,
    f.id::text AS feature_id,
    ST_AsBinary(f.geom) AS geom
FROM geo.features AS f
JOIN geo.layers AS l ON l.id = f.layer_id
WHERE l.name = 'watersheds'
  AND f.status = 'published'
  AND f.geom IS NOT NULL
  AND f.properties ->> 'huc12' IS NOT NULL
ORDER BY huc12
