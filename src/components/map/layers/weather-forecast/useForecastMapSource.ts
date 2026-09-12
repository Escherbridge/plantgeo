"use client";

import { useEffect } from "react";
import type { GeoJSONSource, LayerSpecification, Map as MapLibreMap } from "maplibre-gl";
import { safeRemoveLayerAndSource } from "@/lib/map/layer-utils";

export function useForecastMapSource(map: MapLibreMap | null, sourceId: string,
  layer: LayerSpecification, data: GeoJSON.FeatureCollection<GeoJSON.Point>) {
  useEffect(() => {
    if (!map) return;
    const paint = () => {
      if (!map.isStyleLoaded()) return;
      const source = map.getSource(sourceId) as GeoJSONSource | undefined;
      if (source) source.setData(data);
      else map.addSource(sourceId, { type: "geojson", data });
      if (!map.getLayer(layer.id)) map.addLayer(layer);
    };
    paint();
    map.on("style.load", paint);
    return () => {
      map.off("style.load", paint);
      safeRemoveLayerAndSource(map, [layer.id], sourceId);
    };
  }, [map, sourceId, layer, data]);
}
