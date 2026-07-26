"""Harden intervention-parent trigger reads for the constrained loader role.

Revision ID: 20260725_0012
Revises: 20260725_0011
"""

from collections.abc import Sequence

from agri_data_service.db.sql_objects import load_object_sql
from alembic import op

revision = "20260725_0012"
down_revision = "20260725_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        r"""
        DO $guard_owner$
        DECLARE
            owner_role pg_catalog.pg_roles%ROWTYPE;
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_roles
                WHERE rolname = 'plantgeo_intervention_guard_owner'
            ) THEN
                CREATE ROLE plantgeo_intervention_guard_owner
                    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOREPLICATION NOBYPASSRLS NOINHERIT;
            END IF;

            SELECT *
            INTO STRICT owner_role
            FROM pg_catalog.pg_roles
            WHERE rolname = 'plantgeo_intervention_guard_owner';

            IF owner_role.rolcanlogin
                OR owner_role.rolsuper
                OR owner_role.rolcreatedb
                OR owner_role.rolcreaterole
                OR owner_role.rolreplication
                OR owner_role.rolbypassrls
                OR owner_role.rolinherit
            THEN
                RAISE EXCEPTION
                    'plantgeo_intervention_guard_owner attributes violate the locked owner contract';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_catalog.pg_auth_members
                WHERE roleid = owner_role.oid
                   OR member = owner_role.oid
            ) THEN
                RAISE EXCEPTION
                    'plantgeo_intervention_guard_owner must not have role memberships';
            END IF;
        END
        $guard_owner$;

        REVOKE CREATE ON SCHEMA agri FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON SCHEMA agri
            FROM plantgeo_intervention_guard_owner;
        GRANT USAGE, CREATE ON SCHEMA agri
            TO plantgeo_intervention_guard_owner;

        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA agri
            FROM plantgeo_intervention_guard_owner;
        GRANT SELECT ON TABLE
            agri.normalized_source_feature,
            agri.analysis_subject,
            agri.intervention_evidence_lineage,
            agri.intervention_analysis_run,
            agri.intervention_evidence_input,
            agri.release_set
        TO plantgeo_intervention_guard_owner;
        GRANT UPDATE (id) ON TABLE agri.release_set
            TO plantgeo_intervention_guard_owner;
        """
    )
    op.execute(load_object_sql("functions/protect_intervention_evidence_parents.sql", or_replace=True))
    op.execute(
        r"""
        REVOKE EXECUTE ON FUNCTION
            agri.protect_intervention_evidence_parents()
        FROM PUBLIC;
        ALTER FUNCTION agri.protect_intervention_evidence_parents()
            OWNER TO plantgeo_intervention_guard_owner;
        REVOKE CREATE ON SCHEMA agri
            FROM plantgeo_intervention_guard_owner;
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "The constrained loader depends on the hardened parent guard; "
        "restore a verified backup into a fresh database."
    )
