"""What `fetch_watersheds_snapshot` accepts, rejects, and dates -- with no network call.

`ingest.watersheds.fetch_watersheds` is monkeypatched shut: this module tests the accept/reject and
watermark logic layered on top of it, never the paged ArcGIS walk that module already owns.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.pipeline.direct.watersheds import source as source_module
from agri_data_service.pipeline.direct.watersheds.source import WatershedsSourceError, fetch_watersheds_snapshot

if TYPE_CHECKING:
    from collections.abc import Sequence

VALID_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 45.0], [-119.0, 46.0], [-120.0, 46.0], [-120.0, 45.0]]],
}
BBOX = "-125,42,-111,49"
#: The measured Sandy River `loaddate`, 2013-01-18, from `ingest/watersheds.py`'s own module docstring.
OLDER_LOADDATE_MS = 1358492970000
NEWER_LOADDATE_MS = 1400000000000


def _feature(huc12: str, *, loaddate: object, name: str = "Test Creek") -> dict[str, object]:
    return {
        "properties": {
            "huc12": huc12,
            "name": name,
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
async def test_accepted_basins_carry_the_parsed_loaddate_and_the_watermark_is_their_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    features = [
        _feature("170900011201", loaddate=OLDER_LOADDATE_MS),
        _feature("170900011202", loaddate=NEWER_LOADDATE_MS),
    ]
    monkeypatch.setattr(source_module, "fetch_watersheds", _stub_fetch(features))

    snapshot = await fetch_watersheds_snapshot(bbox=BBOX, client=object())

    assert {record.huc12 for record in snapshot.accepted} == {"170900011201", "170900011202"}
    assert snapshot.rejected_count == 0
    expected_newest = datetime.fromtimestamp(NEWER_LOADDATE_MS / 1000, tz=UTC)
    assert snapshot.watermark.day == expected_newest.date()
    assert snapshot.watermark.instant == expected_newest


@pytest.mark.asyncio
async def test_a_feature_with_no_huc12_is_rejected_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    features = [
        _feature("170900011201", loaddate=OLDER_LOADDATE_MS),
        {"properties": {"name": "no huc12 here"}, "geometry": VALID_SQUARE},
    ]
    monkeypatch.setattr(source_module, "fetch_watersheds", _stub_fetch(features))

    snapshot = await fetch_watersheds_snapshot(bbox=BBOX, client=object())

    assert {record.huc12 for record in snapshot.accepted} == {"170900011201"}
    assert snapshot.rejected_count == 1


@pytest.mark.asyncio
async def test_an_empty_fetch_reports_a_source_empty_watermark(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(source_module, "fetch_watersheds", _stub_fetch([]))

    snapshot = await fetch_watersheds_snapshot(bbox=BBOX, client=object())

    assert snapshot.accepted == ()
    assert snapshot.rejected_count == 0
    assert snapshot.watermark.day is None
    assert snapshot.watermark.instant is None


@pytest.mark.asyncio
async def test_a_nonempty_population_with_no_parseable_loaddate_refuses_rather_than_reports_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DO NOT DELETE. Basins exist but their vintage is unknown -- not the same claim as `source_empty`."""
    features = [_feature("170900011201", loaddate=None)]
    monkeypatch.setattr(source_module, "fetch_watersheds", _stub_fetch(features))

    with pytest.raises(WatershedsSourceError, match="NONE carried a parseable loaddate"):
        await fetch_watersheds_snapshot(bbox=BBOX, client=object())
