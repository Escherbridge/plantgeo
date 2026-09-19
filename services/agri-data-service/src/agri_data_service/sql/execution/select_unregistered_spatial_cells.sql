-- select_unregistered_spatial_cells
-- Purpose: name the day-partition cell keys the lattice dimension does not hold, BEFORE any
--          governed row is written for them.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: entity_keys (text[]) -- one day partition's raw cell keys, unprefixed;
--         grid_name (text) -- the lattice those keys belong to, always the NDVI 0.25-degree grid.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- Why this exists: the observation insert joins agri.spatial_cell, so a cell the dimension does not
-- hold would simply not land -- the turn would report a smaller promotion than it performed and no
-- error at all. Asking first turns that silent shortfall into one named refusal. The registration
-- verb cannot create the missing cell: a cell row needs a polygon and a resolution, and a Parquet
-- partition row carries a cell id and a value.
--
-- How this query works, clause by clause:
--
--   unnest(CAST(:entity_keys AS text[])) AS incoming(entity_key)
--     unnest turns one array parameter into a one-column table, so the whole batch is asked about in
--     a single round trip. The CAST states the array type explicitly, because the driver sends an
--     empty or all-text list without one and the planner must know what it is comparing.
--
--   LEFT JOIN agri.spatial_cell ... ON cell.cell_key = :grid_name || ':' || incoming.entity_key
--     A LEFT JOIN keeps every incoming key whether or not a cell matches; an INNER JOIN would drop
--     exactly the rows this query exists to find. agri.spatial_cell stores the GRID-QUALIFIED key
--     ("<grid>:<cell>"), so the prefix is rebuilt here rather than being sent twice from Python.
--
--   WHERE cell.id IS NULL
--     The only rows with no matching cell are the ones the LEFT JOIN filled with nulls. This is the
--     standard anti-join: keep what did not match.
--
--   ORDER BY incoming.entity_key
--     A stable order, so the error message a failed turn prints is the same on every retry.
SELECT incoming.entity_key
FROM unnest(CAST(:entity_keys AS text[])) AS incoming(entity_key)
LEFT JOIN agri.spatial_cell AS cell
    ON cell.cell_key = CAST(:grid_name AS text) || ':' || incoming.entity_key
WHERE cell.id IS NULL
ORDER BY incoming.entity_key
