# Standalone scalar field fixture

Run `node e2e/scalar-field-fixture/run.mjs` from the repository root after installing
the locked npm dependencies and Playwright Chromium. This runner bundles the actual
`VegetationLayer` and `HoverTooltip` with the vegetation flag enabled and starts an
ephemeral HTTP server bound to 127.0.0.1. It does not start Next, call application
APIs, or read production data. External browser requests are blocked and reported
as failures. Build, screenshots, and the JSON receipt live in the ignored
`.tmp/scalar-field-fixture/run-<timestamp>-<pid>/` directory printed by the runner.
Each run serves its own bundle and writes its own receipt; concurrent diagnostic
runs cannot overwrite another browser's code or evidence. SHA-256 hashes before
and after the run cover the declared renderer, integration, fixture, and lockfile
manifest. Any change to those files fails the run. The command exits nonzero on a
failed gate.
Hashes use canonical UTF-8 text with CRLF normalized to LF and record
`sourceHashEncoding: "utf8-lf"`, allowing comparison with Git's committed text
blobs when the checkout uses Windows line endings.
For a harness diagnosis only, append `--scenario=detail --device=desktop` to run
one case; a selected case is not a full fixture pass. The full run is 29 cases:
13 on each device and an additional direct field-to-detail-to-reload desktop
sequence with distinct screenshot names. This retains the short transition that
exposed the SDK symbol-index race during development.
The mode-race case replaces the displayed negative value with -0.4 and switches
measured to satellite and back synchronously while source data is in flight.
It requires the rendered-label and unrestricted-click queries to remain safe and
the actual mouse/touch tooltip to display the new value. No composite period is
set, so the satellite selection introduces no raster request.
The `refusal-layout` case runs both empty-collection and null replacements on
each selected device. It first proves a valid custom field, then temporarily
intercepts only this map instance's `idle` dispatch while zooming into native
detail. A recorded SDK idle attempt confirms native symbol placement settled;
the controller's actual `layoutPending` lock remains set because listeners did
not receive idle. The runner opens the actual mouse/touch tooltip before each
replacement and asserts that it clears and cannot reopen before idle release.
During that interval the old nine-feature source must remain queued, the custom
mesh must be empty, fill/outline/text paint must be zero, and every captured canvas
pixel must equal the background. Raw native query rows can remain while their
serialized replacement waits; the shared inspection gate must reject them.
Restoring the original idle dispatcher must converge to an empty native source;
a later valid -0.4 replacement must restore labels and real inspection. Each cycle
records before/suppressed/recovered screenshots and all phase diagnostics in the
receipt. `node e2e/scalar-field-fixture/run.mjs --scenario=refusal-layout` is the
minimal two-device repair check; adding `detail,mode-race` retains adjacent
inspection and source-switch coverage. Neither selection is a full fixture pass.
The bounded repair evidence selection is
`node e2e/scalar-field-fixture/run.mjs --scenario=field,mixed-days,detail,mode-race,refusal-layout,reload`
(12 cases across both devices).
Every inspection interaction also checks the actual tooltip bounds against all
four viewport edges. The fixture applies border-box sizing to match Tailwind's
preflight; visibility alone does not prove a caption is readable without clipping.
Comma-separated scenarios select a sequence in the normal case order. Optional
`--gl-diagnostics` records nonzero errors and stacks from existing WebGL `getError`
calls, returning their original values unchanged and performing no extra GL reads.

The synthetic 3-by-3 quarter-degree grid includes negative NDVI, a missing center,
identical duplicated cells, and mixed observation days and units. PNG samples assert exact
ramp colors, fractional alpha blending, a hard shared edge, transparent missing/outside support, duplicate
invariance, and complete clearing after empty data. Desktop and touch viewports
exercise native high-detail numeric labels and the actual metadata tooltip. Style
replacement, globe projection, and pitched cameras exercise lifecycle and native fallback.

Pixel captures temporarily hide the fixture title and tooltip root using Playwright's
screenshot stylesheet. Full evidence screenshots retain both. An element screenshot
alone includes overlapping DOM, which would contaminate empty-canvas pixel counts
and compare different scenario titles in duplicate-invariance assertions.
Ordinary cases wait for MapLibre's `idle` event with an eight-second limit before querying
labels. Its `loaded()` method does not include pending symbol placement, and can
leave the feature index stale immediately after a data-and-zoom update.
An idle listener can begin the next serialized update, so the fixture checks
`loaded()` again in a microtask after all idle listeners and waits for another
idle if needed, retaining the same overall timeout.
Direct label queries and the unrestricted point query used by the map's click
path remain hard gates, including the transition from an empty source to detail.

The local glyph protocol generates ASCII SDF glyphs with MapLibre's existing
TinySDF dependency and the machine's Arial font. This preserves the native symbol
and collision path without downloading glyphs. Tooltip CSS is a small fixture
stylesheet, not the application's Tailwind build. These are functional pixel
assertions within the same run, not cross-machine image snapshots or a claim
about production font layout. SwiftShader Chromium verifies one WebGL2 path;
hardware browsers, terrain, unsupported WebGL2, forced shader/upload/context loss,
real basemap contrast, dense data/performance, and other scalar layers remain
separate visual gates.
