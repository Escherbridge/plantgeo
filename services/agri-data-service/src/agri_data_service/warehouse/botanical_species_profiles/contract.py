"""Bounded botanical identity and evidence models; see this directory's AGENTS.md."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime  # noqa: TC003 - Pydantic resolves timestamp fields at runtime.
from typing import Final, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FIRST_PRINTABLE_CODEPOINT = 32
MAX_TAXA = 100
MAX_ASSERTIONS = 10_000
MAX_OBJECT_BYTES = 32 * 1024 * 1024
MAX_RELEASE_BYTES = 64 * 1024 * 1024
SCHEMA_VERSION: Final = "1"
NORMALIZATION_RECIPE: Final = "identity-and-temperature-v1"
RECONCILIATION_POLICY: Final = "conservative-evidence-v1"
RELEASE_PATTERN = re.compile(r"bspf-[0-9a-f]{64}")

SectionName = Literal[
    "growth_requirements",
    "fuel_traits",
    "fire_response",
    "agricultural_roles",
    "companion_evidence",
    "objective_effects",
]
FieldState = Literal["known", "unknown", "conflict", "refused", "withdrawn"]
EvidenceKind = Literal[
    "measured_trait",
    "curated_requirement",
    "categorical_summary",
    "reviewed_literature",
    "occurrence_association",
    "reviewed_effect",
]

TRAIT_SECTIONS: dict[str, SectionName] = {
    "growth_habit": "growth_requirements",
    "climate_summary": "growth_requirements",
    "native_status": "growth_requirements",
    "hardiness_range": "growth_requirements",
    "temperature_envelope": "growth_requirements",
    "precipitation_envelope": "growth_requirements",
    "light_requirement": "growth_requirements",
    "soil_moisture": "growth_requirements",
    "soil_drainage": "growth_requirements",
    "soil_texture": "growth_requirements",
    "soil_ph": "growth_requirements",
    "salinity": "growth_requirements",
    "elevation": "growth_requirements",
    "drought_tolerance": "growth_requirements",
    "phenology": "growth_requirements",
    "tissue_water_content": "fuel_traits",
    "live_fuel_moisture": "fuel_traits",
    "dead_fuel_moisture": "fuel_traits",
    "leaf_dry_matter_content": "fuel_traits",
    "volatile_oils": "fuel_traits",
    "resins": "fuel_traits",
    "extractives": "fuel_traits",
    "heat_content": "fuel_traits",
    "ash_content": "fuel_traits",
    "mineral_content": "fuel_traits",
    "surface_area_to_volume_ratio": "fuel_traits",
    "bulk_density": "fuel_traits",
    "curing": "fuel_traits",
    "canopy_architecture": "fuel_traits",
    "branching_architecture": "fuel_traits",
    "litter_persistence": "fuel_traits",
    "litter_behavior": "fuel_traits",
    "fuel_bed_behavior": "fuel_traits",
    "flammability": "fuel_traits",
    "fire_tolerance": "fire_response",
    "fire_response": "fire_response",
    "post_fire_recovery": "fire_response",
    "agricultural_use": "agricultural_roles",
    "pollinator_value": "agricultural_roles",
    "edible": "agricultural_roles",
    "timber_value": "agricultural_roles",
    "guild_roles": "agricultural_roles",
    "nitrogen_fixation": "agricultural_roles",
    "rooting_form": "agricultural_roles",
    "biomass_accumulation": "agricultural_roles",
    "companion_relationship": "companion_evidence",
    "objective_effect": "objective_effects",
}
SECTION_NAMES: tuple[SectionName, ...] = (
    "growth_requirements",
    "fuel_traits",
    "fire_response",
    "agricultural_roles",
    "companion_evidence",
    "objective_effects",
)


class ProfileError(ValueError):
    """A botanical profile request violated its bounded contract."""


class ProfileUnavailableError(ProfileError):
    """The explicitly pinned published release is unavailable."""


class ProfileIntegrityError(ProfileError):
    """Published bytes or evidence disagree with their content identity."""


class ProfileConflictError(ProfileError):
    """An immutable object or conditional release pointer conflicted."""


class FrozenModel(BaseModel):
    """Strict evidence record with bounded strings and no undeclared fields."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_max_length=2000, allow_inf_nan=False)


def canonical_bytes(value: object) -> bytes:
    """Encode the stable, finite JSON representation used for content identities."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def content_sha256(payload: bytes) -> str:
    """Hash exact object bytes."""
    return hashlib.sha256(payload).hexdigest()


def require_release_id(value: str) -> str:
    """Validate an exact release identifier before constructing any storage key."""
    if not RELEASE_PATTERN.fullmatch(value):
        raise ProfileError("release_id must be bspf- followed by a lowercase SHA-256 digest")
    return value


def _require_text(value: str) -> str:
    if not value or value != value.strip() or any(ord(char) < FIRST_PRINTABLE_CODEPOINT for char in value):
        raise ValueError("identity and evidence text must be nonempty, trimmed and contain no controls")
    return value


def _require_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("source evidence requires an absolute public HTTP(S) URL without credentials")
    return _require_text(value)


def _require_utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("evidence timestamps must use UTC")
    return value


class TaxonIdentity(FrozenModel):
    """One canonical authority/version/concept triple; names never act as join keys."""

    authority: str = Field(min_length=1, max_length=128)
    authority_version: str = Field(min_length=1, max_length=128)
    taxon_id: str = Field(min_length=1, max_length=200)

    _identity_text = field_validator("authority", "authority_version", "taxon_id")(_require_text)

    @property
    def key(self) -> str:
        """Return an unambiguous content key for this concept."""
        return content_sha256(canonical_bytes(self.model_dump(mode="json")))


class TaxonName(FrozenModel):
    """A source-identified synonym or cultivar retained with its accepted concept."""

    name: str = Field(min_length=1)
    source_name_id: str = Field(min_length=1)
    accepted_taxon_id: str = Field(min_length=1)
    status: Literal["synonym", "cultivar"]
    source_id: str = Field(min_length=1)
    source_version: str | None = None
    source_record_json: str | None = Field(default=None, max_length=32768)


class TaxonRecord(FrozenModel):
    """A canonical accepted taxon plus explicitly identified source names."""

    identity: TaxonIdentity
    accepted_name: str = Field(min_length=1)
    rank: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    source_record_json: str | None = Field(default=None, max_length=32768)
    synonyms: tuple[TaxonName, ...] = Field(default=(), max_length=200)
    cultivars: tuple[TaxonName, ...] = Field(default=(), max_length=200)
    authoring_row_id: str | None = None
    authoring_provenance: str = "source-direct-reviewed-snapshot"

    @model_validator(mode="after")
    def validate_names(self) -> Self:
        for expected_status, names in (("synonym", self.synonyms), ("cultivar", self.cultivars)):
            for name in names:
                if name.accepted_taxon_id != self.identity.taxon_id or name.status != expected_status:
                    raise ValueError("source names must explicitly join to their canonical accepted taxon")
        return self


class SourceRelease(FrozenModel):
    """One admitted machine-readable source and its exact reviewed reuse evidence."""

    source_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    release_url: str
    download_url: str
    licence_id: str = Field(min_length=1)
    licence_url: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    terms_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retrieved_at: datetime
    reviewer: str = Field(min_length=1)
    reviewed_at: datetime
    review_basis: str = Field(min_length=1)
    machine_readable_verified: Literal[True]
    reuse_allowed: Literal[True]
    access: Literal["public"] = "public"
    admitted_traits: tuple[str, ...] = Field(default=(), max_length=100)
    admitted_taxon_ids: tuple[str, ...] = Field(default=(), max_length=MAX_TAXA)

    _urls = field_validator("release_url", "download_url", "licence_url")(_require_url)
    _timestamps = field_validator("retrieved_at", "reviewed_at")(_require_utc)

    @field_validator("admitted_traits")
    @classmethod
    def validate_admitted_traits(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(trait not in TRAIT_SECTIONS for trait in value) or len(set(value)) != len(value):
            raise ValueError("admitted traits must be unique terms from the botanical vocabulary")
        return value


class TraitValue(FrozenModel):
    """One typed scalar or numeric interval, retaining its declared unit."""

    text: str | None = None
    number: float | None = None
    boolean: bool | None = None
    lower: float | None = None
    upper: float | None = None
    unit: str | None = None

    @model_validator(mode="after")
    def validate_value(self) -> Self:
        scalar_count = sum(value is not None for value in (self.text, self.number, self.boolean))
        is_range = self.lower is not None or self.upper is not None
        if scalar_count + int(is_range) != 1:
            raise ValueError("a trait value must have exactly one scalar or interval representation")
        if is_range and (self.lower is None or self.upper is None or self.lower > self.upper):
            raise ValueError("numeric intervals require ordered lower and upper bounds")
        for value in (self.number, self.lower, self.upper):
            if value is not None and not math.isfinite(value):
                raise ValueError("numeric values must be finite")
        return self


class AssertionContext(FrozenModel):
    """Explicit measurement context; absent context remains null."""

    tissue_or_component: str | None = None
    live_dead_state: str | None = None
    moisture_basis: str | None = None
    season: str | None = None
    life_stage: str | None = None
    geography: str | None = None
    environment: str | None = None
    conditions: str | None = None


class TraitAssertion(FrozenModel):
    """An attributable value, missingness record, correction or withdrawal."""

    assertion_id: str = Field(min_length=1, max_length=200)
    identity: TaxonIdentity
    trait: str
    source_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    source_url: str
    source_record_id: str = Field(min_length=1)
    licence_id: str = Field(min_length=1)
    evidence_locator: str = Field(min_length=1)
    evidence_kind: EvidenceKind
    raw_value: TraitValue | None = None
    normalized_value: TraitValue | None = None
    qualifier: str | None = None
    method: str | None = None
    context: AssertionContext = AssertionContext()
    retrieved_at: datetime
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    missingness: Literal["not_reported", "not_measured", "not_applicable", "restricted", "unresolved"] | None = None
    review_state: Literal["approved", "unreviewed", "rejected", "withdrawn"]
    reviewer: str | None = None
    review_reason: str = Field(min_length=1)
    corrects_assertion_id: str | None = None
    withdraws_assertion_id: str | None = None
    related_taxon: TaxonIdentity | None = None
    authoring_row_id: str | None = None
    authoring_provenance: str = Field(min_length=1)

    _source_url = field_validator("source_url")(_require_url)
    _retrieved = field_validator("retrieved_at")(_require_utc)

    @field_validator("trait")
    @classmethod
    def validate_trait(cls, value: str) -> str:
        if value not in TRAIT_SECTIONS:
            raise ValueError("trait is outside the botanical profile vocabulary")
        return value

    @model_validator(mode="after")
    def validate_assertion(self) -> Self:
        if self.missingness is not None and (self.raw_value is not None or self.normalized_value is not None):
            raise ValueError("missingness cannot carry a value")
        if self.missingness is None and not self.withdraws_assertion_id and self.raw_value is None:
            raise ValueError("a nonmissing assertion requires its original value")
        if (self.raw_value is None) != (self.normalized_value is None):
            raise ValueError("raw and normalized values must both be present or both absent")
        if self.review_state in {"approved", "withdrawn"} and not self.reviewer:
            raise ValueError("approved assertions and withdrawals require an attributable reviewer")
        for instant in (self.valid_from, self.valid_to):
            if instant is not None:
                _require_utc(instant)
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("assertion validity interval is reversed")
        return self


class ReconciliationDecision(FrozenModel):
    """One deterministic taxon/trait decision retaining every candidate identifier."""

    decision_id: str
    identity: TaxonIdentity
    trait: str
    state: FieldState
    assertion_ids: tuple[str, ...]
    selected_assertion_ids: tuple[str, ...]
    excluded_assertion_ids: tuple[str, ...]
    value: TraitValue | None
    reason: str


class ProfileField(FrozenModel):
    """A published field with explicit knowledge state and evidence links."""

    trait: str
    state: FieldState
    value: TraitValue | None
    assertion_ids: tuple[str, ...]
    decision_id: str
    reason: str


class ProfileSection(FrozenModel):
    """A distinct growth, fuel, response, role, companion or effect evidence section."""

    name: SectionName
    fields: tuple[ProfileField, ...]


class SpeciesProfile(FrozenModel):
    """Approved published values and explicit gaps for one canonical taxon."""

    identity: TaxonIdentity
    accepted_name: str
    sections: tuple[ProfileSection, ...]


class ReleaseRequest(FrozenModel):
    """The full reviewed, bounded authoring snapshot used to bind a release."""

    taxa: tuple[TaxonRecord, ...] = Field(min_length=1, max_length=MAX_TAXA)
    assertions: tuple[TraitAssertion, ...] = Field(default=(), max_length=MAX_ASSERTIONS)
    sources: tuple[SourceRelease, ...] = Field(min_length=1, max_length=20)
    reviewer: str = Field(min_length=1)
    review_decision_id: str = Field(min_length=1)
    reviewed_at: datetime
    authoring_census_state: Literal["unavailable", "reviewed_snapshot"] = "unavailable"
    authoring_census_note: str = Field(min_length=1)
    normalization_recipe: Literal["identity-and-temperature-v1"] = NORMALIZATION_RECIPE
    reconciliation_policy: Literal["conservative-evidence-v1"] = RECONCILIATION_POLICY

    _reviewed = field_validator("reviewed_at")(_require_utc)


class ArtifactReceipt(FrozenModel):
    """A content-addressed required release artifact."""

    name: Literal["taxa", "assertions", "decisions", "profiles"]
    key: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=1, le=MAX_OBJECT_BYTES)
    row_count: int = Field(ge=0, le=MAX_ASSERTIONS)


class ReleaseManifest(FrozenModel):
    """The required artifacts and all review/source inputs binding a release ID."""

    release_id: str
    schema_version: Literal["1"] = SCHEMA_VERSION
    source_releases: tuple[SourceRelease, ...]
    normalization_recipe: str
    reconciliation_policy: str
    reviewer: str
    review_decision_id: str
    reviewed_at: datetime
    authoring_census_state: Literal["unavailable", "reviewed_snapshot"]
    authoring_census_note: str
    artifacts: tuple[ArtifactReceipt, ...]
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PublishedRelease(FrozenModel):
    """A fully verified immutable release with no editable-row serving fallback."""

    release_id: str
    manifest: ReleaseManifest
    taxa: tuple[TaxonRecord, ...]
    assertions: tuple[TraitAssertion, ...]
    decisions: tuple[ReconciliationDecision, ...]
    profiles: tuple[SpeciesProfile, ...]

    def lookup(self, identity: TaxonIdentity) -> SpeciesProfile | None:
        """Match only the complete canonical identity triple."""
        return next((profile for profile in self.profiles if profile.identity == identity), None)
