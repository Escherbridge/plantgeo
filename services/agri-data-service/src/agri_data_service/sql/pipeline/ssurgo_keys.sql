-- ssurgo_keys
-- Purpose: select one bounded page of native polygon keys, ordered, before any geometry fetch.
-- Loaded by: pipeline.direct.soil_survey.source
-- Params: area, after_key, page_size -- all validated as ASCII decimal or a checked symbol before
--         they ever reach this template.
-- Dialect: T-SQL, run remotely by USDA SDA (Tabular/post.rest); no PostgreSQL involvement.
--
-- Safety: after_key and page_size are checked ASCII-decimal (capped digit count and page-row
-- ceiling) and area is checked against ^[A-Z]{2}[0-9]{3}$, all before formatting -- the same
-- literal-substitution boundary every T-SQL file here rests on (AGENTS.md).
--
-- How this query works, clause by clause:
--
--   SELECT TOP {page_size} p.mupolygonkey
--     TOP bounds the response to one page's worth of keys before the expensive geometry and
--     component projection ever runs -- a joined key+geometry+attribute fetch timed out even at
--     one row in a bounded source probe (AGENTS.md, "Source and identity"), so this file exists
--     to keep that projection off the hot path entirely.
--
--   FROM mupolygon p
--   INNER JOIN mapunit mu ON mu.mukey = p.mukey
--   INNER JOIN legend lg ON lg.lkey = mu.lkey
--     Walk from the native polygon up to its survey area, the same join shape ssurgo_page.sql
--     uses to fetch the matching rows, so the two queries agree on which polygons belong to
--     the requested area.
--
--   WHERE lg.areasymbol = '{area}' AND p.mupolygonkey > {after_key}
--     The area scopes the page; the strict greater-than on the native integer key is the whole
--     resume mechanism -- keyset pagination, not an OFFSET, so a page never repeats or skips a
--     row regardless of how many times capture.py resumes this area.
--
--   ORDER BY p.mupolygonkey
--     A total order on the native key. Without it TOP could return an arbitrary subset on a
--     retried call, breaking the resume guarantee the WHERE clause above depends on.
SELECT TOP {page_size} p.mupolygonkey
FROM mupolygon p
INNER JOIN mapunit mu ON mu.mukey = p.mukey
INNER JOIN legend lg ON lg.lkey = mu.lkey
WHERE lg.areasymbol = '{area}' AND p.mupolygonkey > {after_key}
ORDER BY p.mupolygonkey
