"""Capture the ground-truth ``agri`` schema DDL from a migrated database.

Both the regeneration command and the schema-parity test depend on producing
the *same* ``pg_dump`` output the declarative tree was built from. Centralising
the flags and the banner normalisation here keeps them in lock-step.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

# Flags that define the canonical dump. Changing any of these changes the tree,
# so the parity test and regenerate.py must share this exact list.
DUMP_ARGS = (
    "--schema-only",
    "--schema=agri",
    "--no-owner",
    "--no-privileges",
)
_BANNER = re.compile(r"(?m)^-- Dumped (?:from|by).*\n")


def resolve_pg_dump(explicit: str | None = None) -> str | None:
    """Locate a ``pg_dump`` executable (explicit arg, ``PG_DUMP``, ``PGBIN``, PATH)."""
    for candidate in (explicit, os.environ.get("PG_DUMP")):
        if candidate and Path(candidate).exists():
            return candidate
    pgbin = os.environ.get("PGBIN")
    if pgbin:
        for name in ("pg_dump", "pg_dump.exe"):
            p = Path(pgbin) / name
            if p.exists():
                return str(p)
    return shutil.which("pg_dump")


def to_libpq_url(dsn: str) -> str:
    """Strip a SQLAlchemy driver suffix so libpq/pg_dump accepts the URL."""
    return re.sub(r"^postgresql\+[a-z0-9]+://", "postgresql://", dsn)


def dump_agri(dsn: str, pg_dump: str | None = None) -> str:
    """Return the normalised schema-only DDL of the ``agri`` schema at ``dsn``.

    The version banner (which legitimately varies by client/server build) is
    stripped so the output depends only on schema content.
    """
    binary = resolve_pg_dump(pg_dump)
    if not binary:
        raise FileNotFoundError("pg_dump not found; set PG_DUMP or PGBIN, or put pg_dump on PATH")
    result = subprocess.run(
        [binary, *DUMP_ARGS, "-d", to_libpq_url(dsn)],
        check=True,
        capture_output=True,
        text=True,
    )
    return _BANNER.sub("", result.stdout)
