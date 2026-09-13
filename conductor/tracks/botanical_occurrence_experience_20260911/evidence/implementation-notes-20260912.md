---
type: track-evidence
track: botanical_occurrence_experience_20260911
status: implemented
---

# Botanical occurrence experience — implementation notes (2026-09-12)

## What exists

- `src/lib/botanical-occurrences.ts` — wire types for the four-state response
  (`detail`/`aggregate`/`refused`/`unavailable`) and `fetchBotanicalOccurrences`, which never
  throws (network/parse failures normalize to `unavailable`). Also exports
  `BOTANICAL_LAYER_DEFINITIONS` (paste-ready registry identity fields) and
  `BOTANICAL_DETAIL_MIN_ZOOM = 11`.
- `src/components/map/layers/BotanicalOccurrencesLayer.tsx` — detail-zoom specimen points.
  Three circle layers (`confirmed`+`exact`, `confirmed`+`generalized`, `possible`) so a
  possible-membership record is never visually indistinguishable from an admitted one.
  `botanicalOccurrencesToGeoJSON` filters out any feature without finite lon/lat, so
  nonspatial records can never become map features regardless of what the caller passes in.
- `src/components/map/layers/BotanicalRichnessLayer.tsx` — aggregate choropleth. The four
  non-documented evaluation states are read directly off `cell.evaluation` from the server
  (never inferred from an empty/zero aggregate), and `BOTANICAL_RICHNESS_LEGEND` carries the
  four labels verbatim: "zero documented records", "outside admitted coverage",
  "withheld/generalized only", "not evaluated".
- `src/components/map/layers/BotanicalCollectionEffortLayer.tsx` — context layer over the
  same aggregate cells, muted zinc ramp (visually distinct from richness's saturated green),
  measure selectable among `record_count`/`event_estimate`/`collection_count`, each declaring
  "count per cell" as its aggregation via `BOTANICAL_EFFORT_MEASURE_LABELS`.
- `src/components/panels/BotanicalOccurrenceDetails.tsx` — selected-specimen fields plus the
  fixed disclosure line (`BOTANICAL_SPECIMEN_DISCLOSURE`), and `formatEventInterval` for
  interval/partial dates (e.g. "1987 (year precision)").
- `src/components/panels/BotanicalFilters.tsx` — release_set_id gates the rest of the form;
  `BOTANICAL_NO_RELEASE_PINNED_MESSAGE` renders alone until it is set. No free-text name
  search — only exact `taxon_concept_id`. Separate "collecting-event window" date inputs vs.
  a read-only "source snapshot" line. Zoom band line names both rungs.
- `src/stores/botanical-occurrence-store.ts` — filters (release id defaults to `null`, not
  `""`), last response, selected feature; same `create()+devtools` shape as `soil-store.ts`.
- `src/__tests__/components/botanical-occurrence-experience.test.tsx` — covers: filters block
  fetching until a release is pinned; details panel renders interval precision + disclosure;
  richness legend carries the four labels; a `refused` response surfaces its reason; nonspatial
  counts are read as a number and never turn into geojson features.

## What is pending (owned by a separate, serialized integrator)

- Registration in `src/lib/map/layer-registry.ts` (new `LayerToggleId` members, `PanelId:
  "botanical"`, three `LayerRegistryEntry` objects) — hunks in `shared-registration.patch`.
- Any `layer-render-contract.ts` / `time-slider.ts` / `parquet-slider-capabilities.ts` changes,
  contingent on the open question in the patch file about whether this plane needs a
  `warehouseLayerName` at all given it is event-window filtered, not day-slider driven.
- The actual proxy/route decision: this slice's `fetchBotanicalOccurrences` calls the
  agri-data-service directly via `NEXT_PUBLIC_AGRI_BOTANICAL_SERVICE_URL` (falling back to
  `http://localhost:8000`), rather than through a Next.js API route, because no such route
  exists yet and creating one would be inventing the shared integration this track explicitly
  defers.
- Wiring these three layers/panels into whatever mounts `DroughtLayer`/`VegetationLayer` today
  (e.g. a `LayerManager`) and into the panel dock — out of scope per the task's file-ownership
  boundary.

## Predicted sweep failures

- `tsc`: none expected from these new files in isolation. Risk: if the project's `tsconfig`
  enables `noUncheckedIndexedAccess` or a strict MapLibre expression type, the `as never` casts
  on `fill-color`/paint expressions in `BotanicalRichnessLayer.tsx` and
  `BotanicalCollectionEffortLayer.tsx` may need a narrower type than `unknown[]` — this mirrors
  how `DroughtLayer.tsx`'s `match` expression is typed inline, so if that file compiles clean
  these should too.
- `vitest`: confirmed `@testing-library/user-event` is NOT a dependency anywhere in this repo
  (`grep -n "user-event" package.json` empty), so the test uses `fireEvent.change` from
  `@testing-library/react` instead — no new dependency risk.
- `vitest`: `getByLabelText(/release set id/i)` depends on the native implicit `<label>` wrapping
  in `BotanicalFilters.tsx`'s `LabeledInput`; this pattern works with `@testing-library/react`
  by default and needs no `htmlFor`/`id` pairing, but flag if the project's testing-library
  version behaves differently.
- No changes made to any file outside this track's ownership list; `layer-registry.ts`,
  `layer-render-contract.ts`, `time-slider.ts`, `parquet-slider-capabilities.ts`, and all
  `AGENTS.md`/regional-intelligence files are untouched.
