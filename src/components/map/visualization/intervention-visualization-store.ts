import { create } from "zustand";
import { devtools } from "zustand/middleware";

/**
 * Which EXTRA rendering the `interventions` layer draws, on top of its always-on
 * fill/outline/points. "off" changes nothing from what a reader has always seen -- see
 * src/components/map/AGENTS.md "Interventions visualization modes" for the full design and why
 * heatmap/points read the Martin tile source directly while flow alone calls a tRPC procedure.
 */
export type InterventionVisualizationMode = "off" | "heatmap" | "points" | "flow";

interface InterventionVisualizationState {
  mode: InterventionVisualizationMode;
  setMode: (mode: InterventionVisualizationMode) => void;
}

/**
 * Ephemeral, like `vegetation-store`'s `mode` -- not persisted, so a reload always starts at the
 * default "off" rather than reviving a mode the reader may not remember choosing. Default OFF is
 * the owner's explicit requirement: nothing on the map changes for a reader who never opens this
 * control.
 */
export const useInterventionVisualizationStore = create<InterventionVisualizationState>()(
  devtools((set) => ({
    mode: "off",
    setMode: (mode) => set({ mode }),
  }))
);
