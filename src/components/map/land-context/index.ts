/**
 * Barrel for the land-context map/interaction layer
 * (conductor/tracks/pnw_land_contact_experience_20260911/spec.md).
 *
 * Integration (done in MapView.tsx):
 * 1. `<LandContextController />` bridges the real tRPC reader
 *    (`useLandContextQuery`) results into the store.
 * 2. `<LandContextLayer map={mapInstance} />` renders native MapLibre
 *    source/layers (not deck.gl -- see the note at the top of
 *    `LandContextLayer.tsx` for why) and wires hover/click into the store.
 *    Its return also mounts `LandContextStatusNotice` (the per-family
 *    "what is admitted / what a click does / what came back" pills) and
 *    `WideAreaSelectionAction`, so neither needs its own MapView slot.
 *    `deriveLandContextNotices` is the pure derivation behind the notice,
 *    in LayerManager's `parquetLayerFaults` entry shape, should the
 *    integrator prefer one shared notice stack.
 * 3. `<LandContextIdentityCard />` (dismissible hover/focus identity card)
 *    and `<LandContextAccessibleFeatureList />` (keyboard-reachable textual
 *    equivalent of the map layer, spec "equivalent textual feature/contact
 *    lists") are mounted directly in MapView.tsx. The toggle UI lives in
 *    `LayerPanel.tsx`'s own `LandContextDockSection` -- `LandContextToggles`
 *    below is an unstyled placeholder superseded by it and intentionally
 *    NOT mounted; safe to delete in a future cleanup. The pinned detail
 *    panel is `LandContextPanelHost` (`src/components/panels/land-context`)
 *    -- the richer, real-data implementation that superseded this
 *    directory's own `LandContextDetailPanel` (deleted).
 */
export { LandContextController } from "@/components/map/land-context/LandContextController";
export { LandContextToggles } from "@/components/map/land-context/LandContextToggles";
export { LandContextLayer } from "@/components/map/land-context/LandContextLayer";
export { LandContextIdentityCard } from "@/components/map/land-context/LandContextIdentityCard";
export { LandContextAccessibleFeatureList } from "@/components/map/land-context/LandContextAccessibleFeatureList";
export { useLandContextQuery } from "@/components/map/land-context/useLandContextQuery";
export { WideAreaSelectionAction } from "@/components/map/land-context/mobile/WideAreaSelectionAction";
export {
  LandContextStatusNotice,
  deriveLandContextNotices,
} from "@/components/map/land-context/LandContextStatusNotice";
export type { LandContextNotice, LandContextNoticeInput } from "@/components/map/land-context/LandContextStatusNotice";
