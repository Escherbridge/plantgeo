-- ssurgo_summary
-- Purpose: capture one survey area's current native delineation count and its publication watermark.
-- Loaded by: pipeline.direct.soil_survey.source
-- Params: area, a validated USDA survey-area symbol, formatted as a SQL literal.
-- Dialect: T-SQL, run remotely by USDA SDA (Tabular/post.rest); no PostgreSQL involvement.
--
-- Safety: `area` is checked by require_area() against ^[A-Z]{2}[0-9]{3}$ before it ever reaches
-- this template. There is no bind-parameter path into USDA SDA's POST endpoint, so validated
-- literal substitution is the whole safety boundary here (AGENTS.md, "Literal substitution is
-- still the safety boundary").
--
-- How this query works, clause by clause:
--
--   FROM sacatalog sac
--     Start at the survey-area catalog so an existing survey with zero captured polygons still
--     returns its own row and its own vintage, rather than disappearing behind an inner join.
--
--   INNER JOIN legend lg ON lg.areasymbol = sac.areasymbol
--     legend is SSURGO's join key between a catalog entry and its map units.
--
--   LEFT JOIN mapunit mu ON mu.lkey = lg.lkey
--   LEFT JOIN mupolygon p ON p.mukey = mu.mukey
--     LEFT, not INNER: an area can be published with a catalog and legend entry before any map
--     unit polygon exists for it. COUNT(p.mupolygonkey) below then correctly returns zero rather
--     than dropping the row.
--
--   COUNT(p.mupolygonkey) AS delineation_count
--     Count native polygon identities, not polygon rows: mukey repeats across delineations, but
--     mupolygonkey is the one-per-native-polygon grain this whole layer captures at.
--
--   CONVERT(varchar(33), MAX(sac.saverest), 126) AS saverest
--     style 126 renders an ODBC canonical (ISO 8601) string, preserving the source's own clock
--     without inventing a timezone it never declared. MAX collapses the GROUP BY to one row even
--     though sac.saverest is already one value per area; SQL Server requires every non-grouped
--     column to be wrapped in an aggregate.
--
--   WHERE sac.areasymbol = '{area}'
--     One survey area per call; capture.py issues this once per area, at the start and the end of
--     its acquisition, and refuses if the two censuses disagree.
SELECT COUNT(p.mupolygonkey) AS delineation_count,
       CONVERT(varchar(33), MAX(sac.saverest), 126) AS saverest
FROM sacatalog sac
INNER JOIN legend lg ON lg.areasymbol = sac.areasymbol
LEFT JOIN mapunit mu ON mu.lkey = lg.lkey
LEFT JOIN mupolygon p ON p.mukey = mu.mukey
WHERE sac.areasymbol = '{area}'
