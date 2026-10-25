"""The 1,568-cell NDVI support: pinned `agri.spatial_cell` identities checked against the region lattice.

Rationale (why a pinned file, not a database read): `pipeline/lanes/vegetation/AGENTS.md` "Support".
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Final

from agri_data_service.ingest.vegetation import NDVI_CELL_SPACING_DEGREES, NDVI_GRID_NAME

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agri_data_service.foundation.region.manifest import AnalysisLattice

#: The cell count every historical vegetation day was written against (56 x 28 over the lattice).
NDVI_SUPPORT_CELL_COUNT: Final = 1_568
#: The prefix every support `cell_key` carries; the raw fetch key is unprefixed `"{lat}:{lon}"`.
NDVI_SUPPORT_CELL_KEY_PREFIX: Final = f"{NDVI_GRID_NAME}:"
#: The operator-generated `agri.spatial_cell` snapshot (AGENTS.md "Support"); absent until generated.
PINNED_SUPPORT_PATH: Final = Path(__file__).with_name("support_cells.json")
_HALF_STEP_DEGREES: Final = NDVI_CELL_SPACING_DEGREES / 2


class VegetationSupportError(RuntimeError):
    """Raised when the support cannot be resolved identically to the one the history was written on."""


@dataclass(frozen=True, slots=True)
class VegetationSupportCell:
    """One lattice cell, carrying the `agri.spatial_cell` identity the published rows carry."""

    cell_id: str
    cell_key: str
    cell_longitude: float
    cell_latitude: float


@dataclass(frozen=True, slots=True)
class VegetationSupport:
    """The complete, key-ordered lattice plus the index a raw fetch record joins through."""

    cells: tuple[VegetationSupportCell, ...]
    by_cell_key: Mapping[str, VegetationSupportCell]

    def resolve(self, raw_cell_key: str) -> VegetationSupportCell | None:
        """The support cell an UNPREFIXED raw fetch `cellKey` names, or None when it names none."""
        return self.by_cell_key.get(f"{NDVI_SUPPORT_CELL_KEY_PREFIX}{raw_cell_key}")

    @property
    def bbox(self) -> str:
        """The `west,south,east,north` box the fetch asks over, derived from the cells (legacy formula)."""
        longitudes = [cell.cell_longitude for cell in self.cells]
        latitudes = [cell.cell_latitude for cell in self.cells]
        return (
            f"{min(longitudes) - _HALF_STEP_DEGREES:.6f},{min(latitudes) - _HALF_STEP_DEGREES:.6f},"
            f"{max(longitudes) + _HALF_STEP_DEGREES:.6f},{max(latitudes) + _HALF_STEP_DEGREES:.6f}"
        )

    @property
    def digest_label(self) -> str:
        """A short support label for request units: grid name and cell count."""
        return f"{NDVI_GRID_NAME}:{len(self.cells)}"


def build_support(cells: Sequence[VegetationSupportCell]) -> VegetationSupport:
    """Index a cell sequence by key, refusing a wrong count, a foreign prefix or a duplicate key."""
    if len(cells) != NDVI_SUPPORT_CELL_COUNT:
        raise VegetationSupportError(
            f"the {NDVI_GRID_NAME} support holds {len(cells)} cells, not the {NDVI_SUPPORT_CELL_COUNT} every "
            "historical vegetation day was written against"
        )
    indexed: dict[str, VegetationSupportCell] = {}
    for cell in cells:
        if not cell.cell_key.startswith(NDVI_SUPPORT_CELL_KEY_PREFIX):
            raise VegetationSupportError(f"support cell {cell.cell_key!r} is not a {NDVI_SUPPORT_CELL_KEY_PREFIX}* key")
        if not cell.cell_id.strip():
            raise VegetationSupportError(f"support cell {cell.cell_key!r} carries a blank cell_id")
        if cell.cell_key in indexed:
            raise VegetationSupportError(f"support cell key {cell.cell_key!r} is duplicated")
        indexed[cell.cell_key] = cell
    ordered = tuple(sorted(cells, key=lambda cell: cell.cell_key))
    return VegetationSupport(cells=ordered, by_cell_key=indexed)


def require_lattice_agreement(support: VegetationSupport, lattice: AnalysisLattice) -> None:
    """Refuse a support whose keys or centroids differ from the region's declared analysis lattice."""
    declared = {cell.cell_key: cell for cell in lattice.cells()}
    if set(declared) != set(support.by_cell_key):
        missing = sorted(set(declared) - set(support.by_cell_key))[:3]
        extra = sorted(set(support.by_cell_key) - set(declared))[:3]
        raise VegetationSupportError(
            "the pinned support and the region lattice disagree on cell keys "
            f"(missing e.g. {missing}, extra e.g. {extra})"
        )
    for key, lattice_cell in declared.items():
        cell = support.by_cell_key[key]
        if (cell.cell_latitude, cell.cell_longitude) != (lattice_cell.latitude, lattice_cell.longitude):
            raise VegetationSupportError(
                f"support cell {key!r} sits at ({cell.cell_latitude}, {cell.cell_longitude}), not the lattice "
                f"centroid ({lattice_cell.latitude}, {lattice_cell.longitude})"
            )


def support_from_rows(rows: Sequence[Mapping[str, object]]) -> VegetationSupport:
    """Build the support from `{cell_id, cell_key, cell_longitude, cell_latitude}` rows, narrowing each field."""
    return build_support(
        [
            VegetationSupportCell(
                cell_id=_text(row, "cell_id"),
                cell_key=_text(row, "cell_key"),
                cell_longitude=_ordinate(row, "cell_longitude"),
                cell_latitude=_ordinate(row, "cell_latitude"),
            )
            for row in rows
        ]
    )


@cache
def load_pinned_support(path: Path = PINNED_SUPPORT_PATH) -> VegetationSupport:
    """Read the pinned snapshot once per process; a missing file is a named error, never an empty support."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise VegetationSupportError(
            f"{path.name} is missing: the vegetation support's agri.spatial_cell identities have not been "
            "pinned yet (pipeline/lanes/vegetation/AGENTS.md 'Support')"
        ) from error
    if not isinstance(payload, list):
        raise VegetationSupportError(f"{path.name} is not a JSON list of support cells")
    return support_from_rows(payload)


def _text(row: Mapping[str, object], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise VegetationSupportError(f"support row field {field!r} came back as {value!r}, not an identifier")
    return value


def _ordinate(row: Mapping[str, object], field: str) -> float:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)):
        raise VegetationSupportError(f"support row field {field!r} came back as {value!r}, not a finite number")
    return float(value)


__all__ = [
    "NDVI_SUPPORT_CELL_COUNT",
    "NDVI_SUPPORT_CELL_KEY_PREFIX",
    "PINNED_SUPPORT_PATH",
    "VegetationSupport",
    "VegetationSupportCell",
    "VegetationSupportError",
    "build_support",
    "load_pinned_support",
    "require_lattice_agreement",
    "support_from_rows",
]
