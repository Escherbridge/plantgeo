import type {
  LayerSpecification,
  FillExtrusionLayerSpecification,
} from "@maplibre/maplibre-gl-style-spec";
import { styleBackedLayerEntries } from "@/lib/map/layer-registry";

const MARTIN_SOURCE = "martin-dynamic";
// Table-backed OSM tiles live in a separate composite: mixing them with the
// function sources makes Martin declare vector_layers, which MapLibre then
// validates against and rejects every function-backed layer. See sources.ts.
const OSM_SOURCE = "martin-osm";

// GeoJSON sources for the tRPC-fed layers at the bottom of this file. Neither is
// declared in styles.ts, because a geojson source has no static URL to declare: the
// component owning the viewport query adds the source and sets its data, exactly as
// DroughtLayer and WaterLayer already do for "drought-monitor" and "water-gauges".
export const WATERSHEDS_SOURCE = "watersheds";
export const SOIL_SURVEY_SOURCE = "soil-survey";

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

// Layers below are hidden by default and revealed via their activeLayers
// toggles (see STYLE_LAYER_TOGGLE_MAP, synced in LayerManager).
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

// "network" is the only sensor property the producer guarantees on every row:
// sensors.py._matches_networks rejects a station before collection unless its
// network matches the configured roster (ASOS/ASOS-HFM/RAWS/NonFedAWOS), so it
// is never optional the way station_name is. The fix to geo.sensor_tiles()'s
// SELECT list is written (drizzle/0010_sensor_tile_properties.sql), but not yet
// applied to production -- prod is migrated only through 0008 -- so until that
// migration runs, the live function still emits sensor_type/status/name, none
// of which any producer populates, and this layer keeps rendering every
// station in the neutral grey fallback. See src/components/map/AGENTS.md.
export const sensorsLayer: LayerSpecification = {
  id: "sensors",
  type: "circle",
  source: MARTIN_SOURCE,
  "source-layer": "sensors",
  minzoom: 4,
  layout: { visibility: "none" },
  paint: {
    "circle-color": [
      "match",
      ["get", "network"],
      "ASOS",
      "#0ea5e9",
      "ASOS-HFM",
      "#0284c7",
      "RAWS",
      "#f59e0b",
      "NonFedAWOS",
      "#22c55e",
      "#9ca3af",
    ],
    "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 2, 8, 4, 12, 6],
    "circle-stroke-width": 1,
    "circle-stroke-color": "#ffffff",
    "circle-opacity": 0.85,
  },
};

// Oregon OEM fire-evacuation areas. "severity" is deterministically derived from
// evacuationLevel at ingestion (evacuation_zones.py EVACUATION_LEVEL_SEVERITIES)
// and reuses fire-perimeters' vocabulary minus "low" -- Oregon's scale has three
// levels, not four.
export const evacuationZonesLayer: LayerSpecification = {
  id: "evacuation-zones",
  type: "fill",
  source: MARTIN_SOURCE,
  "source-layer": "evacuation_zones",
  minzoom: 4,
  layout: { visibility: "none" },
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
      "#9ca3af",
    ],
    "fill-opacity": 0.45,
  },
};

export const evacuationZonesOutlineLayer: LayerSpecification = {
  id: "evacuation-zones-outline",
  type: "line",
  source: MARTIN_SOURCE,
  "source-layer": "evacuation_zones",
  minzoom: 4,
  layout: { visibility: "none" },
  paint: {
    "line-color": "#dc2626",
    "line-width": 1.5,
  },
};

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
// "building_tiles" is the Martin *source id*; the MVT layer name emitted by
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
  source: OSM_SOURCE,
  "source-layer": "osm_roads",
  minzoom: 10,
  paint: {
    "line-color": "#94a3b8",
    "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.5, 16, 3],
  },
};

export const waterwaysLayer: LayerSpecification = {
  id: "osm-waterways",
  type: "line",
  source: OSM_SOURCE,
  "source-layer": "osm_waterways",
  minzoom: 8,
  paint: {
    "line-color": "#3b82f6",
    "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.5, 16, 3],
  },
};

// ---------------------------------------------------------------------------
// tRPC-fed GeoJSON layers
//
// These draw from a per-viewport tRPC query (environmental.getWatersheds and
// environmental.getSoilSurvey) instead of a Martin tile source, so they are
// deliberately absent from getLayers(): the three static styles in styles.ts declare
// only vector/raster sources, and baking a geojson-backed layer into them would point
// at a source that does not exist until the query resolves. They also carry no
// `visibility: "none"`, because presence -- not a layout property -- is how a
// component-added layer is toggled off; setSource/addLayer and removal are the whole
// lifecycle. Declared here so the paint lives in one place beside its siblings.
// ---------------------------------------------------------------------------

// USGS NHD+ HR HUC12 boundaries. Painted flat rather than data-driven: the collection
// is proxied straight from the provider, so no attribute is guaranteed on every
// feature the way "severity" is on evacuation zones. Colour and opacity match the
// watershed fill WaterLayer already draws for the same source id.
export const watershedsLayer: LayerSpecification = {
  id: "watersheds-fill",
  type: "fill",
  source: WATERSHEDS_SOURCE,
  paint: {
    "fill-color": "#1565c0",
    "fill-opacity": 0.05,
  },
};

export const watershedsOutlineLayer: LayerSpecification = {
  id: "watersheds-outline",
  type: "line",
  source: WATERSHEDS_SOURCE,
  paint: {
    "line-color": "#1565c0",
    "line-width": 1,
    "line-opacity": 0.6,
  },
};

// USDA SSURGO map units. "drainageClass" is the one property usda-soil.ts sets on
// every feature it emits -- normalizeDrainageClass always returns a string -- which is
// why the fill keys to it rather than to mukey or hydric. normalizeDrainageClass tests
// its specific phrases ("very poorly", "somewhat poorly", "moderately well", "somewhat
// excessively") before the general ones ("poorly", "excessively", "well") that would
// otherwise swallow them, so all seven of its stable ids are reachable and each gets its
// own arm here, ordered driest to wettest: excessively- and somewhat-excessively-drained
// (warm/dry), well- and moderately-well-drained (green, the two "good" classes), then
// somewhat-poorly-, poorly-, and very-poorly-drained (blue, deepening with wetness).
// Anything the function can't classify falls through as the raw SSURGO string or
// "unknown" and hits the neutral grey default -- a degraded but honest fallback rather
// than a wrong colour.
export const soilSurveyLayer: LayerSpecification = {
  id: "soil-survey-fill",
  type: "fill",
  source: SOIL_SURVEY_SOURCE,
  paint: {
    "fill-color": [
      "match",
      ["get", "drainageClass"],
      "excessively-drained",
      "#d97706",
      "somewhat-excessively-drained",
      "#ca8a04",
      "well-drained",
      "#65a30d",
      "moderately-well-drained",
      "#0d9488",
      "somewhat-poorly-drained",
      "#0284c7",
      "poorly-drained",
      "#1d4ed8",
      "very-poorly-drained",
      "#1e3a8a",
      "#9ca3af",
    ],
    "fill-opacity": 0.35,
  },
};

export const soilSurveyOutlineLayer: LayerSpecification = {
  id: "soil-survey-outline",
  type: "line",
  source: SOIL_SURVEY_SOURCE,
  paint: {
    "line-color": "#78716c",
    "line-width": 0.5,
    "line-opacity": 0.7,
  },
};

export function getLayers(): LayerSpecification[] {
  return [
    firePerimetersLayer,
    firePerimetersOutlineLayer,
    sensorsLayer,
    evacuationZonesLayer,
    evacuationZonesOutlineLayer,
    interventionsLayer,
    interventionsOutlineLayer,
    buildingFootprintsLayer,
    roadsLayer,
    waterwaysLayer,
  ];
}

/**
 * Maps an activeLayers toggle id to the concrete style layer ids it controls.
 * Derived from the layer registry so a new style-backed toggle is one registry entry
 * rather than a hand-edit here that can silently disagree with the rest.
 */
export const STYLE_LAYER_TOGGLE_MAP: Record<string, string[]> = Object.fromEntries(
  styleBackedLayerEntries().map((entry) => [entry.toggleId, entry.styleLayerIds])
);
