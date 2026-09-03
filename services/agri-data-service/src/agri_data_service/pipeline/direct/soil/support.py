"""The 1,568-cell ERA5-Land support: the Sentinel-2 NDVI 0.25-degree lattice, read from the dimension."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING, Final

from sqlalchemy import text

from agri_data_service.execution.backfill_types import AnalysisGridCell

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

#: `agri.spatial_cell.grid_name` for the lattice all eight soil streams were written against. It is
#: the SENTINEL-2 NDVI analysis lattice, not an ERA5 one: the three reviewed plans
#: (`plans/open-meteo-era5-land-pnw-{vpd,soiltemp,ndvi-lattice}-20220802-20260802.json`) carry
#: `grid_name: sentinel2-ndvi-0p25deg` while requesting the `era5_land` model, and
#: `scripts/soil_temperature_snapshot_breakdown.py` EXPECTED_CELL_GRID pins the same value on every
#: written row. The SUPPORT KEY (`era5-land-0.1deg`) names the source's own resolution and is a
#: different fact; see `pipeline/direct/AGENTS.md`, "The lattice and the support are two facts".
ERA5_LAND_GRID_NAME: Final = "sentinel2-ndvi-0p25deg"

#: Exactly how many cells that lattice holds. Two independent measurements agree: all three reviewed
#: plans carry 1,568 `cells` entries, and the pinned extent below is a COMPLETE box -- 56 longitudes
#: by 28 latitudes -- so the count is also arithmetic rather than a sample. A different count is a
#: changed support, and a day written against it would not be comparable with the history it extends.
ERA5_LAND_SUPPORT_CELL_COUNT: Final = 1_568

#: How many of those cells actually carry a value on a complete day. The remaining 98 are ocean or
#: out-of-domain, where ERA5-Land models nothing and the archive answers `null` rather than zero:
#: `scripts/vpd_snapshot_breakdown.py` and `scripts/build_soil_moisture_from_canonical_snapshot.py`
#: both pin EXPECTED_CELLS_PER_DAY = 1,470 and both REFUSE a day that holds any other number, across
#: all 1,556 immutable days. MEASURED for moisture and VPD; INHERITED for temperature, which rides
#: the same lattice, the same model and therefore the same land-sea mask but was never counted
#: independently. A forward day that answers a different number is refused rather than published, so
#: the first live temperature day proves or refutes the inheritance loudly.
ERA5_LAND_VALUE_CELL_COUNT: Final = 1_470

#: Coordinates are keyed at this precision so a float that round-trips through JSON still lands on
#: its centroid EXACTLY. Matching is equality on the key, never a nearest-neighbour search: a
#: tolerance window is how a value silently binds to the cell next door.
_COORDINATE_QUANTUM: Final = Decimal("0.000001")

#: The measured shape of the pinned lattice, read from the `cells` arrays of the three reviewed
#: plans: a 0.25-degree step over the Pacific Northwest, with centroids offset a HALF STEP off the
#: integer degree (42.125, 42.375, ...), which is why an integer-degree guard would reject every
#: real cell. 56 longitudes x 28 latitudes = 1,568, and the plans carry exactly that many, so this
#: lattice -- unlike the NASA POWER one -- is its complete bounding box and holds no gaps.
ERA5_LAND_SUPPORT_STEP_DEGREES: Final = Decimal("0.25")
ERA5_LAND_SUPPORT_CENTROID_OFFSET_DEGREES: Final = Decimal("0.125")
ERA5_LAND_SUPPORT_WEST: Final = Decimal("-124.875")
ERA5_LAND_SUPPORT_EAST: Final = Decimal("-111.125")
ERA5_LAND_SUPPORT_SOUTH: Final = Decimal("42.125")
ERA5_LAND_SUPPORT_NORTH: Final = Decimal("48.875")

#: The prefix every cell of this lattice keys under; a key without it belongs to another grid.
ERA5_LAND_SUPPORT_CELL_KEY_PREFIX: Final = f"{ERA5_LAND_GRID_NAME}:"

# One table, one predicate, no CTE and no join: `code_styleguides/sql.md` keeps a lookup of this
# shape inline beside its caller. `pipeline/direct/AGENTS.md` records the judgement.
_SUPPORT_CELLS_SQL: Final = text(
    "SELECT cell.id AS cell_id, cell.cell_key AS cell_key, cell.coverage_fraction AS coverage_fraction, "
    "ST_X(cell.centroid) AS cell_longitude, ST_Y(cell.centroid) AS cell_latitude "
    "FROM agri.spatial_cell AS cell WHERE cell.grid_name = :grid_name ORDER BY cell.cell_key"
)


class SoilSupportError(RuntimeError):
    """Raised when the ERA5-Land support cannot be resolved bit-identically to the historical one."""


@dataclass(frozen=True, slots=True)
class Era5LandSupportCell:
    """One analysis cell of the lattice, carrying the identity the historical rows carry."""

    cell_id: str
    cell_key: str
    cell_longitude: float
    cell_latitude: float
    coverage_fraction: float

    @property
    def analysis_cell(self) -> AnalysisGridCell:
        """Render this cell in the shape the shared archive attribution guard binds a point through."""
        return AnalysisGridCell(
            cell_key=self.cell_key,
            latitude=self.cell_latitude,
            longitude=self.cell_longitude,
        )


@dataclass(frozen=True, slots=True)
class Era5LandSupport:
    """The complete, ordered lattice plus the coordinate index a response is matched through."""

    cells: tuple[Era5LandSupportCell, ...]
    by_coordinate: Mapping[tuple[Decimal, Decimal], Era5LandSupportCell]

    def resolve(self, longitude: float, latitude: float) -> Era5LandSupportCell | None:
        """Return the one support cell a returned coordinate belongs to, or None when it belongs to none."""
        return self.by_coordinate.get((quantize_coordinate(longitude), quantize_coordinate(latitude)))


def quantize_coordinate(value: float) -> Decimal:
    """Round one WGS84 ordinate to the fixed key precision the support index is built at."""
    return Decimal(repr(value)).quantize(_COORDINATE_QUANTUM, rounding=ROUND_HALF_EVEN)


def build_support(cells: Sequence[Era5LandSupportCell]) -> Era5LandSupport:
    """Index an already-validated cell sequence by quantized centroid, refusing a collision."""
    if len(cells) != ERA5_LAND_SUPPORT_CELL_COUNT:
        raise SoilSupportError(
            f"the {ERA5_LAND_GRID_NAME} lattice holds {len(cells)} cells, not the "
            f"{ERA5_LAND_SUPPORT_CELL_COUNT} every historical soil day was written against; "
            "a day on a different support is not comparable with the history it extends"
        )
    indexed: dict[tuple[Decimal, Decimal], Era5LandSupportCell] = {}
    for cell in cells:
        require_pinned_lattice_cell(cell)
        key = (quantize_coordinate(cell.cell_longitude), quantize_coordinate(cell.cell_latitude))
        held = indexed.get(key)
        if held is not None:
            raise SoilSupportError(
                f"support cells {held.cell_key!r} and {cell.cell_key!r} share centroid {key}; "
                "an archive coordinate could not be bound to one of them"
            )
        indexed[key] = cell
    return Era5LandSupport(cells=tuple(cells), by_coordinate=indexed)


def require_pinned_lattice_cell(cell: Era5LandSupportCell) -> None:
    """Refuse a cell off the pinned lattice, so a re-keyed dimension cannot pass as the support."""
    longitude = quantize_coordinate(cell.cell_longitude)
    latitude = quantize_coordinate(cell.cell_latitude)
    longitude_remainder = (longitude - ERA5_LAND_SUPPORT_CENTROID_OFFSET_DEGREES) % ERA5_LAND_SUPPORT_STEP_DEGREES
    latitude_remainder = (latitude - ERA5_LAND_SUPPORT_CENTROID_OFFSET_DEGREES) % ERA5_LAND_SUPPORT_STEP_DEGREES
    if longitude_remainder or latitude_remainder:
        raise SoilSupportError(
            f"support cell {cell.cell_key!r} sits at ({longitude}, {latitude}), which is off the "
            f"{ERA5_LAND_SUPPORT_STEP_DEGREES}-degree step (offset "
            f"{ERA5_LAND_SUPPORT_CENTROID_OFFSET_DEGREES}) every historical soil day was written on"
        )
    if not (
        ERA5_LAND_SUPPORT_WEST <= longitude <= ERA5_LAND_SUPPORT_EAST
        and ERA5_LAND_SUPPORT_SOUTH <= latitude <= ERA5_LAND_SUPPORT_NORTH
    ):
        raise SoilSupportError(
            f"support cell {cell.cell_key!r} at ({longitude}, {latitude}) is outside the pinned extent "
            f"{ERA5_LAND_SUPPORT_WEST}..{ERA5_LAND_SUPPORT_EAST} by "
            f"{ERA5_LAND_SUPPORT_SOUTH}..{ERA5_LAND_SUPPORT_NORTH}"
        )
    if not cell.cell_key.startswith(ERA5_LAND_SUPPORT_CELL_KEY_PREFIX):
        raise SoilSupportError(
            f"support cell {cell.cell_key!r} is not a {ERA5_LAND_SUPPORT_CELL_KEY_PREFIX}* key; the "
            "historical rows were written against that lattice and only that one"
        )


async def load_era5_land_support(session: AsyncSession) -> Era5LandSupport:
    """Read the lattice from `agri.spatial_cell`, the dimension the snapshot rows were built from."""
    result = await session.execute(_SUPPORT_CELLS_SQL, {"grid_name": ERA5_LAND_GRID_NAME})
    cells = tuple(
        Era5LandSupportCell(
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
        raise SoilSupportError(f"agri.spatial_cell.{column} came back as {value!r}, which is not an identifier")
    return rendered


def _required_ordinate(value: object, *, column: str) -> float:
    """Narrow one untrusted result value to a finite float, naming the column when it is not."""
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise SoilSupportError(f"agri.spatial_cell.{column} came back as {type(value).__name__}, not a number")
    number = float(value)
    if not math.isfinite(number):
        raise SoilSupportError(f"agri.spatial_cell.{column} came back non-finite")
    return number


__all__ = [
    "ERA5_LAND_GRID_NAME",
    "ERA5_LAND_SUPPORT_CELL_COUNT",
    "ERA5_LAND_SUPPORT_CELL_KEY_PREFIX",
    "ERA5_LAND_SUPPORT_CENTROID_OFFSET_DEGREES",
    "ERA5_LAND_SUPPORT_EAST",
    "ERA5_LAND_SUPPORT_NORTH",
    "ERA5_LAND_SUPPORT_SOUTH",
    "ERA5_LAND_SUPPORT_STEP_DEGREES",
    "ERA5_LAND_SUPPORT_WEST",
    "ERA5_LAND_VALUE_CELL_COUNT",
    "Era5LandSupport",
    "Era5LandSupportCell",
    "SoilSupportError",
    "build_support",
    "load_era5_land_support",
    "quantize_coordinate",
    "require_pinned_lattice_cell",
]
