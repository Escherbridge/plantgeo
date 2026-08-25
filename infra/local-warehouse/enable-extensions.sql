-- Manual owner-operated extension gate for the dedicated PlantGeo warehouse.
-- Run after verifying pg_available_extensions and before Alembic/Drizzle migrations.
-- Never copy this file into an image init directory or a long-running service startup command.
\set ON_ERROR_STOP on

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

SELECT extname, extversion
FROM pg_extension
WHERE extname IN ('postgis', 'vector', 'pgcrypto')
ORDER BY extname;
