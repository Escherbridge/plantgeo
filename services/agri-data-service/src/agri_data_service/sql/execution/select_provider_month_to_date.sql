-- Purpose: one pool's month-to-date source-usage rollup (charged, suspect, the basis split, the
--          metering epoch) -- section 1 of `agri-service ops jobs-usage-report`, and the ONLY
--          statement `execution/provider_budget.py` (G1, f1-executor) will read to decide whether
--          a lane may be admitted against the paid Open-Meteo cap (spec Sec 4.9.2, WQ-4; design
--          Sec 2.2-2.3; plan 0W.4).
-- Loaded by: agri_data_service.execution.usage_report (month_to_date -- the only loader; a second
--            call site is a spec violation, see sql/AGENTS.md "LOADED rule")
-- Params: pool (text) -- the one pool this call reports on; the Python loader calls this statement
--         once per pool when an operator asks for every pool.
--         now (timestamptz) -- "now" for both the UTC month boundary and the epoch comparison.
--         logical_caps (text holding JSON) -- a JSON object mapping lane_id -> its logical cap,
--         i.e. `foundation/observability/vocabulary.py::LANE_LOGICAL_CAPS` (today `{"soil": 1602}`).
--         weighted_pools (text[]) -- mirrors `foundation/observability/usage.py::_WEIGHTED_POOLS`
--         by hand (that name is private to its own module): today `{open-meteo-paid, open-meteo-free}`.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- Escaping a literal colon: `execution/gap_repair_contract.py::REPAIR_LANE_SUFFIX` is a colon
-- followed by "gap-repair", which this statement's `regexp_replace` call needs as a pattern
-- argument. Written with a bare leading colon it would ALSO look like a bind parameter to
-- SQLAlchemy's scan, so it is written with a leading backslash below -- a backslash-escaped colon
-- reads as literal text, never a bind candidate, and SQLAlchemy strips the backslash when it
-- compiles the statement (see `select_provider_usage.sql`'s header for the same note in full,
-- since this statement shares the same lane-id derivation).
--
-- Pool attribution is the one genuinely hard part of this statement, because only ONE of the four
-- charging bases (`metered`) ever has real per-host data to attribute from -- `reported`, `suspect`
-- and `lost` attempts by definition never wrote a usage line, so `usage.hosts` is empty for every
-- one of them. The rule this statement applies, spelled out once here rather than three times below:
--
--   * An attempt that DOES carry host data is attributed to `pool` when at least one of the hosts
--     it metered resolved to that pool (`touched_pool` below) -- exact, because it is read straight
--     off what actually happened.
--   * An attempt that carries NO host data (reported/suspect/lost) is attributed to `pool` when its
--     OWN lane is a weighted lane (a key of `logical_caps`) AND `pool` itself is one of
--     `weighted_pools`. This is a documented approximation, not an exact fact: today the only
--     weighted lane is `soil`, which can land on EITHER Open-Meteo pool depending on whether
--     `OPEN_METEO_API_KEY` was set when it ran, and a hostless attempt carries no record of which.
--     A hostless soil attempt therefore counts fully against WHICHEVER pool this statement is run
--     for, rather than being split -- conservative for `open-meteo-paid` (the one pool WQ-4 actually
--     enforces a stop on), and revisit this rule if a second weighted lane with one fixed pool lands.
--
-- How this statement works, clause by clause:
--
--   WITH epoch AS (...)
--     A CTE ("common table expression") -- a named subquery written up front and referenced below
--     like a table. This one is the METERING EPOCH: `min(started_at)` over every attempt o5a's fold
--     ever stamped (`metrics ? 'spawned'`), computed fresh on every read so history is never
--     re-priced by a later code change -- the report literally says "metering since <epoch>".
--
--   scoped AS (...)
--     Every non-in-flight attempt from the requested UTC month, on or after the epoch. `date_trunc
--     ('month', now, 'UTC')` is Postgres's month-start function with an explicit third argument
--     (PG14+) that fixes the boundary to UTC regardless of the session's `TimeZone` GUC -- the
--     two-argument form truncates in the SESSION zone, which is wrong for a report whose window
--     labels and epoch are all UTC. Passing `now` explicitly (rather than calling `now()` twice) is
--     what lets a test pin a fixed month without depending on the wall clock. The upper bound
--     `attempt.started_at < month_start + interval '1 month'` matters just as much as the lower one:
--     without it, calling this with a PAST `now` (the closed-month receipt, WQ-6) sums every LATER
--     month too, not just the one asked for. `attempt.status NOT IN ('running', 'deferred')` is the
--     EXCLUDED row of the spec's charging table: an attempt still in flight has not finished
--     spending anything yet, and charging it now would double-count once it closes.
--     `epoch.epoch_at IS NOT NULL` (not `IS NULL OR ...`) is deliberate: with no metering epoch yet
--     established, nothing has ever been "priced since the epoch" -- an unconditional `IS NULL OR`
--     would instead price every historical attempt the very first time this statement ran.
--
--   touched_pool AS EXISTS (SELECT 1 FROM jsonb_each(...) WHERE entry ->> 'pool' = pool)
--     jsonb_each expands the `usage.hosts` object into one row per host; EXISTS asks only "is there
--     at least one", never materializing the rows themselves. This is the host-based half of the
--     attribution rule above. jsonb_each RAISES on anything that is not a JSON object, and
--     `usage.hosts` is JSON `null` -- not SQL NULL, so a bare COALESCE never catches it -- for a
--     `not_spawned`, `reported` or `suspect` attempt (`job_executor_service.py::_not_spawned_usage`
--     writes `"hosts": null` literally, and the spawned path falls back to it too). `jsonb_typeof`
--     substitutes an empty object for anything that is not already a JSON object, the same guard
--     `select_provider_usage.sql`'s own LATERAL join uses, so this EXISTS runs against `{}` (no
--     rows, `touched_pool = false`) instead of raising.
--
--   attributed AS (...)
--     Applies the attribution rule: CASE WHEN this attempt actually carries host data THEN use
--     `touched_pool` ELSE fall back to the lane-based rule. "Actually carries host data" is the
--     same `jsonb_typeof(...) = 'object'` test (never bare `IS NOT NULL`, for the reason above) plus
--     `<> '{}'::jsonb` so an attempt that touched zero hosts still falls to the lane-based branch.
--     `jsonb_object_keys(logical_caps)` turns the bound JSON object's top-level keys back into rows,
--     so "is this lane weighted" is a plain set-membership test against the SAME map the `lost` CTE
--     reads its cap value from -- one bind, two uses, never two ways of spelling "the weighted-lane
--     set".
--
--   metered AS (...)
--     The four `count(*) FILTER (WHERE ...)` calls are the basis split (spec Sec 4.9.2's table):
--     each restricts one aggregate to attempts stamped with that `charged_basis` AND attributed to
--     THIS pool (`attributed.attributed_to_pool`) -- without that second condition every count would
--     silently include every OTHER pool's and every unweighted lane's attempts too, since `scoped`
--     is not itself pool-filtered (it cannot be: a `metered` attempt's own hosts are the only place
--     pool is known, and `attributed` is where that gets resolved). `charged`/`suspect` are summed
--     the same way, only over attempts this pool run attributes to it. A `lost` attempt has no
--     `charged_basis` key at all (its metrics are the server-default `{}`), so it matches none of
--     the four FILTERs here and is counted separately by the `lost` CTE below -- exactly the case
--     the spec's charging table lists on its own row.
--
--   lost AS (...)
--     `status = 'lost' AND NOT (metrics ? 'spawned')` is a lease-reaper close with no metrics at
--     all (`jobs/lease.py::close_attempt_lost.sql`), the spec's `lost` charging-basis row: 0 charged,
--     the lane's logical cap as suspect. `CAST(logical_caps AS jsonb) ->> lane_id` reads that cap
--     back out of the SAME bound map (already proven a key by `attributed_to_pool`'s ELSE branch,
--     so the numeric cast here is never applied to a NULL).
--
--   The final SELECT
--     Combines all three one-row CTEs with CROSS JOIN (each produces exactly one row, so this never
--     multiplies anything) and adds the `lost` counts into the basis split and the suspect total so
--     the caller sees one coherent picture: `suspect_basis_count` covers BOTH the `suspect` charging
--     basis and a `lost` attempt landing the same way, matching the spec's own charging table, which
--     lists them as two conditions of the same basis.
WITH epoch AS (
    SELECT min(attempt.started_at) AS epoch_at
    FROM agri.job_attempt AS attempt
    WHERE attempt.metrics ? 'spawned'
),
scoped AS (
    SELECT
        attempt.status,
        attempt.metrics,
        regexp_replace(definition.name, '\:gap-repair$', '') AS lane_id,
        EXISTS (
            SELECT 1
            FROM jsonb_each(
                CASE
                    WHEN jsonb_typeof(attempt.metrics -> 'usage' -> 'hosts') = 'object'
                    THEN attempt.metrics -> 'usage' -> 'hosts'
                    ELSE '{}'::jsonb
                END
            ) AS host_entry(host, entry)
            WHERE host_entry.entry ->> 'pool' = CAST(:pool AS text)
        ) AS touched_pool
    FROM agri.job_attempt AS attempt
    JOIN agri.job_work_item AS item ON item.id = attempt.job_work_item_id
    JOIN agri.job_run AS run ON run.id = item.job_run_id
    JOIN agri.job_definition AS definition ON definition.id = run.job_definition_id
    CROSS JOIN epoch
    WHERE attempt.status NOT IN ('running', 'deferred')
      AND attempt.started_at >= date_trunc('month', CAST(:now AS timestamptz), 'UTC')
      AND attempt.started_at < date_trunc('month', CAST(:now AS timestamptz), 'UTC') + interval '1 month'
      AND epoch.epoch_at IS NOT NULL
      AND attempt.started_at >= epoch.epoch_at
),
attributed AS (
    SELECT
        scoped.*,
        CASE
            WHEN jsonb_typeof(scoped.metrics -> 'usage' -> 'hosts') = 'object'
                 AND scoped.metrics -> 'usage' -> 'hosts' <> '{}'::jsonb
            THEN scoped.touched_pool
            ELSE
                scoped.lane_id IN (SELECT jsonb_object_keys(CAST(:logical_caps AS jsonb)))
                AND CAST(:pool AS text) = ANY(CAST(:weighted_pools AS text[]))
        END AS attributed_to_pool
    FROM scoped
),
metered AS (
    SELECT
        count(*) FILTER (
            WHERE attributed.metrics -> 'usage' ->> 'charged_basis' = 'metered' AND attributed.attributed_to_pool
        ) AS metered_count,
        count(*) FILTER (
            WHERE attributed.metrics -> 'usage' ->> 'charged_basis' = 'reported' AND attributed.attributed_to_pool
        ) AS reported_count,
        count(*) FILTER (
            WHERE attributed.metrics -> 'usage' ->> 'charged_basis' = 'suspect' AND attributed.attributed_to_pool
        ) AS suspect_basis_count,
        count(*) FILTER (
            WHERE attributed.metrics -> 'usage' ->> 'charged_basis' = 'not_spawned' AND attributed.attributed_to_pool
        ) AS not_spawned_count,
        COALESCE(
            sum(NULLIF(attributed.metrics -> 'usage' ->> 'charged', '')::numeric)
                FILTER (WHERE attributed.attributed_to_pool),
            0
        ) AS charged,
        COALESCE(
            sum(NULLIF(attributed.metrics -> 'usage' ->> 'suspect', '')::numeric)
                FILTER (WHERE attributed.attributed_to_pool),
            0
        ) AS suspect
    FROM attributed
),
lost AS (
    SELECT
        count(*) AS lost_count,
        COALESCE(
            sum(CAST(CAST(:logical_caps AS jsonb) ->> attributed.lane_id AS numeric)),
            0
        ) AS suspect_from_lost
    FROM attributed
    WHERE attributed.status = 'lost'
      AND NOT (attributed.metrics ? 'spawned')
      AND attributed.attributed_to_pool
)
SELECT
    CAST(:pool AS text) AS pool,
    epoch.epoch_at,
    metered.metered_count,
    metered.reported_count,
    (metered.suspect_basis_count + lost.lost_count) AS suspect_basis_count,
    metered.not_spawned_count,
    lost.lost_count,
    metered.charged,
    (metered.suspect + lost.suspect_from_lost) AS suspect
FROM epoch
CROSS JOIN metered
CROSS JOIN lost
