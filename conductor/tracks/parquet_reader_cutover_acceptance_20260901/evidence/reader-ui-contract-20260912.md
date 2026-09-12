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

## Remaining acceptance gates

No Railway, production database, object storage, writer, scheduler, publication
pointer or deployment state was accessed. The branch is local and must not be
pushed by this task. Current product/day/zoom cold/warm request traces, timing and
request-count measurements, per-lane bootstrap/policy proof and exact deployed
tree/rollback acceptance remain open in the owning production acceptance track.
This implementation does not close the all-reader production handoff.
