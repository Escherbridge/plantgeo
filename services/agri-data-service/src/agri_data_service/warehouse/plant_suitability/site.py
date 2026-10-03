"""Site conditions at load: fixed vocabularies, caller-declared input provenance, required groups and withholding.

Rationale, the column groups, the required-group gate and the withholding rule: AGENTS.md §Site inputs here.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

import polars as pl

from agri_data_service.warehouse.plant_suitability.axes import (
    DRAINAGE_CLASSES,
    HYDRIC_RATINGS,
    NWPL_REGIONS,
    RESTRICTION_STATUSES,
    TEXTURE_GROUPS,
    site_class_columns,
)
from agri_data_service.warehouse.plant_suitability.config import canonical_json
from agri_data_service.warehouse.plant_suitability.licences import KNOWN_LICENCES
from agri_data_service.warehouse.plant_suitability.schemas import (
    SITE_CONDITIONS_SCHEMA,
    SITE_INPUT_GROUPS,
    USPS_STATE_CODES,
    assert_vocabularies,
    conform,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.warehouse.plant_suitability.config import RuleConfig

# A withheld soil survey still withholds the cells it marks: nulling the reason would score them (fail open).
NEVER_WITHHELD_COLUMNS = frozenset({"scoring_null_reason"})
SITE_VOCABULARIES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "state": USPS_STATE_CODES,
        "nwpl_region": NWPL_REGIONS,
        "hydric_rating": HYDRIC_RATINGS,
        "drainage_class": DRAINAGE_CLASSES,
        "restriction_status": RESTRICTION_STATUSES,
        "site_texture_group": TEXTURE_GROUPS,
    }
)


@dataclass(frozen=True)
class SiteInputSource:
    """Where one site-input group came from: its source name, licence id and an optional release or checksum."""

    source: str
    licence: str
    release: str | None = None


@dataclass(frozen=True)
class SiteInputProvenance:
    """The caller's declaration of each site-input group's source; unknown groups or licence ids raise here."""

    groups: Mapping[str, SiteInputSource]

    def __post_init__(self) -> None:
        """Freeze a copy of the groups; refuse a group SITE_INPUT_GROUPS does not define, or an unknown licence id."""
        # A copy, read-only: the caller's dict mutated after prepare() would change serving under stale metadata.
        object.__setattr__(self, "groups", MappingProxyType(dict(self.groups)))
        unknown_groups = sorted(set(self.groups) - set(SITE_INPUT_GROUPS))
        unknown_licences = sorted({source.licence for source in self.groups.values()} - KNOWN_LICENCES)
        if unknown_groups or unknown_licences:
            message = f"site input provenance names unknown groups {unknown_groups} or licence ids {unknown_licences}"
            raise ValueError(message)

    def declaration_json(self) -> str:
        """Every declared group with its columns, source, licence and release, as canonical JSON."""
        declared = {
            group: {
                "columns": list(SITE_INPUT_GROUPS[group]),
                "source": source.source,
                "licence": source.licence,
                "release": source.release,
            }
            for group, source in self.groups.items()
        }
        return canonical_json(declared)

    def withheld_groups(self, config: RuleConfig) -> dict[str, str]:
        """Each declared group whose licence the preset does not permit, with that licence (empty without a gate)."""
        if config.permitted_licences is None:
            return {}
        return {
            group: source.licence
            for group, source in sorted(self.groups.items())
            if source.licence not in config.permitted_licences
        }


def assert_required_site_inputs(provenance: SiteInputProvenance, config: RuleConfig) -> None:
    """Raise, naming them, when a group the rule set requires is undeclared or its licence gate withholds it."""
    required = config.required_site_input_groups
    undeclared = sorted(required - set(provenance.groups))
    withheld = {group: licence for group, licence in provenance.withheld_groups(config).items() if group in required}
    if undeclared or withheld:
        message = (
            f"rule set {config.name!r} requires site input groups that are undeclared {undeclared} or withheld by its "
            f"licence gate {withheld}: declare a permitted source for each, so no pick is served blind to them"
        )
        raise ValueError(message)


def withhold_site_inputs(cells: pl.DataFrame, provenance: SiteInputProvenance, config: RuleConfig) -> pl.DataFrame:
    """Under a licence gate: raise on an undeclared group with values, and null each non-permitted group."""
    if config.permitted_licences is None:
        return cells
    undeclared = {
        group: carrying
        for group, columns in SITE_INPUT_GROUPS.items()
        if group not in provenance.groups
        and (carrying := [column for column in columns if cells[column].null_count() < cells.height])
    }
    if undeclared:
        message = f"site input groups carry values without a provenance declaration: {undeclared}"
        raise ValueError(message)
    withheld = [
        column
        for group in provenance.withheld_groups(config)
        for column in SITE_INPUT_GROUPS[group]
        if column not in NEVER_WITHHELD_COLUMNS
    ]
    return cells.with_columns([pl.lit(None, dtype=cells.schema[column]).alias(column) for column in withheld])


def prepare_site(site: pl.DataFrame, provenance: SiteInputProvenance, config: RuleConfig) -> pl.DataFrame:
    """Conformed, vocabulary-checked site conditions, licence-withheld groups nulled, plus the SSURGO site classes."""
    cells = conform(site, SITE_CONDITIONS_SCHEMA, "site conditions")
    assert_vocabularies(cells, SITE_VOCABULARIES, "site conditions")
    duplicated = cells.filter(pl.col("cell_id").is_duplicated())["cell_id"].unique().to_list()
    if duplicated:
        message = f"site conditions repeat cell ids {duplicated[:10]}"
        raise ValueError(message)
    return withhold_site_inputs(cells, provenance, config).with_columns(site_class_columns())
