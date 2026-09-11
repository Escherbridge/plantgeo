-- Purpose: lock the bounded version inventory before changing one executor lane's enable state.
-- Loaded by: agri_data_service.execution.job_lane_control
-- Params: name (text), limit (integer).
-- Exact name excludes siblings; id order gives concurrent controls consistent lock ordering.
-- LIMIT includes an overflow row, which causes refusal before mutation.
-- FOR UPDATE holds these definition rows until commit/rollback; it does not lock or stop workers.
SELECT id, name, version, enabled
FROM agri.job_definition
WHERE name = :name
ORDER BY id
LIMIT :limit
FOR UPDATE
