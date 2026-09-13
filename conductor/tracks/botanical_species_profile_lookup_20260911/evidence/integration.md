---
type: integration-evidence
track: botanical_species_profile_lookup_20260911
status: local-implementation-verified-candidate-unaccepted
---

> **Carried forward from `codex/botanical-species-profile-lookup` commit `edc6afd`, 2026-09-12.**
> This describes that branch's own tree at that commit, not the current
> `claude/herbaria-botanical-lanes` port. See [port-notes-20260912.md](port-notes-20260912.md)
> for this port's own reconciliation and test results.

# Local implementation and unaccepted candidate handoff

The implementation starts on committed main
`bc7b5e1ff5dbb6eac929d5db927acd6b7ff2ea4a`, including the original `b9b7bf4`
contracts and the separately owned PNW admission packet. That packet is unchanged.
The continuation after host shutdown performs only local implementation, tests,
review and commit. It opens no private environment file, accesses no remote
service, ingests no source data, writes no database, publishes no data release and
deploys nothing. The existing four-taxon bundle remains an **unaccepted local
fixture/candidate**. Production authoring census and source admission are pending.

Source rights and field scope belong to
`botanical_species_source_admission_20260911`; build, serving and agent behavior
belong here. Neither storage choice has a separate track.

## Preserved local candidate

Release ID:
`bspf-0f6b58aba4610edf9b6bcde1e7a0da0c44cf3e4671c1b624b13d7ed95b5ab501`.

The earlier local build produced and read back five immutable Parquet objects: four
taxa, eight source assertions, 188 per-field reconciliation decisions, four
profiles and one manifest. There are six directly linked synonym names and zero
admitted cultivars. All 47 fields exist in each profile: two categorical fields
are known and 45 remain explicitly unknown. No absent value becomes false, zero,
a growth match or a mitigation claim.

| Taxon | WCVP v16 plant_name_id | Direct synonyms | Admitted values |
| --- | --- | --- | --- |
| Pseudotsuga menziesii | 379633 | 1 | lifeform `tree`; habitat summary `temperate` |
| Pinus ponderosa | 380358 | 0 | lifeform `tree`; habitat summary `temperate` |
| Alnus rubra | 6584 | 4 | lifeform `tree`; habitat summary `temperate` |
| Trifolium repens | 2439997 | 1 | lifeform `perennial`; habitat summary `temperate` |

Names were discovered inside one pinned Kew release and then selected by exact
IDs. Synonyms use that release's accepted-ID links; synonyms of accepted
subspecies do not silently become species-level synonyms. Raw taxonomy records,
source authors, ranks, IDs, family-review flags and raw categorical values remain
in the release. The source `reviewed=Y` is family peer review, not measurement or
independent per-value approval. The preserved source descriptor records its author review at creation time. The
final local code/API/agent verdict does not independently admit those source
values or authorize their production use.

The source is WCVP 16, extracted 2026-06-04, under archive-specific CC BY 3.0.
Attribution: Govaerts R (ed.). 2026. WCVP: World Checklist of Vascular Plants.
Facilitated by the Royal Botanic Gardens, Kew. The derivative selects four taxa,
preserves source summaries unchanged and adds evidence/missingness envelopes;
it does not infer quantitative establishment requirements.

The complete 88,179,649-byte source archive remains an ignored local artifact.
The five lookup artifacts total 55,323 bytes and are committed under
`evidence/releases/<release-id>/`. `integration-receipt.json` maps each portable
file to its immutable object key and SHA-256. The original 18,112-byte archive
README is committed with source-admission evidence, retaining release and reuse
terms even if Kew's mutable download URL advances.

## Local inspection and future publication boundary

The five portable Parquet files retain their original SHA-256 values and release
identity. `integration-receipt.json` maps them to the immutable object keys.
For local inspection, place copies at those keys under a scratch store root and
run `python -m agri_data_service.pipeline.direct.botanical_species_profiles inspect
--store <root> --release-id <exact-id>` with the data service's `src` on
`PYTHONPATH`. Inspection verifies all artifacts and performs no source ingestion,
network or database access. The mutable pointer is never a serving default.

The offline builder, conditional pointer publication and rollback are implemented
and exercised by synthetic local tests. Running them with source data requires
a later authorized continuation after census and source admission. No successor
source candidate or remote publication is created by this continuation. The
existing immutable candidate retains its creation-time census-unavailable note;
that note is historical and does not establish any production table contents.

The CLI accepts explicit census state/note metadata for a future reviewed
snapshot. The operator must first verify the receipt and attach its path, SHA-256
and preservation decision. A flag does not perform or certify a live census.

## Registered API and tools

`GET /api/v1/botanical-species-profiles/lookup` is mounted by `create_app` for
`combined_local` and `published_reader`. Its required query parameters are
`authority=WCVP`, `authority_version=16`, `taxon_id=<canonical-id>` and the exact
`release_id` above. There is no name-only, latest, selected-day or zoom mode.
`receiver_writer` does not mount the route.

The `species_information` tool takes the same identity and release, plus optional
`assertion_limit` and `cursor`. It is part of `WAREHOUSE_TOOLS`; the Anthropic
runner, OpenAI-facing schemas and MCP list/call dispatch derive from that same
registry. Reference profile counts do not increase environmental row coverage or
the agent's local-evidence sufficiency denominator.

Responses retain six sections: growth requirements, fuel traits, fire response,
agricultural roles, companion evidence and objective effects. They carry raw and
normalized assertions, licence, source versions, measurement context, decisions,
unknown/conflicting/refused/withdrawn fields and release identity. Assertion pages
default to 25 and cap at 100, the shared reader has a 14-second deadline, and a
profile JSON payload above 2 MiB is refused; HTTP uses the same compact UTF-8
bytes that the reader measures. MCP adds its ordinary protocol envelope. Continuation tokens bind both taxon and release;
consumers must collect all contributing assertion IDs before citing a field
whose evidence spans pages. Unpublished or corrupt releases return typed
refusals. An absent exact taxon is an explicit unknown.

The default serving adapter uses the existing validated object-store configuration.
No profile-specific database connection or query exists. Local tests and the
demonstrator inject a read-only local store; they do not contact production.

## Scientific, census and production gates

Production authoring census remains pending. Current deployed columns, counts,
review states and values are unknown. Local PostgreSQL/`pgt` belong to another
project, and the earlier Railway attempt did not complete a census. The sanitized
`railway-census.json` records only that limited outcome. No private environment
file, remote service or database is accessed in this local-only continuation.

A future census must preserve original relational UUIDs and reviewed evidence.
Crosswalks, source/licence/version evidence and measurement context are required
before separate assertions can enter a successor candidate. Default Booleans,
name-only matches or existing approvals cannot fill missing provenance. The
preserved WCVP candidate does not supersede existing reviewed rows that may exist.

Quantitative temperature, precipitation, hardiness, light, soils, salinity,
elevation, drought and phenology requirements remain gaps. USDA PLANTS requires
an exact reusable versioned characteristics delivery and an authoritative taxon
crosswalk. Agricultural roles and companions remain gaps. TRY requests and
restricted records are excluded. FEIS requires reviewable per-value citations,
methods and applicability before any fuel assertion can enter.

All 19 fuel fields are unknown: tissue water, live and dead moisture, leaf dry
matter, volatile oils, resins, extractives, heat, ash, minerals, surface-area to
volume, bulk density, curing, canopy and branching architecture, litter
persistence/behavior, fuel-bed behavior and flammability. Fire tolerance, response
and post-fire recovery are separate unknown fields. No lifeform summary is used
to fill any of them.

Production still requires an explicitly authorized immutable-object upload,
readback at the production reader, ownership integration, access/configuration
checks and a chosen pinned release in the caller. This task performs none of
those production actions. Any recommendation ranking, occurrence/environment
composition, suitability validation or objective-effect claim belongs to
`botanical_species_recommendation_validation_20260911` and remains unserved here.

Final local verification and independent code/API review are recorded in
`integration-receipt.json` and `independent-review.md`; source-author and integrator
statements do not constitute the independent verdict. That verdict covers code
and agent behavior only; source admission and the production census remain open.


## Final local verification

The complete Python service sweep passed formatting, Ruff, mypy and pytest on
service digest `b0ddaeeb60a4374bbd5c98b96de6e679de19ec5b2de206beacdb90eebf4b3674`
over 1,328 files. Pytest recorded 6,086 passed, 150 skipped and one expected failure,
with zero failures or errors. External database test variables were absent.
`python-quality-receipt.json` is the official full-sweep receipt.

The repository boundary and TypeScript checks passed. ESLint passed with zero
errors and 542 pre-existing warnings in unchanged baseline files. The changed
JavaScript/TypeScript test selector chose no test surface; this is not a full
frontend-suite pass. `verification.json` records commands, log hashes, counts,
limits and earlier failed attempts with their resolved causes.

Local tests cover immutable content integrity, canonical/source identity,
corrections and withdrawals, unknown/refused fields, registered reader-profile
HTTP routes, byte-exact UTF-8 response limits, provider schemas, WAREHOUSE_TOOLS,
MCP list/call, continuation and environmental-sufficiency exclusion. These are
local engineering checks; no production endpoint or source admission is certified.
