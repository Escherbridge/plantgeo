-- historical_observed_days
-- Purpose: one row per (historical stream, UTC day) across all three history tables, with how many
--          rows that day holds. The three tables are separate physical tables but one logical family,
--          so they are reported through a single statement and a single day axis.
-- Loaded by: agri_data_service.ingest.validation
-- Params: row_limit (int) -- a hard cap on returned rows; the Python side refuses a result that
--         reaches the cap rather than reasoning about a truncated day set.
--
-- The first line above is a dispatch marker the unit tests match statements on. It stays first and
-- stays spelled as it is -- see "Marker protocol" in sql/AGENTS.md.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too and would mint a bind
-- parameter nobody supplies.
--
-- What this returns: one row per stream per day, ordered by stream then day, each carrying a literal
-- stream name, the day, and the row count for it.
--
-- WHY 'UTC' IS PINNED EXPLICITLY: `date_bucket` is a `timestamptz`, an instant rather than a wall
-- clock. Casting one straight to `date` renders it in whatever time zone the SESSION is set to, so the
-- same stored instant would be reported as two different days by a laptop run and a Railway run for
-- several hours either side of midnight. Converting to UTC first pins the answer.
--
-- How this query works, clause by clause:
--
--   SELECT 'historical_vegetation' AS stream
--     A literal value, not a column. There is no table here naming the stream -- the stream IS the
--     table -- so each branch labels its own rows so the three can be told apart once merged.
--
--   (historical.date_bucket AT TIME ZONE 'UTC')::date
--     `AT TIME ZONE 'UTC'` re-expresses the stored instant as wall-clock time in UTC; `::date` is
--     PostgreSQL's short spelling of CAST(value AS date) and discards the time of day, keeping the
--     calendar day. The parentheses matter: without them the cast would bind tighter than the time
--     zone conversion.
--
--   WHERE historical.date_bucket IS NOT NULL
--     Only dated rows contribute a day. Undated rows are counted as a validity fault by
--     historical_validity_counts.sql; they must not collapse into a single NULL "day" here. `IS NOT
--     NULL` rather than `<> NULL` because comparing anything to NULL in SQL yields NULL -- neither
--     true nor false -- so `IS` is the only test that works.
--
--   GROUP BY (historical.date_bucket AT TIME ZONE 'UTC')::date
--     GROUP BY collapses many rows into one row per distinct value of the listed expression -- here,
--     one row per day. Once rows are collapsed there is no single "the row" left to select, so every
--     other selected column must either be an aggregate (`count(*)`) or a constant (the stream label).
--     The whole expression is repeated rather than its output name reused, because standard SQL
--     evaluates GROUP BY before the SELECT list has produced its names.
--
--   count(*)
--     The number of rows in the group. `*` here does not mean "all columns"; it means "count rows,
--     including rows whose columns are NULL".
--
--   UNION ALL
--     Stacks the three result sets one after another into a single result. `ALL` means "keep every
--     row"; a bare UNION would additionally de-duplicate the combined rows, which would cost a sort
--     and could silently merge two streams that happened to report the same day and the same count.
--     The branches must agree on column count and types, which they do by construction.
--
--   ORDER BY 1, 2
--     Ordering by output POSITION rather than by name: column 1 is `stream`, column 2 is
--     `observed_day`. Positional ordering is used because an ORDER BY attached to a UNION applies to
--     the combined result, where the individual branches' table aliases are no longer in scope. The
--     order also makes the LIMIT below deterministic -- without it a capped result would be an
--     arbitrary sample rather than the earliest days.
--
--   LIMIT row_limit
--     The cap, applied to the combined result. It exists so an unexpectedly long history cannot pull
--     an unbounded result into memory; the Python side treats hitting it as an error, not an answer.
  SELECT 'historical_vegetation' AS stream,
         (historical.date_bucket AT TIME ZONE 'UTC')::date AS observed_day,
         count(*) AS observation_count
    FROM geo.historical_vegetation AS historical
   WHERE historical.date_bucket IS NOT NULL
   GROUP BY (historical.date_bucket AT TIME ZONE 'UTC')::date
UNION ALL
  SELECT 'historical_fire_data' AS stream,
         (historical.date_bucket AT TIME ZONE 'UTC')::date AS observed_day,
         count(*) AS observation_count
    FROM geo.historical_fire_data AS historical
   WHERE historical.date_bucket IS NOT NULL
   GROUP BY (historical.date_bucket AT TIME ZONE 'UTC')::date
UNION ALL
  SELECT 'historical_water_drought' AS stream,
         (historical.date_bucket AT TIME ZONE 'UTC')::date AS observed_day,
         count(*) AS observation_count
    FROM geo.historical_water_drought AS historical
   WHERE historical.date_bucket IS NOT NULL
   GROUP BY (historical.date_bucket AT TIME ZONE 'UTC')::date
   ORDER BY 1, 2
   LIMIT :row_limit
