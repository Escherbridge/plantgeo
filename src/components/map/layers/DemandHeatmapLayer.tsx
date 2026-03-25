"use client";

import { useEffect, useRef } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { trpc } from "@/lib/trpc/client";
import { DEMO_DEMAND_POINTS } from "@/lib/map/demo-data";

const DEMO_HEATMAP_DATA: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: DEMO_DEMAND_POINTS.map((p) => ({
    type: "Feature" as const,
    geometry: { type: "Point" as const, coordinates: [p.lon, p.lat] },
    properties: { voteCount: Math.round(p.weight * 10) },
  })),
};

const SOURCE_ID = "demand-heatmap-source";
const LAYER_ID = "demand-heatmap-layer";

interface DemandHeatmapLayerProps {
  map: MapLibreMap | null;
  bbox: string;
  visible: boolean;
}

export function DemandHeatmapLayer({ map, bbox, visible }: DemandHeatmapLayerProps) {
  const addedRef = useRef(false);

  const demandQuery = trpc.analytics.getDemandDensity.useQuery(
    { bbox },
    { enabled: visible && !!map }
  );

  // Add source and layer once map is ready
  useEffect(() => {
    if (!map || !visible) return;

    const addLayer = () => {
      if (addedRef.current) return;

      if (!map.getSource(SOURCE_ID)) {
        map.addSource(SOURCE_ID, {
          type: "geojson",
          data: { type: "FeatureCollection", features: [] },
        });
      }

      if (!map.getLayer(LAYER_ID)) {
        map.addLayer({
          id: LAYER_ID,
          type: "heatmap",
          source: SOURCE_ID,
          paint: {
            // Weight by voteCount: 0→0, 10→1
            "heatmap-weight": [
              "interpolate",
              ["linear"],
              ["get", "voteCount"],
              0, 0,
              10, 1,
            ],
            // Intensity increases with zoom
            "heatmap-intensity": [
              "interpolate",
              ["linear"],
              ["zoom"],
              0, 1,
              9, 3,
            ],
            // Emerald gradient: transparent → yellow → dark green
            "heatmap-color": [
              "interpolate",
              ["linear"],
              ["heatmap-density"],
              0,   "rgba(0,0,0,0)",
              0.2, "rgba(134,239,172,0.4)",   // green-300
              0.4, "rgba(74,222,128,0.6)",    // green-400
              0.6, "rgba(234,179,8,0.75)",    // yellow-500
              0.8, "rgba(22,163,74,0.85)",    // green-600
              1,   "rgba(20,83,45,1)",        // green-900
            ],
            // Radius increases with zoom
            "heatmap-radius": [
              "interpolate",
              ["linear"],
              ["zoom"],
              0, 2,
              9, 20,
            ],
            "heatmap-opacity": 0.85,
          },
        });
        addedRef.current = true;
      }
    };

    if (map.isStyleLoaded()) {
      addLayer();
    } else {
      map.once("styledata", addLayer);
    }

    return () => {
      if (!map || !map.isStyleLoaded()) return;
      if (map.getLayer(LAYER_ID)) map.removeLayer(LAYER_ID);
      if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID);
      addedRef.current = false;
    };
  }, [map, visible]);

  // Update source data when query resolves, falling back to demo data on error or empty result
  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;

    const hasRealData =
      !demandQuery.isError &&
      demandQuery.data != null &&
      (demandQuery.data as GeoJSON.FeatureCollection).features?.length > 0;

    const payload: GeoJSON.FeatureCollection = hasRealData
      ? (demandQuery.data as GeoJSON.FeatureCollection)
      : DEMO_HEATMAP_DATA;

    const source = map.getSource(SOURCE_ID);
    if (source && source.type === "geojson") {
      (source as ReturnType<MapLibreMap["getSource"]> & { setData: (data: GeoJSON.FeatureCollection) => void }).setData(
        payload
      );
    }
  }, [map, demandQuery.data, demandQuery.isError]);

  // This component renders nothing itself — it's a map side-effect component
  return null;
}
