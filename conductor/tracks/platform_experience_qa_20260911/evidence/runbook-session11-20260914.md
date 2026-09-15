---
type: evidence
status: active
recorded_on: 2026-09-14
---

# Session 11: publication dates and stale review decisions

Candidate17 completes the bounded Session11 engineering correction with **129 passing tests in
three affected files**, full typecheck and data-boundary passes, and lint passing for those three
test files. This is a scoped pass. Candidate15's failed full frontend sweep and Candidate16's
failed scoped sweep remain retained; no full frontend or full lint run is claimed for Candidate17.
The [canonical check packet](check-receipt-session11-20260914.json) binds the exact applications,
source manifests, logs, exit files, source comparisons, reviews and preserved originals.

Candidate17 has1,749 files and manifest SHA-256
`d2e12b9af07b6b9039c623362d0ed19b7a534d599ced4add5446b126d24b9635`.
Its production runtime is unchanged from Candidate15. Browser acceptance is separate: root has
reported a Session15 D26 browser pass, but independent result/frame acceptance is pending at this
packet checkpoint. This Session11 engineering receipt does not accept that browser result or the
separate fresh stale-review journey. The overall runbook remains RED and no whole case is promoted.

## Applied behavior and exact source transitions

The independently reviewed D260914-26 and D260914-27 runtime batch was applied as Candidate15,
manifest `bb85c770879e058043e4e2673dd8a01c04e4f284795f66748983f885dd722547`.
The [Candidate15 application receipt](application-session11-candidate15-20260914.json) records
all1,747 C14 hashes verified,15 existing files changed, two files added and1,732 prior files
unchanged. No commit, deployment or production mutation occurred.

Typed readers distinguish requested selections from actual served dates. Unavailable SSURGO
does not acquire a selected-date drawing claim; static watersheds carry their returned release
date. Retained and paused typed frames preserve pending selections, while same-day viewport
placeholders do not claim an older day. Shared selected-day agent context is unchanged. The
composite streamflow/groundwater water row remains a documented legacy boundary outside this
bounded fix. The retrospective [D26 review record](../../../../.omc/research/runbook-20260914/session11/independent-d26-review.md)
explicitly identifies its pre-application collaboration provenance and the three review findings
resolved before approval.

Canonical publication and rejection bind their final database update to pending status. A stale
decision returns a conflict and preserves the first committed decision. The queue shows the
refusal, refreshes pending rows and retains unsent notes. Query-refresh errors preserve the
mutation explanation and distinguish access failures from generic load failures. The
[independent moderation review](../../../../.omc/research/runbook-20260914/session11/independent-moderation-review.md)
records the atomic predicate and meaningful router/component regressions. The original diagnostic
rejected feature is preserved; the browser regression must use a new normally submitted,
consented synthetic proposal.

## Retained check sequence

| Candidate | Frontend checks | Type / boundary | Lint |
| --- | --- | --- | --- |
|15|Full frontend:8 failed,2,534 passed across193 files;2 failed files and191 passed files. Separate tooling:12 passed.|Typecheck exit2 with two test-helper union diagnostics; boundary exit0.|Full repository, explicit `.omc/**` CLI exclusion: exit0,0 errors,9,887 warnings.|
|16|Three-file scope:1 failed,128 passed;1 failed file and2 passed files.|Full typecheck exit0; full boundary exit0.|Full repository with the same explicit exclusion: exit0,0 errors,9,887 warnings.|
|17|Three-file scope:129 passed,0 failed across3 files.|Full typecheck exit0; full boundary exit0.|Only the three affected test files: exit0, empty output. No full C17 lint execution.|

Initial PowerShell commands resolved `npm.ps1`, which host execution policy rejected before the
gates ran. Subsequent commands used installed `npm.cmd`; policy was unchanged. Initial launch
failures and empty outputs are not gate passes. Actual logs and exit files are retained under
`.omc/research/runbook-20260914/session11/` and hash-bound in the check packet. The original
preexisting-dirty-patch reverse check exits0 and remains separately bound. All1,749 source hashes
matched after each candidate's recorded sweep.

The C15 failures were three climate and five layer-manager drawn-date/retained-frame cases.
Diagnosis found generic undated climate/drought fixtures, a weather date assertion inconsistent
with its retained blank-renderer check, and a fire fixture whose served date contradicted the
selected-day expectation. The test helper also limited valid layer IDs to water/drought despite
the new weather regressions. The reviewed correction supplied consumed publication metadata,
preserved paused/retained/error checks and used the existing `LiveLayerDayReport` layer type.
No runtime defect was established by those eight fixture failures.

## Candidate16 encoding failure and complete restoration

The [Candidate16 application receipt](application-session11-candidate16-20260914.json) binds
manifest `9a3f11845181c435275ebd3b928ab1361c6e0fbc8175f5b1c727dffafb7f5efe` and its three-test-file
change. Its original eight date failures were resolved, but the remaining MTBS assertion failure
exposed a new authoring defect: the correction generator read UTF-8 originals using Python's
implicit Windows encoding, then encoded the resulting text as UTF-8. An en dash in an unrelated
assertion became mojibake, and a plus/minus character in a store-test comment also changed. The
climate test remained ASCII. C16 full lint completed with the result in the table; it is no longer
pending.

The earlier correction review verified hash consistency and the generated diff but missed
character corruption in complete staged files. That failure and its approval are preserved under
`session11/corrections/candidate16-encoding-failure-packet/`; the original C16 result remains
failed. The [17-path encoding audit](../../../../.omc/research/runbook-20260914/session11/encoding-audit.md)
compared exact C14 originals against C15 staged files and found no runtime transcoding. Runtime
fire-years punctuation remains valid and was not changed to satisfy the corrupted assertion.

The repair generator explicitly reads archived C15 originals as UTF-8. The
[independent restoration review](../../../../.omc/research/runbook-20260914/session11/corrections/independent-utf8-restoration-review.md)
went beyond another generated hash comparison: it applied the previously reviewed intended patch
to independently verified C15 originals in isolated scratch and compared all three reconstructed
whole-file byte sequences against the proposal. All three matched; every original non-ASCII line
was preserved exactly and in order. That reconstruction supersedes the earlier approval for
staged-byte fidelity while retaining its history.

After the complete C16 sweep finished, the [Candidate17 application receipt](application-session11-candidate17-20260914.json)
records full source preflight, archival of both replaced C16 files and exact staged-byte writes
to only `LayerManager.test.tsx` and `useMetricAtDate.test.tsx`. `ClimateFieldLayers.test.tsx` is
among the1,747 paths unchanged from C16. All runtime files remain identical to C15. The new
manifest was frozen only after the complete expected source inventory matched.

## Engineering acceptance and remaining browser boundary

The [independent C17 check review](../../../../.omc/research/runbook-20260914/session11/independent-candidate17-check-review.md),
SHA-256 `3efb788d7f53d326e548380306828ce819cd63812771fd8d055c1e31207ce6fd`, accepts the exact
application and completed scoped engineering checks. It independently rehashed all1,749 current
files with zero mismatches. The three-file test run completed in5.21 seconds and retains React act
warnings; passing counts do not mean warning-free output.

C16's full lint pass is retained for that candidate, with unchanged runtime and nearly all source
bytes; it is not relabeled as a full C17 lint run. C17 is not a new full frontend or Python quality
receipt. The source remains an uncommitted working tree, not immutable commit or release evidence.

Session15's reported browser pass and the Session13 stale-review regression require their own
exact source/response/frame/cleanup review. Engineering acceptance here does not prove those
browser behaviors, simultaneous database scheduling, all-layer QA, full case coverage or runbook
completion. The canonical Session11 packet is submitted for a separate final documentation review.
