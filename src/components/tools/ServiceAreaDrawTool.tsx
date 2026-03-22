"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { trpc } from "@/lib/trpc/client";
import type { Map as MapLibreMap } from "maplibre-gl";

interface ServiceAreaDrawToolProps {
  teamId: string;
  /** Current service area GeoJSON polygon (if any) */
  existingServiceArea?: Record<string, unknown> | null;
  /** MapLibre map instance ref */
  mapRef?: React.RefObject<MapLibreMap | null>;
  onSaved?: () => void;
}

type DrawState = "idle" | "drawing" | "complete";

interface Coordinate {
  lng: number;
  lat: number;
}

const SOURCE_ID = "service-area-draw";
const FILL_LAYER_ID = "service-area-fill";
const LINE_LAYER_ID = "service-area-line";
const PREVIEW_LAYER_ID = "service-area-preview-line";

function coordsToGeoJsonPolygon(coords: Coordinate[]) {
  if (coords.length < 3) return null;
  const ring = coords.map((c) => [c.lng, c.lat] as [number, number]);
  // Close the ring
  ring.push(ring[0]);
  return {
    type: "Polygon" as const,
    coordinates: [ring],
  };
}

export function ServiceAreaDrawTool({
  teamId,
  existingServiceArea,
  mapRef,
  onSaved,
}: ServiceAreaDrawToolProps) {
  const [drawState, setDrawState] = useState<DrawState>(
    existingServiceArea ? "complete" : "idle"
  );
  const [drawnCoords, setDrawnCoords] = useState<Coordinate[]>([]);
  const [hoverCoord, setHoverCoord] = useState<Coordinate | null>(null);
  const [error, setError] = useState<string | null>(null);

  const isDrawing = drawState === "drawing";
  const coordsRef = useRef<Coordinate[]>(drawnCoords);
  coordsRef.current = drawnCoords;

  const updateMutation = trpc.teams.updateTeam.useMutation({
    onSuccess: () => {
      setError(null);
      onSaved?.();
    },
    onError: (e) => setError(e.message),
  });

  // ── MapLibre layer setup ───────────────────────────────────────────────

  const getMap = useCallback(() => mapRef?.current ?? null, [mapRef]);

  const initLayers = useCallback(() => {
    const map = getMap();
    if (!map) return;

    if (!map.getSource(SOURCE_ID)) {
      map.addSource(SOURCE_ID, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
    }

    if (!map.getLayer(FILL_LAYER_ID)) {
      map.addLayer({
        id: FILL_LAYER_ID,
        type: "fill",
        source: SOURCE_ID,
        paint: {
          "fill-color": "#4caf50",
          "fill-opacity": 0.2,
        },
      });
    }

    if (!map.getLayer(LINE_LAYER_ID)) {
      map.addLayer({
        id: LINE_LAYER_ID,
        type: "line",
        source: SOURCE_ID,
        paint: {
          "line-color": "#4caf50",
          "line-width": 2,
          "line-opacity": 0.8,
        },
      });
    }

    if (!map.getLayer(PREVIEW_LAYER_ID)) {
      map.addLayer({
        id: PREVIEW_LAYER_ID,
        type: "line",
        source: SOURCE_ID,
        filter: ["==", ["get", "preview"], true],
        paint: {
          "line-color": "#4caf50",
          "line-width": 1,
          "line-opacity": 0.5,
          "line-dasharray": [4, 2],
        },
      });
    }
  }, [getMap]);

  const updateMapSource = useCallback(
    (coords: Coordinate[], hover: Coordinate | null) => {
      const map = getMap();
      if (!map) return;
      const source = map.getSource(SOURCE_ID) as
        | { setData: (d: unknown) => void }
        | undefined;
      if (!source) return;

      const features: unknown[] = [];

      if (coords.length >= 3) {
        const polygon = coordsToGeoJsonPolygon(coords);
        if (polygon) {
          features.push({ type: "Feature", geometry: polygon, properties: { preview: false } });
        }
      } else if (coords.length >= 2) {
        features.push({
          type: "Feature",
          geometry: {
            type: "LineString",
            coordinates: coords.map((c) => [c.lng, c.lat]),
          },
          properties: { preview: false },
        });
      }

      // Preview line from last vertex to hover
      if (hover && coords.length >= 1) {
        const last = coords[coords.length - 1];
        features.push({
          type: "Feature",
          geometry: {
            type: "LineString",
            coordinates: [
              [last.lng, last.lat],
              [hover.lng, hover.lat],
            ],
          },
          properties: { preview: true },
        });
      }

      source.setData({ type: "FeatureCollection", features });
    },
    [getMap]
  );

  // Show existing service area on mount
  useEffect(() => {
    const map = getMap();
    if (!map || !existingServiceArea) return;

    const onLoad = () => {
      initLayers();
      const source = map.getSource(SOURCE_ID) as
        | { setData: (d: unknown) => void }
        | undefined;
      if (source) {
        source.setData({
          type: "FeatureCollection",
          features: [
            {
              type: "Feature",
              geometry: existingServiceArea,
              properties: { preview: false },
            },
          ],
        });
      }
    };

    if (map.loaded()) {
      onLoad();
    } else {
      map.once("load", onLoad);
    }
  }, [existingServiceArea, getMap, initLayers]);

  // ── Drawing event handlers ─────────────────────────────────────────────

  useEffect(() => {
    const map = getMap();
    if (!map || !isDrawing) return;

    map.getCanvas().style.cursor = "crosshair";

    const handleClick = (e: { lngLat: { lng: number; lat: number } }) => {
      const newCoord = { lng: e.lngLat.lng, lat: e.lngLat.lat };
      setDrawnCoords((prev) => {
        const next = [...prev, newCoord];
        updateMapSource(next, null);
        return next;
      });
    };

    const handleMouseMove = (e: { lngLat: { lng: number; lat: number } }) => {
      const coord = { lng: e.lngLat.lng, lat: e.lngLat.lat };
      setHoverCoord(coord);
      updateMapSource(coordsRef.current, coord);
    };

    const handleDblClick = (e: { lngLat: { lng: number; lat: number }; preventDefault: () => void }) => {
      e.preventDefault();
      setDrawState("complete");
      setHoverCoord(null);
      map.getCanvas().style.cursor = "";
      const finalCoords = coordsRef.current;
      if (finalCoords.length >= 3) {
        updateMapSource(finalCoords, null);
      }
    };

    map.on("click", handleClick);
    map.on("mousemove", handleMouseMove);
    map.on("dblclick", handleDblClick);

    return () => {
      map.off("click", handleClick);
      map.off("mousemove", handleMouseMove);
      map.off("dblclick", handleDblClick);
      map.getCanvas().style.cursor = "";
    };
  }, [isDrawing, getMap, updateMapSource]);

  // ── Actions ────────────────────────────────────────────────────────────

  const handleStartDraw = useCallback(() => {
    const map = getMap();
    if (map) initLayers();
    setDrawnCoords([]);
    setHoverCoord(null);
    setDrawState("drawing");
    setError(null);
  }, [getMap, initLayers]);

  const handleClear = useCallback(() => {
    setDrawnCoords([]);
    setDrawState("idle");
    setHoverCoord(null);
    const map = getMap();
    if (map) {
      const source = map.getSource(SOURCE_ID) as
        | { setData: (d: unknown) => void }
        | undefined;
      source?.setData({ type: "FeatureCollection", features: [] });
    }
  }, [getMap]);

  const handleSave = useCallback(async () => {
    const polygon = coordsToGeoJsonPolygon(drawnCoords);
    if (!polygon) {
      setError("Draw at least 3 points to create a polygon.");
      return;
    }
    await updateMutation.mutateAsync({
      id: teamId,
      serviceArea: polygon as Record<string, unknown>,
    });
  }, [drawnCoords, teamId, updateMutation]);

  const hasDrawn = drawnCoords.length >= 3 || (existingServiceArea && drawState === "complete");

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <div
          className={`w-2 h-2 rounded-full ${
            drawState === "drawing"
              ? "bg-green-500 animate-pulse"
              : drawState === "complete"
              ? "bg-green-500"
              : "bg-[hsl(var(--muted-foreground))]"
          }`}
        />
        <span className="text-sm font-medium text-[hsl(var(--foreground))]">
          {drawState === "drawing"
            ? "Click to add vertices — double-click to finish"
            : drawState === "complete"
            ? `Service area defined (${drawnCoords.length || "existing"} vertices)`
            : "No service area defined"}
        </span>
      </div>

      {error && (
        <p className="text-xs text-red-500 bg-red-500/10 rounded-lg px-3 py-2">{error}</p>
      )}

      <div className="flex gap-2 flex-wrap">
        {drawState !== "drawing" && (
          <button
            onClick={handleStartDraw}
            className="px-3 py-2 rounded-lg bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] text-sm font-medium hover:opacity-90 transition-opacity"
          >
            {existingServiceArea || drawnCoords.length > 0 ? "Redraw" : "Draw Service Area"}
          </button>
        )}

        {drawState === "drawing" && (
          <button
            onClick={() => {
              setDrawState(drawnCoords.length >= 3 ? "complete" : "idle");
              setHoverCoord(null);
              const map = getMap();
              if (map) map.getCanvas().style.cursor = "";
            }}
            className="px-3 py-2 rounded-lg bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))] text-sm font-medium hover:opacity-90 transition-opacity"
          >
            Cancel Drawing
          </button>
        )}

        {hasDrawn && drawState !== "drawing" && (
          <>
            <button
              onClick={handleSave}
              disabled={updateMutation.isPending}
              className="px-3 py-2 rounded-lg bg-green-600 text-white text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              {updateMutation.isPending ? "Saving…" : "Save Service Area"}
            </button>
            <button
              onClick={handleClear}
              className="px-3 py-2 rounded-lg border border-[hsl(var(--border))] text-[hsl(var(--foreground))] text-sm hover:bg-[hsl(var(--muted))] transition-colors"
            >
              Clear
            </button>
          </>
        )}
      </div>

      {drawState === "drawing" && drawnCoords.length > 0 && (
        <p className="text-xs text-[hsl(var(--muted-foreground))]">
          {drawnCoords.length} {drawnCoords.length === 1 ? "vertex" : "vertices"} placed.
          {drawnCoords.length < 3 && ` Add ${3 - drawnCoords.length} more to close polygon.`}
        </p>
      )}
    </div>
  );
}
