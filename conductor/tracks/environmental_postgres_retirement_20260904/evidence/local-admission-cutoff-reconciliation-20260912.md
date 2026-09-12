---
type: track-evidence
track: environmental_postgres_retirement_20260904
audited_on: 2026-09-12
status: local_evidence_reconciled_runtime_gates_open
source_commit: 6c08907ba392d8dcdff3e96c41fdb27c6a74fe9d
source_tree: 92f3baef253389b737f3e36fd47a96b52c43c312
---

# Local admission and cutoff reconciliation

This receipt reconciles the checked-in September 10–12 contracts without
contacting Railway, object storage, PostgreSQL, `pgt`, a scheduler or a writer.
It records no publication, pointer change, deployment, ingestion cutoff, data
deletion or current production count. Read-only search created task-local `.omc`
coordination files; they were removed before final validation and were not
treated as evidence.

## Prepared evidence is not locally reproducible admission evidence

The September 10 summaries retain useful hashes and measured populations, but
the decisive ignored artifacts they link are absent from this checkout:

| Gate | Checked-in fact | Missing local identity/evidence |
| --- | --- | --- |
| Signal | 222 legacy days, 3,506,555 preserved rows and 888 four-rung candidate files are summarized. The transferred archive is recorded as 54,850,520 bytes, SHA-256 `4ca8a36083474d55426d8032275306198af5e30349c22407bc7f13bb6f768124`. | `signal-coordinate-artifacts-20260910.tar.gz`, its transfer receipt, the schema census and per-day verification JSON are absent. The summary cannot be re-hashed into a publication input or current-state pin. |
| Sensors | 5,935 positive rows for September 5–6 are summarized. The source archive is recorded with SHA-256 `eb3ca823a0a3319cc42e47c4066893cfd88efd58979bfb1e177949d9eb70537e`; candidate hashes are retained per day. | The rescue archive, candidate manifest and independent JSON receipt are absent. The saved capture was incomplete in any case: 516 next links were not followed, 76 responses carried payload errors, and original wire bytes were not retained. |
| Static soil | Twelve local SoilGrids-derived objects were previously re-hashed, and the old manifest is recorded with SHA-256 `a70359386bea7468b10bda0665f3b1728dd9c0c5118c8d94300e4b7933e92c5f`. | `data/raster/soil/` and all three linked local/remote identity receipts are absent. The old manifest did not bind the six PMTiles hashes. Remote evidence covered only first/last 64-KiB ranges and explicitly set `remote_full_hash_verified=false`. |

Consequently none of these three admission gates can advance from this tree.
Signal still needs a content-addressed candidate archive and verification
receipt plus fresh lane/day/rung and availability state under ordinary locks.
Sensors still needs the pinned captured input, current marker/head inventory,
writer and retry-worker quiescence, original-evidence preservation and a
complete resumable correction receipt. Its partial positive capture may support
positive incremental rows; it cannot prove a source-complete day or authorize
replacing an absence merely because a later poll is empty.

Static soil needs either bounded full-object verification against all twelve
saved digests or publication of independently verified local bytes under
immutable versioned keys. The resulting manifest must bind every COG and
PMTiles object, transform, release/license, property/depth/statistic, scale,
units and palette. A `latest` URL, matching lengths, ETags or sampled endpoint
ranges are not immutable upstream or object identity. Static SoilGrids and the
dark soil-survey Parquet/low-zoom restoration remain distinct product gates.

## The effective cutoff remains unproved

The latest checked-in cutoff receipt says only that the reviewed active list
was changed with deployments skipped and labels the result
`configured_pending_deployment`. Repository source can identify registered
definitions, but it cannot prove the allowlist of a running executor, required
lanes, old process termination, fenced leases, attempts or external invocations.
A later code deployment does not by itself supply those facts. The ingestion
cutoff therefore remains open until one current runtime packet binds the deployed
service/revision to definitions, active and required settings, processes,
leases and attempts before and after the pause.

The source-direct writers also still load fixed climate, soil and vegetation
support definitions from `agri.spatial_cell`. Removing that last environmental
dimension dependency requires a bucket-pinned support artifact preserving exact
IDs, coordinates, coverage fractions, ordering and grid validation. No such
source/object identity is checked in or admitted by this receipt.

## Exact next evidence

1. Restore or recapture each named ignored artifact and verify it against the
   checked-in hash before treating the September 10 summary as executable input.
2. Capture current signal and sensor publication identities, all four rungs,
   availability pointers/generations and ownership/quiescence under the reviewed
   operator contract; retain before/after and rollback identities.
3. Produce the twelve-object immutable static-soil manifest and full-object
   verification/write receipts, while preserving unknown upstream retrieval
   identity explicitly.
4. Produce the deployed cutoff packet and the pinned fixed-support artifact.

No historical PostgreSQL re-export, old scheduler replay, marker deletion or
range-only identity shortcut is authorized by these missing-evidence findings.
