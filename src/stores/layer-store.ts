import { create } from "zustand";
import { devtools } from "zustand/middleware";

interface LayerStoreState {
  legendVisible: boolean;

  toggleLegend: () => void;
}

export const useLayerStore = create<LayerStoreState>()(
  devtools((set) => ({
    legendVisible: true,

    toggleLegend: () => set((s) => ({ legendVisible: !s.legendVisible })),
  }))
);
