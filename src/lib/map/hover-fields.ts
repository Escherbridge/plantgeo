// Pure field-selection + formatting for the shared map hover tooltip.
// No React, no maplibre imports (types only) -- keeps this testable in isolation.

import {
  formatAbsoluteDate,
  formatTimestampWithRelative,
  resolveObservationIso,
  toIsoTimestamp,
} from "@/lib/map/time-format";

/** Style layer ids the shared hover manager queries via queryRenderedFeatures. */
export const HOVERABLE_LAYER_IDS: string[] = [
  "published-fire-circles",
  "water-gauges-circle",
  "groundwater-wells-circle",
  "fire-perimeters",
  "interventions",
  "building-footprints",
  "osm-roads",
  "osm-waterways",
];

export interface HoverContent {
  title: string;
  lines: string[];
}

type Properties = Record<string, unknown>;

/** Coerces a possibly-string numeric property to a finite number, else null. */
function toFiniteNumber(value: unknown): number | null {
  const num = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
  return Number.isFinite(num) ? num : null;
}

/** Formats a numeric property with fixed decimals + unit suffix. Null-safe. */
function formatFixed(value: unknown, decimals: number, suffix: string): string | null {
  const num = toFiniteNumber(value);
  return num === null ? null : `${num.toFixed(decimals)}${suffix}`;
}

/** Formats a numeric property rounded to an integer + unit suffix. Null-safe. */
function formatInteger(value: unknown, suffix: string): string | null {
  const num = toFiniteNumber(value);
  return num === null ? null : `${Math.round(num)}${suffix}`;
}

/** Formats a numeric property with locale grouping (e.g. "1,234") + unit suffix. */
function formatLocaleNumber(value: unknown, suffix: string): string | null {
  const num = toFiniteNumber(value);
  return num === null ? null : `${num.toLocaleString()}${suffix}`;
}

/** Trims a string property, rejecting empty/null-like sentinel strings. */
function stringField(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  const lower = trimmed.toLowerCase();
  if (lower === "null" || lower === "undefined" || lower === "nan") return null;
  return trimmed;
}

/** "below_normal" -> "Below normal". Single words humanize to a capitalized word. */
function humanizeSnakeCase(value: string): string {
  const spaced = value.replace(/_/g, " ").trim();
  if (!spaced) return spaced;
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase();
}

/** Drops null lines and returns null when nothing meaningful survived. */
function buildContent(title: string, lines: (string | null)[]): HoverContent | null {
  const filtered = lines.filter((line): line is string => line !== null);
  if (filtered.length === 0) return null;
  return { title, lines: filtered };
}

function formatFireDetection(props: Properties): HoverContent | null {
  const confidence = formatInteger(props.confidence, "%");
  const frp = formatFixed(props.frp, 1, " MW");
  const brightness = formatInteger(props.brightness, " K");
  const satellite = stringField(props.satellite);
  const detected = formatTimestampWithRelative(resolveObservationIso(props));

  return buildContent("Fire detection", [
    confidence ? `Confidence: ${confidence}` : null,
    frp ? `FRP: ${frp}` : null,
    brightness ? `Brightness: ${brightness}` : null,
    satellite ? `Satellite: ${satellite}` : null,
    detected ? `Detected: ${detected}` : null,
  ]);
}

function formatWaterGauge(props: Properties): HoverContent | null {
  const title = stringField(props.siteName) ?? "Water gauge";
  const flow = formatFixed(props.flowCfs, 1, " cfs");
  const condition = stringField(props.condition);
  const trend = stringField(props.trend);
  const percentile = props.percentile != null ? formatInteger(props.percentile, "%") : null;
  const updated = formatTimestampWithRelative(toIsoTimestamp(props.updatedAt));

  return buildContent(title, [
    flow ? `Flow: ${flow}` : null,
    condition ? `Condition: ${humanizeSnakeCase(condition)}` : null,
    trend ? `Trend: ${humanizeSnakeCase(trend)}` : null,
    percentile ? `Percentile: ${percentile}` : null,
    updated ? `Updated: ${updated}` : null,
  ]);
}

function formatGroundwaterWell(props: Properties): HoverContent | null {
  const title = stringField(props.siteName) ?? "Groundwater well";
  const depth = formatFixed(props.depthFt, 1, " ft");
  const trend = stringField(props.trend);
  const updated = formatTimestampWithRelative(toIsoTimestamp(props.updatedAt));

  return buildContent(title, [
    depth ? `Depth: ${depth}` : null,
    trend ? `Trend: ${humanizeSnakeCase(trend)}` : null,
    updated ? `Updated: ${updated}` : null,
  ]);
}

function formatFirePerimeter(props: Properties): HoverContent | null {
  const title = stringField(props.incidentName) ?? "Fire perimeter";
  const acres = formatLocaleNumber(props.gisAcres, " acres");
  const contained = props.percentContained != null ? formatInteger(props.percentContained, "% contained") : null;
  const severity = stringField(props.severity);
  const cause = stringField(props.fireCause);
  const state = stringField(props.pooState);
  // Discovery is when the fire started burning; polygonDateTime is only when the
  // perimeter outline was last redrawn. Showing both keeps them from being confused.
  const discovered = formatAbsoluteDate(toIsoTimestamp(props.fireDiscoveryDateTime));
  const perimeterUpdated = formatTimestampWithRelative(toIsoTimestamp(props.polygonDateTime));

  return buildContent(title, [
    acres ? `Size: ${acres}` : null,
    contained,
    severity ? `Severity: ${humanizeSnakeCase(severity)}` : null,
    cause ? `Cause: ${cause}` : null,
    state ? `State: ${state}` : null,
    discovered ? `Discovered: ${discovered}` : null,
    perimeterUpdated ? `Perimeter updated: ${perimeterUpdated}` : null,
  ]);
}

function formatIntervention(props: Properties): HoverContent | null {
  const title = stringField(props.name) ?? "Intervention";
  const priority = stringField(props.priority);
  const status = stringField(props.status);
  const description = stringField(props.description);

  return buildContent(title, [
    priority ? `Priority: ${priority}` : null,
    status ? `Status: ${status}` : null,
    description,
  ]);
}

function formatBuildingFootprint(props: Properties): HoverContent | null {
  const title = stringField(props.name) ?? "Building";
  const height = formatFixed(props.height, 0, " m");
  const levels = props.levels != null ? formatInteger(props.levels, "") : null;
  const buildingType = stringField(props.building_type);

  return buildContent(title, [
    height ? `Height: ${height}` : null,
    levels ? `Levels: ${levels}` : null,
    buildingType ? `Type: ${buildingType}` : null,
  ]);
}

function formatRoad(props: Properties): HoverContent | null {
  const title = stringField(props.name) ?? stringField(props.highway) ?? "Road";
  const highway = stringField(props.highway);
  const surface = stringField(props.surface);
  const lanes = props.lanes != null ? formatInteger(props.lanes, "") : null;
  const maxspeed = stringField(props.maxspeed);

  return buildContent(title, [
    highway ? `Type: ${highway}` : null,
    surface ? `Surface: ${surface}` : null,
    lanes ? `Lanes: ${lanes}` : null,
    maxspeed ? `Max speed: ${maxspeed}` : null,
  ]);
}

function formatWaterway(props: Properties): HoverContent | null {
  const title = stringField(props.name) ?? stringField(props.waterway) ?? "Waterway";
  const waterway = stringField(props.waterway);

  return buildContent(title, [waterway ? `Type: ${waterway}` : null]);
}

const FORMATTERS: Record<string, (props: Properties) => HoverContent | null> = {
  "published-fire-circles": formatFireDetection,
  "water-gauges-circle": formatWaterGauge,
  "groundwater-wells-circle": formatGroundwaterWell,
  "fire-perimeters": formatFirePerimeter,
  interventions: formatIntervention,
  "building-footprints": formatBuildingFootprint,
  "osm-roads": formatRoad,
  "osm-waterways": formatWaterway,
};

/** Per-layer field selection + unit formatting for the hover tooltip. Null when nothing to show. */
export function formatHoverContent(layerId: string, properties: Properties): HoverContent | null {
  const formatter = FORMATTERS[layerId];
  if (!formatter) return null;
  return formatter(properties ?? {});
}
