"""Fire source completeness and the quiet-day to detected-day publication transition."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.ingest.firms import ProductAvailability, parse_firms_csv
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.fire_detections import source as fire_source
from agri_data_service.pipeline.direct.fire_detections.adapter import DirectFireDetectionsAdapter
from agri_data_service.pipeline.direct.fire_detections.rows import fire_table_from_features
from agri_data_service.pipeline.errors import PipelineOperationError
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.fire_detections import FIRE_DETECTIONS_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

DAY = date(2026, 9, 18)
FETCHED_AT = datetime(2026, 9, 20, tzinfo=UTC)
PRODUCTS = ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT")
CSV = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,version,frp\n"
    "47.83797,-113.26495,312.4,0.4,0.4,2026-09-18,1106,N,n,2.0NRT,12.3"
)


@pytest.mark.parametrize("failed_product", [None, PRODUCTS[1]])
async def test_source_requires_every_applicable_product_before_accepting_a_quiet_day(
    monkeypatch: pytest.MonkeyPatch, failed_product: str | None
) -> None:
    client_context = AsyncMock()
    monkeypatch.setattr(fire_source, "upstream_client", Mock(return_value=client_context))
    monkeypatch.setattr(
        fire_source,
        "fetch_product_availability",
        AsyncMock(return_value={product: ProductAvailability(product, DAY, DAY) for product in PRODUCTS}),
    )

    async def fetch(_client: object, _bbox: str, _span: int, product: str, _day: date) -> list[dict[str, object]]:
        if product == failed_product:
            raise RuntimeError("upstream unavailable")
        return []

    fetch_mock = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(fire_source, "fetch_active_fires", fetch_mock)
    operation = fire_source.fetch_fire_day(
        day=DAY,
        bbox="-125,42,-111,49",
        max_records=100,
        retry_attempts=1,
        retry_base_seconds=0.1,
        retry_max_seconds=0.1,
    )
    if failed_product is None:
        result = await operation
        assert result.table.num_rows == 0
        assert set(result.source_products) == set(PRODUCTS)
        assert result.product_counts == dict.fromkeys(PRODUCTS, 0)
    else:
        with pytest.raises(PipelineOperationError) as caught:
            await operation
        assert isinstance(caught.value.__cause__, PipelineOperationError)
        assert "complete constellation" in str(caught.value.__cause__)
    assert {call.args[3] for call in fetch_mock.await_args_list} == set(PRODUCTS)


async def test_complete_quiet_day_is_governed_absent_then_retracted_when_detections_arrive() -> None:
    store = ObjectStore(RecordingBackend())
    session = AsyncMock()
    quiet = fire_table_from_features([], day=DAY, fetched_at=FETCHED_AT, max_records=100, source_products=PRODUCTS)
    quiet_adapter = DirectFireDetectionsAdapter(fetch_source=AsyncMock(return_value=quiet))
    absent = await quiet_adapter(session, store, day=DAY, run_id="quiet-day")
    assert absent.absence_recorded
    assert absent.row_count == 0
    for tier in ZOOM_TIERS:
        assert store.absence_exists(FIRE_DETECTIONS_STREAM, "observed", tier, DAY)

    detected = fire_table_from_features(
        parse_firms_csv(CSV, PRODUCTS[0]), day=DAY, fetched_at=FETCHED_AT, max_records=100, source_products=PRODUCTS
    )
    detected_adapter = DirectFireDetectionsAdapter(fetch_source=AsyncMock(return_value=detected))
    published = await detected_adapter(session, store, day=DAY, run_id="detected-day")
    assert not published.absence_recorded
    assert published.row_count == 1
    assert store.partition_exists(FIRE_DETECTIONS_STREAM, "observed", LANE_BASE_ZOOM_TIER, DAY)
    for tier in ZOOM_TIERS:
        assert not store.absence_exists(FIRE_DETECTIONS_STREAM, "observed", tier, DAY)


async def test_adapter_refuses_a_fetch_for_the_wrong_day_without_publishing() -> None:
    backend = RecordingBackend()
    wrong_day = fire_table_from_features([], day=date(2026, 9, 17), fetched_at=FETCHED_AT, max_records=100)
    adapter = DirectFireDetectionsAdapter(fetch_source=AsyncMock(return_value=wrong_day))
    with pytest.raises(PipelineOperationError, match="returned source day"):
        await adapter(AsyncMock(), ObjectStore(backend), day=DAY, run_id="wrong-day")
    assert backend.objects == {}
