"""A memory-bounded DuckDB session over the Parquet warehouse. See `AGENTS.md` in this directory.

Read-only. Never writes to Postgres, never creates a local database file, and is forbidden
from spilling to local disk -- a query that would exceed the cap errors instead of consuming
the machine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import duckdb

SERVICE_ROOT: Final = Path(__file__).resolve().parent.parent

# A cross-join of many large multipolygons against many points will exhaust any cap; these
# limits exist to make that failure loud and local rather than fatal to the host. See AGENTS.md
# "Why the memory ceiling is not advisory".
DEFAULT_MEMORY_LIMIT: Final = "1600MB"
DEFAULT_THREAD_COUNT: Final = 3
SPILLING_DISABLED: Final = "0GiB"

# Objects written before the zoom ladder existed sit at a shallower key depth and would be
# double-counted by a `zoom=*` glob. Every read pins one tier explicitly.
BASE_ZOOM_TIER: Final = 13


def read_environment_file(env_path: Path | None = None) -> dict[str, str]:
    """Parse .env without importing the service's settings machinery."""
    path = env_path or (SERVICE_ROOT / ".env")
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        matched = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line.strip())
        if matched:
            values[matched.group(1)] = matched.group(2)
    return values


@dataclass(frozen=True, slots=True)
class WarehouseSession:
    """An open DuckDB connection plus the bucket URI its readers should address."""

    connection: duckdb.DuckDBPyConnection
    bucket_uri: str

    def partition_glob(self, layer: str, kind: str, zoom: int = BASE_ZOOM_TIER, year: str = "*", month: str = "*") -> str:
        """The read_parquet glob for one layer at one zoom tier, with the tier always pinned."""
        return (
            f"{self.bucket_uri}/layer={layer}/kind={kind}/zoom={zoom:02d}/"
            f"year={year}/month={month}/day=*/part-*.parquet"
        )


def open_warehouse_session(
    memory_limit: str = DEFAULT_MEMORY_LIMIT,
    thread_count: int = DEFAULT_THREAD_COUNT,
    env_path: Path | None = None,
) -> WarehouseSession:
    """Open an in-memory DuckDB session wired to the object store, with spilling disabled."""
    environment = read_environment_file(env_path)
    missing = [
        key
        for key in (
            "OBJECT_STORE_ENDPOINT_URL",
            "OBJECT_STORE_BUCKET",
            "OBJECT_STORE_ACCESS_KEY_ID",
            "OBJECT_STORE_SECRET_ACCESS_KEY",
            "OBJECT_STORE_REGION",
        )
        if key not in environment
    ]
    if missing:
        raise RuntimeError(f"object store credentials absent from .env: {', '.join(missing)}")

    endpoint_host = re.sub(r"^https?://", "", environment["OBJECT_STORE_ENDPOINT_URL"])
    connection = duckdb.connect()  # ':memory:' -- deliberately no local database file
    connection.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    connection.execute(
        f"SET memory_limit='{memory_limit}'; SET threads={thread_count};"
        f"SET max_temp_directory_size='{SPILLING_DISABLED}';"
        "SET preserve_insertion_order=false; SET enable_progress_bar=false;"
    )
    connection.execute(
        f"SET s3_endpoint='{endpoint_host}';"
        f"SET s3_region='{environment['OBJECT_STORE_REGION']}';"
        f"SET s3_access_key_id='{environment['OBJECT_STORE_ACCESS_KEY_ID']}';"
        f"SET s3_secret_access_key='{environment['OBJECT_STORE_SECRET_ACCESS_KEY']}';"
        "SET s3_url_style='path';"
    )
    return WarehouseSession(connection=connection, bucket_uri=f"s3://{environment['OBJECT_STORE_BUCKET']}")
