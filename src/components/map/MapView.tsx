"use client";

import { Suspense, useEffect, useRef, useState, useCallback } from "react";
import dynamic from "next/dynamic";
import maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";
import { DEFAULT_VIEWPORT, useMapStore } from "@/stores/map-store";
import { LAND_CONTEXT_GROUP_IDS, useLandContextStore } from "@/stores/land-context-store";
import { isClickOwnedByAnotherSurface } from "@/components/map/land-context/click-ownership";
import { DataLoadingChip } from "@/components/map/DataLoadingChip";
import { SyncIndicator } from "@/components/ui/SyncIndicator";
import { getStyle, skyThemes } from "@/lib/map/styles";
import { MapProvider } from "@/lib/map/map-context";
import { Skeleton } from "@/components/ui/skeleton";
import { MapFocus } from "./MapFocus";
import { readMapFocus } from "@/lib/map/focus-params";
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
import { trpc } from "@/lib/trpc/client";
import { invalidateInterventionDraftsOverlay } from "@/lib/map/use-intervention-drafts";
import {
  LandContextController,
  LandContextLayer,
  LandContextIdentityCard,
  LandContextAccessibleFeatureList,
} from "@/components/map/land-context";
import { LandContextPanelHost } from "@/components/panels/land-context";

const RegionalIntelligencePanel = dynamic(
  () => import("@/components/panels/RegionalIntelligencePanel"),
  { ssr: false }
);

const AiInterventionWorkspace = dynamic(
  () =>
    import("@/components/map/AiInterventionWorkspace").then(
      (mod) => mod.AiInterventionWorkspace
    ),
  { ssr: false }
);

/**
 * The one surface both location actions now open, with the query cache it will need.
 *
 * Holds its own `trpc.useUtils()` rather than MapView doing so, for the same reason
 * `AgentAnalysisPrompt` holds `useRegionalIntelligence`: it mounts only while a location has
 * been picked, so MapView's own hook set -- and the render-count contract pinned in
 * `map-view-render-count.test.tsx` -- stays untouched by a feature that isn't in use. This
 * wrapper is where `InterventionProposalModal`'s `invalidateInterventionDraftsOverlay` call
 * lives on, so Phase 4 can hand it to the ported submit form without hoisting anything.
 */
function MapWorkspace({
  session,
  onClose,
}: {
  session: WorkspaceSession;
  onClose: () => void;
}) {
  const trpcUtils = trpc.useUtils();
  return (
    <AiInterventionWorkspace
      coordinates={session.coordinates}
      initialMode={session.mode}
      onClose={onClose}
      onInterventionSubmitted={() => {
        void invalidateInterventionDraftsOverlay(trpcUtils);
      }}
    />
  );
}

/** Which pane the workspace opens in, and the point it opens over. */
interface WorkspaceSession {
  mode: "ai" | "intervention";
  coordinates: [number, number];
}

/**
 * The confirm-before-analyse popup, with the analysis controller it needs.
 *
 * Held here rather than in MapView so its layer-toggle subscription costs this popup's renders,
 * not the map's -- see "useRegionalIntelligence" in `src/hooks/AGENTS.md`.
 */
function AgentAnalysisPrompt({
  coordinates,
  onClose,
  onOpenWorkspace,
}: {
  coordinates: [number, number];
  onClose: () => void;
  onOpenWorkspace: (session: WorkspaceSession) => void;
}) {
  const { queryLocation } = useRegionalIntelligence();

  const handleAnalyze = useCallback(
    (precision: "approximate" | "exact") => {
      const [lon, lat] = coordinates;
      const coordinateDigits = precision === "approximate" ? 2 : 6;
      const requestedLat = Number(lat.toFixed(coordinateDigits));
      const requestedLon = Number(lon.toFixed(coordinateDigits));
      useRegionalIntelligenceStore.getState().openPanel(
        requestedLat,
        requestedLon,
        precision
      );
      // Dispatched before the close that unmounts this component: `queryLocation` reads the
      // viewed-layer ref synchronously and then owns its own request, so the unmount cannot
      // strand it.
      void queryLocation(requestedLat, requestedLon, undefined, precision);
      // The workspace opens over the coordinate the user CONFIRMED, at the precision they
      // chose -- not the raw click -- so the pane and the request it just sent name the same
      // point. `onClose` then dismisses this popup; it never closes the workspace.
      onOpenWorkspace({ mode: "ai", coordinates: [requestedLon, requestedLat] });
      onClose();
    },
    [coordinates, onClose, onOpenWorkspace, queryLocation]
  );

  return (
    <AgentInteraction
      coordinates={coordinates}
      onAnalyze={handleAnalyze}
      onProposeIntervention={() =>
        onOpenWorkspace({ mode: "intervention", coordinates })
      }
      onClose={onClose}
    />
  );
}


// True while at least one land-context group is toggled on; the canvas click is then a
// land-context selection, never an agent query (see land-context/AGENTS.md).
function landContextOwnsClick(): boolean {
  const enabled = useLandContextStore.getState().enabledGroups;
  return LAND_CONTEXT_GROUP_IDS.some((group) => Boolean(enabled[group]));
}

export default function MapView() {
  const mapContainer = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [mapInstance, setMapInstance] = useState<maplibregl.Map | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [webglError, setWebglError] = useState(false);
  const [agentCoords, setAgentCoords] = useState<[number, number] | null>(null);
  // ONE workspace, in one of two modes -- the two independent `agentCoords`/`interventionCoords`
  // overlay slices this replaced could never both be open, and closing one never returned to the
  // other. See conductor/tracks/ai_intervention_workspace_20260913/.
  const [workspaceSession, setWorkspaceSession] = useState<WorkspaceSession | null>(null);

  // One selector per field, never the whole store: MapView owns the map instance and every
  // layer under it, so a re-render here is the most expensive one on the page. Subscribing to
  // the store object re-rendered it on every unrelated write -- a feature selection, a layer
  // toggle, a query-point pin. See conductor/code_styleguides/typescript.md, "Subscribe to the
  // narrowest Zustand state slice".
  //
  // `viewport` is deliberately absent from this list. It is read once, to seed the camera, by a
  // `[]`-dependency callback -- and `setViewport` mints a new object on every moveend, resize
  // and load, so subscribing to it re-rendered this whole subtree on every pan. `initMap` reads
  // it through `getState()` instead.
  const is3DEnabled = useMapStore((state) => state.is3DEnabled);
  const isGlobeView = useMapStore((state) => state.isGlobeView);
  const terrainExaggeration = useMapStore((state) => state.terrainExaggeration);
  const currentStyle = useMapStore((state) => state.currentStyle);
  const isTerrainEnabled = useMapStore((state) => state.isTerrainEnabled);
  const setViewport = useMapStore((state) => state.setViewport);

  const prevStyleRef = useRef(currentStyle);
  // Only the open flag: every streaming token of an analysis writes this store.
  const isAIOpen = useRegionalIntelligenceStore((state) => state.isOpen);

  const handleCloseAgentInteraction = useCallback(() => {
    setAgentCoords(null);
  }, []);

  /**
   * Opens (or re-points) the workspace and dismisses the click popup.
   *
   * Deliberately writes nothing else. Neither store is reset here: the workspace itself decides
   * whether a new coordinate may overwrite an unsubmitted draft (see its seeding guard), which
   * is what keeps a second map click from silently destroying the first click's work.
   */
  const handleOpenWorkspace = useCallback((session: WorkspaceSession) => {
    setWorkspaceSession(session);
    setAgentCoords(null);
  }, []);

  const handleCloseWorkspace = useCallback(() => {
    setWorkspaceSession(null);
  }, []);

  const initMap = useCallback(() => {
    if (mapRef.current || !mapContainer.current) return;

    const protocol = new Protocol();
    maplibregl.addProtocol("pmtiles", protocol.tile);

    // The seed camera, read once. This callback runs once per mount and never re-runs, so a
    // subscription would only cost renders -- see the note above the selectors.
    const viewport = useMapStore.getState().viewport;
    const focus = readMapFocus(new URLSearchParams(window.location.search));
    const m = new maplibregl.Map({
      container: mapContainer.current,
      style: getStyle(currentStyle),
      center: [focus?.longitude ?? viewport.longitude, focus?.latitude ?? viewport.latitude],
      zoom: focus?.zoom ?? viewport.zoom,
      bearing: viewport.bearing,
      pitch: is3DEnabled ? 60 : 0,
      maxPitch: 85,
      canvasContextAttributes: { antialias: false, failIfMajorPerformanceCaveat: false },
      // Attribution text lives on /about#attribution instead of the map chrome;
      // MapLibre's default bar otherwise squeezes into the bottom-right corner
      // that SyncIndicator claims (see the comment above it).
      attributionControl: false,
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
      // One click, one meaning. `isClickOwnedByAnotherSurface` is the single predicate every
      // bare-click registrant checks: a panel capturing a query point, an intervention feature
      // (which opens its own modal), or a scalar cell under inspection each own the click and
      // the agent popup must not open over them. Land context additionally owns the click while
      // any of its groups is on (it makes the point selection). Read from stores, not props, so
      // this handler stays registered once for the life of the map -- see map/AGENTS.md.
      if (isClickOwnedByAnotherSurface(m, e.point)) return;
      if (landContextOwnsClick()) return;
      // Do not send coordinates to the analysis service until the user confirms.
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
            {/* PNW land-context reference plane (parcels/land-use, electric utility
                territories, BLM lands, state-managed lands). Controller bridges the
                bounded tRPC reader into the store; Layer renders native MapLibre
                source/layers per group and wires hover/click; PanelHost renders the
                persistent detail panel from the same store selection. Toggle controls
                for the four groups live in LayerPanel's own LandContextDockSection --
                LandContextToggles is an unstyled placeholder superseded by it and is
                deliberately not mounted here (see land-context/index.ts). */}
            <LandContextController />
            <LandContextLayer map={mapInstance} />
            {/* Dismissible hover/keyboard-focus identity card -- spec "concise feature
                identity ... on hover or keyboard focus". Renders nothing while no
                feature is hovered/focused (LandContextIdentityCard returns null),
                so it costs nothing when the layer is off. */}
            <LandContextIdentityCard />
            <LandContextPanelHost />
            {/* Keyboard/screen-reader-equivalent textual feature list -- spec
                "Provide keyboard selection and equivalent textual feature/contact
                lists"; every action reachable via map hover/click must also be
                reachable via keyboard focus + Enter/Space. Always mounted (renders
                null when `results` is empty) and placed in normal document flow
                after the map controls so it is reachable by Tab without a pointer,
                independent of whether the pinned detail panel is open. */}
            <div className="pointer-events-auto absolute bottom-20 left-4 z-20 max-h-[40vh] overflow-y-auto">
              <LandContextAccessibleFeatureList />
            </div>
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
            {/* Offline/sync status chip -- a marker with no controls, `MapDateSummary`
                precedent. The controls it used to open standalone (area download, storage,
                conflict resolution) are the dock's Offline & Sync section now; see
                src/components/map/AGENTS.md "One manager, no floating surfaces". ManagerRail
                claims bottom-left and MapLibre's own controls claim top-right, so this is the
                one open corner left for persistent chrome. */}
            <div className="pointer-events-none absolute bottom-4 right-4 z-10 flex flex-col items-end gap-2">
              <div className="pointer-events-auto">
                <SyncIndicator />
              </div>
            </div>
            {/* The STANDALONE analysis panel, for the conversations that were not opened from
                the workspace (a resumed saved conversation, for instance). It stands itself
                down -- it reads `isVisible` -- while the workspace is mounted and embedding the
                same conversation, so the two never stack. Mount gating stays on `isOpen` alone
                so MapView gains no new store subscription; see map-view-render-count.test.tsx. */}
            {isAIOpen && <RegionalIntelligencePanel />}
            {agentCoords && (
              <AgentAnalysisPrompt
                coordinates={agentCoords}
                onClose={handleCloseAgentInteraction}
                onOpenWorkspace={handleOpenWorkspace}
              />
            )}
            {workspaceSession && (
              <MapWorkspace session={workspaceSession} onClose={handleCloseWorkspace} />
            )}
          </>
        )}
      </div>
    </MapProvider>
  );
}
