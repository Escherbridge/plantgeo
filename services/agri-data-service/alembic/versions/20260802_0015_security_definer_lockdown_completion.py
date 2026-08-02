"""Extend the 0012 locked-owner recipe to the remaining unlocked SECURITY DEFINER functions.

Revision ID: 20260802_0015
Revises: 20260801_0014

Eleven SECURITY DEFINER trigger/utility functions were shipped with a pinned
``search_path`` and (in most cases) ``REVOKE EXECUTE ... FROM PUBLIC``, but
none of them had a dedicated, locked, non-login owner: they remained owned by
whichever role applied the migration that created them (a broad admin/loader
identity), which is exactly the SECURITY DEFINER privilege-escalation risk the
0012 recipe closed for ``protect_intervention_evidence_parents``. This
revision creates three narrowly scoped NOLOGIN owner roles -- validated with
the same locked-attribute and no-membership checks as 0012 -- and re-parents
the eleven functions (plus the one materialized view one of them refreshes)
onto them, grouped by the minimal read/write footprint each function needs:

* ``plantgeo_forecast_input_recorder_owner`` -- the seven
  ``record_forecast_*`` triggers that only upsert
  ``agri.forecast_input_recorded_at``.
* ``plantgeo_forecast_mv_refresh_owner`` -- ``refresh_forecast_ml_daily_serving``,
  which also becomes the owner of ``agri.mv_forecast_ml_daily_serving`` (REFRESH
  requires matview ownership) and needs SELECT on the series it aggregates.
* ``plantgeo_release_lineage_guard_owner`` -- the three intervention-evidence
  lineage/release-membership guards, which need read-only access to the
  release, evidence, and source-record tables they validate against, plus the
  narrow ``UPDATE (id)`` row-lock grant on the three parents they pin with
  ``FOR SHARE`` (PostgreSQL requires UPDATE on at least one column of a
  row-locked relation, which is why 0012 already grants ``UPDATE (id)`` on
  ``release_set`` to ``plantgeo_intervention_guard_owner``).

No function body changes: only ownership and grants move, so the declarative
tree under ``db/agri/**`` (dumped with ``--no-owner --no-privileges``, see
``db/tools/dump_schema.py``) is unaffected and needs no regeneration.
"""

from collections.abc import Sequence

from alembic import op

revision = "20260802_0015"
down_revision = "20260801_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- plantgeo_forecast_input_recorder_owner: the seven append-only
    # forecast_input_recorded_at upsert triggers. -----------------------------
    op.execute(
        r"""
        DO $guard_owner_input_recorder$
        DECLARE
            owner_role pg_catalog.pg_roles%ROWTYPE;
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_roles
                WHERE rolname = 'plantgeo_forecast_input_recorder_owner'
            ) THEN
                CREATE ROLE plantgeo_forecast_input_recorder_owner
                    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOREPLICATION NOBYPASSRLS NOINHERIT;
            END IF;

            SELECT *
            INTO STRICT owner_role
            FROM pg_catalog.pg_roles
            WHERE rolname = 'plantgeo_forecast_input_recorder_owner';

            IF owner_role.rolcanlogin
                OR owner_role.rolsuper
                OR owner_role.rolcreatedb
                OR owner_role.rolcreaterole
                OR owner_role.rolreplication
                OR owner_role.rolbypassrls
                OR owner_role.rolinherit
            THEN
                RAISE EXCEPTION
                    'plantgeo_forecast_input_recorder_owner attributes violate the locked owner contract';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_catalog.pg_auth_members
                WHERE roleid = owner_role.oid
                   OR member = owner_role.oid
            ) THEN
                RAISE EXCEPTION
                    'plantgeo_forecast_input_recorder_owner must not have role memberships';
            END IF;
        END
        $guard_owner_input_recorder$;

        REVOKE ALL PRIVILEGES ON SCHEMA agri
            FROM plantgeo_forecast_input_recorder_owner;
        GRANT USAGE, CREATE ON SCHEMA agri
            TO plantgeo_forecast_input_recorder_owner;

        REVOKE ALL PRIVILEGES ON TABLE agri.forecast_input_recorded_at
            FROM plantgeo_forecast_input_recorder_owner;
        GRANT INSERT, SELECT ON TABLE agri.forecast_input_recorded_at
            TO plantgeo_forecast_input_recorder_owner;
        GRANT UPDATE (recorded_at) ON TABLE agri.forecast_input_recorded_at
            TO plantgeo_forecast_input_recorder_owner;

        REVOKE EXECUTE ON FUNCTION agri.record_forecast_release_set_item_insert() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.record_forecast_release_set_item_update() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.record_forecast_release_set_item_delete() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.record_forecast_release_content_insert() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.record_forecast_release_content_update() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.record_forecast_release_content_delete() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.record_forecast_input_change() FROM PUBLIC;

        ALTER FUNCTION agri.record_forecast_release_set_item_insert()
            OWNER TO plantgeo_forecast_input_recorder_owner;
        ALTER FUNCTION agri.record_forecast_release_set_item_update()
            OWNER TO plantgeo_forecast_input_recorder_owner;
        ALTER FUNCTION agri.record_forecast_release_set_item_delete()
            OWNER TO plantgeo_forecast_input_recorder_owner;
        ALTER FUNCTION agri.record_forecast_release_content_insert()
            OWNER TO plantgeo_forecast_input_recorder_owner;
        ALTER FUNCTION agri.record_forecast_release_content_update()
            OWNER TO plantgeo_forecast_input_recorder_owner;
        ALTER FUNCTION agri.record_forecast_release_content_delete()
            OWNER TO plantgeo_forecast_input_recorder_owner;
        ALTER FUNCTION agri.record_forecast_input_change()
            OWNER TO plantgeo_forecast_input_recorder_owner;

        REVOKE CREATE ON SCHEMA agri
            FROM plantgeo_forecast_input_recorder_owner;
        """
    )

    # -- plantgeo_forecast_mv_refresh_owner: refresh_forecast_ml_daily_serving,
    # which must also own the matview it refreshes (REFRESH requires matview
    # ownership pre-MAINTAIN-privilege) and read the series it aggregates. ----
    op.execute(
        r"""
        DO $guard_owner_mv_refresh$
        DECLARE
            owner_role pg_catalog.pg_roles%ROWTYPE;
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_roles
                WHERE rolname = 'plantgeo_forecast_mv_refresh_owner'
            ) THEN
                CREATE ROLE plantgeo_forecast_mv_refresh_owner
                    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOREPLICATION NOBYPASSRLS NOINHERIT;
            END IF;

            SELECT *
            INTO STRICT owner_role
            FROM pg_catalog.pg_roles
            WHERE rolname = 'plantgeo_forecast_mv_refresh_owner';

            IF owner_role.rolcanlogin
                OR owner_role.rolsuper
                OR owner_role.rolcreatedb
                OR owner_role.rolcreaterole
                OR owner_role.rolreplication
                OR owner_role.rolbypassrls
                OR owner_role.rolinherit
            THEN
                RAISE EXCEPTION
                    'plantgeo_forecast_mv_refresh_owner attributes violate the locked owner contract';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_catalog.pg_auth_members
                WHERE roleid = owner_role.oid
                   OR member = owner_role.oid
            ) THEN
                RAISE EXCEPTION
                    'plantgeo_forecast_mv_refresh_owner must not have role memberships';
            END IF;
        END
        $guard_owner_mv_refresh$;

        REVOKE ALL PRIVILEGES ON SCHEMA agri
            FROM plantgeo_forecast_mv_refresh_owner;
        GRANT USAGE, CREATE ON SCHEMA agri
            TO plantgeo_forecast_mv_refresh_owner;

        REVOKE ALL PRIVILEGES ON TABLE
            agri.forecast_series,
            agri.v_forecast_series_serving,
            agri.mv_forecast_ml_daily_serving
        FROM plantgeo_forecast_mv_refresh_owner;
        GRANT SELECT ON TABLE
            agri.forecast_series,
            agri.v_forecast_series_serving
        TO plantgeo_forecast_mv_refresh_owner;

        REVOKE EXECUTE ON FUNCTION agri.refresh_forecast_ml_daily_serving() FROM PUBLIC;

        ALTER FUNCTION agri.refresh_forecast_ml_daily_serving()
            OWNER TO plantgeo_forecast_mv_refresh_owner;
        ALTER MATERIALIZED VIEW agri.mv_forecast_ml_daily_serving
            OWNER TO plantgeo_forecast_mv_refresh_owner;

        REVOKE CREATE ON SCHEMA agri
            FROM plantgeo_forecast_mv_refresh_owner;

        DO $grant_mv_refresher_capability$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_catalog.pg_roles
                WHERE rolname = 'plantgeo_forecast_mv_refresher'
            ) THEN
                GRANT EXECUTE ON FUNCTION agri.refresh_forecast_ml_daily_serving()
                    TO plantgeo_forecast_mv_refresher;
            END IF;
        END
        $grant_mv_refresher_capability$;
        """
    )

    # -- plantgeo_release_lineage_guard_owner: the three intervention-evidence
    # lineage/release-membership BEFORE INSERT guards. -------------------------
    op.execute(
        r"""
        DO $guard_owner_lineage_guard$
        DECLARE
            owner_role pg_catalog.pg_roles%ROWTYPE;
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_roles
                WHERE rolname = 'plantgeo_release_lineage_guard_owner'
            ) THEN
                CREATE ROLE plantgeo_release_lineage_guard_owner
                    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOREPLICATION NOBYPASSRLS NOINHERIT;
            END IF;

            SELECT *
            INTO STRICT owner_role
            FROM pg_catalog.pg_roles
            WHERE rolname = 'plantgeo_release_lineage_guard_owner';

            IF owner_role.rolcanlogin
                OR owner_role.rolsuper
                OR owner_role.rolcreatedb
                OR owner_role.rolcreaterole
                OR owner_role.rolreplication
                OR owner_role.rolbypassrls
                OR owner_role.rolinherit
            THEN
                RAISE EXCEPTION
                    'plantgeo_release_lineage_guard_owner attributes violate the locked owner contract';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_catalog.pg_auth_members
                WHERE roleid = owner_role.oid
                   OR member = owner_role.oid
            ) THEN
                RAISE EXCEPTION
                    'plantgeo_release_lineage_guard_owner must not have role memberships';
            END IF;
        END
        $guard_owner_lineage_guard$;

        REVOKE ALL PRIVILEGES ON SCHEMA agri
            FROM plantgeo_release_lineage_guard_owner;
        GRANT USAGE, CREATE ON SCHEMA agri
            TO plantgeo_release_lineage_guard_owner;

        REVOKE ALL PRIVILEGES ON TABLE
            agri.release_set,
            agri.release_set_item,
            agri.source_release,
            agri.intervention_evidence_input,
            agri.analysis_subject,
            agri.artifact,
            agri.signal_observation,
            agri.drought_polygon_snapshot,
            agri.signal_coverage_audit,
            agri.source_coverage_audit
        FROM plantgeo_release_lineage_guard_owner;
        GRANT SELECT ON TABLE
            agri.release_set,
            agri.release_set_item,
            agri.source_release,
            agri.intervention_evidence_input,
            agri.analysis_subject,
            agri.artifact,
            agri.signal_observation,
            agri.drought_polygon_snapshot,
            agri.signal_coverage_audit,
            agri.source_coverage_audit
        TO plantgeo_release_lineage_guard_owner;

        -- The three guards pin their parents with ``SELECT ... FOR SHARE`` so a
        -- concurrent transaction cannot invalidate the lineage between the check
        -- and the child insert. PostgreSQL requires UPDATE (or DELETE) privilege
        -- on at least one column of every row-locked relation in addition to
        -- SELECT, so each locked parent gets the same narrow ``UPDATE (id)``
        -- grant that 0012 gave ``plantgeo_intervention_guard_owner`` on
        -- ``release_set``. ``id`` is the immutable surrogate key and is itself
        -- blocked from mutation by the existing immutability triggers, so this
        -- is a row-lock capability rather than a real write capability.
        --   * release_set    -- FOR SHARE OF release_set in
        --     enforce_intervention_analysis_release_set and
        --     enforce_intervention_lineage_release_membership.
        --   * source_release -- FOR SHARE OF source_release / subject_release in
        --     enforce_intervention_lineage_release_membership and
        --     FOR SHARE OF ..., source_release in
        --     enforce_normalized_feature_artifact_release.
        --   * artifact       -- FOR SHARE OF artifact, ... in
        --     enforce_normalized_feature_artifact_release.
        GRANT UPDATE (id) ON TABLE agri.release_set
            TO plantgeo_release_lineage_guard_owner;
        GRANT UPDATE (id) ON TABLE agri.source_release
            TO plantgeo_release_lineage_guard_owner;
        GRANT UPDATE (id) ON TABLE agri.artifact
            TO plantgeo_release_lineage_guard_owner;

        REVOKE EXECUTE ON FUNCTION agri.enforce_intervention_lineage_release_membership() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.enforce_normalized_feature_artifact_release() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.enforce_intervention_analysis_release_set() FROM PUBLIC;

        ALTER FUNCTION agri.enforce_intervention_lineage_release_membership()
            OWNER TO plantgeo_release_lineage_guard_owner;
        ALTER FUNCTION agri.enforce_normalized_feature_artifact_release()
            OWNER TO plantgeo_release_lineage_guard_owner;
        ALTER FUNCTION agri.enforce_intervention_analysis_release_set()
            OWNER TO plantgeo_release_lineage_guard_owner;

        REVOKE CREATE ON SCHEMA agri
            FROM plantgeo_release_lineage_guard_owner;
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Downstream triggers and the ML refresh path depend on the hardened locked "
        "owners; restore a verified backup into a fresh database."
    )
