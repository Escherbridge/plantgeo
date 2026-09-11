---
type: track-plan
track: botanical_species_profile_lookup_20260911
status: active
---

# Plan

The authorized implementation session may admit and ingest verified
non-Herbaria reference sources, publish a bounded profile release and wire the
species-information agent tool. It may not accept restricted terms, scrape an
unlicensed source, infer missing traits or mutate production.

## P0 — inventory and contract freeze

- [ ] Inventory `agri.species`, its evidence/review posture, current readers and
  the species-trait feature vocabulary before proposing schema work.
- [ ] Freeze the canonical taxon authority/version and the join from admitted
  occurrence concepts without relying on normalized name text alone.
- [ ] Freeze assertion, reconciliation, conflict, missingness and published
  profile grains with per-value source and licence provenance.
- [ ] Separate measured traits, curated requirements, categorical summaries and
  any future occurrence-derived associations.

## P1 — source admission and reviewed authoring

- [ ] Admit the first growth-requirement source field by field, including bulk
  access, terms, coverage, identifiers, units, version and update behavior.
- [ ] Admit fuel and deployment assertions only with tissue/fuel component,
  live/dead and wet/dry basis, unit, method, season, life stage, geography and
  environmental context; keep water, oils, resins, heat, ash, curing, structure,
  fire response and post-fire recovery distinct.
- [ ] Map source assertions into a reviewed authoring surface without treating
  the existing wide `agri.species` columns as sufficient evidence by themselves.
- [ ] Defer a second source as a separately admitted enrichment; preserve
  competing assertions and never overwrite silently.
- [ ] Keep suitability requirements separate from species-objective effect
  evidence used by the recommendation models.

## P2 — immutable lookup publication

- [ ] Build assertion, decision, profile and release-manifest Parquet artifacts
  keyed by canonical taxon concept.
- [ ] Reconcile every approved authoring value to the published profile and
  declare conflicts, missing fields, rejected assertions and withdrawals.
- [ ] Publish all required artifacts before one conditional release pointer
  advances; prove idempotent replay, interruption recovery and rollback.
- [ ] Prohibit direct database serving fallback when a profile release or field
  is absent.

## P3 — profile API, agent lookup and recommendation handoff

- [ ] Expose bounded profile lookup by taxon and pinned release with per-value
  evidence, licence, review state, conflict and missingness.
- [ ] Wire a species-information agent tool that returns growth requirements,
  fuel/tissue composition, agricultural roles, companion evidence, citations
  and explicit unknown/refusal states from the pinned profile release.
- [ ] Hand the pinned profile and assertion schema to the separate recommendation
  validation track; do not rank species in the lookup plane.
- [ ] Verify that API and agent reads use the immutable Parquet profile rather
  than silently falling back to editable database rows.
- [ ] Prove the profile route through the registered application blueprint and
  the species-information tool through both the model-facing tool registry and
  MCP descriptor/call surfaces; importing a new module is not acceptance.

## P4 — integration and independent acceptance

- [ ] Verify taxonomy joins, units, normalization, per-field licences, conflict
  handling, source updates, authoring-to-Parquet reconciliation and rollback.
- [ ] Verify that the lookup makes no occurrence, suitability or objective-effect
  claim and that its downstream handoff retains every source identity.
- [ ] Have the integration owner publish a machine-readable exact-tree receipt
  after authoring, publication, API and agent-tool wiring are combined.
- [ ] Obtain a dependency-last, separate botanical-science, data-governance and
  agent-honesty verdict after the integration owner finishes; the reviewer does
  not author or integrate the candidate.
