-- Purpose: report when the published USGS WBD HUC12 boundary set last CHANGED -- the source
--          watermark the watersheds static lane stamps its snapshot with, instead of stamping
--          the cron's run date.
-- Loaded by: agri_data_service.pipeline.parquet.lane_registry
-- Params: none
--
-- A HUC12 BOUNDARY IS NOT A MEASUREMENT TAKEN ON A DATE. It is a reference fact with a version,
-- so the day in its partition path is a VERSION STAMP, not an observation time, and there is no
-- per-day obligation for this lane to miss. The lane used to re-snapshot the newest settled day
-- on every tick; it now writes one snapshot dated at whatever day this query reports, and writes
-- nothing at all while a partition dated at or after that day already exists.
--
-- WHY updated_at IS A REAL CHANGE CLOCK AND NOT A POLL CLOCK. sql/ingest/refresh_features.sql
-- moves updated_at only inside an UPDATE gated on
-- `(properties - 'geometry' - 'geometry_repaired') IS DISTINCT FROM (next_properties - 'geometry')`,
-- so a re-fetch of unchanged ground leaves it exactly where it was. That gate is what makes this
-- query answer "when did this last change" rather than "when did we last look" -- the distinction
-- the whole watermark model rests on. Contrast geo.geometry.last_confirmed_at, which
-- src/lib/server/services/usda-soil.ts:833 advances on every re-fetch of unchanged ground; it is a
-- poll clock and is deliberately absent from every watermark query in this tree.
--
-- created_at is taken alongside it because a brand-new basin is a change that updated_at need not
-- record: 0022_features_write_time_indexes.sql:13 states plainly that "an insert moves created_at
-- and a refresh of an already-walked day moves only updated_at". GREATEST of the two is therefore
-- the honest answer to "the newest change to this reference set", and both are returned separately
-- so the caller's provenance line can say which one won.
--
-- KNOWN LIMITATION, recorded rather than papered over: a geometry-only revision that leaves
-- `properties` untouched would move neither column, because refresh_features.sql's change test
-- deliberately strips the geometry key before comparing. Nothing in this repo currently produces
-- one for this layer -- ingest/watersheds.py writes the polygon through the same properties
-- payload -- but a future producer that did would leave this watermark behind, and the snapshot
-- would read as current while the boundary had moved.
--
-- THE PREDICATES ARE TRANSCRIBED from watersheds_day_export.sql's own WHERE clause, and must stay
-- transcribed. A watermark computed over a wider population than the export writes would trigger a
-- snapshot for a row that never lands in it; a narrower one would call the lane current while a
-- published row was missing. The two queries answer about exactly the same rows or neither is
-- trustworthy. `idx_features_layer_updated_at` and `idx_features_layer_created_at`
-- (0022_features_write_time_indexes.sql:26-28) both lead on layer_id, so this stays a bounded
-- index read rather than a scan of geo.features.
SELECT
    max(f.updated_at) AS feature_updated_at,
    max(f.created_at) AS feature_created_at,
    GREATEST(max(f.updated_at), max(f.created_at)) AS watermark_at,
    count(*) AS row_count
FROM geo.features AS f
JOIN geo.layers AS l ON l.id = f.layer_id
WHERE l.name = 'watersheds'
  AND f.status = 'published'
  AND f.geom IS NOT NULL
  AND f.properties ->> 'huc12' IS NOT NULL
