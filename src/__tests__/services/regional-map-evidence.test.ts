import { beforeEach, describe, expect, it, vi } from "vitest";
import { PgDialect } from "drizzle-orm/pg-core";

vi.mock("@/lib/server/db", () => ({ db: { execute: vi.fn(), transaction: vi.fn() } }));
vi.mock("@/lib/server/services/community-activity", () => ({
  aggregateActivityGrid: vi.fn(),
  activityGridToFeatureCollection: vi.fn(),
}));
vi.mock("@/lib/server/services/raster-catalog", () => ({ getPublishedSoilRasters: vi.fn() }));
vi.mock("@/lib/server/services/land-context", () => ({ readBoundedAoiIntersection: vi.fn() }));

import { db } from "@/lib/server/db";
import { aggregateActivityGrid, activityGridToFeatureCollection } from "@/lib/server/services/community-activity";
import { getPublishedSoilRasters } from "@/lib/server/services/raster-catalog";
import { readBoundedAoiIntersection } from "@/lib/server/services/land-context";
import { readAppMapEvidence, readBoundedAppMapEvidence, selectionTile } from "@/lib/server/services/regional-map-evidence";

const now = new Date("2026-09-20T12:00:00Z");
const args = {
  surface_name: "interventions", longitude: -116.2, latitude: 43.6, zoom: 13,
  day: "2026-09-20", range_start: "2026-08-20", range_end: "2026-10-20", time_scale: "month",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(db.transaction).mockImplementation(async (callback) => callback(db as never));
});

describe("all application map layers share containing-tile retrieval", () => {
  it.each([[-180, 0], [180, 0], [0, 85], [-116.2, 43.6]])("contains the coordinate at world and tile edges", (lon, lat) => {
    const tile = selectionTile(lon, lat, 13)!;
    expect(lon).toBeGreaterThanOrEqual(tile.bbox[0]);
    expect(lon).toBeLessThanOrEqual(tile.bbox[2]);
    expect(lat).toBeGreaterThanOrEqual(tile.bbox[1]);
    expect(lat).toBeLessThanOrEqual(tile.bbox[3]);
    expect(selectionTile(lon, 90, 13)).toBeNull();
  });

  it("reads exactly the public intervention tile scope and exposes truncation", async () => {
    const rows = Array.from({ length: 51 }, (_, id) => ({ id, properties: { kind: "request" } }));
    vi.mocked(db.execute).mockResolvedValue(rows as unknown as Awaited<ReturnType<typeof db.execute>>);
    const result = await readAppMapEvidence(args, now);
    const statement = vi.mocked(db.execute).mock.calls[1][0];
    if (typeof statement === "string") throw new Error("Expected a parameterized SQL statement");
    const query = new PgDialect().sqlToQuery(statement.getSQL());
    expect(query.sql).toContain("l.name = 'interventions'");
    expect(query.sql).toContain("l.is_public IS TRUE AND f.status = 'published'");
    expect(query.sql).toContain("ST_Intersects");
    expect(query.sql).toContain("'type', f.properties ->> 'type'");
    expect(query.sql).not.toContain("ST_DWithin");
    expect(result).toMatchObject({ lanes: [{ selected: {
      state: "published", served_day: "2026-09-20", truncated: true, access_scope: "public_published_only",
    } }], history: { complete: false, state: "historical_snapshots_not_published" } });
  });

  it("does not answer a past selection with today's public records", async () => {
    const result = await readAppMapEvidence({ ...args, day: "2026-09-01" }, now);
    expect(result).toMatchObject({ lanes: [{ selected: { state: "historical_snapshot_unavailable", features: [] } }] });
    expect(db.execute).not.toHaveBeenCalled();
  });

  it("keeps whole community cells when their centre lies beyond the tile", async () => {
    const feature = { type: "Feature" as const, geometry: { type: "Point" as const, coordinates: [-116.205, 43.605] }, properties: { featureCount: 3, voteCount: 0 } };
    vi.mocked(aggregateActivityGrid).mockResolvedValue({ cells: [], cellDegrees: 0.01, observedAt: now });
    vi.mocked(activityGridToFeatureCollection).mockReturnValue({ type: "FeatureCollection", features: [feature] });
    const result = await readAppMapEvidence({ ...args, surface_name: "demand-heatmap" }, now);
    expect(aggregateActivityGrid).toHaveBeenCalledWith(db, expect.objectContaining({
      boundingBox: selectionTile(args.longitude, args.latitude, args.zoom)!.bbox, minimumFeatureCount: 3,
    }));
    expect(result).toMatchObject({ lanes: [{ selected: { features: [feature], minimum_cell_members: 3 } }] });
  });

  it("does not turn soil raster legends into coordinate measurements", async () => {
    vi.mocked(getPublishedSoilRasters).mockResolvedValue([{
      property: "phh2o", unit: "pH", scaleDivisor: 10, valueMin: 3, valueMax: 9, colorRamp: [],
      archiveUrl: "https://example.com/soil.pmtiles", minZoom: 0, maxZoom: 10,
      attribution: "ISRIC", sourceName: "SoilGrids", sourceRelease: "2.0", licenseName: "CC-BY-4.0",
      bounds: [-180, -85, 180, 85],
    }]);
    const result = await readAppMapEvidence({ ...args, surface_name: "soil-phh2o" }, now);
    expect(result).toMatchObject({
      lanes: [{ selected: { state: "numeric_values_unavailable", features: [], served_day: null } }],
      raster_publication: { sourceRelease: "2.0", numeric_values_available: false, tile: { z: 10 } },
    });
  });

  it("rejects a fabricated calendar date or incompatible window before reading", async () => {
    expect(await readAppMapEvidence({ ...args, day: "2026-02-30" }, now)).toMatchObject({ error: "invalid_selection" });
    expect(await readAppMapEvidence({ ...args, range_end: "2026-08-01" }, now)).toMatchObject({ error: "invalid_selection" });
    expect(db.execute).not.toHaveBeenCalled();
  });

  it("propagates unavailable boundary coverage rather than claiming publication", async () => {
    vi.mocked(readBoundedAoiIntersection).mockResolvedValue({ status: "ok", data: [{
      coverageState: "source_unbound_for_region", sourceFeature: null,
    }] } as Awaited<ReturnType<typeof readBoundedAoiIntersection>>);
    expect(await readAppMapEvidence({ ...args, surface_name: "land-context" }, now)).toMatchObject({
      lanes: [{ selected: { state: "refused", refusal_code: "source_unbound_for_region", features: [] } }],
    });
  });

  it("returns on caller cancellation while an application query is still pending", async () => {
    let finish!: () => void;
    vi.mocked(db.transaction).mockImplementation(() => new Promise((resolve) => { finish = () => resolve([]); }));
    const controller = new AbortController();
    const pending = readBoundedAppMapEvidence({ ...args, surface_name: "soil-phh2o" }, controller.signal);
    const assertion = expect(pending).rejects.toThrow("turn cancelled");
    await vi.waitFor(() => expect(db.transaction).toHaveBeenCalled());
    controller.abort(new Error("turn cancelled"));
    await assertion;
    finish();
  });
});
