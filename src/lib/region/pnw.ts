import type { Region } from "@/lib/region/region";

/**
 * The Pacific Northwest pilot manifest.
 *
 * `satisfies Region` checks these literal values against the Zod-inferred shape without widening
 * them to it, so `getRegion()` callers keep the narrower literal types where TypeScript can infer
 * them. Values must match
 * `services/agri-data-service/src/agri_data_service/foundation/region/pnw.json` field for field;
 * `src/__tests__/region/manifest-parity.test.ts` diffs the two, and `src/lib/region/AGENTS.md`
 * explains why `envelope` and `subEnvelopes` carry three different-looking PNW boxes on purpose.
 */
/**
 * The pilot's admin codes as a literal tuple, declared once so both the union type
 * (`RegionAdminCode`) and the two-letter state tuple the Zod/Drizzle enums need are DERIVED from
 * this manifest rather than hand-copied beside it (`engineering-principles.md` §1).
 * `regionSchema` itself only asserts `z.array(z.string())`, because a future region may bind codes
 * this deployment never does, so the literal types live here with the values.
 */
export const PNW_ADMIN_CODES = ["US-WA", "US-OR", "US-ID"] as const;

export const PNW = {
  slug: "pnw",
  displayName: "Pacific Northwest",
  envelope: { west: -126, south: 41, east: -110, north: 50 },
  defaultCameraEnvelope: { west: -125, south: 42, east: -111, north: 49 },
  subEnvelopes: {
    burn_severity: { west: -125, south: 42, east: -111, north: 49 },
    botanical_seed: { west: -125, south: 41, east: -110, north: 50 },
  },
  crs: 4326,
  latticePitchDegrees: 0.01,
  latticeOriginRule: "floor_to_cell_origin",
  timezone: "America/Los_Angeles",
  isoCountryCodes: ["US"],
  adminCodes: [...PNW_ADMIN_CODES],
  enabledLayers: [
    { layerSlug: "soil-survey", sourceSlug: "ssurgo", coverage: "regional" },
    { layerSlug: "fire-detections", sourceSlug: "firms", coverage: "global" },
    { layerSlug: "vegetation", sourceSlug: "sentinel2_ndvi", coverage: "global" },
    { layerSlug: "burn-severity", sourceSlug: "mtbs", coverage: "regional" },
    { layerSlug: "evacuation-zones", sourceSlug: "oregon_oem_arcgis", coverage: "regional" },
    { layerSlug: "fire-perimeters", sourceSlug: "wfigs", coverage: "regional" },
    { layerSlug: "water-gauges", sourceSlug: "usgs_nwis", coverage: "regional" },
    { layerSlug: "sensors", sourceSlug: "noaa_nws", coverage: "regional" },
    { layerSlug: "weather-observations", sourceSlug: "open_meteo", coverage: "global" },
    { layerSlug: "watersheds", sourceSlug: "hydrosheds", coverage: "global" },
    { layerSlug: "drought", sourceSlug: "usdm", coverage: "regional" },
    { layerSlug: "signal", sourceSlug: "era5_land_and_nasa_power", coverage: "global" },
    { layerSlug: "botanical-occurrences", sourceSlug: "gbif", coverage: "global" },
  ],
} satisfies Region;
