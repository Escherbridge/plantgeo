---
type: track-evidence
slug: parquet_reader_cutover_acceptance_20260901
artifact: reader-ui-contract
date: 2026-09-12
status: authored
base_commit: 64f4f892bd2b744cc097c7f76a1f239997b80f52
base_tree: f8697da694a3f9fbbf28e70ff7581bd59546bfd6
branch: codex/reader-ui-contract-20260912
---

# Reader UI contract correction

The original internal source approval below predates the external checkout review
of `3143a227d680feb4c2379b936115e20030dd8b7f`. That external review returned
**changes requested**, with the two P2 findings and follow-up recorded in
"External checkout review and follow-up" below. The original commit is retained
unchanged as review history.

This local Priority 1 slice starts directly at the named audit base. It does not
carry the subsequent documentation-only audit commit into the code branch. The
final immutable commit, tree, changed paths, verification results and log digests
are recorded together in the local task handoff after the frozen-tree sweep; a
commit cannot contain its own identity. This receipt records the authored scope
and verification contract, not a production acceptance verdict.

## Temporal acceptance

`requestedDay` identifies the question and `servedDay` identifies the evidence
that answered it. The browser must validate both before accepting a successful
response as the current settled-day answer. Equality is required by an exact-day
reader, while the existing release, snapshot and bounded-window readers retain
their different contracts. Permitted older evidence must remain dated as such.
Disallowed responses must produce an explicit notice or refusal and must not
advance the drawn-day ledger or expose their rows as the selected day's answer.

The correction preserves governed absence, unpublished day, never-written lane,
upstream failure and truncation as distinct outcomes. A retained placeholder
must match a previously accepted payload and its original request context; a
rejected cached response cannot become accepted merely because the key changes.
Previously accepted live windows retain their original clock context across UTC
midnight. A first-seen placeholder without that provenance is explicitly refused.
Query cancellation and viewport/rung keys retain
their existing ownership. An undated static lookup does not acquire a day key or
a fabricated daily freshness claim.

The allowed source relationships retained by the correction are:

| Reader | Allowed relationship |
| --- | --- |
| Fire detections (`dayRange=1`), sensors, soil and climate fields | The response answers the requested day; the served/observed day is that same day. |
| Drought | An eligible release at or before the request, within the server's maximum fourteen-day carry; the server still enforces the shorter six-day bound for a superseded release. |
| Vegetation | A served partition within the inclusive thirty-day trailing window. |
| Current water/weather observations | Today or the preceding UTC day under the current-day window; historical selections require their exact day. |
| Evacuation, fire-perimeter and burn snapshots/releases | Evidence at or before the requested as-of day, with the existing server product predicates and publication validation retained. |
| Undated static lookup | No selected-day key or daily freshness promise; the response's own snapshot relationship remains distinct from a map date. |

Window refusals may name the missing/absence partition within their window. They
retain that typed evidence and its date instead of being relabelled as an exact
selected-day absence. The drawn-day ledger continues to identify the answered
as-of question; an older served day is named separately so it does not create a
false loading indicator. Retained frames use their own answered date.

## Coverage presentation

The Layer Panel identifies coverage derived from a checksummed availability index
separately from discovered coverage. It names the source publication ceiling
separately from the layer's available dates. A carried release may legitimately
answer beyond its publication ceiling; the caption must not clamp the slider or
describe those carried days as new source publications. Static snapshots do not
acquire an inferred daily backlog.

## Verification and custody

The implementation, regression tests and evidence were authored as one batch for
independent review and the integrated local sweep. The separate verifier evaluates
the complete diff; implementation agents do not approve their own work. The
compile-only correction discovered by that sweep is recorded explicitly below.

Final independent source verdict: **approved for the local verification sweep**.
The verifier reviewed the complete implementation, regression suites and Conductor
scope in a separate context. Its findings were resolved before freezing: coverage
checksum wording, served-versus-answered date separation, rejected-placeholder
resurrection, original live-day context across midnight, omitted-date field
validation, static notice wording, composite-water retention and a temperature
assertion's encoding. No unresolved source findings remain. This verdict does not
claim executable test success; the final local handoff records those results.

The separate verifier's caption pass accepted the final source after removing a
per-response checksum claim and confirming that snapshot ceilings create no
daily-lag statement. Index provenance may arrive through a pointer-bound rollup;
the caption therefore says where coverage came from without asserting that the
browser or every response recomputed the generation checksum.

Final requested commands, against the explicit base above:

- `npm run check:data-boundary`
- `npm run type-check`
- `npm run lint`
- `npm run test:changed -- --base 64f4f892bd2b744cc097c7f76a1f239997b80f52`
- `git diff --check`

The selector plan and raw check logs are retained locally. Results must be reported
as scoped tests, even if the selected surface is broad. No Python quality receipt
or full-suite/release pass is inferred. Dependencies were restored with the locked
offline npm install and lifecycle scripts disabled; no dependency manifest was
changed.

## Executed sweep and compile-only correction

The integrated sweep ran once against frozen tree
`ae9802efc3ae3398c9fd64bf5907f522c4e9d562`:

| Gate | Result |
| --- | --- |
| Data boundary | Passed all three checks. |
| Lint | Exit 0, zero errors and 555 warnings. Warnings are not described as a clean lint run. |
| Source-related tests | 26 files passed; 609 tests passed. |
| Filesystem/command contract batch | 9 files passed, 2 skipped; 213 tests passed, 13 skipped. This is a second batch in the same scoped command, not a unique-test total. |
| Diff whitespace | Passed against the explicit base. |
| Initial type check | Failed with one TS18048 at the field acceptance hook's state-updater callback. |

After all sweep results settled, the only code correction captured the already
guarded `query.data` in a local constant before using it inside the updater.
The independent verifier approved that stable narrowing: payload identity, request
context, effect dependencies and runtime behavior remain the same. This receipt
was then updated to preserve the failure and its disposition before the final
type-check recheck. The scoped test command was not repeated. Its tested tree
above remains distinct from the final commit/tree named in the local handoff;
the final type-check result is recorded there. No test, lint or boundary result is
silently relabelled as having executed against a different tree.

## External checkout review and follow-up

External review task: `01a0949c-ff3f-72e1-96b8-f49cf11b5272`, titled
"PlantGeo reader contract reviewer". Its independent verdict was **changes
requested: two P2 findings**, against commit
`3143a227d680feb4c2379b936115e20030dd8b7f`, tree
`56c8613f1bca6f2ec5bd03f194f8638ae268cf78`. The reviewer verified the original
31-path custody record and all seven log digests; it did not grant the candidate
an independent pass. Its focused reproductions were local and in memory.

1. `WeatherHistoryReport` validated against the immediate slider selection while
   its request still used the debounced day. A valid day-B response landing during
   B-to-C debounce was refused and never acquired the provenance needed to remain
   a retained frame during C's fetch. The follow-up validates against the settled
   request while keeping immediate selection and loading presentation separate.
2. A window terminal absence could claim one requested partition and an older
   served partition, even though that evidence represents one exact day envelope.
   The follow-up requires the absence's own requested and served days to match for
   vegetation/live-observation windows. That matched day may still precede the
   selected window endpoint; release/snapshot absence carry remains separate.

Regression scope includes B landing during the real B-to-C debounce then retaining
its accepted identity during C's fetch, older served-partition mismatches inside
both allowed windows, and preservation of valid earlier matched absences. A fresh
source reviewer evaluates this four-file implementation/test follow-up separately
from its author before the focused check batch and new commit.

Follow-up source review: **approved**, with no unresolved findings. The fresh
reviewer confirmed that the debounce regression exercises the actual slider timer
and accepted-state effect with stable payload identity, that both mismatched
absence regressions are inside their otherwise allowed windows, and that valid
release/snapshot absences remain admitted. Query keys, cancellation and the
acceptance hook are unchanged. This is the follow-up source review's verdict;
the external task's changes-requested verdict remains the historical disposition
of the unchanged original commit.

The follow-up is a new child of `3143a227`; no amend, rebase or replacement of that
reviewed commit is authorized. Its exact commit/tree, changed paths, command exit
codes, test counts and log digests are recorded in the final local follow-up
handoff. Only the affected helper, acceptance hook, weather panel, map manager and
fire-hook suites plus final type/diff checks are selected; unrelated suites and
production acceptance are not rerun or inferred.

Follow-up recheck result: **passed**. The complete implementation/test batch was
frozen at tree `ad3074ff03f8ac67a8404fc493e0a7ab36f89ee2`. One focused Vitest
invocation (`node node_modules/vitest/vitest.mjs run --configLoader runner
--maxWorkers=2`, with the five files below) passed all **172 tests in 5 files**, with
no skips:

| Focused file | Passed tests |
| --- | --- |
| `src/__tests__/components/WeatherHistoryReport.test.tsx` | 13 |
| `src/__tests__/lib/environmental/parquet-day-contract.test.ts` | 55 |
| `src/__tests__/hooks/useParquetDayContract.test.ts` | 11 |
| `src/__tests__/components/LayerManager.test.tsx` | 75 |
| `src/__tests__/hooks/useParquetFireDetections.test.ts` | 18 |

`npm run type-check` passed. ESLint on the four changed implementation/test files
passed with zero errors and eleven warnings. The initial diff check against
`3143a227d680feb4c2379b936115e20030dd8b7f` passed. Tests and types were not rerun
after these result paragraphs were added: only this evidence and its metadata
summary changed, and the final diff check covers that documentation update.
The final handoff records the final tree and verifies that its implementation/test
blobs match the tested tree. Logs retain the React `act(...)` warnings from the
focused run; this is local mocked verification, not browser or production evidence.

## Remaining acceptance gates

No Railway, production database, object storage, writer, scheduler, publication
pointer or deployment state was accessed. The branch is local and must not be
pushed by this task. Current product/day/zoom cold/warm request traces, timing and
request-count measurements, per-lane bootstrap/policy proof and exact deployed
tree/rollback acceptance remain open in the owning production acceptance track.
This implementation does not close the all-reader production handoff.
