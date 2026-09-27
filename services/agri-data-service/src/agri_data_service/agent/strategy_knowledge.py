"""Client, typed refusals and bounded projection for the private strategy-knowledge (literature) service.

The three model-facing tools live in `agent/tools.py`, beside the ledger they record into; this module
owns everything else and imports nothing from `tools.py`. See agent/AGENTS.md, "Strategy knowledge
(literature) tools".
"""

from __future__ import annotations

import asyncio
import json
import time
import unicodedata
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal, NamedTuple

import httpx
import structlog
from anthropic import beta_async_tool
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from agri_data_service.agent.report import LITERATURE_EVIDENCE_ORIGIN, STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE
from agri_data_service.config import settings

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterator, Mapping, Sequence

    from anthropic.lib.tools import BetaAsyncFunctionTool

__all__ = [
    "LEGACY_PROVENANCE_BASIS",
    "LITERATURE_EVIDENCE_DOMAIN",
    "LITERATURE_EVIDENCE_ORIGIN",
    "LITERATURE_TOOL_NAMES",
    "NOT_CONFIGURED",
    "REGION_ARGUMENT_DROPPED",
    "REGION_BOXES",
    "REJECTED_ARGUMENTS",
    "STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE",
    "UNAVAILABLE",
    "ContextQuerySource",
    "FactBasis",
    "FactProvenance",
    "LiteratureCallPlan",
    "ProvenancedSiteFacts",
    "RawSiteProfile",
    "RegionBox",
    "SanitizedSiteProfile",
    "ServerContext",
    "ServerContextPoint",
    "SiteFacts",
    "StrategyAnswer",
    "StrategyContext",
    "StrategySiteProfile",
    "ask",
    "bound_strategy_context",
    "current_strategy_context",
    "derive_region",
    "literature_tool",
    "plan_literature_call",
    "portable_schema",
    "provenanced_site_facts",
    "sanitize_site_profile",
    "server_site_profile",
    "service_arguments",
    "use_transport",
]

logger = structlog.get_logger()

#: The three model-facing literature tools; the bridge honours `server_context` for these alone.
LITERATURE_TOOL_NAMES: Final = frozenset(
    {
        "search_environmental_strategies",
        "get_environmental_strategies",
        "search_strategy_research_findings",
    }
)

# --- Provenance and bounds ---------------------------------------------------------

# LITERATURE_EVIDENCE_ORIGIN / STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE (C4 of the frozen contract) are defined
# once in agent/report.py, whose validators enforce the pairing, and re-exported here.
#: Ledger domain; the sufficiency gate never counts it as a measured surface.
LITERATURE_EVIDENCE_DOMAIN: Final = "literature_reference"

#: Under the HTTP tool bridge's 12 s deadline (`routes/agent_tools.py::TOOL_TIMEOUT_SECONDS`).
REQUEST_TIMEOUT_SECONDS: Final = 8.0
#: Counted on the wire: the request asks for `identity`, so no decompression happens before the cap.
MAX_RESPONSE_BYTES: Final = 1_048_576
_REQUEST_HEADERS: Final = {"Accept-Encoding": "identity"}
_REQUEST_ID_HEADER: Final = "X-Request-ID"
_IDENTITY_ENCODINGS: Final = frozenset({"", "identity"})
#: In-flight strategy-knowledge calls per event loop; the service's own cap answers a fast 503 above it.
MAX_CONCURRENT_CALLS: Final = 4
#: strategy-knowledge's `context_query` limit; the verbatim question keeps its LAST characters.
MAX_USER_QUESTION_CHARACTERS: Final = 2_000
DEFAULT_RESULTS: Final = 5
MAX_RESULTS: Final = 10
MAX_STRATEGY_IDS: Final = 5
MAX_FILTER_VALUES: Final = 6
MAX_QUERY_CHARACTERS: Final = 500
MAX_ID_CHARACTERS: Final = 120

_MAX_TEXT_CHARACTERS: Final = 1_200
_MAX_LIST_ITEMS: Final = 8
_MAX_MAPPING_ITEMS: Final = 16
_MAX_CITATIONS: Final = 8
_MAX_DETAIL_CHARACTERS: Final = 300

#: `site_profile` is advisory (boost-only, capped) in the strategy service, so it is sanitized field by
#: field rather than rejected whole; bounds on what a rejected field reports back.
MAX_SITE_PROFILE_IGNORED: Final = 20
_MAX_SITE_PROFILE_REASON_CHARACTERS: Final = 200
_MAX_SITE_PROFILE_FIELD_NAME_CHARACTERS: Final = 60

# --- Typed refusals ----------------------------------------------------------------

NOT_CONFIGURED: Final = "strategy_knowledge_not_configured"
UNAVAILABLE: Final = "strategy_knowledge_unavailable"
REJECTED_ARGUMENTS: Final = "strategy_knowledge_rejected_arguments"

_REJECTED_STATUSES: Final = frozenset({httpx.codes.BAD_REQUEST, httpx.codes.REQUEST_ENTITY_TOO_LARGE})

_REFUSAL_NOTES: Final[dict[str, str]] = {
    NOT_CONFIGURED: (
        "This is a REFUSAL, not an absence. The strategy-knowledge literature service is not configured "
        "on this deployment (STRATEGY_KNOWLEDGE_URL is unset), so no literature was searched. Nothing "
        "follows about whether strategies or findings exist for this need; say the literature lookup is "
        "unavailable and label any strategy reasoning model_inference."
    ),
    UNAVAILABLE: (
        "This is a REFUSAL, not an absence. The strategy-knowledge literature service could not answer "
        "(timeout, connection failure, server error, not ready, or an over-budget or malformed response). "
        "It is a fact about the service, not about the literature: do not report that no strategy or "
        "finding exists, and label any strategy reasoning model_inference."
    ),
    REJECTED_ARGUMENTS: (
        "This is a REFUSAL, not an absence. The strategy-knowledge service rejected these arguments, so "
        "nothing was searched. Correct them (enum values exactly as published, strategy_id values copied "
        "from a result) or drop the offending filter, and never read the rejection as an empty result."
    ),
}

LITERATURE_NOTE: Final = (
    "Literature-grounded strategy knowledge (claim_tier literature_grounded): what cited sources report, "
    "NOT a measurement, observation or prediction at this location. The service never saw the coordinate; "
    "site_profile only boosted or filtered the ranking. Cite with evidenceOrigin 'literature' and "
    "evidenceSource 'strategy-knowledge', never with evidenceReadIds. Quote rates and magnitudes only as "
    "reported, with their conditions, and never extrapolate them to this site. An empty result means no "
    "matching record in this corpus_version, not that no strategy exists."
)

# --- Vocabulary, copied verbatim from strategy-knowledge `vocabulary.py` -----------
#
# Literal enums so the published schema tells the model every accepted value; a drift test parses the
# service's own source and compares.

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
FirePhase = Literal["pre_fire", "during_fire", "post_fire_emergency", "post_fire_recovery", "long_term_resilience"]
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
EvidenceStrength = Literal[
    "ai_synthesis_only",
    "anecdotal_or_news",
    "expert_guidance",
    "field_trial_or_case_study",
    "peer_reviewed_experiment",
    "review_or_meta_analysis",
]
ServiceTool = Literal["search_strategies", "get_strategy", "search_findings"]

# --- Model-facing argument types ---------------------------------------------------

StrategyQuery = Annotated[str, Field(min_length=1, max_length=MAX_QUERY_CHARACTERS)]


def _wrap_lone_string(value: Any) -> Any:
    """Wrap a lone string in a one-element list; anything else (a real list, None) passes through.

    Runs BEFORE the `list[...]` validation, so an invalid string still fails exactly as it would inside a
    list. See agent/AGENTS.md, "A lone string is a one-element list filter".
    """
    return [value] if isinstance(value, str) else value


#: Applied to EVERY list filter; the published schema is unchanged (a BeforeValidator adds no schema).
_LONE_STRING_AS_LIST: Final = BeforeValidator(_wrap_lone_string)

GoalFilter = Annotated[list[Goal], _LONE_STRING_AS_LIST, Field(max_length=MAX_FILTER_VALUES)] | None
LandUseFilter = Annotated[list[LandUse], _LONE_STRING_AS_LIST, Field(max_length=MAX_FILTER_VALUES)] | None
FirePhaseFilter = Annotated[list[FirePhase], _LONE_STRING_AS_LIST, Field(max_length=MAX_FILTER_VALUES)] | None
RegionFilter = Annotated[list[Region], _LONE_STRING_AS_LIST, Field(max_length=MAX_FILTER_VALUES)] | None
MinimumEvidence = EvidenceStrength | None
ResultLimit = Annotated[int, Field(ge=1, le=MAX_RESULTS)]
StrategyId = Annotated[str, Field(min_length=1, max_length=MAX_ID_CHARACTERS)]
StrategyIds = Annotated[list[StrategyId], _LONE_STRING_AS_LIST, Field(min_length=1, max_length=MAX_STRATEGY_IDS)]
OptionalStrategyId = StrategyId | None

# Published as strings (numbers are coerced), which the service's `str | int` fields accept, so the schema
# needs no union; the service parses a digit string as an MTBS class or an NLCD code.
BurnSeverity = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
LandCover = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class StrategySiteProfile(BaseModel):
    """Server-read site facts, each with a basis label; never a day, coordinate, range or surface name.

    Mirrors strategy-knowledge `SiteProfile`.
    """

    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    slope_pct: Annotated[float, Field(ge=0, le=1_000)] | None = Field(
        default=None, description="Slope in percent; >= 30 boosts steep_slope."
    )
    soil_ph: Annotated[float, Field(ge=0, le=14)] | None = Field(
        default=None, description="Soil pH; < 5.5 acidic, > 7.8 alkaline."
    )
    sand_pct: Annotated[float, Field(ge=0, le=100)] | None = Field(
        default=None, description="Sand fraction in percent; >= 70 sandy_coarse."
    )
    clay_pct: Annotated[float, Field(ge=0, le=100)] | None = Field(
        default=None, description="Clay fraction in percent; >= 40 clay_heavy."
    )
    soil_organic_carbon_pct: Annotated[float, Field(ge=0, le=100)] | None = Field(
        default=None, description="Soil organic carbon in percent; < 1.0 low_organic_matter."
    )
    electrical_conductivity_ds_m: Annotated[float, Field(ge=0, le=1_000)] | None = Field(
        default=None, description="Electrical conductivity in dS/m; >= 4 saline_sodic."
    )
    burn_severity: BurnSeverity | None = Field(
        default=None, description="'high', 'moderate', 'low' (or MTBS class '2'-'4'); high also boosts hydrophobic."
    )
    days_since_fire: Annotated[int, Field(ge=0, le=36_500)] | None = Field(
        default=None, description="Days since the fire; <= 60 post_fire_emergency, <= 1095 post_fire_recovery."
    )
    annual_precip_mm: Annotated[float, Field(ge=0, le=20_000)] | None = Field(
        default=None, description="Annual precipitation in mm; < 350 droughty."
    )
    land_cover: LandCover | None = Field(
        default=None, description="NLCD class name ('Cultivated Crops') or code ('82'); mapped to land_use filters."
    )
    region: Region | None = Field(default=None, description="Region enum value, passed through as a filter.")


#: What a tool function actually declares for `site_profile`. Loose ON PURPOSE: `pydantic.validate_call`,
#: which `BaseFunctionTool.__init__` always builds from a tool's real parameter annotation and NEVER
#: from an `input_schema=` override, is what runs before the function body does -- so a strict
#: `StrategySiteProfile | None` here would reject a whole call over one bad key before
#: `sanitize_site_profile` ever saw it. The published schema still advertises the strict shape; see
#: `literature_tool` and `_patch_site_profile_schema`.
RawSiteProfile = dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class SanitizedSiteProfile:
    """A raw `site_profile` hint split into what the service will see and what was dropped, and why."""

    profile: StrategySiteProfile | None
    ignored: tuple[dict[str, str], ...]


def sanitize_site_profile(raw: RawSiteProfile) -> SanitizedSiteProfile:
    """Keep every `site_profile` key that validates on its OWN against `StrategySiteProfile`; drop the rest.

    site_profile only boosts or filters the literature ranking -- it is advisory, never a hard gate --
    so one bad hint (an unknown key a model invented, or a value outside a field's range) must not sink
    an otherwise-good call the way validating the object as a single strict model would. Each key is
    re-checked in ISOLATION with the model's own rules, so a bad key can never mask a good one: an
    unknown key surfaces as pydantic's own "extra_forbidden" on that key alone, because `StrategySiteProfile`
    forbids extras. FILTERS (goals, land_use, fire_phase, min_evidence, strategy_id, ids, limit, query)
    are never touched here and stay strict -- this function only ever sees the site_profile object.
    """
    if not isinstance(raw, dict) or not raw:
        return SanitizedSiteProfile(None, ())
    valid: dict[str, Any] = {}
    ignored: list[dict[str, str]] = []
    for key, value in raw.items():
        field_name = str(key)[:_MAX_SITE_PROFILE_FIELD_NAME_CHARACTERS]
        try:
            validated = StrategySiteProfile.model_validate({field_name: value})
        except ValidationError as error:
            if len(ignored) < MAX_SITE_PROFILE_IGNORED:
                ignored.append({"field": field_name, "reason": _site_profile_field_reason(error)})
            continue
        dumped = validated.model_dump(mode="json", exclude_none=True)
        if field_name in dumped:
            valid[field_name] = dumped[field_name]
    profile = StrategySiteProfile.model_validate(valid) if valid else None
    return SanitizedSiteProfile(profile, tuple(ignored))


def _site_profile_field_reason(error: ValidationError) -> str:
    """The first issue's own message, bounded; the rejected value is never included (`include_input=False`)."""
    issues = error.errors(include_url=False, include_context=False, include_input=False)
    message = issues[0]["msg"] if issues else "invalid value"
    if len(message) <= _MAX_SITE_PROFILE_REASON_CHARACTERS:
        return message
    return f"{message[: _MAX_SITE_PROFILE_REASON_CHARACTERS - 3]}..."


# --- Server-owned site context (seams S1/S2) -----------------------------------------
#
# The server, not the model, owns the site facts a literature call is boosted and filtered by. See
# agent/AGENTS.md, "Server-owned site facts".

Longitude = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
Latitude = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]


def _clean_user_question(value: Any) -> Any:
    """Strip control characters (newlines kept), keep the LAST 2000 characters, and map blank to None."""
    if not isinstance(value, str):
        return value
    kept = "".join(character for character in value if character == "\n" or unicodedata.category(character) != "Cc")
    return kept[-MAX_USER_QUESTION_CHARACTERS:].strip() or None


#: The user's verbatim messages, latest last, front-truncated; cleaned before length validation.
UserQuestion = Annotated[
    Annotated[str, Field(min_length=1, max_length=MAX_USER_QUESTION_CHARACTERS)] | None,
    BeforeValidator(_clean_user_question),
]

#: C3: the site brief's literature seed; never the user's words.
MAX_SITE_BRIEF_QUERY_CHARACTERS: Final = 600


def _clean_site_brief_query(value: Any) -> Any:
    """Strip every control character, trim, and map blank to None (C3)."""
    if not isinstance(value, str):
        return value
    kept = "".join(character for character in value if unicodedata.category(character) != "Cc")
    return kept.strip() or None


SiteBriefQuery = Annotated[
    Annotated[str, Field(min_length=1, max_length=MAX_SITE_BRIEF_QUERY_CHARACTERS)] | None,
    BeforeValidator(_clean_site_brief_query),
]

#: C3 basis vocabulary; `survey_estimate` is reserved and has no producer in this build.
FactBasis = Literal["measured", "model_estimate", "classified", "classified_remote_sensing", "survey_estimate"]
#: Echoed for a legacy caller's site facts, which arrive with no provenance at all (C3).
LEGACY_PROVENANCE_BASIS: Final = "provenance_absent_legacy"
MAX_SOIL_DISTANCE_METERS: Final = 2_000
MAX_SECTION_DISTANCE_METERS: Final = 100_000
#: The region is derived here from the map point, so its provenance is agri's own.
REGION_PROVENANCE: Final[dict[str, str]] = {
    "basis": "classified",
    "label": "Region derived by the agri service from the map point (fixed region box table)",
}


class FactProvenance(BaseModel):
    """Where one site fact came from and the label the model must repeat beside it (C3)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    basis: FactBasis
    source: Annotated[str, Field(min_length=1, max_length=80)]
    label: Annotated[str, Field(min_length=1, max_length=200)]
    release_id: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    depth: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    resolution_m: Annotated[int, Field(ge=1, le=100_000)] | None = None
    distance_m: Annotated[int, Field(ge=0, le=MAX_SECTION_DISTANCE_METERS)] | None = None

    @model_validator(mode="after")
    def _soil_distance_is_bounded(self) -> FactProvenance:
        """A model-estimate (soil) distance is at most the soil read's 2,000 m radius."""
        too_far = self.distance_m is not None and self.distance_m > MAX_SOIL_DISTANCE_METERS
        if self.basis == "model_estimate" and too_far:
            raise ValueError("a model_estimate distance_m must be at most 2000")
        return self


class SiteFacts(BaseModel):
    """Server-read site facts (S1), each with a basis label: `SiteProfile` minus `slope_pct` and `region`."""

    model_config = ConfigDict(extra="forbid", frozen=True, coerce_numbers_to_str=True)

    soil_ph: Annotated[float, Field(ge=0, le=14, allow_inf_nan=False)] | None = None
    sand_pct: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None = None
    clay_pct: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None = None
    soil_organic_carbon_pct: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None = None
    electrical_conductivity_ds_m: Annotated[float, Field(ge=0, le=1_000, allow_inf_nan=False)] | None = None
    burn_severity: BurnSeverity | None = None
    days_since_fire: Annotated[int, Field(ge=0, le=36_500)] | None = None
    annual_precip_mm: Annotated[float, Field(ge=0, le=20_000, allow_inf_nan=False)] | None = None
    land_cover: LandCover | None = None


class StrategyContext(BaseModel):
    """Out-of-band literature context for one run or bridge call (S2); the model never sees or sets it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_question: UserQuestion = None
    longitude: Longitude | None = None
    latitude: Latitude | None = None
    site_facts: SiteFacts | None = None
    site_facts_provenance: dict[str, FactProvenance] | None = None
    site_brief_query: SiteBriefQuery = None

    @model_validator(mode="after")
    def _point_is_whole(self) -> StrategyContext:
        """A coordinate is both halves or neither."""
        if (self.longitude is None) != (self.latitude is None):
            raise ValueError("longitude and latitude must be given together")
        return self


class ServerContextPoint(BaseModel):
    """The map point on the bridge wire (S1), WGS84."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    longitude: Longitude
    latitude: Latitude


class ServerContext(BaseModel):
    """The bridge request's optional `server_context` object (S1)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_question: UserQuestion = None
    point: ServerContextPoint | None = None
    site_facts: SiteFacts | None = None
    site_facts_provenance: dict[str, FactProvenance] | None = None
    site_brief_query: SiteBriefQuery = None

    def strategy_context(self) -> StrategyContext:
        """The same facts in the S2 shape the tools read."""
        return StrategyContext(
            user_question=self.user_question,
            longitude=self.point.longitude if self.point else None,
            latitude=self.point.latitude if self.point else None,
            site_facts=self.site_facts,
            site_facts_provenance=self.site_facts_provenance,
            site_brief_query=self.site_brief_query,
        )


class RegionBox(NamedTuple):
    """One inclusive lon/lat box; `region=None` marks a band too ambiguous to label."""

    region: Region | None
    west: float
    south: float
    east: float
    north: float
    area: str


# Coarse box table, first match wins (deterministic precedence). Ambiguity bands come first so a
# border strip never inherits either neighbour; nothing matching (open ocean, Hawaii, Pacific islands,
# the Ural gap) is None. Boxes include adjacent coastal water. The Cascade crest splits pnw_westside
# from pnw_inland at -121.3 in Washington, stepping west in Oregon (-121.7, then -122.1). Never
# north_america_general/global/general: the service's region filter admits those itself.
# See agent/AGENTS.md, "The region table".
REGION_BOXES: Final[tuple[RegionBox, ...]] = (
    # Ambiguity bands: a wrong region is a HARD filter in the service, None only skips it.
    RegionBox(None, -121.4, 45.6, -121.2, 49.0, "Cascade crest, Washington"),
    RegionBox(None, -121.8, 44.0, -121.6, 45.6, "Cascade crest, northern Oregon"),
    RegionBox(None, -122.2, 42.0, -122.0, 44.0, "Cascade crest, southern Oregon"),
    RegionBox(None, -109.5, 42.5, -104.05, 45.0, "Wyoming basins between the Northern and Southern Rockies"),
    RegionBox(None, -111.5, 37.0, -109.05, 42.0, "eastern Utah, Wasatch/Uinta beside the Colorado Plateau"),
    RegionBox(None, -106.7, 29.3, -104.0, 31.8, "Rio Grande border, El Paso to Presidio"),
    RegionBox(None, -104.0, 25.8, -100.0, 29.8, "Rio Grande border, Big Bend to Eagle Pass"),
    RegionBox(None, -100.0, 25.8, -99.0, 27.6, "Rio Grande border, Laredo"),
    RegionBox(None, -99.0, 25.8, -97.1, 26.4, "Rio Grande border, lower valley"),
    RegionBox(None, -89.2, 36.9, -82.0, 39.15, "Ohio River valley, Midwest/Southeast transition"),
    RegionBox(None, -83.0, 38.0, -75.0, 39.7, "Mid-Atlantic transition"),
    RegionBox(None, -95.2, 48.0, -89.0, 49.4, "Minnesota-Ontario border lakes"),
    RegionBox(None, -77.0, 43.5, -74.3, 45.1, "St. Lawrence River border"),
    RegionBox(None, -79.3, 42.85, -78.9, 43.3, "Niagara River border"),
    RegionBox(None, -71.5, 45.0, -70.0, 46.5, "Maine/New Hampshire-Quebec border"),
    RegionBox(None, -117.1, 32.53, -114.7, 32.72, "Imperial Valley/Mexicali border strip"),
    RegionBox(None, -84.4, 45.5, -81.5, 46.0, "Manitoulin Island and the North Channel"),
    RegionBox(None, -69.3, 47.0, -67.8, 47.5, "St. John River border strip, Edmundston"),
    RegionBox(None, -172.5, 62.5, -168.0, 67.0, "Bering Strait"),
    RegionBox(None, -58.0, 59.5, -11.0, 84.0, "Greenland"),
    RegionBox(None, -72.0, 76.0, -58.0, 84.0, "Greenland, Nares Strait"),
    RegionBox(None, 32.2, 27.5, 60.0, 42.0, "Levant, Anatolia, Caucasus and Iran"),
    RegionBox(None, 34.5, 12.3, 60.0, 27.5, "Arabian Peninsula and the Red Sea coast"),
    RegionBox(None, -18.5, 27.5, -13.0, 33.5, "Canary Islands and Madeira"),
    RegionBox(None, 130.5, -11.0, 151.0, 0.0, "New Guinea"),
    # Chukotka before alaska, whose westernmost box spans the antimeridian side of the Bering Sea.
    RegionBox("asia", -180.0, 62.0, -172.5, 72.0, "Chukotka"),
    RegionBox("alaska", -180.0, 51.0, -141.0, 72.0, "Alaska mainland and Aleutians"),
    RegionBox("alaska", -138.0, 57.5, -134.0, 60.0, "Alaska panhandle, Juneau and Haines"),
    RegionBox("alaska", -136.0, 54.6, -131.0, 57.5, "Alaska panhandle, Sitka to Ketchikan"),
    RegionBox("alaska", -141.0, 58.5, -138.0, 60.0, "Alaska, Yakutat coast"),
    RegionBox("alaska", 172.0, 51.0, 180.0, 53.5, "western Aleutians"),
    RegionBox("pnw_westside", -124.8, 45.6, -121.3, 49.0, "Washington west of the Cascade crest"),
    RegionBox("pnw_westside", -124.6, 44.0, -121.7, 45.6, "northern Oregon west of the Cascade crest"),
    RegionBox("pnw_westside", -124.6, 42.0, -122.1, 44.0, "southern Oregon west of the Cascade crest"),
    RegionBox("pnw_inland", -121.3, 46.0, -116.0, 49.0, "eastern Washington and the Idaho panhandle"),
    RegionBox("pnw_inland", -121.3, 45.6, -116.9, 46.0, "south-central Washington"),
    RegionBox("pnw_inland", -121.7, 44.0, -116.9, 45.6, "north-central and northeastern Oregon"),
    RegionBox("pnw_inland", -122.1, 42.0, -121.0, 44.0, "Oregon east slope and Klamath basin"),
    RegionBox("california", -124.5, 32.53, -120.0, 42.0, "California west of -120"),
    RegionBox("california", -120.0, 32.53, -116.0, 35.0, "southern California coast and basins"),
    RegionBox("california", -120.0, 35.0, -118.5, 38.5, "Sierra Nevada and the southern Central Valley"),
    RegionBox("great_basin_high_desert", -121.0, 42.0, -116.9, 44.0, "southeastern Oregon high desert"),
    RegionBox("great_basin_high_desert", -117.1, 42.0, -111.0, 44.0, "Snake River Plain and Owyhee"),
    RegionBox("great_basin_high_desert", -120.0, 38.5, -111.5, 42.0, "Nevada and western Utah"),
    RegionBox("great_basin_high_desert", -118.5, 37.0, -111.5, 38.5, "central Nevada and southwestern Utah"),
    RegionBox("northern_rockies", -116.9, 44.0, -111.0, 46.0, "central Idaho and southwestern Montana"),
    RegionBox("northern_rockies", -116.0, 46.0, -110.0, 49.0, "northern Idaho mountains and western Montana"),
    RegionBox("northern_rockies", -111.0, 42.5, -109.5, 46.0, "Yellowstone, Tetons and Bozeman"),
    RegionBox("southern_rockies", -109.05, 37.0, -104.8, 41.0, "Colorado west of the Front Range edge"),
    RegionBox("southern_rockies", -111.0, 41.0, -105.0, 42.5, "southern Wyoming"),
    RegionBox("southern_rockies", -108.0, 35.5, -105.0, 37.0, "northern New Mexico mountains"),
    RegionBox("southwest", -118.5, 35.0, -114.0, 37.0, "Mojave: southern Nevada and Death Valley"),
    RegionBox("southwest", -116.0, 32.53, -114.8, 35.0, "southeastern California deserts"),
    RegionBox("southwest", -114.8, 32.5, -104.0, 37.0, "Arizona and New Mexico"),
    RegionBox("southwest", -111.1, 31.3, -104.0, 32.5, "southern Arizona and New Mexico border strip"),
    RegionBox("southwest", -113.3, 32.03, -111.1, 32.5, "southwestern Arizona, Ajo"),
    RegionBox("southwest", -112.2, 31.7, -111.1, 32.03, "southwestern Arizona, Tohono O'odham"),
    RegionBox("great_plains_texas", -104.0, 25.8, -93.8, 33.6, "Texas"),
    RegionBox("great_plains_texas", -104.0, 33.6, -94.5, 36.5, "Texas panhandle, Oklahoma, eastern New Mexico"),
    RegionBox("great_plains_texas", -104.8, 36.5, -97.0, 49.0, "central and northern Great Plains"),
    RegionBox("great_plains_texas", -109.5, 45.0, -104.0, 49.0, "eastern Montana"),
    RegionBox("us_midwest", -97.0, 36.5, -89.0, 49.0, "Missouri to Minnesota"),
    RegionBox("us_midwest", -89.0, 37.8, -82.4, 46.0, "Illinois, Indiana, Ohio, Michigan, Wisconsin"),
    RegionBox("us_midwest", -90.5, 45.5, -84.4, 47.5, "Upper Peninsula of Michigan"),
    RegionBox("us_midwest", -82.4, 38.4, -80.5, 41.98, "eastern Ohio"),
    RegionBox("us_southeast", -94.5, 24.4, -79.8, 30.7, "Gulf coast and Florida"),
    RegionBox("us_southeast", -94.5, 30.7, -75.4, 36.6, "Arkansas to the Carolinas"),
    RegionBox("us_southeast", -89.6, 36.5, -75.2, 38.0, "Kentucky and Virginia south of the transition"),
    RegionBox("us_northeast", -80.6, 39.7, -74.7, 42.3, "Pennsylvania and New York's Southern Tier"),
    RegionBox("us_northeast", -79.8, 42.0, -78.9, 42.6, "western New York, Lake Erie shore"),
    RegionBox("us_northeast", -78.9, 42.0, -73.3, 43.5, "western and central New York"),
    RegionBox("us_northeast", -74.7, 40.5, -69.9, 45.0, "New England, eastern New York, New Jersey north"),
    RegionBox("us_northeast", -75.6, 38.4, -73.9, 41.4, "New Jersey south and the Delaware shore"),
    RegionBox("us_northeast", -71.1, 43.0, -66.9, 45.2, "southern Maine"),
    RegionBox("us_northeast", -70.0, 45.2, -67.8, 47.5, "northern Maine"),
    RegionBox("canada", -141.0, 49.0, -95.2, 83.5, "western and northern Canada"),
    RegionBox("canada", -95.2, 45.0, -52.0, 83.5, "eastern Canada"),
    RegionBox("canada", -82.4, 41.7, -74.3, 45.0, "southern Ontario"),
    RegionBox("latin_america", -117.5, 14.5, -86.5, 32.7, "Mexico"),
    RegionBox("latin_america", -92.5, 7.0, -77.0, 18.5, "Central America"),
    RegionBox("latin_america", -85.0, 10.0, -59.0, 23.7, "Caribbean"),
    RegionBox("latin_america", -82.0, -56.0, -34.0, 12.6, "South America"),
    RegionBox("europe", -10.0, 36.0, -1.5, 44.0, "Portugal and western Spain"),
    RegionBox("europe", -1.5, 37.3, 3.5, 44.0, "eastern Spain and the Balearics"),
    RegionBox("europe", -10.5, 44.0, 45.0, 71.5, "Europe north of 44N to the Volga"),
    RegionBox("europe", -25.0, 63.0, -13.0, 67.0, "Iceland"),
    RegionBox("europe", 3.5, 38.0, 12.0, 44.0, "southern France, Corsica, Sardinia, western Italy"),
    RegionBox("europe", 12.0, 36.4, 26.0, 44.0, "Italy, Sicily, the Balkans and Greece"),
    RegionBox("europe", 14.1, 35.7, 14.7, 36.1, "Malta"),
    RegionBox("europe", 23.4, 34.8, 26.4, 35.8, "Crete"),
    RegionBox("europe", 26.0, 41.9, 30.0, 44.0, "eastern Bulgaria and the Black Sea coast"),
    RegionBox("africa", -18.0, -35.0, 52.0, 33.0, "Africa south of 33N"),
    RegionBox("africa", -18.0, 33.0, 11.6, 37.5, "Morocco, Algeria and Tunisia"),
    RegionBox("oceania", 112.5, -44.0, 154.0, -10.5, "Australia"),
    RegionBox("oceania", 166.0, -47.5, 179.0, -34.0, "New Zealand"),
    RegionBox("asia", 60.0, 5.0, 130.0, 78.0, "Asia east of 60E"),
    RegionBox("asia", 130.0, 24.0, 146.0, 78.0, "Japan, Korea's east and the Russian Far East"),
    RegionBox("asia", 90.0, -11.0, 131.0, 5.0, "maritime Southeast Asia"),
    RegionBox("asia", 146.0, 40.0, 180.0, 78.0, "Kamchatka, the Kurils and northeast Siberia"),
)


#: WGS84 coordinate range, restated as plain floats (`Longitude`/`Latitude` above are pydantic Field
#: bounds, not directly usable in a bare comparison) so a caller's raw float pair is range-checked
#: the same way before `derive_region` ever looks at `REGION_BOXES`.
_MIN_LONGITUDE_DEGREES: Final = -180.0
_MAX_LONGITUDE_DEGREES: Final = 180.0
_MIN_LATITUDE_DEGREES: Final = -90.0
_MAX_LATITUDE_DEGREES: Final = 90.0


def derive_region(longitude: float, latitude: float) -> Region | None:
    """The first `REGION_BOXES` entry containing the point, or None (ambiguous band, open ocean, bad input)."""
    in_range = (
        _MIN_LONGITUDE_DEGREES <= longitude <= _MAX_LONGITUDE_DEGREES
        and _MIN_LATITUDE_DEGREES <= latitude <= _MAX_LATITUDE_DEGREES
    )
    if not in_range:  # NaN fails both comparisons.
        return None
    for box in REGION_BOXES:
        if box.west <= longitude <= box.east and box.south <= latitude <= box.north:
            return box.region
    return None


_strategy_context: ContextVar[StrategyContext | None] = ContextVar("strategy_knowledge_context", default=None)


@contextmanager
def bound_strategy_context(context: StrategyContext | None) -> Iterator[None]:
    """Bind `context` (None clears it) for every literature call inside the block."""
    token = _strategy_context.set(context)
    try:
        yield
    finally:
        _strategy_context.reset(token)


def current_strategy_context() -> StrategyContext | None:
    """The bound server context, or None on the MCP/external path."""
    return _strategy_context.get()


class ProvenancedSiteFacts(NamedTuple):
    """The site facts a literature call may forward, their echoed labels, and the keys dropped (C3)."""

    values: dict[str, Any]
    provenance: dict[str, dict[str, str]]
    dropped: list[str]


def provenanced_site_facts(context: StrategyContext) -> ProvenancedSiteFacts:
    """Apply the C3 provenance-aware drop: a new-shape caller loses keys without provenance; legacy is echoed."""
    values: dict[str, Any] = context.site_facts.model_dump(exclude_none=True) if context.site_facts else {}
    labels = context.site_facts_provenance
    if labels is None:
        return ProvenancedSiteFacts(values, {key: {"basis": LEGACY_PROVENANCE_BASIS} for key in values}, [])
    kept = {key: value for key, value in values.items() if key in labels}
    dropped = [f"{key}(no_provenance)" for key in values if key not in labels]
    echoed = {key: {"basis": labels[key].basis, "label": labels[key].label} for key in kept}
    return ProvenancedSiteFacts(kept, echoed, dropped)


def server_site_profile(context: StrategyContext) -> StrategySiteProfile | None:
    """The context's server-read site facts (each with a basis label) plus the region derived from its point."""
    values = provenanced_site_facts(context).values
    if context.longitude is not None and context.latitude is not None:
        region = derive_region(context.longitude, context.latitude)
        if region is not None:
            values["region"] = region
    return StrategySiteProfile.model_validate(values) if values else None


SiteProfileSource = Literal["server", "caller_asserted", "none"]
ContextQuerySource = Literal["user_question", "site_brief", "none"]
#: Named in `site_profile_dropped` when a server context discards the model's top-level region filter.
REGION_ARGUMENT_DROPPED: Final = "region(argument)"


@dataclass(frozen=True, slots=True)
class LiteratureCallPlan:
    """What one literature call sends, and the provenance labels its payload carries (S2)."""

    site_profile: StrategySiteProfile | None
    region: list[str] | None
    context_query: str | None
    site_profile_source: SiteProfileSource
    #: None on the external path, where nothing is discarded and the key is omitted.
    site_profile_dropped: tuple[str, ...] | None
    site_profile_ignored: tuple[dict[str, str], ...]
    #: C4: which text `context_query` carries; None on the external path.
    context_query_source: ContextQuerySource | None = None
    #: C4: `{key: {basis, label}}` for every forwarded site_profile key; None on the external path.
    site_profile_provenance: dict[str, dict[str, str]] | None = None


def plan_literature_call(
    raw_site_profile: RawSiteProfile = None, region: Sequence[str] | None = None
) -> LiteratureCallPlan:
    """Server context wins: its facts replace the model's site_profile and region; else the advisory path."""
    context = current_strategy_context()
    if context is None:
        sanitized = sanitize_site_profile(raw_site_profile)
        asserted = isinstance(raw_site_profile, dict) and bool(raw_site_profile)
        return LiteratureCallPlan(
            site_profile=sanitized.profile,
            region=list(region) if region is not None else None,
            context_query=None,
            site_profile_source="caller_asserted" if asserted else "none",
            site_profile_dropped=None,
            site_profile_ignored=sanitized.ignored,
        )
    dropped = (
        [str(key)[:_MAX_SITE_PROFILE_FIELD_NAME_CHARACTERS] for key in raw_site_profile][:MAX_SITE_PROFILE_IGNORED]
        if isinstance(raw_site_profile, dict)
        else []
    )
    if region:
        dropped.append(REGION_ARGUMENT_DROPPED)
    facts = provenanced_site_facts(context)
    dropped.extend(facts.dropped)
    profile = server_site_profile(context)
    provenance = dict(facts.provenance)
    if profile is not None and profile.region is not None:
        provenance["region"] = dict(REGION_PROVENANCE)
    context_query, context_query_source = _context_query(context)
    return LiteratureCallPlan(
        site_profile=profile,
        region=None,
        context_query=context_query,
        site_profile_source="server",
        site_profile_dropped=tuple(dropped),
        site_profile_ignored=(),
        context_query_source=context_query_source,
        site_profile_provenance=provenance,
    )


def _context_query(context: StrategyContext) -> tuple[str | None, ContextQuerySource]:
    """C4: the user's own words when present, else the site brief's seed, else nothing."""
    if context.user_question:
        return context.user_question, "user_question"
    if context.site_brief_query:
        return context.site_brief_query, "site_brief"
    return None, "none"


# --- Portable published schema -----------------------------------------------------


def portable_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """The same schema with `$ref`s inlined and optional `anyOf [X, null]` collapsed to X.

    Pydantic publishes both for a nested model and an optional parameter; the live map agent drives
    Gemini through OpenRouter, and no other bridge tool publishes either. See agent/AGENTS.md.
    """
    definitions: Mapping[str, Any] = schema.get("$defs", {})

    def rewrite(node: Any) -> Any:
        if isinstance(node, list):
            return [rewrite(item) for item in node]
        if not isinstance(node, dict):
            return node
        body = {key: value for key, value in node.items() if key != "$defs"}
        reference = body.pop("$ref", None)
        if isinstance(reference, str):
            return rewrite({**definitions[reference.rsplit("/", 1)[-1]], **body})
        options = body.pop("anyOf", None)
        if isinstance(options, list):
            concrete = [option for option in options if option != {"type": "null"}]
            if len(concrete) == 1:
                if "default" in body and body["default"] is None:
                    del body["default"]
                return rewrite({**concrete[0], **body})
            body["anyOf"] = options
        return {key: rewrite(value) for key, value in body.items()}

    rewritten: dict[str, Any] = rewrite(dict(schema))
    return rewritten


def _patch_site_profile_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Restore the strict, descriptive `StrategySiteProfile` shape for a top-level `site_profile` property.

    A tool function types `site_profile` as `RawSiteProfile` (a loose dict) so `pydantic.validate_call`
    accepts anything and lets `sanitize_site_profile` filter it field by field instead of rejecting the
    whole call. Inferring the schema straight from that loose annotation would publish `site_profile` as
    a bare `{"type": "object"}` with no properties, which tells the model nothing about which keys are
    meaningful. This substitutes the strict model schema back in, keeping only the docstring-derived
    `description` the loose annotation still produced, so the PUBLISHED schema is unaffected by the
    looser runtime type.
    """
    properties = schema.get("properties")
    if not isinstance(properties, dict) or "site_profile" not in properties:
        return dict(schema)
    current = properties["site_profile"]
    description = current.get("description") if isinstance(current, dict) else None
    strict = StrategySiteProfile.model_json_schema()
    patched = {**strict, "description": description} if description else strict
    return {**schema, "properties": {**properties, "site_profile": patched}}


def literature_tool(function: Callable[..., Coroutine[Any, Any, str]]) -> BetaAsyncFunctionTool[Any]:
    """`beta_async_tool` publishing `portable_schema`; arguments are still validated against the signature."""
    inferred = beta_async_tool(function)
    published = portable_schema(_patch_site_profile_schema(inferred.input_schema))
    return beta_async_tool(function, input_schema=published)


# --- The call ----------------------------------------------------------------------

#: Tests answer requests in-process through this; production leaves it None and httpx opens sockets.
_transport: ContextVar[httpx.AsyncBaseTransport | None] = ContextVar("strategy_knowledge_transport", default=None)


@contextmanager
def use_transport(transport: httpx.AsyncBaseTransport) -> Iterator[None]:
    """Answer every strategy-knowledge request through `transport` for one block (tests only)."""
    token = _transport.set(transport)
    try:
        yield
    finally:
        _transport.reset(token)


@dataclass(frozen=True, slots=True)
class StrategyAnswer:
    """One tool outcome: the model-facing payload and the ledger detail recorded beside it."""

    payload: dict[str, Any]
    ledger_detail: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _Refused:
    """Why one exchange cannot be answered."""

    code: str
    detail: str


class _ResponseOverBudgetError(Exception):
    """The service answered more than MAX_RESPONSE_BYTES."""


class _UnrequestedEncodingError(Exception):
    """The service compressed a body we asked for as `identity`, so its wire size is not its real size."""


def service_arguments(**arguments: Any) -> dict[str, Any]:
    """Drop unset filters and flatten the site profile into the JSON object the service validates."""
    body: dict[str, Any] = {}
    for name, value in arguments.items():
        if isinstance(value, StrategySiteProfile):
            profile = value.model_dump(mode="json", exclude_none=True)
            if profile:
                body[name] = profile
        elif value is not None and value != []:
            body[name] = value
    return body


#: One (loop, Semaphore) per event loop: a Semaphore binds to the loop it first waits on.
_call_slots: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Semaphore]] = {}


def _call_slot() -> asyncio.Semaphore:
    """This loop's MAX_CONCURRENT_CALLS Semaphore, created lazily; closed loops' entries are pruned."""
    loop = asyncio.get_running_loop()
    entry = _call_slots.get(id(loop))
    if entry is None or entry[0] is not loop:
        for key, (known_loop, _) in list(_call_slots.items()):
            if known_loop.is_closed():
                _call_slots.pop(key, None)
        entry = (loop, asyncio.Semaphore(MAX_CONCURRENT_CALLS))
        _call_slots[id(loop)] = entry
    return entry[1]


async def ask(tool_name: str, service_tool: ServiceTool, arguments: Mapping[str, Any]) -> StrategyAnswer:
    """Call one strategy-knowledge tool and project its answer; every failure is a typed refusal."""
    started = time.perf_counter()
    origin = settings.strategy_knowledge_url
    if not origin:
        answer = refusal(tool_name, NOT_CONFIGURED, "STRATEGY_KNOWLEDGE_URL is not set on this service")
        _log_literature_call(tool_name, answer, started, request_id=None)
        return answer
    request_id = uuid.uuid4().hex
    async with _call_slot():
        decoded = await _exchange(f"{origin}/v1/tools/{service_tool}", arguments, request_id)
    answer = (
        refusal(tool_name, decoded.code, decoded.detail)
        if isinstance(decoded, _Refused)
        else _answered(tool_name, service_tool, decoded)
    )
    _log_literature_call(tool_name, answer, started, request_id=request_id)
    return answer


def _log_literature_call(tool_name: str, answer: StrategyAnswer, started: float, *, request_id: str | None) -> None:
    """One `literature_call` event per ask: tool, status, ms, result_count, request_id."""
    detail = answer.ledger_detail
    logger.info(
        "literature_call",
        tool=tool_name,
        status=detail["state"] if detail["state"] == "answered" else detail.get("error"),
        ms=round((time.perf_counter() - started) * 1000, 1),
        result_count=detail.get("result_count"),
        request_id=request_id,
    )


def _answered(tool_name: str, service_tool: ServiceTool, decoded: Mapping[str, Any]) -> StrategyAnswer:
    """The bounded projection of a 200 answer, and its ledger detail."""
    projected = _PROJECTIONS[service_tool](decoded)
    result_count = len(projected.get("results", projected.get("strategies", [])))
    return StrategyAnswer(
        payload={
            "tool": tool_name,
            "evidence_domain": LITERATURE_EVIDENCE_DOMAIN,
            "cite_as": {
                "evidenceOrigin": LITERATURE_EVIDENCE_ORIGIN,
                "evidenceSource": STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE,
            },
            "claim_tier": decoded.get("claim_tier"),
            "corpus_version": decoded.get("corpus_version"),
            "index_is_stale": decoded.get("index_is_stale"),
            **projected,
            **{key: decoded[key] for key in _PASSTHROUGH_ECHO_KEYS if key in decoded},
            "result_count": result_count,
            "note": LITERATURE_NOTE,
        },
        ledger_detail={
            "evidence_domain": LITERATURE_EVIDENCE_DOMAIN,
            "state": "answered",
            "result_count": result_count,
            "corpus_version": decoded.get("corpus_version"),
        },
    )


def refusal(tool_name: str, code: str, detail: str) -> StrategyAnswer:
    """A typed refusal payload; never an exception, never an empty success."""
    return StrategyAnswer(
        payload={
            "tool": tool_name,
            "error": code,
            "refusal_detail": detail[:_MAX_DETAIL_CHARACTERS],
            "evidence_domain": LITERATURE_EVIDENCE_DOMAIN,
            "note": _REFUSAL_NOTES[code],
        },
        ledger_detail={"evidence_domain": LITERATURE_EVIDENCE_DOMAIN, "state": "refused", "error": code},
    )


async def _exchange(  # noqa: PLR0911 - one return per refusal class the response is sorted into.
    url: str, arguments: Mapping[str, Any], request_id: str
) -> dict[str, Any] | _Refused:
    """One bounded request: the decoded 200 body, or the refusal that classifies why there is none.

    Any non-200 other than 400/413 -- including the service's own 503 concurrency cap -- is UNAVAILABLE.
    """
    try:
        status, body = await _post(url, arguments, request_id)
    except _ResponseOverBudgetError:
        return _Refused(UNAVAILABLE, f"the response exceeded its {MAX_RESPONSE_BYTES}-byte budget")
    except _UnrequestedEncodingError:
        return _Refused(UNAVAILABLE, "the service compressed a response requested as identity")
    except (httpx.HTTPError, TimeoutError) as error:
        return _Refused(UNAVAILABLE, f"the request failed ({type(error).__name__})")
    decoded = _decode(body)
    if status in _REJECTED_STATUSES:
        return _Refused(REJECTED_ARGUMENTS, _service_detail(decoded, status))
    if status != httpx.codes.OK:
        return _Refused(UNAVAILABLE, _service_detail(decoded, status))
    if decoded is None:
        return _Refused(UNAVAILABLE, "the service answered with a body that is not a JSON object")
    return decoded


async def _post(url: str, arguments: Mapping[str, Any], request_id: str) -> tuple[int, bytes]:
    """POST the arguments and read at most MAX_RESPONSE_BYTES of the answer inside one wall-clock deadline."""
    headers = {**_REQUEST_HEADERS, _REQUEST_ID_HEADER: request_id}
    async with (
        asyncio.timeout(REQUEST_TIMEOUT_SECONDS),
        httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False, transport=_transport.get()
        ) as client,
        client.stream("POST", url, json=dict(arguments), headers=headers) as response,
    ):
        if response.headers.get("content-encoding", "").strip().lower() not in _IDENTITY_ENCODINGS:
            raise _UnrequestedEncodingError
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
            raise _ResponseOverBudgetError
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise _ResponseOverBudgetError
        return response.status_code, bytes(body)


def _decode(body: bytes) -> dict[str, Any] | None:
    """The body as a JSON object, or None when it is anything else."""
    try:
        decoded = json.loads(body)
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _service_detail(decoded: Mapping[str, Any] | None, status: int) -> str:
    """The status plus the service's own error code and detail, bounded."""
    parts = [f"HTTP {status}"]
    for key in ("error", "detail"):
        value = decoded.get(key) if decoded else None
        if isinstance(value, str) and value:
            parts.append(value)
    return ": ".join(parts)[:_MAX_DETAIL_CHARACTERS]


# --- Bounded projection ------------------------------------------------------------
#
# Keeps identity, summaries, goals, evidence strength, actions and citation title/URL; drops facet
# texts, snippets, scores and other bulk the model does not need to choose or cite.

_STRATEGY_HIT_KEYS: Final = (
    "rank",
    "strategy_id",
    "name",
    "summary",
    "family_id",
    "category",
    "goals",
    "fire_phase",
    "evidence_strength",
    "citation_count",
    "review_state",
    "matched_facet",
    "boosted_by",
)
_STRATEGY_RECORD_KEYS: Final = (
    "strategy_id",
    "resolved_from",
    "name",
    "summary",
    "family_id",
    "category",
    "goals",
    "fire_phase",
    "land_use",
    "region",
    "actions",
    "application_rate",
    "timing",
    "slope_guidance",
    "soil_conditions",
    "scale",
    "cost_level",
    "labor_intensity",
    "time_to_effect",
    "benefits",
    "risks_limitations",
    "nrcs_practice_code",
    "evidence_strength",
    "review_state",
)
_FINDING_KEYS: Final = (
    "rank",
    "finding_id",
    "claim",
    "conditions",
    "direction",
    "magnitude",
    "variables",
    "study_type",
    "evidence_strength",
    "linked_strategy_ids",
    "excerpt",
    "review_state",
)
_CITATION_KEYS: Final = ("title", "url", "publisher", "year")
_SEARCH_ECHO_KEYS: Final = ("query", "applied_filters", "site_profile", "ranked_candidates", "not_found")
#: CONTRACT-WAVE2 S3 echoes, forwarded verbatim whenever the service sends them: how it read the query.
_PASSTHROUGH_ECHO_KEYS: Final = ("query_intent", "context_query_used")


def _bounded(value: Any) -> Any:
    """Cap strings, lists and mappings so one oversized record cannot flood the model's context."""
    if isinstance(value, str):
        return value if len(value) <= _MAX_TEXT_CHARACTERS else f"{value[:_MAX_TEXT_CHARACTERS]} [truncated]"
    if isinstance(value, list):
        return [_bounded(item) for item in value[:_MAX_LIST_ITEMS]]
    if isinstance(value, dict):
        return {str(key): _bounded(item) for key, item in list(value.items())[:_MAX_MAPPING_ITEMS]}
    return value


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _mappings(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _pick(entry: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    """The named keys that carry a value, bounded."""
    return {key: _bounded(entry[key]) for key in keys if entry.get(key) not in (None, "", [], {})}


def _citations(value: Any) -> list[dict[str, Any]]:
    """Title/URL cards, one per distinct source."""
    seen: set[str] = set()
    cards: list[dict[str, Any]] = []
    for citation in _mappings(value):
        card = _pick(citation, _CITATION_KEYS)
        identity = str(card.get("url") or card.get("title") or "")
        if identity and identity not in seen:
            seen.add(identity)
            cards.append(card)
        if len(cards) >= _MAX_CITATIONS:
            break
    return cards


def _project_strategy_search(decoded: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **_pick(decoded, _SEARCH_ECHO_KEYS),
        "results": [_pick(hit, _STRATEGY_HIT_KEYS) for hit in _mappings(decoded.get("results"))[:MAX_RESULTS]],
    }


def _project_strategy_records(decoded: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "strategies": [
            {**_pick(record, _STRATEGY_RECORD_KEYS), "citations": _citations(record.get("citations"))}
            for record in _mappings(decoded.get("strategies"))[:MAX_STRATEGY_IDS]
        ],
        "not_found": _bounded(decoded.get("not_found") or []),
    }


def _project_finding_search(decoded: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **_pick(decoded, _SEARCH_ECHO_KEYS),
        "results": [
            {**_pick(finding, _FINDING_KEYS), "source": _pick(_mapping(finding.get("source")), _CITATION_KEYS)}
            for finding in _mappings(decoded.get("results"))[:MAX_RESULTS]
        ],
    }


_PROJECTIONS: Final[dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]]] = {
    "search_strategies": _project_strategy_search,
    "get_strategy": _project_strategy_records,
    "search_findings": _project_finding_search,
}
