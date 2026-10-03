"""Site conditions at load: fixed vocabularies, caller-declared input provenance, required groups and withholding.

Rationale, the column groups, the required-group gate and the withholding rule: AGENTS.md §Site inputs here.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

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
from agri_data_service.warehouse.plant_suitability.licences import (
    CREDIT_LICENCE_TEXTS,
    DATED_ATTRIBUTION_TERMS,
    KNOWN_LICENCES,
    SourceCredit,
)
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
# Strict ISO calendar date: date.fromisoformat alone also takes "20260926" and week dates.
ISO_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
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
    """Where one site-input group came from: source, licence id, release, access date and the works it credits."""

    source: str
    licence: str
    release: str | None = None
    accessed: str | None = None
    credits: tuple[SourceCredit, ...] = ()

    def __post_init__(self) -> None:
        """Freeze the credits as a tuple, so a caller's list mutated after prepare() changes nothing served."""
        object.__setattr__(self, "credits", tuple(self.credits))

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> SiteInputSource:
        """A source from its JSON form, whose `credits` is a list of SourceCredit field mappings."""
        source_credits = tuple(SourceCredit(**credit) for credit in values.get("credits", ()))
        return cls(**{**values, "credits": source_credits})

    def credit_lines(self) -> list[str]:
        """The dated attribution its licence obliges (if any), then each credited work's line."""
        attribution = self.attribution()
        return [*([attribution] if attribution else []), *(credit.text() for credit in self.credits)]

    def accessed_date(self) -> datetime.date | None:
        """The access date, or None when undeclared; raises unless it is a calendar date written YYYY-MM-DD."""
        if self.accessed is None:
            return None
        if not ISO_DATE_PATTERN.fullmatch(self.accessed):
            message = f"site input access date {self.accessed!r} is not written YYYY-MM-DD"
            raise ValueError(message)
        return datetime.date.fromisoformat(self.accessed)

    def attribution(self) -> str | None:
        """The attribution its licence obliges every use to state, or None for a licence without dated terms."""
        terms = DATED_ATTRIBUTION_TERMS.get(self.licence)
        accessed = self.accessed_date()
        return None if terms is None or accessed is None else terms.attribution(accessed)


@dataclass(frozen=True)
class SiteInputProvenance:
    """The caller's declaration of each site-input group's source; unknown groups or licence ids raise here."""

    groups: Mapping[str, SiteInputSource]

    def __post_init__(self) -> None:
        """Freeze a copy; refuse an unknown group or licence id, or a dated-attribution licence with no access date."""
        # A copy, read-only: the caller's dict mutated after prepare() would change serving under stale metadata.
        object.__setattr__(self, "groups", MappingProxyType(dict(self.groups)))
        unknown_groups = sorted(set(self.groups) - set(SITE_INPUT_GROUPS))
        unknown_licences = sorted({source.licence for source in self.groups.values()} - KNOWN_LICENCES)
        if unknown_groups or unknown_licences:
            message = f"site input provenance names unknown groups {unknown_groups} or licence ids {unknown_licences}"
            raise ValueError(message)
        # Every preset, gated or not: the licence's own terms require the date, so no rule set may serve without it.
        accessed = {group: source.accessed_date() for group, source in sorted(self.groups.items())}
        undated = {
            group: source.licence
            for group, source in sorted(self.groups.items())
            if source.licence in DATED_ATTRIBUTION_TERMS and accessed[group] is None
        }
        if undated:
            message = (
                f"site input groups {undated} carry a licence whose attribution must state the data access date: "
                "declare accessed='YYYY-MM-DD'"
            )
            raise ValueError(message)
        assert_credits_discharge_licences(self.groups)

    def declaration_json(self) -> str:
        """Every declared group: columns, source, licence, release, access date, dated attribution, credit lines."""
        declared = {
            group: {
                "columns": list(SITE_INPUT_GROUPS[group]),
                "source": source.source,
                "licence": source.licence,
                "release": source.release,
                "accessed": source.accessed,
                "attribution": source.attribution(),
                "credits": [credit.text() for credit in source.credits],
            }
            for group, source in self.groups.items()
        }
        return canonical_json(declared)

    def attributions(self, config: RuleConfig) -> list[str]:
        """Every distinct credit line of every served group, sorted (withheld groups are not served)."""
        withheld = self.withheld_groups(config)
        served = [source for group, source in self.groups.items() if group not in withheld]
        return sorted({line for source in served for line in source.credit_lines()})

    def withheld_groups(self, config: RuleConfig) -> dict[str, str]:
        """Each group whose licence, or a credited work's, the preset does not permit, with that licence."""
        if config.permitted_licences is None:
            return {}
        withheld: dict[str, str] = {}
        for group, source in sorted(self.groups.items()):
            licences = (source.licence, *(credit.licence for credit in source.credits))
            unpermitted = [licence for licence in licences if licence not in config.permitted_licences]
            if unpermitted:
                withheld[group] = unpermitted[0]
        return withheld


def assert_credits_discharge_licences(groups: Mapping[str, SiteInputSource]) -> None:
    """Raise when a group's licence obliges a credit it does not declare, or a credit names a licence with no form."""
    uncredited = sorted(
        group for group, source in groups.items() if source.licence in CREDIT_LICENCE_TEXTS and not source.credits
    )
    no_credit_form = {
        group: sorted({credit.licence for credit in source.credits} - set(CREDIT_LICENCE_TEXTS))
        for group, source in sorted(groups.items())
        if {credit.licence for credit in source.credits} - set(CREDIT_LICENCE_TEXTS)
    }
    if uncredited or no_credit_form:
        message = (
            f"site input groups {uncredited} carry a licence that obliges a credit but declare none, or credit works "
            f"under licences with no credit form {no_credit_form}"
        )
        raise ValueError(message)


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
