"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, RasterTileSource } from "maplibre-gl";
import { getNDVITileUrl, getNDWITileUrl, NDVI_COLOR_RAMP, NDWI_COLOR_RAMP } from "@/lib/vegetation";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";

export type VegetationMode = "ndvi" | "ndwi" | "nbr";

interface VegetationLayerProps {
  map: MapLibreMap | null;
  mode?: VegetationMode;
  year?: number;
  month?: number;
  ndviMode?: "absolute" | "anomaly";
  showNDWI?: boolean;
  opacity?: number;
  visible?: boolean;
}

const NDVI_LAYER_ID = "ndvi-overlay-layer";
const NDWI_LAYER_ID = "ndwi-overlay-layer";
const NBR_LAYER_ID = "nbr-recovery-layer";

const NBR_COLOR_RAMP = [
  { value: -1.0, color: "#7a0000", label: "Severely burned" },
  { value: -0.5, color: "#c0392b", label: "Moderately burned" },
  { value: -0.1, color: "#e67e22", label: "Low severity" },
  { value: 0.1, color: "#27ae60", label: "Unburned" },
  { value: 0.5, color: "#1abc9c", label: "Enhanced greenness" },
];

function ColorLegend({
  title,
  ramp,
}: {
  title: string;
  ramp: { color: string; label: string }[];
}) {
  return (
    <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 text-xs">
      <p className="font-semibold mb-2 text-[hsl(var(--foreground))]">{title}</p>
      <div className="flex flex-col gap-1">
        {ramp.map((stop) => (
          <div key={stop.color} className="flex items-center gap-2">
            <span
              className="w-4 h-3 rounded-sm shrink-0"
              style={{ backgroundColor: stop.color }}
            />
            <span className="text-[hsl(var(--muted-foreground))]">{stop.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function VegetationLayer({
  map,
  mode = "ndvi",
  year = new Date().getFullYear(),
  month = new Date().getMonth() + 1,
  ndviMode = "absolute",
  showNDWI = false,
  opacity = 0.75,
  visible = true,
}: VegetationLayerProps) {
  // Keep latest prop values in refs so the style.load handler always uses current values
  const propsRef = useRef({ mode, year, month, ndviMode, showNDWI, opacity, visible });
  propsRef.current = { mode, year, month, ndviMode, showNDWI, opacity, visible };

  const addAllLayers = useCallback((m: MapLibreMap) => {
    const { mode, year, month, ndviMode, showNDWI, opacity } = propsRef.current;
    const beforeId = getFirstSymbolLayer(m);

    // --- NDVI ---
    if (!m.getSource("ndvi-overlay")) {
      m.addSource("ndvi-overlay", {
        type: "raster",
        tiles: [getNDVITileUrl(year, month, ndviMode)],
        tileSize: 256,
        attribution: "NASA GIBS / Copernicus",
      });
    }
    if (!m.getLayer(NDVI_LAYER_ID)) {
      m.addLayer({
        id: NDVI_LAYER_ID,
        type: "raster",
        source: "ndvi-overlay",
        paint: { "raster-opacity": mode === "ndvi" ? opacity : 0 },
      }, beforeId);
    }

    // --- NDWI ---
    if (!m.getSource("ndwi-overlay")) {
      m.addSource("ndwi-overlay", {
        type: "raster",
        tiles: [getNDWITileUrl(year, month)],
        tileSize: 256,
        attribution: "NASA GIBS",
      });
    }
    if (!m.getLayer(NDWI_LAYER_ID)) {
      m.addLayer({
        id: NDWI_LAYER_ID,
        type: "raster",
        source: "ndwi-overlay",
        paint: { "raster-opacity": showNDWI && mode === "ndwi" ? opacity : 0 },
      }, beforeId);
    }

    // --- NBR ---
    if (!m.getSource("nbr-recovery")) {
      m.addSource("nbr-recovery", {
        type: "raster",
        tiles: [
          "https://tiles.arcgis.com/tiles/P3ePLMYs2RVChkJx/arcgis/rest/services/NatureServe_LandscapeIntegrity/MapServer/tile/{z}/{y}/{x}",
        ],
        tileSize: 256,
      });
    }
    if (!m.getLayer(NBR_LAYER_ID)) {
      m.addLayer({
        id: NBR_LAYER_ID,
        type: "raster",
        source: "nbr-recovery",
        paint: { "raster-opacity": mode === "nbr" ? opacity : 0 },
      }, beforeId);
    }
  }, []);

  const removeAllLayers = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(m, [NDVI_LAYER_ID], "ndvi-overlay");
    safeRemoveLayerAndSource(m, [NDWI_LAYER_ID], "ndwi-overlay");
    safeRemoveLayerAndSource(m, [NBR_LAYER_ID], "nbr-recovery");
  }, []);

  // Main effect: add/remove layers and listen for style changes
  useEffect(() => {
    if (!map) return;

    if (!visible) {
      removeAllLayers(map);
      return;
    }

    // Handler that re-adds all layers after a style change
    const onStyleLoad = () => {
      if (!propsRef.current.visible) return;
      addAllLayers(map);
    };

    // Add layers now if style is ready, otherwise wait for first load
    if (map.isStyleLoaded()) {
      addAllLayers(map);
    } else {
      map.once("style.load", () => addAllLayers(map));
    }

    // Persist layers across future style changes
    map.on("style.load", onStyleLoad);

    return () => {
      map.off("style.load", onStyleLoad);
      removeAllLayers(map);
    };
  }, [map, visible, addAllLayers, removeAllLayers]);

  // Update tile URLs and opacity when year/month/mode/opacity change
  useEffect(() => {
    if (!map || !visible) return;
    try {
      if (!map.getStyle()) return;
    } catch {
      return;
    }

    // NDVI tile URL + opacity
    const ndviSource = map.getSource("ndvi-overlay") as RasterTileSource | undefined;
    if (ndviSource) {
      ndviSource.setTiles([getNDVITileUrl(year, month, ndviMode)]);
    }
    if (map.getLayer(NDVI_LAYER_ID)) {
      map.setPaintProperty(NDVI_LAYER_ID, "raster-opacity", mode === "ndvi" ? opacity : 0);
    }

    // NDWI tile URL + opacity
    const ndwiSource = map.getSource("ndwi-overlay") as RasterTileSource | undefined;
    if (ndwiSource) {
      ndwiSource.setTiles([getNDWITileUrl(year, month)]);
    }
    if (map.getLayer(NDWI_LAYER_ID)) {
      map.setPaintProperty(
        NDWI_LAYER_ID,
        "raster-opacity",
        showNDWI && mode === "ndwi" ? opacity : 0
      );
    }

    // NBR opacity (tile URL is static)
    if (map.getLayer(NBR_LAYER_ID)) {
      map.setPaintProperty(NBR_LAYER_ID, "raster-opacity", mode === "nbr" ? opacity : 0);
    }
  }, [map, year, month, ndviMode, mode, showNDWI, opacity, visible]);

  return null;
}

/** Inline color legend for the active vegetation mode */
export function VegetationLegend({ mode }: { mode: VegetationMode }) {
  if (mode === "nbr") return <ColorLegend title="Burn Recovery (NBR)" ramp={NBR_COLOR_RAMP} />;
  if (mode === "ndwi") return <ColorLegend title="Water Stress (NDWI)" ramp={NDWI_COLOR_RAMP} />;
  return <ColorLegend title="Vegetation Health (NDVI)" ramp={NDVI_COLOR_RAMP} />;
}
