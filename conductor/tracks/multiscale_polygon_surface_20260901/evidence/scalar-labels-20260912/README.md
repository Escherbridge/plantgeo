---
type: track-evidence
track: multiscale_polygon_surface_20260901
related_track: platform_experience_qa_20260911
observed_at: 2026-09-12
status: partial-local-renderer-evidence
review_base: 0f4f4821f1700a7d3fc46d09ee4bda6ab25cf985
---

# Bounded scalar-value annotation candidate

The isolated checkout at `C:/Users/atooz/.codex/worktrees/4a6e/plantgeo` started
at local main `0f4f4821f1700a7d3fc46d09ee4bda6ab25cf985`. This packet extends the
integrated weather treatment with numeric labels for existing climate and soil
scalar surfaces. It is a local integration handoff, not multiscale or platform-QA
closure. The enclosing Git commit binds the implementation and this evidence;
the final task response records its exact commit and tree.

## Intake and scope

The owner inspected the multiscale spec/plan, layer registry, render/support
contracts, current weather implementation, September 3 canvas captures, and the
integrated weather receipt and September 11 unavailable-state browser receipt.
The `/root/renderer_audit` lane independently inventoried the remaining forms.

| Surface | Finding and bounded disposition |
| --- | --- |
| Weather | Existing integrated code already labels temperature and wind and supplies a historical report. Retained unchanged. Its earlier live browser evidence only covers unavailability. |
| Climate and soil | Existing declared polygons already carry truthful scalar values and units, but have no numeric labels. Add one shared collision-aware symbol layer per eligible component, reusing the same source. |
| Climate isobands | Their `value` is a band representative. Keep range legends and omit numeric measurement labels. The retained legacy climate symbol branch can label its served scalar values. |
| Vegetation | Fixed 0.25-degree support must remain discrete. Numeric annotation could be a later bounded task; no smoothing or new renderer is justified here. |
| Water and fire | Existing mean-discharge and detection-count captions/picking express the aggregate meaning. Do not encode density as measured discharge or detection cells as perimeters. |
| Soil survey | The registry explicitly records the coarse counted-point summary as a deviation. Source geometry/reclassification needs its owner's contract decision. No generic renderer can repair it truthfully. |

Labels use environmental definition units, the served `aggregated` flag for an
`avg` qualifier, and appropriate sub-unit precision. Missing/non-numeric values
produce no label. Real zero is retained; tiny nonzero values use a threshold
caption instead of rounding to a false zero. Existing geometry, missingness,
legends, source caps, selected-day behavior and the governed Parquet/PostgreSQL
boundaries are unchanged. Climate/soil have no shared hover registration today;
that pre-existing requirement remains open, and this packet claims no new picking.

## Browser and screenshot evidence

`canvas-report.json` and the 26 PNGs were captured from the actual React
ClimateFieldLayer, SoilFieldLayer and WeatherLayer components in Chromium,
with SwiftShader, a blank local basemap, device scale 1, and all non-local browser
requests aborted. Viewports were 1280x720 and 390x844. These are synthetic renderer
fixtures: three adjoining scalar cells built with the repository's actual
`servedCellLattice` and `tessellatedCellPolygon`, not observations or reader data.
Climate values were 0, 12.5 and 24; soil moisture values were 0, 0.18 and 0.31.
The weather smoke case uses one synthetic raw model sample. No data service,
production endpoint, database, source loader or active load was accessed.

For each viewport, captures cover climate and soil at z3, z7, z10 and z13,
then climate empty, soil zero opacity, climate style replacement, climate
isoband label exclusion, and a weather raw-sample smoke case. The isoband
fixture only tests label exclusion by form; its cell polygons do not certify
real dissolved-band topology. Existing reader code still supplies the real form.

All 26 scenarios recorded zero MapLibre errors and zero page errors. Numeric
labels appeared at every tested field zoom. At low zoom some labels are omitted
by collisions; at high zoom MapLibre can repeat the same cell's label across tile
clips. Neither rendered-label count nor repeated captions are observation counts.
The one extra symbol layer adds no source features, requests or aggregation.

The direct WebGL `brightPixels` probe returned unusable zero values and is retained
with `readPixelsReliable: false`; it is not acceptance evidence. PNGs were decoded
into a separate 2D canvas for the `screenshot*` pixel fields, excluding the first
60 rows containing the synthetic-fixture title:

- Desktop empty and zero-opacity images: all 844,800 sampled pixels exactly equal
  the background, and zero bright pixels.
- Mobile empty and zero-opacity images: all 305,760 sampled pixels exactly equal
  the background, and zero bright pixels.
- Desktop climate z7: 629 bright text/halo pixels. Its interior row y320, x370–910,
  crossing the three adjoining cells contains zero background pixels.
- Mobile soil z7: 285 bright text/halo pixels. Its interior row y405, x127–262,
  crossing the adjoining supports contains zero background pixels.

The report includes seam probes for every screenshot, but only the two rows above
are positioned inside adjoining cells; the other row counts are raw diagnostics.
These two probes do not establish the full cross-product continuity matrix.
The approximately 0.95–1.17 second `renderAndSettleMs` includes intentional fixture
waits and local placement; it is not a live request-to-paint performance budget.
Full published-rung conservation, production density/response sizes, cold/warm
requests, dense basemap label interactions, live selected-day transitions, hover,
touch journeys and mobile application reflow remain unproven.

## Independent review and validation

Authoring was delegated to `/root/executor`. A separate `/root/verifier` reviewed
the complete runtime/test/documentation diff against reader value/aggregation/unit
contracts and independently inspected mobile soil z3 and desktop climate z13 PNGs.
It approved the bounded source change with no blocking findings, explicitly
retaining collision, repeated-label, live-data, hover and performance limitations.
The reviewer made no edits and did not repeat the owner's final test sweep.

The final source sweep used the exact review base above:

| Gate | Result |
| --- | --- |
| `npm run check:data-boundary` | PASS: 12 documented URL rules, restricted imports and observation-fabrication checks. |
| `npm run type-check` | PASS. |
| `npm run lint` | PASS after temporary fixture cleanup: 0 errors, 553 existing warning-only findings. |
| `npm run test:changed -- --base 0f4f4821f1700a7d3fc46d09ee4bda6ab25cf985` | PASS: 6 related files / 118 tests; contract batch 9 passed files, 2 skipped files, 213 passed tests and 13 skipped tests. Scoped receipt, not a full-suite pass. |
| `git diff --check` | PASS. |

All source/test fixes were applied before this sweep. The first lint invocation
also scanned the task's temporary `.omc/research/scalar-canvas/bundle.js` and fixture
sources, producing 103 errors and 3,388 warnings. Those temporary files were removed
after preserving the complete recipe in `reproduce.md`; only lint was rerun, and
it passed. No source changes were made after the passing type/boundary/test checks.
The original sandbox prevented esbuild from traversing dependency ancestors;
local fixture and validation processes ran with approved filesystem access.

The 13 contract skips are 4 PostGIS cases and 9 climate SQL cases without explicit
test DSNs. No database was supplied or started. No Python paths changed and no
Python quality receipt is claimed. Existing frontend dependencies were reused via
a local junction, with no package installation. Temporary fixture server/browser
processes exited at capture completion.

## Root integration handoff

Integrate the enclosing commit from `codex/scalar-value-labels-20260912` onto the
root candidate. Reconcile the two track-plan appended checkpoints if the root
has updated those shared files. Runtime ownership is limited to the shared helper
and the two scalar components; the rest is regression tests and documentation.
After integration, use the changed-surface selector against the root's actual
integration base and retain these synthetic evidence limits. Both tracks remain
active. No remote push, release, deployment or data-plane mutation is authorized
by this packet.

## Captured runtime file identities

- src/lib/map/measured-value-label.ts: b701b7e9e338891ef5197b27502f4ffac0456d5a6a371fee2252b996c0a3d4c1
- src/components/map/layers/ClimateFieldLayer.tsx: 8f21a35f988f940ddbd7ff11c190ad981d46cfc7baea4618ed722e8969c35f3b
- src/components/map/layers/SoilFieldLayer.tsx: 4bb7ac7c5f1055376fdfc3167c5a49dca04b22fd2d9fc7c738b6376c3bb9ffc9
