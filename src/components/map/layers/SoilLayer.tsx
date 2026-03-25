"use client";

import { useEffect, useRef } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";

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
  const addedRef = useRef(false);

  useEffect(() => {
    if (!map) return;

    if (!visible) {
      if (map.isStyleLoaded()) {
        if (map.getLayer(SOIL_LAYER_ID)) map.removeLayer(SOIL_LAYER_ID);
        if (map.getSource(SOIL_SOURCE_ID)) map.removeSource(SOIL_SOURCE_ID);
        addedRef.current = false;
      }
      return;
    }

    const tileUrl = getSoilTileUrl(property);

    function addLayer() {
      if (!map || !map.isStyleLoaded()) return;

      if (map.getSource(SOIL_SOURCE_ID)) {
        // Update existing source tiles
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (map.getSource(SOIL_SOURCE_ID) as any).setTiles([tileUrl]);
        if (map.getLayer(SOIL_LAYER_ID)) {
          map.setPaintProperty(SOIL_LAYER_ID, "raster-opacity", opacity);
        }
        return;
      }

      map.addSource(SOIL_SOURCE_ID, {
        type: "raster",
        tiles: [tileUrl],
        tileSize: 256,
        attribution: "SoilGrids &mdash; ISRIC",
      });

      // Find first symbol layer for proper ordering
      const layers = map.getStyle().layers || [];
      let beforeId: string | undefined;
      for (const l of layers) {
        if (l.type === "symbol") {
          beforeId = l.id;
          break;
        }
      }

      map.addLayer(
        {
          id: SOIL_LAYER_ID,
          type: "raster",
          source: SOIL_SOURCE_ID,
          paint: { "raster-opacity": opacity },
        },
        beforeId,
      );

      addedRef.current = true;
    }

    if (map.isStyleLoaded()) {
      addLayer();
    } else {
      map.once("styledata", addLayer);
    }

    const onStyleLoad = () => {
      if (visible) addLayer();
    };
    map.on("style.load", onStyleLoad);

    return () => {
      map.off("style.load", onStyleLoad);
      if (map.isStyleLoaded()) {
        if (map.getLayer(SOIL_LAYER_ID)) map.removeLayer(SOIL_LAYER_ID);
        if (map.getSource(SOIL_SOURCE_ID)) map.removeSource(SOIL_SOURCE_ID);
      }
      addedRef.current = false;
    };
  }, [map, visible, property, opacity]);

  return null;
}
