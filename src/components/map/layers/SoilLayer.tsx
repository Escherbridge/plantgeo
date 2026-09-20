"use client";

import { useCallback, useEffect, useRef } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import type { PublishedSoilRaster } from "@/lib/map/soil-raster";

export type { SoilProperty, PublishedSoilRaster } from "@/lib/map/soil-raster";
export { SOIL_PROPERTY_LABELS, SOIL_PROPERTY_POINT_FIELD } from "@/lib/map/soil-raster";

interface SoilLayerProps {
  map: MapLibreMap | null;
  release: PublishedSoilRaster;
  visible?: boolean;
  opacity?: number;
  opacityScale?: number;
}

/** One independently switchable SoilGrids PMTiles raster release. */
export function SoilLayer({
  map,
  release,
  visible = true,
  opacity = 0.7,
  opacityScale = 1,
}: SoilLayerProps) {
  const sourceId = `soilgrids-${release.property}-source`;
  const layerId = `soilgrids-${release.property}-layer`;
  const releaseIdentity = [
    release.archiveUrl,
    release.minZoom,
    release.maxZoom,
    release.attribution,
    ...release.bounds,
  ].join("|");
  const drawnOpacity = opacity * opacityScale;
  const propsRef = useRef({ visible, release, drawnOpacity });
  useEffect(() => {
    propsRef.current = { visible, release, drawnOpacity };
  }, [visible, release, drawnOpacity]);

  const addLayer = useCallback(
    (target: MapLibreMap) => {
      const current = propsRef.current;
      if (!target.getSource(sourceId)) {
        target.addSource(sourceId, {
          type: "raster",
          url: `pmtiles://${current.release.archiveUrl}`,
          tileSize: 256,
          minzoom: current.release.minZoom,
          maxzoom: current.release.maxZoom,
          bounds: current.release.bounds,
          attribution: current.release.attribution,
        });
      }
      if (!target.getLayer(layerId)) {
        target.addLayer(
          {
            id: layerId,
            type: "raster",
            source: sourceId,
            paint: { "raster-opacity": current.drawnOpacity },
          },
          getFirstSymbolLayer(target)
        );
      }
    },
    [layerId, sourceId]
  );

  const removeLayer = useCallback(
    (target: MapLibreMap) => safeRemoveLayerAndSource(target, [layerId], sourceId),
    [layerId, sourceId]
  );

  useEffect(() => {
    if (!map) return;
    if (!visible) {
      removeLayer(map);
      return;
    }
    const onStyleLoad = () => {
      if (propsRef.current.visible) addLayer(map);
    };
    if (map.isStyleLoaded()) addLayer(map);
    else map.once("style.load", onStyleLoad);
    map.on("style.load", onStyleLoad);
    return () => {
      map.off("style.load", onStyleLoad);
      removeLayer(map);
    };
  }, [map, visible, releaseIdentity, addLayer, removeLayer]);

  useEffect(() => {
    if (!map || !visible || !map.getLayer(layerId)) return;
    map.setPaintProperty(layerId, "raster-opacity", drawnOpacity);
  }, [map, visible, layerId, drawnOpacity]);

  return null;
}
