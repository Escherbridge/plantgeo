"use client";

import { useCallback, useEffect } from "react";
import type { Map as MapLibreMap, GeoJSONSource } from "maplibre-gl";
import { useLayerToggle } from "@/lib/map/layer-toggle-context";
import { MARTIN_SOURCE_BY_LAYER_TOGGLE, INTERVENTION_STATUS_COLOR } from "@/lib/map/layers";
import {
  useInterventionVisualizationStore,
  type InterventionVisualizationMode,
} from "@/components/map/visualization/intervention-visualization-store";
import { useInterventionFlowData, type InterventionFlowEdge } from "@/components/map/visualization/useInterventionFlowData";

// `MARTIN_SOURCE_BY_LAYER_TOGGLE.interventions` is "intervention_tiles" -- the fallback literal
// is defensive only, matching the one `layers.ts` itself uses, and keeps this module from
// crashing if the registry entry is ever renamed without updating this file.
const INTERVENTIONS_SOURCE_ID = MARTIN_SOURCE_BY_LAYER_TOGGLE.interventions ?? "intervention_tiles";
// `geo.intervention_tiles()`'s MVT layer name -- the same literal `layers.ts` repeats at each of
// its own three style layers over this source (there is no exported constant for it there).
const INTERVENTIONS_SOURCE_LAYER = "interventions";

const HEATMAP_LAYER_ID = "interventions-visualization-heatmap";
const POINTS_LAYER_ID = "interventions-visualization-points";
const FLOW_SOURCE_ID = "interventions-visualization-flow-source";
const FLOW_LAYER_ID = "interventions-visualization-flow";

interface InterventionVisualizationLayerProps {
  map: MapLibreMap | null;
}

function emptyFlowCollection(): GeoJSON.FeatureCollection<GeoJSON.LineString> {
  return { type: "FeatureCollection", features: [] };
}

function flowGeoJSON(
  edges: ReadonlyArray<InterventionFlowEdge>
): GeoJSON.FeatureCollection<GeoJSON.LineString> {
  return {
    type: "FeatureCollection",
    features: edges.map((edge) => ({
      type: "Feature",
      properties: { value: edge.value },
      geometry: { type: "LineString", coordinates: [edge.source, edge.target] },
    })),
  };
}

function visibilityFor(
  mode: InterventionVisualizationMode,
  target: InterventionVisualizationMode
): "visible" | "none" {
  return mode === target ? "visible" : "none";
}

/**
 * The off/heatmap/points/flow extra renderings for the `interventions` toggle, ADDITIVE to its
 * always-on fill/outline/points (`interventionsLayer`/`interventionsOutlineLayer`/
 * `interventionsPointsLayer` in `src/lib/map/layers.ts`) -- see
 * src/components/map/AGENTS.md "Interventions visualization modes" for the full design. Default
 * mode is "off" (`useInterventionVisualizationStore`), so this draws nothing until a reader opens
 * the row's control (`InterventionVisualizationModeControl`, mounted in `LayerRow`).
 *
 * Heatmap and points reuse the already-published `intervention_tiles` Martin source -- no extra
 * network cost, no tRPC round trip. Martin already projects everything these two need (category/
 * status/kind feed `INTERVENTION_STATUS_COLOR`; point density is MapLibre's own heatmap
 * aggregation over the same rows). Flow is the one mode with no admitted tile data: no
 * intervention feature carries a `source`/`target` pair today, so it alone reads
 * `visualization.getFlowData` over a private GeoJSON source this component fills itself. If that
 * data never arrives, the line layer simply draws nothing -- the same "empty feed, not a hidden
 * layer" rule `src/components/map/AGENTS.md` states for every other viewport-proxied read.
 *
 * Gated on the `interventions` toggle itself (`isInterventionsLayerOn`): a sub-control of a
 * layer's row must not keep drawing once that layer's own eye is off, matching every other
 * per-row control (`LayerOpacitySlider`, `LayerTimeSlider`) on the same row.
 */
export function InterventionVisualizationLayer({ map }: InterventionVisualizationLayerProps) {
  const isInterventionsLayerOn = useLayerToggle("interventions");
  const mode = useInterventionVisualizationStore((state) => state.mode);
  const shouldRender = isInterventionsLayerOn && mode !== "off";

  const { edges } = useInterventionFlowData(shouldRender && mode === "flow" ? "flow" : "off");

  const addAllLayers = useCallback((m: MapLibreMap) => {
    if (!m.getLayer(HEATMAP_LAYER_ID)) {
      m.addLayer({
        id: HEATMAP_LAYER_ID,
        type: "heatmap",
        source: INTERVENTIONS_SOURCE_ID,
        "source-layer": INTERVENTIONS_SOURCE_LAYER,
        minzoom: 6,
        layout: { visibility: "none" },
        paint: {
          "heatmap-weight": 1,
          "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 6, 1, 16, 3],
          "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 6, 8, 16, 30],
          "heatmap-opacity": 0.8,
          // MapLibre's own published heatmap-density colour ramp (the stock example every
          // heatmap-layer tutorial ships), not an authored palette -- there is nothing
          // layer-legends.ts needs to own here since this mode is opt-in and unlegended, like the
          // other diagnostic overlays in that module's `LEGENDLESS_TOGGLE_REASONS` table.
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
    if (!m.getLayer(POINTS_LAYER_ID)) {
      m.addLayer({
        id: POINTS_LAYER_ID,
        type: "circle",
        source: INTERVENTIONS_SOURCE_ID,
        "source-layer": INTERVENTIONS_SOURCE_LAYER,
        minzoom: 6,
        layout: { visibility: "none" },
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 6, 3, 14, 9],
          // Shares the ONE colour authority every other intervention layer paints from -- see
          // src/components/map/AGENTS.md "The legend IS the active-layer chips" ("no colour is
          // written in the legend [or any renderer]: every swatch is imported from the module
          // whose paint expression uses it").
          "circle-color": INTERVENTION_STATUS_COLOR,
          "circle-stroke-width": 1,
          "circle-stroke-color": "#ffffff",
        },
      });
    }
    if (!m.getSource(FLOW_SOURCE_ID)) {
      m.addSource(FLOW_SOURCE_ID, { type: "geojson", data: emptyFlowCollection() });
    }
    if (!m.getLayer(FLOW_LAYER_ID)) {
      m.addLayer({
        id: FLOW_LAYER_ID,
        type: "line",
        source: FLOW_SOURCE_ID,
        layout: { visibility: "none", "line-cap": "round" },
        paint: {
          "line-color": "#0ea5e9",
          "line-width": ["interpolate", ["linear"], ["get", "value"], 0, 1, 50, 6],
          "line-opacity": 0.75,
        },
      });
    }
  }, []);

  // Removes only what THIS component owns. Deliberately never removes `INTERVENTIONS_SOURCE_ID`
  // -- that is the shared Martin source the base interventions fill/outline/points layers read
  // too, and dropping it here would break them.
  const removeAllLayers = useCallback((m: MapLibreMap) => {
    if (!m.getStyle()) return;
    for (const id of [HEATMAP_LAYER_ID, POINTS_LAYER_ID, FLOW_LAYER_ID]) {
      if (m.getLayer(id)) m.removeLayer(id);
    }
    if (m.getSource(FLOW_SOURCE_ID)) m.removeSource(FLOW_SOURCE_ID);
  }, []);

  // Re-added on every style swap, which wipes every custom layer -- see "Style swaps and
  // render-mode state" in src/components/map/AGENTS.md. Mount this component AFTER LayerManager
  // in MapView (see the mount note in this file's sibling, the component's own export) so this
  // handler registers after LayerManager's and this opt-in overlay stacks above the base
  // rendering, matching "Style.load listener order" in the same doc.
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

  // Visibility follows the selected mode; a mode flip never adds/removes the layers themselves,
  // matching LayerManager's own appliers under "isStyleLoaded() is a signal to retry on, never a
  // gate to drop writes behind" -- every write is guarded by `getLayer()` so a pass against a
  // half-built style after a slow source is a no-op rather than a dropped write, and the
  // `styledata` pass is a cheap convergence re-run for a layer added late.
  useEffect(() => {
    if (!map) return;
    const apply = () => {
      if (map.getLayer(HEATMAP_LAYER_ID)) {
        map.setLayoutProperty(
          HEATMAP_LAYER_ID,
          "visibility",
          shouldRender ? visibilityFor(mode, "heatmap") : "none"
        );
      }
      if (map.getLayer(POINTS_LAYER_ID)) {
        map.setLayoutProperty(
          POINTS_LAYER_ID,
          "visibility",
          shouldRender ? visibilityFor(mode, "points") : "none"
        );
      }
      if (map.getLayer(FLOW_LAYER_ID)) {
        map.setLayoutProperty(
          FLOW_LAYER_ID,
          "visibility",
          shouldRender ? visibilityFor(mode, "flow") : "none"
        );
      }
    };
    apply();
    map.on("styledata", apply);
    return () => {
      map.off("styledata", apply);
    };
  }, [map, mode, shouldRender]);

  // Fills the flow source from the viewport-bbox read. Empty until a producer starts writing
  // `source`/`target` onto an intervention's properties (see this component's own module doc) --
  // an empty source draws nothing, which is the correct, honest state for data that does not
  // exist yet, never a hidden layer.
  useEffect(() => {
    if (!map) return;
    const source = map.getSource(FLOW_SOURCE_ID) as GeoJSONSource | undefined;
    source?.setData(flowGeoJSON(edges));
  }, [map, edges]);

  return null;
}
