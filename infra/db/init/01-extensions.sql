-- Container initialization installs extensions and fixes the application role's
-- default schema. Drizzle owns public/geo/tracking; Alembic owns agri and its
-- governed seed records.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Without this, PostgreSQL resolves the `geo` role's unqualified tables into
-- the same-named `geo` schema after that schema is created by Drizzle.
ALTER ROLE CURRENT_USER SET search_path TO public;
