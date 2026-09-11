---
type: track-spec
slug: botanical_species_recommendation_validation_20260911
status: planned
---

# Botanical species recommendation evidence and validation

## Outcome

Produce species-specific recommendations that explain why a plant is documented
near a place, whether its reviewed establishment requirements are compatible
with the selected environmental context, and whether separate evidence supports
the requested ecological or management effect. All inputs are release-pinned and
all three conclusions remain individually inspectable.

No model fit, API change, source download or production recommendation is
authorized by this planning pass.

## Evidence composition

Recommendations consume four versioned inputs: the botanical occurrence release,
the nonspatial species-profile release, the selected environmental releases and
day/window, and any separately admitted species-objective-effect release. The
canonical taxon crosswalk and every input release ID enter the recommendation
receipt.

The response preserves three evidence sections:

1. **Documented occurrence** reports admitted specimens, represented support,
   collection date, age, uncertainty and collection-effort limitations. It is
   realized evidence, not abundance, planting success or biological absence.
2. **Establishment compatibility** compares each published requirement with the
   corresponding environmental value and returns compatible, incompatible,
   unknown or not-evaluated. Missing profile or environmental terms are not
   imputed and do not count as a match.
3. **Objective effect** cites distinct species × objective × condition evidence.
   Growth compatibility never proves wildfire mitigation, drought mitigation,
   water conservation, carbon gain, soil improvement or another effect.

Fuel analysis may consume separately admitted water, oil, resin, extractive,
heat-content, ash, curing, architecture and fuel-bed assertions, but it must keep
flammability, fire tolerance, fire response and post-fire regeneration distinct.
Agricultural deployment may consume establishment requirements, rooting,
nitrogen-fixation, biomass, phenology, companion and management evidence while
keeping suitability separate from the claimed deployment outcome.

The agent and API show all three sections even when one is empty. A ranking may
order candidates only under a versioned, reviewed scoring or model contract and
must retain its component verdicts. One unexplained blended score is prohibited.

## Occurrence-derived associations

Any association learned from specimens is a separately versioned evidence kind,
not a replacement for curated requirements. Deduplicate syndicated records and
biological collection events, preserve cultivated/escape state, identification
history, event-date precision and coordinate uncertainty, and select an
environmental support no finer than the represented locality.

Collection density measures sampling as well as biology. Missing specimens do
not become absence labels. Spatial or temporal validation blocks must prevent
nearby or duplicate collections from leaking across train and test sets.
Background selection, spatial thinning, sampling-bias treatment, environmental
period choice and applicability geography are frozen in the model receipt.

## Site and time semantics

Long-term establishment compatibility and selected-day stress are separate
outputs. Environmental normals, seasonal water balance, extremes, soil,
elevation ranges and terrain support retain their native periods and spatial
resolution. A cell mean cannot exclude a species when the cell's supported
range overlaps the requirement; range overlap and containment are distinct
predicates.

Every recommendation receipt records geometry/support, scenario or selected day,
taxon authority/version, occurrence release, profile release, environmental
products and periods, effect-evidence release, match rules/model version,
missing/conflicting inputs, applicability limits and abstention state.

## Validation and abstention

Start with a bounded, named Pacific Northwest pilot whose taxa have reviewed
profiles and enough independent evidence. Evaluate component accuracy,
calibration where probabilities are shown, spatial/temporal transfer, coverage,
conflicts and abstentions. Independent survey or establishment evidence is
required before claiming predictive suitability; specimens alone can support a
documented-occurrence baseline only.

The service abstains when taxon identity, critical requirements, environmental
coverage, effect evidence or applicability support is insufficient. It must not
manufacture a precise success probability from broad national summaries or
unvalidated occurrence associations.

## Acceptance

Acceptance requires reproducible release joins, source attribution for every
reason, no missing-as-zero conversion, taxon/cultivar preservation, uncertainty-
aware spatial joins, time-honest covariates, sampling-bias controls, blocked
validation, component metrics, abstention tests, bounded API/agent responses,
rollback and independent botanical/statistical review.

The final reviewer is separate from data preparation, model fitting and API
integration. The integration receipt belongs to `r4`; the dependency-last
independent verdict belongs to `r5`. No author or integration slice may approve
its own recommendation claims.
