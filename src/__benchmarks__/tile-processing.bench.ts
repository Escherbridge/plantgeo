// @vitest-environment node

import { bench, describe } from "vitest";
import { clusterActionNetworkPoints, type ActionNetworkPoint } from "@/lib/action-network-clustering";
import { activityGridToFeatureCollection } from "@/lib/server/services/community-activity";
import { haversineDistance, lineStringDistance, polygonArea } from "@/lib/map/measurement";

/**
 * Ad-hoc performance sweep (per repo policy: `vitest bench`, never a CI gate) over the real
 * per-tile-scale hot paths: `clusterActionNetworkPoints` and `activityGridToFeatureCollection`
 * both run once per demand-heatmap tile request (see `src/lib/server/services/community-activity.ts`
 * and `src/app/api/community/activity/route.ts`), and `measurement.ts` runs once per drawn
 * shape while a reader is actively measuring on the map. 2,000 points is a busy but ordinary
 * viewport-scale tile; `large-dataset.bench.ts` is the 100k-feature stress case.
 */

function syntheticPoint(index: number): ActionNetworkPoint {
  // Spread across the Pacific Northwest bbox this repo's own coverage-region default targets,
  // so cell-grouping in `clusterActionNetworkPoints` has realistic density rather than one cell.
  const longitude = -126 + (index % 317) * (16 / 317);
  const latitude = 41 + Math.floor(index / 317) * (9 / 64);
  return {
    type: "Feature",
    geometry: { type: "Point", coordinates: [longitude, latitude] },
    properties: {
      voteCount: index % 11,
      featureCount: 1 + (index % 5),
    },
  };
}

const TILE_SCALE_POINTS: ActionNetworkPoint[] = Array.from({ length: 2000 }, (_, index) =>
  syntheticPoint(index)
);

describe("clusterActionNetworkPoints (per-tile scale)", () => {
  bench("zoom 3 (coarse grid, heavy cell collisions)", () => {
    clusterActionNetworkPoints(TILE_SCALE_POINTS, 3, {});
  });

  bench("zoom 9 (fine grid, near one cluster per point)", () => {
    clusterActionNetworkPoints(TILE_SCALE_POINTS, 9, {});
  });

  bench("zoom 6 with minimum-vote and minimum-feature-count filters applied", () => {
    clusterActionNetworkPoints(TILE_SCALE_POINTS, 6, {
      minimumVotes: 3,
      minimumFeatureCount: 2,
    });
  });
});

describe("activityGridToFeatureCollection (per-tile scale)", () => {
  const grid = {
    cells: TILE_SCALE_POINTS.map((point) => ({
      longitude: point.geometry.coordinates[0],
      latitude: point.geometry.coordinates[1],
      featureCount: point.properties.featureCount,
      voteCount: point.properties.voteCount,
    })),
    cellDegrees: 0.5,
    observedAt: new Date("2026-08-14T00:00:00Z"),
  };

  bench("builds a FeatureCollection from 2,000 activity cells", () => {
    activityGridToFeatureCollection(grid);
  });
});

describe("measurement.ts geometry math (per-shape scale)", () => {
  // A drawn polygon rarely exceeds a few hundred vertices; 500 is a deliberately generous
  // freehand trace.
  const ring: [number, number][] = Array.from({ length: 500 }, (_, index) => {
    const angle = (index / 500) * 2 * Math.PI;
    return [-120 + Math.cos(angle) * 2, 45 + Math.sin(angle) * 2];
  });
  const line: [number, number][] = ring.slice(0, 250);

  bench("polygonArea over a 500-vertex ring", () => {
    polygonArea(ring);
  });

  bench("lineStringDistance over a 250-point line", () => {
    lineStringDistance(line);
  });

  bench("haversineDistance, single pair", () => {
    haversineDistance(ring[0], ring[250]);
  });
});
