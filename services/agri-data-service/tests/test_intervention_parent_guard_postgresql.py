"""Disposable PostgreSQL proof for constrained-loader release-set finalization."""

import hashlib
import os

import psycopg2
import pytest

DATABASE_URL = os.getenv("INTERVENTION_PARENT_GUARD_TEST_DATABASE_URL")
DISPOSABLE_PREFIX = "plantgeo_"
INTERVENTION_TABLES = (
    "intervention_analysis_run",
    "intervention_evidence_input",
)
TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
GUARD_OWNER = "plantgeo_intervention_guard_owner"


@pytest.mark.skipif(
    not DATABASE_URL,
    reason=(
        "set INTERVENTION_PARENT_GUARD_TEST_DATABASE_URL to an admin connection "
        "for a migrated disposable plantgeo_* database"
    ),
)
def test_loader_finalizes_without_intervention_table_grants() -> None:  # noqa: PLR0915
    assert DATABASE_URL is not None
    connection = psycopg2.connect(DATABASE_URL)
    connection.autocommit = False

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), version_num FROM public.alembic_version")
            database_name, revision = cursor.fetchone()
            assert database_name.startswith(DISPOSABLE_PREFIX)
            assert database_name != "plantgeo"
            assert revision in {"20260725_0012", "20260725_0013"}

            cursor.execute(
                """
                SELECT
                    p.prosecdef,
                    p.proconfig,
                    owner_role.rolname,
                    owner_role.rolcanlogin,
                    owner_role.rolsuper,
                    owner_role.rolcreatedb,
                    owner_role.rolcreaterole,
                    owner_role.rolreplication,
                    owner_role.rolbypassrls,
                    owner_role.rolinherit,
                    EXISTS (
                        SELECT 1
                        FROM pg_catalog.pg_auth_members AS membership
                        WHERE membership.roleid = owner_role.oid
                           OR membership.member = owner_role.oid
                    ),
                    EXISTS (
                        SELECT 1
                        FROM pg_catalog.aclexplode(
                            coalesce(
                                p.proacl,
                                pg_catalog.acldefault('f', p.proowner)
                            )
                        ) AS privilege
                        WHERE privilege.grantee = 0
                          AND privilege.privilege_type = 'EXECUTE'
                    )
                FROM pg_catalog.pg_proc AS p
                INNER JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
                INNER JOIN pg_catalog.pg_roles AS owner_role ON owner_role.oid = p.proowner
                WHERE n.nspname = 'agri'
                  AND p.proname = 'protect_intervention_evidence_parents'
                  AND p.pronargs = 0
                """
            )
            (
                is_security_definer,
                function_settings,
                owner_role,
                owner_can_login,
                owner_is_superuser,
                owner_can_create_database,
                owner_can_create_role,
                owner_can_replicate,
                owner_can_bypass_rls,
                owner_inherits,
                owner_has_memberships,
                public_can_execute,
            ) = cursor.fetchone()
            assert is_security_definer is True
            assert function_settings == ["search_path=pg_catalog, agri"]
            assert owner_role == GUARD_OWNER
            assert owner_can_login is False
            assert owner_is_superuser is False
            assert owner_can_create_database is False
            assert owner_can_create_role is False
            assert owner_can_replicate is False
            assert owner_can_bypass_rls is False
            assert owner_inherits is False
            assert owner_has_memberships is False
            assert public_can_execute is False

            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_namespace AS namespace
                    CROSS JOIN LATERAL pg_catalog.aclexplode(
                        coalesce(
                            namespace.nspacl,
                            pg_catalog.acldefault('n', namespace.nspowner)
                        )
                    ) AS privilege
                    WHERE namespace.nspname = 'agri'
                      AND privilege.grantee = 0
                      AND privilege.privilege_type = 'CREATE'
                ),
                has_schema_privilege(%s, 'agri', 'CREATE')
                """,
                (GUARD_OWNER,),
            )
            public_can_create, owner_can_create = cursor.fetchone()
            assert public_can_create is False
            assert owner_can_create is False

            suffix = hashlib.sha256(os.urandom(16)).hexdigest()[:12]
            cursor.execute(
                """
                INSERT INTO agri.release_set(logical_key, as_of_time, manifest_checksum, state)
                VALUES (%s, clock_timestamp(), %s, 'draft')
                RETURNING id
                """,
                (
                    f"intervention-parent-guard-{suffix}",
                    hashlib.sha256(f"intervention-parent-guard:{suffix}".encode()).hexdigest(),
                ),
            )
            release_set_id = cursor.fetchone()[0]

            cursor.execute("GRANT USAGE ON SCHEMA agri TO plantgeo_loader")
            cursor.execute("GRANT SELECT ON agri.release_set TO plantgeo_loader")
            cursor.execute(
                "GRANT UPDATE (state, validated_at) ON agri.release_set TO plantgeo_loader"
            )
            for table_name in INTERVENTION_TABLES:
                cursor.execute(f"REVOKE ALL PRIVILEGES ON TABLE agri.{table_name} FROM plantgeo_loader")

            cursor.execute("SET LOCAL ROLE plantgeo_loader")
            cursor.execute("SELECT current_user")
            assert cursor.fetchone()[0] == "plantgeo_loader"
            cursor.execute(
                """
                SELECT table_name, privilege_name
                FROM unnest(%s::text[]) AS requested_table(table_name)
                CROSS JOIN unnest(%s::text[]) AS requested_privilege(privilege_name)
                WHERE has_table_privilege(
                    current_user,
                    format('agri.%%I', table_name),
                    privilege_name
                )
                """,
                (list(INTERVENTION_TABLES), list(TABLE_PRIVILEGES)),
            )
            assert cursor.fetchall() == []
            cursor.execute(
                """
                SELECT count(*)
                FROM information_schema.column_privileges
                WHERE grantee = current_user
                  AND table_schema = 'agri'
                  AND table_name = ANY(%s::text[])
                """,
                (list(INTERVENTION_TABLES),),
            )
            assert cursor.fetchone()[0] == 0
            cursor.execute(
                """
                SELECT has_function_privilege(
                    current_user,
                    'agri.protect_intervention_evidence_parents()',
                    'EXECUTE'
                )
                """
            )
            assert cursor.fetchone()[0] is False

            cursor.execute(
                """
                UPDATE agri.release_set
                SET state = 'validated', validated_at = clock_timestamp()
                WHERE id = %s
                """,
                (release_set_id,),
            )
            assert cursor.rowcount == 1

            cursor.execute("RESET ROLE")
            cursor.execute(
                "SELECT state, validated_at IS NOT NULL FROM agri.release_set WHERE id = %s",
                (release_set_id,),
            )
            assert cursor.fetchone() == ("validated", True)
    finally:
        connection.rollback()
        connection.close()
