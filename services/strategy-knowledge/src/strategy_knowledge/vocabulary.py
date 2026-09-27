"""Closed vocabularies, goals, NRCS resource concerns and evidence ranks: the single source of truth.

Every enum is a `Literal` so the MCP input schemas publish the allowed values to the calling model; the
value tuples are derived from the literals, never retyped. See AGENTS.md section "Vocabulary".
"""

from typing import Final, Literal, get_args

SCHEMA_VERSION: Final = "strategy-knowledge/1"
CLAIM_TIER: Final = "literature_grounded"

Goal = Literal[
    "soil_health",
    "water_management",
    "carbon_sequestration",
    "erosion_control",
    "wildfire_resilience",
    "biodiversity_habitat",
    "nutrient_cycling",
    "contaminant_remediation",
    "biomass_circularity",
    "drought_climate_adaptation",
    "productivity_yield",
    "air_quality",
]
GoalState = Literal["stated", "inferred"]
FirePhase = Literal["pre_fire", "during_fire", "post_fire_emergency", "post_fire_recovery", "long_term_resilience"]
Category = Literal[
    "fuel_reduction",
    "biomass_utilization",
    "biochar_production",
    "soil_amendment",
    "composting",
    "mulching",
    "post_fire_stabilization",
    "erosion_control",
    "hydrophobicity_treatment",
    "revegetation",
    "invasive_weed_control",
    "grazing_management",
    "cover_cropping",
    "tillage_management",
    "water_management",
    "defensible_space",
    "infrastructure_protection",
    "monitoring_assessment",
    "nutrient_management",
    "contaminant_remediation",
    "other",
]
LandUse = Literal[
    "cropland",
    "rangeland",
    "pasture",
    "forest",
    "woodland",
    "orchard_vineyard",
    "garden_residential",
    "wildland_urban_interface",
    "riparian",
    "mine_or_degraded_land",
    "general",
]
#: DESIGN.md v1.1 (pilot join, 2026-09-26) added the ten values from us_midwest through oceania.
Region = Literal[
    "pnw_westside",
    "pnw_inland",
    "northern_rockies",
    "great_basin_high_desert",
    "southern_rockies",
    "california",
    "southwest",
    "great_plains_texas",
    "us_midwest",
    "us_southeast",
    "us_northeast",
    "alaska",
    "canada",
    "latin_america",
    "europe",
    "asia",
    "africa",
    "oceania",
    "north_america_general",
    "global",
    "general",
]
SoilCondition = Literal[
    "hydrophobic",
    "burned_high_severity",
    "burned_low_moderate_severity",
    "sandy_coarse",
    "clay_heavy",
    "alkaline",
    "acidic",
    "low_organic_matter",
    "compacted",
    "steep_slope",
    "erodible",
    "saline_sodic",
    "contaminated",
    "nutrient_poor",
    "droughty",
]
Scale = Literal["garden_plot", "field", "farm_ranch", "stand_forest_unit", "landscape_watershed"]
Level = Literal["low", "medium", "high"]
EvidenceStrength = Literal[
    "ai_synthesis_only",
    "anecdotal_or_news",
    "expert_guidance",
    "field_trial_or_case_study",
    "peer_reviewed_experiment",
    "review_or_meta_analysis",
]
SourceType = Literal[
    "extension_guidance",
    "peer_reviewed_study",
    "government_report",
    "news_article",
    "nonprofit_guidance",
    "book_manual",
    "research_project_page",
    "ai_synthesis",
]
WildfireRelevance = Literal["direct", "indirect", "background"]
ContentType = Literal[
    "practice_guidance",
    "research_finding",
    "mechanism_explanation",
    "quantitative_data",
    "case_study",
    "site_condition_guidance",
    "policy_economics",
    "background_context",
    "definitions_glossary",
    "methods",
    "survey_responses",
    "references",
    "front_matter",
    "noise",
]
Relevance = Literal["core", "supporting", "peripheral", "off_topic"]
StudyType = Literal[
    "field_experiment",
    "observational_field",
    "lab_or_greenhouse",
    "model_simulation",
    "meta_analysis",
    "review",
    "survey",
    "case_report",
    "expert_opinion",
]
Direction = Literal["increase", "decrease", "no_effect", "mixed", "conditional", "not_applicable"]
Facet = Literal["overview", "how_to", "fit", "outcomes"]
VariableRole = Literal["driver", "response"]

#: Regions inside North America; a filter naming one also admits `north_america_general` (AGENTS.md "Filters").
NORTH_AMERICAN_REGIONS: Final[frozenset[str]] = frozenset(
    {
        "pnw_westside",
        "pnw_inland",
        "northern_rockies",
        "great_basin_high_desert",
        "southern_rockies",
        "california",
        "southwest",
        "great_plains_texas",
        "us_midwest",
        "us_southeast",
        "us_northeast",
        "alaska",
        "canada",
        "north_america_general",
    },
)
GLOBAL_REGION: Final = "global"
NORTH_AMERICA_GENERAL_REGION: Final = "north_america_general"

#: Facet -> the label its embedded document names it by: `"<strategy name> (<label>): <facet text>"`.
FACET_LABELS: Final[dict[str, str]] = {
    "overview": "overview",
    "how_to": "how to",
    "fit": "fit",
    "outcomes": "outcomes",
}

GOALS: Final[tuple[str, ...]] = get_args(Goal)
GOAL_STATES: Final[tuple[str, ...]] = get_args(GoalState)
#: The stronger goal state: the text claims the outcome ("inferred" is a reasonable application it does not claim).
STATED_GOAL_STATE: Final = "stated"
FIRE_PHASES: Final[tuple[str, ...]] = get_args(FirePhase)
CATEGORIES: Final[tuple[str, ...]] = get_args(Category)
LAND_USES: Final[tuple[str, ...]] = get_args(LandUse)
REGIONS: Final[tuple[str, ...]] = get_args(Region)
SOIL_CONDITIONS: Final[tuple[str, ...]] = get_args(SoilCondition)
SCALES: Final[tuple[str, ...]] = get_args(Scale)
LEVELS: Final[tuple[str, ...]] = get_args(Level)
EVIDENCE_STRENGTHS: Final[tuple[str, ...]] = get_args(EvidenceStrength)
SOURCE_TYPES: Final[tuple[str, ...]] = get_args(SourceType)
WILDFIRE_RELEVANCES: Final[tuple[str, ...]] = get_args(WildfireRelevance)
CONTENT_TYPES: Final[tuple[str, ...]] = get_args(ContentType)
RELEVANCES: Final[tuple[str, ...]] = get_args(Relevance)
STUDY_TYPES: Final[tuple[str, ...]] = get_args(StudyType)
DIRECTIONS: Final[tuple[str, ...]] = get_args(Direction)
FACETS: Final[tuple[str, ...]] = get_args(Facet)
VARIABLE_ROLES: Final[tuple[str, ...]] = get_args(VariableRole)

#: DESIGN.md section 4: the `$gte` scale behind `min_evidence`.
EVIDENCE_RANK: Final[dict[str, int]] = {strength: rank for rank, strength in enumerate(EVIDENCE_STRENGTHS, start=1)}

#: DESIGN.md section 3: each goal's NRCS resource concerns.
NRCS_RESOURCE_CONCERNS: Final[dict[str, tuple[str, ...]]] = {
    "soil_health": ("Soil",),
    "water_management": ("Water",),
    "carbon_sequestration": ("Air", "Soil"),
    "erosion_control": ("Soil", "Water"),
    "wildfire_resilience": ("Plants", "Human"),
    "biodiversity_habitat": ("Animals", "Plants"),
    "nutrient_cycling": ("Soil", "Water"),
    "contaminant_remediation": ("Soil", "Water"),
    "biomass_circularity": ("Energy", "Plants"),
    "drought_climate_adaptation": ("Water", "Plants"),
    "productivity_yield": ("Plants",),
    "air_quality": ("Air",),
}

#: Vocabulary name -> allowed values; what `list_facets` publishes and the validator checks against.
VOCABULARIES: Final[dict[str, tuple[str, ...]]] = {
    "goal": GOALS,
    "goal_state": GOAL_STATES,
    "fire_phase": FIRE_PHASES,
    "category": CATEGORIES,
    "land_use": LAND_USES,
    "region": REGIONS,
    "soil_conditions": SOIL_CONDITIONS,
    "scale": SCALES,
    "level": LEVELS,
    "evidence_strength": EVIDENCE_STRENGTHS,
    "source_type": SOURCE_TYPES,
    "wildfire_relevance": WILDFIRE_RELEVANCES,
    "content_type": CONTENT_TYPES,
    "relevance": RELEVANCES,
    "study_type": STUDY_TYPES,
    "direction": DIRECTIONS,
    "facet": FACETS,
}

#: strategy_schema.md rule 3: the AI-generated source may find strategies but never be their only citation.
AI_SYNTHESIS_SOURCE_PREFIX: Final = "24-"

#: The land_use / region value every filter also admits (DESIGN.md section 10).
GENERAL_VALUE: Final = "general"


def broader_regions(region: str) -> tuple[str, ...]:
    """Regions whose records also apply to `region`: general, global, and north_america_general inside NA."""
    broader = [GENERAL_VALUE, GLOBAL_REGION]
    if region in NORTH_AMERICAN_REGIONS:
        broader.append(NORTH_AMERICA_GENERAL_REGION)
    return tuple(broader)


#: Content types and relevance levels `search_passages` leaves out unless asked for explicitly.
DEFAULT_EXCLUDED_CONTENT_TYPES: Final[tuple[str, ...]] = ("noise",)
DEFAULT_EXCLUDED_RELEVANCE: Final[tuple[str, ...]] = ("off_topic",)

#: Excerpt word bounds (DESIGN.md section 5 says 8-25; the corpus was validated at >= 6, AGENTS.md "Validation").
MAXIMUM_EXCERPT_WORDS: Final = 25
MINIMUM_EXCERPT_WORDS: Final = 6
CONTRACT_MINIMUM_EXCERPT_WORDS: Final = 8

#: Default review_state when a record carries none; see AGENTS.md section "review_state".
DEFAULT_REVIEW_STATE: Final = "machine_extracted"
PASSAGE_REVIEW_STATE: Final = "verbatim_source_text"
