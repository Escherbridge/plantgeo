"""The soil support: the manifest's 1,568-cell lattice, its request chunks, its probe pair, and its cell identities.

Rationale (why identities ship as package data, why ordinals are checked): `soil/AGENTS.md` "The support".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING, Final

from agri_data_service.pipeline.lanes._shared.digest import COMPACT_SEPARATORS, canonical_json_bytes, sha256_hex
from agri_data_service.pipeline.lanes.soil.archive import NATIVE_GRID_DEGREES

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agri_data_service.foundation.region.manifest import AnalysisLattice, AnalysisLatticeCell
    from agri_data_service.pipeline.lanes.soil.archive import Point

#: Cells every historical soil day was written on (`direct/soil/support.py` ERA5_LAND_SUPPORT_CELL_COUNT).
SUPPORT_CELL_COUNT: Final = 1_568

#: Cells per request: the reviewed plans' `chunk_cell_count` (`direct/soil/source.py` ERA5_LAND_CHUNK_CELL_COUNT).
CHUNK_CELL_COUNT: Final = 50

#: The package-data file holding each cell's `agri.spatial_cell` identity (`soil/AGENTS.md` "Cell identities").
SUPPORT_IDENTITY_RESOURCE: Final = "support_cells.json"

#: The document's schema tag; a different tag is refused rather than guessed at.
SUPPORT_IDENTITY_SCHEMA: Final = "soil-era5-land-support-identity-v1"

#: Coordinate key precision (`direct/soil/support.py::_COORDINATE_QUANTUM`): equality on the key, never nearest.
_COORDINATE_QUANTUM: Final = Decimal("0.000001")


class SoilSupportError(RuntimeError):
    """The lattice or the identity document is not the support the history was written on."""


@dataclass(frozen=True, slots=True)
class SupportChunk:
    """One request's cells, cut from the support in `cell_key` order."""

    key: str
    cells: tuple[AnalysisLatticeCell, ...]

    @property
    def points(self) -> tuple[Point, ...]:
        """The (latitude, longitude) centroids the request names, in order."""
        return tuple((cell.latitude, cell.longitude) for cell in self.cells)


@dataclass(frozen=True, slots=True)
class SupportCellIdentity:
    """One cell as the historical rows carry it: the dimension's id, centroid, coverage and support ordinal."""

    cell_key: str
    cell_id: str
    cell_latitude: float
    cell_longitude: float
    coverage_fraction: float
    support_ordinal: int


@dataclass(frozen=True, slots=True)
class SupportIdentity:
    """Every cell's identity, indexed by quantized centroid so an answered point binds to exactly one cell."""

    by_coordinate: Mapping[tuple[Decimal, Decimal], SupportCellIdentity]

    def resolve(self, point: Point) -> SupportCellIdentity | None:
        """The one cell a requested (latitude, longitude) belongs to, or `None`."""
        return self.by_coordinate.get((quantize(point[0]), quantize(point[1])))


def quantize(value: float) -> Decimal:
    """One ordinate at the fixed key precision (`direct/soil/support.py::quantize_coordinate`)."""
    return Decimal(repr(value)).quantize(_COORDINATE_QUANTUM, rounding=ROUND_HALF_EVEN)


def support_cells(lattice: AnalysisLattice) -> tuple[AnalysisLatticeCell, ...]:
    """The lattice in `cell_key` order (the legacy support's `ORDER BY cell_key`), refusing a changed support."""
    cells = tuple(sorted(lattice.cells(), key=lambda cell: cell.cell_key))
    if len(cells) != SUPPORT_CELL_COUNT:
        raise SoilSupportError(f"the soil lattice holds {len(cells)} cells, not {SUPPORT_CELL_COUNT}")
    step = NATIVE_GRID_DEGREES
    native = {(round(cell.latitude / step), round(cell.longitude / step)) for cell in cells}
    if len(native) != len(cells):
        # `direct/soil/source.py::support_chunks`: two cells in one 0.1-degree box get each other's value.
        raise SoilSupportError("two support cells share one native ERA5-Land box and cannot be told apart")
    return cells


def support_chunks(cells: Sequence[AnalysisLatticeCell]) -> tuple[SupportChunk, ...]:
    """Fixed 50-cell chunks keyed `cells-NNNN`, so a unit names the same cells on every turn."""
    return tuple(
        SupportChunk(key=f"cells-{index // CHUNK_CELL_COUNT:04d}", cells=tuple(cells[index : index + CHUNK_CELL_COUNT]))
        for index in range(0, len(cells), CHUNK_CELL_COUNT)
    )


def support_digest(cells: Sequence[AnalysisLatticeCell]) -> str:
    """Binds a checkpoint to the ordered support and its chunk boundaries."""
    keys = [[chunk.key, [cell.cell_key for cell in chunk.cells]] for chunk in support_chunks(cells)]
    return sha256_hex(canonical_json_bytes(keys, sort_keys=True, separators=COMPACT_SEPARATORS, allow_nan=False))


def probe_points(lattice: AnalysisLattice) -> tuple[Point, Point]:
    """The two on-lattice centroids straddling the extent centre (`direct/soil/source.py::probe_cells`).

    Two, never one: a one-location answer is a bare object, not the array every parse expects.
    """
    pitch = Decimal(repr(lattice.pitch_degrees))
    half = pitch / 2
    envelope = lattice.envelope
    centre_longitude = (Decimal(repr(envelope.west)) + Decimal(repr(envelope.east))) / 2
    latitude = (Decimal(repr(envelope.south)) + Decimal(repr(envelope.north))) / 2 - half
    candidates = ((float(latitude), float(centre_longitude - half)), (float(latitude), float(centre_longitude + half)))
    on_lattice = {(quantize(cell.latitude), quantize(cell.longitude)) for cell in lattice.cells()}
    if any((quantize(lat), quantize(lon)) not in on_lattice for lat, lon in candidates):
        raise SoilSupportError(f"the probe cells {candidates} are not on the soil lattice; its shape changed")
    return candidates


def parse_support_identity(document: Mapping[str, object]) -> SupportIdentity:
    """Validate the identity document: 1,568 cells, unique centroids, ordinals equal to `cell_key` order."""
    if document.get("schema") != SUPPORT_IDENTITY_SCHEMA:
        raise SoilSupportError(f"the support identity document is not {SUPPORT_IDENTITY_SCHEMA!r}")
    raw = document.get("cells")
    if not isinstance(raw, list) or len(raw) != SUPPORT_CELL_COUNT:
        raise SoilSupportError(f"the support identity document must hold exactly {SUPPORT_CELL_COUNT} cells")
    cells = tuple(
        SupportCellIdentity(
            cell_key=str(entry["cell_key"]),
            cell_id=str(entry["cell_id"]),
            cell_latitude=float(entry["cell_latitude"]),
            cell_longitude=float(entry["cell_longitude"]),
            coverage_fraction=float(entry["coverage_fraction"]),
            support_ordinal=int(entry["support_ordinal"]),
        )
        for entry in raw
    )
    by_key_order = sorted(cells, key=lambda cell: cell.cell_key)
    if [cell.support_ordinal for cell in by_key_order] != list(range(SUPPORT_CELL_COUNT)):
        # The legacy ordinal is Postgres `ORDER BY cell_key`; a collation that disagrees with Python's sort
        # would also have chunked the legacy requests differently, so it is refused, not reconciled.
        raise SoilSupportError("support ordinals do not follow cell_key order; legacy chunking is not reproducible")
    indexed: dict[tuple[Decimal, Decimal], SupportCellIdentity] = {}
    for cell in cells:
        key = (quantize(cell.cell_latitude), quantize(cell.cell_longitude))
        if key in indexed:
            raise SoilSupportError(f"cells {indexed[key].cell_key!r} and {cell.cell_key!r} share centroid {key}")
        indexed[key] = cell
    return SupportIdentity(by_coordinate=indexed)


@cache
def load_support_identity() -> SupportIdentity:
    """Read the shipped identity document once; absent or malformed is `SoilSupportError` (a day's `strategy_error`)."""
    resource = resources.files(__package__).joinpath(SUPPORT_IDENTITY_RESOURCE)
    try:
        document = json.loads(resource.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError) as error:
        raise SoilSupportError(
            f"{SUPPORT_IDENTITY_RESOURCE} is not shipped or not JSON ({error}); see soil/AGENTS.md 'Cell identities'"
        ) from error
    return parse_support_identity(document)


__all__ = [
    "CHUNK_CELL_COUNT",
    "SUPPORT_CELL_COUNT",
    "SUPPORT_IDENTITY_RESOURCE",
    "SUPPORT_IDENTITY_SCHEMA",
    "SoilSupportError",
    "SupportCellIdentity",
    "SupportChunk",
    "SupportIdentity",
    "load_support_identity",
    "parse_support_identity",
    "probe_points",
    "quantize",
    "support_cells",
    "support_chunks",
    "support_digest",
]
