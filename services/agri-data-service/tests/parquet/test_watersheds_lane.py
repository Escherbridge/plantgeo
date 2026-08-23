"""The watersheds-lane exporter: grain conformance, part-spilling, and the empty-release refusal.

The governed SQL itself is exercised against a real database elsewhere; these tests pin the
behaviour that is pure Python -- that the release day is bound into every row rather than
derived from source data, that a release larger than one part's row budget spills across
`part-N` files, and that a genuinely empty release is refused by the writer rather than this
lane silently reporting success with nothing written.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.lanes import watersheds as watersheds_lane
from agri_data_service.pipeline.lanes.watersheds import (
    export_watersheds_release,
    read_watersheds_release,
)
from agri_data_service.pipeline.parquet.objectstore import EmptyPartitionError, ObjectStore
from agri_data_service.warehouse.schemas.watersheds import WATERSHEDS_SCHEMA, WATERSHEDS_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Sequence

AUGUST_SEVENTH = date(2026, 8, 7)


def watershed_row(huc12: str, release_day: date) -> dict[str, object]:
    """One exported grain row, shaped exactly as the SQL's column list returns it."""
    return {
        "huc12": huc12,
        "name": "Test Basin",
        "areasqkm": 12.5,
        "tohuc": "170900010101",
        "states": "OR,WA",
        "hutype": "S",
        "source": "USGS NHDPlus HR WBDHU12",
        "observed_at": datetime(2013, 1, 18, tzinfo=UTC),
        "data_available_at": None,
        "release_day": release_day,
        "feature_id": "11111111-1111-1111-1111-111111111111",
        "geom": b"\x01\x03\x00\x00\x00",
    }


class _Result:
    def __init__(self, rows: Sequence[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> Sequence[dict[str, object]]:
        return self._rows


class RecordingSession:
    """Captures each statement's bound params and answers with one row per configured huc12."""

    def __init__(self, *, huc_codes: tuple[str, ...] = ("120090601",)) -> None:
        self.calls: list[dict[str, Any]] = []
        self._huc_codes = huc_codes

    async def execute(self, _statement: Any, params: dict[str, Any]) -> _Result:
        self.calls.append(params)
        release_day = params["release_day"]
        return _Result([watershed_row(code, release_day) for code in self._huc_codes])


@pytest.mark.asyncio
async def test_release_day_is_bound_into_every_row_not_derived_from_source_data() -> None:
    """A HUC12 boundary carries no per-row day of its own (docs/lanes/watersheds.md sections 3, 7)."""
    session = RecordingSession(huc_codes=("120090601", "120090602"))

    table = await read_watersheds_release(session, release_day=AUGUST_SEVENTH)  # type: ignore[arg-type]

    assert session.calls == [{"release_day": AUGUST_SEVENTH}]
    assert table.column("release_day").to_pylist() == [AUGUST_SEVENTH, AUGUST_SEVENTH]


@pytest.mark.asyncio
async def test_the_read_conforms_to_the_registered_schema() -> None:
    session = RecordingSession(huc_codes=("120090601", "120090602", "120090603"))

    table = await read_watersheds_release(session, release_day=AUGUST_SEVENTH)  # type: ignore[arg-type]

    expected_rows = 3
    assert table.schema.equals(WATERSHEDS_SCHEMA.arrow_schema)
    assert table.num_rows == expected_rows


@pytest.mark.asyncio
async def test_the_export_lands_at_the_observed_partition_sorted_to_the_grain() -> None:
    session = RecordingSession(huc_codes=("120090603", "120090601", "120090602"))
    backend = RecordingBackend()
    store = ObjectStore(backend)

    receipts = await export_watersheds_release(session, store, day=AUGUST_SEVENTH)  # type: ignore[arg-type]

    expected_rows = 3
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.key == partition_path(WATERSHEDS_STREAM, "observed", LANE_BASE_ZOOM_TIER, AUGUST_SEVENTH, 0)
    assert receipt.kind == "observed"
    assert receipt.row_count == expected_rows


@pytest.mark.asyncio
async def test_a_release_larger_than_one_part_spills_across_part_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HUC12 geometry is heavy; a release must be able to spread across bounded part files."""
    monkeypatch.setattr(watersheds_lane, "ROWS_PER_PART", 2)
    huc_codes = ("120090601", "120090602", "120090603", "120090604", "120090605")
    session = RecordingSession(huc_codes=huc_codes)
    backend = RecordingBackend()
    store = ObjectStore(backend)

    receipts = await export_watersheds_release(session, store, day=AUGUST_SEVENTH)  # type: ignore[arg-type]

    assert [receipt.row_count for receipt in receipts] == [2, 2, 1]
    assert [receipt.key for receipt in receipts] == [
        partition_path(WATERSHEDS_STREAM, "observed", LANE_BASE_ZOOM_TIER, AUGUST_SEVENTH, part_index)
        for part_index in range(3)
    ]
    # Every part still reads as one present day; gap detection lists the directory, never a file.
    assert store.list_partition_keys(WATERSHEDS_STREAM, "observed", LANE_BASE_ZOOM_TIER) == tuple(
        receipt.relative_path for receipt in receipts
    )


@pytest.mark.asyncio
async def test_an_empty_release_is_refused_rather_than_silently_writing_nothing() -> None:
    """A source with no published basins must fail loudly, not report success with zero receipts."""
    session = RecordingSession(huc_codes=())
    backend = RecordingBackend()
    store = ObjectStore(backend)

    with pytest.raises(EmptyPartitionError):
        await export_watersheds_release(session, store, day=AUGUST_SEVENTH)  # type: ignore[arg-type]

    assert backend.objects == {}
