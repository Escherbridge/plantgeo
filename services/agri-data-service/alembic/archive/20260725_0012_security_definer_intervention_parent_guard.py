"""Harden intervention-parent trigger reads for the constrained loader role.

Revision ID: 20260725_0012
Revises: 20260725_0011

The trigger body is embedded here rather than loaded from ``db/agri/`` because
``20260803_0018`` drops ``agri.protect_intervention_evidence_parents``, and the
declarative tree is regenerated from a dump of *head* — so the canonical file no
longer exists and ``load_object_sql`` could not replay this revision. A migration
whose object a later revision drops must carry its own DDL. See ``db/AGENTS.md``.
"""

from collections.abc import Sequence

from alembic import op

revision = "20260725_0012"
down_revision = "20260725_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PROTECT_INTERVENTION_EVIDENCE_PARENTS = r"""
CREATE OR REPLACE FUNCTION agri.protect_intervention_evidence_parents() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'agri'
    AS $$
        DECLARE
            is_referenced boolean := false;
            is_critical_change boolean := TG_OP = 'DELETE';
        BEGIN
            CASE TG_TABLE_NAME
                WHEN 'source_release' THEN
                    IF TG_OP = 'UPDATE' THEN
                        is_critical_change := ROW(
                            NEW.data_source_id,
                            NEW.source_version,
                            NEW.retrieved_at,
                            NEW.data_available_at,
                            NEW.observed_from,
                            NEW.observed_to,
                            NEW.payload_checksum,
                            NEW.payload_bytes,
                            NEW.schema_version,
                            NEW.transform_version,
                            NEW.license_snapshot,
                            NEW.query_parameters,
                            NEW.quality_summary,
                            NEW.validated_at,
                            NEW.supersedes_release_id
                        ) IS DISTINCT FROM ROW(
                            OLD.data_source_id,
                            OLD.source_version,
                            OLD.retrieved_at,
                            OLD.data_available_at,
                            OLD.observed_from,
                            OLD.observed_to,
                            OLD.payload_checksum,
                            OLD.payload_bytes,
                            OLD.schema_version,
                            OLD.transform_version,
                            OLD.license_snapshot,
                            OLD.query_parameters,
                            OLD.quality_summary,
                            OLD.validated_at,
                            OLD.supersedes_release_id
                        );
                    END IF;
                    SELECT EXISTS (
                        SELECT 1 FROM agri.normalized_source_feature WHERE source_release_id = OLD.id
                        UNION ALL
                        SELECT 1 FROM agri.analysis_subject WHERE source_release_id = OLD.id
                        UNION ALL
                        SELECT 1 FROM agri.intervention_evidence_lineage WHERE source_release_id = OLD.id
                    ) INTO is_referenced;
                WHEN 'artifact' THEN
                    IF TG_OP = 'UPDATE' THEN
                        is_critical_change := (to_jsonb(NEW) - 'created_at')
                            IS DISTINCT FROM (to_jsonb(OLD) - 'created_at');
                    END IF;
                    SELECT EXISTS (
                        SELECT 1
                        FROM agri.normalized_source_feature
                        WHERE artifact_id = OLD.id
                           OR source_release_id = OLD.source_release_id
                        UNION ALL
                        SELECT 1
                        FROM agri.analysis_subject
                        WHERE artifact_id = OLD.id
                           OR source_release_id = OLD.source_release_id
                    ) INTO is_referenced;
                WHEN 'release_set' THEN
                    IF TG_OP = 'UPDATE' THEN
                        is_critical_change := ROW(
                            NEW.logical_key,
                            NEW.as_of_time,
                            NEW.manifest_checksum,
                            NEW.description
                        ) IS DISTINCT FROM ROW(
                            OLD.logical_key,
                            OLD.as_of_time,
                            OLD.manifest_checksum,
                            OLD.description
                        );
                    END IF;
                    SELECT EXISTS (
                        SELECT 1 FROM agri.intervention_analysis_run WHERE release_set_id = OLD.id
                        UNION ALL
                        SELECT 1 FROM agri.intervention_evidence_input WHERE release_set_id = OLD.id
                    ) INTO is_referenced;
                WHEN 'release_set_item' THEN
                    IF TG_OP = 'UPDATE' THEN
                        is_critical_change := ROW(
                            NEW.release_set_id,
                            NEW.source_release_id,
                            NEW.source_role
                        ) IS DISTINCT FROM ROW(
                            OLD.release_set_id,
                            OLD.source_release_id,
                            OLD.source_role
                        );
                    END IF;
                    PERFORM 1
                    FROM agri.release_set
                    WHERE id = OLD.release_set_id
                    FOR UPDATE;
                    SELECT EXISTS (
                        SELECT 1 FROM agri.intervention_analysis_run WHERE release_set_id = OLD.release_set_id
                        UNION ALL
                        SELECT 1 FROM agri.intervention_evidence_input WHERE release_set_id = OLD.release_set_id
                    ) INTO is_referenced;
                ELSE
                    RAISE EXCEPTION 'Unsupported intervention evidence parent table';
            END CASE;
            IF TG_TABLE_NAME = 'source_release'
                AND is_referenced
                AND TG_OP = 'UPDATE'
            THEN
                IF ROW(NEW.validation_state, NEW.retraction_reason)
                        IS DISTINCT FROM ROW(OLD.validation_state, OLD.retraction_reason)
                    AND NOT (
                        OLD.validation_state = 'valid'
                        AND NEW.validation_state = 'retracted'
                        AND NEW.retraction_reason IS NOT NULL
                    )
                THEN
                    RAISE EXCEPTION 'Referenced source releases allow only valid-to-retracted lifecycle changes';
                END IF;
            END IF;
            IF is_referenced AND is_critical_change THEN
                RAISE EXCEPTION '% content is frozen by intervention evidence lineage', TG_TABLE_NAME;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END
        $$;
"""


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
    op.execute(_PROTECT_INTERVENTION_EVIDENCE_PARENTS)
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
        "The constrained loader depends on the hardened parent guard; restore a verified backup into a fresh database."
    )
