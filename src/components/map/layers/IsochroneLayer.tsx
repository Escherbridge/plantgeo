"use client";

import { useEffect } from "react";
import { useMap } from "@/lib/map/map-context";
import type { GeoJSONSource } from "maplibre-gl";

const SOURCE_ID = "isochrone-source";
const FILL_LAYER_ID = "isochrone-fill";
const OUTLINE_LAYER_ID = "isochrone-outline";

const EMERALD_BANDS = [
  "#d1fae5",
  "#a7f3d0",
  "#6ee7b7",
  "#34d399",
  "#10b981",
  "#059669",
];

const EMPTY_GEOJSON: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

interface IsochroneLayerProps {
  data: GeoJSON.FeatureCollection | null;
}

export function IsochroneLayer({ data }: IsochroneLayerProps) {
  const map = useMap();

  useEffect(() => {
    if (!map) return;

    const setup = () => {
      if (!map.getSource(SOURCE_ID)) {
        map.addSource(SOURCE_ID, {
          type: "geojson",
          data: EMPTY_GEOJSON,
        });
      }

      if (!map.getLayer(FILL_LAYER_ID)) {
        map.addLayer({
          id: FILL_LAYER_ID,
          type: "fill",
          source: SOURCE_ID,
          paint: {
            "fill-color": [
              "case",
              ...EMERALD_BANDS.flatMap((color, i) => [
                ["==", ["get", "contour"], i],
                color,
              ]),
              EMERALD_BANDS[0],
            ] as unknown as string,
            "fill-opacity": 0.35,
          },
        });
      }

      if (!map.getLayer(OUTLINE_LAYER_ID)) {
        map.addLayer({
          id: OUTLINE_LAYER_ID,
          type: "line",
          source: SOURCE_ID,
          paint: {
            "line-color": "#059669",
            "line-width": 1.5,
            "line-opacity": 0.8,
          },
        });
      }
    };

    if (map.isStyleLoaded()) {
      setup();
    } else {
      map.once("style.load", setup);
    }

    return () => {
      if (map.getLayer(OUTLINE_LAYER_ID)) map.removeLayer(OUTLINE_LAYER_ID);
      if (map.getLayer(FILL_LAYER_ID)) map.removeLayer(FILL_LAYER_ID);
      if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID);
    };
  }, [map]);

  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;
    const source = map.getSource(SOURCE_ID) as GeoJSONSource | undefined;
    if (!source) return;

    if (data) {
      const tagged: GeoJSON.FeatureCollection = {
        type: "FeatureCollection",
        features: data.features.map((f, i) => ({
          ...f,
          properties: { ...f.properties, contour: i },
        })),
      };
      source.setData(tagged);
    } else {
      source.setData(EMPTY_GEOJSON);
    }
  }, [map, data]);

  return null;
}

export default IsochroneLayer;
