"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { safeRemoveLayerAndSource } from "@/lib/map/layer-utils";

/**
 * One published observation. Every measurement is nullable because the two drawn layers
 * need different ones: a station with wind but no temperature draws an arrow, a station
 * with temperature but no wind draws a dot, and neither is dropped for lacking the other's
 * field. Humidity is drawn by neither and only ever captions the tooltip.
 */
export interface WeatherPoint {
  coordinates: [number, number];
  /** Wind speed in m/s. */
  windSpeed: number | null;
  /** Wind direction in degrees (0 = North, 90 = East). */
  windDirection: number | null;
  /** Air temperature in °C, as Open-Meteo's `temperature_2m` reports it. */
  temperature: number | null;
  /** Relative humidity, percent. */
  humidity: number | null;
  observedAt?: string | null;
}

interface WeatherLayerProps {
  map: MapLibreMap | null;
  data: WeatherPoint[];
  visible?: boolean;
  /** The wind arrows. */
  layerId?: string;
  /** The temperature dots, drawn under the arrows. */
  temperatureLayerId?: string;
  sourceId?: string;
  /**
   * The reader's MULTIPLIER over both layers' authored strengths. The symbol layer takes it
   * on `text-opacity` only -- it sets `text-field` and never `icon-image`, so `icon-opacity`
   * would be a silent no-op -- and the circle layer on its fill and stroke.
   */
  opacityScale?: number;
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
 * Wind speed classes: calm (blue) -> moderate (green) -> strong (red). Ordered by
 * ascending `below`, with the open top class last, so the lookup below and the legend read
 * the one table.
 */
export const WIND_SPEED_CLASSES = [
  { below: 5, color: "#3b82f6", label: "< 5 m/s — calm" },
  { below: 10, color: "#22c55e", label: "5–10 m/s — light" },
  { below: 20, color: "#f59e0b", label: "10–20 m/s — moderate" },
  { below: null, color: "#ef4444", label: "≥ 20 m/s — strong" },
] as const;

function windSpeedToColor(speed: number): string {
  const matched = WIND_SPEED_CLASSES.find(
    (windClass) => windClass.below === null || speed < windClass.below
  );
  return (matched ?? WIND_SPEED_CLASSES[WIND_SPEED_CLASSES.length - 1]).color;
}

/**
 * The temperature ramp, in °C because that is the unit the feed measures in: `weather.ts`
 * asks Open-Meteo for `temperature_2m` without a `temperature_unit`, whose default is
 * Celsius, and nothing converts it between there and here.
 *
 * Moreland's cool-warm diverging palette rather than a rainbow. Its two arms separate on the
 * blue/red axis, which protanopia and deuteranopia both preserve, and its lightness rises to
 * the neutral middle and falls again, so the ordering survives greyscale and every form of
 * colour blindness on lightness alone. Stops are evenly spaced 10 °C apart, which is what
 * lets the legend's bar double as a value axis (see `LegendRampBlock` in layer-legends.ts).
 */
export const TEMPERATURE_COLOR_STOPS = [
  { celsius: -20, color: "#3b4cc0" },
  { celsius: -10, color: "#6788ee" },
  { celsius: 0, color: "#9abbff" },
  { celsius: 10, color: "#dddcdc" },
  { celsius: 20, color: "#f7b89c" },
  { celsius: 30, color: "#e26952" },
  { celsius: 40, color: "#b40426" },
] as const;

/** The authored strength of the temperature dots' fill; the multiplier scales it. */
const TEMPERATURE_CIRCLE_OPACITY = 0.8;

/** …and of their stroke, which is what keeps two adjacent stations readable as two. */
const TEMPERATURE_STROKE_OPACITY = 0.55;

export function WeatherLayer({
  map,
  data,
  visible = true,
  layerId = "weather-wind",
  temperatureLayerId = "weather-temperature",
  sourceId = "weather-wind-source",
  opacityScale = 1,
}: WeatherLayerProps) {
  const geojson = useMemo<GeoJSON.FeatureCollection>(
    () => ({
      type: "FeatureCollection",
      features: data.map((point, i) => {
        // Which of the two layers may draw this station, decided once and carried as a flag
        // rather than re-derived in a filter expression. Both layers filter on it, which is
        // what keeps a null out of `windSpeedToColor` and out of the temperature
        // `interpolate` -- an unmeasured field is never coalesced to a number the upstream
        // did not report.
        const hasWind = point.windSpeed !== null && point.windDirection !== null;
        const hasTemperature = point.temperature !== null;
        const arrow = hasWind ? directionToArrow(point.windDirection as number) : "";
        return {
          type: "Feature" as const,
          id: i,
          geometry: {
            type: "Point" as const,
            coordinates: point.coordinates,
          },
          properties: {
            hasWind,
            hasTemperature,
            arrow,
            windSpeed: point.windSpeed,
            windDirection: point.windDirection,
            color: hasWind ? windSpeedToColor(point.windSpeed as number) : "transparent",
            temperature: point.temperature,
            humidity: point.humidity,
            observedAt: point.observedAt ?? null,
            label: hasWind
              ? `${arrow} ${(point.windSpeed as number).toFixed(1)} m/s`
              : "",
          },
        };
      }),
    }),
    [data]
  );

  // Keep latest props in refs so the style.load handler uses current values.
  const propsRef = useRef({ visible, geojson, opacityScale });
  useEffect(() => {
    propsRef.current = { visible, geojson, opacityScale };
  }, [visible, geojson, opacityScale]);

  const addAllLayers = useCallback(
    (m: MapLibreMap) => {
      if (m.getSource(sourceId)) {
        (m.getSource(sourceId) as maplibregl.GeoJSONSource).setData(
          propsRef.current.geojson
        );
      } else {
        m.addSource(sourceId, { type: "geojson", data: propsRef.current.geojson });
      }

      // Added before the arrows so the dots sit under them: MapLibre appends, and an arrow
      // drawn beneath its own station's dot would be unreadable.
      if (!m.getLayer(temperatureLayerId)) {
        m.addLayer({
          id: temperatureLayerId,
          type: "circle",
          source: sourceId,
          filter: ["==", ["get", "hasTemperature"], true],
          paint: {
            "circle-color": [
              "interpolate",
              ["linear"],
              ["get", "temperature"],
              ...TEMPERATURE_COLOR_STOPS.flatMap((stop) => [stop.celsius, stop.color]),
            ],
            // Big enough to read a colour off at a regional view, small enough that a dense
            // grid of stations stays a grid rather than a sheet.
            "circle-radius": [
              "interpolate",
              ["linear"],
              ["zoom"],
              4,
              4,
              10,
              9,
              14,
              14,
            ],
            "circle-opacity":
              TEMPERATURE_CIRCLE_OPACITY * propsRef.current.opacityScale,
            "circle-stroke-width": 1,
            "circle-stroke-color": "rgba(0,0,0,0.6)",
            "circle-stroke-opacity":
              TEMPERATURE_STROKE_OPACITY * propsRef.current.opacityScale,
          },
        });
      }

      if (!m.getLayer(layerId)) {
        m.addLayer({
          id: layerId,
          type: "symbol",
          source: sourceId,
          filter: ["==", ["get", "hasWind"], true],
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
            "text-opacity": propsRef.current.opacityScale,
          },
        });
      }
    },
    [layerId, temperatureLayerId, sourceId]
  );

  const removeAllLayers = useCallback(
    (m: MapLibreMap) => {
      safeRemoveLayerAndSource(m, [layerId, temperatureLayerId], sourceId);
    },
    [layerId, temperatureLayerId, sourceId]
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

  // The multiplier, applied without a rebuild. This component is the single writer for both
  // layers -- the `weather` registry entry is `renderKind: "component"` with no
  // `styleLayerIds`, so `LayerManager.applyOpacity` structurally cannot reach either. See
  // src/lib/map/layer-opacity.ts.
  useEffect(() => {
    if (!map || !visible) return;
    try {
      if (!map.getStyle()) return;
    } catch {
      return;
    }
    if (map.getLayer(layerId)) {
      map.setPaintProperty(layerId, "text-opacity", opacityScale);
    }
    if (map.getLayer(temperatureLayerId)) {
      map.setPaintProperty(
        temperatureLayerId,
        "circle-opacity",
        TEMPERATURE_CIRCLE_OPACITY * opacityScale
      );
      map.setPaintProperty(
        temperatureLayerId,
        "circle-stroke-opacity",
        TEMPERATURE_STROKE_OPACITY * opacityScale
      );
    }
  }, [map, visible, layerId, temperatureLayerId, opacityScale]);

  return null;
}
