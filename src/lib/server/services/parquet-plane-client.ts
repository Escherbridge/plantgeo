import { z } from "zod";
import { fetchBoundedJson, providerUrl } from "@/lib/server/http/bounded-upstream";
import type { ZoomTier } from "@/lib/map/zoom-tiers";
import type { DayRange } from "@/types/time-slider";
import {
  assertExhaustiveParquetPlaneState,
  type ParquetPlaneEnvelope,
} from "@/lib/server/services/parquet-envelope";

/**
 * Bounded client for the agri-data-service Parquet plane: the four reads the map needs against the
 * day-partitioned warehouse (owner's 2026-08-22 pivot -- Postgres keeps community features, every
 * data plane becomes Parquet on object storage read by DuckDB/Polars).
 *
 * THE WIRE FORMAT IS NOT FROZEN. Everything this client assumes about it -- route segments, query
 * parameter names, response field names and shapes -- lives in the single `WIRE` section below and
 * nowhere else, so agreeing the contract with the service is one edit in one place rather than a
 * hunt through four functions. Nothing outside that section spells a wire name; the public types
 * above and below it are this codebase's own vocabulary and are meant to outlive the format.
 *
 * DAYS ARE OPAQUE. Every day crossing this module is a `YYYY-MM-DD` string that is shape-checked
 * and passed through -- never parsed into a `Date`, never formatted from one, never converted
 * between zones. `PUBLISHER_NAMED_DAY_RULE` (environmental-read-model.ts) is why: 37.5% of the
 * stored water-gauge rows carry a `-07:00` offset, and a single instant-based conversion moves
 * 6,279 of 16,743 of them onto the following calendar day. The server owns day semantics; this
 * client owns none. `src/__tests__/services/parquet-plane-client.test.ts` fails if a date
 * conversion ever appears in this module or in `parquet-envelope.ts`.
 *
 * FAULTS ARE THROWN, NEVER ENVELOPED. A timeout, an oversized body, a 5xx, an unreachable host and
 * a payload that breaks the contract all propagate as the `bounded-upstream` taxonomy so
 * `rethrowUpstreamFault` maps them to the retryable tRPC code the map's `retry: 1` already handles.
 * The four envelope states describe what the WAREHOUSE holds; a transport blip that returned
 * `day_not_written` would turn an outage into a positive claim that nothing was ever ingested --
 * exactly the confusion `MetricAtDateAvailability`'s `request_failed` member exists to prevent. An
 * unset base URL is likewise a thrown `UpstreamConfigurationError` (in production; development
 * falls back to the local default) rather than a quiet empty answer.
 *
 * ONE ZOOM LADDER. `zoomTier` is typed as `ZoomTier` from `src/lib/map/zoom-tiers.ts` -- imported,
 * never re-derived here -- so a caller must resolve a map zoom through `resolveZoomTier` and this
 * client cannot invent a rung. Two ladders that disagree would request partition paths that were
 * never written and read as an empty map over data that exists.
 */

/**
 * Base URL of the agri-data-service Parquet plane.
 *
 * Deliberately NOT `AGRI_DATA_SERVICE_URL`, which the forecast bridge reads. That one is optional
 * in production and degrades to a Forecast tab reporting itself unavailable; this plane is the
 * map's data source, so an unset URL in production is a configuration fault that must be loud --
 * `providerUrl` throwing is the whole behaviour difference. Keeping them separate also lets the
 * plane move to its own deployment without re-pointing the forecast bridge. Collapsing them later,
 * if they permanently share a host, is a one-line change here.
 */
const PARQUET_SERVICE_URL_ENV = "AGRI_PARQUET_SERVICE_URL";

/** Matches the agri-data-service's own local port, as `AGRI_DATA_SERVICE_URL` documents. */
const PARQUET_SERVICE_DEVELOPMENT_URL = "http://localhost:8000";

/**
 * Byte ceiling for a row read. Provisional and generous: a z13 day of a geometry-bearing lane is
 * the largest thing this client will ever hold, and the serving side bounds the row count itself
 * (reported as `truncated`) rather than relying on this. Lower it once a measured page size exists.
 */
const MAX_ROW_RESPONSE_BYTES = 16 * 1024 * 1024;

/** Coverage carries no geometry -- one census row per lane -- so it needs a fraction of the room. */
const MAX_COVERAGE_RESPONSE_BYTES = 4 * 1024 * 1024;

/** A DuckDB scan over one day's partitions, with headroom for a cold object-store read. */
const ROW_READ_TIMEOUT_MS = 15_000;

/** Coverage walks the whole warehouse's object listing; the memoization below is what hides its cost. */
const COVERAGE_TIMEOUT_MS = 20_000;

/**
 * How long one coverage answer is reused, in seconds.
 *
 * Coverage is deliberately whole-warehouse -- no bbox, no zoom -- so every viewport in every
 * session shares this one cached entry. Adding either axis would fragment the cache into one entry
 * per viewport and defeat the point; that was investigated and settled. Five minutes sits at the
 * fast end of the agreed 5-30 minute band, which keeps a newly drained lane visible within one
 * coffee break while still collapsing a burst of page loads into a single upstream read.
 */
const COVERAGE_REVALIDATE_SECONDS = 300;

/** `YYYY-MM-DD`. A shape check only: nothing here turns a day into an instant. */
const CALENDAR_DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

/** A caller handed this client something it will not put on the wire. Never a transport fault. */
export class ParquetPlaneRequestError extends Error {}

/**
 * The service answered 200 and the body broke the published contract. Permanent until a deploy
 * fixes one side, so it is its own class rather than an `UpstreamPayloadError` -- `rethrowUpstreamFault`
 * treats that one as transient and would tell the client to retry a mismatch that cannot heal.
 */
export class ParquetPlaneContractError extends Error {}

/** Which half of a stream a read addresses; `paths.py`'s `PartitionKind`. */
export const PARQUET_PARTITION_KINDS = ["observed", "forecast"] as const;

/** One of the two partition kinds above. */
export type ParquetPartitionKind = (typeof PARQUET_PARTITION_KINDS)[number];

/**
 * What a lane's partition DAY means; `lane_contract.py`'s `LaneNature`.
 *
 * Carried on coverage because it decides how a day may be read: for `daily_series` the day is an
 * observation day, for `release_series` it is a publication's own date, and for `static_lookup` it
 * is a VERSION STAMP with no time axis at all -- so a slider must not offer a scrub across one.
 */
export const PARQUET_LANE_NATURES = [
  "daily_series",
  "release_series",
  "static_lookup",
] as const;

/** One of the three lane natures above. */
export type ParquetLaneNature = (typeof PARQUET_LANE_NATURES)[number];

/** Fields every one of the three row reads carries. */
interface ParquetReadBase {
  /** Layer slug, lower-case and hyphenated (`drought-areas`), as the partition path spells it. */
  layer: string;
  /**
   * The tier serving this read. Resolve a map zoom with `resolveZoomTier`; never hand-pick a rung.
   */
  zoomTier: ZoomTier;
  /** Defaults to `observed`, and is always sent explicitly so the server never has to guess. */
  kind?: ParquetPartitionKind;
  /**
   * `"west,south,east,north"`, omitted for an unbounded read.
   *
   * Optional per call rather than required, because the layers do not agree today and this client
   * must not change what any of them asks for: drought, vegetation and soil all take a viewport
   * bbox, while the live fire-detections path (`GET /api/fires?date=`, src/app/api/fires/route.ts)
   * takes none at all. A bbox added to a read whose current equivalent has none would silently
   * shrink that layer's answer.
   */
  bbox?: string;
}

/** One layer's rows for one day. */
export interface ParquetLayerDayRequest extends ParquetReadBase {
  /** `YYYY-MM-DD`, passed through opaquely. */
  day: string;
}

/** One layer's rows for a closed day range, both ends inclusive. */
export interface ParquetLayerDayWindowRequest extends ParquetReadBase {
  /** `YYYY-MM-DD`, passed through opaquely. */
  firstDay: string;
  /** `YYYY-MM-DD`, passed through opaquely. */
  lastDay: string;
}

/** The newest release at or before a day, for `release_series` and `static_lookup` lanes. */
export interface ParquetLatestReleaseRequest extends ParquetReadBase {
  /** `YYYY-MM-DD`, passed through opaquely. The answer reports the release's OWN day. */
  asOfDay: string;
}

/** What one lane has and has not written, over its whole published span. */
export interface ParquetLaneCoverage {
  /** Layer slug as the partition path spells it. */
  layer: string;
  nature: ParquetLaneNature;
  kind: ParquetPartitionKind;
  /** Oldest day this lane holds, `YYYY-MM-DD`; null when it has written nothing at all. */
  earliestDay: string | null;
  /** Newest day this lane holds, `YYYY-MM-DD`; null when it has written nothing at all. */
  latestDay: string | null;
  /**
   * Runs of days inside the lane's own span covered by neither a part file nor an absence marker.
   * Ranges rather than days for the reason `SliderLayerCapability.coverageGaps` is: a four-year
   * lane's day list is noise, and a run is what a reader can act on.
   */
  gapRanges: DayRange[];
  /**
   * Runs of days settled by a governed-absence marker -- deliberate emptiness, never a gap.
   * Disjoint from `gapRanges` by construction: a day cannot both hold a marker and hold nothing.
   */
  governedAbsenceRanges: DayRange[];
}

/** The whole warehouse's census: one entry per lane, no viewport and no tier. */
export interface ParquetWarehouseCoverage {
  /**
   * When the service computed this census, as an opaque ISO-8601 instant. Load-bearing because the
   * answer is memoized for minutes: a caller captioning it as "now" would overstate its freshness.
   */
  generatedAt: string;
  lanes: ParquetLaneCoverage[];
}

/* ---------------------------------------------------------------------------
 * WIRE
 *
 * The ONLY place this client states what the HTTP contract looks like. Route segments, query
 * parameter names, response field names and response shapes all live here; freezing (or changing)
 * the contract is an edit inside this block and nowhere else.
 *
 * Every assumption it currently makes, stated so none of them is silent:
 *  1. Routes hang off `/api/v1/parquet/`, matching the forecast plane's `/api/v1/forecasts/`.
 *  2. Query parameters are snake_case, matching `series_key`/`valid_from` on that plane.
 *  3. `zoom` travels as a plain tier integer (`9`, not the path segment's zero-padded `09`); the
 *     server pads it when it builds a partition prefix. The two must agree on the ladder itself,
 *     which is why the tier is typed rather than a free number.
 *  4. Row reads are unpaginated: the server owns the row budget and reports it as `truncated`.
 *     There is no `limit`/`offset` here until one is needed and measured.
 *  5. Every one of the four warehouse states arrives as HTTP 200 with a `state` field. A non-2xx is
 *     a transport or serving fault and never a statement about warehouse content.
 *  6. The window read answers EVERY day in the closed range, in ascending order -- a gap day is
 *     stated as `day_not_written`, never omitted. `decodeWindow` enforces exactly that, because a
 *     short array would read as "the missing days are fine".
 *  7. Coverage is per lane and tier-agnostic: a day counts as covered when any published tier holds
 *     it. Per-tier coverage would multiply the census by four to answer a question no caller asks.
 * ------------------------------------------------------------------------- */

const WIRE = {
  basePath: "/api/v1/parquet",
  routes: {
    day: "day",
    window: "window",
    release: "release",
    coverage: "coverage",
  },
  params: {
    layer: "layer",
    kind: "kind",
    zoom: "zoom",
    bbox: "bbox",
    day: "day",
    firstDay: "first_day",
    lastDay: "last_day",
    asOfDay: "as_of",
  },
} as const;

const wireAbsenceSchema = z.object({
  reason: z.string(),
  upstream_response: z.string(),
  recorded_at: z.string(),
  run_id: z.string(),
});

const wireEnvelopeSchema = z.discriminatedUnion("state", [
  z.object({
    state: z.literal("published"),
    requested_day: z.string(),
    served_day: z.string(),
    // `z.record(z.unknown())` and not a per-layer shape: the warehouse has a schema per layer per
    // kind, so narrowing a row belongs to the caller that knows which layer it asked for.
    rows: z.array(z.record(z.unknown())),
    truncated: z.boolean(),
  }),
  z.object({
    state: z.literal("governed_absence"),
    requested_day: z.string(),
    served_day: z.string(),
    absence: wireAbsenceSchema,
  }),
  z.object({
    state: z.literal("day_not_written"),
    requested_day: z.string(),
  }),
  z.object({
    state: z.literal("lane_never_written"),
    requested_day: z.string(),
  }),
]);

const wireWindowSchema = z.object({ days: z.array(wireEnvelopeSchema) });

const wireDayRangeSchema = z.object({ from: z.string(), to: z.string() });

const wireCoverageSchema = z.object({
  generated_at: z.string(),
  lanes: z.array(
    z.object({
      layer: z.string(),
      nature: z.enum(PARQUET_LANE_NATURES),
      kind: z.enum(PARQUET_PARTITION_KINDS),
      earliest_day: z.string().nullable(),
      latest_day: z.string().nullable(),
      gap_ranges: z.array(wireDayRangeSchema),
      governed_absence_ranges: z.array(wireDayRangeSchema),
    })
  ),
});

type WireEnvelope = z.infer<typeof wireEnvelopeSchema>;

/** Wire envelope to this codebase's union. The `default` arm is the union's exhaustiveness proof. */
function toEnvelope(wire: WireEnvelope): ParquetPlaneEnvelope {
  switch (wire.state) {
    case "published":
      return {
        state: "published",
        requestedDay: wire.requested_day,
        servedDay: wire.served_day,
        rows: wire.rows,
        truncated: wire.truncated,
      };
    case "governed_absence":
      return {
        state: "governed_absence",
        requestedDay: wire.requested_day,
        servedDay: wire.served_day,
        evidence: {
          reason: wire.absence.reason,
          upstreamResponse: wire.absence.upstream_response,
          recordedAt: wire.absence.recorded_at,
          runId: wire.absence.run_id,
        },
      };
    case "day_not_written":
      return { state: "day_not_written", requestedDay: wire.requested_day };
    case "lane_never_written":
      return { state: "lane_never_written", requestedDay: wire.requested_day };
    default:
      return assertExhaustiveParquetPlaneState(wire);
  }
}

/** Parses one envelope, or refuses the whole answer. */
function decodeEnvelope(payload: unknown): ParquetPlaneEnvelope {
  const parsed = wireEnvelopeSchema.safeParse(payload);
  if (!parsed.success) {
    throw new ParquetPlaneContractError(
      "Parquet plane answered with an envelope that is not one of the four published states"
    );
  }
  return toEnvelope(parsed.data);
}

/**
 * Parses a window answer and checks it describes the whole closed range.
 *
 * The ends are compared as STRINGS and the ordering check is lexicographic. That is exact for
 * fixed-width ISO days -- lexicographic order is chronological order -- and it is the only way to
 * verify the range without date arithmetic, which this module refuses to do. A short, reordered or
 * duplicated answer is a contract error rather than a silently narrower window, because the days it
 * left out would otherwise read as days nothing is wrong with.
 */
function decodeWindow(
  payload: unknown,
  firstDay: string,
  lastDay: string
): ParquetPlaneEnvelope[] {
  const parsed = wireWindowSchema.safeParse(payload);
  if (!parsed.success) {
    throw new ParquetPlaneContractError(
      "Parquet plane answered a day window that is not a list of published envelopes"
    );
  }
  const days = parsed.data.days.map(toEnvelope);
  const first = days.at(0);
  const last = days.at(-1);
  if (first === undefined || last === undefined) {
    throw new ParquetPlaneContractError(
      `Parquet plane described no day of the window ${firstDay}..${lastDay}`
    );
  }
  if (first.requestedDay !== firstDay || last.requestedDay !== lastDay) {
    throw new ParquetPlaneContractError(
      `Parquet plane answered ${first.requestedDay}..${last.requestedDay} for the window ${firstDay}..${lastDay}`
    );
  }
  for (let index = 1; index < days.length; index += 1) {
    if (days[index - 1].requestedDay >= days[index].requestedDay) {
      throw new ParquetPlaneContractError(
        `Parquet plane repeated or misordered ${days[index].requestedDay} in the window ${firstDay}..${lastDay}`
      );
    }
  }
  return days;
}

/** Parses the whole-warehouse census, or refuses it. */
function decodeCoverage(payload: unknown): ParquetWarehouseCoverage {
  const parsed = wireCoverageSchema.safeParse(payload);
  if (!parsed.success) {
    throw new ParquetPlaneContractError(
      "Parquet plane answered a coverage census that does not match the published contract"
    );
  }
  return {
    generatedAt: parsed.data.generated_at,
    lanes: parsed.data.lanes.map((lane) => ({
      layer: lane.layer,
      nature: lane.nature,
      kind: lane.kind,
      earliestDay: lane.earliest_day,
      latestDay: lane.latest_day,
      gapRanges: lane.gap_ranges,
      governedAbsenceRanges: lane.governed_absence_ranges,
    })),
  };
}

/* ------------------------------------------------------------------------- */

/** The service root, with one route appended. Never reads `process.env` directly. */
function endpoint(route: string): URL {
  const url = providerUrl(PARQUET_SERVICE_URL_ENV, PARQUET_SERVICE_DEVELOPMENT_URL);
  url.pathname = `${url.pathname.replace(/\/$/, "")}${WIRE.basePath}/${route}`;
  return url;
}

/** Shape-checks a caller's day. Never parses it: a `Date` here is the day-shift bug. */
function requireCalendarDay(value: string, field: string): string {
  if (!CALENDAR_DAY_PATTERN.test(value)) {
    throw new ParquetPlaneRequestError(`${field} must be a YYYY-MM-DD calendar day, got "${value}"`);
  }
  return value;
}

/** The parameters every row read shares, applied in one place so the four routes cannot drift. */
function applyReadBase(url: URL, request: ParquetReadBase): void {
  if (request.layer.trim() === "") {
    throw new ParquetPlaneRequestError("layer must be a non-empty layer slug");
  }
  url.searchParams.set(WIRE.params.layer, request.layer);
  url.searchParams.set(WIRE.params.kind, request.kind ?? "observed");
  url.searchParams.set(WIRE.params.zoom, String(request.zoomTier));
  if (request.bbox !== undefined) url.searchParams.set(WIRE.params.bbox, request.bbox);
}

/** GET with the shared bounds; `revalidateSeconds` omitted means `cache: "no-store"`. */
async function readJson(
  url: URL,
  maxBytes: number,
  timeoutMs: number,
  revalidateSeconds?: number
): Promise<unknown> {
  return fetchBoundedJson(
    url,
    { method: "GET", headers: { Accept: "application/json" } },
    { maxBytes, timeoutMs, ...(revalidateSeconds === undefined ? {} : { revalidateSeconds }) }
  );
}

/**
 * One layer's rows for one day at one tier.
 *
 * Uncached (`no-store`), like every other viewport read here: a day at the live edge is still being
 * written, and a stale cached answer for it would report a day as thinner than the warehouse holds.
 */
export async function getParquetLayerDay(
  request: ParquetLayerDayRequest
): Promise<ParquetPlaneEnvelope> {
  const url = endpoint(WIRE.routes.day);
  applyReadBase(url, request);
  url.searchParams.set(WIRE.params.day, requireCalendarDay(request.day, "day"));
  return decodeEnvelope(await readJson(url, MAX_ROW_RESPONSE_BYTES, ROW_READ_TIMEOUT_MS));
}

/**
 * One layer's rows for a closed day range, one envelope per day in ascending order.
 *
 * Per day rather than one merged answer, because a window is exactly where the four states differ
 * from each other: a governed absence in the middle of a window is a fact about that day, and
 * merging the range into a single collection would erase it.
 */
export async function getParquetLayerDayWindow(
  request: ParquetLayerDayWindowRequest
): Promise<ParquetPlaneEnvelope[]> {
  const firstDay = requireCalendarDay(request.firstDay, "firstDay");
  const lastDay = requireCalendarDay(request.lastDay, "lastDay");
  // String comparison, not date arithmetic: fixed-width ISO days sort chronologically.
  if (firstDay > lastDay) {
    throw new ParquetPlaneRequestError(`day window ${firstDay}..${lastDay} runs backwards`);
  }
  const url = endpoint(WIRE.routes.window);
  applyReadBase(url, request);
  url.searchParams.set(WIRE.params.firstDay, firstDay);
  url.searchParams.set(WIRE.params.lastDay, lastDay);
  const payload = await readJson(url, MAX_ROW_RESPONSE_BYTES, ROW_READ_TIMEOUT_MS);
  return decodeWindow(payload, firstDay, lastDay);
}

/**
 * The newest release at or before a day, for lanes whose partition day is a publication date
 * (`release_series`) or a version stamp (`static_lookup`).
 *
 * The published answer reports the release's own `servedDay`, never the day asked for -- a weekly
 * USDM release carried forward six days is still that release, and saying otherwise would present
 * it as fresher than it is.
 */
export async function getParquetLatestRelease(
  request: ParquetLatestReleaseRequest
): Promise<ParquetPlaneEnvelope> {
  const url = endpoint(WIRE.routes.release);
  applyReadBase(url, request);
  url.searchParams.set(WIRE.params.asOfDay, requireCalendarDay(request.asOfDay, "asOfDay"));
  return decodeEnvelope(await readJson(url, MAX_ROW_RESPONSE_BYTES, ROW_READ_TIMEOUT_MS));
}

/**
 * The whole warehouse's coverage census.
 *
 * No bbox and no zoom, deliberately and permanently: this one answer is shared by every viewport,
 * and the Next.js data cache below is what makes it cheap. Adding either axis would fragment the
 * cache into an entry per viewport, which is the entire cost this shape avoids.
 */
export async function getParquetWarehouseCoverage(): Promise<ParquetWarehouseCoverage> {
  const url = endpoint(WIRE.routes.coverage);
  const payload = await readJson(
    url,
    MAX_COVERAGE_RESPONSE_BYTES,
    COVERAGE_TIMEOUT_MS,
    COVERAGE_REVALIDATE_SECONDS
  );
  return decodeCoverage(payload);
}
