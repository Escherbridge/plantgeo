"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import { supportCellPolygon, type AggregateEnvelopeSupport } from "@/lib/map/layer-render-contract";

/** A published sample or declared aggregate; each drawn signal remains independently nullable. */
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
  /** Precipitation accumulated for the source reading, in millimetres. */
  precipitation: number | null;
  observedAt?: string | null;
  observedDay?: string;
  support?: AggregateEnvelopeSupport;
  sampleKind?: "model_estimate";
}

interface WeatherLayerProps {
  map: MapLibreMap | null;
  data: WeatherPoint[];
  visible?: boolean;
  /** The wind arrows. */
  layerId?: string;
  /** The detail temperature dots; the cell fill appends `-cells` to this id. */
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
export function directionToArrow(degrees: number): string {
  // Wind direction is where wind comes FROM; arrow points where it goes TO
  const arrows = ["↓", "↙", "←", "↖", "↑", "↗", "→", "↘"];
  const index = Math.round(((degrees % 360 + 360) % 360) / 45) % 8;
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

/** Preserve declared cell support; raw samples never acquire an invented footprint. */
export function weatherFeatures(data: WeatherPoint[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: data.flatMap((point, index): GeoJSON.Feature[] => {
      const polygon = point.support?.supportKind === "aggregate_cell"
        ? supportCellPolygon(...point.coordinates, point.support) : null;
      const hasWind = point.windSpeed !== null && point.windDirection !== null;
      const arrow = hasWind ? directionToArrow(point.windDirection as number) : "";
      const properties = {
        hasWind, hasTemperature: point.temperature !== null, hasCell: polygon !== null,
        arrow, windSpeed: point.windSpeed, windDirection: point.windDirection,
        color: hasWind ? windSpeedToColor(point.windSpeed as number) : "transparent",
        temperature: point.temperature, humidity: point.humidity,
        precipitation: point.precipitation,
        observedAt: point.observedAt ?? null, observedDay: point.observedDay ?? null,
        supportKind: point.support?.supportKind ?? "raw_point",
        sampleKind: point.sampleKind ?? null,
        temperatureLabel: point.temperature === null ? "" : `${Math.round(point.temperature)}°`,
        label: hasWind ? `${arrow} ${(point.windSpeed as number).toFixed(1)} m/s` : "",
      };
      const coordinates: [number, number] = polygon
        ? [(polygon.coordinates[0][0][0] + polygon.coordinates[0][2][0]) / 2,
          (polygon.coordinates[0][0][1] + polygon.coordinates[0][2][1]) / 2]
        : point.coordinates;
      const marker: GeoJSON.Feature = {
        type: "Feature", id: `${index}-point`, geometry: { type: "Point", coordinates }, properties,
      };
      return polygon ? [marker, { type: "Feature", id: `${index}-cell`, geometry: polygon, properties }] : [marker];
    }),
  };
}

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
    () => weatherFeatures(data),
    [data]
  );
  const cellLayerId = `${temperatureLayerId}-cells`;
  const temperatureLabelLayerId = `${temperatureLayerId}-labels`;

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

      if (!m.getLayer(cellLayerId)) {
        m.addLayer({
          id: cellLayerId, type: "fill", source: sourceId,
          filter: ["all", ["==", ["geometry-type"], "Polygon"], ["==", ["get", "hasTemperature"], true]],
          paint: {
            "fill-color": ["interpolate", ["linear"], ["get", "temperature"],
              ...TEMPERATURE_COLOR_STOPS.flatMap((stop) => [stop.celsius, stop.color])],
            "fill-opacity": 0.65 * propsRef.current.opacityScale,
            "fill-antialias": false,
          },
        });
      }
      // Added before the arrows so the dots sit under them: MapLibre appends, and an arrow
      // drawn beneath its own station's dot would be unreadable.
      if (!m.getLayer(temperatureLayerId)) {
        m.addLayer({
          id: temperatureLayerId,
          type: "circle",
          source: sourceId,
          filter: ["all", ["==", ["geometry-type"], "Point"], ["==", ["get", "hasTemperature"], true], ["==", ["get", "hasCell"], false]],
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

      if (!m.getLayer(temperatureLabelLayerId)) {
        m.addLayer({
          id: temperatureLabelLayerId,
          type: "symbol",
          source: sourceId,
          filter: [
            "all",
            ["==", ["geometry-type"], "Point"],
            ["==", ["get", "hasTemperature"], true],
          ],
          layout: {
            "text-field": ["get", "temperatureLabel"],
            "text-font": ["Noto Sans Regular"],
            "text-size": ["interpolate", ["linear"], ["zoom"], 4, 10, 10, 13, 14, 15],
            "text-offset": [0, -0.6],
            "text-anchor": "bottom",
            "text-allow-overlap": false,
            "text-ignore-placement": false,
          },
          paint: {
            "text-color": "#ffffff",
            "text-halo-color": "rgba(0,0,0,0.8)",
            "text-halo-width": 1.25,
            "text-opacity": propsRef.current.opacityScale,
          },
        });
      }

      if (!m.getLayer(layerId)) {
        m.addLayer({
          id: layerId,
          type: "symbol",
          source: sourceId,
          filter: ["all", ["==", ["geometry-type"], "Point"], ["==", ["get", "hasWind"], true]],
          layout: {
            "text-field": ["get", "label"],
            "text-font": ["Noto Sans Regular"],
            "text-size": 12,
            "text-offset": [0, 0.55],
            "text-anchor": "top",
            "text-rotation-alignment": "map",
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
    [layerId, temperatureLayerId, cellLayerId, temperatureLabelLayerId, sourceId]
  );

  const removeAllLayers = useCallback(
    (m: MapLibreMap) => {
      safeRemoveLayerAndSource(
        m,
        [layerId, temperatureLabelLayerId, temperatureLayerId, cellLayerId],
        sourceId
      );
    },
    [layerId, temperatureLayerId, cellLayerId, temperatureLabelLayerId, sourceId]
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
    if (map.getLayer(temperatureLabelLayerId)) {
      map.setPaintProperty(temperatureLabelLayerId, "text-opacity", opacityScale);
    }
    if (map.getLayer(cellLayerId)) map.setPaintProperty(cellLayerId, "fill-opacity", 0.65 * opacityScale);
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
  }, [
    map,
    visible,
    layerId,
    temperatureLayerId,
    cellLayerId,
    temperatureLabelLayerId,
    opacityScale,
  ]);

  return null;
}
