import type { Region } from "@/lib/region/region";

/**
 * The Kenya Highlands manifest: this tree's second region, and a real deployment artefact.
 *
 * Mirrors
 * `services/agri-data-service/src/agri_data_service/foundation/region/kenya_highlands.json` field
 * for field; `src/__tests__/region/second-region-manifest.test.ts` diffs the two exactly as the
 * pilot's parity test does. Why a shipped manifest rather than a test fixture, and what it
 * deliberately leaves empty, is in `src/lib/region/AGENTS.md` and in the service tree's
 * `foundation/region/AGENTS.md`.
 */

/**
 * The region's admin codes as a literal tuple, declared once beside the values, exactly as the
 * pilot's are. `getRegion()` checks the manifest it parses against the tuple of whichever region
 * is selected, so the two halves of one manifest may only be edited together.
 *
 * ISO 3166-2:KE counties: Kiambu, Nakuru, Nyandarua, Nyeri -- the central highland counties the
 * envelope below is drawn around.
 */
export const KENYA_HIGHLANDS_ADMIN_CODES = ["KE-13", "KE-31", "KE-35", "KE-36"] as const;

export const KENYA_HIGHLANDS = {
  slug: "kenya-highlands",
  displayName: "Kenya Highlands",
  envelope: { west: 34, south: -5, east: 42, north: 5 },
  defaultCameraEnvelope: { west: 35.5, south: -1.5, east: 38.5, north: 1.5 },
  // Empty on purpose: a sub-envelope exists for a lane that reads it, and the two lanes that do
  // (burn severity, the botanical seed classifier) bind no source in this region.
  subEnvelopes: {},
  // No projected work is declared for this region yet, and inventing a UTM zone for it would be a
  // manifest author's decision rather than one the work asked for. `null` is the honest value.
  crs: null,
  latticePitchDegrees: 0.01,
  latticeOriginRule: "floor_to_cell_origin",
  timezone: "Africa/Nairobi",
  isoCountryCodes: ["KE"],
  adminCodes: [...KENYA_HIGHLANDS_ADMIN_CODES] satisfies readonly (typeof KENYA_HIGHLANDS_ADMIN_CODES)[number][],
  // The platform's whole layer VOCABULARY, identical in every manifest by rule: a slug here and
  // absent from `enabledLayers` is a governed absence, a slug absent from here is not a federated
  // layer at all. `second-region-manifest.test.ts` pins this list to the pilot's, character for
  // character, because the vocabulary is platform-wide and only the bindings are per region.
  platformLayers: [
    "botanical-occurrences",
    "burn-severity",
    "drought",
    "evacuation-zones",
    "fire-detections",
    "fire-perimeters",
    "fire-risk",
    "land-context",
    "sensors",
    "climate-field-air-temperature",
    "climate-field-dew-point",
    "climate-field-precipitation",
    "climate-field-relative-humidity",
    "climate-field-shortwave-radiation",
    "climate-field-soil-wetness-profile",
    "climate-field-soil-wetness-root-zone",
    "climate-field-soil-wetness-surface",
    "climate-field-wind-speed",
    "soil-field-moisture",
    "soil-field-temperature",
    "soil-field-vpd",
    "soil-survey",
    "vegetation",
    "water-gauges",
    "watersheds",
    "weather-forecast",
    "weather-observations",
  ],
  // Only `coverage: global` sources: every other source this build knows is US-scoped (USDM, MTBS,
  // SSURGO, WFIGS, USGS NWIS, NOAA NWS, the Oregon OEM portal), so burn-severity, drought,
  // evacuation-zones, fire-perimeters, sensors, soil-survey, water-gauges and land-context are
  // governed absences here rather than bindings this deployment could not serve.
  enabledLayers: [
    { layerSlug: "fire-detections", sourceSlug: "firms", coverage: "global" },
    { layerSlug: "vegetation", sourceSlug: "sentinel2_ndvi", coverage: "global" },
    { layerSlug: "weather-observations", sourceSlug: "open_meteo", coverage: "global" },
    { layerSlug: "watersheds", sourceSlug: "hydrosheds", coverage: "global" },
    { layerSlug: "climate-field-air-temperature", sourceSlug: "nasa_power", coverage: "global" },
    { layerSlug: "climate-field-dew-point", sourceSlug: "nasa_power", coverage: "global" },
    { layerSlug: "climate-field-precipitation", sourceSlug: "nasa_power", coverage: "global" },
    { layerSlug: "climate-field-relative-humidity", sourceSlug: "nasa_power", coverage: "global" },
    { layerSlug: "climate-field-shortwave-radiation", sourceSlug: "nasa_power", coverage: "global" },
    { layerSlug: "climate-field-soil-wetness-profile", sourceSlug: "nasa_power", coverage: "global" },
    { layerSlug: "climate-field-soil-wetness-root-zone", sourceSlug: "nasa_power", coverage: "global" },
    { layerSlug: "climate-field-soil-wetness-surface", sourceSlug: "nasa_power", coverage: "global" },
    { layerSlug: "climate-field-wind-speed", sourceSlug: "nasa_power", coverage: "global" },
    { layerSlug: "soil-field-moisture", sourceSlug: "era5_land", coverage: "global" },
    { layerSlug: "soil-field-temperature", sourceSlug: "era5_land", coverage: "global" },
    { layerSlug: "soil-field-vpd", sourceSlug: "era5_land", coverage: "global" },
    { layerSlug: "botanical-occurrences", sourceSlug: "gbif", coverage: "global" },
  ],
} satisfies Region;
