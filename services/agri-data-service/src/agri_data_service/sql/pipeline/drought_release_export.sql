-- drought_release_export
-- Purpose: export every US Drought Monitor drought-class polygon of one weekly release
--          (geo.drought_areas) to Parquet, for writing to
--          layer=drought/kind=observed/year=/month=/day=/part-N.parquet.
-- Loaded by: agri_data_service.pipeline.lanes.drought
-- Params: valid_date (text -- the exact ISO YYYY-MM-DD release Tuesday to export, matched by
--         equality against the stored column; never a range, since one release is one
--         indivisible weekly unit, see ingest/usdm.py DroughtRelease)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md -- SQLAlchemy's text() scans comments too, and a colon-prefixed word here would
-- mint a phantom bind parameter no caller supplies.
--
-- THIS LANE HAS horizon none (warehouse/schemas/drought.py) -- a drought class is a published
-- USDM assessment, not a physical process this repo projects forward, so there is no forecast
-- sibling and no date-range predicate to widen: exactly one release, exactly one Tuesday, per
-- call.
--
-- geo.drought_areas.valid_date is stored as varchar with length ten, NOT a native date column,
-- guarded by a CHECK constraint that proves the shape (drizzle/0007_governed_environmental_
-- ingestion.sql, lines 11 and 17: a four-digit year, a hyphen, a two-digit month, a hyphen, a
-- two-digit day, and nothing else). The equality filter below therefore compares text to text;
-- the conversion into a real calendar date happens only in the SELECT list, once the row is
-- already known to match.
--
-- How this query works, clause by clause:
--
--   to_date(valid_date, 'YYYY-MM-DD')
--     Parses the stored text into a real calendar date using the given pattern (four-digit year,
--     two-digit month, two-digit day). Deliberately UNGUARDED, unlike the reporting query in
--     drought_area_observed_days.sql: that query scans every historical row and must survive one
--     bad string to report honestly on the rest, but this query has already narrowed to one
--     release by the equality filter below, so if the stored text for that one release ever
--     failed to parse the honest answer is to abort the export with a database error, never to
--     coerce a fabricated date or silently drop the row.
--
--   WHERE valid_date matches the bound release parameter
--     Exact text equality against the parameter -- one release is one indivisible unit
--     (ingest/usdm.py, DroughtRelease), so this is never a range predicate.
--
--   id AS area_id (cast to text)
--     geo.drought_areas' own surrogate key, carried through as provenance back to the warehouse
--     row; it is not part of the export grain, which is the (valid_date, dm_category) pair the
--     source table's own unique index already enforces (see ORDER BY below).
--
--   ST_AsBinary(geom) AS geom
--     The polygon as well-known binary rather than GeoJSON text, this lane's chosen wire format
--     for geometry (matches watersheds_day_export.sql). WKB carries no coordinate-system header;
--     every row in this stream is WGS 84, EPSG number 4326 -- the column's own declared type is
--     geometry of kind MULTIPOLYGON in that reference system (drizzle/0007_governed_
--     environmental_ingestion.sql, line 13), confirmed again by the write path's own SRID stamp
--     onto the same reference system (sql/ingest/store_drought_area.sql, line 103). A reader
--     must apply that reference system itself; nothing in the Parquet file records it.
--
--   ORDER BY dm_category
--     The lane's grain: one row per drought class of one release, the same pair
--     (valid_date, dm_category) the source table's own unique index enforces
--     (drizzle/0007_governed_environmental_ingestion.sql, line 20). valid_date is already
--     constant across every row this statement returns, so ordering by dm_category alone
--     produces the grain's full sort order. Sorting here, ahead of the sort write_partition also
--     applies per part, keeps row order stable across any part files pipeline.lanes.drought
--     slices this release into.
SELECT
    id::text                          AS area_id,
    to_date(valid_date, 'YYYY-MM-DD') AS valid_date,
    dm_category,
    source_url,
    ingested_at,
    ST_AsBinary(geom)                 AS geom
FROM geo.drought_areas
WHERE valid_date = :valid_date
ORDER BY dm_category
