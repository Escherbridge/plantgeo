---
type: track-evidence
track: postgres_shrink_ingest_repoint_20260825
audited_on: 2026-09-12
status: ownership_reconciled_execution_gates_open
source_commit: 64f4f892bd2b744cc097c7f76a1f239997b80f52
source_tree: f8697da694a3f9fbbf28e70ff7581bd59546bfd6
---

# Ownership and removal-proof audit

This repository-only audit reconciles the shrink track's current authority with
its successors and the September 12 retirement, publication, offline-export and
conformity receipts. It did not access PostgreSQL, `pgt`, Railway, object
storage, a writer, scheduler or deployment surface, and it changed no runtime
code. It records no cutover, publication, removal, migration, acceptance or
completion.

## Supersession controls the historical ledger

The September 11 authority blocks at the top of this track's spec and plan, and
the matching `metadata.json.reconciliation_20260911`, supersede the dated slice
ledger wherever they conflict. In particular:

- the September 8 baseline collapse and September 9 production rebuild mean the
  intact-database, no-drop and old P6 sequence are historical evidence, not an
  execution queue;
- `s0` may never restore a Railway cron or shared Parquet drain. Gapless `p5`
  owns executor-only handoff and controlled removal of legacy scheduler
  configuration; rollback disables the executor lane and does not recreate the
  cron or drain;
- `s2b` through `s4` forward writers, historical gap authoring, governed
  absences, repair schedules and scheduled-advance proof belong to
  `gapless_parquet_publication_20260901`; and
- all environmental P5/P6 relation, producer, filler, reader and data-plane
  retirement belongs to `environmental_postgres_retirement_20260904`. The old
  `s2` note and September 1 spec text saying P5/P6 remain here are retained
  provenance, not current authority.

The successor also replaces the old global-GREEN-before-any-drop rule. An
individual environmental relation may be retired only with its own three-part
packet: counted Parquet parity without under-coverage, repository-wide zero
readers, and an archived relation snapshot whose object key and SHA-256 are
recorded. Drops remain reviewed Alembic migrations rehearsed on the disposable
database for `agri`-owned relations and explicitly authorized for production.
`geo`/Drizzle-owned cleanup requires a current Drizzle-owner handoff and the
matching migration-contract update; Alembic does not own those objects. The
final acceptance verdict still governs completion, but is not a substitute for
any per-relation packet.

## Exact ownership of the remaining work

| Work | Current owner | Evidence still required |
| --- | --- | --- |
| Forward writers, gap/absence work, leases, recovery and scheduler handoff | `gapless_parquet_publication_20260901` | Bind every required historical horizon to a source-direct or preserved-Parquet owner; prove the effective deployed cutoff and leases; retain recovery receipts and three scheduled advances per activated lane; obtain the exact downstream acceptance handoff. The September 10 cutoff remains `configured_pending_deployment`. |
| Environmental code and relation retirement | `environmental_postgres_retirement_20260904` | Re-inventory current environmental relations, fill commands, recovery paths, Next.js/agent/Martin/CLI/test readers and support dependencies after the rebuild. Replace still-needed PostgreSQL archive paths, admit the pinned fixed-support artifact, then produce a three-part packet and reviewed rollback for each relation before removal; a migration may group coherent objects, but the evidence packet remains relation-specific. Signal, sensor, static-soil and older-MTBS gates remain open. |
| Agent/MCP environmental reads | `environmental_postgres_retirement_20260904` with reader/acceptance evidence from the named reader tracks | Repoint the selected-day, spatial-neighbour and temporal-neighbour paths without a silent PostgreSQL fallback; prove refusal/truncation semantics and zero legacy readers before deleting `sql/agent/` or related tools. Historical `s7` is provenance only. |
| Selected eight-lane historical builders | `offline_export_service_20260908` for its bounded builder/phase evidence; gapless/retirement for forward and cutoff consequences | Do not rebuild the rejected standalone staging service. Bind the current eight-lane source/history/rung manifest, reconcile `soil-field-vpd`, obtain accepted review and measured end-to-end cost for the selected builders, and separately recover/read back relative-humidity 1981–2017 availability. The selected ERA5-Land and NASA POWER builder files are not conformity deletion candidates. |
| Legacy scheduler files | `gapless_parquet_publication_20260901:p5` | Removal waits for exact deployed executor ownership, no-overlap/no-in-flight proof and the controlled handoff. No shrink or conformity task may restore or independently delete them. |
| Drizzle environmental row cleanup | `environmental_postgres_retirement_20260904` under the expanded successor charter | Keep community/feed/social data intact, update the Drizzle migration contract with any authorized migration, and retain per-layer preservation, rollback and acceptance evidence. The historical `s6` ownership field and conformity's dormant-migration inventory confer no write authority. |

## Package and conformity boundary

The completed `s2a` work is a prerequisite, not a standing implementation task:
the shared `parquet_ops/` core, grouped `interface/cli/` and `agri-service`
rename already landed. The only remaining shrink-side package obligation is to
freeze the exact current `s2a`-derived tree and issue an explicit, path-by-path
handoff before conformity `c2` edits `interface/cli/`, `parquet_ops/`,
`warehouse/parquet/` or the four named snapshot product scripts. That handoff
must preserve these exclusions and serializations:

- `interface/cli/data.py`, availability publication and the direct/executor
  registries remain with gapless while their artifact and handoff contracts are
  open;
- `pipeline/direct/`, its tests and lane registration are delegated forward
  publication scope, not conformity scope;
- the selected `build_era5_land_from_canonical_snapshot.py` and
  `build_nasa_power_from_canonical_snapshot.py` builders remain outside the
  current conformity `c2` inventory; and
- migration files and environmental removal code remain with environmental
  retirement, never with a generic dead-code sweep.

The September 12 conformity inventory is inventory only. Its package removals
for `@deck.gl/mapbox`, `@deck.gl/react`, `jotai`, `s3fs` and `redis` are already
recorded as applied; they must not be uninstalled a second time. Their remaining
gate is the one integrated frontend/Python/type/lint/test/build receipt on the
accepted final tree, including both Python image builds and independent review.
`preact` remains retained behind auth/lock-graph proof. The CLI/core candidates
are refactors, not deletions: the 26 pinned thin-adapter violations, six lane
framework types and snapshot receipt/finalization responsibilities need
behavior-, help-, exit-code- and byte-equivalence evidence after the explicit
handoff. Contingent routes, services and Python planes remain gated by their
named runtime/owner proofs; migration and serving tests remain protected
evidence.

## Next gates for this historical track

1. Record an immutable `s2a`-derived file/tree inventory and an explicit
   shrink-to-conformity handoff, with the gapless, selected-builder, migration
   and retirement exclusions above. Until then conformity `c2` remains gated.
2. Let conformity finish only its already-authorized package/refactor/removal
   candidates, run the single integrated final sweep, and obtain independent
   review and rollback evidence. Do not repeat completed dependency removals.
3. Keep forward publication, full-horizon gap ownership, executor cutoff,
   leases, recovery and three-advance proof in gapless. Nothing in this receipt
   authorizes a writer, scheduler or legacy-service change.
4. Keep every environmental producer/reader/fill-command deletion and every
   relation or row drop in environmental retirement. Require current inventory,
   source-direct replacement where needed, fixed-support admission, the exact
   three-part packet and production authorization for each mutation.
5. Recover or recapture the missing signal, sensor and static-soil inputs and
   re-hash them against the recorded identities. Capture fresh signal and sensor
   day/rung, availability, ownership and writer/retry-worker quiescence state;
   bind all twelve static-soil COG/PMTiles objects in a full-object immutable
   manifest; and obtain the deployed cutoff packet plus the bucket-pinned
   fixed-support artifact before any dependent retirement gate advances.
6. Preserve this shrink track as historical bridge/baseline and ownership
   provenance until the successors publish their exact handoffs. Do not restart
   the bridge, shared drain, old historical export or old P6 sequence, and do not
   mark this track complete from local evidence.
