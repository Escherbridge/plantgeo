"use client";

import { GeoJsonLayer as DeckGeoJsonLayer } from "@deck.gl/layers";
import type { Color } from "@deck.gl/core";
import type { Feature, Geometry } from "geojson";
import { DECK_DEFAULT_PROPS } from "@/lib/map/deck-config";

interface GeoJsonLayerProps {
  id?: string;
  data: Feature<Geometry>[] | string;
  filled?: boolean;
  stroked?: boolean;
  extruded?: boolean;
  getFillColor?: Color | ((f: Feature) => Color);
  getLineColor?: Color | ((f: Feature) => Color);
  getLineWidth?: number | ((f: Feature) => number);
  getElevation?: number | ((f: Feature) => number);
  onHover?: (info: { object?: Feature; x: number; y: number }) => void;
  onClick?: (info: { object?: Feature }) => void;
}

export function createGeoJsonLayer({
  id = "geojson",
  data,
  filled = true,
  stroked = true,
  extruded = false,
  getFillColor = [100, 200, 100, 160],
  getLineColor = [255, 255, 255, 200],
  getLineWidth = 1,
  getElevation = 0,
  onHover,
  onClick,
}: GeoJsonLayerProps) {
  return new DeckGeoJsonLayer<Feature>({
    ...DECK_DEFAULT_PROPS,
    id,
    data: data as string,
    filled,
    stroked,
    extruded,
    getFillColor: getFillColor as Color,
    getLineColor: getLineColor as Color,
    getLineWidth,
    lineWidthMinPixels: 1,
    getElevation,
    onHover,
    onClick,
  });
}
