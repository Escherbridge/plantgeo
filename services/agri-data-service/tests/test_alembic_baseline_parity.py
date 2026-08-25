"""The greenfield baseline actually builds what the declarative tree declares, on a real database.

Set ``AGRI_TEST_DATABASE_URL`` to a disposable database migrated to head; see ``db/AGENTS.md``
(*Provisioning the disposable database*) and ``tests/conftest.py``.

WHAT THIS ADDS OVER `test_declarative_schema_parity`. That module dumps the migrated database with
``pg_dump --no-owner --no-privileges`` and compares the text to ``db/agri/**``. It therefore cannot
see the two things a collapsed history is most likely to lose:

* **Whether every object was built at all**, as opposed to whether the ones that were built have the
  right DDL text. The checks below walk ``db/manifest.sql`` and look each object up in the
  catalogue, so an object file the baseline silently skipped is a failure here and invisible there.
* **The privilege layer**, which ``--no-privileges`` discards by construction. The archived chain's
  ``REVOKE ... FROM PUBLIC`` statements are re-applied by the baseline; nothing else proves they ran.

It also pins the extension set, because a database carrying ``timescaledb`` is the exact state the
collapse exists to stop a fresh build from reaching.
"""

from __future__ import annotations

from pathlib import Path

import psycopg2.errors
import psycopg2.extensions
import pytest

from agri_data_service.routes.health.contracts import EXPECTED_ALEMBIC_REVISION, REQUIRED_EXTENSIONS
from tests.test_alembic_baseline_contract import baseline_module

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
_DB_ROOT = _SERVICE_ROOT / "db"

# Object-file directory -> the pg_class relkind (or pg_proc prokind) a file in it must produce.
_RELKIND_BY_DIRECTORY = {"tables": ("r", "p"), "views": ("v",), "materialized_views": ("m",), "sequences": ("S",)}
_PROKIND_BY_DIRECTORY = {"functions": ("f", "a", "w"), "procedures": ("p",)}


def _manifest_objects() -> list[tuple[str, str]]:
    """``(directory, object name)`` for every file ``db/manifest.sql`` includes, in manifest order."""
    manifest = (_DB_ROOT / "manifest.sql").read_text(encoding="utf-8")
    objects = []
    for line in manifest.splitlines():
        if not line.startswith("\\i "):
            continue
        relative = Path(line.split(maxsplit=1)[1].strip())
        objects.append((relative.parent.name, relative.stem))
    return objects


def _fetch(connection: psycopg2.extensions.connection, sql: str) -> list[tuple]:
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return cursor.fetchall()


def test_the_database_is_at_the_baseline_revision_and_no_other(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    """A collapsed history means one legal `alembic_version` value; anything else is a stale database."""
    ((revision,),) = _fetch(agri_db_connection, "SELECT version_num FROM public.alembic_version")
    assert revision == EXPECTED_ALEMBIC_REVISION


def test_every_relation_the_manifest_declares_exists_with_the_right_relkind(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    """Tables, views, materialized views and sequences, by name and kind."""
    by_name = dict(
        _fetch(
            agri_db_connection,
            "SELECT relname, relkind FROM pg_class "
            "WHERE relnamespace = 'agri'::regnamespace AND relkind IN ('r', 'p', 'v', 'm', 'S')",
        )
    )

    missing = []
    wrong_kind = []
    for directory, object_name in _manifest_objects():
        expected = _RELKIND_BY_DIRECTORY.get(directory)
        if expected is None:
            continue
        if object_name not in by_name:
            missing.append(f"{directory}/{object_name}")
        elif by_name[object_name] not in expected:
            wrong_kind.append(f"{directory}/{object_name} is relkind {by_name[object_name]!r}, expected {expected}")

    assert not missing, f"the baseline did not build {len(missing)} declared relation(s): {sorted(missing)[:10]}"
    assert not wrong_kind, f"relkind mismatch: {sorted(wrong_kind)[:10]}"


def test_every_routine_the_manifest_declares_exists_with_the_right_prokind(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    """Functions and procedures. Overloads collapse to one name, which is what the tree stores."""
    built: dict[str, set[str]] = {}
    for name, prokind in _fetch(
        agri_db_connection, "SELECT proname, prokind FROM pg_proc WHERE pronamespace = 'agri'::regnamespace"
    ):
        built.setdefault(name, set()).add(prokind)

    missing = []
    wrong_kind = []
    for directory, object_name in _manifest_objects():
        expected = _PROKIND_BY_DIRECTORY.get(directory)
        if expected is None:
            continue
        if object_name not in built:
            missing.append(f"{directory}/{object_name}")
        elif not built[object_name] & set(expected):
            wrong_kind.append(f"{directory}/{object_name} is prokind {sorted(built[object_name])}, expected {expected}")

    assert not missing, f"the baseline did not build {len(missing)} declared routine(s): {sorted(missing)[:10]}"
    assert not wrong_kind, f"prokind mismatch: {sorted(wrong_kind)[:10]}"


def test_every_declared_trigger_and_foreign_key_file_produced_catalogue_rows(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    """These two directories are keyed by TABLE, not by object, so the check is `at least one`."""
    tables_with_triggers = {
        name
        for (name,) in _fetch(
            agri_db_connection,
            "SELECT DISTINCT cls.relname FROM pg_trigger trg JOIN pg_class cls ON cls.oid = trg.tgrelid "
            "WHERE cls.relnamespace = 'agri'::regnamespace AND NOT trg.tgisinternal",
        )
    }
    tables_with_foreign_keys = {
        name
        for (name,) in _fetch(
            agri_db_connection,
            "SELECT DISTINCT conrelid::regclass::text FROM pg_constraint "
            "WHERE connamespace = 'agri'::regnamespace AND contype = 'f'",
        )
    }
    tables_with_foreign_keys = {name.removeprefix("agri.") for name in tables_with_foreign_keys}

    declared_triggers = {name for directory, name in _manifest_objects() if directory == "triggers"}
    declared_foreign_keys = {name for directory, name in _manifest_objects() if directory == "foreign_keys"}

    assert not (declared_triggers - tables_with_triggers), (
        f"declared trigger file(s) produced no trigger: {sorted(declared_triggers - tables_with_triggers)}"
    )
    assert not (declared_foreign_keys - tables_with_foreign_keys), (
        "declared foreign-key file(s) produced no constraint: "
        f"{sorted(declared_foreign_keys - tables_with_foreign_keys)}"
    )


def test_timescaledb_is_absent_and_the_required_extensions_are_present(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    """The state the collapse exists to guarantee: a fresh build never installs what 0026 dropped."""
    installed = {name for (name,) in _fetch(agri_db_connection, "SELECT extname FROM pg_extension")}

    assert not (set(REQUIRED_EXTENSIONS) - installed), (
        f"missing required extension(s): {sorted(set(REQUIRED_EXTENSIONS) - installed)}"
    )
    assert "timescaledb" not in installed
    assert "timescaledb_toolkit" not in installed

    leftover_schemas = {
        name
        for (name,) in _fetch(
            agri_db_connection,
            "SELECT nspname FROM pg_namespace WHERE nspname LIKE '%timescaledb%' OR nspname LIKE '\\_timescaledb%'",
        )
    }
    assert not leftover_schemas, f"TimescaleDB catalogue schemas present: {sorted(leftover_schemas)}"


def _check_invoked_agri_routines(connection: psycopg2.extensions.connection) -> list[str]:
    """Every `agri` routine some `agri` CHECK constraint calls, as a GRANT-ready identity signature."""
    return [
        signature
        for (signature,) in _fetch(
            connection,
            "SELECT DISTINCT format('%I.%I(%s)', 'agri', routine.proname, "
            "pg_get_function_identity_arguments(routine.oid)) "
            "FROM pg_constraint constraint_row "
            "JOIN pg_depend dependency ON dependency.objid = constraint_row.oid "
            "AND dependency.classid = 'pg_constraint'::regclass "
            "JOIN pg_proc routine ON routine.oid = dependency.refobjid "
            "AND dependency.refclassid = 'pg_proc'::regclass "
            "WHERE constraint_row.contype = 'c' AND constraint_row.connamespace = 'agri'::regnamespace "
            "AND routine.pronamespace = 'agri'::regnamespace ORDER BY 1",
        )
    ]


def _insert_one_candidate_evaluation(connection: psycopg2.extensions.connection, evaluation_key: str) -> None:
    """One valid row whose `receipt_checksum` CHECK re-derives the digest by calling an `agri` function."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT agri.forecast_candidate_evaluation_receipt_checksum("
            "%s, 'series', 'family', 'v1', '{}'::jsonb, 1::bigint, repeat('a', 64), 1, 0, 0, 'baseline')",
            (evaluation_key,),
        )
        ((receipt_checksum,),) = cursor.fetchall()
        cursor.execute("SET LOCAL ROLE agri_check_constraint_probe")
        cursor.execute(
            "INSERT INTO agri.forecast_candidate_evaluation ("
            "evaluation_key, series_key, candidate_family, candidate_version, hyperparameters, "
            "simulation_seed, export_manifest_checksum, horizon_steps, development_origin_count, "
            "final_holdout_origin_count, metrics, decision, decision_reason, receipt_checksum) "
            "VALUES (%s, 'series', 'family', 'v1', '{}'::jsonb, 1, repeat('a', 64), 1, 0, 0, "
            "'{}'::jsonb, 'baseline', 'probe', %s)",
            (evaluation_key, receipt_checksum),
        )
        cursor.execute("RESET ROLE")


def test_a_non_owner_writer_can_satisfy_every_check_constraint_that_calls_a_function(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    """A CHECK is evaluated with the WRITER's privileges, so the blanket REVOKE locks writers out.

    Rolled back, so the probe role and the row never outlive the test. The role is created here
    rather than assumed because the five operator roles are provisioned outside Alembic and a
    disposable test database has none of them -- what is under test is the baseline's grant BLOCK,
    replayed verbatim, not whether this particular database happened to have a role at apply time.
    """
    baseline = baseline_module()

    with agri_db_connection.cursor() as cursor:
        cursor.execute("CREATE ROLE agri_check_constraint_probe NOLOGIN")
        cursor.execute("GRANT USAGE ON SCHEMA agri TO agri_check_constraint_probe")
        cursor.execute("GRANT INSERT ON agri.forecast_candidate_evaluation TO agri_check_constraint_probe")

    # Without the grant block the same INSERT is refused -- this is the regression, not a hypothetical.
    with pytest.raises(psycopg2.errors.InsufficientPrivilege) as refusal:
        _insert_one_candidate_evaluation(agri_db_connection, "check-probe-before-grant")
    assert "permission denied for function" in str(refusal.value)
    agri_db_connection.rollback()

    with agri_db_connection.cursor() as cursor:
        cursor.execute("CREATE ROLE agri_check_constraint_probe NOLOGIN")
        cursor.execute("GRANT USAGE ON SCHEMA agri TO agri_check_constraint_probe")
        cursor.execute("GRANT INSERT ON agri.forecast_candidate_evaluation TO agri_check_constraint_probe")
        # The baseline grants to operator-provisioned roles only if they exist; alias the probe in.
        cursor.execute(
            baseline._CHECK_CONSTRAINT_EXECUTE_GRANTS_SQL.replace(
                "'plantgeo_local_developer'", "'agri_check_constraint_probe'"
            )
        )

    _insert_one_candidate_evaluation(agri_db_connection, "check-probe-after-grant")

    granted = {
        signature
        for (signature,) in _fetch(
            agri_db_connection,
            "SELECT format('%I.%I(%s)', 'agri', proname, pg_get_function_identity_arguments(oid)) "
            "FROM pg_proc WHERE pronamespace = 'agri'::regnamespace "
            "AND has_function_privilege('agri_check_constraint_probe', oid, 'EXECUTE')",
        )
    }
    check_invoked = _check_invoked_agri_routines(agri_db_connection)
    assert check_invoked, "no agri CHECK constraint calls an agri function; this test has lost its subject"
    assert not set(check_invoked) - granted, (
        f"CHECK-invoked routine(s) a non-owner writer still cannot execute: {sorted(set(check_invoked) - granted)}"
    )
    agri_db_connection.rollback()


def test_public_holds_no_privilege_anywhere_in_the_agri_schema(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    """The layer `pg_dump --no-privileges` discards, so only a live catalogue read can prove it ran."""
    ((schema_usage, schema_create),) = _fetch(
        agri_db_connection,
        "SELECT has_schema_privilege('public', 'agri', 'USAGE'), has_schema_privilege('public', 'agri', 'CREATE')",
    )
    assert not schema_usage, "PUBLIC has USAGE on schema agri"
    assert not schema_create, "PUBLIC has CREATE on schema agri"

    executable = _fetch(
        agri_db_connection,
        "SELECT proname FROM pg_proc WHERE pronamespace = 'agri'::regnamespace "
        "AND has_function_privilege('public', oid, 'EXECUTE') ORDER BY proname",
    )
    assert not executable, (
        f"PUBLIC can EXECUTE {len(executable)} agri routine(s): {[row[0] for row in executable[:10]]}"
    )

    readable = _fetch(
        agri_db_connection,
        "SELECT relname FROM pg_class WHERE relnamespace = 'agri'::regnamespace AND relkind IN ('r', 'p', 'v', 'm') "
        "AND has_table_privilege('public', oid, 'SELECT') ORDER BY relname",
    )
    assert not readable, f"PUBLIC can SELECT {len(readable)} agri relation(s): {[row[0] for row in readable[:10]]}"
