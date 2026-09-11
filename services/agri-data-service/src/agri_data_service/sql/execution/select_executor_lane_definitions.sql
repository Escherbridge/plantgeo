-- Purpose: inspect all versions of one exact executor definition without taking locks.
-- Loaded by: agri_data_service.execution.job_lane_control
-- Params: name (text), limit (integer).
-- The exact name excludes sibling lanes. Stable id ordering makes receipts comparable.
-- LIMIT includes one overflow row so the caller refuses an unexpectedly large version set.
SELECT id, name, version, enabled
FROM agri.job_definition
WHERE name = :name
ORDER BY id
LIMIT :limit
