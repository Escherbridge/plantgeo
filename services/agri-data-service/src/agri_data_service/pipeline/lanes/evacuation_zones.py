"""Export one dated snapshot of the evacuation-zones lane from Postgres to Parquet partition(s).

Layer L3: may import `foundation` and `warehouse`; may NOT import method, planes, or interface.
See `AGENTS.md` in this directory for why this lane is a full re-snapshot rather than a day-range
read, and `docs/lanes/evacuation-zones.md` for the source-system facts the loaded SQL transcribes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.pipeline.parquet.objectstore import conform_to_stream_schema
from agri_data_service.warehouse.schemas.evacuation_zones import (
    EVACUATION_ZONES_SCHEMA,
    EVACUATION_ZONES_STREAM,
)

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore, ParquetWriteReceipt

_SNAPSHOT_EXPORT_SQL: Final = text(load_query_sql("pipeline/evacuation_zones_day_export.sql"))

# This layer has "never been censused for polygon byte size" (docs/lanes/evacuation-zones.md §5);
# the closest measured precedent, burn-severity, hid 37.5 MB in 541 rows (~69 KB/row). This bounds
# a single part file's row count so one day's snapshot cannot silently become one oversized object
# the way geo.drought_areas did (995 rows, 500 MB) -- tune down once a real byte census exists.
MAX_ROWS_PER_PART: Final = 200


async def read_evacuation_zones_snapshot(session: AsyncSession, *, snapshot_day: date) -> pa.Table:
    """Return every currently-published Oregon OEM evacuation area, stamped with `snapshot_day`.

    Oregon's feed is current-state only (docs/lanes/evacuation-zones.md §3): there is no honest
    day-range filter this read can apply, so every call returns the same population regardless of
    `snapshot_day` except for the stamp itself -- a full re-snapshot, never a delta. The result is
    conformed and sorted to the registered grain here (not left to `write_partition` alone) so
    that slicing it into parts afterwards preserves one global order across every part file.
    """
    result = await session.execute(_SNAPSHOT_EXPORT_SQL, {"snapshot_day": snapshot_day})
    columns: dict[str, list[object]] = {name: [] for name in EVACUATION_ZONES_SCHEMA.column_names}
    for row in result.mappings():
        for name, values in columns.items():
            values.append(row[name])
    raw = pa.table({name: pa.array(values) for name, values in columns.items()})
    return conform_to_stream_schema(raw, EVACUATION_ZONES_SCHEMA)


def split_into_parts(table: pa.Table) -> tuple[pa.Table, ...]:
    """Slice an already grain-sorted table into row-bounded parts, preserving that order."""
    return tuple(
        table.slice(offset, MAX_ROWS_PER_PART) for offset in range(0, table.num_rows, MAX_ROWS_PER_PART)
    )


async def export_evacuation_zones_day(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
) -> tuple[ParquetWriteReceipt, ...]:
    """Export today's Oregon OEM snapshot to `kind=observed`, spilling across parts as needed.

    A day the upstream genuinely serves nothing -- Oregon's feed down, or (per
    docs/lanes/evacuation-zones.md §6) a genuinely quiet day statewide -- is a governed absence via
    `store.write_absence`, never an empty partition. That refusal is not re-implemented here:
    `write_partition` raises `EmptyPartitionError` on a zero-row table by design, and this function
    always makes at least one `write_partition` call so that refusal fires rather than being
    silently bypassed by an empty `parts` tuple.
    """
    table = await read_evacuation_zones_snapshot(session, snapshot_day=day)
    parts = split_into_parts(table) or (table,)
    return tuple(
        store.write_partition(part, layer=EVACUATION_ZONES_STREAM, kind="observed", day=day, part_index=index)
        for index, part in enumerate(parts)
    )
