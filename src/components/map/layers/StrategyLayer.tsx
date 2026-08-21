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
// The Martin source id for geo.strategy_recommendations_tiles, matching the bare ids in
// infra/martin/martin.yaml. This is NOT "martin-dynamic" any more -- that composite was
// split into one source per function on 2026-08-21 (src/lib/map/sources.ts).
//
// Two registrations still have to land before this layer can draw, and neither is in this
// file: geo.strategy_recommendations_tiles is absent from infra/martin/martin.yaml (Martin
// runs auto_publish: false, so it serves nothing for this id), and the id is therefore also
// absent from DYNAMIC_TILE_SOURCE_IDS, so no style declares the source. Until both land,
// map.getSource() returns undefined and the effect below returns early -- the layer is inert
// rather than broken, and adding the id to either list before Martin answers would leave a
// source that never resolves, holding map.isStyleLoaded() false for the whole session.
const SOURCE_ID = "strategy_recommendations_tiles";

// The ST_AsMVT tag geo.strategy_recommendations_tiles actually emits (drizzle/0028, which
// spells it 'strategy-recommendations'). It was "strategy_recommendations" here, with an
// underscore, which matches nothing in the tile and renders nothing while reporting no error.
const SOURCE_LAYER = "strategy-recommendations";

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
          "source-layer": SOURCE_LAYER,
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
          "source-layer": SOURCE_LAYER,
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
