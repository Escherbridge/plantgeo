-- Purpose: write the new open version of every place the caller decided to open or supersede,
--          taking the geometry and its centroid from PostGIS rather than from Python, in one
--          statement for the whole batch.
-- Loaded by: agri_data_service.ingest.geometry
-- Params: geometry_ids (text[]) -- the identifier each new version will carry, minted in Python
--         as a fresh UUID before this runs so that close_geometry_versions.sql can name it as a
--         successor in the same transaction.
--         natural_keys (text[]) -- the place each version belongs to.
--         producers (text[]) -- which upstream produced the shape.
--         version_valid_froms (text[]) -- the instant each version starts being true, as
--         timestamptz text. The literal minus-infinity is how "this producer supplied no honest
--         observation time" is spelled, because that has no datetime equivalent in Python.
--         feature_ids (text[]) -- for a place whose shape is already stored on a geo.features
--         row, that row's id as text; empty string when the shape comes as GeoJSON instead.
--         geojsons (text[]) -- for a place supplying its own shape, RFC 7946 GeoJSON text;
--         empty string when the shape comes from a feature row instead.
--         grid_names (text[]) -- for a raster grid cell, the name of the grid; empty string for
--         a place that is not a grid cell.
--         cell_keys (text[]) -- for a raster grid cell, the cell's key within that grid; empty
--         string otherwise.
--         resolution_metres (text[]) -- for a raster grid cell, its resolution in metres,
--         spelled as text; empty string otherwise.
--         run_clock (timestamptz) -- when this ingestion run happened. Unlike everything above
--         it is bound as a real timestamp, not text, and it is a write clock rather than a
--         validity bound: its only destination is last_confirmed_at.
--
-- Every array is positional and they must all be exactly the same length: element 3 of each one
-- describes the same new version. Nothing in the SQL can check that, so the caller builds all
-- nine from the same sequence in the same pass.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap"
-- in sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and would
-- mint a bind parameter nobody supplies.
--
-- What this returns: the identifier and place key of every version row actually written. Rows
-- that lost a race and were skipped are absent, so the result is what landed and not what was
-- asked for. The caller does not trust it as the final answer either -- it re-reads the open
-- version per place afterwards with select_current_geometry_ids.sql, so the identifier it hands
-- back is always the one the database kept.
--
-- Background: what "Type-2 versioning" means here
--
--   geo.geometry keeps the whole history of a place's shape as a chain of rows rather than one
--   row it overwrites. Each row is valid from version_valid_from until version_valid_to, and
--   the newest row of a chain has version_valid_to set to NULL, meaning "still true, no end
--   known". That NULL-ended row is the open version, and a place must never have two. This
--   statement writes new open versions -- both the first version of a place that has never been
--   versioned, and the successor of a chain that close_geometry_versions.sql just ended. Both
--   halves of a supersession run inside one transaction, so a reader never sees a place with no
--   open version or with two.
--
-- How this query works, clause by clause:
--
--   WITH request AS (...)
--     A CTE ("common table expression") -- a named subquery written up front and then
--     referenced below as if it were a table. This one is a pure reshaping step: it turns nine
--     parallel arrays into a normal set of rows with named, typed columns, so everything after
--     it reads like a query against a table instead of like array handling.
--
--   unnest(CAST(geometry_ids AS text[]), CAST(natural_keys AS text[]), ...)
--     unnest with several arrays at once zips them: it produces one row per position, taking
--     one element from each array. That is what makes the arrays positional, and what makes
--     equal lengths a requirement -- a short array is padded with NULLs rather than rejected,
--     so a length mismatch would silently write a version with missing fields rather than fail.
--     One call carries the entire batch in one round trip.
--
--     Each CAST exists purely to pin its parameter's type. A bind parameter arrives with no
--     type of its own and unnest is overloaded for every array type, so without the cast the
--     database cannot tell which one is meant. The Python call site declares the same types
--     again with bindparam(...); both halves are needed for a Python list to arrive as a real
--     PostgreSQL text array. Every array is text for one reason: an array holds a single type,
--     and text is the one spelling that can carry a UUID, a timestamp, an integer and the
--     literal minus-infinity alike.
--
--   AS requested(geometry_id, natural_key, producer, version_valid_from, ...)
--     Names the zipped table and its columns in array order, so they can be referred to by name
--     below rather than by position.
--
--   NULLIF(requested.feature_id, '')
--     Returns NULL when the two arguments are equal, and the value otherwise. Because the
--     arrays are all text, the empty string is the agreed spelling of "this place did not
--     supply this field"; NULLIF converts that convention back into a real NULL so the CASE
--     branches below can test it with IS NOT NULL. Exactly one of feature_id and geojson is
--     ever non-empty for a given place, and the three grid columns are either all empty or all
--     populated together.
--
--   CAST(NULLIF(requested.resolution_metres, '') AS integer)
--     Nested deliberately, and the order matters: the empty string is turned into NULL first,
--     because casting an empty string straight to integer is an error rather than a NULL. The
--     column is a real integer in the table, so the text spelling has to be parsed here.
--
--   resolved AS (...)
--     A second CTE, layered on the first, that answers one question: what geometry is actually
--     being stored for this place? The two supported answers -- a shape already stored on a
--     feature row, or GeoJSON supplied inline -- are collapsed into a single geom column, so
--     the INSERT below has only one case to handle.
--
--   SELECT request.*, CASE ... END AS geom
--     The star copies through every column the first CTE produced, so the second one only has
--     to add the geometry rather than restate the other nine columns.
--
--   CASE WHEN request.feature_id IS NOT NULL THEN feature.geom
--        ELSE ST_SetSRID(ST_GeomFromGeoJSON(request.geojson), 4326) END
--     CASE is an if/else that produces a value. The first branch takes the geometry PostGIS has
--     already parsed and normalised onto the feature row, which is both cheaper and
--     byte-identical to what is stored, so the version cannot drift from the feature it was
--     derived from. The second branch parses the supplied GeoJSON: ST_GeomFromGeoJSON builds a
--     geometry from the text, and ST_SetSRID stamps it with spatial reference system 4326 --
--     ordinary longitude/latitude on the WGS 84 globe. GeoJSON carries no SRID of its own, and
--     the column will not accept a geometry whose SRID does not match, so stamping it is what
--     makes the write legal.
--
--   LEFT JOIN geo.features AS feature ON feature.id = CAST(request.feature_id AS uuid)
--     A join matches rows of one table to rows of another on a condition. A plain (inner) join
--     drops rows on the left that find no match; LEFT keeps them, filling the right-hand
--     columns with NULL. LEFT is required because a place supplying GeoJSON has no feature id
--     to match on at all, and an inner join would drop every such place from the batch.
--
--   INSERT INTO geo.geometry (...) SELECT ... FROM resolved
--     An insert whose rows come from a query rather than from a VALUES list. That is what makes
--     this one statement for the whole batch: the SELECT produces as many rows as the arrays
--     had elements, and every one of them is written in a single pass.
--
--   CASE WHEN resolved.grid_name IS NOT NULL THEN 'grid_cell'
--        WHEN GeometryType(resolved.geom) IN ('POINT', 'MULTIPOINT') THEN 'point'
--        WHEN GeometryType(...) IN ('POLYGON', 'MULTIPOLYGON') THEN 'polygon'
--        WHEN GeometryType(...) IN ('LINESTRING', 'MULTILINESTRING') THEN 'line' END
--     Names what kind of thing this version is. A grid cell is a grid cell whatever its outline
--     happens to be, so that test comes first; otherwise the kind is read off the geometry
--     itself with GeometryType, which reports the PostGIS type name of a shape.
--
--     Note there is no ELSE branch, and that omission is the point. A CASE with no matching
--     branch yields NULL, the geom_kind column is NOT NULL, and so a geometry of a type this
--     dimension does not name -- a collection, a curve -- fails the write instead of being
--     stored under a kind nobody can interpret. The backfill script under scripts/ that first
--     populated this table refuses on exactly the same grounds: never store a version whose
--     kind the dimension does not name. Widening the dimension is a deliberate act, not
--     something an unexpected upstream shape gets to do by accident.
--
--   ST_Centroid(resolved.geom)
--     The geometric centre of the shape, stored alongside it. It is computed here, by PostGIS,
--     from the very geometry being written, rather than being sent from Python -- so the two
--     columns can never disagree, and a centroid is available for cheap map queries that do not
--     need the full outline.
--
--   NULL (in the version_valid_to position)
--     Written explicitly to say what it means: this is the new open version, valid from its
--     start instant with no end known yet.
--
--   CAST(run_clock AS timestamptz) (in the last_confirmed_at position)
--     Staleness only -- "the most recent run that saw this version agreeing with upstream". It
--     is never a validity bound; version_valid_from and version_valid_to are the only columns
--     that say when a shape was true. Mixing the two would let an ingestion schedule rewrite
--     history.
--
--   ON CONFLICT (natural_key) WHERE version_valid_to IS NULL DO NOTHING
--     ON CONFLICT says what to do when an insert would violate a unique constraint, instead of
--     raising an error. The constraint here is the partial unique index that permits at most
--     one row per place with version_valid_to IS NULL -- the database's own enforcement of "a
--     place has exactly one open version". The WHERE clause repeats the index's condition
--     because a partial index has to be identified by it.
--
--     DO NOTHING means the conflicting row is skipped and the rest of the batch still lands.
--     The alternative form, DO UPDATE SET column = EXCLUDED.column, would instead overwrite the
--     existing row, where EXCLUDED is a pseudo-table naming the row that was being inserted --
--     the values that lost -- so that the update can choose between the old and the new value.
--     DO UPDATE is exactly wrong here: overwriting the open version is the one thing a
--     history-keeping table must never do, because it destroys the shape that was true before.
--
--     Reaching this at all means another transaction opened a version for the same place
--     between this run's classification and this write. The advisory lock taken by
--     lock_geometry_keys.sql is what normally makes that impossible; DO NOTHING is the backstop
--     that makes a lost race lose harmlessly rather than raise, and the caller's re-read
--     afterwards is what makes it lose *correctly*, by adopting the winner's identifier.
--
--   RETURNING geometry_id, natural_key
--     RETURNING makes a writing statement also produce rows, describing what it just changed --
--     the same trip that performs the write reports its result. Combined with DO NOTHING it
--     reports only the rows that actually landed, which is how a lost race becomes visible
--     rather than assumed away.
WITH request AS (
    SELECT requested.geometry_id,
           requested.natural_key,
           requested.producer,
           CAST(requested.version_valid_from AS timestamptz) AS version_valid_from,
           NULLIF(requested.feature_id, '') AS feature_id,
           NULLIF(requested.geojson, '') AS geojson,
           NULLIF(requested.grid_name, '') AS grid_name,
           NULLIF(requested.cell_key, '') AS cell_key,
           CAST(NULLIF(requested.resolution_metres, '') AS integer) AS resolution_metres
    FROM unnest(
             CAST(:geometry_ids AS text[]),
             CAST(:natural_keys AS text[]),
             CAST(:producers AS text[]),
             CAST(:version_valid_froms AS text[]),
             CAST(:feature_ids AS text[]),
             CAST(:geojsons AS text[]),
             CAST(:grid_names AS text[]),
             CAST(:cell_keys AS text[]),
             CAST(:resolution_metres AS text[])
         ) AS requested(
             geometry_id, natural_key, producer, version_valid_from,
             feature_id, geojson, grid_name, cell_key, resolution_metres
         )
),
resolved AS (
    SELECT request.*,
           CASE
               WHEN request.feature_id IS NOT NULL THEN feature.geom
               ELSE ST_SetSRID(ST_GeomFromGeoJSON(request.geojson), 4326)
           END AS geom
    FROM request
    LEFT JOIN geo.features AS feature ON feature.id = CAST(request.feature_id AS uuid)
)
INSERT INTO geo.geometry (
    geometry_id, natural_key, version_valid_from, version_valid_to,
    geom_kind, geom, centroid, grid_name, cell_key, resolution_m, producer, last_confirmed_at
)
SELECT CAST(resolved.geometry_id AS uuid),
       resolved.natural_key,
       resolved.version_valid_from,
       NULL,
       CASE
           WHEN resolved.grid_name IS NOT NULL THEN 'grid_cell'
           WHEN GeometryType(resolved.geom) IN ('POINT', 'MULTIPOINT') THEN 'point'
           WHEN GeometryType(resolved.geom) IN ('POLYGON', 'MULTIPOLYGON') THEN 'polygon'
           WHEN GeometryType(resolved.geom) IN ('LINESTRING', 'MULTILINESTRING') THEN 'line'
       END,
       resolved.geom,
       ST_Centroid(resolved.geom),
       resolved.grid_name,
       resolved.cell_key,
       resolved.resolution_metres,
       resolved.producer,
       CAST(:run_clock AS timestamptz)
FROM resolved
ON CONFLICT (natural_key) WHERE version_valid_to IS NULL DO NOTHING
RETURNING geometry_id, natural_key
