import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type { MapStyle, Viewport } from "@/types/map";

export type { Viewport };

interface MapState {
  viewport: Viewport;
  activeLayers: string[];
  selectedFeatureId: string | null;
  is3DEnabled: boolean;
  isGlobeView: boolean;
  terrainExaggeration: number;
  currentStyle: MapStyle;
  isTerrainEnabled: boolean;

  setViewport: (viewport: Partial<Viewport>) => void;
  toggleLayer: (layerId: string) => void;
  selectFeature: (id: string | null) => void;
  toggle3D: () => void;
  toggleGlobe: () => void;
  setTerrainExaggeration: (value: number) => void;
  setCurrentStyle: (style: MapStyle) => void;
  toggleTerrain: () => void;
  resetView: () => void;
}

const DEFAULT_VIEWPORT: Viewport = {
  longitude: -119.4179,
  latitude: 36.7783,
  zoom: 6,
  bearing: 0,
  pitch: 0,
};

export const useMapStore = create<MapState>()(
  devtools((set) => ({
    viewport: { ...DEFAULT_VIEWPORT },
    activeLayers: ["fire-perimeters", "sensors"],
    selectedFeatureId: null,
    is3DEnabled: true,
    isGlobeView: false,
    terrainExaggeration: 1.5,
    currentStyle: "dark",
    isTerrainEnabled: true,

    setViewport: (v) =>
      set((s) => ({ viewport: { ...s.viewport, ...v } })),
    toggleLayer: (id) =>
      set((s) => ({
        activeLayers: s.activeLayers.includes(id)
          ? s.activeLayers.filter((l) => l !== id)
          : [...s.activeLayers, id],
      })),
    selectFeature: (id) => set({ selectedFeatureId: id }),
    toggle3D: () =>
      set((s) => ({
        is3DEnabled: !s.is3DEnabled,
        viewport: {
          ...s.viewport,
          pitch: s.is3DEnabled ? 0 : 60,
        },
      })),
    toggleGlobe: () => set((s) => ({ isGlobeView: !s.isGlobeView })),
    setTerrainExaggeration: (value) => set({ terrainExaggeration: value }),
    setCurrentStyle: (style) => set({ currentStyle: style }),
    toggleTerrain: () => set((s) => ({ isTerrainEnabled: !s.isTerrainEnabled })),
    resetView: () =>
      set({
        viewport: { ...DEFAULT_VIEWPORT },
        is3DEnabled: true,
        isGlobeView: false,
      }),
  }))
);
