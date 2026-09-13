/**
 * Barrel for the land-context map/interaction layer
 * (conductor/tracks/pnw_land_contact_experience_20260911/spec.md).
 *
 * INTEGRATION CONTRACT for whoever wires this into the map shell / sidebar:
 * 1. Mount `<LandContextController />` once to bridge `useLandContextQuery` results into the
 *    store (`src/stores/land-context-store.ts`).
 * 2. Mount `<LandContextToggles />` wherever the four group toggles belong (likely the layer
 *    panel / sidebar -- this worker did not touch either).
 * 3. Call `useLandContextDeckLayers(handlers)` from the component that owns the DeckGL/
 *    MapboxOverlay instance and merge its returned `Layer[]` into that instance's `layers` prop.
 *    Do NOT instantiate a second DeckGL overlay -- see the interleaved-mode risk note in
 *    `LandContextLayer.tsx`.
 * 4. Render `<LandContextIdentityCard />` and `<LandContextDetailPanel />` as map overlay
 *    children, plus `<LandContextAccessibleFeatureList />` for the keyboard-equivalent list.
 * 5. Replace `useLandContextQuery` (currently a stub returning empty results) with the real
 *    tRPC-backed reader once the reference-plane track exposes one.
 */
export { LandContextController } from "@/components/map/land-context/LandContextController";
export { LandContextToggles } from "@/components/map/land-context/LandContextToggles";
export {
  useLandContextDeckLayers,
  type LandContextPickHandlers,
} from "@/components/map/land-context/LandContextLayer";
export { LandContextIdentityCard } from "@/components/map/land-context/LandContextIdentityCard";
export { LandContextDetailPanel } from "@/components/map/land-context/LandContextDetailPanel";
export { LandContextAccessibleFeatureList } from "@/components/map/land-context/LandContextAccessibleFeatureList";
export { useLandContextQuery } from "@/components/map/land-context/useLandContextQuery";
