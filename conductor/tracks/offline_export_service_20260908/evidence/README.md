---
type: evidence
track: offline_export_service_20260908
status: ready_for_authorized_publication
date: 2026-09-11
---

# Offline export selected-builder evidence

## Outcome

The rejected standalone export service remains rejected. The selected in-repository builders are the
authority for the eight completed day-grain histories:

- `build_era5_land_from_canonical_snapshot.py` owns VPD and the four soil-temperature products.
- `build_nasa_power_from_canonical_snapshot.py` owns the three NASA POWER air-temperature products.

The read-only production audit has a zero-finding `verified` verdict. All eight histories have one
part and one completion marker at each required rung for every declared historical day, with zero
owed history days. Forward days are reported separately and were not rebuilt. The exact manifest is
[`selected-builder-manifest-20260911.json`](selected-builder-manifest-20260911.json).

Relative humidity's deferred 1981-01-01 through 2017-12-31 availability history is now represented
by a fully digested, parser-validated local packet. It is deliberately not uploaded or applied. The
exact handoff is
[`relative-humidity-history-candidate-20260911.json`](relative-humidity-history-candidate-20260911.json).

## Measurements

The current eight-lane GET/LIST-only audit completed in 110.438361 seconds using 24 list operations.
It examined 173,654 listed objects and 5,893,948,572 aggregate listed bytes; the tracked
[`selected-builder-audit-receipt-20260911.json`](selected-builder-audit-receipt-20260911.json)
is 132,594 bytes with SHA-256
`952aeba68821f80e92686e814b2e4aad49150bcde33b2d8914629096d83ec5b7`. All 32 lane/rung
object-count and byte-count measurements are nonzero.
This is a current verification measurement, not a reconstruction of the September 9 build or upload
rate. Historical stage/build/upload wall time, peak memory and local disk telemetry were not recorded
by the selected builders and remain explicitly unavailable; no estimate is substituted.

The relative-humidity bounded-packet resume verified 67,570 content-addressed local evidence files
(81,624,560 bytes), split and parser-validated 54,056 rows into three inputs (68,599,200 bytes total),
and completed in 511.519640 seconds with 469,711,845 bytes peak traced Python memory. The complete
v3 local packet contains 67,578 files and 150,236,825 bytes; its 4,567-byte receipt has SHA-256
`05be63c4fb3d2afa91f3ca83144fc36cba3cc86d5d04f5e0c866408b0f8de6f3`. The receipt binds immutable
bootstrap SHA-256 `9c9334b62424cf42cab33654f76bedea63cc04ea6dfe2246106f4f3dcbba8ffd`
and verified source-inventory root
`6d1bb64dae30af098d425f840b25709352eef4a31510df76646de1f9a2723e3e`. Its sibling
67,578-entry SHA-256 custody manifest is 15,379,894 bytes with digest
`d644501b608051b230d5a94ea8b22f339624c6d43496aa14837f65211b6b3f70`. The original physical digest pass completed
but its single 68,597,776-byte request exceeded the 64 MiB loader cap before the attempt wrote a
receipt, so its exact wall time and retry count are unavailable.

The final tracked
[`warm-capability-probe-20260911.json`](warm-capability-probe-20260911.json) completed in 634 ms
with HTTP 200. It reported 22 serving capabilities, one unrelated withheld `soil-survey` capability,
and available coverage. Relative humidity still
begins at 2018-01-01, ends at 2026-09-06, reports 3,106 observed days, uses availability authority,
and requires rungs 0/5/9/13. The local packet therefore remains a deferred-history append rather than
a rebuild or correction of current history.

## Reconciliation of the original phases

- Phase 0: **pass**. The exact eight-lane source/history/rung manifest is bound to a checksumed audit.
- Phase 1: **superseded**. The chosen builders stream immutable, manifest-pinned source objects and
  use create-only destination writes; the discarded standalone local staging service is not rebuilt.
- Phase 2: **pass**. The two selected parameterized builders emit day-grain observed ladders with
  real `PartitionCompletion` markers and never write physical data to a live prefix.
- Phase 3: **pass with a disclosed historical telemetry gap**. Current live/frozen/source verification
  is measured and zero-finding; the original stage/build/upload timing was not preserved.
- Phase 4: **pass for local construction**. The exact relative-humidity history is fully digested and
  split into three documents below 64 MiB, each accepted by `load_publication_request`. Upload/apply
  remains unperformed and unauthorized.
- Phase 5: **pass for the offline lane**. Warm serving is healthy and the immutable publication/rollback
  handoff is complete. Runtime forward-gap closure and post-apply production acceptance remain with
  their named owners.

## Mutation boundary and rollback handoff

No upload, availability publication, pointer advance, deployment, overwrite, deletion, database
write, or production data mutation was performed. The local packet records the compatible publication
base pointer and generation, but the applying owner must capture the immediate current generation
before each serialized apply; that immediate prior generation is the authoritative rollback target.
All evidence uploads must be create-only and byte-identical on replay. The three inputs must first be
revalidated offline using their exact hashes and row counts, then applied only after exact owner
authorization and in filename order.

## Review and validation

The implementation review and final affected Python validation are recorded in
[`review-20260911.md`](review-20260911.md). This track does not own the combined repository
`QUALITY_RECEIPT.json`; integration owns that final cross-lane receipt.
