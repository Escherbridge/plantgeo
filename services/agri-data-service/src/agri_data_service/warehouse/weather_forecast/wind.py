"""Meteorological wind component conversion and vector means; see AGENTS.md."""

from collections.abc import Iterable
from dataclasses import dataclass
from math import atan2, cos, degrees, fsum, hypot, isfinite, radians, sin

CALM_TOLERANCE_M_S = 1e-10
FULL_CIRCLE = 360


def _finite(value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError("wind components require finite numeric values")


@dataclass(frozen=True, slots=True)
class WindVector:
    """Eastward and northward velocity components in metres per second."""

    u: float
    v: float

    def __post_init__(self) -> None:
        _finite(self.u)
        _finite(self.v)

    @property
    def speed(self) -> float:
        return hypot(self.u, self.v)

    @property
    def direction(self) -> float | None:
        if self.speed <= CALM_TOLERANCE_M_S:
            return None
        direction = degrees(atan2(-self.u, -self.v)) % FULL_CIRCLE
        return 0.0 if abs(direction - FULL_CIRCLE) <= CALM_TOLERANCE_M_S else direction


def meteorological_vector(speed: float, direction: float | None) -> WindVector:
    """Convert speed and clockwise degrees from north into a velocity vector."""
    _finite(speed)
    if speed < 0:
        raise ValueError("wind speed cannot be negative")
    if direction is None:
        if speed == 0:
            return WindVector(0.0, 0.0)
        raise ValueError("non-calm wind requires direction")
    _finite(direction)
    if not 0 <= direction <= FULL_CIRCLE:
        raise ValueError("meteorological direction must be in [0, 360]")
    angle = radians(direction)
    return WindVector(-speed * sin(angle), -speed * cos(angle))


def mean_wind(vectors: Iterable[WindVector]) -> WindVector:
    """Average components with equal weights; an empty selection has no mean."""
    values = tuple(vectors)
    if not values:
        raise ValueError("cannot average an empty wind selection")
    count = len(values)
    return WindVector(fsum(value.u / count for value in values), fsum(value.v / count for value in values))
