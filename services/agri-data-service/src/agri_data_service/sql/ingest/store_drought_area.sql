-- Purpose: store one US Drought Monitor drought-class polygon, repairing its rings inside PostGIS,
--          and report back both whether the repair emptied it and whether a row was actually written.
-- Loaded by: agri_data_service.ingest.usdm
-- Params: geometry (text holding GeoJSON) -- the class polygon exactly as the publisher sent it,
--         valid_date (date) -- the release day this class belongs to,
--         dm_category (int) -- the drought class, 0 through 4,
--         source_url (text) -- where the release was fetched from, kept for provenance.
-- Placeholder: replace_predicate -- a PEP-3101 named slot, filled in at LOAD TIME from a Python
--         boolean (the operator's `--replace` flag) with the literal word true or the literal word
--         false. It is NEVER request input and never anything a caller supplies, which is the only
--         reason substituting it into the text rather than binding it is acceptable here. See
--         "Constant interpolation" in sql/AGENTS.md. It is the one and only brace pair in this file;
--         any other literal brace would have to be doubled or Python's str.format would raise.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too and would mint a bind
-- parameter nobody supplies.
--
-- What this returns: exactly one row, always, whether or not anything was written. `repaired_to_empty`
-- is true when repairing the publisher's polygon left nothing behind; `rows_stored` is 1 when a row
-- was written or replaced and 0 when the conflict rule declined to touch the existing row.
--
-- WHY THE REPAIR RUNS IN A CTE INSTEAD OF INLINE: so the same repaired expression can be both stored
-- and tested for emptiness in ONE statement. `geo.drought_areas.geom` is NOT NULL, and a NOT NULL
-- column happily accepts `MULTIPOLYGON EMPTY` -- which is exactly what the repair chain yields from a
-- zero-area ring. Storing that would say "this drought class exists and covers nothing", a fabricated
-- coverage claim wearing a valid geometry's shape, and `ST_Intersects` against it answers false for
-- every point on Earth without complaining. Production holds 0 such rows today; the guard exists
-- because widening `DROUGHT_AREA_GEOMETRY_TYPES` widened what can reach this statement. See
-- ingest/AGENTS.md "usdm.py".
--
-- How this query works, clause by clause:
--
--   WITH repaired AS (...)
--     A CTE ("common table expression") -- a named subquery written up front and then referenced below
--     like a table. This one yields a single row holding one repaired geometry. Naming it is what lets
--     the two steps below both use the SAME repaired value; writing the chain twice would repeat an
--     expensive computation and, worse, leave two places for it to drift apart.
--
--   ST_GeomFromGeoJSON(<the geometry parameter>)
--     PostGIS. Parses the publisher's GeoJSON text into a geometry value. The polygon crosses the
--     Python/database boundary as text and is never turned into a Python shape -- the database is the
--     single implementation of what a valid ring is.
--
--   ST_SetSRID(..., 4326)
--     Stamps the geometry with a spatial reference id. 4326 is WGS 84 longitude/latitude, which
--     everything in this warehouse is stored in. GeoJSON carries no SRID of its own, so without this
--     the value would be "unknown coordinate system" and would refuse to compare with anything.
--
--   ST_MakeValid(...)
--     Repairs a self-intersecting or otherwise invalid ring into a valid geometry. Publishers emit
--     these routinely at national resolution. Repairing may change the geometry's TYPE -- a bow-tie
--     polygon becomes a collection of pieces -- which is why the next step exists.
--
--   ST_CollectionExtract(..., 3)
--     Pulls just the polygonal parts out of whatever ST_MakeValid produced; 3 is PostGIS's type code
--     for polygons. Stray points and lines from a repaired ring are discarded rather than stored.
--
--   ST_Multi(...)
--     Wraps the result as a MULTIPOLYGON, so a class made of one contiguous area and a class made of
--     several are stored in the same column type. The publisher emits a bare Polygon for the first
--     case and a MultiPolygon for the second; this normalises the difference away.
--
--   stored AS (INSERT ... RETURNING id)
--     A data-modifying CTE. PostgreSQL allows a write inside WITH, and its RETURNING rows become the
--     CTE's contents, which is how the count below is obtained without a second round trip. Every part
--     of the statement sees the same snapshot, so the emptiness verdict below is computed from the
--     same repaired value that was offered for storage.
--
--   SELECT <parameters>, repaired.geom FROM repaired WHERE NOT ST_IsEmpty(repaired.geom)
--     The write's source rows. `ST_IsEmpty` is true for a geometry with no points at all; negating it
--     means a repair that emptied the polygon produces NO source row, so nothing is written. This is
--     the guard described above, enforced in the statement rather than only in the reaction to it.
--
--   ON CONFLICT (valid_date, dm_category) DO UPDATE SET ... WHERE <the replace_predicate slot>
--     What happens when a row for this release day and drought class already exists. Without an ON
--     CONFLICT clause the write would fail on the unique constraint; DO UPDATE instead re-writes the
--     existing row. The trailing WHERE decides whether that re-write actually happens: with the slot
--     filled by the literal false the condition is never satisfied, the existing row is left exactly
--     as it is, and no row is returned -- so a re-run is a genuine no-op. THIS PREDICATE IS THE MOST
--     DANGEROUS THING IN THE FILE TO "simplify" away: without it the weekly cron would rewrite roughly
--     19 MB of geometry every hour for the rest of the week, silently.
--
--   EXCLUDED.geom / EXCLUDED.source_url
--     Inside ON CONFLICT, `EXCLUDED` is the pseudo-table holding the row that was PROPOSED -- the one
--     the conflict rejected. So these read "take the new value", as against the bare column names on
--     the left, which refer to the row already stored.
--
--   RETURNING id
--     RETURNING makes a write also behave like a read, yielding one row for each row it actually
--     wrote or re-wrote. Nothing is done with the id itself; the rows are counted below. It is the
--     only honest way to learn whether the conflict rule declined.
--
--   SELECT ST_IsEmpty(repaired.geom) AS repaired_to_empty, (SELECT count(*) FROM stored) ...
--     The final answer. The first column re-asks the emptiness question so the Python side can raise a
--     typed refusal rather than silently writing nothing; the second is a scalar subquery -- a query
--     used in place of a single value -- counting the rows the write actually returned. Reading from
--     `repaired` here, rather than from the table, is what guarantees both columns describe the very
--     geometry this statement handled.
WITH repaired AS (
    SELECT ST_Multi(
               ST_CollectionExtract(
                   ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326)),
                   3
               )
           ) AS geom
),
stored AS (
    INSERT INTO geo.drought_areas (valid_date, dm_category, geom, source_url)
    SELECT :valid_date, :dm_category, repaired.geom, :source_url
    FROM repaired
    WHERE NOT ST_IsEmpty(repaired.geom)
    ON CONFLICT (valid_date, dm_category) DO UPDATE
        SET geom = EXCLUDED.geom,
            source_url = EXCLUDED.source_url,
            ingested_at = now()
        WHERE {replace_predicate}
    RETURNING id
)
SELECT ST_IsEmpty(repaired.geom) AS repaired_to_empty,
       (SELECT count(*) FROM stored) AS rows_stored
FROM repaired
