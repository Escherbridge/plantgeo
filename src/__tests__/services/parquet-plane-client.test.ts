import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * providerUrl and fetchBoundedJson are the only seams stubbed -- providerUrl so a base URL is
 * resolvable outside production without touching the environment, fetchBoundedJson so no test
 * reaches the network. The error classes, the zod contract and every mapping run for real, which is
 * what lets the fault-taxonomy assertions below mean anything.
 */
vi.mock("@/lib/server/http/bounded-upstream", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/server/http/bounded-upstream")>();
  return {
    ...actual,
    providerUrl: vi.fn(actual.providerUrl),
    fetchBoundedJson: vi.fn(),
  };
});

import { TRPCError } from "@trpc/server";
import {
  fetchBoundedJson,
  providerUrl,
  UpstreamConfigurationError,
  UpstreamHttpError,
  UpstreamTimeoutError,
} from "@/lib/server/http/bounded-upstream";
import { rethrowUpstreamFault } from "@/lib/server/trpc/upstream-fault";
import { resolveZoomTier } from "@/lib/map/zoom-tiers";
import {
  getParquetLatestRelease,
  getParquetLayerDay,
  getParquetLayerDayWindow,
  getParquetWarehouseCoverage,
  ParquetPlaneContractError,
  ParquetPlaneRequestError,
} from "@/lib/server/services/parquet-plane-client";

const mockedProviderUrl = vi.mocked(providerUrl);
const mockedFetch = vi.mocked(fetchBoundedJson);

/** A wire envelope exactly as the service is contracted to serialize it (snake_case). */
function wirePublished(requestedDay: string, servedDay = requestedDay) {
  return {
    state: "published",
    requested_day: requestedDay,
    served_day: servedDay,
    rows: [{ cell_id: "43.1250:-113.6250", value: 0.42 }],
    truncated: false,
  };
}

function wireAbsent(requestedDay: string, servedDay = requestedDay) {
  return {
    state: "governed_absence",
    requested_day: requestedDay,
    served_day: servedDay,
    absence: {
      reason: "USDM published no release this week",
      upstream_response: "200 {}",
      recorded_at: "2026-08-20T06:14:02+00:00",
      run_id: "run-4711",
    },
  };
}

function wireMissing(requestedDay: string) {
  return { state: "day_not_written", requested_day: requestedDay };
}

function requestedUrl(callIndex = 0): URL {
  return mockedFetch.mock.calls[callIndex][0] as URL;
}

function requestedOptions(callIndex = 0) {
  return mockedFetch.mock.calls[callIndex][2];
}

beforeEach(() => {
  mockedProviderUrl.mockReset();
  mockedFetch.mockReset();
  // A fresh URL per call: `endpoint()` mutates `pathname`, so one shared instance would let the
  // second read of a test append its route onto the first one's.
  mockedProviderUrl.mockImplementation(() => new URL("http://agri.internal:8000"));
});

describe("getParquetLayerDay", () => {
  it("addresses the day route with the layer, kind, tier and day", async () => {
    mockedFetch.mockResolvedValue(wirePublished("2026-08-20"));

    const envelope = await getParquetLayerDay({
      layer: "drought-areas",
      day: "2026-08-20",
      zoomTier: 9,
      bbox: "-125,42,-111,49",
    });

    const url = requestedUrl();
    expect(url.pathname).toBe("/api/v1/parquet/day");
    expect(url.searchParams.get("layer")).toBe("drought-areas");
    expect(url.searchParams.get("day")).toBe("2026-08-20");
    expect(url.searchParams.get("bbox")).toBe("-125,42,-111,49");
    // Always sent, never left for the server to default.
    expect(url.searchParams.get("kind")).toBe("observed");
    expect(envelope).toMatchObject({ state: "published", servedDay: "2026-08-20" });
  });

  it("sends the tier as the ladder's own rung, resolved through resolveZoomTier", async () => {
    mockedFetch.mockResolvedValue(wirePublished("2026-08-20"));

    // z11.4 is served by the z9 partitions. A client that sent 11 would request a prefix the
    // writer never wrote and read as an empty day over data that exists.
    await getParquetLayerDay({
      layer: "vegetation",
      day: "2026-08-20",
      zoomTier: resolveZoomTier(11.4),
    });

    expect(requestedUrl().searchParams.get("zoom")).toBe("9");
  });

  it("omits bbox entirely for a layer whose live path has none", async () => {
    mockedFetch.mockResolvedValue(wirePublished("2026-08-20"));

    // fire-detections is served today by `GET /api/fires?date=` with no bbox at all; adding one
    // here would silently shrink that layer's answer.
    await getParquetLayerDay({ layer: "fire-detections", day: "2026-08-20", zoomTier: 13 });

    expect(requestedUrl().searchParams.has("bbox")).toBe(false);
  });

  it("carries a passed-through kind rather than assuming observed", async () => {
    mockedFetch.mockResolvedValue(wirePublished("2026-08-20"));

    await getParquetLayerDay({
      layer: "vegetation",
      day: "2026-08-20",
      zoomTier: 5,
      kind: "forecast",
    });

    expect(requestedUrl().searchParams.get("kind")).toBe("forecast");
  });

  it("reads a day off the wire without a byte of caching", async () => {
    mockedFetch.mockResolvedValue(wirePublished("2026-08-20"));

    await getParquetLayerDay({ layer: "vegetation", day: "2026-08-20", zoomTier: 0 });

    // No `revalidateSeconds` means `cache: "no-store"`: a live-edge day is still being written.
    expect(requestedOptions()).not.toHaveProperty("revalidateSeconds");
  });

  it("refuses a malformed day before it reaches the wire", async () => {
    await expect(
      getParquetLayerDay({ layer: "vegetation", day: "2026-8-1", zoomTier: 9 })
    ).rejects.toBeInstanceOf(ParquetPlaneRequestError);
    expect(mockedFetch).not.toHaveBeenCalled();
  });

  it("refuses an empty layer slug before it reaches the wire", async () => {
    await expect(
      getParquetLayerDay({ layer: "  ", day: "2026-08-20", zoomTier: 9 })
    ).rejects.toBeInstanceOf(ParquetPlaneRequestError);
    expect(mockedFetch).not.toHaveBeenCalled();
  });

  it("maps a governed absence onto the union with its evidence intact", async () => {
    mockedFetch.mockResolvedValue(wireAbsent("2026-08-20"));

    const envelope = await getParquetLayerDay({
      layer: "drought-areas",
      day: "2026-08-20",
      zoomTier: 9,
    });

    expect(envelope).toEqual({
      state: "governed_absence",
      requestedDay: "2026-08-20",
      servedDay: "2026-08-20",
      evidence: {
        reason: "USDM published no release this week",
        upstreamResponse: "200 {}",
        recordedAt: "2026-08-20T06:14:02+00:00",
        runId: "run-4711",
      },
    });
  });

  it("rejects a state outside the four published ones", async () => {
    // `conflict` is a real warehouse status (both a part file and a marker) that this union
    // deliberately does not carry; the service must resolve it before answering.
    mockedFetch.mockResolvedValue({ state: "conflict", requested_day: "2026-08-20" });

    await expect(
      getParquetLayerDay({ layer: "drought-areas", day: "2026-08-20", zoomTier: 9 })
    ).rejects.toBeInstanceOf(ParquetPlaneContractError);
  });
});

describe("getParquetLayerDayWindow", () => {
  it("addresses the window route with both ends of the closed range", async () => {
    mockedFetch.mockResolvedValue({
      days: [wirePublished("2026-08-18"), wireAbsent("2026-08-19"), wireMissing("2026-08-20")],
    });

    const days = await getParquetLayerDayWindow({
      layer: "vegetation",
      firstDay: "2026-08-18",
      lastDay: "2026-08-20",
      zoomTier: 9,
    });

    const url = requestedUrl();
    expect(url.pathname).toBe("/api/v1/parquet/window");
    expect(url.searchParams.get("first_day")).toBe("2026-08-18");
    expect(url.searchParams.get("last_day")).toBe("2026-08-20");
    // The three states stay three answers; a window is exactly where merging would erase them.
    expect(days.map((day) => day.state)).toEqual([
      "published",
      "governed_absence",
      "day_not_written",
    ]);
  });

  it("refuses a window that runs backwards", async () => {
    await expect(
      getParquetLayerDayWindow({
        layer: "vegetation",
        firstDay: "2026-08-20",
        lastDay: "2026-08-18",
        zoomTier: 9,
      })
    ).rejects.toBeInstanceOf(ParquetPlaneRequestError);
    expect(mockedFetch).not.toHaveBeenCalled();
  });

  it("refuses an answer that stops short of the range it was asked for", async () => {
    // The dropped day would otherwise read as a day nothing is wrong with.
    mockedFetch.mockResolvedValue({
      days: [wirePublished("2026-08-18"), wirePublished("2026-08-19")],
    });

    await expect(
      getParquetLayerDayWindow({
        layer: "vegetation",
        firstDay: "2026-08-18",
        lastDay: "2026-08-20",
        zoomTier: 9,
      })
    ).rejects.toBeInstanceOf(ParquetPlaneContractError);
  });

  it("refuses an answer that omits an interior day while preserving both endpoints", async () => {
    mockedFetch.mockResolvedValue({
      days: [wirePublished("2026-08-18"), wirePublished("2026-08-20")],
    });

    await expect(
      getParquetLayerDayWindow({
        layer: "vegetation",
        firstDay: "2026-08-18",
        lastDay: "2026-08-20",
        zoomTier: 9,
      })
    ).rejects.toThrow("2026-08-19 was required");
  });

  it("refuses a repeated or misordered day", async () => {
    mockedFetch.mockResolvedValue({
      days: [wirePublished("2026-08-18"), wirePublished("2026-08-18"), wirePublished("2026-08-20")],
    });

    await expect(
      getParquetLayerDayWindow({
        layer: "vegetation",
        firstDay: "2026-08-18",
        lastDay: "2026-08-20",
        zoomTier: 9,
      })
    ).rejects.toBeInstanceOf(ParquetPlaneContractError);
  });

  it("refuses an empty window answer", async () => {
    mockedFetch.mockResolvedValue({ days: [] });

    await expect(
      getParquetLayerDayWindow({
        layer: "vegetation",
        firstDay: "2026-08-18",
        lastDay: "2026-08-20",
        zoomTier: 9,
      })
    ).rejects.toBeInstanceOf(ParquetPlaneContractError);
  });
});

describe("getParquetLatestRelease", () => {
  it("addresses the release route with the as-of day and reports the release's own day", async () => {
    mockedFetch.mockResolvedValue(wirePublished("2026-08-20", "2026-08-14"));

    const envelope = await getParquetLatestRelease({
      layer: "drought-areas",
      asOfDay: "2026-08-20",
      zoomTier: 9,
    });

    const url = requestedUrl();
    expect(url.pathname).toBe("/api/v1/parquet/release");
    expect(url.searchParams.get("as_of")).toBe("2026-08-20");
    expect(envelope).toMatchObject({
      state: "published",
      requestedDay: "2026-08-20",
      servedDay: "2026-08-14",
    });
  });

  it("refuses a malformed as-of day before it reaches the wire", async () => {
    await expect(
      getParquetLatestRelease({ layer: "drought-areas", asOfDay: "20260820", zoomTier: 9 })
    ).rejects.toBeInstanceOf(ParquetPlaneRequestError);
    expect(mockedFetch).not.toHaveBeenCalled();
  });
});

describe("getParquetWarehouseCoverage", () => {
  const GENERATION_SHA256 = "a".repeat(64);

  const census = {
    coverage_schema_version: 2,
    generated_at: "2026-08-23T04:00:00+00:00",
    evaluated_through_day: "2026-08-23",
    lanes: [
      {
        layer: "drought-areas",
        nature: "release_series",
        kind: "observed",
        zoom: 9,
        earliest_day: "2022-08-04",
        latest_day: "2026-08-14",
        published_ranges: [{ from: "2022-08-04", to: "2026-08-14" }],
        gap_ranges: [{ from: "2024-01-04", to: "2024-01-11" }],
        governed_absence_ranges: [{ from: "2025-12-25", to: "2025-12-25" }],
        coverage_authority: "availability",
        availability_generation_sha256: GENERATION_SHA256,
        availability_pointer_key: "availability/drought-areas/_LATEST.json",
        source_ceiling_day: "2026-08-14",
        required_rungs: [0, 5, 9, 13],
        withheld_reason: null,
      },
      {
        layer: "interventions",
        nature: "daily_series",
        kind: "observed",
        zoom: 9,
        earliest_day: null,
        latest_day: null,
        published_ranges: [],
        gap_ranges: [],
        governed_absence_ranges: [],
        coverage_authority: "census",
        availability_generation_sha256: null,
        availability_pointer_key: null,
        source_ceiling_day: null,
        required_rungs: [],
        withheld_reason: null,
      },
    ],
  };

  it("asks for the whole warehouse: no bbox, no zoom, no layer", async () => {
    mockedFetch.mockResolvedValue(census);

    await getParquetWarehouseCoverage();

    const url = requestedUrl();
    expect(url.pathname).toBe("/api/v1/parquet/coverage");
    // Settled: either axis would fragment the shared cache into one entry per viewport.
    expect(url.searchParams.has("bbox")).toBe(false);
    expect(url.searchParams.has("zoom")).toBe(false);
    expect(url.searchParams.has("layer")).toBe(false);
  });

  it("is memoized inside the agreed 5-30 minute band, unlike every viewport read", async () => {
    mockedFetch.mockResolvedValue(census);

    await getParquetWarehouseCoverage();

    const revalidateSeconds = requestedOptions()?.revalidateSeconds;
    expect(revalidateSeconds).toBeGreaterThanOrEqual(300);
    expect(revalidateSeconds).toBeLessThanOrEqual(1_800);
  });

  it("uses a short public boot budget and single-flights concurrent cold coverage reads", async () => {
    let resolveFetch!: (value: unknown) => void;
    mockedFetch.mockReturnValue(
      new Promise((resolve) => {
        resolveFetch = resolve;
      })
    );

    const first = getParquetWarehouseCoverage();
    const second = getParquetWarehouseCoverage();

    expect(mockedFetch).toHaveBeenCalledTimes(1);
    expect(requestedOptions()?.timeoutMs).toBe(8_000);
    resolveFetch(census);
    await expect(Promise.all([first, second])).resolves.toEqual([
      expect.any(Object),
      expect.any(Object),
    ]);
  });

  it("keeps a never-drained lane's nulls rather than inventing a span for it", async () => {
    mockedFetch.mockResolvedValue(census);

    const coverage = await getParquetWarehouseCoverage();

    expect(coverage.coverageSchemaVersion).toBe(2);
    expect(coverage.generatedAt).toBe("2026-08-23T04:00:00+00:00");
    expect(coverage.evaluatedThroughDay).toBe("2026-08-23");
    expect(coverage.lanes[0]).toEqual({
      layer: "drought-areas",
      nature: "release_series",
      kind: "observed",
      zoomTier: 9,
      earliestDay: "2022-08-04",
      latestDay: "2026-08-14",
      publishedRanges: [{ from: "2022-08-04", to: "2026-08-14" }],
      gapRanges: [{ from: "2024-01-04", to: "2024-01-11" }],
      governedAbsenceRanges: [{ from: "2025-12-25", to: "2025-12-25" }],
      coverageAuthority: "availability",
      availabilityGenerationSha256: GENERATION_SHA256,
      availabilityPointerKey: "availability/drought-areas/_LATEST.json",
      sourceCeilingDay: "2026-08-14",
      requiredRungs: [0, 5, 9, 13],
      withheldReason: null,
    });
    expect(coverage.lanes[1]).toMatchObject({ earliestDay: null, latestDay: null });
  });

  /**
   * A lane that walked the object store must not arrive looking like one that read the published
   * index. The two are different strengths of claim; collapsing them would let a slider caption a
   * listing as though it were a checksummed generation.
   */
  it("keeps a census-authority lane distinguishable from an availability-backed one", async () => {
    mockedFetch.mockResolvedValue(census);

    const coverage = await getParquetWarehouseCoverage();

    expect(coverage.lanes[1]).toMatchObject({
      coverageAuthority: "census",
      availabilityGenerationSha256: null,
      availabilityPointerKey: null,
      requiredRungs: [],
    });
  });

  it("carries every withheld reason the availability index can report", async () => {
    for (const reason of [
      "availability_unpublished",
      "availability_stale",
      "availability_malformed",
      "availability_checksum_invalid",
    ] as const) {
      mockedFetch.mockResolvedValue({
        ...census,
        lanes: [{ ...census.lanes[0], withheld_reason: reason }],
      });

      const coverage = await getParquetWarehouseCoverage();

      expect(coverage.lanes[0].withheldReason).toBe(reason);
    }
  });

  it("rejects a lane nature outside the three the warehouse defines", async () => {
    mockedFetch.mockResolvedValue({
      ...census,
      lanes: [{ ...census.lanes[0], nature: "weekly_series" }],
    });

    await expect(getParquetWarehouseCoverage()).rejects.toBeInstanceOf(ParquetPlaneContractError);
  });

  it("rejects legacy tier-agnostic coverage instead of letting one rung stand in for four", async () => {
    const { zoom: _zoom, ...tierAgnosticLane } = census.lanes[0];
    mockedFetch.mockResolvedValue({ ...census, lanes: [tierAgnosticLane] });

    await expect(getParquetWarehouseCoverage()).rejects.toBeInstanceOf(ParquetPlaneContractError);
  });

  /**
   * A version-1 body carries no `withheld_reason` at all. Accepting it would read that silence as
   * "every lane's availability index is healthy" -- the exact fail-open the field exists to close.
   */
  it("refuses a coverage body written to a schema version this client cannot read", async () => {
    mockedFetch.mockResolvedValue({ ...census, coverage_schema_version: 1 });

    // Named in the message, because "the census does not match the contract" and "the service is
    // still on version 1" are different pages for whoever is holding the deploy.
    await expect(getParquetWarehouseCoverage()).rejects.toBeInstanceOf(ParquetPlaneContractError);
    await expect(getParquetWarehouseCoverage()).rejects.toThrow(/version 1/);
  });

  it("refuses a coverage body that names no schema version at all", async () => {
    const { coverage_schema_version: _version, ...unversioned } = census;
    mockedFetch.mockResolvedValue(unversioned);

    await expect(getParquetWarehouseCoverage()).rejects.toBeInstanceOf(ParquetPlaneContractError);
  });

  /**
   * Fail closed on the WIRE, not just at the capability gate. An omitted `withheld_reason` read as
   * "healthy" would turn a serving side that has not been redeployed yet into a silent claim that
   * every lane's availability index is fine.
   */
  it.each([
    "coverage_authority",
    "availability_generation_sha256",
    "availability_pointer_key",
    "source_ceiling_day",
    "required_rungs",
    "withheld_reason",
  ])("rejects a coverage lane that omits %s rather than defaulting it", async (field) => {
    const { [field]: _omitted, ...incompleteLane } = census.lanes[0] as Record<string, unknown>;
    mockedFetch.mockResolvedValue({ ...census, lanes: [incompleteLane] });

    await expect(getParquetWarehouseCoverage()).rejects.toBeInstanceOf(ParquetPlaneContractError);
  });

  it("rejects a required rung the ladder does not publish", async () => {
    mockedFetch.mockResolvedValue({
      ...census,
      lanes: [{ ...census.lanes[0], required_rungs: [0, 5, 9, 11] }],
    });

    await expect(getParquetWarehouseCoverage()).rejects.toBeInstanceOf(ParquetPlaneContractError);
  });

  /**
   * One caller's cancellation must never abort a read the other callers are already awaiting:
   * this answer is single-flighted and shared by every viewport in every session.
   */
  it("sends no caller signal, because every viewport shares this one in-flight read", async () => {
    mockedFetch.mockResolvedValue(census);

    await getParquetWarehouseCoverage();

    expect(requestedOptions()?.signal).toBeUndefined();
  });
});

/**
 * Cancellation reaches the socket or it does nothing. A signal accepted on the request and then
 * dropped before `fetchBounded` is the worst of both: the caller believes the read was abandoned
 * while the upstream keeps working, which is exactly the load the abort exists to shed.
 */
describe("request cancellation", () => {
  it("threads the caller's signal into every row read, and never into a query parameter", async () => {
    const controller = new AbortController();
    mockedFetch.mockResolvedValue(wirePublished("2026-08-20"));

    await getParquetLayerDay({
      layer: "vegetation",
      day: "2026-08-20",
      zoomTier: 9,
      signal: controller.signal,
    });

    expect(requestedOptions()?.signal).toBe(controller.signal);
    expect(requestedUrl().searchParams.has("signal")).toBe(false);
  });

  it("threads the caller's signal through the window read", async () => {
    const controller = new AbortController();
    mockedFetch.mockResolvedValue({
      days: [wirePublished("2026-08-20"), wirePublished("2026-08-21")],
    });

    await getParquetLayerDayWindow({
      layer: "vegetation",
      firstDay: "2026-08-20",
      lastDay: "2026-08-21",
      zoomTier: 9,
      signal: controller.signal,
    });

    expect(requestedOptions()?.signal).toBe(controller.signal);
  });

  it("threads the caller's signal through the release read", async () => {
    const controller = new AbortController();
    mockedFetch.mockResolvedValue(wirePublished("2026-08-24", "2026-08-18"));

    await getParquetLatestRelease({
      layer: "drought-areas",
      asOfDay: "2026-08-24",
      zoomTier: 9,
      signal: controller.signal,
    });

    expect(requestedOptions()?.signal).toBe(controller.signal);
  });

  it("omits the option entirely for a caller with nothing to cancel", async () => {
    mockedFetch.mockResolvedValue(wirePublished("2026-08-20"));

    await getParquetLayerDay({ layer: "vegetation", day: "2026-08-20", zoomTier: 9 });

    expect(requestedOptions()?.signal).toBeUndefined();
  });
});

describe("fault taxonomy", () => {
  /**
   * A transport failure must never arrive as an envelope. `day_not_written` is a positive claim
   * that the warehouse holds nothing for a day; a 503 saying that would turn an outage into a
   * statement of fact, which is the confusion `MetricAtDateAvailability.request_failed` documents.
   */
  it("propagates a 5xx unchanged, and rethrowUpstreamFault makes it retryable", async () => {
    mockedFetch.mockRejectedValue(new UpstreamHttpError(503));

    const failure = await getParquetLayerDay({
      layer: "vegetation",
      day: "2026-08-20",
      zoomTier: 9,
    }).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(UpstreamHttpError);
    expect(() => rethrowUpstreamFault(failure, "The Parquet plane")).toThrow(TRPCError);
    try {
      rethrowUpstreamFault(failure, "The Parquet plane");
    } catch (error) {
      expect((error as TRPCError).code).toBe("SERVICE_UNAVAILABLE");
    }
  });

  it("propagates a timeout unchanged", async () => {
    mockedFetch.mockRejectedValue(new UpstreamTimeoutError("Upstream request timed out"));

    await expect(
      getParquetLayerDay({ layer: "vegetation", day: "2026-08-20", zoomTier: 9 })
    ).rejects.toBeInstanceOf(UpstreamTimeoutError);
  });

  it("lets an unconfigured base URL throw instead of answering as an empty warehouse", async () => {
    mockedProviderUrl.mockImplementation(() => {
      throw new UpstreamConfigurationError("AGRI_PARQUET_SERVICE_URL is not configured");
    });

    await expect(
      getParquetLayerDay({ layer: "vegetation", day: "2026-08-20", zoomTier: 9 })
    ).rejects.toBeInstanceOf(UpstreamConfigurationError);
    expect(mockedFetch).not.toHaveBeenCalled();
  });

  it("keeps a contract mismatch out of the retryable class", () => {
    // A drifted payload cannot heal by retrying; only a deploy fixes it, so it must not be
    // relabelled SERVICE_UNAVAILABLE the way a timeout or a 5xx is.
    const mismatch = new ParquetPlaneContractError("drifted");
    expect(() => rethrowUpstreamFault(mismatch, "The Parquet plane")).toThrow(mismatch);
    expect(() => rethrowUpstreamFault(mismatch, "The Parquet plane")).not.toThrow(TRPCError);
  });
});

/**
 * The day-shift tripwire.
 *
 * `PUBLISHER_NAMED_DAY_RULE` (environmental-read-model.ts) records that 37.5% of the stored
 * water-gauge rows carry a `-07:00` offset, so ONE instant-based conversion moves 6,279 of 16,743
 * of them onto the following calendar day -- a map that renders perfectly and is wrong. Both
 * modules therefore never convert a publisher day to an instant. Window adjacency may increment
 * the numeric calendar fields, but must never involve timezone-sensitive APIs. This check is
 * textual, like the migration/read-model agreement check in
 * src/__tests__/lib/observation-day-contract.test.ts, because the property it defends is the
 * ABSENCE of a call: no runtime assertion can prove a conversion was never introduced.
 */
describe("day strings stay opaque", () => {
  const MODULE_PATHS = [
    "src/lib/server/services/parquet-plane-client.ts",
    "src/lib/server/services/parquet-envelope.ts",
  ];

  const FORBIDDEN_CONVERSIONS = [
    "new Date(",
    "Date.parse(",
    "Date.UTC(",
    ".toISOString(",
    ".getTime(",
    ".getTimezoneOffset(",
    ".toLocale",
    "AT TIME ZONE",
  ];

  it.each(MODULE_PATHS)("never converts a day in %s", (modulePath) => {
    const source = readFileSync(modulePath, "utf8");
    for (const conversion of FORBIDDEN_CONVERSIONS) {
      expect(source, `${modulePath} must not call ${conversion}`).not.toContain(conversion);
    }
  });

  it("checks the calendar day by shape only, never by parsing it", async () => {
    // A well-shaped day that does not exist is still passed through: refusing it would require
    // a calendar, and the server owns the calendar. What is refused is a day that is not ten
    // characters of `YYYY-MM-DD`, which is the only claim a shape check can honestly make.
    mockedFetch.mockResolvedValue(wirePublished("2026-02-30"));

    const envelope = await getParquetLayerDay({
      layer: "vegetation",
      day: "2026-02-30",
      zoomTier: 9,
    });

    expect(requestedUrl().searchParams.get("day")).toBe("2026-02-30");
    expect(envelope.requestedDay).toBe("2026-02-30");
  });
});

/**
 * The frozen wire contract, read from the SAME golden fixtures the serving side asserts against in
 * `services/agri-data-service/tests/contract/`. Hand-written payloads above prove this client's
 * behaviour; these prove both sides still mean the same thing by the same bytes. If a fixture and
 * this client disagree, one of the two lanes has drifted and the other has not noticed yet.
 */
describe("the frozen wire contract", () => {
  const FIXTURES = "services/agri-data-service/tests/contract/fixtures";

  function fixture(name: string): unknown {
    return JSON.parse(readFileSync(`${FIXTURES}/${name}.json`, "utf8"));
  }

  async function decodeDay(name: string) {
    mockedFetch.mockResolvedValue(fixture(name));
    return getParquetLayerDay({ layer: "vegetation", day: "2026-08-06", zoomTier: 9 });
  }

  it("maps a published day into the union's published arm", async () => {
    expect(await decodeDay("day_published")).toEqual({
      state: "published",
      requestedDay: "2026-08-06",
      servedDay: "2026-08-06",
      rows: [
        { cell_id: 4127, cell_longitude: -116.2023, cell_latitude: 43.615, normalized_value: 0.412 },
        { cell_id: 4128, cell_longitude: -116.1891, cell_latitude: 43.615, normalized_value: 0.389 },
      ],
      truncated: false,
    });
  });

  it("carries the row-budget bit through, so a subset is never read as a whole day", async () => {
    expect(await decodeDay("day_published_truncated")).toMatchObject({ truncated: true });
  });

  it("renames every absence field into this codebase's spelling", async () => {
    expect(await decodeDay("day_governed_absence")).toEqual({
      state: "governed_absence",
      requestedDay: "2026-08-09",
      servedDay: "2026-08-09",
      evidence: {
        reason: "upstream published no scenes for this day",
        upstreamResponse: "HTTP 200, features: []",
        recordedAt: "2026-08-10T04:12:57Z",
        runId: "ingest-vegetation:9f3c1e40-2b77-4a51-9d0e-6c8b21ad5f13",
      },
    });
  });

  it("keeps day_not_written and lane_never_written distinct", async () => {
    // They license different sentences: a gap in a record that exists, versus no record at all.
    expect(await decodeDay("day_not_written")).toEqual({
      state: "day_not_written",
      requestedDay: "2026-08-11",
    });
    expect(await decodeDay("day_lane_never_written")).toEqual({
      state: "lane_never_written",
      requestedDay: "2026-08-06",
    });
  });

  it("reports a carried-forward release at its own day, never as fresher than it is", async () => {
    mockedFetch.mockResolvedValue(fixture("release_carry_forward"));

    const envelope = await getParquetLatestRelease({
      layer: "drought-areas",
      asOfDay: "2026-08-24",
      zoomTier: 9,
    });

    expect(envelope).toMatchObject({
      state: "published",
      requestedDay: "2026-08-24",
      servedDay: "2026-08-18",
    });
  });

  it("states an absent release at the marker's own day too", async () => {
    mockedFetch.mockResolvedValue(fixture("release_governed_absence"));

    const envelope = await getParquetLatestRelease({
      layer: "drought-areas",
      asOfDay: "2026-08-24",
      zoomTier: 9,
    });

    expect(envelope).toMatchObject({ state: "governed_absence", servedDay: "2026-08-18" });
  });

  it("accepts the window fixture as a complete closed range", async () => {
    mockedFetch.mockResolvedValue(fixture("window"));

    const days = await getParquetLayerDayWindow({
      layer: "signal",
      firstDay: "2026-08-06",
      lastDay: "2026-08-09",
      zoomTier: 9,
    });

    // Four days, ascending, with the gap STATED rather than omitted -- WIRE assumption 6.
    expect(days.map((day) => day.requestedDay)).toEqual([
      "2026-08-06",
      "2026-08-07",
      "2026-08-08",
      "2026-08-09",
    ]);
    expect(days.map((day) => day.state)).toEqual([
      "published",
      "day_not_written",
      "governed_absence",
      "day_not_written",
    ]);
  });

  it("maps the coverage census, keeping a never-written lane's bounds null", async () => {
    mockedFetch.mockResolvedValue(fixture("coverage"));

    const coverage = await getParquetWarehouseCoverage();
    const lane = (layer: string, zoomTier: number) =>
      coverage.lanes.find((entry) => entry.layer === layer && entry.zoomTier === zoomTier);

    expect(coverage.generatedAt).toBe("2026-08-25T04:00:00Z");
    expect(coverage.evaluatedThroughDay).toBe("2026-08-25");
    // soil-survey has 238,986 source rows and 0 written; the census must say so, not guess a day.
    expect(lane("soil-survey", 13)).toMatchObject({
      nature: "static_lookup",
      earliestDay: null,
      latestDay: null,
    });
    expect(lane("signal", 13)).toMatchObject({
      nature: "daily_series",
      zoomTier: 13,
      earliestDay: "2022-04-30",
      latestDay: "2026-08-06",
      publishedRanges: [{ from: "2022-04-30", to: "2026-08-06" }],
      governedAbsenceRanges: [{ from: "2026-08-07", to: "2026-08-16" }],
    });
  });

  /**
   * The golden bytes both suites read carry the six provenance fields, and the census-authority
   * shape they encode -- nulls and an empty `required_rungs` -- is the one an object-store walk
   * produces. Asserting it here is what proves the two languages mean the same thing by it.
   */
  it("carries the coverage provenance the serving side declares in the same bytes", () => {
    const raw = fixture("coverage") as {
      coverage_schema_version: number;
      lanes: readonly Record<string, unknown>[];
    };

    expect(raw.coverage_schema_version).toBe(2);
    for (const lane of raw.lanes) {
      expect(lane).toMatchObject({
        coverage_authority: "census",
        availability_generation_sha256: null,
        availability_pointer_key: null,
        required_rungs: [],
        withheld_reason: null,
      });
    }
  });

  it("decodes the fixture's census-authority lanes without inventing an index generation", async () => {
    mockedFetch.mockResolvedValue(fixture("coverage"));

    const coverage = await getParquetWarehouseCoverage();

    expect(coverage.coverageSchemaVersion).toBe(2);
    expect(coverage.lanes.every((lane) => lane.coverageAuthority === "census")).toBe(true);
    expect(coverage.lanes.every((lane) => lane.availabilityGenerationSha256 === null)).toBe(true);
    expect(coverage.lanes.every((lane) => lane.withheldReason === null)).toBe(true);
  });

  /**
   * The withheld-lane golden bytes. Both halves matter: an index-backed lane naming the generation
   * and pointer an operator can fetch, and a lane whose index was never published at all -- which
   * carries a pointer key it looked for, a null generation because there was nothing to name, and
   * empty bounds it must never be described from.
   */
  it("decodes an availability-backed census and the lane that withheld itself inside it", async () => {
    mockedFetch.mockResolvedValue(fixture("coverage_availability"));

    const coverage = await getParquetWarehouseCoverage();
    const lane = (layer: string, zoomTier: number) =>
      coverage.lanes.find((entry) => entry.layer === layer && entry.zoomTier === zoomTier);

    expect(lane("signal", 13)).toMatchObject({
      coverageAuthority: "availability",
      availabilityGenerationSha256:
        "9b1f0c4d2a7e63518c0dfb2e94a7150c3d6b8e2f41905ac7db3e6f82c150a4d7",
      availabilityPointerKey: "layer=signal/kind=observed/availability/_LATEST.json",
      // The lane's OWN horizon, days ahead of its newest written day and weeks behind the census
      // day: the gap between the two is owed, everything after it is simply not published yet.
      sourceCeilingDay: "2026-08-07",
      latestDay: "2026-08-05",
      requiredRungs: [0, 5, 9, 13],
      withheldReason: null,
    });
    expect(coverage.evaluatedThroughDay).toBe("2026-08-25");

    expect(lane("burn-severity", 13)).toMatchObject({
      withheldReason: "availability_unpublished",
      // A pointer key it looked for, and no generation because there was nothing to name.
      availabilityPointerKey: "layer=burn-severity/kind=observed/availability/_LATEST.json",
      availabilityGenerationSha256: null,
      earliestDay: null,
      latestDay: null,
      requiredRungs: [],
    });
  });
});
