-- Purpose: read one exact scheduler definition version without changing its operator pause state.
-- Loaded by: agri_data_service.execution.job_executor_service
-- Params: name (text), version (text)
--
-- How this query works, clause by clause:
--
--   SELECT id, enabled
--     Returns the durable definition identity and its stored pause switch. The caller needs enabled
--     before deciding whether it may load or create any scheduled work.
--
--   FROM agri.job_definition
--     Reads the existing ledger definition directly; no reconciliation helper is used because such
--     a helper could overwrite an operator-disabled row with the code default.
--
--   WHERE name = ... AND version = ...
--     Restricts the read to the exact code-owned name and version supplied as bound parameters.
--
--   LIMIT 1
--     The table's name/version uniqueness means at most one row can match. The explicit limit records
--     that the caller expects a single optional row.
SELECT id, enabled
FROM agri.job_definition
WHERE name = :name AND version = :version
LIMIT 1
