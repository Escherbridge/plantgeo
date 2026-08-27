-- vegetation_publication_attempt
-- Purpose: record one attempt against an unchanged durable vegetation target.
-- Loaded by: agri_data_service.db.vegetation_publication
-- Params: observed_day (date), source_fingerprint (text), last_error (text nullable)
UPDATE agri.vegetation_publication_day
SET attempt_count = attempt_count + 1,
    last_attempted_at = clock_timestamp(),
    last_error = CAST(:last_error AS text)
WHERE observed_day = CAST(:observed_day AS date)
  AND source_fingerprint = CAST(:source_fingerprint AS text)
