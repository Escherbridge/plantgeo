-- Purpose: resolve the identity of one NDVI source release back to its stored row id.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: data_source_id (uuid) -- the registered Sentinel-2 NDVI source; source_version (text) --
--         the upstream product version label; payload_checksum (text) -- the corpus fingerprint;
--         transform_version (text) -- the immutable name of the transform that produced the corpus.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: exactly one row holding the release's id. It is the read half of a
-- write-then-read pair: insert_source_release.sql writes the row and deliberately reports nothing,
-- and this statement then resolves the id whether that insert created the row or found it already
-- there. The caller reads the result as strictly-one, so both no row and several rows are errors
-- rather than something to paper over.
--
-- How this query works, clause by clause:
--
--   WHERE data_source_id = ... AND source_version = ... AND payload_checksum = ...
--         AND transform_version = ...
--     The four ANDed predicates are not an arbitrary filter: together they are exactly the columns
--     the table's uniqueness constraint is declared on, which is what guarantees at most one row can
--     match. Matching on fewer of them -- on the checksum alone, say -- could return a release
--     produced by a different transform over identical bytes, and the caller would pin its forecasts
--     to the wrong lineage. AND means every condition must hold; a row satisfying three of the four
--     is not a match.
SELECT id
FROM agri.source_release
WHERE data_source_id = :data_source_id
  AND source_version = :source_version
  AND payload_checksum = :payload_checksum
  AND transform_version = :transform_version
