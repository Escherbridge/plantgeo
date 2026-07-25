\set ON_ERROR_STOP on

BEGIN;

CREATE ROLE plantgeo_forecast_refresh_operator
    LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'refresh_password';
GRANT CONNECT ON DATABASE plantgeo TO plantgeo_forecast_refresh_operator;
GRANT plantgeo_forecast_mv_refresher TO plantgeo_forecast_refresh_operator
    WITH INHERIT FALSE, SET TRUE;

COMMIT;

SELECT
    operator.rolname,
    operator.rolcanlogin,
    operator.rolinherit,
    membership.admin_option,
    membership.inherit_option,
    membership.set_option
FROM pg_roles AS operator
JOIN pg_auth_members AS membership ON membership.member = operator.oid
JOIN pg_roles AS capability ON capability.oid = membership.roleid
WHERE operator.rolname = 'plantgeo_forecast_refresh_operator'
  AND capability.rolname = 'plantgeo_forecast_mv_refresher';
