"use client";

import { useEffect, useRef } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";

const FIRE_SOURCE_ID = "firms-fire-wms";
const FIRE_LAYER_ID = "firms-fire-layer";

// NASA FIRMS WMS -- free, no auth, shows fire detections as raster tiles
const FIRMS_WMS_URL =
  "https://firms.modaps.eosdis.nasa.gov/mapserver/wms/fires/USA_contiguous_and_Hawaii/?" +
  "SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap" +
  "&LAYERS=fires_viirs_snpp_24" +
  "&FORMAT=image/png&TRANSPARENT=true" +
  "&SRS=EPSG:3857&WIDTH=256&HEIGHT=256" +
  "&BBOX={bbox-epsg-3857}";

interface FireLayerProps {
  map: MapLibreMap | null;
  visible?: boolean;
  opacity?: number;
}

export function FireLayer({
  map,
  visible = true,
  opacity = 0.85,
}: FireLayerProps) {
  const addedRef = useRef(false);

  useEffect(() => {
    if (!map) return;

    if (!visible) {
      if (map.isStyleLoaded()) {
        if (map.getLayer(FIRE_LAYER_ID)) map.removeLayer(FIRE_LAYER_ID);
        if (map.getSource(FIRE_SOURCE_ID)) map.removeSource(FIRE_SOURCE_ID);
        addedRef.current = false;
      }
      return;
    }

    function addLayer() {
      if (!map || !map.isStyleLoaded()) return;
      if (addedRef.current) return;

      map.addSource(FIRE_SOURCE_ID, {
        type: "raster",
        tiles: [FIRMS_WMS_URL],
        tileSize: 256,
        attribution: "NASA FIRMS",
      });

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
          id: FIRE_LAYER_ID,
          type: "raster",
          source: FIRE_SOURCE_ID,
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
      addedRef.current = false;
      if (visible) addLayer();
    };
    map.on("style.load", onStyleLoad);

    return () => {
      map.off("style.load", onStyleLoad);
      if (map.isStyleLoaded()) {
        if (map.getLayer(FIRE_LAYER_ID)) map.removeLayer(FIRE_LAYER_ID);
        if (map.getSource(FIRE_SOURCE_ID)) map.removeSource(FIRE_SOURCE_ID);
      }
      addedRef.current = false;
    };
  }, [map, visible, opacity]);

  return null;
}
