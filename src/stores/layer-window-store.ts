import { useMemo } from "react";
import { create } from "zustand";
import { devtools, persist } from "zustand/middleware";
import { isLayerToggleId, type LayerToggleId } from "@/lib/map/layer-registry";
import {
  DEFAULT_LAYER_WINDOW_PRESET,
  isLayerWindowPreset,
  trailingLayerWindow,
  type AnalysisLayerWindow,
  type LayerWindowPreset,
} from "@/lib/regional-analysis-selection";
import { resolveLayerDate, useTimeSliderStore } from "@/stores/time-slider-store";
import type { SliderCapabilities } from "@/types/time-slider";

/** Per-layer history window presets; see stores/AGENTS.md §layer-window. */
interface LayerWindowState {
  /** SPARSE: an absent key is the 30-day default, so a new layer needs no migration. */
  layerWindowPresets: Partial<Record<LayerToggleId, LayerWindowPreset>>;
  /** Writing the default deletes the key, so "unset" stays the one spelling of the default. */
  setLayerWindowPreset: (layerId: LayerToggleId, preset: LayerWindowPreset) => void;
}

function sanitizeLayerWindowPresets(value: unknown): Partial<Record<LayerToggleId, LayerWindowPreset>> {
  if (typeof value !== "object" || value === null) return {};
  const sanitized: Partial<Record<LayerToggleId, LayerWindowPreset>> = {};
  for (const [layerId, preset] of Object.entries(value as Record<string, unknown>)) {
    if (!isLayerToggleId(layerId) || !isLayerWindowPreset(preset)) continue;
    if (preset !== DEFAULT_LAYER_WINDOW_PRESET) sanitized[layerId] = preset;
  }
  return sanitized;
}

export const useLayerWindowStore = create<LayerWindowState>()(
  devtools(
    persist(
      (set) => ({
        layerWindowPresets: {},
        setLayerWindowPreset: (layerId, preset) =>
          set((state) => {
            const current = state.layerWindowPresets[layerId] ?? DEFAULT_LAYER_WINDOW_PRESET;
            if (!isLayerWindowPreset(preset) || current === preset) return state;
            const next = { ...state.layerWindowPresets };
            if (preset === DEFAULT_LAYER_WINDOW_PRESET) delete next[layerId];
            else next[layerId] = preset;
            return { layerWindowPresets: next };
          }),
      }),
      {
        // Same persistence shape as layer-store's `plantgeo-layer-opacity`.
        name: "plantgeo-layer-window",
        version: 1,
        partialize: (state) => ({ layerWindowPresets: state.layerWindowPresets }),
        merge: (persisted, current) => ({
          ...current,
          layerWindowPresets: sanitizeLayerWindowPresets(
            (persisted as { layerWindowPresets?: unknown } | undefined)?.layerWindowPresets
          ),
        }),
      }
    ),
    { name: "layer-window" }
  )
);

/** This layer's preset, falling back to the default. */
export function layerWindowPresetFor(
  presets: Partial<Record<LayerToggleId, LayerWindowPreset>>,
  layerId: LayerToggleId
): LayerWindowPreset {
  return presets[layerId] ?? DEFAULT_LAYER_WINDOW_PRESET;
}

/**
 * One layer's window: its preset, trailing from `day` (default: the layer's own resolved day),
 * capped at the server's UTC today. Null until capabilities name a today.
 */
export function layerWindowFor(
  presets: Partial<Record<LayerToggleId, LayerWindowPreset>>,
  layerDates: Record<string, string>,
  capabilities: SliderCapabilities | null,
  layerId: LayerToggleId,
  day: string = resolveLayerDate(layerDates, capabilities, layerId)
): AnalysisLayerWindow | null {
  if (capabilities === null) return null;
  return trailingLayerWindow(day, layerWindowPresetFor(presets, layerId), capabilities.serverCurrentDate);
}

/** One layer's preset and live window, re-derived when its day, preset or today moves. */
export function useLayerWindow(layerId: LayerToggleId): {
  preset: LayerWindowPreset;
  window: AnalysisLayerWindow | null;
} {
  const preset = useLayerWindowStore((state) => layerWindowPresetFor(state.layerWindowPresets, layerId));
  const day = useTimeSliderStore((state) => resolveLayerDate(state.layerDates, state.capabilities, layerId));
  const today = useTimeSliderStore((state) => state.capabilities?.serverCurrentDate ?? null);
  const window = useMemo(
    () => (today === null ? null : trailingLayerWindow(day, preset, today)),
    [day, preset, today]
  );
  return { preset, window };
}
