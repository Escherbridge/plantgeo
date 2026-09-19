"""The published lattice: one flooring rule, in integer micro-degrees, for every rung of every lane.

Layer L0: stdlib only. Three lanes used to carry three copies of this arithmetic (`fire_risk_daily`
shifted integers, `forecast_lane_bootstrap` divided Polars floats, `weather_forecast_daily` divided
Python floats), and the three disagreed at cell edges. Rationale, and why the INTEGER form is the
one that survived, live in `AGENTS.md` in this directory.

**Origins, not centroids.** Every function here returns the ORIGIN of the cell a coordinate falls
in: the lowest longitude and latitude the cell covers. A reader that wants the centre adds half a
pitch; nothing in this warehouse stores a centroid, because a coarse cell then contains exactly the
base cells whose own origins floor into it, which is an invariant a reader can check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal

from plantgeo_ml_service.foundation.parquet_paths import BASE_PARTITION_ZOOM, ZoomTier

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Coordinates are carried as integers of this many per degree. Six decimals is ~11 cm at the
#: equator, finer than any lane's grain, and an integer cannot drift by frame length the way
#: `value / pitch` does (memory note "Polars division is frame-length dependent").
MICRO_DEGREES_PER_DEGREE: Final = 1_000_000

#: Where the lattice STARTS. Snapping to the antimeridian and the south pole rather than to zero
#: means one cell never straddles the prime meridian or the equator.
LONGITUDE_ORIGIN_MICRO: Final = -180 * MICRO_DEGREES_PER_DEGREE
LATITUDE_ORIGIN_MICRO: Final = -90 * MICRO_DEGREES_PER_DEGREE

LatticeAxis = Literal["longitude", "latitude"]

AXIS_ORIGIN_MICRO: Final[Mapping[LatticeAxis, int]] = {
    "longitude": LONGITUDE_ORIGIN_MICRO,
    "latitude": LATITUDE_ORIGIN_MICRO,
}

#: The pitch of each DERIVED rung, in micro-degrees: agri-data-service's
#: `warehouse/parquet/tiers.py::TIER_RESOLUTION_DEGREES` (0.01, 0.2, 5.0 degrees) stated as integers.
#: `BASE_PARTITION_ZOOM` is deliberately absent: the base rung is written at whatever grain its
#: lane's own producer has, so it has no derivation pitch to declare.
TIER_PITCH_MICRO: Final[Mapping[ZoomTier, int]] = {9: 10_000, 5: 200_000, 0: 5_000_000}

#: The same ladder in degrees, for the one caller that must speak the sibling's float vocabulary.
TIER_RESOLUTION_DEGREES: Final[Mapping[ZoomTier, float]] = {
    zoom: pitch / MICRO_DEGREES_PER_DEGREE for zoom, pitch in TIER_PITCH_MICRO.items()
}


class LatticeError(ValueError):
    """Raised when a rung has no lattice pitch, or a coordinate cannot be placed on one."""


def tier_pitch_micro(zoom: ZoomTier) -> int:
    """Return one derived rung's pitch in micro-degrees, or refuse by naming why the base has none."""
    pitch = TIER_PITCH_MICRO.get(zoom)
    if pitch is None:
        raise LatticeError(
            f"z{zoom} has no lattice pitch: it is the base rung (z{BASE_PARTITION_ZOOM}), written at the grain "
            f"its lane's own producer has. The derived rungs are {tuple(sorted(TIER_PITCH_MICRO))}"
        )
    return pitch


def axis_origin_micro(axis: LatticeAxis) -> int:
    """Return where the lattice starts on one axis, in micro-degrees."""
    origin = AXIS_ORIGIN_MICRO.get(axis)
    if origin is None:
        raise LatticeError(f"{axis!r} is not a lattice axis; the lattice has {tuple(sorted(AXIS_ORIGIN_MICRO))}")
    return origin


def to_micro_degrees(value: float) -> int:
    """Return one coordinate as whole micro-degrees, rounding rather than truncating its float dust."""
    return round(value * MICRO_DEGREES_PER_DEGREE)


def from_micro_degrees(micro: int) -> float:
    """Return whole micro-degrees as a coordinate, with no accumulated product to round away."""
    return micro / MICRO_DEGREES_PER_DEGREE


def floor_micro_to_pitch(micro: int, *, pitch_micro: int, origin_micro: int) -> int:
    """Floor one micro-degree coordinate onto its cell ORIGIN, snapping to the lattice origin first.

    Integer floor division, so a negative coordinate — every longitude in this warehouse's envelope
    is negative — floors DOWN rather than toward zero, which is what `%` in C would not have done.
    """
    if pitch_micro < 1:
        raise LatticeError(f"a lattice pitch is at least one micro-degree, got {pitch_micro}")
    return ((micro - origin_micro) // pitch_micro) * pitch_micro + origin_micro


def floor_coordinate(value: float, *, zoom: ZoomTier, axis: LatticeAxis) -> float:
    """Return the origin of the `zoom`-rung cell holding one coordinate on one axis."""
    return from_micro_degrees(
        floor_micro_to_pitch(
            to_micro_degrees(value),
            pitch_micro=tier_pitch_micro(zoom),
            origin_micro=axis_origin_micro(axis),
        )
    )


__all__ = [
    "AXIS_ORIGIN_MICRO",
    "LATITUDE_ORIGIN_MICRO",
    "LONGITUDE_ORIGIN_MICRO",
    "MICRO_DEGREES_PER_DEGREE",
    "TIER_PITCH_MICRO",
    "TIER_RESOLUTION_DEGREES",
    "LatticeAxis",
    "LatticeError",
    "axis_origin_micro",
    "floor_coordinate",
    "floor_micro_to_pitch",
    "from_micro_degrees",
    "tier_pitch_micro",
    "to_micro_degrees",
]
