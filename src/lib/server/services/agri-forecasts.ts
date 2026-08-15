import { z } from "zod";
import {
  fetchBoundedJson,
  providerUrl,
  UpstreamConfigurationError,
} from "@/lib/server/http/bounded-upstream";

/**
 * Bounded proxy over the agri-data-service's published forecast serving view
 * (`GET /forecasts/`). See `src/lib/server/AGENTS.md` §agri-forecasts.
 */

/** The upstream answered 200 but the body broke the published contract -- permanent until a deploy fixes one side, so never relabelled as transient. */
export class ForecastContractError extends Error {}

/** Upstream caps: page limit 250, offset 10k, series key 255 chars (routes/forecasts.py). */
export const forecastSeriesRequestSchema = z.object({
  seriesKey: z.string().min(1).max(255),
  validFrom: z.string().datetime({ offset: true }).optional(),
  validTo: z.string().datetime({ offset: true }).optional(),
  limit: z.number().int().min(1).max(250).default(250),
  offset: z.number().int().min(0).max(10_000).default(0),
});

export type ForecastSeriesRequest = z.infer<typeof forecastSeriesRequestSchema>;

// Only the fields the band chart, receipt selection, and the provenance caption
// consume; z.object strips the rest of the serving record (support, spatial,
// checksums) on parse.
const upstreamRecordSchema = z.object({
  publication: z.object({
    publication_id: z.string().uuid(),
    publication_key: z.string(),
    published_at: z.string().datetime({ offset: true }),
    receipt_checksum: z.string(),
    forecast_receipt_id: z.string().uuid(),
  }),
  series: z.object({
    series_key: z.string(),
    entity_type: z.string(),
    entity_key: z.string(),
    metric_name: z.string(),
    metric_unit: z.string(),
  }),
  lineage: z.object({
    forecast_method: z.enum(["sql_linear", "ml"]),
  }),
  point: z.object({
    issue_time: z.string().datetime({ offset: true }),
    valid_time: z.string().datetime({ offset: true }),
    horizon_step: z.number().int(),
    point_value: z.number(),
    p10_value: z.number().nullable(),
    p50_value: z.number().nullable(),
    p90_value: z.number().nullable(),
  }),
});

const upstreamPageSchema = z.object({
  data: z.array(upstreamRecordSchema),
  limit: z.number().int(),
  offset: z.number().int(),
  hasMore: z.boolean(),
});

export type UpstreamForecastPage = z.infer<typeof upstreamPageSchema>;

type UpstreamForecastRecord = z.infer<typeof upstreamRecordSchema>;

export interface ForecastBandPoint {
  validTime: string;
  horizonStep: number;
  pointValue: number;
  p10: number | null;
  p50: number | null;
  p90: number | null;
}

/**
 * `availability` follows the ProxiedFeatureCollection idiom: "unavailable" means the
 * capability is withheld (no base URL configured), while "published" with zero points
 * means the serving plane answered and simply holds no receipts for this series --
 * the expected state until a forecast run publishes.
 */
export interface PublishedForecastSeries {
  availability: "published" | "unavailable";
  reason: string | null;
  seriesKey: string;
  metricName: string | null;
  metricUnit: string | null;
  entityKey: string | null;
  forecastMethod: "sql_linear" | "ml" | null;
  issuedAt: string | null;
  publishedAt: string | null;
  publicationKey: string | null;
  /**
   * Points from receipts older than the one drawn, dropped rather than interleaved.
   * Non-zero tells a caller the page held more history than the chart shows.
   */
  staleReceiptPointsDropped: number;
  hasMore: boolean;
  points: ForecastBandPoint[];
}

const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
const UPSTREAM_TIMEOUT_MS = 8000;

function endpoint(): URL {
  const url = providerUrl("AGRI_DATA_SERVICE_URL", "http://localhost:8000");
  url.pathname = `${url.pathname.replace(/\/$/, "")}/api/v1/forecasts/`;
  return url;
}

function unavailableSeries(seriesKey: string, reason: string): PublishedForecastSeries {
  return {
    availability: "unavailable",
    reason,
    seriesKey,
    metricName: null,
    metricUnit: null,
    entityKey: null,
    forecastMethod: null,
    issuedAt: null,
    publishedAt: null,
    publicationKey: null,
    staleReceiptPointsDropped: 0,
    hasMore: false,
    points: [],
  };
}

/**
 * One page may span several runs: the publication pointer is unique per scope, not
 * per series window, so a fresh run's points arrive interleaved with the run it
 * superseded -- and one receipt can be an item of two publications, which would
 * double-draw every point under a receipt-only key. The chart must draw ONE run:
 * the latest issue time wins (composite key as a deterministic tiebreaker) and
 * every other record is dropped and counted, never averaged or interleaved.
 */
function selectLatestRun(records: UpstreamForecastRecord[]): UpstreamForecastRecord[] {
  const byRun = new Map<string, UpstreamForecastRecord[]>();
  for (const record of records) {
    const runKey = `${record.publication.publication_id}|${record.publication.forecast_receipt_id}`;
    const bucket = byRun.get(runKey);
    if (bucket === undefined) byRun.set(runKey, [record]);
    else bucket.push(record);
  }
  let chosen: UpstreamForecastRecord[] = [];
  let chosenKey = "";
  for (const [runKey, bucket] of byRun) {
    if (chosen.length === 0) {
      chosen = bucket;
      chosenKey = runKey;
      continue;
    }
    const chosenIssue = Date.parse(chosen[0].point.issue_time);
    const candidateIssue = Date.parse(bucket[0].point.issue_time);
    const candidateWins =
      candidateIssue > chosenIssue ||
      (candidateIssue === chosenIssue && runKey > chosenKey);
    if (candidateWins) {
      chosen = bucket;
      chosenKey = runKey;
    }
  }
  return chosen;
}

/** Flattens one serving page into chart-ready points from the latest receipt only. */
export function toPublishedSeries(
  seriesKey: string,
  page: UpstreamForecastPage
): PublishedForecastSeries {
  // The server's total order is (valid_time, receipt, horizon_step), so the one
  // run's subsequence is already in valid-time order -- no client re-sort.
  const records = selectLatestRun(page.data);
  const identity = records[0] ?? null;
  return {
    availability: "published",
    reason: null,
    seriesKey,
    metricName: identity?.series.metric_name ?? null,
    metricUnit: identity?.series.metric_unit ?? null,
    entityKey: identity?.series.entity_key ?? null,
    forecastMethod: identity?.lineage.forecast_method ?? null,
    issuedAt: identity?.point.issue_time ?? null,
    publishedAt: identity?.publication.published_at ?? null,
    publicationKey: identity?.publication.publication_key ?? null,
    staleReceiptPointsDropped: page.data.length - records.length,
    hasMore: page.hasMore,
    points: records.map((record) => ({
      validTime: record.point.valid_time,
      horizonStep: record.point.horizon_step,
      pointValue: record.point.point_value,
      p10: record.point.p10_value,
      p50: record.point.p50_value,
      p90: record.point.p90_value,
    })),
  };
}

export async function getPublishedForecastSeries(
  request: ForecastSeriesRequest
): Promise<PublishedForecastSeries> {
  let url: URL;
  try {
    url = endpoint();
  } catch (error) {
    // An unset base URL is a deployment without the bridge, not a fault worth a 500.
    if (error instanceof UpstreamConfigurationError) {
      return unavailableSeries(request.seriesKey, "forecast_service_not_configured");
    }
    throw error;
  }

  url.searchParams.set("series_key", request.seriesKey);
  url.searchParams.set("spatial", "none");
  url.searchParams.set("limit", String(request.limit));
  url.searchParams.set("offset", String(request.offset));
  if (request.validFrom !== undefined) url.searchParams.set("valid_from", request.validFrom);
  if (request.validTo !== undefined) url.searchParams.set("valid_to", request.validTo);

  const payload = await fetchBoundedJson(
    url,
    { method: "GET" },
    { maxBytes: MAX_RESPONSE_BYTES, timeoutMs: UPSTREAM_TIMEOUT_MS }
  );
  const parsed = upstreamPageSchema.safeParse(payload);
  if (!parsed.success) {
    throw new ForecastContractError(
      "forecast serving payload did not match the published contract"
    );
  }
  return toPublishedSeries(request.seriesKey, parsed.data);
}
