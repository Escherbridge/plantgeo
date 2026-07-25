\set ON_ERROR_STOP on

BEGIN;

CREATE ROLE plantgeo_local_developer
    LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'developer_password';
CREATE ROLE plantgeo_local_viewer
    LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'viewer_password';

GRANT CONNECT, CREATE, TEMPORARY ON DATABASE plantgeo TO plantgeo_local_developer;
GRANT ALL PRIVILEGES ON SCHEMA agri TO plantgeo_local_developer;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA agri TO plantgeo_local_developer;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA agri TO plantgeo_local_developer;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA agri TO plantgeo_local_developer;
GRANT plantgeo_forecast_writer,
      plantgeo_forecast_publisher,
      plantgeo_forecast_reader,
      plantgeo_forecast_mv_refresher
TO plantgeo_local_developer;
GRANT pg_monitor TO plantgeo_local_developer;

ALTER DEFAULT PRIVILEGES FOR ROLE plantgeo_owner IN SCHEMA agri
    GRANT ALL PRIVILEGES ON TABLES TO plantgeo_local_developer;
ALTER DEFAULT PRIVILEGES FOR ROLE plantgeo_owner IN SCHEMA agri
    GRANT ALL PRIVILEGES ON SEQUENCES TO plantgeo_local_developer;
ALTER DEFAULT PRIVILEGES FOR ROLE plantgeo_owner IN SCHEMA agri
    GRANT EXECUTE ON FUNCTIONS TO plantgeo_local_developer;

GRANT CONNECT ON DATABASE plantgeo TO plantgeo_local_viewer;
GRANT USAGE ON SCHEMA agri TO plantgeo_local_viewer;
GRANT pg_read_all_data, pg_monitor, plantgeo_forecast_reader TO plantgeo_local_viewer;
ALTER ROLE plantgeo_local_viewer SET default_transaction_read_only = on;

COMMIT;

SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls
FROM pg_roles
WHERE rolname IN ('plantgeo_local_developer', 'plantgeo_local_viewer')
ORDER BY rolname;
