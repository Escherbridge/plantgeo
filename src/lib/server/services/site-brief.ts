/**
 * Server-built site brief `site-brief/1` (CONTRACT C5): a pure, deterministic builder over
 * normalised integer inputs, canonically identical to agri `agent/site_brief.py`.
 * See src/lib/server/services/soil/AGENTS.md §site-brief for the parity rules.
 */
import type { LiteratureServerContext, LiteratureSiteFacts } from "./regional-evidence-tools";
import type { SoilProperties } from "./soilgrids";

export const SITE_BRIEF_VERSION = "site-brief/1" as const;

/** The brief's own kill switch (review M7): off by default, and off means no brief read at all. */
export const SITE_BRIEF_FLAG = "SITE_BRIEF_ENABLED";

/** Shared flag rule (CONTRACT C2): only the exact value `true`, after trimming whitespace, is on. */
export function readsFlagEnabled(value: string | undefined): boolean {
  return value?.trim() === "true";
}

/** Whether `SITE_BRIEF_ENABLED` is on; see soil/AGENTS.md §flags. */
export function siteBriefEnabled(): boolean {
  return readsFlagEnabled(process.env[SITE_BRIEF_FLAG]);
}

export const SOILGRIDS_PROPERTIES = [
  "phh2o", "soc", "nitrogen", "bdod", "cec", "ocd", "clay", "sand", "silt", "cfvo",
] as const;
export type SoilGridsProperty = (typeof SOILGRIDS_PROPERTIES)[number];

export const SOILGRIDS_DEPTHS = ["0-5cm", "5-15cm", "15-30cm"] as const;
export type SoilGridsDepth = (typeof SOILGRIDS_DEPTHS)[number];

/** ISRIC mapped integers (CONTRACT C1 units), per depth and property. */
export type SoilGridsMapped = Record<SoilGridsDepth, Record<SoilGridsProperty, number>>;

/** The one reason vocabulary every unavailable section uses (CONTRACT C5.6). */
export const SITE_BRIEF_REASONS = [
  "reads_disabled", "lane_never_written", "not_published", "outside_release_coverage",
  "not_bound_in_region", "no_cell_within_radius", "no_observation_within_radius",
  "serving_at_capacity", "timeout", "read_failed",
] as const;
export type SiteBriefReason = (typeof SITE_BRIEF_REASONS)[number];

export const USDM_CLASSES = ["none", "D0", "D1", "D2", "D3", "D4"] as const;
export type UsdmClass = (typeof USDM_CLASSES)[number];

export type BurnSeverity = "low" | "moderate" | "high";

export const SOILGRIDS_SOURCE = "SoilGrids v2.0";
export const SOILGRIDS_RESOLUTION_M = 250;
export const SOILGRIDS_CELL_DEGREES = 0.005;
export const SOILGRIDS_TOPSOIL_DEPTH = "0-30 cm (thickness-weighted)";
export const SOILGRIDS_TOPSOIL_LABEL = `${SOILGRIDS_SOURCE} ${SOILGRIDS_RESOLUTION_M} m model estimate, 0-30 cm (thickness-weighted)`;

export interface SectionUnavailable {
  state: "unavailable";
  reason: SiteBriefReason;
  /** Only on a soil `no_cell_within_radius`. */
  radius_m?: number;
}

/** CONTRACT C5.1: the pure builders' only input. Every number is an integer. */
export interface SiteBriefInputs {
  built_on: string;
  point: { longitude_e5: number; latitude_e5: number };
  soil: { state: "available"; release_id: string; distance_m: number; mapped: SoilGridsMapped } | SectionUnavailable;
  fire: {
    state: "available";
    latest_fire_day?: string | null;
    burn_severity?: BurnSeverity | null;
    detections_last_30_days: number;
    source: string;
  } | SectionUnavailable;
  drought: { state: "available"; usdm_class: UsdmClass; week_of: string } | SectionUnavailable;
  weather: {
    state: "available";
    observed_at: string;
    distance_m: number;
    temperature_tenths_c: number | null;
    relative_humidity_pct: number | null;
  } | SectionUnavailable;
  land_cover: {
    state: "available";
    class_name: string;
    class_code: number;
    fraction_permille: number;
    edition_year: number;
    release_day: string;
    cell_m: number;
  } | SectionUnavailable;
}

export type SoilDepthValues = {
  ph: number; soc_g_kg: number; nitrogen_g_kg: number; bdod_g_cm3: number; cec_cmolc_kg: number;
  ocd_kg_m3: number; clay_pct: number; sand_pct: number; silt_pct: number; cfvo_vol_pct: number;
};

export interface SoilTopsoil {
  ph: number;
  soc_pct: number;
  clay_pct: number;
  sand_pct: number;
  silt_pct: number;
  cfvo_vol_pct: number;
  bdod_g_cm3: number;
  texture_class: string;
  reaction_class: string;
  soc_band: string;
  label: string;
}

export interface SiteBriefSoil {
  state: "available";
  basis: "model_estimate";
  source: string;
  release_id: string;
  resolution_m: number;
  cell_deg: number;
  distance_m: number;
  label: string;
  depths: Record<SoilGridsDepth, SoilDepthValues>;
  topsoil_0_30cm: SoilTopsoil;
}

export interface SiteBriefFire {
  state: "available";
  basis: "measured";
  days_since_fire: number | null;
  latest_fire_day: string | null;
  burn_severity: BurnSeverity | null;
  detections_last_30_days: number;
  source: string;
  label: string;
}

export interface SiteBriefDrought {
  state: "available";
  basis: "classified";
  usdm_class: UsdmClass;
  week_of: string;
  label: string;
}

export interface SiteBriefWeather {
  state: "available";
  basis: "measured";
  observed_at: string;
  distance_m: number;
  temperature_c: number | null;
  relative_humidity_pct: number | null;
  label: string;
}

export interface SiteBriefLandCover {
  state: "available";
  basis: "classified_remote_sensing";
  source: string;
  class_name: string;
  class_code: number;
  fraction_pct: number;
  edition_year: number;
  release_day: string;
  cell_km: number;
  label: string;
}

export interface SiteBriefDescriptor {
  text: string;
  seed: string;
}

/** CONTRACT C5.3 output shape. */
export interface SiteBrief {
  brief_version: typeof SITE_BRIEF_VERSION;
  built_on: string;
  point: { longitude: number; latitude: number };
  soil: SiteBriefSoil | SectionUnavailable;
  fire: SiteBriefFire | SectionUnavailable;
  drought: SiteBriefDrought | SectionUnavailable;
  weather: SiteBriefWeather | SectionUnavailable;
  land_cover: SiteBriefLandCover | SectionUnavailable;
  descriptors: SiteBriefDescriptor[];
  literature_seed: string;
}

/** Output key, decimal places and physical unit per property (CONTRACT C1 unit table; divisor = 10^decimals). */
export const SOILGRIDS_PROPERTY_OUTPUT: Record<SoilGridsProperty, { key: keyof SoilDepthValues; decimals: 1 | 2; unit: string }> = {
  phh2o: { key: "ph", decimals: 1, unit: "pH (water)" },
  soc: { key: "soc_g_kg", decimals: 1, unit: "g/kg" },
  nitrogen: { key: "nitrogen_g_kg", decimals: 2, unit: "g/kg" },
  bdod: { key: "bdod_g_cm3", decimals: 2, unit: "g/cm3" },
  cec: { key: "cec_cmolc_kg", decimals: 1, unit: "cmol(c)/kg" },
  ocd: { key: "ocd_kg_m3", decimals: 1, unit: "kg/m3" },
  clay: { key: "clay_pct", decimals: 1, unit: "% (mass)" },
  sand: { key: "sand_pct", decimals: 1, unit: "% (mass)" },
  silt: { key: "silt_pct", decimals: 1, unit: "% (mass)" },
  cfvo: { key: "cfvo_vol_pct", decimals: 1, unit: "vol %" },
};

const USDM_NAMES: Record<UsdmClass, string> = {
  none: "no drought",
  D0: "abnormally dry",
  D1: "moderate drought",
  D2: "severe drought",
  D3: "extreme drought",
  D4: "exceptional drought",
};

const LITERATURE_SEED_MAX_CHARACTERS = 600;
const MILLISECONDS_PER_DAY = 86_400_000;

/** Round half up by integer division: `(2N + D) // (2D)` for N >= 0, D > 0 (CONTRACT C5.2). */
export function roundHalfUp(numerator: number, denominator: number): number {
  if (!Number.isSafeInteger(numerator) || !Number.isSafeInteger(denominator) || numerator < 0 || denominator <= 0) {
    throw new RangeError(`roundHalfUp needs a non-negative integer over a positive integer, got ${numerator}/${denominator}`);
  }
  const dividend = 2 * numerator + denominator;
  const divisor = 2 * denominator;
  return (dividend - (dividend % divisor)) / divisor;
}

/** Render a non-negative integer count of 10^-decimals units with fixed decimals, never via a float. */
export function formatFixedDecimals(integer: number, decimals: number): string {
  const scale = 10 ** decimals;
  const whole = (integer - (integer % scale)) / scale;
  if (decimals === 0) return String(whole);
  return `${whole}.${String(integer % scale).padStart(decimals, "0")}`;
}

function emit(integer: number, decimals: number): number {
  return integer / 10 ** decimals;
}

/** Thickness-weighted mean numerator over 0-30 cm: 5·v[0-5] + 10·v[5-15] + 15·v[15-30]. */
function thicknessWeighted(mapped: SoilGridsMapped, property: SoilGridsProperty): number {
  return 5 * mapped["0-5cm"][property] + 10 * mapped["5-15cm"][property] + 15 * mapped["15-30cm"][property];
}

/** USDA soil texture triangle on per-mille sand, silt and clay; first match wins (CONTRACT C5.2). */
export function usdaTextureClass(sand: number, silt: number, clay: number): string {
  const [s, t, c] = [sand, silt, clay];
  if (2 * t + 3 * c < 300) return "sand";
  if (2 * t + 3 * c >= 300 && t + 2 * c < 300) return "loamy sand";
  if ((c >= 70 && c < 200 && s > 520 && t + 2 * c >= 300) || (c < 70 && t < 500 && t + 2 * c >= 300)) return "sandy loam";
  if (c >= 70 && c < 270 && t >= 280 && t < 500 && s <= 520) return "loam";
  if ((t >= 500 && c >= 120 && c < 270) || (t >= 500 && t < 800 && c < 120)) return "silt loam";
  if (t >= 800 && c < 120) return "silt";
  if (c >= 200 && c < 350 && t < 280 && s > 450) return "sandy clay loam";
  if (c >= 270 && c < 400 && s > 200 && s <= 450) return "clay loam";
  if (c >= 270 && c < 400 && s <= 200) return "silty clay loam";
  if (c >= 350 && s > 450) return "sandy clay";
  if (c >= 400 && t >= 400) return "silty clay";
  if (c >= 400 && s <= 450 && t < 400) return "clay";
  return "unclassified";
}

/** Normalise texture tenths to 1000 and classify; `unclassified` when nothing was measured. */
export function textureClassFromTenths(clay: number, sand: number, silt: number): string {
  const total = clay + sand + silt;
  if (total <= 0) return "unclassified";
  const normalisedClay = roundHalfUp(1000 * clay, total);
  const normalisedSand = roundHalfUp(1000 * sand, total);
  const normalisedSilt = 1000 - normalisedClay - normalisedSand;
  return usdaTextureClass(normalisedSand, normalisedSilt, normalisedClay);
}

/** USDA Soil Survey Manual reaction class on pH tenths. */
export function reactionClass(phTenths: number): string {
  if (phTenths <= 34) return "ultra acid";
  if (phTenths <= 44) return "extremely acid";
  if (phTenths <= 50) return "very strongly acid";
  if (phTenths <= 55) return "strongly acid";
  if (phTenths <= 60) return "moderately acid";
  if (phTenths <= 65) return "slightly acid";
  if (phTenths <= 73) return "neutral";
  if (phTenths <= 78) return "slightly alkaline";
  if (phTenths <= 84) return "moderately alkaline";
  if (phTenths <= 90) return "strongly alkaline";
  return "very strongly alkaline";
}

/** SOC band on soc_pct tenths. */
export function socBand(socTenthsPercent: number): string {
  if (socTenthsPercent < 10) return "low organic carbon";
  if (socTenthsPercent < 20) return "moderate organic carbon";
  if (socTenthsPercent < 40) return "high organic carbon";
  return "very high organic carbon";
}

/** Physical per-depth values: exact `mapped / divisor` (CONTRACT C1). */
export function soilDepthValues(mapped: SoilGridsMapped): Record<SoilGridsDepth, SoilDepthValues> {
  const result = {} as Record<SoilGridsDepth, SoilDepthValues>;
  for (const depth of SOILGRIDS_DEPTHS) {
    const values = {} as SoilDepthValues;
    for (const property of SOILGRIDS_PROPERTIES) {
      const { key, decimals } = SOILGRIDS_PROPERTY_OUTPUT[property];
      values[key] = emit(mapped[depth][property], decimals);
    }
    result[depth] = values;
  }
  return result;
}

interface TopsoilIntegers {
  phTenths: number;
  socTenthsPercent: number;
  clayTenths: number;
  sandTenths: number;
  siltTenths: number;
  cfvoTenths: number;
  bdodHundredths: number;
}

function topsoilIntegers(mapped: SoilGridsMapped): TopsoilIntegers {
  return {
    phTenths: roundHalfUp(thicknessWeighted(mapped, "phh2o"), 30),
    // soc dg/kg -> % : dg/kg / 100 = %, so tenths of % = N / (30 * 10).
    socTenthsPercent: roundHalfUp(thicknessWeighted(mapped, "soc"), 300),
    clayTenths: roundHalfUp(thicknessWeighted(mapped, "clay"), 30),
    sandTenths: roundHalfUp(thicknessWeighted(mapped, "sand"), 30),
    siltTenths: roundHalfUp(thicknessWeighted(mapped, "silt"), 30),
    cfvoTenths: roundHalfUp(thicknessWeighted(mapped, "cfvo"), 30),
    bdodHundredths: roundHalfUp(thicknessWeighted(mapped, "bdod"), 30),
  };
}

/** Thickness-weighted 0-30 cm topsoil summary with texture, reaction and SOC classes. */
export function topsoilSummary(mapped: SoilGridsMapped): SoilTopsoil {
  const integers = topsoilIntegers(mapped);
  return {
    ph: emit(integers.phTenths, 1),
    soc_pct: emit(integers.socTenthsPercent, 1),
    clay_pct: emit(integers.clayTenths, 1),
    sand_pct: emit(integers.sandTenths, 1),
    silt_pct: emit(integers.siltTenths, 1),
    cfvo_vol_pct: emit(integers.cfvoTenths, 1),
    bdod_g_cm3: emit(integers.bdodHundredths, 2),
    texture_class: textureClassFromTenths(integers.clayTenths, integers.sandTenths, integers.siltTenths),
    reaction_class: reactionClass(integers.phTenths),
    soc_band: socBand(integers.socTenthsPercent),
    label: SOILGRIDS_TOPSOIL_LABEL,
  };
}

/** Whole days between two ISO calendar days (proleptic Gregorian, UTC). */
export function daysBetween(laterDay: string, earlierDay: string): number {
  const parse = (day: string) => Date.UTC(Number(day.slice(0, 4)), Number(day.slice(5, 7)) - 1, Number(day.slice(8, 10)));
  return Math.round((parse(laterDay) - parse(earlierDay)) / MILLISECONDS_PER_DAY);
}

/** Kilometres with one decimal from whole metres: round_half_up(m, 100) tenths of a km. */
function kilometresLabel(distanceMetres: number): string {
  return formatFixedDecimals(roundHalfUp(distanceMetres, 100), 1);
}

function soilSection(soil: SiteBriefInputs["soil"]): SiteBrief["soil"] {
  if (soil.state !== "available") return unavailable(soil);
  return {
    state: "available",
    basis: "model_estimate",
    source: SOILGRIDS_SOURCE,
    release_id: soil.release_id,
    resolution_m: SOILGRIDS_RESOLUTION_M,
    cell_deg: SOILGRIDS_CELL_DEGREES,
    distance_m: soil.distance_m,
    label: `${SOILGRIDS_SOURCE} ${SOILGRIDS_RESOLUTION_M} m model estimate, sampled at the centre of a ~500 m cell ${soil.distance_m} m from this point (release ${soil.release_id})`,
    depths: soilDepthValues(soil.mapped),
    topsoil_0_30cm: topsoilSummary(soil.mapped),
  };
}

function fireSection(fire: SiteBriefInputs["fire"], builtOn: string): SiteBrief["fire"] {
  if (fire.state !== "available") return unavailable(fire);
  const latestFireDay = fire.latest_fire_day ?? null;
  const burnSeverity = fire.burn_severity ?? null;
  return {
    state: "available",
    basis: "measured",
    days_since_fire: latestFireDay === null ? null : daysBetween(builtOn, latestFireDay),
    latest_fire_day: latestFireDay,
    burn_severity: burnSeverity,
    detections_last_30_days: fire.detections_last_30_days,
    source: fire.source,
    label: latestFireDay !== null
      ? `Mapped fire perimeter and MTBS burn severity, burned ${latestFireDay}`
      : `No mapped fire perimeter at this point; ${fire.detections_last_30_days} satellite fire detections nearby in the last 30 days`,
  };
}

function droughtSection(drought: SiteBriefInputs["drought"]): SiteBrief["drought"] {
  if (drought.state !== "available") return unavailable(drought);
  return {
    state: "available",
    basis: "classified",
    usdm_class: drought.usdm_class,
    week_of: drought.week_of,
    label: `US Drought Monitor class, week of ${drought.week_of}`,
  };
}

function weatherSection(weather: SiteBriefInputs["weather"]): SiteBrief["weather"] {
  if (weather.state !== "available") return unavailable(weather);
  return {
    state: "available",
    basis: "measured",
    observed_at: weather.observed_at,
    distance_m: weather.distance_m,
    temperature_c: weather.temperature_tenths_c === null ? null : weather.temperature_tenths_c / 10,
    relative_humidity_pct: weather.relative_humidity_pct,
    label: `Nearest weather-station observation, ${weather.observed_at}, ${kilometresLabel(weather.distance_m)} km away`,
  };
}

function landCoverSection(landCover: SiteBriefInputs["land_cover"]): SiteBrief["land_cover"] {
  if (landCover.state !== "available") return unavailable(landCover);
  const fractionPct = roundHalfUp(landCover.fraction_permille, 10);
  const cellKm = landCover.cell_m / 1000;
  return {
    state: "available",
    basis: "classified_remote_sensing",
    source: "USDA CDL",
    class_name: landCover.class_name,
    class_code: landCover.class_code,
    fraction_pct: fractionPct,
    edition_year: landCover.edition_year,
    release_day: landCover.release_day,
    cell_km: cellKm,
    label: `USDA CDL ${landCover.edition_year} (released ${landCover.release_day}), dominant class of a ${cellKm} km cell (${fractionPct}%)`,
  };
}

function unavailable(section: SectionUnavailable): SectionUnavailable {
  return {
    state: "unavailable",
    reason: section.reason,
    ...(section.reason === "no_cell_within_radius" && section.radius_m !== undefined ? { radius_m: section.radius_m } : {}),
  };
}

/** Descriptors in pinned order: soil reaction+texture, SOC band, fire, drought, land cover. */
function descriptorsFor(inputs: SiteBriefInputs): SiteBriefDescriptor[] {
  const descriptors: SiteBriefDescriptor[] = [];
  if (inputs.soil.state === "available") {
    const integers = topsoilIntegers(inputs.soil.mapped);
    const reaction = reactionClass(integers.phTenths);
    const texture = textureClassFromTenths(integers.clayTenths, integers.sandTenths, integers.siltTenths);
    const soilPhrase = texture === "unclassified" ? `${reaction} topsoil` : `${reaction} ${texture} topsoil`;
    descriptors.push({
      text: `${soilPhrase} (pH ${formatFixedDecimals(integers.phTenths, 1)}, SoilGrids model estimate 0-30 cm)`,
      seed: soilPhrase,
    });
    const band = socBand(integers.socTenthsPercent);
    descriptors.push({
      text: `${band} topsoil (${formatFixedDecimals(integers.socTenthsPercent, 1)}% SOC, SoilGrids model estimate 0-30 cm)`,
      seed: band,
    });
  }
  if (inputs.fire.state === "available") {
    const day = inputs.fire.latest_fire_day ?? null;
    const severity = inputs.fire.burn_severity ?? null;
    if (day !== null) {
      const severityText = severity === null ? "" : `, ${severity} severity`;
      descriptors.push({
        text: `burned ${day}${severityText} (mapped perimeter and MTBS, measured)`,
        seed: `burned ${day.slice(0, 4)}${severityText}`,
      });
    } else if (inputs.fire.detections_last_30_days > 0) {
      descriptors.push({
        text: `${inputs.fire.detections_last_30_days} satellite fire detections nearby in the last 30 days (measured)`,
        seed: "recent fire activity",
      });
    }
  }
  if (inputs.drought.state === "available") {
    const name = USDM_NAMES[inputs.drought.usdm_class];
    const classText = inputs.drought.usdm_class === "none" ? name : `${name} ${inputs.drought.usdm_class}`;
    descriptors.push({
      text: `${classText} (US Drought Monitor, week of ${inputs.drought.week_of})`,
      seed: name,
    });
  }
  if (inputs.land_cover.state === "available") {
    const className = inputs.land_cover.class_name.toLowerCase();
    const cellKm = inputs.land_cover.cell_m / 1000;
    descriptors.push({
      text: `${className}, ${roundHalfUp(inputs.land_cover.fraction_permille, 10)}% of a ${cellKm} km cell (USDA CDL ${inputs.land_cover.edition_year} classified imagery)`,
      seed: className,
    });
  }
  return descriptors;
}

/** Join descriptor seeds with "; " and cut at a word boundary to 600 characters (CONTRACT C5.4). */
export function literatureSeed(descriptors: readonly SiteBriefDescriptor[]): string {
  const joined = descriptors.map((descriptor) => descriptor.seed).filter((seed) => seed.length > 0).join("; ");
  if (joined.length <= LITERATURE_SEED_MAX_CHARACTERS) return joined;
  const cut = joined.slice(0, LITERATURE_SEED_MAX_CHARACTERS);
  const boundary = cut.lastIndexOf(" ");
  return boundary > 0 ? cut.slice(0, boundary) : cut;
}

/** Build `site-brief/1` from normalised inputs; pure and deterministic. */
export function buildSiteBrief(inputs: SiteBriefInputs): SiteBrief {
  const descriptors = descriptorsFor(inputs);
  return {
    brief_version: SITE_BRIEF_VERSION,
    built_on: inputs.built_on,
    point: { longitude: inputs.point.longitude_e5 / 1e5, latitude: inputs.point.latitude_e5 / 1e5 },
    soil: soilSection(inputs.soil),
    fire: fireSection(inputs.fire, inputs.built_on),
    drought: droughtSection(inputs.drought),
    weather: weatherSection(inputs.weather),
    land_cover: landCoverSection(inputs.land_cover),
    descriptors,
    literature_seed: literatureSeed(descriptors),
  };
}

/** Canonical JSON (CONTRACT C5.5): sorted keys, compact, integral floats as integer literals. */
export function canonicalJson(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new RangeError("canonical JSON has no representation for a non-finite number");
    if (Number.isInteger(value) && Math.abs(value) < 2 ** 53) return String(value === 0 ? 0 : value);
    return String(value);
  }
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    const keys = Object.keys(record).filter((key) => record[key] !== undefined).sort();
    return `{${keys.map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(",")}}`;
  }
  throw new TypeError(`canonical JSON cannot encode a ${typeof value}`);
}

/** Basis of a server-read site fact (CONTRACT C3). `survey_estimate` is reserved; no producer. */
export type FactBasis = "measured" | "model_estimate" | "classified" | "classified_remote_sensing" | "survey_estimate";

/** CONTRACT C3 `site_facts_provenance[key]`. */
export interface FactProvenance {
  basis: FactBasis;
  source: string;
  label: string;
  release_id?: string;
  depth?: string;
  resolution_m?: number;
  distance_m?: number;
}

const MAX_PROVENANCE_LABEL_CHARACTERS = 200;
const MAX_PROVENANCE_SOURCE_CHARACTERS = 80;
const MAX_DAYS_SINCE_FIRE = 36_500;
const SOIL_FACT_KEYS = ["soil_ph", "soil_organic_carbon_pct", "sand_pct", "clay_pct"] as const;
const FIRE_FACT_KEYS = ["days_since_fire", "burn_severity"] as const;

/** One measured read behind a site fact: the shape of `regional-analysis-workflow.ts::SiteFactObservation`. */
export interface SiteFactReading {
  fact: string;
  value: number | string;
  readId: string;
}

/** What the server read this request, so every `site_facts` key can be given its provenance (review M1). */
export interface SiteFactSources {
  /** The request's measured-read ledger (`RegionalAnalysisWorkflow.siteFactObservations`). */
  observations?: readonly SiteFactReading[];
  /** Evidence source of each read id, from the evidence audit. */
  sourceByReadId?: ReadonlyMap<string, string>;
  /** The payload's one SoilGrids read; its 0-5 cm pH and SOC reach `site_facts` through the workflow. */
  soilProperties?: SoilProperties | null;
}

/** The workflow's own conversion of the payload's 0-5 cm soil values (`payloadSoilObservations`). */
function payloadSoilFactValue(key: string, soil: SoilProperties): number | null {
  if (key === "soil_ph") return Math.round(soil.ph * 100) / 100;
  if (key === "soil_organic_carbon_pct") return Math.round((soil.organicCarbon / 10) * 100) / 100;
  return null;
}

/** Model-estimate provenance for a site fact that came from the payload's 0-5 cm SoilGrids read. */
function surfaceSoilProvenance(soil: SoilProperties): FactProvenance {
  return provenance({
    basis: "model_estimate",
    source: SOILGRIDS_SOURCE,
    ...(soil.releaseId !== undefined ? { release_id: soil.releaseId } : {}),
    depth: "0-5 cm",
    resolution_m: SOILGRIDS_RESOLUTION_M,
    ...(soil.distanceM !== undefined ? { distance_m: soil.distanceM } : {}),
    label: soil.label ?? `${SOILGRIDS_SOURCE} ${SOILGRIDS_RESOLUTION_M} m model estimate, 0-5 cm`,
  });
}

/** The reads that produced one fact's value; fire facts are derived from their fire-day reads. */
function producingReads(key: string, value: number | string, observations: readonly SiteFactReading[]): SiteFactReading[] {
  if (key === "days_since_fire") return observations.filter((reading) => reading.fact === "fire_day");
  if (key === "burn_severity") {
    return observations.filter((reading) => reading.fact === "burn_severity_by_day" && String(reading.value).endsWith(`|${value}`));
  }
  return observations.filter((reading) => reading.fact === key && reading.value === value);
}

/** Provenance from the measured reads that produced a value, or null when no read produced it. */
function readProvenance(key: string, value: number | string, sources: SiteFactSources): FactProvenance | null {
  const reads = producingReads(key, value, sources.observations ?? []);
  if (reads.length === 0) return null;
  const readIds = [...new Set(reads.map((reading) => reading.readId))];
  const sourceNames = [...new Set(readIds.map((readId) => sources.sourceByReadId?.get(readId)).filter((name): name is string => !!name))];
  const source = sourceNames.length > 0 ? sourceNames.join(" + ") : "warehouse read";
  const landCover = key === "land_cover";
  return provenance({
    basis: landCover ? "classified_remote_sensing" : "measured",
    source,
    label: `${landCover ? "Land cover class from classified imagery" : "Measured value"}, read by the server this request from ${source} (read ${readIds.join(", ")})`,
  });
}

function provenance(entry: FactProvenance): FactProvenance {
  return {
    ...entry,
    source: entry.source.slice(0, MAX_PROVENANCE_SOURCE_CHARACTERS),
    label: entry.label.slice(0, MAX_PROVENANCE_LABEL_CHARACTERS),
  };
}

/** Strip control characters and bound to 600 characters; undefined when nothing is left. */
export function siteBriefQuery(brief: SiteBrief): string | undefined {
  const stripped = brief.literature_seed.replace(/[\u0000-\u001F\u007F-\u009F]+/g, " ").trim();
  return stripped.length > 0 ? stripped.slice(0, LITERATURE_SEED_MAX_CHARACTERS) : undefined;
}

/**
 * Overlay the brief on the literature server context (CONTRACT C3): soil keys from the topsoil
 * summary, land cover from CDL, fire keys from the fire section, `site_brief_query`, and a
 * provenance entry for EVERY remaining key, taken from the reads that produced it (review M1):
 * a payload SoilGrids value is a 0-5 cm model estimate, any other read is measured. A key no read
 * produced keeps no entry, and agri drops it. With neither a brief nor a soil read the context
 * passes through untouched (the wave-2 shape). `user_question` is never touched.
 */
export function withSiteBrief(
  serverContext: LiteratureServerContext,
  brief: SiteBrief | null,
  sources: SiteFactSources = {},
): LiteratureServerContext {
  const soilProperties = sources.soilProperties ?? null;
  if (brief === null && soilProperties === null) return serverContext;
  const facts: LiteratureSiteFacts = { ...(serverContext.site_facts ?? {}) };
  const provenanceByKey: Record<string, FactProvenance> = {};

  if (brief !== null && brief.soil.state === "available") {
    const soil = brief.soil;
    const topsoil = soil.topsoil_0_30cm;
    facts.soil_ph = topsoil.ph;
    facts.soil_organic_carbon_pct = topsoil.soc_pct;
    facts.sand_pct = topsoil.sand_pct;
    facts.clay_pct = topsoil.clay_pct;
    for (const key of SOIL_FACT_KEYS) {
      provenanceByKey[key] = provenance({
        basis: "model_estimate",
        source: SOILGRIDS_SOURCE,
        release_id: soil.release_id,
        depth: SOILGRIDS_TOPSOIL_DEPTH,
        resolution_m: SOILGRIDS_RESOLUTION_M,
        distance_m: soil.distance_m,
        label: `${SOILGRIDS_TOPSOIL_LABEL}, cell centre ${soil.distance_m} m away`,
      });
    }
  }

  if (brief !== null && brief.fire.state === "available" && brief.fire.latest_fire_day !== null) {
    const fire = brief.fire;
    if (fire.days_since_fire !== null && fire.days_since_fire >= 0 && fire.days_since_fire <= MAX_DAYS_SINCE_FIRE) {
      facts.days_since_fire = fire.days_since_fire;
    } else {
      delete facts.days_since_fire;
    }
    // Same-fire pairing: a severity is trusted only for the fire that sets days_since_fire.
    if (fire.burn_severity !== null) facts.burn_severity = fire.burn_severity;
    else delete facts.burn_severity;
    for (const key of FIRE_FACT_KEYS) {
      if (facts[key] !== undefined) provenanceByKey[key] = provenance({ basis: "measured", source: fire.source, label: fire.label });
    }
  }

  if (brief !== null && brief.land_cover.state === "available") {
    facts.land_cover = brief.land_cover.class_name;
    provenanceByKey.land_cover = provenance({
      basis: "classified_remote_sensing", source: brief.land_cover.source, label: brief.land_cover.label,
    });
  }

  // Every remaining key: a payload SoilGrids value is never labelled measured, whatever else agrees with it.
  for (const [key, value] of Object.entries(facts) as [string, number | string][]) {
    if (provenanceByKey[key] !== undefined) continue;
    const fromPayloadSoil = soilProperties !== null && typeof value === "number"
      && payloadSoilFactValue(key, soilProperties) === value;
    const entry = fromPayloadSoil ? surfaceSoilProvenance(soilProperties) : readProvenance(key, value, sources);
    if (entry !== null) provenanceByKey[key] = entry;
  }

  const query = brief === null ? undefined : siteBriefQuery(brief);
  const hasFacts = Object.keys(facts).length > 0;
  const keptProvenance = Object.fromEntries(Object.entries(provenanceByKey)
    .filter(([key]) => (facts as Record<string, unknown>)[key] !== undefined)) as LiteratureServerContext["site_facts_provenance"];
  return {
    ...(serverContext.user_question !== undefined ? { user_question: serverContext.user_question } : {}),
    ...(serverContext.point !== undefined ? { point: serverContext.point } : {}),
    ...(hasFacts ? { site_facts: facts, site_facts_provenance: keptProvenance } : {}),
    ...(query !== undefined ? { site_brief_query: query } : {}),
  };
}

/** One line per section for the model, each with its basis label; unavailable sections say why. */
export function describeSiteBrief(brief: SiteBrief): string {
  const section = (name: string, value: { state: string; label?: string; reason?: string; radius_m?: number }) =>
    value.state === "available"
      ? `- ${name}: ${value.label}`
      : `- ${name}: not available (${value.reason}${value.radius_m !== undefined ? `, no SoilGrids cell centre within ${value.radius_m} m` : ""}). This is a gap in what the server could read, not a condition of the site.`;
  const lines = [
    section("soil", brief.soil),
    ...(brief.soil.state === "available" ? [`  topsoil: ${brief.soil.topsoil_0_30cm.label}`] : []),
    section("fire", brief.fire),
    section("drought", brief.drought),
    section("weather", brief.weather),
    section("land cover", brief.land_cover),
  ];
  const descriptors = brief.descriptors.length > 0
    ? brief.descriptors.map((descriptor) => `- ${descriptor.text}`).join("\n")
    : "- (none: no section of the brief could be read)";
  return `Built ${brief.built_on} by the server from its own reads at this point.\nSections and their basis labels:\n${lines.join("\n")}\nSite descriptors:\n${descriptors}\nFull brief (JSON):\n${JSON.stringify(brief)}`;
}
