"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, RasterTileSource } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import { DEMO_SOIL_POINTS } from "@/lib/map/demo-data";

const SOIL_SOURCE_ID = "soilgrids-wms";
const SOIL_LAYER_ID = "soilgrids-layer";
const SOIL_CIRCLE_SOURCE_ID = "soil-demo-points";
const SOIL_CIRCLE_LAYER_ID = "soil-demo-circles";

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

const SOIL_WMS_BASE = "https://maps.isric.org/mapserv";

function getSoilTileUrl(property: SoilProperty): string {
  return (
    `${SOIL_WMS_BASE}?map=/map/${property}.map` +
    "&SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap" +
    `&LAYERS=${property}_0-5cm_mean` +
    "&CRS=EPSG:3857&FORMAT=image/png&TRANSPARENT=true" +
    "&WIDTH=256&HEIGHT=256" +
    "&BBOX={bbox-epsg-3857}"
  );
}

/** Maps SoilProperty to the corresponding field name on DEMO_SOIL_POINTS. */
const PROPERTY_FIELD_MAP: Record<SoilProperty, string> = {
  phh2o: "ph",
  soc: "organicCarbon",
  clay: "clay",
  sand: "sand",
  nitrogen: "nitrogen",
  bdod: "bulkDensity",
  cec: "cec",
};

/** Value ranges [low, mid, high] for the green-yellow-red interpolation per property. */
const PROPERTY_RANGES: Record<SoilProperty, [number, number, number]> = {
  phh2o: [5.0, 7.0, 9.0],
  soc: [0, 3, 7],
  clay: [0, 25, 50],
  sand: [0, 40, 80],
  nitrogen: [0, 0.2, 0.4],
  bdod: [0.8, 1.2, 1.6],
  cec: [10, 25, 40],
};

/** Build a GeoJSON FeatureCollection from DEMO_SOIL_POINTS. */
function buildSoilFeatureCollection(): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: DEMO_SOIL_POINTS.map((pt) => ({
      type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: [pt.lon, pt.lat] },
      properties: {
        ph: pt.ph,
        organicCarbon: pt.organicCarbon,
        clay: pt.clay,
        sand: pt.sand,
        nitrogen: pt.nitrogen,
        bulkDensity: pt.bulkDensity,
        cec: pt.cec,
      },
    })),
  };
}

/** Build data-driven circle-color expression for the given soil property. */
function getCircleColorExpr(property: SoilProperty): maplibregl.ExpressionSpecification {
  const field = PROPERTY_FIELD_MAP[property];
  const [low, mid, high] = PROPERTY_RANGES[property];
  return [
    "interpolate",
    ["linear"],
    ["get", field],
    low, "#2ecc71",   // green
    mid, "#f1c40f",   // yellow
    high, "#e74c3c",  // red
  ];
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

    // --- Demo circle overlay ---
    if (!m.getSource(SOIL_CIRCLE_SOURCE_ID)) {
      m.addSource(SOIL_CIRCLE_SOURCE_ID, {
        type: "geojson",
        data: buildSoilFeatureCollection(),
      });
    }
    if (!m.getLayer(SOIL_CIRCLE_LAYER_ID)) {
      m.addLayer(
        {
          id: SOIL_CIRCLE_LAYER_ID,
          type: "circle",
          source: SOIL_CIRCLE_SOURCE_ID,
          paint: {
            "circle-radius": 8,
            "circle-color": getCircleColorExpr(property),
            "circle-stroke-width": 2,
            "circle-stroke-color": "#ffffff",
            "circle-opacity": opacity,
          },
        },
        beforeId,
      );
    }
  }, []);

  const removeAllLayers = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(m, [SOIL_LAYER_ID], SOIL_SOURCE_ID);
    safeRemoveLayerAndSource(m, [SOIL_CIRCLE_LAYER_ID], SOIL_CIRCLE_SOURCE_ID);
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

  // Update tile URLs, circle colors, and opacity when property/opacity change
  useEffect(() => {
    if (!map || !visible) return;
    try {
      if (!map.getStyle()) return;
    } catch {
      return;
    }

    // Update WMS raster tile URL + opacity
    const rasterSource = map.getSource(SOIL_SOURCE_ID) as RasterTileSource | undefined;
    if (rasterSource) {
      rasterSource.setTiles([getSoilTileUrl(property)]);
    }
    if (map.getLayer(SOIL_LAYER_ID)) {
      map.setPaintProperty(SOIL_LAYER_ID, "raster-opacity", opacity);
    }

    // Update circle color expression + opacity
    if (map.getLayer(SOIL_CIRCLE_LAYER_ID)) {
      map.setPaintProperty(SOIL_CIRCLE_LAYER_ID, "circle-color", getCircleColorExpr(property));
      map.setPaintProperty(SOIL_CIRCLE_LAYER_ID, "circle-opacity", opacity);
    }
  }, [map, property, opacity, visible]);

  return null;
}
