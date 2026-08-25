"""A revision layered on the greenfield baseline must be idempotent against its own regenerated tree.

THE TRAP, STATED ONCE. `20260825_0000` executes the CURRENT `db/agri/**` tree, and
`db/tools/regenerate.py` rebuilds that tree by running `alembic upgrade head`. So the sequence the
guides tell the next author to follow --- write the revision, then regenerate --- makes the tree
contain the revision's own objects. The build that produced the tree is fine. The NEXT build from
empty is not: it runs the baseline (creating the object FROM THE TREE) and then the revision
(creating it AGAIN). `db/AGENTS.md` provisions the disposable `agri_db` test database with exactly
that `alembic upgrade head` from empty, so the whole real-database gate stops being buildable the
day after a non-idempotent schema revision lands.

TWO GATES, DELIBERATELY DIFFERENT IN STRENGTH.

* `test_no_revision_layered_on_the_baseline_uses_non_idempotent_ddl` needs no database and always
  runs. It is a lint: it knows the handful of shapes that are certainly not re-appliable
  (`op.create_table`, a bare `CREATE INDEX`, `ADD COLUMN` without `IF NOT EXISTS` ...). It cannot
  prove idempotence, only catch the obvious loss of it, and it is worth having precisely because
  the rehearsal below skips without a database.
* The rehearsal builds a real database from empty and is the authoritative check. It also proves
  itself: one case injects a revision that re-applies a tree file the baseline already loaded and
  asserts the build FAILS, another injects the guarded form of the same change and asserts it
  SUCCEEDS. Without the negative case a green rehearsal would prove only that the harness ran.

Set `AGRI_MIGRATION_REHEARSAL_ADMIN_DSN` to a libpq DSN on a maintenance database that may
`CREATE DATABASE`/`DROP DATABASE` (the disposable local warehouse on 127.0.0.1:5442, never
production, never `plantgeo`). Marked `agri_db_migration_rehearsal`, which conftest exempts from
the no-silent-skip gate: it needs a database deliberately NOT at head.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest

from tests.conftest import AGRI_DB_REHEARSAL_MARKER, PROTECTED_DATABASE_NAME
from tests.test_alembic_baseline_contract import executed_sql
from tests.test_alembic_head_pin_contract import revision_chain

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
_VERSIONS = _SERVICE_ROOT / "alembic" / "versions"
_REPOSITORY_ROOT = _SERVICE_ROOT.parents[1]
_EXTENSIONS_SQL = _REPOSITORY_ROOT / "infra" / "local-warehouse" / "enable-extensions.sql"

BASELINE_FILE_NAME = "20260825_0000_agri_greenfield_baseline.py"
ADMIN_DSN_ENV = "AGRI_MIGRATION_REHEARSAL_ADMIN_DSN"
REHEARSAL_DATABASE_PREFIX = "agri_forward_rehearsal"

# Alembic operation helpers with no `IF NOT EXISTS` form. Each re-run raises `DuplicateObject`.
_NON_IDEMPOTENT_OPERATIONS = (
    "op.create_table(",
    "op.add_column(",
    "op.create_index(",
    "op.create_primary_key(",
    "op.create_unique_constraint(",
    "op.create_check_constraint(",
    "op.create_foreign_key(",
    "op.create_table_comment(",
)

# Raw-SQL shapes, matched on what a revision EXECUTES rather than what it explains.
_UNGUARDED_SQL = (
    (re.compile(r"\bCREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS)", re.IGNORECASE), "CREATE TABLE without IF NOT EXISTS"),
    (
        re.compile(r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?!IF\s+NOT\s+EXISTS)", re.IGNORECASE),
        "CREATE INDEX without IF NOT EXISTS",
    ),
    (re.compile(r"\bADD\s+COLUMN\s+(?!IF\s+NOT\s+EXISTS)", re.IGNORECASE), "ADD COLUMN without IF NOT EXISTS"),
    (
        re.compile(r"\bCREATE\s+SEQUENCE\s+(?!IF\s+NOT\s+EXISTS)", re.IGNORECASE),
        "CREATE SEQUENCE without IF NOT EXISTS",
    ),
    (re.compile(r"\bCREATE\s+SCHEMA\s+(?!IF\s+NOT\s+EXISTS)", re.IGNORECASE), "CREATE SCHEMA without IF NOT EXISTS"),
)

_RULE = (
    "Every revision layered on the greenfield baseline must be idempotent against a db/agri/** tree "
    "that already contains its own changes -- the baseline replays that tree, and regenerate.py puts "
    "the change into it. Use IF NOT EXISTS, a NOT EXISTS probe around ADD CONSTRAINT, and "
    "load_object_sql(..., or_replace=True) or drop-then-create for programmable objects. "
    "See db/AGENTS.md, 'Layering a revision on the greenfield baseline'."
)


def _layered_revisions() -> list[Path]:
    return [path for path in sorted(_VERSIONS.glob("*.py")) if path.name != BASELINE_FILE_NAME]


def test_no_revision_layered_on_the_baseline_uses_non_idempotent_ddl() -> None:
    """Static lint. Passes vacuously while the baseline is alone -- and stops passing on the next one."""
    findings: list[str] = []
    for path in _layered_revisions():
        source = path.read_text(encoding="utf-8")
        findings.extend(
            f"{path.name}: {operation} has no IF NOT EXISTS form"
            for operation in _NON_IDEMPOTENT_OPERATIONS
            if operation in source
        )
        executed = executed_sql(path)
        findings.extend(
            f"{path.name}: {description}" for pattern, description in _UNGUARDED_SQL if pattern.search(executed)
        )
        if re.search(r"\bADD\s+CONSTRAINT\b", executed, re.IGNORECASE) and "pg_constraint" not in executed:
            findings.append(
                f"{path.name}: ADD CONSTRAINT with no pg_constraint existence probe "
                "(PostgreSQL has no ADD CONSTRAINT IF NOT EXISTS)"
            )

    assert not findings, "\n".join([*findings, "", _RULE])


def _require_admin_dsn() -> str:
    dsn = os.environ.get(ADMIN_DSN_ENV)
    if not dsn:
        pytest.skip(
            f"set {ADMIN_DSN_ENV} to a libpq DSN on a maintenance database that may CREATE/DROP "
            f"DATABASE on a disposable server (never production, never {PROTECTED_DATABASE_NAME!r})"
        )
    return dsn


def _swap_database(dsn: str, database: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


def _run_maintenance(admin_dsn: str, statement: str) -> None:
    connection = psycopg2.connect(admin_dsn)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute(statement)
    finally:
        connection.close()


def _enable_reviewed_extensions(dsn: str) -> None:
    """Run the reviewed operator gate. `\\set ON_ERROR_STOP` is a psql meta-command, not SQL."""
    statements = "\n".join(
        line for line in _EXTENSIONS_SQL.read_text(encoding="utf-8").splitlines() if not line.startswith("\\")
    )
    connection = psycopg2.connect(dsn)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute(statements)
    finally:
        connection.close()


def _rehearsal_alembic_ini(version_locations: Path, destination: Path) -> Path:
    """The committed alembic.ini with absolute paths and one throwaway version path substituted in.

    `script_location` must stay the real `alembic/` so `env.py` -- and the target announcement it
    logs -- is the one under review; only where revisions are *found* changes.
    """
    configuration = (_SERVICE_ROOT / "alembic.ini").read_text(encoding="utf-8")
    configuration = configuration.replace(
        "script_location = alembic",
        f"script_location = {_SERVICE_ROOT / 'alembic'}\nversion_locations = {version_locations}",
    ).replace("prepend_sys_path = src", f"prepend_sys_path = {_SERVICE_ROOT / 'src'}")
    destination.write_text(configuration, encoding="utf-8")
    return destination


def _alembic_upgrade_head(dsn: str, alembic_ini: Path) -> subprocess.CompletedProcess[str]:
    """Run `alembic upgrade head` against ``dsn``. `DATABASE_URL_SYNC` is the only DSN env.py reads."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(alembic_ini), "upgrade", "head"],
        cwd=_SERVICE_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "DATABASE_URL_SYNC": dsn},
    )


def _build_from_empty(admin_dsn: str, database: str, alembic_ini: Path) -> subprocess.CompletedProcess[str]:
    _run_maintenance(admin_dsn, f'DROP DATABASE IF EXISTS "{database}"')
    _run_maintenance(admin_dsn, f'CREATE DATABASE "{database}"')
    target = _swap_database(admin_dsn, database)
    try:
        _enable_reviewed_extensions(target)
        return _alembic_upgrade_head(target, alembic_ini)
    finally:
        _run_maintenance(admin_dsn, f'DROP DATABASE IF EXISTS "{database}"')


def _throwaway_version_path(tmp_path: Path, probe_body: str) -> Path:
    """The committed `versions/` plus one synthetic revision chained onto the baseline."""
    destination = tmp_path / "versions"
    destination.mkdir()
    for path in sorted(_VERSIONS.glob("*.py")):
        (destination / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    head = revision_chain(_VERSIONS)[-1]
    (destination / "29990101_9999_forward_rehearsal_probe.py").write_text(
        '"""Synthetic revision used only by tests/test_alembic_baseline_forward_rehearsal.py."""\n\n'
        "from agri_data_service.db.sql_objects import load_object_sql  # noqa: F401\n"
        "from alembic import op\n\n"
        'revision = "29990101_9999"\n'
        f'down_revision = "{head}"\n'
        "branch_labels = None\n"
        "depends_on = None\n\n\n"
        "def upgrade() -> None:\n"
        f"{probe_body}\n\n\n"
        "def downgrade() -> None:\n"
        "    raise NotImplementedError\n",
        encoding="utf-8",
    )
    return destination


@pytest.mark.agri_db_migration_rehearsal
def test_the_committed_migration_path_builds_from_empty_against_the_current_tree() -> None:
    """The build `db/AGENTS.md` tells an operator to make, and the one a regeneration breaks first."""
    admin_dsn = _require_admin_dsn()
    result = _build_from_empty(
        admin_dsn, f"{REHEARSAL_DATABASE_PREFIX}_committed", _SERVICE_ROOT / "alembic.ini"
    )

    assert result.returncode == 0, (
        "`alembic upgrade head` no longer builds an empty database from the committed tree.\n"
        f"{result.stdout[-4000:]}\n{result.stderr[-4000:]}\n\n{_RULE}"
    )


@pytest.mark.agri_db_migration_rehearsal
def test_the_rehearsal_catches_a_revision_that_recreates_what_the_tree_already_holds(tmp_path: Path) -> None:
    """The negative control: without this, a green rehearsal proves only that the harness ran.

    The probe is the exact mistake the guides used to invite -- author the migration normally, then
    regenerate -- reduced to its essence: re-apply one unmodified tree file that the baseline has
    already loaded.
    """
    admin_dsn = _require_admin_dsn()
    version_locations = _throwaway_version_path(tmp_path, '    op.execute(load_object_sql("tables/species.sql"))')
    alembic_ini = _rehearsal_alembic_ini(version_locations, tmp_path / "alembic.ini")

    result = _build_from_empty(admin_dsn, f"{REHEARSAL_DATABASE_PREFIX}_double_apply", alembic_ini)

    assert result.returncode != 0, "a revision re-creating agri.species built cleanly; the rehearsal is not looking"
    combined = f"{result.stdout}{result.stderr}"
    assert "already exists" in combined, f"expected a duplicate-object failure, got:\n{combined[-4000:]}"


@pytest.mark.agri_db_migration_rehearsal
def test_the_guarded_form_of_the_same_change_builds_cleanly(tmp_path: Path) -> None:
    """The positive control: the rule is satisfiable, so a red rehearsal is a real finding."""
    admin_dsn = _require_admin_dsn()
    version_locations = _throwaway_version_path(
        tmp_path,
        '    op.execute("CREATE TABLE IF NOT EXISTS agri.forward_rehearsal_probe (id integer PRIMARY KEY)")',
    )
    alembic_ini = _rehearsal_alembic_ini(version_locations, tmp_path / "alembic.ini")

    result = _build_from_empty(admin_dsn, f"{REHEARSAL_DATABASE_PREFIX}_guarded", alembic_ini)

    assert result.returncode == 0, f"{result.stdout[-4000:]}\n{result.stderr[-4000:]}"


def test_the_rehearsal_marker_is_registered_so_it_cannot_run_dark() -> None:
    """A skip here is structural; conftest must say so rather than let the sweep gate flag it."""
    assert AGRI_DB_REHEARSAL_MARKER == "agri_db_migration_rehearsal"
