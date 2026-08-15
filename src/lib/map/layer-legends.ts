/**
 * What each drawn layer's colours mean: one legend spec per `LayerToggleId`, assembled from
 * the very constants the renderers paint with. See src/components/map/AGENTS.md
 * "The legend legends what is drawn".
 *
 * Two rules hold this module together:
 *
 * 1. No colour is written here. Every swatch, class and ramp stop is imported from the
 *    module that draws it, so a palette edit reaches the map and the legend in one commit
 *    and the two can never disagree. Labels ARE written here -- they are prose about the
 *    encoding, not the encoding.
 * 2. A toggle gets a spec only when turning it on actually paints something. A layer whose
 *    upstream publishes nothing (`soil`) is absent on purpose: legending a colour the map
 *    never draws is the exact drift this module exists to prevent.
 *    `LEGENDLESS_TOGGLE_REASONS` records why for each.
 */

import {
  FIRE_BRIGHTNESS_COLOR_STOPS,
  FIRE_CONTAINMENT_COLOR_STOPS,
} from "@/components/map/layers/FireLayer";
import { DROUGHT_DRAWN_CLASSES } from "@/components/map/layers/DroughtLayer";
import { GAUGE_READING_COLORS, WELL_TREND_COLORS } from "@/components/map/layers/WaterLayer";
import {
  TEMPERATURE_COLOR_STOPS,
  WIND_SPEED_CLASSES,
} from "@/components/map/layers/WeatherLayer";
import { DEMAND_DENSITY_COLOR_STOPS } from "@/components/map/layers/DemandHeatmapLayer";
import { NDVI_COLOR_RAMP } from "@/lib/vegetation";
import {
  DEFAULT_SOIL_FIELD_DEPTHS,
  SOIL_FIELD_MEASURES,
  soilFieldDepthDefinition,
  type SoilFieldDepth,
  type SoilFieldMeasure,
} from "@/lib/environmental/soil-field";
import {
  CLIMATE_FIELD_SIGNALS,
  CLIMATE_RENDER_FORM_LABELS,
  climateFieldSignalForToggle,
  DEFAULT_AIR_TEMPERATURE_VARIANT,
  resolveClimateRenderForm,
  type AirTemperatureVariant,
  type ClimateFieldSignalId,
  type ClimateRenderForm,
} from "@/lib/environmental/climate-field";
import {
  BURN_SEVERITY_ACRES_STOPS,
  BURN_SEVERITY_UNCLASSIFIED_LABEL,
  EVACUATION_SEVERITY_CLASSES,
  EVACUATION_UNCLASSIFIED_LABEL,
  FIRE_PERIMETER_OUTLINE_COLOR,
  FIRE_PERIMETER_SEVERITY_CLASSES,
  FIRE_PERIMETER_UNCLASSIFIED_LABEL,
  INTERVENTION_OUTLINE_COLOR,
  INTERVENTION_PRIORITY_CLASSES,
  INTERVENTION_UNCLASSIFIED_LABEL,
  INTERVENTION_UNPRIORITIZED_POINT_COLOR,
  INTERVENTION_UNPRIORITIZED_POINT_LABEL,
  SENSOR_NETWORK_CLASSES,
  SENSOR_UNCLASSIFIED_LABEL,
  SOIL_SURVEY_DRAINAGE_CLASSES,
  SOIL_SURVEY_UNCLASSIFIED_LABEL,
  UNCLASSIFIED_FILL_COLOR,
  WATERSHED_BOUNDARY_COLOR,
  type StyleClass,
} from "@/lib/map/layers";
import { LAYER_TOGGLE_IDS, type LayerToggleId } from "@/lib/map/layer-registry";
import type { LayerVisibility } from "@/lib/map/layer-toggle-context";
import type { VegetationMode } from "@/components/map/layers/VegetationLayer";

/** One colour of a ramp. Only the stops worth captioning carry a label. */
export interface LegendRampStop {
  color: string;
  label?: string;
}

/** One row of a class list: a colour and what it means. */
export interface LegendClass {
  color: string;
  label: string;
}

/**
 * A gradient bar plus the captions for its ends and, where the middle stop is captioned,
 * its middle.
 *
 * The stops are spread EVENLY along the bar rather than positioned by their value. For the
 * banded soil fields that is exact -- the interior bands are uniform. For a ramp whose
 * stops are unevenly spaced (NDVI, log-spaced burn acreage) the bar reads as the ordered
 * palette rather than as a value axis, which keeps each caption pinned to the colour it
 * actually describes; a value-positioned bar would put the middle caption under a colour
 * that is not the middle stop's.
 */
export interface LegendRampBlock {
  kind: "ramp";
  caption?: string;
  stops: readonly LegendRampStop[];
}

/** Swatch-and-label rows. `dot` for point layers, `swatch` for areas. */
export interface LegendClassesBlock {
  kind: "classes";
  caption?: string;
  shape: "swatch" | "dot";
  classes: readonly LegendClass[];
}

/**
 * A single flat sample. `fillColor` is omitted for an outline that is drawn over a fill
 * some other block already legends -- a perimeter's border is one colour at every
 * severity, which a reader would otherwise take for the severity itself.
 */
export interface LegendSwatchBlock {
  kind: "swatch";
  label: string;
  fillColor?: string;
  outlineColor: string;
}

/** Free text for an encoding no swatch can carry -- marker size, glyph meaning. */
export interface LegendNoteBlock {
  kind: "note";
  text: string;
}

export type LegendBlock =
  | LegendRampBlock
  | LegendClassesBlock
  | LegendSwatchBlock
  | LegendNoteBlock;

/** Everything the legend shows for one layer. */
export interface LayerLegendSpec {
  /** The layer's name as a reader knows it, not its toggle id. */
  title: string;
  blocks: readonly LegendBlock[];
}

/** One resolved layer section, ready to render. */
export interface LegendEntry {
  toggleId: LayerToggleId;
  spec: LayerLegendSpec;
}

/**
 * The display modes that change what a layer paints, and so what its legend must say.
 * Passed in rather than read from the stores here, which keeps this module pure and lets a
 * test resolve any mode without mounting a provider.
 */
export interface LegendContext {
  vegetationMode: VegetationMode;
  ndviMode: "absolute" | "anomaly";
  soilFieldDepth: Record<SoilFieldMeasure, SoilFieldDepth>;
  /**
   * The form each climate signal is currently drawn in, where the reader has chosen one.
   *
   * SPARSE, and no longer a single `climateFieldSignal`. Which signal is drawn stopped being a
   * question with one answer on 2026-08-10: each of the nine has its own row, its own switch
   * and its own axis, so the legend renders a section for every one that is ON. A missing entry
   * here means the row is on its signal's default form, not that it is off -- `visibility` is
   * what says that.
   */
  climateRenderForms: Partial<Record<ClimateFieldSignalId, ClimateRenderForm>>;
  /** Only read on `air-temperature`; the other eight signals publish one value. */
  climateFieldVariant: AirTemperatureVariant;
}

/** The modes the stores seed themselves with. */
export const DEFAULT_LEGEND_CONTEXT: LegendContext = {
  vegetationMode: "ndvi",
  ndviMode: "absolute",
  soilFieldDepth: DEFAULT_SOIL_FIELD_DEPTHS,
  climateRenderForms: {},
  climateFieldVariant: DEFAULT_AIR_TEMPERATURE_VARIANT,
};

/** Why a registry toggle deliberately has no legend spec. A drawn layer must never appear here. */
export const LEGENDLESS_TOGGLE_REASONS: Partial<Record<LayerToggleId, string>> = {
  soil:
    "SoilLayer's tile template comes from getEnvironmentalTileTemplate, which returns an " +
    "empty string until a first-party SoilGrids raster release exists, so the layer adds no " +
    "source and paints no colour to legend.",
};

/**
 * A class list plus the row every unclassified feature falls to.
 *
 * `fallbackColor` defaults to the shared neutral because that is what every classified fill
 * falls back to. It is a parameter for the one layer whose renderer deliberately chose a
 * different fallback -- interventions' submitted points, where "unclassified" is the normal
 * state rather than a gap -- so the legend still reads its colour off the renderer.
 */
function classesWithFallback(
  classes: readonly StyleClass[],
  fallbackLabel: string,
  fallbackColor: string = UNCLASSIFIED_FILL_COLOR
): LegendClass[] {
  return [
    ...classes.map(({ color, label }) => ({ color, label })),
    { color: fallbackColor, label: fallbackLabel },
  ];
}

/**
 * What a stream gauge is drawn as, reading its colours from the record WaterLayer paints each
 * gauge with. This was a five-class flow-condition ramp; every class but the last was
 * unreachable, because NWIS instantaneous values carry no percentile to classify against --
 * see GAUGE_READING_COLORS in WaterLayer.tsx for why. A legend must only name states the map
 * can actually take.
 */
const STREAM_CONDITION_CLASSES: readonly LegendClass[] = [
  { color: GAUGE_READING_COLORS.reporting, label: "Reporting discharge" },
  { color: GAUGE_READING_COLORS.no_reading, label: "No reading" },
];

const WELL_TREND_CLASSES: readonly LegendClass[] = [
  { color: WELL_TREND_COLORS.rising, label: "Rising" },
  { color: WELL_TREND_COLORS.stable, label: "Stable" },
  { color: WELL_TREND_COLORS.declining, label: "Declining" },
  { color: WELL_TREND_COLORS.unknown, label: "Trend not reported" },
];

/**
 * The heatmap's density stops, captioned at the ends only: `heatmap-density` is a
 * normalised 0..1 quantity, so an interior stop names no number a reader could use.
 */
function demandDensityRampStops(): LegendRampStop[] {
  const lastIndex = DEMAND_DENSITY_COLOR_STOPS.length - 1;
  return DEMAND_DENSITY_COLOR_STOPS.map((stop, index) => ({
    color: stop.color,
    label: index === 0 ? "Low" : index === lastIndex ? "High" : undefined,
  }));
}

/**
 * The temperature ramp, captioned at the two ends and the middle -- the three `rampCaptions`
 * renders. The numbers come from the stop table itself rather than being typed here, so a
 * palette or range edit in `WeatherLayer` moves the captions with it. Degrees Celsius
 * because that is what the feed measures in; see `TEMPERATURE_COLOR_STOPS`.
 */
function temperatureRampStops(): LegendRampStop[] {
  const lastIndex = TEMPERATURE_COLOR_STOPS.length - 1;
  const middleIndex = Math.floor(TEMPERATURE_COLOR_STOPS.length / 2);
  return TEMPERATURE_COLOR_STOPS.map((stop, index) => ({
    color: stop.color,
    label:
      index === 0 || index === lastIndex || index === middleIndex
        ? `${stop.celsius}°C`
        : undefined,
  }));
}

/** Specs that do not depend on any display mode. */
const STATIC_LAYER_LEGENDS: Partial<Record<LayerToggleId, LayerLegendSpec>> = {
  fire: {
    title: "Active fire",
    blocks: [
      {
        kind: "ramp",
        caption: "Incidents: containment",
        stops: FIRE_CONTAINMENT_COLOR_STOPS,
      },
      {
        kind: "ramp",
        caption: "Detections: brightness",
        stops: FIRE_BRIGHTNESS_COLOR_STOPS,
      },
      {
        kind: "note",
        text: "Dot size: incident size (detections: confidence).",
      },
    ],
  },
  "fire-perimeters": {
    title: "Fire perimeters",
    blocks: [
      {
        kind: "classes",
        shape: "swatch",
        classes: classesWithFallback(
          FIRE_PERIMETER_SEVERITY_CLASSES,
          FIRE_PERIMETER_UNCLASSIFIED_LABEL
        ),
      },
      {
        kind: "swatch",
        label: "Perimeter outline, at every containment",
        outlineColor: FIRE_PERIMETER_OUTLINE_COLOR,
      },
    ],
  },
  water: {
    title: "Water",
    blocks: [
      {
        kind: "classes",
        caption: "Stream gauges",
        shape: "dot",
        classes: STREAM_CONDITION_CLASSES,
      },
      {
        kind: "classes",
        caption: "Groundwater wells",
        shape: "dot",
        classes: WELL_TREND_CLASSES,
      },
      { kind: "note", text: "Wells draw smaller than gauges at every zoom." },
    ],
  },
  drought: {
    title: "Drought (USDM)",
    blocks: [
      {
        kind: "classes",
        shape: "swatch",
        classes: DROUGHT_DRAWN_CLASSES.map(({ color, label }) => ({ color, label })),
      },
    ],
  },
  // Two encodings from one toggle and one feed: a temperature dot per station, with the wind
  // arrow drawn over it. A station measuring only one of the two draws only that one.
  weather: {
    title: "Wind & temperature",
    blocks: [
      { kind: "ramp", caption: "Temperature", stops: temperatureRampStops() },
      { kind: "classes", caption: "Wind speed", shape: "dot", classes: WIND_SPEED_CLASSES },
      {
        kind: "note",
        text: "Arrows point where the wind is blowing to, captioned with the measured speed.",
      },
    ],
  },
  sensors: {
    title: "Weather stations",
    blocks: [
      {
        kind: "classes",
        caption: "Network",
        shape: "dot",
        classes: classesWithFallback(SENSOR_NETWORK_CLASSES, SENSOR_UNCLASSIFIED_LABEL),
      },
    ],
  },
  watersheds: {
    title: "Watersheds",
    blocks: [
      {
        kind: "swatch",
        label: "HUC12 boundary",
        fillColor: WATERSHED_BOUNDARY_COLOR,
        outlineColor: WATERSHED_BOUNDARY_COLOR,
      },
      { kind: "note", text: "Boundaries only: the fill carries no measurement." },
      // Said here because the toggle is reachable at every zoom while the layer is not, and
      // a switched-on layer drawing nothing is otherwise indistinguishable from a broken
      // one. The floor is a measured tile-weight bound -- see watershedsLayer in layers.ts.
      {
        kind: "note",
        text: "Drawn from zoom 7 in; below that the HUC12 outlines are too heavy to serve.",
      },
    ],
  },
  // One palette across all three of the survey's render shapes -- delineations, unioned
  // drainage classes, and the zoomed-out lattice dots -- because all three colour on
  // `drainageClass` off this one table. Only the SHAPE changes with zoom, which is what the
  // note says; a second class list per tier would be the same colours claiming to be
  // different encodings.
  "soil-survey": {
    title: "Soil survey (SSURGO)",
    blocks: [
      {
        kind: "classes",
        caption: "Drainage class",
        shape: "swatch",
        classes: classesWithFallback(
          SOIL_SURVEY_DRAINAGE_CLASSES,
          SOIL_SURVEY_UNCLASSIFIED_LABEL
        ),
      },
      {
        kind: "note",
        text:
          "Zoomed out, one dot per grid cell instead of surveyed outlines: its colour is " +
          "the cell's most common drainage class and its size the map units counted in it.",
      },
    ],
  },
  "demand-heatmap": {
    title: "Community demand",
    blocks: [
      { kind: "ramp", caption: "Request density", stops: demandDensityRampStops() },
    ],
  },
  // Two shapes, because the toggle draws two geometries from one tile: ingested zones as
  // filled polygons, and interactively submitted sites as points. They share the priority
  // palette but not its fallback -- see INTERVENTION_UNPRIORITIZED_POINT_COLOR in layers.ts
  // for why a submitted site's "no priority" is a normal state and not a missing value.
  interventions: {
    title: "Interventions",
    blocks: [
      {
        kind: "classes",
        caption: "Zones",
        shape: "swatch",
        classes: classesWithFallback(
          INTERVENTION_PRIORITY_CLASSES,
          INTERVENTION_UNCLASSIFIED_LABEL
        ),
      },
      { kind: "swatch", label: "Zone outline (dashed)", outlineColor: INTERVENTION_OUTLINE_COLOR },
      {
        kind: "classes",
        caption: "Submitted sites",
        shape: "dot",
        classes: classesWithFallback(
          INTERVENTION_PRIORITY_CLASSES,
          INTERVENTION_UNPRIORITIZED_POINT_LABEL,
          INTERVENTION_UNPRIORITIZED_POINT_COLOR
        ),
      },
      {
        kind: "note",
        text: "Submitted sites appear only after a reviewer publishes them.",
      },
    ],
  },
  "evacuation-zones": {
    title: "Evacuation zones",
    blocks: [
      {
        kind: "classes",
        shape: "swatch",
        classes: classesWithFallback(
          EVACUATION_SEVERITY_CLASSES,
          EVACUATION_UNCLASSIFIED_LABEL
        ),
      },
    ],
  },
  "burn-severity": {
    title: "Burn scars (MTBS)",
    blocks: [
      {
        kind: "ramp",
        // Burned AREA, not a severity class: MTBS publishes no polygon-level class, so the
        // fill keys to acres. See the note above burnSeverityLayer in layers.ts.
        caption: "Burned area",
        stops: BURN_SEVERITY_ACRES_STOPS,
      },
      {
        kind: "classes",
        shape: "swatch",
        classes: [
          { color: UNCLASSIFIED_FILL_COLOR, label: BURN_SEVERITY_UNCLASSIFIED_LABEL },
        ],
      },
    ],
  },
};

/**
 * The vegetation spec for a display mode, or null when that mode paints nothing.
 *
 * Only NDVI ever draws. NDWI has no upstream at all -- `getNDWITileUrl` returns an empty
 * string unconditionally, so VegetationLayer never adds the raster -- and NBR is
 * unpublished for the same reason `soil` is, so both modes are legended by their absence
 * rather than by a ramp for tiles that never arrive.
 */
export function vegetationLegendSpec(
  mode: VegetationMode,
  ndviMode: "absolute" | "anomaly"
): LayerLegendSpec | null {
  if (mode !== "ndvi") return null;
  const blocks: LegendBlock[] = [
    {
      kind: "ramp",
      caption: "NDVI",
      stops: NDVI_COLOR_RAMP.map(({ color, label }) => ({ color, label })),
    },
  ];
  // The measured cells are painted on absolute NDVI whatever the mode says, because the
  // anomaly baseline was never published; saying so is cheaper than a reader concluding
  // the ramp means something it does not.
  if (ndviMode === "anomaly") {
    blocks.push({
      kind: "note",
      text: "Anomaly mode publishes no raster; the drawn cells are absolute NDVI.",
    });
  }
  return { title: "Vegetation", blocks };
}

/** The ERA5-Land field spec for a measure at the depth the panel has selected. */
export function soilFieldLegendSpec(
  measure: SoilFieldMeasure,
  depth: SoilFieldDepth
): LayerLegendSpec {
  const definition = SOIL_FIELD_MEASURES[measure];
  return {
    title: `${definition.quantityLabel} (${definition.unitLabel})`,
    blocks: [
      {
        kind: "ramp",
        caption: soilFieldDepthDefinition(measure, depth).label,
        stops: definition.bands.map((band) => ({ color: band.color, label: band.label })),
      },
    ],
  };
}

/**
 * The NASA POWER spec for one signal, in the form its row is currently drawn in.
 *
 * The caption carries two things a ramp alone cannot say. The air-temperature STATISTIC,
 * because `mean`, `max` and `min` share one band table and a reader looking at the colours
 * still needs to be told which of the three they are keyed to -- the other eight signals
 * publish one value and have no statistic to name. And the FORM, because since the nine rows
 * landed the same ramp can reach the map as a filled cell, a contour or a point, and three
 * legend sections showing identical swatches over three differently-drawn layers is exactly
 * the confusion nine rows were meant to remove.
 */
export function climateFieldLegendSpec(
  signal: ClimateFieldSignalId,
  variant: AirTemperatureVariant = DEFAULT_AIR_TEMPERATURE_VARIANT,
  renderForm?: ClimateRenderForm
): LayerLegendSpec {
  const definition = CLIMATE_FIELD_SIGNALS[signal];
  const selectedVariant = definition.variants.find(
    (candidate) => candidate.variant === variant
  );
  const form = resolveClimateRenderForm(signal, renderForm);
  const formLabel = CLIMATE_RENDER_FORM_LABELS[form];
  const blocks: LegendBlock[] = [
    {
      kind: "ramp",
      caption:
        selectedVariant === undefined
          ? formLabel
          : `${selectedVariant.label} · ${formLabel}`,
      stops: definition.bands.map((band) => ({ color: band.color, label: band.label })),
    },
  ];
  // The three soil-wetness signals are measured on part of the lattice. A ramp with no note
  // would let an almost-blank map read as an outage rather than as the pilot it is.
  if (definition.coverageNote !== null) {
    blocks.push({ kind: "note", text: definition.coverageNote });
  }
  return { title: `${definition.quantityLabel} (${definition.unitLabel})`, blocks };
}

/** One layer's legend in the given display modes, or null when it paints nothing to legend. */
export function layerLegendSpec(
  toggleId: LayerToggleId,
  context: LegendContext = DEFAULT_LEGEND_CONTEXT
): LayerLegendSpec | null {
  if (toggleId === "vegetation") {
    return vegetationLegendSpec(context.vegetationMode, context.ndviMode);
  }
  if (toggleId === "soil-moisture") {
    return soilFieldLegendSpec("moisture", context.soilFieldDepth.moisture);
  }
  if (toggleId === "soil-temperature") {
    return soilFieldLegendSpec("temperature", context.soilFieldDepth.temperature);
  }
  if (toggleId === "soil-vpd") {
    return soilFieldLegendSpec("vpd", context.soilFieldDepth.vpd);
  }
  // Nine toggles resolve through one branch rather than nine string comparisons: the toggle id
  // carries its signal, so this cannot fall out of step with the registry the way a hand-listed
  // set of nine `if`s would when a tenth signal lands.
  const climateSignal = climateFieldSignalForToggle(toggleId);
  if (climateSignal !== null) {
    return climateFieldLegendSpec(
      climateSignal,
      context.climateFieldVariant,
      context.climateRenderForms[climateSignal]
    );
  }
  return STATIC_LAYER_LEGENDS[toggleId] ?? null;
}

/**
 * Every switched-on layer that has something to legend, in registry declaration order so
 * the card's sections never reshuffle as toggles come and go.
 */
export function activeLegendEntries(
  visibility: LayerVisibility,
  context: LegendContext = DEFAULT_LEGEND_CONTEXT
): LegendEntry[] {
  const entries: LegendEntry[] = [];
  for (const toggleId of LAYER_TOGGLE_IDS) {
    if (!visibility[toggleId]) continue;
    const spec = layerLegendSpec(toggleId, context);
    if (spec !== null) entries.push({ toggleId, spec });
  }
  return entries;
}
