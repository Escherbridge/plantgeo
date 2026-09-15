---
type: independent-review
date: 2026-09-14
reviewer: /root/independent_verifier
status: accepted-with-scope-limits
check_receipt_sha256: a9f46cb2c49a2038a6c408999e38b1a8aac36d3e23923b9b94d03cdc352def87
overall_platform_qa: RED
---

# Independent review: Session 10 temperature and VPD variants

Accept the corrected desktop and separate mobile browser results as bounded response and
control evidence. Preserve the first desktop failure as an incorrect harness opacity
expectation. Unobstructed mobile map visuals and isolated VPD numeric labels remain
unverified. No complete case or runbook gate is accepted by this review.

The frozen [check packet](check-receipt-session10-20260914.json) is 115,471 bytes, SHA-256
`a9f46cb2c49a2038a6c408999e38b1a8aac36d3e23923b9b94d03cdc352def87`.
Independent recursive rehash checked **272 references across 208 unique paths**, including
all **205 artifact-list entries**, with zero hash/size mismatches. The reviewer also read the
receipt generator without executing it, independently parsed the actual reports and field
bodies, and compared cleanup summaries to the original runner receipts. The
[session ledger](runbook-session10-20260914.md) accurately carries the material limits.

## Source and execution boundaries

The packet binds Candidate 14's retained source manifest,
`79d15c03290945063dd228cce768e57ab0b2eb4d442ce22f9fa894c66de63685`, externally through root's
coordinated freeze. The runners did not embed this manifest. The packet and this review do
not turn that external binding into a new execution-time filesystem attestation.

The corrected ten-input harness manifest is
`c7bc4d25a02cc0d0289d930cb4dbaa58a0f8ca0bdc1a529164599588a14d7e32`.
The first attempt's executed spec and README hashes differ from that corrected packet and
are explicitly retained as historical inputs. The subsequent desktop and mobile receipts
match the corrected inputs. Product behavior was not changed to satisfy the opacity test.

The earlier independent harness/result reviewer was `/root/workspace_social_audit`; the
packet author was `/root/qa_inventory`. This separate canonical review evaluates their
retained evidence. Visual conclusions below rely on the separately authored result review,
SHA-256 `45da7efe1254b65f05995fc8254e33234258fd6d5496418a155b506d8d82567c`, whose reviewer
personally examined the depth, strength, visibility and date frames, final desktop page and
mobile canvas crop. This lane did not repeat those image inspections or execute any browser,
test, service, database, network or cleanup operation.

## Reconciled outcomes

| Attempt | Report outcome | Field responses | Cleanup completion UTC |
| --- | --- | --- | --- |
| Desktop `22-52-39-624Z-cc1a7ec9` | One failed, zero passed/skipped; 36.806 seconds | Four temperature depths, 280 Polygon cells each, rung 5 | 22:53:23.227 |
| Desktop `23-01-44-370Z-a5e619e2` | One passed, zero failed/skipped; 28.085 seconds | Seven temperature/VPD variants, 280 cells each, rung 5 | 23:02:18.724 |
| Mobile `23-05-16-541Z-e59bddca` | One passed, zero failed/skipped; 21.698 seconds | Seven temperature/VPD variants, six cells each, rung 9 | 23:05:44.914 |

The first failure expected zero opacity although the actual range minimum and documented
contract are 0.05. Invisible but hit-testable features are intentionally avoided; visibility
is controlled separately. The corrected journey tests Home at 5 percent, End at 100 percent,
and visibility off/on while preserving day and strength. Its pass does not erase the first
failed execution. VPD, previous-day, latest and corrected visibility behavior were not
reached in that first attempt.

Independent body parsing confirmed published availability, exact requested/observed day,
matching measure/depth, Celsius for temperature or kPa for VPD, false truncation, finite
feature values, Polygon geometry type, and equality of declared cell count and actual
feature count. Successful projects each contain September 5 temperature surface, root-zone,
deep and substratum; September 4 substratum; and VPD surface for September 5 and September 4.
This checks geometry type and response coherence, not full polygon topology or scientific
validation of the measured values.

Desktop uses bbox `[-126.955078,45.407227,-119.044922,50.592773]`; mobile uses
`[-123.267792,47.458923,-122.732208,48.541077]`. Their different extents and rungs explain why
the observed counts are not a conservation comparison. Returning to latest can reuse a
previous response; a restored display is not counted as a fresh upstream request.

Bridge attempt/admission/completion totals reconcile to 5, 8 and 7 requests, including
coverage where requested. Rejections are zero. All retained upstream responses are complete
HTTP 200 with worker exit zero, no timeout, and matching body hashes/sizes. Browser blocked
requests, failed requests, page errors and evidence errors are zero in all three attempts;
the first primary assertion is still a failure. This anonymous slice includes no identity,
model invocation or application mutation.

Each runner records two immediately revalidated owned-process stops and four already-exited
processes. All three have empty remaining/unverified owned-process sets and independently
record closed application port 3128 and bridge port 3138. Successful cleanup is accepted
separately from test success, including for the first failed attempt.

## Visual limits and disposition

The separate visual review supports changed desktop Celsius labels across the four depths
and the documented strength/visibility controls. It also establishes important limits:

- The open manager covers essentially the entire 390 by 844 mobile canvas. These frames
  establish controls, legends/rungs and selected dates, not unobstructed mobile values or
  date-summary presentation. Emulated touch plus injected keys is not physical-device QA.
- Temperature and VPD are enabled together, and sampled labels remain Celsius. Typed kPa
  responses establish VPD data delivery, not isolated VPD numeric-label rendering.
- Desktop `06-both-latest.png` contains a transient mixed-date summary. Associated JSON and
  the final page settle to September 5 for both layers. This is neither a proven persistent
  defect nor evidence that every screenshot waited for settled paint.
- Full history, all rungs, hover values, full T07 disagreement, dense production basemaps,
  all fonts, and complete accessibility remain outside this slice.

PGQA-L12/L13, T01/T02/T12 and M05 remain partially covered. The appropriate next visual
variant closes the manager through the actual UI, waits for settled dates and isolates VPD
before capturing desktop/mobile labels. The existing [defect ledger](defects.md), including
the separately reproduced date-summary defect, is not cleared by these narrower results.
Overall platform QA remains **RED**, with no whole-case promotions.
