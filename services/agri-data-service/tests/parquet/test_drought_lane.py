"""The drought-lane exporter: grain conformance, byte-bounded part-spilling, and the empty-release refusal.

The governed SQL itself (to_date parsing, the WKB cast, the SRID) is exercised against a real
database elsewhere; these tests pin what is pure Python here -- that a release is filtered by its
exact ISO valid_date, that a release whose serialized bytes exceed the per-part byte budget spills
across part-N files sized from ITS OWN measured bytes rather than a fixed row-count guess
(geo.drought_areas measures ~500 KB/row: conductor/RUNBOOK.md section 0.24.1), that the write lands
sorted to the (valid_date, dm_category) grain, and that a Tuesday USDM never published is refused
by the writer rather than this lane reporting success with nothing written.
"""

from __future__ import annotations

import io
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.lanes import drought as drought_lane
from agri_data_service.pipeline.lanes.drought import (
    export_drought_release,
    read_drought_release,
)
from agri_data_service.pipeline.parquet.objectstore import EmptyPartitionError, ObjectStore
from agri_data_service.warehouse.schemas.drought import DROUGHT_SCHEMA, DROUGHT_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Sequence

AUGUST_FOURTH = date(2026, 8, 4)  # a real USDM release Tuesday (ingest/usdm.py TUESDAY == 1)

# A geom blob heavy enough that PARQUET_TARGET_PART_BYTES can be monkeypatched below it to force
# spillage, standing in for the WKB payload that dominates a real row's weight.
_HEAVY_GEOM: bytes = b"\x01\x06\x00\x00\x00" + b"\x00" * 512


def drought_row(dm_category: int, valid_date: date, *, geom: bytes = _HEAVY_GEOM) -> dict[str, object]:
    """One exported grain row, shaped exactly as the SQL's column list returns it."""
    return {
        "area_id": "11111111-1111-1111-1111-111111111111",
        "valid_date": valid_date,
        "dm_category": dm_category,
        "source_url": f"https://droughtmonitor.unl.edu/data/json/usdm_{valid_date:%Y%m%d}.json",
        "ingested_at": datetime(2026, 8, 6, 12, tzinfo=UTC),
        "geom": geom,
    }


class _Result:
    def __init__(self, rows: Sequence[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> Sequence[dict[str, object]]:
        return self._rows


class RecordingSession:
    """Captures each statement's bound params and answers with one row per configured class."""

    def __init__(self, *, dm_categories: tuple[int, ...] = (0, 1, 2, 3, 4)) -> None:
        self.calls: list[dict[str, Any]] = []
        self._dm_categories = dm_categories

    async def execute(self, _statement: Any, params: dict[str, Any]) -> _Result:
        self.calls.append(params)
        valid_date = date.fromisoformat(params["valid_date"])
        return _Result([drought_row(category, valid_date) for category in self._dm_categories])


@pytest.mark.asyncio
async def test_the_release_is_filtered_by_its_exact_iso_valid_date_string() -> None:
    """One release is one indivisible unit (ingest/usdm.py, DroughtRelease); never a date range."""
    session = RecordingSession(dm_categories=(0, 1))

    table = await read_drought_release(session, valid_date=AUGUST_FOURTH)  # type: ignore[arg-type]

    assert session.calls == [{"valid_date": "2026-08-04"}]
    assert table.column("valid_date").to_pylist() == [AUGUST_FOURTH, AUGUST_FOURTH]


@pytest.mark.asyncio
async def test_the_read_conforms_to_the_registered_schema() -> None:
    session = RecordingSession(dm_categories=(0, 2, 4))

    table = await read_drought_release(session, valid_date=AUGUST_FOURTH)  # type: ignore[arg-type]

    expected_rows = 3
    assert table.schema.equals(DROUGHT_SCHEMA.arrow_schema)
    assert table.num_rows == expected_rows


@pytest.mark.asyncio
async def test_the_export_lands_at_the_observed_partition_sorted_to_the_grain() -> None:
    session = RecordingSession(dm_categories=(4, 0, 2))
    backend = RecordingBackend()
    store = ObjectStore(backend)

    receipts = await export_drought_release(session, store, day=AUGUST_FOURTH)  # type: ignore[arg-type]

    expected_rows = 3
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.key == partition_path(DROUGHT_STREAM, "observed", LANE_BASE_ZOOM_TIER, AUGUST_FOURTH, 0)
    assert receipt.kind == "observed"
    assert receipt.row_count == expected_rows

    written = pq.read_table(io.BytesIO(backend.objects[receipt.key]))
    assert written.column("dm_category").to_pylist() == [0, 2, 4]


@pytest.mark.asyncio
async def test_a_release_heavier_than_the_byte_budget_spills_across_part_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spillage is bounded by the release's OWN measured bytes, never a fixed row-count guess."""
    monkeypatch.setattr(drought_lane, "PARQUET_TARGET_PART_BYTES", 700)
    session = RecordingSession(dm_categories=(0, 1, 2, 3, 4))
    backend = RecordingBackend()
    store = ObjectStore(backend)

    receipts = await export_drought_release(session, store, day=AUGUST_FOURTH)  # type: ignore[arg-type]

    expected_total_rows = 5
    assert len(receipts) > 1
    assert sum(receipt.row_count for receipt in receipts) == expected_total_rows
    assert [receipt.key for receipt in receipts] == [
        partition_path(DROUGHT_STREAM, "observed", LANE_BASE_ZOOM_TIER, AUGUST_FOURTH, part_index)
        for part_index in range(len(receipts))
    ]
    # Every part still reads as one present release; gap detection lists the directory, never a file.
    assert store.list_partition_keys(DROUGHT_STREAM, "observed", LANE_BASE_ZOOM_TIER) == tuple(
        receipt.relative_path for receipt in receipts
    )


@pytest.mark.asyncio
async def test_an_unpublished_release_is_refused_rather_than_silently_writing_nothing() -> None:
    """A Tuesday USDM never published must fail loudly, not report success with zero receipts."""
    session = RecordingSession(dm_categories=())
    backend = RecordingBackend()
    store = ObjectStore(backend)

    with pytest.raises(EmptyPartitionError):
        await export_drought_release(session, store, day=AUGUST_FOURTH)  # type: ignore[arg-type]

    assert backend.objects == {}
