-- ssurgo_point
-- Purpose: answer "what soil is at this exact location" -- STAGE 1 of a two-stage answer: every
--         admitted SSURGO delineation whose PRECOMPUTED bounding box contains one point, at the
--         z13 native rung. STAGE 2 (the exact ring test) runs afterwards in Python.
-- Loaded by: agri_data_service.planes.soil_survey (registers the temporary view "ssurgo_view"
--            from an in-memory Arrow table before running this query; see that module's
--            "Admitted release read path" section)
-- Params (positional, bound by DuckDB, in this order):
--   1 longitude, 2 longitude (repeated), 3 latitude, 4 latitude (repeated) -- the requested point,
--     WGS84 degrees; repeated because the bbox prefilter below reads it once per box edge
--   5 candidate_row_limit -- a STRUCTURAL ceiling
--     (`planes.soil_survey._MAX_POINT_CANDIDATE_ROWS` = MAX_PARTS_PER_VIEWPORT * MAX_PART_ROWS),
--     NOT the caller's own match cap (`DEFAULT_MAX_POINT_MATCHES`): this query is only the bbox
--     prefilter, so it is bounded by how many rows a gathered table can ever hold, not by how many
--     matches the caller asked for.
-- Dialect: DuckDB with the spatial extension, run inside the serving session's bounded read;
--          no PostgreSQL involvement, and no GEOS predicate (see below).
--
-- TWO-STAGE selection (Q4, 2026-09-27; revised after review finding 2, 2026-09-27): a GEOS
-- predicate here -- ST_Covers(geometry, ST_Point(...)) -- would silently exclude any delineation
-- whose ring is invalid_unrepaired, turning "always serve" into "serve unless the point happens to
-- fall inside a bad ring". So membership is decided in two stages instead of one SQL predicate:
--   STAGE 1 (this file): every delineation whose bbox_* columns contain the point -- a cheap
--   prefilter on plain DOUBLE columns, exactly like ssurgo_viewport.sql's viewport overlap test,
--   and just as capable of over-matching at a delineation's corners.
--   STAGE 2 (`planes.soil_survey._filter_point_candidates_by_ring`, Python): the TRUE ring is
--   tested against the point using `wkb_polygon_contains_point`, a hand-rolled, dependency-free
--   ray-cast (the SAME decoder the day-partitioned point-lookup lane above already uses) -- not
--   GEOS, so Q4's "never a GEOS predicate" rule still holds even though this stage IS an exact
--   geometric test. A delineation whose `geometry_wkb` cannot be decoded is KEPT rather than
--   dropped, so a bad geometry still surfaces labelled `invalid_unrepaired` instead of vanishing.
--   `geometry_wkb` is therefore selected here (not excluded), because stage 2 needs the raw bytes;
--   `ST_AsGeoJSON` still renders the real ring for whichever rows survive both stages, so the map
--   draws each match's true shape.
--
-- How this query works, clause by clause:
--
--   SELECT *, ST_AsGeoJSON(ST_GeomFromWKB(geometry_wkb)) AS geometry_json
--     Every stored column, INCLUDING geometry_wkb (stage 2 needs it), plus the same rendering-only
--     GeoJSON column ssurgo_viewport.sql produces: ST_GeomFromWKB parses the stored bytes and
--     ST_AsGeoJSON renders them. This pair only RENDERS the ring; it never tests it -- that is
--     stage 2's job, in Python, using a different decode of the same bytes.
--
--   WHERE bbox_west <= ? AND bbox_east >= ? AND bbox_south <= ? AND bbox_north >= ?
--     "the point's longitude falls between this row's west and east edges, AND the point's
--     latitude falls between this row's south and north edges" -- the standard "box contains
--     point" test, on the plain DOUBLE bbox columns only.
--
--   ORDER BY mupolygonkey
--     Same stable ordering as ssurgo_viewport.sql, carried through the Python filter, so a repeated
--     call for the same point returns candidates -- and therefore matches -- in the same order.
--
--   LIMIT ?
--     Bounded by the structural ceiling described above, so a pathological viewport with many
--     bbox-overlapping candidates cannot make this query, or the Python ring test that follows it,
--     unbounded.
SELECT
    *,
    ST_AsGeoJSON(ST_GeomFromWKB(geometry_wkb)) AS geometry_json
FROM ssurgo_view
WHERE bbox_west <= ?
  AND bbox_east >= ?
  AND bbox_south <= ?
  AND bbox_north >= ?
ORDER BY mupolygonkey
LIMIT ?
