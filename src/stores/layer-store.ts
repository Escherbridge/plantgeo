import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type { LayerStyle } from "@/lib/map/layer-styles";

interface LayerStoreState {
  selectedLayerId: string | null;
  filterExpressions: Map<string, unknown[]>;
  styleOverrides: Map<string, LayerStyle>;
  legendVisible: boolean;

  setSelectedLayer: (id: string | null) => void;
  setFilterExpressions: (layerId: string, expressions: unknown[]) => void;
  clearFilterExpressions: (layerId: string) => void;
  setStyleOverride: (layerId: string, style: LayerStyle) => void;
  clearStyleOverride: (layerId: string) => void;
  toggleLegend: () => void;
}

export const useLayerStore = create<LayerStoreState>()(
  devtools((set) => ({
    selectedLayerId: null,
    filterExpressions: new Map(),
    styleOverrides: new Map(),
    legendVisible: true,

    setSelectedLayer: (id) => set({ selectedLayerId: id }),

    setFilterExpressions: (layerId, expressions) =>
      set((s) => {
        const next = new Map(s.filterExpressions);
        next.set(layerId, expressions);
        return { filterExpressions: next };
      }),

    clearFilterExpressions: (layerId) =>
      set((s) => {
        const next = new Map(s.filterExpressions);
        next.delete(layerId);
        return { filterExpressions: next };
      }),

    setStyleOverride: (layerId, style) =>
      set((s) => {
        const next = new Map(s.styleOverrides);
        next.set(layerId, style);
        return { styleOverrides: next };
      }),

    clearStyleOverride: (layerId) =>
      set((s) => {
        const next = new Map(s.styleOverrides);
        next.delete(layerId);
        return { styleOverrides: next };
      }),

    toggleLegend: () => set((s) => ({ legendVisible: !s.legendVisible })),
  }))
);
