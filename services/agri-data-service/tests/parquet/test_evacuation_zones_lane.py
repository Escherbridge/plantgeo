"""The evacuation-zones snapshot exporter: grain conformance, part-splitting, and the empty refusal.

The loaded SQL's transcription of `geo.evacuation_zone_tiles`'s WHERE clause is exercised against
a real database elsewhere; these tests pin the behaviour that is pure Python -- that a snapshot is
read in exactly one statement (this is a small features table, not the 11 GB heap `signal.py`
batches around), that the result conforms to and is sorted by the registered grain, that a
snapshot larger than `MAX_ROWS_PER_PART` spills across sequential part files in that same sorted
order, and that a zero-row snapshot is refused by the writer rather than silently producing no
partitions at all.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.pipeline.lanes.evacuation_zones import (
    MAX_ROWS_PER_PART,
    export_evacuation_zones_day,
    read_evacuation_zones_snapshot,
    split_into_parts,
)
from agri_data_service.pipeline.parquet.objectstore import EmptyPartitionError, ObjectStore
from agri_data_service.warehouse.schemas.evacuation_zones import (
    EVACUATION_ZONES_SCHEMA,
    EVACUATION_ZONES_STREAM,
)
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Sequence

AUGUST_SIXTH = date(2026, 8, 6)


def zone_row(index: int) -> dict[str, object]:
    """One exported grain row, shaped exactly as the SQL's column list returns it."""
    global_id = f"{index:04d}"
    return {
        "global_id": global_id,
        "natural_key": f"or-oem-evacuation-areas:{global_id}",
        "producer": "or-oem-evacuation-areas",
        "snapshot_day": AUGUST_SIXTH,
        "evacuation_area_name": f"Zone {global_id}",
        "fire_name": "Test Fire",
        "county": "Jackson",
        "hazard_type": "Wildfire",
        "evacuation_level": 2,
        "evacuation_level_label": "Be Set",
        "severity": "high",
        "structures_within": 12.0,
        "addresses_within": 8.0,
        "population_within": 20.0,
        "editor_name": "oem-editor",
        "observed_at": datetime(2026, 8, 5, 10, tzinfo=UTC),
        "source": "Oregon OEM Fire Evacuation Areas",
        "geometry_wkb": b"wkb-bytes",
        "geometry_version_id": "11111111-1111-1111-1111-111111111111",
        "geometry_version_valid_from": datetime(2026, 8, 1, tzinfo=UTC),
        "geometry_last_confirmed_at": datetime(2026, 8, 6, 9, tzinfo=UTC),
        "data_available_at": None,
        "feature_updated_at": datetime(2026, 8, 6, 9, tzinfo=UTC),
    }


class _Result:
    def __init__(self, rows: Sequence[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> Sequence[dict[str, object]]:
        return self._rows


class RecordingSession:
    """Captures every bound param set and answers with a fixed set of zone rows."""

    def __init__(self, rows: Sequence[dict[str, object]]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._rows = rows

    async def execute(self, _statement: Any, params: dict[str, Any]) -> _Result:
        self.calls.append(params)
        return _Result(self._rows)


@pytest.mark.asyncio
async def test_a_snapshot_is_read_in_exactly_one_statement_not_batched() -> None:
    """Unlike `signal.py`'s 11 GB heap, `geo.features` is small enough for one read per snapshot."""
    session = RecordingSession([zone_row(0), zone_row(1)])

    await read_evacuation_zones_snapshot(session, snapshot_day=AUGUST_SIXTH)  # type: ignore[arg-type]

    assert session.calls == [{"snapshot_day": AUGUST_SIXTH}]


@pytest.mark.asyncio
async def test_the_read_conforms_to_the_registered_schema_and_sorts_to_the_grain() -> None:
    session = RecordingSession([zone_row(2), zone_row(0), zone_row(1)])

    table = await read_evacuation_zones_snapshot(session, snapshot_day=AUGUST_SIXTH)  # type: ignore[arg-type]

    assert table.schema.equals(EVACUATION_ZONES_SCHEMA.arrow_schema)
    assert table.column("natural_key").to_pylist() == [
        "or-oem-evacuation-areas:0000",
        "or-oem-evacuation-areas:0001",
        "or-oem-evacuation-areas:0002",
    ]


def test_split_into_parts_bounds_row_count_and_preserves_order() -> None:
    remainder = 7
    total = MAX_ROWS_PER_PART * 2 + remainder
    table = pa.table({"natural_key": pa.array([f"or-oem-evacuation-areas:{index:04d}" for index in range(total)])})

    parts = split_into_parts(table)

    assert [part.num_rows for part in parts] == [MAX_ROWS_PER_PART, MAX_ROWS_PER_PART, remainder]
    reassembled = pa.concat_tables(parts).column("natural_key").to_pylist()
    assert reassembled == table.column("natural_key").to_pylist()


@pytest.mark.asyncio
async def test_export_spills_a_large_snapshot_across_sequential_parts() -> None:
    remainder = 3
    total = MAX_ROWS_PER_PART + remainder
    session = RecordingSession([zone_row(index) for index in reversed(range(total))])
    backend = RecordingBackend()
    store = ObjectStore(backend)

    receipts = await export_evacuation_zones_day(session, store, day=AUGUST_SIXTH)  # type: ignore[arg-type]

    assert [receipt.row_count for receipt in receipts] == [MAX_ROWS_PER_PART, remainder]
    assert [receipt.key for receipt in receipts] == [
        store.key_for(partition_path(EVACUATION_ZONES_STREAM, "observed", AUGUST_SIXTH, part_index=index))
        for index in range(len(receipts))
    ]
    assert all(receipt.kind == "observed" for receipt in receipts)
    assert sum(receipt.row_count for receipt in receipts) == total


@pytest.mark.asyncio
async def test_a_zero_row_snapshot_is_refused_by_the_writer_not_silently_dropped() -> None:
    """A day Oregon's feed serves nothing must surface as `EmptyPartitionError`, never a no-op."""
    session = RecordingSession([])
    backend = RecordingBackend()
    store = ObjectStore(backend)

    with pytest.raises(EmptyPartitionError):
        await export_evacuation_zones_day(session, store, day=AUGUST_SIXTH)  # type: ignore[arg-type]

    assert list(backend.objects) == []
