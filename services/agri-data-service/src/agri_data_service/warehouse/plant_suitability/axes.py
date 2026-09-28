"""Envelope axes as tri-state Polars expressions (1 pass, 0 fail, null unknown); see AGENTS.md §Axes here."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from agri_data_service.warehouse.plant_suitability.config import RuleConfig

ENVELOPE_AXES = (
    "cold", "dry_year_precip", "mean_precip", "ph", "frost_free_days", "texture",
    "root_depth", "salinity", "calcareous", "anaerobic", "drought", "wetland_indicator",
)  # fmt: skip
AXIS_NAMES = (*ENVELOPE_AXES, "source_applicability")
ARID_WEST = "AW"
LOW_TOLERANCE, MEDIUM_TOLERANCE = 1, 2
# PLANTS salinity classes: None 0-2, Low 2.1-4, Medium 4.1-8, High > 8 dS/m.
SALINITY_LIMIT_DS_PER_M = {0: 2.0, 1: 4.0, 2: 8.0, 3: float("inf")}
NON_SALINE_CEILING_DS_PER_M = 2.0
CALCAREOUS_THRESHOLD_PERCENT = 1.0
MARGIN_AXES = ("cold", "dry_year_precip", "mean_precip", "ph", "frost_free_days", "root_depth")
COLD_MARGIN_SCALE_C = 50.0 / 9.0
PRECIPITATION_MARGIN_FRACTION = 0.20
PH_MARGIN_SCALE = 0.5
FROST_FREE_DAYS_MARGIN_SCALE = 30.0
ROOT_DEPTH_MARGIN_SCALE_CM = 25.0
MARGIN_DECIMALS = 3

WET_DRAINAGE_CLASSES = ["Poorly drained", "Very poorly drained"]
SOMEWHAT_WET_DRAINAGE_CLASSES = ["Somewhat poorly drained"]
WELL_DRAINED_CLASSES = ["Moderately well drained", "Well drained"]
DROUGHTY_DRAINAGE_CLASSES = ["Somewhat excessively drained", "Excessively drained"]
POORLY_DRAINED_WETNESS = ["wet", "somewhat_wet"]


def site_class_columns() -> list[pl.Expr]:
    """Wetness class and drainage regime from SSURGO drainage class and hydric rating (a hydric Yes wins)."""
    drainage, hydric = pl.col("drainage_class"), pl.col("hydric_rating")
    wetness = (
        pl.when(drainage.is_in(WET_DRAINAGE_CLASSES) | (hydric == "Yes"))
        .then(pl.lit("wet"))
        .when(drainage.is_in(SOMEWHAT_WET_DRAINAGE_CLASSES))
        .then(pl.lit("somewhat_wet"))
        .when(drainage.is_in(WELL_DRAINED_CLASSES + DROUGHTY_DRAINAGE_CLASSES))
        .then(pl.lit("not_wet"))
        .otherwise(pl.lit("unknown"))
    )
    regime = (
        pl.when(hydric == "Yes")
        .then(pl.lit("wet_side"))
        .when(drainage.is_in(DROUGHTY_DRAINAGE_CLASSES))
        .then(pl.lit("droughty"))
        .when(drainage.is_in(WELL_DRAINED_CLASSES))
        .then(pl.lit("well_drained"))
        .when(drainage.is_in(WET_DRAINAGE_CLASSES + SOMEWHAT_WET_DRAINAGE_CLASSES))
        .then(pl.lit("wet_side"))
        .otherwise(pl.lit("unknown"))
    )
    return [wetness.alias("site_wetness_class"), regime.alias("site_drainage_regime")]


def status(unknown: pl.Expr, passes: pl.Expr) -> pl.Expr:
    """Tri-state axis: null when unknown, else 1/0."""
    return pl.when(unknown).then(None).when(passes).then(1).otherwise(0).cast(pl.Int8)


def site_frost_free_days() -> pl.Expr:
    """Cell frost-free days minus the region's measured station bias."""
    return pl.col("median_frost_free_days") - pl.col("frost_free_days_station_bias")


def frost_free_uncertain() -> pl.Expr:
    """A shortfall no larger than the station-bias magnitude: 'uncertain frost-free fit', unknown rather than fail."""
    shortfall = pl.col("frost_free_days_min") - site_frost_free_days()
    return ((shortfall > 0) & (shortfall <= pl.col("frost_free_days_station_bias").abs())).fill_null(value=False)


def nwpl_rating_in_region() -> pl.Expr:
    """The taxon's NWPL 2022 indicator in the cell's region, else in the other western region."""
    return (
        pl.when(pl.col("nwpl_region") == ARID_WEST)
        .then(pl.coalesce("nwpl_aw", "nwpl_wmvc"))
        .otherwise(pl.coalesce("nwpl_wmvc", "nwpl_aw"))
    )


def site_meets(site_column: str, trait_column: str, *, site_at_most: bool = False) -> pl.Expr:
    """Site value at least (or at most) the taxon's limit; unknown when either is null."""
    site, trait = pl.col(site_column), pl.col(trait_column)
    return status(site.is_null() | trait.is_null(), site <= trait if site_at_most else site >= trait)


def ph_axis() -> pl.Expr:
    """Site pH inside the taxon's pH range; a single failing bound fails even when the other is null."""
    ph, ph_min, ph_max = pl.col("site_ph"), pl.col("ph_min"), pl.col("ph_max")
    fails = (ph_min.is_not_null() & (ph < ph_min)) | (ph_max.is_not_null() & (ph > ph_max))
    unknown_bound = ph_min.is_null() | ph_max.is_null()
    return (
        pl.when(ph.is_null()).then(None).when(fails).then(0).when(unknown_bound).then(None).otherwise(1).cast(pl.Int8)
    )


def texture_axis() -> pl.Expr:
    """The taxon is adapted to the cell's PLANTS texture group."""
    group = pl.col("site_texture_group")
    adapted = (
        pl.when(group == "coarse")
        .then(pl.col("texture_coarse"))
        .when(group == "medium")
        .then(pl.col("texture_medium"))
        .when(group == "fine")
        .then(pl.col("texture_fine"))
        .otherwise(None)
    )
    return status(adapted.is_null(), adapted == 1)


def root_depth_axis(config: RuleConfig) -> pl.Expr:
    """Root depth against a recorded restriction; only v0 passes a 'none_recorded' status with a null depth."""
    restriction = pl.col("restriction_status")
    none_recorded = restriction == "none_recorded"
    none_recorded_passes = pl.lit(value=False) if config.null_restriction_depth_is_unknown else none_recorded
    return (
        pl.when(none_recorded_passes)
        .then(1)
        .when((restriction == "recorded") & pl.col("root_depth_min_cm").is_not_null())
        .then((pl.col("root_depth_min_cm") <= pl.col("restriction_depth_cm")).cast(pl.Int8))
        .otherwise(None)
        .cast(pl.Int8)
    )


def salinity_axis() -> pl.Expr:
    """A non-saline cell passes; a saline cell needs the taxon's salinity class limit."""
    conductivity = pl.col("ec_ds_per_m")
    limit = pl.col("salinity_tolerance").replace_strict(SALINITY_LIMIT_DS_PER_M, default=None, return_dtype=pl.Float64)
    return (
        pl.when(conductivity.is_null())
        .then(None)
        .when(conductivity <= NON_SALINE_CEILING_DS_PER_M)
        .then(1)
        .when(limit.is_null())
        .then(None)
        .when(conductivity <= limit)
        .then(1)
        .otherwise(0)
        .cast(pl.Int8)
    )


def calcareous_axis() -> pl.Expr:
    """A non-calcareous cell passes; a calcareous cell fails only a taxon with CaCO3 tolerance None."""
    carbonate, tolerance = pl.col("caco3_percent"), pl.col("caco3_tolerance")
    return (
        pl.when(carbonate.is_null())
        .then(None)
        .when(carbonate < CALCAREOUS_THRESHOLD_PERCENT)
        .then(1)
        .when(tolerance.is_null())
        .then(None)
        .when(tolerance >= LOW_TOLERANCE)
        .then(1)
        .otherwise(0)
        .cast(pl.Int8)
    )


def tolerance_axis(site_class: str, lenient_class: str, severe_class: str, tolerance: str) -> pl.Expr:
    """The severe class needs Medium tolerance, the milder constraining class Low; the lenient class passes."""
    site_value, tolerance_value = pl.col(site_class), pl.col(tolerance)
    return (
        pl.when(site_value == lenient_class)
        .then(1)
        .when(site_value == "unknown")
        .then(None)
        .when(tolerance_value.is_null())
        .then(None)
        .when(site_value == severe_class)
        .then((tolerance_value >= MEDIUM_TOLERANCE).cast(pl.Int8))
        .otherwise((tolerance_value >= LOW_TOLERANCE).cast(pl.Int8))
        .cast(pl.Int8)
    )


def wetland_indicator_axis() -> pl.Expr:
    """Unknown without an NWPL route; OBL fails on not_wet cells, FACW on droughty and well-drained; others pass."""
    rating, wetness, regime = nwpl_rating_in_region(), pl.col("site_wetness_class"), pl.col("site_drainage_regime")
    obligate = pl.when(wetness == "unknown").then(None).when(wetness == "not_wet").then(0).otherwise(1)
    dry_regime = regime.is_in(["droughty", "well_drained"])
    facultative_wet = pl.when(regime == "unknown").then(None).when(dry_regime).then(0).otherwise(1)
    return (
        pl.when(pl.col("nwpl_route").is_null())
        .then(None)
        .when(rating == "OBL")
        .then(obligate)
        .when(rating == "FACW")
        .then(facultative_wet)
        .otherwise(1)
        .cast(pl.Int8)
    )


def axis_expressions(config: RuleConfig) -> dict[str, pl.Expr]:
    """Axis name -> tri-state expression over a cross-joined (cell x taxon) frame."""
    frost_free_days = site_frost_free_days()
    frost_free_unknown = frost_free_days.is_null() | pl.col("frost_free_days_min").is_null() | frost_free_uncertain()
    return {
        "cold": site_meets("record_min_c", "min_temp_c"),
        "dry_year_precip": site_meets("dry_year_precip_mm", "precip_min_mm"),
        "mean_precip": site_meets("mean_annual_precip_mm", "precip_max_mm", site_at_most=True),
        "ph": ph_axis(),
        "frost_free_days": status(frost_free_unknown, frost_free_days >= pl.col("frost_free_days_min")),
        "texture": texture_axis(),
        "root_depth": root_depth_axis(config),
        "salinity": salinity_axis(),
        "calcareous": calcareous_axis(),
        "anaerobic": tolerance_axis("site_wetness_class", "not_wet", "wet", "anaerobic_tolerance"),
        "drought": tolerance_axis("site_drainage_regime", "wet_side", "droughty", "drought_tolerance"),
        "wetland_indicator": wetland_indicator_axis(),
    }


def margin_expressions() -> dict[str, pl.Expr]:
    """Axis name -> headroom between site value and PLANTS limit in axis steps (negative means the axis fails)."""
    minimum, maximum, ph = pl.col("precip_min_mm"), pl.col("precip_max_mm"), pl.col("site_ph")
    dry_year_headroom = pl.col("dry_year_precip_mm") - minimum
    mean_headroom = maximum - pl.col("mean_annual_precip_mm")
    root_headroom = pl.col("restriction_depth_cm") - pl.col("root_depth_min_cm")
    restriction_recorded = pl.col("restriction_status") == "recorded"
    return {
        "cold": (pl.col("record_min_c") - pl.col("min_temp_c")) / COLD_MARGIN_SCALE_C,
        "dry_year_precip": pl.when(minimum > 0).then(dry_year_headroom / (PRECIPITATION_MARGIN_FRACTION * minimum)),
        "mean_precip": pl.when(maximum > 0).then(mean_headroom / (PRECIPITATION_MARGIN_FRACTION * maximum)),
        "ph": pl.min_horizontal(ph - pl.col("ph_min"), pl.col("ph_max") - ph) / PH_MARGIN_SCALE,
        "frost_free_days": (site_frost_free_days() - pl.col("frost_free_days_min")) / FROST_FREE_DAYS_MARGIN_SCALE,
        "root_depth": pl.when(restriction_recorded).then(root_headroom / ROOT_DEPTH_MARGIN_SCALE_CM),
    }


def evaluate_pairs(
    site: pl.DataFrame, candidates: pl.DataFrame, row_axis: pl.DataFrame, config: RuleConfig
) -> pl.DataFrame:
    """Cells x candidate taxa with every axis, failed/unknown counts, the robustness margin and the rank helpers."""
    axes = axis_expressions(config)
    paired = (
        site.join(candidates.drop("applicability_rows", strict=False), how="cross")
        .join(row_axis, on=["cell_id", "plant_id"], how="left")
        .with_columns(
            nwpl_rating_in_region().alias("nwpl_rating_in_region"),
            frost_free_uncertain().alias("frost_free_uncertain"),
            pl.col("in_region_supported").fill_null(value=False),
            **{f"axis_{name}": axes[name] for name in ENVELOPE_AXES},
        )
        .with_columns(
            (~pl.col("in_region_supported")).cast(pl.Int8).alias("in_region_rank"),
            pl.col("frost_free_uncertain").cast(pl.Int8).alias("frost_free_uncertain_rank"),
        )
    )
    # A margin only counts on a known axis: an unknown axis has no limit to measure against.
    known_margins = {
        f"margin_{name}": pl.when(pl.col(f"axis_{name}").is_null()).then(None).otherwise(expression)
        for name, expression in margin_expressions().items()
    }
    paired = paired.with_columns(**known_margins)
    axis_columns = [pl.col(f"axis_{name}") for name in AXIS_NAMES]
    smallest_margin = pl.min_horizontal([f"margin_{name}" for name in MARGIN_AXES])
    binding_axis = pl.coalesce(
        [pl.when(pl.col(f"margin_{name}") == smallest_margin).then(pl.lit(name)) for name in MARGIN_AXES]
    )
    return paired.with_columns(
        pl.sum_horizontal([(column == 0).fill_null(value=False).cast(pl.Int8) for column in axis_columns]).alias(
            "failed_axis_count"
        ),
        pl.sum_horizontal([column.is_null().cast(pl.Int8) for column in axis_columns]).alias("unknown_axis_count"),
        smallest_margin.round(MARGIN_DECIMALS).alias("robustness_margin"),
        binding_axis.alias("binding_axis"),
    ).with_columns((pl.col("failed_axis_count") == 0).alias("is_pick"))
