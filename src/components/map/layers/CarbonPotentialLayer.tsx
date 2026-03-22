"use client";

import { useEffect, useRef } from "react";
import type { Map as MapLibreMap, GeoJSONSource } from "maplibre-gl";
import type { CarbonPotential } from "@/lib/server/services/carbon-potential";

export type CarbonClass = "very_low" | "low" | "medium" | "high" | "very_high";

interface CarbonPoint {
  lat: number;
  lon: number;
  potential: CarbonPotential;
}

interface CarbonPotentialLayerProps {
  map: MapLibreMap | null;
  points?: CarbonPoint[];
  opacity?: number;
}

/** Green ramp — darker = higher carbon sequestration potential */
export const CARBON_COLORS: Record<CarbonClass, string> = {
  very_low: "#f1f8e9",
  low: "#c5e1a5",
  medium: "#8bc34a",
  high: "#558b2f",
  very_high: "#1b5e20",
};

const CARBON_CLASS_LABELS: Record<CarbonClass, string> = {
  very_low: "Very Low (<0.2 tC/ha/yr)",
  low: "Low (0.2–0.5 tC/ha/yr)",
  medium: "Medium (0.5–1.0 tC/ha/yr)",
  high: "High (1.0–2.0 tC/ha/yr)",
  very_high: "Very High (>2.0 tC/ha/yr)",
};

export function classifyCarbonPotential(potentialGain: number): CarbonClass {
  if (potentialGain < 0.2) return "very_low";
  if (potentialGain < 0.5) return "low";
  if (potentialGain < 1.0) return "medium";
  if (potentialGain < 2.0) return "high";
  return "very_high";
}

const CARBON_SOURCE = "carbon-potential-source";
const CARBON_CIRCLES_LAYER = "carbon-circles-layer";
const CARBON_LABELS_LAYER = "carbon-labels-layer";

export function CarbonPotentialLayer({
  map,
  points = [],
  opacity = 0.85,
}: CarbonPotentialLayerProps) {
  const addedRef = useRef(false);

  function buildGeoJSON(): GeoJSON.FeatureCollection {
    return {
      type: "FeatureCollection",
      features: points.map((p) => {
        const cls = classifyCarbonPotential(p.potential.potentialGain);
        return {
          type: "Feature" as const,
          geometry: { type: "Point" as const, coordinates: [p.lon, p.lat] },
          properties: {
            potentialGain: p.potential.potentialGain,
            currentOC: p.potential.currentOC,
            yearsToSaturation: p.potential.yearsToSaturation,
            confidenceClass: p.potential.confidenceClass,
            carbonClass: cls,
            color: CARBON_COLORS[cls],
            label: `${p.potential.potentialGain.toFixed(2)} tC/ha/yr`,
          },
        };
      }),
    };
  }

  useEffect(() => {
    if (!map) return;

    function addLayers() {
      if (!map) return;
      const geojson = buildGeoJSON();

      if (!map.getSource(CARBON_SOURCE)) {
        map.addSource(CARBON_SOURCE, { type: "geojson", data: geojson });

        map.addLayer({
          id: CARBON_CIRCLES_LAYER,
          type: "circle",
          source: CARBON_SOURCE,
          paint: {
            "circle-radius": 12,
            "circle-color": ["get", "color"],
            "circle-opacity": opacity,
            "circle-stroke-width": 2,
            "circle-stroke-color": "#fff",
          },
        });

        map.addLayer({
          id: CARBON_LABELS_LAYER,
          type: "symbol",
          source: CARBON_SOURCE,
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
        (map.getSource(CARBON_SOURCE) as GeoJSONSource).setData(geojson);
        if (map.getLayer(CARBON_CIRCLES_LAYER)) {
          map.setPaintProperty(CARBON_CIRCLES_LAYER, "circle-opacity", opacity);
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
      if (map.getLayer(CARBON_LABELS_LAYER)) map.removeLayer(CARBON_LABELS_LAYER);
      if (map.getLayer(CARBON_CIRCLES_LAYER)) map.removeLayer(CARBON_CIRCLES_LAYER);
      if (map.getSource(CARBON_SOURCE)) map.removeSource(CARBON_SOURCE);
      addedRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  // Update data when points change
  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;
    const source = map.getSource(CARBON_SOURCE) as GeoJSONSource | undefined;
    if (!source) return;
    source.setData(buildGeoJSON());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, points, opacity]);

  return null;
}

/** Color legend for carbon sequestration potential */
export function CarbonPotentialLegend() {
  const classes: CarbonClass[] = ["very_low", "low", "medium", "high", "very_high"];
  return (
    <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 text-xs">
      <p className="font-semibold mb-2 text-[hsl(var(--foreground))]">
        Carbon Sequestration Potential
      </p>
      <div className="flex flex-col gap-1">
        {classes.map((cls) => (
          <div key={cls} className="flex items-center gap-2">
            <span
              className="w-4 h-3 rounded-sm shrink-0"
              style={{ backgroundColor: CARBON_COLORS[cls] }}
            />
            <span className="text-[hsl(var(--muted-foreground))]">
              {CARBON_CLASS_LABELS[cls]}
            </span>
          </div>
        ))}
      </div>
      <p className="text-[10px] text-[hsl(var(--muted-foreground))] mt-2">
        Estimated tC/ha/yr under optimal management
      </p>
    </div>
  );
}
