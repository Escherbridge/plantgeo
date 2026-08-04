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
  getPublishedFireDetections,
  getPublishedStreamflowGauges,
  getSliderCapabilities,
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
