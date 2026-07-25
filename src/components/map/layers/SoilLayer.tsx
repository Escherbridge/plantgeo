"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, RasterTileSource } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import { getEnvironmentalTileTemplate } from "@/lib/vegetation";

const SOIL_SOURCE_ID = "soilgrids-wms";
const SOIL_LAYER_ID = "soilgrids-layer";

export type SoilProperty = "phh2o" | "soc" | "clay" | "sand" | "nitrogen" | "bdod" | "cec";

export const SOIL_PROPERTY_LABELS: Record<SoilProperty, string> = {
  phh2o: "pH (H\u2082O)",
  soc: "Organic Carbon",
  clay: "Clay Content",
  sand: "Sand Content",
  nitrogen: "Nitrogen",
  bdod: "Bulk Density",
  cec: "CEC",
};

function getSoilTileUrl(property: SoilProperty): string {
  return getEnvironmentalTileTemplate(
    `soil/${property}/0-5cm/mean/{z}/{x}/{y}.png`
  );
}

interface SoilLayerProps {
  map: MapLibreMap | null;
  visible?: boolean;
  property?: SoilProperty;
  opacity?: number;
}

export function SoilLayer({
  map,
  visible = true,
  property = "soc",
  opacity = 0.7,
}: SoilLayerProps) {
  // Keep latest prop values in refs so the style.load handler always uses current values
  const propsRef = useRef({ visible, property, opacity });
  propsRef.current = { visible, property, opacity };

  const addAllLayers = useCallback((m: MapLibreMap) => {
    const { property, opacity } = propsRef.current;
    const beforeId = getFirstSymbolLayer(m);
    const tileUrl = getSoilTileUrl(property);
    if (!tileUrl) return;

    // --- WMS raster layer ---
    if (!m.getSource(SOIL_SOURCE_ID)) {
      m.addSource(SOIL_SOURCE_ID, {
        type: "raster",
        tiles: [tileUrl],
        tileSize: 256,
        attribution: "SoilGrids &mdash; ISRIC",
      });
    }
    if (!m.getLayer(SOIL_LAYER_ID)) {
      m.addLayer(
        {
          id: SOIL_LAYER_ID,
          type: "raster",
          source: SOIL_SOURCE_ID,
          paint: { "raster-opacity": opacity },
        },
        beforeId,
      );
    }

  }, []);

  const removeAllLayers = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(m, [SOIL_LAYER_ID], SOIL_SOURCE_ID);
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

  // Update tile URL and opacity when property/opacity change.
  useEffect(() => {
    if (!map || !visible) return;
    try {
      if (!map.getStyle()) return;
    } catch {
      return;
    }

    // Update WMS raster tile URL + opacity
    const rasterSource = map.getSource(SOIL_SOURCE_ID) as RasterTileSource | undefined;
    const tileUrl = getSoilTileUrl(property);
    if (rasterSource && tileUrl) {
      rasterSource.setTiles([tileUrl]);
    }
    if (map.getLayer(SOIL_LAYER_ID)) {
      map.setPaintProperty(SOIL_LAYER_ID, "raster-opacity", opacity);
    }

  }, [map, property, opacity, visible]);

  return null;
}
