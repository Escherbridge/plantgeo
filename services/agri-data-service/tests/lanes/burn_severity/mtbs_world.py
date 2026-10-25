"""A fake MTBS EDW MapServer layer 63 behind `httpx.MockTransport`, answering the two query shapes the lane sends.

The provider edge is the only thing faked: the real `ConfigProviderClient` sends to it through the real
`lanes/_providers/mtbs-edw.toml`. It answers `returnCountOnly` with the cohort's count and a GeoJSON page
by `resultOffset`/`resultRecordCount` in `fire_id` order, and refuses a page wider than `max_page_rows`
with HTTP 500, as the live host refuses an oversized polygon answer (`ingest/mtbs.py::DEFAULT_PAGE_SIZE`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

import httpx

QUERY_PATH: Final = "/arcx/rest/services/EDW/EDW_MTBS_01/MapServer/63/query"
YEAR_PREFIX: Final = "year = "


def fire(fire_id: str, ignition_year: int, *, west: float = -120.0) -> dict[str, object]:
    """One burned-area boundary as the live layer publishes it: lowercase fields, a unit-square polygon."""
    return {
        "type": "Feature",
        "properties": {
            "fire_id": fire_id,
            "fire_name": f"FIRE {fire_id}",
            "year": ignition_year,
            "fire_type": "Wildfire",
            "acres": 1100.0,
            "map_id": 10029267,
            "asmnt_type": "Initial",
            "ig_date": int(f"{ignition_year}0801"),
            "pre_id": "804003020220904",
            "post_id": "804003020221006",
            "perim_id": None,
            "dnbr_offst": -1,
            "dnbr_stddv": 16,
            "nodata_threshold": -970,
            "greenness_threshold": -150,
            "low_threshold": 70,
            "moderate_threshold": 9999,
            "high_threshold": 9999,
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[west, 45.0], [west + 1, 45.0], [west + 1, 46.0], [west, 46.0], [west, 45.0]]],
        },
    }


def cohort(ignition_year: int, size: int) -> list[dict[str, object]]:
    """`size` fires ignited in `ignition_year`, ids sortable in the order the service pages them."""
    return [fire(f"F{ignition_year}{index:04d}", ignition_year) for index in range(size)]


@dataclass
class MtbsWorld:
    """The upstream: each fire year's features, an optional lying count, and the widest page the host serves."""

    fires: dict[int, list[dict[str, object]]] = field(default_factory=dict)
    count_overrides: dict[int, int] = field(default_factory=dict)
    max_page_rows: int = 50
    requests: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        """The MockTransport handler: a count, or one page of the year's features."""
        self.requests.append(request)
        if request.url.path != QUERY_PATH:
            return httpx.Response(404, json={"error": {"code": 404}})
        params = request.url.params
        year = int(params["where"].removeprefix(YEAR_PREFIX))
        features = sorted(self.fires.get(year, []), key=_fire_id)
        if params.get("returnCountOnly") == "true":
            return httpx.Response(200, json={"count": self.count_overrides.get(year, len(features))})
        offset, size = int(params["resultOffset"]), int(params["resultRecordCount"])
        if size > self.max_page_rows:
            return httpx.Response(500, json={"error": {"code": 500, "message": "Error performing query operation"}})
        return httpx.Response(200, json={"type": "FeatureCollection", "features": features[offset : offset + size]})

    def sends_for(self, ignition_year: int) -> list[httpx.QueryParams]:
        """Every query this year's cohort received, in order."""
        return [
            request.url.params
            for request in self.requests
            if request.url.params.get("where") == f"{YEAR_PREFIX}{ignition_year}"
        ]

    def pages_for(self, ignition_year: int) -> list[tuple[int, int]]:
        """`(resultOffset, resultRecordCount)` of every page request this year's cohort received."""
        return [
            (int(params["resultOffset"]), int(params["resultRecordCount"]))
            for params in self.sends_for(ignition_year)
            if params.get("returnCountOnly") != "true"
        ]


def _fire_id(feature: dict[str, object]) -> str:
    properties = feature["properties"]
    assert isinstance(properties, dict)
    return str(properties["fire_id"])
