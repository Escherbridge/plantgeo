-- Purpose: compute the single content fingerprint of the whole NDVI observation corpus up to a
--          cutoff day, together with the shape statistics that describe it.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: layer_name (text) -- the geo layer the NDVI features live on, always 'vegetation';
--         cutoff_day (date) -- the publisher-named day the corpus stops at, inclusive.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: exactly one row, always -- payload_checksum, cell_day_count, cell_count,
-- row_count, first_observed_day, last_observed_day. When no observation exists at or before the
-- cutoff, the row still comes back but every column is empty (NULL), and the caller turns that into
-- an explicit error rather than registering an empty release.
--
-- Why a checksum at all: the checksum is the identity of the observation corpus. Two runs that see
-- byte-identical data produce the same checksum and therefore register the same governed release;
-- one extra observation anywhere changes it, which forces a new release rather than silently
-- redefining the old one. Everything downstream is pinned to that value.
--
-- How this query works, clause by clause:
--
--   WITH corpus AS (...)
--     A CTE ("common table expression") -- a named subquery written up front and referenced below
--     like a table. This one turns raw feature rows into the canonical fact table being fingerprinted:
--     one row per cell per observed day, holding that day's mean NDVI and how many source rows went
--     into it.
--
--   feature.properties->>'cellKey'
--     The features table keeps its per-feature attributes in one JSON column; the arrow-with-two-
--     angle-brackets operator reads one named field out of it as ordinary text.
--
--   substring(feature.properties->>'observedAt', 1, 10)::date
--     The observation timestamp is an ISO 8601 string whose first ten characters are the calendar
--     date. Slicing those characters and casting them to a date keeps the publisher's own naming of
--     the day; converting the full timestamp instead would re-bucket observations by session time
--     zone and move them across day boundaries.
--
--   avg((feature.properties->>'ndvi')::double precision)
--     JSON fields come out as text, so the value is cast to a floating-point number before it can be
--     averaged. This is the daily cell mean -- the one number that represents a cell on a day.
--
--   feature.layer_id = (SELECT id FROM geo.layers WHERE name = :layer_name)
--     Resolves the layer NAME to its id via an uncorrelated subquery rather than joining geo.layers
--     onto the scan. geo.features is partitioned by layer_id: a join filtered on layer.name only
--     restricts the layers side, so the planner can never fold it into a partition-pruning constant
--     for the features side and must scan every partition. A scalar subquery on the (unpartitioned,
--     11-row) layers table runs once as an init-plan and hands the Append node one concrete layer_id
--     before it opens a single partition.
--
--   GROUP BY 1, 2
--     Collapses rows agreeing on the first two selected expressions, the cell key and the observed
--     day. The numbers are SELECT-list positions, shorthand for repeating those expressions. Every
--     other column in the CTE must therefore be an aggregate: there is no single ndvi for a group of
--     rows, only their average, and no single count, only how many there were.
--
--   string_agg(concat_ws('|', ...), '|' ORDER BY corpus.entity_key, corpus.observed_day)
--     The heart of the fingerprint. concat_ws joins one row's fields into a single string with '|'
--     between them ("with separator" is what the ws means; it also skips empty fields rather than
--     leaving stray separators). string_agg then concatenates every row's string into one long
--     document, again separated by '|'. The ORDER BY inside the aggregate is not decoration and not
--     optional: without it the database may combine rows in any order it likes, and the same data
--     would hash differently from run to run. Sorting by cell then day makes the document canonical,
--     which is what makes the checksum reproducible.
--
--   encode(digest(..., 'sha256'), 'hex')
--     digest runs the SHA-256 hash over that document and returns raw bytes; encode renders those
--     bytes as the familiar lowercase hexadecimal string. Hashing the assembled document rather than
--     hashing row by row is what makes the result depend on the whole corpus at once.
--
--   count(*)::integer AS cell_day_count / count(DISTINCT corpus.entity_key)::integer AS cell_count
--     Two different counts over the same CTE. The plain count is how many cell-day facts exist;
--     DISTINCT counts each cell key once no matter how many days it has. The casts narrow the
--     database's default wide integer to the ordinary integer the caller expects.
--
--   coalesce(sum(corpus.source_row_count), 0)::integer AS row_count
--     How many raw feature rows fed the corpus. COALESCE returns its first non-empty argument, so an
--     empty corpus reports 0 here instead of nothing at all -- summing no rows yields NULL, not zero,
--     and a missing count would be indistinguishable from a real one.
--
--   min(corpus.observed_day) / max(corpus.observed_day)
--     The inclusive span the corpus actually covers, which becomes the registered release's observed
--     window. These are the observed extremes, not the requested ones: the release describes what
--     was really there.
--
--   (the outer query has no GROUP BY)
--     A SELECT made entirely of aggregates over a whole table collapses to exactly one row, even
--     when the table is empty -- which is why the empty-corpus case arrives as a row full of NULLs
--     rather than as no row at all, and why the caller checks for a NULL checksum.
WITH corpus AS (
    SELECT
        feature.properties->>'cellKey' AS entity_key,
        substring(feature.properties->>'observedAt', 1, 10)::date AS observed_day,
        avg((feature.properties->>'ndvi')::double precision) AS metric_value,
        count(*) AS source_row_count
    FROM geo.features AS feature
    WHERE feature.layer_id = (SELECT id FROM geo.layers WHERE name = :layer_name)
      AND substring(feature.properties->>'observedAt', 1, 10)::date <= :cutoff_day
    GROUP BY 1, 2
)
SELECT
    encode(
        digest(
            string_agg(
                concat_ws('|', corpus.entity_key, corpus.observed_day::text, corpus.metric_value::text),
                '|'
                ORDER BY corpus.entity_key, corpus.observed_day
            ),
            'sha256'
        ),
        'hex'
    ) AS payload_checksum,
    count(*)::integer AS cell_day_count,
    count(DISTINCT corpus.entity_key)::integer AS cell_count,
    coalesce(sum(corpus.source_row_count), 0)::integer AS row_count,
    min(corpus.observed_day) AS first_observed_day,
    max(corpus.observed_day) AS last_observed_day
FROM corpus
