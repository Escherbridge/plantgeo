import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type { MapStyle, Viewport } from "@/types/map";

export type { Viewport };

/** A place on the map the user picked, for the point-query panels to read. */
export interface MapQueryPoint {
  lat: number;
  lon: number;
}

interface MapState {
  viewport: Viewport;
  activeLayers: string[];
  selectedFeatureId: string | null;
  /**
   * The point a panel is querying, or null when none is picked. Interaction state, so it
   * lives beside `selectedFeatureId` rather than in a store of its own -- see
   * src/components/map/AGENTS.md "Picking a point to query".
   */
  queryPoint: MapQueryPoint | null;
  /**
   * True while a panel is capturing map clicks as query points. MapView's own click handler
   * reads this and stands down, so one click cannot both drop a query pin and open the
   * agent popup.
   */
  isCapturingQueryPoint: boolean;
  is3DEnabled: boolean;
  isGlobeView: boolean;
  terrainExaggeration: number;
  currentStyle: MapStyle;
  isTerrainEnabled: boolean;

  setViewport: (viewport: Partial<Viewport>) => void;
  toggleLayer: (layerId: string) => void;
  selectFeature: (id: string | null) => void;
  setQueryPoint: (point: MapQueryPoint | null) => void;
  /** Arms/disarms click capture; disarming always clears the point it was capturing. */
  setCapturingQueryPoint: (capturing: boolean) => void;
  toggle3D: () => void;
  toggleGlobe: () => void;
  setTerrainExaggeration: (value: number) => void;
  setCurrentStyle: (style: MapStyle) => void;
  toggleTerrain: () => void;
  resetView: () => void;
}

// PNW-wide: matches the INGEST_BBOX coverage area so the map opens on data
// instead of a Boise close-up; ServiceAreaLayer's fitBounds also lands users
// on the exact coverage bbox once it resolves, covering any drift here.
export const DEFAULT_VIEWPORT: Viewport = {
  longitude: -118,
  latitude: 45.5,
  zoom: 5.2,
  bearing: 0,
  pitch: 0,
  // The window every viewport-scoped query silently assumed before MapView started reporting
  // the real one. Kept as the seed so the first frame -- rendered before the map has a
  // container to measure -- asks for exactly what it always asked for.
  widthPx: 1024,
  heightPx: 512,
};

export const useMapStore = create<MapState>()(
  devtools((set) => ({
    viewport: { ...DEFAULT_VIEWPORT },
    // Deliberately empty. A layer switched on before the map has mounted reads as
    // enabled while drawing nothing: the dynamically imported layers register their
    // `style.load` listener only once their chunk resolves, which is later than the
    // single rAF-scheduled initial style load they need to observe. Starting with
    // nothing on means every layer is switched on after mount, which is the path that
    // works. See src/components/map/AGENTS.md.
    activeLayers: [],
    selectedFeatureId: null,
    queryPoint: null,
    isCapturingQueryPoint: false,
    is3DEnabled: false,
    isGlobeView: false,
    terrainExaggeration: 1.5,
    currentStyle: "dark",
    isTerrainEnabled: false,

    setViewport: (v) =>
      set((s) => ({ viewport: { ...s.viewport, ...v } })),
    toggleLayer: (id) =>
      set((s) => ({
        activeLayers: s.activeLayers.includes(id)
          ? s.activeLayers.filter((l) => l !== id)
          : [...s.activeLayers, id],
      })),
    selectFeature: (id) => set({ selectedFeatureId: id }),
    setQueryPoint: (point) => set({ queryPoint: point }),
    // Disarming clears the point as well, so closing the panel that armed capture can never
    // leave a pin on the map with nothing reading it.
    setCapturingQueryPoint: (capturing) =>
      set(capturing ? { isCapturingQueryPoint: true } : { isCapturingQueryPoint: false, queryPoint: null }),
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
    // is3DEnabled must agree with DEFAULT_VIEWPORT.pitch, or the 3D control
    // reports a tilt the camera does not have.
    resetView: () =>
      set({
        viewport: { ...DEFAULT_VIEWPORT },
        is3DEnabled: DEFAULT_VIEWPORT.pitch > 0,
        isGlobeView: false,
      }),
  }))
);
