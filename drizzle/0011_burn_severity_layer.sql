-- MTBS burn-severity ingestion (services/agri-data-service/.../ingest/mtbs.py)
-- is fully wired and passes its own tests, but the ingestion layer lookup
-- fails loudly at runtime with "configured ingestion layer does not exist:
-- burn-severity" because geo.layers never got a row for it. The 8 rows this
-- table has ever held were the original seed in 0001_handy_riptide.sql:307-317
-- (fire-perimeters, fire-detections, water-gauges, weather-observations,
-- evacuation-zones, sensors, vegetation, interventions); burn-severity was
-- never added alongside them despite the producer existing.
--
-- Column set and shape match that original seed exactly: name/type/description/
-- is_public explicit, everything else (style, min_zoom, max_zoom, team_id,
-- sort_order, created_at, updated_at, id) left to its column default, which is
-- what all 8 existing rows already carry (verified against production
-- 2026-08-04: style='{}', min_zoom=0, max_zoom=22, team_id=NULL, sort_order=0).
INSERT INTO geo.layers (name, type, description, is_public)
VALUES
  ('burn-severity', 'vector', 'MTBS burned-area boundaries by published release', true)
ON CONFLICT (name) DO NOTHING;
