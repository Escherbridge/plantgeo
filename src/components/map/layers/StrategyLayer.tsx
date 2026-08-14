"use client";

import { useEffect } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { useMapStore } from "@/stores/map-store";
import { STRATEGY_COLOR_RAMPS } from "@/lib/map/layers/strategy-layer";

interface StrategyLayerProps {
  map: MapLibreMap | null;
  loaded: boolean;
}

const LAYER_FILL_ID = "strategy-recommendations-fill";
const LAYER_OUTLINE_ID = "strategy-recommendations-outline";
const SOURCE_ID = "martin-dynamic";

export function StrategyLayer({ map, loaded }: StrategyLayerProps) {
  const activeLayers = useMapStore((s) => s.activeLayers);
  const isEnabled = activeLayers.includes("strategy-recommendations");

  useEffect(() => {
    if (!map || !loaded) return;

    const sourceExists = map.getSource(SOURCE_ID);
    if (!sourceExists) return;

    const fillExists = map.getLayer(LAYER_FILL_ID);
    const outlineExists = map.getLayer(LAYER_OUTLINE_ID);

    if (isEnabled) {
      if (!fillExists) {
        map.addLayer({
          id: LAYER_FILL_ID,
          type: "fill",
          source: SOURCE_ID,
          "source-layer": "strategy_recommendations",
          minzoom: 4,
          paint: {
            "fill-color": [
              "match",
              ["get", "strategy_type"],
              "regenerative_ag",
              STRATEGY_COLOR_RAMPS.regenerative_ag,
              "agroforestry",
              STRATEGY_COLOR_RAMPS.agroforestry,
              "biochar",
              STRATEGY_COLOR_RAMPS.biochar,
              "wildfire_buffer",
              STRATEGY_COLOR_RAMPS.wildfire_buffer,
              "water_conservation",
              STRATEGY_COLOR_RAMPS.water_conservation,
              "#9ca3af",
            ],
            "fill-opacity": 0.45,
          },
        });
      }

      if (!outlineExists) {
        map.addLayer({
          id: LAYER_OUTLINE_ID,
          type: "line",
          source: SOURCE_ID,
          "source-layer": "strategy_recommendations",
          minzoom: 4,
          paint: {
            "line-color": "#15803d",
            "line-width": 1.5,
          },
        });
      }

      map.setLayoutProperty(LAYER_FILL_ID, "visibility", "visible");
      map.setLayoutProperty(LAYER_OUTLINE_ID, "visibility", "visible");
    } else {
      if (fillExists) map.setLayoutProperty(LAYER_FILL_ID, "visibility", "none");
      if (outlineExists) map.setLayoutProperty(LAYER_OUTLINE_ID, "visibility", "none");
    }
  }, [map, loaded, isEnabled]);

  return null;
}
