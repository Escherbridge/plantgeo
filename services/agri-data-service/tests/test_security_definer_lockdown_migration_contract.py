"""Static contracts for the 0015 SECURITY DEFINER lockdown-completion revision.

Companion to ``test_intervention_parent_guard_migration_contract.py`` (the 0012
contract for the one function that was already locked down). This file checks
the migration text only; ``test_security_definer_lockdown_postgresql.py``
proves the resulting grants/owners/search_path against a real database.
"""

from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
_VERSIONS_DIR = _SERVICE_ROOT / "alembic" / "versions"

_INPUT_RECORDER_OWNER = "plantgeo_forecast_input_recorder_owner"
_MV_REFRESH_OWNER = "plantgeo_forecast_mv_refresh_owner"
_LINEAGE_GUARD_OWNER = "plantgeo_release_lineage_guard_owner"

_INPUT_RECORDER_FUNCTIONS = (
    "record_forecast_release_set_item_insert",
    "record_forecast_release_set_item_update",
    "record_forecast_release_set_item_delete",
    "record_forecast_release_content_insert",
    "record_forecast_release_content_update",
    "record_forecast_release_content_delete",
    "record_forecast_input_change",
)
_LINEAGE_GUARD_FUNCTIONS = (
    "enforce_intervention_lineage_release_membership",
    "enforce_normalized_feature_artifact_release",
    "enforce_intervention_analysis_release_set",
)
_ALL_LOCKED_FUNCTIONS = (*_INPUT_RECORDER_FUNCTIONS, "refresh_forecast_ml_daily_serving", *_LINEAGE_GUARD_FUNCTIONS)
# SECURITY DEFINER functions left unlocked by 0012, closed by 0015.
_EXPECTED_LOCKED_FUNCTION_COUNT = 11


def _migration() -> str:
    matches = sorted(_VERSIONS_DIR.glob("*_0015_*.py"))
    assert len(matches) == 1, f"expected exactly one 0015 migration file, found {matches}"
    return matches[0].read_text(encoding="utf-8")


def test_revision_is_forward_only_and_chained_to_0014() -> None:
    migration = _migration()

    assert 'revision = "20260802_0015"' in migration
    assert 'down_revision = "20260801_0014"' in migration
    assert "raise NotImplementedError" in migration
    assert "DROP FUNCTION" not in migration
    assert "CREATE FUNCTION" not in migration


def test_no_function_body_is_touched_only_ownership_and_grants_move() -> None:
    migration = _migration()

    # No CREATE OR REPLACE / load_object_sql: this revision changes only
    # ownership and grants, never a function body (db/AGENTS.md forward-load
    # workflow is for body changes, which this is not).
    assert "load_object_sql" not in migration
    assert "CREATE OR REPLACE FUNCTION" not in migration


def test_exactly_eleven_functions_are_covered() -> None:
    assert len(_ALL_LOCKED_FUNCTIONS) == _EXPECTED_LOCKED_FUNCTION_COUNT
    assert len(set(_ALL_LOCKED_FUNCTIONS)) == _EXPECTED_LOCKED_FUNCTION_COUNT


def test_all_eleven_functions_get_a_locked_owner_and_public_revoke() -> None:
    migration = _migration()

    for function_name in _ALL_LOCKED_FUNCTIONS:
        assert f"REVOKE EXECUTE ON FUNCTION agri.{function_name}() FROM PUBLIC;" in migration, function_name
        assert f"ALTER FUNCTION agri.{function_name}()\n            OWNER TO" in migration, function_name


def test_input_recorder_functions_are_owned_by_the_input_recorder_role() -> None:
    migration = _migration()

    for function_name in _INPUT_RECORDER_FUNCTIONS:
        assert (
            f"ALTER FUNCTION agri.{function_name}()\n            OWNER TO {_INPUT_RECORDER_OWNER};"
        ) in migration


def test_mv_refresh_function_owns_the_matview_it_refreshes() -> None:
    migration = _migration()

    assert (
        "ALTER FUNCTION agri.refresh_forecast_ml_daily_serving()\n"
        f"            OWNER TO {_MV_REFRESH_OWNER};"
    ) in migration
    assert (
        "ALTER MATERIALIZED VIEW agri.mv_forecast_ml_daily_serving\n"
        f"            OWNER TO {_MV_REFRESH_OWNER};"
    ) in migration


def test_lineage_guard_functions_are_owned_by_the_lineage_guard_role() -> None:
    migration = _migration()

    for function_name in _LINEAGE_GUARD_FUNCTIONS:
        assert (
            f"ALTER FUNCTION agri.{function_name}()\n            OWNER TO {_LINEAGE_GUARD_OWNER};"
        ) in migration


def test_owner_roles_are_created_with_locked_attributes_and_validated() -> None:
    migration = _migration()

    for role_name in (_INPUT_RECORDER_OWNER, _MV_REFRESH_OWNER, _LINEAGE_GUARD_OWNER):
        assert f"CREATE ROLE {role_name}" in migration
        assert f"REVOKE CREATE ON SCHEMA agri\n            FROM {role_name};" in migration
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


def test_mv_refresher_capability_grant_is_conditional_on_role_existing() -> None:
    migration = _migration()

    assert "IF EXISTS (\n                SELECT 1 FROM pg_catalog.pg_roles" in migration
    assert "WHERE rolname = 'plantgeo_forecast_mv_refresher'" in migration
    assert (
        "GRANT EXECUTE ON FUNCTION agri.refresh_forecast_ml_daily_serving()\n"
        "                    TO plantgeo_forecast_mv_refresher;"
    ) in migration
