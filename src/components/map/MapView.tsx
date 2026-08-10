"use client";

import { Suspense, useEffect, useRef, useState, useCallback } from "react";
import dynamic from "next/dynamic";
import maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";
import { DEFAULT_VIEWPORT, useMapStore } from "@/stores/map-store";
import { DataLoadingChip } from "@/components/map/DataLoadingChip";
import { getStyle, skyThemes } from "@/lib/map/styles";
import { MapProvider } from "@/lib/map/map-context";
import { Skeleton } from "@/components/ui/skeleton";
import { MapFocus } from "./MapFocus";
import { ReverseGeocode } from "@/components/search/ReverseGeocode";
import MapKeyboardShortcuts from "./MapKeyboardShortcuts";
import { ManagerRail } from "./layer-panel/ManagerRail";
import { LayerPanel } from "./layer-panel/LayerPanel";
import LayerManager from "./LayerManager";
import HoverTooltip from "./HoverTooltip";
import { MapDateSummary } from "./MapDateSummary";
import TimeSliderCapabilitiesLoader from "./TimeSliderCapabilitiesLoader";
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

    // The container's size travels with every viewport write, because it is half of what the
    // bbox derivation needs and it can change without the camera moving at all (a window
    // resize, the dock opening). Reported on load as well as on move, so the very first
    // queries are scoped to the real screen rather than to the 1024x512 default.
    const publishViewport = () => {
      const center = m.getCenter();
      const container = m.getContainer();
      setViewport({
        longitude: center.lng,
        latitude: center.lat,
        zoom: m.getZoom(),
        bearing: m.getBearing(),
        pitch: m.getPitch(),
        widthPx: container.clientWidth || DEFAULT_VIEWPORT.widthPx,
        heightPx: container.clientHeight || DEFAULT_VIEWPORT.heightPx,
      });
    };

    m.on("load", publishViewport);
    m.on("moveend", publishViewport);
    m.on("resize", publishViewport);

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
      {/*
        No `--layer-panel-inset` since 2026-08-09. That variable existed so the chrome the
        manager would cover could slide out from under it; the only two consumers were the
        manager's own toggle button and the bottom toolbar, and both are gone -- the toolbar
        into the manager, the button into `ManagerRail`, which unmounts while the manager is
        open and therefore has nothing to keep clear of. The panel is an overlay in this same
        box and never reflows the canvas -- see LayerPanel.tsx for why the camera takes padding
        instead.
      */}
      <div className="relative h-full w-full">
        {isLoading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center">
            <Skeleton className="h-full w-full" />
          </div>
        )}
        <div ref={mapContainer} className="h-full w-full" />
        {mapInstance && (
          <>
            <DataLoadingChip />
            {/* Suspense because MapFocus reads useSearchParams; MapView itself is
                only ever mounted client-side, but the boundary keeps that true
                regardless of how a future route renders it. */}
            <Suspense fallback={null}>
              <MapFocus />
            </Suspense>
            <ReverseGeocode />
            {/* Headless and always mounted: r/t/g/1/2/3 and Ctrl/Cmd+K. The surfaces that
                used to bind these collapse now, and a collapsed section is an unmounted
                section, so the bindings cannot live inside one. */}
            <MapKeyboardShortcuts />
            {/* The collapsed manager: the way back in, the unread alert count and the legend,
                in one row. Mounted before the panel so the panel paints over its shadow
                rather than under it; it renders nothing while the panel is open. */}
            <ManagerRail />
            {/* The one control surface on this map: search, render mode, every layer switch
                with its own opacity and date sliders, and every former right-hand sheet, in one
                left-edge column. See src/components/map/AGENTS.md "One manager, no floating
                surfaces". Its dates are CONTROLS and it is closable; `MapDateSummary` below
                states them and is not. */}
            <LayerPanel />
            {/* Mounted before LayerManager so its style.load handler registers
                first -- see the ordering note in ServiceAreaLayer.tsx. */}
            <ServiceAreaLayer map={mapInstance} />
            <LayerManager />
            {/* The one thing about time that never leaves the screen: what the drawn layers are
                showing, and whether that is one day or several. It replaced `TimeDatePill` on
                2026-08-09 -- the pill asserted the map's ONE date, and there is no such date now
                -- but the invariant the pill carried survives it: a date that silently filters
                what is drawn cannot be something a reader opens a panel to find. The per-layer
                controls live on the dock's rows; this states and never sets. */}
            <MapDateSummary />
            {/* Headless and always mounted: the only read of
                environmental.getSliderCapabilities, whose payload carries every layer's axis
                and the server's today. Capabilities stay GLOBAL even though the days are now
                per layer -- one payload feeds every row's slider -- and it cannot live in the
                dock, because a closed dock unmounts while the days it supplies still key every
                warehouse-backed query on this map. */}
            <TimeSliderCapabilitiesLoader />
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
