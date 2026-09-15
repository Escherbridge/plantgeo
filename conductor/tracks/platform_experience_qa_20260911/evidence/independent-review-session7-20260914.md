---
type: independent-review
date: 2026-09-14
reviewer: /root/independent_verifier
status: accepted-with-scope-limits
check_receipt_sha256: 52f6c6f27aa89c7028736ce45f969ade682f09faeeecf890327126d6b5fe9e7f
source_manifest_sha256: 79d15c03290945063dd228cce768e57ab0b2eb4d442ce22f9fa894c66de63685
overall_platform_qa: RED
---

# Independent review: Session 7 workspace and environmental browser evidence

Candidate 14's drawing teardown correction and its bounded desktop/mobile workspace
regressions are accepted. The final anonymous environmental browser journey also passed its
automated assertions. Independent visual and DOM review nevertheless found **D260914-26**:
the date summary counts unpublished SSURGO as a layer displaying today's data. That variant
fails and remains open. This review does not accept whole-platform QA or promote any complete
case in the 220-case ledger.

The frozen [check packet](check-receipt-session7-20260914.json) is 100,774 bytes, SHA-256
`52f6c6f27aa89c7028736ce45f969ade682f09faeeecf890327126d6b5fe9e7f`. Independent recursive
verification checked **310 referenced artifacts with zero hash/size mismatches**. The
[session ledger](runbook-session7-20260914.md), [execution variants](execution-variants-20260914.md)
and [defect ledger](defects.md) preserve failed attempts and distinguish observed variants
from complete case acceptance.

## Candidate and drawing lifecycle

Candidate 14's manifest SHA-256 is
`79d15c03290945063dd228cce768e57ab0b2eb4d442ce22f9fa894c66de63685`, covering 1,747 paths.
Independent rehashes found no mismatches. Its delta from Candidate 13 is limited to
`InterventionDrawControl.tsx`, the real workspace recovery regression file and the map
directory's rationale. Owner changes predating this run remain outside this authored delta;
the packet retains the root's successful reverse-application preservation check.

Browser attempt 6 exposed an actual application failure during About navigation:
`draw.stop()` accessed MapLibre after the parent removed the map. The earlier locator-only
interpretation was withdrawn. Moving drawing attachment/disposal and the latest-props
update into layout effects makes drawing disposal precede the actual owners' passive map
cleanup. The fix does not suppress SDK errors. The real workspace and standalone modal
regressions bind fake drawing sessions to each map and reject stopping after removal.
StrictMode checks balance sessions per map, permit replay maps with no drawing session,
and require an actual session overall. This is meaningful coverage of the observed ordering
failure, rather than a requirement that every StrictMode map must start drawing.

Candidate 14 passed **12 tooling tests and 192 Vitest files / 2,514 tests**, full typecheck
and data-boundary checks. Lint exited zero with **zero errors and 9,887 warnings**; generated
`.omc/**` scratch was excluded after access errors, with no tracked JavaScript omitted by
that exclusion. These are scoped command receipts, not a warning-free lint claim. Python
was unchanged; its independently reviewed Linux comparison and retained Windows failure
remain in the [Session 6 review](independent-review-session6-20260914.md).

## Unsent workspace observations

The reviewed suite selects only identity resume and the unsent journey; it excludes the
contribution mutation specifications. Normal fresh login uses previously registered local
identities. AI calls and tRPC POST mutations are blocked and counted. The suite does not
submit drafts, invoke models, forge cookies or promote users.

After the fix, desktop attempt 7 passed both its identity prerequisite and workspace journey
in 48.957 seconds; mobile attempt 8 passed both in 36.662 seconds. Their report hashes are
`5ee42b9dc7a64d57796307dd4b628ece0e21cc7267bd3c0b16baf4796a240e51` and
`17d62e4581783550f16663f774d60b38b38242c9c944b9698128f444b527e0d4`, respectively.
Both retained zero analysis calls and zero denied mutation attempts. Cleanup verified empty
remaining/unverified owned-process sets and a closed app port at 22:04:01.423 and
22:07:35.805 UTC, respectively.

The journeys exercise Point and Polygon drawing, canceled Close/Escape, repeated pane
switching, actual About/Map client navigation with unchanged document time origin, old canvas
disconnection, restored fields/location/consent/valid geometry on a new canvas, pending new
location notice, and confirmed discard followed by empty defaults. A name-and-consent probe
without geometry remains unable to submit. Mobile uses native taps at an emulated 390×844
viewport, with injected Escape/Tab keys. This does not establish physical-device behavior,
complete keyboard/focus restoration, exact vertex fidelity, undo history, streaming recovery
or hidden initialization. The applicable bounded workspace mappings are A20, A22 and A24;
A21 remains unexercised. Focus returning to BODY remains a limitation.

## Environmental observations and open date-summary defect

The anonymous environmental harness retains the reviewed owned-process cleanup boundary,
blocks model and application mutation calls, and forwards only bounded public Parquet reads
through the fixed local bridge. Fixture basemap, terrain and pinned glyph assets make this
a mixed fixture/live-data browser experiment; it is not a production browser deployment.
The bridge restricts routes, dates, bounding boxes and rungs, with two workers, 15-second
requests, a 2 MiB response limit, 40 attempts and a bounded lifetime. Rejections and live
counters are durable. The specifically allowed Next stack-mapping POST is a read-only
development diagnostic; other framework actions remain blocked.

All three failed environmental attempts are retained: missing Martin/diagnostic allowances
and an overwritten primary error; incorrect decoding of SuperJSON's undefined date; and an
incorrect expectation that `not_generated` uses the upstream-unavailable fault indicator.
The corrections preserve strict typed state/day assertions. They neither coerce arbitrary
null values nor change product behavior. The coverage note actually shown for the selected
missing day is generic and does not prove that the current rung has data elsewhere.

Final attempt `2026-09-14T22-36-10-462Z-7f5fc368` passed one test in **24.536 seconds**,
report SHA-256 `2646595602cf49672b5466cd0fcae395d14526ecc9a033626fcbc08313a943a6`.
Eight retained tRPC captures and seven completed bridge requests independently reconcile;
all returned HTTP 200, all body hashes/sizes matched, and bridge rejects, page errors,
blocked requests, request failures and evidence errors were zero. Both app and bridge ports
were closed with no remaining/unverified owned processes at 22:36:41.928 UTC.

At zoom 8 / rung 5 in the recorded Pacific Northwest viewport, the actual typed responses
showed weather September 14 with 18 rows; selected September 6 `not_generated` with reason
`day_not_written` and no data; each September 5 moisture depth published with 280 features;
SSURGO unavailable with zero features; and watersheds requested September 14 but served from
August 7 with 63 rows. All applicable truncation flags were false. Returning to previously
read weather/surface moisture can use the client cache; it is not evidence of a new request.

The reviewer personally inspected all six final full-frame screenshots. Weather degree
labels and moisture values coexist; selecting the missing weather day removes temperature
labels while retaining moisture; surface/root-zone/deep selection changes the displayed
depth and values; returning to surface restores its values; watershed outlines appear with
the static snapshot caption. Clipped legend content and offscreen SSURGO controls limit
visual claims; their typed/DOM assertions are separate evidence.

The final DOM and screenshot also expose D260914-26: the summary says four visible layers
span three dates and assigns September 14 to unavailable SSURGO. SSURGO has no served data.
`resolveDrawnViewedDays` falls back from absent `drawnDate` to the selected layer date, so
this is a product semantic defect beyond the passing harness assertions. The next fix must
distinguish selected dates from dates of displayed data without losing selected-day context
for unavailable daily agent requests. T01/T03/T05/L10 and full temporal acceptance remain
open; the passing browser result must not be presented as resolving this finding.

## Disposition

Accept the hash-bound code correction, recorded engineering checks and the explicitly
described browser variants. Keep overall QA **RED**, with 178 cases not run and 42 blocked,
zero complete-case promotions. Remaining gates include the date-summary defect, all-layer
and all-rung coverage, production and migration evidence, real-human access decisions,
source admission/history/refresh obligations, model/streaming behavior, and full accessibility
and physical-device validation. This lane authored this review only and ran no application
tests, database mutations, remote requests or process cleanup during final verification.
