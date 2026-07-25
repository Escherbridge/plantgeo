-- Run manually after migration 20260723_0009 when plantgeo_loader already exists.
-- Example: psql ... -f grant-resolution-aware-loader.sql

BEGIN;

DO $$
DECLARE
    loader_role pg_roles%ROWTYPE;
BEGIN
    IF current_user <> 'plantgeo_owner' THEN
        RAISE EXCEPTION 'grant gate must run as plantgeo_owner, not %', current_user;
    END IF;
    IF current_database() <> 'plantgeo'
        AND current_database() !~ '^plantgeo_geospatial_test_[a-z0-9_]+$'
    THEN
        RAISE EXCEPTION 'grant gate must target plantgeo or a guarded disposable geospatial test database';
    END IF;
    IF to_regnamespace('agri') IS NULL THEN
        RAISE EXCEPTION 'agri schema is missing; apply reviewed migrations first';
    END IF;

    SELECT *
    INTO loader_role
    FROM pg_roles
    WHERE rolname = 'plantgeo_loader';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'plantgeo_loader must be created by the reviewed loader-role gate first';
    END IF;
    IF NOT loader_role.rolcanlogin
        OR loader_role.rolsuper
        OR loader_role.rolcreatedb
        OR loader_role.rolcreaterole
        OR loader_role.rolreplication
        OR loader_role.rolbypassrls
        OR loader_role.rolinherit
    THEN
        RAISE EXCEPTION 'plantgeo_loader attributes differ from the constrained role contract';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_auth_members AS membership
        INNER JOIN pg_roles AS member_role ON member_role.oid = membership.member
        WHERE member_role.rolname = 'plantgeo_loader'
    ) THEN
        RAISE EXCEPTION 'plantgeo_loader has role memberships; inspect before granting 0009 access';
    END IF;

    IF to_regclass('agri.normalized_source_feature') IS NULL
        OR to_regclass('agri.analysis_subject') IS NULL
        OR to_regclass('agri.intervention_analysis_run') IS NULL
        OR to_regclass('agri.intervention_evidence_input') IS NULL
        OR to_regclass('agri.intervention_evidence_lineage') IS NULL
    THEN
        RAISE EXCEPTION 'migration 20260723_0009 must be applied before this grant gate';
    END IF;
END
$$;

REVOKE ALL PRIVILEGES ON TABLE
    agri.normalized_source_feature,
    agri.analysis_subject,
    agri.intervention_analysis_run,
    agri.intervention_evidence_input,
    agri.intervention_evidence_lineage
FROM plantgeo_loader;
GRANT SELECT, INSERT ON TABLE
    agri.normalized_source_feature,
    agri.analysis_subject,
    agri.intervention_analysis_run,
    agri.intervention_evidence_input,
    agri.intervention_evidence_lineage
TO plantgeo_loader;
GRANT SELECT ON TABLE
    agri.source_release,
    agri.artifact,
    agri.release_set,
    agri.release_set_item,
    agri.signal_observation,
    agri.drought_polygon_snapshot,
    agri.signal_coverage_audit,
    agri.source_coverage_audit
TO plantgeo_loader;

-- Revision 0009 uses UUID primary keys and creates no sequences.

COMMIT;
