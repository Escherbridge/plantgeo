"""What the direct watersheds adapter publishes, and what it governs absent -- through the real
shared `fill_one_lane_day` finalizer, never through a hand-rolled substitute.

NEEDS DuckDB's `spatial` extension twice over: once for `support.py`'s base-rung conversion and once
for `fill_one_lane_day`'s own z9/z5/z0 `HierarchicalDissolve` derivation
(`warehouse/schemas/watersheds.py::WATERSHEDS_TIER_DERIVATION`) -- the identical shape
`tests/direct/test_drought_adapter.py`'s module docstring documents for drought.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import pytest

from agri_data_service.foundation.parquet.lane_contract import SourceWatermark
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.direct.watersheds.adapter import WATERSHEDS_DIRECT_KIND, DirectWatershedsAdapter
from agri_data_service.pipeline.direct.watersheds.source import WatershedRecord, WatershedsSnapshotSource
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.gap_fill import fill_one_lane_day, unlocked_lane_day
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.watersheds import WATERSHEDS_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

DAY = date(2026, 8, 7)
VALID_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 45.0], [-119.0, 46.0], [-120.0, 46.0], [-120.0, 45.0]]],
}
FETCHED_AT = datetime(2026, 8, 7, 6, 0, tzinfo=UTC)
LOADDATE = datetime(2013, 1, 18, tzinfo=UTC)


class SessionDouble:
    """Answers the statement-timeout pin and counts rollbacks; executes no real SQL."""

    def __init__(self) -> None:
        self.rollbacks = 0

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> None:  # noqa: ARG002
        return None

    async def rollback(self) -> None:
        self.rollbacks += 1


def _record(huc12: str) -> WatershedRecord:
    return WatershedRecord(
        huc12=huc12,
        name="Test Creek",
        areasqkm=12.5,
        tohuc="170900011200",
        states="OR",
        hutype="S",
        observed_at=LOADDATE,
        geometry=VALID_SQUARE,
    )


def snapshot(*huc12s: str) -> WatershedsSnapshotSource:
    accepted = tuple(_record(huc12) for huc12 in huc12s)
    watermark = SourceWatermark(day=LOADDATE.date(), basis="test", instant=LOADDATE)
    return WatershedsSnapshotSource(
        bbox="-125,42,-111,49",
        accepted=accepted,
        rejected_count=0,
        fetched_at=FETCHED_AT,
        watermark=watermark,
    )


def adapter_for(source: WatershedsSnapshotSource) -> DirectWatershedsAdapter:
    """Bind a pre-built snapshot into the adapter so no test opens a socket."""

    async def fetch() -> WatershedsSnapshotSource:
        return source

    return DirectWatershedsAdapter(fetch_source=fetch)


def direct_lane(adapter: DirectWatershedsAdapter) -> Any:
    async def watermark(session: Any, store: Any, *, today: date) -> SourceWatermark:  # noqa: ARG001
        return SourceWatermark(day=LOADDATE.date(), basis="test", instant=LOADDATE)

    return replace(LANE_REGISTRY[WATERSHEDS_STREAM], adapter=adapter, watermark=watermark)


@pytest.mark.asyncio
async def test_a_snapshot_writes_every_rung_and_marks_the_base_last() -> None:
    """The shared finalizer must produce all four rungs, and the base marker must land after them."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    adapter = adapter_for(snapshot("170900011201", "170900011202"))

    outcome, parts, rows, _written_bytes, detail = await fill_one_lane_day(
        SessionDouble(),
        store,
        direct_lane(adapter),
        day=DAY,
        run_id="watersheds-test",
        now=lambda: FETCHED_AT,
        today=DAY,
        lane_day_lock=unlocked_lane_day,
        extend_availability=False,
    )

    assert outcome == "written", detail
    assert parts == 1
    assert rows == 2  # noqa: PLR2004 - both basins of the fixture snapshot reach the base rung
    for tier in ZOOM_TIERS:
        assert store.partition_exists(WATERSHEDS_STREAM, WATERSHEDS_DIRECT_KIND, tier, DAY), tier
        assert store.read_completion_marker(WATERSHEDS_STREAM, WATERSHEDS_DIRECT_KIND, tier, DAY) is not None, tier
    written = [key for key in backend.objects if f"layer={WATERSHEDS_STREAM}/" in key]
    base_marker = next(key for key in written if "_complete.json" in key and f"zoom={LANE_BASE_ZOOM_TIER}" in key)
    assert written.index(base_marker) == max(written.index(key) for key in written if "_complete.json" in key), (
        "the base completion marker must be the last claim written for the day"
    )


@pytest.mark.asyncio
async def test_an_empty_snapshot_governs_a_zero_row_day_absent_at_every_rung() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    adapter = adapter_for(snapshot())

    outcome, parts, rows, _written_bytes, _detail = await fill_one_lane_day(
        SessionDouble(),
        store,
        direct_lane(adapter),
        day=DAY,
        run_id="watersheds-empty-test",
        now=lambda: FETCHED_AT,
        today=DAY,
        lane_day_lock=unlocked_lane_day,
        extend_availability=False,
    )

    assert outcome == "absent"
    assert parts == 0
    assert rows == 0
    for tier in ZOOM_TIERS:
        assert store.absence_exists(WATERSHEDS_STREAM, WATERSHEDS_DIRECT_KIND, tier, DAY), tier


@pytest.mark.asyncio
async def test_written_rows_carry_a_direct_namespaced_feature_id_never_a_bare_huc12() -> None:
    """DO NOT DELETE. `_refuse_null_base_columns` hard-fails a null `feature_id` at z13 -- see
    `rows.py`'s module docstring. Reading it back proves the namespaced sentinel actually landed,
    not merely that the write did not raise."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    adapter = adapter_for(snapshot("170900011201"))

    await fill_one_lane_day(
        SessionDouble(),
        store,
        direct_lane(adapter),
        day=DAY,
        run_id="watersheds-feature-id-test",
        now=lambda: FETCHED_AT,
        today=DAY,
        lane_day_lock=unlocked_lane_day,
        extend_availability=False,
    )

    table = store.read_partition(WATERSHEDS_STREAM, WATERSHEDS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY)
    feature_ids = table.column("feature_id").to_pylist()
    assert feature_ids == ["direct:170900011201"]
