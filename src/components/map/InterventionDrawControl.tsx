"use client";

import { useLayoutEffect, useRef, useState } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { TerraDraw, TerraDrawPointMode, TerraDrawPolygonMode, TerraDrawSelectMode } from "terra-draw";
import { TerraDrawMapLibreGLAdapter } from "terra-draw-maplibre-gl-adapter";
import type { InterventionGeometry } from "@/lib/geo/intervention-geometry-schema";

type DrawMode = "point" | "polygon";

export interface InterventionDrawControlProps {
  /** The live MapLibre map instance to attach terra-draw to. */
  map: MapLibreMap;
  /** Called with the currently drawn geometry, or `null` once nothing is drawn. */
  onGeometryChange: (geometry: InterventionGeometry | null) => void;
  /** Restores a saved geometry when a new drawing session attaches. */
  initialGeometry?: InterventionGeometry | null;
  /** Reports restoration failure or clears a previous error after success. */
  onRestoreError?: (message: string | null) => void;
}

/** Thin wrapper around terra-draw: point/polygon/clear toggle emitting `InterventionGeometry`. */
export function InterventionDrawControl({ map, onGeometryChange, initialGeometry, onRestoreError }: InterventionDrawControlProps) {
  const drawRef = useRef<TerraDraw | null>(null);
  const [mode, setMode] = useState<DrawMode>("point");
  const modeRef = useRef<DrawMode>("point");
  const latestProps = useRef({ initialGeometry, onGeometryChange, onRestoreError });

  useLayoutEffect(() => {
    latestProps.current = { initialGeometry, onGeometryChange, onRestoreError };
  }, [initialGeometry, onGeometryChange, onRestoreError]);

  // Dispose drawing before the map owner's passive cleanup; see map/AGENTS.md.
  useLayoutEffect(() => {
    let draw: TerraDraw | null = null;
    const handleChange = () => {
      const snapshot = draw?.getSnapshot() ?? [];
      const latest = snapshot.at(-1);
      latestProps.current.onGeometryChange(
        latest ? (latest.geometry as unknown as InterventionGeometry) : null
      );
    };

    const attach = () => {
      if (draw || !map.isStyleLoaded()) return;
      draw = new TerraDraw({
        adapter: new TerraDrawMapLibreGLAdapter({ map }),
        modes: [new TerraDrawPointMode(), new TerraDrawPolygonMode(), new TerraDrawSelectMode()],
      });
      drawRef.current = draw;
      draw.start();
      draw.setMode(modeRef.current);

      const savedGeometry = latestProps.current.initialGeometry;
      if (savedGeometry) {
        try {
          if (savedGeometry.type === "MultiPolygon") throw new Error("Unsupported drawing geometry");
          const results = draw.addFeatures([{
            type: "Feature",
            geometry: savedGeometry,
            properties: { mode: savedGeometry.type === "Point" ? "point" : "polygon" },
          }]);
          if (results.length !== 1 || !results[0].valid) throw new Error("Invalid drawing geometry");
          latestProps.current.onRestoreError?.(null);
        } catch {
          draw.clear();
          latestProps.current.onRestoreError?.("Your saved geometry could not be restored. It is still saved; clear and redraw it to continue.");
        }
      }
      draw.on("change", handleChange);
      map.off("load", attach);
      map.off("render", attach);
    };
    map.on("load", attach);
    map.on("render", attach);
    attach();

    return () => {
      map.off("load", attach);
      map.off("render", attach);
      draw?.off("change", handleChange);
      draw?.stop();
      drawRef.current = null;
    };
  }, [map]);

  function selectMode(next: DrawMode) {
    modeRef.current = next;
    setMode(next);
    drawRef.current?.setMode(next);
  }

  function clearGeometry() {
    drawRef.current?.clear();
    onGeometryChange(null);
  }

  return (
    <div role="group" aria-label="Draw intervention geometry" className="flex gap-2">
      <button
        type="button"
        aria-pressed={mode === "point"}
        onClick={() => selectMode("point")}
        className="min-h-11 px-3 py-1.5 rounded-lg border border-[hsl(var(--border))] text-sm text-[hsl(var(--foreground))] aria-pressed:bg-[hsl(var(--primary))] aria-pressed:text-[hsl(var(--primary-foreground))]"
      >
        Point
      </button>
      <button
        type="button"
        aria-pressed={mode === "polygon"}
        onClick={() => selectMode("polygon")}
        className="min-h-11 px-3 py-1.5 rounded-lg border border-[hsl(var(--border))] text-sm text-[hsl(var(--foreground))] aria-pressed:bg-[hsl(var(--primary))] aria-pressed:text-[hsl(var(--primary-foreground))]"
      >
        Polygon
      </button>
      <button
        type="button"
        onClick={clearGeometry}
        className="min-h-11 px-3 py-1.5 rounded-lg border border-[hsl(var(--border))] text-sm text-[hsl(var(--foreground))]"
      >
        Clear
      </button>
    </div>
  );
}
