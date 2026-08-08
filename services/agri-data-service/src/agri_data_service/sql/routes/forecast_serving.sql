-- Purpose: return one bounded page of published forecast points for a single series,
--          each carrying its full publication / series / support / lineage identity, and
--          optionally the forecast cell's centroid or polygon as GeoJSON.
-- Loaded by: agri_data_service.routes.forecasts
-- Params: series_key (text) -- the one governed series the caller asked for.
--         valid_from (timestamptz, nullable) -- inclusive lower bound on valid time;
--         NULL means "no lower bound".
--         valid_to (timestamptz, nullable) -- exclusive upper bound on valid time;
--         NULL means "no upper bound".
--         spatial_mode (text: 'none' | 'centroid' | 'geojson') -- how much geometry the
--         caller wants back. 'none' returns none, 'centroid' returns a point,
--         'geojson' additionally attempts the cell polygon.
--         fetch_limit (int) -- rows to fetch. The route passes the caller's page size
--         PLUS ONE, then trims the extra row; the presence of that extra row is how it
--         answers "hasMore" without a second counting query.
--         offset (int) -- rows to skip before the page starts.
--
-- Parameter names appear above WITHOUT a leading colon, and the clause quotes in the
-- walkthrough below drop their colons too. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words as well as real
-- SQL, and would mint a bind parameter nobody supplies. The statement itself, below
-- the comments, writes them with the colon as normal.
--
-- Placeholder: this file is .format()ted once, at module import time, before it is
--         handed to text(). Three PEP-3101 named slots -- written in curly braces in
--         the CASE branches below -- are filled from Python module constants in
--         agri_data_service.routes.forecasts. None of them is ever request input, which
--         is what makes baking them into the statement text legal rather than a
--         SQL-injection surface; every value that DOES come from the request is a bound
--         parameter instead. The slots are:
--           max_cell_geometry_storage_bytes  from _MAX_CELL_GEOMETRY_STORAGE_BYTES
--           max_cell_geometry_points         from _MAX_CELL_GEOMETRY_POINTS
--           max_cell_geojson_bytes           from _MAX_CELL_GEOJSON_BYTES
--         Because .format() consumes curly braces, any literal brace added to this file
--         later must be DOUBLED ({{ and }}) or the format call raises KeyError and the
--         whole service fails to import. There are none today.
--
-- What this returns: one row per published forecast point in the requested series and
-- time window, ordered by valid time. Every row is self-describing -- it carries the
-- publication and receipt it came from, the metric and entity it describes, the native
-- and output support it was computed at, the checksums needed to reproduce it, the
-- point value with its p10/p50/p90 band, and (when asked for, and only when small
-- enough) the geometry of the cell it belongs to.
--
-- Why it is safe: it reads only the serving view, which exposes published values alone,
-- so an unpublished or retracted forecast is unreachable from here. The raw forecast
-- value table underneath that view is deliberately never named in this statement, and a
-- unit test asserts as much by searching the statement text for it -- which is also why
-- the walkthrough below refers to it in words rather than writing the name out.
-- It is bounded three ways at once -- a row cap (fetch limit), a serialized-geometry
-- byte cap, and a geometry vertex cap -- so no single request can be made to return an
-- unbounded response.
--
-- How this query works, clause by clause:
--
--   WITH serving_page AS MATERIALIZED (...)
--     A CTE ("common table expression") -- a named subquery written up front and then
--     referenced below like a table. This one picks the exact page of rows the request
--     asked for, so everything after it works on at most a few hundred rows instead of
--     the whole serving view.
--     MATERIALIZED tells the database to compute the CTE once into a temporary result
--     instead of inlining its text into the query that uses it. Without it the planner
--     is free to substitute the subquery at each reference and re-run the work per
--     reference -- which matters a great deal for the second CTE below, whose GeoJSON
--     serialization is referenced three times.
--
--   FROM agri.v_forecast_series_serving AS serving
--     The published serving view. It is the only forecast source this statement reads;
--     the raw value table underneath it is deliberately not touched, so nothing that has
--     not been published can leak into a response.
--
--   LEFT JOIN agri.spatial_cell AS cell ON cell.id = serving.spatial_cell_id
--     Attaches the geographic cell a forecast belongs to, when it has one. LEFT means
--     "keep the left-hand row even if the right-hand table has no match": a forecast for
--     a non-spatial entity has no cell, and a plain (inner) join would silently drop
--     those rows from the page entirely. Where there is no match the cell columns
--     arrive as NULL, which the CASE expressions below read as "no geometry".
--     cell.id is also selected as joined_spatial_cell_id, which lets the outer query
--     tell "there was no cell to serialize" apart from "there was a cell but we chose
--     not to send its geometry" -- two very different answers for a client.
--
--   WHERE serving.series_key = series_key
--     Restricts the whole statement to the single series the request named. Series keys
--     are unique, so this is the primary narrowing filter.
--
--   AND (CAST(valid_from AS timestamptz) IS NULL OR serving.valid_time >= CAST(valid_from AS timestamptz))
--     An optional lower time bound expressed as a single fixed clause rather than by
--     building different SQL for the two cases: when the parameter is NULL the left half
--     is true and the filter passes everything, so one statement text serves both a
--     bounded and an unbounded request.
--     The CAST exists purely to pin the parameter's type. A bare parameter that may be
--     NULL gives the database nothing to infer a type from, and it will either guess
--     wrongly or refuse the statement; naming timestamptz removes the guess. The upper
--     bound clause immediately after it works the same way, except that it uses > rather
--     than >= -- the window is inclusive at the start and exclusive at the end, so
--     adjacent pages of time cannot both contain the same instant.
--
--   ORDER BY serving.valid_time, serving.forecast_receipt_id, serving.horizon_step
--     A total order -- every row sorts strictly before or after every other, with no
--     ties left for the database to break however it likes. Paging without a total order
--     can repeat a row on one page and skip it on the next, because equal rows may come
--     back in a different sequence each time the query runs.
--
--   LIMIT fetch_limit OFFSET offset
--     The page itself: skip offset rows, then take fetch_limit of them. Both are bound
--     parameters, never text pasted into the statement. Note that the route sets
--     fetch_limit to the caller's page size plus one; the extra row never reaches the
--     response, it only proves that a further page exists.
--
--   spatial_page AS MATERIALIZED (SELECT serving_page.*, ...)
--     The second CTE. serving_page.* forwards every column of the page unchanged, and
--     two computed columns are added alongside: a candidate centroid and a candidate
--     polygon, both as GeoJSON text. They are "candidates" because the outer query
--     applies one further size test before deciding to emit them.
--     This CTE is MATERIALIZED for the reason given above: cell_geojson_candidate is
--     read three times by the outer SELECT, and without materialization the planner
--     could re-serialize the same polygon on each read.
--
--   CASE WHEN spatial_mode IN ('centroid', 'geojson')
--        THEN ST_AsGeoJSON(serving_page.cell_centroid, 6, 0) ELSE NULL END
--     A CASE expression is SQL's if/else: it walks its WHEN branches in order and yields
--     the first matching THEN, or the ELSE if none match. This one switches on the
--     request's spatial mode, so a caller who asked for 'none' pays nothing at all for
--     geometry -- the work is skipped, not computed and discarded.
--     ST_AsGeoJSON converts a PostGIS geometry into GeoJSON text. The 6 is the number of
--     decimal places kept for each coordinate (roughly 11 cm on the ground, far finer
--     than any forecast cell needs, and it stops full float precision from bloating the
--     payload). The 0 is an options flag meaning "emit no bounding box and no CRS
--     block" -- both are redundant here, since every geometry served is WGS84.
--
--   CASE WHEN spatial_mode = 'geojson' THEN
--       CASE WHEN pg_column_size(serving_page.cell_geometry) <= {max_cell_geometry_storage_bytes} THEN
--           CASE WHEN ST_NPoints(serving_page.cell_geometry) <= {max_cell_geometry_points}
--                THEN <serialize the cell geometry, as for the centroid above>
--                ELSE NULL END
--       ELSE NULL END
--   ELSE NULL END
--     Three nested CASE expressions forming a guard chain, cheapest test first, and the
--     nesting is what makes them a chain rather than three independent tests: an inner
--     CASE is only ever reached when the CASE around it already matched. So a polygon is
--     serialized only if all three hold, and the serialization call appears exactly once
--     on this path -- serializing first and measuring afterwards would do the expensive
--     work on precisely the oversized geometries the guards exist to refuse. A unit test
--     pins that "exactly once" by counting the call in the statement text, which is why
--     the innermost branch is described above rather than quoted verbatim.
--     pg_column_size is the number of bytes the geometry occupies in storage, which is
--     cheap to read because it does not decode the geometry. ST_NPoints is the number of
--     vertices in it, which does. Storage size is therefore the outer guard and vertex
--     count the inner one.
--     What the caps protect is response size, not the database. A single high-resolution
--     drought or watershed polygon can carry tens of thousands of vertices and expand to
--     megabytes of GeoJSON text; a page of them would produce a response no browser
--     should be asked to parse, on an endpoint whose whole contract is to be bounded.
--
--   spatial_page.centroid_candidate::jsonb AS centroid_geojson
--     ST_AsGeoJSON returns text -- a string that happens to contain JSON. The ::jsonb
--     cast (the double-colon is PostgreSQL's short form of CAST) parses it into a real
--     JSON value, so the driver hands Python a dict rather than a string the route would
--     have to parse again. It also fails loudly here if the text were ever malformed,
--     instead of quietly shipping a broken string to the client.
--
--   CASE WHEN octet_length(spatial_page.cell_geojson_candidate) <= {max_cell_geojson_bytes}
--        THEN spatial_page.cell_geojson_candidate::jsonb ELSE NULL END AS cell_geojson
--     The last size gate, and the only one that measures the thing actually being sent:
--     octet_length is the length of the serialized GeoJSON text in bytes. The two guards
--     in the CTE above are proxies -- storage bytes and vertex count both correlate with
--     output size but neither equals it -- so a geometry can pass both and still expand
--     past the wire budget. This branch is where that is caught. NULL means no geometry
--     is sent for this row.
--
--   CASE WHEN spatial_mode = 'geojson'
--        AND spatial_page.joined_spatial_cell_id IS NOT NULL
--        AND (candidate IS NULL OR octet_length(candidate) > {max_cell_geojson_bytes})
--        THEN true ELSE false END AS geometry_omitted
--     An honesty flag. It is true only when the caller asked for geometry, a cell really
--     existed to provide it (that is what the joined_spatial_cell_id test establishes,
--     and it is why the LEFT JOIN selects that column at all), and the geometry was
--     nonetheless dropped by one of the caps. A client reading NULL geometry can then
--     tell "this forecast has no geography" from "this forecast's geography was too
--     large to send", and retry the latter differently rather than concluding the data
--     is missing.
--
--   ORDER BY spatial_page.valid_time, spatial_page.forecast_receipt_id, spatial_page.horizon_step
--     The same total order as the CTE, repeated. Ordering inside a subquery or CTE is
--     not a promise about the order of the final result; only an ORDER BY on the
--     outermost SELECT is. Repeating it is what makes the page's order guaranteed rather
--     than incidental.
WITH serving_page AS MATERIALIZED (
    SELECT
        serving.publication_id,
        serving.publication_key,
        serving.scope_key,
        serving.publication_manifest_checksum,
        serving.published_at,
        serving.forecast_receipt_id,
        serving.receipt_checksum,
        serving.series_id,
        serving.series_key,
        serving.source_variant_key,
        serving.entity_type,
        serving.entity_key,
        serving.metric_name,
        serving.metric_unit,
        serving.representation_kind,
        serving.spatial_support_kind,
        serving.source_spatial_resolution_m,
        serving.output_spatial_resolution_m,
        serving.source_temporal_support,
        serving.output_temporal_support,
        serving.aggregation_method,
        serving.release_set_id,
        serving.forecast_run_id,
        serving.forecast_method,
        serving.input_release_checksum,
        serving.feature_checksum,
        serving.model_checksum,
        serving.parameter_checksum,
        serving.training_run_id,
        serving.training_code_checksum,
        serving.validation_checksum,
        serving.issue_time,
        serving.valid_time,
        serving.horizon_step,
        serving.point_value,
        serving.p10_value,
        serving.p50_value,
        serving.p90_value,
        serving.spatial_cell_id,
        cell.id AS joined_spatial_cell_id,
        cell.centroid AS cell_centroid,
        cell.geometry AS cell_geometry
    FROM agri.v_forecast_series_serving AS serving
    LEFT JOIN agri.spatial_cell AS cell ON cell.id = serving.spatial_cell_id
    WHERE serving.series_key = :series_key
      AND (CAST(:valid_from AS timestamptz) IS NULL OR serving.valid_time >= CAST(:valid_from AS timestamptz))
      AND (CAST(:valid_to AS timestamptz) IS NULL OR serving.valid_time < CAST(:valid_to AS timestamptz))
    ORDER BY
        serving.valid_time,
        serving.forecast_receipt_id,
        serving.horizon_step
    LIMIT :fetch_limit
    OFFSET :offset
),
spatial_page AS MATERIALIZED (
    SELECT
        serving_page.*,
        CASE
            WHEN :spatial_mode IN ('centroid', 'geojson')
                THEN ST_AsGeoJSON(serving_page.cell_centroid, 6, 0)
            ELSE NULL
        END AS centroid_candidate,
        CASE
            WHEN :spatial_mode = 'geojson' THEN
                CASE
                    WHEN pg_column_size(serving_page.cell_geometry) <= {max_cell_geometry_storage_bytes} THEN
                        CASE
                            WHEN ST_NPoints(serving_page.cell_geometry) <= {max_cell_geometry_points}
                                THEN ST_AsGeoJSON(serving_page.cell_geometry, 6, 0)
                            ELSE NULL
                        END
                    ELSE NULL
                END
            ELSE NULL
        END AS cell_geojson_candidate
    FROM serving_page
)
SELECT
    spatial_page.publication_id,
    spatial_page.publication_key,
    spatial_page.scope_key,
    spatial_page.publication_manifest_checksum,
    spatial_page.published_at,
    spatial_page.forecast_receipt_id,
    spatial_page.receipt_checksum,
    spatial_page.series_id,
    spatial_page.series_key,
    spatial_page.source_variant_key,
    spatial_page.entity_type,
    spatial_page.entity_key,
    spatial_page.metric_name,
    spatial_page.metric_unit,
    spatial_page.representation_kind,
    spatial_page.spatial_support_kind,
    spatial_page.source_spatial_resolution_m,
    spatial_page.output_spatial_resolution_m,
    spatial_page.source_temporal_support,
    spatial_page.output_temporal_support,
    spatial_page.aggregation_method,
    spatial_page.release_set_id,
    spatial_page.forecast_run_id,
    spatial_page.forecast_method,
    spatial_page.input_release_checksum,
    spatial_page.feature_checksum,
    spatial_page.model_checksum,
    spatial_page.parameter_checksum,
    spatial_page.training_run_id,
    spatial_page.training_code_checksum,
    spatial_page.validation_checksum,
    spatial_page.issue_time,
    spatial_page.valid_time,
    spatial_page.horizon_step,
    spatial_page.point_value,
    spatial_page.p10_value,
    spatial_page.p50_value,
    spatial_page.p90_value,
    spatial_page.spatial_cell_id,
    spatial_page.centroid_candidate::jsonb AS centroid_geojson,
    CASE
        WHEN octet_length(spatial_page.cell_geojson_candidate) <= {max_cell_geojson_bytes}
            THEN spatial_page.cell_geojson_candidate::jsonb
        ELSE NULL
    END AS cell_geojson,
    CASE
        WHEN :spatial_mode = 'geojson'
         AND spatial_page.joined_spatial_cell_id IS NOT NULL
         AND (
             spatial_page.cell_geojson_candidate IS NULL
             OR octet_length(spatial_page.cell_geojson_candidate) > {max_cell_geojson_bytes}
         )
            THEN true
        ELSE false
    END AS geometry_omitted
FROM spatial_page
ORDER BY
    spatial_page.valid_time,
    spatial_page.forecast_receipt_id,
    spatial_page.horizon_step
