"""Assemble one fetched NHDPlus_HR snapshot into `WATERSHEDS_SCHEMA`'s base-rung Arrow table, and
split it into parts by measured geometry bytes rather than by row count.

THE `direct:` feature_id, AND WHY IT MAY NOT BE NULL. A direct fetch never touches `geo.features`,
so at first glance the honest value looks like NULL -- the same honesty `WATERSHEDS_SCHEMA`'s own
field comment states for a dissolved coarse-rung basin with no single source row behind it
(`warehouse/schemas/watersheds.py:77-81`). But `WATERSHEDS_TIER_DERIVATION` declares
`base_non_null_columns=("feature_id",)`, and `pipeline/parquet/objectstore.py::_refuse_null_base_columns`
enforces that UNCONDITIONALLY at z13: any null there fails the whole write with `ParquetWriteError`,
regardless of producer. The column stays nullable in the Arrow schema ONLY so the coarse rungs may
null it; at the base rung a null reads as a producer regression, not as a legitimate direct-fetch
fact. So this module mints `direct_feature_id(huc12)` instead -- the identical namespacing
`pipeline/direct/drought/rows.py::direct_area_id` applies to `area_id` for the same reason
(`DIRECT_AREA_ID_PREFIX`, "The `direct:` area_id, and why it is not a lineage column"): a value that
satisfies the non-null contract while remaining UNAMBIGUOUSLY not a `geo.features.id` UUID
(`ingest/writer.py::UUID_PATTERN`), so no reader can mistake it for a real foreign key. Coarse rungs
still null it via `ColumnAggregation(column="feature_id", how="null")` regardless of this base value,
since a dissolved HUC10 has no single feature id, synthetic or real, to report.

NO `data_available_at`: measured 100% NULL across every production row today (docs/lanes/
watersheds.md section 5, trap 5), and this writer has no producer for it any more than the Postgres
path did. Unlike `feature_id`, this column carries no `base_non_null_columns` entry, so NULL here is
simply true, not a write-time refusal.

WATERSHEDS IS A BIG PAYLOAD, AND ROW COUNT IS THE WRONG BUDGET. The Postgres exporter this replaced
(`pipeline/lanes/watersheds.py`, deleted 2026-09-06) sliced parts at a flat `ROWS_PER_PART = 1_000`,
which -- at the ~21,572 B WKB/row this lane measures -- writes roughly 21.5 MB per part, nearly
three times the 8 MiB budget
`pipeline/lanes/fire_perimeters.py::MAX_PART_PAYLOAD_BYTES` sets for the next-heaviest geometry
snapshot lane. `chunk_rows_by_geometry_bytes` below copies THAT lane's own
`_chunk_row_indices_by_geometry_bytes` budget and algorithm instead: at ~21,572 B/row, 8 MiB fits
roughly 388 rows, so 9,396 basins spill across roughly 25 parts -- crossing into DOUBLE-DIGIT,
UNPADDED part indices (`part-10.parquet` sorts lexically BEFORE `part-9.parquet`,
`foundation/parquet/paths.py::PART_FILE_STEM`). Never sort a listed part sequence by its raw
`relative_path` string; parse `part_index` out of it first (`try_parse_partition_path`) and sort on
that integer. This module itself never needs to re-sort a listing -- it only WRITES parts, in
strictly increasing `part_index` order -- but `parity.py` and any future reader of this lane's parts
must observe the same rule.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.ingest.watersheds import WATERSHEDS_PROPERTY_SOURCE
from agri_data_service.pipeline.direct.watersheds.support import (
    convert_watershed_geometries_to_wkb,
    watersheds_geometry_session,
)
from agri_data_service.pipeline.parquet.objectstore import conform_to_stream_schema
from agri_data_service.warehouse.schemas.watersheds import WATERSHEDS_SCHEMA

if TYPE_CHECKING:
    from datetime import date

    from agri_data_service.pipeline.direct.watersheds.source import WatershedsSnapshotSource

#: Identical value and identical reasoning to `pipeline/lanes/fire_perimeters.py::MAX_PART_PAYLOAD_BYTES`:
#: 8 MiB keeps each part comfortably inside a single WBDHU12 batch response's own byte budget
#: (`ingest/watersheds.py::WBDHU12_BOUNDS`, 32 MiB per page), with headroom for Parquet's own framing.
#: Never size this lane by row count -- size it by geometry bytes.
MAX_PART_PAYLOAD_BYTES: Final = 8 * 1024 * 1024

#: Mirrors `pipeline/direct/drought/rows.py::DIRECT_AREA_ID_PREFIX` exactly: namespaces a direct
#: row's `feature_id` as never a genuine `geo.features.id`. See the module docstring.
DIRECT_FEATURE_ID_PREFIX: Final = "direct"


def direct_feature_id(huc12: str) -> str:
    """Build the deterministic, `direct:`-namespaced id a direct row carries in place of a real one."""
    return f"{DIRECT_FEATURE_ID_PREFIX}:{huc12}"


def watersheds_snapshot_table(source: WatershedsSnapshotSource, *, release_day: date) -> pa.Table:
    """Build the whole-lane base-rung table for one release day, WKB-converted through DuckDB spatial.

    An empty `source.accepted` still returns a genuinely zero-row, schema-shaped table -- never
    special-cased away -- so `store.write_partition`'s own `EmptyPartitionError` is what turns an
    honestly empty extent into a governed absence. That is the contract the Postgres path this writer
    replaced stated in its own docstring, quoted here because that module (`pipeline/lanes/
    watersheds.py`) was deleted on 2026-09-06: "a source that genuinely has nothing published still
    reaches `store.write_partition` with a zero-row table."

    SORTED TO THE GRAIN HERE, not left to `write_partition` alone -- the identical discipline
    `pipeline/lanes/fire_perimeters.py::read_fire_perimeters_snapshot` documents: slicing an
    already-sorted table into parts afterwards preserves one global huc12 order across every part
    file, rather than each part independently re-sorting an arbitrary sample of the whole.
    """
    if not source.accepted:
        return pa.Table.from_pylist([], schema=WATERSHEDS_SCHEMA.arrow_schema)
    with watersheds_geometry_session() as session:
        wkb_by_huc12 = convert_watershed_geometries_to_wkb(
            session, [(record.huc12, record.geometry) for record in source.accepted]
        )
    rows = [
        {
            "huc12": record.huc12,
            "name": record.name,
            "areasqkm": record.areasqkm,
            "tohuc": record.tohuc,
            "states": record.states,
            "hutype": record.hutype,
            "source": WATERSHEDS_PROPERTY_SOURCE,
            "observed_at": record.observed_at,
            "data_available_at": None,
            "release_day": release_day,
            "feature_id": direct_feature_id(record.huc12),
            "geom": wkb_by_huc12[record.huc12],
        }
        for record in source.accepted
    ]
    raw = pa.Table.from_pylist(rows, schema=WATERSHEDS_SCHEMA.arrow_schema)
    return conform_to_stream_schema(raw, WATERSHEDS_SCHEMA)


def chunk_rows_by_geometry_bytes(table: pa.Table, *, max_bytes: int = MAX_PART_PAYLOAD_BYTES) -> tuple[pa.Table, ...]:
    """Split `table` into contiguous row-index runs whose summed `geom` bytes stay under `max_bytes`.

    Copies `pipeline/lanes/fire_perimeters.py::_chunk_row_indices_by_geometry_bytes` exactly, down to
    the empty-input guarantee: a ZERO-ROW `table` still returns a one-element tuple holding an empty
    table, never an empty tuple, so the caller always makes at least one `write_partition` call and a
    genuinely empty extent meets `EmptyPartitionError`'s refusal instead of silently skipping the
    write altogether. `table` is expected to already be sorted to the grain (`conform_to_stream_schema`
    inside `store.write_partition`, or the caller's own upstream sort) so each chunk is a contiguous
    slice of one global order rather than an arbitrary resample.
    """
    geometry_lengths = [len(value.as_py()) for value in table.column("geom")]
    chunks: list[list[int]] = [[]]
    running = 0
    for index, length in enumerate(geometry_lengths):
        if running and running + length > max_bytes:
            chunks.append([])
            running = 0
        chunks[-1].append(index)
        running += length
    return tuple(table.take(pa.array(indices, type=pa.int64())) for indices in chunks)


__all__ = [
    "DIRECT_FEATURE_ID_PREFIX",
    "MAX_PART_PAYLOAD_BYTES",
    "chunk_rows_by_geometry_bytes",
    "direct_feature_id",
    "watersheds_snapshot_table",
]
