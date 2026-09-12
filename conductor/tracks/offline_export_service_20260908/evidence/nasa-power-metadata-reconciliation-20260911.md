---
type: evidence
track: offline_export_service_20260908
status: complete
created: 2026-09-11
---

# NASA POWER metadata reconciliation — September 11, 2026

## Audit boundary and ownership

This read-only reconciliation began from repository HEAD
`8c14ea0117ba14d5584f737b793f989566102514` and tree
`de840fb558a110e0aded8f2baf44a39b9717f0d7`; the working tree was clean before
these documentation edits. No Railway, production, PostgreSQL, scheduler, writer,
deployment or object-store operation was inspected or changed.

The metadata's retained `source_head: fa20223` remains the dated operational
checkpoint; the repository HEAD and tree above identify only this local reconciliation
input and are recorded separately in metadata. They are not a fresh production audit.

The offline track remains `active` and its metadata owner remains
`atoozmc@gmail.com`. It owns reconciliation of the selected in-repository builder
against its original phase ledger, the measured end-to-end performance comparison,
and deferred relative-humidity history. The environmental retirement track also
remains `active`; its preserved
[runtime repair record](../../environmental_postgres_retirement_20260904/evidence/parquet-runtime-repair-20260910.md#final-temperature-publication-verified)
and [exact generation receipts](../../environmental_postgres_retirement_20260904/evidence/temperature-bootstrap-receipts-20260910.json)
own the temperature publication facts used here. Gapless publication and production
acceptance retain forward advancement and broad runtime acceptance respectively.

## Prepared and published are different states

The runtime record contains an earlier prepared/pending checkpoint: exact inputs had
passed dry validation, evidence uploads existed, and locked publication attempts had
started. Those artifacts did not establish a published head, verified generation
readback, deployed serving result or completed availability release. Its later
“Final temperature publication verified” section explicitly supersedes that pending
checkpoint for the bounded historical slice.

The following facts are published and receipt-backed:

| Lane | Complete days | Window | Rows and rungs | Generation SHA-256 |
| --- | ---: | --- | --- | --- |
| `climate-field-air-temperature-mean` | 1,560 | 2022-04-30..2026-08-06 | 6,240; 1,560 each at z0/z5/z9/z13 | `527989e565b95d6369a59fa8e0b6404b58969fddc1f919d7e6a01f90c538f3b2` |
| `climate-field-air-temperature-min` | 1,560 | 2022-04-30..2026-08-06 | 6,240; 1,560 each at z0/z5/z9/z13 | `a607e073e88702bcdd4a7a0278ff3aabf147ef0d281acd2b283ee554823c1377` |
| `climate-field-air-temperature-max` | 1,560 | 2022-04-30..2026-08-06 | 6,240; 1,560 each at z0/z5/z9/z13 | `962763ebdc1325cf32949e43c8c581f77869f6a926ae2d32d012c1810a80757d` |

Each generation is fully digested and was independently read through the normal
availability reader. The common source ceiling is 2026-09-05, but that ceiling does
not fill or publish the interval after 2026-08-06.

## Dead-letter correction

The offline metadata previously presented
`climate-nasa-power-direct-forward` at attempt 6/6 since September 7 as a current
`blocked_by` value. That observation remains useful incident history, but it is no
longer a blocker for the published temperature-history slice above. The metadata now
records it as `historical_resolved_for_published_temperature_history` and scopes the
resolution to that exact window.

This documentation-only audit did not read a current operational ledger or observe a
new scheduled run. It therefore makes no claim that the forward lane is healthy,
deployed or advancing.

## Other evidence availability and the ERA5 count correction

The decisive ignored `.omc/research` artifacts linked by the environmental track for
signal coordinates, sensor candidates and static-soil admission are absent from this
worktree. In particular, the linked signal coordinate archive and verification JSON,
sensor candidate manifest and independent receipts, and static-soil hash/metadata/live
acceptance receipts cannot be re-hashed here. Their checked-in summaries remain
preparation evidence only and cannot justify admission or publication from this audit.

The offline metadata also carried a stale claim that `soil-field-vpd` already held 462
correct live days. Dated checked-in evidence does not support that exact number:
[September 7 evidence](../../environmental_postgres_retirement_20260904/evidence/snapshot-products-vs-availability-20260907.md)
records 448 sparse complete days; the selected builder at `fb72d07` pins 446 contiguous
resume days; and the [September 9 cutover archive](../../../RUNBOOK-archive-2026-09.md#session-handoff-2026-09-09--parquet-cutover-16---21-layers-and-a-live-latency-regression)
records 1,556 built days plus a 1,572-day serving span. The metadata and plan now
require an exact retained receipt before any resume action instead of presenting one
of those dated populations as current.

## Remaining gates

- Reconcile every original phase verdict against the chosen in-repository builders
  and independent reviews; do not mark the broad phase-review task complete from this
  metadata correction.
- Bind the complete eight-lane history/rung manifest without rebuilding the verified
  temperature window.
- Record measured staging, build, upload and verification cost against the September
  7 baseline.
- Publish and receipt the deferred relative-humidity 1981–2017 availability history.
- Obtain fresh forward-health, warm reader/capability, deployment/burn-in and broad
  production-acceptance evidence through their owning tracks.

This evidence reconciles labels only. It is not a repair receipt, deployment receipt,
forward burn-in result or parent-track closure.
