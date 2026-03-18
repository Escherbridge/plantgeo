import type {
  LayerSpecification,
  FillExtrusionLayerSpecification,
} from "@maplibre/maplibre-gl-style-spec";

const MARTIN_SOURCE = "martin-dynamic";

export function buildings3dLayer(
  baseColor: string,
  accentColor: string
): FillExtrusionLayerSpecification {
  return {
    id: "buildings-3d",
    type: "fill-extrusion",
    source: "protomaps",
    "source-layer": "buildings",
    minzoom: 14,
    paint: {
      "fill-extrusion-height": [
        "coalesce",
        ["get", "render_height"],
        ["get", "height"],
        10,
      ],
      "fill-extrusion-base": [
        "coalesce",
        ["get", "render_min_height"],
        ["get", "min_height"],
        0,
      ],
      "fill-extrusion-color": [
        "interpolate",
        ["linear"],
        ["coalesce", ["get", "render_height"], ["get", "height"], 10],
        0,
        baseColor,
        50,
        accentColor,
        150,
        accentColor,
      ],
      "fill-extrusion-opacity": [
        "interpolate",
        ["linear"],
        ["zoom"],
        14,
        0,
        16,
        0.85,
      ],
    },
  };
}

export const firePerimetersLayer: LayerSpecification = {
  id: "fire-perimeters",
  type: "fill",
  source: MARTIN_SOURCE,
  "source-layer": "fire_risk",
  minzoom: 4,
  paint: {
    "fill-color": [
      "match",
      ["get", "severity"],
      "critical",
      "#dc2626",
      "high",
      "#ea580c",
      "moderate",
      "#f59e0b",
      "#fbbf24",
    ],
    "fill-opacity": 0.5,
  },
};

export const firePerimetersOutlineLayer: LayerSpecification = {
  id: "fire-perimeters-outline",
  type: "line",
  source: MARTIN_SOURCE,
  "source-layer": "fire_risk",
  minzoom: 4,
  paint: {
    "line-color": "#dc2626",
    "line-width": 2,
  },
};

export const sensorsLayer: LayerSpecification = {
  id: "sensors",
  type: "circle",
  source: MARTIN_SOURCE,
  "source-layer": "sensors",
  minzoom: 8,
  paint: {
    "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 3, 16, 8],
    "circle-color": [
      "match",
      ["get", "status"],
      "active",
      "#22c55e",
      "warning",
      "#f59e0b",
      "offline",
      "#ef4444",
      "#6b7280",
    ],
    "circle-stroke-width": 1.5,
    "circle-stroke-color": "#ffffff",
  },
};

export const interventionsLayer: LayerSpecification = {
  id: "interventions",
  type: "fill",
  source: MARTIN_SOURCE,
  "source-layer": "interventions",
  minzoom: 6,
  paint: {
    "fill-color": [
      "match",
      ["get", "intervention_type"],
      "reforestation",
      "#15803d",
      "firebreak",
      "#b45309",
      "wetland",
      "#0369a1",
      "#6d28d9",
    ],
    "fill-opacity": 0.4,
  },
};

export const interventionsOutlineLayer: LayerSpecification = {
  id: "interventions-outline",
  type: "line",
  source: MARTIN_SOURCE,
  "source-layer": "interventions",
  minzoom: 6,
  paint: {
    "line-color": "#4b5563",
    "line-width": 1,
    "line-dasharray": [2, 1],
  },
};

export const roadsLayer: LayerSpecification = {
  id: "osm-roads",
  type: "line",
  source: MARTIN_SOURCE,
  "source-layer": "geo.osm_roads",
  minzoom: 10,
  paint: {
    "line-color": "#94a3b8",
    "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.5, 16, 3],
  },
};

export const waterwaysLayer: LayerSpecification = {
  id: "osm-waterways",
  type: "line",
  source: MARTIN_SOURCE,
  "source-layer": "geo.osm_waterways",
  minzoom: 8,
  paint: {
    "line-color": "#3b82f6",
    "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.5, 16, 3],
  },
};

export function getLayers(): LayerSpecification[] {
  return [
    firePerimetersLayer,
    firePerimetersOutlineLayer,
    sensorsLayer,
    interventionsLayer,
    interventionsOutlineLayer,
    roadsLayer,
    waterwaysLayer,
  ];
}
