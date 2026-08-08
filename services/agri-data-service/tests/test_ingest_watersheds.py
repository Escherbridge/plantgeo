"""USGS WBD HUC12 ingestion: id-batched fetching, the loaddate as the observation day, and snapshot identity."""

# ruff: noqa: PLR2004

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import pytest

from agri_data_service.ingest.watersheds import (
    WATERSHEDS_SOURCE,
    build_watershed_identity,
    build_watershed_write,
    fetch_watersheds,
    parse_load_date,
    run_watersheds_ingestion_job,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.ingest.writer import FeatureWrite

# Captured 2026-08-07 from NHDPlus_HR layer 12. `loaddate` really is epoch MILLISECONDS, and
# `states` really is null on these rows -- both are what the parser is written around.
SANDY_RIVER_LOAD_DATE_MS = 1358492970000


class RecordingWriter:
    """A feature writer that records what a job handed it, so a job test needs no database."""

    def __init__(self) -> None:
        self.writes: list[FeatureWrite] = []

    async def __call__(self, writes: Sequence[FeatureWrite]) -> int:
        self.writes = list(writes)
        return len(self.writes)


def _feature(huc12: str, *, load_date: object = SANDY_RIVER_LOAD_DATE_MS) -> dict[str, object]:
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-122.1, 45.4], [-122.0, 45.4], [-122.0, 45.5], [-122.1, 45.4]]],
        },
        "properties": {
            "huc12": huc12,
            "name": "Trout Creek-Sandy River",
            "areasqkm": 42.83,
            "tohuc": "170800010703",
            "states": None,
            "hutype": "S",
            "loaddate": load_date,
        },
    }


@pytest.fixture(autouse=True)
def _clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("INGEST_BBOX", "INGEST_MAX_SOURCE_RECORDS", "WATERSHEDS_LAYER_ID"):
        monkeypatch.delenv(variable, raising=False)


def test_the_load_date_is_read_as_epoch_milliseconds() -> None:
    # 1358492970000 is 2013-01-18, the day USGS loaded that boundary. Reading it as SECONDS would
    # land in 1970 and every boundary would sort before the axis instead of on its real day.
    assert parse_load_date(SANDY_RIVER_LOAD_DATE_MS) == datetime(2013, 1, 18, 7, 9, 30, tzinfo=UTC)
    # The day is what the slider and geo.feature_observation_day actually read.
    assert parse_load_date(SANDY_RIVER_LOAD_DATE_MS).date().isoformat() == "2013-01-18"
    assert parse_load_date(SANDY_RIVER_LOAD_DATE_MS // 1000).year == 1970


def test_an_unusable_load_date_leaves_the_boundary_honestly_undated() -> None:
    assert parse_load_date(None) is None
    assert parse_load_date("2013-01-18") is None
    # A bool is an int in Python; without the explicit guard True would parse as epoch 0.001s.
    assert parse_load_date(True) is None


def test_a_basin_is_keyed_by_its_huc12_alone_so_a_rerun_refreshes_it_in_place() -> None:
    identity = build_watershed_identity(_feature("170800010702")["properties"])  # type: ignore[arg-type]

    # No timestamp in the local id. Boundaries are a snapshot: folding the load date in -- what
    # build_streamflow_gauge_identity does, where per-reading versions ARE the point -- would mint
    # a new version of an unchanged polygon on every re-ingest.
    assert identity.producer_local_id == "170800010702"
    assert identity.entity_local_id == "170800010702"
    assert identity.observed_at == datetime(2013, 1, 18, 7, 9, 30, tzinfo=UTC)


def test_a_write_carries_observed_at_so_the_read_model_can_date_it() -> None:
    write = build_watershed_write(_feature("170800010702"), "watersheds")
    assert write is not None

    # The read model dates every row from COALESCE(observedAt, updatedAt, polygonDateTime), and
    # `loaddate` is in none of them -- without this key the layer reports "Not yet observed" at
    # every date forever.
    assert str(write.properties["observedAt"]).startswith("2013-01-18")
    assert write.properties["huc12"] == "170800010702"
    # The layer's own lowercase GeoJSON spellings, which hover-fields.ts already reads.
    assert write.properties["areasqkm"] == 42.83
    assert write.properties["hutype"] == "S"
    assert write.properties["geometry"]["type"] == "Polygon"  # type: ignore[index]


def test_an_undated_basin_is_written_without_acquiring_a_clock_reading() -> None:
    write = build_watershed_write(_feature("170800010702", load_date=None), "watersheds")
    assert write is not None
    assert "observedAt" not in write.properties


def test_a_feature_with_no_huc12_is_rejected_rather_than_keyed_on_nothing() -> None:
    feature = _feature("170800010702")
    feature["properties"]["huc12"] = ""  # type: ignore[index]
    assert build_watershed_write(feature, "watersheds") is None


@pytest.mark.asyncio
async def test_geometry_is_fetched_by_explicit_object_ids_rather_than_by_offset() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if "returnIdsOnly=true" in url:
            return httpx.Response(200, json={"objectIdFieldName": "OBJECTID", "objectIds": [1, 2, 3]})
        return httpx.Response(200, json={"type": "FeatureCollection", "features": [_feature("170800010702")]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await fetch_watersheds(client, "-125,42,-111,49")

    # One id-only request, then geometry addressed by name. Offset paging needs a stable sort, and
    # asking this layer to sort WHILE returning geometry answers HTTP 500 over the PNW envelope.
    assert "returnIdsOnly=true" in seen[0]
    assert "objectIds=1%2C2%2C3" in seen[1] or "objectIds=1,2,3" in seen[1]
    assert "resultOffset" not in seen[1]
    assert "orderByFields" not in seen[1]


@pytest.mark.asyncio
async def test_an_arcgis_fault_behind_http_200_fails_the_job_rather_than_reporting_no_basins() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"code": 500, "message": "Unable to complete operation"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(Exception, match="error object"):
            await fetch_watersheds(client, "-125,42,-111,49")


@pytest.mark.asyncio
async def test_the_job_skips_rather_than_querying_the_world_without_a_bbox() -> None:
    writer = RecordingWriter()
    result = await run_watersheds_ingestion_job(writer, bbox=None)

    assert result.source == WATERSHEDS_SOURCE
    assert result.status == "skipped"
    assert writer.writes == []


@pytest.mark.asyncio
async def test_the_job_applies_no_record_cap_because_boundaries_are_a_closed_set() -> None:
    ids = list(range(1, 21))

    def handler(request: httpx.Request) -> httpx.Response:
        if "returnIdsOnly=true" in str(request.url):
            return httpx.Response(200, json={"objectIds": ids})
        return httpx.Response(
            200,
            json={"type": "FeatureCollection", "features": [_feature(f"1708000107{n:02d}") for n in ids]},
        )

    writer = RecordingWriter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await run_watersheds_ingestion_job(writer, bbox="-125,42,-111,49", client=client)

    # A cap bounds an unbounded observation stream. This is a closed set of national boundaries, and
    # truncating it leaves permanent holes in a basin map rather than dropping the oldest of a feed.
    assert result.status == "ingested"
    assert result.truncated is False
    assert len(writer.writes) == 20
