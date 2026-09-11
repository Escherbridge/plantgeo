---
type: track-evidence
slug: environmental_postgres_retirement_20260904
recorded_on: 2026-09-11
status: active
---

# Product repair and retirement handoff

This branch prepares product repairs and strengthens retirement proof. It does
not close the environmental retirement track or authorize deployment, object
publication, a queue change, archival `pg_dump`, or a relation drop. The exact
review and final verification outcomes belong to the
[independent receipt](independent-repair-review-20260911.md).

The source base is `fa202230958fb55521963e886eb031be5fc266c4`; the branch is
`codex/environmental-retirement-repairs-20260911`. It started with the parent
task's uncommitted September 11 Conductor reconciliation. Unrelated inherited
changes remain outside this repair commit. Integrate that parent documentation
baseline as well: this track's current plan references its preserved operational
retrospective. Do not mistake those inherited files for a separately verified
product implementation or discard them during branch intake.

## Preserved artifact custody

The signal and sensor source archives and both current-MTBS source/preparation
directories now have exact copies in this task's
`.omc/research/preserved-repair-inputs-20260911/`. All 63 regular files and
239,572,564 bytes were compared with their originals after copying; the
[custody receipt](preserved-repair-inputs-custody-20260911.json) lists every hash.
This removes the older worktree as the only copy of the inputs needed for replay.
It does not establish remote archival or production database preservation.

The raw sensor archive does not contain its positive candidates. A separate
[supplemental receipt](preserved-sensor-candidate-custody-20260911.json) preserves
the six candidate, manifest and audit/provenance files, another 9,401,169 bytes,
under the same custody root. Both receipts are required for sensor repair intake.

The task artifact root is
`C:/Users/atooz/.codex/worktrees/2849/plantgeo/.omc/research/`. It also holds the
new older-MTBS preservation archive, the complete static-soil candidate objects
and the signal reconstruction/preflight artifacts named in the product packets.
These large byte artifacts are ignored by Git. Branch intake must preserve or
transfer the hash-pinned files as well as the committed evidence; a cherry-pick
alone does not carry the underlying source or candidate objects.

The evidence directory disables Git line-ending conversion for JSON files so
their complete byte identities survive staging and checkout. The final packaging
check compares every staged owned JSON blob with its recorded workspace bytes.

## Reviewable product packets

| Product | Local implementation and evidence | Required next owner action |
| --- | --- | --- |
| Signal | Exact 222-day archive verification, preserved original values and coordinate witnesses, ordinary multipart reconstruction, and a journaled physical correction operator. Fresh read-only PREPARE verified every day twice and bound 444 original plus 2,212 replacement pins. [Packet](signal-sensor-candidate-revalidation-20260911.md). | Establish mutation-time owner/quiescence proof and exact authorization, then complete every day/rung under real ownership. The gapless owner must bind correction/source receipts into bootstrap before reader acceptance. |
| Sensors | Reproduced 5,935 positive rows in 1,030 blocks, preserving QC values and incomplete pagination/roster evidence; recovered the older prepared correction request. [Packet](signal-sensor-candidate-revalidation-20260911.md). | Refresh target pins and quiescence before using the existing correction operator; preserve the explicit incomplete source population and independently read final availability and values. |
| Static SoilGrids | Twelve complete asset hashes, a pinned candidate manifest, full-resolution local point reader, actual valid pixels, nodata and boundary evidence. [Packet](soil-restoration-preparation-20260911.md). | Admit an immutable descriptor with full object readback and pointer CAS, then integrate the admitted reader/agent contract. The local candidate must not be relabeled as admitted. |
| Soil-survey | A bounded preservation preparer conserves native identities/attributes at z13 and generalized z9/z5/z0. Live census found 959 native parts in two roots without completion or population authority. [Packet](soil-restoration-preparation-20260911.md). | Obtain complete source/release authority and a streaming driver sized for the actual roots before replacing the registry's PostgreSQL exporter. Do not publish a partial root or define detail-only rendering as completion. |
| Older MTBS | Fresh 3,077-fire 1984–2017 source capture, 136 year/rung parts, current 747-fire rollback reproduction, preservation archive and blocked admission packet. [Packet](mtbs-older-recovery-20260911.md). | Resolve distinct-scope catalogue composition and physical/day-index collision policy before admission. Earliest eligible day is September 12; preserve current and historical semantics. |
| FIRMS and NWIS history | Product-specific source-only preparation packages and thin local scripts, preserving exact response/value/day identity and rejecting ambiguous input. One bounded NWIS source response preserves seven daily readings; it does not cover the historical population. [Packet](archive-source-preparation-20260911.md). | Complete source capture evidence and use a fenced generic historical admission/queue handoff. No old PostgreSQL archive handler was re-enabled or silently aliased to these candidates. |

## Shared-file proposals

These are implementation contracts for the owners of the shared files, not
changes made in this branch.

1. **Signal provenance bootstrap:** extend
   `scripts/compile_availability_bootstrap.py::_bind_day` and
   `_finish_compilation` to accept a reviewed, hash-pinned correction request and
   source archive receipt. Require the exact signal lane/day/rungs, completed
   correction journal and equality between request-bound physical parts and
   current objects. Bind these receipts through the existing
   `SourceEvidence.object_receipts`; physical completion markers alone do not
   carry coordinate-witness lineage. Missing, mismatched, foreign or incomplete
   correction evidence must refuse compilation. The product evidence lists the
   corresponding refusal/success tests.
2. **Soil admission and readers:** static soil needs a distinct admitted
   descriptor and ownership-fenced pointer operation, followed by the reader
   owner's plane/HTTP/agent adapter. For soil-survey, replace
   `lane_registry.py::_soil_survey_polygon_key_batches`, `_soil_survey_watermark`
   and `_fill_soil_survey` with the proven preservation/source reader. Keep
   static-vintage semantics and ordinary all-rung finalization; no synthetic
   daily gap or PostgreSQL fallback.
3. **Older MTBS composition:** add a separately validated older scope beside
   `warehouse/mtbs_snapshots.py`'s current descriptor and compose eligible scopes
   in `parquet_ops/mtbs_snapshot_catalog.py::load_latest_mtbs_snapshot` and its
   reader contract. Replacement is within a disjoint year component; empty
   replacement must suppress that component rather than fall back. The generic
   owner must define how multiple components share a lane/day/rung and its
   availability row without overwriting one another. A different first
   admission day does not solve future day collisions.
4. **Archive ownership:** retain source/product identity and existing durable
   job lease/checkpoint semantics while replacing the path from
   `ingest/commands.py::run_archive_definition_slice` through
   `ArchiveWalkContext` and `run_source_backfill`. Inspect and fence the enabled
   old definitions/backlog before cancel/requeue or migration; preserve the
   distinction between fire full-day replacement and water-gauge incoming
   daily-value candidates that may overlap existing readings. Do not lower the
   existing forward cutoffs as a shortcut to historical ownership.
5. **Reader/production acceptance:** verify exact and adjacent selected days,
   every required rung, spatial and temporal neighbours with actual distances,
   explicit partial/refusal/truncation outcomes, and cold/warm browser behavior
   on the eventual deployed commit. The current read-only agent baseline in the
   [runtime checkpoint](runtime-and-retirement-readback-20260911.md) reproduces
   pre-repair failures. It is not a receipt for this branch.

## Retirement verdict and authorization

The [current inventory](retirement-reinventory-20260911.json) evaluates all 27
non-KEEP historical candidates using the expanded operator-script reader scan.
Every packet is blocked. Four lexical zero-reader results still lack required
parity/preservation evidence. Shared `geo.features` remains necessary for
community interventions; its whole table is not a retirement target. Current
catalogue estimates, a small relation, or an old inventory class cannot replace
exact population/value proof and a restorable archive.

The fresh runtime readback establishes a dated effective 20-lane allowlist and
an empty operational lease census. Enabled definitions and old backlog remain;
manual or standalone writers are not covered by that census. Every mutation
requires a fresh exact target/owner packet. Signal's physical correction cannot
atomically fence a request already in flight in the object store, so external
writer quiescence remains a real precondition. A failed partial correction stays
journaled for exact resume or separately reviewed restoration; no automatic
rollback to PostgreSQL ingestion is provided.

The track metadata explicitly requires owner confirmation for Railway edits,
`--apply`, production `pg_dump` archival and migrations. None was executed here.
The concrete packets deliberately retain missing implementation, population,
ownership or acceptance fields; they are not ready-to-fire approval requests
while those fields remain open.

The attempted Codex integration-task message was rejected by automatic approval
review because destination authorization was not established. No task message
was sent. This repository handoff is the authorized local record for branch
intake and does not bypass that rejection.
