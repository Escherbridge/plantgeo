"use client";

import { useEffect, useRef, useState } from "react";
import type { Map as MapLibreMap, RasterTileSource } from "maplibre-gl";
import { getNDVITileUrl, getNDWITileUrl, NDVI_COLOR_RAMP, NDWI_COLOR_RAMP } from "@/lib/server/services/vegetation";

export type VegetationMode = "ndvi" | "ndwi" | "nbr";

interface VegetationLayerProps {
  map: MapLibreMap | null;
  mode?: VegetationMode;
  year?: number;
  month?: number;
  ndviMode?: "absolute" | "anomaly";
  showNDWI?: boolean;
  opacity?: number;
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
}: VegetationLayerProps) {
  const addedRef = useRef<Set<string>>(new Set());

  // Add or update NDVI raster layer
  useEffect(() => {
    if (!map) return;

    const tileUrl = getNDVITileUrl(year, month, ndviMode);

    function addNDVILayer() {
      if (!map) return;

      if (map.getSource("ndvi-overlay")) {
        (map.getSource("ndvi-overlay") as RasterTileSource).setTiles([tileUrl]);
        if (map.getLayer(NDVI_LAYER_ID)) {
          map.setPaintProperty(NDVI_LAYER_ID, "raster-opacity", mode === "ndvi" ? opacity : 0);
        }
        return;
      }

      map.addSource("ndvi-overlay", {
        type: "raster",
        tiles: [tileUrl],
        tileSize: 256,
        attribution: "NASA GIBS / Copernicus",
      });

      map.addLayer({
        id: NDVI_LAYER_ID,
        type: "raster",
        source: "ndvi-overlay",
        paint: {
          "raster-opacity": mode === "ndvi" ? opacity : 0,
        },
      });

      addedRef.current.add(NDVI_LAYER_ID);
    }

    if (map.isStyleLoaded()) {
      addNDVILayer();
    } else {
      map.once("styledata", addNDVILayer);
    }

    return () => {
      if (!map || !map.isStyleLoaded()) return;
      if (map.getLayer(NDVI_LAYER_ID)) map.removeLayer(NDVI_LAYER_ID);
      if (map.getSource("ndvi-overlay")) map.removeSource("ndvi-overlay");
      addedRef.current.delete(NDVI_LAYER_ID);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  // Update NDVI tile URL when year/month/ndviMode changes
  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;
    const source = map.getSource("ndvi-overlay") as RasterTileSource | undefined;
    if (!source) return;
    source.setTiles([getNDVITileUrl(year, month, ndviMode)]);
    if (map.getLayer(NDVI_LAYER_ID)) {
      map.setPaintProperty(NDVI_LAYER_ID, "raster-opacity", mode === "ndvi" ? opacity : 0);
    }
  }, [map, year, month, ndviMode, mode, opacity]);

  // NDWI layer
  useEffect(() => {
    if (!map) return;

    const ndwiUrl = getNDWITileUrl(year, month);

    function addNDWILayer() {
      if (!map) return;

      if (map.getSource("ndwi-overlay")) {
        (map.getSource("ndwi-overlay") as RasterTileSource).setTiles([ndwiUrl]);
        if (map.getLayer(NDWI_LAYER_ID)) {
          map.setPaintProperty(
            NDWI_LAYER_ID,
            "raster-opacity",
            showNDWI && mode === "ndwi" ? opacity : 0
          );
        }
        return;
      }

      map.addSource("ndwi-overlay", {
        type: "raster",
        tiles: [ndwiUrl],
        tileSize: 256,
        attribution: "NASA GIBS",
      });

      map.addLayer({
        id: NDWI_LAYER_ID,
        type: "raster",
        source: "ndwi-overlay",
        paint: {
          "raster-opacity": showNDWI && mode === "ndwi" ? opacity : 0,
        },
      });

      addedRef.current.add(NDWI_LAYER_ID);
    }

    if (map.isStyleLoaded()) {
      addNDWILayer();
    } else {
      map.once("styledata", addNDWILayer);
    }

    return () => {
      if (!map || !map.isStyleLoaded()) return;
      if (map.getLayer(NDWI_LAYER_ID)) map.removeLayer(NDWI_LAYER_ID);
      if (map.getSource("ndwi-overlay")) map.removeSource("ndwi-overlay");
      addedRef.current.delete(NDWI_LAYER_ID);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  // Update NDWI visibility
  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;
    const source = map.getSource("ndwi-overlay") as RasterTileSource | undefined;
    if (!source) return;
    source.setTiles([getNDWITileUrl(year, month)]);
    if (map.getLayer(NDWI_LAYER_ID)) {
      map.setPaintProperty(
        NDWI_LAYER_ID,
        "raster-opacity",
        showNDWI && mode === "ndwi" ? opacity : 0
      );
    }
  }, [map, year, month, showNDWI, mode, opacity]);

  // NBR layer
  useEffect(() => {
    if (!map) return;

    function addNBRLayer() {
      if (!map) return;
      if (map.getSource("nbr-recovery")) {
        if (map.getLayer(NBR_LAYER_ID)) {
          map.setPaintProperty(NBR_LAYER_ID, "raster-opacity", mode === "nbr" ? opacity : 0);
        }
        return;
      }

      map.addSource("nbr-recovery", {
        type: "raster",
        tiles: [
          "https://tiles.arcgis.com/tiles/P3ePLMYs2RVChkJx/arcgis/rest/services/NatureServe_LandscapeIntegrity/MapServer/tile/{z}/{y}/{x}",
        ],
        tileSize: 256,
      });

      map.addLayer({
        id: NBR_LAYER_ID,
        type: "raster",
        source: "nbr-recovery",
        paint: {
          "raster-opacity": mode === "nbr" ? opacity : 0,
        },
      });

      addedRef.current.add(NBR_LAYER_ID);
    }

    if (map.isStyleLoaded()) {
      addNBRLayer();
    } else {
      map.once("styledata", addNBRLayer);
    }

    return () => {
      if (!map || !map.isStyleLoaded()) return;
      if (map.getLayer(NBR_LAYER_ID)) map.removeLayer(NBR_LAYER_ID);
      if (map.getSource("nbr-recovery")) map.removeSource("nbr-recovery");
      addedRef.current.delete(NBR_LAYER_ID);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  // Update NBR visibility
  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;
    if (map.getLayer(NBR_LAYER_ID)) {
      map.setPaintProperty(NBR_LAYER_ID, "raster-opacity", mode === "nbr" ? opacity : 0);
    }
  }, [map, mode, opacity]);

  return null;
}

/** Inline color legend for the active vegetation mode */
export function VegetationLegend({ mode }: { mode: VegetationMode }) {
  if (mode === "nbr") return <ColorLegend title="Burn Recovery (NBR)" ramp={NBR_COLOR_RAMP} />;
  if (mode === "ndwi") return <ColorLegend title="Water Stress (NDWI)" ramp={NDWI_COLOR_RAMP} />;
  return <ColorLegend title="Vegetation Health (NDVI)" ramp={NDVI_COLOR_RAMP} />;
}
