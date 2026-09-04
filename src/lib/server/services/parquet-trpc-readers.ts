import { TRPCError } from "@trpc/server";
import { z } from "zod";
import {
  climateFieldSignalDefinition,
  climateFieldSignalName,
  CLIMATE_FIELD_ATTRIBUTION,
  type AirTemperatureVariant,
  type ClimateFieldSignalId,
} from "@/lib/environmental/climate-field";
import {
  SOIL_FIELD_ATTRIBUTION,
  SOIL_FIELD_SOURCE_KEY,
  SOIL_FIELD_SUPPORT_KEY,
  soilFieldBandFor,
  soilFieldDepthDefinition,
  soilFieldMeasureDefinition,
  type SoilFieldDepth,
  type SoilFieldMeasure,
} from "@/lib/environmental/soil-field";
import type {
  AggregateEnvelopeSupport,
  AggregationMethod,
  SupportKind,
} from "@/lib/map/layer-render-contract";
import {
  BASE_ZOOM_TIER,
  LANE_BASE_LATTICES,
  latticeCellIndex,
  latticeCellSpan,
  mintedSupportId,
  resolveZoomTier,
  servedCellLattice,
  tessellatedCellPolygon,
  type CellLaneId,
  type ServedCellLattice,
  type ZoomTier,
} from "@/lib/map/zoom-tiers";
import { granularityForZoomTier } from "@/lib/server/services/zoom-granularity";
import { isFreshObservation } from "@/lib/server/services/environmental-time";
import {
  SOIL_FIELD_MAX_CELLS,
  type PublishedSoilFieldCollection,
  type SoilFieldFeatureProperties,
  type SoilFieldReadOptions,
} from "@/lib/server/services/environmental-read-model";
import {
  UpstreamAbortedError,
  UpstreamConfigurationError,
  UpstreamHttpError,
  UpstreamPayloadError,
  UpstreamTimeoutError,
} from "@/lib/server/http/bounded-upstream";
import {
  getParquetLatestRelease,
  getParquetLayerDay,
  getParquetLayerDayWindow,
  ParquetPlaneContractError,
  ParquetPlaneRequestError,
} from "@/lib/server/services/parquet-plane-client";
import {
  assertExhaustiveParquetPlaneState,
  type GovernedAbsenceEvidence,
  type ParquetPlaneEnvelope,
} from "@/lib/server/services/parquet-envelope";

const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const DAY_MS = 86_400_000;
const WATER_LIVE_MAX_AGE_MS = 6 * 60 * 60 * 1_000;
const WEATHER_LIVE_MAX_AGE_MS = 3 * 60 * 60 * 1_000;
const DROUGHT_RELEASE_INTERVAL_DAYS = 7;
const DROUGHT_MAX_CARRY_FORWARD_DAYS = 14;
export const VEGETATION_TRAILING_DAYS = 30;

const SOIL_FIELD_PARQUET_MAX_OBSERVATION_AGE_DAYS = 0;
const SNAPSHOT_SOURCE_MANIFEST_SHA256 =
  "465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f";

const SOIL_FIELD_LANES = {
  moisture: {
    surface: "soil-field-moisture-0-7cm",
    "root-zone": "soil-field-moisture-7-28cm",
    deep: "soil-field-moisture-28-100cm",
  },
  temperature: {
    surface: "soil-temperature-0-to-7cm",
    "root-zone": "soil-temperature-7-to-28cm",
    deep: "soil-temperature-28-to-100cm",
    substratum: "soil-temperature-100-to-255cm",
  },
  vpd: { surface: "soil-field-vpd" },
} as const;

const SOIL_TEMPERATURE_SOURCE_PARAMETERS = {
  surface: "soil_temperature_0_to_7cm_mean",
  "root-zone": "soil_temperature_7_to_28cm_mean",
  deep: "soil_temperature_28_to_100cm_mean",
  substratum: "soil_temperature_100_to_255cm_mean",
} as const satisfies Readonly<Record<SoilFieldDepth, string>>;

const SOIL_MOISTURE_SOURCE_PARAMETERS = {
  surface: "soil_moisture_0_to_7cm_mean",
  "root-zone": "soil_moisture_7_to_28cm_mean",
  deep: "soil_moisture_28_to_100cm_mean",
} as const satisfies Readonly<Record<Exclude<SoilFieldDepth, "substratum">, string>>;

/**
 * What must be shown wherever each lane's values are drawn.
 *
 * Two of the five already have a published constant beside their value vocabulary and are reused
 * from there rather than restated. The other three had none anywhere in the tree -- neither
 * `layer-registry.ts` nor `layer-legends.ts` carries an attribution field -- so they are stated
 * here, next to the reader that puts them on the wire, and a registry that grows one should read
 * from this table rather than adding a second copy. See `src/lib/server/services/AGENTS.md`
 * "Tessellated support geometry".
 */
const LANE_ATTRIBUTIONS = {
  "fire-detections": "NASA FIRMS (LANCE/ESDIS)",
  "water-gauges": "U.S. Geological Survey NWIS",
  "weather-observations": "Open-Meteo",
  vegetation: "Copernicus Sentinel-2 surface reflectance",
  "climate-field": CLIMATE_FIELD_ATTRIBUTION,
  "soil-field": SOIL_FIELD_ATTRIBUTION,
} as const satisfies Readonly<Record<CellLaneId, string>>;

/** Everything one row knows about itself that the envelope cannot derive from the ladder. */
interface CellSupportInput {
  lane: CellLaneId;
  /** The exact Parquet product read, when the lane publishes several; defaults to the lane. */
  sourceLayer?: string;
  zoomTier: ZoomTier;
  supportKind: SupportKind;
  aggregationMethod: AggregationMethod;
  /** Source observations or features behind this envelope. Read off a column, never inferred. */
  contributorCount: number;
  /** The warehouse's own identity for the cell, or null on a rung that publishes none. */
  cellId: string | null;
  longitude: number;
  latitude: number;
  observedDay: string;
  newestObservedAt: string | null;
}

/**
 * The support envelope one served row declares about itself.
 *
 * ONE builder for every lane, so the cell size can only come from the shared tier table
 * (`servedCellLattice`) and the identity can only come from the row or from `mintedSupportId`.
 * Before this, the client re-derived cell width from a private tier table and read "this is an
 * aggregate" off `cellId === null`, which made a rung whose rows happen to carry ids
 * indistinguishable from raw observations.
 *
 * A `raw_point` carries no cell size at all: a genuine station has no footprint, and publishing
 * one would license a renderer to draw a square around a gauge.
 */
function cellSupport(input: CellSupportInput): AggregateEnvelopeSupport {
  const lattice = servedCellLattice(input.zoomTier, LANE_BASE_LATTICES[input.lane]);
  const cellSize =
    input.supportKind === "raw_point"
      ? {}
      : {
          cellWidthDegrees: lattice.cellSizeDegrees,
          cellHeightDegrees: lattice.cellSizeDegrees,
          // The corner this row's cell was actually snapped to, so the client draws the SAME
          // square rather than re-deriving one from a lattice whose phase it cannot see. Both
          // sides now run `latticeCellSpan`; see `cellOriginDegrees` on the envelope.
          cellOriginDegrees: [
            latticeCellSpan(latticeCellIndex(input.longitude, lattice), lattice)[0],
            latticeCellSpan(latticeCellIndex(input.latitude, lattice), lattice)[0],
          ] as const,
        };
  return {
    zoomTier: input.zoomTier,
    supportKind: input.supportKind,
    supportId:
      input.cellId ?? mintedSupportId(input.zoomTier, input.longitude, input.latitude),
    // A raw point is centred on its own coordinate. `cell_center` rather than `cell_origin`
    // because the one thing a renderer must not do with a station is offset it by half a cell,
    // which naming a corner would invite.
    origin: input.supportKind === "raw_point" ? "cell_center" : lattice.origin,
    ...cellSize,
    aggregationMethod: input.aggregationMethod,
    contributorCount: input.contributorCount,
    provenance: {
      sourceLayer: input.sourceLayer ?? input.lane,
      observedDay: input.observedDay,
      newestObservedAt: input.newestObservedAt,
      attribution: LANE_ATTRIBUTIONS[input.lane],
    },
  };
}

/**
 * `aborted` is the odd one out and deliberately so: the other six describe the UPSTREAM, while an
 * abort describes the caller that walked away. It is never retryable, never an outage, and -- see
 * `rejectAborted` below -- never stored as an answer.
 */
export type ParquetReaderFailureKind =
  | "configuration"
  | "http"
  | "network"
  | "payload"
  | "timeout"
  | "contract"
  | "aborted";

export type ParquetReaderResult<T> =
  | {
      state: "ready";
      requestedDay: string;
      servedDay: string;
      data: T;
      truncated: boolean;
    }
  | {
      state: "absent";
      requestedDay: string;
      servedDay: string;
      evidence: GovernedAbsenceEvidence;
    }
  | {
      state: "not_generated";
      requestedDay: string;
      reason: "day_not_written" | "lane_never_written";
    }
  | {
      state: "upstream_unavailable";
      fault: {
        kind: ParquetReaderFailureKind;
        message: string;
        status?: number;
      };
    };

/**
 * An abandoned read is never an answer: turns an `aborted` fault into a thrown error.
 *
 * Every reader here returns its faults as DATA, which is what lets the map caption an outage
 * instead of blanking -- but react-query stores data, and a `{ kind: "aborted" }` payload cached
 * against a viewport would be replayed to the next reader of that key as though the warehouse had
 * said something. Throwing puts it on the error path, where `retry: 1` and the placeholder rules
 * already handle it. 499 rather than 503: the client closed the request, and nothing is down.
 *
 * It lives beside the readers rather than in one router because EVERY procedure that threads a
 * `signal` owes the same guard -- see `src/lib/server/services/AGENTS.md` §request-cancellation.
 */
export function rejectAborted<T>(result: ParquetReaderResult<T>): ParquetReaderResult<T> {
  if (result.state === "upstream_unavailable" && result.fault.kind === "aborted") {
    throw new TRPCError({ code: "CLIENT_CLOSED_REQUEST", message: result.fault.message });
  }
  return result;
}

export interface ParquetViewportRead {
  bbox?: string;
  date?: string;
  mapZoom: number;
  /** Test seam for omitted-day selection; production callers leave it unset. */
  nowMs?: number;
  /**
   * The tRPC resolver's cancellation, threaded down to the socket.
   *
   * Optional so a caller with nothing to cancel is unchanged, and named `signal` to match the
   * resolver option it is spread from -- `({ input, signal }) => read({ ...input, signal })`.
   * `getParquetClimateField` is the one reader that cannot use this name, because its `signal` is
   * already the measured quantity; see the `Omit` on its input.
   */
  signal?: AbortSignal;
}

const daySchema = z.string().regex(DAY_PATTERN);
const instantSchema = z
  .string()
  .endsWith("Z")
  .refine((value) => Number.isFinite(Date.parse(value)), "Expected a UTC instant");
const finiteNumberSchema = z.number().finite();

const waterGaugeRowSchema = z
  .object({
    site_number: z.string().nullable(),
    observed_at: instantSchema,
    observed_day: daySchema,
    site_name: z.string().nullable(),
    latitude: finiteNumberSchema.nullable(),
    longitude: finiteNumberSchema.nullable(),
    flow_cfs: finiteNumberSchema.nullable(),
    percentile: finiteNumberSchema.nullable(),
    condition: z.string().nullable(),
    trend: z.string().nullable(),
    source: z.string().min(1),
    geometry_linked: z.boolean(),
    data_available_at: instantSchema.nullable(),
    ingested_at: instantSchema,
  })
  .strict();

export interface ParquetWaterGauge {
  siteNumber: string | null;
  observedAt: string;
  observedDay: string;
  siteName: string | null;
  latitude: number | null;
  longitude: number | null;
  flowCfs: number | null;
  percentile: number | null;
  condition: string | null;
  trend: string | null;
  source: string;
  geometryLinked: boolean;
  dataAvailableAt: string | null;
  ingestedAt: string;
  /**
   * What this row stands for on the ground. `raw_point` at the detail rung, where a row IS one
   * USGS gauge, and `aggregate_cell` on every derived rung, where `site_number` and `site_name`
   * were nulled by the derivation because a cell of several gauges has no one identity.
   */
  support: AggregateEnvelopeSupport;
}

const weatherRowSchema = z
  .object({
    latitude: finiteNumberSchema,
    longitude: finiteNumberSchema,
    observed_at: instantSchema,
    observed_day: daySchema,
    external_id: z.string().nullable(),
    temperature_c: finiteNumberSchema,
    relative_humidity_pct: finiteNumberSchema,
    wind_speed_ms: finiteNumberSchema,
    wind_direction_deg: finiteNumberSchema.nullable(),
    precipitation_mm: finiteNumberSchema,
    source: z.string().min(1),
    feature_id: z.string().nullable(),
    ingested_at: instantSchema,
  })
  .strict();

export interface ParquetWeatherObservation {
  latitude: number;
  longitude: number;
  observedAt: string;
  observedDay: string;
  externalId: string | null;
  temperatureC: number;
  relativeHumidityPct: number;
  windSpeedMs: number;
  windDirectionDeg: number | null;
  precipitationMm: number;
  source: string;
  featureId: string | null;
  ingestedAt: string;
  /**
   * What this row stands for on the ground, on the same rule as the gauges: `raw_point` at the
   * detail rung, where a row IS one sampled Open-Meteo observation, and `aggregate_cell` on every
   * derived rung, where the derivation floored several samples into one of the ladder's cells.
   *
   * `weather` is an `event_point` layer in `LAYER_RENDER_CONTRACT`, so the base rung declares no
   * cell size at all -- see `LANE_BASE_LATTICES["weather-observations"]` for the sampled-grid
   * question that classification leaves open.
   */
  support: AggregateEnvelopeSupport;
}

const vegetationRowSchema = z
  .object({
    cell_id: z.string().nullable(),
    grid_name: z.string().min(1),
    metric_name: z.string().min(1),
    metric_unit: z.string().min(1),
    observed_day: daySchema,
    metric_value: finiteNumberSchema,
    observation_checksum: z.string().nullable(),
    data_available_at: instantSchema,
    release_count: z.number().int().nonnegative(),
    allowed_client_exposure: z.literal(true),
    cell_longitude: finiteNumberSchema,
    cell_latitude: finiteNumberSchema,
  })
  .strict();

export interface ParquetVegetationObservation {
  cellId: string | null;
  gridName: string;
  metricName: string;
  metricUnit: string;
  observedDay: string;
  metricValue: number;
  observationChecksum: string | null;
  dataAvailableAt: string;
  releaseCount: number;
  allowedClientExposure: true;
  longitude: number;
  latitude: number;
  /** The quarter-degree cell this NDVI mean describes, at every rung. */
  support: AggregateEnvelopeSupport;
}

const positionSchema = z.tuple([finiteNumberSchema, finiteNumberSchema]).rest(finiteNumberSchema);
const ringSchema = z.array(positionSchema).min(4);
const polygonCoordinatesSchema = z.array(ringSchema).min(1);
/**
 * The two geometry types every native-polygon lane serves.
 *
 * Shared by drought, fire-perimeters, burn-severity, evacuation-zones and watersheds rather than
 * restated per lane: the Parquet API renders EVERY WKB column the same way -- `ST_AsGeoJSON` over
 * `geom`/`geometry_wkb` (`parquet_ops/warehouse_reader.py:335-340`) -- so one decoder is the whole
 * of what those five readers need, and five copies would be five chances to accept a shape the
 * `native_polygon` render contract forbids.
 */
const polygonGeometrySchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("Polygon"), coordinates: polygonCoordinatesSchema }).strict(),
  z
    .object({ type: z.literal("MultiPolygon"), coordinates: z.array(polygonCoordinatesSchema).min(1) })
    .strict(),
]);

export type ParquetPolygonGeometry = z.infer<typeof polygonGeometrySchema>;

const droughtRowSchema = z
  .object({
    area_id: z.string().min(1),
    valid_date: daySchema,
    dm_category: z.number().int().min(0).max(4),
    source_url: z.string().min(1),
    ingested_at: instantSchema,
    geom: z.string().min(1),
  })
  .strict();

function decodePolygonGeometry(geojson: string, lane: string): ParquetPolygonGeometry {
  let rawGeometry: unknown;
  try {
    rawGeometry = JSON.parse(geojson);
  } catch {
    throw contractError(`${lane} geom is not GeoJSON text`);
  }
  const geometry = polygonGeometrySchema.safeParse(rawGeometry);
  if (!geometry.success) throw contractError(`${lane} geom is not a Polygon or MultiPolygon`);
  return geometry.data;
}

export interface ParquetDroughtArea {
  areaId: string;
  validDate: string;
  droughtCategory: 0 | 1 | 2 | 3 | 4;
  sourceUrl: string;
  ingestedAt: string;
  geometry: ParquetPolygonGeometry;
}

/**
 * The four `geo.features` polygon lanes and the one point lane that left Martin across the
 * environmental_postgres_retirement_20260904 track, as the Parquet warehouse publishes them.
 * Four moved in wave C; `fire-perimeters` moved last, once its lane became a `static_lookup`.
 *
 * Every schema below is `.strict()` over the lane's REGISTERED arrow columns
 * (`warehouse/schemas/<lane>.py`) with the one substitution the serving path makes: a `geom` or
 * `geometry_wkb` binary column reaches the wire as GeoJSON text, and `_serving_source_key` is
 * popped off the row before it is serialized (`warehouse_reader.py:297`). A column added upstream
 * therefore fails these readers loudly instead of arriving unread -- which is the point, because
 * the Martin tile functions these replace projected a hand-written SELECT list that had already
 * drifted from its producer twice (`fire_risk_tiles` emitted `risk_level`/`name`, which WFIGS has
 * never written; `sensor_tiles` emitted `sensor_type`/`status` for the same reason).
 */
const firePerimeterRowSchema = z
  .object({
    feature_id: z.string().min(1),
    unique_fire_identifier: z.string().min(1),
    // The VERSION STAMP this whole partition shares, not an observation day.
    snapshot_day: daySchema,
    // NULLABLE BY CONTRACT, and the one nullability in this file that a renderer depends on:
    // `geo.feature_observation_day` returns NULL for a row it cannot date
    // (`drizzle/0018_fire_discovery_observation_day.sql:39-40`) and such a row is drawn at EVERY
    // slider date. See `firePerimetersInFrame`.
    observed_day: daySchema.nullable(),
    incident_name: z.string().nullable(),
    irwin_id: z.string().nullable(),
    fire_discovery_at: instantSchema.nullable(),
    polygon_at: instantSchema.nullable(),
    gis_acres: finiteNumberSchema.nullable(),
    fire_cause: z.string().nullable(),
    incident_type_category: z.string().nullable(),
    poo_state: z.string().nullable(),
    percent_contained: finiteNumberSchema.nullable(),
    severity: z.string().nullable(),
    status: z.string().min(1),
    data_available_at: instantSchema.nullable(),
    updated_at: instantSchema,
    geometry_wkb: z.string().min(1),
  })
  .strict();

/**
 * One WFIGS incident, as of whichever snapshot answered the request.
 *
 * Six projected fields out of the eighteen validated above, on `getParquetEvacuationZones`'s own
 * rule: a reader projects what the presenter draws plus the identity of the version that answered.
 * The other twelve are validated so an upstream column change fails this reader loudly, and are
 * deliberately not put on the wire -- `incident_name`, `gis_acres`, `percent_contained`,
 * `fire_cause`, `poo_state`, `fire_discovery_at` and `polygon_at` would widen the tooltip, which
 * `geo.fire_risk_tiles()` never did and which is a `hover-fields.ts` change with its own review,
 * not something this cutover should decide by quietly shipping the columns. `status` is
 * `'published'` by construction of the export's WHERE clause and `data_available_at` is 100% NULL
 * in production, so neither has anything to say to a client.
 */
export interface ParquetFirePerimeter {
  featureId: string;
  uniqueFireIdentifier: string;
  /** The day this population was CAPTURED -- never the day an incident was observed. */
  snapshotDay: string;
  /** The incident's own date, or null for a row WFIGS gave no parseable timestamp. */
  observedDay: string | null;
  severity: string | null;
  geometry: ParquetPolygonGeometry;
}

const burnSeverityRowSchema = z
  .object({
    feature_id: z.string().min(1),
    fire_id: z.string().min(1),
    natural_key: z.string().min(1),
    release_identifier: z.string().min(1),
    mapping_revision: z.string().min(1),
    fire_year: z.number().int().nullable(),
    ignition_date: daySchema,
    observed_day: daySchema,
    data_available_at: instantSchema,
    fire_name: z.string().nullable(),
    fire_type: z.string().nullable(),
    assessment_type: z.string().nullable(),
    acres: finiteNumberSchema.nullable(),
    severity_class: z.string().nullable(),
    dnbr_offset: z.number().int().nullable(),
    dnbr_standard_deviation: z.number().int().nullable(),
    nodata_threshold: z.number().int().nullable(),
    greenness_threshold: z.number().int().nullable(),
    low_threshold: z.number().int().nullable(),
    moderate_threshold: z.number().int().nullable(),
    high_threshold: z.number().int().nullable(),
    allowed_client_exposure: z.boolean(),
    geom: z.string().min(1),
  })
  .strict();

export interface ParquetBurnScar {
  fireId: string;
  fireName: string | null;
  fireYear: number | null;
  fireType: string | null;
  assessmentType: string | null;
  ignitionDate: string;
  observedDay: string;
  acres: number | null;
  severityClass: string | null;
  dataAvailableAt: string;
  geometry: ParquetPolygonGeometry;
}

const evacuationZoneRowSchema = z
  .object({
    global_id: z.string().min(1),
    natural_key: z.string().min(1),
    producer: z.string().min(1),
    snapshot_day: daySchema,
    evacuation_area_name: z.string().nullable(),
    fire_name: z.string().nullable(),
    county: z.string().nullable(),
    hazard_type: z.string().nullable(),
    evacuation_level: z.number().int().nullable(),
    evacuation_level_label: z.string().nullable(),
    severity: z.string().nullable(),
    structures_within: finiteNumberSchema.nullable(),
    addresses_within: finiteNumberSchema.nullable(),
    population_within: finiteNumberSchema.nullable(),
    editor_name: z.string().nullable(),
    observed_at: instantSchema.nullable(),
    source: z.string().min(1),
    geometry_wkb: z.string().min(1),
    geometry_version_id: z.string().nullable(),
    geometry_version_valid_from: instantSchema.nullable(),
    geometry_last_confirmed_at: instantSchema.nullable(),
    data_available_at: instantSchema.nullable(),
    feature_updated_at: instantSchema.nullable(),
  })
  .strict();

export interface ParquetEvacuationZone {
  naturalKey: string;
  snapshotDay: string;
  evacuationAreaName: string | null;
  fireName: string | null;
  county: string | null;
  hazardType: string | null;
  evacuationLevel: number | null;
  evacuationLevelLabel: string | null;
  severity: string | null;
  structuresWithin: number | null;
  populationWithin: number | null;
  observedAt: string | null;
  geometry: ParquetPolygonGeometry;
}

const watershedRowSchema = z
  .object({
    huc12: z.string().min(1),
    name: z.string().nullable(),
    areasqkm: finiteNumberSchema.nullable(),
    tohuc: z.string().nullable(),
    states: z.string().nullable(),
    hutype: z.string().nullable(),
    source: z.string().min(1),
    observed_at: instantSchema.nullable(),
    data_available_at: instantSchema.nullable(),
    release_day: daySchema,
    feature_id: z.string().nullable(),
    geom: z.string().min(1),
  })
  .strict();

/**
 * One published basin, at whatever rung of the HUC hierarchy served the request.
 *
 * `hucLevel` is the LENGTH of the code and never an assumption: `HierarchicalDissolve` truncates
 * `huc12` to ten digits at z9, eight at z5 and six at z0 (`warehouse/schemas/watersheds.py:101-104`),
 * so the code itself is the only honest statement of which rung a feature came from -- exactly the
 * guarantee `geo.watershed_rollup`'s `huc_level` column made, derived rather than stored.
 */
export interface ParquetWatershed {
  huc: string;
  hucLevel: number;
  name: string | null;
  areaSquareKm: number | null;
  toHuc: string | null;
  states: string | null;
  huType: string | null;
  releaseDay: string;
  observedAt: string | null;
  geometry: ParquetPolygonGeometry;
}

const sensorRowSchema = z
  .object({
    sensor_id: z.string().nullable(),
    station_name: z.string().nullable(),
    network: z.string().nullable(),
    observed_day: daySchema,
    observed_at: instantSchema,
    measurement_name: z.string().min(1),
    value: finiteNumberSchema,
    unit_code: z.string().nullable(),
    quality_control: z.string().nullable(),
    feature_id: z.string().nullable(),
    data_available_at: instantSchema.nullable(),
    station_longitude: finiteNumberSchema.nullable(),
    station_latitude: finiteNumberSchema.nullable(),
  })
  .strict();

/**
 * One station-day, collapsed from the lane's tall one-row-per-measurement grain.
 *
 * `geo.sensor_tiles()` emitted one MVT point per `(sensor_id, geom, observed_day)` and projected
 * four attributes; the Parquet lane keeps all sixteen captured measurements as separate rows
 * (`warehouse/schemas/sensors.py` header). Collapsing here rather than in the presenter is what
 * keeps the map's station count equal to the tile function's: without it a station reporting
 * sixteen fields would draw sixteen coincident dots.
 */
export interface ParquetSensorStation {
  sensorId: string | null;
  stationName: string | null;
  network: string | null;
  observedDay: string;
  observedAt: string;
  longitude: number;
  latitude: number;
  /** Every measurement this station reported on the served day, newest reading per name. */
  measurements: readonly {
    name: string;
    value: number;
    unitCode: string | null;
    observedAt: string;
  }[];
}

const fireDetectionRowSchema = z
  .object({
    cell_longitude: finiteNumberSchema,
    cell_latitude: finiteNumberSchema,
    observed_day: daySchema,
    detection_count: z.number().int().nonnegative(),
    frp_sum: finiteNumberSchema.nullable(),
    frp_observation_count: z.number().int().nonnegative(),
    high_confidence_detection_count: z.number().int().nonnegative(),
    newest_observed_at: instantSchema,
  })
  .strict();

const climateSnapshotLineageRowSchema = z
  .object({
    support_key: z.string().min(1),
    signal_name: z.string().min(1),
    normalized_unit: z.string().min(1),
    cell_id: z.string().nullable(),
    observed_day: daySchema,
    normalized_value: finiteNumberSchema,
    observation_count: z.number().int().positive(),
    newest_observed_at: instantSchema,
    coverage_fraction: finiteNumberSchema.nullable(),
    allowed_client_exposure: z.boolean().nullable(),
    cell_longitude: finiteNumberSchema,
    cell_latitude: finiteNumberSchema,
    source_key: z.string().min(1),
    source_parameter: z.string().min(1),
    source_snapshot_id: z.string().min(1),
    source_manifest_sha256: z.string().regex(/^[a-f0-9]{64}$/),
    precedence_contract: z.string().min(1),
    selected_source_row_id: z.number().int().nullable(),
    selected_source_row_sha256: z.string().regex(/^[a-f0-9]{64}$/).nullable(),
    selected_source_release_id: z.string().nullable(),
    selected_source_release_retrieved_at: instantSchema.nullable(),
    selected_source_release_payload_checksum: z.string().nullable(),
    selected_source_part_key: z.string().nullable(),
    selected_source_part_sha256: z.string().regex(/^[a-f0-9]{64}$/).nullable(),
    selected_source_row_ordinal: z.number().int().nonnegative().nullable(),
    input_source_row_count: z.number().int().positive(),
    input_source_row_digest: z.string().nullable(),
    input_source_row_ids: z.array(z.number().int()).nullable(),
    input_source_row_sha256s: z.array(z.string().regex(/^[a-f0-9]{64}$/)).nullable(),
    input_source_release_ids: z.array(z.string()).nullable(),
    input_source_part_keys: z.array(z.string()).nullable(),
    input_source_part_sha256s: z.array(z.string().regex(/^[a-f0-9]{64}$/)).nullable(),
    input_source_row_ordinals: z.array(z.number().int().nonnegative()).nullable(),
  })
  .strict();

const signalPlaneRowSchema = z
  .object({
    support_key: z.string().min(1),
    signal_name: z.string().min(1),
    normalized_unit: z.string().min(1),
    cell_id: z.string().nullable(),
    observed_day: daySchema,
    normalized_value: finiteNumberSchema,
    observation_count: z.number().int().positive(),
    newest_observed_at: instantSchema,
    coverage_fraction: finiteNumberSchema.nullable(),
    allowed_client_exposure: z.boolean().nullable(),
    cell_longitude: finiteNumberSchema,
    cell_latitude: finiteNumberSchema,
  })
  .strict();

const selectedSnapshotRowShape = {
  selected_observation_id: z.number().int().nullable(),
  selected_canonical_row_sha256: z.string().regex(/^[a-f0-9]{64}$/).nullable(),
  selected_source_release_id: z.string().nullable(),
  selected_release_retrieved_at: instantSchema.nullable(),
  physical_candidate_count: z.number().int().positive(),
  lineage_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  input_manifest_sha256: z.string().regex(/^[a-f0-9]{64}$/),
};

const soilWetnessRowSchema = signalPlaneRowSchema.extend(selectedSnapshotRowShape).strict();

const soilTemperatureRowSchema = signalPlaneRowSchema
  .extend({
    data_source_key: z.string().min(1),
    source_parameter: z.string().min(1),
    ...selectedSnapshotRowShape,
  })
  .strict();

type SoilServingRow = Pick<
  z.infer<typeof signalPlaneRowSchema>,
  | "support_key"
  | "signal_name"
  | "normalized_unit"
  | "cell_id"
  | "observed_day"
  | "normalized_value"
  | "observation_count"
  | "newest_observed_at"
  | "coverage_fraction"
  | "allowed_client_exposure"
  | "cell_longitude"
  | "cell_latitude"
>;

export const PARQUET_CLIMATE_FIELD_SIGNAL_IDS = [
  "air-temperature",
  "dew-point",
  "precipitation",
  "relative-humidity",
  "shortwave-radiation",
  "wind-speed",
  "soil-wetness-surface",
  "soil-wetness-root-zone",
  "soil-wetness-profile",
] as const satisfies readonly ClimateFieldSignalId[];

export type ParquetClimateFieldSignalId =
  (typeof PARQUET_CLIMATE_FIELD_SIGNAL_IDS)[number];

const PARQUET_CLIMATE_FIELD_SIGNAL_SET = new Set<ClimateFieldSignalId>(
  PARQUET_CLIMATE_FIELD_SIGNAL_IDS
);

const STANDARD_CLIMATE_FIELD_LANES = {
  "air-temperature": {
    layer: null,
    rowContract: "signal-plane",
    sourceParameter: null,
  },
  "dew-point": {
    layer: "climate-field-dew-point",
    rowContract: "signal-plane",
    sourceParameter: null,
  },
  precipitation: {
    layer: "climate-field-precipitation",
    rowContract: "snapshot-lineage",
    sourceParameter: "PRECTOTCORR",
  },
  "relative-humidity": {
    layer: "climate-field-relative-humidity",
    rowContract: "snapshot-lineage",
    sourceParameter: "RH2M",
  },
  "shortwave-radiation": {
    layer: "climate-field-shortwave-radiation",
    rowContract: "snapshot-lineage",
    sourceParameter: "ALLSKY_SFC_SW_DWN",
  },
  "wind-speed": {
    layer: "climate-field-wind-speed",
    rowContract: "signal-plane",
    sourceParameter: null,
  },
  "soil-wetness-surface": {
    layer: "soil-wetness-surface",
    rowContract: "soil-wetness",
    sourceParameter: null,
  },
  "soil-wetness-root-zone": {
    layer: "soil-wetness-root-zone",
    rowContract: "soil-wetness",
    sourceParameter: null,
  },
  "soil-wetness-profile": {
    layer: "soil-wetness-profile",
    rowContract: "soil-wetness",
    sourceParameter: null,
  },
} as const satisfies Record<
  ClimateFieldSignalId,
  {
    layer: string | null;
    rowContract: "signal-plane" | "snapshot-lineage" | "soil-wetness";
    sourceParameter: string | null;
  }
>;

export interface ParquetClimateFieldObservation {
  /** The stored cell's identity at z13; null on the coarse rungs, whose cells are anonymous aggregates. */
  cellId: string | null;
  observedDay: string;
  value: number;
  observationCount: number;
  newestObservedAt: string;
  coverageFraction: number | null;
  allowedClientExposure: boolean | null;
  longitude: number;
  latitude: number;
  /**
   * The lattice cell this value describes. `tessellated_cell` at every rung -- the value is a
   * continuous field's sample, and the presentation decides whether it is drawn as that cell, as a
   * dissolved band across several of them, or as a mark at its centre.
   */
  support: AggregateEnvelopeSupport;
}

export interface ParquetFireDetectionCell {
  longitude: number;
  latitude: number;
  observedDay: string;
  detectionCount: number;
  frpSum: number | null;
  frpObservationCount: number;
  highConfidenceDetectionCount: number;
  newestObservedAt: string;
  /**
   * `aggregate_cell` at EVERY rung, including the detail one: FIRMS publishes no raw rung, so a
   * z13 row is already a 0.005-degree detection-density cell rather than one hotspot. See
   * `FIRE_DETECTION_DETAIL_FORMS` in the render contract.
   */
  support: AggregateEnvelopeSupport;
}

export interface ParquetVegetationWindow {
  firstDay: string;
  lastDay: string;
  observations: readonly ParquetVegetationObservation[];
  days: readonly ParquetReaderResult<readonly ParquetVegetationObservation[]>[];
}

export interface ParquetFireWindow {
  firstDay: string;
  lastDay: string;
  cells: readonly ParquetFireDetectionCell[];
  days: readonly ParquetReaderResult<readonly ParquetFireDetectionCell[]>[];
}

function currentUtcDay(nowMs = Date.now()): string {
  return new Date(nowMs).toISOString().slice(0, 10);
}

function selectedDay(date: string | undefined, nowMs: number | undefined): string {
  const day = date ?? currentUtcDay(nowMs);
  const parsedMs = Date.parse(`${day}T00:00:00Z`);
  if (
    !DAY_PATTERN.test(day) ||
    Number.isNaN(parsedMs) ||
    new Date(parsedMs).toISOString().slice(0, 10) !== day
  ) {
    throw new ParquetPlaneRequestError(`date must be a YYYY-MM-DD calendar day, got "${day}"`);
  }
  return day;
}

function addUtcDays(day: string, count: number): string {
  return new Date(Date.parse(`${day}T00:00:00Z`) + count * DAY_MS).toISOString().slice(0, 10);
}

function rejectFutureDay(day: string, nowMs: number, layer: string): void {
  const today = currentUtcDay(nowMs);
  if (day > today) {
    throw new ParquetPlaneRequestError(
      `${layer} cannot read future day ${day}; server UTC today is ${today}`
    );
  }
}

function envelopeDayMs(day: string, field: string): number {
  const parsedMs = Date.parse(`${day}T00:00:00Z`);
  if (
    !DAY_PATTERN.test(day) ||
    Number.isNaN(parsedMs) ||
    new Date(parsedMs).toISOString().slice(0, 10) !== day
  ) {
    throw contractError(`Parquet plane returned an invalid ${field} calendar day "${day}"`);
  }
  return parsedMs;
}

function releaseServedDay(envelope: ParquetPlaneEnvelope, asOfDay: string): string {
  if (envelope.state !== "published" && envelope.state !== "governed_absence") {
    throw contractError(
      `drought release lookup for ${asOfDay} contradicted an earlier published release with ${envelope.state}`
    );
  }
  return envelope.servedDay;
}

function contractError(message: string): ParquetPlaneContractError {
  return new ParquetPlaneContractError(message);
}

function parseRows<T>(rows: readonly Record<string, unknown>[], schema: z.ZodType<T>, layer: string): T[] {
  const parsed = z.array(schema).safeParse(rows);
  if (!parsed.success) {
    throw contractError(`${layer} rows do not match the registered Parquet schema`);
  }
  return parsed.data;
}

function newestByKey<T>(
  rows: readonly T[],
  keyFor: (row: T) => string,
  observedAtFor: (row: T) => string
): T[] {
  const newest = new Map<string, T>();
  for (const row of rows) {
    const key = keyFor(row);
    const previous = newest.get(key);
    if (
      previous === undefined ||
      Date.parse(observedAtFor(row)) > Date.parse(observedAtFor(previous))
    ) {
      newest.set(key, row);
    }
  }
  return [...newest.values()];
}

function mapEnvelope<T>(
  envelope: ParquetPlaneEnvelope,
  decodeRows: (rows: readonly Record<string, unknown>[]) => T
): ParquetReaderResult<T> {
  switch (envelope.state) {
    case "published":
      return {
        state: "ready",
        requestedDay: envelope.requestedDay,
        servedDay: envelope.servedDay,
        data: decodeRows(envelope.rows),
        truncated: envelope.truncated,
      };
    case "governed_absence":
      return {
        state: "absent",
        requestedDay: envelope.requestedDay,
        servedDay: envelope.servedDay,
        evidence: envelope.evidence,
      };
    case "day_not_written":
      return {
        state: "not_generated",
        requestedDay: envelope.requestedDay,
        reason: "day_not_written",
      };
    case "lane_never_written":
      return {
        state: "not_generated",
        requestedDay: envelope.requestedDay,
        reason: "lane_never_written",
      };
    default:
      return assertExhaustiveParquetPlaneState(envelope);
  }
}

type ParquetUpstreamFailure = Extract<
  ParquetReaderResult<never>,
  { state: "upstream_unavailable" }
>;

export function parquetUpstreamFailure(error: unknown): ParquetUpstreamFailure | null {
  // First, and ahead of the timeout arm: an abort and a timeout are the same DOMException on the
  // wire, and calling a client that navigated away a service outage would page someone for it.
  if (error instanceof UpstreamAbortedError) {
    return { state: "upstream_unavailable", fault: { kind: "aborted", message: error.message } };
  }
  if (error instanceof UpstreamConfigurationError) {
    return { state: "upstream_unavailable", fault: { kind: "configuration", message: error.message } };
  }
  if (error instanceof UpstreamHttpError) {
    return {
      state: "upstream_unavailable",
      fault: { kind: "http", message: error.message, status: error.status },
    };
  }
  if (error instanceof UpstreamPayloadError) {
    return { state: "upstream_unavailable", fault: { kind: "payload", message: error.message } };
  }
  if (error instanceof UpstreamTimeoutError) {
    return { state: "upstream_unavailable", fault: { kind: "timeout", message: error.message } };
  }
  if (
    error instanceof TypeError &&
    /(?:fetch failed|failed to fetch|networkerror|network request failed)/i.test(error.message)
  ) {
    return { state: "upstream_unavailable", fault: { kind: "network", message: error.message } };
  }
  if (error instanceof ParquetPlaneContractError) {
    return { state: "upstream_unavailable", fault: { kind: "contract", message: error.message } };
  }
  return null;
}

async function boundedResult<T>(work: () => Promise<ParquetReaderResult<T>>): Promise<ParquetReaderResult<T>> {
  try {
    return await work();
  } catch (error) {
    const failure = parquetUpstreamFailure(error);
    if (failure !== null) return failure;
    throw error;
  }
}

function commonRequest(input: ParquetViewportRead, layer: string) {
  const day = selectedDay(input.date, input.nowMs);
  const zoomTier = resolveZoomTier(input.mapZoom);
  return {
    day,
    request: {
      layer,
      day,
      zoomTier,
      ...(input.bbox === undefined ? {} : { bbox: input.bbox }),
      ...(input.signal === undefined ? {} : { signal: input.signal }),
    },
  };
}

function decodeWaterRows(
  rows: readonly Record<string, unknown>[],
  zoomTier: ZoomTier
): ParquetWaterGauge[] {
  return parseRows(rows, waterGaugeRowSchema, "water-gauges").map((row) => ({
    siteNumber: row.site_number,
    observedAt: row.observed_at,
    observedDay: row.observed_day,
    siteName: row.site_name,
    latitude: row.latitude,
    longitude: row.longitude,
    flowCfs: row.flow_cfs,
    percentile: row.percentile,
    condition: row.condition,
    trend: row.trend,
    source: row.source,
    geometryLinked: row.geometry_linked,
    dataAvailableAt: row.data_available_at,
    ingestedAt: row.ingested_at,
    support: cellSupport({
      lane: "water-gauges",
      zoomTier,
      supportKind: zoomTier === BASE_ZOOM_TIER ? "raw_point" : "aggregate_cell",
      // `none` at the raw rung, where nothing was combined at all. `mean` above it, and NOT
      // `count`: the number a coarse cell carries is `flow_cfs`, the mean discharge over the
      // gauges the derivation floored into that cell -- which is what `WaterLayer` colours by
      // and what the caption calls "Mean discharge". `count` would name a different quantity
      // from the one on the row, and how many gauges contributed is already `contributorCount`.
      aggregationMethod: zoomTier === BASE_ZOOM_TIER ? "none" : "mean",
      // One row is one reading; `newestWaterRows` sums these as it folds a cell's readings
      // together, so the published figure is measured rather than assumed.
      contributorCount: 1,
      // The site number where there is one. An UNLOCATED gauge -- this lane's coordinates are
      // nullable, and the derivation drops those rows from every rung above the base -- has no
      // position to mint an id from either, so its own reading instant is the identity.
      cellId:
        row.site_number ??
        (row.longitude === null || row.latitude === null
          ? `water-gauges:${row.observed_day}:${row.observed_at}`
          : null),
      longitude: row.longitude ?? 0,
      latitude: row.latitude ?? 0,
      observedDay: row.observed_day,
      newestObservedAt: row.observed_at,
    }),
  }));
}

/**
 * The newest reading per gauge or per cell, carrying the COUNT of everything folded into it.
 *
 * The fold is what makes `contributorCount` honest here: this lane publishes no observation-count
 * column, so the only defensible number is the one this function can see -- the readings that
 * shared this envelope's key. Summing them onto the winner keeps the count with the row that
 * survives, rather than reporting 1 for a cell that answered for six gauges.
 */
function newestWaterRows(rows: readonly ParquetWaterGauge[]): ParquetWaterGauge[] {
  const keyFor = (row: ParquetWaterGauge) =>
    row.siteNumber ?? `${row.longitude}:${row.latitude}`;
  const foldedCounts = new Map<string, number>();
  for (const row of rows) {
    const key = keyFor(row);
    foldedCounts.set(key, (foldedCounts.get(key) ?? 0) + row.support.contributorCount);
  }
  return newestByKey(rows, keyFor, (row) => row.observedAt).map((row) => ({
    ...row,
    support: {
      ...row.support,
      contributorCount: foldedCounts.get(keyFor(row)) ?? row.support.contributorCount,
    },
  }));
}

function decodeWeatherRows(
  rows: readonly Record<string, unknown>[],
  zoomTier: ZoomTier
): ParquetWeatherObservation[] {
  return parseRows(rows, weatherRowSchema, "weather-observations").map((row) => ({
    latitude: row.latitude,
    longitude: row.longitude,
    observedAt: row.observed_at,
    observedDay: row.observed_day,
    externalId: row.external_id,
    temperatureC: row.temperature_c,
    relativeHumidityPct: row.relative_humidity_pct,
    windSpeedMs: row.wind_speed_ms,
    windDirectionDeg: row.wind_direction_deg,
    precipitationMm: row.precipitation_mm,
    source: row.source,
    featureId: row.feature_id,
    ingestedAt: row.ingested_at,
    support: cellSupport({
      lane: "weather-observations",
      zoomTier,
      supportKind: zoomTier === BASE_ZOOM_TIER ? "raw_point" : "aggregate_cell",
      // `none` at the raw rung, where nothing was combined. `mean` above it, on the same reading
      // as the gauges: what a derived row carries is the averaged temperature, humidity and wind
      // of the samples that fell in its cell, not a tally of them.
      aggregationMethod: zoomTier === BASE_ZOOM_TIER ? "none" : "mean",
      // One row is one observation. The lane publishes no observation-count column, and
      // `newestWeatherRows` keeps the newest row per position rather than folding several, so
      // there is no measured number larger than this to report.
      contributorCount: 1,
      // The station identity where the lane published one; otherwise the rung and the position,
      // minted by `cellSupport`.
      cellId: row.external_id ?? row.feature_id,
      longitude: row.longitude,
      latitude: row.latitude,
      observedDay: row.observed_day,
      newestObservedAt: row.observed_at,
    }),
  }));
}

function newestWeatherRows(rows: readonly ParquetWeatherObservation[]): ParquetWeatherObservation[] {
  return newestByKey(
    rows,
    (row) => `${row.longitude}:${row.latitude}`,
    (row) => row.observedAt
  );
}

export function isParquetClimateFieldSignal(
  signal: ClimateFieldSignalId
): signal is ParquetClimateFieldSignalId {
  return PARQUET_CLIMATE_FIELD_SIGNAL_SET.has(signal);
}

function climateFieldProduct(
  signal: ParquetClimateFieldSignalId,
  variant: AirTemperatureVariant
) {
  const contract = STANDARD_CLIMATE_FIELD_LANES[signal];
  const layer =
    signal === "air-temperature"
      ? `climate-field-air-temperature-${variant}`
      : contract.layer;
  if (layer === null) {
    throw contractError(`${signal} has no registered Parquet product layer`);
  }
  return {
    ...contract,
    layer,
  };
}

function decodeClimateFieldRows(
  rows: readonly Record<string, unknown>[],
  signal: ParquetClimateFieldSignalId,
  variant: AirTemperatureVariant,
  servedDay: string,
  zoomTier: ZoomTier
): ParquetClimateFieldObservation[] {
  const contract = climateFieldProduct(signal, variant);
  const { layer } = contract;
  const expectedSignalName = climateFieldSignalName(signal, variant);
  const expectedUnit = climateFieldSignalDefinition(signal).unit;
  const seenCells = new Set<string>();
  const parsed =
    contract.rowContract === "snapshot-lineage"
      ? parseRows(rows, climateSnapshotLineageRowSchema, layer)
      : contract.rowContract === "soil-wetness"
        ? parseRows(rows, soilWetnessRowSchema, layer)
        : parseRows(rows, signalPlaneRowSchema, layer);
  return parsed.map((row) => {
    if (
      row.support_key !== "surface" ||
      row.signal_name !== expectedSignalName ||
      row.normalized_unit !== expectedUnit ||
      row.observed_day !== servedDay
    ) {
      throw contractError(`${layer} returned a row outside its registered climate contract`);
    }
    // The same rule `decodeSoilFieldRows` enforces: only the detail rung carries a stored cell
    // identity, and the coarse rungs carry an anonymous aggregate. A z13 row with no `cell_id`,
    // or a coarse row that kept one, means the reader is reading a rung it did not ask for.
    //
    // It stays a READER-SIDE integrity check and nothing more. The client no longer reads support
    // off this nullability -- `support` says the rung, the form and the cell size outright -- so a
    // lane that one day publishes ids on every rung would relax this guard and change no renderer.
    if ((zoomTier === BASE_ZOOM_TIER) !== (row.cell_id !== null)) {
      throw contractError(`${layer} returned invalid cell identity nullability at z${zoomTier}`);
    }
    if (
      contract.rowContract === "snapshot-lineage" &&
      (contract.sourceParameter === null ||
        !("source_key" in row) ||
        row.source_key !== "nasa-power-daily" ||
        !("source_parameter" in row) ||
        row.source_parameter !== contract.sourceParameter ||
        !("source_manifest_sha256" in row) ||
        row.source_manifest_sha256 !== SNAPSHOT_SOURCE_MANIFEST_SHA256)
    ) {
      throw contractError(`${layer} returned a row outside its pinned source contract`);
    }
    if (
      contract.rowContract === "soil-wetness" &&
      (!("input_manifest_sha256" in row) ||
        row.input_manifest_sha256 !== SNAPSHOT_SOURCE_MANIFEST_SHA256)
    ) {
      throw contractError(`${layer} returned a row outside its pinned source contract`);
    }
    if (
      row.cell_longitude < -180 ||
      row.cell_longitude > 180 ||
      row.cell_latitude < -90 ||
      row.cell_latitude > 90
    ) {
      throw contractError(`${layer} returned a cell outside WGS84 bounds`);
    }
    // Keyed on the coordinate pair where there is no identity to key on, so the duplicate check
    // survives the coarse rungs instead of silently passing on a set of nulls.
    const cellKey = row.cell_id ?? `${row.cell_longitude}:${row.cell_latitude}`;
    if (seenCells.has(cellKey)) {
      throw contractError(`${layer} returned duplicate cell ${cellKey} for ${servedDay}`);
    }
    seenCells.add(cellKey);
    return {
      cellId: row.cell_id,
      observedDay: row.observed_day,
      value: row.normalized_value,
      observationCount: row.observation_count,
      newestObservedAt: row.newest_observed_at,
      coverageFraction: row.coverage_fraction,
      allowedClientExposure: row.allowed_client_exposure,
      longitude: row.cell_longitude,
      latitude: row.cell_latitude,
      support: cellSupport({
        lane: "climate-field",
        sourceLayer: layer,
        zoomTier,
        supportKind: "tessellated_cell",
        aggregationMethod: "mean",
        contributorCount: row.observation_count,
        cellId: row.cell_id,
        longitude: row.cell_longitude,
        latitude: row.cell_latitude,
        observedDay: row.observed_day,
        newestObservedAt: row.newest_observed_at,
      }),
    };
  });
}

/**
 * One climate day, and the ONE physical rung that answered it.
 *
 * The tier travels beside the result rather than inside its `ready` arm because every state needs
 * it: an empty collection built from a `day_not_written` still has to declare which rung was asked,
 * or the renderer cannot say whether it is looking at stored cells or at an aggregate.
 */
export interface ParquetClimateFieldRead {
  zoomTier: ZoomTier;
  result: ParquetReaderResult<readonly ParquetClimateFieldObservation[]>;
}

/**
 * Read one exact published climate day from a promoted, frozen-layout Parquet lane, at the rung
 * that serves the caller's map zoom.
 *
 * `abortSignal`, not `signal`, and `Omit<ParquetViewportRead, "signal">` to make that a compile
 * error rather than a convention: this lane's `signal` is already the measured quantity
 * (`ClimateFieldSignalId`), and one field meaning two things is exactly the collision that ends
 * with a `ClimateFieldSignalId` passed to `fetch`.
 *
 * EXACTLY ONE RUNG PER REQUEST. The hard-coded `zoomTier: 13` this replaced asked the detail rung
 * at every zoom, so a zoomed-out viewport paid for stored cells it could not draw and the z9/z5/z0
 * partitions the lane publishes were never read at all.
 */
export async function getParquetClimateField(
  input: Omit<ParquetViewportRead, "signal"> & {
    bbox: string;
    signal: ClimateFieldSignalId;
    variant: AirTemperatureVariant;
    abortSignal?: AbortSignal;
  }
): Promise<ParquetClimateFieldRead> {
  const day = selectedDay(input.date, input.nowMs);
  const zoomTier = resolveZoomTier(input.mapZoom);
  if (!isParquetClimateFieldSignal(input.signal)) {
    return {
      zoomTier,
      result: {
        state: "upstream_unavailable",
        fault: {
          kind: "contract",
          message: `No frozen-layout Parquet reader is registered for ${input.signal}`,
        },
      },
    };
  }
  const signal = input.signal;
  const layer = climateFieldProduct(signal, input.variant).layer;
  const result = await boundedResult(async () =>
    mapEnvelope(
      await getParquetLayerDay({
        layer,
        day,
        zoomTier,
        bbox: input.bbox,
        ...(input.abortSignal === undefined ? {} : { signal: input.abortSignal }),
      }),
      (rows) => decodeClimateFieldRows(rows, signal, input.variant, day, zoomTier)
    )
  );
  return { zoomTier, result };
}

/**
 * A soil-field collection that also declares WHICH rung answered it and what its cells stand for.
 *
 * Declared here rather than added to `PublishedSoilFieldCollection`: that interface is the
 * PostgreSQL read model's own vocabulary and is shared with a reader that has no zoom ladder.
 * Extending it locally keeps the rung and its support on every Parquet answer without asserting
 * the older reader publishes either. The same trade `ZoomedClimateFieldCollection` makes.
 */
export interface ZoomedSoilFieldCollection extends PublishedSoilFieldCollection {
  /** The one physical rung the rows came from; exactly one per request. */
  zoomTier: ZoomTier;
  /**
   * ONE envelope for the whole collection, not one per feature.
   *
   * Every feature in a soil-field answer shares the rung, the cell size, the origin semantics and
   * the attribution, and the per-feature part that does vary -- the cell's own identity -- is
   * already on each feature as `cellKey`. A copy of this object per cell would repeat five
   * constant fields up to `SOIL_FIELD_MAX_CELLS` times for no reader.
   */
  support: AggregateEnvelopeSupport;
}

/**
 * What a whole soil-field answer's cells stand for.
 *
 * `supportId` names the LATTICE rather than a cell, because that is the support unit this envelope
 * describes: one lane, one day, one rung. It is stable across pans of the same request, which is
 * what makes it usable as a cache identity.
 */
function soilFieldSupport(
  layer: string,
  zoomTier: ZoomTier,
  observedDay: string,
  rows: readonly SoilServingRow[]
): AggregateEnvelopeSupport {
  const lattice = servedCellLattice(zoomTier, LANE_BASE_LATTICES["soil-field"]);
  const newestObservedAt = rows.reduce<string | null>(
    (newest, row) =>
      newest === null || Date.parse(row.newest_observed_at) > Date.parse(newest)
        ? row.newest_observed_at
        : newest,
    null
  );
  return {
    zoomTier,
    supportKind: "tessellated_cell",
    supportId: `${layer}:${observedDay}:z${zoomTier}`,
    origin: lattice.origin,
    cellWidthDegrees: lattice.cellSizeDegrees,
    cellHeightDegrees: lattice.cellSizeDegrees,
    aggregationMethod: "mean",
    contributorCount: rows.reduce((total, row) => total + row.observation_count, 0),
    provenance: {
      sourceLayer: layer,
      observedDay,
      newestObservedAt,
      attribution: LANE_ATTRIBUTIONS["soil-field"],
    },
  };
}

function soilFieldLane(measure: SoilFieldMeasure, depth: SoilFieldDepth): string {
  if (measure === "moisture") {
    if (depth === "root-zone") return SOIL_FIELD_LANES.moisture["root-zone"];
    if (depth === "deep") return SOIL_FIELD_LANES.moisture.deep;
    return SOIL_FIELD_LANES.moisture.surface;
  }
  if (measure === "temperature") {
    return SOIL_FIELD_LANES.temperature[depth];
  }
  return SOIL_FIELD_LANES.vpd.surface;
}

function emptyParquetSoilField(
  reason: NonNullable<PublishedSoilFieldCollection["reason"]>,
  measure: SoilFieldMeasure,
  depth: SoilFieldDepth,
  layer: string,
  requestedDay: string,
  zoomTier: ZoomTier,
  newestAvailableDay: string | null
): ZoomedSoilFieldCollection {
  const definition = soilFieldMeasureDefinition(measure);
  return {
    type: "FeatureCollection",
    features: [],
    availability: "unavailable",
    reason,
    granularity: granularityForZoomTier(zoomTier),
    measure,
    depth,
    unit: definition.unit,
    attribution: SOIL_FIELD_ATTRIBUTION,
    observedDay: null,
    requestedDay,
    newestAvailableDay,
    cellCount: 0,
    truncated: false,
    maxCellCount: SOIL_FIELD_MAX_CELLS,
    maxObservationAgeDays: SOIL_FIELD_PARQUET_MAX_OBSERVATION_AGE_DAYS,
    latticeDegrees: servedCellLattice(zoomTier, LANE_BASE_LATTICES["soil-field"]).cellSizeDegrees,
    smoothingSigmaDegrees: null,
    bands: definition.bands,
    sourceClientExposureApproved: false,
    zoomTier,
    // Declared even when nothing was drawn: an empty collection still has to say which rung was
    // asked and at what pitch, or the panel cannot tell "no cells here" from "no rung answered".
    support: soilFieldSupport(layer, zoomTier, requestedDay, []),
  };
}

function decodeSoilFieldRows(
  rows: readonly Record<string, unknown>[],
  measure: SoilFieldMeasure,
  depth: SoilFieldDepth,
  signalName: string,
  unit: string,
  layer: string,
  servedDay: string,
  zoomTier: ZoomTier
): SoilServingRow[] {
  const parsed =
    measure === "moisture"
      ? parseRows(rows, climateSnapshotLineageRowSchema, layer)
      : measure === "temperature"
        ? parseRows(rows, soilTemperatureRowSchema, layer)
        : parseRows(rows, signalPlaneRowSchema, layer);
  const seenCells = new Set<string>();
  return parsed.map((row) => {
    if (
      row.support_key !== SOIL_FIELD_SUPPORT_KEY ||
      row.signal_name !== signalName ||
      row.normalized_unit !== unit ||
      row.observed_day !== servedDay ||
      (measure === "moisture" &&
        (!("source_key" in row) ||
          row.source_key !== SOIL_FIELD_SOURCE_KEY ||
          depth === "substratum" ||
          !("source_parameter" in row) ||
          row.source_parameter !== SOIL_MOISTURE_SOURCE_PARAMETERS[depth] ||
          !("source_manifest_sha256" in row) ||
          row.source_manifest_sha256 !== SNAPSHOT_SOURCE_MANIFEST_SHA256)) ||
      (measure === "temperature" &&
        (!("data_source_key" in row) ||
          row.data_source_key !== SOIL_FIELD_SOURCE_KEY ||
          !("source_parameter" in row) ||
          row.source_parameter !== SOIL_TEMPERATURE_SOURCE_PARAMETERS[depth] ||
          !("input_manifest_sha256" in row) ||
          row.input_manifest_sha256 !== SNAPSHOT_SOURCE_MANIFEST_SHA256))
    ) {
      throw contractError(`${layer} returned a row outside its registered soil-field contract`);
    }
    if ((zoomTier === BASE_ZOOM_TIER) !== (row.cell_id !== null)) {
      throw contractError(`${layer} returned invalid cell identity nullability at z${zoomTier}`);
    }
    if (
      row.cell_longitude < -180 ||
      row.cell_longitude > 180 ||
      row.cell_latitude < -90 ||
      row.cell_latitude > 90
    ) {
      throw contractError(`${layer} returned a cell outside WGS84 bounds`);
    }
    const cellKey = row.cell_id ?? `${row.cell_longitude}:${row.cell_latitude}`;
    if (seenCells.has(cellKey)) {
      throw contractError(`${layer} returned duplicate cell ${cellKey} for ${servedDay}`);
    }
    seenCells.add(cellKey);
    return row;
  });
}

/**
 * The complete tessellating cell one served soil row stands for.
 *
 * Two defects this replaces, both from re-deriving the footprint out of the ladder's own grid:
 * z9 drew a 0.01-degree speck for a quarter-degree measurement, and z5 drew 0.2-degree cells on a
 * grid that 0.25 does not divide, leaving roughly a third of the viewport as background between
 * them. `servedCellLattice` takes the coarser of the ladder's grid and the lane's base grain, so
 * both rungs now draw the quarter-degree cell the row actually describes, and the corners come
 * from the lattice index rather than from this row's own float -- which is what makes two
 * neighbours' shared edge the same double rather than merely a close one.
 */
function soilFieldPolygon(row: SoilServingRow, lattice: ServedCellLattice): GeoJSON.Polygon {
  return tessellatedCellPolygon(row.cell_longitude, row.cell_latitude, lattice);
}

/**
 * Read one soil-field viewport exclusively from its registered Parquet product lane.
 *
 * The cancellation is an intersection on the parameter rather than a field added to
 * `SoilFieldReadOptions`: that interface is the PostgreSQL read model's own vocabulary
 * (`environmental-read-model.ts`) and is shared with readers that have no socket to cancel.
 */
export async function getParquetSoilField(
  bbox: string,
  options: SoilFieldReadOptions & { signal?: AbortSignal } = {}
): Promise<ZoomedSoilFieldCollection> {
  const measure = options.measure ?? "moisture";
  const definition = soilFieldMeasureDefinition(measure);
  const requestedDepth = options.depth ?? definition.defaultDepth;
  const { depth, signalName } = soilFieldDepthDefinition(measure, requestedDepth);
  const requestedDay = selectedDay(options.date, undefined);
  const zoomTier = resolveZoomTier(options.zoom ?? BASE_ZOOM_TIER);
  const layer = soilFieldLane(measure, depth);
  if (requestedDay > currentUtcDay()) {
    return emptyParquetSoilField(
      "not_forecastable",
      measure,
      depth,
      layer,
      requestedDay,
      zoomTier,
      null
    );
  }
  const envelope = await getParquetLayerDay({
    layer,
    day: requestedDay,
    zoomTier,
    bbox,
    ...(options.signal === undefined ? {} : { signal: options.signal }),
  });
  if (envelope.state !== "published") {
    return emptyParquetSoilField(
      "not_published",
      measure,
      depth,
      layer,
      requestedDay,
      zoomTier,
      null
    );
  }
  if (envelope.servedDay !== requestedDay) {
    throw contractError(`${layer} served ${envelope.servedDay} for exact day ${requestedDay}`);
  }

  const rows = decodeSoilFieldRows(
    envelope.rows,
    measure,
    depth,
    signalName,
    definition.unit,
    layer,
    envelope.servedDay,
    zoomTier
  );
  const drawable = rows.slice(0, SOIL_FIELD_MAX_CELLS);
  const lattice = servedCellLattice(zoomTier, LANE_BASE_LATTICES["soil-field"]);
  const features = drawable.map((row): GeoJSON.Feature<GeoJSON.Polygon> => {
    const band = soilFieldBandFor(measure, row.normalized_value);
    const properties: SoilFieldFeatureProperties = {
      value: row.normalized_value,
      bandIndex: band.bandIndex,
      bandLabel: band.label,
      aggregated: zoomTier !== BASE_ZOOM_TIER,
      cellKey: row.cell_id,
      coverageFraction: row.coverage_fraction,
    };
    return {
      type: "Feature",
      id: row.cell_id ?? `${layer}:${envelope.servedDay}:${row.cell_longitude}:${row.cell_latitude}`,
      geometry: soilFieldPolygon(row, lattice),
      properties,
    };
  });
  if (features.length === 0) {
    return emptyParquetSoilField(
      "not_published",
      measure,
      depth,
      layer,
      requestedDay,
      zoomTier,
      envelope.servedDay
    );
  }
  return {
    type: "FeatureCollection",
    features,
    availability: "published",
    reason: null,
    granularity: granularityForZoomTier(zoomTier),
    measure,
    depth,
    unit: definition.unit,
    attribution: SOIL_FIELD_ATTRIBUTION,
    observedDay: envelope.servedDay,
    requestedDay,
    newestAvailableDay: null,
    cellCount: features.length,
    truncated: envelope.truncated || rows.length > SOIL_FIELD_MAX_CELLS,
    maxCellCount: SOIL_FIELD_MAX_CELLS,
    maxObservationAgeDays: SOIL_FIELD_PARQUET_MAX_OBSERVATION_AGE_DAYS,
    latticeDegrees: lattice.cellSizeDegrees,
    smoothingSigmaDegrees: null,
    bands: definition.bands,
    sourceClientExposureApproved: rows.every(
      (row) => row.allowed_client_exposure === true
    ),
    zoomTier,
    support: soilFieldSupport(layer, zoomTier, envelope.servedDay, drawable),
  };
}

export async function getParquetWaterGauges(
  input: ParquetViewportRead & { bbox: string }
): Promise<ParquetReaderResult<readonly ParquetWaterGauge[]>> {
  const nowMs = input.nowMs ?? Date.now();
  const { day, request } = commonRequest({ ...input, nowMs }, "water-gauges");
  if (day !== currentUtcDay(nowMs)) {
    return boundedResult(async () =>
      mapEnvelope(await getParquetLayerDay(request), (rows) =>
        newestWaterRows(decodeWaterRows(rows, request.zoomTier))
      )
    );
  }

  return boundedResult(async () => {
    const firstDay = addUtcDays(day, -1);
    const envelopes = await getParquetLayerDayWindow({
      layer: "water-gauges",
      firstDay,
      lastDay: day,
      zoomTier: request.zoomTier,
      bbox: input.bbox,
      ...(input.signal === undefined ? {} : { signal: input.signal }),
    });
    const days = envelopes.map((envelope) =>
      mapEnvelope(envelope, (rows) => decodeWaterRows(rows, request.zoomTier))
    );
    const published = days.filter(
      (entry): entry is Extract<(typeof days)[number], { state: "ready" }> => entry.state === "ready"
    );
    if (published.length === 0) return windowTerminal(days, firstDay, day);
    const data = newestWaterRows(
      published
        .flatMap((entry) => entry.data)
        .filter((row) => isFreshObservation(row.observedAt, WATER_LIVE_MAX_AGE_MS, nowMs))
    );
    if (data.length === 0) {
      const current = days.find((entry) =>
        "requestedDay" in entry ? entry.requestedDay === day : false
      );
      if (current === undefined) {
        throw contractError(`water-gauges window omitted its requested current day ${day}`);
      }
      if (published.some((entry) => entry.truncated)) {
        return {
          state: "upstream_unavailable",
          fault: {
            kind: "contract",
            message: "The truncated water live window could not prove whether a fresh row exists",
          },
        };
      }
      if (current.state === "ready") return { ...current, data: [] };
      return current;
    }
    const freshest = data.reduce((newest, row) =>
      Date.parse(row.observedAt) > Date.parse(newest.observedAt) ? row : newest
    );
    return {
      state: "ready",
      requestedDay: day,
      servedDay: freshest.observedDay,
      data,
      truncated: published.some((entry) => entry.truncated),
    };
  });
}

export async function getParquetWeatherObservations(
  input: ParquetViewportRead & { bbox: string }
): Promise<ParquetReaderResult<readonly ParquetWeatherObservation[]>> {
  const nowMs = input.nowMs ?? Date.now();
  const { day, request } = commonRequest({ ...input, nowMs }, "weather-observations");
  if (day !== currentUtcDay(nowMs)) {
    return boundedResult(async () =>
      mapEnvelope(await getParquetLayerDay(request), (rows) =>
        newestWeatherRows(decodeWeatherRows(rows, request.zoomTier))
      )
    );
  }

  return boundedResult(async () => {
    const firstDay = addUtcDays(day, -1);
    const envelopes = await getParquetLayerDayWindow({
      layer: "weather-observations",
      firstDay,
      lastDay: day,
      zoomTier: request.zoomTier,
      bbox: input.bbox,
      ...(input.signal === undefined ? {} : { signal: input.signal }),
    });
    const days = envelopes.map((envelope) =>
      mapEnvelope(envelope, (rows) => decodeWeatherRows(rows, request.zoomTier))
    );
    const published = days.filter(
      (entry): entry is Extract<(typeof days)[number], { state: "ready" }> => entry.state === "ready"
    );
    if (published.length === 0) return windowTerminal(days, firstDay, day);

    const data = newestWeatherRows(
      published
        .flatMap((entry) => entry.data)
        .filter((row) => isFreshObservation(row.observedAt, WEATHER_LIVE_MAX_AGE_MS, nowMs))
    );
    if (data.length === 0) {
      const current = days.find((entry) =>
        "requestedDay" in entry ? entry.requestedDay === day : false
      );
      if (current === undefined) {
        throw contractError(`weather-observations window omitted its requested current day ${day}`);
      }
      if (published.some((entry) => entry.truncated)) {
        return {
          state: "upstream_unavailable",
          fault: {
            kind: "contract",
            message: "The truncated weather live window could not prove whether a fresh row exists",
          },
        };
      }
      if (current.state === "ready") return { ...current, data: [] };
      return current;
    }
    const freshest = data.reduce((newest, row) =>
      Date.parse(row.observedAt) > Date.parse(newest.observedAt) ? row : newest
    );
    return {
      state: "ready",
      requestedDay: day,
      servedDay: freshest.observedDay,
      data,
      truncated: published.some((entry) => entry.truncated),
    };
  });
}

export async function getParquetDrought(
  input: ParquetViewportRead
): Promise<ParquetReaderResult<readonly ParquetDroughtArea[]>> {
  const nowMs = input.nowMs ?? Date.now();
  const day = selectedDay(input.date, nowMs);
  rejectFutureDay(day, nowMs, "drought");
  const zoomTier = resolveZoomTier(input.mapZoom);
  return boundedResult(async () => {
    const releaseRequest = {
      layer: "drought",
      zoomTier,
      ...(input.bbox === undefined ? {} : { bbox: input.bbox }),
      ...(input.signal === undefined ? {} : { signal: input.signal }),
    } as const;
    const envelope = await getParquetLatestRelease({
      ...releaseRequest,
      asOfDay: day,
    });
    const result = mapEnvelope(
      envelope,
      (rows) =>
        parseRows(rows, droughtRowSchema, "drought").map((row) => ({
          areaId: row.area_id,
          validDate: row.valid_date,
          droughtCategory: row.dm_category as 0 | 1 | 2 | 3 | 4,
          sourceUrl: row.source_url,
          ingestedAt: row.ingested_at,
          geometry: decodePolygonGeometry(row.geom, "drought"),
        }))
    );
    if (result.state !== "ready" && result.state !== "absent") return result;

    const requestedMs = envelopeDayMs(day, "requested");
    const servedMs = envelopeDayMs(result.servedDay, "served");
    const carryForwardDays = Math.round((requestedMs - servedMs) / DAY_MS);
    if (carryForwardDays < 0) {
      throw contractError(`drought release ${result.servedDay} is after requested day ${day}`);
    }
    if (result.state === "ready" && result.data.some((row) => row.validDate !== result.servedDay)) {
      throw contractError("drought rows do not match the release day reported by the Parquet plane");
    }

    const today = currentUtcDay(nowMs);
    let maxCarryForwardDays = DROUGHT_MAX_CARRY_FORWARD_DAYS;
    if (day < today) {
      const latestEnvelope = await getParquetLatestRelease({
        ...releaseRequest,
        asOfDay: today,
      });
      const latestServedDay = releaseServedDay(latestEnvelope, today);
      const latestServedMs = envelopeDayMs(latestServedDay, "latest served");
      if (latestServedMs > envelopeDayMs(today, "latest requested")) {
        throw contractError(`drought release ${latestServedDay} is after server UTC today ${today}`);
      }
      if (latestServedMs > servedMs && latestServedMs <= requestedMs) {
        throw contractError(
          `drought lookup for ${day} skipped the newer stored release ${latestServedDay}`
        );
      }
      if (latestServedMs > servedMs) {
        maxCarryForwardDays = DROUGHT_RELEASE_INTERVAL_DAYS - 1;
      }
    }
    if (carryForwardDays > maxCarryForwardDays) {
      return { state: "not_generated", requestedDay: day, reason: "day_not_written" };
    }
    return result;
  });
}

/**
 * Every published Oregon OEM evacuation area as of the requested day.
 *
 * `static_lookup`, and the export is a FULL re-snapshot per release day with no date predicate
 * (`sql/pipeline/evacuation_zones_day_export.sql:14-22`), so the newest release at or before the
 * day IS the standing set -- the same population `geo.evacuation_zone_tiles()` served, which that
 * export transcribes clause for clause. Nothing is carried forward and nothing is unioned: one
 * release answers the whole layer.
 */
export async function getParquetEvacuationZones(
  input: ParquetViewportRead
): Promise<ParquetReaderResult<readonly ParquetEvacuationZone[]>> {
  const nowMs = input.nowMs ?? Date.now();
  const day = selectedDay(input.date, nowMs);
  rejectFutureDay(day, nowMs, "evacuation-zones");
  const zoomTier = resolveZoomTier(input.mapZoom);
  return boundedResult(async () =>
    mapEnvelope(
      await getParquetLatestRelease({
        layer: "evacuation-zones",
        asOfDay: day,
        zoomTier,
        ...(input.bbox === undefined ? {} : { bbox: input.bbox }),
        ...(input.signal === undefined ? {} : { signal: input.signal }),
      }),
      (rows) =>
        parseRows(rows, evacuationZoneRowSchema, "evacuation-zones").map((row) => ({
          naturalKey: row.natural_key,
          snapshotDay: row.snapshot_day,
          evacuationAreaName: row.evacuation_area_name,
          fireName: row.fire_name,
          county: row.county,
          hazardType: row.hazard_type,
          evacuationLevel: row.evacuation_level,
          evacuationLevelLabel: row.evacuation_level_label,
          severity: row.severity,
          structuresWithin: row.structures_within,
          populationWithin: row.population_within,
          observedAt: row.observed_at,
          geometry: decodePolygonGeometry(row.geometry_wkb, "evacuation-zones"),
        }))
    )
  );
}

/**
 * The rows of one resolved snapshot that are IN FRAME at the day a caller asked for.
 *
 * Two properties, both load-bearing, both transcribed from
 * `planes/fire_perimeters.py:269`'s `observed_day IS NULL OR observed_day <= as_of`:
 *
 * 1. AN UNDATED INCIDENT IS NEVER EXCLUDED. It matches `tile-layer-date-filter.ts:44-53` --
 *    `["any", ["!", ["has", "observed_day"]], ["<=", ["get", "observed_day"], selectedDate]]` --
 *    which keeps a row `geo.feature_observation_day` could not date at every slider date. The
 *    retired `daily_series` export deleted those rows outright, because its `= :observed_day`
 *    predicate can never match NULL, so it served strictly fewer perimeters than Martin drew.
 * 2. THE COMPARISON IS AGAINST `asOfDay`, THE DAY THE CALLER ASKED FOR -- never against the
 *    served snapshot's own capture day. The two differ whenever the newest snapshot at or before
 *    the request is older than the request, which is the normal case for a lane written by a
 *    cron. `asOfDay` is the value `tileLayerDateFilter` would have compared client-side, so
 *    comparing against the capture day instead would quietly answer a different question -- and
 *    an `=== observedDay` equality would be the retired lane's bug, restored.
 *
 * A pure day-string comparison, never `Date.parse`: `YYYY-MM-DD` sorts lexicographically, and the
 * named-day rule (`parquet-plane-client.ts` header) forbids turning a published day into an
 * instant anywhere on this path.
 */
function firePerimetersInFrame<T extends { observed_day: string | null }>(
  rows: readonly T[],
  asOfDay: string
): T[] {
  return rows.filter((row) => row.observed_day === null || row.observed_day <= asOfDay);
}

/**
 * Every WFIGS incident that was current as of the requested day, from the newest snapshot the
 * lane captured at or before it.
 *
 * `static_lookup` since the lane's 2026-09-04 re-registration
 * (`warehouse/schemas/fire_perimeters.py`), and that re-registration is what made this reader
 * possible at all: `geo.features` holds one row per incident refreshed IN PLACE, so one published
 * snapshot IS the standing set that `geo.fire_risk_tiles()` drew. While the lane was registered
 * `daily_series` on a per-incident `observed_day`, its 177 perimeters sat across 45 partition
 * days and no bounded read reproduced their union.
 *
 * THE SNAPSHOT-RESOLUTION RULE IS NOT RE-IMPLEMENTED HERE. `getParquetLatestRelease` reaches
 * `resolve_release` (`parquet_ops/serving.py:154`), whose "newest day at or before `as_of`,
 * reported at the release's OWN day" is the same rule `resolve_fire_perimeters_as_of` states for
 * the Polars path, down to treating a conflict day as a refusal rather than falling back to an
 * older clean one. What this reader owes on top of it is the IN-FRAME filter, which the wire's
 * generic release route does not apply -- see `firePerimetersInFrame` for the two properties that
 * filter must have.
 *
 * `servedDay` is therefore the SNAPSHOT day and `requestedDay` the slider day, exactly as they are
 * for evacuation zones; a gap between them means the map is drawing the newest capture at or
 * before the requested day, not a same-day reading, which is the only honest answer a snapshot
 * source can give about the past.
 */
export async function getParquetFirePerimeters(
  input: ParquetViewportRead
): Promise<ParquetReaderResult<readonly ParquetFirePerimeter[]>> {
  const nowMs = input.nowMs ?? Date.now();
  const day = selectedDay(input.date, nowMs);
  rejectFutureDay(day, nowMs, "fire-perimeters");
  const zoomTier = resolveZoomTier(input.mapZoom);
  return boundedResult(async () =>
    mapEnvelope(
      await getParquetLatestRelease({
        layer: "fire-perimeters",
        asOfDay: day,
        zoomTier,
        ...(input.bbox === undefined ? {} : { bbox: input.bbox }),
        ...(input.signal === undefined ? {} : { signal: input.signal }),
      }),
      (rows) =>
        // `day`, the REQUESTED day, is the only date this filter may read. The served snapshot day
        // is deliberately out of scope here -- it is not in this closure's argument list at all.
        firePerimetersInFrame(
          parseRows(rows, firePerimeterRowSchema, "fire-perimeters"),
          day
        ).map((row) => ({
          featureId: row.feature_id,
          uniqueFireIdentifier: row.unique_fire_identifier,
          snapshotDay: row.snapshot_day,
          observedDay: row.observed_day,
          severity: row.severity,
          geometry: decodePolygonGeometry(row.geometry_wkb, "fire-perimeters"),
        }))
    )
  );
}

/**
 * The USGS WBD basin set at the rung the camera can draw, as of the requested day.
 *
 * `static_lookup` and a full snapshot for the same reason evacuation zones are: the export carries
 * no date predicate at all, because a HUC12 boundary is re-keyed in place rather than resampled
 * (`sql/pipeline/watersheds_day_export.sql:13-22`). `hucLevel` is the code's own length, so a HUC6
 * rollup can never be captioned as a HUC12 -- the guarantee `geo.watershed_rollup.huc_level` made
 * as a stored column, made instead from the value it described.
 *
 * The rung mapping is NOT identical to the retired tile function's, and the difference is a
 * property of the ladder rather than of this reader: `geo.watershed_tiles()` routed z>=10 to HUC12,
 * z>=8 to HUC10, z>=6 to HUC8, z>=4 to HUC6 and below that HUC4
 * (`drizzle/0023_watershed_zoom_generalization.sql:150-153`), while the four published rungs are
 * z13=HUC12, z9=HUC10, z5=HUC8, z0=HUC6. So z10-z12 now draws HUC10 where it drew HUC12, and z0-z3
 * draws HUC6 where it drew HUC4. There is no HUC4 rung to ask for; the ladder's floor is HUC6.
 */
export async function getParquetWatersheds(
  input: ParquetViewportRead
): Promise<ParquetReaderResult<readonly ParquetWatershed[]>> {
  const nowMs = input.nowMs ?? Date.now();
  const day = selectedDay(input.date, nowMs);
  rejectFutureDay(day, nowMs, "watersheds");
  const zoomTier = resolveZoomTier(input.mapZoom);
  return boundedResult(async () =>
    mapEnvelope(
      await getParquetLatestRelease({
        layer: "watersheds",
        asOfDay: day,
        zoomTier,
        ...(input.bbox === undefined ? {} : { bbox: input.bbox }),
        ...(input.signal === undefined ? {} : { signal: input.signal }),
      }),
      (rows) =>
        parseRows(rows, watershedRowSchema, "watersheds").map((row) => ({
          huc: row.huc12,
          hucLevel: row.huc12.length,
          name: row.name,
          areaSquareKm: row.areasqkm,
          toHuc: row.tohuc,
          states: row.states,
          huType: row.hutype,
          releaseDay: row.release_day,
          observedAt: row.observed_at,
          geometry: decodePolygonGeometry(row.geom, "watersheds"),
        }))
    )
  );
}

/**
 * How many MTBS releases one burn-severity answer may union before it reports itself truncated.
 *
 * MTBS publishes roughly one release a year and the warehouse holds four of them, so twelve is
 * several times the standing history rather than a guess at it. It is a CEILING on round trips,
 * not a retention rule: the walk stops at the lane's floor long before it, and a lane that ever
 * grows past it answers `truncated` rather than silently dropping its oldest scars.
 */
const BURN_SEVERITY_MAX_RELEASES = 12;

/**
 * Every MTBS burned-area boundary published at or before the requested day.
 *
 * This is the one reader here that unions several releases, and it does so because the export is
 * SCOPED TO ONE RELEASE DAY -- "the tile function serves the whole layer at every zoom; this query
 * answers only the rows dated to `release_day`"
 * (`sql/pipeline/burn_severity_day_export.sql:20-21`). The 541 published scars are therefore spread
 * across the lane's release days, and a single `getParquetLatestRelease` would draw only the newest
 * release: last year's fire scars would vanish from a map that has always drawn them.
 *
 * The walk is the release resolver used as designed rather than a new path: each answer reports its
 * own `servedDay`, and asking again for the day before it yields the previous release, terminating
 * at `day_not_written` once the lane's floor is passed. It reproduces exactly the set
 * `geo.burn_severity_tiles()` plus the `observed_day <= day` style filter drew, at a bounded number
 * of round trips instead of one 37.5 MB, 28.4-second unsimplified read.
 */
export async function getParquetBurnSeverity(
  input: ParquetViewportRead
): Promise<ParquetReaderResult<readonly ParquetBurnScar[]>> {
  const nowMs = input.nowMs ?? Date.now();
  const day = selectedDay(input.date, nowMs);
  rejectFutureDay(day, nowMs, "burn-severity");
  const zoomTier = resolveZoomTier(input.mapZoom);
  const releaseRequest = {
    layer: "burn-severity",
    zoomTier,
    ...(input.bbox === undefined ? {} : { bbox: input.bbox }),
    ...(input.signal === undefined ? {} : { signal: input.signal }),
  } as const;

  return boundedResult(async () => {
    const scars: ParquetBurnScar[] = [];
    let newestServedDay: string | null = null;
    let firstAnswer: ParquetReaderResult<ParquetBurnScar[]> | null = null;
    let asOfDay = day;
    let truncated = false;

    for (let release = 0; release < BURN_SEVERITY_MAX_RELEASES; release += 1) {
      const answer = mapEnvelope(
        await getParquetLatestRelease({ ...releaseRequest, asOfDay }),
        (rows) =>
          parseRows(rows, burnSeverityRowSchema, "burn-severity").map((row) => ({
            fireId: row.fire_id,
            fireName: row.fire_name,
            fireYear: row.fire_year,
            fireType: row.fire_type,
            assessmentType: row.assessment_type,
            ignitionDate: row.ignition_date,
            observedDay: row.observed_day,
            acres: row.acres,
            severityClass: row.severity_class,
            dataAvailableAt: row.data_available_at,
            geometry: decodePolygonGeometry(row.geom, "burn-severity"),
          }))
      );
      firstAnswer ??= answer;
      // A governed absence or an unwritten day ENDS the walk rather than failing it: the releases
      // already collected are a true statement about the day, and the older ones simply stop.
      if (answer.state !== "ready") break;
      newestServedDay ??= answer.servedDay;
      scars.push(...answer.data);
      truncated ||= answer.truncated;
      // The release before this one. Day arithmetic on the SERVED day, never the requested one:
      // asking `requestedDay - 1` again would re-serve the release just read, forever. The walk
      // needs no floor of its own -- once `asOfDay` drops below the lane's first release the plane
      // answers `day_not_written` in one call and the loop above ends.
      asOfDay = addUtcDays(answer.servedDay, -1);
      if (release === BURN_SEVERITY_MAX_RELEASES - 1) truncated = true;
    }

    // Nothing published at or before the day: report the plane's own first answer, whatever it
    // said, so an absence stays an absence and an unwritten lane stays unwritten.
    if (newestServedDay === null) {
      return firstAnswer ?? { state: "not_generated", requestedDay: day, reason: "day_not_written" };
    }
    // The served day is the NEWEST release in the union, which is the day the map is drawing: an
    // older member does not make the answer older than its freshest release.
    return { state: "ready", requestedDay: day, servedDay: newestServedDay, data: scars, truncated };
  });
}

/**
 * The published sensor roster for one day, one feature per station rather than per measurement.
 *
 * The lane is tall by design -- one row per `(sensor_id, observed_day, measurement_name)`, sixteen
 * NWS fields where `geo.sensor_tiles()` projected four -- so the collapse here is what keeps the
 * station COUNT equal to the tile function's `DISTINCT ON (sensor_id, geom, observation_day)`
 * rather than drawing sixteen coincident dots per station.
 *
 * At a coarse rung `sensor_id` and `station_name` are null by construction (`GridAggregation` nulls
 * what no single station can claim), so the merge key falls back to the cell's own coordinates.
 * That is the honest key there: the row IS a cell of several stations, and giving it a station
 * identity would be the fabricated-identity bug this lane's aggregations exist to avoid.
 */
export async function getParquetSensorStations(
  input: ParquetViewportRead
): Promise<ParquetReaderResult<readonly ParquetSensorStation[]>> {
  const nowMs = input.nowMs ?? Date.now();
  const { day, request } = commonRequest({ ...input, nowMs }, "sensors");
  rejectFutureDay(day, nowMs, "sensors");
  return boundedResult(async () =>
    mapEnvelope(await getParquetLayerDay(request), (rows) =>
      collapseSensorRows(parseRows(rows, sensorRowSchema, "sensors"))
    )
  );
}

/** One station per merge key, carrying every measurement it reported and its newest reading. */
function collapseSensorRows(
  rows: readonly z.infer<typeof sensorRowSchema>[]
): ParquetSensorStation[] {
  const stations = new Map<string, ParquetSensorStation>();
  for (const row of rows) {
    // A row with no coordinates cannot be drawn, and never was: `geo.sensor_tiles()` required
    // `f.geom IS NOT NULL`. Dropped rather than plotted at a fabricated origin.
    if (row.station_longitude === null || row.station_latitude === null) continue;
    const key = row.sensor_id ?? `${row.station_longitude}:${row.station_latitude}`;
    const existing = stations.get(key);
    const measurement = {
      name: row.measurement_name,
      value: row.value,
      unitCode: row.unit_code,
      observedAt: row.observed_at,
    };
    if (existing === undefined) {
      stations.set(key, {
        sensorId: row.sensor_id,
        stationName: row.station_name,
        network: row.network,
        observedDay: row.observed_day,
        observedAt: row.observed_at,
        longitude: row.station_longitude,
        latitude: row.station_latitude,
        measurements: [measurement],
      });
      continue;
    }
    stations.set(key, {
      ...existing,
      // The station's own timestamp is the NEWEST reading it filed that day, matching the
      // `observedAt DESC` tie-break `geo.sensor_tiles()` used to pick its winning row.
      observedAt:
        Date.parse(row.observed_at) > Date.parse(existing.observedAt)
          ? row.observed_at
          : existing.observedAt,
      network: existing.network ?? row.network,
      stationName: existing.stationName ?? row.station_name,
      measurements: [...existing.measurements, measurement],
    });
  }
  return [...stations.values()];
}

function windowTerminal<T>(
  days: readonly ParquetReaderResult<T>[],
  firstDay: string,
  lastDay: string
): Exclude<ParquetReaderResult<never>, { state: "ready" | "upstream_unavailable" }> {
  const missing = [...days]
    .reverse()
    .find((day): day is Extract<ParquetReaderResult<T>, { state: "not_generated" }> => day.state === "not_generated");
  if (missing !== undefined) return missing;
  const absent = [...days]
    .reverse()
    .find((day): day is Extract<ParquetReaderResult<T>, { state: "absent" }> => day.state === "absent");
  if (absent !== undefined) return absent;
  throw contractError(`Parquet plane described no state in the window ${firstDay}..${lastDay}`);
}

export async function getParquetVegetation(
  input: ParquetViewportRead & { bbox: string }
): Promise<ParquetReaderResult<ParquetVegetationWindow>> {
  const nowMs = input.nowMs ?? Date.now();
  const lastDay = selectedDay(input.date, nowMs);
  rejectFutureDay(lastDay, nowMs, "vegetation");
  const firstDay = addUtcDays(lastDay, -(VEGETATION_TRAILING_DAYS - 1));
  const zoomTier = resolveZoomTier(input.mapZoom);
  return boundedResult(async () => {
    const envelopes = await getParquetLayerDayWindow({
      layer: "vegetation",
      firstDay,
      lastDay,
      zoomTier,
      bbox: input.bbox,
      ...(input.signal === undefined ? {} : { signal: input.signal }),
    });
    const days = envelopes.map((envelope) =>
      mapEnvelope(envelope, (rows) =>
        parseRows(rows, vegetationRowSchema, "vegetation").map((row) => ({
          cellId: row.cell_id,
          gridName: row.grid_name,
          metricName: row.metric_name,
          metricUnit: row.metric_unit,
          observedDay: row.observed_day,
          metricValue: row.metric_value,
          observationChecksum: row.observation_checksum,
          dataAvailableAt: row.data_available_at,
          releaseCount: row.release_count,
          allowedClientExposure: row.allowed_client_exposure,
          longitude: row.cell_longitude,
          latitude: row.cell_latitude,
          support: cellSupport({
            lane: "vegetation",
            zoomTier,
            // `tessellated_cell`, never `raw_point`: `LAYER_RENDER_CONTRACT` pins this lane's
            // support at 0.25 degrees and permits only the cell at every band. A centre dot for a
            // quarter-degree measurement is the fictitious footprint that entry exists to forbid.
            supportKind: "tessellated_cell",
            aggregationMethod: "mean",
            contributorCount: row.release_count,
            cellId: row.cell_id,
            longitude: row.cell_longitude,
            latitude: row.cell_latitude,
            observedDay: row.observed_day,
            // The lane publishes an AVAILABILITY instant (`data_available_at`), never an
            // observation one, and the two are not interchangeable -- so the envelope says null
            // rather than passing off a publication time as a measurement time.
            newestObservedAt: null,
          }),
        }))
      )
    );
    const published = days.filter(
      (day): day is Extract<(typeof days)[number], { state: "ready" }> => day.state === "ready"
    );
    if (published.length === 0) return windowTerminal(days, firstDay, lastDay);

    const newestByCell = new Map<string, ParquetVegetationObservation>();
    for (const day of published) {
      for (const row of day.data) {
        const key = row.cellId ?? `${row.longitude}:${row.latitude}`;
        newestByCell.set(key, row);
      }
    }
    const newest = published.at(-1)!;
    return {
      state: "ready",
      requestedDay: lastDay,
      servedDay: newest.servedDay,
      data: { firstDay, lastDay, observations: [...newestByCell.values()], days },
      truncated: published.some((day) => day.truncated),
    };
  });
}

export async function getParquetFireDetections(
  input: ParquetViewportRead & { dayRange?: number }
): Promise<ParquetReaderResult<ParquetFireWindow>> {
  const lastDay = selectedDay(input.date, input.nowMs);
  const requestedRange = input.date === undefined ? input.dayRange ?? 1 : 1;
  if (!Number.isInteger(requestedRange) || requestedRange < 1 || requestedRange > 10) {
    throw new ParquetPlaneRequestError(`dayRange must be an integer from 1 through 10, got ${requestedRange}`);
  }
  const firstDay = addUtcDays(lastDay, -(requestedRange - 1));
  const zoomTier = resolveZoomTier(input.mapZoom);
  return boundedResult(async () => {
    const envelopes = await getParquetLayerDayWindow({
      layer: "fire-detections",
      firstDay,
      lastDay,
      zoomTier,
      ...(input.bbox === undefined ? {} : { bbox: input.bbox }),
      ...(input.signal === undefined ? {} : { signal: input.signal }),
    });
    const days = envelopes.map((envelope) =>
      mapEnvelope(envelope, (rows) =>
        parseRows(rows, fireDetectionRowSchema, "fire-detections").map((row) => ({
          longitude: row.cell_longitude,
          latitude: row.cell_latitude,
          observedDay: row.observed_day,
          detectionCount: row.detection_count,
          frpSum: row.frp_sum,
          frpObservationCount: row.frp_observation_count,
          highConfidenceDetectionCount: row.high_confidence_detection_count,
          newestObservedAt: row.newest_observed_at,
          support: cellSupport({
            lane: "fire-detections",
            zoomTier,
            supportKind: "aggregate_cell",
            aggregationMethod: "count",
            contributorCount: row.detection_count,
            // The lane publishes no cell identity at any rung: its grain IS the snapped
            // coordinate pair, so the id is minted from the rung and that pair.
            cellId: null,
            longitude: row.cell_longitude,
            latitude: row.cell_latitude,
            observedDay: row.observed_day,
            newestObservedAt: row.newest_observed_at,
          }),
        }))
      )
    );
    const published = days.filter(
      (day): day is Extract<(typeof days)[number], { state: "ready" }> => day.state === "ready"
    );
    if (published.length === 0) return windowTerminal(days, firstDay, lastDay);
    const newest = published.at(-1)!;
    return {
      state: "ready",
      requestedDay: lastDay,
      servedDay: newest.servedDay,
      data: { firstDay, lastDay, cells: published.flatMap((day) => day.data), days },
      truncated: published.some((day) => day.truncated),
    };
  });
}
