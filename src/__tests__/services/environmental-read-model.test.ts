import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Two seams into the warehouse, both stubbed so nothing here touches a real database:
 * `queryChain` is the drizzle query-builder chain (`getPublishedFireDetections`'s path),
 * `dbExecute` is the raw `sql` path the slider-capability and metric-at-date queries use.
 * vi.hoisted keeps both ahead of the vi.mock factories' hoist point.
 */
const queryChain = vi.hoisted(() => ({
  select: vi.fn((..._args: unknown[]) => queryChain),
  from: vi.fn((..._args: unknown[]) => queryChain),
  innerJoin: vi.fn((..._args: unknown[]) => queryChain),
  where: vi.fn((..._args: unknown[]) => queryChain),
  orderBy: vi.fn((..._args: unknown[]) => queryChain),
  limit: vi.fn(() => Promise.resolve([] as unknown[])),
}));

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
  queryChain.select.mockReturnValue(queryChain);
  queryChain.from.mockReturnValue(queryChain);
  queryChain.innerJoin.mockReturnValue(queryChain);
  queryChain.where.mockReturnValue(queryChain);
  queryChain.orderBy.mockReturnValue(queryChain);
  queryChain.limit.mockResolvedValue([]);
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

describe("getSliderCapabilities -- the 36-year trap", () => {
  /** One observed day and how many observations landed on it. */
  type ObservedDay = readonly [day: string, observationCount: number];

  /** Mirrors OBSERVATION_CLUSTER_GAP_DAYS in environmental-read-model.ts. */
  const GAP_THRESHOLD_DAYS = 21;
  /** Mirrors OBSERVATION_DENSITY_FLOOR_FRACTION in environmental-read-model.ts. */
  const DENSITY_FLOOR_FRACTION = 0.01;

  /**
   * Reimplements BOTH axis rules embedded in readObservationWindows' SQL -- continuity
   * clustering, then the density floor over the newest cluster -- so a fixture mirroring
   * production's real day/count distribution can be turned into the row shape the database
   * is documented to hand back, without a database.
   */
  function summarizeObservedDays(sortedDays: readonly ObservedDay[]) {
    if (sortedDays.length === 0) {
      return {
        layer_name: "",
        dense_earliest_day: null,
        clustered_earliest_day: null,
        recorded_earliest_day: null,
        dense_day_count: 0,
        clustered_day_count: 0,
        recorded_day_count: 0,
        density_floor: null,
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
    return {
      layer_name: "",
      dense_earliest_day: denseDays[0]?.[0] ?? null,
      clustered_earliest_day: newestCluster[0]?.[0] ?? null,
      recorded_earliest_day: sortedDays[0][0],
      dense_day_count: denseDays.length,
      clustered_day_count: newestCluster.length,
      recorded_day_count: sortedDays.length,
      density_floor: densityFloor,
    };
  }

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
        dense_day_count: 3,
        clustered_day_count: 3,
        recorded_day_count: 3,
        density_floor: 63,
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
        dense_day_count: 0,
        clustered_day_count: 0,
        recorded_day_count: 0,
        density_floor: null,
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

    expect(dbExecute).toHaveBeenCalledTimes(1);
    expect(second.layers).toEqual(first.layers);
    expect(third.layers).toEqual(first.layers);
  });
});

describe("the capability scan is typed for Postgres, not only for TypeScript", () => {
  /**
   * getSliderCapabilities answered 500 in production with
   * `invalid input syntax for type bigint: "0.01"`, so TimeSliderPanel's query stayed
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
            dense_day_count: 34,
            clustered_day_count: 34,
            recorded_day_count: 34,
            density_floor: 110,
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
                  dense_day_count: 3,
                  clustered_day_count: 15,
                  recorded_day_count: 22,
                  density_floor: 110,
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
    dbExecute.mockResolvedValue([]);
    await getSliderCapabilities();

    const statement = renderSqlText(dbExecute.mock.calls[0]?.[0]);
    expect(statement).toContain("substring(");
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
      queryChain.limit.mockResolvedValue([
        gaugeRow("14105700", -999999, updatedAt),
        // Reverse flow is real at these gauges and reaches -172,000 cfs in production, so the
        // exclusion must compare exactly -- "negative means missing" would erase a measurement.
        gaugeRow("14211720", -15_700, updatedAt),
        gaugeRow("13206000", 42.5, updatedAt),
      ]);

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
      // The live read pages by created_at, which for a past day returns today's rows only.
      expect(queryChain.limit).not.toHaveBeenCalled();

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
        queryChain.limit.mockResolvedValue([
          gaugeRow("13206000", 42.5, `${TODAY}T04:00:00.000-07:00`),
        ]);
        return Promise.all([
          getPublishedStreamflowGauges(VIEWPORT),
          getPublishedStreamflowGauges(VIEWPORT, TODAY),
        ]);
      });

      expect(today).toEqual(omitted);
      expect(today).toHaveLength(1);
      // Today's answer is the query it has always been: no named-day statement is issued.
      expect(dbExecute).not.toHaveBeenCalled();
      expect(queryChain.limit).toHaveBeenCalledTimes(2);
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

    it("still drops a partial observation rather than zero-filling it", async () => {
      const observations = await atServerToday(async () => {
        const partial = weatherRow(-116.2, 43.6, `${PAST_DAY}T18:00:00.000Z`);
        partial.properties.windDirection = null as unknown as number;
        dbExecute.mockResolvedValueOnce([partial]);
        return getPublishedWeatherForBbox(VIEWPORT, PAST_DAY);
      });

      expect(observations).toEqual([]);
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
      // created_at is a "last touched" column the refresh path rewrites, so the live read's
      // floor on it can never answer for a past day.
      expect(queryChain.limit).not.toHaveBeenCalled();
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
