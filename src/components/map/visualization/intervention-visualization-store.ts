import { create } from "zustand";
import { devtools } from "zustand/middleware";

/** Which extra rendering the `interventions` layer draws; "off" (the default) changes nothing. */
export type InterventionVisualizationMode = "off" | "heatmap" | "points";

interface InterventionVisualizationState {
  mode: InterventionVisualizationMode;
  setMode: (mode: InterventionVisualizationMode) => void;
}

/** Ephemeral (not persisted), so a reload always starts at "off". */
export const useInterventionVisualizationStore = create<InterventionVisualizationState>()(
  devtools((set) => ({
    mode: "off",
    setMode: (mode) => set({ mode }),
  }))
);
