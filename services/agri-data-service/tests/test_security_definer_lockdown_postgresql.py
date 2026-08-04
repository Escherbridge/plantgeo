"""Real-Postgres proof that every SECURITY DEFINER function in ``agri`` is locked down.

Asserts, against a real head-migrated database, that *every* ``agri``
SECURITY DEFINER function has a non-PUBLIC execute grant and a pinned
``search_path``, and that the one function still owned by a dedicated role
keeps it. A newly discovered SECURITY DEFINER function missing from
``_EXPECTED_FUNCTIONS`` fails loudly instead of being silently skipped, so the
inventory in this test is the enforcement mechanism against future drift.

Revision ``20260803_0018`` retired the lineage guards and reassigned the seven
``record_*`` writers away from ``plantgeo_forecast_input_recorder_owner``; they
are kept SECURITY DEFINER but now run as the migrating role, which is the
privilege widening that revision's docstring accepts. Only
``refresh_forecast_ml_daily_serving`` still has a locked NOLOGIN owner, because
it must own the matview it non-concurrently refreshes.
"""

import psycopg2
import pytest

_MV_REFRESH_OWNER = "plantgeo_forecast_mv_refresh_owner"

# The seven input recorders. SECURITY INVOKER since 0018: they were SECURITY DEFINER only so
# a restricted role could append to agri.forecast_input_recorded_at without holding INSERT on
# it, owned by a locked NOLOGIN role that 0018 retires. Reassigning them would have widened
# their privileges rather than preserved them, so the definer bit was dropped instead.
_INPUT_RECORDER_FUNCTIONS = frozenset(
    {
        "record_forecast_release_set_item_insert",
        "record_forecast_release_set_item_update",
        "record_forecast_release_set_item_delete",
        "record_forecast_release_content_insert",
        "record_forecast_release_content_update",
        "record_forecast_release_content_delete",
        "record_forecast_input_change",
    }
)

# function_name -> the dedicated NOLOGIN role that must still own it.
_EXPECTED_LOCKED_OWNER_BY_FUNCTION: dict[str, str] = {
    "refresh_forecast_ml_daily_serving": _MV_REFRESH_OWNER,
}

# Every SECURITY DEFINER function found in `agri` must appear here exactly once
# (see test_every_security_definer_function_is_in_the_locked_inventory). After 0018 the
# refresher is the only one left: it must stay SECURITY DEFINER because a non-concurrent
# REFRESH requires matview ownership.
_EXPECTED_FUNCTIONS = frozenset(_EXPECTED_LOCKED_OWNER_BY_FUNCTION)

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

    missing_from_inventory = discovered - _EXPECTED_FUNCTIONS
    assert not missing_from_inventory, (
        f"undeclared SECURITY DEFINER function(s) found in agri: {missing_from_inventory}; "
        "add them to _EXPECTED_FUNCTIONS, and to _EXPECTED_LOCKED_OWNER_BY_FUNCTION if "
        "they are meant to keep a dedicated NOLOGIN owner"
    )
    missing_from_database = _EXPECTED_FUNCTIONS - discovered
    assert not missing_from_database, (
        f"expected SECURITY DEFINER function(s) not found (renamed/dropped?): {missing_from_database}"
    )


def test_input_recorders_exist_and_run_as_the_invoker(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    """All seven recorders survive 0018 and none of them carries the definer bit."""
    with agri_db_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT procedure.proname, procedure.prosecdef
            FROM pg_catalog.pg_proc AS procedure
            INNER JOIN pg_catalog.pg_namespace AS namespace
                ON namespace.oid = procedure.pronamespace
            WHERE namespace.nspname = 'agri'
              AND procedure.proname = ANY(%s)
            """,
            (sorted(_INPUT_RECORDER_FUNCTIONS),),
        )
        found = dict(cursor.fetchall())

    missing = _INPUT_RECORDER_FUNCTIONS - found.keys()
    assert not missing, (
        f"input recorder(s) missing: {missing}. They feed agri.forecast_input_recorded_at, which "
        "v_forecast_timeseries_contract INNER JOINs and forecast_daily_bootstrap RAISEs on."
    )
    still_definer = {name for name, prosecdef in found.items() if prosecdef}
    assert not still_definer, f"input recorder(s) still SECURITY DEFINER after 0018: {still_definer}"


def test_owner_locked_functions_keep_their_dedicated_nologin_owner(
    security_definer_rows: list[dict],
) -> None:
    for row in security_definer_rows:
        expected_owner = _EXPECTED_LOCKED_OWNER_BY_FUNCTION.get(row["function_name"])
        if expected_owner is None:
            continue
        assert row["owner_name"] == expected_owner, row["function_name"]
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


def test_locked_owner_roles_have_no_role_memberships(
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
            (sorted(set(_EXPECTED_LOCKED_OWNER_BY_FUNCTION.values())),),
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
