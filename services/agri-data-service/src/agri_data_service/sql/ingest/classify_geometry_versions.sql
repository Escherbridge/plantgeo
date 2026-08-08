-- Purpose: for every place in the batch, report in one round trip what its version chain looks
--          like against the shape being claimed now -- whether a shape could be resolved at all,
--          which version is currently open, whether the claimed shape differs from it, and
--          whether the claimed observation time is late enough to date a successor.
-- Loaded by: agri_data_service.ingest.geometry
-- Params: natural_keys (text[]) -- one place key per requested place.
--         feature_ids (text[]) -- for a place whose shape is already stored on a geo.features
--         row, that row's id as text; empty string when the shape comes as GeoJSON instead.
--         geojsons (text[]) -- for a place supplying its own shape, RFC 7946 GeoJSON text;
--         empty string when the shape comes from a feature row instead.
--         observed_ats (text[]) -- when the producer says it saw this shape, as timestamptz
--         text. The literal minus-infinity is how "this producer supplied no honest
--         observation time" is spelled, because that has no datetime equivalent in Python.
--
-- All four arrays are positional and must be exactly the same length: element 3 of each one
-- describes the same requested place. Nothing in the SQL can check that, so the caller builds
-- all four from the same sequence in the same pass.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap"
-- in sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and would
-- mint a bind parameter nobody supplies.
--
-- What this returns: one row per requested place, carrying four facts and no decisions. It
-- writes nothing. The caller reads these rows and decides what to do; splitting it that way
-- means the decision logic is ordinary testable Python rather than a nest of CASE branches.
--
-- Background: what "Type-2 versioning" means here
--
--   geo.geometry does not hold one row per place. It holds the whole history of that place's
--   shape as a chain of rows: each row is one version, valid from the instant named in
--   version_valid_from until the instant named in version_valid_to. The newest version of a
--   place has version_valid_to set to NULL, meaning "still true, no end known" -- that is the
--   open version, and there must never be more than one per place. When a shape changes, the
--   open version is stamped with an end instant and pointed at its replacement, and a new open
--   version is written. Nothing is ever overwritten in place, so a reading of a map made last
--   March can still be joined to the shape that was true last March. The four columns below
--   are exactly what the caller needs in order to choose between opening the first version,
--   confirming the existing one, superseding it, or refusing to act.
--
-- How this query works, clause by clause:
--
--   WITH request AS (...)
--     A CTE ("common table expression") -- a named subquery written up front and then
--     referenced below as if it were a table. This one is a pure reshaping step: it turns four
--     parallel arrays into a normal set of rows with named, typed columns, so everything after
--     it reads like a query against a table instead of like array handling.
--
--   unnest(CAST(natural_keys AS text[]), CAST(feature_ids AS text[]), ...)
--     unnest with several arrays at once zips them: it produces one row per position, taking
--     one element from each array. That is what makes the arrays positional, and what makes
--     equal lengths a requirement -- a short array is padded with NULLs rather than rejected,
--     so a length mismatch would silently produce a request with missing fields rather than an
--     error. One call carries the entire batch in one round trip.
--
--     Each CAST exists purely to pin its parameter's type. A bind parameter arrives with no
--     type of its own and unnest is overloaded for every array type, so without the cast the
--     database cannot tell which one is meant. The Python call site declares the same types
--     again with bindparam(...); both halves are needed for a Python list to arrive as a real
--     PostgreSQL text array.
--
--   AS requested(natural_key, feature_id, geojson, observed_at)
--     Names the zipped table and its columns in array order, so the columns can be referred to
--     by name below.
--
--   NULLIF(requested.feature_id, '')
--     Returns NULL when the two arguments are equal, and the value otherwise. Every element of
--     these arrays is text, because an array cannot hold a mixture of "a value" and "nothing";
--     the empty string is the agreed spelling of "this place did not supply this field". NULLIF
--     converts that convention back into a real NULL so the CASE below can test it with IS NOT
--     NULL. Exactly one of feature_id and geojson is ever non-empty for a given place.
--
--   CAST(requested.observed_at AS timestamptz)
--     Parses the observation instant out of its text spelling into a real timestamp with time
--     zone, so it can be compared with version_valid_from further down. It is text on the way
--     in only because minus-infinity has to be expressible.
--
--   resolved AS (...)
--     A second CTE, layered on the first, that answers one question: what geometry is actually
--     being claimed for this place? The two supported answers -- a shape already stored on a
--     feature row, or GeoJSON supplied inline -- are collapsed here into a single geom column,
--     so the comparison below has only one case to handle.
--
--   CASE WHEN request.feature_id IS NOT NULL THEN feature.geom
--        ELSE ST_SetSRID(ST_GeomFromGeoJSON(request.geojson), 4326) END
--     CASE is an if/else that produces a value. The first branch takes the geometry PostGIS has
--     already parsed and normalised onto the feature row -- cheaper and, more importantly,
--     byte-identical to what is stored, so re-parsing cannot introduce a spurious difference.
--     The second branch parses the supplied GeoJSON: ST_GeomFromGeoJSON builds a geometry from
--     the text, and ST_SetSRID stamps it with spatial reference system 4326, which is ordinary
--     longitude/latitude on the WGS 84 globe. GeoJSON carries no SRID of its own, and PostGIS
--     refuses to compare two geometries with different SRIDs, so stamping it is what makes the
--     comparison below possible at all.
--
--   LEFT JOIN geo.features AS feature ON feature.id = CAST(request.feature_id AS uuid)
--     A join matches rows of one table to rows of another on a condition. A plain (inner) join
--     drops rows on the left that find no match; LEFT keeps them, filling the right-hand
--     columns with NULL. LEFT is required here for two reasons: a place supplying GeoJSON has
--     no feature id to match on at all, and a place naming a feature row that does not exist
--     must still come back as a row, so the caller learns its geometry could not be resolved
--     rather than silently losing it from the batch.
--
--   resolved.geom IS NULL AS geometry_missing
--     The refusal signal. It is true when neither source produced a geometry -- a named feature
--     row that is absent, or GeoJSON that parsed to nothing. The caller raises rather than
--     versioning a shape it does not actually have.
--
--   LEFT JOIN geo.geometry AS open_version
--     ON open_version.natural_key = resolved.natural_key
--    AND open_version.version_valid_to IS NULL
--     Finds the place's currently-open version, if it has one. The second condition is what
--     picks the open version out of the whole chain: version_valid_to IS NULL means "not yet
--     ended". Note it sits in the join condition and not in a WHERE clause -- and that
--     distinction is load-bearing. In a WHERE clause it would be applied after the join and
--     would throw away the rows where the LEFT join found nothing, turning it back into an
--     inner join and hiding every place that has no version yet, which is precisely the case
--     the caller most needs to hear about.
--
--   open_version.geometry_id AS open_geometry_id
--     NULL when the place has no open version. That is how the caller distinguishes "this place
--     has never been versioned, open its first version" from every other outcome.
--
--   ((open_version.geom = resolved.geom) OR ST_Equals(open_version.geom, resolved.geom))
--     Two different notions of "the same shape", deliberately tried in this order.
--
--     The = operator on geometries is PostGIS exact equality: same coordinates, in the same
--     order, in the same structure. It is cheap, it can use an index, and it is what carries
--     virtually every tick, because an unchanged upstream shape normally arrives byte-identical
--     to the one already stored.
--
--     ST_Equals is the topological fallback: it asks whether the two shapes occupy the same
--     space, regardless of how they are written down. A polygon whose ring starts at a
--     different vertex, or that has been re-encoded by a producer's serialiser, is a different
--     sequence of coordinates but the same region. Without this second test such a re-encoding
--     would look like a genuine change and would mint a new version on every run forever. It is
--     much more expensive than =, which is why it is second: OR stops as soon as the cheap test
--     succeeds, so the expensive one runs only on the rows that actually differ literally.
--     See ingest/AGENTS.md "geometry.py".
--
--     When the place has no open version, open_version.geom is NULL and the whole expression is
--     NULL rather than false -- SQL comparisons against NULL yield "unknown", not "no". The
--     caller reads this column as "true only if it is exactly true", so unknown behaves as not
--     unchanged, which is the safe reading.
--
--   (resolved.observed_at > open_version.version_valid_from) AS successor_is_datable
--     Whether the claimed observation is strictly later than the moment the open version began.
--     A new version has to start at some instant, and the only honest instant available is when
--     the producer says it saw the new shape. If that instant is not after the open version's
--     own start, there is no boundary that can be cut without inventing history -- the two
--     versions would overlap or the newer one would start before the older one. Strictly later,
--     not later-or-equal, is what the comparison says, and a producer that stamps a redrawn
--     shape with the same second as the previous version therefore lands here as not datable.
--     This is also NULL rather than false when either side is missing, and the caller again
--     treats only an explicit true as true.
WITH request AS (
    SELECT requested.natural_key,
           NULLIF(requested.feature_id, '') AS feature_id,
           NULLIF(requested.geojson, '') AS geojson,
           CAST(requested.observed_at AS timestamptz) AS observed_at
    FROM unnest(
             CAST(:natural_keys AS text[]),
             CAST(:feature_ids AS text[]),
             CAST(:geojsons AS text[]),
             CAST(:observed_ats AS text[])
         ) AS requested(natural_key, feature_id, geojson, observed_at)
),
resolved AS (
    SELECT request.natural_key,
           request.observed_at,
           CASE
               WHEN request.feature_id IS NOT NULL THEN feature.geom
               ELSE ST_SetSRID(ST_GeomFromGeoJSON(request.geojson), 4326)
           END AS geom
    FROM request
    LEFT JOIN geo.features AS feature ON feature.id = CAST(request.feature_id AS uuid)
)
SELECT resolved.natural_key AS natural_key,
       resolved.geom IS NULL AS geometry_missing,
       open_version.geometry_id AS open_geometry_id,
       ((open_version.geom = resolved.geom) OR ST_Equals(open_version.geom, resolved.geom))
           AS geometry_unchanged,
       (resolved.observed_at > open_version.version_valid_from) AS successor_is_datable
FROM resolved
LEFT JOIN geo.geometry AS open_version
  ON open_version.natural_key = resolved.natural_key
 AND open_version.version_valid_to IS NULL
