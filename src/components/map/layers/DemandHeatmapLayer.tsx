"use client";

import { useEffect, useRef } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import {
  type ActionNetworkFilters,
  useActionNetworkFeatures,
} from "@/hooks/useActionNetworkFeatures";

const SOURCE_ID = "demand-heatmap-source";
const LAYER_ID = "demand-heatmap-layer";

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
            "heatmap-color": [
              "interpolate",
              ["linear"],
              ["heatmap-density"],
              0,
              "rgba(0,0,0,0)",
              0.2,
              "rgba(134,239,172,0.4)",
              0.4,
              "rgba(74,222,128,0.6)",
              0.6,
              "rgba(234,179,8,0.75)",
              0.8,
              "rgba(22,163,74,0.85)",
              1,
              "rgba(20,83,45,1)",
            ],
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

    map.on("style.load", addLayer);
    if (map.isStyleLoaded()) {
      addLayer();
    } else {
      map.once("styledata", addLayer);
    }

    return () => {
      map.off("style.load", addLayer);
      map.off("styledata", addLayer);
      if (map.isStyleLoaded()) {
        if (map.getLayer(LAYER_ID)) map.removeLayer(LAYER_ID);
        if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID);
      }
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
