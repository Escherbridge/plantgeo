---
type: lane-brief
track: ingestion_warehouse_consolidation_20260803
lane: S
status: ready
depends_on: none
---

# Lane S — soil, the serving path

**Read `README.md` in this directory first** for the shared rules and the wave plan.

Lanes A–G are already running in other sessions. This lane was carved to be
conflict-free with all of them, which makes its boundary narrower than you might
expect. Read "Files you own" carefully — several soil-adjacent files belong to
lanes F and G and you must not touch them.

## Goal

The owner's report is *"No soil data."* When this lane is done we know exactly why,
and the soil layer either renders real provenanced data or says honestly why it
cannot — with no code path that silently returns empty.

This lane owns the **serving** half: read model, tRPC/API surface, and the map layer
component. Lane F owns the **acquisition** half (SoilGrids → COG → R2). They meet at a
declared contract, not in shared files.

## Measured facts — verified against PRODUCTION on 2026-08-03, do not re-derive

| Fact | Evidence |
|---|---|
| `geo.layers` holds **8** rows and **none is soil**: evacuation-zones, fire-detections, fire-perimeters, interventions, sensors, vegetation, water-gauges, weather-observations | `select id, name from geo.layers` against prod |
| Production Martin serves **16** sources and **none is soil** — vegetation and drought have tile sources, soil does not | `GET https://plantgeo-martin-production.up.railway.app/catalog` |
| Production tile infrastructure is **healthy** — Martin catalog `200`, PMTiles basemap `206`, `NEXT_PUBLIC_DYNAMIC_TILES_URL` correctly points at the deployed Martin (not localhost) | Railway variables + live curl |
| `geo.features` ≈ 19,113 rows and rising on a cron. **Never hardcode this number** | `pg_stat_user_tables` |
| Most `geo` tile-backed tables are **empty**: `fire_detections`, `historical_fire_data`, `historical_vegetation`, `historical_water_drought`, all five `osm_*`, `poi` — all 0 rows | `pg_stat_user_tables` |

**So soil is not tile-served and has no `geo` layer row.** Whatever renders it is a
client-side layer fed by an API, not a Martin source. That is the thing to trace.

## Files you own

Touch only these. Other sessions are live in the neighbouring files.

```
src/components/map/layers/SoilLayer.tsx
src/lib/server/services/soilgrids.ts
src/lib/server/services/usda-soil.ts
src/lib/server/trpc/routers/environmental.ts     (soil procedures only)
src/app/api/ingest/soil/route.ts
src/lib/server/services/carbon-potential.ts      (only if it blocks soil serving)
src/lib/server/services/usle.ts                  (only if it blocks soil serving)
```

**Explicitly NOT yours** — and why:

| Path | Owner | If you need a change there |
|---|---|---|
| `src/components/panels/**` (incl. `SoilPanel.tsx`) | **lane G** | report the exact change needed; do not edit |
| `src/stores/**` (incl. `soil-store`) | **lane G** | same |
| `scripts/**`, `data/**`, `infra/**` — SoilGrids fetch, COG/PMTiles build, R2 upload | **lane F** | same; this is soil *acquisition* |
| `src/lib/server/db/schema.ts`, `drizzle/**` | **lane B** | if soil needs a `geo.layers` row or a table, hand lane B the DDL |
| `src/lib/server/trpc/routers/environmental.ts` | **lane J** (wave 3 — no concurrent writer) | you may edit the **soil procedures only**. Announce the edit so lane J rebases onto it rather than reverting it. Do not touch anything else in that file |
| `src/components/map/LayerManager.tsx` | **lane G** (granted outright, 2026-08-03) | read-only, and lane G is `in-progress` in it right now. The `SoilLayer` mount is at `:171-175` and the `dynamic()` import at `:36-37` (both verified). If either needs changing, report the exact diff |

If the fix genuinely requires a file you do not own, **stop and report it**. A
cross-lane edit will be silently overwritten by whichever session commits last.

## The work

1. **Classify first, fix second.** Decide which of these soil is, and cite the first
   thing that fails:
   - **DELIBERATE** — a governance stub returning empty on purpose. Check for
     `unavailableCollection()`, `getEnvironmentalTileTemplate()` returning `""`, and any
     `PRECONDITION_FAILED` throw on the soil path.
   - **BROKEN** — real wiring defect.
   - **NO_DATA** — code correct, nothing ingested.

   Getting this wrong has already cost this repo a cycle. See [`../plan.md`](../plan.md)
   and the note below on the 2026-08-03 narrowing.

2. **Trace the chain end to end** and write down each hop:
   `SoilPanel` toggle → `activeLayers.includes("soil")` (`LayerManager.tsx:173`) →
   `SoilLayer.tsx` → tRPC `environmental.*` or `/api/...` → `soilgrids.ts` / `usda-soil.ts`
   → upstream or database. Report exactly where it goes empty.

3. **Check the data-boundary gate.** `npm run check:data-boundary` rejects direct
   client→upstream links for environmental *measurements*; only cartographic context is
   allowlisted (`scripts/check-client-provider-urls.mjs`). If soil currently works by
   calling SoilGrids from the client, that gate is *why* it is stubbed — the fix is a
   server-side proxy/persist path, not an allowlist entry.

4. **Fix within your boundary.** If the fix is a server route or the layer component,
   do it. If it needs persisted rasters, that is lane F: define the contract (URL shape,
   property names, value encoding, `data_available_at`) and hand it over.

5. **Honest degradation if it cannot render.** If soil genuinely has no data, the layer
   must say so where the user can see it — not render an empty layer. Note that the
   toggle itself lives in a panel owned by lane G, so specify the change rather than making it.

## Traps specific to this lane

- **The "empty layers are deliberate" rule was NARROWED on 2026-08-03 — narrowed, not deleted.**
  The owner ruled: *"role gating is no longer a blocker... treat this as the dev project it is,
  not the enterprise red tape fest."* So do not preserve a governance stub merely because it
  looks intentional — open it, and aggregate output is explicitly fine because aggregation is
  itself the privacy mechanism. What survives: some stubs are still real and documented
  (`demand-heatmap` in `src/components/map/AGENTS.md`), an empty feed must never be
  indistinguishable from a toggle being off, and unavailable data stays visibly unavailable
  rather than getting a substitute value. That is why step 1 makes you *classify* rather than
  just delete the guard. See `README.md` §"Rules every lane inherits" — lanes G and H inherit
  the same wording, so do not report this as a contradiction.
- **`data_available_at` must never be `now()`** and must never derive from
  `geo.features.created_at`, which is "last touched" — the refresh path rewrites it
  (`src/lib/server/services/ingest.ts:107-122`). One lazy default poisons every downstream
  model with lookahead, invisibly.
- **Do not add a `geo.layers` row yourself** — that is `schema.ts`, owned by lane B.
- **SoilGrids is rate-limited.** Do not hammer it from a test loop; record a payload and
  replay it.
- The soil layer is `dynamic()`-imported (`LayerManager.tsx:36-37`), so it is **not** in the
  homepage JS bundle. Grepping the homepage chunks proves nothing about it — a mistake
  already made once today.

## Definition of done

```powershell
# from the repo root — one sweep, at the end, not test-fix-test
npm run type-check
npm run lint
npm run check:data-boundary      # must pass; soil must not add a client->upstream link
npm test
```

Plus, stated explicitly in your report:
1. The classification (DELIBERATE / BROKEN / NO_DATA) with the first failure at `file:line`.
2. Evidence the layer now renders, **or** the exact honest-degradation copy shown instead.
3. Any change needed in a file owned by lane B, F or G, written as a ready-to-apply
   instruction for that session.

Verify against **production**, not local — the owner's words: *"i want this in prod, not at
all worried about local validation."* Local containers are stopped deliberately; keep them off.

## Open questions

1. **Does soil belong in `geo` at all, or only as an R2 raster?** Vegetation and drought
   have Martin sources; soil has neither a source nor a layer row. Recommendation: soil is
   a raster covariate, so serve it from R2 as lane F publishes it and give it a `geo.layers`
   row only if it needs per-feature interactivity. Confirm before asking lane B for DDL.
2. **SoilGrids vs USDA** — both service files exist (`soilgrids.ts`, `usda-soil.ts`). Which
   is authoritative, and is the other dead code? Delete the loser when you touch it.
3. **What did commit `208d056` actually do for soil?** It claims to serve "drought, soil and
   NDVI from real provenanced sources", yet soil has no layer row and no tile source. Read
   the diff (`git show 208d056`) and reconcile; the claim may be optimistic for soil
   specifically even if true for drought.
