-- insert_coverage_absence
-- Purpose: record one governed absence -- a span of days one cell's signal was asked for and the
--          provider answered nothing to -- so the gap filler stops re-walking it forever.
-- Loaded by: agri_data_service.execution.coverage_fill
-- Params: source_release_id (uuid) -- the probe release that is this absence's evidence; cell_id
--         (uuid) -- the lattice cell that was probed; signal_name (text) and source_parameter
--         (text) -- the warehouse signal and the provider's own variable name for it; support_key
--         (text) -- the spatial support; window_start and window_end (timestamptz) -- the probed
--         span, inclusive, each at midnight UTC; expected_observation_count (integer) -- how many
--         days that span holds; details (text holding JSON) -- the probe evidence.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: one row holding the literal 1 when a row was inserted, and NO row at all when
-- an identical one already existed. The caller counts the rows it gets back and reports that as the
-- number written. Reporting the number offered instead would print a write that never happened,
-- which is the same class of claim as a lane reporting success having written nothing.
--
-- One row per span, not one per day. A four-year window with no data is a single honest gap
-- record; writing 1,462 of them would say the same thing 1,462 times and would also invite the
-- opposite mistake of writing is_observed = false observation rows, which is a claim about
-- measurement rather than about publication.
--
-- How this query works, clause by clause:
--
--   received_observation_count is the literal 0, and status is the literal 'no_data'
--     Both are fixed rather than parameters because this statement records exactly one kind of
--     fact. The table's ck_signal_coverage_status_matches_counts CHECK requires a no_data row to
--     have received exactly zero, so leaving either free would let a caller author a row the
--     database then rejects at the very end of a long transaction. A partial fill is a different
--     fact with a different status and does not come through here.
--
--   cast(details as jsonb)
--     details arrives as text -- the caller serialises a Python dictionary to a JSON string -- and
--     the column stores a parsed document. The cast performs the parse and pins the parameter's
--     type, which the database will not guess for a bare parameter.
--
--   on conflict (source_release_id, cell_id, signal_name, source_parameter, support_key,
--                window_start, window_end) do nothing
--     The table's own uniqueness constraint, restated so a re-run is idempotent. Re-applying the
--     same probe therefore writes nothing rather than aborting the whole transaction on a
--     duplicate key -- which matters because the caller writes one row per cell per signal in a
--     single transaction, and one collision must not discard the rest.
--
--   returning 1
--     RETURNING emits a row only for rows the statement actually wrote, so a skipped conflict
--     emits nothing. That is what lets the caller distinguish "wrote 36 absences" from "offered 36
--     absences and wrote none because the previous run already had". The value itself is unused;
--     only whether a row comes back carries information.
insert into agri.signal_coverage_audit (
    source_release_id, cell_id, signal_name, source_parameter, support_key,
    window_start, window_end, expected_observation_count, received_observation_count,
    status, details
)
values (
    :source_release_id, :cell_id, :signal_name, :source_parameter, :support_key,
    :window_start, :window_end, :expected_observation_count, 0,
    'no_data', cast(:details as jsonb)
)
on conflict (
    source_release_id, cell_id, signal_name, source_parameter, support_key, window_start, window_end
) do nothing
returning 1
