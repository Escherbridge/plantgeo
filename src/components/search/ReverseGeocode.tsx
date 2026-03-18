"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import maplibregl from "maplibre-gl";
import { Copy, Navigation } from "lucide-react";
import { useMap } from "@/lib/map/map-context";
import { MapPopup, MapPopupBody } from "@/components/ui/map-popup";
import { cn } from "@/lib/utils";
import type { NormalizedGeocodingResult } from "@/lib/server/services/geocoding";

interface PopupState {
  x: number;
  y: number;
  lat: number;
  lon: number;
  address: string | null;
  loading: boolean;
}

export function ReverseGeocode() {
  const map = useMap();
  const [popup, setPopup] = useState<PopupState | null>(null);
  const markerRef = useRef<maplibregl.Marker | null>(null);
  const popupCoordsRef = useRef<{ lat: number; lon: number } | null>(null);
  const touchStartRef = useRef<{ x: number; y: number; time: number } | null>(null);
  const touchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const closePopup = useCallback(() => {
    setPopup(null);
    popupCoordsRef.current = null;
    markerRef.current?.remove();
    markerRef.current = null;
  }, []);

  const openPopup = useCallback(
    async (lat: number, lon: number, mapInstance: maplibregl.Map) => {
      const point = mapInstance.project([lon, lat]);

      markerRef.current?.remove();
      const marker = new maplibregl.Marker({ color: "hsl(var(--primary))" })
        .setLngLat([lon, lat])
        .addTo(mapInstance);
      markerRef.current = marker;

      popupCoordsRef.current = { lat, lon };
      setPopup({ x: point.x, y: point.y, lat, lon, address: null, loading: true });

      try {
        const res = await fetch(`/api/geocode/reverse?lat=${lat}&lon=${lon}`);
        const data: { results: NormalizedGeocodingResult[] } = await res.json();
        const address = data.results[0]?.displayName ?? null;
        setPopup((prev) =>
          prev ? { ...prev, address, loading: false } : null
        );
      } catch {
        setPopup((prev) =>
          prev ? { ...prev, address: null, loading: false } : null
        );
      }
    },
    []
  );

  useEffect(() => {
    if (!map) return;

    const handleContextMenu = (e: maplibregl.MapMouseEvent) => {
      e.preventDefault();
      openPopup(e.lngLat.lat, e.lngLat.lng, map);
    };

    const handleClick = () => {
      closePopup();
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") closePopup();
    };

    const handleTouchStart = (e: maplibregl.MapTouchEvent) => {
      if (e.points.length !== 1) return;
      const touch = e.originalEvent.touches[0];
      touchStartRef.current = { x: touch.clientX, y: touch.clientY, time: Date.now() };

      if (touchTimerRef.current) clearTimeout(touchTimerRef.current);
      touchTimerRef.current = setTimeout(() => {
        if (touchStartRef.current) {
          openPopup(e.lngLat.lat, e.lngLat.lng, map);
        }
      }, 500);
    };

    const handleTouchEnd = () => {
      if (touchTimerRef.current) {
        clearTimeout(touchTimerRef.current);
        touchTimerRef.current = null;
      }
      touchStartRef.current = null;
    };

    const handleTouchMove = (e: maplibregl.MapTouchEvent) => {
      if (!touchStartRef.current) return;
      const touch = e.originalEvent.touches[0];
      const dx = touch.clientX - touchStartRef.current.x;
      const dy = touch.clientY - touchStartRef.current.y;
      if (Math.sqrt(dx * dx + dy * dy) > 10) {
        if (touchTimerRef.current) {
          clearTimeout(touchTimerRef.current);
          touchTimerRef.current = null;
        }
        touchStartRef.current = null;
      }
    };

    map.on("contextmenu", handleContextMenu);
    map.on("click", handleClick);
    map.on("touchstart", handleTouchStart);
    map.on("touchend", handleTouchEnd);
    map.on("touchmove", handleTouchMove);
    window.addEventListener("keydown", handleKeyDown);

    return () => {
      map.off("contextmenu", handleContextMenu);
      map.off("click", handleClick);
      map.off("touchstart", handleTouchStart);
      map.off("touchend", handleTouchEnd);
      map.off("touchmove", handleTouchMove);
      window.removeEventListener("keydown", handleKeyDown);
      if (touchTimerRef.current) clearTimeout(touchTimerRef.current);
      markerRef.current?.remove();
      markerRef.current = null;
    };
  }, [map, openPopup, closePopup]);

  useEffect(() => {
    if (!map) return;

    const handleMove = () => {
      const coords = popupCoordsRef.current;
      if (!coords) return;
      const point = map.project([coords.lon, coords.lat]);
      setPopup((prev) => (prev ? { ...prev, x: point.x, y: point.y } : null));
    };

    map.on("move", handleMove);
    return () => { map.off("move", handleMove); };
  }, [map]);

  const handleCopyCoords = useCallback(() => {
    if (!popup) return;
    const text = `${popup.lat.toFixed(6)}, ${popup.lon.toFixed(6)}`;
    navigator.clipboard.writeText(text).catch(() => {});
  }, [popup]);

  const handleDirections = useCallback(() => {
    if (!popup) return;
    console.log("Directions to:", popup.lat, popup.lon);
  }, [popup]);

  if (!popup) return null;

  return (
    <div
      className="pointer-events-none absolute inset-0 z-20"
      style={{ position: "absolute", top: 0, left: 0, width: "100%", height: "100%" }}
    >
      <div
        className="pointer-events-auto absolute"
        style={{
          left: popup.x,
          top: popup.y,
          transform: "translate(-50%, calc(-100% - 12px))",
        }}
      >
        <MapPopup
          title="Location"
          onClose={closePopup}
        >
          <MapPopupBody>
            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-1">
                <p className="text-sm font-medium text-[hsl(var(--foreground))] leading-snug">
                  {popup.loading
                    ? "Loading..."
                    : (popup.address ?? "Unknown location")}
                </p>
                <p className="text-xs text-[hsl(var(--muted-foreground))] font-mono">
                  {popup.lat.toFixed(6)}, {popup.lon.toFixed(6)}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={handleCopyCoords}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium",
                    "bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))]",
                    "hover:bg-[hsl(var(--accent))] hover:text-[hsl(var(--accent-foreground))]",
                    "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))]"
                  )}
                >
                  <Copy className="h-3 w-3" />
                  Copy coordinates
                </button>
                <button
                  onClick={handleDirections}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium",
                    "bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))]",
                    "hover:bg-[hsl(var(--accent))] hover:text-[hsl(var(--accent-foreground))]",
                    "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))]"
                  )}
                >
                  <Navigation className="h-3 w-3" />
                  Directions to here
                </button>
              </div>
            </div>
          </MapPopupBody>
        </MapPopup>
      </div>
    </div>
  );
}
