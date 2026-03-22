"use client";

import React, { useEffect, useRef } from "react";
import { useMap } from "@/lib/map/map-context";
import { useDrawingStore } from "@/stores/drawing-store";

const SOURCE_ID = "drawing-annotations";
const FILL_LAYER_ID = "drawing-fill";
const LINE_LAYER_ID = "drawing-line";
const CIRCLE_LAYER_ID = "drawing-circle";
const SYMBOL_LAYER_ID = "drawing-labels";

export function AnnotationLayer() {
  const map = useMap();
  const features = useDrawingStore((s) => s.features);
  const initializedRef = useRef(false);

  useEffect(() => {
    if (!map) return;

    const init = () => {
      if (initializedRef.current) return;
      initializedRef.current = true;

      map.addSource(SOURCE_ID, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });

      map.addLayer({
        id: FILL_LAYER_ID,
        type: "fill",
        source: SOURCE_ID,
        filter: ["==", "$type", "Polygon"],
        paint: {
          "fill-color": "#3b82f6",
          "fill-opacity": 0.2,
        },
      });

      map.addLayer({
        id: LINE_LAYER_ID,
        type: "line",
        source: SOURCE_ID,
        filter: ["any", ["==", "$type", "LineString"], ["==", "$type", "Polygon"]],
        paint: {
          "line-color": "#3b82f6",
          "line-width": 2,
          "line-dasharray": [1, 0],
        },
      });

      map.addLayer({
        id: CIRCLE_LAYER_ID,
        type: "circle",
        source: SOURCE_ID,
        filter: ["==", "$type", "Point"],
        paint: {
          "circle-radius": 6,
          "circle-color": "#3b82f6",
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 2,
        },
      });

      map.addLayer({
        id: SYMBOL_LAYER_ID,
        type: "symbol",
        source: SOURCE_ID,
        filter: ["all", ["==", "$type", "Point"], ["has", "label"]],
        layout: {
          "text-field": ["get", "label"],
          "text-size": 13,
          "text-offset": [0, 1.5],
          "text-anchor": "top",
          "text-font": ["Open Sans Regular"],
        },
        paint: {
          "text-color": "#ffffff",
          "text-halo-color": "#000000",
          "text-halo-width": 1,
        },
      });
    };

    if (map.loaded()) {
      init();
    } else {
      map.once("load", init);
    }

    return () => {
      if (!initializedRef.current) return;
      try {
        if (map.getLayer(SYMBOL_LAYER_ID)) map.removeLayer(SYMBOL_LAYER_ID);
        if (map.getLayer(CIRCLE_LAYER_ID)) map.removeLayer(CIRCLE_LAYER_ID);
        if (map.getLayer(LINE_LAYER_ID)) map.removeLayer(LINE_LAYER_ID);
        if (map.getLayer(FILL_LAYER_ID)) map.removeLayer(FILL_LAYER_ID);
        if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID);
      } catch {
        // map may already be removed
      }
      initializedRef.current = false;
    };
  }, [map]);

  useEffect(() => {
    if (!map || !initializedRef.current) return;
    const src = map.getSource(SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
    if (src) {
      src.setData(features);
    }
  }, [map, features]);

  return null;
}
