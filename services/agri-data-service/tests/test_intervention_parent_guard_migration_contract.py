"""Static contracts for the 0012 intervention-parent guard hardening."""

from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
_VERSIONS_DIR = _SERVICE_ROOT / "alembic" / "versions"
_CANONICAL_FUNCTION = (
    _SERVICE_ROOT / "db" / "agri" / "functions" / "protect_intervention_evidence_parents.sql"
)
_GUARD_OWNER = "plantgeo_intervention_guard_owner"


def _migration() -> str:
    matches = sorted(_VERSIONS_DIR.glob("*_0012_*.py"))
    assert len(matches) == 1, f"expected exactly one 0012 migration file, found {matches}"
    return matches[0].read_text(encoding="utf-8")


def test_revision_is_forward_only_and_chained_to_0011() -> None:
    migration = _migration()

    assert 'revision = "20260725_0012"' in migration
    assert 'down_revision = "20260725_0011"' in migration
    assert "raise NotImplementedError" in migration
    assert "DROP FUNCTION" not in migration


def test_upgrade_forward_loads_the_canonical_function_definition() -> None:
    migration = _migration()

    assert (
        'load_object_sql("functions/protect_intervention_evidence_parents.sql", or_replace=True)'
        in migration
    )
    assert "CREATE FUNCTION agri.protect_intervention_evidence_parents()" not in migration
    assert "CREATE OR REPLACE FUNCTION" not in migration


def test_guard_is_security_definer_with_a_locked_search_path() -> None:
    function_sql = _CANONICAL_FUNCTION.read_text(encoding="utf-8")

    assert "LANGUAGE plpgsql SECURITY DEFINER" in function_sql
    assert "SET search_path TO 'pg_catalog', 'agri'" in function_sql
    assert "agri.intervention_analysis_run" in function_sql
    assert "agri.intervention_evidence_input" in function_sql


def test_upgrade_uses_an_isolated_locked_function_owner() -> None:
    migration = _migration()

    assert f"CREATE ROLE {_GUARD_OWNER}" in migration
    for locked_attribute in (
        "NOLOGIN",
        "NOSUPERUSER",
        "NOCREATEDB",
        "NOCREATEROLE",
        "NOREPLICATION",
        "NOBYPASSRLS",
        "NOINHERIT",
    ):
        assert locked_attribute in migration
    for checked_attribute in (
        "rolcanlogin",
        "rolsuper",
        "rolcreatedb",
        "rolcreaterole",
        "rolreplication",
        "rolbypassrls",
        "rolinherit",
    ):
        assert checked_attribute in migration
    assert "pg_auth_members" in migration
    assert (
        "ALTER FUNCTION agri.protect_intervention_evidence_parents()\n"
        f"            OWNER TO {_GUARD_OWNER};"
    ) in migration
    assert (
        "GRANT UPDATE (id) ON TABLE agri.release_set\n"
        f"            TO {_GUARD_OWNER};"
    ) in migration


def test_direct_execution_and_schema_creation_remain_revoked() -> None:
    migration = _migration()

    revoke_public_execute = (
        "REVOKE EXECUTE ON FUNCTION\n"
        "            agri.protect_intervention_evidence_parents()\n"
        "        FROM PUBLIC;"
    )
    grant_create = f"GRANT USAGE, CREATE ON SCHEMA agri\n            TO {_GUARD_OWNER};"
    alter_owner = (
        "ALTER FUNCTION agri.protect_intervention_evidence_parents()\n"
        f"            OWNER TO {_GUARD_OWNER};"
    )
    revoke_owner_create = f"REVOKE CREATE ON SCHEMA agri\n            FROM {_GUARD_OWNER};"

    assert revoke_public_execute in migration
    assert "REVOKE CREATE ON SCHEMA agri FROM PUBLIC;" in migration
    assert migration.index(grant_create) < migration.index(alter_owner) < migration.index(
        revoke_owner_create
    )
    assert "GRANT EXECUTE" not in migration
