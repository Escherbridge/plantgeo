-- Purpose: set every version of one exact executor definition to the requested enable state.
-- Loaded by: agri_data_service.execution.job_lane_control
-- Params: name (text), enabled (boolean).
-- The name filter matches the admin toggle's all-version behavior, never a sibling definition.
-- IS DISTINCT FROM skips unchanged rows so repeat controls preserve their update timestamps.
-- RETURNING identifies changed rows for comparison with the preceding locked inventory.
-- Runs, work items, attempts and leases are not updated by this statement.
UPDATE agri.job_definition
SET enabled = CAST(:enabled AS boolean), updated_at = now()
WHERE name = :name AND enabled IS DISTINCT FROM CAST(:enabled AS boolean)
RETURNING id
