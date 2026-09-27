-- ssurgo_page
-- Purpose: acquire one bounded page of full native SSURGO delineations, geometry and attributes.
-- Loaded by: pipeline.direct.soil_survey.source
-- Params: area, polygon_keys -- a comma-joined list of ASCII-decimal native keys, all validated
--         before they ever reach this template.
-- Dialect: T-SQL, run remotely by USDA SDA (Tabular/post.rest); no PostgreSQL involvement.
--
-- Safety: every key in polygon_keys is checked ASCII-decimal with a bounded digit count and the
-- whole list is checked unique and within the page-row ceiling before formatting; area is checked
-- against ^[A-Z]{2}[0-9]{3}$. Read only the keys ssurgo_keys.sql already returned for this exact
-- page -- payload keys are re-checked against that inventory after this call returns
-- (AGENTS.md, "Literal substitution is still the safety boundary").
--
-- How this query works, clause by clause:
--
--   p.mupolygongeo.STAsText() AS geom
--     STAsText renders SQL Server's native geometry type as WKT text, which is what this layer's
--     preparation step (a later slice) parses and validates. The native key set this returns must
--     exactly match ssurgo_keys.sql's inventory for the same page -- capture.py refuses the page
--     if it does not.
--
--   LEFT JOIN component c ON c.mukey = p.mukey
--     AND c.cokey = (SELECT TOP 1 c2.cokey FROM component c2
--                    WHERE c2.mukey = p.mukey ORDER BY c2.comppct_r DESC, c2.cokey)
--     A correlated subquery picks exactly one dominant component per map unit -- the one with the
--     largest component percentage, breaking ties on its own key for a stable, deterministic
--     choice. This is the same dominant-component rule the retired PostgreSQL-era ingest used, so
--     drainagecl/hydricrating/nirrcapcl mean the same thing they always did. LEFT keeps the
--     delineation even where no component row exists.
--
--   CONVERT(varchar(33), sac.saverest, 126) AS saverest
--     Same ODBC-canonical rendering as ssurgo_summary.sql, so a page's per-row vintage and the
--     area's own census vintage are always directly comparable strings.
--
--   WHERE p.mupolygonkey IN ({polygon_keys}) AND lg.areasymbol = '{area}'
--     Scope to exactly the native keys the paired key-inventory call named, within the one
--     requested area -- never a broader area or key-range scan.
--
--   ORDER BY p.mupolygonkey
--     A total order, so a page's rows compare 1:1 against ssurgo_keys.sql's own ordered inventory
--     without an extra sort step in Python.
SELECT p.mupolygonkey, p.mukey, mu.muname, lg.areasymbol,
       CONVERT(varchar(33), sac.saverest, 126) AS saverest,
       c.compname, c.drainagecl, c.hydricrating, c.nirrcapcl,
       p.mupolygongeo.STAsText() AS geom
FROM mupolygon p
INNER JOIN mapunit mu ON mu.mukey = p.mukey
INNER JOIN legend lg ON lg.lkey = mu.lkey
INNER JOIN sacatalog sac ON sac.areasymbol = lg.areasymbol
LEFT JOIN component c ON c.mukey = p.mukey
  AND c.cokey = (SELECT TOP 1 c2.cokey FROM component c2
                WHERE c2.mukey = p.mukey ORDER BY c2.comppct_r DESC, c2.cokey)
WHERE p.mupolygonkey IN ({polygon_keys}) AND lg.areasymbol = '{area}'
ORDER BY p.mupolygonkey
