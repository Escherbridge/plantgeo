"use client";

import { useMemo } from "react";
import type { CircleLayerSpecification, Map as MapLibreMap } from "maplibre-gl";
import type { ForecastVariable, WeatherForecastValue } from "@/lib/environmental/weather-forecast";
import { scalarForecastFeatures } from "./weather-forecast/presentation";
import { useForecastMapSource } from "./weather-forecast/useForecastMapSource";

interface WeatherFieldLayerProps {
  map: MapLibreMap | null;
  values: WeatherForecastValue[];
  runId: string;
  validAt: string;
  variable: ForecastVariable;
}

/** Draw scalar samples; see weather-forecast/AGENTS.md for support limits. */
export function WeatherFieldLayer({ map, values, runId, validAt, variable }: WeatherFieldLayerProps) {
  const data = useMemo(() => scalarForecastFeatures(values, runId, validAt, variable), [values, runId, validAt, variable]);
  const layer = useMemo<CircleLayerSpecification>(() => ({
    id: "weather-forecast-scalar", source: "weather-forecast-scalar", type: "circle",
    paint: { "circle-radius": 5, "circle-color": ["get", "color"], "circle-stroke-color": "#0f172a",
      "circle-stroke-width": 1, "circle-opacity": 0.85 },
  }), []);
  useForecastMapSource(map, "weather-forecast-scalar", layer, data);
  return null;
}
