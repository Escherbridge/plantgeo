/**
 * The map layer registry: one entry per switchable layer. Rationale and the drift it
 * replaces are in src/components/map/AGENTS.md "The layer toggle is the only source of
 * layer visibility".
 */

import { SOIL_FIELD_MEASURES } from "@/lib/environmental/soil-field";
import {
  CLIMATE_FIELD_SIGNALS,
  CLIMATE_FIELD_SIGNAL_IDS,
  type ClimateFieldSignalId,
  type ClimateFieldToggleId,
} from "@/lib/environmental/climate-field";
import { SLIDER_STREAM_LAYER_NAMES, SNAPSHOT_SURFACE_LAYER_NAMES } from "@/types/time-slider";
import type { PanelId } from "@/stores/panel-store";

/** Every toggle id the registry knows. `activeLayers` may also hold user-uploaded layer ids. */
export type LayerToggleId =
  | ClimateFieldToggleId
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
  | "soil-vpd"
  | "demand-heatmap"
  | "interventions"
  | "strategy-recommendations"
  | "evacuation-zones"
  | "burn-severity";

/** How a toggle reaches the map: a React-mounted layer component, or baked style layers. */
export type LayerRenderKind = "component" | "style";

/**
 * The glyph a layer row draws, named rather than imported.
 *
 * This module is plain TypeScript that panels, stores and node-run tests all import, so it
 * must not pull a React component in. The names are resolved to `lucide-react` icons by
 * `src/components/map/layer-panel/layer-icons.tsx`, which is exhaustive over this union --
 * a new member fails to compile there rather than rendering nothing.
 */
export type LayerIconName =
  | "flame"
  | "flame-kindling"
  | "shield-alert"
  | "droplets"
  | "waves"
  | "wind"
  | "radio-tower"
  | "cloud-sun"
  | "cloud-rain"
  | "sun"
  | "gauge"
  | "leaf"
  | "mountain"
  | "layers"
  | "thermometer"
  | "sprout"
  | "users";

/** Identity and wiring for one switchable layer. */
export interface LayerRegistryEntry {
  /** The id carried in map-store's `activeLayers`. */
  toggleId: LayerToggleId;
  /**
   * The layer's name as a reader knows it. The single source of truth: until 2026-08-08 it
   * existed only as a hand-typed `label` prop at each `<LayerToggle>` call site, so a tree
   * grouped by category had no way to name a layer without duplicating sixteen strings.
   */
  label: string;
  /** The glyph the layer tree draws beside `label`. */
  icon: LayerIconName;
  renderKind: LayerRenderKind;
  /** Style layer ids flipped with setLayoutProperty; empty for React-mounted layers. */
  styleLayerIds: string[];
  /**
   * The warehouse stream this toggle renders: a `geo.layers.name`, one of
   * `SLIDER_STREAM_LAYER_NAMES`, one of `SNAPSHOT_SURFACE_LAYER_NAMES`, or null when nothing
   * the slider describes backs it.
   *
   * A stream name here is the whole of what gives a row an axis -- `hasSelectableDay` reads it,
   * and a null costs the row its slider, its scrubbing and its date-filtered map read at once.
   * `SNAPSHOT_SURFACE_LAYER_NAMES` is the one exception: it is a NAME with no axis behind it,
   * resolved by the capability resolver as a fixed reference row rather than an observation
   * window -- see the constant's own doc for why that is still a real resolution and not a
   * dressed-up null. Every non-null value must resolve one of these three ways or it is
   * silently dropped by the §9 LEFT JOIN (docs/layer-lane-standard.md) -- tiles render, history
   * reports zero, no slider mounts, nothing errors. Never hand-type one: the constants are in
   * src/types/time-slider.ts precisely so a typo cannot publish a name no capability answers to.
   */
  warehouseLayerName: string | null;
  /**
   * The category that owns this switch. Total, not nullable: `building-footprints` was the one
   * uncategorised layer, and it went with the 3D-footprints removal, so every layer is now
   * reachable from exactly one category's group in the dock.
   */
  panelId: PanelId;
  /**
   * Set when the capability is withheld -- by governance, or because the data behind it
   * doesn't exist yet -- the switch is disabled and the layer never renders either way.
   */
  permanentlyUnavailableReason: string | null;
}

/**
 * The glyph each climate signal's row draws.
 *
 * Lives here rather than on the signal definition because an icon name is a REGISTRY concern:
 * `LayerIconName` is declared in this module and resolved by `layer-icons.tsx`, and putting the
 * field on `ClimateFieldSignalDefinition` would make `climate-field.ts` -- which this module
 * imports -- import this one back. Exhaustive over the signal union, so a tenth signal fails to
 * compile here rather than rendering a row with no glyph.
 *
 * Nine distinct glyphs where the single `Climate` row used to carry one `cloud-sun`: nine rows
 * stacked in one dock group are told apart by their icons before their labels are read, and
 * nine identical clouds would undo exactly that.
 */
const CLIMATE_SIGNAL_ICONS: Readonly<Record<ClimateFieldSignalId, LayerIconName>> = {
  "air-temperature": "thermometer",
  "dew-point": "droplets",
  precipitation: "cloud-rain",
  "relative-humidity": "gauge",
  "shortwave-radiation": "sun",
  "wind-speed": "wind",
  "soil-wetness-surface": "sprout",
  "soil-wetness-root-zone": "sprout",
  "soil-wetness-profile": "sprout",
};

/**
 * One registry row per NASA POWER signal, read through `environmental.getClimateField`.
 *
 * The second lane served out of the MODEL plane (`agri.signal_observation`), so like the three
 * ERA5-Land fields above, each row's capability is published as a STREAM -- the days
 * `geo.climate_field_observation` can answer for, per signal -- rather than out of `geo.layers`.
 *
 * NINE rows since 2026-08-10, where one `Climate` row with a signal picker stood before. The
 * old shape was argued for on the grounds that nine signals are "nine answers to what was the
 * weather, only one of which can be painted over a cell at a time", and the second half of that
 * is still true of a FILLED field -- which is why `renderForms` exists and why only air
 * temperature defaults to `field`. The first half was the mistake: one toggle means one
 * `warehouseLayerName`, one capability, one axis, and that axis was computed over every signal
 * in the lane unioned together. A four-cell pilot and a 397-cell field were handed the same
 * scrubbable days and the same "latest observed" date. Nine rows is what makes each axis
 * describe the signal it belongs to.
 *
 * DERIVED from the signal table, not hand-listed: the label, the stream name and the toggle id
 * all come from `climate-field.ts`, so a tenth signal appears in the dock, on the map and on
 * the slider with no edit here beyond one glyph above.
 */
const CLIMATE_FIELD_ENTRIES = CLIMATE_FIELD_SIGNAL_IDS.reduce((entries, signal) => {
  const definition = CLIMATE_FIELD_SIGNALS[signal];
  entries[definition.toggleId] = {
    toggleId: definition.toggleId,
    label: definition.label,
    icon: CLIMATE_SIGNAL_ICONS[signal],
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: definition.streamName,
    panelId: "climate",
    permanentlyUnavailableReason: null,
  };
  return entries;
  // Seeded with a cast because the record is only exhaustive once the fold has run; the fold
  // is over `CLIMATE_FIELD_SIGNAL_IDS`, which is `Object.keys` of a record exhaustive over the
  // signal union, so every key does get written.
}, {} as Record<ClimateFieldToggleId, LayerRegistryEntry>);

export const LAYER_REGISTRY: Record<LayerToggleId, LayerRegistryEntry> = {
  fire: {
    toggleId: "fire",
    label: "Fire Detections",
    icon: "flame",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: "fire-detections",
    panelId: "fire",
    permanentlyUnavailableReason: null,
  },
  "fire-perimeters": {
    toggleId: "fire-perimeters",
    label: "Active Fire Perimeters",
    icon: "flame-kindling",
    renderKind: "style",
    styleLayerIds: ["fire-perimeters", "fire-perimeters-outline"],
    warehouseLayerName: "fire-perimeters",
    panelId: "fire",
    permanentlyUnavailableReason: null,
  },
  water: {
    toggleId: "water",
    label: "Water Gauges",
    icon: "droplets",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: "water-gauges",
    panelId: "water",
    permanentlyUnavailableReason: null,
  },
  // Drought has no geo.layers row -- it lives in geo.drought_areas as weekly releases -- so it
  // is published as a STREAM capability instead, under the name below. It claimed null here
  // until 2026-08-09, and the cost was not cosmetic: no capability meant no axis, so
  // `resolveLayerDate` fell through to the server's today, `requestDate` was undefined on every
  // render, and `getDroughtClassification` -- which has accepted a `date` all along -- could
  // only ever be asked for the live edge.
  drought: {
    toggleId: "drought",
    label: "Drought Monitor",
    icon: "sprout",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: SLIDER_STREAM_LAYER_NAMES.drought,
    panelId: "water",
    permanentlyUnavailableReason: null,
  },
  weather: {
    toggleId: "weather",
    label: "Wind & Weather",
    icon: "wind",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: "weather-observations",
    panelId: "water",
    permanentlyUnavailableReason: null,
  },
  // Published rows served by geo.sensor_tiles(), live in martin.yaml. See sensorsLayer in
  // layers.ts; the switch that reaches it is WaterDetails', added later than the style layer.
  sensors: {
    toggleId: "sensors",
    label: "Sensor Stations",
    icon: "radio-tower",
    renderKind: "style",
    styleLayerIds: ["sensors"],
    warehouseLayerName: "sensors",
    panelId: "water",
    permanentlyUnavailableReason: null,
  },
  // USGS WBD HUC12 boundaries, drawn from geo.watershed_tiles() rather than by proxying
  // environmental.getWatersheds per viewport. 0017_watershed_persistence gave them a geo.layers
  // row and the tile function, and `agri-cli ingest-watersheds` persists 9,396 PNW basins keyed
  // by HUC12 code, so the name claimed here is the layer this toggle actually draws.
  //
  // The proxy could never draw at an ordinary zoom: it caps a request at 1 square degree
  // (MAX_WATERSHED_BBOX_SQUARE_DEGREES in src/lib/server/services/hydrosheds.ts) while the
  // viewport bbox is ~767 at the default zoom, so every request was rejected and the layer fell
  // back to an empty collection. The tile path has no bbox ceiling and, since
  // 0023_watershed_zoom_generalization, no minzoom either: payload is bounded by drawing the
  // HUC rung the zoom can carry rather than by hiding the layer. The panel's basin LIST still
  // comes from the capped proxy, so it can be empty while the map draws — which is why the
  // Watersheds tab explains the ceiling instead of reporting an outage.
  //
  // Its 2013 WBD loaddate does not drag the slider axis: sliderDomain excludes snapshot layers
  // from the axis start.
  watersheds: {
    toggleId: "watersheds",
    label: "Watershed Boundaries",
    icon: "waves",
    renderKind: "style",
    styleLayerIds: ["watersheds-fill", "watersheds-outline"],
    warehouseLayerName: "watersheds",
    panelId: "water",
    permanentlyUnavailableReason: null,
  },
  vegetation: {
    toggleId: "vegetation",
    label: "Vegetation (NDVI)",
    icon: "leaf",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: "vegetation",
    panelId: "vegetation",
    permanentlyUnavailableReason: null,
  },
  // Rendered from raster tiles, not from a geo.layers feed.
  soil: {
    toggleId: "soil",
    label: "Soil Properties",
    icon: "mountain",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: null,
    panelId: "soil",
    // SoilLayer resolves its raster template through `getEnvironmentalTileTemplate`, which
    // returns "" unconditionally, so this switch has never had anything behind it. It read as
    // an ordinary working toggle -- flip it, watch nothing happen, conclude the data is
    // missing. The capability is withheld, and the row now says so instead of pretending.
    permanentlyUnavailableReason:
      "Soil property rasters are not published yet: no first-party SoilGrids tile release exists, so this layer has no tiles to draw. Click the map with the Soil section open to read measured values at a point.",
  },
  // USDA SSURGO map units, rendered per viewport through environmental.getSoilSurvey.
  // Distinct from `soil` above, which draws the SoilGrids raster: this one is the vector
  // survey polygons. See soilSurveyLayer in layers.ts.
  //
  // `warehouseLayerName` was null here until the conformance audit found the drop: 0013's
  // 0013_soil_survey_persistence.sql gave this layer a real `geo.layers` row on 2026-08-05
  // ('soil-survey', see the migration's INSERT), and geo.mv_soil_survey_grid/_union (the
  // pre-aggregation layer, 2026-08-15) give it a genuinely cheap history to report -- but
  // nobody repointed the registry after persistence landed, so this toggle silently lost its
  // §9 catalogue row: no axis, no scrubbing, no date-filtered read, and no error anywhere
  // saying why. The proxy-per-viewport READ path is unaffected; only the capability name
  // changes, from null to the geo.layers row it has carried since 2026-08-05.
  "soil-survey": {
    toggleId: "soil-survey",
    label: "Soil Survey (SSURGO)",
    icon: "layers",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: "soil-survey",
    panelId: "soil",
    permanentlyUnavailableReason: null,
  },
  // ERA5-Land volumetric soil water, read through environmental.getSoilField. The first layer
  // served out of the MODEL plane (agri.signal_observation) rather than geo.features, so its
  // capability is published as a STREAM -- the days `geo.soil_field_observation` can answer
  // for this measure -- rather than out of geo.layers. The stream is per MEASURE, not per lane:
  // moisture, temperature and vpd are three toggles with three sliders, so one shared name
  // would put three rows on one axis and one row's scrub would move the other two.
  // See SoilFieldLayer in layers/SoilFieldLayer.tsx.
  "soil-moisture": {
    toggleId: "soil-moisture",
    // Read off the measure vocabulary rather than restated: SoilDetails already captioned
    // this switch with `definition.layerLabel`, and two copies of a label is the drift this
    // field exists to end.
    label: SOIL_FIELD_MEASURES.moisture.layerLabel,
    icon: "droplets",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: SLIDER_STREAM_LAYER_NAMES.soilMoisture,
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
    label: SOIL_FIELD_MEASURES.temperature.layerLabel,
    icon: "thermometer",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: SLIDER_STREAM_LAYER_NAMES.soilTemperature,
    panelId: "soil",
    permanentlyUnavailableReason: null,
  },
  // Daily-max vapor pressure deficit, the same ERA5-Land lane and the same reader as the two
  // soil-state fields above with one atmospheric signal (vapor_pressure_deficit, kPa) and a
  // single pseudo-depth -- Open-Meteo derives it from 2 m temperature and humidity, so there
  // is no profile to select. Grouped under the soil panel because it rides that lane's
  // lattice and depth vocabulary, not because it measures soil: it is the map's atmospheric-
  // dryness (fire-weather) field.
  "soil-vpd": {
    toggleId: "soil-vpd",
    label: SOIL_FIELD_MEASURES.vpd.layerLabel,
    icon: "cloud-sun",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: SLIDER_STREAM_LAYER_NAMES.soilVapourPressureDeficit,
    panelId: "soil",
    permanentlyUnavailableReason: null,
  },
  ...CLIMATE_FIELD_ENTRIES,
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
    label: "Demand Heatmap",
    icon: "users",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: null,
    panelId: "community",
    permanentlyUnavailableReason: null,
  },
  // Three style layers, not two: the fill and its dashed outline draw ingested zones, and
  // "interventions-points" draws the Point geometry every interactive submission carries.
  // A fill layer cannot render a Point, so before the circle layer existed this toggle was
  // switched on, served its tile and painted nothing for approved recommendations. All
  // three must be listed here or applyVisibility flips only part of the layer.
  interventions: {
    toggleId: "interventions",
    label: "Interventions",
    icon: "sprout",
    renderKind: "style",
    styleLayerIds: ["interventions", "interventions-outline", "interventions-points"],
    warehouseLayerName: "interventions",
    panelId: "community",
    permanentlyUnavailableReason: null,
  },
  // Rendered from the three geo.mv_strategy_recommendations_{coarse,regional,detail} tiers
  // directly, not from a day-scrubbable geo.features/stream feed -- there is no observation
  // day here, only a strategy_id/cell_id grain. `warehouseLayerName` names the DECLARED
  // SNAPSHOT capability the conformance audit found this toggle had always been missing: the
  // literal it carried matched neither a `geo.layers` row nor a `SLIDER_STREAM_LAYER_NAMES`
  // entry, so the §9 LEFT JOIN silently dropped it -- tiles painted, history reported zero, no
  // slider ever mounted. Sourced from SNAPSHOT_SURFACE_LAYER_NAMES rather than hand-typed so
  // the registry and the capability resolver that must register this exact name cannot drift.
  "strategy-recommendations": {
    toggleId: "strategy-recommendations",
    label: "ML Strategy Recommendations",
    icon: "sprout",
    renderKind: "component",
    styleLayerIds: [],
    warehouseLayerName: SNAPSHOT_SURFACE_LAYER_NAMES.strategyRecommendations,
    panelId: "community",
    permanentlyUnavailableReason: null,
  },
  // Published Oregon OEM rows, previously with no tile function at all -- see
  // evacuationZonesLayer/evacuationZonesOutlineLayer in layers.ts; FireDetails owns the switch.
  "evacuation-zones": {
    toggleId: "evacuation-zones",
    label: "Evacuation Zones",
    icon: "shield-alert",
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
    // "Burn History", not "Burn Severity": the published rows carry burned area and no
    // severity class, and the fill says so. Kept byte-identical to the switch FireDetails
    // captioned before the label moved here.
    label: "Burn History (MTBS)",
    icon: "flame-kindling",
    renderKind: "style",
    styleLayerIds: ["burn-severity", "burn-severity-outline"],
    warehouseLayerName: "burn-severity",
    panelId: "fire",
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

/** The layer's name as a reader knows it. The only place a switch or a row may read it from. */
export function layerLabel(toggleId: LayerToggleId): string {
  return LAYER_REGISTRY[toggleId].label;
}

/** Entries whose visibility is flipped with setLayoutProperty instead of mount/unmount. */
export function styleBackedLayerEntries(): LayerRegistryEntry[] {
  return layerRegistryEntries().filter((entry) => entry.styleLayerIds.length > 0);
}

/** The toggle that renders a warehouse stream name, or null when that stream has no renderer. */
export function toggleIdForWarehouseLayerName(layerName: string): LayerToggleId | null {
  const entry = layerRegistryEntries().find(
    (candidate) => candidate.warehouseLayerName === layerName
  );
  return entry?.toggleId ?? null;
}

/** The category that owns a toggle's switch, or null for a user-uploaded (non-registry) layer. */
export function panelIdForLayerToggle(layerId: string): PanelId | null {
  if (!isLayerToggleId(layerId)) return null;
  return LAYER_REGISTRY[layerId].panelId;
}

/** Panels owning at least one layer, in registry declaration order. */
export function panelIdsOwningLayers(): PanelId[] {
  const ordered: PanelId[] = [];
  for (const entry of layerRegistryEntries()) {
    if (!ordered.includes(entry.panelId)) ordered.push(entry.panelId);
  }
  return ordered;
}

/**
 * Toggles nothing reaches — always empty; a non-empty result is a wiring gap.
 *
 * `panelId` is total now, so the claim this makes is entirely about the SWITCH: a category is
 * a claim about a component the registry never sees, and the panel field alone returned `[]`
 * while `sensors` and `evacuation-zones` had no switch in any panel. Pass the toggle ids the
 * panel sources actually render — see src/__tests__/lib/map/layer-registry.test.ts, which
 * reads them out of src/components.
 */
export function unreachableLayerToggleIds(renderedToggleIds?: Iterable<string>): LayerToggleId[] {
  if (renderedToggleIds === undefined) return [];
  const rendered = new Set(renderedToggleIds);
  return layerRegistryEntries()
    .filter((entry) => !rendered.has(entry.toggleId))
    .map((entry) => entry.toggleId);
}
