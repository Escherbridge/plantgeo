"""Capture the ground-truth ``agri`` schema DDL from a migrated database.

Both the regeneration command and the schema-parity test depend on producing
the *same* ``pg_dump`` output the declarative tree was built from. Centralising
the flags, the banner normalisation, and the cross-major rules keeps them in
lock-step. See ``db/AGENTS.md`` (*Toolchain and major-version awareness*) for
why each cross-major rule exists and what it does and does not prove.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Flags that define the canonical dump. Changing any of these changes the tree,
# so the parity test and regenerate.py must share this exact list.
DUMP_ARGS = (
    "--schema-only",
    "--schema=agri",
    "--no-owner",
    "--no-privileges",
)
# The committed db/agri/** tree is generated from a PostgreSQL 16 server. Byte
# exactness is guaranteed only against this major; every other major goes through
# the enumerated normalisation below.
CANONICAL_SERVER_MAJOR = 16
# PostgreSQL 18 catalogues NOT NULL as real pg_constraint rows, so its pg_dump
# emits an explicit ``CONSTRAINT <name>`` on a column-level NOT NULL whenever the
# stored name is not the one a restore would derive (``<table>_<column>_not_null``).
FIRST_CATALOGUED_NOT_NULL_MAJOR = 18

_BANNER = re.compile(r"(?m)^-- Dumped (?:from|by).*\n")
# pg18's pg_dump brackets its output with psql \restrict/\unrestrict meta-commands
# carrying a randomly generated token. They are not DDL and the token differs on
# every run, so they can never be compared; the trailing one would otherwise be
# swept into the last object's body by the splitter.
_PSQL_RESTRICT = re.compile(r"(?m)^\\(?:un)?restrict [A-Za-z0-9]+[ \t]*\n")
# Anchored to a column-level ``NOT NULL``: it deletes only a constraint *name*, so
# a NOT NULL appearing or disappearing, a type change, or a table constraint still
# has to match byte-for-byte afterwards. WARNING: the anchoring is NOT the
# guarantee -- this is not string-literal aware and will rewrite a DEFAULT whose
# text resembles a constraint clause. Callers MUST pair it with a catalogue
# cross-check; see ``normalize_named_not_null`` and db/AGENTS.md rule 2.
_NAMED_NOT_NULL = re.compile(r" CONSTRAINT (?P<name>[A-Za-z0-9_]+) NOT NULL")
_VERSION = re.compile(r"(\d+)")


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


def _dump_argv(pg_dump: str | None, argv_prefix: Sequence[str] | None) -> list[str]:
    """The argv that invokes pg_dump: either a local binary or a caller-supplied launcher.

    ``argv_prefix`` exists because ``pg_dump`` cannot dump a server newer than
    itself, and a newer client is not always installable on the host. Supplying
    e.g. ``["podman", "exec", "-i", "<container>", "pg_dump"]`` runs the client
    that ships alongside the server instead. See ``db/AGENTS.md``.
    """
    if argv_prefix:
        return list(argv_prefix)
    binary = resolve_pg_dump(pg_dump)
    if not binary:
        raise FileNotFoundError("pg_dump not found; set PG_DUMP or PGBIN, or put pg_dump on PATH")
    return [binary]


def pg_dump_major(pg_dump: str | None = None, argv_prefix: Sequence[str] | None = None) -> int:
    """Return the major version of the pg_dump that would be invoked."""
    result = subprocess.run(
        [*_dump_argv(pg_dump, argv_prefix), "--version"], check=True, capture_output=True, text=True
    )
    match = _VERSION.search(result.stdout)
    if not match:
        raise ValueError(f"could not parse a major version from {result.stdout!r}")
    return int(match.group(1))


def normalize_named_not_null(dump: str) -> tuple[str, list[str]]:
    """Drop pg18+ inline NOT NULL constraint names; return the text and the names dropped.

    Callers **must** verify every returned name against the server's own
    ``pg_constraint`` rows -- that cross-check, not the regex anchoring, is what
    makes the rule provably narrow. The pattern is not string-literal aware and
    will rewrite a DEFAULT containing text that resembles a constraint clause
    (e.g. ``DEFAULT ' CONSTRAINT evil NOT NULL'::text``); the catalogue check is
    what catches that. Invoking this bare would silently corrupt such a default.
    See ``db/AGENTS.md`` rule 2.
    """
    names = [match.group("name") for match in _NAMED_NOT_NULL.finditer(dump)]
    return _NAMED_NOT_NULL.sub(" NOT NULL", dump), names


def dump_agri(dsn: str, pg_dump: str | None = None, argv_prefix: Sequence[str] | None = None) -> str:
    """Return the schema-only DDL of the ``agri`` schema at ``dsn``, minus non-DDL noise.

    Strips the version banner (which legitimately varies by client/server build)
    and pg18's ``\\restrict``/``\\unrestrict`` markers (not DDL, random token), so
    the output depends only on schema content. Major-version normalisation is
    *not* applied here -- callers decide, because it must be a no-op on the
    canonical major.
    """
    result = subprocess.run(
        [*_dump_argv(pg_dump, argv_prefix), *DUMP_ARGS, "-d", to_libpq_url(dsn)],
        check=True,
        capture_output=True,
        text=True,
    )
    return _PSQL_RESTRICT.sub("", _BANNER.sub("", result.stdout))
