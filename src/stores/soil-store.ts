import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type { SoilProperty } from "@/components/map/layers/SoilLayer";

interface SoilState {
  property: SoilProperty;
  opacity: number;
  setProperty: (p: SoilProperty) => void;
  setOpacity: (o: number) => void;
}

export const useSoilStore = create<SoilState>()(
  devtools((set) => ({
    property: "soc",
    opacity: 0.7,
    setProperty: (property) => set({ property }),
    setOpacity: (opacity) => set({ opacity }),
  }))
);
