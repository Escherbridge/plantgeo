"use client";

import { useEffect, useRef } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";

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
  layerId = "weather-wind",
  sourceId = "weather-wind-source",
}: WeatherLayerProps) {
  const addedRef = useRef(false);

  useEffect(() => {
    if (!map || data.length === 0) return;

    const geojson: GeoJSON.FeatureCollection = {
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
    };

    function addLayers() {
      if (map!.getSource(sourceId)) {
        (map!.getSource(sourceId) as maplibregl.GeoJSONSource).setData(geojson);
        return;
      }

      map!.addSource(sourceId, {
        type: "geojson",
        data: geojson,
      });

      map!.addLayer({
        id: layerId,
        type: "symbol",
        source: sourceId,
        layout: {
          "text-field": ["get", "label"],
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

      addedRef.current = true;
    }

    if (map.isStyleLoaded()) {
      addLayers();
    } else {
      map.once("styledata", addLayers);
    }

    return () => {
      if (!map || !map.isStyleLoaded()) return;
      if (map.getLayer(layerId)) map.removeLayer(layerId);
      if (map.getSource(sourceId)) map.removeSource(sourceId);
      addedRef.current = false;
    };
  }, [map, data, layerId, sourceId]);

  return null;
}
