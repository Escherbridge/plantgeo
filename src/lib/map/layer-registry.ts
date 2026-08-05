/**
 * The map layer registry: one entry per switchable layer. Rationale and the drift it
 * replaces are in src/components/map/AGENTS.md "The layer toggle is the only source of
 * layer visibility".
 */

import type { PanelId } from "@/stores/panel-store";

/** Every toggle id the registry knows. `activeLayers` may also hold user-uploaded layer ids. */
export type LayerToggleId =
  | "fire"
  | "fire-perimeters"
  | "water"
  | "drought"
  | "weather"
  | "sensors"
  | "watersheds"
  | "vegetation"
  | "soil"
  | "soil-survey"
  | "demand-heatmap"
  | "interventions"
  | "evacuation-zones"
  | "building-footprints";

/** How a toggle reaches the map: a React-mounted layer component, or baked style layers. */
export type LayerRenderKind = "component" | "style";

/** Identity and wiring for one switchable layer. */
export interface LayerRegistryEntry {
  /** The id carried in map-store's `activeLayers`. */
  toggleId: LayerToggleId;
  renderKind: LayerRenderKind;
  /** Style layer ids flipped with setLayoutProperty; empty for React-mounted layers. */
  styleLayerIds: string[];
  /** The `geo.layers.name` this toggle renders, or null when no warehouse layer backs it. */
  warehouseLayerName: string | null;
  /** The sidebar panel that owns this switch, or null when no panel does. */
  panelId: PanelId | null;
  /** Set when the capability is withheld by governance; the switch is disabled and the layer never renders. */
  permanentlyUnavailableReason: string | null;
}

export const LAYER_REGISTRY: Record<LayerToggleId, LayerRegistryEntry> = {
  fire: {
    toggleId: "fire",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: "fire-detections",
    panelId: "fire",
    permanentlyUnavailableReason: null,
  },
  "fire-perimeters": {
    toggleId: "fire-perimeters",
    renderKind: "style",
    styleLayerIds: ["fire-perimeters", "fire-perimeters-outline"],
    warehouseLayerName: "fire-perimeters",
    panelId: "fire",
    permanentlyUnavailableReason: null,
  },
  water: {
    toggleId: "water",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: "water-gauges",
    panelId: "water",
    permanentlyUnavailableReason: null,
  },
  // Drought has no geo.layers row -- it lives in geo.drought_areas as weekly releases --
  // so it claims no warehouse layer name and gets no slider capability.
  drought: {
    toggleId: "drought",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: null,
    panelId: "water",
    permanentlyUnavailableReason: null,
  },
  weather: {
    toggleId: "weather",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: "weather-observations",
    panelId: "water",
    permanentlyUnavailableReason: null,
  },
  // 750 published rows, geo.sensor_tiles() already live in martin.yaml -- the
  // style layer was the only missing piece. See sensorsLayer in layers.ts.
  sensors: {
    toggleId: "sensors",
    renderKind: "style",
    styleLayerIds: ["sensors"],
    warehouseLayerName: "sensors",
    panelId: "water",
    permanentlyUnavailableReason: null,
  },
  // USGS NHD+ HR HUC12 boundaries, proxied per viewport through
  // environmental.getWatersheds. A live upstream feed rather than a geo.layers
  // release, so it claims no warehouse layer name and gets no slider capability --
  // the same shape drought has. See watershedsLayer in layers.ts.
  watersheds: {
    toggleId: "watersheds",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: null,
    panelId: "water",
    permanentlyUnavailableReason: null,
  },
  vegetation: {
    toggleId: "vegetation",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: "vegetation",
    panelId: "vegetation",
    permanentlyUnavailableReason: null,
  },
  // Rendered from raster tiles, not from a geo.layers feed.
  soil: {
    toggleId: "soil",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: null,
    panelId: "soil",
    permanentlyUnavailableReason: null,
  },
  // USDA SSURGO map units, proxied per viewport through environmental.getSoilSurvey.
  // Distinct from `soil` above, which draws the SoilGrids raster: this one is the
  // vector survey polygons. Also upstream-proxied, so no warehouse layer name.
  // See soilSurveyLayer in layers.ts.
  "soil-survey": {
    toggleId: "soil-survey",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: null,
    panelId: "soil",
    permanentlyUnavailableReason: null,
  },
  "demand-heatmap": {
    toggleId: "demand-heatmap",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: null,
    panelId: "community",
    permanentlyUnavailableReason:
      "Aggregate demand is not published, because it would leak the locations the ledger exists to protect. It stays dark until a reviewed, access-controlled warehouse publication is in place.",
  },
  interventions: {
    toggleId: "interventions",
    renderKind: "style",
    styleLayerIds: ["interventions", "interventions-outline"],
    warehouseLayerName: "interventions",
    panelId: "community",
    permanentlyUnavailableReason: null,
  },
  // 381 published Oregon OEM rows, previously with no tile function at all --
  // see evacuationZonesLayer/evacuationZonesOutlineLayer in layers.ts.
  "evacuation-zones": {
    toggleId: "evacuation-zones",
    renderKind: "style",
    styleLayerIds: ["evacuation-zones", "evacuation-zones-outline"],
    warehouseLayerName: "evacuation-zones",
    panelId: "fire",
    permanentlyUnavailableReason: null,
  },
  // Toggled from the MapControls toolbar, so no panel governs it.
  "building-footprints": {
    toggleId: "building-footprints",
    renderKind: "style",
    styleLayerIds: ["building-footprints"],
    warehouseLayerName: null,
    panelId: null,
    permanentlyUnavailableReason: null,
  },
};

export const LAYER_TOGGLE_IDS = Object.keys(LAYER_REGISTRY) as LayerToggleId[];

/** Every registry entry, in declaration order. */
export function layerRegistryEntries(): LayerRegistryEntry[] {
  return LAYER_TOGGLE_IDS.map((toggleId) => LAYER_REGISTRY[toggleId]);
}

/** True when the string names a registry layer rather than a user-uploaded one. */
export function isLayerToggleId(value: string): value is LayerToggleId {
  return Object.prototype.hasOwnProperty.call(LAYER_REGISTRY, value);
}

/** Entries whose visibility is flipped with setLayoutProperty instead of mount/unmount. */
export function styleBackedLayerEntries(): LayerRegistryEntry[] {
  return layerRegistryEntries().filter((entry) => entry.styleLayerIds.length > 0);
}

/** The toggle that renders a `geo.layers.name`, or null when that layer has no renderer. */
export function toggleIdForWarehouseLayerName(layerName: string): LayerToggleId | null {
  const entry = layerRegistryEntries().find(
    (candidate) => candidate.warehouseLayerName === layerName
  );
  return entry?.toggleId ?? null;
}

/** The panel that owns a toggle's switch, or null for user-uploaded and toolbar layers. */
export function panelIdForLayerToggle(layerId: string): PanelId | null {
  if (!isLayerToggleId(layerId)) return null;
  return LAYER_REGISTRY[layerId].panelId;
}
