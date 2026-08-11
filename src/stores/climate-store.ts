import { create } from "zustand";
import { devtools } from "zustand/middleware";
import {
  DEFAULT_AIR_TEMPERATURE_VARIANT,
  resolveClimateRenderForm,
  type AirTemperatureVariant,
  type ClimateFieldSignalId,
  type ClimateRenderForm,
} from "@/lib/environmental/climate-field";

/**
 * Climate DISPLAY state: how each of the NASA POWER lane's nine signals is drawn when its own
 * row is switched on, and which daily statistic air temperature reports.
 *
 * No `signal`, and its removal on 2026-08-10 is the point of this store's current shape. There
 * used to be one -- the single `climate-field` layer drew one signal at a time and this held
 * which -- and it made "which signal is on the map" a question with exactly one answer. Each
 * signal now owns a registry toggle, so that question is answered by `layer-store.activeLayers`
 * like it is for every other layer, and what is left here is the two things the toggle cannot
 * say: the FORM a signal is painted in, and the air-temperature statistic.
 *
 * No `opacity` and no `date`, unchanged. Opacity is per `LayerToggleId` in
 * `layer-store.layerOpacity`, and each row's day comes from its own slider -- this is never a
 * second time control. See `src/components/map/AGENTS.md` "Every layer draws its own day".
 */
interface ClimateState {
  /**
   * The form each signal is drawn in, SPARSE: a signal with no entry is on its own default,
   * not on a shared one. Sparse rather than seeded with nine defaults because the defaults
   * live on the signal definitions, and a copy here would be a second place for them to drift
   * -- and a persisted copy would pin a signal to a form its `renderForms` list has since
   * stopped offering. Read it through `climateRenderForm` below, never directly.
   */
  renderForms: Partial<Record<ClimateFieldSignalId, ClimateRenderForm>>;
  /**
   * Held even while air temperature's own row is switched off, so turning it back on restores
   * the statistic the reader last chose rather than snapping to mean.
   */
  airTemperatureVariant: AirTemperatureVariant;
  setRenderForm: (signal: ClimateFieldSignalId, form: ClimateRenderForm) => void;
  setAirTemperatureVariant: (variant: AirTemperatureVariant) => void;
}

export const useClimateStore = create<ClimateState>()(
  devtools(
    (set) => ({
      renderForms: {},
      airTemperatureVariant: DEFAULT_AIR_TEMPERATURE_VARIANT,
      setRenderForm: (signal, form) =>
        set((state) => ({ renderForms: { ...state.renderForms, [signal]: form } })),
      setAirTemperatureVariant: (airTemperatureVariant) => set({ airTemperatureVariant }),
    }),
    { name: "climate-store" }
  )
);

/**
 * The form one signal is drawn in: the reader's choice when it is still offered, else the
 * signal's own default.
 *
 * The resolution runs HERE rather than at each renderer, so a form a signal withholds -- a
 * contour across daily rainfall, a contour across the soil-wetness pilot -- cannot reach the
 * map from a stale store entry no matter which surface reads it. See `resolveClimateRenderForm`.
 */
export function climateRenderForm(
  state: Pick<ClimateState, "renderForms">,
  signal: ClimateFieldSignalId
): ClimateRenderForm {
  return resolveClimateRenderForm(signal, state.renderForms[signal]);
}
