import { beforeEach, describe, expect, it, vi } from "vitest";
import { PgDialect } from "drizzle-orm/pg-core";

vi.mock("@/lib/server/db", () => ({ db: { execute: vi.fn(), transaction: vi.fn() } }));
vi.mock("@/lib/server/services/community-activity", () => ({
  aggregateActivityGrid: vi.fn(),
  activityGridToFeatureCollection: vi.fn(),
}));
vi.mock("@/lib/server/services/raster-catalog", () => ({ getPublishedSoilRasters: vi.fn() }));
vi.mock("@/lib/server/services/land-context", () => ({ readBoundedAoiIntersection: vi.fn() }));
vi.mock("@/lib/server/services/soilgrids", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/server/services/soilgrids")>()),
  getSoilProperties: vi.fn(),
}));

import { db } from "@/lib/server/db";
import { aggregateActivityGrid, activityGridToFeatureCollection } from "@/lib/server/services/community-activity";
import { getPublishedSoilRasters } from "@/lib/server/services/raster-catalog";
import { readBoundedAoiIntersection } from "@/lib/server/services/land-context";
import { getSoilProperties, soilEstimateFromMapped } from "@/lib/server/services/soilgrids";
import { buildRegionalMeasurementFacts } from "@/lib/server/services/regional-measurement-facts";
import { readAppMapEvidence, readBoundedAppMapEvidence, selectionTile } from "@/lib/server/services/regional-map-evidence";

const now = new Date("2026-09-20T12:00:00Z");
const args = {
  surface_name: "interventions", longitude: -116.2, latitude: 43.6, zoom: 13,
  day: "2026-09-20", range_start: "2026-08-20", range_end: "2026-10-20", time_scale: "month",
};

const PH_RASTER = {
  property: "phh2o" as const, unit: "pH", scaleDivisor: 10, valueMin: 3, valueMax: 9, colorRamp: [],
  archiveUrl: "https://example.com/soil.pmtiles", minZoom: 0, maxZoom: 10,
  attribution: "ISRIC", sourceName: "SoilGrids", sourceRelease: "2.0", licenseName: "CC-BY-4.0",
  bounds: [-180, -85, 180, 85] as [number, number, number, number],
};

const MAPPED = {
  "0-5cm": { phh2o: 57, soc: 243, nitrogen: 190, bdod: 121, cec: 182, ocd: 380, clay: 189, sand: 371, silt: 440, cfvo: 98 },
  "5-15cm": { phh2o: 57, soc: 210, nitrogen: 160, bdod: 127, cec: 170, ocd: 340, clay: 195, sand: 380, silt: 425, cfvo: 102 },
  "15-30cm": { phh2o: 58, soc: 180, nitrogen: 130, bdod: 131, cec: 160, ocd: 300, clay: 201, sand: 390, silt: 409, cfvo: 106 },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(db.transaction).mockImplementation(async (callback) => callback(db as never));
  // The flag-off answer: every pre-P5 deployment sees exactly this.
  vi.mocked(getSoilProperties).mockResolvedValue({ state: "unavailable", reason: "reads_disabled" });
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

  it("returns lane values as a labelled model estimate that can never become a measurement fact", async () => {
    vi.mocked(getPublishedSoilRasters).mockResolvedValue([PH_RASTER]);
    vi.mocked(getSoilProperties).mockResolvedValue({
      state: "available", properties: soilEstimateFromMapped(MAPPED, "soilgrids-v2.0/2020-06-02", 140),
    });
    const result = await readAppMapEvidence({ ...args, surface_name: "soil-phh2o" }, now);
    expect(getSoilProperties).toHaveBeenCalledWith(args.latitude, args.longitude);
    expect(result).toMatchObject({
      lanes: [{ selected: {
        state: "model_estimate", basis: "model_estimate", property: "phh2o",
        values: { "0-5cm": 5.7, "5-15cm": 5.7, "15-30cm": 5.8 }, unit: "pH (water)",
        release_id: "soilgrids-v2.0/2020-06-02", distance_m: 140, numeric_values_available: true,
        label: expect.stringContaining("SoilGrids v2.0 250 m model estimate"),
      } }],
      raster_publication: { numeric_values_available: true },
      note: expect.stringContaining("not a soil sample or measurement"),
    });
    expect(JSON.stringify(result)).not.toContain('"state":"published"');
    expect(buildRegionalMeasurementFacts([{ id: "soil-read", source: "soil-phh2o", result }])).toEqual({ facts: [], omittedFacts: 0 });
  });

  it("says no estimate lies within the radius for a masked cell rather than reporting unavailability", async () => {
    vi.mocked(getPublishedSoilRasters).mockResolvedValue([PH_RASTER]);
    vi.mocked(getSoilProperties).mockResolvedValue({ state: "unavailable", reason: "no_cell_within_radius", radiusM: 1000 });
    expect(await readAppMapEvidence({ ...args, surface_name: "soil-phh2o" }, now)).toMatchObject({
      lanes: [{ selected: { state: "no_estimate_within_radius", reason: "no_cell_within_radius", radius_m: 1000 } }],
      raster_publication: { numeric_values_available: false },
    });
  });

  it("keeps a flag-off or unwritten lane as numeric_values_unavailable and names the reason", async () => {
    vi.mocked(getPublishedSoilRasters).mockResolvedValue([PH_RASTER]);
    for (const reason of ["reads_disabled", "lane_never_written", "serving_at_capacity"] as const) {
      vi.mocked(getSoilProperties).mockResolvedValueOnce({ state: "unavailable", reason });
      expect(await readAppMapEvidence({ ...args, surface_name: "soil-phh2o" }, now)).toMatchObject({
        lanes: [{ selected: { state: "numeric_values_unavailable", reason } }],
      });
    }
  });

  it("does not read the lane for a toggle whose raster release is not published", async () => {
    vi.mocked(getPublishedSoilRasters).mockResolvedValue([]);
    expect(await readAppMapEvidence({ ...args, surface_name: "soil-phh2o" }, now)).toMatchObject({
      lanes: [{ selected: { state: "raster_release_not_published" } }],
    });
    expect(getSoilProperties).not.toHaveBeenCalled();
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
