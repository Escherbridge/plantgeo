"""The registered watersheds watermark: NHDPlus_HR's own loaddate, and never a database session.

`pipeline/parquet/lane_registry.py::_watersheds_watermark` is a three-line delegation to
`read_watersheds_source_watermark`; what is worth pinning is here, where the bbox policy and the
source call live. `ingest.watersheds.fetch_watersheds` is monkeypatched shut throughout -- no network
call, and no database at all, which is the property the 2026-09-06 swap exists for.
"""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.pipeline.direct.watersheds import source as source_module
from agri_data_service.pipeline.direct.watersheds.watermark import (
    WatershedsWatermarkError,
    read_watersheds_source_watermark,
)
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY, LaneRegistration, LaneRegistryError

if TYPE_CHECKING:
    from collections.abc import Sequence

VALID_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 45.0], [-119.0, 46.0], [-120.0, 46.0], [-120.0, 45.0]]],
}
BBOX = "-125,42,-111,49"
#: 2019-11-21, the load date the live NHDPlus_HR extent reported on 2026-09-06 -- the measurement that
#: proved the direct writer correctly publishes NOTHING, because the published version (2026-08-07) is
#: later than the source's own vintage.
MEASURED_LOADDATE_MS = 1574294400000
OLDER_LOADDATE_MS = 1358492970000


def _feature(huc12: str, *, loaddate: object) -> dict[str, object]:
    return {
        "properties": {
            "huc12": huc12,
            "name": "Test Creek",
            "areasqkm": 12.5,
            "tohuc": "170900011200",
            "states": "OR",
            "hutype": "S",
            "loaddate": loaddate,
        },
        "geometry": VALID_SQUARE,
    }


def _stub_fetch(features: Sequence[dict[str, object]]):  # noqa: ANN202 - matches `fetch_watersheds`'s own shape
    async def fetch_watersheds(client: Any, bbox: str) -> Sequence[dict[str, object]]:  # noqa: ARG001
        return features

    return fetch_watersheds


@pytest.mark.asyncio
async def test_the_watermark_is_the_sources_own_loaddate_and_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    """DO NOT DELETE. This is the clock that replaced `sql/pipeline/lane_watermark_watersheds.sql`.

    That query read `geo.features`' change-gated `updated_at`/`created_at` for this layer -- an
    INGESTION-time proxy for the source's vintage, written by `postgres-watersheds` and by nothing
    else, so it freezes at a version that never changes again the moment that lane stops. WBD's own
    `loaddate` answers the same question and keeps answering it after `geo.features` is dropped.
    """
    monkeypatch.setenv("INGEST_BBOX", BBOX)
    features = [
        _feature("170900011201", loaddate=OLDER_LOADDATE_MS),
        _feature("170900011202", loaddate=MEASURED_LOADDATE_MS),
    ]
    monkeypatch.setattr(source_module, "fetch_watersheds", _stub_fetch(features))

    watermark = await read_watersheds_source_watermark()

    newest = datetime.fromtimestamp(MEASURED_LOADDATE_MS / 1000, tz=UTC)
    assert watermark.day == newest.date()
    assert watermark.instant == newest
    assert "loaddate" in watermark.basis


@pytest.mark.asyncio
async def test_reading_the_watermark_opens_no_database_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of the swap: this answer is reachable with Postgres gone.

    Asserted structurally rather than by mocking a session -- `read_watersheds_source_watermark`
    accepts no session to pass one to, and the registry's wrapper is what absorbs the uniform
    resolver shape. A future edit that reintroduced a query here would have to change this signature.
    """
    monkeypatch.setenv("INGEST_BBOX", BBOX)
    stub = _stub_fetch([_feature("170900011201", loaddate=MEASURED_LOADDATE_MS)])
    monkeypatch.setattr(source_module, "fetch_watersheds", stub)

    parameters = inspect.signature(read_watersheds_source_watermark).parameters

    assert set(parameters) == {"bbox"}
    assert (await read_watersheds_source_watermark(bbox=BBOX)).day is not None


@pytest.mark.asyncio
async def test_an_unconfigured_bbox_is_an_unread_watermark_not_an_empty_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`day=None` means "USGS publishes no basins here", which would be a fabrication.

    `resolve_static_lane` turns `day=None` into `source_empty` and a raised read into
    `watermark_unread`; only the second is true of a run that never asked.
    """
    monkeypatch.delenv("INGEST_BBOX", raising=False)

    with pytest.raises(WatershedsWatermarkError, match="unread watermark, never an empty source"):
        await read_watersheds_source_watermark()


def test_the_registered_lane_still_refuses_a_writer_ceiling_on_a_static_lookup() -> None:
    """Swapping both fields changed nothing about the NATURE, and the nature is what refuses a ceiling.

    A `writer_ceiling` divides a calendar window between two writers, and a version-stamped lane has
    no calendar window to divide -- so `conflicts_with` on the two executor specs
    (`parquet-watersheds` against `watersheds-direct-forward`) stays the ENTIRE mutual-exclusion
    guard here, exactly as it was before the swap.
    """
    registered = LANE_REGISTRY["watersheds"]

    assert registered.nature == "static_lookup"
    assert registered.writer_ceiling is None
    with pytest.raises(LaneRegistryError, match="has no calendar window"):
        LaneRegistration(
            slug="watersheds",
            adapter=registered.adapter,
            history_floor=date(2026, 8, 7),
            publication_lag_days=0,
            nature="static_lookup",
            floor_basis="test fixture",
            watermark=registered.watermark,
            writer_ceiling=date(2026, 8, 7),
        )
