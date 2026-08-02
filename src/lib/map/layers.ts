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

// Layers below are hidden by default and revealed via the "fire-perimeters"/
// "interventions"/"building-footprints" activeLayers toggles (see
// STYLE_LAYER_TOGGLE_MAP, synced in LayerManager).
export const firePerimetersLayer: LayerSpecification = {
  id: "fire-perimeters",
  type: "fill",
  source: MARTIN_SOURCE,
  "source-layer": "fire_risk",
  minzoom: 4,
  layout: { visibility: "none" },
  paint: {
    // "severity" is derived from the WFIGS percentContained field at ingestion
    // (see runFirePerimetersIngestionJob). Perimeters whose containment is not
    // reported render neutral grey rather than borrowing a severity colour.
    "fill-color": [
      "match",
      ["get", "severity"],
      "critical",
      "#dc2626",
      "high",
      "#ea580c",
      "moderate",
      "#f59e0b",
      "low",
      "#fbbf24",
      "#9ca3af",
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
  layout: { visibility: "none" },
  paint: {
    "line-color": "#dc2626",
    "line-width": 2,
  },
};

// The "sensors" style layer was removed: nothing in the platform ever writes to
// the geo.features "sensors" layer, so geo.sensor_tiles() can only ever return
// an empty tile. The Martin function is left published for a future producer;
// the toggle is gone so the UI stops advertising a layer that cannot populate.

export const interventionsLayer: LayerSpecification = {
  id: "interventions",
  type: "fill",
  source: MARTIN_SOURCE,
  "source-layer": "interventions",
  minzoom: 6,
  layout: { visibility: "none" },
  paint: {
    // Styled on "priority" because that is the only classification
    // createIntervention actually persists; "intervention_type" is never
    // written, so colouring by it painted every zone the same fabricated hue.
    "fill-color": [
      "match",
      ["get", "priority"],
      "High",
      "#b45309",
      "Medium",
      "#6d28d9",
      "Low",
      "#0369a1",
      "#9ca3af",
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
  layout: { visibility: "none" },
  paint: {
    "line-color": "#4b5563",
    "line-width": 1,
    "line-dasharray": [2, 1],
  },
};

// Martin-served 3D building footprints from geo.osm_buildings — a separate
// dataset from the always-on protomaps "buildings" basemap layer above.
// "geo.building_tiles" is the Martin *source id*; the MVT layer name emitted by
// ST_AsMVT inside geo.building_tiles() is "buildings" (drizzle/0001:499).
export const buildingFootprintsLayer: FillExtrusionLayerSpecification = {
  id: "building-footprints",
  type: "fill-extrusion",
  source: MARTIN_SOURCE,
  "source-layer": "buildings",
  minzoom: 13,
  layout: { visibility: "none" },
  paint: {
    "fill-extrusion-height": ["coalesce", ["get", "height"], 8],
    "fill-extrusion-base": ["coalesce", ["get", "min_height"], 0],
    "fill-extrusion-color": "#8b5cf6",
    "fill-extrusion-opacity": 0.75,
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
    interventionsLayer,
    interventionsOutlineLayer,
    buildingFootprintsLayer,
    roadsLayer,
    waterwaysLayer,
  ];
}

/**
 * Maps an activeLayers toggle id to the concrete style layer ids it controls.
 * Synced against map-store's activeLayers in LayerManager via setLayoutProperty,
 * since these are static style layers (not React-mounted components).
 */
export const STYLE_LAYER_TOGGLE_MAP: Record<string, string[]> = {
  "fire-perimeters": ["fire-perimeters", "fire-perimeters-outline"],
  interventions: ["interventions", "interventions-outline"],
  "building-footprints": ["building-footprints"],
};
