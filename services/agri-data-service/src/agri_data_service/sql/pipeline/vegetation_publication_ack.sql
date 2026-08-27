-- vegetation_publication_ack
-- Purpose: compare-and-ack one physically verified vegetation generation.
-- Loaded by: agri_data_service.db.vegetation_publication
-- Params: observed_day (date), source_fingerprint (text)
UPDATE agri.vegetation_publication_day
SET published_fingerprint = CAST(:source_fingerprint AS text),
    published_at = clock_timestamp(),
    last_error = NULL
WHERE observed_day = CAST(:observed_day AS date)
  AND source_fingerprint = CAST(:source_fingerprint AS text)
RETURNING observed_day
