"""One prepared rule engine for the batch cell build and the per-point tool; see AGENTS.md §Engine here."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

import polars as pl

from agri_data_service.warehouse.plant_suitability.applicability import (
    applicability,
    forest_woodland_site,
    supporting_rows,
)
from agri_data_service.warehouse.plant_suitability.axes import AXIS_NAMES, evaluate_pairs
from agri_data_service.warehouse.plant_suitability.config import (
    assert_rule_config,
    canonical_json,
    rule_config_fingerprint,
)
from agri_data_service.warehouse.plant_suitability.labels import (
    NO_FIRE_CLAIM,
    FireTextAllowList,
    assert_no_fire_claims,
    assert_no_fire_field_on_woody_buffer,
    find_fire_claims,
    fire_resistance_label,
    metadata_texts,
    pick_label_expression,
    served_allow_list,
)
from agri_data_service.warehouse.plant_suitability.licences import licence_gate
from agri_data_service.warehouse.plant_suitability.origin import (
    INTRODUCED_PLACEHOLDER,
    decide_origins,
    document_statements,
)
from agri_data_service.warehouse.plant_suitability.pools import (
    WOODY_GUILD,
    RegionContext,
    build_pool,
    load_guide_rows,
    matched_rows,
    prepare_exclusions,
    prepare_species,
    region_guild_counts,
    region_rows,
)
from agri_data_service.warehouse.plant_suitability.ranking import TOP_PICK_COUNT, ranked_picks
from agri_data_service.warehouse.plant_suitability.regions import CurationRegionData, pilot_region_data
from agri_data_service.warehouse.plant_suitability.schemas import (
    CELL_RECOMMENDATIONS_SCHEMA,
    GUILDS,
    NWPL_RESOLUTION_COLUMN,
    SPECIES_ENVELOPE_SCHEMA,
    conform,
    polars_schema,
)
from agri_data_service.warehouse.plant_suitability.site import (
    SiteInputProvenance,
    assert_dated_access_after,
    assert_fixture_inputs_allowed,
    assert_required_site_inputs,
    prepare_site,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    import pyarrow as pa  # type: ignore[import-untyped]

    from agri_data_service.warehouse.plant_suitability.config import RuleConfig

# Bumped on any change to what a served table says for the same inputs and rule set (AGENTS.md §Engine).
ENGINE_VERSION = "2026-10-05"
# List columns whose element order means nothing (the engine reads them as sets), and paired lists (values ->
# keys): the inputs digest sorts both, so reordering inside a list cell never changes it (review R3).
SET_LIKE_LIST_COLUMNS = (
    "synonym_names", "recorded_states", "fire_resistant_values", "matched_plant_ids", "noxious_states",
)  # fmt: skip
PAIRED_LIST_COLUMNS: Mapping[str, str] = MappingProxyType({"in_region": "applies_to_regions"})
# Per-call cell budget (measured ~127 MiB per 1,000 Boise cells, 1.40 GiB at 10,000); the producer chunks regions.
MAX_CELLS_PER_CALL = 10_000
# Per-guild (cell x taxon) pairs evaluated at once: that 1.40 GiB peak was 10,000 cells x a 63-taxon pool.
MAX_CELL_TAXON_PAIRS = 640_000
STATUS_SCORED = "scored"
STATUS_NO_REGIONAL_GUIDE = "no_regional_guide"
STATUS_LICENCE_EXCLUDED = "licence_excluded"
STATUS_NO_ELIGIBLE_TAXA = "no_eligible_taxa"
GUILD_STATUSES = (STATUS_SCORED, STATUS_NO_REGIONAL_GUIDE, STATUS_LICENCE_EXCLUDED, STATUS_NO_ELIGIBLE_TAXA)
METADATA_RULE_CONFIG = "plantgeo:rule_config"
METADATA_ENGINE_VERSION = "plantgeo:engine_version"
METADATA_INPUTS_SHA256 = "plantgeo:inputs_sha256"
METADATA_PICK_DEFINITION = "plantgeo:pick_definition"
METADATA_NO_FIRE_CLAIM = "plantgeo:no_fire_claim"
METADATA_ADMITTED_SOURCES = "plantgeo:admitted_sources"
METADATA_EXCLUDED_SOURCES = "plantgeo:excluded_sources"
METADATA_SITE_INPUTS = "plantgeo:site_inputs"
METADATA_WITHHELD_SITE_INPUTS = "plantgeo:withheld_site_inputs"
METADATA_ATTRIBUTIONS = "plantgeo:attributions"
METADATA_KEYS = (
    METADATA_RULE_CONFIG, METADATA_ENGINE_VERSION, METADATA_INPUTS_SHA256, METADATA_PICK_DEFINITION,
    METADATA_NO_FIRE_CLAIM, METADATA_ADMITTED_SOURCES, METADATA_EXCLUDED_SOURCES, METADATA_SITE_INPUTS,
    METADATA_WITHHELD_SITE_INPUTS, METADATA_ATTRIBUTIONS,
)  # fmt: skip
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
    """Conformed species, admitted guide rows (per region too), origin statements, exclusions, region data, counts."""

    species: pl.DataFrame
    loaded_rows_sha256: str
    admitted_rows: pl.DataFrame
    region_rows: pl.DataFrame
    documents: pl.DataFrame
    exclusions: pl.DataFrame
    source_ids: frozenset[str]
    excluded_sources: Mapping[str, str]
    guide_row_counts: Mapping[tuple[str, str], int]
    admitted_row_counts: Mapping[tuple[str, str], int]
    scorable_row_counts: Mapping[tuple[str, str], int]
    admitted_scorable_row_counts: Mapping[tuple[str, str], int]
    region_data: CurationRegionData


@dataclass(frozen=True)
class RegionPools:
    """One (region, state): each guild's labelled candidate pool and its status (scored, or why it is empty)."""

    candidates: Mapping[str, pl.DataFrame]
    statuses: Mapping[str, str]


@dataclass
class RegionCache:
    """The engine's only mutable state: built pools, each region's first-seen state, and the lock guarding both."""

    pools: dict[tuple[str, str], RegionPools] = field(default_factory=dict)
    region_states: dict[str, str] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


def assert_ratings_resolved_under(species: pl.DataFrame, config: RuleConfig) -> None:
    """Raise when the envelope's NWPL ratings were not resolved by resolve_wetland_ratings under this rule set."""
    expected = rule_config_fingerprint(config)
    found = sorted({str(value) for value in species[NWPL_RESOLUTION_COLUMN].to_list()})
    if set(found) - {expected}:
        message = (
            f"species envelope NWPL ratings were resolved under {found}, not {expected!r}: run "
            "resolve_wetland_ratings with this rule set first"
        )
        raise ValueError(message)


def prepare_inputs(
    species: pl.DataFrame,
    guide_rows: pl.DataFrame,
    exclusions: pl.DataFrame,
    config: RuleConfig,
    region_data: CurationRegionData,
) -> PreparedInputs:
    """Validate the reference tables once; a guide row naming a missing taxon, or an unknown reading, raises."""
    prepared_species = prepare_species(species)
    assert_ratings_resolved_under(prepared_species, config)
    every_row = load_guide_rows(guide_rows)
    named_ids = set(every_row.select(pl.col("matched_plant_ids").explode(empty_as_null=True)).to_series().drop_nulls())
    missing = named_ids - set(prepared_species["plant_id"].to_list())
    if missing:
        message = f"guide rows name PLANTS taxa missing from the species envelope: {sorted(missing)[:10]}"
        raise ValueError(message)
    rows, excluded_sources = licence_gate(every_row, config)
    return PreparedInputs(
        species=prepared_species,
        loaded_rows_sha256=canonical_rows_digest(every_row),
        admitted_rows=rows,
        region_rows=region_rows(rows, region_data.in_region_overrides(config.in_region_readings)),
        documents=document_statements(matched_rows(rows, prepared_species), config),
        exclusions=prepare_exclusions(exclusions),
        source_ids=frozenset(every_row["source_id"].to_list()),
        excluded_sources=MappingProxyType(excluded_sources),
        guide_row_counts=MappingProxyType(region_guild_counts(every_row)),
        admitted_row_counts=MappingProxyType(region_guild_counts(rows)),
        scorable_row_counts=MappingProxyType(region_guild_counts(every_row, scorable_only=True)),
        admitted_scorable_row_counts=MappingProxyType(region_guild_counts(rows, scorable_only=True)),
        region_data=region_data,
    )


def canonical_list_cells(frame: pl.DataFrame) -> pl.DataFrame:
    """The frame with each set-like list cell sorted, and each paired list reordered by its key list's order."""
    set_like = [pl.col(name).list.sort() for name in SET_LIKE_LIST_COLUMNS if name in frame.columns]
    paired = [
        expression
        for values, keys in PAIRED_LIST_COLUMNS.items()
        if {values, keys} <= set(frame.columns)
        for expression in (
            pl.col(values).list.gather(pl.col(keys).list.eval(pl.element().arg_sort())),
            pl.col(keys).list.sort(),
        )
    ]
    return frame.with_columns(*set_like, *paired)


def canonical_rows_digest(frame: pl.DataFrame) -> str:
    """sha256 of the rows as sorted JSON lines, so neither row order nor order inside a list cell changes it."""
    lines = sorted(canonical_list_cells(frame).write_ndjson().splitlines())
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def inputs_digest(inputs: PreparedInputs, site_provenance: SiteInputProvenance) -> str:
    """sha256 over the envelope, every loaded guide row, exclusions, the curation region data and site inputs."""
    # Every loaded row, not only the admitted ones: rows the licence gate drops still decide guild statuses.
    parts = {
        "species_envelope": canonical_rows_digest(inputs.species.select(SPECIES_ENVELOPE_SCHEMA.names)),
        "guide_rows": inputs.loaded_rows_sha256,
        "exclusions": canonical_rows_digest(inputs.exclusions),
        "region_data": inputs.region_data.digest_value(),
        "site_inputs": site_provenance.declaration_json(),
    }
    return hashlib.sha256(canonical_json(parts).encode("utf-8")).hexdigest()


def admitted_sources(inputs: PreparedInputs) -> dict[str, dict[str, str]]:
    """Each admitted source_id with its citation short name and licence id (one of each, checked at load)."""
    sources = inputs.admitted_rows.select("source_id", "source_short_name", "license").unique().sort("source_id")
    return {
        source_id: {"short_name": short_name, "licence": licence}
        for source_id, short_name, licence in sources.iter_rows()
    }


def registered_texts(inputs: PreparedInputs) -> list[str]:
    """What egress may serve with fire words in it: admitted short names and loaded source ids, never config text."""
    short_names = inputs.admitted_rows["source_short_name"].unique().to_list()
    return [*short_names, *sorted(inputs.source_ids)]


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


def guild_status(prepared: PreparedInputs, region: str, guild: str, pool_size: int) -> str:
    """Scored, or why the pool is empty: no guide rows, the gate removed every scorable row, or no taxon eligible."""
    key = (region, guild)
    if pool_size:
        return STATUS_SCORED
    if not prepared.guide_row_counts.get(key):
        return STATUS_NO_REGIONAL_GUIDE
    gate_removed_every_scorable_row = bool(prepared.scorable_row_counts.get(key)) and not (
        prepared.admitted_scorable_row_counts.get(key)
    )
    if not prepared.admitted_row_counts.get(key) or gate_removed_every_scorable_row:
        return STATUS_LICENCE_EXCLUDED
    return STATUS_NO_ELIGIBLE_TAXA


def evaluate_guild(cells: pl.DataFrame, candidates: pl.DataFrame, config: RuleConfig) -> pl.DataFrame:
    """Every (cell, candidate) pair with its axes, counts, margin and rank helpers."""
    return evaluate_pairs(cells, candidates, applicability(cells, candidates), config)


def with_pick_labels(
    picks: pl.DataFrame, cells: pl.DataFrame, candidates: pl.DataFrame, guild: str, config: RuleConfig
) -> pl.DataFrame:
    """Picks with the label naming the guide rows that carry each one at its cell (and, if set, unchecked axes)."""
    support = supporting_rows(picks.select("cell_id", "plant_id", "axis_source_applicability"), cells, candidates)
    label = pick_label_expression(guild, label_unchecked_axes=config.label_unchecked_axes)
    return picks.join(support, on=["cell_id", "plant_id"], how="left").with_columns(label.alias("pick_label"))


def empty_pool_cells(cell_ids: pl.DataFrame, guild: str, status: str) -> pl.DataFrame:
    """A guild with no candidates in the region: status says why, pool size 0 and every count null, not zero."""
    return cell_ids.select(
        "cell_id",
        pl.lit(status).alias(f"{guild}_status"),
        pl.lit(0, dtype=pl.UInt32).alias(f"{guild}_pool_size"),
        *[pl.lit(None, dtype=pl.UInt32).alias(f"{guild}_{suffix}") for suffix in COUNT_SUFFIXES],
        *[pl.lit(None, dtype=dtype).alias(f"{guild}_{suffix}") for suffix, dtype in TOP_THREE_SUFFIXES],
    )


def scored_guild_cells(
    scored: pl.DataFrame, candidates: pl.DataFrame, guild: str, status: str, config: RuleConfig
) -> pl.DataFrame:
    """Per scored cell of one chunk: status, pool size, pick counts, top-3 names / common names / labels, ties."""
    cell_ids = scored.select("cell_id")
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
    top = with_pick_labels(picks.filter(pl.col("rank_index") < TOP_PICK_COUNT), scored, candidates, guild, config)
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
            pl.lit(status).alias(f"{guild}_status"),
            pl.lit(candidates.height, dtype=pl.UInt32).alias(f"{guild}_pool_size"),
            *[pl.col(column).fill_null(0).cast(pl.UInt32) for column in counts],
        )
    )


def guild_cells(
    scored: pl.DataFrame, candidates: pl.DataFrame, guild: str, status: str, config: RuleConfig
) -> pl.DataFrame:
    """Per scored cell, one guild's columns, evaluated in cell chunks of at most MAX_CELL_TAXON_PAIRS pairs."""
    if candidates.height == 0:
        return empty_pool_cells(scored.select("cell_id"), guild, status)
    chunk_size = max(1, MAX_CELL_TAXON_PAIRS // candidates.height)
    chunks = [scored.slice(offset, chunk_size) for offset in range(0, scored.height, chunk_size)]
    return pl.concat(
        [scored_guild_cells(chunk, candidates, guild, status, config) for chunk in chunks], how="vertical_relaxed"
    )


def conform_candidates(frame: pl.DataFrame) -> pl.DataFrame:
    """The per-point frame's columns in order, cast to CANDIDATE_OUTPUT_SCHEMA."""
    return frame.select([pl.col(name).cast(dtype) for name, dtype in CANDIDATE_OUTPUT_SCHEMA.items()])


def ranked_candidates(cell: pl.DataFrame, candidates: pl.DataFrame, guild: str, config: RuleConfig) -> pl.DataFrame:
    """Every pool taxon at one scored cell: picks in rank order with labels, then non-picks by name."""
    evaluated = evaluate_guild(cell, candidates, config)
    picks = with_pick_labels(ranked_picks(evaluated, guild), cell, candidates, guild, config)
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


def with_output_columns(frame: pl.DataFrame) -> pl.DataFrame:
    """The frame plus a typed null column for every CELL_RECOMMENDATIONS_SCHEMA column it lacks (withheld regions)."""
    absent = [
        (name, dtype) for name, dtype in polars_schema(CELL_RECOMMENDATIONS_SCHEMA).items() if name not in frame.columns
    ]
    return frame.with_columns([pl.lit(None, dtype=dtype).alias(name) for name, dtype in absent])


def region_state_pairs(cells: pl.DataFrame) -> dict[str, str]:
    """Each region's one state in the frame; a region spanning two states raises (pools are per region and state)."""
    states = cells.group_by("region", maintain_order=True).agg(pl.col("state").unique().sort())
    spanning = [f"region {region} spans states {values}" for region, values in states.iter_rows() if len(values) != 1]
    if spanning:
        message = f"{'; '.join(spanning)}; pools are decided per (region, state)"
        raise ValueError(message)
    return {region: values[0] for region, values in states.iter_rows()}


@dataclass(frozen=True, eq=False)
class PreparedEngine:
    """A rule set with its reference tables validated once; each region's pools are built on first use and cached."""

    config: RuleConfig
    inputs: PreparedInputs
    site_provenance: SiteInputProvenance
    allow_list: FireTextAllowList = field(init=False, repr=False)
    served_metadata: Mapping[str, str] = field(init=False, repr=False)
    cache: RegionCache = field(init=False, repr=False, compare=False, default_factory=RegionCache)

    def __post_init__(self) -> None:
        """Refuse a missing, withheld or stale required site input or a disallowed fixture, then build the metadata."""
        assert_required_site_inputs(self.site_provenance, self.config)
        assert_fixture_inputs_allowed(self.site_provenance, self.config)
        assert_dated_access_after(self.site_provenance, self.config)
        withheld = self.site_provenance.withheld_groups(self.config)
        metadata = {
            METADATA_RULE_CONFIG: rule_config_fingerprint(self.config),
            METADATA_ENGINE_VERSION: ENGINE_VERSION,
            METADATA_INPUTS_SHA256: inputs_digest(self.inputs, self.site_provenance),
            METADATA_PICK_DEFINITION: self.config.pick_definition,
            METADATA_NO_FIRE_CLAIM: NO_FIRE_CLAIM,
            METADATA_ADMITTED_SOURCES: canonical_json(admitted_sources(self.inputs)),
            METADATA_EXCLUDED_SOURCES: canonical_json(self.inputs.excluded_sources),
            METADATA_SITE_INPUTS: self.site_provenance.declaration_json(),
            METADATA_WITHHELD_SITE_INPUTS: canonical_json(withheld),
            # Stated on its own key so a renderer can show it prominently, as PRISM's terms ask (AGENTS.md §Licences).
            METADATA_ATTRIBUTIONS: canonical_json(self.site_provenance.attributions(self.config)),
        }
        allow_list = served_allow_list(registered_texts(self.inputs))
        assert_no_fire_claims(pl.DataFrame(), metadata, allow_list)
        # A claim with no fire-family stem ("these hedges create defensible space") passes the stem guard above.
        claims = find_fire_claims(metadata_texts(metadata))
        if claims:
            message = f"{len(claims)} metadata texts claim a fire effect, e.g. {claims[0]!r}"
            raise ValueError(message)
        object.__setattr__(self, "allow_list", allow_list)
        object.__setattr__(self, "served_metadata", MappingProxyType(metadata))

    @property
    def excluded_sources(self) -> Mapping[str, str]:
        """Each guide source_id the licence gate dropped, with its licence id (empty without a gate)."""
        return self.inputs.excluded_sources

    @property
    def pools_by_region(self) -> Mapping[tuple[str, str], RegionPools]:
        """A read-only view of the pools built so far, keyed by (region, state)."""
        return MappingProxyType(self.cache.pools)

    def metadata(self) -> dict[str, str]:
        """What every table this engine serves carries (METADATA_KEYS), built once from this prepared state."""
        return dict(self.served_metadata)

    def guard_served_table(self, table: pl.DataFrame, labels_by_guild: Mapping[str, Iterable[str | None]]) -> None:
        """Refuse a table with a fire word outside the allow-list, or whose woody-buffer text carries the fire field."""
        assert_no_fire_claims(table, None, self.allow_list)
        assert_no_fire_field_on_woody_buffer(labels_by_guild)

    def assert_known_regions(self, regions: Iterable[str]) -> None:
        """Raise on a region no guide row names (counted before the licence gate), e.g. a typo such as 'Boise'."""
        known = {region for region, _ in self.inputs.guide_row_counts}
        unknown = sorted(set(regions) - known)
        if unknown:
            message = f"unknown regions {unknown}: no guide row names them (known: {sorted(known)})"
            raise ValueError(message)

    def record_region_states(self, pairs: Mapping[str, str]) -> None:
        """Pin each region to the state it was first served with; a later call pairing it with another raises."""
        with self.cache.lock:
            recorded = self.cache.region_states
            changed = {
                region: (recorded[region], state)
                for region, state in pairs.items()
                if recorded.get(region, state) != state
            }
            if changed:
                message = f"regions served before with another state (first, now): {changed}; pools are per state"
                raise ValueError(message)
            recorded.update(pairs)

    def prepare_site(self, site: pl.DataFrame) -> pl.DataFrame:
        """Site conditions through the load checks, withholding and the release's site classes; bad regions raise."""
        forest_woodland = forest_woodland_site(self.inputs.region_data.forest_woodland_mlras())
        cells = prepare_site(site, self.site_provenance, self.config).with_columns(forest_woodland)
        self.assert_known_regions(cells["region"].unique().to_list())
        self.record_region_states(region_state_pairs(cells))
        return cells

    def build_region_pools(self, region: str, state: str) -> RegionPools:
        """The (region, state) pools and statuses; a state with no noxious-list rows raises unless declared empty."""
        listed_states = set(self.inputs.exclusions["state"].unique().to_list())
        if state not in listed_states | self.config.declared_empty_exclusion_states:
            message = (
                f"no state noxious-list rows for {state} (region {region}): supply them, or declare the state in "
                "RuleConfig.declared_empty_exclusion_states"
            )
            raise ValueError(message)
        candidates = region_candidates(self.inputs, region, state, self.config)
        statuses = {guild: guild_status(self.inputs, region, guild, pool.height) for guild, pool in candidates.items()}
        return RegionPools(MappingProxyType(candidates), MappingProxyType(statuses))

    def region_pools(self, region: str, state: str) -> RegionPools:
        """The (region, state) pools, built once under the cache lock (so concurrent cold calls build them once)."""
        key = (region, state)
        cached = self.cache.pools.get(key)
        if cached is not None:
            return cached
        with self.cache.lock:
            if key not in self.cache.pools:
                self.cache.pools[key] = self.build_region_pools(region, state)
            return self.cache.pools[key]

    def warm(self, region_states: Mapping[str, str]) -> None:
        """Build every named region's pools before serving (region -> state), pinning each pairing."""
        self.assert_known_regions(region_states)
        self.record_region_states(region_states)
        for region, state in region_states.items():
            self.region_pools(region, state)

    def region_table(self, cells: pl.DataFrame) -> pl.DataFrame:
        """One region's cells (one state, checked at load) with every guild's columns; withheld cells stay null."""
        table = cells.select("cell_id", "region", "scoring_null_reason")
        scored = cells.filter(pl.col("scoring_null_reason").is_null())
        if scored.height == 0:
            return table
        pools = self.region_pools(cells["region"][0], cells["state"][0])
        for guild in GUILDS:
            columns = guild_cells(scored, pools.candidates[guild], guild, pools.statuses[guild], self.config)
            table = table.join(columns, on="cell_id", how="left")
        return table

    def evaluate_cells(self, site: pl.DataFrame) -> pl.DataFrame:
        """One row per input cell (input order): per-guild status, pool size, pick counts and the labelled top three."""
        if site.height > MAX_CELLS_PER_CALL:
            message = f"{site.height} cells exceed the per-call budget of {MAX_CELLS_PER_CALL}; evaluate them in chunks"
            raise ValueError(message)
        cells = self.prepare_site(site)
        regions = cells["region"].unique(maintain_order=True).to_list()
        tables = [self.region_table(cells.filter(pl.col("region") == region)) for region in regions]
        if tables:
            combined = pl.concat(tables, how="diagonal_relaxed")
            ordered = cells.select("cell_id").join(combined, on="cell_id", how="left", maintain_order="left")
            served = conform(with_output_columns(ordered), CELL_RECOMMENDATIONS_SCHEMA, "cell recommendations")
        else:
            served = pl.DataFrame(schema=polars_schema(CELL_RECOMMENDATIONS_SCHEMA))
        labels_by_guild = {
            guild: served.select(pl.col(f"{guild}_top3_labels").explode(empty_as_null=True)).to_series().to_list()
            for guild in GUILDS
        }
        self.guard_served_table(served, labels_by_guild)
        return served

    def layer_table(self, site: pl.DataFrame) -> pa.Table:
        """`evaluate_cells` as Arrow, cast to CELL_RECOMMENDATIONS_SCHEMA, carrying this engine's `metadata`."""
        cells = self.evaluate_cells(site)
        return cells.to_arrow().cast(CELL_RECOMMENDATIONS_SCHEMA).replace_schema_metadata(self.metadata())

    def candidates_for_cell(self, cell_site: pl.DataFrame) -> pl.DataFrame:
        """Per guild, every pool taxon ranked at one cell through the batch path, or one row with the batch status."""
        cell = self.prepare_site(cell_site)
        if cell.height != 1:
            message = f"candidates_for_cell takes exactly one site row, got {cell.height}"
            raise ValueError(message)
        scoring_null_reason = cell["scoring_null_reason"][0]
        if scoring_null_reason is not None:
            rows = [guild_status_row(guild, None, scoring_null_reason) for guild in GUILDS]
        else:
            pools = self.region_pools(cell["region"][0], cell["state"][0])
            rows = [
                ranked_candidates(cell, pools.candidates[guild], guild, self.config)
                if pools.candidates[guild].height
                else guild_status_row(guild, pools.statuses[guild], None)
                for guild in GUILDS
            ]
        ranked = pl.concat(rows, how="vertical")
        labels_by_guild = {guild: ranked.filter(pl.col("guild") == guild)["pick_label"].to_list() for guild in GUILDS}
        woody = ranked.filter(pl.col("guild") == WOODY_GUILD)
        labels_by_guild[WOODY_GUILD] = [*labels_by_guild[WOODY_GUILD], *woody["origin_label"].to_list()]
        self.guard_served_table(ranked, labels_by_guild)
        return ranked

    def candidates_with_metadata(self, cell_site: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, str]]:
        """`candidates_for_cell` with the metadata of the same prepared state, for a per-point response."""
        return self.candidates_for_cell(cell_site), self.metadata()


def prepare(  # noqa: PLR0913 - the three reference tables, the rule set and the two declarations.
    species: pl.DataFrame,
    guide_rows: pl.DataFrame,
    exclusions: pl.DataFrame,
    config: RuleConfig,
    site_provenance: SiteInputProvenance | None = None,
    region_data: CurationRegionData | None = None,
) -> PreparedEngine:
    """Validate the rule set, reference tables, region data and site provenance once; both seams serve from that."""
    assert_rule_config(config)
    provenance = SiteInputProvenance({}) if site_provenance is None else site_provenance
    # Read per call, never at import: the pilot release unless the caller passes the published one.
    regions = pilot_region_data() if region_data is None else region_data
    return PreparedEngine(config, prepare_inputs(species, guide_rows, exclusions, config, regions), provenance)


def evaluate_cells(  # noqa: PLR0913 - the site frame plus every argument prepare() takes.
    site: pl.DataFrame,
    species: pl.DataFrame,
    guide_rows: pl.DataFrame,
    exclusions: pl.DataFrame,
    config: RuleConfig,
    site_provenance: SiteInputProvenance | None = None,
    region_data: CurationRegionData | None = None,
) -> pl.DataFrame:
    """`prepare(...).evaluate_cells(site)` for a one-off call."""
    return prepare(species, guide_rows, exclusions, config, site_provenance, region_data).evaluate_cells(site)


def candidates_for_cell(  # noqa: PLR0913 - the site frame plus every argument prepare() takes.
    cell_site: pl.DataFrame,
    species: pl.DataFrame,
    guide_rows: pl.DataFrame,
    exclusions: pl.DataFrame,
    config: RuleConfig,
    site_provenance: SiteInputProvenance | None = None,
    region_data: CurationRegionData | None = None,
) -> pl.DataFrame:
    """`prepare(...).candidates_for_cell(cell_site)` for a one-off call."""
    arguments = (species, guide_rows, exclusions, config, site_provenance, region_data)
    return prepare(*arguments).candidates_for_cell(cell_site)
