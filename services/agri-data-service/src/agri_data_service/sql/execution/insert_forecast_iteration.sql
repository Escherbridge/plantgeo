-- Purpose: open one forecast iteration -- the header row that records what was simulated, from which
--          governed inputs, under which parameters -- and report whether this run is the one that
--          created it.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: iteration_id (uuid) -- the identifier this run minted for the iteration; iteration_key
--         (text) -- its deterministic idempotency key; series_id (uuid) -- the series being forecast;
--         release_set_id (uuid) -- the governed set the inputs are pinned to; purpose (text) -- why
--         this iteration was run; availability_mode (text) -- whether the as-of moment is the natural
--         one or a retrospective replay; method (text) -- the named forecasting method; as_of_time,
--         cutoff_time, history_start (timestamptz) -- the moment stood at, the last day trained on,
--         and the first; horizon_days, simulation_count, simulation_seed (int) -- the simulation's
--         shape and its random seed; gap_policy (text) -- how missing days were handled; lower_bound,
--         upper_bound (float) -- the metric's physical limits; input_release_checksum (text) -- the
--         manifest checksum of the governed release set; input_license_snapshots (text holding JSON)
--         -- the licences behind the inputs; contract_snapshot (text holding JSON) and
--         contract_checksum (text) -- the series contract in force; history_checksum (text) -- the
--         exact governed history trained on; parameter_checksum (text) -- every simulation input
--         folded into one fingerprint; training_day_count, increment_count, expected_value_count
--         (int) -- how much history was used, how large the innovation pool was, and how many values
--         should exist when the iteration is complete.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: one row holding the new iteration's id if this run created it, and NO rows at
-- all if an iteration with this key already existed. That difference is the whole control flow: the
-- caller treats an empty result as "someone already did this" and goes off to
-- select_existing_iteration.sql to check whether the existing row is the same computation, rather
-- than blindly writing a second one.
--
-- Why the row is this wide: an iteration is meant to be reproducible from its own record. Every
-- input that could change the answer -- the governed release set, the contract, the history, the
-- seed, the bounds, the gap policy -- is either stored outright or folded into a checksum stored
-- here. A reader who has only this row can say what was run and check whether re-running it would
-- give the same thing.
--
-- How this query works, clause by clause:
--
--   INSERT INTO agri.forecast_iteration (...) VALUES (...)
--     A single-row insert naming its target columns explicitly, so the statement stays correct if
--     the table later gains a column instead of shifting every value one place along. The id is
--     supplied by the caller rather than generated here, because the caller needs to know it in
--     advance to write the iteration's values and seal it in the same transaction.
--
--   CAST(input_license_snapshots AS jsonb) and CAST(contract_snapshot AS jsonb)
--     Both parameters arrive as text -- they were read out of the database as text precisely so their
--     exact characters survived the round trip -- while both columns store parsed JSON documents. The
--     cast performs that parse and also pins the parameter's type, which the database will not guess
--     for a bare parameter.
--
--   ON CONFLICT (iteration_key) DO NOTHING
--     Idempotency, keyed on the deterministic iteration key. Without it, a concurrent or repeated run
--     would fail on the uniqueness constraint and abort the surrounding transaction, taking every
--     other series in the same batch down with it. DO NOTHING makes the duplicate a no-op, so the
--     batch keeps going and the caller resolves the collision itself. DO NOTHING and never DO UPDATE:
--     a finalized iteration is immutable evidence, and an update would rewrite a forecast that has
--     already been reported.
--
--   RETURNING id
--     Asks the INSERT to hand back a row for each row it actually wrote. Combined with DO NOTHING
--     this is what turns the statement into a test: a row means "created", no row means "already
--     there". No separate existence check is needed, and none could be raceless.
INSERT INTO agri.forecast_iteration (
    id, iteration_key, series_id, release_set_id, purpose, availability_mode, method,
    as_of_time, cutoff_time, history_start, horizon_days, simulation_count, simulation_seed,
    gap_policy, lower_bound, upper_bound, input_release_checksum, input_license_snapshots,
    contract_snapshot, contract_checksum, history_checksum, parameter_checksum,
    training_day_count, increment_count, expected_value_count
)
VALUES (
    :iteration_id, :iteration_key, :series_id, :release_set_id, :purpose, :availability_mode, :method,
    :as_of_time, :cutoff_time, :history_start, :horizon_days, :simulation_count, :simulation_seed,
    :gap_policy, :lower_bound, :upper_bound, :input_release_checksum,
    CAST(:input_license_snapshots AS jsonb), CAST(:contract_snapshot AS jsonb), :contract_checksum,
    :history_checksum, :parameter_checksum, :training_day_count, :increment_count, :expected_value_count
)
ON CONFLICT (iteration_key) DO NOTHING
RETURNING id
