"""Load the harvested literature label plane and map each envelope onto the governed streams.

Claim boundary, review contract, and the envelope-term coverage this mapper measures live in
`method/AGENTS.md` (this package) under `expert_label_plane.py`. In one line: these are
"what a trusted source recommends under these conditions" labels, they never enter the
`20260725_0013` causal plane, and nothing here can reach `approved`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from agri_data_service.foundation.canonical import canonical_json, sha256_digest

if TYPE_CHECKING:
    import uuid
    from collections.abc import Mapping, Sequence

    from agri_data_service.method.ml.covariates_v2 import SiteClimateTerms


REVIEW_TIER_AGENT_REVIEWED: Final = "agent_reviewed_pending_owner_signature"
REVIEW_TIER_OWNER_SIGNED: Final = "owner_signed"
AGENT_REVIEWER_IDENTITY: Final = "literature-label-harvest/adversarial-citation-verifier"

# Structured facts and a short attributed locator only; source prose is not reproduced.
DEFAULT_LICENSE_POSTURE: Final = "structured_facts_and_short_attributed_quote"

# A declared modelling choice, not a source value: the harvest states confidence as an ordinal
# and a fit needs a sample weight. Recorded in every artifact so the mapping is auditable.
CONFIDENCE_WEIGHTS: Final[Mapping[str, float]] = {"high": 0.9, "medium": 0.6, "low": 0.3}

# Envelope terms and how the governed streams can answer them. `direct` terms are derived from
# governed observations; `derived_proxy` is computed from governed observations through a named,
# cited formula; `unexpressible` has no stream behind it and is recorded as an explicit gap.
ENVELOPE_TERM_SUPPORT: Final[Mapping[str, str]] = {
    "mean_annual_precipitation_mm": "direct",
    "mean_annual_temperature_c": "direct",
    "growing_season_frost_free_days": "direct",
    "aridity": "derived_proxy",
    "elevation_m": "unexpressible",
    "soil_texture": "unexpressible",
    "usda_hardiness_zone": "unexpressible",
}
UNEXPRESSIBLE_TERM_REASON: Final[Mapping[str, str]] = {
    "elevation_m": (
        "No governed elevation stream reaches the covariate plane: agri.topography_profiles holds "
        "zero rows and agri.spatial_cell carries geometry without a height attribute."
    ),
    "soil_texture": (
        "No governed soil-texture stream reaches the covariate plane: agri.soil_profiles holds zero "
        "rows and the published SoilGrids rasters are not ingested as cell-level observations."
    ),
    "usda_hardiness_zone": (
        "Hardiness zone is a derived climate classification the warehouse does not compute; deriving "
        "it from the temperature stream would be a new governed signal, not a read."
    ),
}

# A point-valued envelope term ("MAP 283 mm") is a site description, not a boundary. Matching it
# exactly would match nothing, so each numeric term carries a declared tolerance, recorded per
# instance. Half-widths, applied to both ends of a stated range.
NUMERIC_TERM_TOLERANCE: Final[Mapping[str, float]] = {
    "mean_annual_precipitation_mm": 60.0,
    "mean_annual_temperature_c": 2.0,
    "growing_season_frost_free_days": 30.0,
    "elevation_m": 300.0,
}

# A numeric envelope term given as an array is exactly [low, high].
_RANGE_ELEMENT_COUNT: Final = 2

# A numeric envelope term given as an array is exactly [low, high].
_MAX_LABELS_PER_RELEASE: Final = 5_000
_MAX_INSTANCES_PER_LABEL: Final = 400
_IDENTITY_KEY_MAX_LENGTH: Final = 255
_DIGEST_KEY_LENGTH: Final = 16

LabelKind = Literal["species_fit", "strategy_outcome"]
Outcome = Literal["fit", "marginal", "unfit", "effective", "mixed", "ineffective"]
Confidence = Literal["high", "medium", "low"]

OUTCOMES_BY_KIND: Final[Mapping[str, tuple[str, ...]]] = {
    "species_fit": ("fit", "marginal", "unfit"),
    "strategy_outcome": ("effective", "mixed", "ineffective"),
}


class ExpertLabelPlaneError(RuntimeError):
    """Raised when a harvest document or a governed prerequisite cannot be honoured."""


class HarvestSource(BaseModel):
    """The cited work of one harvested label."""

    model_config = ConfigDict(extra="forbid")

    doi: str | None = None
    url: str | None = None
    title: str = Field(min_length=1, max_length=1000)
    journal: str | None = Field(default=None, max_length=500)
    year: int = Field(ge=1800, le=2100)
    supporting_quote_or_finding: str | None = Field(default=None, max_length=1000)


class HarvestCitationCheck(BaseModel):
    """The adversarial verifier's verdict on one label."""

    model_config = ConfigDict(extra="forbid")

    refuted: bool
    doi_resolves: bool
    reason: str = Field(min_length=1)


class HarvestLabel(BaseModel):
    """One harvested (condition envelope -> subject outcome) tuple with its lineage."""

    model_config = ConfigDict(extra="forbid")

    label_kind: LabelKind
    subject: str = Field(min_length=1, max_length=255)
    # Deliberately not `min_length=1`: a harvested label with no stated conditions is a real
    # thing the workflow produces, and it must be reported as unloadable with a reason rather
    # than crash the load of the other 28. `agri.expert_label_envelope_valid` refuses to store
    # one, so the partition in `load_labels` is what keeps the two rules in step.
    condition_envelope: dict[str, object]
    outcome: Outcome
    rationale: str = Field(min_length=1)
    source: HarvestSource
    confidence: Confidence
    harvest_slice: str = Field(min_length=1, max_length=120)
    citation_check: HarvestCitationCheck
    supporting_quote_or_finding: str | None = Field(default=None, max_length=1000)

    def resolved_quote(self) -> str | None:
        """The short attributed locator, wherever the harvest put it."""
        return self.supporting_quote_or_finding or self.source.supporting_quote_or_finding


class HarvestDocument(BaseModel):
    """The whole harvest run: what the verifiers kept and what they refuted."""

    model_config = ConfigDict(extra="forbid")

    harvested_at: str
    workflow: str
    kept: list[HarvestLabel]
    rejected: list[HarvestLabel]


def _validate_outcome_for_kind(label: HarvestLabel) -> None:
    allowed = OUTCOMES_BY_KIND[label.label_kind]
    if label.outcome not in allowed:
        raise ExpertLabelPlaneError(
            f"{label.label_kind} label for {label.subject!r} carries outcome {label.outcome!r}; "
            f"allowed: {', '.join(allowed)}"
        )


def _validate_envelope_terms(label: HarvestLabel) -> None:
    unknown = sorted(set(label.condition_envelope) - set(ENVELOPE_TERM_SUPPORT))
    if unknown:
        raise ExpertLabelPlaneError(
            f"label for {label.subject!r} uses envelope terms outside the vocabulary: {', '.join(unknown)}. "
            "Extend agri.expert_label_envelope_valid and ENVELOPE_TERM_SUPPORT in the same review."
        )


def load_harvest_document(path: Path) -> tuple[HarvestDocument, str]:
    """Read and validate a harvest document, returning it with the sha256 of its exact bytes."""
    raw = path.read_bytes()
    document = HarvestDocument.model_validate_json(raw)
    for label in (*document.kept, *document.rejected):
        _validate_outcome_for_kind(label)
        _validate_envelope_terms(label)
    if len(document.kept) + len(document.rejected) > _MAX_LABELS_PER_RELEASE:
        raise ExpertLabelPlaneError(f"harvest holds more than {_MAX_LABELS_PER_RELEASE} labels; split it into releases")
    return document, sha256_digest(raw)


def normalize_subject(subject: str) -> str:
    """Fold a subject to its join key: lowercase, single-spaced, underscores as spaces."""
    return " ".join(subject.replace("_", " ").lower().split())


def source_key_for(source: HarvestSource) -> str:
    """Deterministic identity for a cited work: its DOI where one exists, else a title digest."""
    if source.doi:
        return f"doi:{source.doi.strip().lower()}"[:_IDENTITY_KEY_MAX_LENGTH]
    digest = sha256_digest(canonical_json({"title": source.title, "year": source.year}))
    return f"work:{digest[:_DIGEST_KEY_LENGTH]}"


def source_checksum_for(source: HarvestSource) -> str:
    """Digest over the identity fields of a cited work."""
    return sha256_digest(
        canonical_json(
            {
                "digest_version": "agri_expert_label_source_v1",
                "doi": source.doi,
                "url": source.url,
                "title": source.title,
                "journal": source.journal,
                "year": source.year,
            }
        )
    )


def label_payload(label: HarvestLabel) -> dict[str, object]:
    """The canonical body a label's checksum and identity key derive from."""
    return {
        "digest_version": "agri_expert_label_v1",
        "label_kind": label.label_kind,
        "subject": label.subject,
        "subject_normalized": normalize_subject(label.subject),
        "outcome": label.outcome,
        "condition_envelope": label.condition_envelope,
        "rationale": label.rationale,
        "supporting_quote": label.resolved_quote(),
        "confidence": label.confidence,
        "harvest_slice": label.harvest_slice,
        "source_key": source_key_for(label.source),
        "source_checksum": source_checksum_for(label.source),
        "citation_check": {
            "refuted": label.citation_check.refuted,
            "doi_resolves": label.citation_check.doi_resolves,
            "reason": label.citation_check.reason,
        },
    }


def label_checksum_for(label: HarvestLabel) -> str:
    """Digest binding a label's content to its lineage and its citation verdict."""
    return sha256_digest(canonical_json(label_payload(label)))


def envelope_checksum_for(label: HarvestLabel) -> str:
    """Digest over the condition envelope alone, so a mapper run can be tied to what it matched."""
    return sha256_digest(
        canonical_json({"digest_version": "agri_expert_label_envelope_v1", "envelope": label.condition_envelope})
    )


def label_key_for(label: HarvestLabel) -> str:
    """Stable per-label identity, so re-loading the same harvest resolves rather than duplicates."""
    digest = label_checksum_for(label)
    subject = normalize_subject(label.subject).replace(" ", "-")[:60]
    return f"{label.label_kind}:{subject}:{digest[:_DIGEST_KEY_LENGTH]}"[:_IDENTITY_KEY_MAX_LENGTH]


def release_key_for(*, harvested_at: str, harvest_document_checksum: str) -> str:
    """Stable identity for one harvest release."""
    return f"literature-labels:{harvested_at}:{harvest_document_checksum[:_DIGEST_KEY_LENGTH]}"[
        :_IDENTITY_KEY_MAX_LENGTH
    ]


def loader_code_checksum() -> str:
    """Digest of this module's source, recorded so a release names the code that produced it."""
    return sha256_digest(Path(__file__).read_bytes())


@dataclass(frozen=True, slots=True)
class LabelLoadReport:
    """What one load of a harvest document wrote and in which review states it left the labels."""

    release_key: str
    release_id: uuid.UUID | None
    harvest_document_checksum: str
    release_checksum: str
    review_tier: str
    source_count: int
    label_count: int
    draft_count: int
    agent_reviewed_count: int
    rejected_count: int
    approved_count: int
    slice_summary: Mapping[str, Mapping[str, int]]
    unloadable: tuple[Mapping[str, str], ...]
    persisted: bool

    def to_summary(self) -> dict[str, object]:
        """The verb's one JSON line."""
        return {
            "unloadable_labels": [dict(entry) for entry in self.unloadable],
            "release_key": self.release_key,
            "release_id": None if self.release_id is None else str(self.release_id),
            "harvest_document_checksum": self.harvest_document_checksum,
            "release_checksum": self.release_checksum,
            "label_review_tier": self.review_tier,
            "source_count": self.source_count,
            "label_count": self.label_count,
            "counts_by_review_state": {
                "draft": self.draft_count,
                "agent_reviewed": self.agent_reviewed_count,
                "rejected": self.rejected_count,
                "approved": self.approved_count,
            },
            "slice_summary": {slice_name: dict(counts) for slice_name, counts in self.slice_summary.items()},
            "persisted": self.persisted,
            "owner_signature": "required for 'approved'; not held by this service",
        }


def summarize_slices(labels: Sequence[HarvestLabel], rejected: Sequence[HarvestLabel]) -> dict[str, dict[str, int]]:
    """Per-slice counts by outcome, plus how many that slice's verifier refuted."""
    summary: dict[str, dict[str, int]] = {}
    for label in labels:
        bucket = summary.setdefault(label.harvest_slice, {})
        bucket[label.outcome] = bucket.get(label.outcome, 0) + 1
        bucket["kept"] = bucket.get("kept", 0) + 1
    for label in rejected:
        bucket = summary.setdefault(label.harvest_slice, {})
        bucket["refuted"] = bucket.get("refuted", 0) + 1
    return summary


def release_checksum_for(*, harvest_document_checksum: str, label_keys: Sequence[str], review_tier: str) -> str:
    """Digest binding a release to the exact set of labels it holds."""
    return sha256_digest(
        canonical_json(
            {
                "digest_version": "agri_expert_label_release_v1",
                "harvest_document_checksum": harvest_document_checksum,
                "review_tier": review_tier,
                "label_keys": sorted(label_keys),
            }
        )
    )


@dataclass(frozen=True, slots=True)
class TermVerdict:
    """How one envelope term fared against the site's governed conditions on one day."""

    term: str
    support: str
    satisfied: bool | None
    site_value: object
    envelope_value: object
    tolerance: float | None
    reason: str | None = None

    def to_payload(self) -> dict[str, object]:
        """The rendering stored in an instance's `envelope_match`."""
        return {
            "support": self.support,
            "satisfied": self.satisfied,
            "site_value": self.site_value,
            "envelope_value": self.envelope_value,
            "tolerance": self.tolerance,
            "reason": self.reason,
        }


def _optional_number(value: object) -> float | None:
    """Read one end of a numeric envelope bound, or `None` when the source left it open."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExpertLabelPlaneError(f"numeric envelope bound has unusable shape {value!r}")
    return float(value)


def _numeric_bounds(envelope_value: object) -> tuple[float | None, float | None]:
    """Read a numeric envelope term as (minimum, maximum), whatever admitted shape it took."""
    if isinstance(envelope_value, (int, float)) and not isinstance(envelope_value, bool):
        value = float(envelope_value)
        return value, value
    if isinstance(envelope_value, list) and len(envelope_value) == _RANGE_ELEMENT_COUNT:
        return _optional_number(envelope_value[0]), _optional_number(envelope_value[1])
    if isinstance(envelope_value, dict):
        return _optional_number(envelope_value.get("min")), _optional_number(envelope_value.get("max"))
    raise ExpertLabelPlaneError(f"numeric envelope term has unusable shape {envelope_value!r}")


def _categorical_values(envelope_value: object) -> tuple[str, ...]:
    """Read a categorical envelope term as a tuple of labels."""
    if isinstance(envelope_value, str):
        return (envelope_value.strip().lower(),)
    if isinstance(envelope_value, list):
        return tuple(str(item).strip().lower() for item in envelope_value)
    raise ExpertLabelPlaneError(f"categorical envelope term has unusable shape {envelope_value!r}")


def evaluate_envelope(
    envelope: Mapping[str, object], climate: SiteClimateTerms
) -> tuple[dict[str, TermVerdict], tuple[str, ...], str]:
    """Compare one envelope against one day's site climate.

    Returns the per-term verdicts, the terms the governed streams cannot express, and the
    resulting match state. A term the streams cannot express is never counted as satisfied and
    never counted as violated -- it is carried out as an explicit gap.
    """
    verdicts: dict[str, TermVerdict] = {}
    unexpressible: list[str] = []
    expressible_count = 0
    violated = False

    site_numeric: Mapping[str, float | None] = {
        "mean_annual_precipitation_mm": climate.mean_annual_precipitation_mm,
        "mean_annual_temperature_c": climate.mean_annual_temperature_c,
        "growing_season_frost_free_days": (
            None if climate.growing_season_frost_free_days is None else float(climate.growing_season_frost_free_days)
        ),
    }

    for term, envelope_value in sorted(envelope.items()):
        support = ENVELOPE_TERM_SUPPORT.get(term)
        if support is None:
            raise ExpertLabelPlaneError(f"envelope term {term!r} is outside the vocabulary")
        if support == "unexpressible":
            unexpressible.append(term)
            verdicts[term] = TermVerdict(
                term=term,
                support=support,
                satisfied=None,
                site_value=None,
                envelope_value=envelope_value,
                tolerance=None,
                reason=UNEXPRESSIBLE_TERM_REASON[term],
            )
            continue

        if term == "aridity":
            site_class = climate.aridity
            wanted = _categorical_values(envelope_value)
            if site_class is None:
                verdicts[term] = TermVerdict(
                    term=term,
                    support=support,
                    satisfied=None,
                    site_value=None,
                    envelope_value=envelope_value,
                    tolerance=None,
                    reason="the trailing year was not complete enough to derive an aridity class",
                )
                continue
            expressible_count += 1
            satisfied = site_class in wanted
            violated = violated or not satisfied
            verdicts[term] = TermVerdict(
                term=term,
                support=support,
                satisfied=satisfied,
                site_value=site_class,
                envelope_value=envelope_value,
                tolerance=None,
                reason="UNEP aridity index from Hargreaves-Samani reference evapotranspiration",
            )
            continue

        site_value = site_numeric.get(term)
        if site_value is None:
            verdicts[term] = TermVerdict(
                term=term,
                support=support,
                satisfied=None,
                site_value=None,
                envelope_value=envelope_value,
                tolerance=None,
                reason="the trailing year was not complete enough to state this term",
            )
            continue
        low, high = _numeric_bounds(envelope_value)
        tolerance = NUMERIC_TERM_TOLERANCE[term]
        expressible_count += 1
        satisfied = (low is None or site_value >= low - tolerance) and (high is None or site_value <= high + tolerance)
        violated = violated or not satisfied
        verdicts[term] = TermVerdict(
            term=term,
            support=support,
            satisfied=satisfied,
            site_value=site_value,
            envelope_value=envelope_value,
            tolerance=tolerance,
        )

    if expressible_count == 0:
        match_state = "unexpressible"
    elif violated:
        match_state = "excluded"
    else:
        match_state = "matched"
    return verdicts, tuple(unexpressible), match_state


def bounded_issue_days(
    window_start: date, window_end: date, *, days_of_month: Sequence[int] = (1, 15)
) -> tuple[date, ...]:
    """A deterministic, bounded evaluation grid over a window.

    A label's envelope is a site description, so every day in the window would match or fail
    together with its neighbours; sampling a fixed grid keeps the instance count bounded and
    seasonally spread without a random draw a checksum could not reproduce.
    """
    days: list[date] = []
    ordinal = window_start.toordinal()
    end = window_end.toordinal()
    while ordinal <= end:
        candidate = date.fromordinal(ordinal)
        if candidate.day in days_of_month:
            days.append(candidate)
        ordinal += 1
    return tuple(days)


@dataclass(frozen=True, slots=True)
class MappingReport:
    """What one envelope-to-stream mapping run produced, including every unexpressible term."""

    release_key: str
    cell_id: str
    schema_version: str
    as_of_time: datetime
    label_count: int
    issue_day_count: int
    instance_count: int
    matched_count: int
    excluded_count: int
    unexpressible_count: int
    unexpressible_terms: Mapping[str, int]
    term_support: Mapping[str, str]
    climate_day_count: int
    persisted: bool

    def to_summary(self) -> dict[str, object]:
        """The verb's one JSON line."""
        return {
            "release_key": self.release_key,
            "cell_id": self.cell_id,
            "feature_schema_version": self.schema_version,
            "as_of_time": self.as_of_time.astimezone(UTC).isoformat(),
            "label_count": self.label_count,
            "issue_day_count": self.issue_day_count,
            "instance_count": self.instance_count,
            "instances_by_match_state": {
                "matched": self.matched_count,
                "excluded": self.excluded_count,
                "unexpressible": self.unexpressible_count,
            },
            "envelope_term_support": dict(self.term_support),
            "unexpressible_term_counts": dict(self.unexpressible_terms),
            "climate_day_count": self.climate_day_count,
            "persisted": self.persisted,
        }
