"use client";

import { GeoJsonLayer } from "@deck.gl/layers";
import type { Color } from "@deck.gl/core";
import type { Feature, Geometry } from "geojson";
import { DECK_DEFAULT_PROPS } from "@/lib/map/deck-config";

export interface PriorityZone {
  id: string;
  strategyType: string;
  requestCount: number;
  totalVotes: number;
  centroidLat: number | null;
  centroidLon: number | null;
  geojson: unknown;
  computedAt: Date | null;
}

interface PriorityZoneLayerProps {
  id?: string;
  data: PriorityZone[];
  onHover?: (info: { object?: PriorityZoneFeature; x: number; y: number }) => void;
  onClick?: (info: { object?: PriorityZoneFeature }) => void;
}

interface PriorityZoneProperties {
  id: string;
  strategyType: string;
  requestCount: number;
  totalVotes: number;
}

type PriorityZoneFeature = Feature<Geometry, PriorityZoneProperties>;

// Fill colors at 0.3 opacity (alpha ~77)
const STRATEGY_FILL_COLORS: Record<string, Color> = {
  keyline: [33, 150, 243, 77],
  silvopasture: [76, 175, 80, 77],
  reforestation: [139, 195, 74, 77],
  biochar: [121, 85, 72, 77],
  water_harvesting: [0, 188, 212, 77],
  cover_cropping: [255, 152, 0, 77],
};

// Stroke colors at full opacity
const STRATEGY_LINE_COLORS: Record<string, Color> = {
  keyline: [33, 150, 243, 255],
  silvopasture: [76, 175, 80, 255],
  reforestation: [139, 195, 74, 255],
  biochar: [121, 85, 72, 255],
  water_harvesting: [0, 188, 212, 255],
  cover_cropping: [255, 152, 0, 255],
};

const DEFAULT_FILL: Color = [156, 163, 175, 77];
const DEFAULT_LINE: Color = [156, 163, 175, 255];

function toGeoJsonFeatures(zones: PriorityZone[]): PriorityZoneFeature[] {
  return zones
    .filter((z) => z.geojson != null)
    .map((z) => ({
      type: "Feature" as const,
      geometry: z.geojson as Geometry,
      properties: {
        id: z.id,
        strategyType: z.strategyType,
        requestCount: z.requestCount,
        totalVotes: z.totalVotes,
      },
    }));
}

export function createPriorityZoneLayer({
  id = "priority-zones",
  data,
  onHover,
  onClick,
}: PriorityZoneLayerProps) {
  const features = toGeoJsonFeatures(data);

  return new GeoJsonLayer<PriorityZoneProperties>({
    ...DECK_DEFAULT_PROPS,
    id,
    data: { type: "FeatureCollection", features } as unknown as string,
    filled: true,
    stroked: true,
    getFillColor: (f) => {
      const strategyType = (f as PriorityZoneFeature).properties?.strategyType ?? "";
      return STRATEGY_FILL_COLORS[strategyType] ?? DEFAULT_FILL;
    },
    getLineColor: (f) => {
      const strategyType = (f as PriorityZoneFeature).properties?.strategyType ?? "";
      return STRATEGY_LINE_COLORS[strategyType] ?? DEFAULT_LINE;
    },
    getLineWidth: 2,
    lineWidthUnits: "pixels",
    lineWidthMinPixels: 1,
    pickable: true,
    onHover,
    onClick,
  });
}
