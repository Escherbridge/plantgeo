"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, GeoJSONSource } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import type { BotanicalAggregateCell } from "@/lib/botanical-occurrences";

const SOURCE_ID = "botanical-collection-effort";
const FILL_LAYER_ID = "botanical-collection-effort-fill";

/** The three effort measures a caller may select, each declaring its own aggregation label. */
export type BotanicalEffortMeasure = "record_count" | "event_estimate" | "collection_count";

/**
 * The aggregation this layer declares per measure. Shown in the legend and the filters panel
 * so a broad, sparsely-annotated cell cannot be mistaken for ecological dominance -- the spec's
 * "context layer, not abundance heatmap" requirement.
 */
export const BOTANICAL_EFFORT_MEASURE_LABELS: Record<BotanicalEffortMeasure, string> = {
  record_count: "specimen records — count per cell",
  event_estimate: "distinct collecting events (estimate) — count per cell",
  collection_count: "contributing collections — count per cell",
};

/** Muted, desaturated ramp -- deliberately unlike the richness layer's saturated green ramp. */
const EFFORT_RAMP: [number, string][] = [
  [0, "#3f3f46"],
  [10, "#71717a"],
  [50, "#a1a1aa"],
  [200, "#d4d4d8"],
];

function buildEffortFillExpression(measure: BotanicalEffortMeasure): unknown[] {
  const stops = EFFORT_RAMP.flatMap(([value, color]) => [value, color]);
  return ["interpolate", ["linear"], ["get", measure], ...stops];
}

/** Converts wire aggregate cells into the FeatureCollection this layer draws. */
export function botanicalEffortToGeoJSON(cells: BotanicalAggregateCell[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: cells.map((cell) => ({
      type: "Feature",
      geometry: cell.geometry,
      properties: {
        cell_id: cell.cell_id,
        record_count: cell.record_count,
        event_estimate: cell.event_estimate,
        collection_count: cell.collection_count,
      },
    })),
  };
}

interface BotanicalCollectionEffortLayerProps {
  map: MapLibreMap | null;
  geojson: GeoJSON.FeatureCollection | null;
  measure: BotanicalEffortMeasure;
  opacity?: number;
  visible?: boolean;
}

/**
 * Context layer for collecting effort/bias. Deliberately its own source/layer ids (not shared
 * with `BotanicalRichnessLayer`) so the two can be toggled independently even though both read
 * the same aggregate response -- matching the "richness view should make this layer or
 * equivalent disclosure easy to reach" acceptance note without forcing simultaneous rungs.
 */
export function BotanicalCollectionEffortLayer({
  map,
  geojson,
  measure,
  opacity = 0.55,
  visible = true,
}: BotanicalCollectionEffortLayerProps) {
  const propsRef = useRef({ geojson, visible, measure });
  propsRef.current = { geojson, visible, measure };

  const addLayers = useCallback((m: MapLibreMap) => {
    const { geojson, measure } = propsRef.current;
    if (!geojson) return;
    const beforeId = getFirstSymbolLayer(m);

    if (!m.getSource(SOURCE_ID)) {
      m.addSource(SOURCE_ID, { type: "geojson", data: geojson });
    } else {
      (m.getSource(SOURCE_ID) as GeoJSONSource).setData(geojson);
    }

    if (!m.getLayer(FILL_LAYER_ID)) {
      m.addLayer(
        {
          id: FILL_LAYER_ID,
          type: "fill",
          source: SOURCE_ID,
          paint: {
            "fill-color": buildEffortFillExpression(measure) as never,
            "fill-opacity": opacity,
          },
        },
        beforeId
      );
    }
  }, [opacity]);

  const removeLayers = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(m, [FILL_LAYER_ID], SOURCE_ID);
  }, []);

  useEffect(() => {
    if (!map) return;
    if (!visible || !geojson) {
      removeLayers(map);
      return;
    }

    const onStyleLoad = () => {
      const current = propsRef.current;
      if (!current.visible || !current.geojson) return;
      addLayers(map);
    };

    if (map.isStyleLoaded()) {
      addLayers(map);
    } else {
      map.once("style.load", () => addLayers(map));
    }
    map.on("style.load", onStyleLoad);

    return () => {
      map.off("style.load", onStyleLoad);
      removeLayers(map);
    };
  }, [map, geojson, visible, addLayers, removeLayers]);

  // Measure changes rebuild the fill expression: setPaintProperty rather than remove/re-add,
  // matching DroughtLayer's opacity-only update effect for cheap in-place paint edits.
  useEffect(() => {
    if (!map || !visible) return;
    try {
      if (!map.getStyle()) return;
    } catch {
      return;
    }
    if (map.getLayer(FILL_LAYER_ID)) {
      map.setPaintProperty(FILL_LAYER_ID, "fill-color", buildEffortFillExpression(measure) as never);
      map.setPaintProperty(FILL_LAYER_ID, "fill-opacity", opacity);
    }
  }, [map, measure, opacity, visible]);

  return null;
}
