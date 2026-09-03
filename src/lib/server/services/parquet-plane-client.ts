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
 * THE WIRE FORMAT IS FROZEN as of `80ac72a`. Everything this client assumes about it -- route
 * segments, query parameter names, response field names and shapes -- lives in the single `WIRE`
 * section below and nowhere else. Nothing outside that section spells a wire name; the public types
 * above and below it are this codebase's own vocabulary and are meant to outlive the format.
 *
 * The freeze is enforced from BOTH sides, so drift fails a build rather than a viewport. The
 * serving side declares the same contract in `services/agri-data-service/tests/contract/`, and one
 * test there PARSES THE `WIRE` BLOCK OUT OF THIS FILE and compares it to a pydantic table -- rename
 * a route here and the Python suite fails. Nine golden fixtures in that directory are read by both
 * suites, this one included (see the `the frozen wire contract` block in the sibling test file), so
 * both languages agree by consuming identical bytes rather than by two hopeful copies.
 *
 * Changing the contract means editing this block, `wire_contract.py` and the fixtures in ONE change,
 * and expecting both suites to fail until all three agree. That is the point: a lane needing a new
 * field asks for the change rather than adding it and discovering the mismatch at the join. Read
 * `services/agri-data-service/tests/contract/AGENTS.md` first -- it records what is deliberately NOT
 * frozen (row contents, row counts, lane order) and the asymmetry that the Python side is the strict
 * one, since zod strips unknown keys by default.
 *
 * DAYS NEVER BECOME INSTANTS. Every day crossing this module is a `YYYY-MM-DD` string that is
 * never parsed into a `Date`, formatted from one, or converted between zones.
 * `PUBLISHER_NAMED_DAY_RULE` (environmental-read-model.ts) is why: 37.5% of the stored
 * water-gauge rows carry a `-07:00` offset, and a single instant-based conversion moves 6,279 of
 * 16,743 of them onto the following calendar day. The one calendar operation is the window
 * decoder's pure field-wise successor, used only to prove no requested day was omitted.
 * `src/__tests__/services/parquet-plane-client.test.ts` fails if an instant conversion ever
 * appears in this module or in `parquet-envelope.ts`.
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

/** Coverage carries no geometry -- one compact census row per lane/rung -- so 4 MiB is ample. */
const MAX_COVERAGE_RESPONSE_BYTES = 4 * 1024 * 1024;

/** A DuckDB scan over one day's partitions, with headroom for a cold object-store read. */
const ROW_READ_TIMEOUT_MS = 15_000;

/** The public slider may degrade while the service continues its shielded cold census build. */
const COVERAGE_TIMEOUT_MS = 8_000;

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

/** Collapse concurrent cold callers; the successful response still lives in Next's shared cache. */
let coverageRequest: Promise<ParquetWarehouseCoverage> | null = null;

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

/**
 * WHICH EVIDENCE decided a lane's days.
 *
 * `availability` means the serving side read the lane's immutable `_LATEST.json` pointer and the
 * `availability.parquet` generation it names -- a published, checksummed statement of what exists.
 * `census` means it walked the object store instead, which is a live listing and therefore a
 * weaker claim: a part still being written appears in a walk before it is readable. Nothing in
 * TypeScript reads either artefact; this field is the serving side reporting which one it used,
 * and it is carried rather than collapsed because a slider captioned from a walk must not be
 * presented as though it were reading the published index.
 */
export const PARQUET_COVERAGE_AUTHORITIES = ["availability", "census"] as const;

/** One of the two authorities above. */
export type ParquetCoverageAuthority = (typeof PARQUET_COVERAGE_AUTHORITIES)[number];

/**
 * Why the serving side refuses to describe a lane AT ALL, in the wire's own declared order.
 *
 * Declaration order doubles as precedence: when several rungs of one capability withhold for
 * different reasons, the earliest member here is the one reported, so the answer is stable
 * rather than dependent on lane ordering. A second, separately-ordered precedence list would be
 * a second place for the same decision to drift.
 *
 * Each member is a statement about the AVAILABILITY INDEX, never about the data: an unpublished
 * index says nothing about whether days exist, which is exactly why a lane carrying one may not
 * be described from census facts instead. See `parquet-slider-capabilities.ts`.
 */
export const PARQUET_AVAILABILITY_WITHHELD_REASONS = [
  "availability_unpublished",
  "availability_stale",
  "availability_malformed",
  "availability_checksum_invalid",
] as const;

/** One of the four withheld reasons above. */
export type ParquetAvailabilityWithheldReason =
  (typeof PARQUET_AVAILABILITY_WITHHELD_REASONS)[number];

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
  /**
   * The caller's cancellation, combined with this read's own timeout by `fetchBounded`.
   *
   * Optional so a caller with nothing to cancel (a background job, a test) is unchanged, and
   * carried on the REQUEST rather than as a second argument so it travels with `...request`
   * spreads the way `bbox` already does. An abandoned viewport surfaces as `UpstreamAbortedError`
   * and never as an envelope -- a cancelled read is not a statement about the warehouse.
   */
  signal?: AbortSignal;
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

/** What one physical lane/rung has and has not written, over its whole published span. */
export interface ParquetLaneCoverage {
  /** Layer slug as the partition path spells it. */
  layer: string;
  nature: ParquetLaneNature;
  kind: ParquetPartitionKind;
  /** Exact frozen-layout rung this evidence describes; evidence from another rung cannot substitute. */
  zoomTier: ZoomTier;
  /** Oldest day this lane holds, `YYYY-MM-DD`; null when it has written nothing at all. */
  earliestDay: string | null;
  /** Newest day this lane holds, `YYYY-MM-DD`; null when it has written nothing at all. */
  latestDay: string | null;
  /** Exact runs with complete, non-conflicting Parquet parts on this physical rung. */
  publishedRanges: DayRange[];
  /**
   * Owed publication days through the census's `evaluatedThroughDay` covered by neither a complete
   * part nor a governed absence. Release cadence and publication lag are applied upstream; callers
   * must not fill every calendar day after a release as though it were owed.
   */
  gapRanges: DayRange[];
  /**
   * Runs of days settled by a governed-absence marker -- deliberate emptiness, never a gap.
   * Disjoint from `gapRanges` by construction: a day cannot both hold a marker and hold nothing.
   */
  governedAbsenceRanges: DayRange[];
  /** Which evidence produced every field above; see `PARQUET_COVERAGE_AUTHORITIES`. */
  coverageAuthority: ParquetCoverageAuthority;
  /**
   * Checksum of the `availability.parquet` generation this evidence was read from; null under
   * `census` authority, where there is no generation to name. Provenance for a human tracing a
   * disputed day back to the exact published artefact -- never compared or recomputed here.
   */
  availabilityGenerationSha256: string | null;
  /** Object key of the `_LATEST.json` pointer that named that generation; null under `census`. */
  availabilityPointerKey: string | null;
  /**
   * Newest day the SOURCE itself can offer, `YYYY-MM-DD`; null when nothing bounds it.
   *
   * Distinct from `latestDay`, which is what the warehouse HOLDS. A lane may hold a day past its
   * source's ceiling only through a bug -- a mislabelled partition, a clock skew, a forecast row
   * written into an observed stream -- and offering that day would put a date on the slider that
   * no upstream ever published. The capability gate withholds such a lane rather than clamping
   * it, because a lane that disagrees with its own source is not trustworthy on the days below
   * the ceiling either.
   */
  sourceCeilingDay: string | null;
  /**
   * Rungs the serving side declares this lane must publish before it may be read.
   *
   * Carried so the client can LABEL what was proved, not so it can lower the bar: the capability
   * gate still requires every rung of the ladder, so a lane declaring fewer cannot talk its way
   * into the census with partial evidence.
   */
  requiredRungs: readonly ZoomTier[];
  /**
   * Set when the availability index itself cannot be trusted, so nothing below it may be read.
   * Null on a healthy lane. See `PARQUET_AVAILABILITY_WITHHELD_REASONS`.
   */
  withheldReason: ParquetAvailabilityWithheldReason | null;
}

/** The whole warehouse's census: one entry per physical lane/rung, with no viewport. */
export interface ParquetWarehouseCoverage {
  /**
   * The body shape the serving side wrote, echoed rather than assumed.
   *
   * Carried even though `decodeCoverage` only ever accepts one value of it, because the number is
   * what an operator needs to see in a log line when a deploy half-lands: "the slider blanked"
   * and "the slider blanked because the service is still on version 1" are different pages.
   */
  coverageSchemaVersion: number;
  /**
   * When the service computed this census, as an opaque ISO-8601 instant. Load-bearing because the
   * answer is memoized for minutes: a caller captioning it as "now" would overstate its freshness.
   */
  generatedAt: string;
  /** UTC day through which cadence, absence, and gap obligations were evaluated. */
  evaluatedThroughDay: string;
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
 *  7. Coverage is per physical lane AND per tier. A part on z13 never proves z9/z5/z0 readable,
 *     and a generic signal part never proves one derived product or depth exists.
 *  8. Every coverage lane names its own AUTHORITY and may withhold itself. The six fields that
 *     carry this (`coverage_authority`, `availability_generation_sha256`,
 *     `availability_pointer_key`, `source_ceiling_day`, `required_rungs`, `withheld_reason`) are
 *     mandatory on every lane, `null` where they do not apply -- an omitted field is a contract
 *     break, not a lane that happens to be healthy. Nothing here reads `_LATEST.json` or
 *     `availability.parquet`; those stay entirely on the serving side and reach this client only
 *     as the six fields above.
 *  9. The coverage body names its own shape in `coverage_schema_version`, and this client accepts
 *     exactly one value of it. See `COVERAGE_SCHEMA_VERSION` below; bumping it is a change to
 *     `wire_contract.py`, `parquet_ops/wire.py`, the fixtures and this file in ONE commit.
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

const wireCalendarDaySchema = z.string().regex(CALENDAR_DAY_PATTERN);
const wireDayRangeSchema = z.object({ from: wireCalendarDaySchema, to: wireCalendarDaySchema });

/**
 * One rung of the ladder, spelled ONCE so `zoom` and `required_rungs` cannot drift apart.
 *
 * Literals rather than `z.number()`, so a rung the ladder does not publish is a contract error
 * here instead of a partition prefix nobody wrote. The proof that these ARE the ladder's rungs
 * is `decodeCoverage` below: it assigns them to `ZoomTier`-typed fields, so a literal added here
 * that `zoom-tiers.ts` does not publish fails the build.
 */
const wireZoomTierSchema = z.union([
  z.literal(0),
  z.literal(5),
  z.literal(9),
  z.literal(13),
]);

/** Lower-case hex, exactly as `availability.parquet` generations are named. */
const wireSha256Schema = z.string().regex(/^[a-f0-9]{64}$/);

/**
 * The coverage body shape this client is written against; `COVERAGE_SCHEMA_VERSION` in
 * `wire_contract.py` and `parquet_ops/wire.py`, which must be bumped in the same change.
 *
 * `1` was the field set frozen before availability indexes existed; `2` adds the six provenance
 * fields. REJECTED rather than logged when it disagrees: a version-1 body carries no
 * `withheld_reason` at all, and reading that silence as "every lane's index is healthy" is exactly
 * the fail-open this decode exists to close. A serving side that has not been redeployed yet must
 * blank the slider, not quietly narrow it. The rejection lives in `decodeCoverage` and names the
 * number, so the operator reading the log sees "still on version 1" rather than "contract drift".
 */
const COVERAGE_SCHEMA_VERSION = 2;

const wireCoverageSchema = z.object({
  // Decoded as a plain integer and gated separately in `decodeCoverage`, not pinned with
  // `z.literal` here: a zod failure would report "the census does not match the contract" for what
  // is really a half-landed deploy, and the version number is the one fact that says which.
  coverage_schema_version: z.number().int(),
  generated_at: z.string(),
  evaluated_through_day: wireCalendarDaySchema,
  lanes: z.array(
    z.object({
      layer: z.string(),
      nature: z.enum(PARQUET_LANE_NATURES),
      kind: z.enum(PARQUET_PARTITION_KINDS),
      zoom: wireZoomTierSchema,
      earliest_day: wireCalendarDaySchema.nullable(),
      latest_day: wireCalendarDaySchema.nullable(),
      published_ranges: z.array(wireDayRangeSchema),
      gap_ranges: z.array(wireDayRangeSchema),
      governed_absence_ranges: z.array(wireDayRangeSchema),
      coverage_authority: z.enum(PARQUET_COVERAGE_AUTHORITIES),
      availability_generation_sha256: wireSha256Schema.nullable(),
      // Mirrors `wire_contract.py` exactly, blankness included: a second, stricter opinion here
      // would reject a body the serving side considers valid, which is the drift the freeze forbids.
      availability_pointer_key: z.string().nullable(),
      source_ceiling_day: wireCalendarDaySchema.nullable(),
      required_rungs: z.array(wireZoomTierSchema),
      withheld_reason: z.enum(PARQUET_AVAILABILITY_WITHHELD_REASONS).nullable(),
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
 * Adjacency uses a Gregorian successor over the fixed-width string fields, never a Date or instant.
 * Endpoints and ordering alone cannot detect an omitted interior day. A short, holed, reordered or
 * duplicated answer is a contract error rather than a silently narrower window, because the days it
 * left out would otherwise read as days nothing is wrong with.
 */
function nextCalendarDay(day: string): string {
  let year = Number(day.slice(0, 4));
  let month = Number(day.slice(5, 7));
  let dayOfMonth = Number(day.slice(8, 10));
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [
    31,
    leapYear ? 29 : 28,
    31,
    30,
    31,
    30,
    31,
    31,
    30,
    31,
    30,
    31,
  ][month - 1];
  if (daysInMonth === undefined || dayOfMonth < 1 || dayOfMonth > daysInMonth) {
    throw new ParquetPlaneContractError(
      `Parquet plane window contains a non-calendar day ${day}`
    );
  }
  dayOfMonth += 1;
  if (dayOfMonth > daysInMonth) {
    dayOfMonth = 1;
    month += 1;
    if (month > 12) {
      month = 1;
      year += 1;
    }
  }
  if (year > 9999) {
    throw new ParquetPlaneContractError(
      `Parquet plane window exceeds the YYYY-MM-DD calendar after ${day}`
    );
  }
  return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(
    dayOfMonth
  ).padStart(2, "0")}`;
}

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
    const expectedDay = nextCalendarDay(days[index - 1].requestedDay);
    if (days[index].requestedDay !== expectedDay) {
      throw new ParquetPlaneContractError(
        `Parquet plane answered ${days[index].requestedDay} where ${expectedDay} was required in the window ${firstDay}..${lastDay}`
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
  if (parsed.data.coverage_schema_version !== COVERAGE_SCHEMA_VERSION) {
    throw new ParquetPlaneContractError(
      `Parquet plane answered coverage schema version ${parsed.data.coverage_schema_version}; ` +
        `this client reads only version ${COVERAGE_SCHEMA_VERSION}`
    );
  }
  return {
    coverageSchemaVersion: parsed.data.coverage_schema_version,
    generatedAt: parsed.data.generated_at,
    evaluatedThroughDay: parsed.data.evaluated_through_day,
    lanes: parsed.data.lanes.map((lane) => ({
      layer: lane.layer,
      nature: lane.nature,
      kind: lane.kind,
      zoomTier: lane.zoom,
      earliestDay: lane.earliest_day,
      latestDay: lane.latest_day,
      publishedRanges: lane.published_ranges,
      gapRanges: lane.gap_ranges,
      governedAbsenceRanges: lane.governed_absence_ranges,
      coverageAuthority: lane.coverage_authority,
      availabilityGenerationSha256: lane.availability_generation_sha256,
      availabilityPointerKey: lane.availability_pointer_key,
      sourceCeilingDay: lane.source_ceiling_day,
      requiredRungs: lane.required_rungs,
      withheldReason: lane.withheld_reason,
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

/** Everything a read may bound itself by, so `readJson` keeps one parameter per concern. */
interface ReadBounds {
  maxBytes: number;
  timeoutMs: number;
  /** Omitted means `cache: "no-store"`. */
  revalidateSeconds?: number;
  /** The caller's cancellation; the timeout above still applies either way. */
  signal?: AbortSignal;
}

/** GET with the shared bounds. */
async function readJson(url: URL, bounds: ReadBounds): Promise<unknown> {
  return fetchBoundedJson(
    url,
    { method: "GET", headers: { Accept: "application/json" } },
    {
      maxBytes: bounds.maxBytes,
      timeoutMs: bounds.timeoutMs,
      ...(bounds.revalidateSeconds === undefined
        ? {}
        : { revalidateSeconds: bounds.revalidateSeconds }),
      ...(bounds.signal === undefined ? {} : { signal: bounds.signal }),
    }
  );
}

/** The byte/time/cancellation bounds every ROW read shares. */
function rowReadBounds(request: ParquetReadBase): ReadBounds {
  return {
    maxBytes: MAX_ROW_RESPONSE_BYTES,
    timeoutMs: ROW_READ_TIMEOUT_MS,
    ...(request.signal === undefined ? {} : { signal: request.signal }),
  };
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
  return decodeEnvelope(await readJson(url, rowReadBounds(request)));
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
  const payload = await readJson(url, rowReadBounds(request));
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
  return decodeEnvelope(await readJson(url, rowReadBounds(request)));
}

/**
 * The whole warehouse's coverage census.
 *
 * No bbox or requested zoom: this one answer includes every rung and is shared by every viewport.
 * Filtering the request on either axis would fragment the cache and let one rung stand in for the
 * others at the capability gate.
 *
 * Takes NO caller signal, unlike the three row reads. The answer is single-flighted and memoized
 * across every session, so one caller's cancellation would abort an in-flight read that other
 * callers are already awaiting -- a browser tab closing would blank the slider for everyone else.
 * The 8-second budget is the only bound this read needs.
 */
export async function getParquetWarehouseCoverage(): Promise<ParquetWarehouseCoverage> {
  coverageRequest ??= (async () => {
    const url = endpoint(WIRE.routes.coverage);
    const payload = await readJson(url, {
      maxBytes: MAX_COVERAGE_RESPONSE_BYTES,
      timeoutMs: COVERAGE_TIMEOUT_MS,
      revalidateSeconds: COVERAGE_REVALIDATE_SECONDS,
    });
    return decodeCoverage(payload);
  })();
  const request = coverageRequest;
  try {
    return await request;
  } finally {
    if (coverageRequest === request) coverageRequest = null;
  }
}
