"""Ranking keys and the per-cell pick order; see AGENTS.md §Ranking here."""

from __future__ import annotations

import polars as pl

TOP_PICK_COUNT = 3
# (column, descending); nulls sort last. In-region leads frost-free certainty by owner decision.
LEADING_KEYS = (
    ("in_region_rank", False),
    ("frost_free_uncertain_rank", False),
    ("unknown_axis_count", False),
    ("introduced_flag_rank", False),
)
# Then each guild's establishment keys (fire tolerance is PLANTS' regrowth-after-fire trait, not a fire effect).
RANKING_KEYS = {
    "greenstrip": (
        *LEADING_KEYS, ("commercial_availability", True), ("fire_tolerance", True), ("seedling_vigor", True),
        ("drought_tolerance", True), ("height_mature_ft", False), ("robustness_margin", True), ("display_name", False),
    ),
    "post_fire_restoration": (
        *LEADING_KEYS, ("is_perennial_grass", True), ("robustness_margin", True), ("commercial_availability", True),
        ("seedling_vigor", True), ("fire_tolerance", True), ("drought_tolerance", True), ("display_name", False),
    ),
    "hedgerow_buffer": (
        *LEADING_KEYS, ("commercial_availability", True), ("drought_tolerance", True), ("robustness_margin", True),
        ("display_name", False),
    ),
}  # fmt: skip


def tie_keys(guild: str) -> list[str]:
    """Every ranking key except the alphabetical display_name tiebreak."""
    return [column for column, _ in RANKING_KEYS[guild] if column != "display_name"]


def ranked_picks(evaluated: pl.DataFrame, guild: str) -> pl.DataFrame:
    """Picks in rank order per cell, with a 0-based `rank_index` and `ties_rank1` (equal to rank 1 on every key)."""
    keys = RANKING_KEYS[guild]
    picks = evaluated.filter(pl.col("is_pick")).sort(
        by=[column for column, _ in keys], descending=[descending for _, descending in keys], nulls_last=True
    )
    ties_first = pl.all_horizontal(
        [pl.col(key).eq_missing(pl.col(key).first().over("cell_id")) for key in tie_keys(guild)]
    )
    return picks.with_columns(
        ties_first.alias("ties_rank1"),
        pl.int_range(pl.len()).over("cell_id").alias("rank_index"),
    )
