"use client";

import { useEffect, useRef, useState } from "react";
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
}

/** Thin wrapper around terra-draw: point/polygon/clear toggle emitting `InterventionGeometry`. */
export function InterventionDrawControl({ map, onGeometryChange }: InterventionDrawControlProps) {
  const drawRef = useRef<TerraDraw | null>(null);
  const [mode, setMode] = useState<DrawMode>("point");

  useEffect(() => {
    const draw = new TerraDraw({
      adapter: new TerraDrawMapLibreGLAdapter({ map }),
      modes: [new TerraDrawPointMode(), new TerraDrawPolygonMode(), new TerraDrawSelectMode()],
    });
    drawRef.current = draw;
    draw.start();
    draw.setMode("point");

    const handleChange = () => {
      const snapshot = draw.getSnapshot();
      const latest = snapshot.at(-1);
      onGeometryChange(
        latest ? (latest.geometry as unknown as InterventionGeometry) : null
      );
    };
    draw.on("change", handleChange);

    return () => {
      draw.off("change", handleChange);
      draw.stop();
      drawRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- map identity is what re-attaches terra-draw
  }, [map]);

  function selectMode(next: DrawMode) {
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
