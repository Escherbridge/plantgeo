"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, GeoJSONSource, RasterTileSource } from "maplibre-gl";
import {
  getEnvironmentalTileTemplate,
  getNDVITileUrl,
  getNDWITileUrl,
  NDVI_COLOR_RAMP,
  NDWI_COLOR_RAMP,
} from "@/lib/vegetation";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import type { ExpressionSpecification } from "@/types/map";

export type VegetationMode = "ndvi" | "ndwi" | "nbr";

interface VegetationLayerProps {
  map: MapLibreMap | null;
  /**
   * Measured NDVI cells read from the warehouse, one per sampling-grid square, each
   * carrying an `ndvi` number in its properties. Empty -- never null -- when the layer is
   * switched off or the viewport holds none, so `setData` has something to clear with.
   */
  geojson?: GeoJSON.FeatureCollection | null;
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
const NDVI_CELL_SOURCE_ID = "vegetation-ndvi-cells";
const NDVI_CELL_FILL_LAYER_ID = "vegetation-ndvi-cells-fill";
const NDVI_CELL_OUTLINE_LAYER_ID = "vegetation-ndvi-cells-outline";

const EMPTY_CELL_COLLECTION: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

/**
 * Fill colour interpolated over the shared NDVI ramp, so a cell and the legend below it
 * cannot disagree about what a value looks like.
 *
 * Derived from NDVI_COLOR_RAMP rather than restated, which is what forces the assertion:
 * MapLibre types an expression as a union of fixed-length tuples, and spreading a mapped
 * array widens it to `(string | number | string[])[]`. Restating the nine stops inline
 * would let the literal be contextually typed -- and would be the first place the fill and
 * the legend drift apart.
 */
const NDVI_CELL_FILL_COLOR = [
  "interpolate",
  ["linear"],
  ["get", "ndvi"],
  ...NDVI_COLOR_RAMP.flatMap((stop) => [stop.value, stop.color]),
] as unknown as ExpressionSpecification;

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
  geojson = null,
  mode = "ndvi",
  year = new Date().getFullYear(),
  month = new Date().getMonth() + 1,
  ndviMode = "absolute",
  showNDWI = false,
  opacity = 0.75,
  visible = true,
}: VegetationLayerProps) {
  // Keep latest prop values in refs so the style.load handler always uses current values
  const propsRef = useRef({ geojson, mode, year, month, ndviMode, showNDWI, opacity, visible });
  propsRef.current = { geojson, mode, year, month, ndviMode, showNDWI, opacity, visible };

  const addAllLayers = useCallback((m: MapLibreMap) => {
    const { geojson, mode, year, month, ndviMode, showNDWI, opacity } = propsRef.current;
    const beforeId = getFirstSymbolLayer(m);
    const ndviTileUrl = getNDVITileUrl(year, month, ndviMode);
    const ndwiTileUrl = getNDWITileUrl(year, month);
    const nbrTileUrl = getEnvironmentalTileTemplate(
      "vegetation/nbr/latest/{z}/{x}/{y}.png"
    );

    // Each product attaches independently. NDWI has no upstream at all (GIBS
    // publishes no water-index raster) and NBR is unpublished, so an
    // all-or-nothing guard here would suppress NDVI, which is real and served.

    // --- NDVI ---
    if (ndviTileUrl && !m.getSource("ndvi-overlay")) {
      m.addSource("ndvi-overlay", {
        type: "raster",
        tiles: [ndviTileUrl],
        tileSize: 256,
        attribution: "NASA GIBS / Copernicus",
      });
    }
    if (ndviTileUrl && !m.getLayer(NDVI_LAYER_ID)) {
      m.addLayer({
        id: NDVI_LAYER_ID,
        type: "raster",
        source: "ndvi-overlay",
        paint: { "raster-opacity": mode === "ndvi" ? opacity : 0 },
      }, beforeId);
    }

    // --- NDWI ---
    if (ndwiTileUrl && !m.getSource("ndwi-overlay")) {
      m.addSource("ndwi-overlay", {
        type: "raster",
        tiles: [ndwiTileUrl],
        tileSize: 256,
        attribution: "NASA GIBS",
      });
    }
    if (ndwiTileUrl && !m.getLayer(NDWI_LAYER_ID)) {
      m.addLayer({
        id: NDWI_LAYER_ID,
        type: "raster",
        source: "ndwi-overlay",
        paint: { "raster-opacity": showNDWI && mode === "ndwi" ? opacity : 0 },
      }, beforeId);
    }

    // --- NBR ---
    if (nbrTileUrl && !m.getSource("nbr-recovery")) {
      m.addSource("nbr-recovery", {
        type: "raster",
        tiles: [nbrTileUrl],
        tileSize: 256,
      });
    }
    if (nbrTileUrl && !m.getLayer(NBR_LAYER_ID)) {
      m.addLayer({
        id: NBR_LAYER_ID,
        type: "raster",
        source: "nbr-recovery",
        paint: { "raster-opacity": mode === "nbr" ? opacity : 0 },
      }, beforeId);
    }

    // --- Measured NDVI grid cells (environmental.getVegetationIndex) ---
    // Added last so it draws ABOVE the rasters: these are the readings this platform
    // ingested and can cite a scene for, while the NDVI raster underneath is a global
    // composite proxied from GIBS. Where the grid has been sampled the measurement wins;
    // everywhere else the raster shows through unchanged. The outline is deliberate --
    // it keeps the cells legible as discrete 0.25-degree samples rather than as a
    // continuous surface the sampling never produced.
    if (!m.getSource(NDVI_CELL_SOURCE_ID)) {
      m.addSource(NDVI_CELL_SOURCE_ID, {
        type: "geojson",
        data: geojson ?? EMPTY_CELL_COLLECTION,
      });
    }
    if (!m.getLayer(NDVI_CELL_FILL_LAYER_ID)) {
      m.addLayer({
        id: NDVI_CELL_FILL_LAYER_ID,
        type: "fill",
        source: NDVI_CELL_SOURCE_ID,
        paint: {
          "fill-color": NDVI_CELL_FILL_COLOR,
          "fill-opacity": mode === "ndvi" ? opacity : 0,
        },
      }, beforeId);
    }
    if (!m.getLayer(NDVI_CELL_OUTLINE_LAYER_ID)) {
      m.addLayer({
        id: NDVI_CELL_OUTLINE_LAYER_ID,
        type: "line",
        source: NDVI_CELL_SOURCE_ID,
        paint: {
          "line-color": "#1b3a1b",
          "line-width": 0.5,
          "line-opacity": mode === "ndvi" ? 0.35 : 0,
        },
      }, beforeId);
    }
  }, []);

  const removeAllLayers = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(m, [NDVI_LAYER_ID], "ndvi-overlay");
    safeRemoveLayerAndSource(m, [NDWI_LAYER_ID], "ndwi-overlay");
    safeRemoveLayerAndSource(m, [NBR_LAYER_ID], "nbr-recovery");
    safeRemoveLayerAndSource(
      m,
      [NDVI_CELL_FILL_LAYER_ID, NDVI_CELL_OUTLINE_LAYER_ID],
      NDVI_CELL_SOURCE_ID
    );
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

    // Add now if the style is ready; the persistent listener covers both the
    // first load and every later swap. See src/components/map/AGENTS.md
    // "Style.load listener order" -- no `once` alongside `on`.
    if (map.isStyleLoaded()) addAllLayers(map);
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
    const ndviTileUrl = getNDVITileUrl(year, month, ndviMode);
    if (ndviSource && ndviTileUrl) {
      ndviSource.setTiles([ndviTileUrl]);
    }
    if (map.getLayer(NDVI_LAYER_ID)) {
      map.setPaintProperty(NDVI_LAYER_ID, "raster-opacity", mode === "ndvi" ? opacity : 0);
    }

    // NDWI tile URL + opacity
    const ndwiSource = map.getSource("ndwi-overlay") as RasterTileSource | undefined;
    const ndwiTileUrl = getNDWITileUrl(year, month);
    if (ndwiSource && ndwiTileUrl) {
      ndwiSource.setTiles([ndwiTileUrl]);
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

    // Measured NDVI cells: new viewport data, then per-mode opacity. setData rather than a
    // re-add, so panning swaps the cells without tearing the source down under the map. The
    // source can legitimately be missing here on the first pass -- the style had not loaded
    // when this ran -- and addAllLayers then creates it from propsRef with this same data.
    const cellSource = map.getSource(NDVI_CELL_SOURCE_ID) as GeoJSONSource | undefined;
    if (cellSource) cellSource.setData(geojson ?? EMPTY_CELL_COLLECTION);
    if (map.getLayer(NDVI_CELL_FILL_LAYER_ID)) {
      map.setPaintProperty(
        NDVI_CELL_FILL_LAYER_ID,
        "fill-opacity",
        mode === "ndvi" ? opacity : 0
      );
    }
    if (map.getLayer(NDVI_CELL_OUTLINE_LAYER_ID)) {
      map.setPaintProperty(
        NDVI_CELL_OUTLINE_LAYER_ID,
        "line-opacity",
        mode === "ndvi" ? 0.35 : 0
      );
    }
  }, [map, geojson, year, month, ndviMode, mode, showNDWI, opacity, visible]);

  return null;
}

/** Inline color legend for the active vegetation mode */
export function VegetationLegend({ mode }: { mode: VegetationMode }) {
  if (mode === "nbr") return <ColorLegend title="Burn Recovery (NBR)" ramp={NBR_COLOR_RAMP} />;
  if (mode === "ndwi") return <ColorLegend title="Water Stress (NDWI)" ramp={NDWI_COLOR_RAMP} />;
  return <ColorLegend title="Vegetation Health (NDVI)" ramp={NDVI_COLOR_RAMP} />;
}
