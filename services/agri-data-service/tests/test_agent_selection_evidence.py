"""Selection retrieval contracts over real Parquet and the shared map day resolver."""

# ruff: noqa: PLR2004 - fixture coordinates, dates and measured values are the assertions.

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from agri_data_service.agent import selection_evidence, tools
from agri_data_service.agent.selection_scope import (
    MAX_LANE_DAY_READS,
    PAGE_DAYS,
    Selection,
    balanced_days,
    evidence_days,
    history_page_days,
    support_lattice,
)
from agri_data_service.parquet_ops.duckdb_session import open_guarded_connection
from agri_data_service.warehouse.parquet.schema import get_stream_schema
from tests.agent_fakes import FakeAgentWarehouse, RefusingWarehouse

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import duckdb

    from agri_data_service.foundation.parquet.zoom import ZoomTier

DAY = date(2026, 6, 15)


@dataclass
class LocalSession:
    """Resolve receipt keys to test-owned local files."""

    connection: duckdb.DuckDBPyConnection
    files: dict[str, str]

    def object_uri(self, key: str) -> str:
        return self.files[key]


@dataclass
class LocalWarehouse(FakeAgentWarehouse):
    """Use real DuckDB for numeric reads and the ordinary in-memory day inventory."""

    files: dict[str, str] = field(default_factory=dict)

    async def run(self, work: Callable[[Any], Any], *, operation: str) -> Any:
        self.operations.append(operation)
        connection = open_guarded_connection()
        try:
            return work(LocalSession(connection, self.files))
        finally:
            connection.close()

    def write(self, root: Path, lane: str, day: date, rows: list[dict[str, Any]], tier: ZoomTier = 13) -> None:
        key = self.listing_store.write_day(lane, "observed", tier, day)
        path = root / f"{lane}-{tier}-{day.isoformat()}.parquet"
        pq.write_table(pa.Table.from_pylist(rows, schema=get_stream_schema(lane, "observed").arrow_schema), path)
        self.files[key] = str(path)


def climate_row(day: date, *, longitude: float, latitude: float, value: float) -> dict[str, Any]:
    return {
        "support_key": "surface",
        "signal_name": "air_temperature",
        "normalized_unit": "degC",
        "cell_id": f"{longitude}/{latitude}",
        "observed_day": day,
        "normalized_value": value,
        "observation_count": 1,
        "newest_observed_at": datetime.combine(day, datetime.min.time(), UTC),
        "coverage_fraction": 1.0,
        "allowed_client_exposure": True,
        "cell_longitude": longitude,
        "cell_latitude": latitude,
    }


async def read_surface(source: LocalWarehouse, surface: str, **overrides: Any) -> dict[str, Any]:
    arguments = {
        "surface_name": surface,
        "day": DAY.isoformat(),
        "longitude": -116.49,
        "latitude": 43.49,
        "range_start": DAY.isoformat(),
        "range_end": DAY.isoformat(),
        "zoom": 13,
        **overrides,
    }
    async with tools.run_context(warehouse_source=source):
        return json.loads(await tools.query_surface_evidence_for_selection(**arguments))


async def test_sparse_climate_support_contains_selection_far_outside_old_radius(tmp_path: Path) -> None:
    source = LocalWarehouse()
    source.write(
        tmp_path,
        "climate-field-dew-point",
        DAY,
        [
            climate_row(DAY, longitude=-116, latitude=43, value=8.25),
            climate_row(DAY, longitude=-115, latitude=44, value=12),
        ],
    )
    result = await read_surface(source, "climate-field-dew-point")
    selected = result["lanes"][0]["selected"]
    assert selected["state"] == "published"
    assert len(selected["features"]) == 1
    feature = selected["features"][0]
    assert feature["distance_meters"] > 50_000
    assert feature["covers_probe_point"] is True
    assert feature["support_bbox"] == [-116.5, 42.5, -115.5, 43.5]
    assert feature["properties"]["normalized_value"] == 8.25
    assert feature["served_day"] == DAY.isoformat()


@pytest.mark.parametrize("surface", ["soil-field-vpd", "vegetation"])
async def test_vpd_and_ndvi_read_their_own_numeric_support(tmp_path: Path, surface: str) -> None:
    source = LocalWarehouse()
    if surface == "vegetation":
        row = {
            "cell_id": "ndvi-local",
            "grid_name": "sentinel2-ndvi-0p25deg",
            "metric_name": "ndvi",
            "metric_unit": "unitless",
            "observed_day": DAY,
            "metric_value": 0.72,
            "observation_checksum": "fixture",
            "data_available_at": datetime(2026, 6, 16, tzinfo=UTC),
            "release_count": 1,
            "allowed_client_exposure": True,
            "cell_longitude": -116.375,
            "cell_latitude": 43.375,
        }
        value_column, expected = "metric_value", 0.72
    else:
        row = climate_row(DAY, longitude=-116.375, latitude=43.375, value=1.8)
        row.update(signal_name="vapor_pressure_deficit", normalized_unit="kPa")
        value_column, expected = "normalized_value", 1.8
    source.write(tmp_path, surface, DAY, [row])
    result = await read_surface(source, surface)
    feature = result["lanes"][0]["selected"]["features"][0]
    assert feature["covers_probe_point"] is True
    assert feature["properties"][value_column] == expected
    assert result["lanes"][0]["parquet_lane"] == surface
    assert feature["support_bbox"] == [-116.5, 43.25, -116.25, 43.5]


async def test_exact_day_missing_and_governed_absence_do_not_borrow_history(tmp_path: Path) -> None:
    source = LocalWarehouse()
    lane = "climate-field-dew-point"
    before, after = DAY - timedelta(days=1), DAY + timedelta(days=1)
    source.write(tmp_path, lane, before, [climate_row(before, longitude=-116, latitude=43, value=5)])
    source.listing_store.write_absence(
        lane,
        "observed",
        13,
        after,
        reason="source_empty",
        upstream_response="no measurements",
        recorded_at=datetime(2026, 6, 17, tzinfo=UTC),
        run_id="fixture",
    )
    result = await read_surface(source, lane, range_start=before.isoformat(), range_end=after.isoformat())
    item = result["lanes"][0]
    assert item["selected"]["state"] == "day_not_written"
    assert item["selected"]["features"] == []
    assert [entry["state"] for entry in item["history"]] == ["published", "day_not_written", "governed_absence"]
    assert item["history"][0]["features"][0]["distance_days"] == 1
    assert item["history"][2]["absence"]["reason"] == "source_empty"


async def test_multi_metric_lanes_keep_unwritten_depths_explicit(tmp_path: Path) -> None:
    source = LocalWarehouse()
    source.write(
        tmp_path, "climate-field-air-temperature-mean", DAY, [climate_row(DAY, longitude=-116, latitude=43, value=18)]
    )
    result = await read_surface(source, "climate-field-air-temperature")
    assert [lane["selected"]["state"] for lane in result["lanes"]] == [
        "published",
        "lane_never_written",
        "lane_never_written",
    ]


@pytest.mark.parametrize(("lane_count", "page_days"), [(1, 3), (2, 3), (3, 1), (4, 1)])
def test_history_page_budget_includes_independent_exact_selected_reads(lane_count: int, page_days: int) -> None:
    assert history_page_days(lane_count) == page_days
    assert lane_count * (page_days + 1) <= MAX_LANE_DAY_READS


async def test_multi_metric_pagination_preserves_selected_day_and_every_history_date(tmp_path: Path) -> None:
    source = LocalWarehouse()
    first, last = DAY - timedelta(days=1), DAY + timedelta(days=1)
    for day in (first, DAY, last):
        source.write(
            tmp_path,
            "climate-field-air-temperature-mean",
            day,
            [climate_row(day, longitude=-116, latitude=43, value=18)],
        )
    sampled: list[str] = []
    for offset in range(3):
        result = await read_surface(
            source,
            "climate-field-air-temperature",
            range_start=first.isoformat(),
            range_end=last.isoformat(),
            page_start=offset,
        )
        history = result["history"]
        assert history["days_per_page"] == 1
        assert history["next_page_start"] == (offset + 1 if offset < 2 else None)
        selected = result["lanes"][0]["selected"]
        assert selected["requested_day"] == DAY.isoformat()
        assert selected["features"][0]["properties"]["normalized_value"] == 18
        sampled.extend(entry["requested_day"] for entry in result["lanes"][0]["history"])
    assert sampled == [first.isoformat(), last.isoformat(), DAY.isoformat()]


def test_long_history_first_page_spans_full_range_and_pages_cover_every_day() -> None:
    first, last = date(2016, 1, 1), date(2026, 12, 31)
    ordered = balanced_days(first, last, DAY)
    assert {first, last, DAY} <= set(ordered[:PAGE_DAYS])
    assert len(ordered) == len(set(ordered)) == (last - first).days + 1
    assert set(ordered) == {first + timedelta(days=offset) for offset in range((last - first).days + 1)}
    selection = Selection.parse(
        longitude=-116.49,
        latitude=43.49,
        zoom=13,
        day=DAY.isoformat(),
        range_start=first.isoformat(),
        range_end=last.isoformat(),
        time_scale="year",
        page_start=PAGE_DAYS,
    )
    assert set(selection.page()).isdisjoint(ordered[:PAGE_DAYS])


@pytest.mark.parametrize(
    ("surface", "tier", "expected"),
    [
        ("climate-field-dew-point", 13, (1.0, -0.5, 0.0)),
        ("climate-field-dew-point", 5, (1.0, -0.5, 0.1)),
        ("soil-field-vpd", 13, (0.25, 0.0, 0.0)),
        ("vegetation", 5, (0.25, 0.0, 0.1)),
        ("vegetation", 0, (5.0, 0.0, 2.5)),
    ],
)
def test_source_support_matches_map_lattice(surface: str, tier: Any, expected: tuple[float, float, float]) -> None:
    assert support_lattice(surface, tier) == expected


def test_catalogue_exposes_all_map_families_without_retired_reader() -> None:
    result = selection_evidence.catalogue()
    names = {row["surface_name"] for row in result["layers"]}
    assert {
        "vegetation",
        "soil-field-vpd",
        "botanical-richness",
        "gbif-occurrences",
        "strategy-recommendations",
        "demand-heatmap",
        "soil-phh2o",
        "soil-soc",
        "soil-nitrogen",
        "soil-bdod",
        "soil-cec",
        "soil-ocd",
        "fire-risk",
        "weather-forecast",
        "watersheds",
        "soil-survey",
    } <= names
    assert "signal" not in names
    exposed = {tool.name for tool in tools.WAREHOUSE_TOOLS}
    assert {"list_environmental_layers", "surface_evidence_for_selection"} <= exposed
    assert (
        not {
            "signals_near_point",
            "signal_value_on_day",
            "signal_neighbors_in_time",
            "nearest_signal_cells",
            "surface_value_near_point",
            "feature_value_near_point",
            "botanical_occurrences_in_region",
            "botanical_occurrence_spatial_neighbours",
            "botanical_occurrence_temporal_neighbours",
        }
        & exposed
    )


async def test_invalid_and_unconfigured_selections_refuse_without_io(monkeypatch: pytest.MonkeyPatch) -> None:
    result = await read_surface(LocalWarehouse(), "vegetation", day="2026-06-15T00:00:00Z")
    assert result["refusal_code"] == "invalid_selection"
    monkeypatch.setattr(selection_evidence.settings, "agent_map_app_url", None)
    result = await read_surface(LocalWarehouse(), "soil-soc")
    assert result["refusal_code"] == "app_reader_not_configured"


def test_sparse_history_prioritizes_real_published_days_on_both_sides() -> None:
    first, last = date(2022, 1, 1), date(2026, 12, 31)
    before, after = DAY - timedelta(days=9), DAY + timedelta(days=17)
    published = {date(2022, 3, 4), date(2024, 9, 7), before, after, date(2026, 11, 27)}
    ordered = evidence_days(first, last, DAY, published)
    assert ordered[:PAGE_DAYS] == (first, last, DAY)
    assert {before, after} <= set(ordered[: PAGE_DAYS + 2])
    assert published <= set(ordered[: len(published) + PAGE_DAYS])
    assert len(ordered) == len(set(ordered)) == (last - first).days + 1


async def test_dense_single_day_botanical_history_can_continue(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = []
    monkeypatch.setattr(
        selection_evidence,
        "read_current_botanical_release",
        lambda: {"state": "current", "release_set_id": "fixture-release"},
    )

    def read(request: Any) -> dict[str, Any]:
        requests.append(request)
        row = {
            "collection_key": "gbif:pnw:vascular",
            "longitude": -116.49,
            "latitude": 43.49,
            "event_interval": {"start": DAY.isoformat(), "end": DAY.isoformat(), "precision": "day"},
        }
        return {
            "state": "detail",
            "release_set_id": "fixture-release",
            "published_at": "2026-09-20T00:00:00Z",
            "features": [row],
            "truncated": request.offset == 0,
        }

    monkeypatch.setattr(selection_evidence, "read_botanical_occurrences", read)
    arguments: dict[str, Any] = {
        "surface_name": "gbif-occurrences",
        "day": DAY.isoformat(),
        "longitude": -116.49,
        "latitude": 43.49,
        "range_start": DAY.isoformat(),
        "range_end": DAY.isoformat(),
    }
    async with tools.run_context(warehouse_source=RefusingWarehouse()):
        first = json.loads(await tools.query_surface_evidence_for_selection(**arguments))
        next_offset = first["history"]["next_page_start"]
        second = json.loads(await tools.query_surface_evidence_for_selection(**arguments, page_start=next_offset))
    assert next_offset == 12
    assert second["history"]["next_page_start"] is None
    assert requests[-1].offset == 12
    assert all(request.collection_key == "gbif:pnw:vascular" for request in requests)
    assert second["lanes"][0]["history"][0]["features"][0]["properties"]["collection_key"] == "gbif:pnw:vascular"
    assert second["lanes"][0]["history"][0]["features"][0]["observed_day"] == DAY.isoformat()
    assert "served_day" not in second["lanes"][0]["history"][0]["features"][0]


async def test_botanical_aggregates_never_claim_date_filtered_richness(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = []
    monkeypatch.setattr(
        selection_evidence,
        "read_current_botanical_release",
        lambda: {"state": "current", "release_set_id": "fixture-release"},
    )

    def read(request: Any) -> dict[str, Any]:
        requests.append(request)
        return {
            "state": "aggregate",
            "cells": [{"documented_taxa": 8, "record_count": 19}],
            "published_at": "2026-09-20T00:00:00Z",
        }

    monkeypatch.setattr(selection_evidence, "read_botanical_occurrences", read)
    result = await read_surface(LocalWarehouse(), "botanical-richness")
    lane = result["lanes"][0]
    assert lane["selected"]["refusal_code"] == "historical_publication_unsupported"
    assert lane["selected"]["features"] == []
    assert lane["snapshot_context"]["cells"][0]["documented_taxa"] == 8
    assert requests[0].event_start is None
    assert requests[0].event_end is None
