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

Fixture-level only. No fixture here runs MapLibre, so runtime behavior against a real map and a
live basemap swap remains unestablished, and listener registration order is still unproven by
executed evidence despite now being structurally guaranteed in source. The four other renderers
corrected in `a112a754` were not re-reviewed in this session. **No whole QA case and no runbook
checklist item is promoted; the 220-case matrix is unchanged.**
