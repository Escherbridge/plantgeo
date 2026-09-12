---
type: evidence-receipt
track: weather_forecast_experience_20260911
recorded_on: 2026-09-12
status: planning_contract_frozen_admission_open
source_commit: 64f4f892bd2b744cc097c7f76a1f239997b80f52
source_tree: f8697da694a3f9fbbf28e70ff7581bd59546bfd6
---

# Forecast experience handoff and acceptance contract

This documentation-only receipt freezes X0's intended behavior and acceptance
targets. It does not implement X0 or approve a forecast release. The
[data receipt](../../weather_forecast_parquet_lane_20260911/evidence/contract-receipt-20260912.md)
owns source reconciliation, April 28 evidence limits, model-run/valid-time/
interval/support/missingness semantics and the F0–F4/X0–X4 dependency order.
The exact deterministic source and governed response remain unadmitted.

## Current implementation versus the planned experience

The [historical approval](../../platform_experience_qa_20260911/evidence/weather-approval-20260912.md)
is limited to the fixed local desktop unavailable-state path. The current
report labels itself **Historical weather** / **Open-Meteo historical estimates**.
It displays a representative served sample and its distance, not an hourly or
daily forecast series. In
[WeatherHistoryReport](../../../../src/components/panels/WeatherHistoryReport.tsx)
(lines 139–156), an owned selected point is preferred, otherwise the anchor is
viewport centre. This behavior does not satisfy a forecast requested for an
arbitrary picked/searched place. Current sampled/aggregate glyphs also do not
prove a continuous scalar field or vector-wind product.

The 2025-04-28 screenshot remains unbound to an exact catalogue/reader response.
The future fixture must cover capability unavailable, exact absence, outage,
catalogue/reader disagreement, loading placeholder and delayed old-day results
across map, card and legend. Clear the previous product/time frame immediately
when it cannot represent the selected context. A synthetic regression can prove
this rule but cannot close the original screenshot's factual reconciliation.

## Mode and selected-context contract

| Mode | Time/product behavior | Required presentation |
| --- | --- | --- |
| Now | Current-condition estimate at its actual served instant from an explicitly identified admitted product; if the chosen card shows a forecast's first valid hour, identify it as forecast. | Source, actual time, support and age. No implication that first-valid forecast is an observation. Unsupported current conditions have an explicit missing state. |
| History | Exact selected historical date and identified sampled-history or separately admitted archive/reanalysis product. No fallback between them. | Requested/served date agreement or explicit reader-defined notice/refusal; sampled estimates remain labelled as model estimates. Preserve the existing toggle identity when later relabelling it **Sampled weather estimates**. |
| Forecast | One immutable published run and its available valid instants/intervals. Hourly steps where supported, daily only for summaries. | Model/run, issue if supplied, publication/update age, valid time, interval and timezone are distinguishable. New-run availability offers a whole-context refresh; never splice runs. |

The shared context is product/version, mode, pinned run where applicable,
selected latitude/longitude, timezone, variable/unit and valid instant/window.
Map, tooltip, card, legend, accessible table and agent must echo that context.
Keep requested place distinct from source/sample coordinate and disclose sample
distance/support. No location selection means an invitation to pick/search;
viewport centre must not silently become the selected forecast place.

Hourly cards show up to 24–48 supported hours; daily outlooks show up to 7–10
supported days. These are display targets, not a guaranteed source horizon.
Shorter availability is labelled and never padded. Include temperature,
conditions, interval precipitation amount/probability, humidity and wind/gust/
direction only when admitted. Daily highs/lows, precipitation and wind summaries
need variable-specific aggregation definitions. Sum non-overlapping amount
intervals only; never sum probabilities or average direction angles. Distinguish
source daily summaries from derived ones and display completeness/partial-day
status. Local days use the selected IANA timezone and may contain 23 or 25 hours;
repeated local hours retain UTC/offset identity. Missing inputs cannot silently
produce complete daily cards.

## Scalar, wind and missingness

One selected scalar uses the field/support class admitted by F0/F1. Native fields
respect their domain; derived interpolation declares its method and support
mask; sampled points stay points until a field is admitted. Legends expose
resolution, support, variable and units. A continuous claim requires no cracks,
tile seams or simultaneous zoom rungs within supported coverage. No-data masks
are preserved rather than filled to create visual continuity.

Wind consumes compatible earth-relative `u`/`v` with declared height/time/support.
Static arrows or barbs are required; particles are optional and stay bounded by
support. Pause and reduced-motion choose a static presentation. Test 350°/10°,
opposing cancellation, calm direction, grid rotation and no-data edges. Numerical
wind in the table/card remains available independently of animation.

Exact absence, outside domain, stale run, not generated, upstream unavailable
and numeric zero have distinct text and state. Units, precipitation intervals
and run/valid times remain consistent across every surface and agent answer.
Uncertainty appears only with an admitted probability/member/quantile product.

## Frozen initial acceptance targets

These are design targets awaiting measurement, not achieved performance. The
F0/F1 source-specific budgets in the data receipt remain prerequisites. A target
change requires a dated reviewed amendment before implementation or acceptance.

| Measure | Initial ceiling / pass rule | Evidence method |
| --- | --- | --- |
| Active decoded field data | At most 65,536 cells and 4 MiB per field response; one scalar plus optional wind. | Log cells, serialized and decoded bytes at dense and domain-edge views; oversize must coarsen/disclose or refuse. |
| Resident forecast cache | At most three time slices and 12 MiB decoded field payload total; GPU forecast resources at most 32 MiB, largest texture dimension at most 2048. | Include masks/wind in memory totals; release evicted slices and measure allocation on repeated changes. Separate metadata overhead in the receipt. |
| Requests | At most two active foreground reads (field + location) and one adjacent-slice prefetch; no duplicate read for the same full context key. | Network trace during rapid scrubbing/place/mode/run changes; abort superseded work and prove delayed responses cannot repaint. |
| Location payload | At most 48 hourly + 10 daily rows, 16 admitted variables, 256 KiB decoded response. | Boundary request and unsupported-field/window fixtures; continuation/refusal disclosed. |
| Wind display | At most 4,096 static vectors or 8,192 animated particles, one presentation at a time. | Count rendered instances; zero particles when reduced-motion or pause selects static mode; no crossing masks. |
| Request-to-paint | p95 at most 2,000 ms cold and 250 ms warm for a supported view/time change. | At least 30 samples each, from committed user selection to final corresponding map + card paint; include network time and record cold/warm cache definition. |
| Mobile frame rate | p95 frame time at most 33.3 ms during a 30-second pan/time/wind scenario after initial load. | Record actual device, OS/browser/GPU, viewport/DPR, network and trace; desktop resize is layout evidence only. |
| Cleanup | Zero owned sources/layers/listeners/animation loops/textures remain after unmount/style replacement; bounded cache stabilizes across 20 cycles. | Instrumented allocation counts plus heap/GPU observations; re-add once after style readiness. |
| Geometry and science | Zero unexplained seams inside support, mixed runs, cross-place/time repaint, unit disagreements or fabricated missing values. | Desktop 1280×720 and narrow layout 390×844 at sparse/dense/domain-edge zooms; screenshot/canvas and numerical fixtures on one immutable candidate. |
| Accessibility | Keyboard place/time selection; textual table parity; concise time announcements; at least 44 CSS-pixel touch targets; missingness beyond colour. | Keyboard-only, screen reader, reduced-motion/pause and real touch walkthroughs; table includes units/times/reasons. |

Cold/warm trials must retain failures and missing responses as separate outcomes;
do not drop slow samples or count a loading skeleton as a completed paint. Record
browser/build/device/network and admitted source/run in the final receipt so a
number can be reproduced. Performance success on synthetic data does not replace
real admitted-data scientific/visual acceptance.

## Gate and review handoff

X0 runtime work waits for F3's accepted run/valid-time response and capability
contract, the April regression evidence handoff and reader/renderer ownership
transfers. X1/X2/X3 consume that single contract; X4 waits for them, F4 and the
shared registry/render contract/LayerManager/router owners in metadata. Do not
mark their implementation checkboxes complete for this documentation slice.

The final candidate must cover platform QA cases **PGQA-W01–W08** in
[the case ledger](../../platform_experience_qa_20260911/evidence/cases.md): mode
separation, scalar support, vector wind, location cards, temporal/agent parity,
April unavailable-day regression, units/missingness and accessibility/budgets.
Use the exact case definitions when executing; all remain blocked here.

Independent planning review precedes integration of these receipts. Later
scientific, design, accessibility and agent-parity acceptance is a separate
review of implementation and measured evidence. No provider, runtime, release,
data movement or infrastructure action is authorized by this receipt.
