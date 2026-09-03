"""The 397-cell NASA POWER support: a ONE-DEGREE sample lattice, read from the historical dimension."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING, Final

from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

#: `agri.spatial_cell.grid_name` for the POWER lattice; `execution/weather_observations/nasa_power.py`
#: NASA_POWER_GRID_NAME and every `LaneCoverageContract` that names this source. THE LABEL NAMES THE
#: POWER PRODUCT RESOLUTION, NOT THE SAMPLE SPACING. See `pipeline/direct/AGENTS.md`, "The grid_name
#: misnomer": the cells below sit on a one-degree step, and the database value is never renamed.
NASA_POWER_GRID_NAME: Final = "nasa-power-0.5-degree"

#: Exactly how many cells that lattice holds. Three independent measurements agree: the snapshot
#: descriptors (`parquet_ops/snapshot_products.py` coverage_cells_per_day), the breakdown builders
#: (`scripts/build_shortwave_radiation_from_canonical_snapshot.py` EXPECTED_CELLS_PER_DAY) and
#: `agent/tools.py` line 81. A different count is a changed support, not a changed day, and a day
#: written against it would not be comparable with any historical day.
NASA_POWER_SUPPORT_CELL_COUNT: Final = 397

#: Coordinates are keyed at this precision so a float that round-trips through JSON still lands on
#: its centroid EXACTLY. Matching is equality on the key, never a nearest-neighbour search: a
#: tolerance window is how a value silently binds to the cell next door, and a POWER point that does
#: not land on a support centroid is a refusal rather than a guess. Integer-degree centroids need no
#: decimal places at all; six is generous and still exact.
_COORDINATE_QUANTUM: Final = Decimal("0.000001")

#: The measured shape of the pinned lattice, read from `plans/nasa-power-western-na-weather-fast-*.json`
#: (`nasa.cells`, 397 `na-sample:1deg:*` entries): a one-degree integer step over western North
#: America. 22 longitudes x 21 latitudes is 462 positions, of which the plan samples 397, so the
#: lattice is a SUBSET of its bounding box and no code may enumerate it from the extent.
NASA_POWER_SUPPORT_STEP_DEGREES: Final = Decimal(1)
NASA_POWER_SUPPORT_WEST: Final = Decimal(-125)
NASA_POWER_SUPPORT_EAST: Final = Decimal(-104)
NASA_POWER_SUPPORT_SOUTH: Final = Decimal(31)
NASA_POWER_SUPPORT_NORTH: Final = Decimal(51)

#: The one-degree step is also why the point endpoint needs no snapping guard: every integer degree
#: is exactly on POWER's 0.5-degree product grid, so the service echoes back what it was asked for.
NASA_POWER_SUPPORT_CELL_KEY_PREFIX: Final = "na-sample:1deg:"

# One table, one predicate, no CTE and no join: `code_styleguides/sql.md` keeps a lookup of this
# shape inline beside its caller. `pipeline/direct/AGENTS.md` records the judgement.
_SUPPORT_CELLS_SQL: Final = text(
    "SELECT cell.id AS cell_id, cell.cell_key AS cell_key, cell.coverage_fraction AS coverage_fraction, "
    "ST_X(cell.centroid) AS cell_longitude, ST_Y(cell.centroid) AS cell_latitude "
    "FROM agri.spatial_cell AS cell WHERE cell.grid_name = :grid_name ORDER BY cell.cell_key"
)


class ClimateSupportError(RuntimeError):
    """Raised when the POWER support cannot be resolved bit-identically to the historical one."""


@dataclass(frozen=True, slots=True)
class NasaPowerSupportCell:
    """One analysis cell of the POWER lattice, carrying the identity the historical rows carry."""

    cell_id: str
    cell_key: str
    cell_longitude: float
    cell_latitude: float
    coverage_fraction: float


@dataclass(frozen=True, slots=True)
class NasaPowerSupport:
    """The complete, ordered POWER lattice plus the coordinate index a response is matched through."""

    cells: tuple[NasaPowerSupportCell, ...]
    by_coordinate: Mapping[tuple[Decimal, Decimal], NasaPowerSupportCell]

    def resolve(self, longitude: float, latitude: float) -> NasaPowerSupportCell | None:
        """Return the one support cell a returned coordinate belongs to, or None when it belongs to none."""
        return self.by_coordinate.get((quantize_coordinate(longitude), quantize_coordinate(latitude)))


def quantize_coordinate(value: float) -> Decimal:
    """Round one WGS84 ordinate to the fixed key precision the support index is built at."""
    return Decimal(repr(value)).quantize(_COORDINATE_QUANTUM, rounding=ROUND_HALF_EVEN)


def build_support(cells: Sequence[NasaPowerSupportCell]) -> NasaPowerSupport:
    """Index an already-validated cell sequence by quantized centroid, refusing a collision."""
    if len(cells) != NASA_POWER_SUPPORT_CELL_COUNT:
        raise ClimateSupportError(
            f"the {NASA_POWER_GRID_NAME} lattice holds {len(cells)} cells, not the "
            f"{NASA_POWER_SUPPORT_CELL_COUNT} every historical climate day was written against; "
            "a day on a different support is not comparable with the history it extends"
        )
    indexed: dict[tuple[Decimal, Decimal], NasaPowerSupportCell] = {}
    for cell in cells:
        require_pinned_lattice_cell(cell)
        key = (quantize_coordinate(cell.cell_longitude), quantize_coordinate(cell.cell_latitude))
        held = indexed.get(key)
        if held is not None:
            raise ClimateSupportError(
                f"support cells {held.cell_key!r} and {cell.cell_key!r} share centroid {key}; "
                "a POWER coordinate could not be bound to one of them"
            )
        indexed[key] = cell
    return NasaPowerSupport(cells=tuple(cells), by_coordinate=indexed)


def require_pinned_lattice_cell(cell: NasaPowerSupportCell) -> None:
    """Refuse a cell off the pinned one-degree lattice, so a re-keyed dimension cannot pass as the support."""
    longitude = quantize_coordinate(cell.cell_longitude)
    latitude = quantize_coordinate(cell.cell_latitude)
    if longitude % NASA_POWER_SUPPORT_STEP_DEGREES or latitude % NASA_POWER_SUPPORT_STEP_DEGREES:
        raise ClimateSupportError(
            f"support cell {cell.cell_key!r} sits at ({longitude}, {latitude}), which is off the "
            f"{NASA_POWER_SUPPORT_STEP_DEGREES}-degree step every historical climate day was written on"
        )
    if not (
        NASA_POWER_SUPPORT_WEST <= longitude <= NASA_POWER_SUPPORT_EAST
        and NASA_POWER_SUPPORT_SOUTH <= latitude <= NASA_POWER_SUPPORT_NORTH
    ):
        raise ClimateSupportError(
            f"support cell {cell.cell_key!r} at ({longitude}, {latitude}) is outside the pinned extent "
            f"{NASA_POWER_SUPPORT_WEST}..{NASA_POWER_SUPPORT_EAST} by "
            f"{NASA_POWER_SUPPORT_SOUTH}..{NASA_POWER_SUPPORT_NORTH}"
        )
    if not cell.cell_key.startswith(NASA_POWER_SUPPORT_CELL_KEY_PREFIX):
        raise ClimateSupportError(
            f"support cell {cell.cell_key!r} is not a {NASA_POWER_SUPPORT_CELL_KEY_PREFIX}* key; the "
            "historical rows were written against that lattice and only that one"
        )


async def load_nasa_power_support(session: AsyncSession) -> NasaPowerSupport:
    """Read the POWER lattice from `agri.spatial_cell`, the dimension the snapshot rows were built from."""
    result = await session.execute(_SUPPORT_CELLS_SQL, {"grid_name": NASA_POWER_GRID_NAME})
    cells = tuple(
        NasaPowerSupportCell(
            cell_id=_required_text(row["cell_id"], column="cell_id"),
            cell_key=_required_text(row["cell_key"], column="cell_key"),
            cell_longitude=_required_ordinate(row["cell_longitude"], column="cell_longitude"),
            cell_latitude=_required_ordinate(row["cell_latitude"], column="cell_latitude"),
            coverage_fraction=_required_ordinate(row["coverage_fraction"], column="coverage_fraction"),
        )
        for row in result.mappings()
    )
    return build_support(cells)


def _required_text(value: object, *, column: str) -> str:
    """Narrow one untrusted result value to a non-blank identifier, naming the column when it is not."""
    rendered = str(value) if value is not None else ""
    if not rendered.strip():
        raise ClimateSupportError(f"agri.spatial_cell.{column} came back as {value!r}, which is not an identifier")
    return rendered


def _required_ordinate(value: object, *, column: str) -> float:
    """Narrow one untrusted result value to a finite float, naming the column when it is not."""
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise ClimateSupportError(f"agri.spatial_cell.{column} came back as {type(value).__name__}, not a number")
    number = float(value)
    if not math.isfinite(number):
        raise ClimateSupportError(f"agri.spatial_cell.{column} came back non-finite")
    return number


__all__ = [
    "NASA_POWER_GRID_NAME",
    "NASA_POWER_SUPPORT_CELL_COUNT",
    "NASA_POWER_SUPPORT_CELL_KEY_PREFIX",
    "NASA_POWER_SUPPORT_EAST",
    "NASA_POWER_SUPPORT_NORTH",
    "NASA_POWER_SUPPORT_SOUTH",
    "NASA_POWER_SUPPORT_STEP_DEGREES",
    "NASA_POWER_SUPPORT_WEST",
    "ClimateSupportError",
    "NasaPowerSupport",
    "NasaPowerSupportCell",
    "build_support",
    "load_nasa_power_support",
    "quantize_coordinate",
    "require_pinned_lattice_cell",
]
