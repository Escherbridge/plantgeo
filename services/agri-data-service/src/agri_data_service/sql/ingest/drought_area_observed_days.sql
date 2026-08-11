-- drought_area_observed_days
-- Purpose: one row per US Drought Monitor release day held in `geo.drought_areas`, with how many
--          drought-class polygons that day carries. The drought layer's equivalent of
--          observed_days.sql, which cannot serve it because drought areas are not features.
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
-- What this returns: one row per release day, in day order, carrying the day and the number of
-- drought-class polygons stored for it.
--
-- WHY THE TWO GUARDS BEFORE to_date: `geo.drought_areas.valid_date` is a VARCHAR, not a date column,
-- so the two checks `geo.feature_observation_day` applies internally have to be applied here by hand.
-- The regular expression proves the string has the right SHAPE, and `pg_input_is_valid` proves the day
-- it names actually EXISTS -- both are needed, because to_date('2026-02-31', ...) raises an error
-- rather than answering NULL, and one bad string would take the whole report down.
--
-- How this query works, clause by clause:
--
--   to_date(drought_areas.valid_date, 'YYYY-MM-DD')
--     Parses the stored text into a real calendar date using the given pattern, where YYYY is a
--     four-digit year, MM a two-digit month and DD a two-digit day.
--
--   WHERE drought_areas.valid_date ~ '^\d{4}-\d{2}-\d{2}$'
--     The `~` operator is "matches this regular expression". `^` anchors at the start of the string
--     and `$` at the end, so the whole value must match, not just part of it. `\d` means a digit and
--     the braced number is how many of them. Net effect: exactly four digits, a hyphen, two digits, a
--     hyphen, two digits, and nothing else.
--
--   AND pg_input_is_valid(drought_areas.valid_date, 'date')
--     A PostgreSQL function that answers true or false for "could this text be parsed as this type",
--     without raising when the answer is no. It is what rejects a well-shaped but impossible day such
--     as the 31st of February, which the shape check above happily accepts.
--
--   GROUP BY to_date(...)
--     GROUP BY collapses many rows into one row per distinct value of the listed expression -- here,
--     one row per release day. Once rows are collapsed there is no single "the polygon" left to
--     select, so the only other selected column is the aggregate `count(*)`, the number of rows that
--     fell into the group. The full expression is repeated rather than the output name being reused,
--     because standard SQL evaluates GROUP BY before the SELECT list has produced its names.
--
--   count(*)
--     The number of rows in the group. `*` here does not mean "all columns"; it means "count rows,
--     including rows whose columns are NULL".
--
--   ORDER BY to_date(...)
--     Chronological order, and it also makes the LIMIT below deterministic -- without an order a
--     capped result would be an arbitrary sample rather than the earliest days.
--
--   LIMIT row_limit
--     The cap. It exists so an unexpectedly long history cannot pull an unbounded result into memory;
--     the Python side treats hitting it as an error, not as an answer.
SELECT to_date(drought_areas.valid_date, 'YYYY-MM-DD') AS observed_day,
       count(*)                                        AS observation_count
  FROM geo.drought_areas AS drought_areas
 WHERE drought_areas.valid_date ~ '^\d{4}-\d{2}-\d{2}$'
   AND pg_input_is_valid(drought_areas.valid_date, 'date')
 GROUP BY to_date(drought_areas.valid_date, 'YYYY-MM-DD')
 ORDER BY to_date(drought_areas.valid_date, 'YYYY-MM-DD')
 LIMIT :row_limit
