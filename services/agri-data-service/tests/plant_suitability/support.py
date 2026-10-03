"""The generated plant-suitability fixture and small readers over the engine's public seams."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

from agri_data_service.warehouse.plant_suitability.engine import candidates_for_cell, evaluate_cells, prepare
from agri_data_service.warehouse.plant_suitability.schemas import SITE_INPUT_GROUPS
from agri_data_service.warehouse.plant_suitability.site import SiteInputProvenance, SiteInputSource
from agri_data_service.warehouse.plant_suitability.wetland import resolve_wetland_ratings

if TYPE_CHECKING:
    from collections.abc import Iterable

    from agri_data_service.warehouse.plant_suitability.config import RuleConfig
    from agri_data_service.warehouse.plant_suitability.engine import PreparedEngine

FIXTURE_DIRECTORY = Path(__file__).resolve().parents[1] / "fixtures" / "plant_suitability"
PRECIPITATION = "precipitation"


@dataclass(frozen=True)
class SuitabilityFixture:
    """Real v0 inputs for ~20 cells per pilot, their provenance, ERA5 precipitation, v0 outputs and the roles.

    A preset whose licence gate would withhold the PRISM precipitation it requires (PRODUCTION) is served the
    fixture's ERA5 precipitation and declaration instead, as production will be; every other preset reads PRISM.
    """

    site: pl.DataFrame
    species: pl.DataFrame
    guide_rows: pl.DataFrame
    exclusions: pl.DataFrame
    wetland_list: pl.DataFrame
    expected_v0: pl.DataFrame
    manifest: dict[str, Any]
    site_provenance: SiteInputProvenance
    era5_precipitation: pl.DataFrame
    era5_precipitation_source: SiteInputSource
    fixed_envelope: pl.DataFrame | None = None

    def role_cell(self, region: str, role: str) -> str:
        """The fixture cell build_fixtures.py chose for a named role."""
        return str(self.manifest["roles"][region][role])

    def taxa(self, region: str, key: str) -> Any:
        """A taxon name (or list) build_fixtures.py recorded for a role."""
        return self.manifest["taxa"][region][key]

    def site_row(self, cell_id: str) -> pl.DataFrame:
        """The one-row site-conditions frame for a fixture cell (PRISM precipitation, as v0 read it)."""
        return self.site.filter(pl.col("cell_id") == cell_id)

    def envelope(self, config: RuleConfig) -> pl.DataFrame:
        """The species envelope as the build step resolves it for the rule set, unless a flow fixed one."""
        if self.fixed_envelope is not None:
            return self.fixed_envelope
        return resolve_wetland_ratings(self.species, self.guide_rows, self.wetland_list, config)

    def uses_era5_precipitation(self, config: RuleConfig) -> bool:
        """True when the preset requires precipitation and its licence gate withholds the PRISM declaration."""
        required = PRECIPITATION in config.required_site_input_groups
        return required and PRECIPITATION in self.site_provenance.withheld_groups(config)

    def provenance(self, config: RuleConfig) -> SiteInputProvenance:
        """The site-input declaration the preset is served with."""
        if not self.uses_era5_precipitation(config):
            return self.site_provenance
        return SiteInputProvenance({**self.site_provenance.groups, PRECIPITATION: self.era5_precipitation_source})

    def served_site(self, site: pl.DataFrame, config: RuleConfig) -> pl.DataFrame:
        """The site rows the preset is served: ERA5 precipitation joined by cell id when it reads ERA5."""
        if not self.uses_era5_precipitation(config):
            return site
        columns = list(SITE_INPUT_GROUPS[PRECIPITATION])
        era5 = site.drop(columns).join(self.era5_precipitation, on="cell_id", how="left", maintain_order="left")
        return era5.select(site.columns)

    def prepared(self, config: RuleConfig) -> PreparedEngine:
        """The prepare-once seam over the fixture's reference tables and the preset's site provenance."""
        return prepare(self.envelope(config), self.guide_rows, self.exclusions, config, self.provenance(config))

    def evaluate(self, config: RuleConfig, site: pl.DataFrame | None = None) -> pl.DataFrame:
        """The batch seam over the fixture's cells (or the given site rows)."""
        cells = self.served_site(self.site if site is None else site, config)
        arguments = (self.envelope(config), self.guide_rows, self.exclusions, config, self.provenance(config))
        return evaluate_cells(cells, *arguments)

    def candidates(self, cell: str | pl.DataFrame, config: RuleConfig) -> pl.DataFrame:
        """The per-point seam for a fixture cell id, or for an explicit one-row site frame."""
        site = self.served_site(self.site_row(cell) if isinstance(cell, str) else cell, config)
        arguments = (self.envelope(config), self.guide_rows, self.exclusions, config, self.provenance(config))
        return candidates_for_cell(site, *arguments)

    def relicensed(self, source_ids: Iterable[str], licence: str) -> SuitabilityFixture:
        """The fixture with every guide row of the given sources' documents (same short name) under another licence."""
        rows = self.guide_rows
        documents = rows.filter(pl.col("source_id").is_in(sorted(source_ids)))["source_short_name"].unique()
        chosen = pl.col("source_short_name").is_in(documents.to_list())
        relicensed = pl.when(chosen).then(pl.lit(licence)).otherwise(pl.col("license")).alias("license")
        return replace(self, guide_rows=rows.with_columns(relicensed))


def load_fixture() -> SuitabilityFixture:
    """Read the tables and declarations tests/plant_suitability/build_fixtures.py wrote."""
    names = ("site_conditions", "species_envelope", "guide_rows", "exclusions", "wetland_list", "v0_expected_cells")
    tables = {name: pl.read_parquet(FIXTURE_DIRECTORY / f"{name}.parquet") for name in names}
    manifest = json.loads((FIXTURE_DIRECTORY / "fixture_cells.json").read_text(encoding="utf-8"))
    declared = json.loads((FIXTURE_DIRECTORY / "site_inputs.json").read_text(encoding="utf-8"))
    era5_source = json.loads((FIXTURE_DIRECTORY / "era5_precipitation_source.json").read_text(encoding="utf-8"))
    return SuitabilityFixture(
        site=tables["site_conditions"],
        species=tables["species_envelope"],
        guide_rows=tables["guide_rows"],
        exclusions=tables["exclusions"],
        wetland_list=tables["wetland_list"],
        expected_v0=tables["v0_expected_cells"],
        manifest=manifest,
        site_provenance=SiteInputProvenance({group: SiteInputSource(**source) for group, source in declared.items()}),
        era5_precipitation=pl.read_parquet(FIXTURE_DIRECTORY / "era5_precipitation.parquet"),
        era5_precipitation_source=SiteInputSource(**era5_source),
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
