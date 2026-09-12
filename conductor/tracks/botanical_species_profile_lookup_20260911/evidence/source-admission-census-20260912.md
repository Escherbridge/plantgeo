---
type: evidence-receipt
track: botanical_species_profile_lookup_20260911
date: 2026-09-12
status: blocked-before-database-connection
audit_base_commit: d42e28ec43368fe9340eb70b4f8b1dcd51ce125f
audit_base_tree: 21faf5359d59325b2955383b2f326371875b757c
---

# Botanical source-admission census and next-gate plan

## Verdict

The production authoring census and every botanical source-admission gate remain
blocked. This read-only local audit identified no evidence that permits a database
connection, source acquisition, ingestion, publication or deployment. It contacted no
Railway service, database, object store or provider; read no credential value; and ran no
application, ingestion or data-service code. Database connections, queries and writes
were all zero.

The current `main` baseline contains the bounded transitional exact-UUID authoring
lookup. The release-pinned WCVP implementation remains a retained, unaccepted Git
candidate outside the current tree. WCVP/non-Herbaria profile admission and WTU/UBC
Herbaria occurrence admission are separate gates. Neither supplies admission evidence
for the other, and neither is a GIS environmental layer.

## Current custody and implementation census

| Surface | Current evidence | Disposition |
| --- | --- | --- |
| Current checkout | Audit base `d42e28e`, tree `21faf535`; the audit began clean on isolated branch `codex/botanical-source-admission-census`. | Documentation-only audit surface. |
| Transitional lookup | The [profile specification](../spec.md) and [local receipt](transitional-authoring-lookup-2026-09-11.md) retain the read-only `agri.species`/approved-companion lookup keyed by exact internal Species UUID. | Integrated but unpublished and unverified authoring data only. It refuses occurrence, ranking, planting, suitability, objective-effect and unsupported fuel/fire claims. |
| Release-pinned candidate | Branch `codex/botanical-species-profile-lookup` still resolves to commit `edc6afdeb23f339b40049ec0828551c2fe1a4d45`, tree `01ad55b6220a13604e8fbf8a4ceda773e35d5658`. It is not an ancestor of this audit base. | Retained candidate evidence, not current-tree implementation, source admission or production acceptance. |
| Canonical local intake | Git objects `32a13c604e6da4331051e6a584f7942e7165af1e`, `2317ee3a2ac35de489d27503dfec91ea3285bd66` and their stated tested source `416342fa5bea836e6a062549b87c2706f53ce45e` remain present. | Historical local integration and verification only; no production or source-admission claim. |
| Portable WCVP candidate | The [reconciliation receipt](census-reconciliation-20260912.json) records five Git-retained Parquet objects totaling 55,323 bytes and matching their original hashes. | Unaccepted local candidate. No object was served, published or re-ingested by this audit. |
| Raw WCVP archive | Recorded identity remains 88,179,649 bytes and SHA-256 `d32ea2b3a85e489b14e83bcc9eae7274532e1d113753f7be290d4b2dfde573fa`. A fresh exact-path check found it absent at the same four locations listed in the reconciliation receipt. | Current bytes were not rehashed. Custody elsewhere is unknown; this is not a claim of global loss. Do not redownload or re-ingest merely to repair custody. |

The branch-only non-Herbaria source packet identifies WCVP version 16, extracted
2026-06-04, as a four-taxon fixture with six directly linked synonym rows. It has four
lifeform and four climate-category values and no quantitative growth, water/oil/fuel,
agricultural-role, companion-effect or recommendation evidence. Its own metadata and
independent code review say production census and independent source admission are
pending. Therefore the candidate remains unaccepted even though local transport,
contract and agent checks historically passed.

USDA PLANTS, TRY and FEIS remain planning/deferred sources. No exact admitted release,
field-level rights and training-use verdict, source hash, canonical crosswalk, complete
measurement context or independent field decision exists in the current evidence. No
field from those source families is admitted by this census.

## Railway census blocker

The prior [reconciliation receipt](census-reconciliation-20260912.json) records a dated
authenticated platform-metadata observation for PlantGeo project
`6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`, production environment
`b7cfa813-8a5c-4fcd-80f2-cab736d840a7` and service
`plantgeo-spatiotemporal-db` (`1e166530-9c8a-4d4a-b685-a70c801fc449`), with an active
public PostgreSQL proxy at that observation time. This audit did not revalidate that
metadata. It establishes a historical scoped target, not current reachability,
authentication, database identity or table contents.

Credential values were never read by the prior reconciliation, its private-environment
inspection did not execute, and no alternate credential path was attempted. This audit
likewise had no permitted credential, network or database path. Local preflight exposed
no relevant connection-variable names, linked Railway directory, Railway CLI,
`psql` or `pg_isready`; no private environment file was opened. The task boundary would
still prohibit a connection if those tools were present.

Accordingly all of the following remain unknown: usable DSN, verified read-only role,
actual database name, current proxy/reachability, deployed schemas and constraints,
presence and counts of `agri.species` and `agri.companion_relationships`, identity,
review and provenance columns, review-state distributions, UUID validity/duplication,
and preservation of reviewed rows. A successful count-only query would not establish
reviewed-row preservation, canonical taxon reconciliation or source admission.

The historical candidate commit contains
`conductor/tracks/botanical_species_profile_lookup_20260911/evidence/railway-census.json`,
whose sanitized result is `production_census_pending` after a prior
`network_connection_unavailable` attempt. That file is absent from the current tree.
The link to it in [the September 12 handoff](census-handoff-20260912.md) is therefore
broken in this checkout, contrary to the accompanying historical review's
`markdown_links_resolve=true` claim. This receipt preserves the defect rather than
rewriting the historical handoff or pretending the branch-only JSON is current evidence.

## Herbaria and GIS boundary

The PNW Herbaria governance track is active while both collection decisions are blocked
and `admitted_releases` is empty. Active governance work is not an admitted occurrence
release. WTU lacks release-bound EML/terms/public-population and immutable identity
evidence. UBC v16.43 has retained standalone EML metadata, but the institutional
distribution still lacks an authoritative, applicable public-coordinate withholding and
generalization policy. Neither archive's bytes, member hashes, actual field map,
complete population or native-ID stability have been measured.

The latest [retained-evidence governance audit](../../pnw_herbaria_source_admission_20260911/evidence/source-governance-audit-20260912.md)
is itself pending independent review. Earlier review and synchronization receipts do not
approve that successor audit, admit a release or authorize transfer. Before any future
capture, the collection-specific pre-acquisition gates, appointed custodian,
access/retention/disposal decision, immutable manifest procedure, attribution and
enforceable archive controls must be approved. The inherited ceiling remains two
archives, one transfer at a time, 64 MiB compressed per archive, 128 MiB compressed
total, 2 GiB decompressed total, 32 members, 600,000 core rows, two million extension
rows, eight HTTP attempts and ten minutes. This audit consumed none of that budget.

Only an independently admitted exact Herbaria release may unblock the separate planned
botanical occurrence Parquet plane. That future spatial plane owns specimen facts,
uncertainty and sparse documented-taxon summaries. It must not acquire trait/profile
columns, infer abundance/current occupancy/suitability, or fall back to an editable
database row. The lightweight species lookup may remain nonspatial and independently
versioned if later approved.

## Exact evidence that unlocks the next gates

1. **Production authoring census:** a separate operator-authorized session supplies a
   secure DSN matching the recorded PlantGeo service and a verified read-only role,
   without printing or persisting credentials. Use a suitable PostgreSQL client with
   startup read-only mode, `REPEATABLE READ READ ONLY`, ten-second connect timeout,
   five-second statement timeout, one-second lock timeout and rollback on close. Record
   only schemas/constraints, aggregate counts, review/provenance coverage, review-state
   distributions and UUID preservation for `agri.species` and
   `agri.companion_relationships` in a new additive dated receipt.
2. **Non-Herbaria profile source admission:** recover custody of an existing WCVP v16
   archive only through an approved custody path and verify its recorded hash; do not
   duplicate-download or ingest it for this gate. Then obtain a non-author,
   field-level botanical/data-governance/rights verdict bound to the source and candidate
   hashes. Preserve unknown traits and unsupported effects. Any USDA/TRY/FEIS enrichment
   requires its own exact-release admission.
3. **PNW Herbaria admission:** independently review the latest retained-evidence audit,
   close WTU release-bound EML/terms/identity or UBC distribution-specific coordinate
   policy, and approve concrete custody/archive controls before requesting bytes. A later
   permitted quarantine pilot measures archive/schema/population/native-ID evidence and
   still requires an independent exact-release admission verdict before occurrence
   handoff.
4. **Serving and publication:** keep the existing exact-UUID transitional route
   fail-closed. Do not merge the branch-only candidate or expose a release label around
   editable wide rows. Final profile serving requires admitted per-value evidence,
   immutable release-pinned artifacts, no database fallback and separate API/agent/MCP
   acceptance. Occurrence GIS publication and recommendation validation retain their own
   later gates.

## Operation receipt

This audit performed zero source downloads, archive probes, database connections,
database queries, database writes, migrations, source ingestions, object-store reads or
writes, data publications, deployments, provider contacts and application/schema/API/data
changes. It preserves the existing blocked admission boundary.
