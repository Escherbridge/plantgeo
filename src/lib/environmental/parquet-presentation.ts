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
