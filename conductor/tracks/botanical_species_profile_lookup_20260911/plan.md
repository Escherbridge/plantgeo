---
type: track-plan
track: botanical_species_profile_lookup_20260911
status: active
---

# Plan

The continuation after host shutdown is local only. Complete and verify the
Parquet reader, registered HTTP/model/MCP wiring and local implementation commit.
Preserve the existing four-taxon WCVP bundle as an unaccepted local candidate.
Do not open private environment files, access remote services, ingest source
data, write to a database, publish a data release or deploy. Local PostgreSQL
and `pgt` belong to another project. Production authoring census and source
admission remain pending gates.

## P0 — repository inventory and contract

- [x] Inventory checked-in `agri.species`, companion evidence, current consumers
  and the species-trait vocabulary without claiming a deployed schema or row count.
- [x] Define exact taxonomic authority/version/concept IDs, source-name links and
  canonical-source record binding; refuse name-only joins.
- [x] Define per-value evidence, raw/normalized units, context, review, missingness,
  conflicts, corrections, withdrawals and immutable release grains.
- [x] Separate measured traits, curated requirements and categorical summaries
  from occurrence associations and objective-effect claims.
- [ ] Complete the authorized production authoring census in a separate continuation.
- [ ] Establish reviewed crosswalks for existing relational and occurrence concepts.

## P1 — source and reviewed authoring gates

- [x] Preserve the previously captured WCVP 16 archive inventory, exact README,
  four-taxon fixture and source-author field mapping without further ingestion.
- [x] Record source-field scope and unknowns; keep 19 fuel fields distinct from
  fire response and preserve measurement/context requirements.
- [x] Keep growth summaries, agricultural roles, companions and objective effects
  separate; defer USDA, TRY and FEIS enrichment.
- [ ] Complete independent source admission after the production authoring census.
- [ ] Preserve and crosswalk existing reviewed UUIDs/evidence into separate assertions
  before any successor candidate; never overwrite or promote default Booleans.
- [ ] Admit quantitative requirements and further sources field by field.

## P2 — local immutable lookup implementation

- [x] Implement taxa, assertions, decisions, profiles and manifest Parquet artifacts
  keyed by canonical concept and content-addressed reviewed inputs.
- [x] Implement conservative reconciliation, immutable writes, readback, conditional
  pointer updates, replay, interruption recovery and rollback.
- [x] Refuse absent/corrupt releases and prohibit database serving fallback.
- [x] Record the final integrated local verification result and exact tree digest.

## P3 — registered API and agent implementation

- [x] Implement bounded lookup by exact taxon and pinned release, retaining evidence,
  source/licence/review fields, unknowns, conflicts and continuation semantics.
- [x] Register the HTTP route in `create_app` for reader profiles and register
  `species_information` in WAREHOUSE_TOOLS for model schemas and MCP list/call.
- [x] Update model prompts and keep static profile counts out of environmental
  evidence sufficiency; refuse ranking and planting-effect claims.
- [x] Isolate shared owner changes in a patch with base-blob receipts.
- [x] Prove the registered HTTP, model/provider and MCP paths in the final sweep.

## P4 — local integration and independent code review

- [x] Record taxonomy/source joins, normalization, correction/missingness handling,
  publication recovery, bounded reads and no-database-fallback test evidence.
- [x] Publish a machine-readable local implementation receipt bound to the exact
  service tree; preserve the existing candidate byte hashes without accepting it.
- [x] Obtain a separate dependency-last code/API/agent-honesty verdict.
- [x] Commit the bounded local implementation on the isolated codex branch.

## Remaining production and science gates

The local code verdict cannot approve source data. Production census, reviewed-row
preservation, source admission, owner integration and explicitly authorized
publication/deployment remain separate gates. Occurrence/environment composition,
quantitative establishment validation, objective effects and species ranking stay
in `botanical_species_recommendation_validation_20260911`.
