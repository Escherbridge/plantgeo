import { create } from "zustand";
import { devtools } from "zustand/middleware";
import {
  DEFAULT_AIR_TEMPERATURE_VARIANT,
  DEFAULT_CLIMATE_FIELD_SIGNAL,
  type AirTemperatureVariant,
  type ClimateFieldSignalId,
} from "@/lib/environmental/climate-field";

/**
 * Climate DISPLAY state: which of the NASA POWER lane's nine signals the one `climate-field`
 * layer draws, and which daily statistic when that signal is air temperature.
 *
 * No `opacity` and no `date`. Opacity is per `LayerToggleId` in `layer-store.layerOpacity`,
 * and the day always comes from the global time slider -- this is a SIGNAL selector, never a
 * second time control. See `src/components/map/AGENTS.md` "One time control, projected per
 * layer".
 */
interface ClimateState {
  signal: ClimateFieldSignalId;
  /**
   * Held even while a non-temperature signal is selected, so switching back to air
   * temperature restores the statistic the reader last chose rather than snapping to mean.
   */
  airTemperatureVariant: AirTemperatureVariant;
  setSignal: (signal: ClimateFieldSignalId) => void;
  setAirTemperatureVariant: (variant: AirTemperatureVariant) => void;
}

export const useClimateStore = create<ClimateState>()(
  devtools(
    (set) => ({
      signal: DEFAULT_CLIMATE_FIELD_SIGNAL,
      airTemperatureVariant: DEFAULT_AIR_TEMPERATURE_VARIANT,
      setSignal: (signal) => set({ signal }),
      setAirTemperatureVariant: (airTemperatureVariant) => set({ airTemperatureVariant }),
    }),
    { name: "climate-store" }
  )
);
