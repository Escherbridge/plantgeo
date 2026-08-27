"""Regenerate the declarative ``agri`` schema tree from the Alembic migrations.

One command that ties the committed ``db/agri/**`` tree to the migration head:
it spins up a disposable database, applies every migration, captures the
resulting DDL with ``pg_dump``, and explodes it into the per-object tree +
``manifest.sql``. The disposable database is dropped afterwards.

Example (local warehouse)::

    uv run python db/tools/regenerate.py \\
        --admin-dsn postgresql://plantgeo_owner:***@127.0.0.1:5442/plantgeo

WHAT THIS PROVES SINCE THE 2026-08-25 COLLAPSE, AND WHAT IT NO LONGER PROVES. The migration chain is
rooted at greenfield baseline ``20260825_0000``; that revision builds the base schema by replaying
``manifest.sql`` -- this very tree -- and live follow-on revisions apply forward deltas. So
regeneration is a **round trip** (tree -> database -> tree),
not an independent second derivation of the schema from 26 hand-written revisions. It still catches
real breakage: any DDL in the tree that PostgreSQL does not re-emit verbatim after a parse shows up
as a diff, which is exactly how the ``= ANY((ARRAY[...])::text[])`` renormalisation was found. What
it can no longer catch is the tree having drifted from the *intent* of the archived chain, because
the chain is no longer on the migration path. If you need that, replay
``alembic/archive/`` against a disposable database (see ``alembic/archive/AGENTS.md``) and diff.

The tree is a fixed point: applying it and re-dumping must reproduce it byte for byte. If a run of
this tool produces a diff on an unchanged tree, that is the fixed point breaking, not noise.

See ``db/AGENTS.md`` for the full workflow and the parity guarantee.
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
_DB_ROOT = _TOOLS.parent  # services/agri-data-service/db
_SERVICE_ROOT = _DB_ROOT.parent  # services/agri-data-service
sys.path.insert(0, str(_TOOLS))

import dump_schema  # noqa: E402
import split_schema  # noqa: E402

_DEFAULT_EXTENSIONS_SQL = _SERVICE_ROOT.parent.parent / "infra" / "local-warehouse" / "enable-extensions.sql"


def _swap_database(dsn: str, db_name: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit((parts.scheme, parts.netloc, f"/{db_name}", parts.query, parts.fragment))


def _psql(psql: str, dsn: str, *args: str) -> None:
    subprocess.run([psql, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", dsn, *args], check=True)


def _server_major(psql: str, dsn: str) -> int:
    """Read ``server_version_num`` from the target and return its major component."""
    result = subprocess.run(
        [psql, "-X", "-q", "-t", "-A", "-v", "ON_ERROR_STOP=1", "-d", dsn, "-c", "SHOW server_version_num"],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip()) // 10000


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--admin-dsn", required=True, help="DSN on a maintenance db able to CREATE/DROP DATABASE")
    ap.add_argument("--db-name", default="plantgeo_schema_regen", help="disposable database name")
    ap.add_argument("--root", type=Path, default=_DB_ROOT, help="declarative tree root (default: db/)")
    ap.add_argument("--extensions-sql", type=Path, default=_DEFAULT_EXTENSIONS_SQL)
    ap.add_argument("--psql", default=None, help="psql path (default: PATH/PGBIN)")
    ap.add_argument("--pg-dump", default=None, help="pg_dump path (default: PATH/PGBIN)")
    ap.add_argument("--keep", action="store_true", help="keep the disposable database")
    args = ap.parse_args(argv)

    psql = args.psql or shutil.which("psql")
    if not psql:
        # Fall back to the same bin directory pg_dump was resolved from.
        pg = dump_schema.resolve_pg_dump(args.pg_dump)
        if pg:
            cand = Path(pg).with_name("psql.exe" if pg.endswith(".exe") else "psql")
            psql = str(cand) if cand.exists() else None
    if not psql:
        ap.error("psql not found; pass --psql or set PGBIN/PATH")

    admin = dump_schema.to_libpq_url(args.admin_dsn)
    disposable = _swap_database(admin, args.db_name)

    # The committed tree is the canonical-major artifact. Regenerating from any other
    # major would silently bake that major's dump dialect (e.g. pg18's catalogued
    # NOT NULL constraint names) into the reviewed source of truth.
    server_major = _server_major(psql, admin)
    if server_major != dump_schema.CANONICAL_SERVER_MAJOR:
        ap.error(
            f"refusing to regenerate from a PostgreSQL {server_major} server: the declarative tree is "
            f"canonical against PostgreSQL {dump_schema.CANONICAL_SERVER_MAJOR}. Point --admin-dsn at a "
            f"PostgreSQL {dump_schema.CANONICAL_SERVER_MAJOR} server, or change CANONICAL_SERVER_MAJOR "
            "deliberately and regenerate the whole tree in a reviewed change."
        )
    dumper_major = dump_schema.pg_dump_major(args.pg_dump)
    if dumper_major < server_major:
        ap.error(f"pg_dump {dumper_major} cannot dump a PostgreSQL {server_major} server; use a newer client")
    sync_dsn = disposable  # alembic reads DATABASE_URL_SYNC (a plain postgresql:// DSN)

    print(f"[regen] creating disposable database {args.db_name}")
    _psql(psql, admin, "-c", f"DROP DATABASE IF EXISTS {args.db_name};")
    _psql(psql, admin, "-c", f"CREATE DATABASE {args.db_name};")
    try:
        print("[regen] enabling reviewed extensions")
        _psql(psql, disposable, "-f", str(args.extensions_sql))

        print("[regen] applying alembic migrations to head")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=_SERVICE_ROOT,
            check=True,
            env={**_env(), "DATABASE_URL_SYNC": sync_dsn},
        )

        print("[regen] capturing schema DDL")
        dump = dump_schema.dump_agri(disposable, args.pg_dump)

        print(f"[regen] writing declarative tree under {args.root}")
        _clean_tree(args.root)
        written = split_schema.write_tree(split_schema.split(dump), args.root)
        print(f"[regen] wrote {len(written)} files")
    finally:
        if not args.keep:
            print(f"[regen] dropping disposable database {args.db_name}")
            _psql(psql, admin, "-c", f"DROP DATABASE IF EXISTS {args.db_name};")

    return 0


def _clean_tree(root: Path) -> None:
    agri = root / "agri"
    if agri.exists():
        shutil.rmtree(agri)
    (root / "manifest.sql").unlink(missing_ok=True)


def _env() -> dict[str, str]:
    return dict(os.environ)


if __name__ == "__main__":
    raise SystemExit(main())
