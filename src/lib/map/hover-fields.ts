// Pure field-selection + formatting for the shared map hover tooltip.
// No React, no maplibre imports (types only) -- keeps this testable in isolation.

import {
  fireCellCaptionText,
  fireDetectionCellLines,
  FIRE_CELL_CAPTION_TITLE,
} from "@/lib/map/fire-cell-caption";
import {
  formatAbsoluteDate,
  formatTimestampWithRelative,
  toIsoTimestamp,
} from "@/lib/map/time-format";

/** Style layer ids the shared hover manager queries via queryRenderedFeatures. */
export const HOVERABLE_LAYER_IDS: string[] = [
  "published-fire-circles",
  "water-gauges-circle",
  "groundwater-wells-circle",
  "sensors",
  "fire-perimeters",
  "evacuation-zones",
  "interventions",
  "interventions-points",
  "watersheds-fill",
  "soil-survey-fill",
  "soil-survey-summary",
  "weather-temperature",
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

/**
 * One published fire-detection CELL -- see `lib/environmental/parquet-fire-presentation.ts`.
 *
 * The lines themselves come from `lib/map/fire-cell-caption.ts`, which `FireLayer`'s click popup
 * reads too: two hand-written copies of these six fields drifted into two different renderings of
 * one cell ("not reported" against "Not reported", `1234.6 MW` against `1,234.6 MW`). This
 * formatter flattens each line and keeps the file-wide rule that a feature carrying none of the
 * fields yields no tooltip rather than a shell of empty labels.
 */
function formatFireDetection(props: Properties): HoverContent | null {
  return buildContent(
    FIRE_CELL_CAPTION_TITLE,
    fireDetectionCellLines(props).map(fireCellCaptionText)
  );
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

function formatSensorStation(props: Properties): HoverContent | null {
  const title = stringField(props.station_name) ?? "Weather station";
  const network = stringField(props.network);
  const sensorId = stringField(props.sensor_id);
  const observed = formatTimestampWithRelative(toIsoTimestamp(props.observed_at));

  return buildContent(title, [
    network ? `Network: ${network}` : null,
    sensorId ? `Station ID: ${sensorId}` : null,
    observed ? `Observed: ${observed}` : null,
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

function formatEvacuationZone(props: Properties): HoverContent | null {
  const title =
    stringField(props.evacuation_area_name) ?? stringField(props.fire_name) ?? "Evacuation zone";
  const level = stringField(props.evacuation_level_label);
  const county = stringField(props.county);
  const structures =
    props.structures_within != null ? formatInteger(props.structures_within, "") : null;
  const population =
    props.population_within != null ? formatInteger(props.population_within, "") : null;

  return buildContent(title, [
    level ? `Level: ${level}` : null,
    county ? `County: ${county}` : null,
    structures ? `Structures within: ${structures}` : null,
    population ? `Population within: ${population}` : null,
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

/**
 * USGS NHD+ HR watershed boundaries. Field names are the WBDHU12 layer's own, as its
 * ArcGIS `f=geojson` output emits them (lowercase attribute names, not the title-case
 * aliases the service catalog displays). A feature carrying none of them yields no
 * tooltip rather than a shell of empty labels.
 */
function formatWatershed(props: Properties): HoverContent | null {
  // Which rung of the HUC hierarchy this feature was drawn from -- 12 is a published basin,
  // anything coarser is the union of its members, built by 0023_watershed_zoom_generalization.
  // The level is read rather than assumed so a HUC6 code can never be presented as a HUC12:
  // a rollup carries no single name, tohuc, hutype or state list, and borrowing a member's
  // would describe the part as the whole.
  const level = toFiniteNumber(props.huc_level) ?? 12;
  const code = stringField(props.huc) ?? stringField(props.huc12);
  const area = formatFixed(props.areasqkm, 1, " km²");

  if (level < 12) {
    const memberBasins = toFiniteNumber(props.basin_count);
    return buildContent(code ? `HUC${level} ${code}` : `HUC${level} watershed`, [
      area ? `Area: ${area}` : null,
      memberBasins === null
        ? null
        : `${memberBasins.toLocaleString()} HUC12 basin${memberBasins === 1 ? "" : "s"}`,
      "Zoom in for individual basins",
    ]);
  }

  const title = stringField(props.name) ?? "Watershed";
  const drainsTo = stringField(props.tohuc);
  const states = stringField(props.states);

  return buildContent(title, [
    code ? `HUC12: ${code}` : null,
    area ? `Area: ${area}` : null,
    states ? `States: ${states}` : null,
    drainsTo ? `Drains to: ${drainsTo}` : null,
  ]);
}

/**
 * USDA SSURGO map units. Every field here is one usda-soil.ts constructs itself while
 * normalizing the Soil Data Access result table (there is no WFS in this path; see
 * `src/lib/server/AGENTS.md` §soil-survey), so the names cannot drift with SDA's casing.
 * `drainageClass` arrives hyphenated ("well-drained"); the humanizer splits on
 * underscores, so it is converted before formatting rather than shown raw.
 *
 * Zoomed out, `getSoilSurvey` draws drainage-class averages instead of individual map
 * units (`aggregated: true`, §soil-survey-zoom in usda-soil.ts) -- those carry no
 * mukey/muname/soilSeries at all, and must be captioned as an average, not a surveyed
 * unit, or this tooltip would be the one place the honesty that field-naming enforces
 * everywhere else in this formatter quietly breaks.
 *
 * Zoomed out FURTHER still, past what the polygon-union budget covers, it draws one point
 * per lattice cell (`summary: true`). That is a third thing again -- neither a surveyed unit
 * nor a merged shape, but a count over a square of ground whose position is the square's
 * centre and not any delineation's -- so it gets its own caption rather than borrowing the
 * average's, which would claim a boundary was drawn where none was.
 */
function formatSoilSurvey(props: Properties): HoverContent | null {
  if (props.summary === true) {
    const drainage = stringField(props.drainageClass);
    const mapUnitCount = typeof props.mapUnitCount === "number" ? props.mapUnitCount : null;
    const hydricFraction =
      typeof props.hydricFraction === "number" ? props.hydricFraction : null;
    const cellDegrees = typeof props.cellDegrees === "number" ? props.cellDegrees : null;
    return buildContent("Soil survey coverage", [
      mapUnitCount === null
        ? null
        : `${mapUnitCount.toLocaleString()} map unit${mapUnitCount === 1 ? "" : "s"} surveyed in this cell`,
      drainage
        ? `Most common drainage: ${humanizeSnakeCase(drainage.replace(/-/g, "_"))}`
        : null,
      hydricFraction === null ? null : `Hydric share: ${Math.round(hydricFraction * 100)}%`,
      cellDegrees === null ? null : `Cell: ${cellDegrees}° square`,
    ]);
  }

  if (props.aggregated === true) {
    const drainage = stringField(props.drainageClass);
    const mapUnitCount =
      typeof props.mapUnitCount === "number" ? props.mapUnitCount : null;
    const hydricFraction =
      typeof props.hydricFraction === "number" ? props.hydricFraction : null;
    return buildContent("Soil drainage average", [
      drainage ? `Dominant drainage: ${humanizeSnakeCase(drainage.replace(/-/g, "_"))}` : null,
      mapUnitCount === null
        ? null
        : `Built from ${mapUnitCount} real map unit${mapUnitCount === 1 ? "" : "s"}`,
      hydricFraction === null ? null : `Hydric share: ${Math.round(hydricFraction * 100)}%`,
    ]);
  }

  const title = stringField(props.muname) ?? "Soil map unit";
  const series = stringField(props.soilSeries);
  const drainage = stringField(props.drainageClass);
  const capability = stringField(props.landCapabilityClass);
  const mapUnitKey = stringField(props.mukey);
  // Only an actual boolean is reportable: an absent hydric rating is not a "No".
  const hydric = typeof props.hydric === "boolean" ? props.hydric : null;

  return buildContent(title, [
    series ? `Series: ${series}` : null,
    drainage ? `Drainage: ${humanizeSnakeCase(drainage.replace(/-/g, "_"))}` : null,
    capability ? `Land capability: ${capability}` : null,
    hydric === null ? null : `Hydric: ${hydric ? "Yes" : "No"}`,
    mapUnitKey ? `Map unit: ${mapUnitKey}` : null,
  ]);
}

/**
 * One published Open-Meteo observation, hovered on its temperature dot rather than on its
 * wind arrow: `weather-wind` is a `text-field` symbol, whose hit area is the glyph run and
 * whose placement collides away at density, so the circle under it is the only shape here
 * that can be reliably pointed at.
 *
 * The feed carries no station identity -- it is a grid sample, not a named site -- so the
 * title is generic rather than inventing one. Units are the ones measured: m/s (weather.ts
 * asks Open-Meteo for `wind_speed_unit=ms`), °C (`temperature_2m`, whose default unit is
 * Celsius), and percent relative humidity. Every field is optional because the layer now
 * draws a station that measured only some of them.
 */
function formatWeatherObservation(props: Properties): HoverContent | null {
  const windSpeed = formatFixed(props.windSpeed, 1, " m/s");
  const windDirection = formatInteger(props.windDirection, "°");
  const temperature = formatFixed(props.temperature, 1, " °C");
  const humidity = formatInteger(props.humidity, "%");
  const observed = formatTimestampWithRelative(toIsoTimestamp(props.observedAt));

  return buildContent("Weather observation", [
    temperature ? `Temperature: ${temperature}` : null,
    windSpeed
      ? `Wind: ${windSpeed}${windDirection ? ` from ${windDirection}` : ""}`
      : null,
    humidity ? `Humidity: ${humidity}` : null,
    observed ? `Observed: ${observed}` : null,
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
  sensors: formatSensorStation,
  "fire-perimeters": formatFirePerimeter,
  "evacuation-zones": formatEvacuationZone,
  interventions: formatIntervention,
  "interventions-points": formatIntervention,
  "watersheds-fill": formatWatershed,
  "soil-survey-fill": formatSoilSurvey,
  // The same formatter: the summary dots are the same collection's features, told apart by
  // their own `summary` flag rather than by which layer drew them.
  "soil-survey-summary": formatSoilSurvey,
  "weather-temperature": formatWeatherObservation,
  "osm-roads": formatRoad,
  "osm-waterways": formatWaterway,
};

/** Per-layer field selection + unit formatting for the hover tooltip. Null when nothing to show. */
export function formatHoverContent(layerId: string, properties: Properties): HoverContent | null {
  const formatter = FORMATTERS[layerId];
  if (!formatter) return null;
  return formatter(properties ?? {});
}
