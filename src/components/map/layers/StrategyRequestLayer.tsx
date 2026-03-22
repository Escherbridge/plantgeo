"use client";

import { ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import type { Color } from "@deck.gl/core";
import { DECK_DEFAULT_PROPS } from "@/lib/map/deck-config";

export interface StrategyRequest {
  id: string;
  strategyType: string;
  title: string;
  description?: string | null;
  lat: number;
  lon: number;
  voteCount: number | null;
  status: string | null;
  createdAt: Date | null;
}

interface StrategyRequestLayerProps {
  id?: string;
  data: StrategyRequest[];
  onHover?: (info: { object?: StrategyRequest; x: number; y: number }) => void;
  onClick?: (info: { object?: StrategyRequest }) => void;
}

// Color by strategy type
const STRATEGY_COLORS: Record<string, Color> = {
  keyline: [33, 150, 243, 220],        // #2196f3 blue
  silvopasture: [76, 175, 80, 220],    // #4caf50 green
  reforestation: [139, 195, 74, 220],  // #8bc34a light green
  biochar: [121, 85, 72, 220],         // #795548 brown
  water_harvesting: [0, 188, 212, 220],// #00bcd4 cyan
  cover_cropping: [255, 152, 0, 220],  // #ff9800 orange
};

const DEFAULT_COLOR: Color = [156, 163, 175, 220];

function getStrategyColor(strategyType: string): Color {
  return STRATEGY_COLORS[strategyType] ?? DEFAULT_COLOR;
}

/**
 * Scale circle radius based on voteCount.
 * Min 6px at 0 votes, max 20px at 50+ votes.
 */
function getRadius(voteCount: number | null): number {
  const votes = voteCount ?? 0;
  return Math.round(6 + Math.min(14, (votes / 50) * 14));
}

export function createStrategyRequestLayer({
  id = "strategy-requests",
  data,
  onHover,
  onClick,
}: StrategyRequestLayerProps) {
  const scatterLayer = new ScatterplotLayer<StrategyRequest>({
    ...DECK_DEFAULT_PROPS,
    id,
    data,
    getPosition: (d) => [d.lon, d.lat],
    getRadius: (d) => getRadius(d.voteCount),
    radiusUnits: "pixels",
    getFillColor: (d) => getStrategyColor(d.strategyType),
    stroked: true,
    getLineColor: [255, 255, 255, 180],
    getLineWidth: 1.5,
    lineWidthUnits: "pixels",
    pickable: true,
    onHover,
    onClick,
  });

  // Vote count label layer
  const labelLayer = new TextLayer<StrategyRequest>({
    id: `${id}-labels`,
    data,
    getPosition: (d) => [d.lon, d.lat],
    getText: (d) => String(d.voteCount ?? 0),
    getSize: 11,
    getColor: [255, 255, 255, 240],
    getTextAnchor: "middle",
    getAlignmentBaseline: "center",
    fontWeight: "bold",
    pickable: false,
  });

  return [scatterLayer, labelLayer];
}
