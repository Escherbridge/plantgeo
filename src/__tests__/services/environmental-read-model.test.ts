import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Two seams into the warehouse, both stubbed so nothing here touches a real database:
 * `queryChain` is the drizzle query-builder chain (`getPublishedFireDetections`'s path),
 * `dbExecute` is the raw `sql` path the slider-capability and metric-at-date queries use.
 * vi.hoisted keeps both ahead of the vi.mock factories' hoist point.
 *
 * Every refactored reader now issues `db.select(...).from(layers)...limit(1)` (via the shared
 * `resolveCachedLayerId`) BEFORE its own `db.select(...).from(features)...limit(n)` -- both
 * calls share this one mocked chain. `lastFromTable` is how `.limit()` tells them apart:
 * keyed on the table `.from()` was last called with, not on call order, because two concurrent
 * readers (a `Promise.all` in the test body) can race their own resolver call ahead of the
 * other's. See resetQueryChain below.
 */
const queryChain = vi.hoisted(() => {
  const chain: {
    lastFromTable: unknown;
    featureRows: unknown[];
    select: ReturnType<typeof vi.fn>;
    from: ReturnType<typeof vi.fn>;
    innerJoin: ReturnType<typeof vi.fn>;
    where: ReturnType<typeof vi.fn>;
    orderBy: ReturnType<typeof vi.fn>;
    limit: ReturnType<typeof vi.fn>;
  } = {
    lastFromTable: undefined,
    featureRows: [],
    select: vi.fn((..._args: unknown[]) => chain),
    from: vi.fn((table: unknown) => {
      chain.lastFromTable = table;
      return chain;
    }),
    innerJoin: vi.fn((..._args: unknown[]) => chain),
    where: vi.fn((..._args: unknown[]) => chain),
    orderBy: vi.fn((..._args: unknown[]) => chain),
    limit: vi.fn(() => Promise.resolve([] as unknown[])),
  };
  return chain;
});

const dbExecute = vi.hoisted(() =>
  vi.fn((..._args: unknown[]) => Promise.resolve([] as unknown[]))
);

vi.mock("@/lib/server/db", () => ({
  db: {
    select: (...args: unknown[]) => queryChain.select(...args),
    execute: (...args: unknown[]) => dbExecute(...args),
  },
}));

// gte/lte are spied (pass-through to the real implementation) rather than stubbed, so a
// bbox's extra spatial predicates can be counted without parsing drizzle's internal SQL AST.
vi.mock("drizzle-orm", async (importOriginal) => {
  const actual = await importOriginal<typeof import("drizzle-orm")>();
  return {
    ...actual,
    gte: vi.fn(actual.gte),
    lte: vi.fn(actual.lte),
  };
});

import { gte, lte } from "drizzle-orm";
import {
  clearLayerIdCache,
  clearSliderCapabilitiesCache,
  getMetricAtDate,
  getPublishedDroughtClassification,
  getPublishedFireDetections,
  getPublishedStreamflowGauges,
  getPublishedVegetationIndex,
  getPublishedWeatherForBbox,
  getSliderCapabilities,
  resolveRequestedObservationDay,
  serverCurrentDate,
} from "@/lib/server/services/environmental-read-model";
import { layers } from "@/lib/server/db/schema";
import { layerRegistryEntries } from "@/lib/map/layer-registry";

/**
 * A plausible `geo.layers.id` for `resolveCachedLayerId`'s own `db.select` call to resolve to.
 * The exact value is inert -- every "main" query below is itself mocked (via `dbExecute` or via
 * `queryChain.featureRows`), so nothing downstream inspects this UUID's bytes.
 */
const FAKE_LAYER_ID = "11111111-1111-4111-8111-111111111111";

/**
 * Flattens a drizzle `sql` template back to its literal text, so a test can assert on the
 * SQL a function actually built. Only the static chunks are recovered; bound parameters are
 * placeholders, which is all these assertions need.
 */
function renderSqlText(value: unknown): string {
  if (value === null || typeof value !== "object") return "";
  const node = value as { queryChunks?: unknown[]; value?: unknown };
  if (Array.isArray(node.queryChunks)) {
    return node.queryChunks.map(renderSqlText).join("");
  }
  if (Array.isArray(node.value)) return node.value.join("");
  return "";
}

/**
 * Tells the FEATURE day-axis scan apart from the STREAM day-axis scan.
 *
 * `JOIN geo.features f` used to be the discriminator, and since drizzle/0029 it discriminates
 * nothing: BOTH axes now read `geo.v_observation_day_census` and neither names `geo.features`
 * at all. A selector looking for the absence of that string therefore matches the FIRST
 * statement -- the feature axis -- and every assertion written for the stream lane silently
 * moved onto the wrong statement. The two are told apart by the only thing that still differs
 * between them, which is the census predicate each one carries.
 */
const FEATURE_AXIS_PREDICATE = "surface_kind = 'feature'";
const STREAM_AXIS_PREDICATE = "surface_kind IN ('signal', 'polygon')";

function isFeatureAxisScan(statement: unknown): boolean {
  return renderSqlText(statement).includes(FEATURE_AXIS_PREDICATE);
}

function isStreamAxisScan(statement: unknown): boolean {
  return renderSqlText(statement).includes(STREAM_AXIS_PREDICATE);
}

/** One element of a drizzle `sql` template: literal text, or a value bound as a parameter. */
type SqlToken = { kind: "text"; text: string } | { kind: "param"; value: unknown };

/**
 * Flattens a drizzle `sql` template into its literal chunks AND its bound parameters, in
 * order. Literal text arrives as a StringChunk, whose `value` is a string[]; an interpolated
 * value sits in `queryChunks` as the bare primitive, because drizzle only wraps it in a Param
 * when the dialect builds the query. renderSqlText above discards parameters entirely, which
 * is exactly why a parameter with no cast after it is invisible to it -- keeping both is what
 * lets a test see what follows a placeholder.
 */
function flattenSql(value: unknown, tokens: SqlToken[] = []): SqlToken[] {
  if (value === undefined || value === null) return tokens;
  if (typeof value !== "object") {
    tokens.push({ kind: "param", value });
    return tokens;
  }
  const node = value as { queryChunks?: unknown[]; value?: unknown; encoder?: unknown };
  if (Array.isArray(node.queryChunks)) {
    for (const chunk of node.queryChunks) flattenSql(chunk, tokens);
  } else if (Array.isArray(node.value)) {
    tokens.push({ kind: "text", text: node.value.join("") });
  } else if ("encoder" in node) {
    // A value wrapped explicitly via sql.param().
    tokens.push({ kind: "param", value: node.value });
  }
  return tokens;
}

/**
 * Fails when a statement binds a fractional value that Postgres would have to type from its
 * surroundings.
 *
 * postgres-js sends every non-bigint JS number as an UNTYPED parameter (OID 0), so a fractional
 * value in a bare arithmetic context next to a bigint resolves as a bigint and throws
 * `invalid input syntax for type bigint` at runtime -- the exact production 500
 * getSliderCapabilities hit. Safe only with its own `::` cast, or inside a PostGIS call whose
 * signature already declares `double precision`.
 */
function expectNoBareFractionalParameter(statement: unknown): void {
  const tokens = flattenSql(statement);
  /** Literal SQL written before `index`; parameters contribute no text of their own. */
  const textBefore = (index: number) =>
    tokens
      .slice(0, index)
      .map((token) => (token.kind === "text" ? token.text : ""))
      .join("");

  const fractional = tokens.flatMap((token, index) =>
    token.kind === "param" &&
    typeof token.value === "number" &&
    !Number.isInteger(token.value)
      ? [{ value: token.value, before: textBefore(index), following: tokens[index + 1] }]
      : []
  );
  // Pins the assertion against passing vacuously: callers pass a fractional viewport.
  expect(fractional.length).toBeGreaterThan(0);

  const untyped = fractional.filter(({ before, following }) => {
    const next = following?.kind === "text" ? following.text.trimStart() : "";
    if (next.startsWith("::")) return false;
    return !/ST_(MakeEnvelope|SimplifyPreserveTopology)\([^()]*$/.test(before);
  });
  expect(untyped.map(({ value }) => value)).toEqual([]);
}

function resetQueryChain() {
  queryChain.lastFromTable = undefined;
  queryChain.featureRows = [];
  queryChain.select.mockReturnValue(queryChain);
  queryChain.from.mockImplementation((table: unknown) => {
    queryChain.lastFromTable = table;
    return queryChain;
  });
  queryChain.innerJoin.mockReturnValue(queryChain);
  queryChain.where.mockReturnValue(queryChain);
  queryChain.orderBy.mockReturnValue(queryChain);
  // A `.from(layers)` call is always `resolveCachedLayerId`'s own lookup (see the queryChain
  // doc comment above); anything else is a reader's real feature select, answered from
  // `queryChain.featureRows` -- set that field directly rather than re-mocking `.limit()`.
  queryChain.limit.mockImplementation(() =>
    Promise.resolve(
      queryChain.lastFromTable === layers ? [{ id: FAKE_LAYER_ID }] : queryChain.featureRows
    )
  );
}

/** Runs `body` under a fixed IANA zone so a local-clock bug surfaces as a diff. */
function withTimeZone<T>(timeZone: string, body: () => T): T {
  const previous = process.env.TZ;
  process.env.TZ = timeZone;
  try {
    return body();
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  resetQueryChain();
  dbExecute.mockResolvedValue([]);
  // The capability payload is memoized in module scope so a public procedure cannot be looped
  // into a whole-warehouse scan per request. Without this, the first test's fixture -- or the
  // default empty result -- would be served to every test after it.
  clearSliderCapabilitiesCache();
  // `resolveCachedLayerId` memoizes a HIT in module scope too (a miss is never cached), so one
  // test's cached `geo.layers.id` must not leak into the next test's call-count assertions.
  clearLayerIdCache();
});

describe("serverCurrentDate", () => {
  it("reads server UTC, never a browser/local clock reading a different calendar day", () => {
    // 2026-08-04T01:00:00Z is still 2026-08-03 in Los Angeles (UTC-7). A function that
    // reads the machine's local day instead of UTC would answer "2026-08-03" here.
    withTimeZone("America/Los_Angeles", () => {
      expect(serverCurrentDate(Date.parse("2026-08-04T01:00:00Z"))).toBe("2026-08-04");
    });
  });

  it("defaults to Date.now() rather than a stale or injected value", () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(Date.parse("2026-08-04T12:00:00Z"));
      expect(serverCurrentDate()).toBe("2026-08-04");
    } finally {
      vi.useRealTimers();
    }
  });

  it("threads through getSliderCapabilities regardless of local TZ", async () => {
    // Kiritimati is UTC+14: 2026-08-04T23:30:00Z is already 2026-08-05 there. A capability
    // resolver that formatted the local day instead of UTC would answer "2026-08-05".
    dbExecute.mockResolvedValueOnce([]);
    vi.useFakeTimers();
    try {
      vi.setSystemTime(Date.parse("2026-08-04T23:30:00Z"));
      const capabilities = await withTimeZone("Pacific/Kiritimati", () => getSliderCapabilities());
      expect(capabilities.serverCurrentDate).toBe("2026-08-04");
    } finally {
      vi.useRealTimers();
    }
  });
});

/** One observed day and how many observations landed on it. */
type ObservedDay = readonly [day: string, observationCount: number];

/** Mirrors OBSERVATION_CLUSTER_GAP_DAYS in environmental-read-model.ts. */
const GAP_THRESHOLD_DAYS = 21;
/** Mirrors OBSERVATION_DENSITY_FLOOR_FRACTION in environmental-read-model.ts. */
const DENSITY_FLOOR_FRACTION = 0.01;
/** Mirrors MAX_REPORTED_DAY_RANGES in environmental-read-model.ts. */
const MAX_REPORTED_DAY_RANGES = 800;

/** Adds whole days to a YYYY-MM-DD string in UTC. */
function addDays(day: string, dayCount: number): string {
  return new Date(Date.parse(`${day}T00:00:00Z`) + dayCount * 86_400_000).toISOString().slice(0, 10);
}

/**
 * Reimplements EVERY axis rule embedded in readObservationWindows' SQL -- continuity
 * clustering, the density floor over the newest cluster, then the coverage gaps and thin
 * ranges derived from the days that survive both -- so a fixture mirroring production's real
 * day/count distribution can be turned into the row shape the database is documented to hand
 * back, without a database.
 *
 * The gap and thin derivations must stay the SQL's twins, not merely plausible: gaps are read
 * off the step between adjacent axis days, and a thin range breaks on an unpublished day as
 * well as on a dense one, which is what keeps a thin range abutting a gap instead of
 * swallowing it.
 */
function summarizeObservedDays(sortedDays: readonly ObservedDay[]) {
  if (sortedDays.length === 0) {
    return {
      layer_name: "",
      dense_earliest_day: null,
      clustered_earliest_day: null,
      recorded_earliest_day: null,
      dense_latest_day: null,
      recorded_latest_day: null,
      dense_day_count: 0,
      clustered_day_count: 0,
      recorded_day_count: 0,
      density_floor: null,
      coverage_gaps: [] as Array<{ from: string; to: string }>,
      thin_ranges: [] as Array<{ from: string; to: string }>,
    };
  }
  let clusterIndex = 0;
  let previousMs: number | null = null;
  const clusterIndexes: number[] = [];
  for (const [day] of sortedDays) {
    const currentMs = Date.parse(`${day}T00:00:00Z`);
    const gapDays = previousMs === null ? null : (currentMs - previousMs) / 86_400_000;
    if (gapDays === null || gapDays > GAP_THRESHOLD_DAYS) clusterIndex += 1;
    clusterIndexes.push(clusterIndex);
    previousMs = currentMs;
  }
  const newestCluster = sortedDays.filter(
    (_day, index) => clusterIndexes[index] === clusterIndex
  );
  const peakCount = Math.max(...newestCluster.map(([, count]) => count));
  const densityFloor = Math.max(1, Math.ceil(peakCount * DENSITY_FLOOR_FRACTION));
  const denseDays = newestCluster.filter(([, count]) => count >= densityFloor);
  const axisStart = denseDays[0]?.[0] ?? null;
  const axis = axisStart === null ? [] : newestCluster.filter(([day]) => day >= axisStart);

  const coverageGaps: Array<{ from: string; to: string }> = [];
  for (let index = 1; index < axis.length; index += 1) {
    const previousDay = axis[index - 1][0];
    const currentDay = axis[index][0];
    if (addDays(previousDay, 1) !== currentDay) {
      coverageGaps.push({ from: addDays(previousDay, 1), to: addDays(currentDay, -1) });
    }
  }

  const thinRanges: Array<{ from: string; to: string }> = [];
  for (const [day, count] of axis) {
    if (count >= densityFloor) continue;
    const openRange = thinRanges[thinRanges.length - 1];
    if (openRange !== undefined && addDays(openRange.to, 1) === day) openRange.to = day;
    else thinRanges.push({ from: day, to: day });
  }

  return {
    layer_name: "",
    dense_earliest_day: axisStart,
    clustered_earliest_day: newestCluster[0]?.[0] ?? null,
    recorded_earliest_day: sortedDays[0][0],
    dense_latest_day: denseDays[denseDays.length - 1]?.[0] ?? null,
    recorded_latest_day: sortedDays[sortedDays.length - 1][0],
    dense_day_count: denseDays.length,
    clustered_day_count: newestCluster.length,
    recorded_day_count: sortedDays.length,
    density_floor: densityFloor,
    coverage_gaps: coverageGaps,
    thin_ranges: thinRanges,
  };
}

/** One layer's window row as readObservationWindows is documented to return it. */
function layerWindowRow(layerName: string, days: readonly ObservedDay[]) {
  return { ...summarizeObservedDays(days), layer_name: layerName };
}

describe("getSliderCapabilities -- the 36-year trap", () => {
  /**
   * Real production water-gauges distribution, measured 2026-08-04. Discontinued gauges carry
   * the timestamp of their final-ever reading, so the record starts 1990-09-30; the modern
   * stragglers from 2026-05-24 onward are the SAME artifact, 1-7 readings a day against
   * 10,911 on 2026-08-03. Continuity alone keeps them, which is why the density floor exists.
   */
  const REAL_WATER_GAUGE_DAYS: readonly ObservedDay[] = [
    ["1990-09-30", 2],
    ["1990-12-13", 1],
    ["1996-07-14", 1],
    ["2003-09-30", 2],
    ["2010-09-30", 1],
    ["2020-12-03", 2],
    ["2026-05-01", 2],
    ["2026-05-24", 1],
    ["2026-06-08", 1],
    ["2026-06-15", 1],
    ["2026-06-16", 2],
    ["2026-06-17", 2],
    ["2026-06-30", 2],
    ["2026-07-01", 7],
    ["2026-07-12", 1],
    ["2026-07-17", 2],
    ["2026-07-27", 1],
    ["2026-07-31", 3],
    ["2026-08-01", 2],
    ["2026-08-02", 2_236],
    ["2026-08-03", 10_911],
    ["2026-08-04", 2_697],
  ];

  /**
   * Real production fire-perimeters distribution, measured 2026-08-04. A genuine low-volume
   * layer: its busiest day mapped 19 perimeters and many real days mapped exactly one. Any
   * absolute reading threshold calibrated to water-gauges would erase all of it.
   */
  const REAL_FIRE_PERIMETER_DAYS: readonly ObservedDay[] = [
    ["2025-07-28", 1],
    ["2026-06-17", 1],
    ["2026-06-24", 1],
    ["2026-06-26", 1],
    ["2026-07-08", 2],
    ["2026-07-16", 3],
    ["2026-07-17", 13],
    ["2026-07-21", 6],
    ["2026-07-28", 2],
    ["2026-08-01", 2],
    ["2026-08-02", 7],
    ["2026-08-03", 19],
    ["2026-08-04", 8],
  ];

  it("excludes the 1990s stragglers, never reporting the bare-min() 1990-09-30 artifact", async () => {
    dbExecute.mockResolvedValueOnce([
      { ...summarizeObservedDays(REAL_WATER_GAUGE_DAYS), layer_name: "water-gauges" },
    ]);

    const capabilities = await getSliderCapabilities();
    const waterGauges = capabilities.layers.find((layer) => layer.layerName === "water-gauges");
    if (!waterGauges) throw new Error("expected a water-gauges capability in the response");

    expect(waterGauges.earliestObservedDate).not.toBe("1990-09-30");
    expect(waterGauges.earliestRecordedObservationDate).toBe("1990-09-30");
  });

  it("floors the 1-7 reading days the continuity rule keeps, and says which rule applied", async () => {
    dbExecute.mockResolvedValueOnce([
      { ...summarizeObservedDays(REAL_WATER_GAUGE_DAYS), layer_name: "water-gauges" },
    ]);

    const capabilities = await getSliderCapabilities();
    const waterGauges = capabilities.layers.find((layer) => layer.layerName === "water-gauges");
    if (!waterGauges) throw new Error("expected a water-gauges capability in the response");

    // Continuity alone stops at 2026-05-24 and leaves 12 days carrying 1-7 readings on the
    // axis; the floor (1% of the layer's own 10,911-reading peak = 110) starts it at the
    // first day that actually carries a national picture.
    expect(waterGauges.earliestContinuousObservationDate).toBe("2026-05-24");
    expect(waterGauges.earliestObservedDate).toBe("2026-08-02");
    expect(waterGauges.earliestObservedDateRule).toBe("density_floored");
    expect(waterGauges.minimumDailyObservationCount).toBe(110);
    expect(waterGauges.observedDayCount).toBe(3);
    expect(waterGauges.gapExcludedObservedDayCount).toBe(7);
    expect(waterGauges.densityExcludedObservedDayCount).toBe(12);
    expect(waterGauges.excludedObservedDayCount).toBe(19);
  });

  it("leaves a genuinely low-volume layer alone: the floor scales to each layer's own peak", async () => {
    dbExecute.mockResolvedValueOnce([
      { ...summarizeObservedDays(REAL_FIRE_PERIMETER_DAYS), layer_name: "fire-perimeters" },
    ]);

    const capabilities = await getSliderCapabilities();
    const perimeters = capabilities.layers.find((layer) => layer.layerName === "fire-perimeters");
    if (!perimeters) throw new Error("expected a fire-perimeters capability in the response");

    // 1% of a 19-perimeter peak floors at 1, so every genuine single-perimeter day survives.
    // Only the isolated 2025-07-28 row is dropped, and by continuity rather than by density.
    expect(perimeters.minimumDailyObservationCount).toBe(1);
    expect(perimeters.earliestObservedDate).toBe("2026-06-17");
    expect(perimeters.earliestObservedDateRule).toBe("gap_clustered");
    expect(perimeters.densityExcludedObservedDayCount).toBe(0);
    expect(perimeters.gapExcludedObservedDayCount).toBe(1);
    expect(perimeters.earliestRecordedObservationDate).toBe("2025-07-28");
  });

  it("reports full_history, with nothing excluded, when every observed day is one dense run", async () => {
    dbExecute.mockResolvedValueOnce([
      {
        layer_name: "fire-detections",
        dense_earliest_day: "2026-08-02",
        clustered_earliest_day: "2026-08-02",
        recorded_earliest_day: "2026-08-02",
        dense_latest_day: "2026-08-04",
        recorded_latest_day: "2026-08-04",
        dense_day_count: 3,
        clustered_day_count: 3,
        recorded_day_count: 3,
        density_floor: 63,
        coverage_gaps: [],
        thin_ranges: [],
      },
    ]);

    const capabilities = await getSliderCapabilities();
    const fireDetections = capabilities.layers.find((layer) => layer.layerName === "fire-detections");
    if (!fireDetections) throw new Error("expected a fire-detections capability in the response");

    expect(fireDetections.earliestObservedDateRule).toBe("full_history");
    expect(fireDetections.excludedObservedDayCount).toBe(0);
    expect(fireDetections.earliestObservedDate).toBe(fireDetections.earliestRecordedObservationDate);
  });

  it("reports no_observations, with a null earliest date, for a layer with nothing mappable", async () => {
    dbExecute.mockResolvedValueOnce([
      {
        layer_name: "sensors",
        dense_earliest_day: null,
        clustered_earliest_day: null,
        recorded_earliest_day: null,
        dense_latest_day: null,
        recorded_latest_day: null,
        dense_day_count: 0,
        clustered_day_count: 0,
        recorded_day_count: 0,
        density_floor: null,
        coverage_gaps: [],
        thin_ranges: [],
      },
    ]);

    const capabilities = await getSliderCapabilities();
    const sensors = capabilities.layers.find((layer) => layer.layerName === "sensors");
    if (!sensors) throw new Error("expected a sensors capability in the response");

    expect(sensors.earliestObservedDate).toBeNull();
    expect(sensors.earliestObservedDateRule).toBe("no_observations");
    expect(sensors.minimumDailyObservationCount).toBeNull();
  });

  it("computes the whole-warehouse scan once per TTL, not once per caller", async () => {
    dbExecute.mockResolvedValue([
      { ...summarizeObservedDays(REAL_WATER_GAUGE_DAYS), layer_name: "water-gauges" },
    ]);

    // A settled scrub fans out several requests at once; each one used to be a sequential
    // scan of geo.features on a public, unauthenticated procedure.
    const [first, second, third] = await Promise.all([
      getSliderCapabilities(),
      getSliderCapabilities(),
      getSliderCapabilities(),
    ]);
    await getSliderCapabilities();

    // Two scans, not two per caller: the geo.features window and the non-geo.features stream
    // window, each behind its own single-flight guard and its own TTL.
    expect(dbExecute).toHaveBeenCalledTimes(2);
    expect(second.layers).toEqual(first.layers);
    expect(third.layers).toEqual(first.layers);
  });
});

describe("the capability scan is typed for Postgres, not only for TypeScript", () => {
  /**
   * getSliderCapabilities answered 500 in production with
   * `invalid input syntax for type bigint: "0.01"`, so the client's capabilities query stayed
   * rejected, `capabilities` stayed undefined, and the time slider never mounted at all.
   *
   * postgres-js sends a plain JS number as an UNTYPED parameter -- its `inferType` answers
   * OID 0 for every number that is not a bigint -- so Postgres resolves the parameter from
   * the expression around it. A bare `MAX(observation_count) * $n` therefore resolves $n
   * against the `COUNT(*)` bigint on its left and tries to read "0.01" as an integer.
   *
   * These assert over the statement the function actually built, not over a stub of it: the
   * bug lives entirely in the parameter's SQL context, so a test that mocked the query away
   * could only ever agree with itself while production kept 500ing.
   */
  async function captureWindowScan() {
    dbExecute.mockResolvedValue([]);
    await getSliderCapabilities();
    return dbExecute.mock.calls[0]?.[0];
  }

  it("casts every fractional parameter it binds, so none can be resolved against a bigint", async () => {
    const tokens = flattenSql(await captureWindowScan());
    const fractional = tokens.flatMap((token, index) =>
      token.kind === "param" &&
      typeof token.value === "number" &&
      !Number.isInteger(token.value)
        ? [{ value: token.value, following: tokens[index + 1] }]
        : []
    );

    // Pins the scan against passing vacuously: the density floor is bound here, and if it
    // ever stops being a parameter the loop below would have nothing left to check.
    expect(fractional.map(({ value }) => value)).toContain(0.01);

    const uncast = fractional.filter(({ following }) => {
      const next = following?.kind === "text" ? following.text.trimStart() : "";
      return !next.startsWith("::");
    });
    expect(uncast.map(({ value }) => value)).toEqual([]);
  });

  it("multiplies the bigint observation count by a numeric, and floors back to a bigint", async () => {
    // renderSqlText drops the placeholder, so the parameter's own cast abuts the `*`.
    const statement = renderSqlText(await captureWindowScan());
    expect(statement).toMatch(/MAX\(observation_count\)\s*\*\s*::numeric/);
    expect(statement).toMatch(/\)::bigint AS density_floor/);
  });
});

describe("each layer reports its own axis: latest day, coverage gaps, thin ranges", () => {
  /** Enough observations that the 1% floor lands at 10, so a thin day can be written as < 10. */
  const DENSE = 1_000;
  /** Below the floor derived from DENSE: published, but not a day worth opening the map on. */
  const THIN = 5;

  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  /** Resolves one layer's capability from a day/count fixture, on a fixed server day. */
  async function capabilityFor(
    layerName: string,
    days: readonly ObservedDay[],
    today: string
  ) {
    vi.setSystemTime(Date.parse(`${today}T12:00:00Z`));
    dbExecute.mockResolvedValueOnce([layerWindowRow(layerName, days)]);
    const capabilities = await getSliderCapabilities();
    const layer = capabilities.layers.find((candidate) => candidate.layerName === layerName);
    if (!layer) throw new Error(`expected a ${layerName} capability in the response`);
    return layer;
  }

  it("reports a hole in the middle of the axis as one closed range", async () => {
    const layer = await capabilityFor(
      "vegetation",
      [
        ["2026-08-01", DENSE],
        ["2026-08-02", DENSE],
        ["2026-08-06", DENSE],
        ["2026-08-07", DENSE],
      ],
      "2026-08-07"
    );

    // Both ends inclusive: the hole is 08-03, 08-04 and 08-05, and the days either side of it
    // are published. A half-open range here would silently claim 08-06 has nothing.
    expect(layer.coverageGaps).toEqual([{ from: "2026-08-03", to: "2026-08-05" }]);
    expect(layer.thinRanges).toEqual([]);
    expect(layer.earliestObservedDate).toBe("2026-08-01");
    expect(layer.latestObservedDate).toBe("2026-08-07");
  });

  it("reports a hole at the head of the axis, starting the day after the axis begins", async () => {
    const layer = await capabilityFor(
      "vegetation",
      [
        ["2026-08-01", DENSE],
        ["2026-08-05", DENSE],
        ["2026-08-06", DENSE],
        ["2026-08-07", DENSE],
      ],
      "2026-08-07"
    );

    expect(layer.earliestObservedDate).toBe("2026-08-01");
    expect(layer.coverageGaps).toEqual([{ from: "2026-08-02", to: "2026-08-04" }]);
    // The axis cannot open on a hole: earliestObservedDate is itself an observed day, so the
    // earliest gap can only ever start the day after it.
    expect(layer.coverageGaps[0]?.from).toBe(addDays(layer.earliestObservedDate ?? "", 1));
  });

  it("reports a hole at the tail running to the server's today, not to the last day that landed", async () => {
    const layer = await capabilityFor(
      "vegetation",
      [
        ["2026-08-01", DENSE],
        ["2026-08-02", DENSE],
        ["2026-08-03", DENSE],
        ["2026-08-04", DENSE],
      ],
      "2026-08-09"
    );

    // A stalled ingest lane is the case this exists for: without the trailing range the axis
    // just ends at 08-04 and reads as "the record stops here", which is a different claim.
    expect(layer.coverageGaps).toEqual([{ from: "2026-08-05", to: "2026-08-09" }]);
    expect(layer.latestObservedDate).toBe("2026-08-04");
  });

  it("closes the trailing gap against a freshly stamped today, never against the cached rows", async () => {
    dbExecute.mockResolvedValue([
      layerWindowRow("vegetation", [
        ["2026-08-01", DENSE],
        ["2026-08-02", DENSE],
      ]),
    ]);

    vi.setSystemTime(Date.parse("2026-08-02T23:58:00Z"));
    const before = await getSliderCapabilities();
    // Three minutes later it is the next UTC day, and the layer list is still memoized.
    vi.setSystemTime(Date.parse("2026-08-03T00:01:00Z"));
    const after = await getSliderCapabilities();

    // One geo.features window scan and one stream window scan, both still memoized.
    expect(dbExecute).toHaveBeenCalledTimes(2);
    expect(before.layers[0]?.coverageGaps).toEqual([]);
    expect(after.layers[0]?.coverageGaps).toEqual([{ from: "2026-08-03", to: "2026-08-03" }]);
  });

  it("reports no gaps at all for an unbroken axis that reaches today", async () => {
    const layer = await capabilityFor(
      "vegetation",
      [
        ["2026-08-01", DENSE],
        ["2026-08-02", DENSE],
        ["2026-08-03", DENSE],
        ["2026-08-04", DENSE],
        ["2026-08-05", DENSE],
      ],
      "2026-08-05"
    );

    expect(layer.coverageGaps).toEqual([]);
    expect(layer.thinRanges).toEqual([]);
    expect(layer.coverageGapsTruncated).toBe(false);
  });

  it("fabricates no range at all for a layer with no observations", async () => {
    const layer = await capabilityFor("sensors", [], "2026-08-09");

    // The tempting bug is a gap from nothing to today, which would assert a hole in a record
    // that was never opened. A layer with no axis has nothing to say about coverage.
    expect(layer.latestObservedDate).toBeNull();
    expect(layer.earliestObservedDate).toBeNull();
    expect(layer.coverageGaps).toEqual([]);
    expect(layer.thinRanges).toEqual([]);
  });

  it("keeps two gaps separated by a single published day apart, never merging across it", async () => {
    const layer = await capabilityFor(
      "vegetation",
      [
        ["2026-08-01", DENSE],
        ["2026-08-03", DENSE],
        ["2026-08-05", DENSE],
      ],
      "2026-08-05"
    );

    expect(layer.coverageGaps).toEqual([
      { from: "2026-08-02", to: "2026-08-02" },
      { from: "2026-08-04", to: "2026-08-04" },
    ]);
    // Merging these into 08-02..08-04 would report a day the warehouse published as a hole.
    for (const gap of layer.coverageGaps) {
      expect(gap.from <= "2026-08-03" && "2026-08-03" <= gap.to).toBe(false);
    }
  });

  it("lets a thin range abut a gap rather than swallow it", async () => {
    const layer = await capabilityFor(
      "water-gauges",
      [
        ["2026-08-01", DENSE],
        ["2026-08-02", THIN],
        ["2026-08-03", THIN],
        ["2026-08-06", THIN],
        ["2026-08-07", DENSE],
      ],
      "2026-08-07"
    );

    // 08-02 and 08-03 are consecutive thin days and collapse; 08-06 is thin too but an
    // unpublished stretch sits between them, so it opens its own range. A thin run that ran
    // 08-02..08-06 would overlap the gap and describe 08-04 as both published and absent.
    expect(layer.thinRanges).toEqual([
      { from: "2026-08-02", to: "2026-08-03" },
      { from: "2026-08-06", to: "2026-08-06" },
    ]);
    expect(layer.coverageGaps).toEqual([{ from: "2026-08-04", to: "2026-08-05" }]);
    expect(addDays(layer.thinRanges[0].to, 1)).toBe(layer.coverageGaps[0].from);
    expect(addDays(layer.coverageGaps[0].to, 1)).toBe(layer.thinRanges[1].from);
  });

  it("returns both lists sorted, and never overlapping each other", async () => {
    const layer = await capabilityFor(
      "water-gauges",
      [
        ["2026-08-01", DENSE],
        ["2026-08-02", THIN],
        ["2026-08-03", THIN],
        ["2026-08-06", THIN],
        ["2026-08-07", DENSE],
      ],
      "2026-08-10"
    );

    for (const ranges of [layer.coverageGaps, layer.thinRanges]) {
      expect(ranges.map((range) => range.from)).toEqual(
        [...ranges].sort((left, right) => (left.from < right.from ? -1 : 1)).map((range) => range.from)
      );
      for (const range of ranges) expect(range.from <= range.to).toBe(true);
    }

    const merged = [...layer.coverageGaps, ...layer.thinRanges].sort((left, right) =>
      left.from < right.from ? -1 : 1
    );
    for (let index = 1; index < merged.length; index += 1) {
      expect(merged[index - 1].to < merged[index].from).toBe(true);
    }
    expect(merged).toHaveLength(4);
  });

  it("opens on the newest day at the axis floor, never on a still-filling live edge", async () => {
    const layer = await capabilityFor(
      "water-gauges",
      [
        ["2026-08-01", DENSE],
        ["2026-08-02", DENSE],
        ["2026-08-03", 3],
      ],
      "2026-08-03"
    );

    // 08-03 is published and stays on the axis; it is simply not the day to open on, because a
    // 3-reading national map looks exactly like an outage. It is marked, not hidden.
    expect(layer.latestObservedDate).toBe("2026-08-02");
    expect(layer.latestRecordedObservationDate).toBe("2026-08-03");
    expect(layer.thinRanges).toEqual([{ from: "2026-08-03", to: "2026-08-03" }]);
    // And it is a published day, so it is not also reported as a hole.
    expect(layer.coverageGaps).toEqual([]);
  });

  it("calls a day thin by the same floor the axis start used, not by a second definition", async () => {
    const layer = await capabilityFor(
      "water-gauges",
      [
        ["2026-08-01", DENSE],
        ["2026-08-02", 9],
        ["2026-08-03", 10],
        ["2026-08-04", DENSE],
      ],
      "2026-08-04"
    );

    // Floor is 1% of the layer's own 1,000-observation peak = 10, and it is inclusive at both
    // the axis start and here: 10 anchors, 9 does not.
    expect(layer.minimumDailyObservationCount).toBe(10);
    expect(layer.thinRanges).toEqual([{ from: "2026-08-02", to: "2026-08-02" }]);
  });

  it("keeps the newest ranges and says so when it dropped older ones", async () => {
    // One published day in two, for ten more days than the report carries: every hole is a
    // single day, so the fixture emits MAX_REPORTED_DAY_RANGES + 10 of them.
    const publishedDayCount = MAX_REPORTED_DAY_RANGES + 11;
    const alternatingDays: ObservedDay[] = Array.from(
      { length: publishedDayCount },
      (_value, index) => [addDays("2020-01-01", index * 2), DENSE]
    );
    const lastPublishedDay = alternatingDays[alternatingDays.length - 1][0];
    const layer = await capabilityFor("vegetation", alternatingDays, lastPublishedDay);

    expect(layer.coverageGaps).toHaveLength(MAX_REPORTED_DAY_RANGES);
    expect(layer.coverageGapsTruncated).toBe(true);
    // The right-hand edge is what a scrubbing user is looking at, so that is what survives.
    const newestGap = layer.coverageGaps[layer.coverageGaps.length - 1];
    expect(newestGap.from).toBe(addDays(lastPublishedDay, -1));
    expect(newestGap.to).toBe(addDays(lastPublishedDay, -1));
    // And the day the surviving list stops describing is reported, not merely the fact that
    // it stops: below this the axis draws clean while a dropped hole hides under it.
    expect(layer.describedFromDay).toBe(layer.coverageGaps[0].from);
  });

  it("accepts the jsonb array as text and drops an entry it cannot read as two calendar days", async () => {
    vi.setSystemTime(Date.parse("2026-08-04T12:00:00Z"));
    dbExecute.mockResolvedValueOnce([
      {
        ...layerWindowRow("vegetation", [
          ["2026-08-01", DENSE],
          ["2026-08-04", DENSE],
        ]),
        // postgres-js parses jsonb for us today; a driver that handed back the text instead
        // must not silently turn the whole axis report into an empty one.
        coverage_gaps: '[{"from":"2026-08-02","to":"2026-08-03"}]',
        thin_ranges: [
          { from: "not-a-day", to: "2026-08-02" },
          { from: "2026-08-03", to: "2026-08-02" },
          { from: "2026-08-02", to: "2026-08-02" },
        ],
      },
    ]);

    const layer = (await getSliderCapabilities()).layers[0];
    expect(layer.coverageGaps).toEqual([{ from: "2026-08-02", to: "2026-08-03" }]);
    // A malformed range is dropped rather than repaired: an invented endpoint would put a
    // marking on the axis the warehouse never asked for.
    expect(layer.thinRanges).toEqual([{ from: "2026-08-02", to: "2026-08-02" }]);
  });
});

/** `count` one-day ranges, every other day from `startDay`; the shape a pathological lane has. */
function alternatingDayRanges(count: number, startDay: string) {
  return Array.from({ length: count }, (_unused, index) => {
    const day = addDays(startDay, index * 2);
    return { from: day, to: day };
  });
}

/** A window row with the range lists written directly, so a cap can be driven past its limit. */
function windowRowWithRanges(fields: {
  layerName: string;
  earliestDay: string;
  latestDay: string;
  coverageGaps?: Array<{ from: string; to: string }>;
  thinRanges?: Array<{ from: string; to: string }>;
}) {
  return {
    layer_name: fields.layerName,
    dense_earliest_day: fields.earliestDay,
    clustered_earliest_day: fields.earliestDay,
    recorded_earliest_day: fields.earliestDay,
    dense_latest_day: fields.latestDay,
    recorded_latest_day: fields.latestDay,
    dense_day_count: 2,
    clustered_day_count: 2,
    recorded_day_count: 2,
    density_floor: 10,
    coverage_gaps: fields.coverageGaps ?? [],
    thin_ranges: fields.thinRanges ?? [],
  };
}

describe("a truncated range list says which day it stopped describing, not merely that it did", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  /** Resolves one capability with today pinned to `latestDay`, so no trailing gap is appended. */
  async function capabilityFrom(row: ReturnType<typeof windowRowWithRanges>) {
    vi.setSystemTime(Date.parse(`${row.recorded_latest_day}T12:00:00Z`));
    dbExecute.mockResolvedValueOnce([row]);
    const layer = (await getSliderCapabilities()).layers.find(
      (candidate) => candidate.layerName === row.layer_name
    );
    if (!layer) throw new Error(`expected a ${row.layer_name} capability in the response`);
    return layer;
  }

  it("reports a describedFromDay that actually bounds the ranges it kept", async () => {
    const gapCount = MAX_REPORTED_DAY_RANGES + 40;
    const gaps = alternatingDayRanges(gapCount, "2022-01-02");
    const layer = await capabilityFrom(
      windowRowWithRanges({
        layerName: "vegetation",
        earliestDay: "2022-01-01",
        latestDay: addDays(gaps[gapCount - 1].to, 1),
        coverageGaps: gaps,
      })
    );

    expect(layer.coverageGapsTruncated).toBe(true);
    expect(layer.coverageGaps).toHaveLength(MAX_REPORTED_DAY_RANGES);
    // The boundary IS the oldest surviving range's own start day, and every kept range is at
    // or after it -- so a consumer that refuses to read the lists below the boundary refuses
    // exactly the region where a dropped gap could be hiding.
    expect(layer.describedFromDay).toBe(layer.coverageGaps[0].from);
    expect(layer.coverageGaps.filter((gap) => gap.from < layer.describedFromDay!)).toEqual([]);
    // ...and the undescribed region is real: the axis starts well before the boundary, which
    // is what the old boolean flag could never say.
    expect(layer.earliestObservedDate! < layer.describedFromDay!).toBe(true);
    expect(layer.coverageGaps.some((gap) => gap.from === gaps[0].from)).toBe(false);
  });

  it("reports a null boundary for a layer whose lists describe its whole axis", async () => {
    const layer = await capabilityFrom(
      windowRowWithRanges({
        layerName: "vegetation",
        earliestDay: "2026-08-01",
        latestDay: "2026-08-09",
        coverageGaps: [{ from: "2026-08-03", to: "2026-08-04" }],
        thinRanges: [{ from: "2026-08-06", to: "2026-08-06" }],
      })
    );

    expect(layer.coverageGapsTruncated).toBe(false);
    expect(layer.thinRangesTruncated).toBe(false);
    expect(layer.describedFromDay).toBeNull();
  });

  it("takes the stricter of the two lists' boundaries, because a day must be described by both", async () => {
    // Thin ranges are truncated far more recently than coverage gaps here. A day between the
    // two boundaries is described by the gap list and NOT by the thin list, so it is not
    // described: reporting the older boundary would license reading the thin list there.
    const layer = await capabilityFrom(
      windowRowWithRanges({
        layerName: "vegetation",
        earliestDay: "2022-01-01",
        latestDay: "2030-12-31",
        coverageGaps: alternatingDayRanges(MAX_REPORTED_DAY_RANGES + 1, "2022-01-02"),
        thinRanges: alternatingDayRanges(MAX_REPORTED_DAY_RANGES + 1, "2026-01-02"),
      })
    );

    expect(layer.coverageGapsTruncated).toBe(true);
    expect(layer.thinRangesTruncated).toBe(true);
    expect(layer.describedFromDay).toBe(layer.thinRanges[0].from);
    expect(layer.describedFromDay! > layer.coverageGaps[0].from).toBe(true);
  });

  it("never reports a boundary the caller did not compute, whichever path built the row", async () => {
    // The flag used to be hardcoded false in buildCapability and only set truthfully inside
    // closeCoverageGapsAtLiveEdge, so any caller not going through getSliderCapabilities was
    // told nothing had been dropped. Both fields are now decided in one place.
    const gaps = alternatingDayRanges(MAX_REPORTED_DAY_RANGES + 5, "2022-01-02");
    const lastObservedDay = addDays(gaps[gaps.length - 1].to, 1);
    dbExecute.mockResolvedValueOnce([
      windowRowWithRanges({
        layerName: "vegetation",
        earliestDay: "2022-01-01",
        latestDay: lastObservedDay,
        coverageGaps: gaps,
      }),
    ]);
    // Ten days after the last observation, so the live-edge gap is appended on top of a list
    // that was ALREADY at the cap.
    vi.setSystemTime(Date.parse(`${addDays(lastObservedDay, 10)}T12:00:00Z`));

    const layer = (await getSliderCapabilities()).layers[0];
    expect(layer.coverageGaps).toHaveLength(MAX_REPORTED_DAY_RANGES);
    expect(layer.coverageGapsTruncated).toBe(true);
    // The trailing gap survives -- it is the newest hole and the one worth seeing -- and the
    // boundary moved forward by the range that fell off the other end to make room for it.
    expect(layer.coverageGaps[layer.coverageGaps.length - 1]).toEqual({
      from: addDays(lastObservedDay, 1),
      to: addDays(lastObservedDay, 10),
    });
    expect(layer.describedFromDay).toBe(layer.coverageGaps[0].from);
    expect(layer.describedFromDay! > gaps[4].from).toBe(true);
  });
});

describe("the streams that are not geo.features layers get an axis of their own", () => {
  /** The fixtures below publish through 2026-08-03, so today is pinned there: a live-edge
   * gap is closeCoverageGapsAtLiveEdge's own subject and has its own tests. */
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(Date.parse("2026-08-03T12:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  /** Routes each scan to its own fixture: one mock serves both statements. */
  function respondWith(fixtures: { features?: unknown[]; streams?: unknown[] }) {
    dbExecute.mockImplementation((...args: unknown[]) =>
      Promise.resolve(isFeatureAxisScan(args[0]) ? (fixtures.features ?? []) : (fixtures.streams ?? []))
    );
  }

  // Four named streams plus one per NASA POWER signal. The lane published a single
  // `climate-field` stream for all nine until 2026-08-10, and that one axis was computed over
  // every signal_name unioned -- so the soil-wetness pilot was handed the full-lattice air
  // temperature field's earliest day, latest day and gap list. Spelling the nine out here is
  // what makes this case fail if they are ever collapsed back into one.
  const CLIMATE_STREAM_NAMES = [
    "climate-field-air-temperature",
    "climate-field-dew-point",
    "climate-field-precipitation",
    "climate-field-relative-humidity",
    "climate-field-shortwave-radiation",
    "climate-field-wind-speed",
    "climate-field-soil-wetness-surface",
    "climate-field-soil-wetness-root-zone",
    "climate-field-soil-wetness-profile",
  ];

  const STREAM_NAMES = [
    "drought-areas",
    "soil-field-moisture",
    "soil-field-temperature",
    "soil-field-vpd",
    ...CLIMATE_STREAM_NAMES,
  ];

  // The 11 `geo.layers` rows seeded by drizzle/0001, 0011, 0013 and 0017 -- see
  // pre-aggregation-catalogue.test.ts, which cross-checks this exact list against those
  // migrations' own INSERT statements. Hand-spelled here too, independently, rather than
  // imported: this describe already treats "hand-spell it a second time" as the anti-drift
  // rule for the stream half of the catalogue, and the same reasoning applies to the feature
  // half now that a registry entry can silently point at a name neither side actually serves.
  const FEATURE_LAYER_NAMES = [
    "fire-perimeters",
    "fire-detections",
    "water-gauges",
    "weather-observations",
    "evacuation-zones",
    "sensors",
    "vegetation",
    "interventions",
    "burn-severity",
    "soil-survey",
    "watersheds",
  ];

  // warehouseLayerName values that resolve OUTSIDE the day-axis catalogue by design: a
  // declared snapshot capability (SNAPSHOT_SURFACE_LAYER_NAMES in src/types/time-slider.ts),
  // not a dropped join. The only member today is strategy-recommendations -- see that
  // constant's doc and docs/layer-lane-standard.md §9.1. Hand-spelled, not imported, for the
  // same reason STREAM_NAMES is: importing the constant under test would let a registry entry
  // and its declaration drift together and still pass.
  const EXPECTED_SNAPSHOT_DECLARATIONS = ["strategy-recommendations"];

  // Toggles whose warehouseLayerName is null BY DESIGN, not because a capability is withheld
  // (permanentlyUnavailableReason covers that separately below) -- demand-heatmap is served by
  // a live k-anonymity-floored aggregate (src/lib/server/services/community-activity.ts) with
  // no per-day history to scrub. Anything else with a null warehouseLayerName and no
  // permanentlyUnavailableReason is the soil-survey bug class: a layer that silently lost its
  // catalogue entry rather than one that was deliberately never given an axis.
  const EXPECTED_AXIS_LESS_BY_DESIGN_TOGGLE_IDS = ["demand-heatmap"];

  it("publishes a capability for drought, the three soil measures and the climate field", async () => {
    respondWith({
      features: [layerWindowRow("vegetation", [["2026-08-03", 1_000]])],
      streams: STREAM_NAMES.map((layerName) =>
        layerWindowRow(layerName, [
          ["2026-08-01", 1_568],
          ["2026-08-02", 1_568],
          ["2026-08-03", 1_568],
        ])
      ),
    });

    const capabilities = await getSliderCapabilities();
    const byName = new Map(capabilities.layers.map((layer) => [layer.layerName, layer]));

    // A healthy scan answers inside the cold-start bound, so the list is COMPLETE and says so.
    // The flag has to be false here or the client holds every stream slider in the outage state
    // while the real axes sit in the same payload.
    expect(capabilities.streamsUnavailable).toBe(false);

    for (const layerName of STREAM_NAMES) {
      const stream = byName.get(layerName);
      if (!stream) throw new Error(`expected a ${layerName} capability in the response`);
      // A real axis, not a placeholder: the same four fields every geo.features layer reports.
      expect({
        layerName,
        earliest: stream.earliestObservedDate,
        latest: stream.latestObservedDate,
        gaps: stream.coverageGaps,
        thin: stream.thinRanges,
        kind: stream.temporalKind,
      }).toEqual({
        layerName,
        earliest: "2026-08-01",
        latest: "2026-08-03",
        gaps: [],
        thin: [],
        // `daily_series`, never `snapshot`: a snapshot defines no axis, so sliderDomain would
        // refuse it and these five would be pinned to today all over again.
        kind: "daily_series",
      });
    }
  });

  it("reads the pre-aggregated census for the signal and polygon planes, and no feature at all", async () => {
    // WHAT CHANGED AND WHY THIS STILL PINS THE SAME THING. Until drizzle/0029 this statement
    // grouped geo.drought_areas, geo.soil_field_observation and geo.climate_field_observation
    // in-line -- ~17M accepted rows on a publicProcedure, which returned a Cloudflare 524 on
    // 2026-08-15 and unmounted every slider at once. Those three relations are now read once
    // per refresh inside geo.mv_drought_observation_day and geo.mv_signal_observation_day, and
    // that the matviews read exactly those governed relations (not the base tables) is asserted
    // against the DDL by src/__tests__/services/pre-aggregation-catalogue.test.ts. What this
    // case still owns is the half no DDL can state: that the stream lane reads the SIGNAL and
    // POLYGON planes of the census and never the feature plane, and that it runs the identical
    // gap/thin pipeline as the feature lane -- so a gap here means what a gap there means.
    respondWith({});
    await getSliderCapabilities();

    const streamScan = dbExecute.mock.calls
      .map(([statement]) => renderSqlText(statement))
      .find((statement) => statement.includes(STREAM_AXIS_PREDICATE));
    if (streamScan === undefined) throw new Error("expected a stream window scan");

    expect(streamScan).toContain("geo.v_observation_day_census");
    expect(streamScan).not.toContain(FEATURE_AXIS_PREDICATE);
    expect(streamScan).not.toContain("JOIN geo.features f");
    // The same pipeline as the feature lane, so a gap here means what a gap there means.
    expect(streamScan).toContain("dense_earliest_day");
    expect(streamScan).toContain("r.observation_count < d.density_floor");
  });

  it("catalogues every climate stream it observes, so none is dropped by the catalogue join", async () => {
    // The catalogue is the OUTER relation of the LEFT JOIN, so a stream the observation subquery
    // emits but the catalogue omits vanishes silently: the layer paints tiles (the field read has
    // its own 30-day backward window) while reporting no history, and the slider never mounts.
    // That is precisely what shipped when the one-stream -> nine-stream split missed this call site.
    respondWith({});
    await getSliderCapabilities();

    const statement = dbExecute.mock.calls.map(([call]) => call).find(isStreamAxisScan);
    if (statement === undefined) throw new Error("expected a stream window scan");

    // The stream names are BOUND PARAMETERS, so renderSqlText cannot see them -- flattenSql keeps
    // both halves. Params are collected only up to `AS stream(name)`, which ends the catalogue's
    // VALUES list; the observation subquery below binds the same nine names in its own VALUES, so
    // an unscoped search would pass against the very bug this pins.
    const catalogueParams: string[] = [];
    for (const token of flattenSql(statement)) {
      if (token.kind === "text" && token.text.includes("AS stream(name)")) break;
      if (token.kind === "param" && typeof token.value === "string") catalogueParams.push(token.value);
    }

    // Asserted against the hand-spelled list above, deliberately NOT against
    // CLIMATE_FIELD_SIGNAL_IDS: the production catalogue is built from that constant, so importing
    // it here would let both sides drift together and still pass.
    for (const streamName of CLIMATE_STREAM_NAMES) {
      expect(catalogueParams).toContain(streamName);
    }
  });

  it("resolves every LAYER_REGISTRY toggle to a catalogue entry, a declared snapshot, or a documented null", () => {
    // §0 of the pre-aggregation-layer design: every one of LAYER_REGISTRY's toggles must
    // resolve to EITHER a catalogue entry (a geo.layers row or a slider stream) OR an
    // explicit, tested snapshot/no-axis declaration. Zero silent drops. This is the test that
    // makes that a checked invariant instead of a claim: the conformance audit that motivated
    // this slice found it already violated twice --
    //   - strategy-recommendations' warehouseLayerName matched neither list (fixed 2026-08-15
    //     by declaring it a SNAPSHOT_SURFACE_LAYER_NAMES entry instead);
    //   - soil-survey's warehouseLayerName was null with no permanentlyUnavailableReason, even
    //     though 0013_soil_survey_persistence.sql gave it a real geo.layers row on 2026-08-05
    //     (fixed 2026-08-15 by pointing it at that row).
    const catalogueNames = new Set([...FEATURE_LAYER_NAMES, ...STREAM_NAMES]);
    expect(catalogueNames.size).toBe(24);

    const violations = layerRegistryEntries()
      .filter((entry) => {
        if (entry.warehouseLayerName === null) {
          const declaredAxisLess = EXPECTED_AXIS_LESS_BY_DESIGN_TOGGLE_IDS.includes(entry.toggleId);
          const withheld = (entry.permanentlyUnavailableReason ?? "").length > 0;
          return !(declaredAxisLess || withheld);
        }
        return !(
          catalogueNames.has(entry.warehouseLayerName) ||
          EXPECTED_SNAPSHOT_DECLARATIONS.includes(entry.warehouseLayerName)
        );
      })
      .map((entry) => `${entry.toggleId} -> ${entry.warehouseLayerName ?? "null"}`);

    expect(violations).toEqual([]);
  });

  it("dates a drought day by the release that covers it, not by the Tuesday it was valid on", () => {
    // USDM publishes weekly and a release stands until the next supersedes it. Feeding the
    // valid dates raw would report six days in seven as unpublished -- an observed absence
    // for days the record describes perfectly well.
    //
    // ASSERTED AGAINST THE DDL, not against a rendered statement, because that is where the
    // expansion moved: `readStreamObservationWindows` now reads a census that already carries
    // one row per COVERED day, so the LEAD/generate_series it used to build per request is in
    // geo.mv_drought_observation_day's defining query. Reading the migration file keeps the
    // bound under test instead of retiring the case along with the SQL that used to carry it.
    const ddl = readFileSync(
      join(process.cwd(), "drizzle", "0029_pre_aggregation_layer.sql"),
      "utf8"
    );
    const start = ddl.indexOf("CREATE MATERIALIZED VIEW IF NOT EXISTS geo.mv_drought_observation_day");
    expect(start).toBeGreaterThanOrEqual(0);
    const block = ddl.slice(start, ddl.indexOf("--> statement-breakpoint", start));

    expect(block).toContain("generate_series");
    expect(block).toContain("LEAD(d.valid_date::date)");
    // Bounded exactly as resolveDroughtRelease bounds it: the next release ends this one's
    // coverage, and the newest release may never carry forward past today.
    expect(block).toContain("release.next_valid_date - 1");
    expect(block).toContain("(now() AT TIME ZONE 'UTC')::date");
  });

  it("omits the streams when their scan fails, rather than reporting them as empty records", async () => {
    // A capability with a null earliestObservedDate makes the client say the stream "has no
    // observations this far back" -- a claim about the warehouse manufactured out of a failed
    // query. No capability makes no claim, which is the state these streams were already in.
    dbExecute.mockImplementation((...args: unknown[]) =>
      isFeatureAxisScan(args[0])
        ? Promise.resolve([layerWindowRow("vegetation", [["2026-08-03", 1_000]])])
        : Promise.reject(new Error("relation \"geo.v_observation_day_census\" does not exist"))
    );

    const capabilities = await getSliderCapabilities();

    expect(capabilities.layers.map((layer) => layer.layerName)).toEqual(["vegetation"]);
    // ...and SAYS the list is short. Omission alone is what took the sliders down on
    // 2026-08-15: the client cannot tell an omitted stream from one with no history, so it
    // read thirteen live layers as dateless and unmounted every one of their controls. The
    // omission is still the right payload; the flag is what makes it legible.
    expect(capabilities.streamsUnavailable).toBe(true);
    // Not cached, so the next call retries rather than serving the failure for a whole TTL.
    await getSliderCapabilities();
    const streamScans = dbExecute.mock.calls.filter(([statement]) => isStreamAxisScan(statement));
    expect(streamScans).toHaveLength(2);
  });

  it("never publishes two capabilities under one name", async () => {
    // findLayerCapability resolves a name to the first match, so a stream sharing a
    // geo.layers name would make which axis a layer draws depend on array order.
    respondWith({
      features: [layerWindowRow("vegetation", [["2026-08-03", 1_000]])],
      streams: [
        layerWindowRow("vegetation", [["2020-01-01", 5]]),
        layerWindowRow("drought-areas", [["2026-08-03", 5]]),
      ],
    });

    const capabilities = await getSliderCapabilities();
    const names = capabilities.layers.map((layer) => layer.layerName);

    expect(names).toEqual([...new Set(names)]);
    // The geo.layers row wins: it is the one the rest of the read model resolves against.
    expect(
      capabilities.layers.find((layer) => layer.layerName === "vegetation")?.earliestObservedDate
    ).toBe("2026-08-03");
  });

  it("casts the fractional density floor it binds, exactly as the feature scan does", async () => {
    // The stream lane runs the same pipeline, so it binds the same 0.01 -- and postgres-js
    // would resolve it against the bigint on its left and 500 the whole capabilities call.
    respondWith({});
    await getSliderCapabilities();

    const streamScan = dbExecute.mock.calls.find(([statement]) => isStreamAxisScan(statement))?.[0];
    expectNoBareFractionalParameter(streamScan);
  });
});

describe("the axis report is derived from the scan that already runs", () => {
  async function captureWindowScan() {
    dbExecute.mockResolvedValue([]);
    await getSliderCapabilities();
    return renderSqlText(dbExecute.mock.calls[0]?.[0]);
  }

  it("adds no second pass over the observed-day series", async () => {
    await captureWindowScan();
    // Gaps and thin ranges are window functions over `ranked`, which is already materialized.
    // A second pass would be a second read of the whole day series on a public procedure.
    // Counted by what each statement READS, not by call count: the feature lane and the stream
    // lane each read the census ONCE, over disjoint surface_kinds, and neither touches
    // geo.features -- which is what drizzle/0029 bought and what would regress silently if a
    // reader were repointed back at the base table.
    const featureAxisScans = dbExecute.mock.calls.filter(([statement]) => isFeatureAxisScan(statement));
    const streamAxisScans = dbExecute.mock.calls.filter(([statement]) => isStreamAxisScan(statement));
    expect(featureAxisScans).toHaveLength(1);
    expect(streamAxisScans).toHaveLength(1);
    const heapScans = dbExecute.mock.calls.filter(([statement]) =>
      renderSqlText(statement).includes("JOIN geo.features f")
    );
    expect(heapScans).toHaveLength(0);
  });

  it("tests thinness against the same density_floor column the axis start is chosen by", async () => {
    const statement = await captureWindowScan();
    expect(statement).toContain("n.observation_count >= d.density_floor");
    expect(statement).toContain("r.observation_count < d.density_floor");
  });

  it("formats every range endpoint in SQL rather than trusting the driver's DATE decoding", async () => {
    const statement = await captureWindowScan();
    // Inside jsonb_build_object there is no toCalendarDate left to normalize a Date object.
    expect(statement).toMatch(/to_char\(previous_observed_day \+ 1, 'YYYY-MM-DD'\)/);
    expect(statement).toMatch(/to_char\(observed_day - 1, 'YYYY-MM-DD'\)/);
  });

  it("casts the row number to integer, because date minus bigint has no operator", async () => {
    const statement = await captureWindowScan();
    expect(statement).toMatch(/\)::integer AS island_key/);
  });

  it("returns an empty array rather than a null for a layer with no ranges", async () => {
    const statement = await captureWindowScan();
    // jsonb_agg over zero rows is NULL, and the contract says DayRange[], never null.
    expect(statement).toContain("COALESCE(g.coverage_gaps, '[]'::jsonb)");
    expect(statement).toContain("COALESCE(t.thin_ranges, '[]'::jsonb)");
  });

  it("joins the range CTEs at one row per layer, so the day counts cannot fan out", async () => {
    const statement = await captureWindowScan();
    // Both CTEs aggregate to one row per layer before the join; joining a per-day relation
    // into the final FROM would multiply `ranked` and inflate dense/clustered/recorded counts.
    expect(statement).toMatch(/AS coverage_gaps\s+FROM axis[\s\S]*?GROUP BY layer_name/);
    expect(statement).toMatch(/AS thin_ranges\s+FROM \([\s\S]*?\) islands\s+GROUP BY layer_name/);
    expect(statement).toContain(
      "GROUP BY l.name, s.dense_earliest_day, g.coverage_gaps, t.thin_ranges"
    );
  });
});

describe("getMetricAtDate -- typed availability, never a bare empty collection", () => {
  it("reports not_published, with a reason naming the metric, for a metric with no backing source", async () => {
    const result = await getMetricAtDate({
      metric: "nonexistent-metric",
      date: "2026-08-03",
      variant: "observed",
    });

    expect(result.availability).toBe("not_published");
    expect(result.reason).toMatch(/nonexistent-metric/);
    expect(result.features).toEqual([]);
    // An unknown metric is rejected before any warehouse read is attempted.
    expect(dbExecute).not.toHaveBeenCalled();
  });

  it("reports not_published, with a reason naming the day, for a day inside the window with nothing recorded", async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(Date.parse("2026-08-04T12:00:00Z"));
      dbExecute
        .mockResolvedValueOnce([
          {
            layer_name: "water-gauges",
            dense_earliest_day: "2026-07-01",
            clustered_earliest_day: "2026-07-01",
            recorded_earliest_day: "2026-07-01",
            dense_latest_day: "2026-08-03",
            recorded_latest_day: "2026-08-03",
            dense_day_count: 34,
            clustered_day_count: 34,
            recorded_day_count: 34,
            density_floor: 110,
            coverage_gaps: [],
            thin_ranges: [],
          },
        ])
        .mockResolvedValueOnce([]);

      const result = await getMetricAtDate({
        metric: "streamflow-cfs",
        date: "2026-08-03",
        variant: "observed",
      });

      expect(result.availability).toBe("not_published");
      expect(result.reason).toBe("Streamflow recorded no observation on 2026-08-03.");
      expect(result.features).toEqual([]);
      // The capability window and the day's rows are two separate reads.
      expect(dbExecute).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("range-checks the date against the shared capability payload, not a second window scan", async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(Date.parse("2026-08-04T12:00:00Z"));
      dbExecute.mockImplementation((...args: unknown[]) =>
        Promise.resolve(
          renderSqlText(args[0]).includes("dense_earliest_day")
            ? [
                {
                  layer_name: "water-gauges",
                  dense_earliest_day: "2026-08-02",
                  clustered_earliest_day: "2026-05-24",
                  recorded_earliest_day: "1990-09-30",
                  dense_latest_day: "2026-08-04",
                  recorded_latest_day: "2026-08-04",
                  dense_day_count: 3,
                  clustered_day_count: 15,
                  recorded_day_count: 22,
                  density_floor: 110,
                  coverage_gaps: [],
                  thin_ranges: [],
                },
              ]
            : []
        )
      );

      await getMetricAtDate({ metric: "streamflow-cfs", date: "2026-08-03", variant: "observed" });
      await getMetricAtDate({ metric: "streamflow-cfs", date: "2026-08-04", variant: "observed" });

      // One window scan for both requests: the prefetch radius fans several of these out per
      // scrub, and the window query is a sequential scan of the whole layer.
      const windowScans = dbExecute.mock.calls.filter(([statement]) =>
        renderSqlText(statement).includes("dense_earliest_day")
      );
      expect(windowScans).toHaveLength(1);
      expect(dbExecute).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("observation days are bucketed by the day the publisher named", () => {
  /**
   * A real production row: USGS stores gauge readings with their own -07:00 offset, so
   * 2026-08-03T23:50:00.000-07:00 is August 3 to the publisher and to anyone cross-checking
   * waterdata.usgs.gov, but 2026-08-04T06:50Z in UTC. 6,279 of 16,743 stored gauge readings
   * (37.5%) straddle that boundary, so UTC bucketing silently displaces them by a day.
   */
  const OFFSET_BEARING_READING = "2026-08-03T23:50:00.000-07:00";

  it("takes the ISO date part, which is the publisher's day, not the UTC day", () => {
    expect(OFFSET_BEARING_READING.slice(0, 10)).toBe("2026-08-03");
    expect(new Date(OFFSET_BEARING_READING).toISOString().slice(0, 10)).toBe("2026-08-04");
  });

  it("buckets in SQL by substring, never by AT TIME ZONE 'UTC'", async () => {
    // READ OFF getMetricAtDate, NOT off getSliderCapabilities. Since drizzle/0029 the capability
    // axis reads geo.v_observation_day_census, whose days were bucketed once at REFRESH time by
    // geo.feature_observation_day -- so the rendered capability statement carries no day
    // expression to assert on any more. getMetricAtDate's candidate/page CTEs still build
    // OBSERVATION_DAY per request and are the live carrier of this rule. The refresh-time half
    // is pinned separately: src/__tests__/lib/observation-day-contract.test.ts holds
    // geo.feature_observation_day and its JavaScript twin to the same keys and the same slice.
    // The capability read has to be answered, or getMetricAtDate range-checks the day against an
    // empty catalogue and returns before it builds a day predicate at all.
    dbExecute.mockImplementation((...args: unknown[]) =>
      Promise.resolve(
        renderSqlText(args[0]).includes("dense_earliest_day")
          ? [
              {
                layer_name: "water-gauges",
                dense_earliest_day: "2026-07-01",
                clustered_earliest_day: "2026-07-01",
                recorded_earliest_day: "2026-07-01",
                dense_latest_day: "2026-08-03",
                recorded_latest_day: "2026-08-03",
                dense_day_count: 34,
                clustered_day_count: 34,
                recorded_day_count: 34,
                density_floor: 110,
                coverage_gaps: [],
                thin_ranges: [],
              },
            ]
          : []
      )
    );
    await getMetricAtDate({ metric: "streamflow-cfs", date: "2026-08-03", variant: "observed" });

    const statement = dbExecute.mock.calls
      .map(([call]) => renderSqlText(call))
      .find((text) => text.includes("substring("));
    if (statement === undefined) throw new Error("expected a statement bucketing by substring");
    expect(statement).toContain(", 1, 10)::date");
    // Restoring the UTC cast moves 37.5% of gauge readings onto the following day.
    expect(statement).not.toContain("AT TIME ZONE 'UTC'");
  });
});

describe("the USGS -999999 no-data sentinel is never drawn", () => {
  /** Builds a stored water-gauges properties payload with the shape the read model expects. */
  function gaugeRow(siteNo: string, flowCfs: number, updatedAt: string) {
    return {
      properties: {
        siteNo,
        siteName: `Site ${siteNo}`,
        geometry: { type: "Point", coordinates: [-116.2, 43.6] },
        flowCfs,
        updatedAt,
      },
    };
  }

  it("drops sentinel gauges outright while keeping genuine reverse flow", async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(Date.parse("2026-08-04T12:00:00Z"));
      const updatedAt = "2026-08-04T04:00:00.000-07:00";
      // Set on the field the `.from(features)` branch reads, not via `.mockResolvedValue`,
      // since that would also feed these rows to `resolveCachedLayerId`'s own `.from(layers)`
      // call sharing this mock -- see resetQueryChain's table-based switch.
      queryChain.featureRows = [
        gaugeRow("14105700", -999999, updatedAt),
        // Reverse flow is real at these gauges and reaches -172,000 cfs in production, so the
        // exclusion must compare exactly -- "negative means missing" would erase a measurement.
        gaugeRow("14211720", -15_700, updatedAt),
        gaugeRow("13206000", 42.5, updatedAt),
      ];

      const gauges = await getPublishedStreamflowGauges("-125,41,-110,49");

      expect(gauges.map((gauge) => gauge.siteNo)).toEqual(["14211720", "13206000"]);
      expect(gauges.map((gauge) => gauge.flowCfs)).toEqual([-15_700, 42.5]);
      // Dropped, not nulled: a pin with a null flow still asserts the gauge reported today.
      expect(gauges.some((gauge) => gauge.flowCfs === -999999)).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("drought carry-forward is bounded, never unlimited", () => {
  /** The two-column release probe getDroughtMetricAtDate issues before reading geometry. */
  function releaseProbe(
    earliest: string | null,
    asOf: string | null,
    next: string | null
  ) {
    return [{ earliest_release: earliest, as_of_release: asOf, next_release: next }];
  }

  /**
   * Regression: shipped comparing the release date as `d.valid_date = $1::date`, which 500s
   * with `operator does not exist: character varying = date` because geo.drought_areas.valid_date
   * is varchar. Every historical drought day failed in production while today's kept working,
   * and nothing caught it -- tsc cannot see a SQL operator, and renderSqlText discards the
   * placeholder, so the cast that abutted it was invisible. flattenSql keeps both, so this
   * asserts on the parameter's actual SQL context. Text comparison is correct here: every
   * stored value is fixed-width ISO YYYY-MM-DD, so lexicographic order is chronological order.
   */
  it("compares the release date as text, since valid_date is varchar and has no = date operator", async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(Date.parse("2026-08-04T12:00:00Z"));
      dbExecute
        .mockResolvedValueOnce(releaseProbe("2022-08-09", "2024-06-11", "2024-06-18"))
        .mockResolvedValueOnce([]);

      await getPublishedDroughtClassification("-125,42,-111,49", "2024-06-15");

      const geometryRead = dbExecute.mock.calls[1]?.[0];
      const tokens = flattenSql(geometryRead);
      const releaseDateParams = tokens.flatMap((token, index) => {
        if (token.kind !== "param" || token.value !== "2024-06-11") return [];
        const next = tokens[index + 1];
        return [next !== undefined && next.kind === "text" ? next.text : ""];
      });
      expect(releaseDateParams.length).toBeGreaterThan(0);
      for (const following of releaseDateParams) {
        expect(following.startsWith("::date")).toBe(false);
      }
      expect(renderSqlText(geometryRead)).not.toMatch(/valid_date\s*=\s*::date/);
    } finally {
      vi.useRealTimers();
    }
  });

  it("refuses a release week the record skips instead of filling it from the week before", async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(Date.parse("2026-08-04T12:00:00Z"));
      // usdm_history.ingest_release_week records a 404 week as is_gap; history_gap_weeks
      // documents that the slider must render these empty.
      dbExecute.mockResolvedValueOnce(releaseProbe("2026-01-06", "2026-03-03", "2026-03-17"));

      const result = await getMetricAtDate({
        metric: "drought-category",
        date: "2026-03-10",
        variant: "observed",
      });

      expect(result.availability).toBe("not_published");
      expect(result.reason).toContain("2026-03-03");
      expect(result.reason).toContain("2026-03-17");
      expect(result.features).toEqual([]);
      // Refused before any geometry is read.
      expect(dbExecute).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("serves the newest release at the live edge, and says how many days it is carrying it", async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(Date.parse("2026-08-04T12:00:00Z"));
      dbExecute
        .mockResolvedValueOnce(releaseProbe("2026-07-28", "2026-07-28", null))
        .mockResolvedValueOnce([
          {
            dm_category: 2,
            valid_date: "2026-07-28",
            geometry: JSON.stringify({
              type: "Polygon",
              coordinates: [[[-117, 43], [-116, 43], [-116, 44], [-117, 43]]],
            }),
          },
        ]);

      const result = await getMetricAtDate({
        metric: "drought-category",
        date: "2026-08-04",
        variant: "observed",
      });

      expect(result.availability).toBe("published");
      expect(result.reason).toBe(
        "As of the 2026-07-28 US Drought Monitor release, 7 days before 2026-08-04."
      );
      expect(result.features[0]?.properties?.issuedOn).toBe("2026-07-28");
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops serving a stalled weekly job rather than aging the same release without limit", async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(Date.parse("2026-09-15T12:00:00Z"));
      dbExecute.mockResolvedValueOnce(releaseProbe("2026-07-28", "2026-07-28", null));

      const result = await getMetricAtDate({
        metric: "drought-category",
        date: "2026-09-15",
        variant: "observed",
      });

      expect(result.availability).toBe("not_published");
      expect(result.reason).toContain("49 days before 2026-09-15");
      expect(result.features).toEqual([]);
      expect(dbExecute).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("bbox narrows the warehouse query", () => {
  it("adds the viewport's spatial predicates only when a bbox is supplied", async () => {
    await getPublishedFireDetections();
    expect(vi.mocked(gte).mock.calls.length).toBe(1); // createdAt floor only
    expect(vi.mocked(lte).mock.calls.length).toBe(0);

    vi.mocked(gte).mockClear();
    vi.mocked(lte).mockClear();

    await getPublishedFireDetections("-124,42,-117,49");
    expect(vi.mocked(gte).mock.calls.length).toBe(3); // createdAt floor + west + south
    expect(vi.mocked(lte).mock.calls.length).toBe(2); // east + north
  });

  it("rejects a malformed bbox before touching the warehouse", async () => {
    await expect(getPublishedFireDetections("not,a,bbox")).rejects.toThrow(RangeError);
    expect(queryChain.select).not.toHaveBeenCalled();
  });
});

describe("getPublishedVegetationIndex -- one cell per place, not one row per observation", () => {
  /**
   * A fractional viewport, so the bbox corners the reader binds are genuinely non-integer.
   * A whole-degree bbox would let the typing assertion below pass vacuously.
   */
  const VIEWPORT = "-116.75,43.25,-115.25,44.5";

  /** The projection getPublishedVegetationIndex selects, with the stored payload's shape. */
  function cellRow(cellKey: string, ndvi: number, observedAt: string) {
    return {
      geometry_id: `geom-${cellKey}`,
      geometry: JSON.stringify({
        type: "Polygon",
        coordinates: [[[-113.75, 43], [-113.5, 43], [-113.5, 43.25], [-113.75, 43.25], [-113.75, 43]]],
      }),
      ndvi: String(ndvi),
      observed_at: observedAt,
      cell_key: cellKey,
      scene_id: "S2A_11TQH_20260804_0_L2A",
      cloud_cover: "3.626448",
      sample_count: "21",
      grid_name: "sentinel2-ndvi-0p25deg",
      resolution_metres: "27830",
      source: "Sentinel-2 L2A",
      provenance_key: `${cellKey}:${observedAt}`,
    };
  }

  /** Runs the reader against an empty warehouse and hands back the statement it built. */
  async function captureCellScan() {
    dbExecute.mockResolvedValue([]);
    await getPublishedVegetationIndex(VIEWPORT);
    return dbExecute.mock.calls[0]?.[0];
  }

  it("collapses the stacked series to the newest row per geometry, never returning it raw", async () => {
    // 184,409 published rows sit on 1,568 distinct cells -- ~118 observations of the same
    // square. Without the DISTINCT ON the map draws every cell a hundred times over, and a
    // single PNW viewport ships 124,959 features instead of 1,036.
    const statement = renderSqlText(await captureCellScan());
    expect(statement).toContain("SELECT DISTINCT ON (c.geometry_id)");
    expect(statement).toMatch(
      /ORDER BY c\.geometry_id, \(c\.properties->>'observedAt'\)::timestamptz DESC/
    );
  });

  it("narrows the scan by viewport and by observation window, both in SQL", async () => {
    const statement = renderSqlText(await captureCellScan());
    // The bbox is an index-usable && against geo.features.geom, not a post-filter.
    expect(statement).toMatch(/f\.geom && ST_MakeEnvelope\(/);
    // The freshness cutoff is bound as text, so its cast is what makes the comparison a
    // timestamp comparison rather than a string one.
    expect(statement).toMatch(/\(f\.properties->>'observedAt'\)::timestamptz >=\s*::timestamptz/);
    // Excluding valueless cells in SQL is what makes LIMIT count only drawable cells.
    expect(statement).toContain("jsonb_typeof(f.properties->'ndvi') = 'number'");
  });

  it("binds no bare fractional parameter, so none can be resolved against a bigint", async () => {
    // postgres-js types every non-bigint JS number as OID 0, so Postgres resolves the
    // parameter from the expression around it. A fractional value in a bare arithmetic
    // context next to a bigint is read as a bigint and throws `invalid input syntax for
    // type bigint` at runtime -- the exact production 500 getSliderCapabilities hit. Every
    // fractional value this query binds must therefore either carry its own cast or land in
    // a PostGIS argument whose signature already declares the type.
    const tokens = flattenSql(await captureCellScan());
    /** Literal SQL written before `index`; parameters contribute no text of their own. */
    const textBefore = (index: number) =>
      tokens
        .slice(0, index)
        .map((token) => (token.kind === "text" ? token.text : ""))
        .join("");

    const fractional = tokens.flatMap((token, index) =>
      token.kind === "param" &&
      typeof token.value === "number" &&
      !Number.isInteger(token.value)
        ? [{ value: token.value, before: textBefore(index), following: tokens[index + 1] }]
        : []
    );

    // Pins the assertion against passing vacuously: the viewport corners above are
    // fractional, so at least the envelope's arguments must show up here.
    expect(fractional.length).toBeGreaterThan(0);

    const untyped = fractional.filter(({ before, following }) => {
      const next = following?.kind === "text" ? following.text.trimStart() : "";
      if (next.startsWith("::")) return false;
      // Otherwise it is only safe inside a PostGIS call that declares its argument types --
      // the innermost parenthesis still open at this point must belong to one.
      return !/ST_(MakeEnvelope|SimplifyPreserveTopology)\([^()]*$/.test(before);
    });
    expect(untyped.map(({ value }) => value)).toEqual([]);
  });

  it("reports stale rather than empty when cells exist here but none was seen recently", async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(Date.parse("2026-08-05T12:00:00Z"));
      dbExecute
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([{ observed_at: "2025-09-01T18:42:36.471Z" }]);

      const result = await getPublishedVegetationIndex(VIEWPORT);

      expect(result.availability).toBe("unavailable");
      expect(result.reason).toBe("stale");
      expect(result.observedAt).toBe("2025-09-01T18:42:36.471Z");
      expect(result.features).toEqual([]);
      // The explaining probe is paid for only when the main read came back empty.
      expect(dbExecute).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("reports not_published, with no observation time, for a viewport the grid never sampled", async () => {
    dbExecute.mockResolvedValueOnce([]).mockResolvedValueOnce([{ observed_at: null }]);

    const result = await getPublishedVegetationIndex(VIEWPORT);

    expect(result.availability).toBe("unavailable");
    expect(result.reason).toBe("not_published");
    expect(result.observedAt).toBeNull();
  });

  it("carries the cell's provenance and drops the duplicated geometry copy", async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(Date.parse("2026-08-05T12:00:00Z"));
      dbExecute.mockResolvedValueOnce([
        cellRow("43.1250:-113.6250", 0.1578, "2026-08-01T18:42:36.471Z"),
        cellRow("43.1250:-113.8750", 0.203, "2026-08-04T18:42:36.471Z"),
      ]);

      const result = await getPublishedVegetationIndex(VIEWPORT);

      expect(result.availability).toBe("published");
      expect(result.reason).toBeNull();
      expect(result.truncated).toBe(false);
      expect(result.cellCount).toBe(2);
      // The collection reports the newest reading in it, not the newest row order-wise.
      expect(result.observedAt).toBe("2026-08-04T18:42:36.471Z");

      const properties = result.features[0]?.properties as Record<string, unknown>;
      expect(properties.ndvi).toBe(0.1578);
      expect(properties.cellKey).toBe("43.1250:-113.6250");
      expect(properties.geometryId).toBe("geom-43.1250:-113.6250");
      expect(properties.provenanceKey).toBe("43.1250:-113.6250:2026-08-01T18:42:36.471Z");
      expect(properties.sampleCount).toBe(21);
      // geo.features.properties keeps its own copy of the geometry; shipping it as a
      // property would double the payload and could disagree with the drawn geom.
      expect(properties.geometry).toBeUndefined();
      expect(result.features[0]?.geometry.type).toBe("Polygon");
    } finally {
      vi.useRealTimers();
    }
  });

  it("says so when it truncates, and probes exactly one row past its own cap", async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(Date.parse("2026-08-05T12:00:00Z"));

      // The cap is read off the statement rather than restated here, so this test cannot
      // drift away from the constant it is guarding.
      const tokens = flattenSql(await captureCellScan());
      const limitIndex = tokens.findIndex(
        (token) => token.kind === "text" && /LIMIT\s*$/.test(token.text)
      );
      const probe = tokens[limitIndex + 1];
      if (probe?.kind !== "param" || typeof probe.value !== "number") {
        throw new Error("expected the cell scan to bind its LIMIT as a parameter");
      }
      const maxCells = probe.value - 1;
      expect(Number.isInteger(maxCells)).toBe(true);
      expect(maxCells).toBeGreaterThan(0);

      // One row past the cap is what distinguishes "there is more" from "the page filled
      // exactly", so `truncated` is never claimed against a complete answer.
      dbExecute.mockResolvedValueOnce(
        Array.from({ length: maxCells + 1 }, (_value, index) =>
          cellRow(`cell-${index}`, 0.4, "2026-08-04T18:42:36.471Z")
        )
      );
      const truncated = await getPublishedVegetationIndex(VIEWPORT);
      expect(truncated.truncated).toBe(true);
      expect(truncated.features).toHaveLength(maxCells);
      expect(truncated.maxCellCount).toBe(maxCells);

      dbExecute.mockResolvedValueOnce(
        Array.from({ length: maxCells }, (_value, index) =>
          cellRow(`cell-${index}`, 0.4, "2026-08-04T18:42:36.471Z")
        )
      );
      const exact = await getPublishedVegetationIndex(VIEWPORT);
      expect(exact.truncated).toBe(false);
      expect(exact.features).toHaveLength(maxCells);
    } finally {
      vi.useRealTimers();
    }
  });

  it("drops a cell whose stored reading is older than the window it publishes", async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(Date.parse("2026-08-05T12:00:00Z"));
      dbExecute.mockResolvedValueOnce([
        cellRow("43.1250:-113.6250", 0.31, "2026-08-04T18:42:36.471Z"),
        // Would have been excluded by the SQL cutoff; re-checked here because the cutoff
        // was computed before the round trip, not after it.
        cellRow("43.1250:-113.8750", 0.28, "2025-01-04T18:42:36.471Z"),
      ]);

      const result = await getPublishedVegetationIndex(VIEWPORT);

      expect(result.features).toHaveLength(1);
      expect(result.maxObservationAgeDays).toBeGreaterThan(0);
      const kept = result.features[0]?.properties as Record<string, unknown>;
      expect(kept.cellKey).toBe("43.1250:-113.6250");
    } finally {
      vi.useRealTimers();
    }
  });

  it("rejects a malformed bbox before touching the warehouse", async () => {
    await expect(getPublishedVegetationIndex("not,a,bbox")).rejects.toThrow(RangeError);
    expect(dbExecute).not.toHaveBeenCalled();
  });
});

/**
 * The slider's day, threaded through the viewport readers.
 *
 * Two failure modes these cases exist to prevent, both of which produce a map that looks fine
 * and lies. First, bucketing a named day in UTC displaces 37.5% of stored USGS readings onto
 * the following calendar day. Second, leaving a now-relative freshness window in place while
 * pinning a past day empties every historical day the warehouse genuinely holds -- a 6-hour
 * streamflow window and a 30-day vegetation window both reject every reading from last spring.
 */
describe("a named day is answered by that day, and today's answer is unchanged", () => {
  /** A fractional viewport, so the corners bound below are genuinely non-integer. */
  const VIEWPORT = "-116.75,43.25,-115.25,44.5";
  const TODAY = "2026-08-05";
  const NOW_MS = Date.parse(`${TODAY}T12:00:00Z`);
  /** Deep enough in the past that every live freshness window would reject it. */
  const PAST_DAY = "2026-05-01";

  /** Runs `body` with the server's clock pinned, so "today" is a known date. */
  async function atServerToday<T>(body: () => Promise<T>): Promise<T> {
    vi.useFakeTimers();
    vi.setSystemTime(NOW_MS);
    try {
      return await body();
    } finally {
      vi.useRealTimers();
    }
  }

  describe("resolveRequestedObservationDay", () => {
    it("treats an omitted day and the server's today as the same live read", () => {
      expect(resolveRequestedObservationDay(undefined, TODAY)).toEqual({ kind: "live" });
      expect(resolveRequestedObservationDay(TODAY, TODAY)).toEqual({ kind: "live" });
    });

    it("routes a past day to the historical read", () => {
      expect(resolveRequestedObservationDay(PAST_DAY, TODAY)).toEqual({
        kind: "historical",
        date: PAST_DAY,
      });
    });

    it("refuses a future day and a non-date outright", () => {
      const future = resolveRequestedObservationDay("2026-08-06", TODAY);
      expect(future.kind).toBe("unobserved");
      const nonsense = resolveRequestedObservationDay("2026-13-45", TODAY);
      expect(nonsense.kind).toBe("unobserved");
      const notADate = resolveRequestedObservationDay("yesterday", TODAY);
      expect(notADate.kind).toBe("unobserved");
    });
  });

  describe("getPublishedStreamflowGauges", () => {
    /** A stored water-gauges row as the reader consumes it, on either path. */
    function gaugeRow(siteNo: string, flowCfs: number, updatedAt: string) {
      return {
        properties: {
          siteNo,
          siteName: `Site ${siteNo}`,
          geometry: { type: "Point", coordinates: [-116.2, 43.6] },
          flowCfs,
          updatedAt,
        },
      };
    }

    it("keeps a reading the 6-hour live window would have rejected, and collapses to one row per gauge", async () => {
      const gauges = await atServerToday(async () => {
        // 02:15 local on the requested day: three months old, so `isFreshObservation` against
        // STREAMFLOW_MAX_AGE_MS rejects it, and re-anchoring six hours to the end of that day
        // would too. A fully observed day must not come back empty for either reason.
        dbExecute.mockResolvedValueOnce([
          gaugeRow("13172500", 512, `${PAST_DAY}T02:15:00.000-07:00`),
        ]);
        return getPublishedStreamflowGauges(VIEWPORT, PAST_DAY);
      });

      expect(gauges.map((gauge) => gauge.siteNo)).toEqual(["13172500"]);
      expect(gauges[0].flowCfs).toBe(512);
      // The live-edge FEATURE select (paging by created_at) is never reached for a past day --
      // that data comes from db.execute instead. resolveCachedLayerId's own lookup still goes
      // through db.select on every path though, so exactly one queryChain call is expected here.
      expect(queryChain.limit).toHaveBeenCalledTimes(1);

      const statement = renderSqlText(dbExecute.mock.calls[0]?.[0]);
      expect(statement).toContain("SELECT DISTINCT ON (f.properties->>'siteNo')");
      expect(statement).toContain("substring(");
      expect(statement).toContain(", 1, 10)::date");
      // Restoring the UTC cast moves 37.5% of gauge readings onto the following day.
      expect(statement).not.toContain("AT TIME ZONE 'UTC'");
    });

    it("keeps an offset-bearing late-evening reading on the day its own timestamp names", async () => {
      const [named, theDayAfter] = await atServerToday(async () => {
        // 23:50 at -07:00 is 06:50Z the NEXT day. The publisher, and anyone cross-checking
        // waterdata.usgs.gov, calls this a 2026-05-01 reading.
        dbExecute.mockResolvedValue([
          gaugeRow("14211720", 88, `${PAST_DAY}T23:50:00.000-07:00`),
        ]);
        return Promise.all([
          getPublishedStreamflowGauges(VIEWPORT, PAST_DAY),
          getPublishedStreamflowGauges(VIEWPORT, "2026-05-02"),
        ]);
      });

      expect(named).toHaveLength(1);
      // Under UTC bucketing this reading would have answered 2026-05-02 instead.
      expect(theDayAfter).toHaveLength(0);
    });

    it("refuses a future day without touching the warehouse", async () => {
      const gauges = await atServerToday(() =>
        getPublishedStreamflowGauges(VIEWPORT, "2026-08-06")
      );

      expect(gauges).toEqual([]);
      expect(dbExecute).not.toHaveBeenCalled();
      expect(queryChain.limit).not.toHaveBeenCalled();
    });

    it("runs the live read, unchanged, for an omitted day and for the server's today", async () => {
      const [omitted, today] = await atServerToday(async () => {
        queryChain.featureRows = [
          gaugeRow("13206000", 42.5, `${TODAY}T04:00:00.000-07:00`),
        ];
        return Promise.all([
          getPublishedStreamflowGauges(VIEWPORT),
          getPublishedStreamflowGauges(VIEWPORT, TODAY),
        ]);
      });

      expect(today).toEqual(omitted);
      expect(today).toHaveLength(1);
      // Today's answer is the query it has always been: no named-day statement is issued.
      expect(dbExecute).not.toHaveBeenCalled();
      // Both concurrent calls race resolveCachedLayerId's cache before either populates it (a
      // Promise.all starts both bodies synchronously), so each independently misses and calls
      // the resolver's own `db.select` -- 2 resolver calls + 2 feature-select calls.
      expect(queryChain.limit).toHaveBeenCalledTimes(4);
    });

    it("binds no bare fractional parameter on the named-day path", async () => {
      await atServerToday(() => getPublishedStreamflowGauges(VIEWPORT, PAST_DAY));
      expectNoBareFractionalParameter(dbExecute.mock.calls[0]?.[0]);
    });
  });

  describe("getPublishedWeatherForBbox", () => {
    /** The projection the named-day read selects: geom coordinates, not the properties copy. */
    function weatherRow(lon: number, lat: number, observedAt: string) {
      return {
        properties: {
          observedAt,
          temperature: 24,
          humidity: 30,
          windSpeed: 5,
          windDirection: 180,
          precipitation: 0,
          // Deliberately disagrees with the geom columns beside it, which is the drift
          // getPublishedWeatherForPoint's own note warns about.
          geometry: { type: "Point", coordinates: [0, 0] },
        },
        lon,
        lat,
      };
    }

    it("collapses to the newest sample per grid point, and reads coordinates from geom", async () => {
      const observations = await atServerToday(async () => {
        dbExecute.mockResolvedValueOnce([
          weatherRow(-116.2, 43.6, `${PAST_DAY}T18:00:00.000Z`),
        ]);
        return getPublishedWeatherForBbox(VIEWPORT, PAST_DAY);
      });

      expect(observations).toHaveLength(1);
      expect(observations[0].lon).toBe(-116.2);
      expect(observations[0].lat).toBe(43.6);

      const statement = renderSqlText(dbExecute.mock.calls[0]?.[0]);
      expect(statement).toContain("SELECT DISTINCT ON (ST_X(f.geom), ST_Y(f.geom))");
      expect(statement).toContain(", 1, 10)::date");
      expect(statement).not.toContain("AT TIME ZONE 'UTC'");
    });

    it("keeps a partial observation's nulls and drops one with no drawable signal", async () => {
      const observations = await atServerToday(async () => {
        // Half a wind pair, but a temperature: renders (as temperature only).
        const partial = weatherRow(-116.2, 43.6, `${PAST_DAY}T18:00:00.000Z`);
        partial.properties.windDirection = null as unknown as number;
        // Neither a complete wind pair nor a temperature: nothing to draw.
        const undrawable = weatherRow(-115.9, 43.4, `${PAST_DAY}T18:00:00.000Z`);
        undrawable.properties.windDirection = null as unknown as number;
        undrawable.properties.temperature = null as unknown as number;
        dbExecute.mockResolvedValueOnce([partial, undrawable]);
        return getPublishedWeatherForBbox(VIEWPORT, PAST_DAY);
      });

      expect(observations).toHaveLength(1);
      expect(observations[0]).toMatchObject({
        lon: -116.2,
        windDirection: null,
      });
    });

    it("refuses a future day without touching the warehouse", async () => {
      const observations = await atServerToday(() =>
        getPublishedWeatherForBbox(VIEWPORT, "2026-08-06")
      );

      expect(observations).toEqual([]);
      expect(dbExecute).not.toHaveBeenCalled();
    });
  });

  describe("getPublishedVegetationIndex", () => {
    /** The projection the cell scan selects, with the stored payload's shape. */
    function cellRow(cellKey: string, ndvi: number, observedAt: string) {
      return {
        geometry_id: `geom-${cellKey}`,
        geometry: JSON.stringify({
          type: "Polygon",
          coordinates: [
            [
              [-113.75, 43],
              [-113.5, 43],
              [-113.5, 43.25],
              [-113.75, 43.25],
              [-113.75, 43],
            ],
          ],
        }),
        ndvi: String(ndvi),
        observed_at: observedAt,
        cell_key: cellKey,
        scene_id: "S2A_11TQH_20260501_0_L2A",
        cloud_cover: "3.6",
        sample_count: "21",
        grid_name: "sentinel2-ndvi-0p25deg",
        resolution_metres: "27830",
        source: "Sentinel-2 L2A",
        provenance_key: `${cellKey}:${observedAt}`,
      };
    }

    it("slides the 30-day window to end at the requested day instead of at now", async () => {
      const result = await atServerToday(async () => {
        // Inside 30 days of 2026-05-01, and ~3.5 months before "now": a now-relative cutoff
        // would blank the whole grid for that day.
        dbExecute.mockResolvedValueOnce([
          cellRow("43.1250:-113.6250", 0.31, "2026-04-20T18:42:36.471Z"),
        ]);
        return getPublishedVegetationIndex(VIEWPORT, PAST_DAY);
      });

      expect(result.availability).toBe("published");
      expect(result.features).toHaveLength(1);

      const statement = renderSqlText(dbExecute.mock.calls[0]?.[0]);
      expect(statement).toMatch(
        /substring\(f\.properties->>'observedAt', 1, 10\)::date >\s*::date/
      );
      expect(statement).toMatch(
        /substring\(f\.properties->>'observedAt', 1, 10\)::date <=\s*::date/
      );
    });

    it("drops a cell read before the window, and one read after the requested day", async () => {
      const result = await atServerToday(async () => {
        dbExecute.mockResolvedValueOnce([
          cellRow("in-window", 0.31, "2026-04-20T18:42:36.471Z"),
          cellRow("before-window", 0.28, "2026-03-01T18:42:36.471Z"),
          // A later reading must never leak backwards into a past day's answer.
          cellRow("after-the-day", 0.55, "2026-06-01T18:42:36.471Z"),
        ]);
        return getPublishedVegetationIndex(VIEWPORT, PAST_DAY);
      });

      expect(
        result.features.map(
          (feature) => (feature.properties as Record<string, unknown>).cellKey
        )
      ).toEqual(["in-window"]);
    });

    it("bounds the empty-case probe to the requested day, so a never-sampled day is not called stale", async () => {
      const result = await atServerToday(async () => {
        dbExecute
          .mockResolvedValueOnce([])
          .mockResolvedValueOnce([{ observed_at: "2026-03-01T18:42:36.471Z" }]);
        return getPublishedVegetationIndex(VIEWPORT, PAST_DAY);
      });

      expect(result.reason).toBe("stale");
      const probe = renderSqlText(dbExecute.mock.calls[1]?.[0]);
      expect(probe).toMatch(/<=\s*::date/);
    });

    it("reports not_forecastable for a future day without reading the warehouse", async () => {
      const result = await atServerToday(() =>
        getPublishedVegetationIndex(VIEWPORT, "2026-08-06")
      );

      expect(result.availability).toBe("unavailable");
      expect(result.reason).toBe("not_forecastable");
      expect(result.features).toEqual([]);
      expect(dbExecute).not.toHaveBeenCalled();
    });

    it("binds no bare fractional parameter on the named-day path", async () => {
      await atServerToday(() => getPublishedVegetationIndex(VIEWPORT, PAST_DAY));
      expectNoBareFractionalParameter(dbExecute.mock.calls[0]?.[0]);
    });
  });

  describe("getPublishedDroughtClassification", () => {
    /** The release probe `resolveDroughtRelease` issues before reading geometry. */
    function releaseProbe(
      earliest: string | null,
      asOf: string | null,
      next: string | null
    ) {
      return [{ earliest_release: earliest, as_of_release: asOf, next_release: next }];
    }

    function classRow(dmCategory: number, validDate: string) {
      return {
        dm_category: dmCategory,
        valid_date: validDate,
        source_url: "https://droughtmonitor.unl.edu/",
        geometry: JSON.stringify({
          type: "Polygon",
          coordinates: [
            [
              [-117, 43],
              [-116, 43],
              [-116, 44],
              [-117, 43],
            ],
          ],
        }),
      };
    }

    it("renders a release week the record skips as empty, not as the week before it", async () => {
      const result = await atServerToday(async () => {
        dbExecute.mockResolvedValueOnce(
          releaseProbe("2026-01-06", "2026-03-03", "2026-03-17")
        );
        return getPublishedDroughtClassification(VIEWPORT, "2026-03-10");
      });

      expect(result.availability).toBe("unavailable");
      expect(result.reason).toBe("release_week_not_published");
      expect(result.features).toEqual([]);
      // Refused before any geometry is read: the shared resolver decides this, not the clip.
      expect(dbExecute).toHaveBeenCalledTimes(1);
    });

    it("pins a past day to the release covering it, never to the newest one", async () => {
      const result = await atServerToday(async () => {
        dbExecute
          .mockResolvedValueOnce(
            releaseProbe("2026-01-06", "2026-04-28", "2026-05-05")
          )
          .mockResolvedValueOnce([classRow(2, "2026-04-28")]);
        return getPublishedDroughtClassification(VIEWPORT, PAST_DAY);
      });

      expect(result.availability).toBe("published");
      // The release's own date, not the requested one, and how far it was carried.
      expect(result.features[0]?.properties?.validDate).toBe("2026-04-28");
      expect(result.carryForwardDays).toBe(3);

      const geometryStatement = renderSqlText(dbExecute.mock.calls[1]?.[0]);
      expect(geometryStatement).toContain("d.valid_date = ");
      expect(geometryStatement).not.toContain("ORDER BY valid_date DESC");
    });

    it("reads the newest release, in one statement, when no day is named", async () => {
      const result = await atServerToday(async () => {
        dbExecute.mockResolvedValueOnce([classRow(1, "2026-08-04")]);
        return getPublishedDroughtClassification(VIEWPORT);
      });

      expect(result.availability).toBe("published");
      // Nothing was carried forward TO, so the field is absent rather than a bare 0.
      expect(result.carryForwardDays).toBeUndefined();
      // The live path must not start paying for a release probe.
      expect(dbExecute).toHaveBeenCalledTimes(1);
      expect(renderSqlText(dbExecute.mock.calls[0]?.[0])).toContain(
        "ORDER BY valid_date DESC"
      );
    });

    it("refuses a future day without touching the warehouse", async () => {
      const result = await atServerToday(() =>
        getPublishedDroughtClassification(VIEWPORT, "2026-08-06")
      );

      expect(result.availability).toBe("unavailable");
      expect(result.reason).toBe("not_forecastable");
      expect(dbExecute).not.toHaveBeenCalled();
    });
  });

  describe("getPublishedFireDetections", () => {
    it("dates a detection by its FIRMS acquisition day, never by created_at", async () => {
      const collection = await atServerToday(async () => {
        dbExecute.mockResolvedValueOnce([
          {
            properties: {
              geometry: { type: "Point", coordinates: [-116.2, 43.6] },
              acqDate: PAST_DAY,
              acqTime: "1042",
              frp: 12.5,
            },
          },
        ]);
        return getPublishedFireDetections(VIEWPORT, 2, PAST_DAY);
      });

      expect(collection.features).toHaveLength(1);
      expect(collection.features[0].properties?.observedAt).toBe(
        `${PAST_DAY}T10:42:00.000Z`
      );
      // created_at is a "last touched" column the refresh path rewrites, so the live read's own
      // FEATURE select can never answer for a past day -- that data comes from db.execute
      // instead. resolveCachedLayerId's own lookup still goes through db.select regardless of
      // day.kind, so exactly one queryChain call is expected here, not zero.
      expect(queryChain.limit).toHaveBeenCalledTimes(1);
      expect(renderSqlText(dbExecute.mock.calls[0]?.[0])).toContain(
        "f.properties->>'acqDate'"
      );
    });

    it("refuses a future day without touching the warehouse", async () => {
      const collection = await atServerToday(() =>
        getPublishedFireDetections(VIEWPORT, 2, "2026-08-06")
      );

      expect(collection.features).toEqual([]);
      expect(dbExecute).not.toHaveBeenCalled();
      expect(queryChain.limit).not.toHaveBeenCalled();
    });
  });
});
