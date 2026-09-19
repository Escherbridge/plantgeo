"""The bounding-box scan of a burn perimeter: well-known binary in, envelope out.

Layer L3, spec FR-5. Split out of `fire_risk_features.py` when that module passed the size ceiling;
this half is PURE and stdlib-only over bytes, so it is testable without a warehouse. Why an envelope
rather than the perimeter itself lives in `AGENTS-fire-risk.md`, "Why a bounding box is honest for
a fire": an envelope OVERSTATES a burn, so every feature built on it is a conservative upper bound.
"""

from __future__ import annotations

import struct
from typing import Final

#: A burn perimeter larger than this is not a perimeter; refusing beats an unbounded scan.
MAX_GEOMETRY_BYTES: Final = 8 * 1024 * 1024

_WKB_HEADER_BYTES: Final = 5
_WKB_POINT: Final = 1
_WKB_LINE_STRING: Final = 2
_WKB_POLYGON: Final = 3
_WKB_MULTI_POINT: Final = 4
_WKB_MULTI_LINE_STRING: Final = 5
_WKB_MULTI_POLYGON: Final = 6
_WKB_GEOMETRY_COLLECTION: Final = 7
_COORDINATE_BYTES: Final = 16


class FireRiskGeometryError(RuntimeError):
    """Raised when a burn perimeter cannot be read as the well-known binary it claims to be."""


def wkb_envelope(payload: bytes) -> tuple[float, float, float, float] | None:
    """Return one well-known-binary geometry's bounding box, or None when it carries no coordinate.

    An envelope OVERSTATES a perimeter, so every feature built on it is a conservative upper bound
    and is named for that. See `AGENTS-fire-risk.md`, "Why a bounding box is honest for a fire".
    """
    if len(payload) > MAX_GEOMETRY_BYTES:
        raise FireRiskGeometryError(
            f"a burn perimeter of {len(payload)} bytes exceeds the {MAX_GEOMETRY_BYTES}-byte scan budget; "
            "geometry that large in this lane means the lane changed shape"
        )
    bounds: list[float] = []
    _scan_geometry(memoryview(payload), 0, bounds)
    if not bounds:
        return None
    longitudes = bounds[0::2]
    latitudes = bounds[1::2]
    return (min(longitudes), min(latitudes), max(longitudes), max(latitudes))


def _scan_geometry(payload: memoryview, offset: int, bounds: list[float]) -> int:
    """Append every coordinate of one WKB geometry to `bounds`; return the offset just past it."""
    if offset + _WKB_HEADER_BYTES > len(payload):
        raise FireRiskGeometryError("a burn perimeter ends inside its own geometry header")
    byte_order = "<" if payload[offset] == 1 else ">"
    geometry_type = struct.unpack_from(f"{byte_order}I", payload, offset + 1)[0]
    cursor = offset + _WKB_HEADER_BYTES
    if geometry_type == _WKB_POINT:
        return _read_points(payload, cursor, 1, byte_order, bounds)
    if geometry_type in (_WKB_LINE_STRING, _WKB_MULTI_POINT):
        count = struct.unpack_from(f"{byte_order}I", payload, cursor)[0]
        if geometry_type == _WKB_LINE_STRING:
            return _read_points(payload, cursor + 4, count, byte_order, bounds)
        return _scan_children(payload, cursor + 4, count, bounds)
    if geometry_type == _WKB_POLYGON:
        return _scan_rings(payload, cursor, byte_order, bounds)
    if geometry_type in (_WKB_MULTI_LINE_STRING, _WKB_MULTI_POLYGON, _WKB_GEOMETRY_COLLECTION):
        count = struct.unpack_from(f"{byte_order}I", payload, cursor)[0]
        return _scan_children(payload, cursor + 4, count, bounds)
    raise FireRiskGeometryError(f"well-known binary type {geometry_type} is not a shape this envelope scan reads")


def _scan_rings(payload: memoryview, cursor: int, byte_order: str, bounds: list[float]) -> int:
    """Walk one polygon's rings, appending their coordinates."""
    ring_count = struct.unpack_from(f"{byte_order}I", payload, cursor)[0]
    position = cursor + 4
    for _ring in range(ring_count):
        point_count = struct.unpack_from(f"{byte_order}I", payload, position)[0]
        position = _read_points(payload, position + 4, point_count, byte_order, bounds)
    return position


def _scan_children(payload: memoryview, cursor: int, count: int, bounds: list[float]) -> int:
    """Walk one multi-geometry's children, each of which carries its own byte order and type."""
    position = cursor
    for _child in range(count):
        position = _scan_geometry(payload, position, bounds)
    return position


def _read_points(payload: memoryview, cursor: int, count: int, byte_order: str, bounds: list[float]) -> int:
    """Append `count` coordinate pairs and return the offset just past them."""
    end = cursor + count * _COORDINATE_BYTES
    if end > len(payload):
        raise FireRiskGeometryError("a burn perimeter claims more coordinates than its bytes hold")
    bounds.extend(struct.unpack_from(f"{byte_order}{count * 2}d", payload, cursor))
    return end


__all__ = [
    "MAX_GEOMETRY_BYTES",
    "FireRiskGeometryError",
    "wkb_envelope",
]
