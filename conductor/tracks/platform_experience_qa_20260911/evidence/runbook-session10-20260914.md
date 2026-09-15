---
type: session-evidence
status: active
date: 2026-09-14
---

# Session 10: temperature and VPD browser variants

This environmental continuation runs separately from the [Session 9 social diagnostics](runbook-session9-20260914.md)
and preserves the [Session 7 weather/moisture evidence](runbook-session7-20260914.md).
Candidate 14 remains frozen. The prepared slice targets PGQA-L12/L13, T01/T02/T12 and M05;
it does not cover the full dimensions of those cases.

The independent reviewer is `/root/workspace_social_audit`; `/root/qa_inventory` authored the
ignored packet. Root executes and owns the exact local processes. The reviewed runner keeps
the Session 7 custody and environment boundaries and permits only five temperature/VPD lanes,
observed September 4â€“5, rungs 5/9, the fixed regional bbox and bounded read-only public bridge.
Desktop uses 1440Ã—1000, zoom 8/rung 5; mobile is separately selected at 390Ã—844, zoom 10/rung 9.
Those are distinct viewport/rung configurations, not a same-bbox conservation comparison.

## First desktop attempt: incorrect opacity expectation

Attempt `2026-09-14T22-52-39-624Z-cc1a7ec9` fails after the four temperature-depth checks.
The native opacity input's minimum is 0.05; Home correctly produces 5 percent, while the harness
expects zero. This is an authored inventory/harness mismatch, not a newly established application
defect. The [map opacity contract](../../../../src/components/map/AGENTS.md) explicitly prohibits
zero opacity: invisible features would remain hit-testable. The separate visibility switch removes
the layer from drawing and picking. Existing store tests enforce the positive floor.

The original PGQA-M05 zero/100 wording is reconciled in the [case inventory](cases.md), retaining
the original expectation in this dated account. The harness correction must check strength at
5/100 percent and separately turn visibility off/on while preserving strength and selected day.
No product change is needed for that correction. The failed attempt remains intact.

Independent partial-evidence review verifies five browser captures and five complete HTTP 200
bridge responses, with no rejection, timeout, capture failure, blocked request or page error.
Each of surface, root-zone, deep and substratum returns 280 untruncated Polygon cells with
requested/observed September 5, Celsius and rung 5. Four depth frames show changed Celsius
labels and depth selection; the substratum legend explains the measured 0.25Â° cells. The final
5-percent frame visibly recedes. Initial label density is still settling in the first frame, so
this does not establish complete label/render-density acceptance.

Cleanup verifies both local ports closed, no remaining/unverified owned processes and valid
recorded stop actions. VPD, September 4, return-to-latest, corrected visibility actions and mobile
were not reached. The attempt is failed overall, with useful partial evidence and no whole-case pass.

## Corrected desktop and separate mobile outcomes

The corrected packet preserves the intentional strength floor: Home is 5 percent, End is
100 percent, and the separate eye switch hides then restores the layer while retaining the
selected day and strength. It changes the harness expectation, not product behavior. The
corrected ten-input manifest SHA-256 is
`c7bc4d25a02cc0d0289d930cb4dbaa58a0f8ca0bdc1a529164599588a14d7e32`.
The original runner receipt retains the original spec and README hashes; those two inputs
are explicitly distinguished from the corrected packet in the canonical receipt.

| Attempt | Project and result | Actual bounded field evidence | Cleanup completed |
| --- | --- | --- | --- |
| `2026-09-14T22-52-39-624Z-cc1a7ec9` | Desktop: 0 passed, 1 failed, 0 skipped; 36.806 seconds | Four temperature depths, 280 cells each; fails authored zero-opacity expectation | 22:53:23.227 UTC |
| `2026-09-14T23-01-44-370Z-a5e619e2` | Desktop: 1 passed, 0 failed/skipped; 28.085 seconds | Seven temperature/VPD field responses, each 280 cells at rung 5 | 23:02:18.724 UTC |
| `2026-09-14T23-05-16-541Z-e59bddca` | Mobile: 1 passed, 0 failed/skipped; 21.698 seconds | Same seven variants, each six cells at rung 9 | 23:05:44.914 UTC |

Each successful project captures temperature surface, root-zone, deep and substratum on
September 5; substratum on September 4; and VPD surface on September 5 and September 4.
The responses report matching requested/observed days, published availability, untruncated
Polygon cells, finite values, and Celsius for temperature or kPa for VPD. Native date controls
exercise independent previous-day and return-to-latest actions; latest may reuse a captured
response, so the receipt does not count a cache reuse as a new upstream read.

The retained bridge records 5, 8 and 7 admitted requests respectively, including coverage
where requested, with zero rejection. Every upstream receipt has a complete HTTP 200 body,
worker exit zero, no timeout, and a verified body size/hash. Browser summaries record zero
blocked requests, failed requests, page errors and evidence errors in all three attempts.
The failed attempt's primary assertion remains a failure. No identity, agent-model or
application mutation is part of this anonymous slice.

Cleanup is independent of the browser result: each attempt records two immediately
revalidated owned processes stopped and four already exited, no remaining or unverified
owned processes, and both application port 3128 and bridge port 3138 closed. Thus the first
attempt has successful cleanup while its test remains failed.

## Visual interpretation and remaining dimensions

The separate result review by `/root/workspace_social_audit` examined the retained images and
response bodies. Desktop temperature labels visibly change across the four depths, and the
strength/visibility controls follow the documented behavior. These observations support the
bounded response/control outcomes above, with the following material limits:

- At the mobile 390 by 844 viewport, the open manager covers essentially the entire canvas,
  including its crop. The frames establish controls, depth/legend/rung and selected dates;
  they do not establish unobstructed mobile map values or date-summary presentation.
- Temperature and VPD remain enabled together in this journey. The sampled labels show
  Celsius; typed VPD kPa responses do not establish isolated VPD numeric-label visuals.
- Desktop `06-both-latest.png` captures a transient mixed-date summary. Its associated JSON
  and the final anonymous page settle to September 5 for both layers. This is transition
  evidence, not a persistent defect or proof that every image waited for settled paint.
- Desktop and mobile use different bboxes and rungs. No cross-rung conservation, full
  history, hover-value, full T07 disagreement or physical-device acceptance is claimed.
  The local basemap and glyph fixtures do not certify dense production basemaps or all fonts.

PGQA-L12/L13, T01/T02/T12 and M05 remain partially covered. No complete case or runbook gate
is promoted by these results. The next visual slice should close the manager through the
actual UI, wait for settled dates, and isolate VPD before capturing desktop/mobile labels.

## Canonical packet and review handoff

[The Session 10 check receipt](check-receipt-session10-20260914.json) binds all three attempts,
205 artifact entries, exact browser/bridge bodies, cleanup records, original versus corrected
executed input hashes, and the prior independent result review. Its SHA-256 is
`a9f46cb2c49a2038a6c408999e38b1a8aac36d3e23923b9b94d03cdc352def87`
(115,471 bytes). The ignored `session10/write-receipt.py` authors it using retained files only;
it neither executes tests nor starts a browser, network request or service.

The source binding is the externally coordinated Candidate 14 manifest
`.omc/research/runbook-20260914/source-manifest-14.json`, 255,718 bytes, SHA-256
`79d15c03290945063dd228cce768e57ab0b2eb4d442ce22f9fa894c66de63685`.
The browser runner did not embed that source manifest; the receipt makes this external
binding and its limits explicit rather than asserting a new execution-time attestation.
The existing result review is `session10/independent-result-review.md`, SHA-256
`45da7efe1254b65f05995fc8254e33234258fd6d5496418a155b506d8d82567c`.

Canonical packet author: `/root/qa_inventory`; execution coordinator: `/root`.
The separate [canonical independent review](independent-review-session10-20260914.md) verifies
272 references across 208 unique paths, including all 205 artifact entries, with zero mismatches.
Its SHA-256 is `7080357594e78aaa3b0f18b2f97f7128bca28fb6e436305c7e9d65de3b6fb8a5`.
No product source, RUNBOOK, defects, global variants or task ledger was modified in this pass.
