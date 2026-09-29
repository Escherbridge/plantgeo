"""A fake USGS Water Data OGC API behind `httpx.MockTransport`, shaped as probe P4 observed it.

The provider edge is the only thing faked: the real `ConfigProviderClient` sends to it through the real
`lanes/_providers/usgs-water-data.toml`. It answers the two collections the lane asks for, filters by
`bbox` (inclusive edges, so a gauge on a tile edge is served to both tiles, as the real API does), by the
`time` interval's named days, and mints a fresh feature `id` on every answer (P4: the UUID "is not stable
over time").
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

DAILY_PATH: Final = "/ogcapi/v1/collections/daily/items"
LOCATIONS_PATH: Final = "/ogcapi/v1/collections/monitoring-locations/items"


@dataclass(frozen=True)
class Gauge:
    """One stream site: its id, name, point and the time-zone abbreviation the locations collection serves."""

    monitoring_location_id: str
    name: str
    longitude: float
    latitude: float
    time_zone: str = "PST"


@dataclass(frozen=True)
class Reading:
    """One served daily value, fields verbatim; `time` overrides the plain `YYYY-MM-DD` label when set."""

    value: str | None
    approval_status: str | None = "Provisional"
    qualifier: list[str | None] | None = None
    time: str | None = None
    unit_of_measure: str = "ft^3/s"


#: The Columbia at The Dalles (tile -125,42,-121,46), Boise at Glenwood Bridge (tile -117,42,-113,46), and a
#: gauge exactly on the -121 meridian, which both western tiles serve.
DALLES: Final = Gauge("USGS-14105700", "COLUMBIA RIVER AT THE DALLES, OR", -121.1722, 45.6076, "PST")
BOISE: Final = Gauge("USGS-13206000", "BOISE RIVER AT GLENWOOD BRIDGE NR BOISE ID", -116.2437, 43.6604, "MST")
MERIDIAN: Final = Gauge("USGS-14120000", "HOOD RIVER AT TUCKER BRIDGE, NEAR HOOD RIVER, OR", -121.0, 44.0, "PST")


def _inside(gauge: Gauge, bbox: str) -> bool:
    west, south, east, north = (float(part) for part in bbox.split(","))
    return west <= gauge.longitude <= east and south <= gauge.latitude <= north


def _asked_days(interval: str) -> tuple[date, date]:
    first, last = interval.split("/")
    return date.fromisoformat(first), date.fromisoformat(last)


@dataclass
class UsgsWaterDataWorld:
    """The upstream: gauges, their readings by (gauge id, named day), and tiles that answer 503."""

    gauges: list[Gauge]
    readings: dict[tuple[str, date], Reading] = field(default_factory=dict)
    #: A per-day default for any (gauge, day) not in `readings`; `None` serves nothing.
    reading_for: Callable[[Gauge, date], Reading | None] = lambda _gauge, _day: None
    #: Daily-values requests to these tile boxes answer 503.
    failing_tiles: set[str] = field(default_factory=set)
    #: (tile bbox, gauge id) -> a value that tile serves instead of the gauge's reading.
    tile_overrides: dict[tuple[str, str], str] = field(default_factory=dict)
    #: Gauge ids the monitoring-locations collection leaves out (a daily value with no site record).
    unnamed: set[str] = field(default_factory=set)
    requests: list[httpx.Request] = field(default_factory=list)

    def reading(self, gauge: Gauge, day: date) -> Reading | None:
        """What the upstream holds for one gauge-day."""
        return self.readings.get((gauge.monitoring_location_id, day)) or self.reading_for(gauge, day)

    def daily_requests(self) -> list[httpx.Request]:
        """Every daily-values request received, in order."""
        return [request for request in self.requests if request.url.path == DAILY_PATH]

    def handler(self, request: httpx.Request) -> httpx.Response:
        """The MockTransport handler: route by collection path."""
        self.requests.append(request)
        params = request.url.params
        if request.url.path == DAILY_PATH:
            if params["bbox"] in self.failing_tiles:
                return httpx.Response(503, content=b"Service Unavailable", headers={"content-type": "text/plain"})
            return self._json(self._daily(params["bbox"], *_asked_days(params["time"])))
        if request.url.path == LOCATIONS_PATH:
            return self._json(self._locations(params["bbox"]))
        return httpx.Response(404, content=b"{}", headers={"content-type": "application/json"})

    def _daily(self, bbox: str, first: date, last: date) -> dict[str, object]:
        features: list[dict[str, object]] = []
        for gauge in self.gauges:
            if not _inside(gauge, bbox):
                continue
            day = first
            while day <= last:
                reading = self.reading(gauge, day)
                if reading is not None:
                    value = self.tile_overrides.get((bbox, gauge.monitoring_location_id), reading.value)
                    features.append(_daily_feature(gauge, day, reading, value))
                day += timedelta(days=1)
        return {"type": "FeatureCollection", "features": features, "numberReturned": len(features), "links": []}

    def _locations(self, bbox: str) -> dict[str, object]:
        features = [
            {
                "type": "Feature",
                "properties": {"monitoring_location_name": gauge.name, "time_zone_abbreviation": gauge.time_zone},
                "id": gauge.monitoring_location_id,
                "geometry": None,
            }
            for gauge in self.gauges
            if _inside(gauge, bbox) and gauge.monitoring_location_id not in self.unnamed
        ]
        return {"type": "FeatureCollection", "features": features, "numberReturned": len(features), "links": []}

    @staticmethod
    def _json(document: dict[str, object]) -> httpx.Response:
        return httpx.Response(
            200, content=json.dumps(document, indent=4).encode("utf-8"), headers={"content-type": "application/json"}
        )


def _daily_feature(gauge: Gauge, day: date, reading: Reading, value: str | None) -> dict[str, object]:
    """One daily-values feature with the P4 property set (`properties=` restricted) and a fresh UUID."""
    return {
        "type": "Feature",
        "properties": {
            "time_series_id": f"series-{gauge.monitoring_location_id}",
            "monitoring_location_id": gauge.monitoring_location_id,
            "parameter_code": "00060",
            "statistic_id": "00003",
            "time": reading.time or day.isoformat(),
            "value": value,
            "unit_of_measure": reading.unit_of_measure,
            "approval_status": reading.approval_status,
            "qualifier": reading.qualifier,
        },
        "id": str(uuid.uuid4()),
        "geometry": {"type": "Point", "coordinates": [gauge.longitude, gauge.latitude]},
    }
