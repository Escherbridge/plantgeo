"""Selection tiles, map support lattices, and balanced calendar pagination."""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.zoom import serving_zoom_tier
from agri_data_service.parquet_ops.request_params import (
    MAX_LONGITUDE,
    MIN_LONGITUDE,
    BoundingBox,
    RequestError,
    parse_calendar_day,
)
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER, TIER_RESOLUTION_DEGREES

if TYPE_CHECKING:
    from agri_data_service.foundation.parquet.zoom import ZoomTier

PAGE_DAYS: Final = 3
MAX_LANE_DAY_READS: Final = 8
MAX_RANGE_DAYS: Final = 36_600
MAX_FEATURES: Final = 12
TIME_SCALES: Final = ("day", "week", "month", "year", "all")
_MERCATOR_LATITUDE: Final = math.degrees(math.atan(math.sinh(math.pi)))
_MAX_MAP_ZOOM: Final = 24
_MAX_TILE_ZOOM: Final = 22


@dataclass(frozen=True, slots=True)
class Selection:
    """A validated map point, its display tile, and the caller's complete calendar window."""

    longitude: float
    latitude: float
    zoom: int
    tier: ZoomTier
    tile_x: int
    tile_y: int
    bbox: BoundingBox
    day: date
    first: date
    last: date
    time_scale: str
    page_start: int

    @classmethod
    def parse(cls, **values: object) -> Selection:
        """Reject malformed selections rather than replacing their date or geography."""
        longitude, latitude = float(str(values["longitude"])), float(str(values["latitude"]))
        if not (MIN_LONGITUDE <= longitude <= MAX_LONGITUDE and -_MERCATOR_LATITUDE <= latitude <= _MERCATOR_LATITUDE):
            raise RequestError("longitude/latitude must be finite coordinates inside the Web Mercator map extent")
        zoom_value = float(str(values["zoom"]))
        if not math.isfinite(zoom_value) or not 0 <= zoom_value <= _MAX_MAP_ZOOM:
            raise RequestError("zoom must be between 0 and 24")
        zoom = min(_MAX_TILE_ZOOM, math.floor(zoom_value))
        time_scale = str(values["time_scale"])
        if time_scale not in TIME_SCALES:
            raise RequestError(f"time_scale must be one of {TIME_SCALES}")
        day = parse_calendar_day(str(values["day"]), "day")
        first = parse_calendar_day(str(values["range_start"]), "range_start")
        last = parse_calendar_day(str(values["range_end"]), "range_end")
        if last < first or (last - first).days >= MAX_RANGE_DAYS:
            raise RequestError(f"range must be ascending and at most {MAX_RANGE_DAYS} calendar days")
        if not first <= day <= last:
            raise RequestError("the selected day must lie inside range_start..range_end")
        page_value = values["page_start"]
        if not isinstance(page_value, int) or isinstance(page_value, bool) or page_value < 0:
            raise RequestError("page_start must be a nonnegative integer")
        tile_x, tile_y, bbox = selection_tile(longitude, latitude, zoom)
        return cls(
            longitude,
            latitude,
            zoom,
            serving_zoom_tier(zoom),
            tile_x,
            tile_y,
            bbox,
            day,
            first,
            last,
            time_scale,
            page_value,
        )

    def page(self, budget: int = PAGE_DAYS) -> tuple[date, ...]:
        """Select a reproducible page spread across the entire active calendar interval."""
        ordered = balanced_days(self.first, self.last, self.day)
        return tuple(sorted(ordered[self.page_start : self.page_start + budget]))

    def to_wire(self) -> dict[str, object]:
        """Expose the requested map tile separately from the warehouse's serving rung."""
        return {
            "longitude": self.longitude,
            "latitude": self.latitude,
            "zoom": self.zoom,
            "zoom_tier": self.tier,
            "time_scale": self.time_scale,
            "range_start": self.first.isoformat(),
            "range_end": self.last.isoformat(),
            "tile": {"z": self.zoom, "x": self.tile_x, "y": self.tile_y, "bbox": list(self.bbox.as_envelope_arguments)},
        }


def selection_tile(longitude: float, latitude: float, zoom: int) -> tuple[int, int, BoundingBox]:
    """Compute the Web Mercator tile containing a WGS84 selection."""
    count = 2**zoom
    x = min(count - 1, math.floor((longitude + 180) / 360 * count))
    limited_latitude = min(_MERCATOR_LATITUDE, max(-_MERCATOR_LATITUDE, latitude))
    y = min(
        count - 1, max(0, math.floor((1 - math.asinh(math.tan(math.radians(limited_latitude))) / math.pi) / 2 * count))
    )
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / count))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / count))))
    return x, y, BoundingBox(x / count * 360 - 180, south, (x + 1) / count * 360 - 180, north)


def balanced_days(first: date, last: date, selected: date) -> tuple[date, ...]:
    """Visit endpoints and the selected day before bisecting the largest remaining gaps."""
    offsets = sorted({0, (last - first).days, (selected - first).days})
    answer = list(offsets)
    gaps = [(-(right - left), left, right) for left, right in pairwise(offsets) if right - left > 1]
    heapq.heapify(gaps)
    while gaps:
        _, left, right = heapq.heappop(gaps)
        middle = (left + right) // 2
        answer.append(middle)
        for low, high in ((left, middle), (middle, right)):
            if high - low > 1:
                heapq.heappush(gaps, (-(high - low), low, high))
    return tuple(first + timedelta(days=offset) for offset in answer)


def evidence_days(first: date, last: date, selected: date, published: set[date]) -> tuple[date, ...]:
    """Prioritize real published neighbours and distribute reads across the whole active interval."""
    available = sorted(day for day in published if first <= day <= last)
    before = [day for day in available if day < selected]
    after = [day for day in available if day > selected]
    priority = [first, last, selected, *before[-1:], *after[:1]]
    if available:
        index_days = balanced_days(date.min, date.min + timedelta(days=len(available) - 1), date.min)
        priority.extend(available[(day - date.min).days] for day in index_days)
    priority.extend(balanced_days(first, last, selected))
    return tuple(dict.fromkeys(priority))


def history_page_days(lane_count: int) -> int:
    """Bound total lane-day work, reserving one exact selected-day read per lane."""
    return min(PAGE_DAYS, max(1, MAX_LANE_DAY_READS // max(1, lane_count) - 1))


def support_lattice(surface: str, tier: ZoomTier) -> tuple[float, float, float]:
    """Mirror servedCellLattice in src/lib/map/zoom-tiers.ts: size, phase, snap correction."""
    if surface.startswith("climate-field-"):
        base_size, phase, centered = 1.0, -0.5, True
    elif surface.startswith("soil-field-") or surface == "vegetation":
        base_size, phase, centered = 0.25, 0.0, True
    elif surface == "fire-detections":
        base_size, phase, centered = 0.005, 0.0, False
    else:
        base_size, phase, centered = 0.0, 0.0, False
    if tier == BASE_ZOOM_TIER:
        return base_size, phase, 0.0 if centered else base_size / 2
    if surface not in {"water-gauges", "weather-observations"} and base_size == 0:
        return 0.0, 0.0, 0.0
    derived = TIER_RESOLUTION_DEGREES[tier]
    return max(base_size, derived), phase if derived < base_size else 0.0, derived / 2
