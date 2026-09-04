-- Purpose: report when the published WFIGS incident-perimeter set last CHANGED -- the source
--          watermark the fire-perimeters static lane stamps its snapshot with, instead of
--          stamping the cron's run date.
-- Loaded by: agri_data_service.pipeline.parquet.lane_registry
-- Params: none
--
-- THIS LANE IS A CURRENT-STATE SNAPSHOT WITH A VERSION, NOT A DAILY SERIES. WFIGS publishes
-- `_Current` -- a live mutable set of active incidents with no archive of what it said yesterday
-- (docs/lanes/fire-perimeters.md section 6) -- and geo.features refreshes one row per incident in
-- place (section 4). The day in the partition path is therefore a version stamp. The lane writes
-- ONE snapshot dated at whatever day this query reports, and nothing at all while a partition dated
-- at or after that day already exists -- so a tick the cron skipped costs nothing, because no day
-- ever carried an obligation. This replaced a daily_series registration on 2026-09-04; the export
-- SQL's header records what that shape cost.
--
-- WHY updated_at IS A REAL CHANGE CLOCK HERE. This layer is refreshed in place through
-- sql/ingest/refresh_features.sql, whose UPDATE is gated on
-- `(properties - 'geometry' - 'geometry_repaired') IS DISTINCT FROM (next_properties - 'geometry')`
-- (refresh_features.sql:130-137). That statement is layer-agnostic -- one gate, layer bound as a
-- parameter -- so the hourly WFIGS poll that finds an incident unchanged moves nothing. Without
-- that gate this column would be a poll clock and this lane would churn a full re-snapshot every
-- hour.
--
-- THREE CHANGE EVENTS, GREATEST OF THE THREE, transcribed from
-- sql/pipeline/lane_watermark_evacuation_zones.sql because both lanes read the same table the same
-- way:
--   * feature.updated_at        -- an attribute changed (containment percent, acreage, severity
--                                 bucket, incident name).
--   * feature.created_at        -- a brand-new incident appeared. An insert moves created_at and a
--                                 refresh of an already-walked row moves only updated_at
--                                 (0022_features_write_time_indexes.sql:13), so neither column
--                                 alone sees every change.
--   * geometry.version_valid_from -- the Type-2 chain minted a new polygon version. Reading this
--                                 matters because refresh_features.sql's change test strips the
--                                 geometry key before comparing, so a shape-only revision moves
--                                 neither of the two columns above.
-- geo.geometry.last_confirmed_at is deliberately NOT read: it advances on every re-confirmation of
-- unchanged ground, which makes it a poll clock, and a poll clock in a version stamp is the
-- fabrication this model exists to refuse.
--
-- WHAT THIS WATERMARK CANNOT SEE, named rather than left to be discovered. When WFIGS reports a
-- materially different polygon but supplies no instant strictly later than the open version's own
-- start, the geometry adapter records `undatable`, leaves the chain untouched and does not update
-- staleness either -- "the same divergence is re-detected on the next tick, and the next, forever"
-- (ingest/AGENTS.md:391; two production rows sat in this state at the 2026-08-10 measurement). None
-- of the three columns below moves for such a row. That is faithful rather than broken: the
-- WAREHOUSE genuinely did not change, so there is no new version to stamp. The staleness is
-- upstream in ingest, and re-snapshotting here could not repair it.
--
-- EXPECTED CADENCE, so a reviewer is not surprised by the bill. WFIGS is polled hourly and this
-- layer holds 177 published rows averaging 130,583 B of geometry each (lane doc section 5), so
-- during fire season at least one incident's containment or acreage will usually have advanced
-- since yesterday and this watermark will move most days -- one full snapshot of roughly 23 MB at
-- the base rung, plus its coarse ladder. That is the price of a shape that answers the map in one
-- read; the daily_series shape it replaced wrote a partition per day too, and still could not.
--
-- geo.geometry is LEFT JOINed, never INNER JOINed, and geometry_id is that table's PRIMARY KEY
-- (0008_geometry_dimension.sql:33) so the join is strictly one-to-at-most-one and count(*) stays a
-- count of features. A feature not yet linked to a geometry version still has a shape on
-- feature.geom, is still drawn by geo.fire_risk_tiles and still exports, so an INNER JOIN here
-- would compute the watermark over a narrower population than the snapshot writes.
--
-- THE PREDICATES ARE TRANSCRIBED from fire_perimeters_day_export.sql's own WHERE clause, which is
-- itself a transcription of geo.fire_risk_tiles, and must stay transcribed -- a watermark over a
-- different population than the export writes either triggers a snapshot for rows that never land
-- in it, or calls the lane current while a published row is missing.
SELECT
    max(feature.updated_at) AS feature_updated_at,
    max(feature.created_at) AS feature_created_at,
    max(geometry.version_valid_from) AS geometry_version_valid_from,
    GREATEST(
        max(feature.updated_at),
        max(feature.created_at),
        max(geometry.version_valid_from)
    ) AS watermark_at,
    count(*) AS row_count
FROM geo.features AS feature
JOIN geo.layers AS layer ON layer.id = feature.layer_id
LEFT JOIN geo.geometry AS geometry ON geometry.geometry_id = feature.geometry_id
WHERE layer.name = 'fire-perimeters'
  AND layer.is_public IS TRUE
  AND feature.status = 'published'
  AND feature.geom IS NOT NULL
