"use client";

import { useCallback, useEffect, useRef } from "react";
import type { Map as MapLibreMap, GeoJSONSource } from "maplibre-gl";
import { safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import type { MapQueryPoint } from "@/stores/map-store";

/**
 * The pin for the place a panel is querying. Drawn as a GeoJSON source rather than a
 * `maplibregl.Marker` so it follows the same attach/re-attach-on-style.load discipline every
 * other layer here does, and so it survives a basemap swap.
 */
const QUERY_POINT_SOURCE_ID = "map-query-point";
const QUERY_POINT_HALO_LAYER_ID = "map-query-point-halo";
const QUERY_POINT_CORE_LAYER_ID = "map-query-point-core";

const EMPTY_COLLECTION: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

interface QueryPointLayerProps {
  map: MapLibreMap | null;
  /** The picked place, or null when nothing is picked. */
  point: MapQueryPoint | null;
}

/** One point feature, or an empty collection when nothing is picked. */
function pointCollection(point: MapQueryPoint | null): GeoJSON.FeatureCollection {
  if (point === null) return EMPTY_COLLECTION;
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: { type: "Point", coordinates: [point.lon, point.lat] },
        properties: {},
      },
    ],
  };
}

export function QueryPointLayer({ map, point }: QueryPointLayerProps) {
  const pointRef = useRef(point);
  pointRef.current = point;

  const addLayers = useCallback((mapInstance: MapLibreMap) => {
    if (!mapInstance.getSource(QUERY_POINT_SOURCE_ID)) {
      mapInstance.addSource(QUERY_POINT_SOURCE_ID, {
        type: "geojson",
        data: pointCollection(pointRef.current),
      });
    }
    // No `beforeId`: the pin marks where the user clicked and must sit above every data
    // layer, including the labels other layers deliberately draw under.
    if (!mapInstance.getLayer(QUERY_POINT_HALO_LAYER_ID)) {
      mapInstance.addLayer({
        id: QUERY_POINT_HALO_LAYER_ID,
        type: "circle",
        source: QUERY_POINT_SOURCE_ID,
        paint: {
          "circle-radius": 12,
          "circle-color": "#f59e0b",
          "circle-opacity": 0.25,
          "circle-stroke-width": 1,
          "circle-stroke-color": "#f59e0b",
        },
      });
    }
    if (!mapInstance.getLayer(QUERY_POINT_CORE_LAYER_ID)) {
      mapInstance.addLayer({
        id: QUERY_POINT_CORE_LAYER_ID,
        type: "circle",
        source: QUERY_POINT_SOURCE_ID,
        paint: {
          "circle-radius": 4,
          "circle-color": "#b45309",
          "circle-stroke-width": 1.5,
          "circle-stroke-color": "#ffffff",
        },
      });
    }
  }, []);

  const removeLayers = useCallback((mapInstance: MapLibreMap) => {
    safeRemoveLayerAndSource(
      mapInstance,
      [QUERY_POINT_CORE_LAYER_ID, QUERY_POINT_HALO_LAYER_ID],
      QUERY_POINT_SOURCE_ID
    );
  }, []);

  useEffect(() => {
    if (!map) return;
    const onStyleLoad = () => addLayers(map);
    if (map.isStyleLoaded()) addLayers(map);
    map.on("style.load", onStyleLoad);
    return () => {
      map.off("style.load", onStyleLoad);
      removeLayers(map);
    };
  }, [map, addLayers, removeLayers]);

  useEffect(() => {
    if (!map) return;
    const source = map.getSource(QUERY_POINT_SOURCE_ID) as GeoJSONSource | undefined;
    if (source) source.setData(pointCollection(point));
  }, [map, point]);

  return null;
}
