"""Pydantic models for the corpus records of DESIGN.md sections 5-7 and the service's own corpus files."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from strategy_knowledge.vocabulary import (
    Category,
    ContentType,
    Direction,
    EvidenceStrength,
    FirePhase,
    Goal,
    GoalState,
    LandUse,
    Level,
    Region,
    Relevance,
    Scale,
    SoilCondition,
    SourceType,
    StudyType,
    VariableRole,
    WildfireRelevance,
)

Goals = dict[Goal, GoalState]


class CorpusModel(BaseModel):
    """Base for every corpus record: tolerant of fields a newer producer adds."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Citation(CorpusModel):
    """One verbatim excerpt supporting a strategy (strategy_schema.md Citation)."""

    source_id: str
    locator: str | None = None
    excerpt: str = ""


class StrategyFacets(CorpusModel):
    """The four paraphrased facet texts of DESIGN.md section 6."""

    overview: str | None = None
    how_to: str | None = None
    fit: str | None = None
    outcomes: str | None = None


class StrategyRecord(CorpusModel):
    """A concrete land-management intervention (strategy_schema.md StrategyRecord)."""

    strategy_id: str
    name: str
    summary: str = ""
    category: Category
    secondary_categories: list[Category] = Field(default_factory=list)
    fire_phase: list[FirePhase] = Field(default_factory=list)
    land_use: list[LandUse] = Field(default_factory=list)
    region: list[Region] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    application_rate: str | None = None
    timing: str | None = None
    soil_conditions: list[SoilCondition] = Field(default_factory=list)
    slope_guidance: str | None = None
    scale: list[Scale] = Field(default_factory=list)
    equipment: list[str] = Field(default_factory=list)
    cost_level: Level | None = None
    labor_intensity: Level | None = None
    time_to_effect: str | None = None
    benefits: list[str] = Field(default_factory=list)
    risks_limitations: list[str] = Field(default_factory=list)
    nrcs_practice_code: str | None = None
    evidence_strength: EvidenceStrength
    sources: list[Citation] = Field(default_factory=list)
    notes: str | None = None


class RegistryStrategy(StrategyRecord):
    """A canonical registry strategy (DESIGN.md section 7): the record plus goals, family and facets."""

    goals: Goals = Field(default_factory=dict)
    family_id: str | None = None
    merged_from: list[str] = Field(default_factory=list)
    facets: StrategyFacets = Field(default_factory=StrategyFacets)
    review_state: str | None = None
    #: Lay and technical words a land manager types for this need; BM25-only (AGENTS.md "Metadata").
    search_terms: list[str] = Field(default_factory=list)

    @field_validator("search_terms", mode="before")
    @classmethod
    def search_terms_default_when_null(cls, value: object) -> object:
        """A registry row written before search terms existed may carry `search_terms: null`."""
        return [] if value is None else value

    @field_validator("facets", mode="before")
    @classmethod
    def facets_default_when_null(cls, value: object) -> object:
        """A registry row not yet faceted may carry `facets: null`; the indexer then falls back to the summary."""
        return {} if value is None else value


class CandidateStrategy(StrategyRecord):
    """A strategy an agent proposed while chunking (DESIGN.md section 5 CandidateStrategy)."""

    name: str | None = None
    summary: str | None = None
    goals: Goals = Field(default_factory=dict)
    matches_existing: str | None = None
    proposed_family: str | None = None
    origin_slice: str | None = None

    @model_validator(mode="after")
    def require_identity_for_new_strategies(self) -> "CandidateStrategy":
        """A new strategy needs a name and summary; a merge into `matches_existing` may carry only its additions."""
        if self.matches_existing is None and not (self.name and self.summary):
            raise ValueError("a new candidate strategy (no matches_existing) needs a name and a summary")
        return self


class Family(CorpusModel):
    """A registry family grouping regional and rate variants of one intervention."""

    family_id: str
    name: str | None = None
    description: str | None = None
    member_strategy_ids: list[str] = Field(default_factory=list)


class Chunk(CorpusModel):
    """One coherent idea unit of a raw file, by line range (DESIGN.md section 5 Chunk)."""

    chunk_id: str
    line_start: int
    line_end: int
    section_path: list[str] = Field(default_factory=list)
    title: str = ""
    content_type: ContentType
    relevance: Relevance
    summary: str = ""
    goals: Goals = Field(default_factory=dict)
    land_use: list[LandUse] = Field(default_factory=list)
    region: list[Region] = Field(default_factory=list)
    soil_conditions: list[SoilCondition] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    linked_strategy_ids: list[str] = Field(default_factory=list)
    linked_finding_ids: list[str] = Field(default_factory=list)


class SkippedRange(CorpusModel):
    """A line range deliberately left out of every chunk."""

    line_start: int
    line_end: int
    reason: str = ""


class FindingVariable(CorpusModel):
    """A driver or response variable of a finding."""

    name: str
    role: VariableRole


class Finding(CorpusModel):
    """A measured, reviewed or quantified result (DESIGN.md section 5 Finding)."""

    finding_id: str
    claim: str
    variables: list[FindingVariable] = Field(default_factory=list)
    direction: Direction
    magnitude: str | None = None
    conditions: str | None = None
    study_type: StudyType
    evidence_strength: EvidenceStrength
    goals: Goals = Field(default_factory=dict)
    region: list[Region] = Field(default_factory=list)
    land_use: list[LandUse] = Field(default_factory=list)
    linked_strategy_ids: list[str] = Field(default_factory=list)
    excerpt: str = ""
    line_start: int = 0
    line_end: int = 0


class SourceEntry(CorpusModel):
    """One row of `corpus/sources.json`: the SourceRecord plus the raw file's hash and fetch facts."""

    source_id: str
    url: str | None = None
    final_url: str | None = None
    title: str | None = None
    publisher: str | None = None
    year: int | None = None
    source_type: SourceType | None = None
    region_focus: list[Region] = Field(default_factory=list)
    wildfire_relevance: WildfireRelevance | None = None
    summary: str | None = None
    goals: Goals = Field(default_factory=dict)
    sha256: str | None = None
    line_count: int | None = None
    fetched_at: str | None = None
    kind: str | None = None
    review_state: str | None = None


class ChunkPlan(CorpusModel):
    """`corpus/chunk_plans/<source_id>.json`: chunks, skipped ranges and candidates, never text."""

    source_id: str
    raw_sha256: str | None = None
    assigned_ranges: list[tuple[int, int]] = Field(default_factory=list)
    chunks: list[Chunk] = Field(default_factory=list)
    skipped: list[SkippedRange] = Field(default_factory=list)
    candidate_strategies: list[CandidateStrategy] = Field(default_factory=list)


class FindingsFile(CorpusModel):
    """`corpus/findings/<source_id>.json`."""

    source_id: str
    raw_sha256: str | None = None
    findings: list[Finding] = Field(default_factory=list)


class PassageWindow(CorpusModel):
    """One <= 180-word window of a chunk's verbatim text, the unit the `passages` collection indexes."""

    passage_id: str
    chunk_id: str
    window_index: int
    source_id: str
    line_start: int
    line_end: int
    word_count: int
    text: str


def dump_record(record: BaseModel) -> dict[str, Any]:
    """Serialise a record the way corpus files store it (JSON-mode, unset optional fields kept)."""
    return record.model_dump(mode="json")
