---
type: track-evidence
track: multiscale_polygon_surface_20260901
observed_at: 2026-09-12
status: local-candidate-refusal-repair
review_base: fe9098ae045dda7a74d8f955dcc8c8e097ea09c9
repair_base: f62666363abd06e8c4aab287ad5f7353b1ec305d
---

# Bounded vegetation scalar field candidate

## Read-only preflight and audit reconciliation

The isolated worktree began clean at detached HEAD `fe9098ae045dda7a74d8f955dcc8c8e097ea09c9`,
equal to local default branch `main`. `origin/HEAD` named `origin/main`; the locally cached
remote-tracking ref was `fa202230958fb55521963e886eb031be5fc266c4`. No fetch, push, merge,
Railway access, production request, or data-plane operation was performed. The local candidate
branch is `codex/scalar-field-vegetation-20260912`.

The archived audit was recovered read-only from task
`01a04997-04fb-7a31-9628-b46ace71f415`, the preserved scalar visual planning task. Its final
audit used base `9f1cd8613e39e15e55f6f2d30576e0074401067d` and identified eleven scalar-dot
views across vegetation, weather and climate. Several recommendations are superseded by
the current code and Conductor plan:

| Archived proposal | Current evidence and disposition |
| --- | --- |
| Replace measured NDVI dots | September 2 already replaced them with declared polygon supports. Preserve those geometries and add optional seam-free value paint plus inspection cues. |
| Bilinear NDVI across same-day neighbors | Current `src/lib/map/AGENTS.md` and render contract permit only measured tessellated cells. This slice uses exact nearest-cell values, with no smoothing or inferred footprints. |
| Add weather temperature and mobile inspection | Current weather already renders declared aggregate cells and raw sampled-model points with separate wind symbols. Shared tooltip already supports touch. Leave weather untouched. |
| Replace climate Points and add scalar labels | Current climate/soil render declared cells/isobands and September 12 added numeric labels. Keep them; the climate surface-slot proposal is deferred. |
| Default-off family flag and native fallback | Retained, allowing only vegetation in this slice. |
| Value-driven paint, missingness, negative NDVI, day boundaries | Retained. No sample-count accumulation, universal land mask, interpolation, or missing-data substitution. |

This is the smallest vegetation-first vertical slice of the reusable renderer. It does not
claim to implement the archived audit's full cross-product smoothing proposal or close M3.
The runtime implementation, focused tests, fixture, and directory docs are bound by the
enclosing commit; its exact commit/tree and changed-file manifest are in the final handoff.

## Compatibility review and bounded refusal repair

Compatibility reviewer task `01a0949c-ff3f-72e1-96b8-f49cf11b5272` returned
**CHANGES REQUESTED** on candidate `f62666363abd06e8c4aab287ad5f7353b1ec305d`, tree
`10d00468d1c3d621814f7111056a39ce3ca24865`. Its independently reproduced P2 showed that
an empty/refused replacement during label relayout cleared the custom mesh but queued the
native-source clear until global map idle. Old cells, labels and inspection remained available
beneath the reader's refusal notice. Twenty move/source/style events did not submit the clear.
The original source approval below preceded this compatibility finding and does not override it.

The repair starts from that exact preserved candidate on
`codex/scalar-field-refusal-repair-20260912`. It immediately suppresses invalidated measured
paint and inspection while retaining the layout/data serialization boundary. The queued source
still converges after idle, and normal populated replacements retain latest-wins behavior.
No reader, data service, backend, release gate or deployment behavior is changed.

The [repair receipt](scalar-field-refusal-repair-20260912/verification.txt) records the separate
source review and focused verification. Its
[changed-file manifest](scalar-field-refusal-repair-20260912/changed-files.txt) is relative to
`f62666363abd06e8c4aab287ad5f7353b1ec305d`; its browser report and screenshots cover only the
selected repair cases. The original 49-path manifest, 27-case receipt and screenshots remain
frozen evidence of `f626663`, rather than being relabelled as results on repaired source.
The enclosing repair commit binds the final files; exact commit/tree IDs are in the handoff.

## Original candidate verification (f626663)

The original candidate had independent source approval and a strict synthetic browser receipt.
The authoritative run is `run-2026-09-12T08-26-05-375Z-53204`: **27 cases passed, zero
failures**, with identical before/after SHA-256 hashes over the declared eleven-file source
manifest. Hash input is UTF-8 with canonical LF endings, matching Git's text normalization.
The [browser receipt](scalar-field-vegetation-20260912/report.json) and all 27 PNG captures
are preserved beside this packet. Each run serves its own bundle and writes a unique receipt.
Earlier shared-directory runs, including a diagnostic label-query fallback, are excluded from
acceptance evidence. The final runner uses direct rendered-label and unrestricted point
queries as hard gates and fails if source changes during execution.

| Check | Result and scope |
| --- | --- |
| `npm run check:data-boundary` | Passed: 12 documented URL rules, restricted imports, and observation-fabrication rules. |
| `npm run type-check` | Final corrected source passed. |
| `npm run lint` | Final corrected source passed: 0 errors, 545 warnings. |
| `npm run test:changed -- --base fe9098ae045dda7a74d8f955dcc8c8e097ea09c9` | Selector conservatively ran the full suite because the new e2e fixture is unclassified: 150 files passed, 2 skipped; 2,222 tests passed, 13 skipped, plus 6 tooling tests passed. |
| Five affected Vitest files after the final tooltip/mock correction | 5 files and 113 tests passed. This is a scoped follow-up, not another full-suite run. |
| `node e2e/scalar-field-fixture/run.mjs` | Final frozen-source browser run: 27 cases passed, including actual tooltip bounds. |
| Independent review | Source/lifecycle review and final screenshot review approved; no remaining actionable finding within this slice. |

The full suite preceded a final correction to tooltip placement and the component mock's
MapLibre visibility type. Full type/lint checks, all five affected test files, and the full
27-case fixture then passed on the corrected source. The scoped files are
`HoverTooltip.test.tsx`, `VegetationLayer.test.tsx`, `hover-fields.test.ts`,
`scalar-field.test.ts`, and `scalar-field-layer.test.ts`. The thirteen full-suite skips are
nine climate SQL and four PostGIS cases without explicit test DSNs. No database was started.
Compact executable output is preserved in
[verification.txt](scalar-field-vegetation-20260912/verification.txt).

Browser gates cover negative values and exact ramp colors, a hard support edge, duplicate PNG
invariance, half-alpha composition, a missing center, transparent outside support, empty
clearing, mixed-day/unit native fallback, globe/pitch fallback, native labels, unrestricted
picking, and exact desktop hover/mobile tap metadata. The direct populated field → detail →
reload sequence passes. Source switches during an actual in-flight data load expose the new
−0.4 value afterward. Mobile captions measure 240 × 154 pixels at x=0, y=436 within a
390 × 844 viewport; desktop captions also remain within their viewport.

The browser investigation corrected actual SDK and lifecycle defects: the installed MapLibre
projection input is `mainMatrix`; style replacement can temporarily lack a projection;
native source data and symbol/layout reparses must be serialized in both orders; and GPU
bindings must be preserved and owned resources unbound before deletion. Only map idle
releases the layout lock, latest queued data wins, and source identity blocks stale-style
writes. Native data readiness gates GPU ownership. Current-caption measurement and coordinate
clamping fix the independently observed mobile tooltip clipping.

The fixture uses Chromium/SwiftShader, local synthetic data, local ASCII glyphs and a blank
basemap. External requests are blocked and fail the run. This is functional and pixel evidence,
not production-data or hardware acceptance. Dependencies were installed locally from the
unchanged lockfile with `npm ci --ignore-scripts --no-audit --no-fund` (730 packages).

The [changed-file manifest](scalar-field-vegetation-20260912/changed-files.txt) enumerates the
whole candidate, including evidence. The enclosing commit binds this packet to its code;
exact commit/tree IDs are provided in the handoff rather than embedded circularly in the commit.

## Remaining visual gates

- Published-day and governed-absence transitions with real reader data, including per-cell NDVI days.
- Cross-product rung conservation and the full default-PNW continuity matrix.
- Dense basemap labels, simultaneous environmental overlays, terrain/globe transitions on real hardware.
- Native desktop hover/mobile tap journeys in the complete application, including empty-ground analysis.
- Cold/warm request-to-paint, response bytes, memory and GPU upload budgets at production density.
- Weather/temperature wind ordering and climate surface ownership if those families are enabled later.
- Cross-browser/mobile hardware screenshots and release acceptance by the owning track.

The build-time flag remains unset by default. This packet authorizes no push, merge or release.
