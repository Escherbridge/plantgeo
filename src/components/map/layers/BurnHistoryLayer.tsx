"use client";

import { useEffect, useRef, useState } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import type { MTBSFireProperties } from "@/lib/server/services/mtbs";

interface BurnHistoryLayerProps {
  map: MapLibreMap | null;
  data: GeoJSON.FeatureCollection;
  visible?: boolean;
  layerId?: string;
  sourceId?: string;
  yearRange?: [number, number];
}

const SEVERITY_COLORS: Record<string, string> = {
  unburned: "#a8f0a8",
  low: "#ffff00",
  moderate: "#ff7f00",
  high: "#ff0000",
  increased_greenness: "#00a000",
};

const SEVERITY_LABELS: Record<string, string> = {
  unburned: "Unburned",
  low: "Low",
  moderate: "Moderate",
  high: "High",
  increased_greenness: "Increased Greenness",
};

export function BurnHistoryLayer({
  map,
  data,
  visible = true,
  layerId = "burn-history-fill",
  sourceId = "burn-history",
  yearRange,
}: BurnHistoryLayerProps) {
  const addedRef = useRef(false);
  const [hoveredFire, setHoveredFire] = useState<MTBSFireProperties | null>(null);

  useEffect(() => {
    if (!map) return;

    function addLayers() {
      if (!map) return;

      if (map.getSource(sourceId)) {
        (map.getSource(sourceId) as maplibregl.GeoJSONSource).setData(data);
        return;
      }

      map.addSource(sourceId, {
        type: "geojson",
        data,
      });

      map.addLayer({
        id: layerId,
        type: "fill",
        source: sourceId,
        paint: {
          "fill-color": [
            "match",
            ["get", "severityClass"],
            "unburned", SEVERITY_COLORS.unburned,
            "low", SEVERITY_COLORS.low,
            "moderate", SEVERITY_COLORS.moderate,
            "high", SEVERITY_COLORS.high,
            "increased_greenness", SEVERITY_COLORS.increased_greenness,
            "#888888",
          ],
          "fill-opacity": 0.6,
        },
        layout: {
          visibility: visible ? "visible" : "none",
        },
      });

      map.addLayer({
        id: `${layerId}-outline`,
        type: "line",
        source: sourceId,
        paint: {
          "line-color": "#333333",
          "line-width": 0.5,
          "line-opacity": 0.7,
        },
        layout: {
          visibility: visible ? "visible" : "none",
        },
      });

      map.on("mouseenter", layerId, (e) => {
        map.getCanvas().style.cursor = "pointer";
        const feature = e.features?.[0];
        if (feature?.properties) {
          setHoveredFire(feature.properties as MTBSFireProperties);
        }
      });

      map.on("mouseleave", layerId, () => {
        map.getCanvas().style.cursor = "";
        setHoveredFire(null);
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
      if (map.getLayer(`${layerId}-outline`)) map.removeLayer(`${layerId}-outline`);
      if (map.getLayer(layerId)) map.removeLayer(layerId);
      if (map.getSource(sourceId)) map.removeSource(sourceId);
      addedRef.current = false;
    };
  }, [map, layerId, sourceId]);

  useEffect(() => {
    if (!map || !addedRef.current) return;
    if (map.getSource(sourceId)) {
      (map.getSource(sourceId) as maplibregl.GeoJSONSource).setData(data);
    }
  }, [map, data, sourceId]);

  useEffect(() => {
    if (!map || !addedRef.current) return;
    const viz = visible ? "visible" : "none";
    if (map.getLayer(layerId)) map.setLayoutProperty(layerId, "visibility", viz);
    if (map.getLayer(`${layerId}-outline`)) map.setLayoutProperty(`${layerId}-outline`, "visibility", viz);
  }, [map, layerId, visible]);

  useEffect(() => {
    if (!map || !addedRef.current || !yearRange) return;
    if (map.getLayer(layerId)) {
      map.setFilter(layerId, ["all",
        [">=", ["get", "fireYear"], yearRange[0]],
        ["<=", ["get", "fireYear"], yearRange[1]],
      ]);
      map.setFilter(`${layerId}-outline`, ["all",
        [">=", ["get", "fireYear"], yearRange[0]],
        ["<=", ["get", "fireYear"], yearRange[1]],
      ]);
    }
  }, [map, layerId, yearRange]);

  if (!visible) return null;

  return (
    <>
      <div className="absolute bottom-24 left-4 bg-black/70 text-white rounded p-2 text-xs pointer-events-none">
        <div className="font-semibold mb-1">Burn Severity (MTBS)</div>
        {Object.entries(SEVERITY_COLORS).map(([key, color]) => (
          <div key={key} className="flex items-center gap-1.5 mb-0.5">
            <span
              className="inline-block w-3 h-3 rounded-sm flex-shrink-0"
              style={{ backgroundColor: color }}
            />
            <span>{SEVERITY_LABELS[key]}</span>
          </div>
        ))}
      </div>
      {hoveredFire && (
        <div className="absolute top-4 right-4 bg-black/80 text-white rounded p-3 text-xs max-w-48 pointer-events-none">
          <div className="font-semibold mb-1">{hoveredFire.fireName}</div>
          <div>Year: {hoveredFire.fireYear}</div>
          <div>Acres: {hoveredFire.acres.toLocaleString()}</div>
          <div>Severity: {SEVERITY_LABELS[hoveredFire.severityClass] ?? hoveredFire.severityClass}</div>
          {hoveredFire.ignitionDate && <div>Date: {hoveredFire.ignitionDate}</div>}
        </div>
      )}
    </>
  );
}
