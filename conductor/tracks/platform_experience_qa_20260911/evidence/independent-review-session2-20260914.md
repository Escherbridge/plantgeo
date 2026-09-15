---
type: review-receipt
track: platform_experience_qa_20260911
recorded_on: 2026-09-14
reviewer: /root/independent_verifier
review_base: 0f16e40dae3cce1d3b6d4ac00138254a968d974f
source_manifest_sha256: bcd401fc6c0194ad3091e6c243df86d33a403d19ddef3e7521d16d5a2e773b03
bounded_engineering_verdict: approved
platform_qa_verdict: RED
---

# Independent Session 2 review

The bounded workspace consent change is approved on candidate 5 and its engineering checks.
The corrected publication sample and historical recovery receipts accurately distinguish their
different levels of proof. **Overall platform QA remains RED and unaccepted.** No browser case,
full-history certification, operational recovery or deployment is approved by this receipt.

This is a separate reviewer task. The reviewer made no runtime/test changes, ran no duplicate
tests and performed no remote operations. Review comprised source inspection, independent local
hashing, log inspection, reconstruction of captured historical output and a separate comparison
of recorded publication metadata. This review artifact and the explicitly authorized verifier
disposition row in the task ledger were authored in Session 2.

## Source and check identity

The [candidate 5 manifest](source-candidate-20260914-5.json) contains 1,665 source entries.
Independent hashing found zero file mismatches; its aggregate SHA-256 is
`bcd401fc6c0194ad3091e6c243df86d33a403d19ddef3e7521d16d5a2e773b03`.
Comparison with Session 1 candidate 4 identifies exactly seven changed files: two runtime
components, three component test files and two directory documents. Python runtime and tests
are unchanged. Prior Python receipts retain their original scope and were not rerun here.

The six log hashes in the [Session 2 check receipt](check-receipt-session2-20260914.json)
matched the captured files at review. The frontend selector chose the full suite: 190 files and
2,502 tests passed. Type checking and data-boundary checks exited zero. ESLint exited zero with
zero errors and 9,887 warnings; this is not a warning-free result. The documentation-link check
retains a nonzero exit for three unrecovered original references. The final scan includes this
artifact and the task-ledger link: 182 local links checked, with the same three missing originals.
The reviewer rehashed all six logs after that final receipt refresh and found zero mismatches.

The candidate remains an uncommitted local tree. Railway's recorded deployed revision is the
base commit `0f16e40dae3cce1d3b6d4ac00138254a968d974f`, not these local changes.

## Consent and lifecycle review

`WorkspaceAnalysisEntry` dispatches only from the explicit shared Send action. Mounting the
workspace, revealing its AI pane, changing precision and Cancel do not dispatch. Approximate
precision defaults to two decimals; exact precision is opt-in and uses six. The same rounded
preserved-draft coordinates and precision feed both the selected analysis location and the
request. A changed proposal location remounts consent with the approximate default. Header
coordinates reflect the currently selected pane's context and transmitted precision.

Embedded `AgentInteraction` delegates Escape and focus handling to the workspace and does not
create a competing floating dialog. Its instance-specific radio-group and description IDs avoid
interference with a simultaneous standalone map popup. Cancel returns to the proposal; the
existing guarded workspace close retains responsibility for discarding drafts and aborting
analysis. The drawing map and form remain mounted across pane changes.

Source inspection confirms `useRegionalIntelligence` has no unmount cleanup that aborts the
request. Its controller is stored outside the consent entry, so replacing the entry with the
analysis panel does not cancel the stream. Close still aborts through that shared store. The new
component request tests mock `queryLocation`; this source finding is not a new actual-hook or
browser streaming regression result. That runtime acceptance remains open.

No material source correctness or security defect was found in the Session 2 authored changes.

## Publication evidence correction required by review

The first reconciliation script treated loops over absent part receipts as successful digest
comparisons. Independent review rejected its claim of 40 equivalent digest/count matches.
Inspection found 13 schema-1 completion markers without per-part metadata: the eight soil
rung-13 markers, weather rung 13, and all four shortwave markers. The shortwave availability
rows additionally contain empty `data_receipts` lists, leaving their measured part hashes
unbound by either publication document.

The corrected [object receipt](environmental-objects-session2-20260914.json), SHA-256
`966f2d29559b3d40cb2bcfcaf3edb08c95a82a359ba92ff0bca2f318135a6ea0`, reports:

| Proof category | Rung samples | Reviewed meaning |
| --- | ---: | --- |
| Full part metadata matches | 27 | Soil/weather rungs 0/5/9: completion part lists, sizes, hashes and rows agree with captured parts and availability receipts. |
| Availability digest and count matches | 9 | Soil/weather rung 13: availability binds part digests; schema-1 completion markers corroborate counts but contain no per-part size/hash declarations. |
| Counts only; part digests unbound | 4 | Shortwave May 31 at every rung: counts agree, but neither marker nor availability declares part digests. Measured hashes/sizes alone do not establish publication binding. |
| Missing objects and availability rows | 8 | Weather September 6 and shortwave June 1 at four rungs each. These are missing publications, not demonstrated governed absences. |

The reviewer separately checked recorded part-set equality, part counts, declared digests,
marker/availability row counts and available size declarations. After distinguishing absent
declarations from comparisons, no compared value mismatched. The ten strict generation reads
and 2,243,437 downloaded object bytes remain bounded operational observations, not whole-history
certification. The raw capture hash matches the receipt; the read script uses bounded read-only
operations and strict availability-generation validation. The reviewer did not repeat remote
downloads or independently remeasure those object bytes. Upstream source/terminal receipt bodies,
fresh source ceilings, map painting, conservation and agent/temporal parity remain outside this
sample's proof.

The [runtime receipt](runtime-session2-20260914.json) now binds raw capture paths/hashes and
read-only Railway operations; both raw hashes matched at review. Scheduler success reports do
not establish physical publication. The recorded held sensors run and unresolved source/history
questions remain actionable gaps, not actions performed by this session.

## Historical recovery verification

The two recovered Markdown files were independently reconstructed from the cited successful
events using the recorded strict encoding conversion and removal of the capture's final CRLF.
The output exactly matches the restored files: audit 10,741 bytes/138 lines; resolution plan
8,003 bytes/119 lines. The event-log hash also matches. This establishes captured-output
reconstruction, not an original file digest that was never recorded.

For the additional [artifact recovery](../../environmental_parquet_serving_20260912/evidence/freshness-audit-20260913/artifact-recovery-20260914.md),
the reviewer checked the source-event log hash and matched the captured original manifest object
to the restored manifest. Restored activation, deployments and executor-tick JSON files each
match their original manifest SHA-256. The manifest's own original encoding identity is not
claimed. Original coverage JSON, coverage CSV and the launch-note document remain missing;
partial captures were not accepted as complete replacements.

## Remaining acceptance boundary

All 219 journey cases remain unpassed across the 31 registry layers and four land-context
groups. Browser/mobile/accessibility/streaming evidence, isolated role/social flows, database
and migration verification, source admission, botanical bbox policy, land-context readers and
geometry, operational recovery, sustained advances and production acceptance remain open.
The [Session 2 ledger](runbook-session2-20260914.md) preserves those gates. Further source edits
require new candidate-impact reconciliation; these bounded approvals cannot close the runbook.
