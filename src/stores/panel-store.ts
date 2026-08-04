import { create } from "zustand";
import { devtools } from "zustand/middleware";
import {
  layerRegistryEntries,
  panelIdForLayerToggle,
  type LayerToggleId,
} from "@/lib/map/layer-registry";
import { useMapStore } from "@/stores/map-store";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type PanelId =
  | "fire"
  | "water"
  | "vegetation"
  | "soil"
  | "community"
  | "strategy"
  | "team"
  | "analytics";

/**
 * Maps each panel to the layer IDs it governs, inverted from the layer registry so a
 * layer's owning panel is declared once, next to the rest of that layer's wiring.
 * Panels with no layers of their own still get an entry, hence the seeded record.
 */
function buildPanelLayerMap(): Record<PanelId, LayerToggleId[]> {
  const panelLayers: Record<PanelId, LayerToggleId[]> = {
    fire:       [],
    water:      [],
    vegetation: [],
    soil:       [],
    community:  [],
    strategy:   [],
    analytics:  [],
    team:       [],
  };
  for (const entry of layerRegistryEntries()) {
    if (entry.panelId !== null) panelLayers[entry.panelId].push(entry.toggleId);
  }
  return panelLayers;
}

const PANEL_LAYER_MAP: Record<PanelId, LayerToggleId[]> = buildPanelLayerMap();

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

interface PanelState {
  /** Currently open sidebar panel (null = all closed). */
  openPanel: PanelId | null;

  /** Layers whose data has been prefetched / initialized. */
  prefetchedLayers: Set<string>;

  // --- Actions ---
  /** Open a panel (or close if already open). */
  togglePanel: (id: PanelId) => void;
  /** Close the currently open panel without touching layers. */
  closePanel: () => void;
  /** Mark a layer as prefetched (data loaded, ready for instant toggle). */
  markPrefetched: (layerId: string) => void;
}

export const usePanelStore = create<PanelState>()(
  devtools(
    (set) => ({
      openPanel: null,
      prefetchedLayers: new Set(),

      togglePanel: (id) =>
        set((s) => ({ openPanel: s.openPanel === id ? null : id })),

      closePanel: () => set({ openPanel: null }),

      markPrefetched: (layerId) =>
        set((s) => {
          const next = new Set(s.prefetchedLayers);
          next.add(layerId);
          return { prefetchedLayers: next };
        }),
    }),
    { name: "panel-store" },
  ),
);

// ---------------------------------------------------------------------------
// Selectors (pure functions, no hooks — safe for use anywhere)
// ---------------------------------------------------------------------------

/** Get the layer IDs associated with a panel. */
export function getLayersForPanel(panelId: PanelId): string[] {
  return PANEL_LAYER_MAP[panelId];
}

/** Get the panel that owns a layer ID (if any). */
export function getPanelForLayer(layerId: string): PanelId | null {
  return panelIdForLayerToggle(layerId);
}

/** All layer IDs managed by any panel. */
export function getAllManagedLayerIds(): string[] {
  return Object.values(PANEL_LAYER_MAP).flat();
}

// ---------------------------------------------------------------------------
// Derived hooks (compose panel-store + map-store)
// ---------------------------------------------------------------------------

/** Hook: does the given panel have any active (visible) layers? */
export function usePanelHasActiveLayers(panelId: PanelId): boolean {
  const activeLayers = useMapStore((s) => s.activeLayers);
  return PANEL_LAYER_MAP[panelId].some((id) => activeLayers.includes(id));
}

/** Hook: is a specific layer prefetched and ready for instant render? */
export function useIsLayerPrefetched(layerId: string): boolean {
  return usePanelStore((s) => s.prefetchedLayers.has(layerId));
}
