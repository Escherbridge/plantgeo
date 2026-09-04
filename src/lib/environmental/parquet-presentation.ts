import type { WaterGauge } from "@/lib/environmental/water";
import {
  isAggregateSupportKind,
  supportCellPolygon,
  type AggregateEnvelopeSupport,
  type SupportKind,
} from "@/lib/map/layer-render-contract";

/** Browser-safe mirror of the four public tRPC states. */
export type ParquetBrowserReaderResult<T> =
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
      evidence: {
        reason: string;
        upstreamResponse: string;
        recordedAt: string;
        runId: string;
      };
    }
  | {
      state: "not_generated";
      requestedDay: string;
      reason: "day_not_written" | "lane_never_written";
    }
  | {
      state: "upstream_unavailable";
      fault: { kind: string; message: string; status?: number };
    };

/**
 * Browser mirror of one served streamflow row.
 *
 * `support` is REQUIRED, here as on the wire: `getStreamflow` declares `raw_point` for a real z13
 * gauge and `aggregate_cell` for a coarse-rung mean, and there is no row it does not declare one
 * for. It was optional until 2026-09-02 so that loosely typed mocks would compile, which is
 * exactly the wrong trade: an envelope-less row silently presented as the wave-1 marker, and the
 * one thing that can tell a real gauge from a cell of several went missing without a compile
 * error anywhere. A renderer may still draw a marker -- for a `raw_point`, whose envelope
 * declares no cell size at all -- but only because the envelope SAID so.
 */
export interface ParquetBrowserWaterGauge {
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
  support: AggregateEnvelopeSupport;
}

export interface ParquetBrowserDroughtArea {
  areaId: string;
  validDate: string;
  droughtCategory: 0 | 1 | 2 | 3 | 4;
  sourceUrl: string;
  ingestedAt: string;
  geometry: GeoJSON.Polygon | GeoJSON.MultiPolygon;
}

/** Browser mirror of one published Oregon OEM evacuation area. */
export interface ParquetBrowserEvacuationZone {
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
  geometry: GeoJSON.Polygon | GeoJSON.MultiPolygon;
}

/**
 * Browser mirror of one WFIGS incident, as of the snapshot that answered the request.
 *
 * `observedDay` is nullable here because it is nullable in the warehouse, and that nullability is
 * the whole subtlety of this layer: an incident WFIGS gave no parseable timestamp is drawn at
 * every slider date rather than hidden. `snapshotDay` is the version that answered and is never
 * confused for it -- one says when the population was captured, the other when the fire was seen.
 */
export interface ParquetBrowserFirePerimeter {
  featureId: string;
  uniqueFireIdentifier: string;
  snapshotDay: string;
  observedDay: string | null;
  severity: string | null;
  geometry: GeoJSON.Polygon | GeoJSON.MultiPolygon;
}

/** Browser mirror of one MTBS burned-area boundary. */
export interface ParquetBrowserBurnScar {
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
  geometry: GeoJSON.Polygon | GeoJSON.MultiPolygon;
}

/** Browser mirror of one basin, at whichever rung of the HUC hierarchy served the camera. */
export interface ParquetBrowserWatershed {
  huc: string;
  hucLevel: number;
  name: string | null;
  areaSquareKm: number | null;
  toHuc: string | null;
  states: string | null;
  huType: string | null;
  releaseDay: string;
  observedAt: string | null;
  geometry: GeoJSON.Polygon | GeoJSON.MultiPolygon;
}

/** Browser mirror of one station-day, collapsed from the lane's tall measurement grain. */
export interface ParquetBrowserSensorStation {
  sensorId: string | null;
  stationName: string | null;
  network: string | null;
  observedDay: string;
  observedAt: string;
  longitude: number;
  latitude: number;
  measurements: readonly {
    name: string;
    value: number;
    unitCode: string | null;
    observedAt: string;
  }[];
}

/**
 * Browser mirror of one measured NDVI observation.
 *
 * `support` declares the 0.25-degree square this platform actually observed, and is REQUIRED on
 * the same rule as the streamflow mirror above. The size is read off it and never off the
 * contract's own `declaredSupportDegrees`: assuming the number the contract states would be the
 * client inferring support again, one indirection further out.
 */
export interface ParquetBrowserVegetationObservation {
  cellId: string | null;
  gridName: string;
  metricName: string;
  metricUnit: string;
  observedDay: string;
  metricValue: number;
  observationChecksum: string | null;
  dataAvailableAt: string;
  releaseCount: number;
  allowedClientExposure: boolean;
  longitude: number;
  latitude: number;
  support: AggregateEnvelopeSupport;
}

export interface ParquetBrowserVegetationWindow {
  firstDay: string;
  lastDay: string;
  observations: readonly ParquetBrowserVegetationObservation[];
}

/**
 * Browser mirror of one served Open-Meteo observation.
 *
 * `support` is required for the same reason it is on the two mirrors above, and it says the same
 * thing as the streamflow one because the lane has the same shape: a sampled point at the detail
 * rung, and a cell of the ladder's own pitch on every rung the derivation floored it onto. The
 * contract classes `weather` as `event_point`, so the detail rung declares no cell size and the
 * layer keeps drawing its dot -- what changes is that the dot's rung, identity and provenance are
 * now stated rather than assumed.
 */
export interface ParquetBrowserWeatherObservation {
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
  support: AggregateEnvelopeSupport;
}

/**
 * An anonymous coarse-rung mean over the square its envelope declares.
 *
 * `support` is what turns it from a dot at a centroid into a drawn cell: `WaterLayer` builds the
 * polygon from it and captions the cell with `contributorCount` gauges. Never null -- every row
 * this presenter reads declares one, and a cell with no declared footprint would be a purple
 * square drawn over ground nothing measured.
 */
export interface WaterGaugeCell {
  latitude: number;
  longitude: number;
  flowCfs: number | null;
  observedAt: string;
  observedDay: string;
  source: string;
  support: AggregateEnvelopeSupport;
}

export interface WaterGaugePresentation {
  gauges: WaterGauge[];
  cells: WaterGaugeCell[];
  unlocatedRows: number;
}

const EMPTY_WATER_PRESENTATION: WaterGaugePresentation = {
  gauges: [],
  cells: [],
  unlocatedRows: 0,
};

function waterCondition(value: string | null): WaterGauge["condition"] {
  return value === "above_normal" ||
    value === "normal" ||
    value === "below_normal" ||
    value === "low" ||
    value === "critically_low"
    ? value
    : "unknown";
}

/**
 * Separates named z13 gauges from anonymous z9/z5/z0 cells without inventing identities.
 *
 * The DECLARED form is what splits them: a row whose envelope names an aggregate form is a cell,
 * whatever ids it happens to carry. That is the contract's own correction of the rule this
 * function used to apply -- "a null site number means aggregate" is exactly the inference from a
 * missing id that `AggregateEnvelopeSupport` replaces, and it made a rung whose rows happened to
 * carry ids indistinguishable from real gauges.
 *
 * The site number stays load-bearing for the other half: a row may only be drawn as a NAMED gauge
 * if it has a name to draw. A row declaring `raw_point` with no site number is therefore still
 * presented as an anonymous cell rather than as a gauge captioned with an empty string.
 */
export function presentParquetWater(
  result: ParquetBrowserReaderResult<readonly ParquetBrowserWaterGauge[]> | undefined
): WaterGaugePresentation {
  if (result?.state !== "ready") return EMPTY_WATER_PRESENTATION;

  const gauges: WaterGauge[] = [];
  const cells: WaterGaugeCell[] = [];
  let unlocatedRows = 0;
  for (const row of result.data) {
    if (row.latitude === null || row.longitude === null) {
      unlocatedRows += 1;
      continue;
    }
    const declaredAggregate = isAggregateSupportKind(row.support.supportKind);
    if (declaredAggregate || row.siteNumber === null) {
      cells.push({
        latitude: row.latitude,
        longitude: row.longitude,
        flowCfs: row.flowCfs,
        observedAt: row.observedAt,
        observedDay: row.observedDay,
        source: row.source,
        support: row.support,
      });
      continue;
    }
    gauges.push({
      siteNo: row.siteNumber,
      siteName: row.siteName ?? "",
      lat: row.latitude,
      lon: row.longitude,
      flowCfs: row.flowCfs,
      percentile: row.percentile,
      condition: waterCondition(row.condition),
      trend:
        row.trend === "rising" || row.trend === "stable" || row.trend === "declining"
          ? row.trend
          : null,
      updatedAt: row.observedAt,
    });
  }
  return { gauges, cells, unlocatedRows };
}

/** Converts a published USDM release to the existing browser-safe GeoJSON presentation. */
export function presentParquetDrought(
  result: ParquetBrowserReaderResult<readonly ParquetBrowserDroughtArea[]> | undefined
): GeoJSON.FeatureCollection {
  if (result?.state !== "ready") return { type: "FeatureCollection", features: [] };
  return {
    type: "FeatureCollection",
    features: result.data.map((area) => ({
      type: "Feature" as const,
      id: area.areaId,
      geometry: area.geometry,
      properties: {
        DM: area.droughtCategory,
        label: `D${area.droughtCategory}`,
        validDate: area.validDate,
        observedAt: `${area.validDate}T00:00:00Z`,
        source: "US Drought Monitor",
        sourceUrl: area.sourceUrl,
      },
    })),
  };
}

const EMPTY_COLLECTION: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

/**
 * An MVT attribute table, built the way `ST_AsMVT` built one: a null-valued attribute is ABSENT,
 * never present-and-null.
 *
 * This is the whole reason the four presenters below do not just spread their rows. The style
 * expressions these features feed were written against Martin tiles and read absence with `has`:
 * `burnSeverityLayer`'s fill is `["case", ["has", "acres"], <ramp>, <grey>]`, and
 * `tileLayerDateFilter` keeps an undated feature alive with `["!", ["has", "observed_day"]]`. A
 * GeoJSON `properties` object carrying `acres: null` answers `has` with TRUE, so a scar with no
 * reported acreage would be interpolated against `null` instead of painted the neutral grey, and
 * an undated feature would be compared against a date instead of kept. Dropping the key is what
 * keeps every one of those expressions meaning what it meant against the tiles.
 */
function mvtProperties(
  entries: Readonly<Record<string, string | number | boolean | null | undefined>>
): Record<string, string | number | boolean> {
  const properties: Record<string, string | number | boolean> = {};
  for (const [key, value] of Object.entries(entries)) {
    if (value === null || value === undefined) continue;
    properties[key] = value;
  }
  return properties;
}

/** The UTC calendar day of an instant, or null when the producer published none. */
function observationDay(observedAt: string | null): string | null {
  if (observedAt === null) return null;
  const parsedMs = Date.parse(observedAt);
  return Number.isNaN(parsedMs) ? null : new Date(parsedMs).toISOString().slice(0, 10);
}

/**
 * Published evacuation areas as the exact attribute table `geo.evacuation_zone_tiles()` emitted.
 *
 * Every key below is one that migration's `SELECT` list named, spelled the same way, because
 * `evacuationZonesLayer`'s `severity` match and `formatEvacuationZone`'s six fields were written
 * against those names and are unchanged by this cutover -- the SOURCE moved, the vocabulary did
 * not. `observed_day` is the date part of the producer's own `observedAt`, which is what
 * `geo.feature_observation_day` returned and so what the row's slider has always filtered on.
 */
export function presentParquetEvacuationZones(
  result: ParquetBrowserReaderResult<readonly ParquetBrowserEvacuationZone[]> | undefined
): GeoJSON.FeatureCollection {
  if (result?.state !== "ready") return EMPTY_COLLECTION;
  return {
    type: "FeatureCollection",
    features: result.data.map((zone) => ({
      type: "Feature" as const,
      id: zone.naturalKey,
      geometry: zone.geometry,
      properties: mvtProperties({
        evacuation_area_name: zone.evacuationAreaName,
        fire_name: zone.fireName,
        county: zone.county,
        hazard_type: zone.hazardType,
        severity: zone.severity,
        evacuation_level: zone.evacuationLevel,
        evacuation_level_label: zone.evacuationLevelLabel,
        structures_within: zone.structuresWithin,
        population_within: zone.populationWithin,
        observed_day: observationDay(zone.observedAt),
      }),
    })),
  };
}

/**
 * Active fire perimeters as the exact attribute table `geo.fire_risk_tiles()` emitted.
 *
 * That function's SELECT list named four attributes -- `risk_level`, `severity`, `name`,
 * `observed_day` (`drizzle/0038_tile_low_zoom_routing.sql:466-472`) -- and EMITTED two. It read
 * `risk_level` and `name` out of the `geo.features` JSONB under keys no producer has ever written:
 * `ingest/wfigs.py:234` writes `incidentName`, never `name`, and nothing anywhere writes
 * `risk_level`. `ST_AsMVT` omits a NULL attribute, so both were absent from every tile this
 * platform has ever served, and no paint expression, legend or hover formatter reads either one.
 * They are named here and not emitted, because emitting `incident_name` under the key `name` would
 * be inventing an attribute the layer never had rather than rebuilding the one it did.
 *
 * `observed_day` MUST stay absent rather than null on an incident WFIGS could not date. It is the
 * `mvtProperties` rule at its sharpest: `tileLayerDateFilter` keeps such a row with
 * `["!", ["has", "observed_day"]]`, and a `properties` object carrying `observed_day: null`
 * answers `has` with TRUE -- which would flip the filter from KEEPING every undated perimeter to
 * comparing it against a date it does not have, and hiding it. The server-side in-frame filter
 * (`firePerimetersInFrame`) already kept those rows; dropping the key here is what stops the
 * client throwing them away again.
 */
export function presentParquetFirePerimeters(
  result: ParquetBrowserReaderResult<readonly ParquetBrowserFirePerimeter[]> | undefined
): GeoJSON.FeatureCollection {
  if (result?.state !== "ready") return EMPTY_COLLECTION;
  return {
    type: "FeatureCollection",
    features: result.data.map((perimeter) => ({
      type: "Feature" as const,
      // `geo.features.id`, which is what the tile function put on `f.id`: the same feature keeps
      // the same identity across the cutover.
      id: perimeter.featureId,
      geometry: perimeter.geometry,
      properties: mvtProperties({
        severity: perimeter.severity,
        observed_day: perimeter.observedDay,
      }),
    })),
  };
}

/**
 * MTBS burn scars as the attribute table `geo.burn_severity_tiles()` emitted.
 *
 * `acres` is deliberately absent rather than null when the source reported none: the fill's
 * `["case", ["has", "acres"], ...]` arm is what paints those scars the neutral grey, and a null
 * would send them through the log ramp instead. `severity_class` is carried even though it is null
 * on every published row, for the same reason `formatBurnSeverity` still reads it -- the day MTBS
 * starts publishing a polygon-level class, the tooltip shows it with no code change.
 */
export function presentParquetBurnSeverity(
  result: ParquetBrowserReaderResult<readonly ParquetBrowserBurnScar[]> | undefined
): GeoJSON.FeatureCollection {
  if (result?.state !== "ready") return EMPTY_COLLECTION;
  return {
    type: "FeatureCollection",
    features: result.data.map((scar) => ({
      type: "Feature" as const,
      id: scar.fireId,
      geometry: scar.geometry,
      properties: mvtProperties({
        fire_id: scar.fireId,
        fire_name: scar.fireName,
        fire_year: scar.fireYear,
        ignition_date: scar.ignitionDate,
        fire_type: scar.fireType,
        assessment_type: scar.assessmentType,
        acres: scar.acres,
        severity_class: scar.severityClass,
        observed_day: scar.observedDay,
      }),
    })),
  };
}

/**
 * Watershed boundaries as the attribute table `geo.watershed_tiles()` emitted, with one field
 * missing and named rather than faked.
 *
 * `basin_count` -- how many HUC12s a coarse feature merges -- has NO Parquet source. The tile
 * function computed it with `count(*)` while building `geo.watershed_rollup`
 * (`drizzle/0023_watershed_zoom_generalization.sql:52`), whereas the lane's `HierarchicalDissolve`
 * declares no counting aggregation, so the number does not exist to publish. Omitted, which makes
 * `formatWatershed` drop that one line; inventing it would be the fabricated-field bug this
 * project has already paid for three times. Restoring it is a `ColumnAggregation` on the
 * watersheds lane, not a renderer change.
 *
 * `huc12` is emitted ONLY at the base rung, exactly as the tile function did (`NULL::text AS huc12`
 * on the rollup branch): a HUC10 code under a `huc12` key would present a rollup as a basin.
 */
export function presentParquetWatersheds(
  result: ParquetBrowserReaderResult<readonly ParquetBrowserWatershed[]> | undefined
): GeoJSON.FeatureCollection {
  if (result?.state !== "ready") return EMPTY_COLLECTION;
  return {
    type: "FeatureCollection",
    features: result.data.map((basin) => ({
      type: "Feature" as const,
      id: basin.huc,
      geometry: basin.geometry,
      properties: mvtProperties({
        huc: basin.huc,
        huc_level: basin.hucLevel,
        huc12: basin.hucLevel === 12 ? basin.huc : null,
        name: basin.name,
        areasqkm: basin.areaSquareKm,
        tohuc: basin.toHuc,
        states: basin.states,
        hutype: basin.huType,
      }),
    })),
  };
}

/**
 * Sensor stations as the attribute table `geo.sensor_tiles()` emitted, one Point per station.
 *
 * Four attributes and not sixteen, deliberately: the reader collapses the lane's tall
 * measurement grain to one station per feature, and the sixteen captured NWS values stay on the
 * row rather than being flattened into a property table no style expression or tooltip reads.
 * Widening the tooltip to show them is a hover-fields change with its own review, not something a
 * presenter should decide by quietly shipping the columns.
 */
export function presentParquetSensorStations(
  result: ParquetBrowserReaderResult<readonly ParquetBrowserSensorStation[]> | undefined
): GeoJSON.FeatureCollection {
  if (result?.state !== "ready") return EMPTY_COLLECTION;
  return {
    type: "FeatureCollection",
    features: result.data.map((station, index) => ({
      type: "Feature" as const,
      id: station.sensorId ?? `${station.longitude}:${station.latitude}:${index}`,
      geometry: { type: "Point" as const, coordinates: [station.longitude, station.latitude] },
      properties: mvtProperties({
        network: station.network,
        sensor_id: station.sensorId,
        station_name: station.stationName,
        observed_at: station.observedAt,
        observed_day: station.observedDay,
      }),
    })),
  };
}

/**
 * The one form measured NDVI is drawn in, at every band.
 *
 * `LAYER_RENDER_CONTRACT.vegetation` permits `tessellated_cell` and nothing else in all three
 * bands, and withholds `isoband` and `raster_surface` on purpose: both assert the index varies
 * smoothly BETWEEN the samples, which is the fictitious finer footprint `declaredSupportDegrees`
 * exists to forbid.
 *
 * The reader agrees -- `getVegetationIndex` declares `tessellated_cell` too -- but the DRAWN form
 * is read from here rather than from the envelope anyway. What a renderer may draw is the
 * contract's answer, not the payload's, and the geometry is identical under either label: the
 * square the envelope itself declares.
 */
export const VEGETATION_CELL_DRAWN_FORM: SupportKind = "tessellated_cell";

/**
 * Measured NDVI as the 0.25-degree squares this platform actually observed.
 *
 * Closes the `shippedDeviation` recorded against `vegetation` on 2026-09-02. Until slice m3 this
 * emitted a Point at each cell's centre and `VegetationLayer` drew it as a zoom-scaled circle --
 * `raw_point` on the contract's vocabulary, and the exact claim `declaredSupportDegrees` forbids,
 * because a dot's radius says nothing about the ground the measurement covers.
 *
 * The size comes from `support.cellWidthDegrees`, never from the contract's declared 0.25 and
 * never from a tier table: assuming the number the contract states would be the client inferring
 * support again, one indirection further out. An envelope that declares no size at all still
 * yields a marker, which is honest about having no declared footprint rather than fabricating one.
 */
export function presentParquetVegetation(
  result: ParquetBrowserReaderResult<ParquetBrowserVegetationWindow> | undefined
): GeoJSON.FeatureCollection {
  if (result?.state !== "ready") return { type: "FeatureCollection", features: [] };
  return {
    type: "FeatureCollection",
    features: result.data.observations.map((observation, index) => {
      const support = observation.support;
      const declaredCell = supportCellPolygon(
        observation.longitude,
        observation.latitude,
        support
      );
      return {
        type: "Feature" as const,
        id: observation.cellId ?? `${observation.longitude}:${observation.latitude}:${index}`,
        geometry:
          declaredCell ?? {
            type: "Point" as const,
            coordinates: [observation.longitude, observation.latitude],
          },
        properties: {
          cellId: observation.cellId,
          gridName: observation.gridName,
          metricName: observation.metricName,
          metricUnit: observation.metricUnit,
          ndvi: observation.metricValue,
          observedDay: observation.observedDay,
          dataAvailableAt: observation.dataAvailableAt,
          releaseCount: observation.releaseCount,
          supportKind: declaredCell === null ? null : VEGETATION_CELL_DRAWN_FORM,
          supportId: support.supportId,
          cellWidthDegrees: support.cellWidthDegrees ?? null,
          cellHeightDegrees: support.cellHeightDegrees ?? null,
        },
      };
    }),
  };
}

/**
 * The weather layer's point vocabulary, projected from strict Parquet rows, each carrying the
 * envelope it was served under.
 *
 * The envelope travels rather than being dropped here because it is the only thing that can say
 * whether a dot is ONE sampled observation or the mean of however many the derivation floored into
 * a coarse cell -- the same distinction the fire and streamflow lanes draw, and one the weather
 * layer had no way to state at all before 2026-09-02. Whether a coarse rung should stop being a
 * dot is a rendering question this presenter deliberately does not answer: `weather` is an
 * `event_point` layer in `LAYER_RENDER_CONTRACT`, and widening that is m0's open sampled-grid
 * ruling, not a presenter's decision.
 */
export function presentParquetWeather(
  result: ParquetBrowserReaderResult<readonly ParquetBrowserWeatherObservation[]> | undefined
) {
  if (result?.state !== "ready") return [];
  return result.data.map((observation) => ({
    coordinates: [observation.longitude, observation.latitude] as [number, number],
    windSpeed: observation.windSpeedMs,
    windDirection: observation.windDirectionDeg,
    temperature: observation.temperatureC,
    humidity: observation.relativeHumidityPct,
    observedAt: observation.observedAt,
    support: observation.support,
  }));
}
