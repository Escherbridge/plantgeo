-- feature_duplicate_identities
-- Purpose: prove that no published feature layer holds two rows claiming the same producer-local id.
--          One row per offending layer, carrying how many ids are duplicated and how many rows are
--          excess. An empty result is the expected, healthy answer.
-- Loaded by: agri_data_service.ingest.validation
-- Params: published_status (text) -- the one `geo.features.status` value the map serves.
--
-- The first line above is a dispatch marker the unit tests match statements on. It stays first and
-- stays spelled as it is -- see "Marker protocol" in sql/AGENTS.md.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too and would mint a bind
-- parameter nobody supplies.
--
-- What this returns: nothing at all, when the warehouse is healthy. Otherwise one row per layer with
-- `duplicate_identity_groups` (how many distinct ids are claimed more than once) and
-- `duplicate_identity` (how many rows beyond the first each of those ids contributes).
--
-- WHY THIS RUNS AT ALL, GIVEN THE UNIQUE INDEX: `features_layer_external_id_unique` already makes a
-- genuine duplicate impossible for any row that carries an id, so this should always return nothing.
-- It is run anyway because that index is PARTIAL -- it does not cover every row -- and because a check
-- that measures the invariant is what proves the index is still in place, rather than assuming it.
--
-- How this query works, clause by clause:
--
--   WITH duplicated AS (...)
--     A CTE ("common table expression") -- a named subquery written up front and then referenced below
--     as if it were a table. This one does the whole detection: it finds every (layer, producer id)
--     pair that occurs more than once. Naming it separately keeps the counting step below readable and
--     lets that step aggregate over the offenders rather than over the whole table a second time.
--
--   features.properties ->> 'id'
--     Pulls the `id` field out of the feature's JSON payload as text. The two-arrow form `->>` yields
--     text; the one-arrow form `->` would yield JSON. This field is the producer's own key for the
--     thing, which is what must be unique within a layer.
--
--   WHERE features.status = published_status AND features.properties ? 'id'
--     Only rows the map serves, and only rows that actually carry an id. The `?` operator asks "does
--     this JSON object have a top-level key with this name". Rows with no id are a different fault,
--     counted separately by feature_validity_counts.sql; they must not all collapse into one giant
--     "duplicate" group of NULLs here.
--
--   GROUP BY features.layer_id, features.properties ->> 'id'
--     GROUP BY collapses many rows into one row per distinct combination of the listed expressions --
--     here, one row per (layer, id) pair. Grouping by layer as well as by id is essential: two
--     different layers legitimately reusing the same producer id are not duplicates of each other.
--
--   HAVING count(*) > 1
--     HAVING filters GROUPS, the way WHERE filters ROWS. It runs after the grouping, so it can test an
--     aggregate; WHERE runs before the grouping and cannot. This keeps only the pairs that occur more
--     than once, i.e. the actual offenders.
--
--   JOIN geo.layers AS layers ON layers.id = duplicated.layer_id
--     The CTE only knows the layer's internal id, so it is joined back to `geo.layers` purely to
--     recover the readable layer name the report prints.
--
--   count(*) AS duplicate_identity_groups
--     Now counting ROWS OF THE CTE, each of which is one duplicated id. So this is "how many distinct
--     ids are claimed more than once in this layer".
--
--   sum(duplicated.occurrence_count - 1)::bigint AS duplicate_identity
--     A different question from the one above, and both are reported because they answer different
--     things. `sum` adds up the excess: an id claimed three times contributes 2. So this is "how many
--     rows would have to go away for the layer to be clean". The `::bigint` cast pins the result type
--     -- `sum` over integers widens to an arbitrary-precision numeric in PostgreSQL, and pinning it to
--     a 64-bit integer is what lets the Python side read it as a plain int instead of a Decimal.
--
--   ORDER BY layers.name
--     A stable order so two runs of the report list layers identically.
WITH duplicated AS (
    SELECT features.layer_id            AS layer_id,
           features.properties ->> 'id' AS producer_local_id,
           count(*)                     AS occurrence_count
      FROM geo.features AS features
     WHERE features.status = :published_status
       AND features.properties ? 'id'
     GROUP BY features.layer_id, features.properties ->> 'id'
    HAVING count(*) > 1
)
SELECT layers.name                                     AS stream,
       count(*)                                        AS duplicate_identity_groups,
       sum(duplicated.occurrence_count - 1)::bigint    AS duplicate_identity
  FROM duplicated
  JOIN geo.layers AS layers
    ON layers.id = duplicated.layer_id
 GROUP BY layers.name
 ORDER BY layers.name
