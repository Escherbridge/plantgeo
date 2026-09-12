"use client";

import { useMemo } from "react";
import type { SymbolLayerSpecification, Map as MapLibreMap } from "maplibre-gl";
import type { WeatherForecastValue } from "@/lib/environmental/weather-forecast";
import { windForecastFeatures } from "./weather-forecast/presentation";
import { useForecastMapSource } from "./weather-forecast/useForecastMapSource";

interface WeatherWindLayerProps {
  map: MapLibreMap | null;
  values: WeatherForecastValue[];
  runId: string;
  validAt: string;
}

/** Static downwind arrows also serve reduced-motion users. */
export function WeatherWindLayer({ map, values, runId, validAt }: WeatherWindLayerProps) {
  const data = useMemo(() => windForecastFeatures(values, runId, validAt), [values, runId, validAt]);
  const layer = useMemo<SymbolLayerSpecification>(() => ({
    id: "weather-forecast-wind", source: "weather-forecast-wind", type: "symbol",
    layout: { "text-field": "↑", "text-size": 24, "text-rotate": ["get", "to"], "text-rotation-alignment": "map" },
    paint: { "text-color": "#f8fafc", "text-halo-color": "#0f172a", "text-halo-width": 1.5 },
  }), []);
  useForecastMapSource(map, "weather-forecast-wind", layer, data);
  return null;
}
