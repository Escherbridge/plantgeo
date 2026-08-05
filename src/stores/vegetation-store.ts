import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type { VegetationMode } from "@/components/map/layers/VegetationLayer";

/**
 * Vegetation DISPLAY state only. There is deliberately no year, month or date here: the one
 * notion of "when" is the time slider's selected day (`useTimeSliderStore`), and the GIBS
 * composite's year/month are projected from it by `useVegetationDisplayMode` in
 * src/lib/map/layer-toggle-context.ts. This store held its own `year`/`month` until 2026-08-05,
 * which gave the app two disagreeing clocks and two competing sliders -- do not reintroduce
 * them. See src/components/map/AGENTS.md "One time control, projected per layer".
 */
interface VegetationState {
  mode: VegetationMode;
  ndviMode: "absolute" | "anomaly";
  showNDWI: boolean;
  opacity: number;
  setMode: (mode: VegetationMode) => void;
  setNDVIMode: (mode: "absolute" | "anomaly") => void;
  setShowNDWI: (show: boolean) => void;
  setOpacity: (opacity: number) => void;
}

export const useVegetationStore = create<VegetationState>()(
  devtools((set) => ({
    mode: "ndvi",
    ndviMode: "absolute",
    showNDWI: false,
    opacity: 0.75,
    setMode: (mode) => set({ mode }),
    setNDVIMode: (ndviMode) => set({ ndviMode }),
    setShowNDWI: (showNDWI) => set({ showNDWI }),
    setOpacity: (opacity) => set({ opacity }),
  }))
);
