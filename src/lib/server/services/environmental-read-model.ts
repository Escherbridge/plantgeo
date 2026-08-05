import { and, desc, eq, gte, lte, sql, type SQL } from "drizzle-orm";
import { db } from "@/lib/server/db";
import { features, layers } from "@/lib/server/db/schema";
import { WEATHER_LAYER_ID } from "@/lib/server/layer-ids";
import type {
  MetricAtDateAvailability,
  MetricAtDateCollection,
  MetricAtDateInput,
  MetricAtDateProperties,
  SliderCapabilities,
  SliderLayerCapability,
  TemporalKind,
} from "@/types/time-slider";
import type { GroundwaterWell, WaterGauge } from "./usgs-water";
import { DROUGHT_CATEGORY_LABELS } from "./usdm-drought";
import {
  firmsDayRange,
  isFreshObservation,
  parseFirmsObservationTime,
  parseZonedObservationTime,
} from "./environmental-time";

const MAX_ROWS = 2_000;
const STREAMFLOW_MAX_AGE_MS = 6 * 60 * 60 * 1_000;
const DROUGHT_MAX_AGE_MS = 14 * 24 * 60 * 60 * 1_000;
/** Matches the upstream Open-Meteo freshness contract in services/weather.ts. */
const WEATHER_MAX_AGE_MS = 3 * 60 * 60 * 1_000;
/** Nearest-first candidates scanned before giving up on a fresh observation. */
const WEATHER_CANDIDATE_ROWS = 8;
/** Sane upper bound on observations rendered for a single viewport bbox. */
const WEATHER_BBOX_MAX_ROWS = 500;
/** ~5 km: USDM boundaries are hand-drawn at roughly this scale anyway. */
const NATIONAL_DROUGHT_TOLERANCE_DEGREES = 0.05;
/** ~55 m: below this the client cannot resolve the extra vertices. */
const MIN_DROUGHT_TOLERANCE_DEGREES = 0.0005;

/**
 * How old a vegetation cell's newest observation may be and still be served.
 *
 * Sentinel-2 revisits every five days but a cell only yields an NDVI sample on a scene
 * clear enough to read, so per-cell freshness is set by cloud, not by revisit. Measured
 * against production 2026-08-05 over all 1,568 cells on record: 1,503 were last observed
 * within 9 days and the remaining 65 between 15 and 28 days. 30 days therefore keeps every
 * cell the warehouse currently holds while still refusing a genuinely abandoned one, and a
 * 14-day window -- the value the drought reader uses -- would blank 65 real cells that have
 * simply been under cloud.
 *
 * It is also the read's main lever on cost: the stored series is four years deep
 * (2022-08-05 onward, 184,409 rows over those same 1,568 cells), so cutting the scan to a
 * 30-day window is what stops every viewport read from walking the whole history.
 */
const VEGETATION_MAX_OBSERVATION_AGE_DAYS = 30;
const VEGETATION_MAX_AGE_MS = VEGETATION_MAX_OBSERVATION_AGE_DAYS * 86_400_000;

/**
 * Upper bound on grid cells returned for one viewport.
 *
 * Stated rather than left implicit, because the bbox is NOT what bounds this read. The
 * sampling grid is fixed at 0.25 degrees, so after the latest-per-cell collapse below even
 * a whole-world bbox answers with the grid itself -- 1,568 cells today, against the 184,409
 * rows that back them. What the bbox buys is a smaller scan, not a smaller answer, and the
 * only thing standing between a future national grid and an unservable payload is this cap.
 * A truncated answer says so in `truncated` rather than silently serving a subset.
 */
const VEGETATION_MAX_CELLS = 4_000;

/**
 * USGS NWIS writes this in place of a reading it does not have -- an ice-affected gauge, a
 * failed sensor, a provisional value pulled back. It arrives as a JSON number and is stored
 * verbatim, so 259 of the 16,743 stored water-gauges rows currently carry it.
 *
 * It must be dropped rather than drawn: -999999 cfs is not a measurement, and rendering it
 * would both invent a reading where none exists and flatten every colour scale on the map.
 * Dropping it is not filtering data -- a gauge left with no real reading correctly reports a
 * gap instead of a fabricated low.
 *
 * Compared exactly, NEVER as "negative means missing": genuine reverse flow is recorded at
 * these gauges, down to -172,000 cfs in the current warehouse.
 */
const USGS_NO_DATA_SENTINEL = -999999;

/** True when a stored numeric field is an upstream "no reading" marker rather than a value. */
function isMissingValueSentinel(value: number | null, sentinel: number): boolean {
  return value !== null && value === sentinel;
}

const CALENDAR_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

/* ---------------------------------------------------------------------------
 * The day the map asked to draw
 *
 * Every viewport reader below answers either for the live edge or for one named calendar
 * day, and the two are separate code paths rather than one parameterized query. See
 * `src/lib/server/AGENTS.md` §slider-day for why.
 * ------------------------------------------------------------------------- */

/** How a reader must answer the day the slider asked for. */
export type RequestedObservationDay =
  /** No day was named, or the named day IS the server's today: read the live edge. */
  | { kind: "live" }
  /** A past day: read that day's own observations, never the live edge's. */
  | { kind: "historical"; date: string }
  /** Nothing is observed on that day, and nothing may be invented for it. */
  | { kind: "unobserved"; date: string; reason: string };

/**
 * Resolves the slider's optional day against the server's own today.
 *
 * The `live` branch is deliberately indistinguishable from no day at all: an omitted day and
 * today's date must run the exact query the reader has always run, because that is the first
 * paint of every session and the one path whose cost and behaviour are already measured.
 *
 * A future day is refused rather than answered. This warehouse publishes no forecast series
 * -- FORECAST_HORIZON_DAYS is 0 and every capability reports no forecast variants -- so the
 * only thing a future day could be answered with is the newest observation wearing a date it
 * does not describe, which is the fabrication the whole read model is written against.
 */
export function resolveRequestedObservationDay(
  date: string | undefined,
  today: string = serverCurrentDate()
): RequestedObservationDay {
  if (date === undefined) return { kind: "live" };
  if (!CALENDAR_DATE_PATTERN.test(date) || Number.isNaN(Date.parse(`${date}T00:00:00Z`))) {
    return { kind: "unobserved", date, reason: `"${date}" is not a calendar date.` };
  }
  if (date === today) return { kind: "live" };
  if (date > today) {
    return {
      kind: "unobserved",
      date,
      reason: `Nothing is observed on ${date}; the server's today is ${today}.`,
    };
  }
  return { kind: "historical", date };
}

/**
 * The calendar day the PUBLISHER named, read from a stored ISO string's own date part.
 *
 * The JavaScript twin of OBSERVATION_DAY, and load-bearing for the same reason:
 * `parseZonedObservationTime` normalizes to UTC, which MOVES 6,279 of the 16,743 stored USGS
 * gauge readings (37.5%) onto the following calendar day -- `2026-08-03T23:50:00.000-07:00`
 * normalizes to `2026-08-04T06:50Z`. Anything comparing a stored observation against a
 * requested day must therefore compare the named day, never the normalized instant.
 */
function publisherNamedDay(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const day = value.slice(0, 10);
  return CALENDAR_DATE_PATTERN.test(day) ? day : null;
}

/** True when a stored observation's publisher-named day is exactly `date`. */
function isObservedOnNamedDay(rawObservationTime: unknown, date: string): boolean {
  return publisherNamedDay(rawObservationTime) === date;
}

/** True when a stored observation's publisher-named day falls in (`after`, `through`]. */
function isObservedWithinNamedDays(
  rawObservationTime: unknown,
  after: string,
  through: string
): boolean {
  const day = publisherNamedDay(rawObservationTime);
  return day !== null && day > after && day <= through;
}

/**
 * OBSERVATION_DAY over one explicitly named JSONB text field.
 *
 * `substring(…, 1, 10)::date`, NEVER `(… AT TIME ZONE 'UTC')::date` -- see OBSERVATION_DAY's
 * own note for the 37.5% of gauge readings that rule is written against.
 */
function namedDaySql(observationTimeText: SQL): SQL {
  return sql`substring(${observationTimeText}, 1, 10)::date`;
}

export function parseBbox(value: string): [number, number, number, number] {
  const coordinates = value.split(",").map(Number);
  if (
    coordinates.length !== 4 ||
    coordinates.some((coordinate) => !Number.isFinite(coordinate))
  ) {
    throw new RangeError("Bounding box must contain four finite numbers");
  }
  const [west, south, east, north] = coordinates;
  if (
    west < -180 ||
    east > 180 ||
    south < -90 ||
    north > 90 ||
    west >= east ||
    south >= north
  ) {
    throw new RangeError("Bounding box must be ordered within WGS84 bounds");
  }
  return [west, south, east, north];
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

function parsePoint(value: unknown): [number, number] | null {
  const geometry = asRecord(value);
  const coordinates = geometry?.coordinates;
  if (geometry?.type !== "Point" || !Array.isArray(coordinates)) return null;
  const longitude = Number(coordinates[0]);
  const latitude = Number(coordinates[1]);
  if (
    !Number.isFinite(longitude) ||
    longitude < -180 ||
    longitude > 180 ||
    !Number.isFinite(latitude) ||
    latitude < -90 ||
    latitude > 90
  ) {
    return null;
  }
  return [longitude, latitude];
}

function finiteNumber(value: unknown): number | null {
  if (
    (typeof value !== "number" && typeof value !== "string") ||
    (typeof value === "string" && value.trim() === "")
  ) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Fire detections for one named day, or the live FIRMS lookback window. */
type FireDetectionRow = { properties: unknown };

/**
 * One day's accepted fire detections, bucketed on the publisher's named day.
 *
 * A separate statement rather than an extra predicate on the live read, because the live read
 * floors on `created_at` -- a "last touched" column the refresh path rewrites -- which for a
 * past day returns today's rows and nothing else. FIRMS dates a detection with `acqDate`
 * (plus `acqTime`), so that is what the day predicate reads, falling back to `observedAt` for
 * rows whose producer already wrote one.
 *
 * Compared as text, not cast to `date`: a single unparseable stored `acqDate` would make a
 * `::date` in the predicate abort the whole statement, whereas the reader's job is to omit
 * that one detection. Both sides are the same fixed-width YYYY-MM-DD form, so the comparison
 * is exact.
 */
async function readFireDetectionsOnDay(
  date: string,
  area: [number, number, number, number] | null
): Promise<FireDetectionRow[]> {
  const observedDayText = sql`COALESCE(
    substring(f.properties->>'observedAt', 1, 10),
    f.properties->>'acqDate'
  )`;
  return db.execute<FireDetectionRow>(sql`
    SELECT f.properties
    FROM geo.features f
    JOIN geo.layers l ON l.id = f.layer_id
    WHERE l.name = ${process.env.FIRMS_LAYER_ID ?? "fire-detections"}
      AND f.status = 'published'
      AND ${observedDayText} = ${date}
      ${
        area
          ? sql`AND f.geom && ST_MakeEnvelope(${area[0]}, ${area[1]}, ${area[2]}, ${area[3]}, 4326)`
          : sql``
      }
    ORDER BY f.properties->>'acqTime' DESC NULLS LAST
    LIMIT ${MAX_ROWS}
  `);
}

/**
 * Reads bounded fire observations already accepted into the platform store.
 *
 * @param date optional YYYY-MM-DD; omitted (or the server's today) reads the live FIRMS
 *   lookback window unchanged, a past day reads that day's own detections, and a future day
 *   returns empty rather than restamping the newest detections.
 */
export async function getPublishedFireDetections(
  bbox?: string,
  dayRange = firmsDayRange(),
  date?: string
): Promise<GeoJSON.FeatureCollection<GeoJSON.Point>> {
  const area = bbox ? parseBbox(bbox) : null;
  const day = resolveRequestedObservationDay(date);
  if (day.kind === "unobserved") return { type: "FeatureCollection", features: [] };
  if (day.kind === "historical") {
    return collectFireDetections(
      await readFireDetectionsOnDay(day.date, area),
      day,
      dayRange
    );
  }

  const since = new Date(Date.now() - dayRange * 86_400_000);
  const rows = await db
    .select({ properties: features.properties })
    .from(features)
    .innerJoin(layers, eq(features.layerId, layers.id))
    .where(
      and(
        eq(layers.name, process.env.FIRMS_LAYER_ID ?? "fire-detections"),
        eq(features.status, "published"),
        gte(features.createdAt, since),
        area ? gte(sql<number>`ST_X(${features.geom})`, area[0]) : undefined,
        area ? lte(sql<number>`ST_X(${features.geom})`, area[2]) : undefined,
        area ? gte(sql<number>`ST_Y(${features.geom})`, area[1]) : undefined,
        area ? lte(sql<number>`ST_Y(${features.geom})`, area[3]) : undefined
      )
    )
    .orderBy(desc(features.createdAt))
    .limit(MAX_ROWS);

  return collectFireDetections(rows, day, dayRange);
}

/**
 * Turns stored detection rows into drawable points, dropping any that cannot be dated.
 *
 * The window each branch applies is what keeps a named day honest: at the live edge it is the
 * FIRMS lookback in `dayRange`, and for a named day it is that day itself. Re-anchoring the
 * lookback to a past date would accept detections from the two days before it, which the map
 * would then draw under the requested date.
 */
function collectFireDetections(
  rows: readonly FireDetectionRow[],
  day: RequestedObservationDay,
  dayRange: number
): GeoJSON.FeatureCollection<GeoJSON.Point> {
  const published: GeoJSON.Feature<GeoJSON.Point>[] = [];
  for (const row of rows) {
    const properties = asRecord(row.properties);
    const point = parsePoint(properties?.geometry);
    const observedAt = properties
      ? parseFirmsObservationTime(properties)
      : null;
    const isWithinWindow =
      day.kind === "historical"
        ? isObservedOnNamedDay(
            properties?.observedAt ?? properties?.acqDate,
            day.date
          )
        : observedAt !== null &&
          isFreshObservation(observedAt, dayRange * 86_400_000);
    if (!properties || !point || !observedAt || !isWithinWindow) continue;
    const { geometry: _geometry, ...safeProperties } = properties;
    published.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: point },
      properties: { ...safeProperties, observedAt },
    });
  }
  return { type: "FeatureCollection", features: published };
}

/** One stored water-gauges row, as both the live and the named-day read path consume it. */
type StreamflowRow = { properties: unknown };

/**
 * One day's newest reading per gauge, bucketed on the publisher's named day.
 *
 * A separate statement rather than an extra predicate on the live read, for two reasons. The
 * live read pages by `created_at DESC` -- a "last touched" column -- so for a past day it
 * returns today's rows and nothing else. And DISTINCT ON makes MAX_ROWS count gauges rather
 * than readings: a busy national day is 10,911 readings over far fewer sites, and paging
 * readings would silently answer with a subset of the country.
 *
 * The freshness window for a named day IS that day. STREAMFLOW_MAX_AGE_MS's six hours exist
 * to refuse a stale reading at the LIVE edge; re-anchoring six hours to the end of a past day
 * would drop every gauge whose last reading that day landed before 18:00, which is most of
 * them, and report a real day as unobserved.
 */
async function readStreamflowGaugesOnDay(
  date: string,
  west: number,
  south: number,
  east: number,
  north: number
): Promise<StreamflowRow[]> {
  return db.execute<StreamflowRow>(sql`
    SELECT DISTINCT ON (f.properties->>'siteNo') f.properties
    FROM geo.features f
    JOIN geo.layers l ON l.id = f.layer_id
    WHERE l.name = ${process.env.WATER_GAUGES_LAYER_ID ?? "water-gauges"}
      AND f.status = 'published'
      AND f.properties->>'siteNo' IS NOT NULL
      AND ${namedDaySql(sql`f.properties->>'updatedAt'`)} = ${date}::date
      AND f.geom && ST_MakeEnvelope(${west}, ${south}, ${east}, ${north}, 4326)
    ORDER BY
      f.properties->>'siteNo',
      (f.properties->>'updatedAt')::timestamptz DESC
    LIMIT ${MAX_ROWS}
  `);
}

/**
 * Reads warehouse-backed streamflow observations in a viewport.
 *
 * @param date optional YYYY-MM-DD; omitted (or the server's today) returns the latest reading
 *   per gauge inside the live freshness window, unchanged. A past day returns that day's
 *   newest reading per gauge. A future day returns empty -- `WaterGauge[]` has no slot to
 *   explain itself in, and the layer's own capability is what captions an empty day (see
 *   `getSliderCapabilities` and `useLayerRenderState`).
 */
export async function getPublishedStreamflowGauges(
  bbox: string,
  date?: string
): Promise<WaterGauge[]> {
  const [west, south, east, north] = parseBbox(bbox);
  const day = resolveRequestedObservationDay(date);
  if (day.kind === "unobserved") return [];

  const rows: StreamflowRow[] =
    day.kind === "historical"
      ? await readStreamflowGaugesOnDay(day.date, west, south, east, north)
      : await db
          .select({ properties: features.properties })
          .from(features)
          .innerJoin(layers, eq(features.layerId, layers.id))
          .where(
            and(
              eq(layers.name, process.env.WATER_GAUGES_LAYER_ID ?? "water-gauges"),
              eq(features.status, "published"),
              gte(sql<number>`ST_X(${features.geom})`, west),
              lte(sql<number>`ST_X(${features.geom})`, east),
              gte(sql<number>`ST_Y(${features.geom})`, south),
              lte(sql<number>`ST_Y(${features.geom})`, north)
            )
          )
          .orderBy(desc(features.createdAt))
          .limit(MAX_ROWS);

  const gauges = new Map<string, WaterGauge>();
  for (const row of rows) {
    const value = asRecord(row.properties);
    if (!value) continue;
    const siteNo = typeof value.siteNo === "string" ? value.siteNo : "";
    const point = parsePoint(value.geometry);
    const updatedAt = parseZonedObservationTime(value.updatedAt);
    // Re-checked here rather than trusted from SQL, the same way the vegetation reader
    // re-checks its own cutoff: the day predicate is bound before the round trip.
    const isWithinWindow =
      day.kind === "historical"
        ? isObservedOnNamedDay(value.updatedAt, day.date)
        : updatedAt !== null && isFreshObservation(updatedAt, STREAMFLOW_MAX_AGE_MS);
    if (!siteNo || !point || !updatedAt || !isWithinWindow || gauges.has(siteNo)) {
      continue;
    }

    // The same exclusion the slider's METRIC_SOURCES applies. The whole row is dropped
    // rather than the field nulled: a gauge with no reading is a gap in the water layer,
    // and a pin drawn with a null flow still asserts "this gauge reported today".
    const flowCfs = finiteNumber(value.flowCfs);
    if (isMissingValueSentinel(flowCfs, USGS_NO_DATA_SENTINEL)) continue;

    const condition = value?.condition;
    const trend = value?.trend;
    gauges.set(siteNo, {
      siteNo,
      siteName: typeof value.siteName === "string" ? value.siteName : siteNo,
      lat: point[1],
      lon: point[0],
      flowCfs,
      percentile: finiteNumber(value.percentile),
      condition:
        condition === "above_normal" ||
        condition === "normal" ||
        condition === "below_normal" ||
        condition === "low" ||
        condition === "critically_low"
          ? condition
          : "unknown",
      trend:
        trend === "rising" || trend === "stable" || trend === "declining"
          ? trend
          : null,
      updatedAt,
    });
  }
  return [...gauges.values()];
}

export interface PublishedWeatherObservation {
  lat: number;
  lon: number;
  observedAt: string;
  temperature: number | null;
  humidity: number | null;
  windSpeed: number | null;
  windDirection: number | null;
  precipitation: number | null;
}

/**
 * Reads the nearest fresh warehouse-backed weather observation to a point.
 * runWeatherIngestionJob samples a coarse grid across INGEST_BBOX, so the
 * nearest sample is returned rather than an interpolation; null means no fresh
 * observation has been published near the point.
 */
export async function getPublishedWeatherForPoint(
  lat: number,
  lon: number
): Promise<PublishedWeatherObservation | null> {
  if (
    !Number.isFinite(lat) ||
    lat < -90 ||
    lat > 90 ||
    !Number.isFinite(lon) ||
    lon < -180 ||
    lon > 180
  ) {
    throw new RangeError("Point must be within WGS84 bounds");
  }

  // MATERIALIZED pins the layer/status filter ahead of the KNN sort, so a sparse
  // weather set cannot walk a large slice of the GiST index before finding its
  // candidates. Returned coordinates are read from the same geom column the sort
  // ranks -- never from the properties copy, which can drift from it silently.
  const rows = await db.execute<{
    properties: unknown;
    lon: number | null;
    lat: number | null;
  }>(sql`
    WITH candidates AS MATERIALIZED (
      SELECT f.properties, f.geom
      FROM geo.features f
      JOIN geo.layers l ON l.id = f.layer_id
      WHERE l.name = ${WEATHER_LAYER_ID}
        AND f.status = 'published'
    )
    SELECT
      properties,
      ST_X(geom) AS lon,
      ST_Y(geom) AS lat
    FROM candidates
    ORDER BY geom <-> ST_SetSRID(ST_MakePoint(${lon}, ${lat}), 4326)
    LIMIT ${WEATHER_CANDIDATE_ROWS}
  `);

  for (const row of rows) {
    const value = asRecord(row.properties);
    const observedAt = parseZonedObservationTime(value?.observedAt);
    const rowLat = finiteNumber(row.lat);
    const rowLon = finiteNumber(row.lon);
    if (
      !value ||
      rowLat === null ||
      rowLon === null ||
      !observedAt ||
      !isFreshObservation(observedAt, WEATHER_MAX_AGE_MS)
    ) {
      continue;
    }
    return {
      lat: rowLat,
      lon: rowLon,
      observedAt,
      temperature: finiteNumber(value.temperature),
      humidity: finiteNumber(value.humidity),
      windSpeed: finiteNumber(value.windSpeed),
      windDirection: finiteNumber(value.windDirection),
      precipitation: finiteNumber(value.precipitation),
    };
  }
  return null;
}

/** True when every rendered field was actually measured -- never zero-filled. */
export function isCompleteWeatherObservation(
  observation: Pick<
    PublishedWeatherObservation,
    "windSpeed" | "windDirection" | "temperature" | "humidity"
  >
): boolean {
  return (
    observation.windSpeed !== null &&
    observation.windDirection !== null &&
    observation.temperature !== null &&
    observation.humidity !== null
  );
}

/** Object type, not an interface: db.execute requires an implicit index signature. */
type WeatherDayRow = {
  properties: unknown;
  lon: number | null;
  lat: number | null;
};

/**
 * One day's newest observation per grid point, bucketed on the publisher's named day.
 *
 * Coordinates come from the `geom` column the DISTINCT ON ranks, never from the properties
 * copy -- the same rule `getPublishedWeatherForPoint` follows, because the two can drift
 * apart silently. The grid is fixed, so collapsing to the newest row per point is what turns
 * a whole day of ingest into one sample per place.
 *
 * The freshness window for a named day IS that day. WEATHER_MAX_AGE_MS's three hours mirror
 * the Open-Meteo contract at the LIVE edge; re-anchored to the end of a past day it would keep
 * roughly one ingest tick in eight and report the rest of a fully observed day as empty.
 */
async function readWeatherOnDay(
  date: string,
  west: number,
  south: number,
  east: number,
  north: number
): Promise<WeatherDayRow[]> {
  return db.execute<WeatherDayRow>(sql`
    SELECT DISTINCT ON (ST_X(f.geom), ST_Y(f.geom))
      f.properties,
      ST_X(f.geom) AS lon,
      ST_Y(f.geom) AS lat
    FROM geo.features f
    JOIN geo.layers l ON l.id = f.layer_id
    WHERE l.name = ${WEATHER_LAYER_ID}
      AND f.status = 'published'
      AND ${namedDaySql(sql`f.properties->>'observedAt'`)} = ${date}::date
      AND f.geom && ST_MakeEnvelope(${west}, ${south}, ${east}, ${north}, 4326)
    ORDER BY
      ST_X(f.geom),
      ST_Y(f.geom),
      (f.properties->>'observedAt')::timestamptz DESC
    LIMIT ${WEATHER_BBOX_MAX_ROWS}
  `);
}

/** Turns one stored weather row into an observation, or null when it cannot be drawn. */
function toWeatherObservation(
  properties: Record<string, unknown> | null,
  point: [number, number] | null,
  observedAt: string | null
): PublishedWeatherObservation | null {
  if (!properties || !point || !observedAt) return null;
  const observation: PublishedWeatherObservation = {
    lat: point[1],
    lon: point[0],
    observedAt,
    temperature: finiteNumber(properties.temperature),
    humidity: finiteNumber(properties.humidity),
    windSpeed: finiteNumber(properties.windSpeed),
    windDirection: finiteNumber(properties.windDirection),
    precipitation: finiteNumber(properties.precipitation),
  };
  // Every rendered field must be measured: a partial observation is dropped rather than
  // back-filled with a zero the upstream never reported.
  return isCompleteWeatherObservation(observation) ? observation : null;
}

/**
 * Reads every published, complete weather observation intersecting a viewport
 * bbox. Unlike getPublishedWeatherForPoint's nearest-1 KNN, this powers the
 * wind layer with the full warehouse spread instead of a single sample --
 * capped at WEATHER_BBOX_MAX_ROWS to bound render cost.
 *
 * @param date optional YYYY-MM-DD; omitted (or the server's today) reads the live freshness
 *   window unchanged, a past day reads that day's newest sample per grid point, and a future
 *   day returns empty rather than restamping the newest samples.
 */
export async function getPublishedWeatherForBbox(
  bbox: string,
  date?: string
): Promise<PublishedWeatherObservation[]> {
  const [west, south, east, north] = parseBbox(bbox);
  const day = resolveRequestedObservationDay(date);
  if (day.kind === "unobserved") return [];

  if (day.kind === "historical") {
    const dayRows = await readWeatherOnDay(day.date, west, south, east, north);
    const observations: PublishedWeatherObservation[] = [];
    for (const row of dayRows) {
      const value = asRecord(row.properties);
      const rowLat = finiteNumber(row.lat);
      const rowLon = finiteNumber(row.lon);
      if (rowLat === null || rowLon === null) continue;
      if (!isObservedOnNamedDay(value?.observedAt, day.date)) continue;
      const observation = toWeatherObservation(
        value,
        [rowLon, rowLat],
        parseZonedObservationTime(value?.observedAt)
      );
      if (observation) observations.push(observation);
    }
    return observations;
  }

  const rows = await db
    .select({ properties: features.properties })
    .from(features)
    .innerJoin(layers, eq(features.layerId, layers.id))
    .where(
      and(
        eq(layers.name, WEATHER_LAYER_ID),
        eq(features.status, "published"),
        gte(sql<number>`ST_X(${features.geom})`, west),
        lte(sql<number>`ST_X(${features.geom})`, east),
        gte(sql<number>`ST_Y(${features.geom})`, south),
        lte(sql<number>`ST_Y(${features.geom})`, north)
      )
    )
    .orderBy(desc(features.createdAt))
    .limit(WEATHER_BBOX_MAX_ROWS);

  const observations: PublishedWeatherObservation[] = [];
  for (const row of rows) {
    const value = asRecord(row.properties);
    const point = parsePoint(value?.geometry);
    const observedAt = parseZonedObservationTime(value?.observedAt);
    if (observedAt === null || !isFreshObservation(observedAt, WEATHER_MAX_AGE_MS)) {
      continue;
    }
    const observation = toWeatherObservation(value, point, observedAt);
    if (observation) observations.push(observation);
  }
  return observations;
}

/** Why a drought viewport read came back empty. The payload has no free-text slot. */
export type PublishedDroughtReason =
  | "not_published"
  | "invalid_observation_time"
  | "stale"
  /** The requested day is in the future; USDM is not forecast. */
  | "not_forecastable"
  /**
   * A release week the record does not hold. It must render EMPTY: a later release is stored,
   * so the preceding one's coverage ended at its own week and filling the gap would invent a
   * map. `usdm_history.ingest_release_week` records exactly this case as `is_gap`.
   */
  | "release_week_not_published";

export interface PublishedDroughtCollection extends GeoJSON.FeatureCollection {
  availability: "published" | "unavailable";
  observedAt: string | null;
  reason?: PublishedDroughtReason;
  /**
   * Days between the release actually served and the day asked for. 0 at the release's own
   * date; non-zero means a legitimately carried-forward weekly release, never an interpolation.
   */
  carryForwardDays?: number;
}

/**
 * Simplification tolerance in degrees for a viewport width.
 * Roughly one screen pixel at a 2000px-wide map, so the clipped polygon is
 * generalized to what the client can actually resolve instead of shipping
 * full-resolution national rings.
 */
function droughtSimplifyTolerance(bboxWidthDegrees: number | null): number {
  if (bboxWidthDegrees === null) return NATIONAL_DROUGHT_TOLERANCE_DEGREES;
  return Math.min(
    NATIONAL_DROUGHT_TOLERANCE_DEGREES,
    Math.max(MIN_DROUGHT_TOLERANCE_DEGREES, bboxWidthDegrees / 2_000)
  );
}

/** Object type, not an interface: db.execute requires an implicit index signature. */
type DroughtAreaRow = {
  dm_category: number;
  valid_date: string;
  source_url: string;
  geometry: string | null;
};

/**
 * Reads one USDM release's classes, clipped and generalized in PostGIS.
 *
 * @param validDate the release to read, or null for the newest stored one.
 *
 * The stored geometry is full-resolution (~19 MB nationally), so clipping to the viewport and
 * simplifying happens in the database -- returning the raw collection would be unservable.
 * Simplifying generalizes boundaries; it never reclassifies, and a class whose clipped
 * geometry is empty is omitted by the caller rather than emitted as a null feature.
 */
async function readDroughtRelease(
  validDate: string | null,
  area: [number, number, number, number] | null
): Promise<DroughtAreaRow[]> {
  const tolerance = droughtSimplifyTolerance(area ? area[2] - area[0] : null);

  // ST_CollectionExtract keeps the clip polygon-only: intersecting a polygon
  // with an envelope can yield touching edges as lines/points at the boundary.
  const clipped = area
    ? sql`ST_CollectionExtract(
        ST_Intersection(
          d.geom,
          ST_MakeEnvelope(${area[0]}, ${area[1]}, ${area[2]}, ${area[3]}, 4326)
        ),
        3
      )`
    : sql`d.geom`;

  return db.execute<DroughtAreaRow>(sql`
    ${
      validDate === null
        ? sql`WITH latest AS (
            SELECT valid_date
            FROM geo.drought_areas
            ORDER BY valid_date DESC
            LIMIT 1
          )`
        : sql``
    }
    SELECT
      d.dm_category,
      d.valid_date,
      d.source_url,
      ST_AsGeoJSON(
        ST_SimplifyPreserveTopology(${clipped}, ${tolerance})
      ) AS geometry
    FROM geo.drought_areas d
    ${validDate === null ? sql`JOIN latest ON latest.valid_date = d.valid_date` : sql``}
    WHERE ${validDate === null ? sql`TRUE` : sql`d.valid_date = ${validDate}::date`}
    ${
      area
        ? sql`AND d.geom && ST_MakeEnvelope(${area[0]}, ${area[1]}, ${area[2]}, ${area[3]}, 4326)`
        : sql``
    }
    ORDER BY d.dm_category
  `);
}

/** Which stored USDM release describes a date, or why none does. */
type DroughtReleaseResolution =
  | { kind: "resolved"; asOfRelease: string; carryForwardDays: number }
  | {
      kind: "unavailable";
      availability: MetricAtDateAvailability;
      /** Sentence for the metric reader, which has a free-text reason slot. */
      reason: string;
      /** Enum code for the viewport reader, whose payload does not. */
      code: PublishedDroughtReason;
    };

/**
 * Resolves which stored USDM release describes a date, with the carry-forward BOUNDED.
 *
 * Shared by `getPublishedDroughtClassification` and `getDroughtMetricAtDate` so the layer and
 * the slider metric can never disagree about what a day means.
 *
 * USDM is a weekly snapshot, not a daily series, so an exact-date match would make six days in
 * seven look empty. Carrying the release forward is what the publisher itself does -- a release
 * stands until the next one supersedes it -- and every caller reports the release's OWN date,
 * never the requested one, so a value is never dressed up as fresher than it is.
 *
 * The bound depends on whether the record already knows what comes next. When a later release
 * is stored, this release's coverage ended at its own week: a request landing past that week
 * sits in a release week the record does not hold, and that week must render EMPTY.
 * `usdm_history.ingest_release_week` records exactly this case as
 * `skip_reason="not_published"`/`is_gap=True`, and `history_gap_weeks` documents that "the
 * slider must render these empty" -- an unbounded lookback would fill the gap the ingest lane
 * honestly reported, painting drought classes on a week USDM never published. When no later
 * release is stored we are at the live edge instead, where the newest release legitimately
 * stands until the next Thursday publication, bounded by DROUGHT_MAX_CARRY_FORWARD_DAYS.
 */
async function resolveDroughtRelease(
  date: string
): Promise<DroughtReleaseResolution> {
  const releases = await db.execute<{
    earliest_release: string | null;
    as_of_release: string | null;
    next_release: string | null;
  }>(sql`
    SELECT
      MIN(valid_date) AS earliest_release,
      MAX(valid_date) FILTER (WHERE valid_date <= ${date}) AS as_of_release,
      MIN(valid_date) FILTER (WHERE valid_date > ${date}) AS next_release
    FROM geo.drought_areas
  `);
  const earliestRelease = toCalendarDate(releases[0]?.earliest_release);
  const asOfRelease = toCalendarDate(releases[0]?.as_of_release);
  const nextRelease = toCalendarDate(releases[0]?.next_release);

  if (earliestRelease === null) {
    return {
      kind: "unavailable",
      availability: "not_yet_observed",
      reason: "No US Drought Monitor release has been published yet.",
      code: "not_published",
    };
  }
  if (asOfRelease === null) {
    return {
      kind: "unavailable",
      availability: "not_yet_observed",
      reason: `No US Drought Monitor release covers ${date}; the record starts ${earliestRelease}.`,
      code: "not_published",
    };
  }

  const carryForwardDays = utcDayDifference(asOfRelease, date) ?? 0;
  if (nextRelease !== null && carryForwardDays >= DROUGHT_RELEASE_INTERVAL_DAYS) {
    return {
      kind: "unavailable",
      availability: "not_published",
      reason: `No US Drought Monitor release covers ${date}; the record skips from ${asOfRelease} to ${nextRelease}.`,
      code: "release_week_not_published",
    };
  }
  if (nextRelease === null && carryForwardDays > DROUGHT_MAX_CARRY_FORWARD_DAYS) {
    return {
      kind: "unavailable",
      availability: "not_published",
      reason: `The newest US Drought Monitor release is ${asOfRelease}, ${carryForwardDays} days before ${date}; it is too stale to describe that day.`,
      code: "stale",
    };
  }
  return { kind: "resolved", asOfRelease, carryForwardDays };
}

/** A drought viewport read that states why it is empty instead of just being empty. */
function emptyDroughtCollection(
  reason: PublishedDroughtReason,
  observedAt: string | null
): PublishedDroughtCollection {
  return {
    type: "FeatureCollection",
    features: [],
    availability: "unavailable",
    observedAt,
    reason,
  };
}

/**
 * Reads the accepted USDM release covering a day, clipped and generalized in PostGIS.
 *
 * Never fetches upstream on request.
 *
 * @param bbox optional "west,south,east,north"; omitted returns the national extent.
 * @param date optional YYYY-MM-DD. Omitted (or the server's today) reads the newest stored
 *   release under the existing now-relative DROUGHT_MAX_AGE_MS gate, unchanged. A past day
 *   resolves through `resolveDroughtRelease`, so the weekly carry-forward and its bound are
 *   the same ones the slider metric applies -- a release week the record skips renders EMPTY.
 *   A future day is refused: USDM is not forecast.
 */
export async function getPublishedDroughtClassification(
  bbox?: string,
  date?: string
): Promise<PublishedDroughtCollection> {
  const area = bbox ? parseBbox(bbox) : null;
  const day = resolveRequestedObservationDay(date);
  if (day.kind === "unobserved") {
    return emptyDroughtCollection(
      CALENDAR_DATE_PATTERN.test(day.date) ? "not_forecastable" : "not_published",
      null
    );
  }

  let asOfRelease: string | null = null;
  let carryForwardDays = 0;
  if (day.kind === "historical") {
    const resolution = await resolveDroughtRelease(day.date);
    if (resolution.kind === "unavailable") {
      return emptyDroughtCollection(resolution.code, null);
    }
    asOfRelease = resolution.asOfRelease;
    carryForwardDays = resolution.carryForwardDays;
  }

  const rows = await readDroughtRelease(asOfRelease, area);
  if (rows.length === 0) {
    return emptyDroughtCollection("not_published", null);
  }

  const observedAt = parseZonedObservationTime(`${rows[0].valid_date}T00:00:00Z`);
  if (!observedAt) {
    return emptyDroughtCollection("invalid_observation_time", null);
  }
  // At the live edge, staleness is measured against now. For a named day the bound is already
  // the release-week rule above, which is what lets a historical day be served at all.
  if (asOfRelease === null && !isFreshObservation(observedAt, DROUGHT_MAX_AGE_MS)) {
    return emptyDroughtCollection("stale", observedAt);
  }

  const collected: GeoJSON.Feature[] = [];
  for (const row of rows) {
    if (!row.geometry) continue;
    const geometry = JSON.parse(row.geometry) as GeoJSON.Geometry;
    // An empty clip means this class does not reach the viewport at all.
    if (
      (geometry.type === "MultiPolygon" || geometry.type === "Polygon") &&
      geometry.coordinates.length === 0
    ) {
      continue;
    }
    collected.push({
      type: "Feature",
      geometry,
      properties: {
        DM: row.dm_category,
        label: DROUGHT_CATEGORY_LABELS[row.dm_category] ?? null,
        validDate: row.valid_date,
        observedAt,
        source: "US Drought Monitor",
        sourceUrl: row.source_url,
      },
    });
  }

  const collection: PublishedDroughtCollection = {
    type: "FeatureCollection",
    features: collected,
    availability: "published",
    observedAt,
  };
  // Only meaningful when a day was actually named: at the live edge nothing was carried
  // forward TO, so reporting 0 there would answer a question nobody asked.
  if (day.kind === "historical") collection.carryForwardDays = carryForwardDays;
  return collection;
}

export interface DroughtCategoryAtPoint {
  dmCategory: number;
  validDate: string;
  observedAt: string;
  sourceUrl: string;
}

/**
 * Highest USDM drought class containing a point, evaluated in PostGIS.
 * Returns null when the newest release is stale or the point is in no class --
 * "no drought reported here" is never reported as D0.
 */
export async function getDroughtCategoryAtPoint(
  lat: number,
  lon: number
): Promise<DroughtCategoryAtPoint | null> {
  if (
    !Number.isFinite(lat) ||
    lat < -90 ||
    lat > 90 ||
    !Number.isFinite(lon) ||
    lon < -180 ||
    lon > 180
  ) {
    throw new RangeError("Point must be within WGS84 bounds");
  }

  const rows = await db.execute<{
    dm_category: number;
    valid_date: string;
    source_url: string;
  }>(sql`
    WITH latest AS (
      SELECT valid_date
      FROM geo.drought_areas
      ORDER BY valid_date DESC
      LIMIT 1
    )
    SELECT d.dm_category, d.valid_date, d.source_url
    FROM geo.drought_areas d
    JOIN latest ON latest.valid_date = d.valid_date
    WHERE ST_Intersects(d.geom, ST_SetSRID(ST_MakePoint(${lon}, ${lat}), 4326))
    ORDER BY d.dm_category DESC
    LIMIT 1
  `);

  const row = rows[0];
  if (!row) return null;
  const observedAt = parseZonedObservationTime(`${row.valid_date}T00:00:00Z`);
  if (!observedAt || !isFreshObservation(observedAt, DROUGHT_MAX_AGE_MS)) {
    return null;
  }
  return {
    dmCategory: row.dm_category,
    validDate: row.valid_date,
    observedAt,
    sourceUrl: row.source_url,
  };
}

/** Canonical `geo.layers.name` for the NDVI grid; mirrors the producer's own env override. */
const VEGETATION_LAYER_ID = process.env.VEGETATION_LAYER_ID ?? "vegetation";

/**
 * One sampling-grid cell's newest NDVI reading, with the provenance that dates it.
 * A type alias rather than an interface, for the same reason MetricAtDateProperties is one:
 * only an alias picks up the implicit index signature GeoJSON's `properties` slot needs.
 */
export type PublishedVegetationCellProperties = {
  /** geo.geometry identity of the cell; stable across observations of the same place. */
  geometryId: string;
  /** The producer's own grid key, e.g. "43.1250:-113.6250". */
  cellKey: string;
  ndvi: number;
  observedAt: string;
  sceneId: string | null;
  cloudCover: number | null;
  /** Usable pixels behind this cell's NDVI; a thin cell is legible as thin. */
  sampleCount: number | null;
  gridName: string | null;
  resolutionMetres: number | null;
  source: string | null;
  /** The stored natural key (cellKey:observedAt), so one reading is traceable upstream. */
  provenanceKey: string;
};

export interface PublishedVegetationCollection
  extends GeoJSON.FeatureCollection<GeoJSON.Polygon | GeoJSON.MultiPolygon> {
  availability: "published" | "unavailable";
  /** Newest observation in the returned set; null when nothing was returned. */
  observedAt: string | null;
  /**
   * `stale` means cells exist here but none was observed inside the window ending at the
   * requested day; `not_forecastable` means the day itself is in the future.
   */
  reason: "not_published" | "stale" | "not_forecastable" | null;
  /** More cells intersect the viewport than the cap allows; this set is a subset. */
  truncated: boolean;
  cellCount: number;
  /** Both bounds published, so the client never has to infer either one. */
  maxCellCount: number;
  maxObservationAgeDays: number;
}

/** Object type, not an interface: db.execute requires an implicit index signature. */
type VegetationCellRow = {
  geometry_id: string | null;
  geometry: string | null;
  ndvi: string | null;
  observed_at: string | null;
  cell_key: string | null;
  scene_id: string | null;
  cloud_cover: string | null;
  sample_count: string | null;
  grid_name: string | null;
  resolution_metres: string | null;
  source: string | null;
  provenance_key: string | null;
};

/** A viewport that holds no vegetation cell at all, or none observed recently enough. */
function emptyVegetationCollection(
  reason: NonNullable<PublishedVegetationCollection["reason"]>,
  observedAt: string | null
): PublishedVegetationCollection {
  return {
    type: "FeatureCollection",
    features: [],
    availability: "unavailable",
    observedAt,
    reason,
    truncated: false,
    cellCount: 0,
    maxCellCount: VEGETATION_MAX_CELLS,
    maxObservationAgeDays: VEGETATION_MAX_OBSERVATION_AGE_DAYS,
  };
}

/**
 * Reads the newest published NDVI observation per sampling-grid cell in a viewport.
 *
 * The whole difficulty is that `vegetation` is a four-year daily series stacked on a small
 * fixed grid, not a snapshot: 184,409 published rows over 1,568 distinct cells, ~118
 * observations of the same place each. Returning the rows raw would draw the same cell a
 * hundred times over, so the read collapses to one row per `geo.geometry` identity -- the
 * newest -- exactly as `getPublishedStreamflowGauges` keeps one row per gauge and
 * `getMetricAtDate` keeps one per geometry. Measured, that collapse is the entire payload
 * story: a PNW viewport goes from 124,959 rows to 1,036 cells.
 *
 * Three bounds, all explicit rather than left to the viewport's good behaviour:
 *   - the bbox filters the scan (`&&` against the GiST index on geo.features.geom);
 *   - VEGETATION_MAX_OBSERVATION_AGE_DAYS bounds how far back a cell may have been seen,
 *     applied in SQL so the cap below counts only cells that will actually be drawn;
 *   - VEGETATION_MAX_CELLS caps the answer, probed one row over so `truncated` is never
 *     claimed against a result that merely filled the page exactly.
 *
 * Nothing is interpolated, carried forward or averaged: a cell under cloud for a month is
 * omitted, not painted with a value from before the cloud.
 *
 * @param date optional YYYY-MM-DD. Omitted (or the server's today) applies the 30-day window
 *   ending NOW, unchanged. A past day slides the same window to end at that day -- "the newest
 *   reading per cell within VEGETATION_MAX_OBSERVATION_AGE_DAYS ending at the selected date" --
 *   which is what stops a request for last spring returning an empty grid under a now-relative
 *   cutoff. A future day returns empty: Sentinel-2 has not flown it.
 */
export async function getPublishedVegetationIndex(
  bbox: string,
  date?: string
): Promise<PublishedVegetationCollection> {
  const [west, south, east, north] = parseBbox(bbox);
  const day = resolveRequestedObservationDay(date);
  if (day.kind === "unobserved") {
    return emptyVegetationCollection("not_forecastable", null);
  }
  const freshSince = new Date(Date.now() - VEGETATION_MAX_AGE_MS).toISOString();
  // The observation window as publisher-named days, for a named day only: (after, through].
  const windowThroughDay = day.kind === "historical" ? day.date : null;
  const windowAfterDay =
    windowThroughDay === null
      ? null
      : addUtcDays(windowThroughDay, -VEGETATION_MAX_OBSERVATION_AGE_DAYS);
  const observedDay = namedDaySql(sql`f.properties->>'observedAt'`);
  // The live branch keeps the timestamptz cutoff it has always used -- every stored value
  // carries an explicit `Z`, so for this layer the named day and the UTC day agree; the named
  // day is what a bounded historical window is expressed in, because that is the form a
  // requested date arrives as.
  const observationWindowSql =
    windowThroughDay === null || windowAfterDay === null
      ? sql`(f.properties->>'observedAt')::timestamptz >= ${freshSince}::timestamptz`
      : sql`${observedDay} > ${windowAfterDay}::date AND ${observedDay} <= ${windowThroughDay}::date`;
  const tolerance = droughtSimplifyTolerance(east - west);

  // Every stored cell is a 5-vertex axis-aligned square (verified: ST_NPoints = 5 on all
  // 184,409 rows, in geo.features.geom and in the geo.geometry dimension alike), so
  // simplification has nothing to remove and is skipped by geometry kind rather than paid
  // for per row -- the same CASE getMetricAtDate uses to leave point layers untouched. The
  // ELSE branch is what keeps a future finer or irregular cell servable.
  const geometrySql = sql`CASE
    WHEN g.geom_kind IN ('point', 'grid_cell') THEN g.geom
    ELSE ST_SimplifyPreserveTopology(g.geom, ${tolerance})
  END`;

  // Values come back as text and are parsed by finiteNumber rather than cast in SQL: a
  // single non-numeric JSONB value would make a `::double precision` in the projection
  // abort the whole statement, whereas the reader's job is to drop that one cell. The one
  // exception is `ndvi`, whose jsonb_typeof test in the WHERE clause is load-bearing for a
  // different reason -- excluding a valueless cell there is what makes LIMIT count only
  // cells that will be drawn.
  //
  // The DISTINCT ON sort casts `observedAt` to timestamptz, which is deterministic here
  // because every stored value carries an explicit `Z`. NULLs cannot win the sort's default
  // NULLS FIRST either: the same expression is filtered above, so a cell with no readable
  // observation time never reaches the ranking.
  const rows = await db.execute<VegetationCellRow>(sql`
    WITH candidate AS (
      SELECT f.geometry_id, f.properties
      FROM geo.features f
      JOIN geo.layers l ON l.id = f.layer_id
      WHERE l.name = ${VEGETATION_LAYER_ID}
        AND f.status = 'published'
        AND f.geometry_id IS NOT NULL
        AND jsonb_typeof(f.properties->'ndvi') = 'number'
        AND ${observationWindowSql}
        AND f.geom && ST_MakeEnvelope(${west}, ${south}, ${east}, ${north}, 4326)
    )
    SELECT DISTINCT ON (c.geometry_id)
      g.geometry_id::text AS geometry_id,
      ST_AsGeoJSON(${geometrySql}) AS geometry,
      c.properties->>'ndvi' AS ndvi,
      c.properties->>'observedAt' AS observed_at,
      c.properties->>'cellKey' AS cell_key,
      c.properties->>'sceneId' AS scene_id,
      c.properties->>'cloudCover' AS cloud_cover,
      c.properties->>'sampleCount' AS sample_count,
      c.properties->>'gridName' AS grid_name,
      c.properties->>'resolutionMetres' AS resolution_metres,
      c.properties->>'source' AS source,
      COALESCE(c.properties->>'id', g.geometry_id::text) AS provenance_key
    FROM candidate c
    JOIN geo.geometry g ON g.geometry_id = c.geometry_id
    ORDER BY c.geometry_id, (c.properties->>'observedAt')::timestamptz DESC
    LIMIT ${VEGETATION_MAX_CELLS + 1}
  `);

  if (rows.length === 0) {
    // Paid for only in the empty case, and only to tell two very different answers apart:
    // a viewport the grid does not cover at all, versus one it covers where every cell has
    // been under cloud longer than the window. MAX over the ISO-8601 text is chronological
    // because every stored value is the same fixed-width UTC format.
    //
    // Bounded to the requested day for a named day: a cell first sampled AFTER that day says
    // nothing about whether the grid covered the viewport then, and reporting it as the
    // "newest observation" here would caption a never-sampled day as merely stale.
    const newest = await db.execute<{ observed_at: string | null }>(sql`
      SELECT MAX(f.properties->>'observedAt') AS observed_at
      FROM geo.features f
      JOIN geo.layers l ON l.id = f.layer_id
      WHERE l.name = ${VEGETATION_LAYER_ID}
        AND f.status = 'published'
        AND f.geom && ST_MakeEnvelope(${west}, ${south}, ${east}, ${north}, 4326)
        ${
          windowThroughDay === null
            ? sql``
            : sql`AND ${observedDay} <= ${windowThroughDay}::date`
        }
    `);
    const newestObservedAt = parseZonedObservationTime(newest[0]?.observed_at);
    return newestObservedAt === null
      ? emptyVegetationCollection("not_published", null)
      : emptyVegetationCollection("stale", newestObservedAt);
  }

  const collected: GeoJSON.Feature<GeoJSON.Polygon | GeoJSON.MultiPolygon>[] = [];
  let newestObservedAt: string | null = null;
  for (const row of rows.slice(0, VEGETATION_MAX_CELLS)) {
    const ndvi = finiteNumber(row.ndvi);
    const observedAt = parseZonedObservationTime(row.observed_at);
    // Re-checked after the round trip because the cutoff above was computed before it.
    const isWithinWindow =
      windowThroughDay === null || windowAfterDay === null
        ? observedAt !== null && isFreshObservation(observedAt, VEGETATION_MAX_AGE_MS)
        : isObservedWithinNamedDays(row.observed_at, windowAfterDay, windowThroughDay);
    if (
      !row.geometry ||
      row.geometry_id === null ||
      ndvi === null ||
      observedAt === null ||
      !isWithinWindow
    ) {
      continue;
    }
    const properties: PublishedVegetationCellProperties = {
      geometryId: row.geometry_id,
      cellKey: row.cell_key ?? row.geometry_id,
      ndvi,
      observedAt,
      sceneId: row.scene_id,
      cloudCover: finiteNumber(row.cloud_cover),
      sampleCount: finiteNumber(row.sample_count),
      gridName: row.grid_name,
      resolutionMetres: finiteNumber(row.resolution_metres),
      source: row.source,
      provenanceKey: row.provenance_key ?? row.geometry_id,
    };
    if (newestObservedAt === null || observedAt > newestObservedAt) {
      newestObservedAt = observedAt;
    }
    collected.push({
      type: "Feature",
      id: row.geometry_id,
      geometry: JSON.parse(row.geometry) as GeoJSON.Polygon | GeoJSON.MultiPolygon,
      properties,
    });
  }

  if (collected.length === 0) {
    return emptyVegetationCollection("not_published", null);
  }
  return {
    type: "FeatureCollection",
    features: collected,
    availability: "published",
    observedAt: newestObservedAt,
    reason: null,
    truncated: rows.length > VEGETATION_MAX_CELLS,
    cellCount: collected.length,
    maxCellCount: VEGETATION_MAX_CELLS,
    maxObservationAgeDays: VEGETATION_MAX_OBSERVATION_AGE_DAYS,
  };
}

/**
 * Reserved read model for a future versioned groundwater layer.
 *
 * `date` is accepted and deliberately unread: no groundwater observation is published on ANY
 * day, so there is nothing for a day predicate to narrow. Taking the parameter now is what
 * lets the caller pass the slider's day uniformly instead of special-casing this one layer,
 * and makes the eventual producer a change to this body alone.
 */
export async function getPublishedGroundwaterWells(
  bbox: string,
  _date?: string
): Promise<GroundwaterWell[]> {
  parseBbox(bbox);
  return [];
}

/* ---------------------------------------------------------------------------
 * Time-slider read model
 *
 * Backed entirely by geo.features -> geo.geometry and geo.drought_areas. There is
 * deliberately no geo.metric_daily: with the warehouse holding days rather than
 * years of history, a dedicated fact table would be premature.
 *
 * These queries are NOT index-driven, and no index can make them so as written. The day
 * predicate is an expression over a JSONB-derived text value, and the btree that would turn
 * it into an Index Cond cannot be created: text->timestamptz and text->date are both
 * CoerceViaIO conversions (pg_cast holds no entry for either), which Postgres treats as
 * STABLE, so CREATE INDEX rejects the expression with 42P17 "functions in index expression
 * must be marked IMMUTABLE". A generated column inherits the same restriction. Measured
 * today: the capability scan is 85 ms over 25,328 rows, a bboxed metric read 0.85 ms, an
 * unbboxed one 44 ms. That is affordable at two days of history and will not be at two years.
 *
 * The bounds that keep it honest until then are (a) the capability payload is memoized rather
 * than recomputed per request, and (b) a viewport bbox narrows the metric read. When history
 * forces the issue, the fix is an explicit IMMUTABLE SQL wrapper over the same COALESCE with a
 * btree on (layer_id, observed_day) -- and that wrapper is only truthful because every stored
 * value carries an explicit UTC offset (verified: 26,416 datable rows, none offsetless). Guard
 * that invariant at write time in the ingest lane first; one offsetless value would land on a
 * different day than the index expected and the two would silently disagree.
 * ------------------------------------------------------------------------- */

/**
 * Gap, in days, that ends a layer's continuous observation record.
 *
 * DO NOT replace the clustering below with `min(version_valid_from)` or any bare
 * `min()`, however much simpler it looks. A handful of DISCONTINUED USGS gauges
 * carry an upstream `updatedAt` of their final-ever reading -- two on 1990-10-01,
 * one on 1990-12-14, then scattered singles through the 1990s and 2000s. A bare
 * min() therefore reports 1990-10-01 for water-gauges and the slider renders a
 * 36-YEAR axis that is empty except for a couple of days at the far right edge:
 * technically correct, completely unusable.
 *
 * Measured against production, this threshold separates the two cases cleanly and
 * is not merely tuned to one layer:
 *   water-gauges    largest gap INSIDE the real record 15 days; the gap that
 *                   separates the 1990-2025 stragglers is 23 days.
 *   fire-perimeters largest gap inside the real record 12 days; the gap back to
 *                   its own isolated 2025-07-28 row is 324 days.
 * A flat count-per-day threshold was rejected: one calibrated to water-gauges'
 * ~9,000-reading days erases fire-perimeters' entire genuine history (which never
 * exceeds 20 rows/day), and one calibrated to fire-perimeters accepts every 1990s
 * straggler. Density has to be measured as continuity, not volume.
 */
const OBSERVATION_CLUSTER_GAP_DAYS = 21;

/**
 * Density floor, as a fraction of the layer's own busiest day inside that newest cluster.
 *
 * Continuity alone does NOT defuse the 36-year trap; it only shortens it. Measured against
 * production, clustering moves water-gauges from 1990-09-30 to 2026-05-24, but the days it
 * keeps between 2026-05-24 and 2026-08-01 carry 1-7 readings each (35 in total) against
 * 15,844 on the last three days. Those 35 are the SAME artifact as the 1990 rows -- the
 * final-ever reading of a discontinued gauge -- and they survive only because straggler
 * spacing happens to tighten toward the present. The axis they produce is 73 days long with
 * 39 days completely empty: scrub to 2026-06-15 and the entire national map draws ONE gauge.
 *
 * The floor is relative to each layer's own peak, never an absolute row count, because an
 * absolute threshold calibrated to water-gauges' ~10,900-reading days erases fire-perimeters'
 * entire genuine record (1-19 perimeters a day) and one calibrated to fire-perimeters accepts
 * every straggler. Measured band for the current warehouse:
 *   water-gauges    must exceed 7/10,911 = 0.064% to drop the stragglers, and stay at or
 *                   below 2,236/10,911 = 20.5% to keep 2026-08-02.
 *   fire-perimeters must stay at or below 1/19 = 5.26% to keep every genuine single-perimeter
 *                   day; its own isolated 2025-07-28 row is already excluded by clustering.
 * 1% sits inside (0.064%, 5.26%] for every layer, near the middle of it on a log scale.
 *
 * The floor only moves the START of the axis forward. Sparse days INSIDE the window are kept
 * and render as the genuine thin days they are -- this never removes an observation.
 *
 * It MUST be bound into SQL with an explicit `::numeric` cast. postgres-js sends a plain JS
 * number as an UNTYPED parameter -- its `inferType` answers OID 0 for every number that is
 * not a bigint -- so Postgres resolves the parameter's type from the expression around it.
 * Multiplied against `MAX(observation_count)`, which is a `COUNT(*)` bigint, operator
 * resolution picks `bigint * bigint` and tries to read "0.01" as a bigint. That is exactly
 * how getSliderCapabilities answered 500 in production with
 * `invalid input syntax for type bigint: "0.01"`, which left TimeSliderPanel's capabilities
 * query rejected and the time slider permanently unmounted. No other fractional parameter in
 * this file needs the cast: every one of them lands in a PostGIS function argument whose
 * signature already declares `double precision`, so there is nothing for Postgres to guess.
 */
const OBSERVATION_DENSITY_FLOOR_FRACTION = 0.01;

/** Upper bound on features returned for one metric/day; a full unbounded day of
 * water-gauges is ~9,000 points, which is a viewport bbox's job to narrow. */
const METRIC_AT_DATE_MAX_ROWS = 5_000;

/** How `earliestObservedDate` was decided, so the UI never has to guess. */
export type EarliestObservedDateRule =
  /** Isolated older observations exist but were excluded from the axis. */
  | "gap_clustered"
  /** Days inside the newest run were too sparse to anchor an axis and were excluded. */
  | "density_floored"
  /** Every observed day forms one continuous, populated run; nothing was excluded. */
  | "full_history"
  /** The layer has no datable, mappable observation at all. */
  | "no_observations";

/**
 * SliderLayerCapability plus the provenance of its earliest date.
 * Structurally a SliderLayerCapability, so it satisfies the shared contract while
 * carrying fields src/types/time-slider.ts has no slot for yet.
 */
export interface ResolvedSliderLayerCapability extends SliderLayerCapability {
  earliestObservedDateRule: EarliestObservedDateRule;
  /**
   * Oldest datable observation on record, including every day the two rules excluded.
   * Differs from earliestObservedDate whenever anything was excluded -- that difference is
   * the 1990 artifact and its modern stragglers, surfaced rather than hidden.
   */
  earliestRecordedObservationDate: string | null;
  /** Start of the newest continuous run, before the density floor was applied. */
  earliestContinuousObservationDate: string | null;
  /** Distinct observed days inside the reported window. */
  observedDayCount: number;
  /** Distinct observed days excluded by both rules together. */
  excludedObservedDayCount: number;
  /** Distinct observed days excluded because they preceded the continuous run. */
  gapExcludedObservedDayCount: number;
  /** Distinct observed days excluded because they fell below the density floor. */
  densityExcludedObservedDayCount: number;
  /** Observations a day needed to anchor the axis; null when the layer has none. */
  minimumDailyObservationCount: number | null;
}

export interface ResolvedSliderCapabilities extends SliderCapabilities {
  layers: ResolvedSliderLayerCapability[];
}

/**
 * Temporal shape per geo.layers.name. This lives in code because geo.layers has no
 * temporal columns -- it carries id/name/type/style/zoom/team/sort_order and nothing
 * about time -- so there is no table to read it from and inventing one is out of scope.
 */
const LAYER_TEMPORAL_KINDS: Readonly<Record<string, TemporalKind>> = {
  "vegetation": "daily_series",
  "weather-observations": "daily_series",
  "water-gauges": "daily_series",
  "fire-detections": "event",
  "fire-perimeters": "event",
  "interventions": "snapshot",
  "evacuation-zones": "snapshot",
  "sensors": "snapshot",
};

/** Conservative shape for a geo.layers row added after this registry was written. */
const DEFAULT_TEMPORAL_KIND: TemporalKind = "snapshot";

/**
 * Every layer reports a zero forecast horizon and no forecast variants because no
 * forecast series exists anywhere in the warehouse: agri.signal_observation and the
 * historical_* tables are empty, and the forecast governance layer was retired. The
 * client turns this into "not forecast beyond today", which is true. Publishing a
 * non-zero horizon here would invite requests for a series nothing can answer, and
 * the resulting empty map would read as a bug rather than as an absent capability.
 * When a forecast producer lands, this is the single place that opens the horizon.
 */
const FORECAST_HORIZON_DAYS = 0;

/** Which upstream payload field dates an observation, in priority order.
 *
 * NEVER add features.created_at or features.updated_at here. Those are "last
 * touched" columns that the refresh path rewrites, so dating an observation from
 * either would silently re-stamp historical readings to the day of the last run.
 * Verified against production that these three keys never co-occur on one row:
 * fire-detections/weather-observations carry only observedAt, water-gauges only
 * updatedAt (the USGS reading time, offset-bearing), fire-perimeters only
 * polygonDateTime. All stored values carry an explicit UTC offset, so the
 * timestamptz cast is deterministic rather than session-dependent. */
const OBSERVATION_TIME_TEXT = sql`COALESCE(
  f.properties->>'observedAt',
  f.properties->>'updatedAt',
  f.properties->>'polygonDateTime'
)`;

/** Instant of an observation. Used for ordering within a day, never for bucketing days. */
const OBSERVATION_TIME = sql`(${OBSERVATION_TIME_TEXT})::timestamptz`;

/**
 * The calendar day the PUBLISHER named, taken from the ISO string's own date part.
 *
 * DO NOT restore `(… AT TIME ZONE 'UTC')::date`. Measured against production, water-gauges
 * is the only layer whose payload carries a non-UTC offset -- all 16,743 rows carry
 * `-07:00`, while fire-detections, weather-observations and fire-perimeters all store `Z`.
 * Under UTC bucketing 6,279 of the 16,743 gauge readings (37.5%) land on the day AFTER the
 * one their own timestamp names: `2026-08-03T23:50:00.000-07:00` buckets to 2026-08-04. A
 * user cross-checking that gauge on waterdata.usgs.gov for August 3 sees the 23:50 reading
 * while the slider shows it on August 4 -- the map renders correctly and lies about the day.
 *
 * Reading the ISO prefix is exact for every stored value (verified: all 26,416 datable rows
 * carry an explicit offset, none is offsetless) and, unlike the text->timestamptz cast, it
 * cannot move with the session TimeZone. `serverCurrentDate` stays UTC; every US offset is
 * negative, so a publisher-named day can never exceed the server's UTC today.
 */
const OBSERVATION_DAY = sql`substring(${OBSERVATION_TIME_TEXT}, 1, 10)::date`;

/** A metric the slider can request, and the stored field that answers it. */
interface MetricSource {
  /** geo.layers.name that holds the observations. */
  layerName: string;
  /** Numeric key inside geo.features.properties. */
  valueKey: string;
  /** Human label used in availability reasons. */
  label: string;
  /** Exact upstream "no reading" marker to exclude, when the producer emits one. */
  missingValueSentinel?: number;
}

/**
 * Metric key -> backing observation field. Only fields verified to be genuinely
 * numeric in production appear here; a metric absent from this map is reported as
 * unavailable rather than guessed at. Notably `percentile` is NOT listed: it exists
 * on water-gauges rows but is never a JSON number, so it cannot answer a metric.
 */
const METRIC_SOURCES: Readonly<Record<string, MetricSource>> = {
  "streamflow-cfs": {
    layerName: "water-gauges",
    valueKey: "flowCfs",
    label: "Streamflow",
    missingValueSentinel: USGS_NO_DATA_SENTINEL,
  },
  "temperature": { layerName: "weather-observations", valueKey: "temperature", label: "Temperature" },
  "humidity": { layerName: "weather-observations", valueKey: "humidity", label: "Humidity" },
  "precipitation": { layerName: "weather-observations", valueKey: "precipitation", label: "Precipitation" },
  "wind-speed": { layerName: "weather-observations", valueKey: "windSpeed", label: "Wind speed" },
  "wind-direction": { layerName: "weather-observations", valueKey: "windDirection", label: "Wind direction" },
  // Both FIRMS channels exclude an exact 0, which is the parser's "no reading", not a
  // measurement. The TypeScript ingester looked up only `header.indexOf("brightness")`, which
  // is -1 for every VIIRS product, and wrote `parseFloat(cell) || 0`: measured against
  // production, all 6,297 stored detections carry `brightness: 0` and 6 carry `frp: 0`. A
  // brightness temperature of 0 K is physically impossible and a detection radiating exactly
  // 0 MW is not an observation either, so serving them as `valueKind: "observed"` would report
  // 5,000 fires at absolute zero. Each FIRMS natural key embeds its own acqDate:acqTime and
  // `firms_day_range()` defaults to 2, so those rows fall outside every future fetch window and
  // are never revisited -- the exclusion is the only thing standing between them and the map.
  // `firms.py:_numeric_column` no longer mints the placeholder, so a future row with no readable
  // cell omits the key entirely and is excluded by the jsonb_typeof test instead.
  "fire-radiative-power": {
    layerName: "fire-detections",
    valueKey: "frp",
    label: "Fire radiative power",
    missingValueSentinel: 0,
  },
  "fire-brightness": {
    layerName: "fire-detections",
    valueKey: "brightness",
    label: "Brightness",
    missingValueSentinel: 0,
  },
  "perimeter-acres": { layerName: "fire-perimeters", valueKey: "gisAcres", label: "Perimeter area" },
  "percent-contained": { layerName: "fire-perimeters", valueKey: "percentContained", label: "Containment" },
};

/** Metric key served from geo.drought_areas rather than geo.features. */
const DROUGHT_METRIC_KEY = "drought-category";

/**
 * USDM publishes weekly, valid each Tuesday, so one release covers exactly the seven days
 * from its own valid_date. A release may legitimately be carried forward across those days --
 * that is what the publisher itself does -- but never further.
 */
const DROUGHT_RELEASE_INTERVAL_DAYS = 7;

/**
 * How far the NEWEST stored release may be carried past its own week when no later release
 * exists yet. USDM publishes the Tuesday-valid map on the following Thursday, so the newest
 * release is routinely 7-9 days old at the live edge and refusing it would blank the layer
 * two days a week. Bounded by the same 14 days getPublishedDroughtClassification already
 * applies, so a stalled weekly job stops being served instead of aging without limit.
 */
const DROUGHT_MAX_CARRY_FORWARD_DAYS = DROUGHT_MAX_AGE_MS / 86_400_000;

/** Server UTC today. The only definition of "today" the slider is allowed to use. */
export function serverCurrentDate(nowMs: number = Date.now()): string {
  return new Date(nowMs).toISOString().slice(0, 10);
}

/** Adds whole days to a YYYY-MM-DD string in UTC. */
function addUtcDays(date: string, dayCount: number): string {
  const startMs = Date.parse(`${date}T00:00:00Z`);
  if (Number.isNaN(startMs)) return date;
  return new Date(startMs + dayCount * 86_400_000).toISOString().slice(0, 10);
}

/** Whole days from one YYYY-MM-DD to another, or null when either is not a calendar date. */
function utcDayDifference(fromDate: string, toDate: string): number | null {
  const fromMs = Date.parse(`${fromDate}T00:00:00Z`);
  const toMs = Date.parse(`${toDate}T00:00:00Z`);
  if (Number.isNaN(fromMs) || Number.isNaN(toMs)) return null;
  return Math.round((toMs - fromMs) / 86_400_000);
}

/** Object type, not an interface: db.execute requires an implicit index signature. */
type ObservationWindowRow = {
  layer_name: string;
  dense_earliest_day: string | null;
  clustered_earliest_day: string | null;
  recorded_earliest_day: string | null;
  dense_day_count: number | string | null;
  clustered_day_count: number | string | null;
  recorded_day_count: number | string | null;
  density_floor: number | string | null;
};

/**
 * Observed days per layer, run through both axis rules: continuity clustering keeps the most
 * recent run, then a density floor keeps the part of that run dense enough to anchor an axis.
 *
 * `geometry_id IS NOT NULL` matters: getMetricAtDate INNER JOINs geo.geometry, so a day whose
 * only observations are unlinked would otherwise advertise availability and return nothing.
 *
 * That filter is a floor on the axis, not a description of the warehouse, and unlinked rows are
 * the steady state rather than an anomaly: the `/api/ingest/*` push routes write through
 * `services/ingest.ts`, which never sets geometry_id. What bounds the resulting depth loss is
 * `agri-cli ingest-geometry-repair`, which now runs as the last job of every `ingest-all` tick
 * (see `ingest/runner.py`); before that it had no CLI verb at all and could only be run by hand
 * with a production DSN, so a skipped manual run silently cost the slider years of depth and
 * read as a rendering bug. getMetricAtDate reports its own per-day exclusion count separately.
 *
 * Reads every layer in one pass. Do NOT reintroduce a per-layer parameter -- getMetricAtDate
 * used to call this once per request just to range-check a date, which made every metric read
 * two whole-layer scans; it now shares one cached payload with getSliderCapabilities.
 */
async function readObservationWindows(): Promise<Map<string, ObservationWindowRow>> {
  const rows = await db.execute<ObservationWindowRow>(sql`
    WITH observed AS (
      SELECT
        l.name AS layer_name,
        ${OBSERVATION_DAY} AS observed_day,
        COUNT(*) AS observation_count
      FROM geo.layers l
      JOIN geo.features f ON f.layer_id = l.id
      WHERE f.status = 'published'
        AND f.geometry_id IS NOT NULL
        AND ${OBSERVATION_TIME_TEXT} IS NOT NULL
      GROUP BY l.name, ${OBSERVATION_DAY}
    ),
    gapped AS (
      SELECT
        layer_name,
        observed_day,
        observation_count,
        observed_day - LAG(observed_day) OVER (
          PARTITION BY layer_name ORDER BY observed_day
        ) AS gap_days
      FROM observed
    ),
    clustered AS (
      SELECT
        layer_name,
        observed_day,
        observation_count,
        SUM(CASE WHEN gap_days IS NULL OR gap_days > ${OBSERVATION_CLUSTER_GAP_DAYS} THEN 1 ELSE 0 END)
          OVER (PARTITION BY layer_name ORDER BY observed_day) AS cluster_index
      FROM gapped
    ),
    ranked AS (
      SELECT
        clustered.*,
        MAX(cluster_index) OVER (PARTITION BY layer_name) AS newest_cluster_index
      FROM clustered
    ),
    newest_cluster AS (
      SELECT layer_name, observed_day, observation_count
      FROM ranked
      WHERE cluster_index = newest_cluster_index
    ),
    density AS (
      SELECT
        layer_name,
        -- ::numeric is load-bearing, not decoration: without it Postgres resolves this
        -- untyped parameter against the bigint on its left and rejects "0.01". See the
        -- constant's own note.
        GREATEST(
          1,
          CEIL(MAX(observation_count) * ${OBSERVATION_DENSITY_FLOOR_FRACTION}::numeric)
        )::bigint AS density_floor
      FROM newest_cluster
      GROUP BY layer_name
    ),
    dense_start AS (
      SELECT n.layer_name, MIN(n.observed_day) AS dense_earliest_day
      FROM newest_cluster n
      JOIN density d ON d.layer_name = n.layer_name
      WHERE n.observation_count >= d.density_floor
      GROUP BY n.layer_name
    )
    SELECT
      l.name AS layer_name,
      s.dense_earliest_day,
      MIN(r.observed_day) FILTER (
        WHERE r.cluster_index = r.newest_cluster_index
      ) AS clustered_earliest_day,
      MIN(r.observed_day) AS recorded_earliest_day,
      COUNT(r.observed_day) FILTER (
        WHERE r.cluster_index = r.newest_cluster_index
          AND s.dense_earliest_day IS NOT NULL
          AND r.observed_day >= s.dense_earliest_day
      ) AS dense_day_count,
      COUNT(r.observed_day) FILTER (
        WHERE r.cluster_index = r.newest_cluster_index
      ) AS clustered_day_count,
      COUNT(r.observed_day) AS recorded_day_count,
      MAX(d.density_floor) AS density_floor
    FROM geo.layers l
    LEFT JOIN ranked r ON r.layer_name = l.name
    LEFT JOIN dense_start s ON s.layer_name = l.name
    LEFT JOIN density d ON d.layer_name = l.name
    GROUP BY l.name, s.dense_earliest_day
    ORDER BY l.name
  `);

  const windows = new Map<string, ObservationWindowRow>();
  for (const row of rows) windows.set(row.layer_name, row);
  return windows;
}

/** Normalizes a DATE column, which the driver may hand back as a Date or a string. */
function toCalendarDate(value: unknown): string | null {
  if (value instanceof Date) return value.toISOString().slice(0, 10);
  if (typeof value !== "string" || !CALENDAR_DATE_PATTERN.test(value.slice(0, 10))) {
    return null;
  }
  return value.slice(0, 10);
}

function toCount(value: number | string | null): number {
  if (value === null) return 0;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

/**
 * Turns one layer's window row into the capability the client consumes.
 * The rule reports the STRONGEST exclusion that moved the date, so a layer whose stragglers
 * were removed by both rules reads "density_floored" rather than hiding that behind
 * "gap_clustered"; the per-rule counts beside it keep both exclusions inspectable.
 */
function buildCapability(row: ObservationWindowRow): ResolvedSliderLayerCapability {
  const denseEarliest = toCalendarDate(row.dense_earliest_day);
  const clusteredEarliest = toCalendarDate(row.clustered_earliest_day);
  const recordedEarliest = toCalendarDate(row.recorded_earliest_day);
  const denseDays = toCount(row.dense_day_count);
  const clusteredDays = toCount(row.clustered_day_count);
  const recordedDays = toCount(row.recorded_day_count);
  const densityFloor = row.density_floor === null ? null : toCount(row.density_floor);

  const rule: EarliestObservedDateRule =
    denseEarliest === null
      ? "no_observations"
      : denseEarliest !== clusteredEarliest
        ? "density_floored"
        : clusteredEarliest !== recordedEarliest
          ? "gap_clustered"
          : "full_history";

  return {
    layerName: row.layer_name,
    temporalKind: LAYER_TEMPORAL_KINDS[row.layer_name] ?? DEFAULT_TEMPORAL_KIND,
    forecastHorizonDays: FORECAST_HORIZON_DAYS,
    forecastVariants: [],
    earliestObservedDate: denseEarliest,
    earliestObservedDateRule: rule,
    earliestRecordedObservationDate: recordedEarliest,
    earliestContinuousObservationDate: clusteredEarliest,
    observedDayCount: denseDays,
    excludedObservedDayCount: Math.max(0, recordedDays - denseDays),
    gapExcludedObservedDayCount: Math.max(0, recordedDays - clusteredDays),
    densityExcludedObservedDayCount: Math.max(0, clusteredDays - denseDays),
    minimumDailyObservationCount: denseEarliest === null ? null : densityFloor,
  };
}

/**
 * How long a computed capability list is reused.
 *
 * readObservationWindows cannot be made index-driven -- it must produce DISTINCT observed
 * days per layer, which needs every row, and the expression index that would help cannot be
 * created because text->timestamptz/date is a CoerceViaIO conversion Postgres treats as
 * STABLE (CREATE INDEX rejects it with 42P17). Measured today the scan is 85 ms over 25,328
 * rows; at one year of ingest it is millions of rows and gigabytes of heap per call, and
 * environmental.getSliderCapabilities is a publicProcedure anyone can call in a loop.
 *
 * So it is not served per request. The payload only changes when an ingest run lands a new
 * day, which is at most hourly, and a single-flight guard collapses a burst of concurrent
 * callers (a scrub prefetch fans out several at once) onto one scan.
 */
const CAPABILITIES_CACHE_TTL_MS = 5 * 60_000;

let cachedLayerCapabilities: {
  expiresAtMs: number;
  layers: ResolvedSliderLayerCapability[];
} | null = null;
let layerCapabilitiesInFlight: Promise<ResolvedSliderLayerCapability[]> | null = null;

/** Drops the memoized capability list. Exists so tests never inherit another test's payload. */
export function clearSliderCapabilitiesCache(): void {
  cachedLayerCapabilities = null;
  layerCapabilitiesInFlight = null;
}

/** The per-layer capability list, computed at most once per TTL across all callers. */
async function readLayerCapabilities(): Promise<ResolvedSliderLayerCapability[]> {
  const cached = cachedLayerCapabilities;
  if (cached !== null && cached.expiresAtMs > Date.now()) return cached.layers;
  if (layerCapabilitiesInFlight !== null) return layerCapabilitiesInFlight;

  layerCapabilitiesInFlight = readObservationWindows()
    .then((windows) => {
      const layers = [...windows.values()].map(buildCapability);
      cachedLayerCapabilities = {
        expiresAtMs: Date.now() + CAPABILITIES_CACHE_TTL_MS,
        layers,
      };
      return layers;
    })
    .finally(() => {
      layerCapabilitiesInFlight = null;
    });
  return layerCapabilitiesInFlight;
}

/**
 * What the slider may offer, and what day the server thinks it is.
 * One capability per geo.layers row; layers with no mappable observation report a
 * null earliestObservedDate rather than being omitted, so the UI can distinguish
 * "this layer exists but has no history" from "this layer does not exist".
 *
 * serverCurrentDate is stamped on every call, never cached with the layers: a payload held
 * across UTC midnight would otherwise keep reporting yesterday as today.
 */
export async function getSliderCapabilities(): Promise<ResolvedSliderCapabilities> {
  return {
    serverCurrentDate: serverCurrentDate(),
    layers: await readLayerCapabilities(),
  };
}

/** A collection that states why it is empty instead of just being empty. */
function emptyMetricCollection(
  availability: MetricAtDateAvailability,
  reason: string
): MetricAtDateCollection {
  return { type: "FeatureCollection", features: [], availability, reason };
}

/**
 * Availability for a date, mirroring layerAvailabilityAt in
 * src/stores/time-slider-store.ts. The client short-circuits with the same rules
 * before requesting; the server repeats them because it must never depend on the
 * client having done so, and because only the server knows UTC today.
 */
function resolveAvailability(
  capability: ResolvedSliderLayerCapability,
  date: string,
  variant: MetricAtDateInput["variant"],
  today: string
): MetricAtDateAvailability {
  if (capability.earliestObservedDate === null) return "not_yet_observed";
  if (date < capability.earliestObservedDate) return "not_yet_observed";
  if (date > today) {
    if (capability.temporalKind === "event") return "not_forecastable";
    if (capability.forecastHorizonDays === 0) return "not_forecastable";
    if (date > addUtcDays(today, capability.forecastHorizonDays)) return "beyond_horizon";
    if (
      variant === "observed" ||
      !capability.forecastVariants.includes(variant)
    ) {
      return "variant_unavailable";
    }
    return "published";
  }
  // Past or present: only the observed series exists. A forecast variant asked of a
  // past day is not an error, it is a series this warehouse does not carry.
  if (variant !== "observed") return "variant_unavailable";
  return "published";
}

/** Explains an unavailable answer in the same voice as describeAvailability. */
function explainUnavailability(
  availability: MetricAtDateAvailability,
  capability: ResolvedSliderLayerCapability,
  label: string,
  date: string
): string {
  switch (availability) {
    case "not_yet_observed":
      return capability.earliestObservedDate === null
        ? `${label} has no observations on record yet.`
        : `${label} has no continuously populated record before ${capability.earliestObservedDate}.`;
    case "not_forecastable":
      return `${label} is not forecast beyond today.`;
    case "beyond_horizon":
      return `${label} is not forecast as far ahead as ${date}.`;
    case "variant_unavailable":
      return `${label} publishes observations only; no forecast series is available.`;
    case "not_published":
      return `${label} has nothing published for ${date}.`;
    case "published":
      return "";
    case "request_failed":
      // Unreachable from here: resolveAvailability never returns it, because a server that can
      // answer at all knows which of the members above applies. The client mints it when the
      // request itself failed. Listed so the switch stays exhaustive rather than falling through.
      return `${label} could not be read for ${date}.`;
  }
}

type MetricRow = {
  geometry_id: string | null;
  geometry: string | null;
  median_value: number | string | null;
  observed_day: unknown;
  provenance_key: string | null;
  /** Day-wide totals, repeated on every row; present even when the page itself is empty. */
  candidate_count: number | string | null;
  unlinked_count: number | string | null;
};

/**
 * One layer's metric for one UTC day, as GeoJSON that explains its own emptiness.
 *
 * Values are read straight from the stored upstream payload: nothing is
 * interpolated, carried forward, smoothed or back-filled, so a day with no
 * observation comes back empty with a reason rather than with a neighbouring day's
 * number wearing today's date. Distinguishing "no data for this day" from "this is
 * broken" is the whole point of the availability/reason pair.
 */
export async function getMetricAtDate(
  input: MetricAtDateInput
): Promise<MetricAtDateCollection> {
  const { metric, date, variant } = input;

  if (!CALENDAR_DATE_PATTERN.test(date) || Number.isNaN(Date.parse(`${date}T00:00:00Z`))) {
    return emptyMetricCollection("not_published", `"${date}" is not a calendar date.`);
  }
  const area = input.bbox ? parseBbox(input.bbox) : null;
  const today = serverCurrentDate();

  if (metric === DROUGHT_METRIC_KEY) {
    return getDroughtMetricAtDate(date, variant, today, area);
  }

  const source = METRIC_SOURCES[metric];
  if (!source) {
    return emptyMetricCollection(
      "not_published",
      `No published source backs the metric "${metric}".`
    );
  }

  // The shared, memoized capability list -- not a per-request window scan. Recomputing it
  // here made every metric read the layer's whole history twice, once to range-check the
  // date and once for the day itself, and the client fans out several requests per scrub.
  const capabilities = await readLayerCapabilities();
  const capability = capabilities.find(
    (layer) => layer.layerName === source.layerName
  );
  if (!capability) {
    return emptyMetricCollection(
      "not_published",
      `${source.label} has no layer registered in the catalogue.`
    );
  }

  const availability = resolveAvailability(capability, date, variant, today);
  if (availability !== "published") {
    return emptyMetricCollection(
      availability,
      explainUnavailability(availability, capability, source.label, date)
    );
  }

  // Polygon layers (fire perimeters reach ~58,000 vertices) are generalized to what
  // the client can resolve; points are returned untouched. Simplifying generalizes a
  // boundary, it never moves a value.
  const tolerance = droughtSimplifyTolerance(area ? area[2] - area[0] : null);
  const geometrySql = sql`CASE
    WHEN g.geom_kind = 'point' THEN g.geom
    ELSE ST_SimplifyPreserveTopology(g.geom, ${tolerance})
  END`;

  // Three CTEs rather than one statement, so the day's totals survive an empty page.
  //
  // `page` INNER JOINs geo.geometry because MetricAtDateFeature.geometryId/geometry are
  // non-nullable -- a feature with no versioned place has no place to draw. But that join
  // silently DROPS rows, and orphans are the steady state, not an anomaly: the deployed
  // ingest path is still the TypeScript writer, which never sets geometry_id, so
  // geo.features accumulates unlinked rows until `agri-cli ingest-geometry-repair` claims
  // them (it now runs inside `ingest-all`, see ingest/commands.py). Measured on production
  // 2026-08-04, streamflow-cfs on that day had 4,314 water-gauges rows of which 1,617 were
  // unlinked, and this procedure answered `availability: "published", reason: null` -- a
  // partially-orphaned day presented as a complete one, with the DISTINCT ON ranking only
  // the linked subset so most sites showed a stale reading rather than the day's newest.
  //
  // `summary` is an ungrouped aggregate, so it always yields exactly one row, and the
  // LEFT JOIN ... ON true carries it onto every page row -- including the zero-row case,
  // where it produces one all-null page row that the geometry test below skips. That is
  // what lets an all-orphaned day say so instead of claiming nothing was observed.
  const rows = await db.execute<MetricRow>(sql`
    WITH candidate AS (
      SELECT f.id, f.geometry_id, f.properties
      FROM geo.features f
      JOIN geo.layers l ON l.id = f.layer_id
      WHERE l.name = ${source.layerName}
        AND f.status = 'published'
        AND ${OBSERVATION_DAY} = ${date}::date
        AND jsonb_typeof(f.properties->${source.valueKey}) = 'number'
        ${
          // Excluded in SQL, not after the fact, so LIMIT counts only real readings.
          source.missingValueSentinel === undefined
            ? sql``
            : sql`AND (f.properties->>${source.valueKey})::double precision
                    <> ${source.missingValueSentinel}::double precision`
        }
        ${
          area
            ? sql`AND f.geom && ST_MakeEnvelope(${area[0]}, ${area[1]}, ${area[2]}, ${area[3]}, 4326)`
            : sql``
        }
    ),
    page AS (
      SELECT DISTINCT ON (f.geometry_id)
        g.geometry_id::text AS geometry_id,
        ST_AsGeoJSON(${geometrySql}) AS geometry,
        (f.properties->>${source.valueKey})::double precision AS median_value,
        ${OBSERVATION_DAY} AS observed_day,
        COALESCE(f.properties->>'id', f.id::text) AS provenance_key
      FROM candidate f
      JOIN geo.geometry g ON g.geometry_id = f.geometry_id
      ORDER BY f.geometry_id, ${OBSERVATION_TIME} DESC
      LIMIT ${METRIC_AT_DATE_MAX_ROWS + 1}
    ),
    summary AS (
      SELECT COUNT(*)::bigint AS candidate_count,
             COUNT(*) FILTER (WHERE geometry_id IS NULL)::bigint AS unlinked_count
      FROM candidate
    )
    SELECT page.geometry_id,
           page.geometry,
           page.median_value,
           page.observed_day,
           page.provenance_key,
           summary.candidate_count,
           summary.unlinked_count
    FROM summary
    LEFT JOIN page ON true
  `);

  // One row beyond the cap distinguishes "exactly a full page" from "there is more",
  // so the truncation notice below is never claimed against a complete result. The
  // all-null carrier row of an empty page never reaches the cap, so it cannot trip this.
  const isTruncated = rows.length > METRIC_AT_DATE_MAX_ROWS;
  const candidateCount = toCount(rows[0]?.candidate_count ?? null);
  const unlinkedCount = toCount(rows[0]?.unlinked_count ?? null);
  const collected: GeoJSON.Feature[] = [];
  for (const metricRow of rows.slice(0, METRIC_AT_DATE_MAX_ROWS)) {
    const medianValue = finiteNumber(metricRow.median_value);
    if (!metricRow.geometry || metricRow.geometry_id === null || medianValue === null) continue;
    const properties: MetricAtDateProperties = {
      geometryId: metricRow.geometry_id,
      medianValue,
      // Observations carry no distribution band; only an ML series would.
      lowValue: null,
      highValue: null,
      valueKind: "observed",
      variant: "observed",
      issuedOn: toCalendarDate(metricRow.observed_day) ?? date,
      provenanceKey: metricRow.provenance_key ?? metricRow.geometry_id,
    };
    collected.push({
      type: "Feature",
      id: metricRow.geometry_id,
      geometry: JSON.parse(metricRow.geometry) as GeoJSON.Geometry,
      properties,
    });
  }

  // Never "recorded no observation" for a day that DID record observations and lost them
  // all to the geometry join: that sentence asserts something false about the warehouse.
  const unlinkedNotice =
    unlinkedCount === 0
      ? null
      : `${unlinkedCount} of ${candidateCount} observations on this date are not yet linked to a place and are not shown.`;

  if (collected.length === 0) {
    return emptyMetricCollection(
      "not_published",
      unlinkedNotice === null
        ? // Inside the observed window but nothing on this specific day: a real gap in the
          // record, which the slider must show as a gap rather than fill.
          `${source.label} recorded no observation on ${date}.`
        : `${source.label} has no drawable observation on ${date}. ${unlinkedNotice}`
    );
  }

  const notices = [
    isTruncated
      ? `Showing ${METRIC_AT_DATE_MAX_ROWS} of a larger result; zoom in to see the rest.`
      : null,
    unlinkedNotice,
  ].filter((notice): notice is string => notice !== null);

  return {
    type: "FeatureCollection",
    features: collected,
    availability: "published",
    reason: notices.length === 0 ? null : notices.join(" "),
  };
}

type DroughtMetricRow = {
  dm_category: number;
  valid_date: string;
  geometry: string | null;
};

/**
 * Drought as of a date: the release `resolveDroughtRelease` says covers it.
 *
 * The weekly carry-forward and its bound live in `resolveDroughtRelease`, shared with
 * `getPublishedDroughtClassification` so the slider metric and the map layer can never
 * disagree about which release a day means. `issuedOn` always reports the release's own date,
 * never the requested one, so a value is never dressed up as fresher than it is.
 *
 * geo.drought_areas has no geo.layers row and no geometry_id, so this metric is
 * absent from getSliderCapabilities. geometryId falls back to the release identity
 * (`usdm:<valid_date>:<category>`), matching the ingest natural key.
 */
async function getDroughtMetricAtDate(
  date: string,
  variant: MetricAtDateInput["variant"],
  today: string,
  area: [number, number, number, number] | null
): Promise<MetricAtDateCollection> {
  if (date > today) {
    return emptyMetricCollection(
      "not_forecastable",
      "Drought classification is not forecast beyond today."
    );
  }
  if (variant !== "observed") {
    return emptyMetricCollection(
      "variant_unavailable",
      "Drought classification publishes observations only; no forecast series is available."
    );
  }

  const resolution = await resolveDroughtRelease(date);
  if (resolution.kind === "unavailable") {
    return emptyMetricCollection(resolution.availability, resolution.reason);
  }
  const { asOfRelease, carryForwardDays } = resolution;

  const tolerance = droughtSimplifyTolerance(area ? area[2] - area[0] : null);
  const clipped = area
    ? sql`ST_CollectionExtract(
        ST_Intersection(
          d.geom,
          ST_MakeEnvelope(${area[0]}, ${area[1]}, ${area[2]}, ${area[3]}, 4326)
        ),
        3
      )`
    : sql`d.geom`;

  const rows = await db.execute<DroughtMetricRow>(sql`
    SELECT
      d.dm_category,
      d.valid_date,
      ST_AsGeoJSON(ST_SimplifyPreserveTopology(${clipped}, ${tolerance})) AS geometry
    FROM geo.drought_areas d
    WHERE d.valid_date = ${asOfRelease}
    ${
      area
        ? sql`AND d.geom && ST_MakeEnvelope(${area[0]}, ${area[1]}, ${area[2]}, ${area[3]}, 4326)`
        : sql``
    }
    ORDER BY d.dm_category
  `);

  const collected: GeoJSON.Feature[] = [];
  for (const row of rows) {
    if (!row.geometry) continue;
    const geometry = JSON.parse(row.geometry) as GeoJSON.Geometry;
    // An empty clip means this class does not reach the viewport at all.
    if (
      (geometry.type === "MultiPolygon" || geometry.type === "Polygon") &&
      geometry.coordinates.length === 0
    ) {
      continue;
    }
    const identity = `usdm:${row.valid_date}:${row.dm_category}`;
    const properties: MetricAtDateProperties = {
      geometryId: identity,
      medianValue: row.dm_category,
      lowValue: null,
      highValue: null,
      valueKind: "observed",
      variant: "observed",
      issuedOn: row.valid_date,
      provenanceKey: identity,
    };
    collected.push({ type: "Feature", id: identity, geometry, properties });
  }

  if (collected.length === 0) {
    return emptyMetricCollection(
      "not_published",
      `No drought class reaches this area in the release covering ${date}.`
    );
  }
  return {
    type: "FeatureCollection",
    features: collected,
    availability: "published",
    // A carried-forward release says so in the payload, not only in `issuedOn`: the client's
    // describeAvailability returns null for "published", so silence here reads as same-day.
    reason:
      carryForwardDays === 0
        ? null
        : `As of the ${asOfRelease} US Drought Monitor release, ${carryForwardDays} day${
            carryForwardDays === 1 ? "" : "s"
          } before ${date}.`,
  };
}
