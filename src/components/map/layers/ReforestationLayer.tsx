"use client";

import { useEffect, useRef, useState } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { trpc } from "@/lib/trpc/client";

export type ReforestationSuitability = "High" | "Medium" | "Low";

interface ReforestationZone {
  id: string;
  suitability: ReforestationSuitability;
  score: number;
  factors: string[];
  geometry: GeoJSON.Polygon;
}

interface ReforestationLayerProps {
  map: MapLibreMap | null;
  bbox?: string;
  onZoneClick?: (zone: ReforestationZone) => void;
}

const REFORESTATION_SOURCE_ID = "reforestation-zones";
const REFORESTATION_FILL_LAYER_ID = "reforestation-zones-fill";
const REFORESTATION_OUTLINE_LAYER_ID = "reforestation-zones-outline";

const SUITABILITY_COLORS: Record<ReforestationSuitability, string> = {
  High: "#1a7a1a",
  Medium: "#4caf50",
  Low: "#cddc39",
};

const SUITABILITY_OPACITY: Record<ReforestationSuitability, number> = {
  High: 0.6,
  Medium: 0.4,
  Low: 0.3,
};

export function ReforestationLayer({
  map,
  bbox,
  onZoneClick,
}: ReforestationLayerProps) {
  const addedRef = useRef(false);
  const [selectedZone, setSelectedZone] = useState<ReforestationZone | null>(null);

  const zonesQuery = trpc.environmental.getReforestationZones.useQuery(
    { bbox: bbox ?? "" },
    { enabled: !!bbox && !!map }
  );

  const features = zonesQuery.data?.features ?? [];

  useEffect(() => {
    if (!map) return;

    function addLayers() {
      if (!map) return;

      const geojson: GeoJSON.FeatureCollection = {
        type: "FeatureCollection",
        features: features.map((f) => ({
          type: "Feature" as const,
          id: f.id as string,
          geometry: f.geometry as GeoJSON.Geometry,
          properties: {
            suitability: (f.properties as Record<string, unknown>).suitability,
            score: (f.properties as Record<string, unknown>).score,
            factors: JSON.stringify((f.properties as Record<string, unknown>).factors ?? []),
          },
        })),
      };

      if (map.getSource(REFORESTATION_SOURCE_ID)) {
        (map.getSource(REFORESTATION_SOURCE_ID) as maplibregl.GeoJSONSource).setData(geojson);
        return;
      }

      map.addSource(REFORESTATION_SOURCE_ID, {
        type: "geojson",
        data: geojson,
      });

      map.addLayer({
        id: REFORESTATION_FILL_LAYER_ID,
        type: "fill",
        source: REFORESTATION_SOURCE_ID,
        paint: {
          "fill-color": [
            "match",
            ["get", "suitability"],
            "High", SUITABILITY_COLORS.High,
            "Medium", SUITABILITY_COLORS.Medium,
            "Low", SUITABILITY_COLORS.Low,
            "#888888",
          ],
          "fill-opacity": [
            "match",
            ["get", "suitability"],
            "High", SUITABILITY_OPACITY.High,
            "Medium", SUITABILITY_OPACITY.Medium,
            "Low", SUITABILITY_OPACITY.Low,
            0.2,
          ],
        },
      });

      map.addLayer({
        id: REFORESTATION_OUTLINE_LAYER_ID,
        type: "line",
        source: REFORESTATION_SOURCE_ID,
        paint: {
          "line-color": [
            "match",
            ["get", "suitability"],
            "High", "#0d4d0d",
            "Medium", "#2e7d32",
            "Low", "#9e9d24",
            "#555555",
          ],
          "line-width": 1.5,
          "line-opacity": 0.8,
        },
      });

      // Click handler
      map.on("click", REFORESTATION_FILL_LAYER_ID, (e) => {
        const feature = e.features?.[0];
        if (!feature) return;
        const props = feature.properties as Record<string, unknown>;
        const zone: ReforestationZone = {
          id: String(feature.id ?? ""),
          suitability: props.suitability as ReforestationSuitability,
          score: Number(props.score ?? 0),
          factors: JSON.parse(String(props.factors ?? "[]")),
          geometry: feature.geometry as GeoJSON.Polygon,
        };
        setSelectedZone(zone);
        onZoneClick?.(zone);
      });

      map.on("mouseenter", REFORESTATION_FILL_LAYER_ID, () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", REFORESTATION_FILL_LAYER_ID, () => {
        map.getCanvas().style.cursor = "";
      });

      addedRef.current = true;
    }

    if (map.isStyleLoaded()) {
      addLayers();
    } else {
      map.once("styledata", addLayers);
    }

    return () => {
      if (!map || !map.isStyleLoaded()) return;
      if (map.getLayer(REFORESTATION_OUTLINE_LAYER_ID)) map.removeLayer(REFORESTATION_OUTLINE_LAYER_ID);
      if (map.getLayer(REFORESTATION_FILL_LAYER_ID)) map.removeLayer(REFORESTATION_FILL_LAYER_ID);
      if (map.getSource(REFORESTATION_SOURCE_ID)) map.removeSource(REFORESTATION_SOURCE_ID);
      addedRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, features]);

  return null;
}

/** Popup info card shown when a reforestation zone is clicked */
interface ZoneInfoCardProps {
  zone: ReforestationZone;
  onClose: () => void;
}

export function ReforestationZoneCard({ zone, onClose }: ZoneInfoCardProps) {
  return (
    <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-4 text-xs w-64">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span
            className="w-3 h-3 rounded-full"
            style={{ backgroundColor: SUITABILITY_COLORS[zone.suitability] }}
          />
          <span className="font-semibold text-sm text-[hsl(var(--foreground))]">
            {zone.suitability} Suitability
          </span>
        </div>
        <button
          onClick={onClose}
          className="text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] text-base leading-none"
        >
          ×
        </button>
      </div>

      <div className="mb-3">
        <div className="flex justify-between text-[hsl(var(--muted-foreground))] mb-1">
          <span>Suitability Score</span>
          <span className="font-medium text-[hsl(var(--foreground))]">{zone.score}/100</span>
        </div>
        <div className="h-2 rounded-full bg-[hsl(var(--muted))] overflow-hidden">
          <div
            className="h-full rounded-full"
            style={{
              width: `${zone.score}%`,
              backgroundColor: SUITABILITY_COLORS[zone.suitability],
            }}
          />
        </div>
      </div>

      {zone.factors.length > 0 && (
        <div>
          <p className="font-medium text-[hsl(var(--muted-foreground))] mb-1">Key Factors</p>
          <ul className="flex flex-col gap-0.5">
            {zone.factors.map((f, i) => (
              <li key={i} className="text-[hsl(var(--foreground))] flex items-center gap-1">
                <span className="text-green-600">•</span> {f}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
