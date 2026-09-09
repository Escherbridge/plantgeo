-- The layer registry, seeded from production so a freshly bootstrapped database can serve.
--
-- WHY THIS IS NOT OPTIONAL. `drizzle/0000_baseline.sql` is a --schema-only dump, and the archived
-- migrations that used to seed `geo.layers` are off the migration path. Without these rows
-- `/api/ready` fails its `count(DISTINCT name) = 8` check (`src/app/api/ready/route.ts`), returns
-- 503, and Railway's healthcheck kills the deployment -- so a new region would never come up.
--
-- Ids are production's, deliberately: layer ids are referenced from configuration and env
-- (`src/lib/server/layer-ids.ts`), so a new region matching production keeps those references
-- valid. `team_id` is NULL for all eleven rows in production, so nothing here is tenant-specific.
--
-- ON CONFLICT (id) DO NOTHING makes this re-runnable and makes it defer to a database that
-- already has its own registry.

INSERT INTO geo.layers (id, name, type, description, style, is_public, min_zoom, max_zoom, team_id, sort_order)
VALUES ('3656dd9a-41c8-472a-afa7-c9bd3cbc3805', 'burn-severity', 'vector', 'MTBS burned-area boundaries by published release', '{}', 't', 0, 22, NULL, 0)
  ON CONFLICT (id) DO NOTHING;
INSERT INTO geo.layers (id, name, type, description, style, is_public, min_zoom, max_zoom, team_id, sort_order)
VALUES ('3beacdc2-28b1-4e1e-95bb-adfb776199b5', 'evacuation-zones', 'vector', 'Evacuation zone boundaries', '{}', 't', 0, 22, NULL, 0)
  ON CONFLICT (id) DO NOTHING;
INSERT INTO geo.layers (id, name, type, description, style, is_public, min_zoom, max_zoom, team_id, sort_order)
VALUES ('17241b0f-f2bb-49ae-9c74-aa4277effba0', 'fire-detections', 'vector', 'Near-real-time fire detections', '{}', 't', 0, 22, NULL, 0)
  ON CONFLICT (id) DO NOTHING;
INSERT INTO geo.layers (id, name, type, description, style, is_public, min_zoom, max_zoom, team_id, sort_order)
VALUES ('9fd6d097-010c-45c5-bb05-1408693da707', 'fire-perimeters', 'vector', 'Active wildfire perimeters', '{}', 't', 0, 22, NULL, 0)
  ON CONFLICT (id) DO NOTHING;
INSERT INTO geo.layers (id, name, type, description, style, is_public, min_zoom, max_zoom, team_id, sort_order)
VALUES ('9c467bfe-0915-467f-a824-590d5aba97ac', 'interventions', 'vector', 'Ecosystem intervention sites', '{}', 't', 0, 22, NULL, 0)
  ON CONFLICT (id) DO NOTHING;
INSERT INTO geo.layers (id, name, type, description, style, is_public, min_zoom, max_zoom, team_id, sort_order)
VALUES ('a8855fc4-0809-47fd-8c70-3da040a58b04', 'sensors', 'vector', 'Environmental sensor locations', '{}', 't', 0, 22, NULL, 0)
  ON CONFLICT (id) DO NOTHING;
INSERT INTO geo.layers (id, name, type, description, style, is_public, min_zoom, max_zoom, team_id, sort_order)
VALUES ('16b910df-bddf-4b56-b569-cd09618a9e7d', 'soil-survey', 'vector', 'USDA SSURGO soil map-unit delineations, persisted per survey-area vintage', '{}', 't', 0, 22, NULL, 0)
  ON CONFLICT (id) DO NOTHING;
INSERT INTO geo.layers (id, name, type, description, style, is_public, min_zoom, max_zoom, team_id, sort_order)
VALUES ('28961c30-884b-4c4c-9f18-ab4a924ba1c6', 'vegetation', 'vector', 'Vegetation coverage areas', '{}', 't', 0, 22, NULL, 0)
  ON CONFLICT (id) DO NOTHING;
INSERT INTO geo.layers (id, name, type, description, style, is_public, min_zoom, max_zoom, team_id, sort_order)
VALUES ('a003154c-c0db-42ed-9b48-8e83261a721a', 'water-gauges', 'vector', 'Current persisted USGS streamflow observations', '{}', 't', 0, 22, NULL, 0)
  ON CONFLICT (id) DO NOTHING;
INSERT INTO geo.layers (id, name, type, description, style, is_public, min_zoom, max_zoom, team_id, sort_order)
VALUES ('cdc7aa71-4145-41e9-ba1a-d179987b130e', 'watersheds', 'vector', 'USGS WBD HUC12 watershed boundaries', '{}', 't', 0, 22, NULL, 0)
  ON CONFLICT (id) DO NOTHING;
INSERT INTO geo.layers (id, name, type, description, style, is_public, min_zoom, max_zoom, team_id, sort_order)
VALUES ('cacde6e7-db8d-4ada-b6e4-f5859a0d3bdd', 'weather-observations', 'vector', 'Current persisted weather observations', '{}', 't', 0, 22, NULL, 0)
  ON CONFLICT (id) DO NOTHING;
