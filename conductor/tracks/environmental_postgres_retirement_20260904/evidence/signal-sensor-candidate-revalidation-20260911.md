---
type: track-evidence
status: preparation-only
updated_on: 2026-09-11
---

# Signal and sensor candidate revalidation — September 11

This slice revalidated preserved artifacts, authored a bounded signal physical
admission operator and completed fresh read-only production PREPARE. No apply,
object publication/retraction, availability, schedule, database or relation change
was executed. The implementation, behavioral checks and local artifact replay
were accepted in the [final repair review](independent-repair-review-20260911.md),
which records the exact verification scopes. The dated preparation supplies
current object identities without establishing production acceptance.

## Preserved signal population

The preserved archive was found in the earlier worktree at
`C:/Users/atooz/.codex/worktrees/1d40/plantgeo/.omc/research/signal-coordinate-artifacts-20260910.tar.gz`.
It is absent from Git and is not implicitly bundled by this note. The new
`scripts/verify_signal_candidates.py` read it without extraction or external I/O
and verified the outer archive, every content-addressed member, the pinned
manifest, canonical source identities, original completion counts, every original
column in original row order, coordinate witnesses and all current derived rungs.

Future intake should use the current-worktree copy at
`C:/Users/atooz/.codex/worktrees/2849/plantgeo/.omc/research/preserved-repair-inputs-20260911/signal-coordinate-artifacts-20260910.tar.gz`.
The parent's [input custody receipt](preserved-repair-inputs-custody-20260911.json),
SHA-256 `e1ec87cd0e4e0a71c0344d856e29557325553d2795d86e7286bcf9412dd2a890`,
records independent source/destination hashes for 63 files totaling 239,572,564 bytes,
including both pinned signal and sensor archives. The historical source path above
remains the path actually used for the revalidation and running production prepare.
Changing the local path does not change the fixed archive bytes or request identity.

| Fact | Independently measurable identity or result |
| --- | --- |
| Archive | 54,850,520 bytes; SHA-256 `4ca8a36083474d55426d8032275306198af5e30349c22407bc7f13bb6f768124` |
| Members | 1,555 regular members; 65,008,613 expanded bytes; every member hash verified |
| Batch manifest | 5,501,822 bytes; SHA-256 `26fd5219f481bd50d2ba8067b28cf2109e76e72dc43b2941f5905d8fdfea2e57` |
| Dates | Exactly 222 ordered dates, 2025-12-28 through 2026-08-06 |
| Original preservation | 3,506,555 rows; all ten original columns and row order unchanged |
| Candidate rungs | z13/z9/z5 each 3,506,555 rows; z0 67,464 rows |
| Witness | 1,965 spatial cells; dimension SHA-256 `0807a35a8adee038c133fa9e429a70a74220a88c445d6d24488d06996dd84ed1` |
| Canonical source | Manifest `465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f`; completion `7cb92dff8ba61f07c56be08d80fba16b23f71a6eb932b9a54165304d54b45134` |

The [local admission evidence packet](../../../../.omc/research/signal-candidate-admission-20260911.json)
has SHA-256 `4bf2f6f1ef5fa4d406fa46fddb721853c369603d9f55f2075cbe3efe93077cd1`.
It includes all 444 original object identities and 888 saved candidate identities.
Its ownership, current bucket state, publication, availability, serving, schedule
execution and relation-retirement fields remain false. Its initial implementation
gate preceded the later operator authored below; it is not an apply request.

## Signal physical admission preparation

`scripts/correct_signal_coordinates.py` now supports read-only production
preparation and an explicit apply surface for the strictly unbootstrapped branch.
`pipeline/parquet/signal_coordinate_correction.py` owns its product-specific
guards. The operator reconstructs the reviewed archive and deterministic ordinary
writer outputs, including a base completion with actual per-part digests.

The ordinary deriver currently splits at 10,000 rows. Its physical output must
therefore not be equated with the saved one-file-per-rung preview. A first local
reconstruction correctly refused the too-small initial object bound before any
request or production mutation existed. The implementation now verifies every
contiguous ordinary part, decoded row count and completion digest. Its 40-object
day ceiling follows the 100,000-row source cap: at most one base part, ten parts
in each derived rung, and four completion markers require at most 35 objects.
Only one day's Arrow data is decoded at a time; retained candidates stay compressed.
Current bucket scope reads reject keys outside the pinned original/replacement set
before GET, use exact per-key byte ceilings, and cap the aggregate day at 16 MiB.
Ordinary writer readback also refuses unknown keys and uses pinned byte ceilings.

The corrected full local reproduction produced **2,212 physical objects and
41,395,777 bytes** for all 222 days, including 888 completion markers and 1,324
parts. Every rung was locally read back; all base completions carry real part
digests. The [offline reconstruction receipt](../../../../.omc/research/signal-coordinate-offline-reconstruction-20260911.json)
binds the [543,456-byte offline request](../../../../.omc/research/signal-coordinate-offline-request-20260911.json),
SHA-256 `7b9949661c1529650b90e2ea93bc216c27a1312ef6b4ce79af9c463512f5416e`.
The nonsecret bucket `plantgeo-parquet-9ymvp7gv` and empty prefix came from the
parent's deployed-settings read; this offline reproduction did not revalidate
current objects or ownership and does not replace production preparation.

Preparation reads all four day-prefix inventories, original bytes/ETags/versions,
target retry/quarantine keys and both availability keys twice, then writes only
one new local request. Apply reconstructs that exact request, requires a separately
pinned current `signal-correction-quiescence/v1` attestation, and holds the ordinary
publication barrier and each processed lane-day lock on a pinned operational
connection. The connection issues no environmental-observation query. Each apply
is bounded to eight days by default, at most 32.

Before physical changes, apply archives the exact request, original source archive,
quiescence proof and prepared replacement objects under
`layer=signal/kind=observed/availability/repairs/<request-sha>/`, with readback.
An ETag-CAS journal records `archived`, `mutating` and `verified`. The ordinary
writer can overwrite only prepared bytes; it can delete only known completion
markers. Unknown objects, unexpected original identities, foreign retries,
quiescence expiry, changed lock connection or either availability key appearing
cause refusal. Resume requires the same request and freshly evidenced quiescence;
previously verified days are physically reread. Final physical completion still
sets availability publication and selected-day serving verification false.

Independent source review identified a lock-loss gap in the first operator draft:
valid SQLAlchemy wrapper flags did not establish live advisory-lock ownership
between physical writes. The revised operator runs the synchronous writer on a
worker thread and marshals every mutation check onto the owning async loop. It
checks session/connection/driver identity before and after a real `pg_backend_pid()`
round trip, with a ten-second query timeout; rereads head/bootstrap and the target
retry/quarantine keys; then checks live ownership again. The thread bridge waits
at most sixty seconds and cancels a timed-out check. Cancellation or error clears
an explicit active flag before the lock contexts unwind, preventing a lingering
worker from receiving permission for further mutations. A physical object request
already in flight remains outside the database lock's fencing ability. The final
synthetic regressions cover this review fix; no production lock-loss exercise ran.

## Current ownership and object observations

### Completed fresh production preparation

After independent acceptance of the ownership, cancellation and bounded-read fixes,
the local operator completed fresh read-only production preparation from
**2026-09-11 19:05:31.683232 UTC through 19:29:03.640976 UTC** (1,411.953 seconds).
The run used the exact preserved signal archive and final local source after the
style/typing cleanup. A fresh Railway deployment read at 19:05:21 UTC identified
executor `95304e24-16ed-4705-a615-c60b0e368a08`, status `SUCCESS`, revision
`fa202230958fb55521963e886eb031be5fc266c4`. Local source hashes are in the receipt;
the deployed revision observation does not claim that this new operator is deployed.

| Fresh preparation evidence | Recorded result |
| --- | --- |
| Current dates | All 222 days, 2025-12-28 through 2026-08-06, each passed twice |
| Original identity readback | 444 exact original object pins; 1,776 positive GETs for physical-byte comparisons and subsequent ETag/version rechecks |
| Current rung inventories | 1,776 LIST calls; 888 listed objects across both passes; every day retained its exact original z13 pair and empty coarse scopes |
| Proposed replacement objects | 2,212 object pins, 41,395,777 bytes, all rungs z0/z5/z9/z13; these are prepared replacement identities, not current published data |
| Availability preflight | `_LATEST.json` and `_BOOTSTRAPPED.json` each returned absence three times |
| Retry preflight | All 444 target retry/quarantine keys returned absence twice each |
| Request budget used | 4,446 API calls: 2,670 GET and 1,776 LIST; 72,830,360 response-body bytes |
| Mutation boundary | Zero database requests, zero remote mutations, no apply, no availability publication; writer/retry-worker quiescence remains false |

The [fresh production request](../../../../.omc/research/signal-coordinate-production-request-20260911-v3.json)
is 543,456 bytes with SHA-256
`6d583c672cfb8312e24a75c04cb8bde03aa7d4e2b9852694bfe2daf9cc3fc6ad`.
It binds all original and proposed replacement pins with
`publication_scope=physical_only_unbootstrapped` and `availability_publication=false`.
The [sanitized observation receipt](../../../../.omc/research/signal-coordinate-production-preparation-20260911-v3.json)
is 47,880 bytes with SHA-256
`79265e40bd5386c6531f1fd23cadb7411315d7e742a3716dda8b17441f6c699d`.
Its `verification_passes` map contains exactly 222 dates, each with value two.
The [local artifact summary](../../../../.omc/research/signal-coordinate-production-summary-20260911-v3.json)
recomputes the request and receipt hashes and reports the exact scope/counts.

Railway variable output stayed in the local launcher's process memory; only the
needed object-store variables were inherited by the child. The S3 adapter admitted
only explicitly scoped GET/LIST methods, with 6,000-call, 4,000-listed-object and
512 MiB response-body budgets. The child had a 30-minute hard deadline and
per-request connection/read timeouts; raw credential-bearing CLI output was neither
printed nor written. No code or artifact was transferred to production disk.

The [initial attempt receipt](../../../../.omc/research/signal-coordinate-production-preparation-20260911.json)
records a local runner import failure before any S3 access. That import was corrected
and the related test import was fixed before the independent integrated sweep.
The [stopped second attempt](../../../../.omc/research/signal-coordinate-production-stopped-20260911-v2.json)
retains its last checkpoint: 2,600 calls, 520 listed objects, 42,579,001 response bytes,
all 222 first-pass dates and 37 second-pass dates. Its original 15-minute subprocess
deadline could not be safely extended. The parent authorized one restart with a
30-minute hard cap; only the positively identified local child was stopped, and no
request was emitted by that partial attempt. The successful third attempt above
repeated the complete preparation from the start.

This observation window provides a concrete current-state packet for review. It
does not establish external-writer quiescence, acquire locks, authorize any future
write, perform physical admission or solve the separate bootstrap provenance handoff.
Every apply still requires current quiescence evidence, exact owner authorization
and the operator's locked revalidation. The historical offline request remains
separate evidence of local reconstruction and is not substituted for this fresh result.

### Earlier bounded runtime observations

The parent task's [runtime read](../../../../.omc/research/retirement-runtime-readonly-20260911.json)
at 18:02:11 UTC binds executor deployment
`95304e24-16ed-4705-a615-c60b0e368a08` and source revision
`fa202230958fb55521963e886eb031be5fc266c4`.
`sensors-direct-forward` and `parquet-signal` remain active/enabled.
Generic `parquet-sensors` remains enabled in the ledger but is not in the active
configuration. This is a configuration observation, not proof of execution.

The [follow-up object/ownership receipt](../../../../.omc/research/retirement-ownership-objects-20260911.json)
was captured at 18:07:55 UTC and completed at 18:08:02 UTC. Both signal
`availability/_LATEST.json` and `availability/bootstrap/_BOOTSTRAPPED.json`
returned `NoSuchKey`. Its sampled first/last legacy days still have their two
z13 objects and empty coarse prefixes. The complete separate active-lease query
returned no rows. These bounded facts do not prove full 222-day current object
identity, process-level drain, all manual writer inactivity or retry-worker
quiescence. They do not authorize a pause or apply.

## Exact bootstrap handoff to the generic owner

The physical operator intentionally leaves signal unbootstrapped. Availability
must not be published over partial progress or by removing/resetting the head.
`scripts/compile_availability_bootstrap.py::_bind_day` currently constructs
`source_receipts` only from the physical completion markers (the comprehension
around lines 811–812 at `fa20223`). `_finish_compilation` wraps those in
`SourceEvidence.object_receipts`. Plain compiler output therefore cannot claim
transitive original-column/coordinate-correction provenance, even after it verifies
the repaired parts.

Proposed generic-owner patch, not applied in this lane:

1. Add an explicit, externally pinned compiler input mapping exact lane/day to
   correction source receipts. Restrict it to the requested lane root and days;
   reject duplicates, missing days, foreign products and inconsistent request IDs.
   For each repaired signal day, bind the exact durable `request.json` and
   `source-archive.tar.gz` receipts. The request already names every original,
   coordinate-source pin and prepared physical object.
2. Before adding a correction day to `_BoundDay`, verify the supplied bytes/digests,
   exact date/rung scope, completed journal/physical receipt and agreement between
   the request's expected objects and that day's bound parts/completions. A missing
   or changed correction input must refuse or exclude the complete day; never
   downgrade it to marker-only provenance silently.
3. Merge those receipts with the marker receipts in `_bind_day` and retain their
   normal canonical ordering in `_finish_compilation`. Existing
   `SourceEvidence.object_receipts` and
   `availability_index.py::_verify_source_evidence_receipt`/`_verify_raw_receipts`
   support the raw evidence binding; preserve their pre-CAS identity revalidation.
   The archive is within the existing 256 MiB raw-evidence cap. Deduplicate its
   repeated read across 222 days and account for actual read/revalidation cost.
4. Add compiler/bootstrap tests for missing/changed archive or request, wrong
   lane/day/rung, unfinished journal, request-to-physical mismatch, mutation before
   pointer CAS, and a successful generation whose source wrapper reaches both
   correction receipts. Retain full required-rung and refusal semantics.

After independent physical verification and that source-receipt handoff, compile
the complete signal history with full part digesting and the actual source
ceiling, then use the supported locked bootstrap path. The 1,338 earlier schema
days still require their own physical ladder evidence. The 25 August 7–31
base-only absences must not be copied to other rungs or hidden by lowering the
ceiling. No generic availability, scheduler, lane-registry or executor file was
edited here.

## Sensor source revalidation and retained conflict

The same older worktree retains `sensors-rescue-20260910.tar.gz` (2,289,729 bytes,
SHA-256 `eb3ca823a0a3319cc42e47c4066893cfd88efd58979bfb1e177949d9eb70537e`)
and candidate manifest
`e99bce200991ea1b57ef4c2e60a6c36598c992c4d7ad27e700962c528cf8dd40`.
The earlier independent raw-to-row reviewer was rerun against these exact pins,
changing only its output destination to this worktree. Reviewer source SHA-256
was `36db50bc638bb38ef6aa0dd075dcb8673a5b739b5ce4028eeaf763252b88370a`.

Future intake uses the current-worktree
`.omc/research/preserved-repair-inputs-20260911/sensors-rescue-20260910.tar.gz`
and the adjacent `sensors-positive-candidates-20260910/` directory. Inspecting all
1,216 tar members established that the rescue archive contains neither candidate
Parquet files nor their manifest. The parent therefore preserved the separate
manifest, both Parquet files, observation audit, response audit and winning-report
provenance: six files totaling 9,401,169 bytes. The [supplemental custody receipt](preserved-sensor-candidate-custody-20260911.json)
has SHA-256 `47f74b97acb4469931a72ca792b4eb6278986e99c17e8d3c0ebe22e0c761ba2c`
and records matching source/destination hashes, including the unchanged manifest
pin above. The earlier 63-file custody receipt remains untouched. Local custody
does not establish durable production archival or candidate admission.

The [new local receipt](../../../../.omc/research/sensors-preserved-revalidation-20260911.json)
has SHA-256 `7fb3331c56163be816311725d1da5fd1837bc15b9bc1a248050b72f657a79acf`.
Every one of 5,935 rows in 1,030 positive station-day blocks matched its captured
winning report, units, QC code, original timestamp/day, identity and pinned roster
coordinates. September 5 has 3,228 rows/516 blocks; September 6 has 2,707 rows/514
blocks. The September 6 all-nodata winner for `TS586` remains excluded. QC values
remain 5,383 `V`, 479 `S` and 73 `C`, with no interpretation or quality upgrade.

There are 524 accepted partial positive pages, 76 rejected capture responses and
516 un-followed next links. `source_complete=false` and upstream population
completeness remain false. Positive rows contradict a total governed absence;
they do not establish complete transport, station coverage or an empty-day claim.

Later September 10 artifacts correct the older tracked summary's statement that
no preparation ran: request
`2e18b2c8ef7c8ed6a781d91b048c6d5080fc73f0a1b12e2e7d431d90c06b0421`
was prepared and independently reproduced on old deployment
`90251911-636a-4836-9a49-6affc6f2fbd7`, with `apply_performed=false`.
The source files are `sensor-preparation-existing-receipt-20260910.json` and
`sensor-preparation-in-place-review-20260910.json` in that older research directory.
Neither proves a current request, current process quiescence or publication.

The parent current-object receipt still records all eight September 5/6 absences.
The held forward run remains `def58693-a0b6-4d97-90f2-3127bfc9b418` in the runtime
read. The existing `correct_sensor_absences.py` operator therefore remains needed;
no sensor source, correction or dedicated test code was removed or changed in this
slice. The 25 earlier coordinate-free sensor days are a distinct outstanding defect.

## Rollback, remaining authorization and acceptance

Signal rollback evidence is the exact preserved original pair for every day plus
the immutable future remote source/request archive and journal. On a failed
physical admission, retain writer pause, archive and journal; restore the same
request after runtime loss, establish current quiescence, and resume known states.
Automatic rollback is unsupported. If restoring original physical bytes is
required, approve a separate exact day/object restoration under the same locks,
after checking current availability. Do not drop a new availability head or use
the obsolete database re-export route to manufacture rollback. Sensor rollback
likewise preserves its source, originals and journal, but must never restore a
false absence as truthful current state.

No new production authorization was inferred from the historical archive-transfer
approval. The completed fresh preparation supplies current object pins for review;
an independently reviewed current writer/retry-worker quiescence packet, exact source/request/rollback review and
explicit owner approval remain required before any apply. The later owner must
independently read physical parts, completion and availability generations at each
required rung, then obtain selected-day, temporal-neighbour and spatial-neighbour
serving evidence at the exact deployed revision. Source candidate checks, an
empty lease query, a successful physical operator and an availability generation
are four separate facts; none alone closes this track or proves a scheduled tick.

The added tests cover member tampering/path/duplicate refusal, altered original
values and coordinates, wrong target/rung scope, ordinary multipart completion,
ownership expiry/loss, unknown objects, interrupted physical admission and exact
resume, changed physical bytes after completion, and refusal when availability
exists. Added review regressions drop the live backend or change its PID after
the first write while keeping wrapper flags unchanged, introduce each of the four
head/bootstrap/retry states mid-write, cancel a blocked writer, and prove unexpected
keys are not fetched and pinned byte budgets are honored. These signal tests passed
in the parent-coordinated Python fallback and received independent source review.
The final repair review preserves the complete run history and separates its
later affected retry from a fresh all-suite pass on the final tree.
