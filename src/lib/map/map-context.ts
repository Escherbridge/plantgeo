"use client";

import { createContext, useContext } from "react";
import type maplibregl from "maplibre-gl";

const MapContext = createContext<maplibregl.Map | null>(null);

export const MapProvider = MapContext.Provider;

export function useMap(): maplibregl.Map | null {
  return useContext(MapContext);
}
