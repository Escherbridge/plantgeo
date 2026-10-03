"""Canonical licence ids and the licence gate; see AGENTS.md §Licences (package-free: build_fixtures loads it)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from agri_data_service.warehouse.plant_suitability.config import RuleConfig

PUBLIC_DOMAIN = "public-domain"
US_GOVERNMENT_WORK = "us-government-work"
CC0 = "CC0-1.0"
CC_BY = "CC-BY-4.0"
CC_BY_NC = "CC-BY-NC-4.0"
CC_BY_NC_SA = "CC-BY-NC-SA-4.0"
CC_BY_ND = "CC-BY-ND-4.0"
ALL_RIGHTS_RESERVED = "all-rights-reserved"
UNRECORDED = "unrecorded"
COMMERCIAL_USE_LICENCES = frozenset({PUBLIC_DOMAIN, US_GOVERNMENT_WORK, CC0, CC_BY})
RESTRICTED_LICENCES = frozenset({CC_BY_NC, CC_BY_NC_SA, CC_BY_ND, ALL_RIGHTS_RESERVED, UNRECORDED})
KNOWN_LICENCES = COMMERCIAL_USE_LICENCES | RESTRICTED_LICENCES


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
