"""Guide rows -> per (region, guild) candidate pools; see AGENTS.md §Pools here."""

from __future__ import annotations

import functools
import operator
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import polars as pl

from agri_data_service.warehouse.plant_suitability.names import (
    BINOMIAL_TOKEN_COUNT,
    bare_tokens,
    binomial_key,
    is_autonym,
    is_infraspecific,
    name_tokens,
    representative_rank,
)
from agri_data_service.warehouse.plant_suitability.origin import ORIGIN_SCOPES
from agri_data_service.warehouse.plant_suitability.schemas import (
    ENVELOPE_CLASSES,
    ENVELOPE_MEASURES,
    GUIDE_ROW_SCHEMA,
    GUILDS,
    SPECIES_ENVELOPE_SCHEMA,
    conform,
)
from agri_data_service.warehouse.plant_suitability.wetland import assert_wetland_genera_rated

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from agri_data_service.warehouse.plant_suitability.config import RuleConfig

MISSING_LICENCE_TEXT = "none recorded"
PAGE_NOT_AVAILABLE = "page n/a"
WOODY_GUILD = "hedgerow_buffer"
WOODY_ROLES = ["shrub", "tree"]
PLANT_ROLES = frozenset({"grass", "forb", "shrub", "tree", "other"})
EXACT_ROUTES = [
    "accepted_name", "plants_synonym", "hand_resolution",
    "plants_symbol_from_regional_list", "plants_accepted_name_from_regional_list",
]  # fmt: skip
SPECIES_ROW_FALLBACKS = ["species_row_of_named_infraspecific_taxon", "species_synonym_of_named_infraspecific_taxon"]
INFRASPECIFIC_FALLBACK = "infraspecific_rows_of_named_species"
UNSCORABLE_ROUTES = ["unmatchable_rank", "hand_resolution_unscorable", "no_plants_characteristics"]
MATCH_ROUTES = frozenset({*EXACT_ROUTES, *SPECIES_ROW_FALLBACKS, INFRASPECIFIC_FALLBACK, *UNSCORABLE_ROUTES})
# Fixed-vocabulary guide-row columns: a misspelt value would silently disable a rule, so load refuses it.
GUIDE_ROW_VOCABULARIES = {
    "guild": frozenset(GUILDS),
    "origin_scope": ORIGIN_SCOPES,
    "plant_role": PLANT_ROLES,
    "match_route": MATCH_ROUTES,
}
CONDITION_COLUMNS = ["min_precip_in", "max_precip_in", "habitat_qualifier"]
NATIVE_L48_STATUSES = frozenset({"N", "N?", "N|N?"})
GENUS_WIDE_EPITHETS = frozenset({"spp.", "species"})
# A merged species keeps its own traits; only a null trait is filled from a member, never combined across members.
FILLABLE_TRAITS = (
    *ENVELOPE_MEASURES, *ENVELOPE_CLASSES,
    "native_status_l48", "nwpl_aw", "nwpl_wmvc", "duration", "family_name", "common_name",
)  # fmt: skip
RANGE_PAIRS = (("precip_min_mm", "precip_max_mm"), ("precip_min_in", "precip_max_in"), ("ph_min", "ph_max"))
UNION_LIST_COLUMNS = ("applicability_rows", "fire_resistant_values")
KEPT_ROW_KEYS = ["named_rank", "representative_rank", "in_state_rank"]


@dataclass(frozen=True)
class RegionContext:
    """What one region's pools read: its matched guide rows, the species table, exclusions, state and rules."""

    matched: pl.DataFrame
    species: pl.DataFrame
    exclusions: pl.DataFrame
    state: str
    config: RuleConfig


@dataclass(frozen=True)
class MergedTaxon:
    """One binomial's kept pool row after the infraspecific merge, and whether a source named that kept row."""

    row: dict[str, object]
    kept_named_by_source: bool


def page_label(page: str | None) -> str:
    """'p.5' for a plain or 'pdf p.N' page, 'web page' for HTML, 'page n/a' when absent, else the text unseparated."""
    if page is None:
        return PAGE_NOT_AVAILABLE
    text = page.strip()
    if re.fullmatch(r"\d+(-\d+)?", text):
        return f"p.{text}"
    pdf_page = re.match(r"pdf pp?\.(\d+(?:-\d+)?)", text)
    if pdf_page:
        return f"p.{pdf_page.group(1)}"
    if text.startswith("web page"):
        return "web page"
    return re.sub(r"[;,|]", " ", text)


def prepare_species(species: pl.DataFrame) -> pl.DataFrame:
    """Species envelope with the name facts pools and origin read, and the perennial-grass ranking key."""
    perennial_grass = (pl.col("family_name") == "Poaceae") & (pl.col("duration").fill_null("") == "Perennial")
    return conform(species, SPECIES_ENVELOPE_SCHEMA, "species envelope").with_columns(
        pl.col("accepted_name").alias("display_name"),
        pl.col("accepted_name").map_elements(binomial_key, return_dtype=pl.String).alias("plant_binomial"),
        pl.col("accepted_name").map_elements(is_autonym, return_dtype=pl.Boolean).alias("plant_is_autonym"),
        pl.col("accepted_name").map_elements(is_infraspecific, return_dtype=pl.Boolean).alias("plant_is_infraspecific"),
        perennial_grass.cast(pl.Int8).alias("is_perennial_grass"),
    )


def licence_key(licence: str) -> str:
    """A licence text as the gate compares it: trimmed and case-folded, never substring-matched."""
    return licence.strip().casefold()


def licence_gate(rows: pl.DataFrame, config: RuleConfig) -> tuple[pl.DataFrame, dict[str, str]]:
    """Rows whose licence the preset permits, and each dropped source_id with its licence text (fails closed)."""
    if config.permitted_licences is None:
        return rows, {}
    permitted = {licence_key(licence) for licence in config.permitted_licences}
    texts = rows["license"].drop_nulls().unique().to_list()
    admitted_texts = [text for text in texts if licence_key(text) in permitted]
    admitted = pl.col("license").is_in(admitted_texts).fill_null(value=False)
    dropped = rows.filter(~admitted).group_by("source_id").agg(pl.col("license").unique())
    excluded = {
        source_id: "; ".join(sorted({licence or MISSING_LICENCE_TEXT for licence in licences}))
        for source_id, licences in dropped.sort("source_id").iter_rows()
    }
    return rows.filter(admitted), excluded


def load_guide_rows(guide_rows: pl.DataFrame, config: RuleConfig) -> tuple[pl.DataFrame, dict[str, str]]:
    """Conformed guide rows the licence gate admits, and the sources it dropped; bad vocabulary or alignment raises."""
    rows = conform(guide_rows, GUIDE_ROW_SCHEMA, "guide rows")
    for column, vocabulary in GUIDE_ROW_VOCABULARIES.items():
        unknown = set(rows[column].unique().to_list()) - vocabulary
        if unknown:
            message = f"guide rows carry unknown {column} values {sorted(unknown, key=str)}"
            raise ValueError(message)
    misaligned = rows.filter(pl.col("applies_to_regions").list.len() != pl.col("in_region").list.len())
    if misaligned.height:
        message = f"in_region and applies_to_regions differ in length on rows {misaligned['guide_row_id'].to_list()}"
        raise ValueError(message)
    return licence_gate(rows, config)


def region_rows(rows: pl.DataFrame, config: RuleConfig) -> pl.DataFrame:
    """One row per (guide row, region) with its in-region flag (after the configured overrides) and label tag."""
    overrides = [
        (pl.col("source_id") == source_id) & (pl.col("region") == region)
        for source_id, region in sorted(config.in_region_overrides)
    ]
    overridden = functools.reduce(operator.or_, overrides, pl.lit(value=False))
    medium_confidence = pl.col("applicability_confidence") == "medium"
    medium_suffix = pl.when(medium_confidence).then(pl.lit(" [medium]")).otherwise(pl.lit(""))
    suffix = pl.when(pl.col("in_region")).then(medium_suffix).otherwise(pl.lit(" [neighbour]"))
    page = pl.col("page").map_elements(page_label, return_dtype=pl.String, skip_nulls=False)
    listed_name = pl.coalesce(pl.col("listed_scientific_name"), pl.format("'{}'", "listed_common_name"))
    return (
        rows.explode(["applies_to_regions", "in_region"], empty_as_null=True)
        .rename({"applies_to_regions": "region"})
        .drop_nulls("region")
        .with_columns(pl.col("in_region") | overridden)
        .with_columns(
            pl.format("{} {}{}", "source_short_name", page, suffix).alias("source_tag"),
            listed_name.alias("listed_name"),
            pl.lit(None, dtype=pl.String).alias("stratified_from"),
        )
    )


def matched_rows(rows: pl.DataFrame, species: pl.DataFrame) -> pl.DataFrame:
    """One row per (guide row, matched PLANTS taxon) with the taxon's name facts."""
    facts = species.select(
        "plant_id", "display_name", "plant_binomial", "plant_is_autonym", "plant_is_infraspecific", "recorded_states"
    )
    return (
        rows.filter(pl.col("matched_plant_ids").list.len() > 0)
        .explode("matched_plant_ids", empty_as_null=True)
        .rename({"matched_plant_ids": "plant_id"})
        .join(facts, on="plant_id", how="inner")
    )


def exclusion_rule(row: dict[str, Any]) -> tuple[str, str | None, bool]:
    """(rule, key, non-native only) for one noxious-list row: genus, hybrid formula or binomial."""
    name = row["scientific_name"]
    tokens = name_tokens(name)
    listed_text = f"{row['listed_name']} {row['common_name']} {row['name_ambiguity_flag'] or ''}".lower()
    non_native_only = "nonnative" in listed_text or "non-native" in listed_text
    genus_wide = len(tokens) == BINOMIAL_TOKEN_COUNT and tokens[1] in GENUS_WIDE_EPITHETS
    if genus_wide or "subgenus" in tokens:
        return "genus", tokens[0], non_native_only
    if "x" in tokens[BINOMIAL_TOKEN_COUNT:]:
        return "hybrid_formula", " ".join(bare_tokens(name)), False
    return "binomial", binomial_key(name), False


def excluded_plant_ids(exclusions: pl.DataFrame, taxa: pl.DataFrame, state: str) -> set[int]:
    """Pool taxa a state noxious-list row matches by binomial, genus (non-native only when listed so) or formula."""
    rules = [exclusion_rule(row) for row in exclusions.filter(pl.col("state") == state).iter_rows(named=True)]
    names = [
        (row["plant_id"], name, (row["native_status_l48"] or "") in NATIVE_L48_STATUSES)
        for row in taxa.select("plant_id", "accepted_name", "synonym_names", "native_status_l48").iter_rows(named=True)
        for name in [row["accepted_name"], *(row["synonym_names"] or [])]
        if name
    ]
    keyed = [
        (plant_id, binomial_key(name), name_tokens(name)[0], " ".join(bare_tokens(name)), native)
        for plant_id, name, native in names
    ]
    return {
        plant_id
        for rule, key, non_native_only in rules
        if key is not None
        for plant_id, binomial, genus, formula, native in keyed
        if (rule == "genus" and genus == key and not (non_native_only and native))
        or (rule == "hybrid_formula" and formula == key)
        or (rule == "binomial" and binomial == key)
    }


def stratification_by_binomial(matched: pl.DataFrame) -> pl.DataFrame:
    """Per binomial: every distinct band / habitat condition its stratified guide rows carry, and their tags."""
    stratified = pl.any_horizontal([pl.col(column).is_not_null() for column in CONDITION_COLUMNS])
    return (
        matched.filter(stratified)
        .group_by("plant_binomial", *CONDITION_COLUMNS)
        .agg(pl.col("source_tag").unique().sort().str.join(" + ").alias("stratified_from"))
    )


def inherit_stratification(pool_rows: pl.DataFrame, stratification: pl.DataFrame) -> pl.DataFrame:
    """In-region rows with no band or qualifier take the conditions of the taxon's stratified rows (open issue 1)."""
    unstratified = pl.all_horizontal([pl.col(column).is_null() for column in CONDITION_COLUMNS])
    has_conditions = pl.col("plant_binomial").is_in(stratification["plant_binomial"].unique().to_list())
    inheriting = (pl.col("in_region") & unstratified & has_conditions).fill_null(value=False)
    replaced = (
        pool_rows.filter(inheriting)
        .drop(*CONDITION_COLUMNS, "stratified_from")
        .join(stratification, on="plant_binomial", how="inner")
    )
    return pl.concat([pool_rows.filter(~inheriting), replaced], how="diagonal_relaxed")


def pool_support(context: RegionContext, guild: str) -> pl.DataFrame:
    """Per PLANTS taxon named for the guild in the region: source support, noxious flag and applicability rows."""
    pool_rows = context.matched.filter(pl.col("guild") == guild)
    if guild == WOODY_GUILD:
        pool_rows = pool_rows.filter(pl.col("plant_role").is_in(WOODY_ROLES))
    present = pl.col("recorded_states").list.contains(context.state).fill_null(value=False)
    listed_species_rank = ~pl.col("listed_scientific_name").map_elements(is_infraspecific, return_dtype=pl.Boolean)
    listed_autonym = pl.col("listed_scientific_name").map_elements(is_autonym, return_dtype=pl.Boolean)
    route = pl.col("match_route")
    named_by_row = (
        route.is_in(EXACT_ROUTES)
        | ((route == INFRASPECIFIC_FALLBACK) & pl.col("plant_is_autonym"))
        | (route.is_in(SPECIES_ROW_FALLBACKS) & listed_autonym)
    )
    # A species-rank listing reaching an infraspecific taxon keeps it only when PLANTS records it in the state.
    out_of_state_rank_change = listed_species_rank & pl.col("plant_is_infraspecific") & ~pl.col("present_in_state")
    pool_rows = (
        pool_rows.with_columns(present.alias("present_in_state"))
        .filter(~out_of_state_rank_change)
        .with_columns(
            named_by_row.alias("named_by_row"),
            pl.col("noxious_states").list.contains(context.state).fill_null(value=False).alias("noxious_row"),
        )
    )
    if context.config.inherit_stratification:
        pool_rows = inherit_stratification(pool_rows, stratification_by_binomial(context.matched))
    condition = pl.struct(*CONDITION_COLUMNS, "in_region", "source_tag", "listed_name", "stratified_from")
    return pool_rows.group_by("plant_id", maintain_order=True).agg(
        pl.col("named_by_row").any().alias("named_by_source"),
        pl.col("noxious_row").any().alias("noxious_in_state"),
        condition.unique(maintain_order=True).alias("applicability_rows"),
        pl.col("present_in_state").first(),
    )


def union_in_order(lists: Iterable[object]) -> list[object]:
    """Every distinct value across the lists in first-seen order (struct values compared by their items)."""
    seen: set[object] = set()
    result: list[object] = []
    for values in lists:
        for value in values if isinstance(values, list) else []:
            key = tuple(sorted(value.items())) if isinstance(value, dict) else value
            if key not in seen:
                seen.add(key)
                result.append(value)
    return result


def merge_members(members: Sequence[Mapping[str, object]]) -> MergedTaxon:
    """The kept member with null traits filled from the others in kept-row order; inverted fills are rejected."""
    kept, others = dict(members[0]), members[1:]
    filled: set[str] = set()
    for column in FILLABLE_TRAITS:
        donor = next((member for member in others if member.get(column) is not None), None)
        if kept.get(column) is None and donor is not None:
            kept[column] = donor[column]
            filled.add(column)
    for minimum_column, maximum_column in RANGE_PAIRS:
        minimum, maximum = kept.get(minimum_column), kept.get(maximum_column)
        if isinstance(minimum, int | float) and isinstance(maximum, int | float) and minimum > maximum:
            kept.update({column: None for column in (minimum_column, maximum_column) if column in filled})
    kept.update({column: union_in_order([member.get(column) for member in members]) for column in UNION_LIST_COLUMNS})
    return MergedTaxon(row=kept, kept_named_by_source=bool(members[0].get("named_by_source")))


def collapse_infraspecific(pool: pl.DataFrame) -> pl.DataFrame:
    """One row per binomial; the kept row is named-by-source, then species/autonym, then in-state, never by name."""
    keyed = pool.with_columns(
        (~pl.col("named_by_source").fill_null(value=False)).cast(pl.Int8).alias("named_rank"),
        pl.col("display_name").map_elements(representative_rank, return_dtype=pl.Int8).alias("representative_rank"),
        (~pl.col("present_in_state").fill_null(value=False)).cast(pl.Int8).alias("in_state_rank"),
    ).sort("plant_binomial", *KEPT_ROW_KEYS, maintain_order=True)
    groups = [group for _, group in keyed.group_by("plant_binomial", maintain_order=True)]
    ties = [
        " / ".join(group["display_name"].head(2).to_list())
        for group in groups
        if group.height > 1 and group.select(KEPT_ROW_KEYS).row(0) == group.select(KEPT_ROW_KEYS).row(1)
    ]
    if ties:
        message = f"kept row undecidable without an alphabetical tiebreak: {ties}"
        raise ValueError(message)
    merged = [merge_members(group.drop(KEPT_ROW_KEYS).to_dicts()) for group in groups]
    records = [{**taxon.row, "kept_named_by_source": taxon.kept_named_by_source} for taxon in merged]
    collapsed = pl.DataFrame(records, schema={**pool.schema, "kept_named_by_source": pl.Boolean}).sort("display_name")
    inverted = collapsed.filter(pl.any_horizontal([pl.col(low) > pl.col(high) for low, high in RANGE_PAIRS]))
    unsupported = collapsed.filter(~pl.col("kept_named_by_source") & ~pl.col("present_in_state"))
    if inverted.height or unsupported.height:
        message = (
            f"merged pool is inconsistent: inverted ranges {inverted['display_name'].to_list()}, kept taxa neither "
            f"named by a source nor recorded in the state {unsupported['display_name'].to_list()}"
        )
        raise ValueError(message)
    return collapsed


def build_pool(context: RegionContext, guild: str) -> pl.DataFrame:
    """Regional-guide taxa for one guild, minus state noxious listings, one row per binomial."""
    pool = pool_support(context, guild).join(context.species, on="plant_id", how="inner")
    excluded = excluded_plant_ids(context.exclusions, pool, context.state)
    pool = pool.filter(~(pl.col("plant_id").is_in(sorted(excluded)) | pl.col("noxious_in_state")))
    collapsed = collapse_infraspecific(pool)
    assert_wetland_genera_rated(collapsed)
    return collapsed
