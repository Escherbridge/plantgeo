-- matview_refresh_watermark_drought_areas
-- Purpose: the newest USDM valid_date and the release row count, the change watermark for the
--          drought views whose contents depend only on what has been published
--          (geo.mv_drought_release_index).
-- Loaded by: agri_data_service.jobs.matview_refresh
-- Params: none.
--
-- TWO COMPONENTS, NOT ONE. USDM revises a release in place often enough that max(valid_date)
-- alone would miss a re-ingest of the same week; the row count moves when a revision adds or
-- drops a category polygon. Projects two scalars and nothing else: geo.drought_areas is 640 kB of
-- heap hiding 495 MB of TOAST across 1,040 rows, and any unbounded projection pulls the lot.
--
-- Returns exactly one row.
SELECT max(valid_date) AS watermark,
       count(*) AS row_count
FROM geo.drought_areas
