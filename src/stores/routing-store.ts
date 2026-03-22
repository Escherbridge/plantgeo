import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type { DecodedRoute } from "@/lib/server/services/routing";

export type TransportMode = "car" | "bike" | "pedestrian" | "truck";

export interface Waypoint {
  lat: number;
  lon: number;
  label?: string;
}

interface RoutingState {
  origin: Waypoint | null;
  destination: Waypoint | null;
  waypoints: Waypoint[];
  activeRoute: DecodedRoute | null;
  alternatives: DecodedRoute[];
  transportMode: TransportMode;
  isCalculating: boolean;

  setOrigin: (origin: Waypoint | null) => void;
  setDestination: (destination: Waypoint | null) => void;
  setWaypoints: (waypoints: Waypoint[]) => void;
  addWaypoint: (waypoint: Waypoint) => void;
  removeWaypoint: (index: number) => void;
  setActiveRoute: (route: DecodedRoute | null) => void;
  setAlternatives: (alternatives: DecodedRoute[]) => void;
  selectAlternative: (index: number) => void;
  setTransportMode: (mode: TransportMode) => void;
  setIsCalculating: (calculating: boolean) => void;
  reset: () => void;
}

export const useRoutingStore = create<RoutingState>()(
  devtools((set, get) => ({
    origin: null,
    destination: null,
    waypoints: [],
    activeRoute: null,
    alternatives: [],
    transportMode: "car",
    isCalculating: false,

    setOrigin: (origin) => set({ origin }),
    setDestination: (destination) => set({ destination }),
    setWaypoints: (waypoints) => set({ waypoints }),
    addWaypoint: (waypoint) =>
      set((s) => ({ waypoints: [...s.waypoints, waypoint] })),
    removeWaypoint: (index) =>
      set((s) => ({
        waypoints: s.waypoints.filter((_, i) => i !== index),
      })),
    setActiveRoute: (route) => set({ activeRoute: route }),
    setAlternatives: (alternatives) => set({ alternatives }),
    selectAlternative: (index) => {
      const { activeRoute, alternatives } = get();
      if (index < 0 || index >= alternatives.length) return;
      const selected = alternatives[index];
      const remaining = alternatives.filter((_, i) => i !== index);
      if (activeRoute) {
        set({ activeRoute: selected, alternatives: [activeRoute, ...remaining] });
      } else {
        set({ activeRoute: selected, alternatives: remaining });
      }
    },
    setTransportMode: (mode) => set({ transportMode: mode }),
    setIsCalculating: (calculating) => set({ isCalculating: calculating }),
    reset: () =>
      set({
        origin: null,
        destination: null,
        waypoints: [],
        activeRoute: null,
        alternatives: [],
        isCalculating: false,
      }),
  }))
);
