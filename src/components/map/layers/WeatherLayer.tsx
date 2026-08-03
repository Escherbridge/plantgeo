"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { safeRemoveLayerAndSource } from "@/lib/map/layer-utils";

export interface WindPoint {
  coordinates: [number, number];
  /** Wind speed in m/s */
  windSpeed: number;
  /** Wind direction in degrees (0 = North, 90 = East) */
  windDirection: number;
  temperature?: number;
  humidity?: number;
}

interface WeatherLayerProps {
  map: MapLibreMap | null;
  data: WindPoint[];
  visible?: boolean;
  layerId?: string;
  sourceId?: string;
}

/**
 * Convert wind direction degrees to a Unicode arrow character.
 * Direction indicates where wind is blowing FROM.
 */
function directionToArrow(degrees: number): string {
  // Wind direction is where wind comes FROM; arrow points where it goes TO
  const arrows = ["↓", "↙", "←", "↖", "↑", "↗", "→", "↘"];
  const index = Math.round(((degrees + 180) % 360) / 45) % 8;
  return arrows[index];
}

/**
 * Wind speed to color: calm (blue) -> moderate (green) -> strong (red)
 */
function windSpeedToColor(speed: number): string {
  if (speed < 5) return "#3b82f6";
  if (speed < 10) return "#22c55e";
  if (speed < 20) return "#f59e0b";
  return "#ef4444";
}

export function WeatherLayer({
  map,
  data,
  visible = true,
  layerId = "weather-wind",
  sourceId = "weather-wind-source",
}: WeatherLayerProps) {
  const geojson = useMemo<GeoJSON.FeatureCollection>(
    () => ({
      type: "FeatureCollection",
      features: data.map((point, i) => ({
        type: "Feature",
        id: i,
        geometry: {
          type: "Point",
          coordinates: point.coordinates,
        },
        properties: {
          arrow: directionToArrow(point.windDirection),
          windSpeed: point.windSpeed,
          windDirection: point.windDirection,
          color: windSpeedToColor(point.windSpeed),
          temperature: point.temperature ?? null,
          humidity: point.humidity ?? null,
          label: `${directionToArrow(point.windDirection)} ${point.windSpeed.toFixed(1)} m/s`,
        },
      })),
    }),
    [data]
  );

  // Keep latest props in refs so the style.load handler uses current values.
  const propsRef = useRef({ visible, geojson });
  useEffect(() => {
    propsRef.current = { visible, geojson };
  }, [visible, geojson]);

  const addAllLayers = useCallback(
    (m: MapLibreMap) => {
      if (m.getSource(sourceId)) {
        (m.getSource(sourceId) as maplibregl.GeoJSONSource).setData(
          propsRef.current.geojson
        );
      } else {
        m.addSource(sourceId, { type: "geojson", data: propsRef.current.geojson });
      }

      if (!m.getLayer(layerId)) {
        m.addLayer({
          id: layerId,
          type: "symbol",
          source: sourceId,
          layout: {
            "text-field": ["get", "label"],
            "text-font": ["Noto Sans Regular"],
            "text-size": 12,
            "text-anchor": "center",
            "text-allow-overlap": false,
            "text-ignore-placement": false,
          },
          paint: {
            "text-color": ["get", "color"],
            "text-halo-color": "rgba(0,0,0,0.6)",
            "text-halo-width": 1,
          },
        });
      }
    },
    [layerId, sourceId]
  );

  const removeAllLayers = useCallback(
    (m: MapLibreMap) => {
      safeRemoveLayerAndSource(m, [layerId], sourceId);
    },
    [layerId, sourceId]
  );

  // Add/remove and re-add across style swaps, which wipe custom layers.
  // Only `visible` may remove the layer -- an empty feed renders an empty
  // source so a style swap can never be mistaken for the toggle being off.
  useEffect(() => {
    if (!map) return;

    if (!visible) {
      removeAllLayers(map);
      return;
    }

    const onStyleLoad = () => {
      if (!propsRef.current.visible) return;
      addAllLayers(map);
    };

    if (map.isStyleLoaded()) addAllLayers(map);
    map.on("style.load", onStyleLoad);

    return () => {
      map.off("style.load", onStyleLoad);
      removeAllLayers(map);
    };
  }, [map, visible, addAllLayers, removeAllLayers]);

  // Push new observations into the existing source without a remount cycle.
  useEffect(() => {
    if (!map || !visible) return;
    const source = map.getSource(sourceId);
    if (source && "setData" in source) {
      (source as maplibregl.GeoJSONSource).setData(geojson);
    }
  }, [map, visible, geojson, sourceId]);

  return null;
}
