-- Purpose: read the availability-gated observed target value per UTC day for one cell and
--          signal, which is the y-vector the evaluation-only wind ridge is fitted against.
-- Loaded by: agri_data_service.execution.covariate_wind_model
-- Params: cell_id (uuid), signal_name (text), as_of_time (timestamptz)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy's text() scans comments for colon-prefixed words too.
--
-- THIS FILE CARRIES THE TARGET-SIDE LEAKAGE GATE. It used to be a substring inside a Python
-- triple-quoted literal, where it was neither reviewable as SQL nor greppable alongside the
-- schema functions that implement the same rule. It is the "data_available_at" predicate
-- below. Changing it changes what the model is allowed to know, so change it deliberately.
--
-- How this query works, clause by clause:
--
--   (observed_at AT TIME ZONE 'UTC')::date
--     observed_at is an absolute instant. "AT TIME ZONE 'UTC'" reads that instant as a wall
--     clock in UTC, and ::date then keeps only the calendar day. Doing it in UTC rather than
--     in the server's local zone is what makes the day boundary the same everywhere, so the
--     same row never lands on two different days on two different machines.
--
--   WHERE cell_id = ... AND signal_name = ...
--     Narrows to the one cell and the one measured variable being forecast.
--
--   support_key = 'surface'
--     Spatial support is part of a value's identity, not a label: a surface reading and an
--     area aggregate of the same variable are different measurements. Pinning it stops the
--     fit from mixing two supports into one series.
--
--   quality_flag = 'accepted' AND is_observed AND normalized_value IS NOT NULL
--     Only accepted, genuinely observed, non-null readings. A rejected or imputed row is not
--     evidence of what actually happened, and admitting one would put a value the warehouse
--     already judged untrustworthy into the target column.
--
--   data_available_at <= as_of_time
--     THE LEAKAGE GATE. data_available_at is when this warehouse could first have known the
--     value. Admitting only rows at or before the run's as-of instant is what stops the model
--     from being trained on a fact that had not been published yet at the time it claims to
--     be standing. Note the honest limit: this is ONE global as-of instant for the whole
--     history, not a per-observation-date knowledge cutoff, so a revision published later than
--     an old day's original release is still admitted for that old day. See execution/AGENTS.md
--     "as_of_mode: global".
--
--   ORDER BY observed_at
--     A total order, so a run reads its history in the same sequence every time.
SELECT (signal.observed_at AT TIME ZONE 'UTC')::date AS observed_date,
       signal.normalized_value
FROM agri.signal_observation AS signal
WHERE signal.cell_id = CAST(:cell_id AS uuid)
  AND signal.signal_name = CAST(:signal_name AS varchar)
  AND signal.support_key = 'surface'
  AND signal.quality_flag = 'accepted'
  AND signal.is_observed
  AND signal.normalized_value IS NOT NULL
  AND signal.data_available_at <= CAST(:as_of_time AS timestamptz)
ORDER BY signal.observed_at
