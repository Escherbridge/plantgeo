"""Real-Postgres proof that every SECURITY DEFINER function in ``agri`` is locked down.

Companion to the 0012/0015 migration-contract tests, which only check migration
text. This asserts, against a real head-migrated database, that *every*
``agri`` SECURITY DEFINER function (not just the eleven this revision touches)
has: a non-PUBLIC execute grant, the expected dedicated NOLOGIN owner, and a
pinned ``search_path``. A newly discovered SECURITY DEFINER function that is
missing from ``_EXPECTED_OWNER_BY_FUNCTION`` fails loudly instead of being
silently skipped, so the inventory in this test is the enforcement mechanism
against future drift.
"""

import psycopg2
import pytest

_INTERVENTION_GUARD_OWNER = "plantgeo_intervention_guard_owner"
_INPUT_RECORDER_OWNER = "plantgeo_forecast_input_recorder_owner"
_MV_REFRESH_OWNER = "plantgeo_forecast_mv_refresh_owner"
_LINEAGE_GUARD_OWNER = "plantgeo_release_lineage_guard_owner"

# function_name -> expected owner role. Every SECURITY DEFINER function found
# in the `agri` schema must appear here exactly once (see
# test_every_security_definer_function_is_in_the_locked_inventory).
_EXPECTED_OWNER_BY_FUNCTION: dict[str, str] = {
    "protect_intervention_evidence_parents": _INTERVENTION_GUARD_OWNER,
    "record_forecast_release_set_item_insert": _INPUT_RECORDER_OWNER,
    "record_forecast_release_set_item_update": _INPUT_RECORDER_OWNER,
    "record_forecast_release_set_item_delete": _INPUT_RECORDER_OWNER,
    "record_forecast_release_content_insert": _INPUT_RECORDER_OWNER,
    "record_forecast_release_content_update": _INPUT_RECORDER_OWNER,
    "record_forecast_release_content_delete": _INPUT_RECORDER_OWNER,
    "record_forecast_input_change": _INPUT_RECORDER_OWNER,
    "refresh_forecast_ml_daily_serving": _MV_REFRESH_OWNER,
    "enforce_intervention_lineage_release_membership": _LINEAGE_GUARD_OWNER,
    "enforce_normalized_feature_artifact_release": _LINEAGE_GUARD_OWNER,
    "enforce_intervention_analysis_release_set": _LINEAGE_GUARD_OWNER,
}

_SECURITY_DEFINER_FUNCTIONS_SQL = """
    SELECT
        procedure.proname AS function_name,
        procedure.oid::regprocedure::text AS signature,
        owner.rolname AS owner_name,
        owner.rolcanlogin,
        owner.rolsuper,
        owner.rolinherit,
        procedure.proconfig,
        has_function_privilege('public', procedure.oid, 'EXECUTE') AS public_can_execute
    FROM pg_catalog.pg_proc AS procedure
    INNER JOIN pg_catalog.pg_namespace AS namespace
        ON namespace.oid = procedure.pronamespace
    INNER JOIN pg_catalog.pg_roles AS owner
        ON owner.oid = procedure.proowner
    WHERE namespace.nspname = 'agri'
      AND procedure.prosecdef
    ORDER BY procedure.proname
"""


@pytest.fixture
def security_definer_rows(agri_db_connection: psycopg2.extensions.connection) -> list[dict]:
    with agri_db_connection.cursor() as cursor:
        cursor.execute(_SECURITY_DEFINER_FUNCTIONS_SQL)
        columns = [column.name for column in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def test_every_security_definer_function_is_in_the_locked_inventory(
    security_definer_rows: list[dict],
) -> None:
    discovered = {row["function_name"] for row in security_definer_rows}
    expected = set(_EXPECTED_OWNER_BY_FUNCTION)

    missing_from_inventory = discovered - expected
    assert not missing_from_inventory, (
        f"undeclared SECURITY DEFINER function(s) found in agri: {missing_from_inventory}; "
        "add them to _EXPECTED_OWNER_BY_FUNCTION with their intended locked owner"
    )
    missing_from_database = expected - discovered
    assert not missing_from_database, (
        f"expected SECURITY DEFINER function(s) not found (renamed/dropped?): {missing_from_database}"
    )


def test_every_security_definer_function_has_its_expected_locked_owner(
    security_definer_rows: list[dict],
) -> None:
    for row in security_definer_rows:
        expected_owner = _EXPECTED_OWNER_BY_FUNCTION[row["function_name"]]
        assert row["owner_name"] == expected_owner, row["function_name"]


def test_every_security_definer_owner_is_a_locked_nologin_non_superuser_role(
    security_definer_rows: list[dict],
) -> None:
    for row in security_definer_rows:
        assert row["rolcanlogin"] is False, row["function_name"]
        assert row["rolsuper"] is False, row["function_name"]
        assert row["rolinherit"] is False, row["function_name"]


def test_every_security_definer_function_revokes_public_execute(
    security_definer_rows: list[dict],
) -> None:
    for row in security_definer_rows:
        assert row["public_can_execute"] is False, row["function_name"]


def test_every_security_definer_function_pins_its_search_path(
    security_definer_rows: list[dict],
) -> None:
    for row in security_definer_rows:
        proconfig = row["proconfig"] or []
        search_path_settings = [setting for setting in proconfig if setting.startswith("search_path=")]
        assert search_path_settings, f"{row['function_name']} has no pinned search_path"
        (search_path_setting,) = search_path_settings
        assert "pg_catalog" in search_path_setting, row["function_name"]
        assert "agri" in search_path_setting, row["function_name"]


def test_owner_roles_have_no_role_memberships(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    with agri_db_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT role.rolname
            FROM pg_catalog.pg_roles AS role
            WHERE role.rolname = ANY(%s)
              AND (
                  EXISTS (
                      SELECT 1 FROM pg_catalog.pg_auth_members AS membership
                      WHERE membership.roleid = role.oid OR membership.member = role.oid
                  )
              )
            """,
            (list(set(_EXPECTED_OWNER_BY_FUNCTION.values())),),
        )
        offenders = [row[0] for row in cursor.fetchall()]
    assert not offenders, f"locked owner role(s) with role memberships: {offenders}"


def test_mv_refresh_owner_also_owns_the_materialized_view_it_refreshes(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    with agri_db_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT owner.rolname
            FROM pg_catalog.pg_class AS class
            INNER JOIN pg_catalog.pg_namespace AS namespace
                ON namespace.oid = class.relnamespace
            INNER JOIN pg_catalog.pg_roles AS owner
                ON owner.oid = class.relowner
            WHERE namespace.nspname = 'agri'
              AND class.relname = 'mv_forecast_ml_daily_serving'
            """
        )
        row = cursor.fetchone()
    assert row is not None, "agri.mv_forecast_ml_daily_serving not found"
    assert row[0] == _MV_REFRESH_OWNER
