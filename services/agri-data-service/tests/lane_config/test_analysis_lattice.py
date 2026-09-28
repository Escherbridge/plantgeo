"""The manifest's named analysis lattice against soil's written lattice (spec §4.1 C2).

`analysis-0p25` is declared once in `pnw.json`; config lanes grid onto it by name. It must produce
exactly the 1,568 `sentinel2-ndvi-0p25deg` cell keys every historical soil day was written against,
read from the soil plan-cell fixture (the reviewed ERA5-Land plan), or a config day would not join
the history it extends.
"""

from __future__ import annotations

import json
from importlib import resources

import pytest
from pydantic import ValidationError

from agri_data_service.foundation.region import Region, RegionEnvelope, load_region
from agri_data_service.foundation.region.manifest import AnalysisLattice
from agri_data_service.pipeline.direct.soil.support import ERA5_LAND_SUPPORT_CELL_COUNT
from tests.direct.soil.conftest import plan_cells


def test_the_pnw_lattice_reproduces_soils_1568_plan_cells_key_for_key() -> None:
    lattice = load_region("pnw").analysis_lattices["analysis-0p25"]
    manifest_cells = {cell.cell_key: (cell.latitude, cell.longitude) for cell in lattice.cells()}
    soil_cells = {cell.cell_key: (cell.cell_latitude, cell.cell_longitude) for cell in plan_cells()}

    assert len(manifest_cells) == ERA5_LAND_SUPPORT_CELL_COUNT
    assert manifest_cells == soil_cells
    assert lattice.cell_keys() == frozenset(soil_cells)


def test_a_region_that_declares_no_lattice_stays_valid_and_serves_none() -> None:
    """C2: the field is optional, so the second manifest loads unedited with no lattice to grid onto."""
    assert dict(load_region("kenya-highlands").analysis_lattices) == {}


@pytest.mark.parametrize(
    ("pitch_degrees", "envelope", "fragment"),
    [
        pytest.param(0.3, RegionEnvelope(west=-125.0, south=42.0, east=-111.0, north=49.0), "not a whole number"),
        pytest.param(0.0, RegionEnvelope(west=-125.0, south=42.0, east=-111.0, north=49.0), "must be > 0"),
    ],
)
def test_a_lattice_that_does_not_tile_its_envelope_is_refused(
    pitch_degrees: float, envelope: RegionEnvelope, fragment: str
) -> None:
    """A partial edge column would silently drop cells, so the manifest refuses the lattice outright."""
    with pytest.raises(ValidationError, match=fragment):
        AnalysisLattice(
            pitch_degrees=pitch_degrees,
            origin_rule="half_step",
            envelope=envelope,
            cell_key_prefix="sentinel2-ndvi-0p25deg:",
        )


def test_a_lattice_reaching_outside_its_region_is_refused() -> None:
    """A lattice wider than the manifest's own envelope would grid lanes onto ground the region never claims."""
    raw = json.loads(
        resources.files("agri_data_service.foundation.region").joinpath("pnw.json").read_text(encoding="utf-8")
    )
    raw["analysis_lattices"]["analysis-0p25"]["envelope"]["west"] = -130.0

    with pytest.raises(ValidationError, match="reaches outside"):
        Region.model_validate(raw)
