---
type: track-spec
slug: botanical_species_profile_lookup_20260911
status: active
---

# Botanical species profile lookup and recommendation evidence

## Outcome

Publish a small, versioned, nonspatial lookup that answers what is known about a
plant taxon's establishment requirements and traits. The lookup is keyed by a
canonical, versioned taxon concept and is designed for API and agent composition
with botanical occurrence and environmental Parquet releases.

This is a reference product, not a GIS observation lane or map layer. The
existing `agri.species` model may remain the reviewed authoring surface because
curation is relational and lightweight. A database row is not a published
recommendation input: approved values must be frozen into one immutable
`botanical-species-profile` Parquet release before serving or training consumes
them. No database fallback may silently replace an absent published profile.

While the production census and immutable profile release remain blocked, the
read-capable service profiles may expose a transitional, read-only lookup of the
already-modeled authoring rows. This exception is keyed only by exact
`agri.species.id` UUID and labels all legacy wide-column values as unverified
authoring data. It is not a published profile, a fallback for a missing release,
or evidence that production is populated.

## Identity and release grains

The contract must preserve four linked grains:

| Artifact | Grain and purpose |
| --- | --- |
| Canonical taxon | One authority/version/concept identifier plus accepted and source names; name-only joins are prohibited. |
| Trait assertion | One taxon × trait × source assertion, with original value, normalized value/unit, qualifier, evidence type, licence, retrieved/valid dates and review state. |
| Reconciliation decision | One taxon × trait decision that selects, combines, refuses or records conflict among assertions without deleting alternatives. |
| Published profile | One release × canonical taxon containing approved serving values, declared missingness and links to all contributing assertions and decisions. |

Release identity binds the taxonomic authority, source versions, normalization
recipe, reconciliation policy, reviewer decision and content hashes. A future
source may fill a missing value or create a competing assertion; it does not
silently overwrite the published value. Reconciliation must publish a new
release and explain every changed field.

## Profile scope

The first profile contract may cover growth habit, native or introduced status,
hardiness range, temperature and precipitation envelopes, light requirement,
soil moisture or drainage, soil texture, pH, salinity, elevation, drought
tolerance and phenology where a source actually supports the term. Each field
declares whether it is a measured trait, curated requirement, categorical expert
summary or occurrence-derived association. These evidence classes cannot be
merged without a visible rule.

Fuel and deployment analysis may additionally admit tissue water content, live
and dead fuel moisture, leaf dry-matter content, volatile oils, extractives,
resins, heat content, ash or mineral content, surface-area-to-volume ratio,
bulk density, curing, litter persistence, canopy or branching architecture,
rooting form, biomass accumulation and nitrogen-fixation evidence. Each assertion
must retain tissue or fuel component, live/dead state, wet/dry basis, unit,
measurement method, season, life stage, geography and environmental context.
`fire_tolerance`, post-fire regeneration, flammability, volatile content and
fuel-bed behavior are different concepts and cannot share one Boolean.

The current `agri.species` columns are an input to the schema inventory, not proof
that their units, vocabularies or provenance are sufficient. The inventory must
reconcile the existing growth, tolerance, precipitation, pH, light and use-value
fields before proposing a migration or a compatibility adapter.

Establishment requirements remain separate from objective effects. Drought
tolerance can help answer whether a species may establish under a water-limited
condition. It does not prove that planting the species mitigates drought.
Wildfire, water, carbon, soil-amendment and other outcome claims require separate
species × objective × condition evidence under the existing recommendation
effect contract.

## Source strategy

Admit the first trait source field by field. Record source terms, version,
coverage, identifiers, units, evidence class and missingness before loading it.
A second source is deferred enrichment: it may expand coverage or provide an
independent assertion after its own admission review, but it is not a prerequisite
for the first specimen pilot or occurrence publication.

Where specimen taxa cannot be resolved to the profile authority, retain an
ambiguous or unmatched state. Do not infer requirements from the specimen
location during the first profile release. A later occurrence-derived association
must be separately versioned, sampling-bias aware and labelled as an association
rather than a physiological requirement.

## Profile API and recommendation handoff

The final profile endpoint accepts canonical taxon identity and a pinned profile
release. It returns approved values, missing and conflicting fields, per-value
provenance, evidence class, licence, release identity and continuation limits.
Normal serving reads the immutable Parquet profile release. The relational
authoring tables are not a serving fallback.

The transitional endpoint instead accepts one exact Species UUID and returns
identity labels, existing lightweight profile fields, and bounded approved
companion rows from the reviewed authoring database. Every value carries
authoring provenance and an honest `unverified_authoring` or
`unknown/not_reported` state. It declares `publication_state=not_published` and
`profile_release_id=null`, reads no GIS/environmental observations, and refuses
ranking, planting, occurrence, suitability, objective-effect, and unsupported
fuel/fire conclusions.

The agent's species-information tool exposes growth requirements, fuel or tissue
composition, agricultural roles, companion evidence and explicit missingness as
separate sections. It returns raw assertion citations as well as normalized
values. It refuses fuel, fire, agronomic or planting claims that lack the
required measurement context or a separately reviewed effect assertion.

This track does not rank species. It hands the pinned profile release and
per-value evidence to `botanical_species_recommendation_validation_20260911`,
which composes three independently identified evidence families:

1. **Documented occurrence:** what admitted specimen releases document nearby,
   with collection bias, age, uncertainty and absence limits.
2. **Establishment compatibility:** how published growth requirements compare
   with the selected environmental release, day/window and spatial support,
   returning compatible, incompatible, unknown or not-evaluated per field.
3. **Objective effect:** whether separately reviewed evidence supports the claim
   that the species changes the requested outcome under comparable conditions.

The profile agent returns only the selected taxon's profile, contributing
assertions and release identity. The downstream recommendation track must not
turn a nearby specimen into a suitability verdict, a growth match into an effect
claim, or an unknown field into a default match.

## Acceptance

Acceptance requires exact authoring-to-Parquet reconciliation, per-value
provenance and licence checks, deterministic normalization, conflict and
withdrawal behavior, source/version changes, canonical taxon matching,
idempotent replay, conditional publication, rollback, bounded profile API and
agent lookup, downstream handoff conformance and independent botanical and
data-governance review.

The independent reviewer runs after authoring, publication, API and integration
slices finish. The integration receipt is owned by `p4`; the independent verdict
is owned by dependency-last `p5`. An author or integrator cannot approve its own
source, botanical or agent claims.
