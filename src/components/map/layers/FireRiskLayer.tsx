"use client";

import { ScatterplotLayer } from "@deck.gl/layers";
import type { Color } from "@deck.gl/core";
import { DECK_DEFAULT_PROPS } from "@/lib/map/deck-config";

export interface FireRiskPoint {
  coordinates: [number, number];
  /** Risk score 0-100 */
  risk: number;
  /** Optional fire radiative power */
  frp?: number;
  confidence?: string;
}

interface FireRiskLayerProps {
  id?: string;
  data: FireRiskPoint[];
  radiusPixels?: number;
  onHover?: (info: { object?: FireRiskPoint; x: number; y: number }) => void;
  onClick?: (info: { object?: FireRiskPoint }) => void;
}

/**
 * Map risk score (0-100) to red-orange-yellow color ramp.
 * High risk (100) = [255, 0, 0], Low risk (0) = [255, 200, 0]
 */
function riskToColor(risk: number): Color {
  const t = Math.min(1, Math.max(0, risk / 100));
  // Interpolate from yellow [255,200,0] to red [255,0,0]
  const green = Math.round(200 * (1 - t));
  const alpha = Math.round(120 + t * 120);
  return [255, green, 0, alpha];
}

export function createFireRiskLayer({
  id = "fire-risk",
  data,
  radiusPixels = 20,
  onHover,
  onClick,
}: FireRiskLayerProps) {
  return new ScatterplotLayer<FireRiskPoint>({
    ...DECK_DEFAULT_PROPS,
    id,
    data,
    getPosition: (d) => d.coordinates,
    getRadius: (d) => radiusPixels * (1 + (d.frp ?? 0) / 500),
    radiusUnits: "pixels",
    getFillColor: (d) => riskToColor(d.risk),
    stroked: true,
    getLineColor: [255, 60, 0, 200],
    getLineWidth: 1,
    lineWidthUnits: "pixels",
    opacity: 0.8,
    pickable: true,
    onHover,
    onClick,
  });
}
