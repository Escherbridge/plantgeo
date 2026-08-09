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
privilege widening that revision's docstring accepts. Revision
``20260808_0019`` did the same for the last one: no ``agri`` function has a
dedicated NOLOGIN owner any more, because there are no dedicated roles left.
``refresh_forecast_ml_daily_serving`` stays SECURITY DEFINER and must still own
the matview it non-concurrently refreshes -- what changed is that both now
belong to the owner credential, so the invariant this file asserts is that the
two share an owner, not which role that owner is.
"""

import psycopg2
import pytest

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

# Every SECURITY DEFINER function found in `agri` must appear here exactly once
# (see test_every_security_definer_function_is_in_the_locked_inventory). After 0018 the
# refresher is the only one left: it must stay SECURITY DEFINER because a non-concurrent
# REFRESH requires matview ownership.
_EXPECTED_FUNCTIONS = frozenset({"refresh_forecast_ml_daily_serving"})

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
        "add them to _EXPECTED_FUNCTIONS and say in this file why they need the definer bit"
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


def test_no_agri_function_is_owned_by_a_retired_forecast_role(
    security_definer_rows: list[dict],
) -> None:
    """20260808_0019 reassigned the last one; a reappearance means the family was re-created."""
    for row in security_definer_rows:
        assert not row["owner_name"].startswith("plantgeo_forecast_"), (
            f"{row['function_name']} is owned by {row['owner_name']}, a role 20260808_0019 retired"
        )


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


def test_no_forecast_capability_role_holds_anything_in_this_database(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    """20260808_0019's guarantee is database-local, so the assertion must be too.

    ``pg_roles`` is a shared catalog: a retired role legitimately survives the DROP with a
    NOTICE while any sibling database on the cluster still carries its grants, so asserting
    global absence fails against exactly the clusters the migration documents. What the
    revision does guarantee here is that no such role owns an object or holds a grant in
    *this* database -- ``pg_shdepend`` records precisely those two dependency kinds per
    database, so an empty result is the teardown, database-locally complete.
    """
    with agri_db_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT role.rolname
            FROM pg_catalog.pg_roles role
            WHERE role.rolname LIKE 'plantgeo\\_forecast\\_%'
              AND EXISTS (
                  SELECT 1
                  FROM pg_catalog.pg_shdepend dependency
                  JOIN pg_catalog.pg_database database_row
                    ON database_row.oid = dependency.dbid
                  WHERE dependency.refobjid = role.oid
                    AND database_row.datname = current_database()
              )
            ORDER BY role.rolname
            """
        )
        survivors = [row[0] for row in cursor.fetchall()]
    assert not survivors, f"retired forecast capability role(s) still own or hold grants here: {survivors}"


def test_the_refresher_still_owns_the_materialized_view_it_refreshes(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    """A non-concurrent REFRESH requires matview ownership, so definer and matview owner must match.

    The role that holds both changed in 20260808_0019 and is now whichever credential ran the
    migration, so this asserts the relationship rather than a name.
    """
    with agri_db_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT matview_owner.rolname, function_owner.rolname
            FROM pg_catalog.pg_class AS class
            INNER JOIN pg_catalog.pg_namespace AS namespace
                ON namespace.oid = class.relnamespace
            INNER JOIN pg_catalog.pg_roles AS matview_owner
                ON matview_owner.oid = class.relowner
            CROSS JOIN pg_catalog.pg_proc AS procedure
            INNER JOIN pg_catalog.pg_roles AS function_owner
                ON function_owner.oid = procedure.proowner
            WHERE namespace.nspname = 'agri'
              AND class.relname = 'mv_forecast_ml_daily_serving'
              AND procedure.pronamespace = namespace.oid
              AND procedure.proname = 'refresh_forecast_ml_daily_serving'
            """
        )
        row = cursor.fetchone()
    assert row is not None, "agri.mv_forecast_ml_daily_serving or its refresher not found"
    matview_owner, function_owner = row
    assert matview_owner == function_owner
    assert not matview_owner.startswith("plantgeo_forecast_")
