# Tracking admin UI

**2026-10-26, owner decision: finish the tracking-admin feature.** `routers/tracking.ts` and
`routers/visualization.ts` were deleted on 2026-10-25 as zero-caller dead code (no UI reached
either) — see `conductor/tracks/config_driven_ingestion_20260926/metadata.json`,
`cleanup_wave_20261025.owner_questions_open`: *"Places / Visualization / Tracking-admin panels:
deleted their zero-caller routers as unused; restore-from-git is trivial if any was 'built ahead
of UI' on purpose."* Tracking was. Both routers were restored verbatim from `0df6e227^` (the
commit before that removal) and re-registered in `src/lib/server/trpc/router.ts`, which this
directory's components are the first and only caller of.

No `AGENTS.md` carried a dated removal note naming `tracking.ts`/`visualization.ts` specifically
(unlike `useWebSocket.ts`, `UserPanel.tsx`, `LayerUpload.tsx`, each of which got one in their own
directory's file) — the same wave's router deletions were undocumented at the directory level.
That same metadata file's open question *"getRouteHistory in services/tracking.ts: unreferenced
after router removal"* is now stale: `getRouteHistory` is called again, from
`trackingRouter.getRouteHistory` below.

## What this directory owns

- `TrackingAdminDashboard.tsx` — the `/admin/tracking` page body: a three-tab shell (Assets /
  Geofences / Alerts). Role gating is NOT this component's job — it lives in
  `src/app/admin/layout.tsx` (redirects non-admins before any child renders) and, independently,
  in every `tracking.*` procedure being `adminProcedure`-gated. Neither is a substitute for the
  other; see that layout's own comment.
- `AssetsPanel.tsx` — create/list fleet assets (`tracking.createAsset` / `listAssets`), a
  "last seen" badge per row from `tracking.getLastPositions` (added here — no procedure exposed
  `services/tracking.ts`'s `getLastPositions` before this pass), and a per-asset route-history
  drill-down (`tracking.getRouteHistory`) rendered on `AssetRouteMap`.
- `AssetRouteMap.tsx` — a standalone MapLibre instance (own `maplibregl.Map`, not the shared
  shell map `InterventionProposalForm.tsx` draws into), following `EmbedMap.tsx`'s construction
  pattern: `maxZoom` always `MAP_MAX_ZOOM`, never a bare literal. Mounts/unmounts per selected
  asset, so it owns its own map lifecycle rather than borrowing one.
- `GeofencesPanel.tsx` — create (GeoJSON Polygon textarea + delete). The textarea is validated
  client-side with a zod schema that MIRRORS `geofenceCreateSchema.geometry` in
  `routers/tracking.ts` field-for-field — UX only; the server re-validates independently and
  remains the real shape boundary.
- `AlertsPanel.tsx` — list + acknowledge (`tracking.getAlerts` / `acknowledgeAlert`).

## `detectStops` is computed server-side, not in this directory

`services/tracking.ts`'s `detectStops` is a pure function, but the module that exports it imports
`@/lib/server/db` at module scope — importing it from a `"use client"` component here would pull
the server DB connection into the browser bundle. `trackingRouter.getRouteHistory` therefore calls
`detectStops` itself and returns `{ positions, stops }`; `AssetsPanel` only ever receives the
already-computed stops.

## Mobile tap-target floor

Every raw `<button>`/`<input>`/`<select>` in this directory carries `TOUCH_TARGET` or
`TOUCH_TARGET_SQUARE` from `@/components/ui/touch-target`, following the house convention — but
`src/__tests__/components/mobile-touch-target-contract.test.ts`'s `SCANNED_DIRECTORIES` does not
include `src/components/admin/**`, so this is convention-only here, not contract-enforced.
Widening that scanner to cover this directory (and `src/components/admin/**` generally) is the
intended way to close the gap; left undone here since that test file is a shared contract outside
this feature's ownership.
