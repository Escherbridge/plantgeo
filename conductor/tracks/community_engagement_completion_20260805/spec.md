---
type: track-spec
track: community_engagement_completion_20260805
status: active
---

# Community engagement completion — specification

Everything below was measured against production
(`switchback.proxy.rlwy.net:37967/plantgeo`, PG18) and the working tree on
2026-08-05. Row counts and file:line citations are evidence, not estimates.

## Goal

A member of the public can submit an intervention recommendation, an expert can
act on it, and the outcome is visible to both the submitter and the map. Two
layers that already hold published rows stop being invisible.

## Why this track exists

`geo.layers` no longer has an "empty layers" problem. Seven of eight layers hold
published rows; `interventions` is the only empty one and is empty **on purpose**
(owner directive 2026-08-04: never seed it — real users submit their own
recommendations). What remains is not ingestion. It is that the paths *around*
the data are incomplete, in four independent ways.

| Layer | Rows | All `published`? | Serving | UI |
|---|---|---|---|---|
| `water-gauges` | 18,819 | yes | yes | yes |
| `fire-detections` | 6,297 | yes | yes | yes |
| `weather-observations` | 3,592 | yes | yes | yes |
| `vegetation` | 1,565 (growing) | yes | **no reader** | raster proxy only |
| `sensors` | 750 | yes | `geo.sensor_tiles()` published | **style layer deleted** |
| `evacuation-zones` | 381 | yes | **no tile function** | **nothing** |
| `fire-perimeters` | 116 | yes | yes | yes |
| `interventions` | 0 | — | `geo.intervention_tiles()` | submit works, **review unreachable** |

## Settled findings

These are measured. Implement against them; do not re-derive them.

**1. The community loop is open at moderation — the highest-severity finding.**
`interventions.submitIntervention`
([routers/interventions.ts:150](../../../src/lib/server/trpc/routers/interventions.ts#L150))
is complete and careful: `contributorProcedure`, explicit `publicationConsent`,
geometry validated through the same validator the machine-ingress route uses
under a vertex ceiling, and every row forced to `status = 'pending_review'` so
nothing skips review. The moderation router is equally complete —
`contributions.listPendingReview` / `publishContribution` / `rejectContribution`
/ `reviewContribution`, all `expertProcedure`.

`ContributionQueue.tsx` wires all four into a working UI. **It is imported by
nothing.** A repo-wide search returns only its own definition. Since
`geo.intervention_tiles` filters `status = 'published'`, a submitted
recommendation can never become visible to anyone, including its author. The
loop is open, and the half that is missing is a mount, not a feature.

**2. The sensors layer was removed on a premise that is now false.**
[src/lib/map/layers.ts:104](../../../src/lib/map/layers.ts#L104) records the
deletion verbatim: *"nothing in the platform ever writes to the geo.features
'sensors' layer, so geo.sensor_tiles() can only ever return an empty tile. The
Martin function is left published for a future producer; the toggle is gone so
the UI stops advertising a layer that cannot populate."*

There are now 750 sensor rows, all `status = 'published'`, and
`geo.sensor_tiles()` is still published in
[infra/martin/martin.yaml:31](../../../infra/martin/martin.yaml#L31) and filters
on exactly that status. The tiles will serve the moment a style layer and toggle
exist. Nothing else is required. The comment that justified the removal should
be deleted with it rather than left to mislead the next reader.

**3. Evacuation-zones has data and no path to a screen.** 381 published rows and
a seeded `geo.layers` row
([drizzle/0001_handy_riptide.sql:313](../../../drizzle/0001_handy_riptide.sql#L313)).
There is no tile function, no `martin.yaml` entry, no style layer, no toggle and
no component. This is the one item here that is a genuine build rather than a
re-connection.

**4. Nothing bridges community submissions to the ML label plane.** No code path
connects `geo.features` rows to `strategy_label_mapping.py` or
`strategy_selection.py`. Combined with the known-empty strategy plane, community
labelling is entirely unbuilt. It is scoped here as a **design question, not an
implementation** — see Open questions.

## Already hardened (2026-08-05, this session)

`ingest-backfill --source sentinel2-ndvi` failed every chunk with *"Sentinel-2
NDVI sampling requires an upstream client on the fetch request"* before reading a
single scene. `run_source_backfill` builds a `FetchRequest` from
`BackfillPlan.client`, which defaults to `None`, and `vegetation.py` hard-raised
instead of owning a client the way
[sensors.py:569](../../../services/agri-data-service/src/agri_data_service/ingest/sensors.py#L569)
does. Fixed in two places: `vegetation._sample_grid` now owns a bounded client as
a fallback (source contract correct for any caller), and `commands._run_backfill`
opens one client for the whole walk (connection pooling across chunks). Verified
by a live 16/16-cell chunk. Recorded here because it is the reason vegetation
history exists at all.

## Non-goals

- **Seeding interventions.** Not in any phase, for any reason, including demos.
- **Opening the `ENVIRONMENTAL_TILES_CONFIGURED` raster gate.** That flag governs
  SoilGrids/NLCD/NBR raster tiles and is a separate pipeline requiring a
  first-party tile release; it is unrelated to every layer in this track.
- **The `getPublishedVegetation` read-model reader.** Vegetation's serving gap is
  real but belongs to the ingestion/warehouse track, not the community track.

## Open questions — owner input required before phase 5

1. Should a published community intervention become an ML **label**, or stay a
   presentation-only artifact? The Type-2 warehouse's value is that it is honest;
   admitting hand-authored rows into a training plane is the same class of risk
   that the never-seed directive exists to prevent.
2. If yes: what is the minimum expert quorum for a submission to count as a
   label — the current single-expert publish, or something stronger?
3. Should rejected submissions be retained as negative labels, or discarded?
4. Does a partner-workspace (`teamId`) submission carry different label weight
   from an individual one?
