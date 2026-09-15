---
type: evidence
status: active
recorded_on: 2026-09-15
---

# Session 18: Scalar, vegetation and occurrence parsed-style admission

This session completes the renderer-admission defect class that Session 17 opened. It starts
from `ec172e881e4aa640231ae073a1d04408fd05ad5a`, which was deployed and bounded-live-verified;
that session's terminal outcome is recorded separately in
[the Session 17 note](runbook-session17-20260915.md) and raised **D260915-32** against the live
regression harness's anchor selection. No source changes were present at intake beyond the
Session 17 canonical records applied in this session.

## Ownership and evidence boundary

| Role | Owned work |
| --- | --- |
| Coordinator/integrator | Intake, partitioning, canonical records, `AGENTS.md`, the single integrated sweep, Git and deployment operations |
| Scalar author | `SoilFieldLayer.tsx`, `ClimateFieldLayer.tsx`, `ScalarFieldLabels.test.tsx`, `ClimateFieldLayers.test.tsx` |
| Vegetation author | `VegetationLayer.tsx`, `VegetationLayer.test.tsx` |
| Occurrence author | `GbifOccurrencesLayer.tsx`, `BotanicalOccurrencesLayer.tsx`, `botanical-occurrence-experience.test.tsx` |
| Independent source reviewer | Adversarial review of the combined tree; no source writes |

The three author lanes held disjoint file partitions and ran concurrently. No author ran tests,
lint or type checking; each instead predicted what might fail. The coordinator applied no source
edits of its own beyond `AGENTS.md` and ran one integrated sweep over the complete batch. The
independent reviewer authored none of the code under review.

## Corrected defect

`use-style-ready.ts` observes `style.load`/`styledata` and recomputes a global `isStyleLoaded()`,
which is true only once **every** source, including unrelated ones, has completed. Five renderers
gated initial source and layer creation on that global signal. A component mounting after
`style.load` has already fired therefore rejected admission and received only `sourcedata`
afterward, while its update effects could write to existing sources but never create absent ones.
The layer silently never rendered. This is a source-proven admission gap, identified by the
Session 16 read-only investigation; it is not a newly reproduced live failure for these five.

The correction applies the same public parsed-style contract landed for Fire and Water in
`ec172e88`: no `useStyleReady`; a persistent `style.load` listener registered exactly once per
map, reading current visibility from a ref so registration order never churns; and a separate
visibility effect that creates sources and layers as soon as `getStyle()` returns a parsed style,
or removes them when hidden. A genuinely unparsed style waits for `style.load`. This separates
permission to create native style resources from the completion of unrelated tile requests. It
does not claim admitted data has painted.

`SoilSurveyLayer` is deliberately excluded and remains the sole consumer of the unchanged shared
hook, because its existing data effect already admits a missing source against a parsed style.

## Renderer-specific preservation

The two occurrence renderers carried a failure the native renderers did not. Their draw effect's
cleanup removed layers on every data or zoom change and re-added only behind the global gate, so
a rerender inside the pending window removed installed layers and never restored them, with no
later `style.load` to retry. Admission now carries no cleanup, teardown moved to the stable
listener effect, and `addLayers` is idempotent and current. Click and picking handlers moved to
their own map-keyed effect so repeated draw cycles cannot duplicate them.

ClimateField retains its form, rung and id teardown-and-rebuild in an effect separate from the
listener registration. SoilField retains its measure ids and the aggregated-cell outline opacity
expression, now pinned by assertion rather than prose. Vegetation retains the scalar controller,
the inspection gate published through `ScalarFieldLayer.sync`, and raster/measured exclusivity;
because its tile template is empty while no day is named, its update effect now calls idempotent
`addAllLayers` first so a source that could not be created at admission is created when the
period arrives. Botanical retains its detail zoom floor, three picking layers and distinct source
identity; GBIF retains two picking layers, its own source, and a clean no-throw empty path,
because no GBIF acquisition has run. Coarse Polygon and detail Point semantics are retained.

## Fixtures

Every fixture in this batch distinguishes a parsed style from source readiness, rejects
`addSource`/`addLayer` against a genuinely unparsed style, and models tile completion as
`sourcedata` only. No synthetic `styledata` event stands in for tile completion. The occurrence
fixture keys recorded listeners by event type **and** layer id in an array; the previous
type-keyed set silently deduplicated and would have concealed a duplicate-handler regression.
Covered regressions: delayed mount installing while `isStyleLoaded()` is false; genuinely
unparsed arrival installing later with the latest pending props; installed-then-rerendered while
readiness is pending remaining installed and current; no duplicate handlers after repeated draw
cycles; stable listener order across style swaps and hidden-to-shown transitions; empty-collection
clearing while enabled; map replacement and unmount cleanup; current data and paint updating
without a rebuild; ClimateField form and rung rebuild identity; Vegetation raster/measured
exclusivity across a delayed admission and a swap; distinct Botanical and GBIF source identity.

## Integrated verification

The complete batch preceded one sweep. The data-boundary check passed twelve documented URL
rules, the client/server restricted-import check and the observation-fabrication check. Full type
checking passed. Lint reports **zero errors and 9,887 warnings**, unchanged from the recorded
baseline. The frontend suite passed **193 files and 2,592 tests** in 173.81 seconds, plus twelve
tooling tests, exit zero. The prior recorded total was 2,550 tests; the increase is this batch's
added regressions. No Python suite or receipt writer was rerun and no Python source changed; the
existing unchanged 885-input quality receipt remains the Python gate.

Bare `npm run lint` now aborts with `EPERM` while scanning a stale, unreadable
`.omc/pytest-regional-*` temporary directory. This is a pre-existing local environment artifact,
not a lint failure and not attributable to this batch; the recorded protocol already excludes
`.omc/**`, and the sweep used that documented exclusion. The condition is worth clearing because
it presents as a lint failure to anyone who does not read the trace.

## Independent review

The independent reviewer returned **APPROVE** with no blocking or major findings, having run the
four changed suites plus the two unmodified guard suites. It confirmed by diff that no assertion
was weakened, that `LayerManager.tsx`, `LayerManager.test.tsx` and `layer-render-contract.test.ts`
genuinely required no change because no public prop, layer id, source id or paint expression
moved, and that `use-style-ready.ts` has exactly one remaining consumer.

Four non-blocking findings are carried forward rather than fixed here, so that applied source
remains exactly what was reviewed:

1. SoilField's `addLayers`/`removeLayers` are memoised on `[ids, fillColor, measure]` rather than
   the empty deps the other four use. Its listener is therefore once-per-map only because
   `measure` is a literal at all three call sites. A future dynamic `measure` would re-register
   the listener at the back of the queue and invert layer stacking.
2. `AGENTS.md` states once-per-map registration as a guarantee for all five; it holds structurally
   for four and incidentally for SoilField. The exception should be named.
3. Within `SoilFieldLayer` and `ClimateFieldLayer`, the admission effect calls `getStyle()` bare
   while the data effect ten lines later guards the same call in `try/catch`. Only one can be
   right. This matches the already-shipped `ec172e88` contract, so it is a consistency question.
4. `VegetationLayer.test.tsx` skips its composite-raster assertions by early return when the
   period is unpublished, which would silently shrink the exclusivity contract on a green tick.

The reviewer also established that ClimateField's stale-`ids` teardown hazard is real in the
abstraction and inert only because the parent renders `ClimateSignalLayer` with `key={signal}`,
so a signal change remounts rather than re-renders. That guarantee lives in the parent, not in
the corrected file.

## Acceptance boundary

These are bounded code-level lifecycle regressions over hand-written stand-ins; none of these
fixtures runs MapLibre. Listener registration order is the one requirement of this change that
tests structurally cannot verify — it needs a real-map basemap-swap observation before stacking
is treated as proven. Real `style.load` timing against the other renderers sharing a `beforeId`,
browser rendering, scientific values, legends, conservation, accessibility, time-history, agent
parity, source admission and production validation all remain separate obligations.

**No whole QA case and no runbook checklist item is promoted by this session.** The formal
220-case matrix is unchanged. Deployment and live validation are recorded separately below once
observed; local verification alone certifies no release.
