---
type: evidence
status: active
recorded_on: 2026-09-15
---

# Session 19: Carried review findings from the renderer-admission batch

Starts from deployed `a112a754731d`. This session fixes exactly the four non-blocking findings
the Session 18 independent review raised and which were deliberately left unfixed so that applied
source matched reviewed source. It changes no admission contract and adds no new behavior.

## Ownership

| Role | Owned work |
| --- | --- |
| Coordinator/integrator | Intake, the single integrated sweep, canonical records, Git and deployment |
| Author | `SoilFieldLayer.tsx`, `ClimateFieldLayer.tsx`, `ScalarFieldLabels.test.tsx`, `VegetationLayer.test.tsx`, `AGENTS.md` |
| Independent source reviewer | Adversarial review of the working tree; no source writes |

The author ran no tests, lint or type checking. A separate live-harness lane ran concurrently and
wrote only to git-excluded research paths; it shares no file with this batch.

## Corrections

**SoilField's listener is now structurally once-per-map rather than incidentally so.** Its
`addLayers`/`removeLayers` were memoised on `[ids, fillColor, measure]`, so those callbacks
changed identity whenever `measure` changed, which re-ran the `style.load` listener effect,
which tore down and re-registered the handler at the back of the queue. Because MapLibre stacks
later-added layers above earlier ones sharing a `beforeId`, that would have inverted layer
stacking. The changing values now live in the existing props ref and both callbacks are memoised
with an empty dependency array, so the listener effect depends on map identity alone. The measure
rebuild that the listener effect previously performed as a side effect is now explicit in the
admission effect, whose cleanup removes the **captured** outgoing ids rather than the ref's
already-updated incoming ids.

A regression pins this: rendering one measure, registering a neighbouring listener, then
rerendering with a different measure asserts that `off("style.load")` is never called and that
registration order is unchanged. The reviewer confirmed by dependency-chain analysis that this
test genuinely fails under the previous shape and is not tautological against the new one.

**The `getStyle()` inconsistency is resolved against the library's actual behavior.** Both files
previously called `getStyle()` bare at the admission site and wrapped the identical call in
`try/catch` ten lines later in the data effect. MapLibre v5 does not throw: `getStyle()` returns
`this.style.serialize()`, and `Style.serialize()` returns `undefined` before load by design, with
a source comment saying so. The reviewer verified this citation independently against the copy in
`node_modules`. The two call sites are unified behind one small `hasParsedStyle` helper per file,
guarded in the safe direction, with the evidence recorded at the call site.

**`AGENTS.md` now states the invariant with its mechanism** — empty-dependency memoisation with
changing values read through a ref — rather than asserting a result the code did not guarantee.
No exception clause is needed because the SoilField gap is fully closed.

**Vegetation's composite-raster assertions now report a skip as a skip.** They previously
early-returned when the period was unpublished, which would have shrunk the raster half of the
raster/measured exclusivity contract silently on a green tick.

## Integrated verification

One sweep over the complete batch. Data-boundary, full type checking and lint passed; lint
reports zero errors and 9,887 warnings, unchanged from baseline. The frontend suite passed
**193 files and 2,593 tests** in 189.13 seconds with **zero skipped**, plus twelve tooling tests,
exit zero. Zero skips confirms the new `skipIf` is not currently masking anything. No Python
source changed and no Python suite or receipt writer was rerun.

## Independent review

**APPROVE**, no blocking or major findings. The reviewer confirmed the empty dependency arrays
are real and complete by tracing every free identifier in both callbacks, enumerated four
transition orderings of the relocated measure rebuild without finding a path that leaves layers
removed-and-not-re-added, double-added, or removed under the wrong ids, and verified the claimed
double-remove idempotence in `safeRemoveLayerAndSource`'s source rather than on assertion.

Three findings are carried forward unfixed so applied source remains exactly what was reviewed:

1. **`ClimateFieldLayer`'s admission cleanup calls `removeLayers`, which reads ids from the props
   ref after React has already updated it.** If `ids` ever changed on a mounted instance it would
   remove the incoming signal's ids — a no-op, since they do not exist yet — and strand the
   outgoing signal's source and six layers on the map permanently. It is inert only because the
   parent renders `ClimateSignalLayer` with `key={signal}`, forcing a remount instead of a prop
   change. **Nothing asserts that key.** `ids` nevertheless sits in the effect's dependency array
   as though a signal change were supported, so a future refactor that drops the key to avoid
   remount flicker converts a correctness invariant into a silent ghost-layer leak with no
   failing test. SoilField is now correct here and ClimateField is not; the fix is one line and
   symmetric.
2. The owned-id list is now stated twice in SoilField — in `removeLayers` and in the admission
   cleanup's necessary hand-inlined copy — so a future fourth layer wired into only one of them
   would be stranded by a measure change, with no production symptom because `measure` is a
   literal at all three call sites.
3. `hasParsedStyle`'s catch can convert a genuine failure into "silently never admits", because
   `serialize()` walks live tile managers and terrain. The reviewer judged this real but
   low-severity, matching the repository's existing precedent, and preferable to an uncaught
   throw inside a React effect.

The reviewer also recorded, for the record only, that writing `propsRef.current` during render is
technically outside React's rules; this is a pre-existing pattern across all five corrected
renderers and the alternative reintroduces a stale-props window, so it should not be changed
without re-deriving the trade-off.

## Acceptance boundary

Fixture-level for the source batch: no fixture here runs MapLibre. Runtime behavior against a
real map and a live basemap swap is covered separately by the live browser evidence recorded
below, which was produced by a different lane against the deployed revision and which supersedes
this section's original statement that listener registration order was unproven. The four other
renderers corrected in `a112a754` were not re-reviewed in this session. **No whole QA case and no runbook
checklist item is promoted; the 220-case matrix is unchanged.**

## Live browser evidence — deployed `a112a754`

A separately owned harness lane ran Playwright 1.62.1 with Chromium against the deployed
production site `https://plantgeo.aevani.com`, read-only: no authentication, no submissions, no
model requests, no server mutation. The harness, its inputs, a machine-readable report and seven
screenshots are retained under
`.omc/research/runbook-20260915-session19/live-admission/`, which is git-excluded.

**A local-execution hazard was found and avoided.** The repository's `playwright.config.ts`
carries a `webServer` block that boots `npm run dev`; using it would have started the application
locally, violating the standing owner rule. The lane wrote a standalone `playwright.live.config.ts`
with no `webServer` and pinned the base URL to the deployed site. Any future browser lane must do
the same. The application exposes no global map handle, so the harness reaches the live MapLibre
instance by walking the React fiber tree for `MapView`'s ref and for the `map` prop each renderer
receives; it patches nothing. Layer rows mount only after the map-manager control is opened.

Every case toggled its layer on **after** `style.load` had already fired — the exact sequence the
corrected defect describes — and each recorded the pre-toggle absence of the source and layers
before asserting installation. All five renderers installed:

| Renderer | Pre-toggle present | Installed | Native layers | Features | State |
| --- | --- | --- | ---: | ---: | --- |
| BotanicalOccurrences | no | yes | 4 of 4 | 1,623 rendered / 961 source | populated |
| GbifOccurrences | no | yes | 3 of 3 | 0 | correctly empty |
| SoilField | no | yes | 3 of 3 | 3,052 | populated |
| ClimateField | no | yes | 2 of 2 for the selected form | 370 | populated |
| Vegetation | no | yes | 2 of 3 | 2,475 | populated |

Days and viewports were discovered from the site's own capability responses rather than assumed,
and each applied date was verified: soil September 6, climate September 10, vegetation
September 8. Three viewports were probed for occurrences; Vancouver returned records and Boise
returned none, so Vancouver at zoom 12, above the detail floor of 11, is the anchor. This
discipline is a direct response to **D260915-32**, where a harness asserted a missing day that was
actually populated.

**The style-swap case closes the gap unit fixtures structurally cannot reach.** With both
occurrence renderers enabled and the basemap swapped from default to light, both re-added
themselves and their relative order against a renderer sharing a `beforeId` was preserved, with
no page errors. This is the first executed evidence for listener registration order; until now it
was guaranteed only by source structure.

Two apparent defects were correctly identified as **harness** defects rather than product
defects. Vegetation first read as empty because the probe ran the moment installation completed,
while installation and data arrival are separate events; a bounded settle window resolved it to
2,475 features. A private-field feature count read `null` everywhere because MapLibre v5 keeps
GeoJSON data private, so the public `querySourceFeatures` is now the authority. Two apparently
missing layers are correct behavior verified in source: ClimateField installs only the selected
form's layers, and Vegetation's value-label layer is gated on the field renderer and ships
hidden. GBIF's zero is correct and was confirmed against the data rather than assumed: all 994
Vancouver records carry the UBC collection key and none carry a GBIF key, so the layer installed
cleanly and rendered empty with no page errors, which is the intended no-throw empty path.

**What this does not establish.** Sources and native layers exist and carry features; that is not
proof that pixels painted correctly, nor of scientific value correctness, legends, accessibility,
picking, popups or agent parity. `querySourceFeatures` counts per tile, so the counts establish
non-emptiness rather than exact record totals. Only one signal per family was exercised, leaving
seven climate and two soil signals untouched; only one basemap swap, one viewport per renderer,
and GBIF's populated path remains untestable until an acquisition exists. No whole QA case and no
runbook checklist item is promoted by this evidence; the 220-case matrix is unchanged.

The lane confirmed no Playwright browsers, servers or ports were left running, no local
application server was ever started, and no tracked file was modified.

## Deployment

Session 19's source landed in `f26beda8` and the live-evidence record in `03a90adc`, a direct
descendant. Railway superseded `f26beda8`'s in-flight builds when the newer commit arrived, which
is its normal behavior for a queued revision; the deployed artifact therefore contains Session
19's source by descent rather than through its own successful build. **`03a90adc` is the revision
to cite**: both of its services reported SUCCESS (`cc4d124b`, `e901b45f`) and
`https://plantgeo.aevani.com/api/ready` returned 200 afterward. The data API and job executor
retain `d167e7f0`, correct because no Python source changed in either commit.

Note for future sessions: a superseded build is not a failed build, but it is also not evidence
that the superseded commit itself ever built. Cite the revision that actually reported SUCCESS.
