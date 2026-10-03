"""Row-level applicability: a taxon counts at a cell only through a guide row whose band and habitat apply there.

Rationale and the qualifier-to-site mapping: AGENTS.md §Applicability in this directory.
"""

from __future__ import annotations

import polars as pl

from agri_data_service.warehouse.plant_suitability.axes import (
    NON_SALINE_CEILING_DS_PER_M,
    POORLY_DRAINED_WETNESS,
    status,
)

MILLIMETRES_PER_INCH = 25.4
FOREST_WOODLAND_MLRAS = ["3", "6", "43B"]
# Every qualifier habitat_status maps to site data; guide-row load refuses any other (pools.GUIDE_ROW_VOCABULARIES).
HABITAT_QUALIFIERS = frozenset((
    "saline_alkali_or_poor_drainage", "sandy_or_loam", "forest_woodland", "juniper_sites", "wet_soils",
    "moist_to_wet_soils",
))  # fmt: skip
CONDITION_KEY = ["min_precip_in", "max_precip_in", "habitat_qualifier"]
SITE_COLUMNS = [
    "mean_annual_precip_mm", "ec_ds_per_m", "site_wetness_class", "site_drainage_regime", "site_texture_group", "mlra",
]  # fmt: skip


def band_status() -> pl.Expr:
    """1 when the cell's mean annual precipitation lies inside the row's band (inclusive; an open side never fails)."""
    precipitation, minimum, maximum = pl.col("mean_annual_precip_mm"), pl.col("min_precip_in"), pl.col("max_precip_in")
    minimum_holds = minimum.is_null() | (precipitation >= minimum * MILLIMETRES_PER_INCH)
    maximum_holds = maximum.is_null() | (precipitation <= maximum * MILLIMETRES_PER_INCH)
    no_band = minimum.is_null() & maximum.is_null()
    inside_band = status(precipitation.is_null(), minimum_holds & maximum_holds)
    return pl.when(no_band).then(1).otherwise(inside_band).cast(pl.Int8)


def habitat_status() -> pl.Expr:
    """1 / 0 / null for the row's habitat qualifier against the cell (a row without one applies)."""
    qualifier, conductivity = pl.col("habitat_qualifier"), pl.col("ec_ds_per_m")
    wetness, regime = pl.col("site_wetness_class"), pl.col("site_drainage_regime")
    texture, mlra = pl.col("site_texture_group"), pl.col("mlra")
    poorly_drained = wetness.is_in(POORLY_DRAINED_WETNESS)
    saline = (conductivity > NON_SALINE_CEILING_DS_PER_M).fill_null(value=False)
    saline_or_poorly_drained = (
        pl.when(saline | poorly_drained)
        .then(1)
        .when(conductivity.is_null() | (wetness == "unknown"))
        .then(None)
        .otherwise(0)
    )
    sandy_or_loam = pl.when(texture.is_null()).then(None).when(texture.is_in(["coarse", "medium"])).then(1).otherwise(0)
    forest_woodland = pl.when(mlra.is_null()).then(None).when(mlra.is_in(FOREST_WOODLAND_MLRAS)).then(1).otherwise(0)
    wet_soils = pl.when(wetness == "unknown").then(None).when(poorly_drained).then(1).otherwise(0)
    moist_to_wet = pl.when(regime == "unknown").then(None).when(regime == "droughty").then(0).otherwise(1)
    return (
        pl.when(qualifier.is_null())
        .then(1)
        .when(qualifier == "saline_alkali_or_poor_drainage")
        .then(saline_or_poorly_drained)
        .when(qualifier == "sandy_or_loam")
        .then(sandy_or_loam)
        .when(qualifier == "forest_woodland")
        .then(forest_woodland)
        .when(qualifier == "wet_soils")
        .then(wet_soils)
        .when(qualifier == "moist_to_wet_soils")
        .then(moist_to_wet)
        .otherwise(None)  # juniper_sites: no juniper-cover layer, so never a silent pass and never a fail
        .cast(pl.Int8)
    )


def both_hold(first: str, second: str) -> pl.Expr:
    """0 when either fails, 1 when both pass, else unknown."""
    first_status, second_status = pl.col(first), pl.col(second)
    return (
        pl.when((first_status == 0) | (second_status == 0))
        .then(0)
        .when((first_status == 1) & (second_status == 1))
        .then(1)
        .otherwise(None)
        .cast(pl.Int8)
    )


def candidate_conditions(candidates: pl.DataFrame) -> pl.DataFrame:
    """One row per (taxon, supporting guide row) with its band, qualifier (vocabulary checked at load), tag and name."""
    conditions = (
        candidates.select("plant_id", "applicability_rows")
        .explode("applicability_rows", empty_as_null=True)
        .unnest("applicability_rows")
    )
    unsupported = set(candidates["plant_id"].to_list()) - set(conditions.drop_nulls("source_tag")["plant_id"].to_list())
    if unsupported:
        message = f"pool taxa without a supporting guide row: {sorted(unsupported)}"
        raise ValueError(message)
    return conditions


def pair_rows(site: pl.DataFrame, conditions: pl.DataFrame) -> pl.DataFrame:
    """Every (cell, taxon, supporting row) with the row's status at that cell."""
    statuses = (
        site.select("cell_id", *SITE_COLUMNS)
        .join(conditions.select(CONDITION_KEY).unique(), how="cross")
        .with_columns(band_status().alias("band_status"), habitat_status().alias("habitat_status"))
        .with_columns(both_hold("band_status", "habitat_status").alias("row_status"))
        .select("cell_id", *CONDITION_KEY, "row_status")
    )
    return conditions.join(statuses, on=CONDITION_KEY, how="inner", nulls_equal=True)


def applicability(site: pl.DataFrame, candidates: pl.DataFrame) -> pl.DataFrame:
    """Per (cell, taxon): `axis_source_applicability` and whether an in-region row carries the support."""
    conditions = candidate_conditions(candidates).select("plant_id", *CONDITION_KEY, "in_region").unique()
    row_status = pl.col("row_status")
    grouped = (
        pair_rows(site, conditions)
        .group_by("cell_id", "plant_id")
        .agg(
            (row_status == 1).any().alias("any_applies"),
            row_status.is_null().any().alias("any_unknown"),
            ((row_status == 1) & pl.col("in_region")).any().alias("in_region_applies"),
            (row_status.is_null() & pl.col("in_region")).any().alias("in_region_unknown"),
        )
    )
    return grouped.select(
        "cell_id",
        "plant_id",
        pl.when(pl.col("any_applies"))
        .then(1)
        .when(pl.col("any_unknown"))
        .then(None)
        .otherwise(0)
        .cast(pl.Int8)
        .alias("axis_source_applicability"),
        pl.when(pl.col("any_applies"))
        .then(pl.col("in_region_applies"))
        .when(pl.col("any_unknown"))
        .then(pl.col("in_region_unknown"))
        .otherwise(pl.lit(value=False))
        .alias("in_region_supported"),
    )


def supporting_rows(pairs: pl.DataFrame, site: pl.DataFrame, candidates: pl.DataFrame) -> pl.DataFrame:
    """Tags, printed names and inherited-stratification sources of the rows carrying each (cell, pick) pair."""
    wanted = pairs.select("cell_id", "plant_id", "axis_source_applicability")
    wanted_cells = site.join(wanted.select("cell_id").unique(), on="cell_id")
    rows = pair_rows(wanted_cells, candidate_conditions(candidates))
    rows = rows.join(wanted, on=["cell_id", "plant_id"], how="inner")
    carrying = rows.filter(
        pl.when(pl.col("axis_source_applicability") == 1)
        .then(pl.col("row_status") == 1)
        .when(pl.col("axis_source_applicability").is_null())
        .then(pl.col("row_status").is_null())
        .otherwise(pl.lit(value=False))
    )
    return carrying.group_by("cell_id", "plant_id").agg(
        pl.col("source_tag").unique().sort().alias("supporting_tags"),
        pl.col("listed_name").unique().sort().alias("supporting_names"),
        pl.col("stratified_from").drop_nulls().unique().sort().alias("stratification_sources"),
    )
