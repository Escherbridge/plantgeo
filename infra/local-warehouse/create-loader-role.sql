-- Run manually after extensions and reviewed migrations, as plantgeo_owner.
-- Example: psql ... -v loader_password='<unique local loader password>' -f create-loader-role.sql
\if :{?loader_password}
\else
\echo 'Provide a unique loader_password with -v loader_password=...'
\quit
\endif

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'plantgeo_loader') THEN
        RAISE EXCEPTION
            'plantgeo_loader already exists; inspect or drop it before recreating the reviewed loader role';
    END IF;
    CREATE ROLE plantgeo_loader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
END
$$;

ALTER ROLE plantgeo_loader
    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT
    PASSWORD :'loader_password';

REVOKE ALL PRIVILEGES ON DATABASE plantgeo FROM plantgeo_loader;
GRANT CONNECT ON DATABASE plantgeo TO plantgeo_loader;
REVOKE ALL PRIVILEGES ON SCHEMA agri FROM plantgeo_loader;
GRANT USAGE ON SCHEMA agri TO plantgeo_loader;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA agri FROM plantgeo_loader;

GRANT SELECT, INSERT ON TABLE
    agri.data_source,
    agri.source_release,
    agri.artifact,
    agri.release_set,
    agri.release_set_item,
    agri.spatial_cell,
    agri.cell_source_crosswalk,
    agri.signal_observation,
    agri.signal_coverage_audit,
    agri.source_coverage_audit,
    agri.drought_polygon_snapshot
TO plantgeo_loader;
GRANT UPDATE (state, validated_at) ON TABLE agri.release_set TO plantgeo_loader;
GRANT USAGE, SELECT ON SEQUENCE
    agri.signal_observation_id_seq,
    agri.drought_polygon_snapshot_id_seq
TO plantgeo_loader;

COMMIT;
