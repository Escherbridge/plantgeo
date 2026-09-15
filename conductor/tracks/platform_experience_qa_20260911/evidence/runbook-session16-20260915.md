---
type: evidence
status: active
recorded_on: 2026-09-15
---

# Session 16: weather installation and feed publication disclosure

The production diagnostic reproduced a weather source/layer installation failure on base
`d167e7f0231804827422f0788f3b0604393a8ba8`. A separately reviewed four-file correction has
been applied and deployed in frontend revision `286eb91b62aeb2a53c8484184d5adec1336f5c82`.
**All five integrated checks and all four post-deploy automated live tests passed.** Separate
independent review accepted the exercised weather rendering and anonymous feed disclosure
on desktop and emulated mobile, with the limits below. This note does not
close D260915-29/D260915-30 or promote a formal QA case or the whole runbook.

## Production evidence before the correction

The [diagnostic report](../../../../.omc/research/runbook-20260915-session16/live-diagnostic/attempt-before-fix-1/report.json)
records one expected pass, zero skips/unexpected/flaky results, and 17.603 seconds from
2026-09-15T00:55:01.744Z. A diagnostic pass means the observation completed; the initial
product behavior failed. The report SHA-256 is
`cb235e54e00d9e45e0089385d2d9429ed0bf3e916a1fc712c348f4065f7f5cea`.
Its deployment label relies on the separately retained Railway revision proof.

The [independent result review](../../../../.omc/research/runbook-20260915-session16/live-diagnostic/independent-before-fix-review.md)
(SHA-256 `2e6aa0647eaa9dc5ef53009fe198a86837a973cec678f5f3599ec773c29cb192`)
and [seven-artifact bindings](../../../../.omc/research/runbook-20260915-session16/live-diagnostic/independent-before-fix-bindings.json)
(SHA-256 `72c4b128756b815136d2d31994f33fe127a5298b1f940a95c0a7ac60e8e651aa`)
retain the typed summary, complete lifecycle trace and before/after screenshots.

Weather returned ready with **18 rows, requestedDay=servedDay=2026-09-15**; capabilities
reported serverCurrentDate and weatherLatest September 15. This is observed availability
advancement relative to earlier September 14 evidence, not proof of a specific publication
job or three scheduled advances.

At 624 ms after diagnostic lookup, weather had been enabled but global style readiness
was false and its source/four layers were absent. The first styledata event at 1,582 ms
still showed that state. Global readiness became true with terrain-dem completion at
2,071 ms, yet weather remained absent through the 7,350 ms explicit frame and later idle
events. Normal off/on installed the source and four layers by the second styledata event
at 8,091 ms. At 9,088 ms idle, and again at the 10,528 ms explicit frame, the map reported
26 source-query features and 16 rendered-query features. Those are renderer counts, not
unique observations or an expected match to the API row count.

The reviewer personally inspected both screenshots: weather was enabled without visible
cells/labels before recovery, then showed translucent cells and degree labels afterward.
The 77-event trace contains 71 sourcedata, two styledata, four idle and no style.load during
its observation window. Satellite completed at 1,332 ms; terrain remained pending later.
The evidence therefore supports a missed installation after readiness, not a satellite-only
cause or a claim that both styledata events came from recovery.

There were no captured page errors, blocked product requests, HTTP errors or unintentional
request failures. The single request failure was the declared exact analytics-script
exclusion. No model or mutation calls were authorized. The successful harness path removes
temporary listeners/holder and closes its anonymous browser context; this packet does not
contain a separate process-cleanup receipt. No additional execution occurred in the review.

## Applied source and independent reviews

The [application receipt](../../../../.omc/research/runbook-20260915-session16/application.json)
(SHA-256 `da02c5d8fdf5b03f7172570de06c549318adc7c226a3d04202987a2c1695349d`)
records application at 2026-09-15T00:59:04.789803Z and exact before/after hashes for:

- `src/components/map/layers/WeatherLayer.tsx`: admits native weather layers when the public
  parsed-style contract allows them, retains a stable style.load subscription, and separates
  visibility admission from data/paint updates and cleanup.
- `src/__tests__/components/WeatherLayer.test.tsx`: replaces artificial source-readiness
  assumptions with regressions for delayed mount, genuinely unparsed styles, current props,
  hidden/empty state, stable listener order, replacement and teardown.
- `src/components/map/AGENTS.md`: explains the weather contract and qualifies the unchanged
  shared hook's source-only completion limitation.
- `src/app/feed/InterventionFeed.tsx`: changes only two disclosure paragraphs to explain
  signed-in access to pending proposals and public locations/details after approval.

The [weather source review](../../../../.omc/research/runbook-20260915-session16/independent-weather-review.md)
has SHA-256 `969acd259744194638ccc3fa007b6099382ef9323370a6ab133af7f434449637`;
the [feed source review](../../../../.omc/research/runbook-20260915-session16/feed-copy/independent-review.md)
has SHA-256 `f4b2c05de3c51b8741a7f8940a102ad3095140dd4273566dc87e85d216cb0ec8`.
Both were authored by the independent workspace review lane before root applied the batch.
Weather was authored by qa_inventory; feed copy by independent_verifier. This note is an
authoring pass for root's independent review, not self-approval of the feed change.

The [source manifest](../../../../.omc/research/runbook-20260915-session16/source-manifest.json)
contains **1,751 files**, SHA-256
`fa0b101ef75cd6af1879c8108887fa885b748b8f55b64742d2037f1d39e675cd`.
All 1,751 current file hashes matched during this draft's read-only verification. The earlier
weather proposal manifest's applied=false is historical proposal state; application.json
records the subsequent actual application.

## Integrated verification

The [canonical check receipt](check-receipt-session16-20260915.json) has committed Git LF
SHA-256 `6bd8aceb44415e2e235978e61742cc51a447722b80aaf2fd002467bec260408e`.
The reviewed working-tree CRLF bytes have SHA-256
`e73656240d7a9567718a19741df386b2a93dc0b36aea3de9a58620ec0080c068`.
These are distinct line-ending representations of the same receipt; no receipt bytes were
modified for this append. It binds all five raw logs and exit files,
the source/application records, independent source reviews and pre-fix diagnostic references.
Root executed the gates; this collector only read retained artifacts. The separate
[independent pre-push review](../../../../.omc/research/runbook-20260915-session16/independent-pre-push-review.md)
(SHA-256 `6d435e03b0e8dabacc065e54c51803b6e6945a816543f3d5b7df8c5bbd64ac5a`)
approved the source, retained checks and canonical packet after the accurate test-launcher
correction. That review predates the deployment/live append below.

Full frontend verification passed **193 test files and 2,545 tests**, plus **12 tooling tests**
with zero tooling failures, skips or cancellations. Vitest reported 212.61 seconds. Full
typecheck and data-boundary checks exited zero. Lint (`eslint . --ignore-pattern .omc/**`)
exited zero with **0 errors and 9,887 warnings**, including historical worktree files; this
is not a warning-free claim. All 1,751 source-manifest entries still matched after the checks.

The fifth check verified the unchanged Python quality receipt for digest
`e73ccd5b704f9009e45deed33d0dc2db60f072cf22d8992a5e3463a1030e0f05` over 885 inputs,
originally generated at 2026-09-15T00:29:21.185841Z. **No Python suite was rerun and no new
Python receipt was generated in Session16.**

Raw logs are UTF-16 LE with BOM; exit files are UTF-8 with BOM. ANSI removal was only for
summary parsing. An earlier collector incorrectly expected `# tests 12`; the actual Node
summary uses the information-symbol prefix. That metadata assertion failure did not change
the successful tests. Raw artifacts were preserved and no checks were repeated.

## Deployment and post-fix automated live result

The [deployment capture](../../../../.omc/research/runbook-20260915-session16/deployments-success.json)
(SHA-256 `aba7d40b7958c835c764c5d4a4527611abcea00e7cae0e202c13e4cf061e3bc6`)
binds frontend revision `286eb91b62aeb2a53c8484184d5adec1336f5c82` to deployment
`7cd08f66-71cf-4c19-93b9-86f29eed2e62`, SUCCESS at 2026-09-15T01:24:06.339Z.
Martin deployment `15b9b7dd-f4eb-4945-9793-9a0e76898bf7` reached SUCCESS at
01:19:12.164Z for the same commit. API and jobs were SKIPPED for this commit; their prior
successful d167e7f deployments remain active. The [watch-pattern capture](../../../../.omc/research/runbook-20260915-session16/deployment-watch-patterns.json)
(SHA-256 `79fa95120f7799bc41609f840d59f6a0e749fc3787a86a4ea3404be64d220fcc`)
records that this change touched neither service's watched paths. This is not an all-services
redeployment claim.

The [frontend deployment logs](../../../../.omc/research/runbook-20260915-session16/frontend-deployment-logs.json)
(SHA-256 `60d32410f14fc233e2ebc8cc9abd6f457445ccbf8d5b525de6fc77cbb142e6d5`)
record 193 passing test files / 2,545 passing tests in the production build, successful
compilation in 7.3 seconds, and the pre-deploy message that Drizzle migrations are up to date.
The original local check receipt above remains unchanged; these are additional deployment
results, not a retrospective replacement of its pending-deployment snapshot.

The [post-deploy report](../../../../.omc/research/runbook-20260915-session16/post-deploy/attempt-20260915-0124/report.json)
(SHA-256 `25d8110f79c2900ca0046d09ef6e9d9dc1226b6d0dcbd8a03b95e004baae5492`)
records **four expected passes, zero skips/unexpected/flaky, no retries**, in 47.395839 seconds
starting at 2026-09-15T01:24:51.632Z. Desktop and mobile each exercised anonymous health/readiness
and public social entry pages, plus weather and unsent workspace entry. The map journeys
asserted weather on first enable before any recovery, then selected missing September 6,
then returned to Latest without an off/on recovery. They subsequently opened proposal and
unsent analysis entry without submitting a proposal or sending analysis.

| Retained map evidence | Desktop | Mobile |
| --- | --- | --- |
| Initial ready requested/served day | September 15, 18 API rows | September 15, 6 API rows |
| First-enable source/rendered query counts | 26 / 16 | 8 / 8 |
| September 6 response | not_generated / day_not_written | not_generated / day_not_written |
| September 6 source/rendered query counts | 0 / 0 | 0 / 0 |
| Latest-restored source/rendered query counts | 26 / 16 | 8 / 8 |

All four layers remained installed/visible while missing-day data cleared. The Latest
restoration reuses the settled data path; the retained summaries contain one ready and one
missing-day response per project, not evidence of a third fresh upstream request. Feature
query counts are not unique API observations. The desktop map summary SHA-256 is
`e528383e3d2d25a009d61bed15370f0426d156a30aff6cbddcf50ca120309e55`;
the mobile map summary is
`fec312f74ea2e4afd3198c1a08885dee54013a56720f7cab01cb7ae0cfac03bb`.
Both are retained beneath the report's results directory with selected-days records and
screenshots. Anonymous entry assertions include the corrected feed disclosure; signed-in
submission/moderation behavior was not exercised by this live run.

Across all four summaries there are no page errors, blocked product requests, HTTP errors
or primary failures. There are **four non-telemetry net::ERR_ABORTED asset requests**:
one desktop terrain tile; two mobile terrain tiles and one mobile PMTiles request. These
were tolerated by the reviewed harness and remain explicit evidence, not zero network
failures. Six exact analytics-script exclusions were intentional and separately classified.
No model or authenticated mutation was permitted.

The [independent actual-result review](../../../../.omc/research/runbook-20260915-session16/post-deploy/independent-actual-result-review.md)
(SHA-256 `fce66dd5a5995cddb506a7385ef2d97668b6d89144d42d64d8f005907391bd33`)
accepts the exercised first-enable/date-clear/restore rendering and anonymous feed disclosure
on desktop and emulated mobile. Its reviewer personally inspected fourteen original images:
six map stages, six date/control stages and both feed frames. Both maps show visible cells
and numeric labels, empty September 6, then restored Latest; the mobile upper-right cell is
partly covered by native controls, so label placement is not universally accepted. Both feed
frames show the corrected pending-signed-in / approved-public disclosure and anonymous gate.
Root separately reported viewing the six weather map frames; this document author reviewed
scalar evidence and attributes visual acceptance to those reviewers.

The [independent result bindings](../../../../.omc/research/runbook-20260915-session16/post-deploy/independent-actual-result-bindings.json)
(SHA-256 `cff888ed7a420d1c13390c52ae46bcd317803d6a810328d66159849226846262`)
cover 35 artifacts, including those fourteen images. The accepted scope excludes real mobile
hardware, every native weather sublayer/scale/day, live style swaps, authenticated feed and
moderation, submission/streaming behavior, the eight other readiness consumers and full QA.
Earlier failed/blank evidence remains retained. The successful CLI/context-finalizer evidence
is not a separate OS process census.

## Remaining gates and limits

The integrated checks, successful frontend deployment and independently accepted bounded
live result are now retained above. Root owns subsequent defect-ledger and release
dispositions. No checkbox or formal-case status was changed by this documentation update.

The [shared-hook impact inventory](../../../../.omc/research/runbook-20260915-session16/use-style-ready-impact.md)
(SHA-256 `fe6ddde524604a9a16bda24024001f5dbb4cf89daf4f2cfdf2bd64050b2627e1`)
identifies eight other direct component consumers. They were inventoried for risk but remain
unaudited for behavioral acceptance and unchanged by this repair. No universal readiness,
all-layer, mobile, slider-history, agent-workspace or whole-runbook acceptance follows from
this bounded desktop diagnosis or the local application. Existing defect-ledger dispositions
remain for root to update only after actual results.
