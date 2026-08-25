"""The memory-bounded DuckDB session every serving read runs inside. Spilling is DISABLED.

Layer L4. Ported from `analysis/warehouse_session.py`, which proved these settings against this
bucket; that module reads `.env` directly and lives outside `src/`, so it cannot be imported here.
The guard block below is the whole reason this module exists -- see `AGENTS.md` in this directory,
"Why the memory ceiling is not advisory".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

import duckdb

from agri_data_service.interface.http import faults

if TYPE_CHECKING:
    from agri_data_service.config import ObjectStoreCredentials

# A serving process answers several requests at once, so a request's ceiling is a FRACTION of the
# host rather than the analysis session's share of an idle machine. `analysis/AGENTS.md` records the
# incident: a cross join materialising ~140,000-vertex USDM polygons per output row spilled until it
# consumed the host. These three settings are what make that failure loud, local and one second long.
SERVING_MEMORY_LIMIT: Final = "1200MB"
SERVING_THREAD_COUNT: Final = 2

# NOT a tuning knob. With spilling disabled an over-budget query raises in about a second; with it
# enabled the same query eats the disk and takes unrelated processes down with it.
SPILLING_DISABLED: Final = "0GiB"

# `duckdb.connect()` with no argument creates a NEW database instance, so `memory_limit` binds to ONE
# connection and not to the process. The per-request session is therefore only half a guard: the
# other half is admission control, and this is the number it admits. Process ceiling =
# SERVING_MAX_CONCURRENT_READS x SERVING_MEMORY_LIMIT, asserted against the declared ceiling below.
SERVING_MAX_CONCURRENT_READS: Final = 3

# What this container is assumed to be able to give DuckDB, in bytes. **NOT MEASURED against the
# Railway service's cgroup** -- the only cap the RUNBOOK records is the DATABASE container's 2 GB,
# which is a different service. It is the number the concurrency above is sized to, so raising either
# without the other fails `test_the_process_memory_ceiling_bounds_concurrency_times_the_per_read_limit`.
# Check it against the serving container's real limit before cutover; if that limit is 2 GB, this
# number and SERVING_MAX_CONCURRENT_READS both have to come down.
SERVING_PROCESS_MEMORY_CEILING_BYTES: Final = 3_600_000_000

# Virtual-host addressing (`bucket.endpoint`), which is what boto3 uses against this store. Path
# style was MEASURED to work too, 2026-08-25, for explicit-key reads against `t3.storageapi.dev`;
# vhost is kept because it is the addressing the rest of the service already signs with.
OBJECT_STORE_URL_STYLE: Final = "vhost"

# LOADED, never INSTALLED. Neither is bundled in the DuckDB Python wheel, so `INSTALL` downloads from
# extensions.duckdb.org into `$HOME/.duckdb` -- a network fetch and a filesystem write on a request
# path, and in the image that home is `/nonexistent`. The image pre-installs them; see `AGENTS.md`.
SERVING_EXTENSIONS: Final[tuple[str, ...]] = ("httpfs", "spatial")

# Where the image pre-installs them. Applied only when it EXISTS, so a developer machine falls back
# to DuckDB's own `$HOME/.duckdb`. `DUCKDB_EXTENSION_DIRECTORY` as an environment variable is NOT
# honoured by DuckDB 1.5.4 -- measured in a container 2026-08-25, it still resolved `$HOME/.duckdb` --
# so the directory has to arrive as this SETTING or not at all.
SERVING_EXTENSION_DIRECTORY: Final = "/opt/duckdb-extensions"

# A day is a calendar day everywhere in this plane. Pinned rather than inherited: the review machine
# defaulted to `America/Denver`, and a session zone is one edit away from moving a timestamp's date.
SERVING_TIME_ZONE: Final = "UTC"


@dataclass(frozen=True, slots=True)
class ServingSession:
    """An open, memory-capped DuckDB connection plus the bucket URI its reads address."""

    connection: duckdb.DuckDBPyConnection
    bucket_uri: str

    def object_uri(self, relative_key: str) -> str:
        """Return the `s3://` URI for one object key expressed in the frozen partition layout."""
        return f"{self.bucket_uri}/{relative_key}"

    def close(self) -> None:
        """Release the connection and the memory it holds."""
        self.connection.close()


def open_serving_session(
    credentials: ObjectStoreCredentials,
    *,
    prefix: str = "",
    memory_limit: str = SERVING_MEMORY_LIMIT,
    thread_count: int = SERVING_THREAD_COUNT,
) -> ServingSession:
    """Open an in-memory DuckDB session wired to the object store, with spilling disabled."""
    connection = open_guarded_connection(memory_limit=memory_limit, thread_count=thread_count)
    try:
        _apply_object_store(connection, credentials)
    except Exception:
        connection.close()
        raise
    root = f"s3://{credentials.bucket}"
    inner = prefix.strip("/")
    return ServingSession(connection=connection, bucket_uri=f"{root}/{inner}" if inner else root)


def open_guarded_connection(
    *,
    memory_limit: str = SERVING_MEMORY_LIMIT,
    thread_count: int = SERVING_THREAD_COUNT,
) -> duckdb.DuckDBPyConnection:
    """Open an in-memory connection carrying the guard and the extensions, with no bucket attached."""
    connection = duckdb.connect()  # ':memory:' -- deliberately no local database file
    try:
        connection.execute(
            f"SET memory_limit='{memory_limit}'; SET threads={thread_count};"
            f"SET max_temp_directory_size='{SPILLING_DISABLED}';"
            f"SET TimeZone='{SERVING_TIME_ZONE}';"
            "SET preserve_insertion_order=false; SET enable_progress_bar=false;"
        )
        load_serving_extensions(connection)
    except Exception:
        connection.close()
        raise
    return connection


def load_serving_extensions(
    connection: duckdb.DuckDBPyConnection,
    *,
    directory: str = SERVING_EXTENSION_DIRECTORY,
) -> None:
    """Load the two extensions this plane reads through, refusing to install one mid-request."""
    # Auto-install is what would otherwise turn a missing extension into a silent download on a
    # request path; with it off, a broken image says so on the first read instead of hanging.
    connection.execute("SET autoinstall_known_extensions=false; SET autoload_known_extensions=false;")
    if Path(directory).is_dir():
        connection.execute(f"SET extension_directory={_sql_literal(directory)};")
    for extension in SERVING_EXTENSIONS:
        try:
            connection.execute(f"LOAD {extension};")
        except duckdb.Error as exc:
            raise faults.serving_extension_unavailable(extension=extension, detail=str(exc)) from exc


def install_serving_extensions(*, directory: str = SERVING_EXTENSION_DIRECTORY) -> None:
    """Install the serving extensions into `directory`. IMAGE BUILD only -- never on a request path."""
    Path(directory).mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(
        config={
            "extension_directory": directory,
            "memory_limit": SERVING_MEMORY_LIMIT,
            "threads": SERVING_THREAD_COUNT,
            "max_temp_directory_size": SPILLING_DISABLED,
        }
    )
    try:
        for extension in SERVING_EXTENSIONS:
            connection.execute(f"INSTALL {extension};")
    finally:
        connection.close()


def _apply_object_store(connection: duckdb.DuckDBPyConnection, credentials: ObjectStoreCredentials) -> None:
    """Point the session at the bucket. The ONLY place a credential is rendered into SQL."""
    endpoint_host = re.sub(r"^https?://", "", credentials.endpoint_url)
    try:
        connection.execute(
            f"SET s3_endpoint={_sql_literal(endpoint_host)};"
            f"SET s3_region={_sql_literal(credentials.region)};"
            f"SET s3_access_key_id={_sql_literal(credentials.access_key_id.get_secret_value())};"
            f"SET s3_secret_access_key={_sql_literal(credentials.secret_access_key.get_secret_value())};"
            f"SET s3_url_style={_sql_literal(OBJECT_STORE_URL_STYLE)};"
        )
    except duckdb.Error as exc:
        # `from exc` would chain a message that QUOTES THE RENDERED STATEMENT, secret included, into
        # the log the route writes. The cause is deliberately dropped rather than redacted in place.
        raise faults.object_store_session_unavailable(detail=type(exc).__name__) from None


def _sql_literal(value: str) -> str:
    """Render one SQL string literal, doubling embedded quotes so a value cannot end the statement."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"
