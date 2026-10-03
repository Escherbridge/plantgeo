"""Arrow schemas for the engine's inputs and its one-row-per-cell output; see AGENTS.md §Schemas here."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from collections.abc import Mapping

GUILDS = ("greenstrip", "post_fire_restoration", "hedgerow_buffer")
# Two-letter USPS codes: the 50 states, DC and the inhabited territories (state noxious lists are keyed by them).
USPS_STATE_CODES = frozenset((
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
    "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC", "AS", "GU", "MP", "PR", "VI",
))  # fmt: skip
# PLANTS measures in original and metric units, and the ordinal classes (None 0, Low 1, Medium 2, High 3; Yes 1).
ENVELOPE_MEASURES = (
    "min_temp_f", "min_temp_c", "precip_min_in", "precip_min_mm", "precip_max_in", "precip_max_mm",
    "ph_min", "ph_max", "frost_free_days_min", "root_depth_min_in", "root_depth_min_cm", "height_mature_ft",
)  # fmt: skip
ENVELOPE_CLASSES = (
    "salinity_tolerance", "caco3_tolerance", "anaerobic_tolerance", "drought_tolerance", "fire_tolerance",
    "seedling_vigor", "commercial_availability", "texture_coarse", "texture_medium", "texture_fine",
)  # fmt: skip
# The rule_config fingerprint resolve_wetland_ratings stamps on the envelope; prepare refuses any other.
NWPL_RESOLUTION_COLUMN = "nwpl_resolved_under"


def fields(arrow_type: pa.DataType, *names: str, nullable: bool = True) -> list[pa.Field]:
    """One Arrow field per name, all of one type."""
    return [pa.field(name, arrow_type, nullable=nullable) for name in names]


TEXT, LABELS, COUNT = pa.string(), pa.list_(pa.string()), pa.uint32()

SPECIES_ENVELOPE_SCHEMA = pa.schema(
    [
        *fields(pa.int64(), "plant_id", nullable=False),
        *fields(TEXT, "accepted_name", nullable=False),
        *fields(TEXT, "common_name", "family_name", "duration", "native_status_l48"),
        *fields(LABELS, "synonym_names", "recorded_states", "fire_resistant_values"),
        *fields(pa.float64(), *ENVELOPE_MEASURES),
        *fields(pa.int64(), *ENVELOPE_CLASSES),
        *fields(TEXT, "nwpl_aw", "nwpl_wmvc"),
        *fields(TEXT, "nwpl_route", nullable=False),
        *fields(TEXT, NWPL_RESOLUTION_COLUMN),
    ]
)

GUIDE_ROW_SCHEMA = pa.schema(
    [
        *fields(pa.int64(), "guide_row_id", nullable=False),
        *fields(TEXT, "source_id", "source_short_name", "guild", "origin_scope", nullable=False),
        *fields(TEXT, "match_route", "plant_role", nullable=False),
        *fields(TEXT, "license", nullable=False),
        *fields(TEXT, "page", "applicability_confidence", "listed_scientific_name", "listed_common_name"),
        *fields(TEXT, "habitat_qualifier", "origin_per_source"),
        *fields(LABELS, "applies_to_regions", nullable=False),
        *fields(pa.list_(pa.bool_()), "in_region", nullable=False),
        *fields(pa.list_(pa.int64()), "matched_plant_ids"),
        *fields(pa.float64(), "min_precip_in", "max_precip_in"),
        *fields(pa.bool_(), "nativity_conflict", nullable=False),
        *fields(LABELS, "noxious_states"),
    ]
)

SITE_CONDITIONS_SCHEMA = pa.schema(
    [
        *fields(TEXT, "cell_id", "region", "state", "nwpl_region", nullable=False),
        *fields(TEXT, "scoring_null_reason", "mlra", "site_texture_group"),
        *fields(TEXT, "restriction_status", "drainage_class", "hydric_rating"),
        *fields(pa.float64(), "record_min_c", "median_frost_free_days", "frost_free_days_station_bias"),
        *fields(pa.float64(), "mean_annual_precip_mm", "dry_year_precip_mm", "site_ph", "restriction_depth_cm"),
        *fields(pa.float64(), "ec_ds_per_m", "caco3_percent"),
    ]
)

# Every site measurement column, grouped by the one source that produced it (the prototype's site_conditions/).
SITE_INPUT_GROUPS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "soil_survey": (
            "drainage_class", "hydric_rating", "restriction_status", "restriction_depth_cm", "ec_ds_per_m",
            "caco3_percent", "mlra", "scoring_null_reason",
        ),
        "soil_ph_texture": ("site_ph", "site_texture_group"),
        "cold": ("record_min_c",),
        "frost_free": ("median_frost_free_days",),
        "frost_free_station_bias": ("frost_free_days_station_bias",),
        "precipitation": ("mean_annual_precip_mm", "dry_year_precip_mm"),
    }
)  # fmt: skip
# Keys and location classes, not measurements: they belong to no site-input group.
STRUCTURAL_SITE_COLUMNS = ("cell_id", "region", "state", "nwpl_region")


def site_column_partition_problems() -> list[str]:
    """Site columns in no group or in two, and group columns the site schema lacks (empty when partitioned)."""
    placed = [*STRUCTURAL_SITE_COLUMNS, *(column for columns in SITE_INPUT_GROUPS.values() for column in columns)]
    unplaced = sorted(set(SITE_CONDITIONS_SCHEMA.names) - set(placed))
    repeated = sorted({column for column in placed if placed.count(column) > 1})
    unknown = sorted(set(placed) - set(SITE_CONDITIONS_SCHEMA.names))
    return [
        f"{problem}: {columns}"
        for problem, columns in (("in no group", unplaced), ("in two groups", repeated), ("not site columns", unknown))
        if columns
    ]


if site_column_partition_problems():
    message = f"site input groups do not partition the site columns: {site_column_partition_problems()}"
    raise ImportError(message)

EXCLUSION_SCHEMA = pa.schema(
    [
        *fields(TEXT, "state", "scientific_name", nullable=False),
        *fields(TEXT, "listed_name", "common_name", "name_ambiguity_flag"),
    ]
)

WETLAND_LIST_SCHEMA = pa.schema([*fields(TEXT, "nwpl_name", nullable=False), *fields(TEXT, "nwpl_aw", "nwpl_wmvc")])

GUILD_OUTPUT_FIELDS = (
    ("status", TEXT), ("pool_size", COUNT), ("count", COUNT), ("count_fully_known", COUNT), ("count_in_region", COUNT),
    ("count_uncertain_frost_free", COUNT), ("count_introduced_flagged", COUNT), ("top3", TEXT),
    ("top3_common_names", TEXT), ("top3_labels", LABELS), ("unknown_axes_top3", TEXT), ("rank1_tie_count", COUNT),
    ("rank1_tied_names", TEXT),
)  # fmt: skip

CELL_RECOMMENDATIONS_SCHEMA = pa.schema(
    [
        *fields(TEXT, "cell_id", "region", nullable=False),
        *fields(TEXT, "scoring_null_reason"),
        *[pa.field(f"{guild}_{suffix}", arrow_type) for guild in GUILDS for suffix, arrow_type in GUILD_OUTPUT_FIELDS],
    ]
)


def polars_schema(schema: pa.Schema) -> pl.Schema:
    """The Polars dtypes of an Arrow schema."""
    return pl.DataFrame(schema.empty_table()).schema


def conform(frame: pl.DataFrame, schema: pa.Schema, table_name: str) -> pl.DataFrame:
    """Select and cast `frame` to `schema`; a missing column or a null in a required column raises."""
    missing = [name for name in schema.names if name not in frame.columns]
    if missing:
        message = f"{table_name} is missing columns {missing}"
        raise ValueError(message)
    conformed = frame.select([pl.col(name).cast(dtype) for name, dtype in polars_schema(schema).items()])
    required_nulls = [field.name for field in schema if not field.nullable and conformed[field.name].null_count()]
    if required_nulls:
        message = f"{table_name} has nulls in required columns {required_nulls}"
        raise ValueError(message)
    return conformed


def assert_vocabularies(frame: pl.DataFrame, vocabularies: Mapping[str, frozenset[str]], table_name: str) -> None:
    """Raise, naming the column and values, when a fixed-vocabulary column holds a value outside it (nulls pass)."""
    for column, vocabulary in vocabularies.items():
        unknown = set(frame[column].drop_nulls().unique().to_list()) - vocabulary
        if unknown:
            message = f"{table_name} carry unknown {column} values {sorted(unknown)}"
            raise ValueError(message)
