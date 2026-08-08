import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type { VegetationMode, VegetationSource } from "@/components/map/layers/VegetationLayer";

/**
 * Vegetation DISPLAY state only. There is deliberately no year, month or date here: the one
 * notion of "when" is the time slider's selected day (`useTimeSliderStore`), and the GIBS
 * composite's year/month are projected from it by `useVegetationDisplayMode` in
 * src/lib/map/layer-toggle-context.ts. This store held its own `year`/`month` until 2026-08-05,
 * which gave the app two disagreeing clocks and two competing sliders -- do not reintroduce
 * them. See src/components/map/AGENTS.md "One time control, projected per layer".
 *
 * `source` is the only dimension a reader can move here, and it is the whole point of this
 * store: NDVI is drawn EITHER as this platform's measured cells OR as the global GIBS
 * composite, never both. Until 2026-08-08 both were painted at once under a predicate that
 * could never be false, which is what made the layer read as a render glitch.
 *
 * `opacity`/`setOpacity` are gone: opacity is now per LAYER, keyed by `LayerToggleId` in
 * `layer-store.layerOpacity`, so the vegetation raster no longer shares a scalar with
 * anything and the panel's slider and the layer tree's slider write one value.
 *
 * `mode`, `ndviMode` and `showNDWI` deliberately carry no setter. NDWI, NBR and the NDVI
 * anomaly have no published upstream at all (see NDWI_UNAVAILABLE_REASON and
 * NDVI_ANOMALY_UNAVAILABLE_REASON in src/lib/vegetation.ts), so they are constants the
 * renderer still branches on rather than controls a reader can reach -- their setters existed
 * only to be called from permanently disabled checkboxes. Re-add a setter the day a source
 * lands, not before.
 */
interface VegetationState {
  mode: VegetationMode;
  source: VegetationSource;
  ndviMode: "absolute" | "anomaly";
  showNDWI: boolean;
  setSource: (source: VegetationSource) => void;
}

export const useVegetationStore = create<VegetationState>()(
  devtools((set) => ({
    mode: "ndvi",
    // The readings this platform ingested and can cite a scene for are the product; the
    // proxied global composite is the backdrop option, so it is not what a reader gets first.
    source: "measured",
    ndviMode: "absolute",
    showNDWI: false,
    setSource: (source) => set({ source }),
  }))
);
