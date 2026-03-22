"use client";

import { useEffect, useRef } from "react";
import type { Map as MapLibreMap, RasterTileSource } from "maplibre-gl";
import {
  NLCD_CLASSES,
  NLCD_CATEGORY_CLASSES,
  type NLCDCategory,
} from "@/lib/server/services/nlcd";

export type LandCoverMode = "2021" | "change";

interface LandCoverLayerProps {
  map: MapLibreMap | null;
  mode?: LandCoverMode;
  enabledCategories?: NLCDCategory[];
  opacity?: number;
}

const NLCD_LAYER_ID = "nlcd-wms-layer";
const NLCD_CHANGE_LAYER_ID = "nlcd-change-layer";

const NLCD_2021_WMS_BASE =
  "https://www.mrlc.gov/geoserver/mrlc_display/NLCD_2021_Land_Cover_L48/wms";

const NLCD_CHANGE_WMS_BASE =
  "https://www.mrlc.gov/geoserver/mrlc_change/nlcd_2019_2021_change_l48/wms";

function buildWMSUrl(base: string, layerName: string): string {
  return (
    `${base}?SERVICE=WMS&REQUEST=GetMap` +
    `&LAYERS=${layerName}&FORMAT=image/png&TRANSPARENT=true` +
    `&VERSION=1.3.0&CRS=EPSG:3857&BBOX={bbox-epsg-3857}&WIDTH=256&HEIGHT=256`
  );
}

export function LandCoverLayer({
  map,
  mode = "2021",
  enabledCategories,
  opacity = 0.75,
}: LandCoverLayerProps) {
  const addedRef = useRef<Set<string>>(new Set());

  // NLCD 2021 layer
  useEffect(() => {
    if (!map) return;

    const tileUrl = buildWMSUrl(NLCD_2021_WMS_BASE, "NLCD_2021_Land_Cover_L48");

    function addNLCDLayer() {
      if (!map) return;

      if (map.getSource("nlcd-wms")) {
        if (map.getLayer(NLCD_LAYER_ID)) {
          map.setPaintProperty(
            NLCD_LAYER_ID,
            "raster-opacity",
            mode === "2021" ? opacity : 0
          );
        }
        return;
      }

      map.addSource("nlcd-wms", {
        type: "raster",
        tiles: [tileUrl],
        tileSize: 256,
      });

      map.addLayer({
        id: NLCD_LAYER_ID,
        type: "raster",
        source: "nlcd-wms",
        paint: {
          "raster-opacity": mode === "2021" ? opacity : 0,
        },
      });

      addedRef.current.add(NLCD_LAYER_ID);
    }

    if (map.isStyleLoaded()) {
      addNLCDLayer();
    } else {
      map.once("styledata", addNLCDLayer);
    }

    return () => {
      if (!map || !map.isStyleLoaded()) return;
      if (map.getLayer(NLCD_LAYER_ID)) map.removeLayer(NLCD_LAYER_ID);
      if (map.getSource("nlcd-wms")) map.removeSource("nlcd-wms");
      addedRef.current.delete(NLCD_LAYER_ID);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  // NLCD change layer
  useEffect(() => {
    if (!map) return;

    const tileUrl = buildWMSUrl(NLCD_CHANGE_WMS_BASE, "nlcd_2019_2021_change_l48");

    function addChangeLayer() {
      if (!map) return;

      if (map.getSource("nlcd-change")) {
        if (map.getLayer(NLCD_CHANGE_LAYER_ID)) {
          map.setPaintProperty(
            NLCD_CHANGE_LAYER_ID,
            "raster-opacity",
            mode === "change" ? opacity : 0
          );
        }
        return;
      }

      map.addSource("nlcd-change", {
        type: "raster",
        tiles: [tileUrl],
        tileSize: 256,
      });

      map.addLayer({
        id: NLCD_CHANGE_LAYER_ID,
        type: "raster",
        source: "nlcd-change",
        paint: {
          "raster-opacity": mode === "change" ? opacity : 0,
        },
      });

      addedRef.current.add(NLCD_CHANGE_LAYER_ID);
    }

    if (map.isStyleLoaded()) {
      addChangeLayer();
    } else {
      map.once("styledata", addChangeLayer);
    }

    return () => {
      if (!map || !map.isStyleLoaded()) return;
      if (map.getLayer(NLCD_CHANGE_LAYER_ID)) map.removeLayer(NLCD_CHANGE_LAYER_ID);
      if (map.getSource("nlcd-change")) map.removeSource("nlcd-change");
      addedRef.current.delete(NLCD_CHANGE_LAYER_ID);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  // Update visibility when mode or opacity changes
  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;
    if (map.getLayer(NLCD_LAYER_ID)) {
      map.setPaintProperty(NLCD_LAYER_ID, "raster-opacity", mode === "2021" ? opacity : 0);
    }
    if (map.getLayer(NLCD_CHANGE_LAYER_ID)) {
      map.setPaintProperty(
        NLCD_CHANGE_LAYER_ID,
        "raster-opacity",
        mode === "change" ? opacity : 0
      );
    }
  }, [map, mode, opacity]);

  return null;
}

/** Class filter legend/UI for NLCD — renders category checkboxes + color swatches */
interface LandCoverLegendProps {
  enabledCategories: NLCDCategory[];
  onToggleCategory: (category: NLCDCategory) => void;
}

const ALL_CATEGORIES = Object.keys(NLCD_CATEGORY_CLASSES) as NLCDCategory[];

export function LandCoverLegend({ enabledCategories, onToggleCategory }: LandCoverLegendProps) {
  return (
    <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 text-xs">
      <p className="font-semibold mb-2 text-[hsl(var(--foreground))]">Land Cover Classes</p>
      <div className="flex flex-col gap-1.5">
        {ALL_CATEGORIES.map((cat) => {
          const codes = NLCD_CATEGORY_CLASSES[cat];
          const sampleColor = NLCD_CLASSES[codes[0]]?.color ?? "#888";
          const enabled = enabledCategories.includes(cat);
          return (
            <label
              key={cat}
              className="flex items-center gap-2 cursor-pointer select-none"
            >
              <input
                type="checkbox"
                checked={enabled}
                onChange={() => onToggleCategory(cat)}
                className="rounded"
              />
              <span
                className="w-4 h-3 rounded-sm shrink-0"
                style={{ backgroundColor: sampleColor }}
              />
              <span className="text-[hsl(var(--muted-foreground))]">{cat}</span>
            </label>
          );
        })}
      </div>
    </div>
  );
}
