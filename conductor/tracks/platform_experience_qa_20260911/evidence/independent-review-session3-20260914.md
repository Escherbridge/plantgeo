---
type: review-receipt
track: platform_experience_qa_20260911
recorded_on: 2026-09-14
reviewer: /root/independent_verifier
review_base: 0f16e40dae3cce1d3b6d4ac00138254a968d974f
source_manifest_sha256: 042fd11caab175b2ab321688f96500543d7e9483f2ccccdd454d2dc5ddfd26e6
bounded_engineering_verdict: approved
platform_qa_verdict: RED
---

# Independent Session 3 review

The bounded workspace drawing-recovery change is approved on candidate 6 and its recorded
engineering checks. The physical and HTTP receipts accurately separate publication metadata,
repeatable responses and upstream truth. **Overall platform QA remains RED and unaccepted.**
This review does not accept the disputed sensor absence, incomplete SSURGO publication, social
disclosure defect, browser journeys, full-history coverage or production recovery.

The reviewer inspected source and retained evidence, independently hashed local artifacts and
separately derived publication categories. No runtime or test file was edited, no tests were
duplicated and no remote operation was performed by this lane. Authored changes are this receipt
and the explicitly authorized verifier disposition row in the task ledger. At the coordinator's
request, the reviewer also refreshed the final local documentation scan and check-packet log hash.

## Candidate and checks

The [candidate 6 manifest](source-candidate-20260914-6.json) binds 1,666 source files with aggregate
SHA-256 `042fd11caab175b2ab321688f96500543d7e9483f2ccccdd454d2dc5ddfd26e6`.
Independent hashing found zero file mismatches. Compared with candidate 5, exactly seven files
changed: three runtime components, two component tests including the new recovery test, and two
directory documents. Python and backend source/tests are unchanged; their earlier receipts are
not promoted to new executions.

All nine log hashes in the [Session 3 check packet](check-receipt-session3-20260914.json) matched
at review. The frontend selector chose the full suite: 191 files and 2,509 tests passed. Type
checking and the data-boundary gate exited zero. ESLint exited zero with zero errors and 9,887
warnings. This is not warning-free validation. The final documentation scan checked 212 local
links including this receipt and retained the same three missing historical references: `coverage.json`,
`coverage.csv` and the environmental track's `fanout-launch-20260913.md`. Its nonzero exit remains
explicit. All nine log hashes were independently rechecked after the final packet refresh, with
zero mismatches.

Candidate 6 remains an uncommitted local tree. These checks do not establish deployment identity,
browser behavior or release acceptance.

## Drawing lifecycle and the fixed review finding

Saved Point/Polygon geometry is restored when a new drawing session attaches, with the map
centered on the preserved proposal location. Initial restoration happens before the change
listener is installed, so importing a feature or clearing an unsuccessful import cannot silently
erase the saved geometry. Invalid or unsupported restoration retains the stored geometry, sets a
visible error and disables submission; explicit Clear removes the retained geometry and error.
Existing form fields survive navigation unmount, while confirmed workspace discard clears them.
Pane switches preserve the same drawing instance. This is in-memory navigation recovery, not
page-reload persistence or reconstruction of a destroyed drawing engine's undo history.

Independent review found a readiness race in the first batch: it subscribed only to MapLibre's
`load` event when `isStyleLoaded()` was false. Installed MapLibre source shows that `load` fires
once per map, while style readiness can become false again during later source/tile work. A
dynamically mounted drawing control in that interval could wait forever. The corrected code
checks readiness immediately and on recurring `render` plus `load`, guards duplicate attachment,
and removes both listeners on attachment and cleanup. The new regression exercises an
already-loaded map becoming ready through render events without a second load event.

The integrated recovery regression uses real workspace/form/control components with fake map and
drawing engines. It verifies restored geometry, retained coordinates/fields, keepalive, explicit
discard and visible failed restoration. It is useful component evidence, not real-canvas,
pointer-interaction or browser rendering acceptance. No remaining blocking defect was found in
the bounded authored source batch after the readiness correction.

## Independent publication reconciliation

The [physical receipt](physical-samples-session3-20260914.json), SHA-256
`6cc9230baeb6b8d896a9eea93b43728858c222f29733504cbcfb7ccb259ab2e7`, binds the three raw captures;
all their local hashes matched. Independent comparison of part sets, declared hashes/sizes,
Parquet row counts and marker/index counts reproduced all 100 rung classifications:

| Evidence category | Rung samples | Limit |
| --- | ---: | --- |
| Full completion part metadata matches | 54 | Captured parts match declared keys, sizes, digests and counts. |
| Availability digest and count matches | 15 | Legacy completion markers omit per-part metadata; availability binds digests. |
| Counts only, part digests unbound | 5 | Measured hashes lack publication-side digest declarations. |
| Budget-limited published-part reads | 2 | Burn severity z13 has one unread part; watersheds z13 has eight. |
| Indexed/marker absence, source unproven | 20 | Five dates across four rungs have checksum-bound absence statements, without upstream-empty proof. |
| No physical objects in sampled rung | 3 | Soil survey/SSURGO z0/5/9 lack objects for the sampled version. |
| Raw parts without completion | 1 | SSURGO z13 has 481 parts and no completion marker; 433 parts were not downloaded. |

The no-marker branch initially omitted its unread-part field, hiding the soil-survey budget
limitation in the summary. Review requested that limitation be exposed; the final receipt records
it before classification. There were no compared metadata mismatches. There are no derived-empty
completion samples in this batch, and no missing declaration is treated as a successful digest
comparison.

Sixteen time-axis generations were verified. The four static lanes correctly use a separate
object census rather than requiring time-axis availability pointers. Their discovery captures
list 1,120 fire-perimeter keys, 27 watershed keys, 98 evacuation-zone keys and 959 soil-survey keys;
all report `possibly_truncated: false` below the 2,000-key limit. Those censuses and selected-day
samples do not certify the full history or every object against upstream source data.

All 105 captured source/terminal evidence objects have matching declared digests. The reviewer
also compared terminal documents' day, rung, state, row count, completion/data references and
source references with their index rows, plus source day/lane identity: zero mismatches. This
verifies agreement among captured statements. The reviewer did not repeat remote downloads or
independently parse the original downloaded Parquet bytes; part measurements remain the bounded
read script's captured observations.

The twenty absence samples are drought 2026-02-24, fire detections 2025-02-16, sensors 2026-09-06,
vegetation 2026-08-27 and burn severity 2026-08-31. Their base-rung documents cite old database
exports or `geo.features` queries. Several explicitly say the upstream system was not contacted.
The source-evidence wrappers bind those same marker statements; their checksums do not turn
zero exported rows into proof that an upstream source was empty.

## HTTP and sensor evidence

The [HTTP receipt](api-samples-session3-20260914.json), SHA-256
`7f9652ac696bc965855010df1b677c87f46a4fdc9e36db082d5f2076684832e2`, binds 74 retained response
bodies. All body hashes matched independently. The 37 cases each have two identical response
hashes at zoom 5 and bbox `[-124,47,-122,49]`, with states 58 published, ten governed_absence,
four day_not_written and two lane_never_written. All returned requested-day fields agree, all
responses are HTTP 200, and none reports server truncation. The executed-script copy matches the
raw receipt's script digest. Canonical normalization correctly reads actual served-day fields
instead of inferring them for the six unavailable responses.

This proves consecutive-response agreement at one spatial sample. It does not establish
controlled cold/warm caches, selected-day frontend/agent parity, upstream correctness or full
spatial coverage. In particular, sensor September 6 returns the same disputed governed-absence
claim that storage contains. Published zero-row viewport samples do not prove a globally empty
source. SSURGO's lane_never_written response is consistent with incomplete raw parts and no
completed publication.

The [sensor investigation](sensor-investigation-session3-20260914.md) correctly separates five
attempts in the historical failed run from three unsuccessful scheduler buckets. Retained primary
output records 2,377 incoming September 6 rows and 2,850 September 5 rows rejected by the existing
absent-state guard. That positive incoming evidence contradicts an empty-source interpretation;
it is not custody of the full recoverable source archive. The exact pinned correction artifacts,
current custody and required writer/retry-worker quiescence remain unestablished. The existing
date-pinned operator's local preparation and guarded application remain distinct from scheduler
supersession. No recovery or production mutation is accepted or claimed by this review.

## Social scope and platform disposition

The [voting scope handoff](public-request-voting-scope-session3-20260914.md) agrees with the
resolved owner decision preserving one-way votes separately from likes, the schema's explicit
dormant-writer disposition, and the unresolved count/eligibility/authorization contract. Retained
schema and aggregate reads do not establish a replacement voting flow. Defined public request,
like/comment, display-name and proposal-consent journeys remain required.

The dated case update preserves old wording, aligns R03 with public requests, blocks C03 on the
replacement voting contract and adds A24 navigation recovery. Current inventory is 220 unique
cases: 178 not_run and 42 blocked, with no passing browser cases. The coordinator's separately
recorded D260914-16 identifies stale private-request assurances on `/community`; the bounded
drawing approval does not accept that disclosure defect or excuse its next source batch.

The [session ledger](runbook-session3-20260914.md) accurately keeps source admission, disputed
absences, incomplete SSURGO, missing historical artifacts, browser/canvas fidelity, authenticated
social journeys, all-layer sliders/neighbours/agent parity, migration/deployment identity and
sustained operation open. Physical samples across thirty streams over Sessions 2/3 do not close
the 31 registry layers and four land-context groups. **The full runbook and platform QA remain
RED; no GREEN, release, archive or runbook-completion verdict is issued.**
