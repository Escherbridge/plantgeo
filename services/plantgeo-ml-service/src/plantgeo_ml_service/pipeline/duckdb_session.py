"""A memory-capped, spill-free DuckDB session pointed at the warehouse bucket over httpfs.

Layer L3. Why spilling is pinned to zero rather than tuned, and why both extensions are LOADED and
never INSTALLED, live in `AGENTS.md` in this directory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

import duckdb

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pyarrow as pa  # type: ignore[import-untyped]  # pyarrow ships no stubs

    from plantgeo_ml_service.config import ObjectStoreCredentials, Settings

#: NOT a tuning knob. With spilling disabled an over-budget query raises in about a second; with it
#: enabled the same query eats the disk and takes unrelated processes down with it.
SPILLING_DISABLED: Final = "0GiB"

#: Neither extension is bundled in the DuckDB wheel, so `INSTALL` would be a network fetch and a
#: filesystem write on a run path. The image pre-installs them into the configured directory.
REQUIRED_EXTENSIONS: Final[tuple[str, ...]] = ("httpfs", "spatial")

#: Virtual-host addressing (`bucket.endpoint`), which is what boto3 signs with against this store.
OBJECT_STORE_URL_STYLE: Final = "vhost"

#: A day is a calendar day everywhere in this plane. Pinned rather than inherited: a session zone is
#: one edit away from moving a timestamp's date.
SESSION_TIME_ZONE: Final = "UTC"

#: A single `read_parquet` call takes an explicit key list, never a glob, so the bucket is never
#: asked to expand a prefix. This caps how long that list may be.
MAX_READ_KEYS: Final = 5_000


class DuckDbSessionError(RuntimeError):
    """Raised when a session cannot be opened, or a read is asked for more keys than it may hold."""


class DuckDbExtensionError(DuckDbSessionError):
    """Raised when a required extension is not present in the configured extension directory."""


@dataclass(frozen=True, slots=True)
class DuckDbSession:
    """An open, memory-capped connection plus the bucket URI its reads address."""

    connection: duckdb.DuckDBPyConnection
    bucket_uri: str

    def object_uri(self, relative_key: str) -> str:
        """Return the `s3://` URI for one object key expressed in the frozen partition layout."""
        return f"{self.bucket_uri}/{relative_key}"

    def close(self) -> None:
        """Release the connection and the memory it holds."""
        self.connection.close()

    def __enter__(self) -> DuckDbSession:
        """Return this session, so a caller can bound its lifetime with `with`."""
        return self

    def __exit__(self, *exception: object) -> None:
        """Close the connection whether the body raised or returned."""
        self.close()


def open_session(
    credentials: ObjectStoreCredentials,
    *,
    settings: Settings | None = None,
    prefix: str = "",
) -> DuckDbSession:
    """Open a guarded connection wired to the bucket, with spilling off and both extensions loaded."""
    connection = open_guarded_connection(settings=settings)
    try:
        apply_object_store(connection, credentials)
    except Exception:
        connection.close()
        raise
    root = f"s3://{credentials.bucket}"
    inner = prefix.strip("/")
    return DuckDbSession(connection=connection, bucket_uri=f"{root}/{inner}" if inner else root)


def open_guarded_connection(*, settings: Settings | None = None) -> duckdb.DuckDBPyConnection:
    """Open an in-memory connection carrying the guard and the extensions, with no bucket attached."""
    resolved = _resolved_settings(settings)
    connection = duckdb.connect()  # ':memory:' -- deliberately no local database file
    try:
        connection.execute(
            f"SET memory_limit='{resolved.duckdb_memory_limit}';"
            f"SET threads={resolved.duckdb_thread_count};"
            f"SET max_temp_directory_size='{SPILLING_DISABLED}';"
            f"SET TimeZone='{SESSION_TIME_ZONE}';"
            "SET preserve_insertion_order=false; SET enable_progress_bar=false;"
        )
        load_extensions(connection, directory=resolved.duckdb_extension_directory)
    except Exception:
        connection.close()
        raise
    return connection


def load_extensions(connection: duckdb.DuckDBPyConnection, *, directory: str) -> None:
    """Load httpfs and spatial, refusing to install one mid-run."""
    connection.execute("SET autoinstall_known_extensions=false; SET autoload_known_extensions=false;")
    if Path(directory).is_dir():
        # A SETTING, not a connect-time config: the sibling does the same, so one pre-installed
        # directory serves both services and a missing directory is a loud LOAD failure, not a fetch.
        connection.execute(f"SET extension_directory={sql_literal(directory)};")
    for extension in REQUIRED_EXTENSIONS:
        try:
            connection.execute(f"LOAD {extension};")
        except duckdb.Error as error:
            raise DuckDbExtensionError(
                f"DuckDB extension {extension!r} is not available in {directory!r}; the image installs it "
                f"at build time and a run never fetches one: {error}"
            ) from error


def install_extensions(*, directory: str) -> None:
    """Install the required extensions into `directory`. IMAGE BUILD only, never on a run path."""
    Path(directory).mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(config={"extension_directory": directory})
    try:
        for extension in REQUIRED_EXTENSIONS:
            connection.execute(f"INSTALL {extension};")
    finally:
        connection.close()


def read_parquet_keys(session: DuckDbSession, keys: Sequence[str], *, columns: Sequence[str] | None = None) -> pa.Table:
    """Read an EXPLICIT list of object keys as one Arrow table; never a glob, never a prefix scan.

    An explicit list is what makes the read bounded and the refusal honest: a glob would silently
    answer from whatever the prefix happened to hold, including a half-written day.
    """
    if not keys:
        raise DuckDbSessionError("read_parquet_keys was given no keys; an empty read is a caller bug, not an answer")
    if len(keys) > MAX_READ_KEYS:
        raise DuckDbSessionError(f"a single read may name at most {MAX_READ_KEYS} keys, got {len(keys)}")
    uris = [session.object_uri(key) for key in keys]
    projection = "*" if columns is None else ", ".join(_quoted_identifier(column) for column in columns)
    statement = f"SELECT {projection} FROM read_parquet(?)"
    return session.connection.execute(statement, [uris]).arrow()


def apply_object_store(connection: duckdb.DuckDBPyConnection, credentials: ObjectStoreCredentials) -> None:
    """Point the session at the bucket. The ONLY place a credential is rendered into SQL."""
    endpoint_host = re.sub(r"^https?://", "", credentials.endpoint_url)
    try:
        connection.execute(
            f"SET s3_endpoint={sql_literal(endpoint_host)};"
            f"SET s3_region={sql_literal(credentials.region)};"
            f"SET s3_access_key_id={sql_literal(credentials.access_key_id.get_secret_value())};"
            f"SET s3_secret_access_key={sql_literal(credentials.secret_access_key.get_secret_value())};"
            f"SET s3_url_style={sql_literal(OBJECT_STORE_URL_STYLE)};"
        )
    except duckdb.Error as error:
        # `from error` would chain a message QUOTING THE RENDERED STATEMENT, secret included, into
        # whatever log the caller writes. The cause is dropped rather than redacted in place.
        raise DuckDbSessionError(f"the object-store session could not be opened: {type(error).__name__}") from None


def sql_literal(value: str) -> str:
    """Render one SQL string literal, doubling embedded quotes so a value cannot end the statement."""
    return "'" + value.replace("'", "''") + "'"


def _quoted_identifier(value: str) -> str:
    """Render one SQL identifier, doubling embedded quotes so a column name cannot end the statement."""
    return '"' + value.replace('"', '""') + '"'


def _resolved_settings(settings: Settings | None) -> Settings:
    """Return the caller's settings, or the process-wide ones, imported lazily to keep L3 importable."""
    if settings is not None:
        return settings
    from plantgeo_ml_service.config import get_settings  # noqa: PLC0415 - avoids a package-root import cycle

    return get_settings()
