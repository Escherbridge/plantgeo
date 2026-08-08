import { create } from "zustand";
import { devtools, persist } from "zustand/middleware";
import { isLayerToggleId, type LayerToggleId } from "@/lib/map/layer-registry";
import { clampLayerOpacity } from "@/lib/map/layer-opacity";

/**
 * Layer PRESENTATION state: how strongly each layer draws. Deliberately not visibility --
 * `map-store.activeLayers` is the single source of that (see src/components/map/AGENTS.md) and
 * the two must never be able to disagree.
 *
 * It held one more field until 2026-08-09: `legendVisible`, the corner legend card's collapsed
 * flag, with two controls over it (the card's own eye and a second one in the manager's header).
 * The legend is the collapsed state of the manager now -- `LayerLegend` opens on hover, focus
 * and click, all of which are local to the component that owns them -- so a global boolean for
 * it would be state nothing outside that component could act on.
 *
 * Not map-store, because MapView destructures map-store wholesale: a record that moves on every
 * slider tick would re-render the entire map chrome. Not layer-toggle-context either -- that
 * module owns no state by rule and must stay a pure composition layer.
 */
interface LayerStoreState {
  /**
   * Per-layer opacity MULTIPLIER over each layer's authored paint -- see
   * src/lib/map/layer-opacity.ts for why a multiplier and not an absolute value.
   *
   * Sparse on purpose: an absent key means "as authored", so adding a registry layer needs no
   * migration and a persisted blob written by an older build cannot pin a layer that did not
   * exist when it was written.
   */
  layerOpacity: Partial<Record<LayerToggleId, number>>;

  setLayerOpacity: (layerId: LayerToggleId, opacity: number) => void;
  /** Deletes the key rather than writing 1: "unset" and "explicitly full" are different facts. */
  resetLayerOpacity: (layerId: LayerToggleId) => void;
}

/**
 * Drops keys that are not registry layers and clamps every value.
 *
 * A hand-edited localStorage entry of `50` would otherwise be handed straight to
 * `setPaintProperty`, and MapLibre's rejection surfaces through MapView's `error` handler --
 * which hard-codes a PMTiles-expiry message and would blame the basemap for it.
 */
function sanitizeLayerOpacity(value: unknown): Partial<Record<LayerToggleId, number>> {
  if (typeof value !== "object" || value === null) return {};
  const sanitized: Partial<Record<LayerToggleId, number>> = {};
  for (const [layerId, opacity] of Object.entries(value as Record<string, unknown>)) {
    if (!isLayerToggleId(layerId)) continue;
    if (typeof opacity !== "number" || !Number.isFinite(opacity)) continue;
    sanitized[layerId] = clampLayerOpacity(opacity);
  }
  return sanitized;
}

export const useLayerStore = create<LayerStoreState>()(
  devtools(
    persist(
      (set) => ({
        layerOpacity: {},

        // Clamped here rather than at the call site: this is the one writer, so nothing
        // downstream -- including a rehydrated blob replayed through it -- can reach 0.
        setLayerOpacity: (layerId, opacity) =>
          set((s) => ({
            layerOpacity: { ...s.layerOpacity, [layerId]: clampLayerOpacity(opacity) },
          })),

        resetLayerOpacity: (layerId) =>
          set((s) => {
            if (s.layerOpacity[layerId] === undefined) return s;
            const next = { ...s.layerOpacity };
            delete next[layerId];
            return { layerOpacity: next };
          }),
      }),
      {
        // Follows search-store, the only other persistence precedent in this repo:
        // devtools(persist) with an explicit `partialize`.
        //
        // No SSR hydration risk: the legend and the manager render only inside
        // `{mapInstance && ...}` in MapView, i.e. after client map init, so persisted state
        // never participates in a server render and no `skipHydration` is needed.
        name: "plantgeo-layer-opacity",
        version: 1,
        partialize: (s) => ({ layerOpacity: s.layerOpacity }),
        merge: (persisted, current) => ({
          ...current,
          layerOpacity: sanitizeLayerOpacity(
            (persisted as { layerOpacity?: unknown } | undefined)?.layerOpacity
          ),
        }),
      }
    )
  )
);
