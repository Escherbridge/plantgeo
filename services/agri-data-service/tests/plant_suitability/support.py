"""The generated plant-suitability fixture and small readers over the engine's two public seams."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

from agri_data_service.warehouse.plant_suitability.engine import candidates_for_cell, evaluate_cells

if TYPE_CHECKING:
    from agri_data_service.warehouse.plant_suitability.config import RuleConfig

FIXTURE_DIRECTORY = Path(__file__).resolve().parents[1] / "fixtures" / "plant_suitability"


@dataclass(frozen=True)
class SuitabilityFixture:
    """Real v0 inputs for ~20 cells per pilot, the frozen v0 outputs for them, and the role manifest."""

    site: pl.DataFrame
    species: pl.DataFrame
    guide_rows: pl.DataFrame
    exclusions: pl.DataFrame
    wetland_list: pl.DataFrame
    expected_v0: pl.DataFrame
    manifest: dict[str, Any]

    def role_cell(self, region: str, role: str) -> str:
        """The fixture cell build_fixtures.py chose for a named role."""
        return str(self.manifest["roles"][region][role])

    def taxa(self, region: str, key: str) -> Any:
        """A taxon name (or list) build_fixtures.py recorded for a role."""
        return self.manifest["taxa"][region][key]

    def site_row(self, cell_id: str) -> pl.DataFrame:
        """The one-row site-conditions frame for a fixture cell."""
        return self.site.filter(pl.col("cell_id") == cell_id)

    def evaluate(self, config: RuleConfig, site: pl.DataFrame | None = None) -> pl.DataFrame:
        """The batch seam over the fixture's cells (or the given site rows)."""
        cells = self.site if site is None else site
        return evaluate_cells(cells, self.species, self.guide_rows, self.exclusions, config)

    def candidates(self, cell: str | pl.DataFrame, config: RuleConfig) -> pl.DataFrame:
        """The per-point seam for a fixture cell id, or for an explicit one-row site frame."""
        site = self.site_row(cell) if isinstance(cell, str) else cell
        return candidates_for_cell(site, self.species, self.guide_rows, self.exclusions, config)


def load_fixture() -> SuitabilityFixture:
    """Read the tables tests/plant_suitability/build_fixtures.py wrote."""
    names = ("site_conditions", "species_envelope", "guide_rows", "exclusions", "wetland_list", "v0_expected_cells")
    tables = {name: pl.read_parquet(FIXTURE_DIRECTORY / f"{name}.parquet") for name in names}
    manifest = json.loads((FIXTURE_DIRECTORY / "fixture_cells.json").read_text(encoding="utf-8"))
    return SuitabilityFixture(
        site=tables["site_conditions"],
        species=tables["species_envelope"],
        guide_rows=tables["guide_rows"],
        exclusions=tables["exclusions"],
        wetland_list=tables["wetland_list"],
        expected_v0=tables["v0_expected_cells"],
        manifest=manifest,
    )


def candidate(candidates: pl.DataFrame, display_name: str, guild: str | None = None) -> dict[str, Any]:
    """One candidate row by name (and guild); a missing taxon fails loudly rather than passing vacuously."""
    rows = candidates.filter(pl.col("display_name") == display_name)
    if guild is not None:
        rows = rows.filter(pl.col("guild") == guild)
    if rows.height == 0:
        message = f"{display_name} is not a {guild or 'pool'} candidate here"
        raise AssertionError(message)
    return rows.row(0, named=True)


def cell_row(cells: pl.DataFrame, cell_id: str) -> dict[str, Any]:
    """One served cell as a dict."""
    return cells.filter(pl.col("cell_id") == cell_id).row(0, named=True)


def served_labels(cells: pl.DataFrame, guild: str) -> list[str]:
    """Every top-3 label the batch output serves for a guild."""
    return cells.select(pl.col(f"{guild}_top3_labels").explode(empty_as_null=True)).to_series().drop_nulls().to_list()
