"""`chunk_rows_by_geometry_bytes`'s byte-budget chunking, `direct_feature_id`'s namespace, and
`watersheds_snapshot_table`'s empty-population passthrough.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.lane_contract import SourceWatermark
from agri_data_service.pipeline.direct.watersheds.rows import (
    DIRECT_FEATURE_ID_PREFIX,
    chunk_rows_by_geometry_bytes,
    direct_feature_id,
    watersheds_snapshot_table,
)
from agri_data_service.pipeline.direct.watersheds.source import WatershedsSnapshotSource
from agri_data_service.warehouse.schemas.watersheds import WATERSHEDS_SCHEMA

RELEASE_DAY = date(2026, 8, 7)


def _table(geometry_sizes: list[int]) -> pa.Table:
    rows = [
        {
            "huc12": f"{index:012d}",
            "name": None,
            "areasqkm": None,
            "tohuc": None,
            "states": None,
            "hutype": None,
            "source": "USGS NHDPlus HR WBDHU12",
            "observed_at": None,
            "data_available_at": None,
            "release_day": RELEASE_DAY,
            "feature_id": direct_feature_id(f"{index:012d}"),
            "geom": b"\x00" * size,
        }
        for index, size in enumerate(geometry_sizes)
    ]
    return pa.Table.from_pylist(rows, schema=WATERSHEDS_SCHEMA.arrow_schema)


def test_direct_feature_id_is_namespaced_never_a_bare_huc12() -> None:
    built = direct_feature_id("170900011201")

    assert built == f"{DIRECT_FEATURE_ID_PREFIX}:170900011201"
    assert built.startswith("direct:")


def test_rows_under_the_budget_stay_in_one_chunk() -> None:
    table = _table([10, 10, 10])

    chunks = chunk_rows_by_geometry_bytes(table, max_bytes=100)

    assert len(chunks) == 1
    assert chunks[0].num_rows == 3  # noqa: PLR2004 - all three rows stay in the one chunk


def test_a_row_that_would_overflow_the_budget_starts_a_new_chunk() -> None:
    table = _table([60, 60, 60])

    chunks = chunk_rows_by_geometry_bytes(table, max_bytes=100)

    assert [chunk.num_rows for chunk in chunks] == [1, 1, 1]


def test_a_single_row_heavier_than_the_budget_is_not_split_further() -> None:
    table = _table([500])

    chunks = chunk_rows_by_geometry_bytes(table, max_bytes=100)

    assert len(chunks) == 1
    assert chunks[0].num_rows == 1


def test_an_empty_table_returns_one_empty_chunk_not_zero_chunks() -> None:
    """DO NOT DELETE. `store.write_partition` must still be called once, so an empty extent meets
    `EmptyPartitionError`'s refusal instead of a silently skipped write."""
    table = _table([])

    chunks = chunk_rows_by_geometry_bytes(table, max_bytes=100)

    assert len(chunks) == 1
    assert chunks[0].num_rows == 0


def test_watersheds_snapshot_table_on_an_empty_source_returns_a_zero_row_schema_shaped_table() -> None:
    source = WatershedsSnapshotSource(
        bbox="-125,42,-111,49",
        accepted=(),
        rejected_count=0,
        fetched_at=datetime(2026, 8, 7, tzinfo=UTC),
        watermark=SourceWatermark(day=None, basis="test"),
    )

    table = watersheds_snapshot_table(source, release_day=RELEASE_DAY)

    assert table.num_rows == 0
    assert table.schema.equals(WATERSHEDS_SCHEMA.arrow_schema)
