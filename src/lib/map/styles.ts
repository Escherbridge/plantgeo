import type { StyleSpecification } from "maplibre-gl";
import type { MapStyle } from "@/types/map";
import { terrainSource, pmtilesSource } from "./sources";
import { buildings3dLayer } from "./layers";

export type { MapStyle };

const hillshadeLayer = {
  id: "hillshade",
  type: "hillshade" as const,
  source: "terrain-dem",
  paint: {
    "hillshade-exaggeration": 0.3,
    "hillshade-shadow-color": "#000000",
    "hillshade-highlight-color": "#ffffff",
  },
};

export const skyThemes: Record<
  MapStyle,
  {
    "sky-color": string;
    "sky-horizon-blend": number;
    "horizon-color": string;
    "horizon-fog-blend": number;
    "fog-color": string;
    "fog-ground-blend": number;
  }
> = {
  dark: {
    "sky-color": "#1a1a2e",
    "sky-horizon-blend": 0.5,
    "horizon-color": "#16213e",
    "horizon-fog-blend": 0.5,
    "fog-color": "#0f3460",
    "fog-ground-blend": 0.5,
  },
  light: {
    "sky-color": "#87CEEB",
    "sky-horizon-blend": 0.4,
    "horizon-color": "#B0E0E6",
    "horizon-fog-blend": 0.3,
    "fog-color": "#E0F0FF",
    "fog-ground-blend": 0.3,
  },
  satellite: {
    "sky-color": "#4A90D9",
    "sky-horizon-blend": 0.5,
    "horizon-color": "#5BC0EB",
    "horizon-fog-blend": 0.4,
    "fog-color": "#87CEEB",
    "fog-ground-blend": 0.4,
  },
};

export const darkStyle: StyleSpecification = {
  version: 8,
  glyphs: "https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf",
  sprite: "https://protomaps.github.io/basemaps-assets/sprites/v4/dark",
  sources: {
    protomaps: pmtilesSource,
    "terrain-dem": terrainSource,
  },
  terrain: { source: "terrain-dem", exaggeration: 1.5 },
  layers: [
    {
      id: "background",
      type: "background",
      paint: { "background-color": "#1a1a2e" },
    },
    {
      id: "earth",
      type: "fill",
      source: "protomaps",
      "source-layer": "earth",
      paint: { "fill-color": "#1e1e30" },
    },
    {
      id: "water",
      type: "fill",
      source: "protomaps",
      "source-layer": "water",
      paint: { "fill-color": "#0f3460" },
    },
    {
      id: "landuse-park",
      type: "fill",
      source: "protomaps",
      "source-layer": "landuse",
      filter: ["in", "pmap:kind", "park", "nature_reserve", "forest"],
      paint: { "fill-color": "#1a2e1a", "fill-opacity": 0.6 },
    },
    {
      id: "roads-highway",
      type: "line",
      source: "protomaps",
      "source-layer": "roads",
      filter: ["==", "pmap:kind", "highway"],
      paint: {
        "line-color": "#3a3a5c",
        "line-width": ["interpolate", ["linear"], ["zoom"], 6, 0.5, 14, 4],
      },
    },
    {
      id: "roads-major",
      type: "line",
      source: "protomaps",
      "source-layer": "roads",
      filter: ["in", "pmap:kind", "major_road", "medium_road"],
      paint: {
        "line-color": "#2a2a4c",
        "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.3, 14, 2.5],
      },
    },
    {
      id: "roads-minor",
      type: "line",
      source: "protomaps",
      "source-layer": "roads",
      minzoom: 12,
      filter: ["in", "pmap:kind", "minor_road", "other"],
      paint: {
        "line-color": "#252540",
        "line-width": ["interpolate", ["linear"], ["zoom"], 12, 0.3, 16, 1.5],
      },
    },
    {
      id: "boundaries",
      type: "line",
      source: "protomaps",
      "source-layer": "boundaries",
      paint: { "line-color": "#3a3a5c", "line-width": 1, "line-dasharray": [3, 2] },
    },
    hillshadeLayer,
    buildings3dLayer("#334155", "#10b981"),
    {
      id: "places-label",
      type: "symbol",
      source: "protomaps",
      "source-layer": "places",
      filter: ["<=", "pmap:min_zoom", 12],
      layout: {
        "text-field": "{name}",
        "text-font": ["Noto Sans Regular"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 4, 10, 10, 14],
      },
      paint: {
        "text-color": "#8888aa",
        "text-halo-color": "#1a1a2e",
        "text-halo-width": 1.5,
      },
    },
  ],
};

export const lightStyle: StyleSpecification = {
  version: 8,
  glyphs: "https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf",
  sprite: "https://protomaps.github.io/basemaps-assets/sprites/v4/light",
  sources: {
    protomaps: pmtilesSource,
    "terrain-dem": terrainSource,
  },
  terrain: { source: "terrain-dem", exaggeration: 1.5 },
  layers: [
    {
      id: "background",
      type: "background",
      paint: { "background-color": "#f0f0f0" },
    },
    {
      id: "earth",
      type: "fill",
      source: "protomaps",
      "source-layer": "earth",
      paint: { "fill-color": "#e8e8e8" },
    },
    {
      id: "water",
      type: "fill",
      source: "protomaps",
      "source-layer": "water",
      paint: { "fill-color": "#a0c4e8" },
    },
    {
      id: "landuse-park",
      type: "fill",
      source: "protomaps",
      "source-layer": "landuse",
      filter: ["in", "pmap:kind", "park", "nature_reserve", "forest"],
      paint: { "fill-color": "#c8e6c8", "fill-opacity": 0.6 },
    },
    {
      id: "roads-highway",
      type: "line",
      source: "protomaps",
      "source-layer": "roads",
      filter: ["==", "pmap:kind", "highway"],
      paint: {
        "line-color": "#e0a040",
        "line-width": ["interpolate", ["linear"], ["zoom"], 6, 0.5, 14, 4],
      },
    },
    {
      id: "roads-major",
      type: "line",
      source: "protomaps",
      "source-layer": "roads",
      filter: ["in", "pmap:kind", "major_road", "medium_road"],
      paint: {
        "line-color": "#d0d0d0",
        "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.3, 14, 2.5],
      },
    },
    {
      id: "roads-minor",
      type: "line",
      source: "protomaps",
      "source-layer": "roads",
      minzoom: 12,
      filter: ["in", "pmap:kind", "minor_road", "other"],
      paint: {
        "line-color": "#e0e0e0",
        "line-width": ["interpolate", ["linear"], ["zoom"], 12, 0.3, 16, 1.5],
      },
    },
    {
      id: "boundaries",
      type: "line",
      source: "protomaps",
      "source-layer": "boundaries",
      paint: { "line-color": "#c0c0c0", "line-width": 1, "line-dasharray": [3, 2] },
    },
    {
      ...hillshadeLayer,
      paint: {
        ...hillshadeLayer.paint,
        "hillshade-shadow-color": "#888888",
        "hillshade-highlight-color": "#ffffff",
      },
    },
    buildings3dLayer("#94a3b8", "#059669"),
    {
      id: "places-label",
      type: "symbol",
      source: "protomaps",
      "source-layer": "places",
      filter: ["<=", "pmap:min_zoom", 12],
      layout: {
        "text-field": "{name}",
        "text-font": ["Noto Sans Regular"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 4, 10, 10, 14],
      },
      paint: {
        "text-color": "#444444",
        "text-halo-color": "#ffffff",
        "text-halo-width": 1.5,
      },
    },
  ],
};

export const satelliteStyle: StyleSpecification = {
  version: 8,
  glyphs: "https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf",
  sources: {
    satellite: {
      type: "raster",
      tiles: [
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      ],
      tileSize: 256,
      attribution: "&copy; Esri, Maxar, Earthstar Geographics",
      maxzoom: 19,
    },
    "terrain-dem": terrainSource,
    protomaps: pmtilesSource,
  },
  terrain: { source: "terrain-dem", exaggeration: 1.5 },
  layers: [
    {
      id: "satellite-base",
      type: "raster",
      source: "satellite",
      minzoom: 0,
      maxzoom: 19,
    },
    hillshadeLayer,
    buildings3dLayer("#64748b", "#06b6d4"),
    {
      id: "places-label",
      type: "symbol",
      source: "protomaps",
      "source-layer": "places",
      filter: ["<=", "pmap:min_zoom", 12],
      layout: {
        "text-field": "{name}",
        "text-font": ["Noto Sans Regular"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 4, 10, 10, 14],
      },
      paint: {
        "text-color": "#ffffff",
        "text-halo-color": "#000000",
        "text-halo-width": 1.5,
      },
    },
  ],
};

export const styles: Record<MapStyle, StyleSpecification> = {
  dark: darkStyle,
  light: lightStyle,
  satellite: satelliteStyle,
};

export function getStyle(name: MapStyle): StyleSpecification {
  return styles[name];
}
