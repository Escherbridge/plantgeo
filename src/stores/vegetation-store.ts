import { create } from "zustand";
import type { VegetationMode } from "@/components/map/layers/VegetationLayer";

interface VegetationState {
  mode: VegetationMode;
  year: number;
  month: number;
  ndviMode: "absolute" | "anomaly";
  showNDWI: boolean;
  opacity: number;
  setMode: (mode: VegetationMode) => void;
  setYear: (year: number) => void;
  setMonth: (month: number) => void;
  setNDVIMode: (mode: "absolute" | "anomaly") => void;
  setShowNDWI: (show: boolean) => void;
  setOpacity: (opacity: number) => void;
}

export const useVegetationStore = create<VegetationState>()((set) => ({
  mode: "ndvi",
  // Default to 2024-07 — recent date with confirmed GIBS NDVI data
  // (current year/month may not have data due to ~2 month processing delay)
  year: 2024,
  month: 7,
  ndviMode: "absolute",
  showNDWI: false,
  opacity: 0.75,
  setMode: (mode) => set({ mode }),
  setYear: (year) => set({ year }),
  setMonth: (month) => set({ month }),
  setNDVIMode: (ndviMode) => set({ ndviMode }),
  setShowNDWI: (showNDWI) => set({ showNDWI }),
  setOpacity: (opacity) => set({ opacity }),
}));
