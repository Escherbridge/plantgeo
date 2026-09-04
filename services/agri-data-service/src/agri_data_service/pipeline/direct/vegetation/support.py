"""The 1,568-cell vegetation support: the same Sentinel-2 NDVI 0.25-degree lattice `soil/` reuses.

`ingest/vegetation.py` NDVI_GRID_NAME names the lattice `sentinel2-ndvi-0p25deg`. This IS the
lattice `pipeline/direct/soil/support.py` reads for ERA5-Land (`pipeline/direct/AGENTS.md`, "Support
cells come from the dimension the history was built from" -> "sits on the 1,568-cell 0.25-degree
sentinel2-ndvi-0p25deg analysis lattice"), so the cell count, step and offset pinned there are the
SAME facts, not independently re-measured. This module does not import `pipeline.direct.soil`: two
sibling direct writers reading one shared dimension each own their own guard rather than coupling
import graphs across B-worker package boundaries.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING, Final

from sqlalchemy import text

from agri_data_service.ingest.vegetation import NDVI_CELL_SPACING_DEGREES, NDVI_GRID_NAME

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

#: Cross-checked against `pipeline/direct/soil/support.py` ERA5_LAND_SUPPORT_CELL_COUNT, the same
#: lattice read for a different product. Also arithmetic: 56 longitudes x 28 latitudes = 1,568 over
#: the pinned extent below, which is that box's COMPLETE tiling (unlike the NASA POWER 397-of-462
#: subset), so a truncated fetch is detectable by count alone.
NDVI_SUPPORT_CELL_COUNT: Final = 1_568

#: Coordinates are keyed at this precision so a value that round-trips through JSON still lands on
#: its centroid exactly. Matching is equality on the key, never a nearest-neighbour search.
_COORDINATE_QUANTUM: Final = Decimal("0.000001")

#: The measured shape of the pinned lattice (`pipeline/direct/soil/support.py`, same numbers): a
#: 0.25-degree step with centroids offset a HALF STEP off the integer degree (42.125, 42.375, ...) --
#: the odd-multiple-of-0.125 trap named in this track's brief. An integer-degree guard would reject
#: every real cell.
NDVI_SUPPORT_STEP_DEGREES: Final = Decimal(str(NDVI_CELL_SPACING_DEGREES))
NDVI_SUPPORT_CENTROID_OFFSET_DEGREES: Final = NDVI_SUPPORT_STEP_DEGREES / 2
NDVI_SUPPORT_WEST: Final = Decimal("-124.875")
NDVI_SUPPORT_EAST: Final = Decimal("-111.125")
NDVI_SUPPORT_SOUTH: Final = Decimal("42.125")
NDVI_SUPPORT_NORTH: Final = Decimal("48.875")

#: The prefix every `agri.spatial_cell.cell_key` of this lattice carries. The RAW fetch layer
#: (`ingest/vegetation.py::ndvi_grid_cells`) mints an UNPREFIXED `"{lat}:{lon}"` key -- "the grid is
#: anchored on the global origin, never on INGEST_BBOX" -- so `source.py` prepends this prefix before
#: joining a fetched record to its support cell. Prefix mismatch is exactly the failure mode this
#: track's other direct writers guard against by construction; here it is a string join, not a
#: coordinate re-derivation, so the guard is the `startswith` check in `require_pinned_lattice_cell`.
NDVI_SUPPORT_CELL_KEY_PREFIX: Final = f"{NDVI_GRID_NAME}:"

# One table, one predicate, no CTE and no join: `code_styleguides/sql.md` keeps a lookup of this
# shape inline beside its caller, the same judgement `pipeline/direct/soil/support.py` records.
_SUPPORT_CELLS_SQL: Final = text(
    "SELECT cell.id AS cell_id, cell.cell_key AS cell_key, "
    "ST_X(cell.centroid) AS cell_longitude, ST_Y(cell.centroid) AS cell_latitude "
    "FROM agri.spatial_cell AS cell WHERE cell.grid_name = :grid_name ORDER BY cell.cell_key"
)


class VegetationSupportError(RuntimeError):
    """Raised when the NDVI support cannot be resolved bit-identically to the historical one."""


@dataclass(frozen=True, slots=True)
class VegetationSupportCell:
    """One analysis cell of the lattice, carrying the identity the historical rows carry."""

    cell_id: str
    cell_key: str
    cell_longitude: float
    cell_latitude: float


@dataclass(frozen=True, slots=True)
class VegetationSupport:
    """The complete, ordered lattice plus the cell-key index a raw fetch record is joined through."""

    cells: tuple[VegetationSupportCell, ...]
    by_cell_key: Mapping[str, VegetationSupportCell]

    def resolve(self, raw_cell_key: str) -> VegetationSupportCell | None:
        """Return the support cell an UNPREFIXED raw fetch `cellKey` belongs to, or None for neither."""
        return self.by_cell_key.get(f"{NDVI_SUPPORT_CELL_KEY_PREFIX}{raw_cell_key}")

    @property
    def bbox(self) -> str:
        """Return the tight `west,south,east,north` box the raw fetch must be asked over.

        Derived from the LOADED cells, not the pinned constants, so a support that somehow drifted
        still reports honestly what it actually covers -- `build_support` still refuses it before
        this property is ever read for a real fetch.
        """
        longitudes = [cell.cell_longitude for cell in self.cells]
        latitudes = [cell.cell_latitude for cell in self.cells]
        half = float(NDVI_SUPPORT_CENTROID_OFFSET_DEGREES)
        return (
            f"{min(longitudes) - half:.6f},{min(latitudes) - half:.6f},"
            f"{max(longitudes) + half:.6f},{max(latitudes) + half:.6f}"
        )


def quantize_coordinate(value: float) -> Decimal:
    """Round one WGS84 ordinate to the fixed key precision the support index is built at."""
    return Decimal(repr(value)).quantize(_COORDINATE_QUANTUM, rounding=ROUND_HALF_EVEN)


def build_support(cells: Sequence[VegetationSupportCell]) -> VegetationSupport:
    """Index an already-validated cell sequence by cell_key, refusing a count mismatch or collision."""
    if len(cells) != NDVI_SUPPORT_CELL_COUNT:
        raise VegetationSupportError(
            f"the {NDVI_GRID_NAME} lattice holds {len(cells)} cells, not the {NDVI_SUPPORT_CELL_COUNT} "
            "every historical vegetation day was written against; a day on a different support is not "
            "comparable with the history it extends"
        )
    indexed: dict[str, VegetationSupportCell] = {}
    for cell in cells:
        require_pinned_lattice_cell(cell)
        if cell.cell_key in indexed:
            raise VegetationSupportError(f"support cell key {cell.cell_key!r} is duplicated in agri.spatial_cell")
        indexed[cell.cell_key] = cell
    return VegetationSupport(cells=tuple(cells), by_cell_key=indexed)


def require_pinned_lattice_cell(cell: VegetationSupportCell) -> None:
    """Refuse a cell off the pinned lattice, so a re-keyed dimension cannot pass as the support."""
    longitude = quantize_coordinate(cell.cell_longitude)
    latitude = quantize_coordinate(cell.cell_latitude)
    longitude_remainder = (longitude - NDVI_SUPPORT_CENTROID_OFFSET_DEGREES) % NDVI_SUPPORT_STEP_DEGREES
    latitude_remainder = (latitude - NDVI_SUPPORT_CENTROID_OFFSET_DEGREES) % NDVI_SUPPORT_STEP_DEGREES
    if longitude_remainder or latitude_remainder:
        raise VegetationSupportError(
            f"support cell {cell.cell_key!r} sits at ({longitude}, {latitude}), which is off the "
            f"{NDVI_SUPPORT_STEP_DEGREES}-degree step (offset {NDVI_SUPPORT_CENTROID_OFFSET_DEGREES}) "
            "every historical vegetation day was written on"
        )
    if not (
        NDVI_SUPPORT_WEST <= longitude <= NDVI_SUPPORT_EAST and NDVI_SUPPORT_SOUTH <= latitude <= NDVI_SUPPORT_NORTH
    ):
        raise VegetationSupportError(
            f"support cell {cell.cell_key!r} at ({longitude}, {latitude}) is outside the pinned extent "
            f"{NDVI_SUPPORT_WEST}..{NDVI_SUPPORT_EAST} by {NDVI_SUPPORT_SOUTH}..{NDVI_SUPPORT_NORTH}"
        )
    if not cell.cell_key.startswith(NDVI_SUPPORT_CELL_KEY_PREFIX):
        raise VegetationSupportError(
            f"support cell {cell.cell_key!r} is not a {NDVI_SUPPORT_CELL_KEY_PREFIX}* key; the historical "
            "rows were written against that lattice and only that one"
        )


async def load_vegetation_support(session: AsyncSession) -> VegetationSupport:
    """Read the lattice from `agri.spatial_cell`, the dimension the historical rows were built from."""
    result = await session.execute(_SUPPORT_CELLS_SQL, {"grid_name": NDVI_GRID_NAME})
    cells = tuple(
        VegetationSupportCell(
            cell_id=_required_text(row["cell_id"], column="cell_id"),
            cell_key=_required_text(row["cell_key"], column="cell_key"),
            cell_longitude=_required_ordinate(row["cell_longitude"], column="cell_longitude"),
            cell_latitude=_required_ordinate(row["cell_latitude"], column="cell_latitude"),
        )
        for row in result.mappings()
    )
    return build_support(cells)


def _required_text(value: object, *, column: str) -> str:
    """Narrow one untrusted result value to a non-blank identifier, naming the column when it is not."""
    rendered = str(value) if value is not None else ""
    if not rendered.strip():
        raise VegetationSupportError(f"agri.spatial_cell.{column} came back as {value!r}, which is not an identifier")
    return rendered


def _required_ordinate(value: object, *, column: str) -> float:
    """Narrow one untrusted result value to a finite float, naming the column when it is not."""
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise VegetationSupportError(f"agri.spatial_cell.{column} came back as {type(value).__name__}, not a number")
    number = float(value)
    if not math.isfinite(number):
        raise VegetationSupportError(f"agri.spatial_cell.{column} came back non-finite")
    return number


__all__ = [
    "NDVI_SUPPORT_CELL_COUNT",
    "NDVI_SUPPORT_CELL_KEY_PREFIX",
    "NDVI_SUPPORT_CENTROID_OFFSET_DEGREES",
    "NDVI_SUPPORT_EAST",
    "NDVI_SUPPORT_NORTH",
    "NDVI_SUPPORT_SOUTH",
    "NDVI_SUPPORT_STEP_DEGREES",
    "NDVI_SUPPORT_WEST",
    "VegetationSupport",
    "VegetationSupportCell",
    "VegetationSupportError",
    "build_support",
    "load_vegetation_support",
    "quantize_coordinate",
    "require_pinned_lattice_cell",
]
