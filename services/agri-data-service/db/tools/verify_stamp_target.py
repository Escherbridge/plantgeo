"""Prove a database may be stamped to the greenfield baseline, before anyone runs `alembic stamp`.

``alembic stamp 20260825_0000`` writes one row and executes no DDL. That is exactly what makes it
dangerous: it ASSERTS that the target's ``agri`` schema is already the schema
``20260825_0000`` builds, and nothing checks the assertion. This tool is the check. It is read-only
on the target -- ``SELECT``s and ``pg_dump --schema-only`` -- and it never stamps anything.

    uv run python db/tools/verify_stamp_target.py \\
        --target-dsn postgresql://user:***@host:5432/plantgeo \\
        --expect-database plantgeo \\
        --admin-dsn postgresql://plantgeo_owner:***@127.0.0.1:5442/postgres

Exit codes: ``0`` safe to stamp, ``1`` at least one gate failed, ``2`` usage or toolchain error.

FOUR GATES, AND WHY EACH ONE IS A GATE RATHER THAN A WARNING.

1. **``--expect-database`` must equal the target's ``current_database()``.** A DSN is easy to
   mis-paste and there is no undo for a stamp. Fail closed on the wrong target.
2. **``timescaledb`` must already be absent.** The collapsed chain ends at ``20260825_0026``, whose
   entire job was dropping that extension. Skipping it by stamping is safe ONLY for a database
   where it was already dropped by hand. Nothing else in the repository verifies that, and after
   the stamp nothing ever can -- the revision that would have done it is off the migration path.
3. **The target must be at a revision the baseline actually supersedes.** ``20260817_0025`` or
   ``20260825_0026``. A database at an EARLIER revision has not had the intervening DDL and must be
   walked forward through ``alembic/archive/`` first; a database already at the baseline needs no
   stamp; anything else this build cannot place at all.
4. **The ``agri`` schema must equal a freshly baseline-built one.** Both sides are captured with
   ``db/tools/dump_schema.py``'s canonical flags on the same client. The comparison is NOT byte
   equality and cannot be: the chain-built target and the tree-replaying baseline render the same
   predicates differently (``= ANY ((ARRAY[...])::text[])`` vs ``= ANY (ARRAY[(...)::text])`` and
   two siblings of it), 74 lines of it, measured. Lines are scored by
   ``agri_data_service.db.schema_diff`` -- exact where they can be, the reviewed reparse rule where
   they cannot -- and ANY line that is neither identical nor a reparse fails the gate. That module
   states plainly what its rule admits and what it therefore misses.

WHAT IT REPORTS BUT DOES NOT GATE ON: privileges. ``pg_dump --no-owner --no-privileges`` discards
them by construction, and the baseline deliberately differs from the 26-revision chain here (it
revokes three routines PUBLIC could still execute, and grants ``EXECUTE`` on every CHECK-invoked
routine to the operator roles). A stamp changes none of that, so the privilege section prints what
the target will still be missing afterwards and leaves the decision to a human.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_TOOLS = Path(__file__).resolve().parent
_DB_ROOT = _TOOLS.parent
_SERVICE_ROOT = _DB_ROOT.parent
sys.path.insert(0, str(_TOOLS))
sys.path.insert(0, str(_SERVICE_ROOT / "src"))

import dump_schema  # noqa: E402

from agri_data_service.db.extensions import REQUIRED_EXTENSIONS  # noqa: E402
from agri_data_service.db.revisions import (  # noqa: E402
    BASELINE_REVISION,
    UnknownAlembicRevisionError,
    revision_rank,
)
from agri_data_service.db.schema_diff import compare_ddl  # noqa: E402

_DEFAULT_EXTENSIONS_SQL = _SERVICE_ROOT.parent.parent / "infra" / "local-warehouse" / "enable-extensions.sql"

# The only revisions a stamp to the baseline is defined for: the chain's last two states.
STAMPABLE_REVISIONS: tuple[str, ...] = ("20260817_0025", "20260825_0026")

# Extensions whose presence means 20260825_0026 was never applied by any means.
FORBIDDEN_EXTENSIONS: tuple[str, ...] = ("timescaledb", "timescaledb_toolkit")

_MAX_UNEXPLAINED_SHOWN = 40


class Gate:
    """One pass/fail check with the evidence that decided it."""

    def __init__(self, name: str, passed: bool, detail: str) -> None:
        self.name = name
        self.passed = passed
        self.detail = detail

    def render(self) -> str:
        return f"[{'PASS' if self.passed else 'FAIL'}] {self.name}\n       {self.detail}"


def _swap_database(dsn: str, database: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


def _redact(dsn: str) -> str:
    parts = urlsplit(dsn)
    return f"{parts.hostname}:{parts.port or 5432}{parts.path}"


def _psql_rows(psql: str, dsn: str, sql: str) -> list[list[str]]:
    """Read-only query through psql, so this tool needs no driver the migration environment lacks."""
    result = subprocess.run(
        [psql, "-X", "-q", "-t", "-A", "-F", "\x1f", "-v", "ON_ERROR_STOP=1", "-d", dsn, "-c", sql],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.split("\x1f") for line in result.stdout.splitlines() if line.strip()]


def _psql_run(psql: str, dsn: str, *args: str) -> None:
    subprocess.run([psql, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", dsn, *args], check=True)


def _gate_expected_database(psql: str, target_dsn: str, expected: str) -> Gate:
    ((observed,),) = _psql_rows(psql, target_dsn, "SELECT current_database()")
    return Gate(
        "target is the database named on the command line",
        observed == expected,
        f"--expect-database {expected!r}, connected to {observed!r} at {_redact(target_dsn)}",
    )


def _gate_timescaledb_absent(psql: str, target_dsn: str) -> Gate:
    installed = {row[0] for row in _psql_rows(psql, target_dsn, "SELECT extname FROM pg_extension")}
    present = sorted(set(FORBIDDEN_EXTENSIONS) & installed)
    return Gate(
        "timescaledb was already dropped by hand",
        not present,
        (
            f"still installed: {present}. Stamping SKIPS 20260825_0026, whose only job was dropping "
            "it, and no later check will ever notice. Drop it first."
        )
        if present
        else f"absent. Installed: {sorted(installed)}",
    )


def _gate_required_extensions(psql: str, target_dsn: str) -> Gate:
    installed = {row[0] for row in _psql_rows(psql, target_dsn, "SELECT extname FROM pg_extension")}
    missing = sorted(set(REQUIRED_EXTENSIONS) - installed)
    return Gate(
        "the baseline's required extensions are installed",
        not missing,
        f"missing {missing}" if missing else f"present: {sorted(REQUIRED_EXTENSIONS)}",
    )


def _gate_revision(psql: str, target_dsn: str) -> Gate:
    rows = _psql_rows(psql, target_dsn, "SELECT version_num FROM public.alembic_version")
    if not rows:
        return Gate("target is at a revision the baseline supersedes", False, "public.alembic_version has no row")
    observed = rows[0][0]
    if observed == BASELINE_REVISION:
        return Gate(
            "target is at a revision the baseline supersedes",
            False,
            f"already at {BASELINE_REVISION}: there is nothing to stamp",
        )
    try:
        rank = revision_rank(observed)
    except UnknownAlembicRevisionError as exc:
        return Gate("target is at a revision the baseline supersedes", False, str(exc))
    if observed not in STAMPABLE_REVISIONS:
        return Gate(
            "target is at a revision the baseline supersedes",
            False,
            (
                f"at {observed} (rank {rank}); a stamp to {BASELINE_REVISION} is defined only for "
                f"{list(STAMPABLE_REVISIONS)}. Walk it forward through alembic/archive/ first."
            ),
        )
    return Gate("target is at a revision the baseline supersedes", True, f"at {observed}")


def _build_reference(psql: str, admin_dsn: str, database: str, extensions_sql: Path, keep: bool) -> str:
    """Create a disposable database and build the `agri` schema from the baseline alone."""
    _psql_run(psql, admin_dsn, "-c", f'DROP DATABASE IF EXISTS "{database}";')
    _psql_run(psql, admin_dsn, "-c", f'CREATE DATABASE "{database}";')
    reference = _swap_database(admin_dsn, database)
    try:
        _psql_run(psql, reference, "-f", str(extensions_sql))
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=_SERVICE_ROOT,
            check=True,
            env={**os.environ, "DATABASE_URL_SYNC": reference},
        )
    except Exception:
        if not keep:
            _psql_run(psql, admin_dsn, "-c", f'DROP DATABASE IF EXISTS "{database}";')
        raise
    return reference


def _gate_schema_equivalent(target_dsn: str, reference_dsn: str, pg_dump: str | None) -> Gate:
    """Not byte equality -- a chain-built target can never reach that. See `db/schema_diff.py`."""
    reference_dump = dump_schema.dump_agri(reference_dsn, pg_dump)
    target_dump = dump_schema.dump_agri(target_dsn, pg_dump)
    comparison = compare_ddl(reference_dump, target_dump)
    detail = comparison.render(limit=_MAX_UNEXPLAINED_SHOWN)
    if not comparison.equivalent:
        detail += "\n       Stamping would record a schema the target does not have."
    return Gate("target agri schema equals a baseline build", comparison.equivalent, detail)


def _privilege_report(psql: str, target_dsn: str, reference_dsn: str) -> str:
    """Not a gate. What a stamp will NOT fix, because `alembic stamp` executes no SQL."""
    public_executable = (
        "SELECT proname FROM pg_proc WHERE pronamespace = 'agri'::regnamespace "
        "AND has_function_privilege('public', oid, 'EXECUTE') ORDER BY 1"
    )
    check_invoked_ungranted = (
        "SELECT DISTINCT routine.proname, grantee.rolname "
        "FROM pg_constraint constraint_row "
        "JOIN pg_depend dependency ON dependency.objid = constraint_row.oid "
        "AND dependency.classid = 'pg_constraint'::regclass "
        "JOIN pg_proc routine ON routine.oid = dependency.refobjid "
        "AND dependency.refclassid = 'pg_proc'::regclass "
        "CROSS JOIN pg_roles grantee "
        "WHERE constraint_row.contype = 'c' AND constraint_row.connamespace = 'agri'::regnamespace "
        "AND routine.pronamespace = 'agri'::regnamespace "
        "AND grantee.rolname IN ('plantgeo_local_developer', 'plantgeo_loader', "
        "'plantgeo_forecast_mv_refresher', 'plantgeo_forecast_refresh_operator', 'plantgeo_local_viewer') "
        "AND NOT has_function_privilege(grantee.oid, routine.oid, 'EXECUTE') ORDER BY 1, 2"
    )
    target_public = [row[0] for row in _psql_rows(psql, target_dsn, public_executable)]
    reference_public = [row[0] for row in _psql_rows(psql, reference_dsn, public_executable)]
    missing_execute = [f"{row[0]} -> {row[1]}" for row in _psql_rows(psql, target_dsn, check_invoked_ungranted)]

    lines = ["", "PRIVILEGES (reported, not gated -- `alembic stamp` executes no SQL)"]
    lines.append(f"  PUBLIC can EXECUTE on target:        {target_public or 'nothing'}")
    lines.append(f"  PUBLIC can EXECUTE on baseline build: {reference_public or 'nothing'}")
    if target_public:
        lines.append(
            "  -> the stamp will NOT revoke these. Run the baseline's REVOKE EXECUTE ON ALL ROUTINES "
            "IN SCHEMA agri FROM PUBLIC by hand if you want the target to match."
        )
    if missing_execute:
        lines.append(f"  operator role(s) lacking EXECUTE on a CHECK-invoked routine: {missing_execute}")
        lines.append(
            "  -> a CHECK is evaluated with the WRITER's privileges. Any of these roles writing to "
            "that table gets `permission denied for function`. The stamp will NOT grant them."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target-dsn", required=True, help="the database about to be stamped; read-only here")
    parser.add_argument("--expect-database", required=True, help="its current_database(); fails closed on mismatch")
    parser.add_argument("--admin-dsn", required=True, help="maintenance DSN able to CREATE/DROP the reference db")
    parser.add_argument("--reference-db-name", default="agri_stamp_reference")
    parser.add_argument("--extensions-sql", type=Path, default=_DEFAULT_EXTENSIONS_SQL)
    parser.add_argument("--psql", default=None, help="psql path (default: PATH/PGBIN)")
    parser.add_argument("--pg-dump", default=None, help="pg_dump path (default: PATH/PGBIN)")
    parser.add_argument("--keep-reference", action="store_true", help="keep the disposable reference database")
    arguments = parser.parse_args(argv)

    psql = arguments.psql or shutil.which("psql")
    if not psql:
        parser.error("psql not found; pass --psql or set PGBIN/PATH")

    target_dsn = dump_schema.to_libpq_url(arguments.target_dsn)
    admin_dsn = dump_schema.to_libpq_url(arguments.admin_dsn)
    print(f"[verify-stamp] target    {_redact(target_dsn)}")
    print(f"[verify-stamp] reference {_redact(admin_dsn)} -> {arguments.reference_db_name}")

    gates = [
        _gate_expected_database(psql, target_dsn, arguments.expect_database),
        _gate_timescaledb_absent(psql, target_dsn),
        _gate_required_extensions(psql, target_dsn),
        _gate_revision(psql, target_dsn),
    ]
    if not all(gate.passed for gate in gates):
        # No reference build: a wrong or unready target is decided before anything is created.
        print("\n".join(gate.render() for gate in gates))
        print(f"\nREFUSED: {sum(not gate.passed for gate in gates)} gate(s) failed. Do not stamp.")
        return 1

    reference_dsn = _build_reference(
        psql, admin_dsn, arguments.reference_db_name, arguments.extensions_sql, arguments.keep_reference
    )
    try:
        gates.append(_gate_schema_equivalent(target_dsn, reference_dsn, arguments.pg_dump))
        report = _privilege_report(psql, target_dsn, reference_dsn)
    finally:
        if not arguments.keep_reference:
            _psql_run(psql, admin_dsn, "-c", f'DROP DATABASE IF EXISTS "{arguments.reference_db_name}";')

    print("\n".join(gate.render() for gate in gates))
    print(report)
    failed = sum(not gate.passed for gate in gates)
    if failed:
        print(f"\nREFUSED: {failed} gate(s) failed. Do not stamp.")
        return 1
    print(
        f"\nCLEARED: every gate passed. `alembic stamp {BASELINE_REVISION}` records a schema this "
        "target demonstrably already has. Re-run this immediately before stamping, not hours before."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
