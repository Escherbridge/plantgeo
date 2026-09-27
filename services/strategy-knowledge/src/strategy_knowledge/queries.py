"""Validated search requests: what the MCP tools build from their parameters and the eval harness from JSON."""

from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field

from strategy_knowledge.search import DEFAULT_FAMILY_DIVERSITY
from strategy_knowledge.site_profile import SiteProfile
from strategy_knowledge.vocabulary import (
    Category,
    ContentType,
    Direction,
    EvidenceStrength,
    FirePhase,
    Goal,
    LandUse,
    Region,
    Relevance,
    Scale,
    SoilCondition,
    StudyType,
)

DEFAULT_LIMIT: Final = 10
MAXIMUM_LIMIT: Final = 50
MAXIMUM_FAMILY_DIVERSITY: Final = 20

Query = Annotated[str, Field(min_length=1, description="Natural-language description of the need or question.")]
Limit = Annotated[int, Field(ge=1, le=MAXIMUM_LIMIT, description="Results per page (1-50).")]
Offset = Annotated[int, Field(ge=0, description="Results to skip; pass the previous response's next_offset.")]


class SearchRequest(BaseModel):
    """Fields every search shares."""

    model_config = ConfigDict(extra="forbid")

    query: Query
    limit: Limit = DEFAULT_LIMIT
    offset: Offset = 0


class StrategySearch(SearchRequest):
    """`search_strategies` parameters."""

    goals: list[Goal] = Field(default_factory=list)
    land_use: list[LandUse] = Field(default_factory=list)
    region: list[Region] = Field(default_factory=list)
    soil_conditions: list[SoilCondition] = Field(default_factory=list)
    scale: list[Scale] = Field(default_factory=list)
    category: list[Category] = Field(default_factory=list)
    fire_phase: list[FirePhase] = Field(default_factory=list)
    site_profile: SiteProfile | None = None
    min_evidence: EvidenceStrength | None = None
    family_diversity: Annotated[int, Field(ge=0, le=MAXIMUM_FAMILY_DIVERSITY)] = DEFAULT_FAMILY_DIVERSITY


class FindingSearch(SearchRequest):
    """`search_findings` parameters."""

    goals: list[Goal] = Field(default_factory=list)
    land_use: list[LandUse] = Field(default_factory=list)
    region: list[Region] = Field(default_factory=list)
    site_profile: SiteProfile | None = None
    min_evidence: EvidenceStrength | None = None
    study_type: list[StudyType] = Field(default_factory=list)
    direction: Direction | None = None
    strategy_id: str | None = None


class PassageSearch(SearchRequest):
    """`search_passages` parameters."""

    source_id: str | None = None
    content_type: list[ContentType] = Field(default_factory=list)
    relevance: list[Relevance] = Field(default_factory=list)
    strategy_id: str | None = None
