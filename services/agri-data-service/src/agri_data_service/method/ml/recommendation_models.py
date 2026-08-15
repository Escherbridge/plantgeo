"""Model A (species fit) and Model B (strategy selection) over the expert label plane.

Both are literature-grounded recommenders: they learn how a source's stated condition envelope
relates to a stated outcome, and they score a query site by comparing its governed conditions
against each candidate's envelope. Nothing here is a causal effect claim, and Model B writes to
no selection or publication surface. Method limits, the effective-sample-size caveat and the
scoring policy live in `method/AGENTS.md` (this package) under `recommendation_models.py`.

A design row is four blocks: the site (governed climate and covariates, drought included), the
envelope the source stated, the subject's identity -- carried by BOTH models now, not only Model
B -- and the subject's species traits. Identity says which plant this is; traits say what kind of
plant it is, which is the only thing left to generalize from when a cross-validation fold removes
every source that names the species. Each row is weighted `confidence_weight` divided by the
label's matched instance count, so one label is one unit of evidence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

import numpy as np
from sklearn.linear_model import LogisticRegression

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.method.ml.expert_label_plane import (
    ENVELOPE_TERM_SUPPORT,
    NUMERIC_TERM_TOLERANCE,
    ExpertLabelPlaneError,
)

if TYPE_CHECKING:
    import uuid
    from collections.abc import Mapping, Sequence


ModelKind = Literal["species_fit", "strategy_selection"]

MODEL_NAME_BY_KIND: Final[Mapping[str, str]] = {
    "species_fit": "species_fit_compatibility_logistic_v1",
    "strategy_selection": "strategy_selection_compatibility_logistic_v1",
}
LABEL_KIND_BY_MODEL_KIND: Final[Mapping[str, str]] = {
    "species_fit": "species_fit",
    "strategy_selection": "strategy_outcome",
}
CLAIM_TIER: Final = "literature_grounded_recommendation_with_citations"
EVALUATION_LABEL: Final = "evaluation_only"
EVALUATION_DISCLAIMER: Final = (
    "Literature-grounded recommendation trained on agent-reviewed published labels pending an "
    "owner signature. Not a causal effect estimate, not an operational plan, not life-safety valid."
)

# Ordinal utility per outcome, used to turn class probabilities into one ranking score.
OUTCOME_UTILITY: Final[Mapping[str, float]] = {
    "fit": 1.0,
    "marginal": 0.5,
    "unfit": 0.0,
    "effective": 1.0,
    "mixed": 0.5,
    "ineffective": 0.0,
}
CLASS_ORDER_BY_MODEL_KIND: Final[Mapping[str, tuple[str, ...]]] = {
    "species_fit": ("unfit", "marginal", "fit"),
    "strategy_selection": ("ineffective", "mixed", "effective"),
}

# Which harvest slices carry wildfire- and water-objective evidence. The objectives are scoring
# weights over LABEL PROVENANCE -- "how much of this candidate's evidence came from the fire or
# the water literature" -- never a claimed wildfire or hydrological effect.
WILDFIRE_EVIDENCE_SLICES: Final = ("strategy-reforestation-fire",)
WATER_EVIDENCE_SLICES: Final = ("strategy-water-harvesting",)

# The covariate positions the compatibility model reads. A deliberate subset of the pinned
# vector: the 28-day rolling means describe the site's recent regime, the drought block carries
# the site's standing water deficit, and the calendar pair carries season. The lag-1/2/3
# meteorology positions are weather, not site character, and would inject day-level noise into a
# model whose target is a time-invariant literature claim.
#
# The drought block (covariate schema indices 36-38: USDM severity at lag 1 and lag 7 plus the
# imputation flag) was previously excluded. It is included now because drought is one of the
# stated objectives and because a recommendation conditioned on region is exactly a
# recommendation conditioned on standing water deficit -- the 28-day precipitation mean describes
# the last month, not the multi-season deficit a USDM class encodes.
#
# CONSEQUENCE, stated plainly: the USDM completeness mask is now BLOCKING. `build_design_row`
# returns `None` for any cell-day whose drought covariates are absent, so those days are counted
# in `skipped_incomplete_rows` and never train. A day the drought stream has not reached is a
# declared gap, not a zero. Un-zeroing `agri.drought_polygon_snapshot` therefore stops being
# optional for training coverage, and the imputed-flag position keeps an imputed severity
# distinguishable from an observed one rather than laundering the two together.
SITE_COVARIATE_FEATURES: Final[tuple[str, ...]] = (
    "air_temperature_mean_roll_mean_28",
    "precipitation_roll_mean_28",
    "relative_humidity_roll_mean_28",
    "wind_speed_roll_mean_28",
    "dew_point_temperature_roll_mean_28",
    "drought_severity_class_lag_1",
    "drought_severity_class_lag_7",
    "drought_severity_imputed_lag_1",
    "day_of_year_sin",
    "day_of_year_cos",
)
SITE_CLIMATE_FEATURES: Final[tuple[str, ...]] = (
    "mean_annual_precipitation_mm",
    "mean_annual_temperature_c",
    "growing_season_frost_free_days",
    "aridity_index",
)
_NUMERIC_ENVELOPE_TERMS: Final[tuple[str, ...]] = (
    "mean_annual_precipitation_mm",
    "mean_annual_temperature_c",
    "growing_season_frost_free_days",
    "elevation_m",
)
ARIDITY_CLASSES: Final[tuple[str, ...]] = (
    "hyper_arid",
    "arid",
    "semi_arid",
    "dry_subhumid",
    "humid",
)

# Species traits, read from `agri.species` and injected here as an already-fetched mapping so
# this module keeps doing no database work. They are what lets a held-out species be scored from
# the KIND of plant it is -- growth habit, drought tolerance, precipitation and pH range, nitrogen
# fixation -- rather than from a subject one-hot that the leave-one-source-out fold just removed.
#
# `agri.species` holds zero rows in production, so absence is the common case and is encoded
# rather than imputed: every trait block carries a `__stated` position, and a subject with no
# trait record encodes as all zeros with every `__stated` at 0.0 and `record_present` at 0.0.
# That is a recorded absence the coefficients can learn to ignore -- exactly the posture the
# envelope terms already take when a source stated no bound -- and it is the reason a missing
# trait does NOT make `build_design_row` return `None`: traits are a declared gap, while the
# governed site covariates are a required input.
SPECIES_TRAIT_RANGE_TERMS: Final[tuple[tuple[str, str, str], ...]] = (
    ("precipitation_mm", "min_precip_mm", "max_precip_mm"),
    ("ph", "min_ph", "max_ph"),
    # One range-typed column rather than a min/max pair, hence the repeated key.
    ("usda_zones", "usda_zones", "usda_zones"),
)
SPECIES_TRAIT_BOOLEAN_TERMS: Final[tuple[str, ...]] = ("nitrogen_fixer", "edible", "timber_value")
SPECIES_TRAIT_CATEGORICAL_TERMS: Final[tuple[str, ...]] = (
    "growth_habit",
    "native_status",
    "light_requirement",
    "drought_tolerance",
    "salt_tolerance",
    "pollinator_value",
)
# Array-valued traits, one-hot per member rather than per value.
SPECIES_TRAIT_MULTI_VALUE_TERMS: Final[tuple[str, ...]] = ("guild_roles",)

DEFAULT_REGULARIZATION_STRENGTH: Final = 1.0
DEFAULT_MAX_ITERATIONS: Final = 2_000
DEFAULT_RANDOM_SEED: Final = 20260814
_MAX_TRAINING_ROWS: Final = 200_000
_MIN_LABELS_FOR_FIT: Final = 6
_IDENTITY_KEY_MAX_LENGTH: Final = 255
_DIGEST_KEY_LENGTH: Final = 16
_STANDARDIZATION_FLOOR: Final = 1e-9
# A numeric envelope term given as an array is exactly [low, high]; a two-class fit is the
# one case sklearn expresses with a single coefficient row and a logistic link.
_RANGE_ELEMENT_COUNT: Final = 2
_BINARY_CLASS_COUNT: Final = 2
# A fold whose training split holds fewer classes than this cannot be fitted at all.
_MIN_CLASSES_FOR_FIT: Final = 2
_DEVIATION_TERMS: Final[tuple[str, ...]] = (
    "mean_annual_precipitation_mm",
    "mean_annual_temperature_c",
    "growing_season_frost_free_days",
)
TRAINING_DEFINITION_NAME: Final = "agri.recommendation.train"
TRAINING_DEFINITION_VERSION: Final = "2026-08-14"
TRAINING_HANDLER_TOKEN: Final = "method.ml.recommendation_train"
TRAINING_QUEUE_NAME: Final = "forecast"
TRAINING_MAX_ATTEMPTS: Final = 3
TRAINING_LEASE_SECONDS: Final = 1_800
TRAINING_TIME_BUDGET_SECONDS: Final = 1_500
ARTIFACT_URI_PREFIX: Final = "agri-eval://recommendation-model"


class RecommendationTrainingError(RuntimeError):
    """Raised when a recommendation fit or its receipt cannot be produced honestly."""


def training_code_checksum() -> str:
    """Digest of this module's source, recorded so a receipt names the code that produced it."""
    return sha256_digest(Path(__file__).read_bytes())


def _require_number(value: object, name: str) -> float:
    """Read a governed numeric that the caller has already proved present."""
    number = _optional_number(value)
    if number is None:
        raise RecommendationTrainingError(f"{name} was expected to carry a value here")
    return number


def _optional_number(value: object) -> float | None:
    """Read one end of a numeric envelope bound, or `None` when the source left it open."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RecommendationTrainingError(f"numeric envelope bound has unusable shape {value!r}")
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
    raise RecommendationTrainingError(f"numeric envelope term has unusable shape {envelope_value!r}")


def _aridity_classes(envelope_value: object) -> tuple[str, ...]:
    if isinstance(envelope_value, str):
        return (envelope_value.strip().lower(),)
    if isinstance(envelope_value, list):
        return tuple(str(item).strip().lower() for item in envelope_value)
    return ()


def _normalized_trait_value(trait_value: object) -> str | None:
    """One categorical trait as a comparable token, or `None` when the species record left it null."""
    if trait_value is None:
        return None
    text = str(trait_value).strip().lower()
    return text or None


def _normalized_trait_values(trait_value: object) -> tuple[str, ...]:
    """An array-valued trait as comparable tokens, dropping nulls the record left in place."""
    if trait_value is None:
        return ()
    if isinstance(trait_value, str):
        single = _normalized_trait_value(trait_value)
        return () if single is None else (single,)
    if isinstance(trait_value, (list, tuple, set, frozenset)):
        return tuple(
            sorted({token for token in (_normalized_trait_value(item) for item in trait_value) if token is not None})
        )
    single = _normalized_trait_value(trait_value)
    return () if single is None else (single,)


def _range_bounds(trait_value: object) -> tuple[float | None, float | None]:
    """Read a range-typed trait column, whether it arrived as a driver Range, a pair, or a mapping."""
    if trait_value is None:
        return None, None
    # A driver Range exposes `.lower`/`.upper`; str and bytes expose same-named METHODS, so they
    # are excluded before the attribute probe rather than after it.
    if not isinstance(trait_value, (str, bytes, list, dict)):
        lower = getattr(trait_value, "lower", None)
        upper = getattr(trait_value, "upper", None)
        if lower is not None or upper is not None:
            return _optional_number(lower), _optional_number(upper)
    return _numeric_bounds(trait_value)


def _trait_bounds(traits: Mapping[str, object], low_key: str, high_key: str) -> tuple[float | None, float | None]:
    """The low and high end of one trait range, from a min/max column pair or a single range column."""
    if low_key == high_key:
        return _range_bounds(traits.get(low_key))
    return _optional_number(traits.get(low_key)), _optional_number(traits.get(high_key))


def build_species_trait_vocabulary(
    traits_by_subject: Mapping[str, Mapping[str, object]],
) -> dict[str, tuple[str, ...]]:
    """The observed categorical trait values, derived from the corpus exactly as subjects are.

    Pinning a vocabulary here rather than in a constant keeps a column from ever being born
    degenerate: a trait value no species in this release states gets no column at all.
    """
    observed: dict[str, set[str]] = {}
    for traits in traits_by_subject.values():
        for term in SPECIES_TRAIT_CATEGORICAL_TERMS:
            value = _normalized_trait_value(traits.get(term))
            if value is not None:
                observed.setdefault(term, set()).add(value)
        for term in SPECIES_TRAIT_MULTI_VALUE_TERMS:
            for value in _normalized_trait_values(traits.get(term)):
                observed.setdefault(term, set()).add(value)
    return {term: tuple(sorted(values)) for term, values in sorted(observed.items())}


def species_trait_feature_names(vocabulary: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    """The ordered trait column names for one corpus-derived trait vocabulary."""
    names: list[str] = ["species_trait__record_present"]
    for term, _low_key, _high_key in SPECIES_TRAIT_RANGE_TERMS:
        names.extend(
            (
                f"species_trait__{term}__center",
                f"species_trait__{term}__half_width",
                f"species_trait__{term}__stated",
            )
        )
    for term in SPECIES_TRAIT_BOOLEAN_TERMS:
        names.extend((f"species_trait__{term}__true", f"species_trait__{term}__stated"))
    for term in (*SPECIES_TRAIT_CATEGORICAL_TERMS, *SPECIES_TRAIT_MULTI_VALUE_TERMS):
        names.extend(f"species_trait__{term}__{value}" for value in vocabulary.get(term, ()))
        names.append(f"species_trait__{term}__stated")
    return tuple(names)


def _species_trait_values(traits: Mapping[str, object], vocabulary: Mapping[str, Sequence[str]]) -> list[float]:
    """The trait block of one design row. Absent traits encode as zeros with `__stated` at 0.0."""
    values: list[float] = [1.0 if traits else 0.0]
    for _term, low_key, high_key in SPECIES_TRAIT_RANGE_TERMS:
        low, high = _trait_bounds(traits, low_key, high_key)
        low_value = low if low is not None else high
        high_value = high if high is not None else low
        if low_value is None or high_value is None:
            values.extend((0.0, 0.0, 0.0))
            continue
        values.extend(((low_value + high_value) / 2.0, (high_value - low_value) / 2.0, 1.0))
    for term in SPECIES_TRAIT_BOOLEAN_TERMS:
        stated_boolean = traits.get(term)
        if stated_boolean is None:
            values.extend((0.0, 0.0))
            continue
        values.extend((1.0 if bool(stated_boolean) else 0.0, 1.0))
    for term in SPECIES_TRAIT_CATEGORICAL_TERMS:
        stated_value = _normalized_trait_value(traits.get(term))
        values.extend(1.0 if stated_value == value else 0.0 for value in vocabulary.get(term, ()))
        values.append(0.0 if stated_value is None else 1.0)
    for term in SPECIES_TRAIT_MULTI_VALUE_TERMS:
        stated_values = _normalized_trait_values(traits.get(term))
        values.extend(1.0 if value in stated_values else 0.0 for value in vocabulary.get(term, ()))
        values.append(1.0 if stated_values else 0.0)
    return values


def design_feature_names(
    *,
    subject_vocabulary: Sequence[str],
    species_trait_vocabulary: Mapping[str, Sequence[str]] | None = None,
) -> tuple[str, ...]:
    """The ordered design-matrix column names, pinned so an artifact is reproducible."""
    names: list[str] = [f"site__{name}" for name in SITE_CLIMATE_FEATURES]
    names.extend(f"site__{name}" for name in SITE_COVARIATE_FEATURES)
    for term in _NUMERIC_ENVELOPE_TERMS:
        names.extend(
            (
                f"envelope__{term}__center",
                f"envelope__{term}__half_width",
                f"envelope__{term}__stated",
            )
        )
    names.extend(f"envelope__aridity__{label}" for label in ARIDITY_CLASSES)
    names.extend(f"deviation__{term}" for term in _DEVIATION_TERMS)
    names.append("deviation__aridity_class_distance")
    names.extend(f"subject__{subject}" for subject in subject_vocabulary)
    names.extend(species_trait_feature_names(species_trait_vocabulary or {}))
    return tuple(names)


def build_design_row(  # noqa: PLR0913 - one keyword per pinned block of the design vector
    *,
    site_climate: Mapping[str, object],
    site_covariates: Mapping[str, float | None],
    envelope: Mapping[str, object],
    subject_normalized: str,
    subject_vocabulary: Sequence[str],
    species_traits: Mapping[str, object] | None = None,
    species_trait_vocabulary: Mapping[str, Sequence[str]] | None = None,
) -> tuple[float, ...] | None:
    """Assemble one design row, or `None` when a required governed value is missing.

    Returning `None` rather than a default is the point: a site whose trailing year is
    incomplete has no annual precipitation, and a fabricated one would train the model on a
    number no stream ever produced. Since the drought block joined `SITE_COVARIATE_FEATURES`,
    a cell-day the USDM stream has not reached is one of those missing values and is skipped.

    `species_traits` is one row of `agri.species` for `subject_normalized`, pre-fetched by the
    caller -- this function does no database work, so a trait can never be read lazily inside a
    training loop. An empty mapping means the species has no trait record, which is encoded as a
    declared absence (see `SPECIES_TRAIT_RANGE_TERMS`) and never blocks the row. Range-typed
    trait columns may arrive as a driver Range, a two-element `[low, high]`, or a
    `{"min": ..., "max": ...}` mapping.
    """
    values: list[float] = []
    for name in SITE_CLIMATE_FEATURES:
        raw = _optional_number(site_climate.get(name))
        if raw is None:
            return None
        values.append(raw)
    for name in SITE_COVARIATE_FEATURES:
        raw_covariate = site_covariates.get(name)
        if raw_covariate is None:
            return None
        values.append(float(raw_covariate))

    site_by_term = {term: _require_number(site_climate[term], term) for term in _DEVIATION_TERMS}
    centers: dict[str, float | None] = {}
    for term in _NUMERIC_ENVELOPE_TERMS:
        if term not in envelope:
            values.extend((0.0, 0.0, 0.0))
            centers[term] = None
            continue
        low, high = _numeric_bounds(envelope[term])
        if low is None and high is None:
            values.extend((0.0, 0.0, 0.0))
            centers[term] = None
            continue
        low_value = low if low is not None else high
        high_value = high if high is not None else low
        if low_value is None or high_value is None:
            values.extend((0.0, 0.0, 0.0))
            centers[term] = None
            continue
        center = (low_value + high_value) / 2.0
        half_width = (high_value - low_value) / 2.0
        values.extend((center, half_width, 1.0))
        centers[term] = center

    envelope_aridity = _aridity_classes(envelope.get("aridity", ()))
    values.extend(1.0 if label in envelope_aridity else 0.0 for label in ARIDITY_CLASSES)

    for term in _DEVIATION_TERMS:
        deviation_center = centers.get(term)
        if deviation_center is None:
            values.append(0.0)
            continue
        values.append((site_by_term[term] - deviation_center) / NUMERIC_TERM_TOLERANCE[term])

    site_aridity = site_climate.get("aridity")
    if not envelope_aridity or site_aridity is None or str(site_aridity) not in ARIDITY_CLASSES:
        values.append(0.0)
    else:
        site_position = ARIDITY_CLASSES.index(str(site_aridity))
        values.append(
            float(
                min(
                    abs(site_position - ARIDITY_CLASSES.index(label))
                    for label in envelope_aridity
                    if label in ARIDITY_CLASSES
                )
                if any(label in ARIDITY_CLASSES for label in envelope_aridity)
                else 0.0
            )
        )

    values.extend(1.0 if subject == subject_normalized else 0.0 for subject in subject_vocabulary)
    values.extend(_species_trait_values(species_traits or {}, species_trait_vocabulary or {}))
    return tuple(values)


@dataclass(frozen=True, slots=True)
class SubjectEvidence:
    """One candidate subject and the labels behind it, as the artifact carries them."""

    subject: str
    subject_normalized: str
    envelope: Mapping[str, object]
    label_count: int
    outcomes: Mapping[str, int]
    citations: tuple[Mapping[str, object], ...]
    wildfire_evidence_fraction: float
    water_evidence_fraction: float
    # The `agri.species` row behind this subject, carried so serving rebuilds the same design row
    # the fit saw. Empty means the species has no trait record -- a declared gap, not a zero.
    traits: Mapping[str, object] = field(default_factory=dict)
    # Design rows this subject contributed. Reporting only: the imbalance it measures is corrected
    # at training time by the per-label weight, never by re-scoring at inference (see `rank_subjects`).
    instance_count: int = 0

    def to_payload(self) -> dict[str, object]:
        """The canonical rendering stored in the artifact and echoed by the serving route."""
        return {
            "subject": self.subject,
            "subject_normalized": self.subject_normalized,
            "envelope": dict(self.envelope),
            "label_count": self.label_count,
            "outcomes": dict(self.outcomes),
            "citations": [dict(citation) for citation in self.citations],
            "wildfire_evidence_fraction": self.wildfire_evidence_fraction,
            "water_evidence_fraction": self.water_evidence_fraction,
            "traits": dict(self.traits),
            "instance_count": self.instance_count,
        }


@dataclass(slots=True)
class _SubjectAccumulator:
    """Mutable per-subject accumulator used while walking the instance rows."""

    subject: str
    envelope: Mapping[str, object]
    label_keys: set[str] = field(default_factory=set)
    outcomes: dict[str, int] = field(default_factory=dict)
    citations: dict[str, Mapping[str, object]] = field(default_factory=dict)
    slices: list[str] = field(default_factory=list)
    instance_count: int = 0


@dataclass(frozen=True, slots=True)
class TrainingMatrix:
    """The assembled design matrix and everything needed to evaluate it honestly."""

    feature_names: tuple[str, ...]
    features: np.ndarray
    targets: tuple[str, ...]
    groups: tuple[str, ...]
    label_keys: tuple[str, ...]
    weights: np.ndarray
    subjects: tuple[SubjectEvidence, ...]
    subject_vocabulary: tuple[str, ...]
    skipped_incomplete_rows: int
    label_count: int
    source_count: int
    species_trait_vocabulary: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # Subjects the fit saw with no `agri.species` row, reported so the trait gap stays visible
    # rather than hiding inside a block of zeros.
    subjects_without_trait_record: int = 0

    @property
    def row_count(self) -> int:
        """Design rows, which is NOT the effective sample size -- read `label_count` for that."""
        return int(self.features.shape[0])


def _objective_fraction(slices: Sequence[str], wanted: Sequence[str]) -> float:
    if not slices:
        return 0.0
    return sum(1 for slice_name in slices if slice_name in wanted) / len(slices)


@dataclass(frozen=True)
class _DecodedInstance:
    """The four decoded blocks one matched training instance contributes to its design row."""

    site_climate: Mapping[str, object]
    site_covariates: Mapping[str, float | None]
    envelope: Mapping[str, object]
    subject_normalized: str


def _decode_training_instance(row: Mapping[str, object]) -> _DecodedInstance:
    """Decode and shape-check one instance row, refusing rather than defaulting a malformed block."""
    envelope_match = row["envelope_match"]
    if not isinstance(envelope_match, dict):
        raise RecommendationTrainingError("envelope_match did not decode as an object")
    site_climate = envelope_match.get("site_climate")
    if not isinstance(site_climate, dict):
        raise RecommendationTrainingError("envelope_match carries no site_climate object")
    feature_values = row["feature_values"]
    if not isinstance(feature_values, list):
        raise RecommendationTrainingError("feature_values did not decode as an array")
    envelope = row["condition_envelope"]
    if not isinstance(envelope, dict):
        raise RecommendationTrainingError("condition_envelope did not decode as an object")
    return _DecodedInstance(
        site_climate=site_climate,
        site_covariates={
            str(entry["feature_name"]): (None if entry.get("feature_value") is None else float(entry["feature_value"]))
            for entry in feature_values
            if isinstance(entry, dict)
        },
        envelope=envelope,
        subject_normalized=str(row["subject_normalized"]),
    )


def assemble_training_matrix(
    rows: Sequence[Mapping[str, object]],
    *,
    model_kind: ModelKind,
    species_traits_by_subject: Mapping[str, Mapping[str, object]] | None = None,
) -> TrainingMatrix:
    """Turn matched training instances into a design matrix, subject evidence, and CV groups.

    `species_traits_by_subject` maps `subject_normalized` to that species' `agri.species` row,
    fetched once by the caller. Subjects absent from it train with a declared trait gap; the
    table is empty in production today, so that is currently every subject.

    Sample weights are `confidence_weight / instances_for_that_label`, so one label contributes
    one unit of evidence however many cell-days its envelope happens to sweep. Without it a
    one-term `{"aridity": "semi_arid"}` label matching nearly every cell-day moves the fitted
    coefficients by orders of magnitude more than a precise five-term trial-site label -- and
    ranking the outputs afterwards cannot undo a function that was fitted wrong.
    """
    if not rows:
        raise RecommendationTrainingError("no matched training instances to fit on")
    if model_kind not in CLASS_ORDER_BY_MODEL_KIND:
        raise RecommendationTrainingError(f"unknown model kind {model_kind!r}")
    # Both models carry subject identity. Model A used to suppress it because the harvest held one
    # label per species, which made a species one-hot a perfectly separating column that
    # leave-one-source-out could never score. The harvest plan now requires >=2 sources per species
    # (>=3 to be safe), which is exactly the condition that makes the column scoreable: hold one
    # source out and another still carries the species. Species traits below are the other half --
    # they let a species whose every source is in the held-out fold still be scored, from the kind
    # of plant it is rather than from an identity the fold removed.
    subjects_in_corpus = {str(row["subject_normalized"]) for row in rows}
    subject_vocabulary = tuple(sorted(subjects_in_corpus))
    corpus_traits = {
        subject: dict(traits)
        for subject, traits in (species_traits_by_subject or {}).items()
        if subject in subjects_in_corpus
    }
    species_trait_vocabulary = build_species_trait_vocabulary(corpus_traits)
    feature_names = design_feature_names(
        subject_vocabulary=subject_vocabulary,
        species_trait_vocabulary=species_trait_vocabulary,
    )

    design_rows: list[tuple[float, ...]] = []
    targets: list[str] = []
    groups: list[str] = []
    label_keys: list[str] = []
    confidence_weights: list[float] = []
    instances_by_label: dict[str, int] = {}
    skipped = 0
    by_subject: dict[str, _SubjectAccumulator] = {}

    for row in rows:
        instance = _decode_training_instance(row)
        envelope = instance.envelope
        subject_normalized = instance.subject_normalized

        design_row = build_design_row(
            site_climate=instance.site_climate,
            site_covariates=instance.site_covariates,
            envelope=envelope,
            subject_normalized=subject_normalized,
            subject_vocabulary=subject_vocabulary,
            species_traits=corpus_traits.get(subject_normalized),
            species_trait_vocabulary=species_trait_vocabulary,
        )
        if design_row is None:
            skipped += 1
            continue
        design_rows.append(design_row)
        targets.append(str(row["outcome"]))
        groups.append(str(row["doi"] or row["source_key"]))
        label_keys.append(str(row["label_key"]))
        confidence_weights.append(_require_number(row["confidence_weight"], "confidence_weight"))
        instances_by_label[str(row["label_key"])] = instances_by_label.get(str(row["label_key"]), 0) + 1

        bucket = by_subject.setdefault(
            subject_normalized,
            _SubjectAccumulator(subject=str(row["subject"]), envelope=envelope),
        )
        bucket.instance_count += 1
        label_key = str(row["label_key"])
        if label_key not in bucket.label_keys:
            bucket.label_keys.add(label_key)
            bucket.outcomes[str(row["outcome"])] = bucket.outcomes.get(str(row["outcome"]), 0) + 1
            bucket.slices.append(str(row["harvest_slice"]))
            citation_key = str(row["doi"] or row["source_key"])
            bucket.citations[citation_key] = {
                "doi": row["doi"],
                "title": row["title"],
                "year": row["publication_year"],
                "journal_or_publisher": row["journal_or_publisher"],
                "outcome": row["outcome"],
                "confidence": row["confidence"],
                "label_key": label_key,
                "label_checksum": row["label_checksum"],
                "review_state": row["review_state"],
            }

    if not design_rows:
        raise RecommendationTrainingError(
            f"every one of the {len(rows)} matched instances lacked a governed value the design "
            "matrix requires; nothing was fabricated to fill them"
        )

    subjects = tuple(
        SubjectEvidence(
            subject=bucket.subject,
            subject_normalized=subject_normalized,
            envelope=bucket.envelope,
            label_count=len(bucket.label_keys),
            outcomes=bucket.outcomes,
            citations=tuple(bucket.citations.values()),
            wildfire_evidence_fraction=_objective_fraction(bucket.slices, WILDFIRE_EVIDENCE_SLICES),
            water_evidence_fraction=_objective_fraction(bucket.slices, WATER_EVIDENCE_SLICES),
            traits=corpus_traits.get(subject_normalized, {}),
            instance_count=bucket.instance_count,
        )
        for subject_normalized, bucket in sorted(by_subject.items())
    )
    # One label = one unit of evidence. The division is by the label's MATCHED design rows, which
    # is what actually entered the fit, not by the days its envelope nominally covered.
    weights = [
        confidence_weight / instances_by_label[label_key]
        for confidence_weight, label_key in zip(confidence_weights, label_keys, strict=True)
    ]
    return TrainingMatrix(
        feature_names=feature_names,
        features=np.asarray(design_rows, dtype=np.float64),
        targets=tuple(targets),
        groups=tuple(groups),
        label_keys=tuple(label_keys),
        weights=np.asarray(weights, dtype=np.float64),
        subjects=subjects,
        subject_vocabulary=subject_vocabulary,
        skipped_incomplete_rows=skipped,
        label_count=len(set(label_keys)),
        source_count=len(set(groups)),
        species_trait_vocabulary=species_trait_vocabulary,
        subjects_without_trait_record=sum(1 for subject in subjects if not subject.traits),
    )


@dataclass(frozen=True, slots=True)
class Standardization:
    """Column means and scales, held explicitly so the artifact stays JSON and never a pickle."""

    mean: tuple[float, ...]
    scale: tuple[float, ...]

    def apply(self, features: np.ndarray) -> np.ndarray:
        """Standardize a design matrix with these pinned moments."""
        standardized: np.ndarray = (features - np.asarray(self.mean)) / np.asarray(self.scale)
        return standardized


def fit_standardization(features: np.ndarray) -> Standardization:
    """Column moments, with a floor so a constant column cannot divide by zero."""
    mean = features.mean(axis=0)
    scale = features.std(axis=0)
    scale = np.where(scale < _STANDARDIZATION_FLOOR, 1.0, scale)
    return Standardization(mean=tuple(float(value) for value in mean), scale=tuple(float(value) for value in scale))


def fit_logistic(
    features: np.ndarray,
    targets: Sequence[str],
    weights: np.ndarray,
    *,
    regularization_strength: float = DEFAULT_REGULARIZATION_STRENGTH,
) -> LogisticRegression:
    """Fit the multinomial logistic classifier. This is the one and only `.fit()` in this lane."""
    model = LogisticRegression(
        C=regularization_strength,
        max_iter=DEFAULT_MAX_ITERATIONS,
        solver="lbfgs",
        random_state=DEFAULT_RANDOM_SEED,
    )
    model.fit(features, list(targets), sample_weight=weights)
    return model


def _macro_f1(actual: Sequence[str], predicted: Sequence[str], classes: Sequence[str]) -> float:
    scores: list[float] = []
    for label in classes:
        true_positive = sum(1 for a, p in zip(actual, predicted, strict=True) if a == label and p == label)
        predicted_positive = sum(1 for p in predicted if p == label)
        actual_positive = sum(1 for a in actual if a == label)
        if actual_positive == 0:
            continue
        precision = true_positive / predicted_positive if predicted_positive else 0.0
        recall = true_positive / actual_positive
        scores.append(0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall))
    return float(sum(scores) / len(scores)) if scores else 0.0


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    """Grouped cross-validation evidence, with the honest sample accounting attached."""

    scheme: str
    fold_count: int
    scored_row_count: int
    effective_sample_size: int
    accuracy: float
    macro_f1: float
    mean_absolute_utility_error: float
    root_mean_squared_utility_error: float
    majority_baseline_accuracy: float
    per_source: tuple[Mapping[str, object], ...]
    class_distribution: Mapping[str, int]
    deviations: tuple[str, ...]
    caveats: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        """The receipt's evaluation body."""
        return {
            "scheme": self.scheme,
            "fold_count": self.fold_count,
            "scored_row_count": self.scored_row_count,
            "effective_sample_size": self.effective_sample_size,
            "accuracy": self.accuracy,
            "macro_f1": self.macro_f1,
            "mean_absolute_utility_error": self.mean_absolute_utility_error,
            "root_mean_squared_utility_error": self.root_mean_squared_utility_error,
            "majority_baseline_accuracy": self.majority_baseline_accuracy,
            "class_distribution": dict(self.class_distribution),
            "per_source": [dict(entry) for entry in self.per_source],
            "deviations": list(self.deviations),
            "caveats": list(self.caveats),
        }


def evaluate_leave_one_source_out(
    matrix: TrainingMatrix, *, model_kind: ModelKind, regularization_strength: float
) -> EvaluationMetrics:
    """Score the model with grouped leave-one-source-out cross-validation.

    Grouping is by cited DOI, so every design row derived from one publication leaves together.
    Spatially-blocked splitting is not available here and the reason is recorded as an explicit
    deviation rather than implied: the pilot is one spatial cell, so there is exactly one block
    and a spatial split would have nothing to hold out.
    """
    sources = sorted(set(matrix.groups))
    classes = tuple(label for label in CLASS_ORDER_BY_MODEL_KIND[model_kind] if label in set(matrix.targets))
    actual: list[str] = []
    predicted: list[str] = []
    utility_errors: list[float] = []
    per_source: list[Mapping[str, object]] = []
    folds_scored = 0

    for source in sources:
        holdout = np.asarray([group == source for group in matrix.groups])
        training = np.logical_not(holdout)
        training_targets = [target for target, keep in zip(matrix.targets, training, strict=True) if keep]
        if len(set(training_targets)) < _MIN_CLASSES_FOR_FIT:
            per_source.append(
                {
                    "source": source,
                    "held_out_rows": int(holdout.sum()),
                    "scored": False,
                    "reason": "the remaining sources carried fewer than two outcome classes",
                }
            )
            continue
        standardization = fit_standardization(matrix.features[training])
        fold_model = fit_logistic(
            standardization.apply(matrix.features[training]),
            training_targets,
            matrix.weights[training],
            regularization_strength=regularization_strength,
        )
        fold_predictions = fold_model.predict(standardization.apply(matrix.features[holdout]))
        fold_actual = [target for target, keep in zip(matrix.targets, holdout, strict=True) if keep]
        fold_utility_errors = [
            abs(OUTCOME_UTILITY[str(prediction)] - OUTCOME_UTILITY[truth])
            for prediction, truth in zip(fold_predictions, fold_actual, strict=True)
        ]
        actual.extend(fold_actual)
        predicted.extend(str(prediction) for prediction in fold_predictions)
        utility_errors.extend(fold_utility_errors)
        folds_scored += 1
        per_source.append(
            {
                "source": source,
                "held_out_rows": int(holdout.sum()),
                "held_out_labels": len({key for key, keep in zip(matrix.label_keys, holdout, strict=True) if keep}),
                "scored": True,
                "accuracy": float(
                    sum(1 for a, p in zip(fold_actual, fold_predictions, strict=True) if a == str(p)) / len(fold_actual)
                ),
                "mean_absolute_utility_error": float(sum(fold_utility_errors) / len(fold_utility_errors)),
                "held_out_outcomes": sorted(set(fold_actual)),
            }
        )

    if not actual:
        raise RecommendationTrainingError(
            "no cross-validation fold could be scored; every hold-out left fewer than two classes"
        )
    class_distribution = {label: matrix.targets.count(label) for label in classes}
    majority_class = max(class_distribution, key=lambda label: class_distribution[label])
    return EvaluationMetrics(
        scheme="grouped_leave_one_source_out",
        fold_count=folds_scored,
        scored_row_count=len(actual),
        effective_sample_size=matrix.label_count,
        accuracy=float(sum(1 for a, p in zip(actual, predicted, strict=True) if a == p) / len(actual)),
        macro_f1=_macro_f1(actual, predicted, classes),
        mean_absolute_utility_error=float(sum(utility_errors) / len(utility_errors)),
        root_mean_squared_utility_error=float(
            math.sqrt(sum(error * error for error in utility_errors) / len(utility_errors))
        ),
        majority_baseline_accuracy=float(sum(1 for target in actual if target == majority_class) / len(actual)),
        per_source=tuple(per_source),
        class_distribution=class_distribution,
        deviations=(
            "Spatially-blocked splitting was not performed: the pilot region is one spatial cell, "
            "so there is exactly one spatial block and a spatial hold-out would be empty. Grouped "
            "leave-one-source-out is used instead and every fold's provenance is listed above.",
            "Temporal honesty is carried by the feature layer, not by the split: every feature is "
            "strictly lagged and availability-gated, and the labels are time-invariant literature "
            "claims, so there is no future-to-past ordering to hold out.",
        ),
        caveats=(
            f"Effective sample size is {matrix.label_count} labels, not {matrix.row_count} design rows: "
            "the rows of one label are the same claim evaluated on different days of one cell. The fit "
            "now applies this rather than only stating it -- each row is weighted "
            "confidence_weight / instances-for-that-label, so a broad envelope cannot outvote a "
            "precise one by sweeping more cell-days.",
            f"{matrix.subjects_without_trait_record} of {len(matrix.subjects)} subjects carried no "
            "agri.species trait record; their trait columns are a recorded absence, not an imputed "
            "value, and those subjects can only be generalized through the envelope descriptors.",
            "Scores prove the training and evaluation framework runs end to end on governed data; "
            "they do not establish an operational recommender.",
            "Labels are agent-reviewed and pending an owner signature; no label is owner-approved.",
        ),
    )


@dataclass(frozen=True, slots=True)
class ModelArtifact:
    """The fitted model as canonical JSON: coefficients, moments, subjects, and lineage."""

    model_name: str
    model_kind: str
    feature_schema_version: str
    feature_names: tuple[str, ...]
    classes: tuple[str, ...]
    coefficients: tuple[tuple[float, ...], ...]
    intercepts: tuple[float, ...]
    standardization: Standardization
    subjects: tuple[SubjectEvidence, ...]
    subject_vocabulary: tuple[str, ...]
    hyperparameters: Mapping[str, object]
    label_release_key: str
    label_release_checksum: str
    label_review_tier: str
    training_code_checksum: str
    # Corpus-derived, exactly like `subject_vocabulary`, so serving rebuilds the same trait
    # columns the fit used. Defaulted so an artifact persisted before traits existed still loads.
    species_trait_vocabulary: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def to_document(self) -> dict[str, object]:
        """The canonical-JSON document whose sha256 is this artifact's identity."""
        return {
            "artifact_version": "agri_recommendation_model_v1",
            "model_name": self.model_name,
            "model_kind": self.model_kind,
            "claim_tier": CLAIM_TIER,
            "label": EVALUATION_LABEL,
            "publication_authorized": False,
            "disclaimer": EVALUATION_DISCLAIMER,
            "feature_schema_version": self.feature_schema_version,
            "feature_names": list(self.feature_names),
            "classes": list(self.classes),
            "coefficients": [list(row) for row in self.coefficients],
            "intercepts": list(self.intercepts),
            "standardization": {"mean": list(self.standardization.mean), "scale": list(self.standardization.scale)},
            "subjects": [subject.to_payload() for subject in self.subjects],
            "subject_vocabulary": list(self.subject_vocabulary),
            "species_trait_vocabulary": {
                term: list(values) for term, values in sorted(self.species_trait_vocabulary.items())
            },
            "hyperparameters": dict(self.hyperparameters),
            "label_release_key": self.label_release_key,
            "label_release_checksum": self.label_release_checksum,
            "label_review_tier": self.label_review_tier,
            "training_code_checksum": self.training_code_checksum,
            "outcome_utility": dict(OUTCOME_UTILITY),
        }

    @property
    def checksum(self) -> str:
        """sha256 over the canonical document; the identity every response and receipt cites."""
        return sha256_digest(canonical_json(self.to_document()))


def build_artifact(  # noqa: PLR0913 - one parameter per pinned lineage field the artifact records
    matrix: TrainingMatrix,
    model: LogisticRegression,
    standardization: Standardization,
    *,
    model_kind: ModelKind,
    feature_schema_version: str,
    label_release_key: str,
    label_release_checksum: str,
    label_review_tier: str,
    regularization_strength: float,
) -> ModelArtifact:
    """Export a fitted classifier as a JSON-extractable artifact -- never a pickle."""
    coefficients = np.atleast_2d(model.coef_)
    intercepts = np.atleast_1d(model.intercept_)
    return ModelArtifact(
        model_name=MODEL_NAME_BY_KIND[model_kind],
        model_kind=model_kind,
        feature_schema_version=feature_schema_version,
        feature_names=matrix.feature_names,
        classes=tuple(str(label) for label in model.classes_),
        coefficients=tuple(tuple(float(value) for value in row) for row in coefficients),
        intercepts=tuple(float(value) for value in intercepts),
        standardization=standardization,
        subjects=matrix.subjects,
        subject_vocabulary=matrix.subject_vocabulary,
        hyperparameters={
            "estimator": "sklearn.linear_model.LogisticRegression",
            "solver": "lbfgs",
            "regularization_strength_C": regularization_strength,
            "max_iterations": DEFAULT_MAX_ITERATIONS,
            "random_seed": DEFAULT_RANDOM_SEED,
            "sample_weight": (
                "expert_label.confidence_weight / matched design rows for that label, so one "
                "label is one unit of evidence regardless of how many cell-days it sweeps"
            ),
            "standardization": "column mean/std fitted on the training design matrix",
        },
        label_release_key=label_release_key,
        label_release_checksum=label_release_checksum,
        label_review_tier=label_review_tier,
        training_code_checksum=training_code_checksum(),
        species_trait_vocabulary=matrix.species_trait_vocabulary,
    )


def class_probabilities(artifact: ModelArtifact, design_row: Sequence[float]) -> dict[str, float]:
    """Recompute the multinomial softmax from the artifact alone -- no estimator object needed."""
    features = (np.asarray(design_row, dtype=np.float64) - np.asarray(artifact.standardization.mean)) / np.asarray(
        artifact.standardization.scale
    )
    scores = np.asarray(artifact.coefficients) @ features + np.asarray(artifact.intercepts)
    if len(artifact.classes) == _BINARY_CLASS_COUNT and scores.shape[0] == 1:
        positive = 1.0 / (1.0 + math.exp(-float(scores[0])))
        return {artifact.classes[0]: 1.0 - positive, artifact.classes[1]: positive}
    shifted = scores - scores.max()
    weights = np.exp(shifted)
    normalized = weights / weights.sum()
    return {label: float(value) for label, value in zip(artifact.classes, normalized, strict=True)}


def expected_utility(probabilities: Mapping[str, float]) -> float:
    """Turn class probabilities into one ordinal ranking score."""
    return float(sum(OUTCOME_UTILITY[label] * value for label, value in probabilities.items()))


@dataclass(frozen=True, slots=True)
class RankedRecommendation:
    """One scored candidate with the citations and tiering it must never be served without."""

    rank: int
    subject: str
    subject_normalized: str
    expected_utility: float
    objective_adjusted_score: float
    class_probabilities: Mapping[str, float]
    wildfire_evidence_fraction: float
    water_evidence_fraction: float
    supporting_label_count: int
    citations: tuple[Mapping[str, object], ...]

    def to_payload(self) -> dict[str, object]:
        """The serving rendering."""
        return {
            "rank": self.rank,
            "subject": self.subject,
            "subject_normalized": self.subject_normalized,
            "expected_utility": self.expected_utility,
            "objective_adjusted_score": self.objective_adjusted_score,
            "class_probabilities": dict(self.class_probabilities),
            "wildfire_evidence_fraction": self.wildfire_evidence_fraction,
            "water_evidence_fraction": self.water_evidence_fraction,
            "supporting_label_count": self.supporting_label_count,
            "citations": [dict(citation) for citation in self.citations],
            "claim_tier": CLAIM_TIER,
        }


def rank_subjects(
    artifact: ModelArtifact,
    *,
    site_climate: Mapping[str, object],
    site_covariates: Mapping[str, float | None],
    wildfire_weight: float,
    water_weight: float,
) -> tuple[tuple[RankedRecommendation, ...], tuple[str, ...]]:
    """Score every subject the artifact knows against one site, returning ranks and skips.

    Subject identity, traits and the trait vocabulary all come from the artifact, so the design
    row scored here is assembled exactly as the fitted one was. `instance_count` is deliberately
    NOT used to weight anything: the per-label imbalance it records is corrected at training
    time, and re-scoring for it here would price the same evidence twice.
    """
    scored: list[RankedRecommendation] = []
    skipped: list[str] = []
    for subject in artifact.subjects:
        design_row = build_design_row(
            site_climate=site_climate,
            site_covariates=site_covariates,
            envelope=subject.envelope,
            subject_normalized=subject.subject_normalized,
            subject_vocabulary=artifact.subject_vocabulary,
            species_traits=subject.traits,
            species_trait_vocabulary=artifact.species_trait_vocabulary,
        )
        if design_row is None:
            skipped.append(subject.subject)
            continue
        probabilities = class_probabilities(artifact, design_row)
        utility = expected_utility(probabilities)
        adjusted = utility * (
            1.0 + wildfire_weight * subject.wildfire_evidence_fraction + water_weight * subject.water_evidence_fraction
        )
        scored.append(
            RankedRecommendation(
                rank=0,
                subject=subject.subject,
                subject_normalized=subject.subject_normalized,
                expected_utility=utility,
                objective_adjusted_score=adjusted,
                class_probabilities=probabilities,
                wildfire_evidence_fraction=subject.wildfire_evidence_fraction,
                water_evidence_fraction=subject.water_evidence_fraction,
                supporting_label_count=subject.label_count,
                citations=subject.citations,
            )
        )
    scored.sort(key=lambda item: (-item.objective_adjusted_score, item.subject_normalized))
    ranked = tuple(
        RankedRecommendation(
            rank=position + 1,
            subject=item.subject,
            subject_normalized=item.subject_normalized,
            expected_utility=item.expected_utility,
            objective_adjusted_score=item.objective_adjusted_score,
            class_probabilities=item.class_probabilities,
            wildfire_evidence_fraction=item.wildfire_evidence_fraction,
            water_evidence_fraction=item.water_evidence_fraction,
            supporting_label_count=item.supporting_label_count,
            citations=item.citations,
        )
        for position, item in enumerate(scored)
    )
    return ranked, tuple(skipped)


def _as_sequence(value: object, name: str) -> list[object]:
    """Read a stored JSON array, refusing anything else."""
    if not isinstance(value, list):
        raise RecommendationTrainingError(f"artifact field {name!r} did not decode as an array")
    return value


def _as_mapping(value: object, name: str) -> Mapping[str, object]:
    """Read a stored JSON object, refusing anything else."""
    if not isinstance(value, dict):
        raise RecommendationTrainingError(f"artifact field {name!r} did not decode as an object")
    return value


def artifact_from_document(document: Mapping[str, object]) -> ModelArtifact:
    """Rebuild an artifact from its stored canonical JSON, for serving."""
    standardization_payload = _as_mapping(document["standardization"], "standardization")
    subjects_payload = _as_sequence(document["subjects"], "subjects")
    subjects = tuple(
        SubjectEvidence(
            subject=str(entry["subject"]),
            subject_normalized=str(entry["subject_normalized"]),
            envelope=_as_mapping(entry["envelope"], "subjects[].envelope"),
            label_count=int(_require_number(entry["label_count"], "subjects[].label_count")),
            outcomes={
                str(key): int(_require_number(value, "subjects[].outcomes"))
                for key, value in _as_mapping(entry["outcomes"], "subjects[].outcomes").items()
            },
            citations=tuple(
                _as_mapping(citation, "subjects[].citations[]")
                for citation in _as_sequence(entry["citations"], "subjects[].citations")
            ),
            wildfire_evidence_fraction=_require_number(
                entry["wildfire_evidence_fraction"], "subjects[].wildfire_evidence_fraction"
            ),
            water_evidence_fraction=_require_number(
                entry["water_evidence_fraction"], "subjects[].water_evidence_fraction"
            ),
            traits=_as_mapping(entry.get("traits", {}), "subjects[].traits"),
            instance_count=int(_require_number(entry.get("instance_count", 0), "subjects[].instance_count")),
        )
        for entry in (_as_mapping(raw, "subjects[]") for raw in subjects_payload)
    )
    return ModelArtifact(
        model_name=str(document["model_name"]),
        model_kind=str(document["model_kind"]),
        feature_schema_version=str(document["feature_schema_version"]),
        feature_names=tuple(str(name) for name in _as_sequence(document["feature_names"], "feature_names")),
        classes=tuple(str(name) for name in _as_sequence(document["classes"], "classes")),
        coefficients=tuple(
            tuple(_require_number(value, "coefficients[][]") for value in _as_sequence(row, "coefficients[]"))
            for row in _as_sequence(document["coefficients"], "coefficients")
        ),
        intercepts=tuple(
            _require_number(value, "intercepts[]") for value in _as_sequence(document["intercepts"], "intercepts")
        ),
        standardization=Standardization(
            mean=tuple(
                _require_number(value, "standardization.mean[]")
                for value in _as_sequence(standardization_payload["mean"], "standardization.mean")
            ),
            scale=tuple(
                _require_number(value, "standardization.scale[]")
                for value in _as_sequence(standardization_payload["scale"], "standardization.scale")
            ),
        ),
        subjects=subjects,
        subject_vocabulary=tuple(
            str(name) for name in _as_sequence(document["subject_vocabulary"], "subject_vocabulary")
        ),
        species_trait_vocabulary={
            str(term): tuple(str(value) for value in _as_sequence(values, "species_trait_vocabulary[]"))
            for term, values in _as_mapping(
                document.get("species_trait_vocabulary", {}), "species_trait_vocabulary"
            ).items()
        },
        hyperparameters=_as_mapping(document["hyperparameters"], "hyperparameters"),
        label_release_key=str(document["label_release_key"]),
        label_release_checksum=str(document["label_release_checksum"]),
        label_review_tier=str(document["label_review_tier"]),
        training_code_checksum=str(document["training_code_checksum"]),
    )


@dataclass(frozen=True, slots=True)
class TrainingReport:
    """One training invocation's evidence: what it read, what it fitted, what it measured."""

    model_kind: str
    model_name: str
    release_key: str
    release_checksum: str
    review_tier: str
    feature_schema_version: str
    artifact: ModelArtifact
    metrics: EvaluationMetrics
    matrix_row_count: int
    label_count: int
    source_count: int
    skipped_incomplete_rows: int
    receipt_id: uuid.UUID | None = None
    training_key: str | None = None
    persisted: bool = False

    def parameter_payload(self) -> dict[str, object]:
        """The pinned knobs this run's parameter checksum derives from."""
        return {
            "digest_version": "agri_recommendation_training_parameters_v1",
            "model_kind": self.model_kind,
            "model_name": self.model_name,
            "release_key": self.release_key,
            "release_checksum": self.release_checksum,
            "feature_schema_version": self.feature_schema_version,
            "hyperparameters": dict(self.artifact.hyperparameters),
            "site_covariate_features": list(SITE_COVARIATE_FEATURES),
            "site_climate_features": list(SITE_CLIMATE_FEATURES),
            "species_trait_terms": {
                "range": [term for term, _low, _high in SPECIES_TRAIT_RANGE_TERMS],
                "boolean": list(SPECIES_TRAIT_BOOLEAN_TERMS),
                "categorical": list(SPECIES_TRAIT_CATEGORICAL_TERMS),
                "multi_value": list(SPECIES_TRAIT_MULTI_VALUE_TERMS),
            },
            "numeric_term_tolerance": dict(NUMERIC_TERM_TOLERANCE),
            "envelope_term_support": dict(ENVELOPE_TERM_SUPPORT),
        }

    @property
    def parameter_checksum(self) -> str:
        """Digest over `parameter_payload`."""
        return sha256_digest(canonical_json(self.parameter_payload()))

    def evaluation_payload(self) -> dict[str, object]:
        """The receipt's `evaluation_metrics` body."""
        return {
            "label": EVALUATION_LABEL,
            "publication_authorized": False,
            "claim_tier": CLAIM_TIER,
            "disclaimer": EVALUATION_DISCLAIMER,
            "label_review_tier": self.review_tier,
            "feature_schema_version": self.feature_schema_version,
            "design_row_count": self.matrix_row_count,
            "label_count": self.label_count,
            "source_count": self.source_count,
            "skipped_incomplete_rows": self.skipped_incomplete_rows,
            "objectives": {
                "wildfire_evidence_slices": list(WILDFIRE_EVIDENCE_SLICES),
                "water_evidence_slices": list(WATER_EVIDENCE_SLICES),
                "policy": (
                    "Objective scores are the share of a candidate's supporting labels drawn from "
                    "the wildfire or water literature slices; they are evidence provenance, not a "
                    "claimed wildfire or hydrological effect."
                ),
            },
            "cross_validation": self.metrics.to_payload(),
            "parameters": self.parameter_payload(),
        }

    @property
    def evaluation_checksum(self) -> str:
        """Digest over the evaluation body."""
        return sha256_digest(canonical_json(self.evaluation_payload()))

    def to_summary(self) -> dict[str, object]:
        """The verb's one JSON line."""
        return {
            "model_kind": self.model_kind,
            "model_name": self.model_name,
            "release_key": self.release_key,
            "label_review_tier": self.review_tier,
            "feature_schema_version": self.feature_schema_version,
            "artifact_checksum": self.artifact.checksum,
            "evaluation_checksum": self.evaluation_checksum,
            "parameter_checksum": self.parameter_checksum,
            "design_row_count": self.matrix_row_count,
            "label_count": self.label_count,
            "source_count": self.source_count,
            "subject_count": len(self.artifact.subjects),
            "metrics": self.metrics.to_payload(),
            "training_key": self.training_key,
            "receipt_id": None if self.receipt_id is None else str(self.receipt_id),
            "persisted": self.persisted,
        }


__all__ = [
    "CLAIM_TIER",
    "EVALUATION_DISCLAIMER",
    "SPECIES_TRAIT_BOOLEAN_TERMS",
    "SPECIES_TRAIT_CATEGORICAL_TERMS",
    "SPECIES_TRAIT_MULTI_VALUE_TERMS",
    "SPECIES_TRAIT_RANGE_TERMS",
    "ExpertLabelPlaneError",
    "ModelArtifact",
    "RankedRecommendation",
    "RecommendationTrainingError",
    "TrainingReport",
    "artifact_from_document",
    "assemble_training_matrix",
    "build_design_row",
    "build_species_trait_vocabulary",
    "class_probabilities",
    "evaluate_leave_one_source_out",
    "expected_utility",
    "rank_subjects",
    "species_trait_feature_names",
]
