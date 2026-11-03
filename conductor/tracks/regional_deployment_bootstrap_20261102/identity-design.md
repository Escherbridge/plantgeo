---
type: architecture
---
# Custom-region admission and reuse

Status: independently investigated design; not implemented by the manifest foundation batch.

## Implementation contract under construction

The next batch uses a versioned typed byte encoding with finite IEEE754 big-endian hexadecimal numbers (negative zero normalized), UTF-8 byte-length strings and UTF-8-sorted object keys. Integer inputs outside the binary64 safe-integer range are refused. Shared golden vectors must prove Python/browser agreement before admission.

Requests carry `X-PlantGeo-Expected-Region-Definition`; responses carry `X-PlantGeo-Region-Definition`, including legitimate empty/unavailable responses. Definition/namespace mismatches return sanitized HTTP 409 refusals. Coverage adds `region_definition_sha256`, `region_geography_sha256`, and `region_identity_version`; dedicated soil status uses the camelCase equivalents.

Server caches bind the resolved data-service endpoint plus expected definition. Browser persistence binds the public tRPC origin plus definition, without exposing the server-only data-service URL. Physical storage moves under the same proven definition retain ordinary historical-cache TTL/revalidation semantics; current capability eligibility remains authoritative. This does not authorize reassigning legacy unscoped entries to a custom definition.

New browser persistence must be scoped for packaged regions as well as custom ones: a packaged manifest can change in a later code release. Prefer a cache-schema migration that treats old unscoped records as misses over assuming they belonged to the currently compiled definition. Refilling a device cache reads the governed serving plane, not upstream providers. Global retained-byte accounting still includes old scopes and bounds their cleanup.

These are coordinated implementation decisions, not a claim that enforcement or custom source admission is finished.

## Three identities

Use a geography digest for the immutable storage namespace, a per-stream support digest for observation/absence reuse, and a definition digest for web/service agreement and response caches. Geography includes the actual footprint, source-relevant camera envelope, subdivision envelopes, country/admin membership, timezone and CRS semantics. Stream support adds the binding, analysis lattice and source/product/schema contract versions. Definition includes geography and sorted selected bindings/support identities.

Labels, credentials, history obligations and refresh cadence must not control data identity. Adding a layer changes agreement without invalidating existing proven streams. Changing a lattice invalidates only its stream. Extending a history range creates work without invalidating already fetched history.

Python and TypeScript must share canonical golden vectors. Ordinary JSON serialization is insufficient because numeric formatting, negative zero and ordering differ. Normalize unordered memberships and bindings while retaining genuinely ordered geometry.

## Enforcement boundary

1. Create custom storage under a deterministic geography-derived prefix, with a conditionally created immutable namespace receipt. Verify it before any listing, census, row read or publication. Missing or incompatible receipts fail closed; never stamp a new digest onto old objects.
2. Bind every custom stream to immutable support proof. Pointer resolution and publication both verify it. Absences require the same proof as observations. A changed lattice/source cannot inherit old completion claims.
3. Carry definition identity through warehouse coverage, slider capabilities, layer eligibility and dedicated soil status. Packaged regions may retain explicitly documented legacy compatibility, but contradictory stated identities must be refused. Custom regions require identity.
4. Check expected identity on the actual row request and response. A successful earlier census cannot authorize rows following a service configuration change.
5. Scope coverage and generation caches to endpoint/storage/identity. Scope browser persisted queries and their synced-day, clearing, revalidation and accounting metadata within the existing global byte budget. Late requests from an old scope must not populate a new scope. Old unscoped records cannot satisfy custom-region requests.

## Existing gaps and implementation seams

The HTTP coverage path currently reads before stamping the configured region. `CoverageCache` scopes freshness by lanes/TTL. The web row guard learns only a slug and permits cold reads without a learned census. IndexedDB persists bare query hashes for historical data. Thus accepting custom JSON alone does not establish safe custom-region serving.

Storage enforcement belongs around runner binding, availability storage and HTTP listing/availability-reader construction. Publication proof belongs in availability identity/pointer resolution or a mandatory immutable stream admission receipt. Wire propagation includes `parquet_ops/wire.py`, `parquet-plane-client.ts`, slider capabilities, `layer-region-binding.ts`, and the separate soil-survey status path. Row exchange checks belong in shared HTTP answering and central web readers, with equivalent treatment for dedicated endpoints.

## Required evidence

Cross-runtime golden digests; unchanged identity after labels/history changes; isolated support invalidation; missing/mismatched namespace and stream proof refusal; inability to relabel old census data after a same-slug change; identity change between census and row reads; custom cold-census failure; dedicated soil status parity; persisted history isolation; bounded cache cleanup and old-scope late-write rejection.

Do not admit or deploy custom-region ingestion until these checks, source-support admission and isolated bootstrap state are complete together.
