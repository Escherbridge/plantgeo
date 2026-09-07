"""USGS WBD HUC12 source adapter: id-batched fetching, the loaddate as the observation day, and snapshot identity.

THE POSTGRES-WRITING HALF OF THIS SUITE WAS DELETED ON 2026-09-06 with the code it exercised.
`test_a_write_carries_observed_at_...`, `test_an_undated_basin_is_written_...`,
`test_a_feature_with_no_huc12_is_rejected_...`, `test_the_job_skips_rather_than_querying_the_world_...`
and `test_the_job_applies_no_record_cap_...` covered `build_watershed_write` and
`run_watersheds_ingestion_job`, which no longer exist; the `RecordingWriter` they shared went with
them. What remains covers the surface `pipeline/direct/watersheds/` fetches through, and the
equivalent direct-writer behaviours are pinned in `tests/direct/test_watersheds_direct_*.py`.
"""

# ruff: noqa: PLR2004

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from agri_data_service.ingest.watersheds import (
    build_watershed_identity,
    fetch_watersheds,
    parse_load_date,
)

# Captured 2026-08-07 from NHDPlus_HR layer 12. `loaddate` really is epoch MILLISECONDS, and
# `states` really is null on these rows -- both are what the parser is written around.
SANDY_RIVER_LOAD_DATE_MS = 1358492970000


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
