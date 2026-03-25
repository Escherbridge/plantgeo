import { create } from "zustand";
import { devtools } from "zustand/middleware";
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

/** Maps each panel to the layer IDs it governs. */
const PANEL_LAYER_MAP: Record<PanelId, string[]> = {
  fire:       ["fire"],
  water:      ["water", "drought"],
  vegetation: ["vegetation"],
  soil:       ["soil"],
  community:  ["demand-heatmap"],
  strategy:   [],
  analytics:  [],
  team:       [],
};

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
  /** Toggle a layer on/off — delegates to map-store. */
  toggleLayer: (layerId: string) => void;
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

      toggleLayer: (layerId) => {
        // Delegate to map-store (single source of truth for layer visibility)
        useMapStore.getState().toggleLayer(layerId);
      },
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
  for (const [panelId, layers] of Object.entries(PANEL_LAYER_MAP)) {
    if (layers.includes(layerId)) return panelId as PanelId;
  }
  return null;
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
