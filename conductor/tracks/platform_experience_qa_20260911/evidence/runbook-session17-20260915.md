---
type: evidence
status: active
recorded_on: 2026-09-15
---

# Session 17: Fire and Water layer admission

The preceding session made verified progress: revision
`286eb91b62aeb2a53c8484184d5adec1336f5c82` deployed with the weather installation and
feed disclosure corrections, and four live cases passed independent review. This session
starts from that revision. Its post-deployment RUNBOOK, defect-ledger and Session 16
updates remain local for inclusion in the next reviewed checkpoint. No existing source
changes were present at intake.

## Ownership and evidence boundary

| Role | Existing task | Owned work |
| --- | --- | --- |
| Coordinator/integrator | `/root` | Intake, canonical records, application, source freeze, checks, Git and deployment operations |
| Author | `/root/qa_inventory` | FireLayer.tsx, WaterLayer.tsx, dark-mode-layer-visibility.test.tsx and relevant map/AGENTS.md sections; proposal copies first |
| Independent source reviewer | `/root/workspace_social_audit` | Exact proposal review, lifecycle and data-family preservation; no source writes |
| Live harness author | `/root/independent_verifier` | Bounded real-data Fire/Water desktop/mobile harness; no source or production writes |

All tasks share the current checkout. Root serializes application and canonical edits;
the source author prepares an immutable local proposal before independent review. The
complete batch precedes one integrated sweep. Checks, deployment and live results are
pending at intake; no whole QA case or checklist item is promoted.

The [preceding source investigation](../../../../.omc/research/runbook-20260915-session16/next-session-findings.md)
has SHA-256 `3ad89224c4bf749c2a9898961928c658699bcfd672596791678c3c2ac27e6ebe`.
It identifies a parsed-style admission gap in seven remaining components, with Soil Survey
an exception because its data effect already creates an absent source using getStyle().
This session owns the Fire and Water pair. Scalar, vegetation and occurrence lifecycles
remain separate subsequent batches; the shared hook is not changed mechanically.

## Required correction and validation

Fire and Water currently gate initial installation on isStyleLoaded(), which waits for
unrelated sources. A late mount after style.load can therefore skip installation and
receive only sourcedata afterward. Their existing data effects only update sources that
already exist. This is a source-proven path, not a newly reproduced live failure.

The correction must admit native layers into a parsed style, retain persistent listener
order and current props, and preserve hide/show, style swaps, replacement and teardown.
Regression fixtures must distinguish parsed style from complete source readiness and
reject addSource/addLayer on a truly unparsed style. Tests must retain Fire's cells,
circles and outlines and Water's gauges, aggregate cells and wells, including data/paint
updates and delegated picking cleanup. Empty collections remain empty mounted sources.

Live validation must use actual published data and record unavailable or unexercised
states honestly. Groundwater is explicitly unpublished and its reader returns an empty
array; source installation cannot establish populated-well or groundwater acceptance.
Fire perimeters and burn severity have separate toggles/readers and remain distinct
full-runbook obligations. A passing bounded renderer regression does not establish
full layer support, conservation, accessibility, time-history or agent parity.

## Applied candidate

The independently approved proposal manifest has SHA-256
`eb8b7213a6ac760c6e3809b3be49ed778b7fb69999832f94fa199a51b8368437`; its complete patch
has SHA-256 `de13030f2550a3223eec65b9cc55c618b054712049d0eba2d86e53724e814fc3`.
The [independent source review](../../../../.omc/research/runbook-20260915-session17/independent-source-review.md)
has SHA-256 `67f1915a8f87f7f5a036bbcbaa6f54bf711c5ccf934776c9f280c3d7e42fc3f9` and
approves the exact four files for application and integrated verification.

Root applied the proposal at 2026-09-15T01:45:29.120757Z after verifying every predecessor
source hash and each proposed file. The [application receipt](../../../../.omc/research/runbook-20260915-session17/application.json)
has SHA-256 `7fadbe6246c87f1165ccdd8115b72ede98c1b69261defd117bd6c6b1d09c0c9f`.
The resulting [1,751-file source manifest](../../../../.omc/research/runbook-20260915-session17/source-manifest.json)
has SHA-256 `f8ba0f5e928791adc5df52a932765754b4a243d1606485bdc373fbe8e6ca7807`.
The full frontend release sweep, type, lint, data-boundary and unchanged Python-receipt
verification were launched after the complete batch. Final results remain pending here.
Source proposal state applied=false remains historical; the application receipt records
the later root operation. D260915-31 owns the bounded correction.

## Candidate 1 integrated result and live discovery

The first integrated sweep is retained as a failure in
[its check receipt](check-receipt-session17-candidate1-20260915.json), SHA-256
`ef6e67cd35bdc91111509b9695325c709f5236b568c414d28e3fb0d096761aee`.
Vitest reports 192 passing files and one failing file: 2,547 passing tests and one
failing test out of 2,548, in 209.25 seconds. The remaining event-aggregation readiness
test expects the Fire polygon layer to stay absent while unrelated sources load,
although its fake already exposes a parsed style. The new readiness tests pass.
Type, boundary and existing Python-receipt checks pass; lint has zero errors and
9,887 warnings with `.omc/**` excluded. No successful release sweep is claimed.

Root assigned the additional `src/__tests__/components/event-aggregation-rendering.test.tsx`
path to the same author for a separately reviewed fixture correction and a scan of related
readiness expectations. Original source, logs and Candidate 1 freeze remain retained.
The follow-up must preserve polygon and point semantics rather than weaken the assertion.

The independently reviewed two-case public discovery ran against the still-deployed
`286eb91b` predecessor: both collections completed in 22.851031 seconds, without retries
or skips. Water's current September 14 response contains 26 aggregate polygon rows and
three candidate public support centres. Its snapshot records a parsed, globally ready
style but no Water sources or native layers. Fire's current September 13 response is
ready but contains no rows in the same viewport; its empty installed source does not
establish a populated Fire rendering pass. Actual discovery review and bounded follow-up
anchor searches remain separate from corrected-deployment acceptance.

## Candidate 2 final integrated verification

The additional test-only proposal is independently approved in
[the candidate2 review](../../../../.omc/research/runbook-20260915-session17/candidate2/independent-review.md),
SHA-256 `77bda890100d5983d59d8805f5eb44013f210e342a4ba9c7bad56d4f70be10d8`.
Its manifest is `e2916489254f119a53f3f1da4da5d6aa59ecfec19f5bd48e993556fecf982340`.
It changes no production code: the existing event-aggregation fixture now distinguishes
parsed style from source readiness and retains coarse Polygon/detail Point semantics.
Three meaningful readiness executions replace the outdated single expectation.

Root applied that exact file after checking all Candidate 1 hashes. The
[follow-up application](../../../../.omc/research/runbook-20260915-session17/candidate2/application.json)
has SHA-256 `70aedac400fbc95dc438265b02441397bb70100d32b3248725c52283e215dab5`.
The final [1,751-file source manifest](../../../../.omc/research/runbook-20260915-session17/candidate2/source-manifest.json)
has SHA-256 `d11d38642bf5e6f87e3f1180b53f04262232a2b27f10dc9d5f98eccf1fc554f4`.
Only the event-aggregation test differs between the two integrated manifests.

The [final check receipt](check-receipt-session17-20260915.json), SHA-256
`24c55e6dc85a4f1d44b29267f2a3d296589bc62602658e8abc6fb1f643324608`, records
**193 passing files / 2,550 frontend tests and 12 passing tooling tests**. Vitest took
183.75 seconds. Full type checking, data-boundary checks and lint passed; lint explicitly
excludes `.omc/**` and reports zero errors and 9,887 warnings. The existing unchanged
885-input Python quality receipt verifies; no Python suite or receipt writer was rerun.
The collector rechecked all final source hashes, both applications and the original
failed receipt/logs. Candidate 1 remains failed evidence, not a relabelled pass.

## Second discovery and release boundary

The separately reviewed three-case follow-up completed against predecessor `286eb91b`
in 39.268076 seconds, with no skips, retries or unexpected results. Its
[report](../../../../.omc/research/runbook-20260915-session17/live-discovery2/attempt-1/report.json)
has SHA-256 `1f6fd816ec20e072c114749c45c27faccab16d5fa897f87a2e757b71f0a16b5d`.
Current September 13 Fire data contains eight coarse polygons in the broader PNW
viewport, and one rung-9 polygon in a refined viewport. The historical fallback was
not needed. Both Water detail searches returned real raw-point gauges for September 14:
three in the first viewport and one in the second. These supply bounded public location
candidates for aggregate/detail rendering tests, not corrected-deployment acceptance.

Independent result review also distinguishes the captured renderer states: broad Fire
has a visible aggregate cell, but the populated refined Fire viewport and both populated
Water detail viewports have absent native sources at globally ready snapshots and blank
overlay captures. The refined Fire observation supplies pre-fix installation evidence
that the earlier genuinely empty northern Fire viewport could not establish.

The final desktop/mobile harness must separately bind reviewed current anchors, verify
populated first-enable rendering, honest empty-day clearing and Latest restoration,
and retain groundwater's unpublished status. This candidate is locally checked; commit,
deployment, final live execution and visual acceptance remain pending at this snapshot.
No formal QA case or runbook checklist item is promoted.

## Terminal outcome — deployment and live regression

Commit, deployment and live execution are no longer pending. `ec172e881e4aa640231ae073a1d04408fd05ad5a`
deployed: frontend `69fbe03b-3c2e-452d-aa9d-0eaa6d28b9d6` SUCCESS 2026-09-15T02:24:14.199Z, Martin
`de34dea0-dccb-4916-b251-4e1e8c01e8ec` SUCCESS 02:19:25.401Z (`deployment-success.json`). Data-API
and job-executor deployments are SKIPPED for unchanged scope and retain their successful `d167e7f0`
revisions. Production build recorded 193 files / 2,550 tests plus 12 tooling tests, successful
compilation, up-to-date migrations and readiness.

The independently approved eight-case harness executed at `live-regression/attempt-20260915-0224`
(reviewed in [independent-review.md](../../../../.omc/research/runbook-20260915-session17/live-regression/independent-review.md),
approval SHA-256 `4686760b8eaf50fb58db2ab7a9bb875618b3a4ce0ab9bfe7978a85999d176996`) produced **4
passes, 4 failures, 0 skips, 0 retries in 127.490632 seconds**; report SHA-256
`e4f3f16959f575e25b7068c7026dcf8e769d3d7b14dc71f470bdc404bc0b3a25`; root terminal exited 1.

All four Fire desktop/mobile aggregate/detail journeys passed. All four Water journeys passed their
native first-enable source/layer installation and populated rendering assertions — exercising and
holding the Session 17 correction's own subject matter — then failed at `regression.browser.ts`
line 63, which asserted September 6 was a missing day. September 6 was in fact ready, with matching
selected/served/observed day and positive data: 122 aggregate rows / 1 detail row desktop, 62
aggregate rows / 1 detail row mobile. The missing-day and Latest-restoration portions of those
journeys were never reached and remain unexercised, not failed-on-the-merits. This is logged as
**D260915-32**, a live-regression harness anchor defect: the retained Water capabilities advertise
only September 6 as a coverage gap, with no governed absences, `coverageGapsTruncated=false`, and
zero additional eligible gap dates in this capture (`live-regression/anchors.json`). A discovery
limitation is not missing-day acceptance; the harness must never manufacture a missing date, use an
arbitrary future day, or alter governed publication to satisfy this test. See
[defects.md](defects.md) and [task-ledger.md](task-ledger.md).

**D260915-31 is updated to reflect deployment plus this bounded live result; it is not closed.**
The missing-day clearing and Latest-restoration behavior remain unexercised for Water, and root
viewed only four of the eight cases' images (Fire desktop aggregate first-enable, Fire mobile
aggregate missing-day, Water mobile detail first-enable, Water desktop aggregate first-enable) —
a complete independent result/image review is still pending. Groundwater remains explicitly
unpublished and its reader returns an empty array; this Water-gauge result does not establish
populated-well or groundwater acceptance. Fire perimeters and burn severity remain separate
toggles/readers and distinct full-runbook obligations. No whole QA case or runbook checklist item
is promoted; the formal 220-case matrix is unchanged by this session.
