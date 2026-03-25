"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import dynamic from "next/dynamic";
import maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";
import { useMapStore } from "@/stores/map-store";
import { getStyle, skyThemes } from "@/lib/map/styles";
import { MapProvider } from "@/lib/map/map-context";
import { Skeleton } from "@/components/ui/skeleton";
import MapControls from "./MapControls";
import SearchBar from "@/components/search/SearchBar";
import { ReverseGeocode } from "@/components/search/ReverseGeocode";
import { CommandPalette } from "@/components/search/CommandPalette";
import { AlertBell } from "@/components/ui/AlertBell";
import PanelManager from "./PanelManager";
import LayerManager from "./LayerManager";
import { useRegionalIntelligenceStore } from "@/stores/regional-intelligence-store";
import { useRegionalIntelligence } from "@/hooks/useRegionalIntelligence";

const RegionalIntelligencePanel = dynamic(
  () => import("@/components/panels/RegionalIntelligencePanel"),
  { ssr: false }
);

export default function MapView() {
  const mapContainer = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [mapInstance, setMapInstance] = useState<maplibregl.Map | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [webglError, setWebglError] = useState(false);

  const {
    viewport,
    is3DEnabled,
    isGlobeView,
    terrainExaggeration,
    currentStyle,
    isTerrainEnabled,
    setViewport,
  } = useMapStore();

  const prevStyleRef = useRef(currentStyle);
  const { isOpen: isAIOpen } = useRegionalIntelligenceStore();
  const { queryLocation } = useRegionalIntelligence();

  const initMap = useCallback(() => {
    if (mapRef.current || !mapContainer.current) return;

    const protocol = new Protocol();
    maplibregl.addProtocol("pmtiles", protocol.tile);

    const m = new maplibregl.Map({
      container: mapContainer.current,
      style: getStyle(currentStyle),
      center: [viewport.longitude, viewport.latitude],
      zoom: viewport.zoom,
      bearing: viewport.bearing,
      pitch: is3DEnabled ? 60 : 0,
      maxPitch: 85,
      canvasContextAttributes: { antialias: false, failIfMajorPerformanceCaveat: false },
    });

    mapRef.current = m;

    m.addControl(new maplibregl.NavigationControl({ visualizePitch: true }));
    m.addControl(new maplibregl.ScaleControl({ maxWidth: 200 }));
    m.addControl(
      new maplibregl.GeolocateControl({ trackUserLocation: true }),
      "top-right"
    );
    m.addControl(new maplibregl.FullscreenControl());

    m.on("load", () => {
      setIsLoading(false);

      if (isTerrainEnabled && m.getSource("terrain-dem")) {
        m.setTerrain({ source: "terrain-dem", exaggeration: terrainExaggeration });
      }

      m.setSky(skyThemes[currentStyle]);

      if (isGlobeView) {
        m.setProjection({ type: "globe" });
      }
    });

    m.on("moveend", () => {
      const center = m.getCenter();
      setViewport({
        longitude: center.lng,
        latitude: center.lat,
        zoom: m.getZoom(),
        bearing: m.getBearing(),
        pitch: m.getPitch(),
      });
    });

    m.on("click", (e) => {
      // Only trigger when no map feature was clicked
      const features = m.queryRenderedFeatures(e.point);
      if (features && features.length > 0) return;
      const { lat, lng } = e.lngLat;
      useRegionalIntelligenceStore.getState().openPanel(lat, lng);
      queryLocation(lat, lng);
    });

    setMapInstance(m);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    initMap();

    // Track WebGL context loss — but only while this map instance is active.
    // React 19 dev mode double-invokes effects, so cleanup destroys the map
    // and fires webglcontextlost. We must ignore that event.
    let contextLostTimer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;

    const canvas = mapRef.current?.getCanvas();
    const onContextLost = (e: Event) => {
      e.preventDefault();
      if (disposed) return; // map was intentionally removed
      contextLostTimer = setTimeout(() => {
        if (!disposed) setWebglError(true);
      }, 3000);
    };
    const onContextRestored = () => {
      if (contextLostTimer) clearTimeout(contextLostTimer);
      setWebglError(false);
    };

    canvas?.addEventListener("webglcontextlost", onContextLost);
    canvas?.addEventListener("webglcontextrestored", onContextRestored);

    return () => {
      disposed = true;
      if (contextLostTimer) clearTimeout(contextLostTimer);
      canvas?.removeEventListener("webglcontextlost", onContextLost);
      canvas?.removeEventListener("webglcontextrestored", onContextRestored);
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
        setMapInstance(null);
        maplibregl.removeProtocol("pmtiles");
      }
    };
  }, [initMap]);

  useEffect(() => {
    const m = mapRef.current;
    if (!m || prevStyleRef.current === currentStyle) return;
    prevStyleRef.current = currentStyle;
    m.setStyle(getStyle(currentStyle));
    m.once("style.load", () => {
      m.setSky(skyThemes[currentStyle]);
      if (isTerrainEnabled && m.getSource("terrain-dem")) {
        m.setTerrain({ source: "terrain-dem", exaggeration: terrainExaggeration });
      }
      if (isGlobeView) {
        m.setProjection({ type: "globe" });
      }
    });
  }, [currentStyle, isTerrainEnabled, terrainExaggeration, isGlobeView]);

  useEffect(() => {
    const m = mapRef.current;
    if (!m || !m.isStyleLoaded()) return;
    if (isTerrainEnabled && m.getSource("terrain-dem")) {
      m.setTerrain({ source: "terrain-dem", exaggeration: terrainExaggeration });
    } else {
      m.setTerrain(null);
    }
  }, [isTerrainEnabled, terrainExaggeration]);

  useEffect(() => {
    const m = mapRef.current;
    if (!m || !m.isStyleLoaded()) return;
    m.setProjection({ type: isGlobeView ? "globe" : "mercator" });
    const targetZoom = isGlobeView
      ? Math.min(m.getZoom(), 5)
      : Math.max(m.getZoom(), 3);
    if (Math.abs(m.getZoom() - targetZoom) > 0.5) {
      m.flyTo({ zoom: targetZoom, duration: 1000 });
    }
  }, [isGlobeView]);

  useEffect(() => {
    const m = mapRef.current;
    if (!m || !m.isStyleLoaded()) return;
    m.easeTo({ pitch: is3DEnabled ? 60 : 0, duration: 500 });
  }, [is3DEnabled]);

  if (webglError) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-[hsl(var(--destructive)/0.1)]">
        <div className="text-center">
          <p className="text-lg font-medium text-[hsl(var(--destructive))]">
            WebGL context lost
          </p>
          <p className="mt-1 text-sm text-[hsl(var(--muted-foreground))]">
            Try refreshing the page
          </p>
          <button
            onClick={() => window.location.reload()}
            className="mt-3 px-4 py-2 rounded-lg bg-[hsl(var(--destructive))] text-white text-sm font-medium hover:opacity-90 transition-opacity"
          >
            Reload Page
          </button>
        </div>
      </div>
    );
  }

  return (
    <MapProvider value={mapInstance}>
      <div className="relative h-full w-full">
        {isLoading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center">
            <Skeleton className="h-full w-full" />
          </div>
        )}
        <div ref={mapContainer} className="h-full w-full" />
        {mapInstance && (
          <>
            <MapControls />
            <SearchBar />
            <ReverseGeocode />
            <CommandPalette />
            <div className="absolute right-12 top-3 z-10">
              <AlertBell />
            </div>
            <PanelManager />
            <LayerManager />
            {isAIOpen && <RegionalIntelligencePanel />}
          </>
        )}
      </div>
    </MapProvider>
  );
}
