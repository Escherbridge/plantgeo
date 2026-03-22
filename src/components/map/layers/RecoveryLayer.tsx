"use client";

import { useEffect, useRef } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";

interface RecoveryLayerProps {
  map: MapLibreMap | null;
  visible?: boolean;
  opacity?: number;
  layerId?: string;
  sourceId?: string;
}

const LEGEND_ITEMS = [
  { color: "#8B0000", label: "No Recovery" },
  { color: "#FF6600", label: "Partial Recovery" },
  { color: "#FFD700", label: "Moderate Recovery" },
  { color: "#90EE90", label: "Good Recovery" },
  { color: "#006400", label: "Full Recovery" },
];

export function RecoveryLayer({
  map,
  visible = true,
  opacity = 0.65,
  layerId = "nbr-recovery-layer",
  sourceId = "nbr-recovery",
}: RecoveryLayerProps) {
  const addedRef = useRef(false);

  useEffect(() => {
    if (!map) return;

    function addLayer() {
      if (!map) return;
      if (map.getLayer(layerId)) {
        map.setLayoutProperty(layerId, "visibility", visible ? "visible" : "none");
        map.setPaintProperty(layerId, "raster-opacity", opacity);
        return;
      }

      if (!map.getSource(sourceId)) return;

      map.addLayer({
        id: layerId,
        type: "raster",
        source: sourceId,
        paint: {
          "raster-opacity": opacity,
        },
        layout: {
          visibility: visible ? "visible" : "none",
        },
      });

      addedRef.current = true;
    }

    if (map.isStyleLoaded()) {
      addLayer();
    } else {
      map.once("styledata", addLayer);
    }

    return () => {
      if (!map || !map.isStyleLoaded()) return;
      if (map.getLayer(layerId)) map.removeLayer(layerId);
      addedRef.current = false;
    };
  }, [map, layerId, sourceId]);

  useEffect(() => {
    if (!map || !addedRef.current) return;
    if (map.getLayer(layerId)) {
      map.setLayoutProperty(layerId, "visibility", visible ? "visible" : "none");
    }
  }, [map, layerId, visible]);

  useEffect(() => {
    if (!map || !addedRef.current) return;
    if (map.getLayer(layerId)) {
      map.setPaintProperty(layerId, "raster-opacity", opacity);
    }
  }, [map, layerId, opacity]);

  if (!visible) return null;

  return (
    <div className="absolute bottom-24 left-4 bg-black/70 text-white rounded p-2 text-xs pointer-events-none">
      <div className="font-semibold mb-1">NBR Recovery Index</div>
      {LEGEND_ITEMS.map((item) => (
        <div key={item.label} className="flex items-center gap-1.5 mb-0.5">
          <span
            className="inline-block w-3 h-3 rounded-sm flex-shrink-0"
            style={{ backgroundColor: item.color }}
          />
          <span>{item.label}</span>
        </div>
      ))}
    </div>
  );
}
