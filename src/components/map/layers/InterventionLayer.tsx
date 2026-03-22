"use client";

import { GeoJsonLayer } from "@deck.gl/layers";
import type { Color } from "@deck.gl/core";
import type { Feature, Geometry } from "geojson";
import { DECK_DEFAULT_PROPS } from "@/lib/map/deck-config";

interface InterventionProperties {
  strategyId?: string;
  name?: string;
  priority?: "High" | "Medium" | "Low";
  status?: string;
  [key: string]: unknown;
}

interface InterventionLayerProps {
  id?: string;
  /** GeoJSON features from geo.features WHERE layer name = 'interventions' */
  data: Feature<Geometry, InterventionProperties>[];
  onHover?: (info: {
    object?: Feature<Geometry, InterventionProperties>;
    x: number;
    y: number;
  }) => void;
  onClick?: (info: {
    object?: Feature<Geometry, InterventionProperties>;
  }) => void;
}

const PRIORITY_COLORS: Record<string, Color> = {
  High: [220, 38, 38, 180],
  Medium: [234, 88, 12, 160],
  Low: [161, 98, 7, 140],
};

const STRATEGY_COLORS: Record<string, Color> = {
  firebreaks: [239, 68, 68, 160],
  controlled_burns: [249, 115, 22, 160],
  vegetation_management: [132, 204, 22, 160],
  water_sources: [14, 165, 233, 160],
  evacuation_routes: [168, 85, 247, 160],
  monitoring_stations: [99, 102, 241, 160],
};

function getFeatureColor(feature: Feature<Geometry, InterventionProperties>): Color {
  const { strategyId, priority } = feature.properties ?? {};
  if (strategyId && STRATEGY_COLORS[strategyId]) {
    return STRATEGY_COLORS[strategyId];
  }
  if (priority && PRIORITY_COLORS[priority]) {
    return PRIORITY_COLORS[priority];
  }
  return [156, 163, 175, 140];
}

export function createInterventionLayer({
  id = "interventions",
  data,
  onHover,
  onClick,
}: InterventionLayerProps) {
  return new GeoJsonLayer<InterventionProperties>({
    ...DECK_DEFAULT_PROPS,
    id,
    data: data as unknown as string,
    filled: true,
    stroked: true,
    getFillColor: (f) => getFeatureColor(f as Feature<Geometry, InterventionProperties>),
    getLineColor: [255, 255, 255, 200],
    getLineWidth: 2,
    lineWidthMinPixels: 1,
    getPointRadius: 8,
    pointRadiusUnits: "pixels",
    pickable: true,
    onHover,
    onClick,
  });
}
