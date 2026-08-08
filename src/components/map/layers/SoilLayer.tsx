"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, RasterTileSource } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import { getEnvironmentalTileTemplate } from "@/lib/vegetation";

const SOIL_SOURCE_ID = "soilgrids-wms";
const SOIL_LAYER_ID = "soilgrids-layer";

/**
 * The six SoilGrids topsoil (0-5cm) properties this platform actually has data for --
 * the same six `soilgrids.ts#PROPERTY_FIELDS` fetches for the point query, and the six
 * the raster pipeline plan names for the first-party tile release (see
 * `conductor/tracks/ingestion_warehouse_consolidation_20260803/lanes/lane-F-raster-sources-to-r2.md`
 * \u00a74.2). "clay" and "sand" were never wired to a data source on either path and are
 * not offered; "ocd" was offered by neither path until now.
 */
export type SoilProperty = "phh2o" | "soc" | "nitrogen" | "bdod" | "cec" | "ocd";

export const SOIL_PROPERTY_LABELS: Record<SoilProperty, string> = {
  phh2o: "pH (H\u2082O)",
  soc: "Organic Carbon",
  nitrogen: "Nitrogen",
  bdod: "Bulk Density",
  cec: "CEC",
  ocd: "Organic Carbon Density",
};

/**
 * The point-query field (`SoilProperties` in `soilgrids.ts`) each raster property
 * corresponds to -- restated as literals rather than imported, since soilgrids.ts
 * pulls in the server DB and this is a client component. Lets SoilDetails highlight the
 * one point-query row that actually matches the selected raster property, instead of
 * always showing the same six fields no matter what is selected.
 */
export const SOIL_PROPERTY_POINT_FIELD: Record<
  SoilProperty,
  "ph" | "organicCarbon" | "nitrogen" | "bulkDensity" | "cec" | "ocd"
> = {
  phh2o: "ph",
  soc: "organicCarbon",
  nitrogen: "nitrogen",
  bdod: "bulkDensity",
  cec: "cec",
  ocd: "ocd",
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
  /** The raster's authored strength. The design value, not a control. */
  opacity?: number;
  /**
   * The reader's per-layer MULTIPLIER over `opacity`. Wired even though the layer is never
   * created today -- `getEnvironmentalTileTemplate` returns "" until a first-party SoilGrids
   * release exists, so `addAllLayers` returns before the source is added. It works the day
   * the tiles land; until then the layer tree renders this row's slider as inert rather than
   * offering a control that adjusts nothing.
   */
  opacityScale?: number;
}

export function SoilLayer({
  map,
  visible = true,
  property = "soc",
  opacity = 0.7,
  opacityScale = 1,
}: SoilLayerProps) {
  const drawnOpacity = opacity * opacityScale;
  // Keep latest prop values in refs so the style.load handler always uses current values
  const propsRef = useRef({ visible, property, drawnOpacity });
  propsRef.current = { visible, property, drawnOpacity };

  const addAllLayers = useCallback((m: MapLibreMap) => {
    const { property, drawnOpacity } = propsRef.current;
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
          paint: { "raster-opacity": drawnOpacity },
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
      map.setPaintProperty(SOIL_LAYER_ID, "raster-opacity", drawnOpacity);
    }

  }, [map, property, drawnOpacity, visible]);

  return null;
}
