"""Where the runtime images keep DuckDB's extensions, shared by every session that loads one.

Both images create the runtime user with home `/nonexistent`, so DuckDB's default extension directory
(`$HOME/.duckdb`) can never exist there: a session that loads `spatial` or `httpfs` without pointing at
this directory first fails with "Can't find the home directory at '/nonexistent'". The serving session
always did; the coarse-rung derivation session did not, which is how every geometry lane's z9 rung
failed in production on 2026-09-02 (see warehouse/parquet/AGENTS.md, "The derivation session and the
extension directory").
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

#: The image-build install target for httpfs and spatial; both Dockerfiles create it and chown it to the
#: runtime user. Applied only when it EXISTS, so a developer machine falls back to DuckDB's own
#: `$HOME/.duckdb`. `DUCKDB_EXTENSION_DIRECTORY` as an environment variable is NOT honoured by DuckDB
#: 1.5.4 (measured in a container 2026-08-25), so the directory has to arrive as a SET statement or not at all.
SERVING_EXTENSION_DIRECTORY: Final = "/opt/duckdb-extensions"


def sql_string_literal(value: str) -> str:
    """Quote a value as a DuckDB string literal, doubling embedded quotes."""
    return "'" + value.replace("'", "''") + "'"


def extension_directory_setting(directory: str = SERVING_EXTENSION_DIRECTORY) -> str | None:
    """The `SET extension_directory` statement a session must run before its first LOAD, or None off-image."""
    if not Path(directory).is_dir():
        return None
    return f"SET extension_directory = {sql_string_literal(directory)}"


__all__ = ["SERVING_EXTENSION_DIRECTORY", "extension_directory_setting", "sql_string_literal"]
