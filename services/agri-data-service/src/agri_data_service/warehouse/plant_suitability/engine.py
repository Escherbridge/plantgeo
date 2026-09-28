"""One rule engine for the batch cell build and the per-point tool; see AGENTS.md §Engine here."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from agri_data_service.warehouse.plant_suitability.applicability import applicability, supporting_rows
from agri_data_service.warehouse.plant_suitability.axes import AXIS_NAMES, evaluate_pairs, site_class_columns
from agri_data_service.warehouse.plant_suitability.labels import (
    NO_FIRE_CLAIM,
    assert_no_fire_claims,
    assert_no_fire_field_on_woody_buffer,
    fire_resistance_label,
    pick_label_expression,
)
from agri_data_service.warehouse.plant_suitability.origin import (
    INTRODUCED_PLACEHOLDER,
    decide_origins,
    document_statements,
)
from agri_data_service.warehouse.plant_suitability.pools import (
    RegionContext,
    build_pool,
    load_guide_rows,
    matched_rows,
    prepare_species,
    region_rows,
)
from agri_data_service.warehouse.plant_suitability.ranking import TOP_PICK_COUNT, ranked_picks
from agri_data_service.warehouse.plant_suitability.schemas import (
    CELL_RECOMMENDATIONS_SCHEMA,
    EXCLUSION_SCHEMA,
    GUILDS,
    SITE_CONDITIONS_SCHEMA,
    conform,
    polars_schema,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pyarrow as pa  # type: ignore[import-untyped]

    from agri_data_service.warehouse.plant_suitability.config import RuleConfig

STATUS_SCORED = "scored"
STATUS_NO_REGIONAL_GUIDE = "no_regional_guide"
COUNT_SUFFIXES = (
    "count", "count_fully_known", "count_in_region", "count_uncertain_frost_free", "count_introduced_flagged",
    "rank1_tie_count",
)  # fmt: skip
TOP_THREE_SUFFIXES = (
    ("top3", pl.String), ("top3_common_names", pl.String), ("top3_labels", pl.List(pl.String)),
    ("unknown_axes_top3", pl.String), ("rank1_tied_names", pl.String),
)  # fmt: skip
# Candidate columns the axes, ranking and labels read (the rest of the envelope stays out of the cross join).
CANDIDATE_COLUMNS = [
    "plant_id", "display_name", "common_name", "min_temp_c", "precip_min_mm", "precip_max_mm", "ph_min", "ph_max",
    "frost_free_days_min", "texture_coarse", "texture_medium", "texture_fine", "root_depth_min_cm",
    "salinity_tolerance", "caco3_tolerance", "anaerobic_tolerance", "height_mature_ft", "is_perennial_grass",
    "fire_tolerance", "seedling_vigor", "drought_tolerance", "commercial_availability", "nwpl_aw", "nwpl_wmvc",
    "nwpl_route", "introduced_flag", "introduced_flag_rank", "origin_category", "origin_label",
    "fire_resistance_label", "applicability_rows",
]  # fmt: skip
# The per-point frame: one row per (guild, pool taxon), or one status row for a guild with no candidates at the cell.
CANDIDATE_OUTPUT_SCHEMA = pl.Schema(
    {
        "guild": pl.String, "status": pl.String, "scoring_null_reason": pl.String, "rank": pl.UInt32,
        "plant_id": pl.Int64, "display_name": pl.String, "common_name": pl.String, "is_pick": pl.Boolean,
        "pick_label": pl.String, "origin_label": pl.String, "introduced_flag": pl.Boolean,
        "in_region_supported": pl.Boolean, "frost_free_uncertain": pl.Boolean, "failed_axis_count": pl.UInt32,
        "unknown_axis_count": pl.UInt32, "robustness_margin": pl.Float64, "binding_axis": pl.String,
        **{f"axis_{name}": pl.Int8 for name in AXIS_NAMES},
    }
)  # fmt: skip


@dataclass(frozen=True)
class PreparedInputs:
    """Conformed species, per-region guide rows, per-document origin statements, exclusions and licence drops."""

    species: pl.DataFrame
    region_rows: pl.DataFrame
    documents: pl.DataFrame
    exclusions: pl.DataFrame
    excluded_sources: dict[str, str]


def prepare_inputs(
    species: pl.DataFrame, guide_rows: pl.DataFrame, exclusions: pl.DataFrame, config: RuleConfig
) -> PreparedInputs:
    """Validate the three reference tables once; a guide row naming a taxon missing from the species table raises."""
    prepared_species = prepare_species(species)
    rows, excluded_sources = load_guide_rows(guide_rows, config)
    named_ids = set(rows.select(pl.col("matched_plant_ids").explode(empty_as_null=True)).to_series().drop_nulls())
    missing = named_ids - set(prepared_species["plant_id"].to_list())
    if missing:
        message = f"guide rows name PLANTS taxa missing from the species envelope: {sorted(missing)[:10]}"
        raise ValueError(message)
    return PreparedInputs(
        species=prepared_species,
        region_rows=region_rows(rows, config),
        documents=document_statements(matched_rows(rows, prepared_species), config),
        exclusions=conform(exclusions, EXCLUSION_SCHEMA, "exclusions"),
        excluded_sources=excluded_sources,
    )


def prepare_site(site: pl.DataFrame) -> pl.DataFrame:
    """Conformed site conditions plus the wetness class and drainage regime derived from SSURGO drainage/hydric."""
    cells = conform(site, SITE_CONDITIONS_SCHEMA, "site conditions").with_columns(site_class_columns())
    duplicated = cells.filter(pl.col("cell_id").is_duplicated())["cell_id"].unique().to_list()
    if duplicated:
        message = f"site conditions repeat cell ids {duplicated[:10]}"
        raise ValueError(message)
    return cells


def label_candidates(pool: pl.DataFrame, origins: pl.DataFrame, guild: str, config: RuleConfig) -> pl.DataFrame:
    """Pool taxa with their region-wide origin, the guild's introduced-flag wording and the fire-resistance label."""
    labelled = pool.join(origins, on="plant_id", how="left")
    undecided = labelled.filter(pl.col("origin_label").is_null())["display_name"].to_list()
    if undecided:
        message = f"pool taxa without an origin decision: {undecided}"
        raise ValueError(message)
    fire_labels = [fire_resistance_label(values) for values in labelled["fire_resistant_values"].to_list()]
    flag_text = config.introduced_flag_text[guild]
    return labelled.with_columns(
        pl.Series("fire_resistance_label", fire_labels, dtype=pl.String),
        pl.col("origin_label").str.replace(INTRODUCED_PLACEHOLDER, flag_text, literal=True),
        pl.col("introduced_flag").cast(pl.Int8).alias("introduced_flag_rank"),
    ).select(CANDIDATE_COLUMNS)


def region_candidates(prepared: PreparedInputs, region: str, state: str, config: RuleConfig) -> dict[str, pl.DataFrame]:
    """Per guild: the region's labelled candidate pool (origin decided once across all three guilds)."""
    region_matched = matched_rows(prepared.region_rows.filter(pl.col("region") == region), prepared.species)
    context = RegionContext(region_matched, prepared.species, prepared.exclusions, state, config)
    pools = {guild: build_pool(context, guild) for guild in GUILDS}
    kept_taxa = pl.concat(
        [pool.select("plant_id", "plant_binomial", "native_status_l48", "present_in_state") for pool in pools.values()]
    )
    origins = decide_origins(kept_taxa, region_matched, prepared.documents, state)
    return {guild: label_candidates(pool, origins, guild, config) for guild, pool in pools.items()}


def evaluate_guild(cells: pl.DataFrame, candidates: pl.DataFrame, config: RuleConfig) -> pl.DataFrame:
    """Every (cell, candidate) pair with its axes, counts, margin and rank helpers."""
    return evaluate_pairs(cells, candidates, applicability(cells, candidates), config)


def with_pick_labels(picks: pl.DataFrame, cells: pl.DataFrame, candidates: pl.DataFrame, guild: str) -> pl.DataFrame:
    """Picks with the label naming the guide rows that carry each one at its cell."""
    support = supporting_rows(picks.select("cell_id", "plant_id", "axis_source_applicability"), cells, candidates)
    return picks.join(support, on=["cell_id", "plant_id"], how="left").with_columns(
        pick_label_expression(guild).alias("pick_label")
    )


def no_guide_cells(cell_ids: pl.DataFrame, guild: str) -> pl.DataFrame:
    """No regional guide names taxa for this guild here: status says so and every count is null, not zero."""
    return cell_ids.select(
        "cell_id",
        pl.lit(STATUS_NO_REGIONAL_GUIDE).alias(f"{guild}_status"),
        pl.lit(0, dtype=pl.UInt32).alias(f"{guild}_pool_size"),
        *[pl.lit(None, dtype=pl.UInt32).alias(f"{guild}_{suffix}") for suffix in COUNT_SUFFIXES],
        *[pl.lit(None, dtype=dtype).alias(f"{guild}_{suffix}") for suffix, dtype in TOP_THREE_SUFFIXES],
    )


def guild_cells(scored: pl.DataFrame, candidates: pl.DataFrame, guild: str, config: RuleConfig) -> pl.DataFrame:
    """Per scored cell: status, pool size, pick counts, top-3 names / common names / labels and rank-1 ties."""
    cell_ids = scored.select("cell_id")
    if candidates.height == 0:
        return no_guide_cells(cell_ids, guild)
    picks = ranked_picks(evaluate_guild(scored, candidates, config), guild)
    per_cell = picks.group_by("cell_id", maintain_order=True).agg(
        pl.len().alias(f"{guild}_count"),
        (pl.col("unknown_axis_count") == 0).sum().alias(f"{guild}_count_fully_known"),
        pl.col("in_region_supported").sum().alias(f"{guild}_count_in_region"),
        pl.col("frost_free_uncertain").sum().alias(f"{guild}_count_uncertain_frost_free"),
        pl.col("introduced_flag").sum().alias(f"{guild}_count_introduced_flagged"),
        pl.col("ties_rank1").sum().alias(f"{guild}_rank1_tie_count"),
        pl.col("display_name").filter(pl.col("ties_rank1")).str.join(";").alias(f"{guild}_rank1_tied_names"),
    )
    top = with_pick_labels(picks.filter(pl.col("rank_index") < TOP_PICK_COUNT), scored, candidates, guild)
    top_lists = (
        top.sort("cell_id", "rank_index")
        .group_by("cell_id", maintain_order=True)
        .agg(
            pl.col("display_name").str.join(";").alias(f"{guild}_top3"),
            pl.col("common_name").fill_null("?").str.join(";").alias(f"{guild}_top3_common_names"),
            pl.col("pick_label").alias(f"{guild}_top3_labels"),
            pl.col("unknown_axis_count").cast(pl.String).str.join(";").alias(f"{guild}_unknown_axes_top3"),
        )
    )
    counts = [f"{guild}_{suffix}" for suffix in COUNT_SUFFIXES]
    return (
        cell_ids.join(per_cell, on="cell_id", how="left")
        .join(top_lists, on="cell_id", how="left")
        .with_columns(
            pl.lit(STATUS_SCORED).alias(f"{guild}_status"),
            pl.lit(candidates.height, dtype=pl.UInt32).alias(f"{guild}_pool_size"),
            *[pl.col(column).fill_null(0).cast(pl.UInt32) for column in counts],
        )
    )


def region_table(cells: pl.DataFrame, prepared: PreparedInputs, config: RuleConfig) -> pl.DataFrame:
    """One region's cells with every guild's columns; withheld cells keep every guild column null."""
    region, states = cells["region"][0], cells["state"].unique().to_list()
    if len(states) != 1:
        message = f"region {region} spans states {states}; pools are decided per (region, state)"
        raise ValueError(message)
    candidates = region_candidates(prepared, region, states[0], config)
    scored = cells.filter(pl.col("scoring_null_reason").is_null())
    table = cells.select("cell_id", "region", "scoring_null_reason")
    for guild in GUILDS:
        table = table.join(guild_cells(scored, candidates[guild], guild, config), on="cell_id", how="left")
    return table


def guard_served_table(
    table: pl.DataFrame, labels_by_guild: Mapping[str, list[str | None]], metadata: Mapping[str, str]
) -> None:
    """Refuse a table whose text or metadata claims a fire effect, or whose woody-buffer labels mention fire."""
    assert_no_fire_claims(table, metadata)
    assert_no_fire_field_on_woody_buffer(labels_by_guild)


def evaluate_cells(
    site: pl.DataFrame,
    species: pl.DataFrame,
    guide_rows: pl.DataFrame,
    exclusions: pl.DataFrame,
    config: RuleConfig,
) -> pl.DataFrame:
    """One row per input cell (input order): per-guild status, pool size, pick counts and the labelled top three."""
    prepared = prepare_inputs(species, guide_rows, exclusions, config)
    cells = prepare_site(site)
    regions = cells["region"].unique(maintain_order=True).to_list()
    tables = [region_table(cells.filter(pl.col("region") == region), prepared, config) for region in regions]
    if tables:
        combined = pl.concat(tables, how="diagonal_relaxed")
        ordered = cells.select("cell_id").join(combined, on="cell_id", how="left", maintain_order="left")
        served = conform(ordered, CELL_RECOMMENDATIONS_SCHEMA, "cell recommendations")
    else:
        served = pl.DataFrame(schema=polars_schema(CELL_RECOMMENDATIONS_SCHEMA))
    labels_by_guild = {
        guild: served.select(pl.col(f"{guild}_top3_labels").explode(empty_as_null=True)).to_series().to_list()
        for guild in GUILDS
    }
    guard_served_table(served, labels_by_guild, layer_metadata(config, prepared.excluded_sources))
    return served


def conform_candidates(frame: pl.DataFrame) -> pl.DataFrame:
    """The per-point frame's columns in order, cast to CANDIDATE_OUTPUT_SCHEMA."""
    return frame.select([pl.col(name).cast(dtype) for name, dtype in CANDIDATE_OUTPUT_SCHEMA.items()])


def ranked_candidates(cell: pl.DataFrame, candidates: pl.DataFrame, guild: str, config: RuleConfig) -> pl.DataFrame:
    """Every pool taxon at one scored cell: picks in rank order with labels, then non-picks by name."""
    evaluated = evaluate_guild(cell, candidates, config)
    picks = with_pick_labels(ranked_picks(evaluated, guild), cell, candidates, guild)
    ranks = picks.select("plant_id", (pl.col("rank_index") + 1).alias("rank"), "pick_label")
    ranked = (
        evaluated.join(ranks, on="plant_id", how="left")
        .sort("rank", "display_name", nulls_last=True)
        .with_columns(
            pl.lit(guild).alias("guild"),
            pl.lit(STATUS_SCORED).alias("status"),
            pl.lit(None, dtype=pl.String).alias("scoring_null_reason"),
        )
    )
    return conform_candidates(ranked)


def guild_status_row(guild: str, status: str | None, scoring_null_reason: str | None) -> pl.DataFrame:
    """A guild with no candidates at the cell: the batch's status and withheld reason, every taxon column null."""
    row = dict.fromkeys(CANDIDATE_OUTPUT_SCHEMA.names()) | {
        "guild": guild,
        "status": status,
        "scoring_null_reason": scoring_null_reason,
    }
    return pl.DataFrame([row], schema=CANDIDATE_OUTPUT_SCHEMA)


def guild_candidates_at_cell(cell: pl.DataFrame, pool: pl.DataFrame, guild: str, config: RuleConfig) -> pl.DataFrame:
    """One guild at one cell: its ranked pool, or a status row when the cell is withheld or no guide names taxa."""
    scoring_null_reason = cell["scoring_null_reason"][0]
    if scoring_null_reason is not None:
        return guild_status_row(guild, None, scoring_null_reason)
    if pool.height == 0:
        return guild_status_row(guild, STATUS_NO_REGIONAL_GUIDE, None)
    return ranked_candidates(cell, pool, guild, config)


def candidates_for_cell(
    cell_site: pl.DataFrame,
    species: pl.DataFrame,
    guide_rows: pl.DataFrame,
    exclusions: pl.DataFrame,
    config: RuleConfig,
) -> pl.DataFrame:
    """Per guild, every pool taxon ranked at one cell through the batch's path, or one row with the batch's status."""
    cell = prepare_site(cell_site)
    if cell.height != 1:
        message = f"candidates_for_cell takes exactly one site row, got {cell.height}"
        raise ValueError(message)
    prepared = prepare_inputs(species, guide_rows, exclusions, config)
    candidates = region_candidates(prepared, cell["region"][0], cell["state"][0], config)
    ranked = pl.concat(
        [guild_candidates_at_cell(cell, pool, guild, config) for guild, pool in candidates.items()], how="vertical"
    )
    labels_by_guild = {guild: ranked.filter(pl.col("guild") == guild)["pick_label"].to_list() for guild in GUILDS}
    guard_served_table(ranked, labels_by_guild, layer_metadata(config, prepared.excluded_sources))
    return ranked


def licence_excluded_sources(guide_rows: pl.DataFrame, config: RuleConfig) -> dict[str, str]:
    """Each guide source_id the preset's licence gate drops, with its licence text (empty when there is no gate)."""
    return load_guide_rows(guide_rows, config)[1]


def layer_metadata(config: RuleConfig, excluded_sources: Mapping[str, str]) -> dict[str, str]:
    """Metadata every served table carries: rules, pick definition, no-fire-claim note and licence-dropped sources."""
    return {
        "plantgeo:rule_config": config.name,
        "plantgeo:pick_definition": config.pick_definition,
        "plantgeo:no_fire_claim": NO_FIRE_CLAIM,
        "plantgeo:excluded_sources": json.dumps(dict(sorted(excluded_sources.items())), ensure_ascii=False),
    }


def with_layer_metadata(cells: pl.DataFrame, config: RuleConfig, excluded_sources: Mapping[str, str]) -> pa.Table:
    """The cell table as Arrow with `layer_metadata` attached to its schema (what a parquet writer persists)."""
    return cells.to_arrow().replace_schema_metadata(layer_metadata(config, excluded_sources))
