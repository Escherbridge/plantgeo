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
  | "soil-moisture"
  | "soil-temperature"
  | "demand-heatmap"
  | "interventions"
  | "evacuation-zones"
  | "burn-severity"
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
  /**
   * Set when the capability is withheld -- by governance, or because the data behind it
   * doesn't exist yet -- the switch is disabled and the layer never renders either way.
   */
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
  // Published rows served by geo.sensor_tiles(), live in martin.yaml. See sensorsLayer in
  // layers.ts; the switch that reaches it is WaterPanel's, added later than the style layer.
  sensors: {
    toggleId: "sensors",
    renderKind: "style",
    styleLayerIds: ["sensors"],
    warehouseLayerName: "sensors",
    panelId: "water",
    permanentlyUnavailableReason: null,
  },
  // USGS NHD+ HR HUC12 boundaries, still RENDERED by proxying environmental.getWatersheds per
  // viewport -- but no longer only that. 0017_watershed_persistence gave them a geo.layers row,
  // geo.watershed_tiles(), and `agri-cli ingest-watersheds` persists 9,396 PNW basins keyed by
  // HUC12 code, so the warehouse now holds what this toggle draws.
  //
  // `warehouseLayerName` stays null until the render path actually moves. It names the layer a
  // toggle DRAWS, not one that merely exists, and this one still draws proxied GeoJSON: claiming
  // the name now would caption a live upstream feed with the warehouse's availability. The switch
  // is blocked on the Martin service redeploying to pick up watershed_tiles from martin.yaml --
  // see the note on DYNAMIC_TILE_SOURCE_IDS in src/lib/map/sources.ts for why the composite cannot
  // list the source before then, and for the exact remaining steps.
  //
  // Note the layer already appears in the slider's per-layer record list regardless, because
  // capabilities are derived from geo.layers rather than from this registry. Its 2013 WBD loaddate
  // does not drag the axis: sliderDomain excludes snapshot layers from the axis start.
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
  // ERA5-Land volumetric soil water, read through environmental.getSoilField. The first
  // layer served out of the MODEL plane (agri.signal_observation) rather than geo.features,
  // so it claims no `geo.layers` name and gets no slider capability -- the same shape drought
  // and watersheds have. It still draws the slider's day; it simply makes no claim about
  // which days the axis should offer. See SoilFieldLayer in layers/SoilFieldLayer.tsx.
  "soil-moisture": {
    toggleId: "soil-moisture",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: null,
    panelId: "soil",
    permanentlyUnavailableReason: null,
  },
  // ERA5-Land soil temperature, the same lane and the same reader as soil-moisture above
  // with a different signal family (soil_temperature_level_1..4, degrees Celsius) and a
  // fourth depth ECMWF publishes for temperature but the moisture lane does not fetch.
  // A SEPARATE toggle rather than a mode of the moisture one: they are two measurements of
  // the same ground and a reader may want either, both or neither, and folding them into one
  // switch would make "off" ambiguous. Not withheld -- the backfill is still filling cells,
  // and partial coverage is reported as such by the reader rather than gated here, because a
  // disabled switch would outlast the gap and nothing would reopen it.
  "soil-temperature": {
    toggleId: "soil-temperature",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: null,
    panelId: "soil",
    permanentlyUnavailableReason: null,
  },
  // Served by /api/v1/action-network's k-anonymity-floored activity grid --
  // aggregateActivityGrid in src/lib/server/services/community-activity.ts groups
  // strategy_requests into zoom-derived cells with a HAVING count(*) >= 3 floor and
  // bbox-independent cell membership, so publishing it never leaks a single private
  // submission's location (see community-activity-anonymity.test.ts). That is the
  // "reviewed, access-controlled warehouse publication" this switch was withheld
  // pending; the 2026-08-03 owner decision reversed the governance stubs generally
  // ("open the gates rather than preserving them"), and this one's gate was already
  // satisfied. DemandHeatmapLayer/useActionNetworkFeatures/the worker needed no changes.
  "demand-heatmap": {
    toggleId: "demand-heatmap",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: null,
    panelId: "community",
    permanentlyUnavailableReason: null,
  },
  interventions: {
    toggleId: "interventions",
    renderKind: "style",
    styleLayerIds: ["interventions", "interventions-outline"],
    warehouseLayerName: "interventions",
    panelId: "community",
    permanentlyUnavailableReason: null,
  },
  // Published Oregon OEM rows, previously with no tile function at all -- see
  // evacuationZonesLayer/evacuationZonesOutlineLayer in layers.ts; FireDashboard owns the switch.
  "evacuation-zones": {
    toggleId: "evacuation-zones",
    renderKind: "style",
    styleLayerIds: ["evacuation-zones", "evacuation-zones-outline"],
    warehouseLayerName: "evacuation-zones",
    panelId: "fire",
    permanentlyUnavailableReason: null,
  },
  // MTBS burned-area boundaries. 0011 gave them a geo.layers row and 0012 the tile
  // function; before that the layer had no path to the map at any level, so it was
  // invisible by construction rather than switched off. See burnSeverityLayer in layers.ts.
  "burn-severity": {
    toggleId: "burn-severity",
    renderKind: "style",
    styleLayerIds: ["burn-severity", "burn-severity-outline"],
    warehouseLayerName: "burn-severity",
    panelId: "fire",
    permanentlyUnavailableReason: null,
  },
  // Toggled from the MapControls toolbar, so no panel governs it. Withheld: the Martin
  // function (building_tiles) is live, but its backing table geo.osm_buildings has 0 rows
  // in production -- the osm2pgsql import (infra/db/import/osm-flex-config.lua) has not
  // been run for the covered region -- so the switch would toggle a capability that can
  // never show anything. Drop this reason the same way demand-heatmap's was dropped, once
  // the import has run and the table is populated; nothing else needs to change.
  "building-footprints": {
    toggleId: "building-footprints",
    renderKind: "style",
    styleLayerIds: ["building-footprints"],
    warehouseLayerName: null,
    panelId: null,
    permanentlyUnavailableReason:
      "3D building footprints are not published yet: the OSM building import has not been run for this region, so geo.osm_buildings has no rows even though the tile function is live.",
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

/**
 * Toggles reachable from the MapControls toolbar instead of the sidebar rail. See
 * src/components/map/AGENTS.md "The layer registry and the toggle context".
 */
export const TOOLBAR_OWNED_LAYER_TOGGLE_IDS: readonly LayerToggleId[] = ["building-footprints"];

/** Panels owning at least one layer, in registry declaration order; the rail's layer buttons. */
export function panelIdsOwningLayers(): PanelId[] {
  const ordered: PanelId[] = [];
  for (const entry of layerRegistryEntries()) {
    if (entry.panelId !== null && !ordered.includes(entry.panelId)) ordered.push(entry.panelId);
  }
  return ordered;
}

/**
 * Toggles nothing reaches — always empty; a non-empty result is a wiring gap.
 *
 * Called with no argument this only catches an entry that claims no panel, which is the
 * weaker half: a `panelId` is a claim about a component the registry never sees, and this
 * returned `[]` while `sensors` and `evacuation-zones` had no switch in any panel. Pass the
 * toggle ids the panel sources actually render to catch that larger class — see
 * src/__tests__/lib/map/layer-registry.test.ts, which reads them out of src/components.
 */
export function unreachableLayerToggleIds(renderedToggleIds?: Iterable<string>): LayerToggleId[] {
  // Toolbar layers are switched by bespoke controls (MapControls), never by a LayerToggle,
  // so they are exempt from both the panel claim and the rendered-switch check.
  const rendered = renderedToggleIds === undefined ? null : new Set(renderedToggleIds);
  return layerRegistryEntries()
    .filter((entry) => {
      if (TOOLBAR_OWNED_LAYER_TOGGLE_IDS.includes(entry.toggleId)) return false;
      if (entry.panelId === null) return true;
      return rendered !== null && !rendered.has(entry.toggleId);
    })
    .map((entry) => entry.toggleId);
}
