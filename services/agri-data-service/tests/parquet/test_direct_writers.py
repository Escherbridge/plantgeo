"""Focused contracts shared by the water-gauge and fire source-direct writers."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.pipeline.direct import fire_detections, water_gauges
from agri_data_service.pipeline.direct.fire_detections import DirectFireDetectionsAdapter
from agri_data_service.pipeline.direct.water_gauges import (
    DirectWaterGaugesError,
    merge_water_gauges_day,
)
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.fire_detections import FIRE_DETECTIONS_STREAM
from agri_data_service.warehouse.schemas.water_gauges import (
    WATER_GAUGES_SCHEMA,
    WATER_GAUGES_STREAM,
)
from tests.parquet.test_objectstore_writer import RecordingBackend

DAY = date(2026, 8, 25)
FIRST_INSTANT = datetime(2026, 8, 25, 1, 0, tzinfo=UTC)
SECOND_INSTANT = FIRST_INSTANT + timedelta(hours=1)


def _row(
    *,
    observed_at: datetime,
    ingested_at: datetime,
    flow_cfs: float,
) -> dict[str, object]:
    return {
        "site_number": "13185000",
        "observed_at": observed_at,
        "observed_day": DAY,
        "site_name": "Boise River",
        "latitude": 43.62,
        "longitude": -116.22,
        "flow_cfs": flow_cfs,
        "percentile": None,
        "condition": "normal",
        "trend": "stable",
        "source": "USGS NWIS",
        "geometry_linked": False,
        "data_available_at": None,
        "ingested_at": ingested_at,
    }


def _table(rows: list[dict[str, object]]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=WATER_GAUGES_SCHEMA.arrow_schema)


def test_direct_package_imports_both_source_writers() -> None:
    assert water_gauges.WATER_GAUGES_STREAM == WATER_GAUGES_STREAM
    assert fire_detections.FIRE_DETECTIONS_STREAM == FIRE_DETECTIONS_STREAM


def test_fire_adapter_explicitly_retracts_a_disproven_absence_before_writing_data() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_absence(
        GovernedAbsence(
            reason="the settled constellation was empty",
            upstream_response="all applicable FIRMS products returned zero rows",
            recorded_at=datetime(2026, 8, 26, tzinfo=UTC),
            run_id="initial-empty",
        ),
        layer=FIRE_DETECTIONS_STREAM,
        kind="observed",
        zoom=LANE_BASE_ZOOM_TIER,
        day=DAY,
    )
    table = pa.Table.from_pylist(
        [
            {
                "cell_longitude": -116.22,
                "cell_latitude": 43.62,
                "observed_day": DAY,
                "detection_count": 1,
                "frp_sum": 4.5,
                "frp_observation_count": 1,
                "high_confidence_detection_count": 1,
                "newest_observed_at": FIRST_INSTANT,
            }
        ],
        schema=fire_detections.FIRE_DETECTIONS_SCHEMA.arrow_schema,
    )

    async def fetch_source() -> fire_detections.FireDaySource:
        return fire_detections.FireDaySource(
            day=DAY,
            raw_records=1,
            deduplicated_records=1,
            source_products=("VIIRS_SNPP_NRT",),
            product_counts={"VIIRS_SNPP_NRT": 1},
            table=table,
        )

    class Session:
        async def rollback(self) -> None:
            return None

    result = asyncio.run(
        DirectFireDetectionsAdapter(fetch_source=fetch_source)(
            Session(),
            store,
            day=DAY,
            run_id="absence-revision",
        )
    )

    assert result.row_count == 1
    assert store.absence_exists(FIRE_DETECTIONS_STREAM, "observed", LANE_BASE_ZOOM_TIER, DAY) is False
    assert store.partition_exists(FIRE_DETECTIONS_STREAM, "observed", LANE_BASE_ZOOM_TIER, DAY) is True


def test_forward_merge_preserves_historical_duplicates_when_adding_a_new_grain() -> None:
    duplicate_a = _row(
        observed_at=FIRST_INSTANT,
        ingested_at=datetime(2026, 8, 25, 2, 0, tzinfo=UTC),
        flow_cfs=12.0,
    )
    duplicate_b = _row(
        observed_at=FIRST_INSTANT,
        ingested_at=datetime(2026, 8, 25, 2, 5, tzinfo=UTC),
        flow_cfs=13.0,
    )
    existing = _table([duplicate_a, duplicate_b])
    incoming = _table(
        [
            _row(
                observed_at=SECOND_INSTANT,
                ingested_at=datetime(2026, 8, 25, 3, 0, tzinfo=UTC),
                flow_cfs=14.0,
            )
        ]
    )

    merged = merge_water_gauges_day(existing, incoming, day=DAY)

    rows = merged.table.to_pylist()
    assert rows[:2] == existing.to_pylist()
    assert len(rows) == 3
    assert merged.existing_rows == 2
    assert merged.added_rows == 1
    assert merged.updated_rows == 0


def test_forward_merge_refuses_an_ambiguous_refresh_without_collapsing_duplicates() -> None:
    duplicate_a = _row(
        observed_at=FIRST_INSTANT,
        ingested_at=datetime(2026, 8, 25, 2, 0, tzinfo=UTC),
        flow_cfs=12.0,
    )
    duplicate_b = _row(
        observed_at=FIRST_INSTANT,
        ingested_at=datetime(2026, 8, 25, 2, 5, tzinfo=UTC),
        flow_cfs=13.0,
    )
    incoming = _table(
        [
            _row(
                observed_at=FIRST_INSTANT,
                ingested_at=datetime(2026, 8, 25, 3, 0, tzinfo=UTC),
                flow_cfs=14.0,
            )
        ]
    )

    with pytest.raises(DirectWaterGaugesError, match="ambiguous refresh"):
        merge_water_gauges_day(_table([duplicate_a, duplicate_b]), incoming, day=DAY)
