"""Canonical licence ids, their attribution terms and the licence gate; see AGENTS.md §Licences (package-free)."""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.warehouse.plant_suitability.config import RuleConfig

PUBLIC_DOMAIN = "public-domain"
US_GOVERNMENT_WORK = "us-government-work"
CC0 = "CC0-1.0"
CC_BY = "CC-BY-4.0"
# Not an SPDX id: PRISM's own terms (https://prism.oregonstate.edu/terms/, fetched 2026-10-03), see AGENTS.md §Licences.
PRISM_TERMS_OF_USE = "PRISM-terms-of-use"
# Not an SPDX id: the ESA licence for Copernicus DEM GLO-30/GLO-90, free with a fixed notice; see AGENTS.md §Licences.
COPERNICUS_DEM = "Copernicus-DEM-licence"
CC_BY_NC = "CC-BY-NC-4.0"
CC_BY_NC_SA = "CC-BY-NC-SA-4.0"
CC_BY_ND = "CC-BY-ND-4.0"
ALL_RIGHTS_RESERVED = "all-rights-reserved"
UNRECORDED = "unrecorded"
COMMERCIAL_USE_LICENCES = frozenset({PUBLIC_DOMAIN, US_GOVERNMENT_WORK, CC0, CC_BY, PRISM_TERMS_OF_USE, COPERNICUS_DEM})
RESTRICTED_LICENCES = frozenset({CC_BY_NC, CC_BY_NC_SA, CC_BY_ND, ALL_RIGHTS_RESERVED, UNRECORDED})
KNOWN_LICENCES = COMMERCIAL_USE_LICENCES | RESTRICTED_LICENCES
# Strict ISO calendar date: date.fromisoformat alone also takes "20260926" and week dates.
ISO_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
MONTH_ABBREVIATIONS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def iso_calendar_date(text: str) -> datetime.date | None:
    """The date `text` writes as YYYY-MM-DD, or None when it is written any other way (raises on a non-date)."""
    return datetime.date.fromisoformat(text) if ISO_DATE_PATTERN.fullmatch(text) else None


@dataclass(frozen=True)
class DatedAttributionTerms:
    """A licence whose every use must state the holder's name, URL and the date the data were accessed."""

    holder: str
    url: str
    terms_url: str
    terms_fetched: str

    def attribution(self, accessed: datetime.date) -> str:
        """The attribution in the holder's own form, e.g. 'PRISM Group, ..., accessed 16 Dec 2025.' (locale-free)."""
        month = MONTH_ABBREVIATIONS[accessed.month - 1]
        return f"{self.holder}, {self.url}, accessed {accessed.day} {month} {accessed.year}."


# Licences whose obligations need an access date; only a declaration that carries one may use them.
DATED_ATTRIBUTION_TERMS: Mapping[str, DatedAttributionTerms] = MappingProxyType(
    {
        PRISM_TERMS_OF_USE: DatedAttributionTerms(
            holder="PRISM Group, Oregon State University",
            url="https://prism.oregonstate.edu",
            terms_url="https://prism.oregonstate.edu/terms/",
            terms_fetched="2026-10-03",
        ),
    }
)
# Licences that oblige a credit naming the work, its holder and the licence, as each credit line states it.
CREDIT_LICENCE_TEXTS: Mapping[str, str] = MappingProxyType(
    {CC_BY: "CC BY 4.0, https://creativecommons.org/licenses/by/4.0/"}
)
# Licences that prescribe the credit line verbatim. Copernicus DEM: the notice for adapted or modified GLO-90 data,
# copied from the Copernicus Data Space COP-DEM page (fetched 2026-10-05; AGENTS.md §Licences).
CREDIT_NOTICES: Mapping[str, str] = MappingProxyType(
    {
        COPERNICUS_DEM: (
            "produced using Copernicus WorldDEM-90 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH "
            "2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved"
        ),
    }
)
CREDIT_FORM_LICENCES = frozenset(CREDIT_LICENCE_TEXTS) | frozenset(CREDIT_NOTICES)
# Guide rows carry no access date and no credit line, so those licences are refused there rather than served uncredited.
GUIDE_ROW_LICENCES = KNOWN_LICENCES - frozenset(DATED_ATTRIBUTION_TERMS) - frozenset(CREDIT_NOTICES)


@dataclass(frozen=True)
class SourceCredit:
    """One licensed work a site-input group draws on, credited title-holder-source-licence (the CC BY TASL form)."""

    work: str
    holder: str
    url: str
    licence: str

    def text(self) -> str:
        """The licence's prescribed notice verbatim, else work, holder, URL, then 'under <licence and deed URL>'."""
        notice = CREDIT_NOTICES.get(self.licence)
        if notice is not None:
            return notice
        return f"{self.work}, {self.holder}, {self.url}, under {CREDIT_LICENCE_TEXTS[self.licence]}"


# (key, value) pairs that must be one-to-one: labels and origin votes cite the short name, the gate reads the id.
SOURCE_IDENTITY_PAIRS = (("source_id", "license"), ("source_short_name", "license"), ("source_id", "source_short_name"))


def assert_one_licence_per_source(rows: pl.DataFrame) -> None:
    """Raise when a source_id or citation short name carries two licence ids, or a source_id two short names."""
    mixed: dict[str, dict[str, list[str]]] = {}
    for key, value in SOURCE_IDENTITY_PAIRS:
        spans = rows.group_by(key).agg(pl.col(value).unique().sort()).filter(pl.col(value).list.len() > 1)
        if spans.height:
            mixed[f"{key} -> {value}"] = dict(spans.sort(key).iter_rows())
    if mixed:
        message = f"guide sources carry more than one licence or short name: {mixed}"
        raise ValueError(message)


def licence_gate(rows: pl.DataFrame, config: RuleConfig) -> tuple[pl.DataFrame, dict[str, str]]:
    """Rows whose licence id the preset permits, and each dropped source_id with its licence id (fails closed)."""
    if config.permitted_licences is None:
        return rows, {}
    admitted = pl.col("license").is_in(sorted(config.permitted_licences))
    dropped = rows.filter(~admitted).select("source_id", "license").unique().sort("source_id")
    return rows.filter(admitted), dict(dropped.iter_rows())
