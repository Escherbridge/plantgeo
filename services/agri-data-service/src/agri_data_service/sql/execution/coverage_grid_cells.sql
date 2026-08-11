-- coverage_grid_cells
-- Purpose: list one lattice's cells with their identifiers and centroids, giving the census the
--          denominator a day's cell fraction is measured against and giving the gap filler the
--          coordinates it probes and the cell ids a governed absence is written against.
-- Loaded by: agri_data_service.execution.coverage_census
-- Params: grid_name (text) -- the agri.spatial_cell.grid_name whose cells are wanted.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: one row per cell, sorted by cell_key. The sort is load-bearing rather than
-- cosmetic: the gap filler spreads its upstream probe evenly across the sorted lattice, so an
-- unsorted result would probe a different, database-order-dependent sample on every run and make
-- one run's evidence unreproducible by the next.
--
-- The count of rows is the expected cell count for every day this lane is contracted to hold. It
-- is measured per request rather than frozen as a constant, because a lattice grows: a hard-coded
-- denominator is how a panel came to print "4 of 397 cells" directly above a live count in the
-- same card.
--
-- How this query works, clause by clause:
--
--   ST_Y(centroid) and ST_X(centroid)
--     PostGIS stores a point as one opaque geometry value; these pull the latitude and longitude
--     back out of it. Y is latitude and X is longitude -- the order is the one place this is easy
--     to invert, and inverting it would send every upstream probe to a mirrored coordinate that
--     still looks like a plausible place on earth.
--
--   where grid_name = grid_name parameter
--     One lattice at a time. agri.spatial_cell holds every grid this platform uses side by side,
--     so an unfiltered read would mix a 397-cell half-degree lattice with a 1,568-cell quarter-
--     degree one and produce a denominator belonging to neither.
--
--   order by cell_key
--     A total order, because cell_key is unique table-wide. Sorting by anything nullable or
--     non-unique would leave ties broken by the storage layer, which is exactly the instability
--     the probe sample cannot tolerate.
select
    cell.id as cell_id,
    cell.cell_key as cell_key,
    ST_Y(cell.centroid) as latitude,
    ST_X(cell.centroid) as longitude
from agri.spatial_cell as cell
where cell.grid_name = :grid_name
order by cell.cell_key
