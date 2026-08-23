"""The water-gauges serving plane, exercised against real local Hive-partitioned Parquet files.

No network, no bucket: `planes/water_gauges.py`'s `root` parameter accepts anything Polars can open,
and a local temp directory laid out exactly like the frozen object-store layout
(`foundation/parquet/paths.py`) exercises the real glob / hive-partitioning / schema logic with zero
mocking. See `conductor/code_styleguides/layer-lanes.md` section 2 for the "kind is a partition,
never a column branch" rule this file's assertions are built around.
"""

# ruff: noqa: PLR2004 -- small literal row/day counts are the whole point of these assertions

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.config import ObjectStoreCredentials
from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.planes.water_gauges import (
    read_water_gauges_forecast,
    read_water_gauges_observed,
    scan_water_gauges_forecast,
    scan_water_gauges_observed,
    water_gauges_partition_root,
)
from agri_data_service.warehouse.schemas.water_gauges import WATER_GAUGES_SCHEMA, WATER_GAUGES_STREAM

if TYPE_CHECKING:
    from pathlib import Path

DAY_ONE = date(2026, 7, 4)
DAY_TWO = date(2026, 7, 5)
DAY_THREE = date(2026, 7, 6)


def _observed_row(
    *,
    site_number: str,
    observed_at: datetime,
    observed_day: date,
    flow_cfs: float | None,
    geometry_linked: bool = True,
) -> dict[str, object]:
    """Build one row matching the lane's registered observed schema field for field."""
    return {
        "site_number": site_number,
        "observed_at": observed_at,
        "observed_day": observed_day,
        "site_name": f"Gauge {site_number}",
        "latitude": 45.0,
        "longitude": -122.0,
        "flow_cfs": flow_cfs,
        "percentile": None,
        "condition": "unknown",
        "trend": "stable",
        "source": "USGS NWIS",
        "geometry_linked": geometry_linked,
        "data_available_at": None,
        "ingested_at": datetime(2026, 7, 4, 5, tzinfo=UTC),
    }


def _write_observed_day(root: Path, day: date, rows: list[dict[str, object]]) -> None:
    table = pa.Table.from_pylist(rows).cast(WATER_GAUGES_SCHEMA.arrow_schema)
    path = root / partition_path(WATER_GAUGES_STREAM, "observed", day)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def _write_forecast_day(root: Path, day: date, rows: list[dict[str, object]]) -> None:
    table = pa.Table.from_pylist(rows)
    path = root / partition_path(WATER_GAUGES_STREAM, "forecast", day)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def test_scan_observed_returns_every_sub_daily_reading_without_collapsing_the_grain(tmp_path: Path) -> None:
    """The exporter's grain is (site_number, observed_at); this reader must not aggregate it away."""
    _write_observed_day(
        tmp_path,
        DAY_ONE,
        [
            _observed_row(
                site_number="A", observed_at=datetime(2026, 7, 4, 1, tzinfo=UTC), observed_day=DAY_ONE, flow_cfs=10.0
            ),
            _observed_row(
                site_number="A", observed_at=datetime(2026, 7, 4, 2, tzinfo=UTC), observed_day=DAY_ONE, flow_cfs=11.0
            ),
        ],
    )

    frame = scan_water_gauges_observed(str(tmp_path)).collect()

    assert frame.height == 2
    assert frame["flow_cfs"].to_list() == [10.0, 11.0]
    assert set(frame["kind"].to_list()) == {"observed"}


def test_geometry_orphaned_rows_are_not_filtered(tmp_path: Path) -> None:
    """docs/lanes/water-gauges.md section 4 measured 37% unlinked; the reader must keep them."""
    _write_observed_day(
        tmp_path,
        DAY_ONE,
        [
            _observed_row(
                site_number="A",
                observed_at=datetime(2026, 7, 4, 1, tzinfo=UTC),
                observed_day=DAY_ONE,
                flow_cfs=10.0,
                geometry_linked=False,
            )
        ],
    )

    frame = scan_water_gauges_observed(str(tmp_path)).collect()

    assert frame.height == 1
    assert frame["geometry_linked"].to_list() == [False]


def test_a_silent_gauge_tick_keeps_its_null_flow_rather_than_being_dropped(tmp_path: Path) -> None:
    _write_observed_day(
        tmp_path,
        DAY_ONE,
        [
            _observed_row(
                site_number="A", observed_at=datetime(2026, 7, 4, 1, tzinfo=UTC), observed_day=DAY_ONE, flow_cfs=None
            )
        ],
    )

    frame = scan_water_gauges_observed(str(tmp_path)).collect()

    assert frame.height == 1
    assert frame["flow_cfs"].to_list() == [None]


def test_an_empty_root_resolves_to_a_correctly_typed_zero_row_frame_rather_than_raising(tmp_path: Path) -> None:
    frame = scan_water_gauges_observed(str(tmp_path)).collect()

    assert frame.height == 0
    assert frame.schema["site_number"] == pl.Utf8
    assert frame.schema["flow_cfs"] == pl.Float64
    assert frame.schema["observed_day"] == pl.Date


def test_read_observed_prunes_to_the_requested_day_range(tmp_path: Path) -> None:
    for day in (DAY_ONE, DAY_TWO, DAY_THREE):
        _write_observed_day(
            tmp_path,
            day,
            [
                _observed_row(
                    site_number="A",
                    observed_at=datetime(day.year, day.month, day.day, 1, tzinfo=UTC),
                    observed_day=day,
                    flow_cfs=5.0,
                )
            ],
        )

    frame = read_water_gauges_observed(str(tmp_path), first_day=DAY_TWO, last_day=DAY_TWO)

    assert frame["observed_day"].to_list() == [DAY_TWO]


def test_read_observed_narrows_by_site_number(tmp_path: Path) -> None:
    _write_observed_day(
        tmp_path,
        DAY_ONE,
        [
            _observed_row(
                site_number="A", observed_at=datetime(2026, 7, 4, 1, tzinfo=UTC), observed_day=DAY_ONE, flow_cfs=1.0
            ),
            _observed_row(
                site_number="B", observed_at=datetime(2026, 7, 4, 1, tzinfo=UTC), observed_day=DAY_ONE, flow_cfs=2.0
            ),
        ],
    )

    frame = read_water_gauges_observed(str(tmp_path), first_day=DAY_ONE, last_day=DAY_ONE, site_numbers=("B",))

    assert frame["site_number"].to_list() == ["B"]


def test_read_observed_refuses_a_backwards_day_range(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="backwards"):
        read_water_gauges_observed(str(tmp_path), first_day=DAY_TWO, last_day=DAY_ONE)


_FORECAST_ROW: dict[str, object] = {
    "site_number": "A",
    "horizon_step": 1,
    "valid_day": DAY_TWO,
    "quantile": 0.5,
    "flow_cfs": 12.5,
    "forecast_run_id": "run-1",
    "random_seed": 7,
    "ensemble_size": 500,
    "horizon_days": 30,
    "issued_on": DAY_ONE,
}
_FORECAST_PROVENANCE_COLUMNS = (
    "forecast_run_id",
    "random_seed",
    "ensemble_size",
    "horizon_days",
    "issued_on",
    "quantile",
)


def test_scan_forecast_carries_provenance_and_never_blends_with_observed(tmp_path: Path) -> None:
    """kind=observed and kind=forecast are read separately and never unioned into one frame."""
    _write_observed_day(
        tmp_path,
        DAY_ONE,
        [
            _observed_row(
                site_number="A", observed_at=datetime(2026, 7, 4, 1, tzinfo=UTC), observed_day=DAY_ONE, flow_cfs=10.0
            )
        ],
    )
    _write_forecast_day(tmp_path, DAY_TWO, [_FORECAST_ROW])

    observed_frame = scan_water_gauges_observed(str(tmp_path)).collect()
    forecast_frame = scan_water_gauges_forecast(str(tmp_path)).collect()

    assert set(observed_frame["kind"].to_list()) == {"observed"}
    assert set(forecast_frame["kind"].to_list()) == {"forecast"}
    assert forecast_frame.height == 1
    assert set(_FORECAST_PROVENANCE_COLUMNS).issubset(set(forecast_frame.columns))
    # Genuinely different shapes -- proof neither call quietly picked up the other kind's file.
    assert set(observed_frame.columns) != set(forecast_frame.columns)


def test_scan_forecast_without_a_schema_hint_raises_on_a_genuinely_empty_root(tmp_path: Path) -> None:
    """No forecast schema is registered for this lane yet; an empty root must not fabricate success."""
    with pytest.raises(pl.exceptions.ComputeError):
        scan_water_gauges_forecast(str(tmp_path)).collect()


def test_scan_forecast_resolves_a_genuinely_empty_root_when_given_an_explicit_schema(tmp_path: Path) -> None:
    frame = scan_water_gauges_forecast(
        str(tmp_path), empty_schema={"site_number": pl.Utf8, "flow_cfs": pl.Float64}
    ).collect()

    assert frame.height == 0
    assert frame.schema["site_number"] == pl.Utf8


def test_read_forecast_prunes_by_a_caller_named_day_column(tmp_path: Path) -> None:
    _write_forecast_day(tmp_path, DAY_ONE, [{**_FORECAST_ROW, "valid_day": DAY_ONE}])
    _write_forecast_day(tmp_path, DAY_TWO, [{**_FORECAST_ROW, "valid_day": DAY_TWO}])

    frame = read_water_gauges_forecast(str(tmp_path), day_column="valid_day", first_day=DAY_TWO, last_day=DAY_TWO)

    assert frame["valid_day"].to_list() == [DAY_TWO]


def test_read_forecast_refuses_a_backwards_day_range(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="backwards"):
        read_water_gauges_forecast(str(tmp_path), day_column="valid_day", first_day=DAY_TWO, last_day=DAY_ONE)


def test_water_gauges_partition_root_builds_the_bucket_uri() -> None:
    credentials = ObjectStoreCredentials(
        endpoint_url="https://storage.example.com",
        region="sjc",
        bucket="plantgeo-warehouse",
        access_key_id="access-key-value",
        secret_access_key="secret-key-value",
    )

    assert water_gauges_partition_root(credentials) == "s3://plantgeo-warehouse/"
    assert water_gauges_partition_root(credentials, prefix="/sandbox/") == "s3://plantgeo-warehouse/sandbox/"
