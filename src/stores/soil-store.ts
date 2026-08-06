import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type { SoilProperty } from "@/components/map/layers/SoilLayer";
import {
  DEFAULT_SOIL_MOISTURE_DEPTH,
  type SoilMoistureDepth,
} from "@/lib/environmental/soil-moisture";

interface SoilState {
  property: SoilProperty;
  opacity: number;
  /**
   * Which ECMWF soil layer the moisture field draws. A DEPTH selector, not a second time
   * control: the day always comes from the global slider. See src/components/map/AGENTS.md
   * "One time control, projected per layer".
   */
  moistureDepth: SoilMoistureDepth;
  setProperty: (p: SoilProperty) => void;
  setOpacity: (o: number) => void;
  setMoistureDepth: (depth: SoilMoistureDepth) => void;
}

export const useSoilStore = create<SoilState>()(
  devtools((set) => ({
    property: "soc",
    opacity: 0.7,
    moistureDepth: DEFAULT_SOIL_MOISTURE_DEPTH,
    setProperty: (property) => set({ property }),
    setOpacity: (opacity) => set({ opacity }),
    setMoistureDepth: (moistureDepth) => set({ moistureDepth }),
  }))
);
