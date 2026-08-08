-- Purpose: retention. Keep only the newest N US Drought Monitor release days in `geo.drought_areas`
--          and remove every row belonging to any older release, reporting the days it removed.
-- Loaded by: agri_data_service.ingest.usdm
-- Params: retain (int) -- how many distinct release days to keep. Counted in RELEASES, not in rows:
--         one release day holds one polygon per drought class, so rows and releases are not the same
--         unit and confusing them would keep far less history than the operator asked for.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too and would mint a bind
-- parameter nobody supplies.
--
-- What this returns: one row per row removed, carrying that row's release day. Several rows share a
-- day, so the caller reduces the result to a distinct set before reporting a count -- the operator is
-- told how many RELEASES went, not how many rows.
--
-- How this query works, clause by clause:
--
--   DELETE FROM geo.drought_areas WHERE valid_date NOT IN (...)
--     A write. Every row whose release day is NOT among the days the subquery names is removed.
--     Framing it as "not in the keep list" rather than "older than some cutoff date" means the rule is
--     expressed in the same unit the operator thinks in -- a number of releases -- and stays correct
--     across the gaps in the publisher's schedule, where "N weeks ago" and "N releases ago" differ.
--
--   NOT IN (<subquery>)
--     A membership test against the rows the subquery returns. One caution worth knowing: if the
--     subquery could yield a NULL, NOT IN answers NULL for every row and nothing at all is removed.
--     It cannot here -- `valid_date` is the table's own NOT NULL key column, and the subquery reads it
--     straight back out of the same table.
--
--   SELECT valid_date FROM geo.drought_areas GROUP BY valid_date
--     The keep list. GROUP BY collapses many rows into one row per distinct value, so this yields each
--     release day exactly once however many drought-class polygons it holds. That is what makes the
--     LIMIT below count releases rather than rows.
--
--   ORDER BY valid_date DESC
--     Newest release first, so the LIMIT keeps the newest ones. `DESC` is descending order; without it
--     the retention would keep the OLDEST releases and remove everything recent.
--
--   LIMIT retain
--     How many release days survive. Bound as a parameter, never interpolated into the text. Note it
--     is named here without the leading colon the statement below writes it with -- a comment quoting
--     SQL is still a comment that text() scans for bind parameters.
--
--   RETURNING valid_date
--     RETURNING makes a write also behave like a read, handing back a row for each row it actually
--     removed. It is the only honest way to learn what this statement did -- the caller reports what
--     came back, not what it assumed would go.
DELETE FROM geo.drought_areas
WHERE valid_date NOT IN (
    SELECT valid_date
    FROM geo.drought_areas
    GROUP BY valid_date
    ORDER BY valid_date DESC
    LIMIT :retain
)
RETURNING valid_date
