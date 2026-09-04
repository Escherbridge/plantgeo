"""Export ONE dated snapshot of the `fire-perimeters` lane from `geo.features` to Parquet partition(s).

Layer L3: may import `foundation` and `warehouse`; may NOT import method, planes, or interface.
See `docs/lanes/fire-perimeters.md` for the source/grain evidence and
`sql/pipeline/fire_perimeters_day_export.sql` for the query this module executes.

THIS LANE IS A `static_lookup` SNAPSHOT, RE-REGISTERED 2026-09-04, and the shape it replaced is why.
It exported one day at a time, filtered on `geo.feature_observation_day` -- but `geo.features` holds
one row per WFIGS incident refreshed in place, not one row per (incident, day), so those partitions
were slices of a snapshot along an axis the source does not have: 177 perimeters across 45 partition
days, near-empty on any single day, and 287 further days holding governed-absence markers. The
partition day is now a VERSION STAMP driven by `sql/pipeline/lane_watermark_fire_perimeters.sql`,
one partition holds the whole standing set, and `evacuation_zones.py` is the sibling this module
now mirrors -- same table, same current-state feed, same full re-snapshot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import conform_to_stream_schema
from agri_data_service.warehouse.schemas.fire_perimeters import (
    FIRE_PERIMETERS_SCHEMA,
    FIRE_PERIMETERS_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore, ParquetWriteReceipt

_SNAPSHOT_EXPORT_SQL: Final = text(load_query_sql("pipeline/fire_perimeters_day_export.sql"))

# `docs/lanes/fire-perimeters.md` #5 measured 130,583 B of geometry per published row on average --
# the heaviest lane besides `geo.drought_areas`/`burn-severity`. Under the snapshot shape EVERY row
# lands in EVERY export rather than being spread over 45 days, so one part would carry the whole
# ~23 MB population unless it is split; the budget below is what makes that split happen. 8 MiB keeps
# each part comfortably under WFIGS_BOUNDS' own 16 MiB response cap (`ingest/wfigs.py:81`), with
# headroom for Parquet's own framing. Never size this lane by row count -- size it by geometry bytes.
MAX_PART_PAYLOAD_BYTES: Final = 8 * 1024 * 1024


async def read_fire_perimeters_snapshot(session: AsyncSession, *, snapshot_day: date) -> pa.Table:
    """Return every currently-published WFIGS incident perimeter, stamped with `snapshot_day`.

    WFIGS's feed is current-state only (`docs/lanes/fire-perimeters.md` #3/#6): there is no honest
    day-range filter this read can apply, so every call returns the same population regardless of
    `snapshot_day` except for the stamp itself -- a full re-snapshot, never a delta. The result is
    conformed and sorted to the registered grain here (not left to `write_partition` alone) so that
    slicing it into parts afterwards preserves one global order across every part file.

    A zero-row result is NOT special-cased into a governed absence the way the old day export did.
    Under `static_lookup` the partition day is a version stamp, and the lane is only ever asked to
    export a day its own watermark named -- and the watermark names a day only when it counted
    published rows (`SourceWatermark(day=None)` otherwise, which `resolve_static_lane` reports as
    `source_empty` and never schedules). An empty read here therefore contradicts the watermark that
    scheduled it, which is a failed read to surface, not a settled fact to record.
    """
    result = await session.execute(_SNAPSHOT_EXPORT_SQL, {"snapshot_day": snapshot_day})
    columns: dict[str, list[object]] = {name: [] for name in FIRE_PERIMETERS_SCHEMA.column_names}
    for row in result.mappings():
        for name, values in columns.items():
            values.append(row[name])
    raw = pa.table({name: pa.array(values) for name, values in columns.items()})
    return conform_to_stream_schema(raw, FIRE_PERIMETERS_SCHEMA)


def _chunk_row_indices_by_geometry_bytes(geometry_lengths: Sequence[int], *, max_bytes: int) -> list[list[int]]:
    """Split row positions into contiguous runs whose summed geometry bytes stay under `max_bytes`.

    Every chunk holds at least one row -- a single perimeter whose own WKB already exceeds the
    budget is not split further, mirroring `ingest/wfigs.py`'s own per-page backstop: "a single
    page that is still too heavy ... fails that page rather than being silently permitted through
    a wider ceiling."

    AN EMPTY INPUT RETURNS `[[]]`, ONE EMPTY CHUNK, AND THAT IS LOAD-BEARING rather than a quirk of
    the seed value. It is what guarantees `export_fire_perimeters_day` always makes at least one
    `write_partition` call, so an empty population meets that writer's own `EmptyPartitionError`
    refusal instead of being silently bypassed by an empty `parts` tuple that reads as success.
    `evacuation_zones.py` buys the same guarantee with its `or (table,)`.
    """
    chunks: list[list[int]] = [[]]
    running = 0
    for index, length in enumerate(geometry_lengths):
        if running and running + length > max_bytes:
            chunks.append([])
            running = 0
        chunks[-1].append(index)
        running += length
    return chunks


async def export_fire_perimeters_day(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
) -> tuple[ParquetWriteReceipt, ...]:
    """Export one WFIGS snapshot to `kind=observed` under `day`, spilling across parts by geometry bytes.

    `day` is the VERSION STAMP this snapshot carries, not a day anything was observed on -- it comes
    from `sql/pipeline/lane_watermark_fire_perimeters.sql` by way of the lane registration, never
    from the cron's run date. Re-exporting the same `day` is how that version is corrected
    (`foundation/parquet/lane_contract.py`), because a static lane rewrites its whole population.

    The rows are already grain-sorted by `read_fire_perimeters_snapshot`, so each spilled part is a
    contiguous, non-overlapping slice of one global order rather than an independently re-sorted
    arbitrary sample. A population that came back empty reaches `write_partition` with a zero-row
    table and raises `EmptyPartitionError`, which the gap-fill driver converts into a governed
    absence; that refusal is deliberately not re-implemented here.
    """
    table = await read_fire_perimeters_snapshot(session, snapshot_day=day)
    geometry_lengths = [len(value.as_py()) for value in table.column("geometry_wkb")]
    return tuple(
        store.write_partition(
            table.take(pa.array(indices, type=pa.int64())),
            layer=FIRE_PERIMETERS_STREAM,
            kind="observed",
            zoom=LANE_BASE_ZOOM_TIER,
            day=day,
            part_index=part_index,
        )
        for part_index, indices in enumerate(
            _chunk_row_indices_by_geometry_bytes(geometry_lengths, max_bytes=MAX_PART_PAYLOAD_BYTES)
        )
    )
