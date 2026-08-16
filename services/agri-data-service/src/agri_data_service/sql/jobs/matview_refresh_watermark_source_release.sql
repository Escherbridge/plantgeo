-- matview_refresh_watermark_source_release
-- Purpose: the newest archive-release retrieval time, the change watermark for every view built
--          over agri.signal_observation (geo.mv_signal_observation_day, geo.mv_signal_cell_daily).
-- Loaded by: agri_data_service.jobs.matview_refresh
-- Params: none.
--
-- WHY THE RELEASE TABLE AND NOT THE OBSERVATION TABLE. agri.signal_observation is 46,068,872 rows
-- / 26 GB and has no index that makes a max() over a write-time column cheap; agri.source_release
-- is small, and every observation this service stores arrives through exactly one of its rows. A
-- lane that lands data always writes a release first, so the release clock cannot lag the data.
--
-- Returns exactly one row.
SELECT max(retrieved_at) AS watermark
FROM agri.source_release
