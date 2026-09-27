import { z } from "zod";
import { DATA_INTERVENTION_TYPES } from "@/lib/environmental/intervention";
import { SOIL_PROPERTY_LABELS, SOIL_RASTER_PROPERTIES, soilRasterToggleId } from "@/lib/map/soil-raster";

/** Environmental source targets; see AGENTS.md for catalogue parity and provenance. */
export const DATA_INTERVENTION_LANES = [
  { id: "fire-detections", label: "Fire detections" },
  { id: "fire-perimeters", label: "Fire perimeters" },
  { id: "burn-severity", label: "Burn severity" },
  { id: "fire-risk", label: "Fire risk" },
  { id: "water-gauges", label: "Water gauges" },
  { id: "watersheds", label: "Watersheds" },
  { id: "drought", label: "Drought" },
  { id: "weather-observations", label: "Weather observations" },
  { id: "weather-forecast", label: "Weather forecasts" },
  { id: "sensors", label: "Sensors" },
  { id: "vegetation", label: "Vegetation" },
  { id: "crop-cover", label: "Crop cover" },
  { id: "soil-survey", label: "Soil survey" },
  { id: "evacuation-zones", label: "Evacuation zones" },
  { id: "soil-field-moisture-0-7cm", label: "Soil moisture (0–7 cm)" },
  { id: "soil-field-moisture-7-28cm", label: "Soil moisture (7–28 cm)" },
  { id: "soil-field-moisture-28-100cm", label: "Soil moisture (28–100 cm)" },
  { id: "soil-temperature-0-to-7cm", label: "Soil temperature (0–7 cm)" },
  { id: "soil-temperature-7-to-28cm", label: "Soil temperature (7–28 cm)" },
  { id: "soil-temperature-28-to-100cm", label: "Soil temperature (28–100 cm)" },
  { id: "soil-temperature-100-to-255cm", label: "Soil temperature (100–255 cm)" },
  { id: "soil-field-vpd", label: "Vapor pressure deficit" },
  { id: "climate-field-air-temperature-mean", label: "Air temperature (mean)" },
  { id: "climate-field-air-temperature-max", label: "Air temperature (maximum)" },
  { id: "climate-field-air-temperature-min", label: "Air temperature (minimum)" },
  { id: "climate-field-dew-point", label: "Dew point" },
  { id: "climate-field-precipitation", label: "Precipitation" },
  { id: "climate-field-relative-humidity", label: "Relative humidity" },
  { id: "climate-field-shortwave-radiation", label: "Shortwave radiation" },
  { id: "climate-field-wind-speed", label: "Wind speed" },
  { id: "soil-wetness-surface", label: "Soil wetness (surface)" },
  { id: "soil-wetness-root-zone", label: "Soil wetness (root zone)" },
  { id: "soil-wetness-profile", label: "Soil wetness (profile)" },
  { id: "botanical-occurrences", label: "Botanical occurrences" },
  { id: "botanical-species-profile", label: "Botanical species profiles" },
  { id: "land-context", label: "Land context" },
  { id: "land-context-boundaries", label: "Land context boundaries" },
  ...SOIL_RASTER_PROPERTIES.map((property) => ({
    id: soilRasterToggleId(property),
    label: `SoilGrids ${SOIL_PROPERTY_LABELS[property]}`,
  })),
] as const;

const DATA_LANE_IDS = new Set<string>(DATA_INTERVENTION_LANES.map(({ id }) => id));

export const DATA_PROVENANCE_LABELS = {
  community: "Community collected data",
  verified_source: "Verified source data",
} as const;

/** Recognizes data activities without accepting arbitrary intervention strings. */
export function isDataInterventionType(type: unknown): type is (typeof DATA_INTERVENTION_TYPES)[number] {
  return DATA_INTERVENTION_TYPES.some((candidate) => candidate === type);
}

function isObservedDay(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000-")) return false;
  const parsed = new Date(`${value}T00:00:00.000Z`);
  return Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value &&
    value <= new Date().toISOString().slice(0, 10);
}

const EvidenceUrlSchema = z.string().trim().max(2_048).url().refine(
  (value) => /^https?:\/\//i.test(value),
  "Use an HTTP or HTTPS dataset or evidence link",
);

export const DataInterventionDetailsSchema = z.object({
  lane: z.string().refine((value) => DATA_LANE_IDS.has(value), "Select an environmental data lane"),
  collectionMethod: z.string().trim().min(1, "Describe how the data is collected").max(2_000),
  observedOn: z.string().refine(isObservedDay, "Use a real observation date on or before today").optional(),
  dataUrl: EvidenceUrlSchema.optional(),
}).strict();

export type DataInterventionDetails = z.infer<typeof DataInterventionDetailsSchema>;

/** Validates the additional requirements of a submitted dataset. */
export function validateDataInterventionDetails(type: unknown, details: unknown): string | null {
  if (!isDataInterventionType(type)) {
    return details === undefined ? null : "Data details are only allowed for data interventions";
  }
  if (details === undefined) return "Provide data collection details";
  const parsed = DataInterventionDetailsSchema.safeParse(details);
  if (!parsed.success) return parsed.error.issues[0]?.message ?? "Invalid data collection details";
  if (type === "data_submission" && !parsed.data.observedOn) return "Provide the observation date";
  if (type === "data_submission" && !parsed.data.dataUrl) return "Provide a dataset or evidence link";
  return null;
}

/** Reads only complete, validated details from a feature's properties. */
export function readDataInterventionDetails(value: unknown): DataInterventionDetails | null {
  const parsed = DataInterventionDetailsSchema.safeParse(value);
  return parsed.success ? parsed.data : null;
}
