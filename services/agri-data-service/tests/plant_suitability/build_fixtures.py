"""Regenerate the plant-suitability fixtures from the frozen v0 prototype; run by hand, never collected by pytest.

    env -u PROJ_LIB -u GDAL_DATA <prototype>/.venv/Scripts/python.exe build_fixtures.py [<prototype directory>]

The prototype (default `<repo>/.omc/research/plant-suitability-v0-20260926`) is gitignored and exists only in the
main checkout. Inputs are the prototype's own intermediate tables (its name matching, species traits and NWPL
parse), expected outputs are its frozen `join/<pilot>_cells.parquet` rows. This script never runs the engine under
test, so the golden comparison cannot be self-fulfilling. Layout: warehouse/plant_suitability/AGENTS.md.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

SERVICE_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIRECTORY = SERVICE_ROOT / "tests" / "fixtures" / "plant_suitability"
ENGINE_DIRECTORY = SERVICE_ROOT / "src" / "agri_data_service" / "warehouse" / "plant_suitability"
DEFAULT_PROTOTYPE_DIRECTORY = SERVICE_ROOT.parents[1] / ".omc" / "research" / "plant-suitability-v0-20260926"
REGIONS = ("boise", "corvallis", "bend")
GUILDS = ("greenstrip", "post_fire_restoration", "hedgerow_buffer")
STATE_PRESENCE_COLUMNS = {"ID": "present_in_id", "OR": "present_in_or", "WA": "present_in_wa"}
RANGE_WIDE_SOURCE_IDS = ["idpm_tn2a_2017", "nrcs_id_tn77_2021", "nrcs_tn50_2008"]
RANGE_WIDE_SHORT_NAMES = frozenset({"NRCS TN PM-2A (2017)", "NRCS ID TN PM-77 (2021)", "NRCS TN PM-50 (2008)"})
# The canonical ids of the engine's licences.py (loaded by path, as schemas.py is, in load_engine_module).
US_GOVERNMENT_WORK = "us-government-work"
UNRECORDED_LICENCE = "unrecorded"
CC_BY_LICENCE = "CC-BY-4.0"
# An assumption, not curation (AGENTS.md §Licences): us-government-work only where regional_lists/SOURCES_*.md names
# one US federal agency as the sole publisher; co-published, contractor-named and non-federal sources stay unrecorded.
SOURCE_LICENCES = {
    "tn16_2009": US_GOVERNMENT_WORK,  # NRCS Idaho TN PM-16
    "bluebunch_pg": US_GOVERNMENT_WORK,  # NRCS plant guide
    # One document, "NRCS OR/WA guide 2000", curated under two ids: a short name carries one licence (load checks it).
    "orwa_2000": US_GOVERNMENT_WORK,  # NRCS OR/WA guide (steppe id)
    "orwa_guide_2000": US_GOVERNMENT_WORK,  # NRCS OR/WA guide (westside id)
    "soda_2016": US_GOVERNMENT_WORK,  # BLM status report
    "blm_range_2026": US_GOVERNMENT_WORK,  # BLM blog
    "blm_twin_falls_pesrp_2013": US_GOVERNMENT_WORK,  # BLM Twin Falls District EA
    "idpm_tn2a_2017": US_GOVERNMENT_WORK,  # NRCS TN PM-2A
    "nrcs_or_tn13_2008": US_GOVERNMENT_WORK,  # USDA-NRCS Oregon TN PM-13
    "nrcs_id_tn77_2021": US_GOVERNMENT_WORK,  # USDA-NRCS Idaho TN PM-77
    "nrcs_tn50_2008": US_GOVERNMENT_WORK,  # USDA-NRCS TN PM-50
    "blm_boise_nfesrp_2005": UNRECORDED_LICENCE,  # BLM EA, but the citation also names North Wind, Inc.
    "nrcs_westside_post_fire": UNRECORDED_LICENCE,  # NRCS text distributed by Marion SWCD
    "marion_swcd_wildfire": UNRECORDED_LICENCE,
    "odfw_2017": UNRECORDED_LICENCE,  # "ODFW 2017", with odfw_rehab_2017: one document, one licence
    "odfw_rehab_2017": UNRECORDED_LICENCE,
    "osu_co_windbreak": UNRECORDED_LICENCE,
    "osu_willamette_hedgerow_2018": UNRECORDED_LICENCE,  # Metro and NRCS PMC staff, published by OSU
    "pnw5_2005": UNRECORDED_LICENCE,  # PNW Extension
    "xerces_gb_2022": UNRECORDED_LICENCE,
    "xerces_idaho_2017": UNRECORDED_LICENCE,
    "xerces_maritime_nw_pollinator": UNRECORDED_LICENCE,
    "xerces_monarch_gb": UNRECORDED_LICENCE,
    "xerces_nppbi_inland_nw": UNRECORDED_LICENCE,
    "xerces_west_hedgerow_422": UNRECORDED_LICENCE,  # Xerces with NRCS
}
# Where each site-input group came from (prototype site_conditions/{soil,climate}/SOURCES.md, join/site_table.py).
# PRISM's terms were never verified (prototype FROZEN.md issue 6), so precipitation stays unrecorded.
SITE_INPUT_SOURCES = {
    "soil_survey": {
        "source": "USDA NRCS SSURGO via Soil Data Access: dominant component, 0-30 cm; MLRA by SDA lookup",
        "licence": US_GOVERNMENT_WORK,
        "release": "SDA accessed 2026-09-26",
    },
    "soil_ph_texture": {
        "source": "USDA NRCS SSURGO dominant component where present, else ISRIC SoilGrids v2.0 0-30 cm",
        "licence": CC_BY_LICENCE,
        "release": "SDA and SoilGrids 2.0 accessed 2026-09-26",
    },
    "cold": {
        "source": "ERA5-Land via the Open-Meteo archive (era5_seamless, 1991-2020 record low), lapse-adjusted to "
        "Copernicus DEM GLO-90",
        "licence": CC_BY_LICENCE,
        "release": "Open-Meteo archive accessed 2026-09-26",
    },
    "frost_free": {
        "source": "ERA5-Land via the Open-Meteo archive (era5_seamless, 1991-2020 median frost-free days)",
        "licence": CC_BY_LICENCE,
        "release": "Open-Meteo archive accessed 2026-09-26",
    },
    "frost_free_station_bias": {
        "source": "NOAA NCEI U.S. Climate Normals 1991-2020 station growing season, against the station cell",
        "licence": US_GOVERNMENT_WORK,
        "release": "NCEI normals-annualseasonal-1991-2020 accessed 2026-09-26",
    },
    "precipitation": {
        "source": "PRISM Climate Group 1991-2020 annual precipitation normal, 800 m (terms not verified)",
        "licence": UNRECORDED_LICENCE,
        "release": "an91/r2207d normals/9120.a",
    },
}
# The permitted precipitation PRODUCTION is served: era5_seamless precipitation is ERA5 at 0.25 degree, not ERA5-Land
# (which returns none; prototype site_conditions/climate/SOURCES.md). v0 read PRISM, so the golden keeps PRISM.
ERA5_PRECIPITATION_SOURCE = {
    "source": "ERA5 via the Open-Meteo archive (era5_seamless, 0.25 degree; 1991-2020 annual precipitation mean and "
    "20th-percentile year)",
    "licence": CC_BY_LICENCE,
    "release": "Open-Meteo archive accessed 2026-09-26",
}
ERA5_PRECIPITATION_COLUMNS = {
    "mean_annual_precip_mm": "mean_annual_precip_mm",
    "dry_year_precip_mm": "annual_precip_p20_mm",
}
# The licence flow's pair of Boise woody-buffer sources: one the PRODUCTION gate drops, one it keeps.
LICENCE_DROPPED_SOURCE = ("pnw5_2005", "PNW0005")
LICENCE_KEPT_SOURCE = ("nrcs_id_tn77_2021", "NRCS ID TN PM-77 (2021)")
TN_2A_SHORT_NAME = "NRCS TN PM-2A (2017)"
# The v0 review's red-osier dogwood upland cell (review/data3/recompute_mismatches_cf_cornus.csv).
RED_OSIER_DOGWOOD_REVIEW_CELL = "-12333_4455"
PLANTS_INTRODUCED_CODES = frozenset({"I", "I?", "W"})
CELLS_PER_REGION = 20
NON_SALINE_CEILING_DS_PER_M = 2.0
LEYMUS = "Leymus triticoides"
EAST_OF_CASCADE_CREST_TAXON = "Krascheninnikovia lanata"
UNCERTAIN_FROST_FREE = "uncertain frost-free fit"
SITE_COLUMN_SOURCES = {
    "cell_id": "cell_id",
    "region": "pilot",
    "state": "state",
    "scoring_null_reason": "scoring_null_reason",
    "mlra": "mlra",
    "nwpl_region": "nwpl_region",
    "record_min_c": "record_min_c",
    "median_frost_free_days": "median_frost_free_days",
    "frost_free_days_station_bias": "frost_free_days_station_bias",
    "mean_annual_precip_mm": "prism_annual_precip_mm",
    "dry_year_precip_mm": "annual_precip_p20_prism_scaled_mm",
    "site_ph": "site_ph",
    "site_texture_group": "site_texture_group",
    "restriction_status": "site_restriction_status",
    "restriction_depth_cm": "ssurgo_restriction_depth_cm",
    "ec_ds_per_m": "site_ec_ds_per_m",
    "caco3_percent": "site_caco3_percent",
    "drainage_class": "ssurgo_drainage_class",
    "hydric_rating": "ssurgo_hydric_rating",
}
SPECIES_TRAIT_COLUMNS = (
    "min_temp_f",
    "min_temp_c",
    "precip_min_in",
    "precip_min_mm",
    "precip_max_in",
    "precip_max_mm",
    "ph_min",
    "ph_max",
    "frost_free_days_min",
    "root_depth_min_in",
    "root_depth_min_cm",
    "height_mature_ft",
    "salinity_tolerance",
    "caco3_tolerance",
    "anaerobic_tolerance",
    "drought_tolerance",
    "fire_tolerance",
    "seedling_vigor",
    "commercial_availability",
    "texture_coarse",
    "texture_medium",
    "texture_fine",
    "nwpl_aw",
    "nwpl_wmvc",
    "nwpl_route",
)
SCORED = pl.col("scoring_null_reason").is_null()


def load_engine_module(name: str) -> ModuleType:
    """One package-free engine module, loaded by path so the prototype interpreter needs no service dependencies."""
    path = ENGINE_DIRECTORY / f"{name}.py"
    specification = importlib.util.spec_from_file_location(f"plant_suitability_{name}", path)
    if specification is None or specification.loader is None:
        message = f"cannot load {path}"
        raise SystemExit(message)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def load_prototype(prototype_directory: Path) -> dict[str, ModuleType]:
    """The prototype's join modules, imported from its own directory."""
    sys.path.insert(0, str(prototype_directory / "join"))
    names = ("regional_pools", "nwpl", "envelope_axes", "site_table", "build_cell_recommendations", "exclusion_match")
    return {name: importlib.import_module(name) for name in names}


def guide_rows(prototype: dict[str, ModuleType], traits: pl.DataFrame) -> pl.DataFrame:
    """Every live regional-list row with the prototype's own PLANTS match, in the prototype's load order."""
    regional_pools = prototype["regional_pools"]
    rows, _ = regional_pools.load_list_rows()
    matched = regional_pools.attach_matches(rows, regional_pools.match_regional_names(rows, traits))
    renamed_listing = matched.filter(
        (pl.col("matched_plant_ids").list.len() > 0) & (pl.col("match_name") != pl.col("scientific_name_normalized"))
    )
    if renamed_listing.height:
        message = f"matched rows whose match name differs from the listed name: {renamed_listing.height}"
        raise SystemExit(message)
    unlicensed = set(matched["source_id"].unique().to_list()) - set(SOURCE_LICENCES)
    unknown_licences = set(SOURCE_LICENCES.values()) - load_engine_module("licences").KNOWN_LICENCES
    if unlicensed or unknown_licences:
        message = f"sources without a fixture licence entry {sorted(unlicensed)}, or unknown ids {unknown_licences}"
        raise SystemExit(message)
    return (
        matched.sort("list_name", "list_row")
        .with_row_index("guide_row_id")
        .select(
            pl.col("guide_row_id").cast(pl.Int64),
            "source_id",
            pl.col("source_short").alias("source_short_name"),
            "page",
            pl.col("source_id").replace_strict(SOURCE_LICENCES, return_dtype=pl.String).alias("license"),
            "guild",
            pl.col("pilots").alias("applies_to_regions"),
            pl.col("pilot_in_region").alias("in_region"),
            "applicability_confidence",
            pl.col("scientific_name_normalized").alias("listed_scientific_name"),
            pl.col("common_name_listed").alias("listed_common_name"),
            "matched_plant_ids",
            "match_route",
            "plant_role",
            pl.col("source_min_precip_in").alias("min_precip_in"),
            pl.col("source_max_precip_in").alias("max_precip_in"),
            "habitat_qualifier",
            pl.col("native_per_source").alias("origin_per_source"),
            pl.when(pl.col("source_id").is_in(RANGE_WIDE_SOURCE_IDS))
            .then(pl.lit("range_wide"))
            .otherwise(pl.lit("regional"))
            .alias("origin_scope"),
            "nativity_conflict",
            pl.col("list_noxious_states").alias("noxious_states"),
        )
    )


def species_rows(traits: pl.DataFrame, plant_ids: set[int]) -> pl.DataFrame:
    """The frozen PLANTS trait rows of every taxon a guide row names, in the engine's envelope shape."""
    records = [
        {
            "plant_id": row["plant_id"],
            "accepted_name": row["name_without_author"],
            "common_name": row["common_name"],
            "family_name": row["family_name"],
            "duration": row["duration"],
            "synonym_names": (row["master_synonym_names"] or []) + (row["characteristic_synonym_names"] or []),
            "native_status_l48": row["native_status_l48"],
            "recorded_states": [state for state, column in STATE_PRESENCE_COLUMNS.items() if row[column]],
            "fire_resistant_values": row["fire_resistant_raw_values"] or [],
            **{column: row[column] for column in SPECIES_TRAIT_COLUMNS},
            # The prototype resolved these ratings, not the engine: tests run resolve_wetland_ratings per rule set.
            "nwpl_resolved_under": None,
        }
        for row in traits.filter(pl.col("plant_id").is_in(sorted(plant_ids))).sort("plant_id").iter_rows(named=True)
    ]
    return pl.DataFrame(records, infer_schema_length=None)


def wetland_rows(prototype: dict[str, ModuleType], species: pl.DataFrame, guides: pl.DataFrame) -> pl.DataFrame:
    """Every NWPL 2022 row sharing a binomial with a fixture taxon's names, printed names or name-table target."""
    binomial_key = prototype["exclusion_match"].binomial_key
    synonyms = species.select(pl.col("synonym_names").explode(empty_as_null=True)).to_series().drop_nulls()
    names = [
        *species["accepted_name"].to_list(),
        *synonyms.to_list(),
        *guides["listed_scientific_name"].drop_nulls().to_list(),
        *prototype["nwpl"].NWPL_NAME_RESOLUTIONS.values(),
    ]
    wanted = {binomial_key(name) for name in names} - {None}
    nwpl = prototype["nwpl"].load_nwpl()
    return nwpl.filter(pl.col("nwpl_binomial_key").is_in(sorted(wanted))).select("nwpl_name", "nwpl_aw", "nwpl_wmvc")


def prototype_evaluation(prototype: dict[str, ModuleType], region: str, guild: str) -> pl.DataFrame:
    """The prototype's own (cell, taxon) axis evaluation for one pilot and guild over every scored cell."""
    build = prototype["build_cell_recommendations"]
    envelope_axes = prototype["envelope_axes"]
    site = prototype["site_table"].build_site_table()
    candidates = pl.read_parquet(build.JOIN_DIRECTORY / "guild_candidates.parquet")
    cells = site.filter((pl.col("pilot") == region) & SCORED).select(build.SITE_EVALUATION_COLUMNS)
    pool = candidates.filter((pl.col("pilot") == region) & (pl.col("guild") == guild)).select(build.CANDIDATE_COLUMNS)
    source_tags = pl.col("applicability_rows").list.eval(pl.element().struct.field("source_tag"))
    tags = pool.select("plant_id", source_tags.alias("tags"))
    in_region_rows = pool.select(
        "plant_id",
        pl.col("applicability_rows")
        .list.eval(
            ~pl.element().struct.field("in_region")
            | pl.element().struct.field("min_precip_in").is_not_null()
            | pl.element().struct.field("max_precip_in").is_not_null()
            | pl.element().struct.field("habitat_qualifier").is_not_null()
        )
        .list.all()
        .alias("in_region_rows_stratified"),
    )
    evaluated = envelope_axes.evaluate(cells, pool, guild, envelope_axes.PRIMARY_VARIANT)
    return evaluated.join(tags, on="plant_id").join(in_region_rows, on="plant_id")


def named_only_by(short_name: str) -> pl.Expr:
    """True when every guide row carrying the pool taxon comes from the one source."""
    return pl.col("tags").list.eval(pl.element().str.starts_with(f"{short_name} ")).list.all()


def first_cell(frame: pl.DataFrame, condition: pl.Expr, role: str) -> str:
    """The lowest cell id meeting the condition; a role nothing satisfies stops the build."""
    matches = frame.filter(condition).sort("cell_id")
    if matches.height == 0:
        message = f"no cell satisfies the fixture role {role!r}"
        raise SystemExit(message)
    return str(matches["cell_id"][0])


def labels_contain(guild: str, text: str) -> pl.Expr:
    """True when any of the cell's top-3 labels for the guild contains the text."""
    return (
        pl.col(f"{guild}_top3_labels")
        .list.eval(pl.element().str.contains(text, literal=True))
        .list.any()
        .fill_null(value=False)
    )


def licence_roles(hedgerow: pl.DataFrame) -> tuple[str, dict[str, Any]]:
    """A Boise cell with woody picks named only by a licence-unrecorded source and only by a federal one."""
    picks = hedgerow.filter(pl.col("is_pick"))
    dropped = picks.filter(named_only_by(LICENCE_DROPPED_SOURCE[1]))
    kept = picks.filter(named_only_by(LICENCE_KEPT_SOURCE[1]))
    both = dropped.select("cell_id").unique().join(kept.select("cell_id").unique(), on="cell_id")
    cell_id = first_cell(both, pl.lit(value=True), "boise PNW0005-only and TN PM-77-only woody picks")
    at_cell = pl.col("cell_id") == cell_id
    taxa = {
        "licence_dropped_source_id": LICENCE_DROPPED_SOURCE[0],
        "licence_dropped_source_only_taxa": sorted(dropped.filter(at_cell)["display_name"].to_list()),
        "licence_kept_source_id": LICENCE_KEPT_SOURCE[0],
        "licence_kept_source_only_taxa": sorted(kept.filter(at_cell)["display_name"].to_list()),
    }
    return cell_id, taxa


def boise_roles(prototype: dict[str, ModuleType], frozen: pl.DataFrame) -> tuple[dict[str, str], dict[str, Any]]:
    """Boise cells for the withheld, burned, saline, Leymus, soil, in-region-versus-neighbour and licence flows."""
    restoration = prototype_evaluation(prototype, "boise", "post_fire_restoration")
    hedgerow = prototype_evaluation(prototype, "boise", "hedgerow_buffer")
    greenstrip = prototype_evaluation(prototype, "boise", "greenstrip")
    every_guild = pl.concat([restoration, hedgerow, greenstrip], how="diagonal_relaxed")
    leymus_picks = restoration.filter(pl.col("is_pick") & (pl.col("display_name") == LEYMUS)).select("cell_id")
    saline = pl.col("site_ec_ds_per_m") > NON_SALINE_CEILING_DS_PER_M
    saline_leymus = frozen.join(leymus_picks, on="cell_id").filter(saline)
    soil_failures = every_guild.group_by("cell_id").agg(
        [(pl.col(f"axis_{axis}") == 0).any().alias(axis) for axis in ("salinity", "calcareous", "root_depth")]
    )
    confirmed_in_region = pl.col("in_region_supported") & pl.col("in_region_rows_stratified")
    uncertain = hedgerow.filter(pl.col("is_pick") & pl.col("frost_free_uncertain") & confirmed_in_region)
    neighbour = hedgerow.filter(
        pl.col("is_pick") & ~pl.col("in_region_supported") & (pl.col("axis_frost_free_days") == 1)
    )
    paired_cell = first_cell(
        uncertain.select("cell_id").unique().join(neighbour.select("cell_id").unique(), on="cell_id"),
        pl.lit(value=True),
        "in-region uncertain versus confirmed neighbour",
    )
    licence_cell, licence_taxa = licence_roles(hedgerow)
    fully_known_somewhere = pl.any_horizontal([pl.col(f"{guild}_count_fully_known") > 0 for guild in GUILDS])
    roles = {
        "withheld_cell": first_cell(frozen, ~SCORED, "boise withheld"),
        "burned_cell": first_cell(frozen, SCORED & pl.col("burned_since_1984"), "boise burned"),
        "saline_cell": first_cell(saline_leymus, pl.lit(value=True), "boise saline with a Leymus pick"),
        "leymus_non_saline_cell": first_cell(
            frozen,
            SCORED
            & (pl.col("site_ec_ds_per_m") <= NON_SALINE_CEILING_DS_PER_M)
            & (pl.col("site_wetness_class") == "not_wet")
            & pl.col("post_fire_restoration_top3").str.contains(LEYMUS, literal=True),
            "boise non-saline Leymus top-3",
        ),
        "soil_failures_cell": first_cell(
            soil_failures, pl.col("salinity") & (pl.col("calcareous") | pl.col("root_depth")), "boise soil failures"
        ),
        "restriction_none_recorded_cell": first_cell(
            frozen,
            SCORED & (pl.col("site_restriction_status") == "none_recorded") & fully_known_somewhere,
            "boise none-recorded restriction",
        ),
        "in_region_uncertain_versus_neighbour_cell": paired_cell,
        "licence_cell": licence_cell,
    }
    at_paired_cell = pl.col("cell_id") == paired_cell
    taxa = {
        "in_region_uncertain_taxa": sorted(uncertain.filter(at_paired_cell)["display_name"].to_list()),
        "confirmed_neighbour_taxa": sorted(neighbour.filter(at_paired_cell)["display_name"].to_list()),
    }
    return roles, taxa | licence_taxa | origin_taxa(prototype)


def origin_taxa(prototype: dict[str, ModuleType]) -> dict[str, list[str]]:
    """Boise taxa PLANTS does not record in ID that v0 left unflagged, split by the scope of their native statements."""
    candidates = pl.read_parquet(prototype["build_cell_recommendations"].JOIN_DIRECTORY / "guild_candidates.parquet")
    not_recorded = pl.col("origin_label").str.contains("PLANTS does not record it in ID", literal=True)
    unflagged = candidates.filter((pl.col("pilot") == "boise") & not_recorded).unique(subset="display_name")
    range_wide_only, state_scoped = [], []
    for row in unflagged.iter_rows(named=True):
        natives = {
            statement.split(" [in-region]")[0]
            for statement in row["origin_documents"].split("; ")
            if statement.endswith("[in-region]: native")
        }
        (range_wide_only if natives <= RANGE_WIDE_SHORT_NAMES else state_scoped).append(row["display_name"])
    return {"range_wide_only_taxa": sorted(range_wide_only), "state_scoped_native_taxa": sorted(state_scoped)}


def bend_roles(prototype: dict[str, ModuleType], frozen: pl.DataFrame) -> tuple[dict[str, str], dict[str, Any]]:
    """Bend cells for the withheld, uncertain-frost-free, wet, burned and east-of-the-Cascade-crest flows."""
    hedgerow = prototype_evaluation(prototype, "bend", "hedgerow_buffer")
    tn_2a_only = hedgerow.filter(
        pl.col("is_pick") & named_only_by(TN_2A_SHORT_NAME) & (pl.col("display_name") == EAST_OF_CASCADE_CREST_TAXON)
    )
    roles = {
        "withheld_cell": first_cell(frozen, ~SCORED, "bend withheld"),
        "uncertain_frost_free_cell": first_cell(
            frozen, SCORED & labels_contain("hedgerow_buffer", UNCERTAIN_FROST_FREE), "bend uncertain frost-free"
        ),
        "wet_cell": first_cell(frozen, SCORED & (pl.col("site_wetness_class") == "wet"), "bend wet"),
        "burned_cell": first_cell(frozen, SCORED & pl.col("burned_since_1984"), "bend burned"),
        "east_of_cascade_crest_cell": first_cell(tn_2a_only, pl.lit(value=True), "bend TN 2A-only pick"),
    }
    return roles, {"east_of_cascade_crest_taxon": EAST_OF_CASCADE_CREST_TAXON}


def corvallis_roles(frozen: pl.DataFrame) -> dict[str, str]:
    """Corvallis cells for the withheld, no-regional-guide, wet, missing-EC and red-osier dogwood flows."""
    return {
        "withheld_cell": first_cell(frozen, ~SCORED, "corvallis withheld"),
        "no_regional_guide_cell": first_cell(
            frozen, SCORED & (pl.col("greenstrip_status") == "no_regional_guide"), "corvallis no regional guide"
        ),
        "wet_cell": first_cell(frozen, SCORED & (pl.col("site_wetness_class") == "wet"), "corvallis wet"),
        "missing_ec_cell": first_cell(frozen, SCORED & pl.col("site_ec_ds_per_m").is_null(), "corvallis missing EC"),
        "red_osier_dogwood_review_cell": first_cell(
            frozen, pl.col("cell_id") == RED_OSIER_DOGWOOD_REVIEW_CELL, "corvallis red-osier dogwood review cell"
        ),
    }


def coordinate_band(column: str, side: int) -> pl.Expr:
    """Which of `side` equal slices of the column's distinct values each cell falls in."""
    return (pl.col(column).rank("dense") - 1) * side // pl.col(column).n_unique()


def spread_cells(frozen: pl.DataFrame) -> list[str]:
    """The middle cell of each square of a longitude x latitude grid, diagonal by diagonal so rows and columns fill."""
    side = math.ceil(math.sqrt(CELLS_PER_REGION))
    squares = (
        frozen.with_columns(
            coordinate_band("centroid_lon", side).alias("column"), coordinate_band("centroid_lat", side).alias("row")
        )
        .group_by("row", "column")
        .agg(pl.col("cell_id").sort_by("centroid_lat", "centroid_lon").get(pl.len() // 2))
        .with_columns(((pl.col("row") + pl.col("column")) % side).alias("diagonal"))
        .sort("diagonal", "row")
    )
    return squares["cell_id"].to_list()


def chosen_cells(frozen: pl.DataFrame, role_cells: list[str]) -> list[str]:
    """The role cells, then cells spread over the pilot in two dimensions, up to CELLS_PER_REGION."""
    chosen = list(dict.fromkeys(role_cells))
    spread = [cell_id for cell_id in spread_cells(frozen) if cell_id not in chosen]
    return chosen + spread[: max(CELLS_PER_REGION - len(chosen), 0)]


def introduced_only(l48_status: str | None) -> bool:
    """True when PLANTS lists only introduced lower-48 status codes for the taxon."""
    codes = set((l48_status or "").split("|")) - {""}
    return bool(codes) and codes <= PLANTS_INTRODUCED_CODES


def noxious_taxa(
    boise_cells: pl.DataFrame, species: pl.DataFrame, binomial_key: Callable[[str], str | None]
) -> dict[str, str]:
    """Boise top-3 picks for the noxious flow: a binomial, a wholly introduced genus and a row-annotated taxon."""
    top_names = sorted(
        {
            name
            for guild in GUILDS
            for joined in boise_cells[f"{guild}_top3"].drop_nulls().to_list()
            for name in joined.split(";")
        }
    )
    genera = species.select(pl.col("accepted_name").str.split(" ").list.first().alias("genus"), "native_status_l48")
    introduced_genera = {
        genus_name
        for genus_name, statuses in genera.group_by("genus").agg("native_status_l48").iter_rows()
        if all(introduced_only(status) for status in statuses)
    }
    genus = min(name.split(" ")[0] for name in top_names if name.split(" ")[0] in introduced_genera)
    binomial_counts: dict[str | None, int] = {}
    for accepted_name in species["accepted_name"].to_list():
        key = binomial_key(accepted_name)
        binomial_counts[key] = binomial_counts.get(key, 0) + 1
    single = [
        name for name in top_names if name.split(" ")[0] != genus and binomial_counts.get(binomial_key(name)) == 1
    ]
    return {"noxious_binomial_taxon": single[0], "noxious_genus": genus, "noxious_annotation_taxon": single[1]}


def era5_precipitation(prototype_directory: Path, site: pl.DataFrame) -> pl.DataFrame:
    """ERA5 annual and 20th-percentile precipitation for every fixture cell, in the site schema's column names."""
    climate = pl.read_parquet(prototype_directory / "site_conditions" / "climate" / "all.parquet")
    columns = [pl.col(source).alias(target) for target, source in ERA5_PRECIPITATION_COLUMNS.items()]
    precipitation = site.select("cell_id").join(climate.select("cell_id", *columns), on="cell_id", how="left")
    missing = precipitation.filter(pl.any_horizontal(pl.all().is_null()))["cell_id"].to_list()
    if missing:
        message = f"fixture cells without ERA5 precipitation: {missing}"
        raise SystemExit(message)
    return precipitation.sort("cell_id")


def write_parquet(frame: pl.DataFrame, schema: Any, name: str, schemas: ModuleType) -> None:
    """Conform to the engine's schema and write a small zstd parquet fixture."""
    schemas.conform(frame, schema, name).write_parquet(FIXTURE_DIRECTORY / f"{name}.parquet", compression="zstd")


def main(prototype_directory: Path) -> None:
    """Write the fixture tables, both precipitation declarations (v0 PRISM, production ERA5) and the role manifest."""
    schemas = load_engine_module("schemas")
    prototype = load_prototype(prototype_directory)
    traits = pl.read_parquet(prototype_directory / "join" / "species_traits.parquet")
    guides = guide_rows(prototype, traits)
    named_ids = guides.select(pl.col("matched_plant_ids").explode(empty_as_null=True)).to_series().drop_nulls()
    named = set(named_ids.to_list())
    species = species_rows(traits, named)
    frozen = {region: pl.read_parquet(prototype_directory / "join" / f"{region}_cells.parquet") for region in REGIONS}
    boise, boise_taxa = boise_roles(prototype, frozen["boise"])
    bend, bend_taxa = bend_roles(prototype, frozen["bend"])
    roles = {"boise": boise, "bend": bend, "corvallis": corvallis_roles(frozen["corvallis"])}
    cells = {region: chosen_cells(frozen[region], list(roles[region].values())) for region in REGIONS}
    selected = pl.concat([frozen[region].filter(pl.col("cell_id").is_in(cells[region])) for region in REGIONS])
    boise_taxa |= noxious_taxa(
        selected.filter(pl.col("pilot") == "boise"), species, prototype["exclusion_match"].binomial_key
    )
    site = selected.select([pl.col(source).alias(target) for target, source in SITE_COLUMN_SOURCES.items()])
    guild_columns = [column for column in selected.columns if column.startswith(GUILDS)]
    exclusions = pl.read_parquet(prototype_directory / "exclusions" / "exclusion.parquet").filter(
        pl.col("jurisdiction").is_in(["ID", "OR"])
    )
    FIXTURE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    write_parquet(site, schemas.SITE_CONDITIONS_SCHEMA, "site_conditions", schemas)
    write_parquet(species, schemas.SPECIES_ENVELOPE_SCHEMA, "species_envelope", schemas)
    write_parquet(guides, schemas.GUIDE_ROW_SCHEMA, "guide_rows", schemas)
    write_parquet(
        exclusions.rename({"jurisdiction": "state", "scientific_name_normalized": "scientific_name"}),
        schemas.EXCLUSION_SCHEMA,
        "exclusions",
        schemas,
    )
    write_parquet(wetland_rows(prototype, species, guides), schemas.WETLAND_LIST_SCHEMA, "wetland_list", schemas)
    expected = selected.select("cell_id", *guild_columns)
    expected.write_parquet(FIXTURE_DIRECTORY / "v0_expected_cells.parquet", compression="zstd")
    manifest = {"roles": roles, "taxa": {"boise": boise_taxa, "bend": bend_taxa}, "cells": cells}
    manifest_text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    (FIXTURE_DIRECTORY / "fixture_cells.json").write_text(manifest_text, encoding="utf-8", newline="\n")
    site_inputs_text = json.dumps(SITE_INPUT_SOURCES, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    (FIXTURE_DIRECTORY / "site_inputs.json").write_text(site_inputs_text, encoding="utf-8", newline="\n")
    era5_precipitation(prototype_directory, site).write_parquet(
        FIXTURE_DIRECTORY / "era5_precipitation.parquet", compression="zstd"
    )
    era5_source_text = json.dumps(ERA5_PRECIPITATION_SOURCE, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    (FIXTURE_DIRECTORY / "era5_precipitation_source.json").write_text(era5_source_text, encoding="utf-8", newline="\n")
    licence_rows = dict(guides.group_by("license").len().iter_rows())
    cell_counts = {region: len(cell_ids) for region, cell_ids in cells.items()}
    print(f"cells {cell_counts}; species {species.height}; guide rows {guides.height} by licence {licence_rows}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PROTOTYPE_DIRECTORY)
