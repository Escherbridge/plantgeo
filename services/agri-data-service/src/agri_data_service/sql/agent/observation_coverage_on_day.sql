-- agent_observation_coverage_on_day
-- Purpose: for any one of the map's 24 catalogue surfaces, say whether the caller's day is
--          covered, how much landed on it, and how that compares with the surface's whole served
--          history -- from the same census the map's time slider is built from.
-- Loaded by: agri_data_service.agent.tools
-- Params: surface_name (text -- a slider capability catalogue name, verbatim),
--         day (date -- the day the map has selected)
--
-- Parameter names appear above WITHOUT a leading colon -- see "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- WHY ONE GENERIC STATEMENT AND NOT ONE PER SURFACE. Section 11 of docs/layer-lane-standard.md
-- obliges every layer to answer three questions for the agent: the value at the selected day,
-- the nearest observation each side of it, and the nearest observation in space. Only the signal
-- plane had all three. Writing eleven more bespoke coverage queries would repeat the mistake the
-- pre-aggregation design was created to undo -- many relations answering one question -- so this
-- statement is parameterised by surface name instead, and reads the one census every surface
-- already reports through.
--
-- THE SOURCE IS geo.v_observation_day_census, a plain view that unions the three per-plane
-- census matviews (feature-backed, signal-backed, polygon-backed) onto one column contract. It is
-- about 35,000 rows in total and indexed on (surface_name, observed_day) beneath, so both halves
-- below are index lookups rather than scans. Crucially it is the SAME relation the slider's
-- observation-window query reads, which is what makes it impossible for the agent to report a day
-- as empty while the slider offers it.
--
-- The census is a view over MATERIALIZED views, and a view reports itself populated even when the
-- matviews beneath it are not. The caller therefore probes the three matviews by name before
-- issuing this statement (materialized_plane_populated.sql) and refuses by name if any is
-- unbuilt, rather than letting a raise escape or an empty result read as an absence.
--
-- How this query works, clause by clause:
--
--   WITH selected AS (...)
--     A CTE ("common table expression") -- a named subquery defined up front and referenced below
--     like a table. This one is the direct hit: the census row for exactly this surface on
--     exactly this day, if there is one. It is a single indexed lookup on the census's own key.
--
--   surface_kind (which plane answered)
--     Carried through so the caller knows which plane answered -- 'feature' for a geo.features
--     layer, 'signal' for a cell-grid stream, 'polygon' for a released polygon set. The three
--     have genuinely different absence semantics and a reader that cannot tell them apart will
--     misread a zero.
--
--   metric_counts (per-metric candidate/unlinked)
--     A small JSON object of per-metric candidate and unlinked counts, precomputed in the census
--     from an explicit metric-key list. It is returned as-is rather than picked apart, so a
--     surface that publishes two metrics reports both without this statement knowing their names.
--
--   history AS (...)
--     The second CTE, the surface's whole served history as three numbers: its first and last
--     covered day and how many covered days lie between. It is what turns "your day has no row"
--     into something actionable -- a day before earliest_observed_day is outside the lane's
--     horizon, a day after latest_observed_day is past its live edge, and a day between the two is
--     a genuine hole. Those are three different answers and the caller gives three different ones.
--
--   count(*) AS observed_day_count
--     Counts census rows, and a census row exists only for a day that carried observations, so
--     this is the number of COVERED days -- not the number of days spanned. The difference between
--     it and the calendar span is the gap count.
--
--   FROM history LEFT JOIN selected ON TRUE
--     history always yields exactly one row (its aggregates have no GROUP BY), so driving from it
--     guarantees an answer even for a day the census has never heard of. Driving from `selected`
--     instead would return nothing at all for an uncovered day, and "no row" is precisely the
--     shape this tool exists to replace with a stated fact. ON TRUE means "no join condition",
--     because there is only one row on each side.
--
--   coalesce(selected.observation_count, 0) AS observation_count
--     Turns the null left by the missing direct hit into an explicit zero, paired with the
--     is_covered boolean below so a reader never has to infer coverage from a count.
WITH selected AS (
    SELECT
        census.surface_kind,
        census.observed_day,
        census.observation_count,
        census.unlinked_count,
        census.distinct_key_count,
        census.newest_observed_at,
        census.metric_counts
    FROM geo.v_observation_day_census AS census
    WHERE census.surface_name = :surface_name
      AND census.observed_day = :day
),
history AS (
    SELECT
        min(census.surface_kind) AS surface_kind,
        min(census.observed_day) AS earliest_observed_day,
        max(census.observed_day) AS latest_observed_day,
        count(*) AS observed_day_count,
        sum(census.observation_count) AS total_observation_count,
        max(census.newest_observed_at) AS newest_observed_at
    FROM geo.v_observation_day_census AS census
    WHERE census.surface_name = :surface_name
)
SELECT
    CAST(:surface_name AS text) AS surface_name,
    coalesce(selected.surface_kind, history.surface_kind) AS surface_kind,
    CAST(:day AS date) AS requested_day,
    selected.observed_day IS NOT NULL AS is_covered,
    coalesce(selected.observation_count, 0) AS observation_count,
    coalesce(selected.unlinked_count, 0) AS unlinked_count,
    coalesce(selected.distinct_key_count, 0) AS distinct_key_count,
    selected.newest_observed_at AS day_newest_observed_at,
    selected.metric_counts,
    history.earliest_observed_day,
    history.latest_observed_day,
    history.observed_day_count,
    history.total_observation_count,
    history.newest_observed_at AS surface_newest_observed_at
FROM history
LEFT JOIN selected ON TRUE
