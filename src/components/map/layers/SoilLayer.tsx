"use client";

import { useEffect, useRef, useState } from "react";
import type { Map as MapLibreMap, GeoJSONSource } from "maplibre-gl";
import { trpc } from "@/lib/trpc/client";

export type SoilProperty = "ph" | "organicCarbon" | "nitrogen" | "bulkDensity" | "cec";

interface SoilLayerProps {
  map: MapLibreMap | null;
  property?: SoilProperty;
  opacity?: number;
  /** lat/lon of a clicked point to query soil properties for */
  queryPoint?: { lat: number; lon: number } | null;
  onPopupData?: (data: SoilPopupData | null) => void;
}

export interface SoilPopupData {
  lat: number;
  lon: number;
  ph: number;
  organicCarbon: number;
  nitrogen: number;
  bulkDensity: number;
  cec: number;
  ocd: number;
}

interface ColorStop {
  value: number;
  color: string;
  label: string;
}

export const SOIL_COLOR_RAMPS: Record<SoilProperty, ColorStop[]> = {
  ph: [
    { value: 4.0, color: "#d32f2f", label: "Very acid (<5.5)" },
    { value: 5.5, color: "#ff9800", label: "Acid (5.5–6.0)" },
    { value: 6.5, color: "#4caf50", label: "Neutral (6.5–7.5)" },
    { value: 7.5, color: "#2196f3", label: "Alkaline (>8)" },
    { value: 9.0, color: "#1565c0", label: "Very alkaline (>9)" },
  ],
  organicCarbon: [
    { value: 0, color: "#fff9c4", label: "Very low (<1 g/kg)" },
    { value: 5, color: "#a5d6a7", label: "Low (1–10 g/kg)" },
    { value: 20, color: "#795548", label: "Moderate (10–30 g/kg)" },
    { value: 40, color: "#4e342e", label: "High (>30 g/kg)" },
    { value: 80, color: "#212121", label: "Very high (>50 g/kg)" },
  ],
  nitrogen: [
    { value: 0, color: "#fff9c4", label: "Very low (<0.5 g/kg)" },
    { value: 0.5, color: "#a5d6a7", label: "Low (0.5–1 g/kg)" },
    { value: 2, color: "#388e3c", label: "Moderate (1–2 g/kg)" },
    { value: 4, color: "#1b5e20", label: "High (>2 g/kg)" },
    { value: 8, color: "#0a2e12", label: "Very high (>4 g/kg)" },
  ],
  bulkDensity: [
    { value: 0.5, color: "#e8f5e9", label: "Very low (<0.8 g/cm³)" },
    { value: 1.0, color: "#81c784", label: "Low (0.8–1.0)" },
    { value: 1.3, color: "#ff9800", label: "Moderate (1.0–1.3)" },
    { value: 1.5, color: "#e64a19", label: "High (1.3–1.5)" },
    { value: 1.8, color: "#b71c1c", label: "Very high (>1.5 g/cm³)" },
  ],
  cec: [
    { value: 0, color: "#fff9c4", label: "Very low (<5 cmol/kg)" },
    { value: 10, color: "#aed581", label: "Low (5–10)" },
    { value: 20, color: "#558b2f", label: "Moderate (10–20)" },
    { value: 40, color: "#1b5e20", label: "High (>20 cmol/kg)" },
    { value: 80, color: "#003300", label: "Very high (>40 cmol/kg)" },
  ],
};

const PROPERTY_LABELS: Record<SoilProperty, string> = {
  ph: "pH",
  organicCarbon: "Organic Carbon (g/kg)",
  nitrogen: "Nitrogen (g/kg)",
  bulkDensity: "Bulk Density (g/cm³)",
  cec: "CEC (cmol/kg)",
};

function valueToColor(value: number, ramp: ColorStop[]): string {
  if (ramp.length === 0) return "#888";
  if (value <= ramp[0].value) return ramp[0].color;
  if (value >= ramp[ramp.length - 1].value) return ramp[ramp.length - 1].color;
  for (let i = 1; i < ramp.length; i++) {
    if (value <= ramp[i].value) {
      return ramp[i - 1].color;
    }
  }
  return ramp[ramp.length - 1].color;
}

const SOIL_POINTS_SOURCE = "soil-points-source";
const SOIL_CIRCLES_LAYER = "soil-circles-layer";
const SOIL_LABELS_LAYER = "soil-labels-layer";

export function SoilLayer({
  map,
  property = "organicCarbon",
  opacity = 0.85,
  queryPoint,
  onPopupData,
}: SoilLayerProps) {
  const addedRef = useRef(false);
  const [samplePoints, setSamplePoints] = useState<
    Array<{ lat: number; lon: number; value: number; color: string }>
  >([]);

  // Fetch soil properties for the query point
  const soilQuery = trpc.environmental.getSoilProperties.useQuery(
    { lat: queryPoint?.lat ?? 0, lon: queryPoint?.lon ?? 0 },
    { enabled: !!queryPoint }
  );

  useEffect(() => {
    if (!soilQuery.data || !queryPoint) return;
    const d = soilQuery.data;
    onPopupData?.({
      lat: queryPoint.lat,
      lon: queryPoint.lon,
      ph: d.ph,
      organicCarbon: d.organicCarbon,
      nitrogen: d.nitrogen,
      bulkDensity: d.bulkDensity,
      cec: d.cec,
      ocd: d.ocd,
    });
    // Add the queried point to sample points
    const ramp = SOIL_COLOR_RAMPS[property];
    const value = d[property as keyof typeof d] as number;
    setSamplePoints((prev) => {
      const filtered = prev.filter(
        (p) =>
          Math.abs(p.lat - queryPoint.lat) > 0.01 ||
          Math.abs(p.lon - queryPoint.lon) > 0.01
      );
      return [...filtered, { lat: queryPoint.lat, lon: queryPoint.lon, value, color: valueToColor(value, ramp) }];
    });
  }, [soilQuery.data, queryPoint, property, onPopupData]);

  // Sync GeoJSON source whenever sample points or property changes
  useEffect(() => {
    if (!map) return;

    const geojson: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: samplePoints.map((p) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [p.lon, p.lat] },
        properties: { value: p.value, color: p.color },
      })),
    };

    function addLayers() {
      if (!map) return;

      if (!map.getSource(SOIL_POINTS_SOURCE)) {
        map.addSource(SOIL_POINTS_SOURCE, { type: "geojson", data: geojson });

        map.addLayer({
          id: SOIL_CIRCLES_LAYER,
          type: "circle",
          source: SOIL_POINTS_SOURCE,
          paint: {
            "circle-radius": 10,
            "circle-color": ["get", "color"],
            "circle-opacity": opacity,
            "circle-stroke-width": 2,
            "circle-stroke-color": "#fff",
          },
        });

        map.addLayer({
          id: SOIL_LABELS_LAYER,
          type: "symbol",
          source: SOIL_POINTS_SOURCE,
          layout: {
            "text-field": ["to-string", ["round", ["get", "value"]]],
            "text-size": 10,
            "text-offset": [0, 1.8],
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
        (map.getSource(SOIL_POINTS_SOURCE) as GeoJSONSource).setData(geojson);
        if (map.getLayer(SOIL_CIRCLES_LAYER)) {
          map.setPaintProperty(SOIL_CIRCLES_LAYER, "circle-opacity", opacity);
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
      if (map.getLayer(SOIL_LABELS_LAYER)) map.removeLayer(SOIL_LABELS_LAYER);
      if (map.getLayer(SOIL_CIRCLES_LAYER)) map.removeLayer(SOIL_CIRCLES_LAYER);
      if (map.getSource(SOIL_POINTS_SOURCE)) map.removeSource(SOIL_POINTS_SOURCE);
      addedRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  // Update data when samplePoints or property changes without full re-mount
  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;
    const source = map.getSource(SOIL_POINTS_SOURCE) as GeoJSONSource | undefined;
    if (!source) return;

    const geojson: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: samplePoints.map((p) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [p.lon, p.lat] },
        properties: { value: p.value, color: p.color },
      })),
    };
    source.setData(geojson);
  }, [map, samplePoints, property]);

  return null;
}

/** Color legend for the active soil property */
export function SoilLegend({ property }: { property: SoilProperty }) {
  const ramp = SOIL_COLOR_RAMPS[property];
  const label = PROPERTY_LABELS[property];
  return (
    <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 text-xs">
      <p className="font-semibold mb-2 text-[hsl(var(--foreground))]">{label}</p>
      <div className="flex flex-col gap-1">
        {ramp.map((stop) => (
          <div key={stop.color} className="flex items-center gap-2">
            <span
              className="w-4 h-3 rounded-sm shrink-0"
              style={{ backgroundColor: stop.color }}
            />
            <span className="text-[hsl(var(--muted-foreground))]">{stop.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
