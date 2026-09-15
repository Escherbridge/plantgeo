---
type: evidence
status: active
recorded_on: 2026-09-15
---

# Session 16: weather installation and feed publication disclosure

The production diagnostic reproduced a weather source/layer installation failure on base
`d167e7f0231804827422f0788f3b0604393a8ba8`. A separately reviewed four-file correction has
been applied locally. **All five integrated checks passed; a new deployment and post-fix
live validation remain pending.** Root will append actual release identity and live results
after those gates complete. This note does not close D260915-29/D260915-30 or promote a
formal QA case or the whole runbook.

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

The [canonical check receipt](check-receipt-session16-20260915.json) has SHA-256
`e73656240d7a9567718a19741df386b2a93dc0b36aea3de9a58620ec0080c068`. It binds all five raw logs and exit files,
the source/application records, independent source reviews and pre-fix diagnostic references.
Root executed the gates; this collector only read retained artifacts. Separate root review of
this authored check packet remains pending.

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

## Remaining gates and limits

The integrated checks are bound to the source manifest above. Root must record a successful
new deployment before post-fix live observations can be attributed to the correction. The
live gate must demonstrate initial weather installation without the recovery toggle and
verify the deployed feed disclosure. Deployment and live results are pending here.

The [shared-hook impact inventory](../../../../.omc/research/runbook-20260915-session16/use-style-ready-impact.md)
(SHA-256 `fe6ddde524604a9a16bda24024001f5dbb4cf89daf4f2cfdf2bd64050b2627e1`)
identifies eight other direct component consumers. They were inventoried for risk but remain
unaudited for behavioral acceptance and unchanged by this repair. No universal readiness,
all-layer, mobile, slider-history, agent-workspace or whole-runbook acceptance follows from
this bounded desktop diagnosis or the local application. Existing defect-ledger dispositions
remain for root to update only after actual results.
