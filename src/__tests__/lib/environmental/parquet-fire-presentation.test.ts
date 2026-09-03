import { describe, expect, it } from "vitest";
import {
  fireDetectionTotals,
  presentParquetFireDetections,
  servingZoomTierForMapZoom,
} from "@/lib/environmental/parquet-fire-presentation";
import type {
  ParquetFireDetectionCell,
  ParquetFireWindow,
  ParquetReaderResult,
} from "@/lib/server/services/parquet-trpc-readers";

function cell(overrides: Partial<ParquetFireDetectionCell> = {}): ParquetFireDetectionCell {
  return {
    longitude: -116.2,
    latitude: 43.6,
    observedDay: "2026-08-28",
    detectionCount: 4,
    frpSum: 120.5,
    frpObservationCount: 4,
    highConfidenceDetectionCount: 2,
    newestObservedAt: "2026-08-28T19:12:00Z",
    ...overrides,
  };
}

function readyWindow(
  cells: ParquetFireDetectionCell[],
  truncated = false
): ParquetReaderResult<ParquetFireWindow> {
  return {
    state: "ready",
    requestedDay: "2026-08-28",
    servedDay: "2026-08-28",
    truncated,
    data: { firstDay: "2026-08-28", lastDay: "2026-08-28", cells, days: [] },
  };
}

/** Every state that is NOT a window of cells; each must present as empty, never as a zero row. */
const TERMINAL_STATES: ParquetReaderResult<ParquetFireWindow>[] = [
  {
    state: "absent",
    requestedDay: "2026-08-28",
    servedDay: "2026-08-28",
    evidence: {
      reason: "source_empty",
      upstreamResponse: "200 with no rows",
      recordedAt: "2026-08-28T02:00:00Z",
      runId: "run-1",
    },
  },
  { state: "not_generated", requestedDay: "2026-08-28", reason: "day_not_written" },
  { state: "not_generated", requestedDay: "2026-08-28", reason: "lane_never_written" },
  {
    state: "upstream_unavailable",
    fault: { kind: "network", message: "connection reset" },
  },
];

describe("presentParquetFireDetections", () => {
  it("draws one point per cell and writes only the cell vocabulary", () => {
    const presented = presentParquetFireDetections(readyWindow([cell()]), 9);

    expect(presented.features).toHaveLength(1);
    const [feature] = presented.features;
    expect(feature.geometry).toEqual({ type: "Point", coordinates: [-116.2, 43.6] });
    // The exact key set: an incident field creeping back in is what this pins. Every
    // expression in FireLayer reads one of these seven and nothing else.
    expect(Object.keys(feature.properties).sort()).toEqual([
      "detectionCount",
      "frpObservationCount",
      "frpSum",
      "highConfidenceDetectionCount",
      "newestObservedAt",
      "observedDay",
      "zoomTier",
    ]);
    expect(feature.properties).toEqual({
      detectionCount: 4,
      frpSum: 120.5,
      frpObservationCount: 4,
      highConfidenceDetectionCount: 2,
      observedDay: "2026-08-28",
      newestObservedAt: "2026-08-28T19:12:00Z",
      zoomTier: 9,
    });
  });

  it("keeps an unreported FRP null rather than coercing it to zero megawatts", () => {
    const presented = presentParquetFireDetections(
      readyWindow([cell({ frpSum: null, frpObservationCount: 0 })]),
      13
    );

    // No reported power is not zero power. `FireLayer` branches on the observation count for
    // exactly this reason, and it can only do so while the null survives presentation.
    expect(presented.features[0].properties.frpSum).toBeNull();
    expect(presented.features[0].properties.frpObservationCount).toBe(0);
  });

  it("labels the cells with the tier they were served at, not one the payload guessed", () => {
    const presented = presentParquetFireDetections(readyWindow([cell(), cell()]), 0);

    expect(presented.features.map((feature) => feature.properties.zoomTier)).toEqual([0, 0]);
  });

  it("names no tier at all rather than a wrong one when the caller could not resolve it", () => {
    const presented = presentParquetFireDetections(readyWindow([cell()]));

    expect(presented.features[0].properties.zoomTier).toBeNull();
  });

  it("draws nothing for every state that is not a served window", () => {
    for (const result of TERMINAL_STATES) {
      expect(presentParquetFireDetections(result, 9).features, result.state).toEqual([]);
    }
    expect(presentParquetFireDetections(undefined, 9).features).toEqual([]);
  });
});

describe("fireDetectionTotals", () => {
  it("counts detections and cells separately, because above z13 they are different numbers", () => {
    const totals = fireDetectionTotals(
      readyWindow([
        cell({ detectionCount: 4, highConfidenceDetectionCount: 2 }),
        cell({ detectionCount: 11, highConfidenceDetectionCount: 0 }),
      ])
    );

    expect(totals).toEqual({
      detectionCount: 15,
      cellCount: 2,
      highConfidenceDetectionCount: 2,
    });
  });

  it("returns zeros only for a served window, leaving every refusal to the caller", () => {
    // The zeros here are indistinguishable from a genuinely empty day BY DESIGN: a caller must
    // read `state` before rendering these numbers, which is why FireDetails switches on it
    // first and prints an em dash for each refusal.
    expect(fireDetectionTotals(readyWindow([]))).toEqual({
      detectionCount: 0,
      cellCount: 0,
      highConfidenceDetectionCount: 0,
    });
    for (const result of TERMINAL_STATES) {
      expect(fireDetectionTotals(result).cellCount, result.state).toBe(0);
    }
  });
});

describe("servingZoomTierForMapZoom", () => {
  it("resolves a map zoom down to the rung that serves it", () => {
    expect(servingZoomTierForMapZoom(3)).toBe(0);
    expect(servingZoomTierForMapZoom(11.4)).toBe(9);
    expect(servingZoomTierForMapZoom(13)).toBe(13);
    expect(servingZoomTierForMapZoom(22)).toBe(13);
  });

  it("names no rung for a zoom no rung serves, instead of throwing through a render", () => {
    // `resolveZoomTier` raises on these, which is right for a server read and wrong inside a
    // React render: the hook disables the query instead, and a thrown error would have blanked
    // the whole map subtree.
    expect(servingZoomTierForMapZoom(Number.NaN)).toBeNull();
    expect(servingZoomTierForMapZoom(-1)).toBeNull();
    expect(servingZoomTierForMapZoom(Number.POSITIVE_INFINITY)).toBeNull();
  });
});
