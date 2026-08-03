// Pure field-selection + formatting for the shared map hover tooltip.
// No React, no maplibre imports (types only) -- keeps this testable in isolation.

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

const ISO_WITH_TIMEZONE = /(?:Z|[+-]\d{2}:\d{2})$/i;
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

/** Resolves a FIRMS-style observedAt, or acqDate+acqTime, to an ISO timestamp. */
function resolveObservationIso(properties: Properties): string | null {
  const observedAt = properties.observedAt;
  if (typeof observedAt === "string" && ISO_WITH_TIMEZONE.test(observedAt.trim())) {
    const t = Date.parse(observedAt);
    if (Number.isFinite(t)) return observedAt;
  }

  const acqDate = properties.acqDate;
  if (typeof acqDate === "string" && DATE_ONLY.test(acqDate.trim())) {
    const acqTimeRaw = properties.acqTime;
    const acqTime =
      typeof acqTimeRaw === "string" || typeof acqTimeRaw === "number"
        ? String(acqTimeRaw).trim().padStart(4, "0")
        : "";
    if (/^\d{4}$/.test(acqTime)) {
      const iso = `${acqDate.trim()}T${acqTime.slice(0, 2)}:${acqTime.slice(2)}:00Z`;
      const t = Date.parse(iso);
      if (Number.isFinite(t)) return iso;
    }
  }

  return null;
}

/** Formats an ISO timestamp as coarse relative time ("3h ago"), or null if unparseable. */
function relativeTime(isoValue: string | null): string | null {
  if (!isoValue) return null;
  const ms = Date.parse(isoValue);
  if (!Number.isFinite(ms)) return null;
  const diffSec = Math.max(0, Math.floor((Date.now() - ms) / 1000));
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return `${Math.floor(diffSec / 86400)}d ago`;
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
  const detected = relativeTime(resolveObservationIso(props));

  return buildContent("Fire detection", [
    confidence ? `Confidence: ${confidence}` : null,
    frp ? `FRP: ${frp}` : null,
    brightness ? `Brightness: ${brightness}` : null,
    satellite ? `Satellite: ${satellite}` : null,
    detected ? `Detected ${detected}` : null,
  ]);
}

function formatWaterGauge(props: Properties): HoverContent | null {
  const title = stringField(props.siteName) ?? "Water gauge";
  const flow = formatFixed(props.flowCfs, 1, " cfs");
  const condition = stringField(props.condition);
  const trend = stringField(props.trend);
  const percentile = props.percentile != null ? formatInteger(props.percentile, "%") : null;
  const updated = relativeTime(stringField(props.updatedAt));

  return buildContent(title, [
    flow ? `Flow: ${flow}` : null,
    condition ? `Condition: ${humanizeSnakeCase(condition)}` : null,
    trend ? `Trend: ${humanizeSnakeCase(trend)}` : null,
    percentile ? `Percentile: ${percentile}` : null,
    updated ? `Updated ${updated}` : null,
  ]);
}

function formatGroundwaterWell(props: Properties): HoverContent | null {
  const title = stringField(props.siteName) ?? "Groundwater well";
  const depth = formatFixed(props.depthFt, 1, " ft");
  const trend = stringField(props.trend);
  const updated = relativeTime(stringField(props.updatedAt));

  return buildContent(title, [
    depth ? `Depth: ${depth}` : null,
    trend ? `Trend: ${humanizeSnakeCase(trend)}` : null,
    updated ? `Updated ${updated}` : null,
  ]);
}

function formatFirePerimeter(props: Properties): HoverContent | null {
  const title = stringField(props.incidentName) ?? "Fire perimeter";
  const acres = formatLocaleNumber(props.gisAcres, " acres");
  const contained = props.percentContained != null ? formatInteger(props.percentContained, "% contained") : null;
  const severity = stringField(props.severity);
  const cause = stringField(props.fireCause);
  const state = stringField(props.pooState);

  return buildContent(title, [
    acres ? `Size: ${acres}` : null,
    contained,
    severity ? `Severity: ${humanizeSnakeCase(severity)}` : null,
    cause ? `Cause: ${cause}` : null,
    state ? `State: ${state}` : null,
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
