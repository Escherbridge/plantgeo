import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type { SoilProperty } from "@/components/map/layers/SoilLayer";
import {
  DEFAULT_SOIL_FIELD_DEPTHS,
  type SoilFieldDepth,
  type SoilFieldMeasure,
} from "@/lib/environmental/soil-field";

interface SoilState {
  property: SoilProperty;
  opacity: number;
  /**
   * Which ECMWF soil layer each field draws, per measure. A DEPTH selector, not a second
   * time control: the day always comes from the global slider. See
   * `src/components/map/AGENTS.md` "One time control, projected per layer".
   *
   * Keyed by measure rather than held as two flat fields so the panel renders one section
   * per measure off one shape, and adding a third field cannot forget a setter.
   */
  fieldDepth: Record<SoilFieldMeasure, SoilFieldDepth>;
  setProperty: (p: SoilProperty) => void;
  setOpacity: (o: number) => void;
  setFieldDepth: (measure: SoilFieldMeasure, depth: SoilFieldDepth) => void;
}

export const useSoilStore = create<SoilState>()(
  devtools((set) => ({
    property: "soc",
    opacity: 0.7,
    fieldDepth: DEFAULT_SOIL_FIELD_DEPTHS,
    setProperty: (property) => set({ property }),
    setOpacity: (opacity) => set({ opacity }),
    setFieldDepth: (measure, depth) =>
      set((state) => ({ fieldDepth: { ...state.fieldDepth, [measure]: depth } })),
  }))
);
