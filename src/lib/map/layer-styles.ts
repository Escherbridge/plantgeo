import type { Map } from "maplibre-gl";

export interface LayerStyle {
  fillColor?: string;
  strokeColor?: string;
  strokeWidth?: number;
  opacity?: number;
}

export const stylePresets: Record<string, LayerStyle> = {
  "fire-red": { fillColor: "#ef4444", strokeColor: "#dc2626", strokeWidth: 2, opacity: 0.8 },
  "eco-green": { fillColor: "#22c55e", strokeColor: "#16a34a", strokeWidth: 1, opacity: 0.75 },
  "water-blue": { fillColor: "#3b82f6", strokeColor: "#2563eb", strokeWidth: 1, opacity: 0.7 },
  "neutral-gray": { fillColor: "#6b7280", strokeColor: "#4b5563", strokeWidth: 1, opacity: 0.6 },
};

export function convertStyle(style: LayerStyle): Record<string, unknown> {
  return {
    "fill-color": style.fillColor ?? "#6b7280",
    "fill-opacity": style.opacity ?? 0.7,
    "fill-outline-color": style.strokeColor ?? "#4b5563",
    "line-color": style.strokeColor ?? "#4b5563",
    "line-width": style.strokeWidth ?? 1,
  };
}

export function applyLayerStyle(map: Map, layerId: string, style: LayerStyle) {
  const paint = convertStyle(style);

  if (style.fillColor !== undefined) {
    map.setPaintProperty(layerId, "fill-color", paint["fill-color"]);
  }
  if (style.opacity !== undefined) {
    map.setPaintProperty(layerId, "fill-opacity", paint["fill-opacity"]);
  }
  if (style.strokeColor !== undefined) {
    map.setPaintProperty(layerId, "fill-outline-color", paint["fill-outline-color"]);
  }
  if (style.strokeWidth !== undefined) {
    map.setPaintProperty(layerId, "line-width", paint["line-width"]);
  }
}
