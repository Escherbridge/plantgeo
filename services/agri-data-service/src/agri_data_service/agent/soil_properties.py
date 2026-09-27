"""Read one SoilGrids v2.0 model estimate from the `soil-properties` Parquet lane (CONTRACT C1, C2).

Returns the C5.1 `soil` input section: mapped integers of the nearest 0.005-degree cell centre within the
radius, or `{"state": "unavailable", "reason": ...}`. Every value is a model estimate, never a measurement.
Gated by `SOIL_PROPERTIES_READS_ENABLED`. See agent/AGENTS.md, "Soil properties (SoilGrids)".
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from agri_data_service.agent import parquet_reads, warehouse
from agri_data_service.agent.site_brief import (
    SOIL_DEPTHS,
    SOIL_PROPERTY_CODES,
    SOIL_READS_ENABLED_VARIABLE,
    flag_enabled,
)
from agri_data_service.parquet_ops.faults import ServingRefusalError
from agri_data_service.parquet_ops.warehouse_reader import PointSupport, spatial_support
from agri_data_service.parquet_ops.wire import DayNotWritten, GovernedAbsenceDay, LaneNeverWritten, PublishedDay

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from agri_data_service.parquet_ops.warehouse_reader import WarehouseListing
    from agri_data_service.parquet_ops.wire import DayEnvelope

SOIL_PROPERTIES_LANE: Final = "soil-properties"
READS_ENABLED_VARIABLE: Final = SOIL_READS_ENABLED_VARIABLE

DEFAULT_RADIUS_METERS: Final = 1_000
MIN_RADIUS_METERS: Final = 50
MAX_RADIUS_METERS: Final = 2_000
#: `point_lane_rows` measures to the ORIGIN; half a 0.005 cell diagonal is ~340 m at 45 N (C2).
ORIGIN_SEARCH_MARGIN_METERS: Final = 400
#: Rows the widened circle can hold: ~pi * 2400^2 / (556 * 390) ~ 83 at the widest radius.
MAX_CANDIDATE_ROWS: Final = 128
DEFAULT_TIMEOUT_SECONDS: Final = 3.0

LATTICE_DEGREES: Final = 0.005
CELL_CENTRE_OFFSET_DEGREES: Final = LATTICE_DEGREES / 2
#: The pinned lattice envelope (products.py): (-125, 42) to (-111, 49).
LATTICE_WEST: Final = -125.0
LATTICE_SOUTH: Final = 42.0
LATTICE_EAST: Final = -111.0
LATTICE_NORTH: Final = 49.0

EARTH_RADIUS_METERS: Final = 6_371_008.8
METERS_PER_DEGREE_LATITUDE: Final = 110_574.0
METERS_PER_DEGREE_LONGITUDE_AT_EQUATOR: Final = 111_320.0
#: Keeps the longitude margin finite near the poles (CONTRACT C5.1; the web reader uses the same floor).
COVERAGE_COSINE_FLOOR: Final = 0.01
_INTEGRAL_TOLERANCE: Final = 1e-9

_SERVING_REASONS: Final[Mapping[str, str]] = {
    "serving_at_capacity": "serving_at_capacity",
    "read_timed_out": "timeout",
}


class SoilLaneCorruptError(ValueError):
    """A z13 value that is not an ISRIC mapped integer: the lane is corrupt and the read fails."""


def soil_reads_enabled() -> bool:
    """The kill switch: only the exact value `true` (whitespace trimmed, case-sensitive, as the web) enables reads."""
    return flag_enabled(READS_ENABLED_VARIABLE)


def clamp_radius(radius_meters: float) -> int:
    """Clamp a requested radius into 50..2000 m, as whole metres."""
    return int(max(MIN_RADIUS_METERS, min(MAX_RADIUS_METERS, round(radius_meters))))


def haversine_meters(longitude_a: float, latitude_a: float, longitude_b: float, latitude_b: float) -> float:
    """Haversine distance on a 6,371,008.8 m sphere (CONTRACT C2)."""
    phi_a = math.radians(latitude_a)
    phi_b = math.radians(latitude_b)
    delta_phi = phi_b - phi_a
    delta_lambda = math.radians(longitude_b - longitude_a)
    chord = math.sin(delta_phi / 2) ** 2 + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_METERS * math.asin(min(1.0, math.sqrt(chord)))


def outside_release_coverage(longitude: float, latitude: float, radius_meters: int) -> bool:
    """True when no lattice cell centre can lie within the radius of the point."""
    latitude_margin = radius_meters / METERS_PER_DEGREE_LATITUDE
    cosine = max(math.cos(math.radians(latitude)), COVERAGE_COSINE_FLOOR)
    longitude_margin = radius_meters / (METERS_PER_DEGREE_LONGITUDE_AT_EQUATOR * cosine)
    return not (
        LATTICE_WEST - longitude_margin <= longitude <= LATTICE_EAST + longitude_margin
        and LATTICE_SOUTH - latitude_margin <= latitude <= LATTICE_NORTH + latitude_margin
    )


def select_nearest_cell(
    rows: Sequence[Mapping[str, Any]], *, longitude: float, latitude: float, radius_meters: int
) -> tuple[Mapping[str, Any], int] | None:
    """C2 nearest-centre selection: min haversine to the centre, ties to lower latitude then longitude."""
    best: tuple[float, float, float, Mapping[str, Any]] | None = None
    for row in rows:
        origin_longitude = float(row["cell_longitude"])
        origin_latitude = float(row["cell_latitude"])
        distance = haversine_meters(
            longitude,
            latitude,
            origin_longitude + CELL_CENTRE_OFFSET_DEGREES,
            origin_latitude + CELL_CENTRE_OFFSET_DEGREES,
        )
        candidate = (distance, origin_latitude, origin_longitude, row)
        if best is None or candidate[:3] < best[:3]:
            best = candidate
    if best is None or best[0] > radius_meters:
        return None
    return best[3], math.floor(best[0] + 0.5)


def mapped_values(row: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    """The thirty z13 values as ISRIC mapped integers, keyed by depth then property (C5.1)."""
    mapped: dict[str, dict[str, int]] = {}
    for depth, _label in SOIL_DEPTHS:
        suffix = depth.removesuffix("cm").replace("-", "_")
        values: dict[str, int] = {}
        for code in SOIL_PROPERTY_CODES:
            column = f"{code}_{suffix}cm"
            raw = row.get(column)
            if raw is None:
                raise SoilLaneCorruptError(f"{column} is missing from a published soil-properties row")
            value = float(raw)
            if not math.isfinite(value) or abs(value - round(value)) >= _INTEGRAL_TOLERANCE or value < 0:
                raise SoilLaneCorruptError(f"{column}={value!r} is not a non-negative ISRIC mapped integer")
            values[code] = round(value)
        mapped[depth] = values
    return mapped


def _unavailable(reason: str, **extra: int) -> dict[str, Any]:
    return {"state": "unavailable", "reason": reason, **extra}


def _search_box(longitude: float, latitude: float, radius_meters: int) -> tuple[float, float, float, float]:
    """C2's bbox from radius: half-widths `r / 110574 + 0.005` and `r / (111320 cos lat) + 0.005`."""
    cosine = max(math.cos(math.radians(latitude)), COVERAGE_COSINE_FLOOR)
    latitude_half = radius_meters / METERS_PER_DEGREE_LATITUDE + LATTICE_DEGREES
    longitude_half = radius_meters / (METERS_PER_DEGREE_LONGITUDE_AT_EQUATOR * cosine) + LATTICE_DEGREES
    return longitude - longitude_half, latitude - latitude_half, longitude + longitude_half, latitude + latitude_half


async def _read_published(longitude: float, latitude: float, radius_meters: int, as_of: date) -> DayEnvelope:
    """Resolve the latest applicable release and read its candidate cells within radius + margin."""
    support = spatial_support(SOIL_PROPERTIES_LANE, "observed")
    if not isinstance(support, PointSupport):
        raise ServingRefusalError("bbox_unsupported", f"{SOIL_PROPERTIES_LANE} does not declare a coordinate pair")
    search_radius = radius_meters + ORIGIN_SEARCH_MARGIN_METERS
    west, south, east, north = _search_box(longitude, latitude, search_radius)

    async def read(keys: tuple[str, ...], evidence_source: WarehouseListing) -> list[dict[str, Any]]:
        return await warehouse.scan(
            parquet_reads.point_lane_rows(support),
            [west, east, south, north, latitude, longitude, search_radius, MAX_CANDIDATE_ROWS],
            part_keys=keys,
            operation="agent_soil_properties_at_point",
            layer=SOIL_PROPERTIES_LANE,
            evidence_source=evidence_source,
            required_columns=(support.longitude_column, support.latitude_column),
        )

    return await warehouse.release_rows(
        layer=SOIL_PROPERTIES_LANE,
        as_of=as_of,
        row_limit=MAX_CANDIDATE_ROWS,
        read=read,
    )


def _release_id(row: Mapping[str, Any], served_day: date) -> str:
    """`soilgrids-v2.0/2020-06-02`: the row's release name and the served version day."""
    return f"{row.get('source_release') or 'soilgrids-v2.0'}/{served_day.isoformat()}"


async def read_soil_properties(  # noqa: PLR0911 - one named C5.6 reason per guard; merging them would
    # blur which refusal fired, the same explicit-guard shape as `soilgrids.ts::readLane`.
    longitude: float,
    latitude: float,
    *,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    as_of: date | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Return the C5.1 soil section for one point; every failure is a C5.6 reason, never a raise."""
    if not soil_reads_enabled():
        return _unavailable("reads_disabled")
    radius = clamp_radius(radius_meters)
    if outside_release_coverage(longitude, latitude, radius):
        return _unavailable("outside_release_coverage")
    served_as_of = as_of or datetime.now(UTC).date()
    try:
        async with asyncio.timeout(timeout_seconds):
            envelope = await _read_published(longitude, latitude, radius, served_as_of)
    except TimeoutError:
        return _unavailable("timeout")
    except ServingRefusalError as refusal:
        return _unavailable(_SERVING_REASONS.get(refusal.code, "read_failed"))
    if isinstance(envelope, LaneNeverWritten):
        return _unavailable("lane_never_written")
    if isinstance(envelope, DayNotWritten | GovernedAbsenceDay):
        return _unavailable("not_published")
    if not isinstance(envelope, PublishedDay):
        return _unavailable("read_failed")
    chosen = select_nearest_cell(
        [dict(row) for row in envelope.rows], longitude=longitude, latitude=latitude, radius_meters=radius
    )
    if chosen is None:
        return _unavailable("no_cell_within_radius", radius_m=radius)
    row, distance = chosen
    try:
        mapped = mapped_values(row)
    except SoilLaneCorruptError:
        return _unavailable("read_failed")
    return {
        "state": "available",
        "release_id": _release_id(row, envelope.served_day),
        "distance_m": distance,
        "cell_longitude": float(row["cell_longitude"]),
        "cell_latitude": float(row["cell_latitude"]),
        "mapped": mapped,
    }


__all__ = [
    "DEFAULT_RADIUS_METERS",
    "MAX_RADIUS_METERS",
    "MIN_RADIUS_METERS",
    "READS_ENABLED_VARIABLE",
    "SOIL_PROPERTIES_LANE",
    "SoilLaneCorruptError",
    "clamp_radius",
    "haversine_meters",
    "mapped_values",
    "outside_release_coverage",
    "read_soil_properties",
    "select_nearest_cell",
    "soil_reads_enabled",
]
