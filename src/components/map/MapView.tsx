"use client";

import { Suspense, useEffect, useRef, useState, useCallback } from "react";
import dynamic from "next/dynamic";
import maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";
import { useMapStore } from "@/stores/map-store";
import { getStyle, skyThemes } from "@/lib/map/styles";
import { MapProvider } from "@/lib/map/map-context";
import { Skeleton } from "@/components/ui/skeleton";
import MapControls from "./MapControls";
import { MapFocus } from "./MapFocus";
import SearchBar from "@/components/search/SearchBar";
import { ReverseGeocode } from "@/components/search/ReverseGeocode";
import { CommandPalette } from "@/components/search/CommandPalette";
import { AlertBell } from "@/components/ui/AlertBell";
import PanelManager from "./PanelManager";
import LayerManager from "./LayerManager";
import HoverTooltip from "./HoverTooltip";
import TimeSliderPanel from "./TimeSliderPanel";
import { ServiceAreaLayer } from "./ServiceAreaLayer";
import { useRegionalIntelligenceStore } from "@/stores/regional-intelligence-store";
import { useRegionalIntelligence } from "@/hooks/useRegionalIntelligence";
import { AgentInteraction } from "./AgentInteraction";

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
  const [agentCoords, setAgentCoords] = useState<[number, number] | null>(null);

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

  const handleCloseAgentInteraction = useCallback(() => {
    setAgentCoords(null);
  }, []);

  const handleAnalyzeLocation = useCallback((precision: "approximate" | "exact") => {
    if (!agentCoords) return;

    const [lon, lat] = agentCoords;
    const coordinateDigits = precision === "approximate" ? 2 : 6;
    const requestedLat = Number(lat.toFixed(coordinateDigits));
    const requestedLon = Number(lon.toFixed(coordinateDigits));
    useRegionalIntelligenceStore.getState().openPanel(
      requestedLat,
      requestedLon,
      precision
    );
    setAgentCoords(null);
    void queryLocation(requestedLat, requestedLon, undefined, precision);
  }, [agentCoords, queryLocation]);

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

    m.on("error", (e) => {
      // Protomaps prunes old daily builds; an expired archive 404s without CORS
      // headers, which MapLibre reports only as "Failed to fetch" (see the note
      // above DEFAULT_PMTILES_ARCHIVE_URL in src/lib/map/sources.ts). Name the
      // actual fix so a blank map is diagnosable instead of a silent failure.
      console.error(
        "MapLibre reported a load error. If the basemap is blank, the pinned " +
          "Protomaps build may have expired -- set NEXT_PUBLIC_PMTILES_URL to a " +
          "current archive.",
        e.error
      );
    });

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
      // A panel capturing query points owns the click: without this stand-down, one click
      // both drops the soil query pin and opens the agent popup over it. Read from the
      // store rather than a prop so this handler stays registered once, for the life of
      // the map -- see src/components/map/AGENTS.md "Picking a point to query".
      if (useMapStore.getState().isCapturingQueryPoint) return;
      // Do not send coordinates to the analysis service until the user confirms.
      const features = m.queryRenderedFeatures(e.point);
      if (features && features.length > 0) return;
      const { lat, lng } = e.lngLat;
      setAgentCoords([lng, lat]);
    });

    // Right-click stays available even while a panel is capturing: it is the one way to
    // reach the agent popup without giving up the pin.
    m.on("contextmenu", (e) => {
      e.preventDefault();
      const { lat, lng } = e.lngLat;
      setAgentCoords([lng, lat]);
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

  // Re-assert render mode after a basemap swap. See src/components/map/AGENTS.md
  // "Style swaps and render-mode state" -- the handler MUST be registered before
  // setStyle, which fires style.load synchronously.
  useEffect(() => {
    const m = mapRef.current;
    if (!m || prevStyleRef.current === currentStyle) return;
    prevStyleRef.current = currentStyle;

    function restoreRenderMode() {
      if (!m) return;
      m.setSky(skyThemes[currentStyle]);
      if (isTerrainEnabled && m.getSource("terrain-dem")) {
        m.setTerrain({ source: "terrain-dem", exaggeration: terrainExaggeration });
      } else {
        m.setTerrain(null);
      }
      m.setProjection({ type: isGlobeView ? "globe" : "mercator" });
    }

    m.once("style.load", restoreRenderMode);
    m.setStyle(getStyle(currentStyle));

    // A no-op diff never fires style.load; drop the handler so a later swap
    // cannot replay these now-stale values.
    return () => {
      m.off("style.load", restoreRenderMode);
    };
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

  // Projection only -- never zoom. See src/components/map/AGENTS.md
  // "The layer toggle is the only source of layer visibility".
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !m.isStyleLoaded()) return;
    m.setProjection({ type: isGlobeView ? "globe" : "mercator" });
  }, [isGlobeView]);

  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;

    const targetPitch = is3DEnabled ? 60 : 0;
    function applyPitch() {
      m?.easeTo({ pitch: targetPitch, duration: 500 });
    }

    // Applying on style.load rather than dropping the change keeps the toggle
    // from becoming a no-op when it is flipped mid-style-load.
    if (!m.isStyleLoaded()) {
      m.once("style.load", applyPitch);
      return () => {
        m.off("style.load", applyPitch);
      };
    }
    applyPitch();
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
            {/* Suspense because MapFocus reads useSearchParams; MapView itself is
                only ever mounted client-side, but the boundary keeps that true
                regardless of how a future route renders it. */}
            <Suspense fallback={null}>
              <MapFocus />
            </Suspense>
            <MapControls />
            <SearchBar />
            <ReverseGeocode />
            <CommandPalette />
            <PanelManager />
            {/* Mounted before LayerManager so its style.load handler registers
                first -- see the ordering note in ServiceAreaLayer.tsx. */}
            <ServiceAreaLayer map={mapInstance} />
            <LayerManager />
            {/* The right-hand panel region, always mounted: it owns the only read of
                environmental.getSliderCapabilities (the store's day and the whole axis come
                from that payload alone) and pins the global time marker at its top, so the
                selected day is reachable without opening any panel. */}
            <TimeSliderPanel />
            <HoverTooltip map={mapInstance} />
            {isAIOpen && <RegionalIntelligencePanel />}
            {agentCoords && (
              <AgentInteraction
                coordinates={agentCoords}
                onAnalyze={handleAnalyzeLocation}
                onClose={handleCloseAgentInteraction}
              />
            )}
          </>
        )}
      </div>
    </MapProvider>
  );
}
