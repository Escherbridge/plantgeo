// @vitest-environment node

import { bench, describe } from "vitest";
import { clusterActionNetworkPoints, type ActionNetworkPoint } from "@/lib/action-network-clustering";
import {
  activityGridToFeatureCollection,
  type ActivityGrid,
} from "@/lib/server/services/community-activity";

/**
 * Ad-hoc stress case (per repo policy: `vitest bench`, never a CI gate): ~100k synthetic
 * features through the same real transforms `tile-processing.bench.ts` exercises at ordinary
 * tile scale. 100k is well past any single MVT tile's feature count -- it stands in for the
 * demand-heatmap worst case (a near-global viewport, no bbox to cut the candidate set down)
 * that `clusterActionNetworkPoints` and `activityGridToFeatureCollection` are the two real
 * functions in the request path for.
 */

const LARGE_FEATURE_COUNT = 100_000;

function buildSyntheticPoints(count: number): ActionNetworkPoint[] {
  const points: ActionNetworkPoint[] = new Array(count);
  // Spread over a wide lon/lat grid rather than one repeated coordinate, so clustering does
  // its real cell-bucketing work instead of collapsing every point into a single accumulator.
  const columns = 400;
  for (let index = 0; index < count; index++) {
    const longitude = -170 + (index % columns) * (340 / columns);
    const latitude = -60 + Math.floor(index / columns) * (120 / Math.ceil(count / columns));
    points[index] = {
      type: "Feature",
      geometry: { type: "Point", coordinates: [longitude, latitude] },
      properties: { voteCount: index % 23, featureCount: 1 + (index % 7) },
    };
  }
  return points;
}

const LARGE_POINTS = buildSyntheticPoints(LARGE_FEATURE_COUNT);

const LARGE_GRID: ActivityGrid = {
  cells: LARGE_POINTS.map((point) => ({
    longitude: point.geometry.coordinates[0],
    latitude: point.geometry.coordinates[1],
    featureCount: point.properties.featureCount,
    voteCount: point.properties.voteCount,
  })),
  cellDegrees: 0.1,
  observedAt: new Date("2026-08-14T00:00:00Z"),
};

/**
 * One-shot `process.memoryUsage()` delta around a hot path, printed via `console.table` at
 * collection time -- deliberately NOT folded into the timed `bench()` samples below, which run
 * their function hundreds of times and would blur one allocation snapshot into GC noise. Run
 * with `--expose-gc` (see `scripts/bench/compare-baseline.mjs`) for a `global.gc()` pass before
 * and after so the delta reflects retained allocation rather than whatever the last minor GC
 * happened to leave behind; falls back to an ungated snapshot when `--expose-gc` is absent.
 */
function reportMemoryDelta(label: string, run: () => unknown): void {
  const gc = (global as { gc?: () => void }).gc;
  gc?.();
  const before = process.memoryUsage();
  run();
  gc?.();
  const after = process.memoryUsage();
  const toMB = (bytes: number) => `${(bytes / 1024 / 1024).toFixed(2)} MB`;
  console.table({
    [label]: {
      heapUsedDelta: toMB(after.heapUsed - before.heapUsed),
      rssDelta: toMB(after.rss - before.rss),
      externalDelta: toMB(after.external - before.external),
    },
  });
}

// Runs once at collection time, the same way a top-level `beforeAll` would but without needing
// a placeholder `bench()` in the same describe purely to make the hook fire.
reportMemoryDelta(
  `clusterActionNetworkPoints over ${LARGE_FEATURE_COUNT.toLocaleString()} points`,
  () => clusterActionNetworkPoints(LARGE_POINTS, 6, {})
);
reportMemoryDelta(
  `activityGridToFeatureCollection over ${LARGE_FEATURE_COUNT.toLocaleString()} cells`,
  () => activityGridToFeatureCollection(LARGE_GRID)
);

describe("clusterActionNetworkPoints (100k-feature stress case)", () => {
  bench(`clusters ${LARGE_FEATURE_COUNT.toLocaleString()} points at zoom 6`, () => {
    clusterActionNetworkPoints(LARGE_POINTS, 6, {});
  });

  bench(`clusters ${LARGE_FEATURE_COUNT.toLocaleString()} points at zoom 12 (finest grid)`, () => {
    clusterActionNetworkPoints(LARGE_POINTS, 12, {});
  });
});

describe("activityGridToFeatureCollection (100k-cell stress case)", () => {
  bench(`builds a FeatureCollection from ${LARGE_FEATURE_COUNT.toLocaleString()} activity cells`, () => {
    activityGridToFeatureCollection(LARGE_GRID);
  });
});
