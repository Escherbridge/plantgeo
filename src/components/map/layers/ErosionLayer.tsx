"use client";

import { useEffect, useRef } from "react";
import type { Map as MapLibreMap, GeoJSONSource } from "maplibre-gl";
import { EROSION_COLORS, type ErosionClass } from "@/lib/server/services/usle";

interface ErosionFeatureProperties {
  erosionClass: ErosionClass;
  erosionScore: number;
  color: string;
  label: string;
}

interface ErosionPoint {
  lat: number;
  lon: number;
  erosionClass: ErosionClass;
  erosionScore: number;
}

interface ErosionLayerProps {
  map: MapLibreMap | null;
  points?: ErosionPoint[];
  opacity?: number;
}

const EROSION_SOURCE = "erosion-points-source";
const EROSION_CIRCLES_LAYER = "erosion-circles-layer";
const EROSION_LABELS_LAYER = "erosion-labels-layer";

const EROSION_CLASS_LABELS: Record<ErosionClass, string> = {
  very_low: "Very Low",
  low: "Low",
  moderate: "Moderate",
  high: "High",
  very_high: "Very High",
};

export function ErosionLayer({
  map,
  points = [],
  opacity = 0.8,
}: ErosionLayerProps) {
  const addedRef = useRef(false);

  function buildGeoJSON(): GeoJSON.FeatureCollection {
    return {
      type: "FeatureCollection",
      features: points.map((p) => {
        const props: ErosionFeatureProperties = {
          erosionClass: p.erosionClass,
          erosionScore: p.erosionScore,
          color: EROSION_COLORS[p.erosionClass],
          label: EROSION_CLASS_LABELS[p.erosionClass],
        };
        return {
          type: "Feature" as const,
          geometry: { type: "Point" as const, coordinates: [p.lon, p.lat] },
          properties: props,
        };
      }),
    };
  }

  useEffect(() => {
    if (!map) return;

    function addLayers() {
      if (!map) return;
      const geojson = buildGeoJSON();

      if (!map.getSource(EROSION_SOURCE)) {
        map.addSource(EROSION_SOURCE, { type: "geojson", data: geojson });

        map.addLayer({
          id: EROSION_CIRCLES_LAYER,
          type: "circle",
          source: EROSION_SOURCE,
          paint: {
            "circle-radius": 12,
            "circle-color": ["get", "color"],
            "circle-opacity": opacity,
            "circle-stroke-width": 2,
            "circle-stroke-color": "#fff",
          },
        });

        map.addLayer({
          id: EROSION_LABELS_LAYER,
          type: "symbol",
          source: EROSION_SOURCE,
          layout: {
            "text-field": ["get", "label"],
            "text-size": 9,
            "text-offset": [0, 2],
            "text-anchor": "top",
          },
          paint: {
            "text-color": "#333",
            "text-halo-color": "#fff",
            "text-halo-width": 1,
          },
        });

        addedRef.current = true;
      } else {
        (map.getSource(EROSION_SOURCE) as GeoJSONSource).setData(geojson);
        if (map.getLayer(EROSION_CIRCLES_LAYER)) {
          map.setPaintProperty(EROSION_CIRCLES_LAYER, "circle-opacity", opacity);
        }
      }
    }

    if (map.isStyleLoaded()) {
      addLayers();
    } else {
      map.once("styledata", addLayers);
    }

    return () => {
      if (!map || !map.isStyleLoaded()) return;
      if (map.getLayer(EROSION_LABELS_LAYER)) map.removeLayer(EROSION_LABELS_LAYER);
      if (map.getLayer(EROSION_CIRCLES_LAYER)) map.removeLayer(EROSION_CIRCLES_LAYER);
      if (map.getSource(EROSION_SOURCE)) map.removeSource(EROSION_SOURCE);
      addedRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  // Update data when points change
  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;
    const source = map.getSource(EROSION_SOURCE) as GeoJSONSource | undefined;
    if (!source) return;
    source.setData(buildGeoJSON());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, points, opacity]);

  return null;
}

/** Color legend for erosion risk classes */
export function ErosionLegend() {
  const classes: ErosionClass[] = ["very_low", "low", "moderate", "high", "very_high"];
  return (
    <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 text-xs">
      <p className="font-semibold mb-2 text-[hsl(var(--foreground))]">Erosion Risk (USLE)</p>
      <div className="flex flex-col gap-1">
        {classes.map((cls) => (
          <div key={cls} className="flex items-center gap-2">
            <span
              className="w-4 h-3 rounded-sm shrink-0"
              style={{ backgroundColor: EROSION_COLORS[cls] }}
            />
            <span className="text-[hsl(var(--muted-foreground))]">
              {EROSION_CLASS_LABELS[cls]}
            </span>
          </div>
        ))}
      </div>
      <p className="text-[10px] text-[hsl(var(--muted-foreground))] mt-2">
        Based on USLE K-factor, slope, and land cover
      </p>
    </div>
  );
}
