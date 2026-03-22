"use client";

import { useEffect, useRef } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";

const STATUS_COLORS: Record<string, string> = {
  active: "#10b981",
  idle: "#f59e0b",
  offline: "#ef4444",
};

const TYPE_ICONS: Record<string, string> = {
  vehicle: "🚗",
  truck: "🚛",
  drone: "🚁",
  person: "🚶",
};

interface VehicleMarkerProps {
  map: MapLibreMap;
  assetId: string;
  lat: number;
  lon: number;
  heading?: number;
  status: "active" | "idle" | "offline";
  type?: string;
}

export function VehicleMarker({
  map,
  assetId,
  lat,
  lon,
  heading = 0,
  status,
  type = "vehicle",
}: VehicleMarkerProps) {
  const markerRef = useRef<import("maplibre-gl").Marker | null>(null);
  const elRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let mounted = true;

    import("maplibre-gl").then(({ Marker }) => {
      if (!mounted) return;

      const el = document.createElement("div");
      el.style.cssText = `
        width: 32px;
        height: 32px;
        border-radius: 50%;
        background: ${STATUS_COLORS[status] ?? STATUS_COLORS.offline};
        border: 2px solid white;
        box-shadow: 0 2px 6px rgba(0,0,0,0.4);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 16px;
        cursor: pointer;
        transform: rotate(${heading}deg);
        transition: transform 0.3s ease;
      `;
      el.textContent = TYPE_ICONS[type] ?? "🚗";
      el.dataset.assetId = assetId;
      elRef.current = el;

      const marker = new Marker({ element: el, anchor: "center" })
        .setLngLat([lon, lat])
        .addTo(map);

      markerRef.current = marker;
    });

    return () => {
      mounted = false;
      markerRef.current?.remove();
      markerRef.current = null;
    };
  }, [map, assetId]);

  useEffect(() => {
    if (markerRef.current) {
      markerRef.current.setLngLat([lon, lat]);
    }
    if (elRef.current) {
      elRef.current.style.transform = `rotate(${heading}deg)`;
      elRef.current.style.background = STATUS_COLORS[status] ?? STATUS_COLORS.offline;
    }
  }, [lat, lon, heading, status]);

  return null;
}
