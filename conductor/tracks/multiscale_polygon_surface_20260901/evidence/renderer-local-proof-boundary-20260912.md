---
type: track-evidence
track: multiscale_polygon_surface_20260901
related_track: platform_experience_qa_20260911
recorded_on: 2026-09-12
status: local-artifact-audit-acceptance-open
source_commit: 843b4b313e03447594b23a67f75c3062b2b1a024
source_tree: 9533bb9e5423240630935df0cd012cd8ead15504
source_task: 01a09475-01c4-7252-8710-a8a57559c919
branch: codex/multiscale-evidence-20260912
---

# Renderer evidence boundary and next proof packet

This source and retained-artifact audit adds no newly executed renderer, browser,
service or test case. The scalar-label subset is integrated and remains useful
within its synthetic scope. **M3 and MS-01 through MS-04 remain open.** Neither
source inspection nor recomputing pixels from old screenshots establishes live
or production behavior. The [multiscale specification](../spec.md), lines 32–47,
and the separately preserved remaining acceptance matrix, lines 143–155, remain
the governing gates. The matrix is retained at audit commit
`ad7ae728b48d975bca248187716ad03896506313`, tree
`b3973d15965061496b6a6fac0fe297ba0ab80bd6`, path
`conductor/tracks/platform_experience_qa_20260911/evidence/remaining-acceptance-matrix-20260912.md`.
That audit commit and file are excluded from this integration; this custody
reference replaces the source packet's local link.

## Custody and reconciliation

The frozen local intake is commit `843b4b313e03447594b23a67f75c3062b2b1a024`,
tree `9533bb9e5423240630935df0cd012cd8ead15504`, in
`C:/Users/atooz/.codex/worktrees/3b59/plantgeo`. No remote revision was fetched.
The source/artifact audit belongs to the coordinating `/root` lane; this document
was authored by `/root/executor`. `/root/renderer_audit` and
`/root/interaction_audit` supplied read-only source findings and owned no writes;
`/root/renderer_packet_verifier` reviews the completed packet without authoring it. The
enclosing commit, final tree and verifier verdict belong in the final handoff.

The scalar owner commit is `2b29d9f1ad475358e96fc6c0e48aabd9a3e7d29b`, tree
`878a071801f472a9cbcd3cb02eb06c2845d5d3c4`. Its integration commit is
`ac4ce702c4f5910b3f291b5704ec6d0f4dda5ac5`, tree
`62ced60f82a228010bf3d5e6f0fd2de538698adb`; the coordinator verified ancestry
into this intake. The [registry](../../../tracks.md), lines 23–26, already records
the integrated slice. The [platform plan](../../platform_experience_qa_20260911/plan.md)
still calls it an author handoff awaiting integration in its final checkpoint.
The platform integration owner should correct that stale intake wording against
these identities, while retaining Q1/Q3 and production acceptance as open.
This packet does not change the platform plan, registry or shared task ledger.

The three scalar runtime files still match the SHA-256 identities in the
[original receipt](scalar-labels-20260912/README.md), final section: the shared
measured-value helper and climate/soil components. WeatherLayer has changed since
the capture candidate. This is a three-file identity match, not identity of the
entire runtime, dependency graph or historical weather smoke implementation.

The [retained-artifact audit](retained-scalar-artifact-audit-20260912.json) binds
all 26 PNG hashes, the original report/recipe hashes and freshly recomputed RGB
counts. Node built-ins inflate PNG data and reverse scanline filters; the sampled
regions and thresholds match the retained recipe. Every recomputed count matches
the old report. PNG CRCs are not independently validated. These are new checks of
retained image bytes, with no fresh rendering or renewed browser-error evidence.
The [offline audit recipe](retained-scalar-artifact-audit-reproduce-20260912.md)
preserves the exact script used; it requires Node built-ins only.

## What the retained scalar fixture actually exercised

The [recipe](scalar-labels-20260912/reproduce.md), lines 27–45, calls
`root.render(null)` before every case. Its `reload` branch replaces the style
while the component is absent, then mounts it. Its empty and opacity cases also
mount fresh components. Consequently these captures do not prove a mounted
`style.load` recovery, populated-to-empty transition, opacity gesture or recovery
after any of those operations. The recording-map lifecycle test described below
exercises different mechanics and cannot widen the browser receipt.

The retained Chromium/SwiftShader captures use a blank local basemap, device scale
1, 1280×720 and 390×844 viewports, three synthetic adjoining scalar cells, and
z3/7/10/13. Non-local requests were aborted. The 390-pixel viewport is a narrow
desktop-browser viewport, not proof of touch input, mobile device behavior, full
application reflow or physical-device GPU performance. No hover or input action
is present in the recipe. The weather smoke uses one synthetic model sample.

The report records zero page/MapLibre errors for those historical 26 cases.
Only desktop climate z7 at y320/x370–910 and narrow soil z7 at y405/x127–262
have documented seam rows inside adjoining cells: both contain zero background
pixels. The other seam rows are diagnostics, not additional continuity passes.
Empty and zero-opacity images contain only background below the excluded 60-row
title region: 844,800 sampled desktop pixels and 305,760 narrow pixels.
Direct WebGL `readPixels` was unreliable; `readPixelsReliable: false` remains.

At zero opacity the report still records three source features and positive
rendered-label counts (six desktop, two narrow). Background-only PNGs therefore
do not mean picking was disabled. The application opacity floor is 0.05 in
[layer-opacity.ts](../../../../src/lib/map/layer-opacity.ts), lines 36–46;
the fixture's direct zero prop is outside that slider's reachable range.
The isoband case checks label exclusion using synthetic cell polygons; it does
not prove real dissolved-band geometry. Captions repeated by tile clipping or
omitted by collision are not support counts or observation counts.

`sourceBytes` is `JSON.stringify(geojson).length`, a JavaScript string length for
the fixture collection, not measured UTF-8 response bytes, transfer encoding,
compressed wire bytes or a service request. Recorded `renderAndSettleMs` spans
947–1172 ms and includes intentional sleeps. It establishes no request-to-paint,
frame-time or cold/warm performance result.

## Requirement-by-requirement local boundary

All source and test references below were inspected, not executed in this audit.
“Can prove” describes a next bounded local run, not a newly passed acceptance case.

| Requirement | Local source/artifact evidence and next provable slice | Remaining acceptance boundary |
| --- | --- | --- |
| One physical rung | The ladder resolves one of z0/5/9/13; climate forwards the served form/rung. Existing screenshots independently mount z3/7/10/13. A mounted threshold sweep can count active sources/layers and compare painted payload identity. | No retained transition proves absence of simultaneous rungs during asynchronous delivery or rapid zoom. MS-02 remains open. |
| Seams, cracks, nested blocks | Integer-index lattice boundaries and synthetic edge tests support exact mathematical adjacency; two retained PNG row probes corroborate two cases. | Real source-part/batch seams, actual dissolved bands, antialiasing, dense basemap, all products/cameras and mobile remain unproven. |
| Stable support identity | Stored support IDs or rung/position IDs describe support; helper tests distinguish rungs. A fixture can prove deterministic identity for identical inputs and explicitly changed identity across rungs. | Feature IDs `0/1/2` in the old fixture do not test reader IDs, reload/day identity, native feature identity or transport serialization. |
| Conservation | Synthetic fire presentation tests conserve supplied counts through a hand-built fold. Actual producer fixtures can check each declared operator against its base contributors. | No current packet reconciles published cross-product detail/aggregate pairs, omissions, masks or provenance. Scalar means must not be summed as counts. |
| Feature, byte, request-to-paint budgets | Implementation caps exist; the label layer shares its existing source. A local bounded service/fixture can measure feature counts, UTF-8/wire bytes, request identity and paint milestones. | No predeclared numeric acceptance budget or dense cold/warm service-backed measurement exists in this packet. MS-04 remains open. |
| Hover and touch | Shared hover helper/recording-event tests cover registered layers. Climate/soil IDs are absent from the shared registry. | A feature owner must supply their selection contract and implementation before a painted-support/value parity journey can pass. No scalar touch/hover capture exists. |
| Style reload | Components register `style.load`; a recording-map test rebuilds current layers/props after a simulated event. | The old browser reload unmounts first. Real mounted style replacement, cleanup, font/sprite loading and restored interaction remain unrun. |
| Opacity | Component paint multipliers and recording-map rerenders are inspected; old zero-prop images are blank. | Slider gestures, reachable 0.05–1 values, hide/show, touch, picking and restoration require mounted application proof. |
| Missingness | Label formatting excludes invalid values and preserves zero; recorded empty mounts are blank. | Populated→governed-empty→populated, missing/refused/error distinctions, stale data and surface/legend/tool agreement need mounted and service-backed variants. |
| Rapid-day recovery | Query/caption/cancellation tests inspect selected versus drawn day, pending/failure states and transport abort wiring. | None measures rapid browser input, out-of-order arrivals and the final painted support/day with a live service. MS-03 stays open. |

The ladder and geometry references are [zoom-tiers.ts](../../../../src/lib/map/zoom-tiers.ts),
lines 25, 58, 299, 368 and 407; [zoom-tiers tests](../../../../src/__tests__/lib/map/zoom-tiers.test.ts),
lines 71–116, 340–378 and 406. The 1,568-cell synthetic lattice sweep covers
vegetation/soil z13/9/5; it excludes z0, where aggregation is required. The
[render-contract tests](../../../../src/__tests__/lib/map/layer-render-contract.test.ts),
lines 498 and 522, and [climate reader tests](../../../../src/__tests__/services/parquet-climate-field.test.ts),
lines 209 and 303, inspect footprint/adjacency and served-lattice band mechanics.
None is GPU or published-data proof.

The [climate component](../../../../src/components/map/layers/ClimateFieldLayer.tsx),
lines 367–437, and [soil component](../../../../src/components/map/layers/SoilFieldLayer.tsx),
lines 198–244, contain lifecycle/data/opacity updates. The
[scalar lifecycle test](../../../../src/__tests__/components/ScalarFieldLabels.test.tsx),
lines 40–70, rerenders a mounted recording map with null data/0.4 opacity,
simulates style reload and checks teardown/form changes. Its map is a fake,
without WebGL. Likewise “paints” in [ClimateFieldLayers tests](../../../../src/__tests__/components/ClimateFieldLayers.test.tsx),
lines 346–402, names prop-level assertions, not rendered pixels.

[Shared hover IDs](../../../../src/lib/map/hover-fields.ts), lines 22–54, omit
climate/soil; neither component supplies its own pointer handlers. The
[HoverTooltip tests](../../../../src/__tests__/components/HoverTooltip.test.tsx),
lines 71–205, use fake mouse/coarse-pointer events. The
[measured-value tests](../../../../src/__tests__/lib/map/measured-value-label.test.ts),
lines 14–20, cover invalid values, real zero, precision and aggregate qualifiers.
[Climate caption tests](../../../../src/__tests__/components/ClimateFieldLayers.test.tsx),
lines 563 and 600, and [metric-at-date tests](../../../../src/__tests__/stores/useMetricAtDate.test.tsx),
lines 512 and 557, support local state mechanics. They do not certify final paint.

Conservation must use each product's declared aggregation and contributor domain.
[Fire derivation](../../../../services/agri-data-service/src/agri_data_service/warehouse/schemas/fire_detections.py),
lines 156–161, sums detection/FRP-contributor counts and FRP totals; all-null FRP
must stay null. [Vegetation](../../../../services/agri-data-service/src/agri_data_service/warehouse/schemas/vegetation.py),
lines 92–100, averages metric values and sums release counts. Intensive scalars
need the declared mean/weight/support semantics and sufficient statistics where
applicable; summing scalar means or silently introducing weights is not valid.
[Fire presentation tests](../../../../src/__tests__/lib/environmental/parquet-fire-presentation.test.ts),
lines 343 and 428, fold six synthetic detail cells in TypeScript, not the actual
producer. [Actual derivation tests](../../../../services/agri-data-service/tests/parquet/test_tier_derivation.py),
lines 116–146 and 226, cover grouping, null sums, unlocated exclusions and area
roll-up; line 304 is one-row-per-lane smoke, not cross-product conservation.

The current [fire presenter](../../../../src/lib/environmental/parquet-fire-presentation.ts),
line 162, retains cell metadata at detail as count-scaled cell points. Those are
not raw individual detections, so the spec's raw-detail gate cannot be closed by
calling the point shape sufficient. Preserve detection-density terminology,
native polygon identity, vegetation's discrete 0.25-degree support and the
recorded soil-survey coarse-summary deviation.

[Reader limits](../../../../src/lib/server/services/environmental-read-model.ts),
lines 1261 and 1834, cap soil at 4,000 rows and climate at 512;
[parquet-plane-client.ts](../../../../src/lib/server/services/parquet-plane-client.ts),
line 80, declares a 16 MiB row-response cap. These are implementation limits,
not agreed feature/byte/performance acceptance budgets or truncation correctness.

## Exact blockers and next-owner prerequisites

1. **Local renderer owner:** supply pinned frontend dependencies and Chromium,
   preserve the three-cell recipe as historical evidence, and add a separate
   mounted fixture. Sweep 4.999/5/5.001, 8.999/9/9.001 and 12.999/13/13.001
   in both directions without remounting; record source/support/rung identity,
   active layers, errors and region-specific pixel masks through each transition.
2. **Local renderer and QA owners:** retain one mount through populated→empty→
   populated, style replacement while populated and empty, opacity 1→0.05→1,
   hide/show and teardown. Add actual mouse, keyboard and touch actions;
   climate/soil interaction ownership is a prerequisite, not a test-only repair.
   Introduce delayed A/B/C day responses and prove the final selected/drawn day,
   stale indication, abort behavior, support and paint after reversed arrivals.
3. **Reader/scientific owner:** construct bounded synthetic base/detail fixtures
   through the actual lane derivation, including unequal contributors, nulls,
   duplicate/release identities, source-part boundaries and declared omissions.
   Compare product-specific operators at every rung. DuckDB spatial fixtures
   attempt `INSTALL spatial` if `LOAD` fails (derivation test lines 73–80);
   use a preinstalled extension or grid-only subset for an offline run, and
   report every excluded geometry case. Do not accidentally download extensions.
4. **QA/performance owner:** freeze numeric feature, decoded/wire-byte, request,
   request-to-first-correct-paint and frame budgets before execution. Name camera,
   device/browser/GPU, DPR, dense basemap, payload, repetitions and cold/warm cache
   state. Instrument request start, response completion, accepted day/rung and
   first correct painted frame; include absent/error/cancel variants.
5. **Publication/reader and service owners:** provide a separately authorized,
   isolated service packet with exact source/tree/configuration and immutable
   release/support/absence identities. Supply populated historical/newest eligible
   and governed-empty days, all required climate/soil depth/statistic intersections,
   detail/aggregate pairs, bounds/truncation and provenance. Production evidence
   must come from its authorized owner; this task accesses none of those systems.
6. **Desktop/mobile QA and independent verifier:** run the complete application
   against that frozen service packet on named desktop and mobile targets; retain
   emulation and physical-device results separately. Capture dense-basemap seams,
   labels/legend/details/tool parity, hover/tap support selection, style/opacity/day
   recovery, reflow/focus/gesture behavior, network timings and evidence hashes.
   Submit the exact reviewed renderer packet to production acceptance only with
   every remaining MS row's outcome; unresolved cells remain blocked or not_run.

No dependency packages were installed and no browser was launched in this audit.
This worktree lacks dependencies; the standard main checkout's `node_modules`
also lacks the required tsc/Vitest/esbuild/Playwright/MapLibre toolchain. A future
local code change should apply its complete fix batch before one final sweep per
[testing policy](../../../../docs/testing.md): boundary/type/lint plus the selected
surface, with `map` and `parquet` batches and explicit
[request-budget](../../../../src/__tests__/lib/net/request-budget.test.ts) and
[bounded-upstream](../../../../src/__tests__/lib/server/http/bounded-upstream.test.ts)
coverage where those transport claims are exercised. These are proposed checks,
not executions or a quality receipt. No Railway, production data/storage, writer,
scheduler, deployment, database or live service was accessed; no push is part of
this local packet. Source-only completion does not close acceptance.

## Verification of this evidence-only change

The complete five-path documentation/artifact batch was authored before the
final checks. The coordinator ran these checks once and then recorded results;
no application, test, dependency or shared harness file changed.

| Check | Observed result and limit |
| --- | --- |
| Offline retained-PNG audit | Exit 0; 26/26 decoded images match all recorded screenshot pixel fields. This rechecks old artifacts, not browser behavior. |
| Document and artifact integrity | Exit 0; 35 local links resolve, 31 source/recipe/report/PNG hashes match, new Markdown has OKF `type`, JSON parses, and the preserved reproduction script matches the executed script. |
| `npm run check:data-boundary` | Exit 0; 12 documented URL rules, restricted imports and observation-fabrication checks pass on the unchanged source tree. |
| `npm run test:changed -- --base 843b4b313e03447594b23a67f75c3062b2b1a024` | Exit 0; selector reports `mode: none`, `commands: []`, `full_suite: false` for these five Conductor paths. **No tests ran.** |
| Type checking, lint, Vitest, Python and browser suites | Not run for this evidence-only change. Missing frontend dependencies independently block a fresh renderer run; no inherited/full-suite pass is claimed. |

The separate verifier's reviewed staged tree, whitespace check, enclosing local
commit/tree and final worktree custody are recorded in the task handoff. This
documentation verdict cannot serve as the renderer or platform acceptance verdict.
