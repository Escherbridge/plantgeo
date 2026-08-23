-- Purpose: list every analysis cell the signal and vegetation day exports batch over, so the
--          gap-fill driver resolves that lane-specific argument from the warehouse instead of
--          carrying a hard-coded cell list that joins to nothing the day the grid changes.
-- Loaded by: agri_data_service.pipeline.parquet.lane_registry
-- Params: none
--
-- ~1,965 rows today (the 397-cell NASA POWER lattice plus the 1,568-cell Sentinel-2 one), which is
-- why this reads the whole dimension rather than paging it. RUNBOOK section 0.26.5 measured the
-- proven signal-plane export at exactly that cell count, and both consumers batch the result
-- themselves (CELL_BATCH_SIZE in pipeline/lanes/signal.py and its vegetation twin).
--
-- Deliberately UNFILTERED by grid name or resolution. The vegetation export narrows to its own
-- ndvi series inside its WHERE clause, so a superset of cells costs it at most a few extra empty
-- batches and can never change what it returns; a filter here would be a second, drifting
-- definition of "the cells this warehouse analyses".
SELECT cell.id AS cell_id
FROM agri.spatial_cell AS cell
ORDER BY cell.id
