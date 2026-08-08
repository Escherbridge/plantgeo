"use client";

import { useEffect, useRef } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import {
  type ActionNetworkFilters,
  useActionNetworkFeatures,
} from "@/hooks/useActionNetworkFeatures";
import { safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import type { ExpressionSpecification } from "@/types/map";

const SOURCE_ID = "demand-heatmap-source";
const LAYER_ID = "demand-heatmap-layer";

/**
 * Density is sequential magnitude (not diverging), so the ramp stays one hue light-to-dark
 * end to end -- the old ramp detoured through amber mid-ramp. Keyed on heatmap-density,
 * which MapLibre normalises to 0..1, so the stops carry no unit of their own.
 */
export const DEMAND_DENSITY_COLOR_STOPS: readonly { density: number; color: string }[] = [
  { density: 0, color: "rgba(0,0,0,0)" },
  { density: 0.2, color: "rgba(209,250,229,0.4)" },
  { density: 0.4, color: "rgba(110,231,183,0.6)" },
  { density: 0.6, color: "rgba(52,211,153,0.75)" },
  { density: 0.8, color: "rgba(5,150,105,0.85)" },
  { density: 1, color: "rgba(6,78,59,1)" },
];

/** Derived from the stops above so the drawn ramp and the legend cannot drift. */
const DEMAND_HEATMAP_COLOR = [
  "interpolate",
  ["linear"],
  ["heatmap-density"],
  ...DEMAND_DENSITY_COLOR_STOPS.flatMap((stop) => [stop.density, stop.color]),
] as unknown as ExpressionSpecification;

interface DemandHeatmapLayerProps {
  map: MapLibreMap | null;
  bbox: string | null;
  zoom: number;
  visible: boolean;
  filters?: ActionNetworkFilters;
}

export function DemandHeatmapLayer({
  map,
  bbox,
  zoom,
  visible,
  filters,
}: DemandHeatmapLayerProps) {
  const addedRef = useRef(false);
  const actionNetwork = useActionNetworkFeatures(
    bbox,
    zoom,
    visible && !!map && bbox !== null,
    filters
  );
  const actionNetworkDataRef = useRef(actionNetwork.data);

  useEffect(() => {
    actionNetworkDataRef.current = actionNetwork.data;
  }, [actionNetwork.data]);

  useEffect(() => {
    if (!map || !visible) return;

    const addLayer = () => {
      if (!map.isStyleLoaded()) return;
      if (
        addedRef.current &&
        map.getSource(SOURCE_ID) &&
        map.getLayer(LAYER_ID)
      ) {
        return;
      }

      if (!map.getSource(SOURCE_ID)) {
        map.addSource(SOURCE_ID, {
          type: "geojson",
          data: actionNetworkDataRef.current,
        });
      }

      if (!map.getLayer(LAYER_ID)) {
        map.addLayer({
          id: LAYER_ID,
          type: "heatmap",
          source: SOURCE_ID,
          paint: {
            // Weight by voteCount: 0 to 0, 10 to 1.
            "heatmap-weight": [
              "interpolate",
              ["linear"],
              ["get", "voteCount"],
              0,
              0,
              10,
              1,
            ],
            "heatmap-intensity": [
              "interpolate",
              ["linear"],
              ["zoom"],
              0,
              1,
              9,
              3,
            ],
            "heatmap-color": DEMAND_HEATMAP_COLOR,
            "heatmap-radius": [
              "interpolate",
              ["linear"],
              ["zoom"],
              0,
              2,
              9,
              20,
            ],
            "heatmap-opacity": 0.85,
          },
        });
      }
      addedRef.current = Boolean(map.getLayer(LAYER_ID));
    };

    if (map.isStyleLoaded()) addLayer();
    map.on("style.load", addLayer);

    return () => {
      map.off("style.load", addLayer);
      safeRemoveLayerAndSource(map, [LAYER_ID], SOURCE_ID);
      addedRef.current = false;
    };
  }, [map, visible]);

  // The worker returns a bounded, viewport-specific collection.
  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;

    const source = map.getSource(SOURCE_ID);
    if (source && source.type === "geojson") {
      const geoJsonSource = source as ReturnType<
        MapLibreMap["getSource"]
      > & {
        setData: (nextData: GeoJSON.FeatureCollection) => void;
      };
      geoJsonSource.setData(actionNetwork.data);
    }
  }, [actionNetwork.data, map]);

  const status = actionNetwork.isLoading
    ? "Loading action-network data."
    : actionNetwork.error?.code === "ACTION_NETWORK_INACTIVE"
      ? "Action-network waypoints are inactive until a reviewed warehouse publication is available. No points are displayed."
      : actionNetwork.error
      ? "Action-network data is unavailable. No points are displayed."
      : actionNetwork.metadata
        ? `Action-network data loaded: ${actionNetwork.metadata.featureCount} visible point groups. Freshness: ${actionNetwork.metadata.sourceFreshness ?? "unknown"}.`
        : "Action-network data is inactive.";

  return (
    <div
      className={actionNetwork.error ? "absolute bottom-4 left-4 z-40 max-w-sm rounded border border-amber-400 bg-amber-50 p-3 text-sm text-amber-950 shadow dark:bg-amber-950 dark:text-amber-50" : "sr-only"}
      role={actionNetwork.error ? "status" : undefined}
      aria-live="polite"
    >
      <p>{status}</p>
      {actionNetwork.error?.retryable && (
        <button
          type="button"
          onClick={actionNetwork.retry}
          className="mt-2 min-h-11 rounded px-2 text-sm font-medium underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-700"
        >
          Retry action-network data
        </button>
      )}
    </div>
  );
}
