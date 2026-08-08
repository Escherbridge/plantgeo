-- Purpose: seal one forecast iteration -- compute its receipt checksum over the values now stored,
--          stamp the moment it was completed, and mark it finalized.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: iteration_id (uuid) -- the iteration to seal.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: nothing. It updates exactly one row.
--
-- Why sealing is a separate, final step: an iteration is written in three parts -- the header, then
-- the values, then this. Only the header and values together are the evidence, so the receipt cannot
-- be computed until the values exist, and the row must not claim to be finalized until the receipt
-- is on it. Any run that dies partway leaves an unsealed row that no reader will accept, which is
-- exactly the behaviour wanted: an incomplete forecast is visibly incomplete rather than quietly
-- short a few horizon steps.
--
-- How this query works, clause by clause:
--
--   UPDATE agri.forecast_iteration SET ... WHERE id = iteration_id
--     Updates the single row named by its primary key. All three columns are set in one statement, so
--     there is no instant at which the row is finalized without a receipt or stamped without a status.
--
--   receipt_checksum = agri.forecast_iteration_receipt_checksum(iteration_id)
--     A shipped database function that reads the iteration and every value row now attached to it and
--     folds them into one fingerprint. It is called here rather than computed in Python for two
--     reasons: it must see the rows as actually stored, and it must be computed identically by every
--     producer. Because it runs after the values are inserted, the receipt covers the finished
--     iteration and any later tampering with a value would no longer agree with it.
--
--   recorded_at = clock_timestamp()
--     clock_timestamp() is the real wall-clock reading at the instant it is evaluated. The more
--     common now() is deliberately not used: now() returns the time the surrounding transaction
--     STARTED and stays frozen for its whole duration, so every iteration sealed in one batch would
--     claim the same completion time. This column is meant to record when the work actually finished.
--
--   status = 'finalized'
--     The flag every reader checks before treating an iteration as evidence. Set last, in the same
--     statement as the receipt, so the two can never disagree.
UPDATE agri.forecast_iteration
SET receipt_checksum = agri.forecast_iteration_receipt_checksum(:iteration_id),
    recorded_at = clock_timestamp(),
    status = 'finalized'
WHERE id = :iteration_id
