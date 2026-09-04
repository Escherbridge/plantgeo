"""One direct vegetation day assembled into its registered base-rung Arrow table."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from agri_data_service.pipeline.direct.vegetation.rows import (
    VEGETATION_ALLOWED_CLIENT_EXPOSURE,
    VegetationRowError,
    vegetation_day_table,
)
from agri_data_service.pipeline.direct.vegetation.source import VegetationCellValue
from agri_data_service.pipeline.direct.vegetation.support import VegetationSupportCell
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_SCHEMA

DAY = date(2026, 9, 6)
FETCHED_AT = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


def _value(
    *, cell_key: str = "sentinel2-ndvi-0p25deg:43.1250:-116.3750", metric_value: float = 0.42
) -> VegetationCellValue:
    return VegetationCellValue(
        cell=VegetationSupportCell(
            cell_id="00000000-0000-4000-8000-000000000001",
            cell_key=cell_key,
            cell_longitude=-116.375,
            cell_latitude=43.125,
        ),
        metric_value=metric_value,
        scene_id="S2A_10TES_20260906_0_L2A",
        observed_at=datetime(2026, 9, 6, 18, 30, tzinfo=UTC),
        cloud_cover_percent=3.5,
        sample_count=21,
        release_count=1,
        record_sha256="a" * 64,
    )


def test_a_governed_day_matches_the_registered_arrow_schema_exactly() -> None:
    table = vegetation_day_table(day=DAY, values=(_value(),), data_available_at=FETCHED_AT)

    assert table.schema == VEGETATION_PLANE_SCHEMA.arrow_schema
    assert table.num_rows == 1


def test_every_row_carries_the_fetch_instant_never_the_scenes_own_acquisition_instant() -> None:
    table = vegetation_day_table(day=DAY, values=(_value(),), data_available_at=FETCHED_AT)

    row = table.to_pylist()[0]

    assert row["data_available_at"] == FETCHED_AT
    assert row["metric_value"] == pytest.approx(0.42)
    assert row["allowed_client_exposure"] is VEGETATION_ALLOWED_CLIENT_EXPOSURE
    assert row["grid_name"] == "sentinel2-ndvi-0p25deg"
    assert row["metric_name"] == "ndvi"


def test_a_zero_row_day_is_refused_rather_than_published_empty() -> None:
    """A day with no filled cells is a governed absence; the base rung must never read as a hollow publish."""
    with pytest.raises(VegetationRowError, match="governed absence"):
        vegetation_day_table(day=DAY, values=(), data_available_at=FETCHED_AT)


def test_a_naive_data_available_at_is_refused() -> None:
    with pytest.raises(VegetationRowError, match="timezone-aware"):
        vegetation_day_table(
            day=DAY,
            values=(_value(),),
            data_available_at=datetime(2026, 9, 6, 12, 0),  # noqa: DTZ001 - the naive input under test, not a defect
        )
