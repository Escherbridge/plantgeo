-- Purpose: record one parent reference, copying the child's and parent's own temporal facts off the
--          value rows. The copies are composite-foreign-keyed back to those rows, so the edge's CHECK
--          constraints are enforcing predicates on real values rather than on a caller's assertion.
-- Loaded by: agri_data_service.execution.seasonal_lineage_persist
-- Params: child_value_id/parent_value_id (bigint), parent_role (text)
INSERT INTO agri.forecast_signal_lineage_edge (
    child_value_id,
    child_origin_cutoff_time,
    child_valid_time,
    child_availability_time,
    child_lineage_depth,
    parent_value_id,
    parent_origin_cutoff_time,
    parent_valid_time,
    parent_availability_time,
    parent_lineage_depth,
    parent_role
)
SELECT
    child.id,
    child.origin_cutoff_time,
    child.valid_time,
    child.availability_time,
    child.lineage_depth,
    parent.id,
    parent.origin_cutoff_time,
    parent.valid_time,
    parent.availability_time,
    parent.lineage_depth,
    :parent_role
FROM agri.forecast_derived_signal_value AS child
CROSS JOIN agri.forecast_derived_signal_value AS parent
WHERE child.id = :child_value_id
  AND parent.id = :parent_value_id
RETURNING id
