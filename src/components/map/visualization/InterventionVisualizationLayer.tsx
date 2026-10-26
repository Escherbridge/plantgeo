"use client";

import { useCallback, useEffect } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { useLayerToggle } from "@/lib/map/layer-toggle-context";
import { MARTIN_SOURCE_BY_LAYER_TOGGLE } from "@/lib/map/layers";
import {
  useInterventionVisualizationStore,
  type InterventionVisualizationMode,
} from "@/components/map/visualization/intervention-visualization-store";

const INTERVENTIONS_SOURCE_ID = MARTIN_SOURCE_BY_LAYER_TOGGLE.interventions ?? "intervention_tiles";
// `geo.intervention_tiles()`'s MVT layer name, the literal `layers.ts` repeats on its own layers.
const INTERVENTIONS_SOURCE_LAYER = "interventions";

const HEATMAP_LAYER_ID = "interventions-visualization-heatmap";

interface InterventionVisualizationLayerProps {
  map: MapLibreMap | null;
}

function visibilityFor(
  mode: InterventionVisualizationMode,
  target: InterventionVisualizationMode
): "visible" | "none" {
  return mode === target ? "visible" : "none";
}

/**
 * Opt-in point-intervention heatmap over the shared `intervention_tiles` Martin source, drawn only
 * while the `interventions` toggle is on. See src/components/map/AGENTS.md "Interventions
 * visualization modes".
 */
export function InterventionVisualizationLayer({ map }: InterventionVisualizationLayerProps) {
  const isInterventionsLayerOn = useLayerToggle("interventions");
  const mode = useInterventionVisualizationStore((state) => state.mode);
  const shouldRender = isInterventionsLayerOn && mode !== "off";

  const addAllLayers = useCallback((m: MapLibreMap) => {
    if (!m.getLayer(HEATMAP_LAYER_ID)) {
      m.addLayer({
        id: HEATMAP_LAYER_ID,
        type: "heatmap",
        source: INTERVENTIONS_SOURCE_ID,
        "source-layer": INTERVENTIONS_SOURCE_LAYER,
        minzoom: 6,
        // Point interventions only: a polygon would contribute one point per vertex.
        filter: ["==", ["geometry-type"], "Point"],
        layout: { visibility: "none" },
        paint: {
          "heatmap-weight": 1,
          "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 6, 1, 16, 3],
          "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 6, 8, 16, 30],
          "heatmap-opacity": 0.8,
          // MapLibre's stock heatmap-density ramp; the mode is opt-in and unlegended.
          "heatmap-color": [
            "interpolate",
            ["linear"],
            ["heatmap-density"],
            0, "rgba(33,102,172,0)",
            0.3, "rgb(103,169,207)",
            0.6, "rgb(253,219,199)",
            1, "rgb(178,24,43)",
          ],
        },
      });
    }
  }, []);

  // Never removes the shared Martin source: the base interventions layers read it too.
  const removeAllLayers = useCallback((m: MapLibreMap) => {
    if (!m.getStyle()) return;
    for (const id of [HEATMAP_LAYER_ID]) {
      if (m.getLayer(id)) m.removeLayer(id);
    }
  }, []);

  // Style swaps wipe custom layers; mount after LayerManager so this stacks above the base layers.
  useEffect(() => {
    if (!map) return;
    const onStyleLoad = () => addAllLayers(map);
    map.on("style.load", onStyleLoad);
    if (map.isStyleLoaded()) addAllLayers(map);
    return () => {
      map.off("style.load", onStyleLoad);
      removeAllLayers(map);
    };
  }, [map, addAllLayers, removeAllLayers]);

  // Mode flips only change visibility; `styledata` re-applies after a layer is added late.
  useEffect(() => {
    if (!map) return;
    const apply = () => {
      if (map.getLayer(HEATMAP_LAYER_ID)) {
        map.setLayoutProperty(HEATMAP_LAYER_ID, "visibility", shouldRender ? visibilityFor(mode, "heatmap") : "none");
      }
    };
    apply();
    map.on("styledata", apply);
    return () => {
      map.off("styledata", apply);
    };
  }, [map, mode, shouldRender]);

  return null;
}
