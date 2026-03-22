import { create } from "zustand";
import { devtools } from "zustand/middleware";

export interface AssetPosition {
  assetId: string;
  lat: number;
  lon: number;
  heading?: number;
  speed?: number;
  altitude?: number;
  time: string;
}

export interface Geofence {
  id: string;
  name: string;
  geometry: GeoJSON.Polygon;
  alertOnEnter: boolean;
  alertOnExit: boolean;
}

interface TrackingFilters {
  status: "all" | "active" | "idle" | "offline";
}

interface TrackingStoreState {
  vehicles: Map<string, AssetPosition>;
  selectedVehicleId: string | null;
  filters: TrackingFilters;
  geofences: Geofence[];

  setVehiclePosition: (id: string, pos: AssetPosition) => void;
  selectVehicle: (id: string | null) => void;
  setFilter: (filters: Partial<TrackingFilters>) => void;
  setGeofences: (geofences: Geofence[]) => void;
  addGeofence: (geofence: Geofence) => void;
  removeGeofence: (id: string) => void;
}

export const useTrackingStore = create<TrackingStoreState>()(
  devtools((set) => ({
    vehicles: new Map(),
    selectedVehicleId: null,
    filters: { status: "all" },
    geofences: [],

    setVehiclePosition: (id, pos) =>
      set((s) => {
        const next = new Map(s.vehicles);
        next.set(id, pos);
        return { vehicles: next };
      }),

    selectVehicle: (id) => set({ selectedVehicleId: id }),

    setFilter: (filters) =>
      set((s) => ({ filters: { ...s.filters, ...filters } })),

    setGeofences: (geofences) => set({ geofences }),

    addGeofence: (geofence) =>
      set((s) => ({ geofences: [...s.geofences, geofence] })),

    removeGeofence: (id) =>
      set((s) => ({ geofences: s.geofences.filter((g) => g.id !== id) })),
  }))
);
