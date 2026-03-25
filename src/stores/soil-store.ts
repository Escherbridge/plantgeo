import { create } from "zustand";
import type { SoilProperty } from "@/components/map/layers/SoilLayer";

interface SoilState {
  property: SoilProperty;
  opacity: number;
  setProperty: (p: SoilProperty) => void;
  setOpacity: (o: number) => void;
}

export const useSoilStore = create<SoilState>()((set) => ({
  property: "soc",
  opacity: 0.7,
  setProperty: (property) => set({ property }),
  setOpacity: (opacity) => set({ opacity }),
}));
