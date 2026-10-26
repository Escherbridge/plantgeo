import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: vi.fn() }));

import type { Context } from "@/lib/server/trpc/init";
import { trackingRouter } from "@/lib/server/trpc/routers/tracking";

/**
 * Flow tests for the tracking-admin router, restored 2026-10-26 after being deleted as
 * zero-caller the day before (no UI used it). These exercise the two real admin flows the new
 * `/admin/tracking` page drives: register a fleet asset and read it back, then draw its route
 * history with stop detection; and manage a geofence end to end. `tracking-access.test.ts`
 * (same directory's sibling in `security/`) already covers the non-admin denial path, so these
 * assume an admin session and assert the OBSERVABLE outcome of each step instead.
 */

const ASSET_ID = "11111111-1111-4111-8111-111111111111";
const GEOFENCE_ID = "22222222-2222-4222-8222-222222222222";

type Row = Record<string, unknown>;

/** Chainable drizzle stand-in, mirroring `src/__tests__/trpc/users.test.ts`. */
function createQueryStub(queue: Row[][]): unknown {
  const proxy: unknown = new Proxy(() => {}, {
    get(_target, property) {
      if (property === "then") {
        return (resolve: (rows: Row[]) => unknown) => resolve(queue.shift() ?? []);
      }
      return () => proxy;
    },
  });
  return proxy;
}

/**
 * Unlike `users.test.ts`'s stub, `execute` also drains the queue here: `getRouteHistory` and
 * `getLastPositions` read via `ctx.db.execute(sql...)` (raw SQL, no query builder chain) rather
 * than `select().from()`, so the same ordered queue has to answer both call shapes.
 */
function createScriptedDatabase(script: Row[][]): Context["db"] {
  const queue = script.map((rows) => [...rows]);
  const runner = {
    select: () => createQueryStub(queue),
    insert: () => createQueryStub(queue),
    update: () => createQueryStub(queue),
    delete: () => createQueryStub(queue),
    execute: async () => queue.shift() ?? [],
    transaction: async (callback: (tx: unknown) => Promise<unknown>) => callback(runner),
  };
  return runner as unknown as Context["db"];
}

const ADMIN_SESSION: NonNullable<Context["session"]> = {
  expires: "2099-01-01T00:00:00.000Z",
  user: { id: "admin-1", platformRole: "admin" } as NonNullable<Context["session"]>["user"],
};

function adminCaller(script: Row[][]) {
  return trackingRouter.createCaller({ db: createScriptedDatabase(script), session: ADMIN_SESSION } as Context);
}

describe("tracking: asset lifecycle", () => {
  it("creates an asset, lists it back, and returns its route history with a detected stop", async () => {
    const createdAsset: Row = {
      id: ASSET_ID,
      name: "Crew truck 1",
      type: "vehicle",
      status: "offline",
      metadata: {},
    };
    const farApartPositions: Row[] = [
      {
        asset_id: ASSET_ID,
        time: new Date("2026-10-20T08:00:00.000Z"),
        heading: 90,
        speed: 12,
        altitude: null,
        metadata: { lat: 39.0, lon: -105.0 },
      },
      {
        asset_id: ASSET_ID,
        time: new Date("2026-10-20T08:20:00.000Z"), // 20 min later: past the 5-min stop threshold
        heading: 0,
        speed: 0,
        altitude: null,
        metadata: { lat: 39.0, lon: -105.0 },
      },
    ];

    const caller = adminCaller([
      [createdAsset], // createAsset's .insert().values().returning()
      [createdAsset], // listAssets' .select().from().orderBy()
      farApartPositions, // getRouteHistory's ctx.db.execute(sql...)
    ]);

    const created = await caller.createAsset({ name: "Crew truck 1" });
    expect(created).toMatchObject({ id: ASSET_ID, name: "Crew truck 1" });

    const assets = await caller.listAssets();
    expect(assets).toEqual([createdAsset]);

    const history = await caller.getRouteHistory({
      assetId: ASSET_ID,
      from: "2026-10-20T00:00:00.000Z",
      to: "2026-10-20T23:59:59.000Z",
    });
    expect(history.positions).toEqual(farApartPositions);
    // The load-bearing assertion: detectStops ran server-side over the SAME rows the caller
    // reads, rather than the admin UI having to re-derive it from raw positions client-side.
    expect(history.stops).toHaveLength(1);
    expect(history.stops[0]).toMatchObject({ lat: 39.0, lon: -105.0, durationMs: 20 * 60 * 1000 });
  });
});

describe("tracking: geofence lifecycle", () => {
  it("creates a geofence and then deletes it", async () => {
    const createdGeofence: Row = {
      id: GEOFENCE_ID,
      name: "Operations area",
      geometry: {
        type: "Polygon",
        coordinates: [[[-105, 39], [-104, 39], [-104, 40], [-105, 39]]],
      },
      alertOnEnter: true,
      alertOnExit: true,
    };

    const caller = adminCaller([
      [createdGeofence], // createGeofence's .insert().values().returning()
      [], // deleteGeofence's .delete().where() (result is discarded by the procedure)
    ]);

    const created = await caller.createGeofence({
      name: "Operations area",
      geometry: createdGeofence.geometry as { type: "Polygon"; coordinates: [number, number][][] },
    });
    expect(created).toMatchObject({ id: GEOFENCE_ID, name: "Operations area" });

    const deleted = await caller.deleteGeofence({ id: GEOFENCE_ID });
    expect(deleted).toEqual({ success: true });
  });
});
