---
type: track-spec
slug: botanical_species_source_admission_20260911
status: active
---

# Non-Herbaria botanical reference source admission

## Outcome

Produce a reviewable admission decision for each non-Herbaria botanical reference
source before its values enter a published species profile. Bind the decision to
the official delivery, exact source release, content hashes, identifiers, field
semantics, reuse terms, coverage, exclusions and unresolved gaps.

This track owns source research and admission evidence. The
`botanical_species_profile_lookup_20260911` track owns the authoring contract,
Parquet publication, HTTP lookup and agent integration. The recommendation
validation track separately owns scientific composition and ranking. This is not
a storage implementation track.

## Authorization and boundaries

The user explicitly permits anonymous reads and bounded downloads from official
public endpoints. No account, correspondence, data-access request, agreement or
terms acceptance is authorized. Restricted and embargoed records stay blocked.
WTU, UBC and other PNW Herbaria specimens remain owned by their separate admission
track and are excluded here. No production or relational database write is part
of this source-admission slice. The later continuation after host shutdown is
local only: no private environment files, remote service access, source ingestion,
database access, publication or deployment. The captured WCVP bytes and inventory
remain an unaccepted local candidate. Production authoring census and independent
source admission are pending before further source ingestion or publication.
Local PostgreSQL and `pgt` belong to another project and are excluded; neither
local connection behavior nor historical notes establish PlantGeo table contents.

## Admission contract

Each source receipt records official URLs, retrieval time, source version/date,
byte length, cryptographic hashes, archive members, field definitions, units,
taxonomic identifiers, source review meaning, allowed uses and attribution.
Missing terms or delivery identity must be explicit. A mutable `latest` URL is
insufficient identity without its captured bytes and content hash.

Admission is field-specific. Taxonomy, lifeform, published habitat summaries,
measured traits, physiological requirements and objective-effect evidence retain
their distinct meanings. A habitat category cannot become a numeric growth
envelope, a regional distribution cannot become an occurrence observation, and
family membership cannot prove nitrogen fixation or another agricultural role.

Canonical identity combines authority, source release and the source's accepted
concept ID. An initial name search within one pinned authority may discover IDs;
subsequent imports and joins must use those IDs. Preserve synonym IDs, original
status/rank, authorship, hybrid markers and infraspecific concepts. A synonym of
an accepted subspecies must not be reassigned to its parent species. Cultivar
identity remains unavailable if the admitted source does not supply it.

Fuel admission requires per-value evidence for component/tissue, live/dead state,
wet/dry basis, units, method, season, life stage, geography and environmental
context. Water, dry matter, oils, resins, extractives, heat content, ash/minerals,
curing, architecture, litter and fuel-bed behavior remain distinct from fire
tolerance, fire response and recovery. Missing context blocks a quantitative fuel
assertion; it does not justify defaulting to a Boolean fire trait.

## Initial candidate

The first inspected source is Kew WCVP version 16, extracted 2026-06-04. The
official archive is licensed CC BY 3.0 and is pinned in
`evidence/source-admission.json`. Four selected accepted species have the source's
family-peer-review flag `Y`. Their lifeform and climate descriptions may support
categorical summaries only. The flag is not a per-value scientific approval.
Achillea millefolium remains excluded from this reviewed candidate because its
flag is `N`.

USDA PLANTS growth characteristics, public TRY enrichment and FEIS fuel values
remain separate candidate sources. Each must pass its own delivery, release,
rights, taxonomic crosswalk and per-field evidence gates before contributing.

## Acceptance

Acceptance requires a reproducible source receipt, exact schema/row inventory,
explicit field admissions and exclusions, preserved source identity, and an
independent botanical and data-governance verdict after the candidate is
assembled. This authoring slice cannot approve its own source claims. The
profile track's dependency-last reviewer may supply that verdict and must cite
this source receipt. Publishing or deploying a profile remains a downstream gate.
