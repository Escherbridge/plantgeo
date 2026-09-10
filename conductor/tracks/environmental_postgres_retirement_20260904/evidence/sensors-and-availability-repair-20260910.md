---
type: evidence
---

# Sensors absence conflict and missing availability heads — 2026-09-10

This records repository findings against the September 10 Railway ingestion audit. No production
object changes, absence retractions, bootstrap applications, or scheduler supersessions were made
by this investigation. Live absence contents still require inspection before an operational repair.

## Sensors: the refusal protects a recorded claim

The audit records `DirectSensorsError: refusing to merge poll rows into sensors z13` with
`status=absent` for September 5 and 6. `pipeline/direct/sensors/adapter.py` intentionally accepts
only data, missing, and recoverable incomplete partitions. The existing
`test_refuses_to_merge_over_a_governed_absence` regression covers this refusal.

The writer never creates absence markers: it merges station-day blocks from a bounded rolling
NWS poll. The incoming rows contradict an existing absence, but the error alone does not establish
who wrote that absence, the evidence it cited, or which coarse rungs and availability rows refer
to it. Automatically deleting it on the next poll would discard that distinction. The forward
writer already treats `DirectSensorsError` as nonretryable within a turn, so increasing retries
does not address this conflict.

Before a repair, capture all four rungs' absence markers with keys, bytes/digests, reason,
`upstream_response`, `recorded_at`, and `run_id`; inventory any parts/completion markers alongside
them; and capture the availability head and affected rows. Preserve a fresh validated poll for the
two days while NWS still retains them (the repository's declared retention is six days). A later
empty response after expiry cannot establish that the source originally published nothing.

A deliberate correction must bind its operator/evidence to those exact markers and the replacement
poll, recheck them under the shared lane-day lock, and rebuild the complete four-rung ladder and
availability generation. Preserve the old absence evidence in the repair record. Do not merely
delete z13, remove the availability head, or replay the outer dead letter. The repository currently
has vegetation-specific absence repair tooling, but no equivalent bounded sensors correction verb.
Therefore sensors repair remains operationally unresolved by this patch.

The 25 coordinate-free sensor days before August 24 are a separate historical defect documented
in `sensors-stranded-days-20260906.md`; fixing the September absence conflict does not repair them.
The older document's PostgreSQL re-export recommendation predates the environmental data-plane
retirement and is not authorization to restore that serving or ingestion path.

## Temperature availability: inspect bootstrap before acting

The audit reports missing availability pointers for climate air-temperature mean/min/max; it does
not establish missing physical data. For each affected lane root, read these two exact keys under
the configured object-store prefix:

- `availability/_LATEST.json`
- `availability/bootstrap/_BOOTSTRAPPED.json`

The parent task subsequently checked both keys through executor SSH using the resolved empty
object-store prefix: all six HEADs were missing for the mean/min/max lanes. That evidence places
the current temperature outage in the missing-bootstrap branch. The parent task's subsequent
bounded prefix listings also found all three temperature stream prefixes entirely empty, while
dew-point and sensors control prefixes contained objects. Thus these lanes currently lack both
the physical data ladders and generation zero; publishing availability metadata alone cannot
recover them. The source-direct producer must first create verified days and their complete ladders,
then the offline bootstrap can index them. The lost-head defect below is a separate recovery bug
found during the investigation. The parent task owns that census evidence and production repair.

If both are absent, generation zero has not been established by the deterministic marker contract.
Use `scripts/compile_availability_bootstrap.py --lane <slug> --dry-run` to census the four rungs and
review exclusions. A real compile writes pinned input, digests, evidence, and the exact upload/apply
commands. Review its row counts and exclusions, then use the supported `availability-bootstrap`
path; a successful new day does not bootstrap the index by itself.

If the marker survives but the head is absent, do not bootstrap a second history or invent a head
from the newest object name. Restore only a head whose generation, bootstrap binding, source
ceiling, and evidence have been verified. A malformed marker also cannot prove that bootstrap
never occurred.

`availability_extension.py` previously treated either case as `not_bootstrapped`, deleting the
terminal day's retry claim before returning. The patch checks the bootstrap marker on a missing
head in both the initial extension and retry paths. An existing or malformed marker now leaves
`retry_owed` with the physical-receipt claim intact. A truly unbootstrapped lane retains its existing
offline bootstrap behavior. This prevents new loss of recovery work; it does not recreate claims
that earlier runs already deleted, nor repair a production head by itself.

Regression coverage added in `tests/parquet/test_availability_extension.py` follows a published
day through missing head, retained retry, restored original head, and successful indexing with
unchanged data parts. A second case verifies that a malformed bootstrap marker cannot clear the
claim. Execution is deferred to the parent task's final integrated verification sweep.

## Pinned rescue review

The approved local archive SHA-256 is
`eb3ca823a0a3319cc42e47c4066893cfd88efd58979bfb1e177949d9eb70537e`.
Independent review verified the bytes and SHA-256 of all ten captured publication objects: eight
absence markers plus the availability head and bootstrap marker. The markers explicitly describe
an empty PostgreSQL export without upstream contact, which does not establish a source absence.

The capture declares `source_complete: false`: 600 HTTP-200 receipts, 516 with an unfollowed next-page
URL, 76 with a payload error, and 31,358 feature-count entries before validation/deduplication.
Positive valid measurements remain usable evidence; empty responses cannot establish absence.
The retained payload format is decoded text re-encoded as UTF-8, not untouched wire bytes.

The current CLI has no captured-input sensors correction. Normal forward publication deliberately
refuses an existing absence; availability-publish only indexes already durable corrected ladders.
A correction must validate the pinned payloads, obtain existing publication/lane locks, compare the
old markers and current day inventory, preserve the original evidence, replace all four rungs
resumably, and conditionally publish corrected availability while preserving unrelated days.
No marker deletion, sensor replay, or partial-source completeness claim was performed.
