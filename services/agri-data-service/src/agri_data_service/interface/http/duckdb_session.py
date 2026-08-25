"""The memory-bounded DuckDB session every serving read runs inside. Spilling is DISABLED.

Layer L4. Ported from `analysis/warehouse_session.py`, which proved these settings against this
bucket; that module reads `.env` directly and lives outside `src/`, so it cannot be imported here.
The guard block below is the whole reason this module exists -- see `AGENTS.md` in this directory,
"Why the memory ceiling is not advisory".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import duckdb

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

# Virtual-host addressing (`bucket.endpoint`), which is what boto3 uses against this store. Path
# style was MEASURED to work too, 2026-08-25, for explicit-key reads against `t3.storageapi.dev`;
# vhost is kept because it is the addressing the rest of the service already signs with.
OBJECT_STORE_URL_STYLE: Final = "vhost"


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
    endpoint_host = re.sub(r"^https?://", "", credentials.endpoint_url)
    connection = duckdb.connect()  # ':memory:' -- deliberately no local database file
    connection.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    connection.execute(
        f"SET memory_limit='{memory_limit}'; SET threads={thread_count};"
        f"SET max_temp_directory_size='{SPILLING_DISABLED}';"
        "SET preserve_insertion_order=false; SET enable_progress_bar=false;"
    )
    connection.execute(
        f"SET s3_endpoint='{endpoint_host}';"
        f"SET s3_region='{credentials.region}';"
        f"SET s3_access_key_id='{credentials.access_key_id.get_secret_value()}';"
        f"SET s3_secret_access_key='{credentials.secret_access_key.get_secret_value()}';"
        f"SET s3_url_style='{OBJECT_STORE_URL_STYLE}';"
    )
    root = f"s3://{credentials.bucket}"
    inner = prefix.strip("/")
    return ServingSession(connection=connection, bucket_uri=f"{root}/{inner}" if inner else root)
