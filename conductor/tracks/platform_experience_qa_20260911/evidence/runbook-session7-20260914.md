---
type: session-evidence
status: active
date: 2026-09-14
---

# Session 7: verified navigation recovery and live environmental browser validation

This continues the [runbook](../../../RUNBOOK.md) and preserves the verified bounded
desktop/mobile contribution journeys from [Session 5](runbook-session5-20260914.md).
The [Linux comparison](runbook-session6-20260914.md) records its independently verified final
offline pass, complete skip ledger and separately retained failed attempt.

## Unsent workspace navigation failure

The independently reviewed unsent suite selects identity resume and a single desktop journey;
it excludes contribution mutation tests, blocks/counts every tRPC POST and retains the AI guard.
Browser attempt `2026-09-14T21-52-57-317Z-29290db0` recorded one passed identity prerequisite
and one failed workspace test in 36.037 seconds. It created a visible unsent Point, exercised
canceled close/Escape and repeated pane switching, then followed the real About link. The URL
and unchanged document time origin were asserted before the failure. No analysis or tRPC mutation
was attempted. Automatic cleanup left no remaining/unverified owned processes and port 3128 was
closed at 21:53:52.575 UTC.

The initial missing-heading assertion did not identify the cause. Actual error-context and the
final screenshot show a runtime error page; the server log records About HTTP 200 followed by
`Cannot read properties of undefined (reading 'getSource')` at `draw.stop()` in
`InterventionDrawControl` teardown. The workspace map owner removes MapLibre before the child
drawing control's passive cleanup runs. This is D260914-25, an application lifecycle defect,
not a proven About locator problem. Navigation return, Polygon recovery and confirmed discard
were not reached in this failed attempt. The correction and later evidence are recorded below;
they do not rewrite the failed attempt.

Original reports remain in the ignored Session 5 attempt directory. Root decoded 26 safe
attachments into `session5/browser6-extracted/` and inspected the final runtime-overlay screenshot.
That overlay also confirms the development-indicator setting does not suppress runtime errors.
Neither the failure nor the earlier partial steps closes a whole case.

## Candidate 14 correction and integrated verification

Candidate 14 moves both the latest-props synchronization and TerraDraw lifecycle to layout
effects, so drawing cleanup precedes the owning map's passive removal. The regression binds
each drawing instance to its actual map under StrictMode and exercises both the workspace and
standalone modal owners; stopping after map removal throws. No runtime exception is suppressed.
The independently reviewed batch changes only the drawing control, its directory rationale and
the workspace recovery regression relative to Candidate 13.

The manifest contains 1,747 source files, SHA-256
`79d15c03290945063dd228cce768e57ab0b2eb4d442ce22f9fa894c66de63685`.
The single integrated frontend sweep passes 12 tooling tests and 192 Vitest files / 2,514 tests.
Type checking and data-boundary checks exit zero. Lint exits zero with 9,887 warnings and the
explicit `.omc/**` CLI exclusion for inaccessible historical scratch. A post-execution hash check
at 22:19:26 UTC verifies every recorded source path with zero mismatches. The coordinator's source
freeze and this external manifest bind the browser runs; their runners do not embed a product
source digest. Reverse-application checking confirms the initial tracked patch remains preserved.

| Attempt | Scope and result | Actual journey and cleanup |
| --- | --- | --- |
| Session 5 browser 7, `2026-09-14T22-02-58-567Z-bc972d46` | Desktop identity resume and unsent workspace, 2/2 passed, 48.957 seconds | Unsent Point and Polygon survive real About/Map client navigation, unchanged document time origin, canvas replacement and repeated pane switching; canceled close/Escape preserves fields and consent; confirmed discard clears geometry and disables submit. No analysis or tRPC mutation. No remaining/unverified owned processes; port 3128 closed. |
| Session 5 browser 8, `2026-09-14T22-06-34-171Z-9a4ba385` | Mobile emulation, 390×844 with touch, 2/2 passed, 36.662 seconds | Same restoration/discard assertions, native taps plus injected Escape/Tab. No analysis or tRPC mutation. No remaining/unverified owned processes; port 3128 closed. |

Each successful run retains 54 decoded JSON/PNG artifacts. A separate verifier inspected the
restored and discarded states on both viewports and reconciled the guards. Desktop report SHA-256
is `5ee42b9dc7a64d57796307dd4b628ece0e21cc7267bd3c0b16baf4796a240e51`;
mobile report SHA-256 is `17d62e4581783550f16663f774d60b38b38242c9c944b9698128f444b527e0d4`.
D260914-25 is fixed within this candidate and bounded journey. Focus after closing remained BODY;
physical-device, touch-only, full accessibility, model-stream and real-human acceptance remain open.
The 220-case matrix retains 178 not-run and 42 blocked entries, with no whole-case pass promoted.

## Refreshed environmental inputs

Eight bounded, unauthenticated, read-only public Parquet requests completed without retries:
one coverage catalogue and seven day reads. Their raw receipt is
`.omc/research/runbook-20260914/session7/refresh-20260914T214534Z-3df00b48/receipt.json`,
SHA-256 `560e09e5e609fc902968abd4a4b4c4087c42623b8d7ec558e971e99bff21446f`.
The three moisture depths on September 5 each returned 58 rows; weather September 14 returned
four rows, while September 6 remained `day_not_written`. Watersheds returned 24 rows at its
August 7 reference day; soil survey remained `lane_never_written`. All day reads used rung 5
and bbox `[-124,47,-122,49]`. Published responses reported no truncation. Current-day weather
values can refresh; matching older state/counts does not prove unchanged values.

The environmental browser slice binds actual per-layer dates, request parameters, depth, viewport,
frames and missingness to real serving reads. A bounded local bridge permits only the declared
public Parquet routes; the application uses the current-weather two-day window, distinct from
the direct day refresh above. The first three browser attempts failed and remain retained. Attempt
`2026-09-14T22-09-02-765Z-d374321b` incorrectly blocked legitimate local Martin reads and Next
stack mapping; its final guard assertion obscured the original journey exception, so the exact
primary failure is unproven. A reviewed harness correction admits only those bounded reads and
preserves the primary error. Attempt `2026-09-14T22-16-51-366Z-4e2dc33c` then timed out waiting
for a matching captured public tRPC response at the first current-weather assertion. Independent
inspection proves a harness decoding defect: the helper discarded SuperJSON metadata marking a
wire `null` as omitted (`undefined`). The real response was ready, requested/served September 14,
18 rows, and the captured frame visibly rendered weather labels. The reviewed correction uses
the application's installed SuperJSON transformer on full wrappers without relaxing date checks.

Attempt `2026-09-14T22-28-06-187Z-c3fd23a2` reached mixed weather September 14 / moisture
September 5, then the typed September 6 weather `not_generated` / `day_not_written` result.
It failed on the harness's incorrect expectation of a transport-fault alert. That alert is reserved
for `upstream_unavailable`; the actual missing-day state is explained by the visible slider
coverage note. Independent frame inspection shows retained moisture labels and no visible
weather labels. This supports a bounded missingness observation, not an exact pixel/value
equivalence proof. The generic coverage note describes incomplete coverage across scales; full
current-rung explanation and all-layer semantics remain acceptance limits. Its correction must
assert the actual coverage note, dates and absence of a false upstream fault.

All three attempts left no remaining/unverified owned processes and closed ports 3128 and 3138.
No complete environmental browser acceptance or whole-case pass is recorded from these failed attempts.

## Completed environmental slice and new date-summary defect

Attempt `2026-09-14T22-36-10-462Z-7f5fc368` passes its one desktop scenario in 24.536 seconds,
with zero skips or flaky outcomes. It retains eight exact tRPC responses and seven public bridge
reads; every read is HTTP 200 and complete, with no body overflow, timeout or rejection. All
browser capture, network, page-error and evidence guards are empty. The pinned glyph range loads
once and real local Martin TileJSON returns HTTP 200. No model, identity or submission action runs.
Cleanup stops only verified owned processes, leaves no remaining/unverified descendants and
closes both ports at 22:36:41.928 UTC.

Independent inspection of six paired DOM/viewport/canvas captures confirms mixed weather
September 14 and moisture September 5 labels, removal of weather cells/degree labels on the
September 6 gap with moisture retained, restored weather, changed root/deep/surface moisture
values and depth legends, and visible watershed outlines with the August 7 static snapshot.
SoilGrids is disabled with its publication explanation; SSURGO returns its typed unavailable
result. This is desktop evidence on a fixture basemap with real environmental responses,
not dense-basemap, count-equivalence, all-rung, mobile, hover or complete temporal acceptance.

The passing assertions do not establish complete date truthfulness. Independent review found
**D260914-26** in the final DOM: never-published SSURGO is shown with September 14 and included
in the summary's four visible dated layers, although only three layers have served data.
`MapDateSummary.resolveDrawnViewedDays` falls back from an absent drawn date to the selected
date under a stale proxied-SSURGO assumption. The layer status also exposes a date despite its
withheld state. This is an application defect queued for the next complete source batch;
PGQA-T01/T03/T05 and L10 remain open. The three prior failed attempts are preserved separately.

The final scenario report SHA-256 is
`2646595602cf49672b5466cd0fcae395d14526ecc9a033626fcbc08313a943a6`;
runner receipt SHA-256 is `ad23bb766cbef45af3fe1ee708d30cfb4538224b9eb644d32df80fa646aad4bc`.
At 22:39:36 UTC, a second source check again verifies all 1,747 Candidate 14 paths with no
mismatch, retaining the earlier post-check separately. The [Session 7 check packet](check-receipt-session7-20260914.json)
binds all workspace/environmental attempts, source checks, integrated gates, raw captures and glyph
provenance and D260914-26; SHA-256 `52f6c6f27aa89c7028736ce45f969ade682f09faeeecf890327126d6b5fe9e7f`.
The [independent review](independent-review-session7-20260914.md) completes 310 referenced
artifact checks with zero mismatches. It accepts the bounded assertions and explicitly retains
the failed SSURGO date-summary variant and overall platform QA RED.

One authorized glyph request returned 76,044 bytes, SHA-256
`62c6d49b15fa836eb6aa45e259c7ca6762f44b011b09e47776efbe4a6db1b397`.
The `Noto Sans Regular/0-255.pbf` URL serves internal stack metadata `Sans Regular`; validation
retains that discrepancy and the installed MapLibre parser's name-independent glyph handling.
Required scalar characters have valid bitmaps and metrics. This supplies a pinned label fixture,
not visual acceptance; dense real-basemap readability and broader font coverage remain open.
