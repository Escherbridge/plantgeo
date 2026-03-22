"use client";

import { useEffect, useRef, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";
import { getStyle } from "@/lib/map/styles";
import type { MapStyle } from "@/types/map";
import "maplibre-gl/dist/maplibre-gl.css";

interface PostMessageFlyTo {
  action: "flyTo";
  center: [number, number];
  zoom?: number;
}

interface PostMessageSetStyle {
  action: "setStyle";
  style: MapStyle;
}

type PostMessage = PostMessageFlyTo | PostMessageSetStyle;

function parseCenter(raw: string | null): [number, number] {
  if (!raw) return [0, 20];
  const parts = raw.split(",").map(Number);
  if (parts.length === 2 && parts.every((n) => !isNaN(n))) {
    return [parts[1], parts[0]]; // lng, lat (MapLibre expects [lng, lat])
  }
  return [0, 20];
}

function parseMarkers(
  raw: string | null
): Array<{ lng: number; lat: number; label: string }> {
  if (!raw) return [];
  return raw
    .split("|")
    .map((part) => {
      const segments = part.split(",");
      if (segments.length < 2) return null;
      const lat = Number(segments[0]);
      const lng = Number(segments[1]);
      if (isNaN(lat) || isNaN(lng)) return null;
      const label = segments.slice(2).join(",") || "";
      return { lat, lng, label };
    })
    .filter((m): m is { lng: number; lat: number; label: string } => m !== null);
}

export default function EmbedMap() {
  const searchParams = useSearchParams();
  const mapContainer = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);

  const centerParam = searchParams.get("center");
  const zoomParam = searchParams.get("zoom");
  const styleParam = (searchParams.get("style") ?? "dark") as MapStyle;
  const markersParam = searchParams.get("markers");

  const center = parseCenter(centerParam);
  const zoom = Number(zoomParam) || 4;
  const markers = parseMarkers(markersParam);

  const initMap = useCallback(() => {
    if (mapRef.current || !mapContainer.current) return;

    const protocol = new Protocol();
    maplibregl.addProtocol("pmtiles", protocol.tile);

    const validStyles: MapStyle[] = ["dark", "light", "satellite"];
    const resolvedStyle: MapStyle = validStyles.includes(styleParam)
      ? styleParam
      : "dark";

    const m = new maplibregl.Map({
      container: mapContainer.current,
      style: getStyle(resolvedStyle),
      center,
      zoom,
      attributionControl: false,
    });

    m.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");

    m.on("load", () => {
      // Add markers
      for (const marker of markers) {
        const el = document.createElement("div");
        el.style.cssText =
          "width:24px;height:24px;background:#10b981;border:2px solid #fff;border-radius:50%;cursor:pointer;";

        const popup = marker.label
          ? new maplibregl.Popup({ offset: 14 }).setText(marker.label)
          : undefined;

        const mk = new maplibregl.Marker({ element: el })
          .setLngLat([marker.lng, marker.lat]);

        if (popup) mk.setPopup(popup);
        mk.addTo(m);
      }
    });

    mapRef.current = m;
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    initMap();
    return () => {
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
        maplibregl.removeProtocol("pmtiles");
      }
    };
  }, [initMap]);

  // Listen for postMessage from parent frame
  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      const m = mapRef.current;
      if (!m) return;

      let msg: PostMessage;
      try {
        msg = typeof event.data === "string" ? JSON.parse(event.data) : event.data;
      } catch {
        return;
      }

      if (!msg || !msg.action) return;

      if (msg.action === "flyTo") {
        const { center: flyCenter, zoom: flyZoom } = msg;
        if (Array.isArray(flyCenter) && flyCenter.length === 2) {
          m.flyTo({
            center: [flyCenter[0], flyCenter[1]],
            zoom: flyZoom ?? m.getZoom(),
            duration: 1500,
          });
        }
      } else if (msg.action === "setStyle") {
        const validStyles: MapStyle[] = ["dark", "light", "satellite"];
        if (validStyles.includes(msg.style)) {
          m.setStyle(getStyle(msg.style));
        }
      }
    }

    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, []);

  return <div ref={mapContainer} style={{ width: "100%", height: "100vh" }} />;
}
