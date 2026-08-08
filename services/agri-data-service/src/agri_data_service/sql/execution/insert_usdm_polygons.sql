-- Purpose: repair, validate and insert one bounded batch of U.S. Drought Monitor polygons for a
--          single weekly source release, and report how many of them survived validation.
-- Loaded by: agri_data_service.execution.historical_writer
-- Params: geometry_jsons (text[]) -- one GeoJSON geometry document per polygon in the batch;
--         geometry_checksums (text[]) -- the digest of each polygon's raw, pre-repair geometry;
--         severity_classes (text[]) -- each polygon's native drought class D0-D4, carried as text
--         and narrowed to smallint below; metadata_jsons (text[]) -- one compact JSON document per
--         polygon; source_release_id (uuid) -- the weekly release every inserted row belongs to;
--         issue_date (date) -- the Tuesday the Drought Monitor published this map;
--         data_available_at (timestamptz) -- when this service actually retrieved the package.
--
-- The four array parameters are positionally parallel: element N of each one describes the same
-- polygon. The caller builds them from one batch of at most 500 polygons, because a national D0
-- multipolygon's GeoJSON runs to megabytes, so this statement is bounded by polygon count rather
-- than by the bind-parameter ceiling that bounds every other batch in that module.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: exactly one row with one column, accepted_count -- the number of polygons in
-- this batch that passed every validity check. The caller compares that number against the batch
-- size it sent and raises if it is short, so a polygon the repair chain could not turn into a valid
-- WGS84 multipolygon is never silently dropped from the release. The rows themselves are written as
-- a side effect of the statement, not returned.
--
-- Why this is raw SQL at all. Everything else in historical_writer.py is built with SQLAlchemy's
-- Python query objects. This one statement cannot be, because SQLAlchemy has no way to emit the
-- MATERIALIZED keyword, and that keyword is the entire point of the rewrite: the geometry repair
-- chain is by far the most expensive work in the USDM lane, and MATERIALIZED is what guarantees it
-- runs exactly once per polygon. The statement replaced a per-polygon validation SELECT followed by
-- a per-polygon INSERT that embedded the identical repair expression a second time -- two database
-- round trips and two full repairs per polygon. It still fails closed, exactly as that pair did.
--
-- How this query works, clause by clause:
--
--   WITH candidate AS MATERIALIZED (...)
--     WITH introduces a CTE ("common table expression") -- a named subquery written up front and
--     then referenced further down as if it were a table. MATERIALIZED is an instruction to the
--     query planner about HOW to evaluate that subquery. Without it, PostgreSQL is free to "inline"
--     a CTE: rather than computing it once and storing the answer, it may paste the CTE's
--     expressions into every place that reads them, and then evaluate those expressions once per
--     reader. Four of the predicates below read this CTE's geom column, so an inlined candidate
--     would run the whole geometry repair chain -- the nested expression that produces that column --
--     four extra times for every single polygon. MATERIALIZED forces the planner to
--     compute the CTE once into a temporary result set and have every reader consult that result.
--     For cheap CTEs the choice does not matter; here the repaired geometry is the most expensive
--     value in the statement, so computing it once instead of once-per-reference is the whole
--     optimisation.
--
--   FROM unnest(CAST(...), CAST(...), CAST(...), CAST(...)) AS pending(...)
--     unnest turns an array parameter into rows -- an array of 500 GeoJSON strings becomes 500
--     one-column rows. Given several arrays at once it zips them side by side, so row N holds
--     element N of every array; that is what makes the four parallel arrays line up into one row
--     per polygon. The AS pending(...) part names the resulting table and its columns so the SELECT
--     above can refer to them. This is how one statement inserts a whole batch: the batch travels
--     as four parameters rather than as 500 separate statements.
--
--   CAST(geometry_jsons AS text[]) and the other three casts on the parameters
--     A cast that exists purely to pin a parameter's type. A bare bind parameter has no type of its
--     own, and unnest accepts arrays of many different element types, so without the cast the
--     database cannot decide what kind of array it was handed and refuses the statement. Naming
--     text[] settles it. (Python-side, the caller also attaches an explicit array type to these four
--     binds; the cast and the Python type declaration are belt and braces on the same question.)
--
--   CAST(pending.severity_class AS smallint) / CAST(pending.metadata_json AS jsonb)
--     These two casts convert, rather than merely pin. Every element arrived as text so that all
--     four arrays could share one array type; here each column is narrowed to the type its
--     destination column actually stores -- a small integer for the drought class, a parsed JSON
--     document for the metadata. A malformed value fails loudly at this point instead of being
--     stored as unreadable text.
--
--   the geometry repair chain -- the five nested calls producing the geom column of candidate
--     Described here rather than quoted; see "a note on this walkthrough's wording" at the end. Read
--     the nesting inside out. The innermost call parses the polygon's GeoJSON text into a geometry
--     value. The next stamps that value with coordinate system 4326, plain WGS84 longitude and
--     latitude, which is what the source publishes and what this warehouse stores. The third repairs
--     it: real-world drought outlines are full of self-intersections and duplicated vertices, and a
--     geometry the database considers invalid cannot be reliably tested or intersected. Repair can
--     return a mixed bag of leftover points and lines alongside the areas, so the fourth call keeps
--     only the polygonal parts and discards the rest -- that is what its literal argument 3 selects.
--     The outermost call guarantees the result is a MULTIPOLYGON even when only one polygon survived,
--     so every stored row has the same geometry type. The raw pre-repair checksum is carried through
--     untouched, so the original bytes remain identifiable after the transform.
--
--   accepted AS MATERIALIZED (SELECT * FROM candidate WHERE ...)
--     The second CTE keeps only the polygons the repair chain actually rescued: still valid, not
--     empty, still in coordinate system 4326, and genuinely a multipolygon. Anything that fails is
--     dropped here -- and because the caller compares the surviving count against the batch size,
--     dropping any is what turns into an error rather than a silent loss. This CTE is MATERIALIZED
--     for the same reason as the first: it is read twice below (once by the INSERT, once by the
--     final count), and inlining it would re-run the filters, and through them the repair chain,
--     for each read.
--
--   inserted AS (INSERT INTO agri.drought_polygon_snapshot ... RETURNING 1)
--     A data-modifying CTE: an INSERT written inside WITH. Two things about it are load-bearing.
--     First, PostgreSQL runs a data-modifying CTE to completion whether or not anything reads it --
--     and nothing does read this one -- so the rows are written even though the final SELECT never
--     mentions inserted. Second, writing the INSERT here rather than as the outer statement is what
--     lets the outer statement be a SELECT, which is how one round trip both writes the rows and
--     reports the count. RETURNING asks an INSERT to hand back a row per row it actually wrote; the
--     constant 1 is used because the identities are not needed, only the fact of the write.
--
--   SELECT CAST(source_release_id AS uuid), CAST(issue_date AS date), ... FROM accepted
--     The rows to insert are selected out of accepted rather than listed as literal VALUES, so the
--     per-polygon columns come from the batch while the three release-level columns are the same
--     scalar parameter repeated on every row. Those three carry casts for the same
--     pin-the-parameter-type reason as the arrays above: the database will not guess a bare
--     parameter's type. The literal 'none' fills impact_type because the Drought Monitor's
--     medium-resolution weekly product does not distinguish impact types, and inventing one would be
--     fabricated provenance.
--
--   ON CONFLICT ON CONSTRAINT uq_drought_polygon_release_identity DO NOTHING
--     Idempotency. That named constraint declares which combination of columns makes a polygon row
--     unique within a release. If a row being inserted would duplicate one already stored, DO
--     NOTHING skips just that row and lets the rest of the statement proceed, instead of aborting
--     the whole transaction with a uniqueness error. Naming the constraint explicitly, rather than
--     listing columns, means the statement always targets the constraint the schema actually
--     declares. This is what makes re-running a weekly release safe.
--
--   SELECT (SELECT count(*) FROM accepted) AS accepted_count
--     A scalar subquery: a query in parentheses that yields exactly one value, used here as the only
--     column of the only output row. Note carefully that the count comes from accepted, the
--     polygons that passed validation -- NOT from inserted, the polygons actually written. On a
--     replay every row conflicts and inserted is empty, but accepted is still the full batch, so the
--     caller's equality check passes and a repeated run is correctly treated as success rather than
--     as data loss. Counting inserted instead would make every idempotent re-run look like a
--     failure.
--
--   a note on this walkthrough's wording
--     The whole file, comments included, is the statement text -- rendering the loaded statement as a
--     string returns these comment lines too. The repair expression above is therefore described
--     rather than quoted, so that each of its function names occurs exactly once in the statement:
--     in the SQL itself. That matters because "evaluated exactly once per polygon" is the
--     load-bearing property of this statement, and a check that proves it by counting how often a
--     function name appears would be broken by a walkthrough that pasted the expression a second
--     time. Do not reintroduce a verbatim copy of it above.
WITH candidate AS MATERIALIZED (
    SELECT pending.geometry_checksum,
           CAST(pending.severity_class AS smallint) AS severity_class,
           CAST(pending.metadata_json AS jsonb) AS metadata_json,
           ST_Multi(
               ST_CollectionExtract(
                   ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(pending.geometry_json), 4326)),
                   3
               )
           ) AS geom
    FROM unnest(
             CAST(:geometry_jsons AS text[]),
             CAST(:geometry_checksums AS text[]),
             CAST(:severity_classes AS text[]),
             CAST(:metadata_jsons AS text[])
         ) AS pending(geometry_json, geometry_checksum, severity_class, metadata_json)
),
accepted AS MATERIALIZED (
    SELECT * FROM candidate
    WHERE ST_IsValid(candidate.geom)
      AND NOT ST_IsEmpty(candidate.geom)
      AND ST_SRID(candidate.geom) = 4326
      AND GeometryType(candidate.geom) = 'MULTIPOLYGON'
),
inserted AS (
    INSERT INTO agri.drought_polygon_snapshot (
        source_release_id, issue_date, severity_class, impact_type,
        geometry, geometry_checksum, data_available_at, metadata_json
    )
    SELECT CAST(:source_release_id AS uuid),
           CAST(:issue_date AS date),
           accepted.severity_class,
           'none',
           accepted.geom,
           accepted.geometry_checksum,
           CAST(:data_available_at AS timestamptz),
           accepted.metadata_json
    FROM accepted
    ON CONFLICT ON CONSTRAINT uq_drought_polygon_release_identity DO NOTHING
    RETURNING 1
)
SELECT (SELECT count(*) FROM accepted) AS accepted_count
