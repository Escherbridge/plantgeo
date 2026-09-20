-- Purpose: derive each requested vegetation cell's footprint from its own features and register it
--          as a spatial cell on the NDVI analysis grid, reporting the cells actually created.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: layer_name (text) -- the geo layer the NDVI features live on, always 'vegetation';
--         cell_keys (text[]) -- one batch of unprefixed cell keys to register; grid_name (text) --
--         the analysis grid these cells belong to, which also becomes their key prefix.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: one row per spatial cell this statement actually inserted, holding its new id.
-- The caller counts those rows. A cell already registered by an earlier pass produces no row, so the
-- count is "newly created", not "in existence" -- which is exactly the measured effect the
-- registration summary reports.
--
-- Why the geometry is derived rather than supplied: the lattice is defined by where observations
-- actually landed. Deriving each cell's box from its own features means the registered footprint can
-- never disagree with the data inside it.
--
-- How this query works, clause by clause:
--
--   WITH selected AS (...)
--     A CTE ("common table expression") -- a named subquery written up front and referenced below
--     like a table. This one collapses all of a cell's features into a single row describing that
--     cell: its key, its footprint, and its resolution.
--
--   feature.properties->>'cellKey'
--     The features table keeps its per-feature attributes in one JSON column; the arrow-with-two-
--     angle-brackets operator reads one named field out of it as ordinary text.
--
--   ANY(CAST(cell_keys AS text[]))
--     "equals any element of this array" -- the set-membership form of an equality test, which is how
--     one statement filters to a whole batch of keys instead of one. The cast exists purely to pin
--     the parameter's type: a bare bind parameter carries no type of its own, and the database will
--     not guess which kind of array it was handed, so naming text[] settles it.
--
--   feature.layer_id = (SELECT id FROM geo.layers WHERE name = :layer_name)
--     Resolves the layer NAME to its id via an uncorrelated subquery rather than joining geo.layers
--     onto the scan. geo.features is partitioned by layer_id: a join filtered on layer.name only
--     restricts the layers side, so the planner can never fold it into a partition-pruning constant
--     for the features side and must scan every partition. A scalar subquery on the (unpartitioned,
--     11-row) layers table runs once as an init-plan and hands the Append node one concrete layer_id
--     before it opens a single partition.
--
--   ST_Force2D(ST_Envelope(ST_Collect(feature.geom)))
--     The footprint, read inside out. ST_Collect is an aggregate: it gathers all of the group's
--     geometries into one multi-part geometry. ST_Envelope then returns the smallest upright
--     rectangle containing that collection -- the cell's bounding box. ST_Force2D drops any height
--     coordinate, so every stored footprint is flat; a stray third dimension on one feature would
--     otherwise make that cell's geometry structurally different from its neighbours' and break
--     comparisons against them.
--
--   min((feature.properties->>'resolutionMetres')::integer)
--     JSON fields arrive as text, so the value is cast to an integer before it can be compared.
--     Taking the minimum is deliberately conservative: if features of differing resolution ever land
--     in one cell, the cell is described by its finest, which is the only claim the data actually
--     supports.
--
--   GROUP BY 1
--     Collapses the features into one row per cell key -- position 1 in the SELECT list is that key.
--     This is why the two expressions beside it must be aggregates: a group of features has no single
--     geometry and no single resolution, only a collected one and a smallest one.
--
--   INSERT INTO agri.spatial_cell (...) SELECT ... FROM selected
--     The rows to insert come from a query rather than from literal VALUES, so one statement
--     registers the whole batch. The insert reads the CTE it just computed.
--
--   grid_name || ':' || selected.entity_key
--     The double-pipe operator joins strings. Stored cell keys are grid-qualified, so the same cell
--     identifier under two different analysis grids stays two distinct rows and can never be
--     confused for one.
--
--   ST_Centroid(selected.geometry)
--     The footprint's centre point, stored alongside the box so that point-based lookups and map
--     labels do not have to recompute it on every read.
--
--   1.0  (the coverage_fraction column)
--     A literal. The footprint was derived from the cell's own features, so by construction the cell
--     is fully covered by the data that defines it; there is no partial-coverage case to record.
--
--   ON CONFLICT DO NOTHING
--     Idempotency. Written without naming columns or a constraint, meaning "if this row would violate
--     ANY uniqueness constraint on the table, skip it silently". A cell registered by an earlier pass
--     is therefore skipped instead of failing on the uniqueness constraint and aborting the
--     surrounding transaction -- and, because a skipped row returns nothing, it also drops out of the
--     count below.
--
--   RETURNING id
--     Asks the INSERT to hand back a row for each row it actually wrote. Skipped conflicts are not
--     written and so are not returned, which is what makes the caller's count mean "newly created".
WITH selected AS (
    SELECT
        feature.properties->>'cellKey' AS entity_key,
        ST_Force2D(ST_Envelope(ST_Collect(feature.geom))) AS geometry,
        min((feature.properties->>'resolutionMetres')::integer) AS resolution_m
    FROM geo.features AS feature
    WHERE feature.layer_id = (SELECT id FROM geo.layers WHERE name = :layer_name)
      AND feature.properties->>'cellKey' = ANY(CAST(:cell_keys AS text[]))
    GROUP BY 1
)
INSERT INTO agri.spatial_cell (
    cell_key, grid_name, resolution_m, geometry, centroid, coverage_fraction
)
SELECT
    :grid_name || ':' || selected.entity_key,
    :grid_name,
    selected.resolution_m,
    selected.geometry,
    ST_Centroid(selected.geometry),
    1.0
FROM selected
ON CONFLICT DO NOTHING
RETURNING id
