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
  adminCodes: ["US-WA", "US-OR", "US-ID"],
  enabledLayers: [
    { layerSlug: "soil-survey", sourceSlug: "ssurgo", coverage: "regional" },
    { layerSlug: "fire-detections", sourceSlug: "firms", coverage: "global" },
    { layerSlug: "vegetation", sourceSlug: "sentinel2_ndvi", coverage: "global" },
    { layerSlug: "burn-severity", sourceSlug: "mtbs", coverage: "regional" },
    { layerSlug: "evacuation-zones", sourceSlug: "oregon_oem_arcgis", coverage: "regional" },
    { layerSlug: "interventions", sourceSlug: "postgres_interventions", coverage: "regional" },
    { layerSlug: "fire-perimeters", sourceSlug: "wfigs", coverage: "regional" },
    { layerSlug: "water-gauges", sourceSlug: "usgs_nwis", coverage: "regional" },
    { layerSlug: "sensors", sourceSlug: "noaa_nws", coverage: "regional" },
    { layerSlug: "weather-observations", sourceSlug: "open_meteo", coverage: "global" },
    { layerSlug: "watersheds", sourceSlug: "hydrosheds", coverage: "global" },
  ],
} satisfies Region;
