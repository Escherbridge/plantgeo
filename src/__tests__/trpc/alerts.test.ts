import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: vi.fn() }));

import type { Context } from "@/lib/server/trpc/init";
import { alertsRouter } from "@/lib/server/trpc/routers/alerts";

const USER_ID = "22222222-2222-4222-8222-222222222222";
const ALERT_ID = "66666666-6666-4666-8666-666666666666";
const LOCATION_ID = "77777777-7777-4777-8777-777777777777";

type Row = Record<string, unknown>;
type BuilderCall = { method: string; args: unknown[] };

/** Chainable drizzle stand-in, mirroring `src/__tests__/trpc/interventions.test.ts`. */
function createQueryStub(queue: Row[][], calls: BuilderCall[]): unknown {
  const proxy: unknown = new Proxy(() => {}, {
    get(_target, property) {
      if (property === "then") {
        return (resolve: (rows: Row[]) => unknown) => resolve(queue.shift() ?? []);
      }
      return (...args: unknown[]) => {
        calls.push({ method: String(property), args });
        return proxy;
      };
    },
  });
  return proxy;
}

function createScriptedDatabase(script: Row[][], calls: BuilderCall[] = []): Context["db"] {
  const queue = script.map((rows) => [...rows]);
  const runner = {
    select: () => createQueryStub(queue, calls),
    insert: () => createQueryStub(queue, calls),
    update: () => createQueryStub(queue, calls),
    delete: () => createQueryStub(queue, calls),
    execute: async () => [],
  };
  return runner as unknown as Context["db"];
}

function sessionFor(userId: string | undefined): NonNullable<Context["session"]> {
  return {
    expires: "2099-01-01T00:00:00.000Z",
    user: (userId === undefined ? {} : { id: userId }) as NonNullable<
      Context["session"]
    >["user"],
  };
}

function callerWith(script: Row[][], session: Context["session"] | null, calls: BuilderCall[] = []) {
  return alertsRouter.createCaller({
    db: createScriptedDatabase(script, calls),
    session,
  } as Context);
}

/**
 * Every string reachable from a recorded builder argument, mirroring
 * `intervention-social.test.ts` -- drizzle's `SQL` carries `Column` instances (whose `name` is
 * the physical column) and `StringChunk`s (the literal operator text), so a predicate built
 * from `eq(environmentalAlerts.userId, userId)` is observable without a database.
 */
function reachableStrings(value: unknown, seen = new WeakSet<object>()): string[] {
  if (typeof value === "string") return [value];
  if (typeof value !== "object" || value === null) return [];
  if (seen.has(value)) return [];
  seen.add(value);
  return Object.values(value as Record<string, unknown>).flatMap((child) =>
    reachableStrings(child, seen)
  );
}

function predicateStrings(calls: BuilderCall[]): string[] {
  return calls
    .filter((call) => call.method === "where")
    .flatMap((call) => call.args.flatMap((arg) => reachableStrings(arg)));
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("alerts.getUnreadCount", () => {
  it("rejects an unauthenticated caller before ever querying the database", async () => {
    const calls: BuilderCall[] = [];
    const caller = callerWith([], null, calls);

    await expect(caller.getUnreadCount()).rejects.toMatchObject({ code: "UNAUTHORIZED" });
    expect(calls).toHaveLength(0);
  });

  it("reports the signed-in caller's own unread count", async () => {
    const caller = callerWith([[{ count: 3 }]], sessionFor(USER_ID));

    await expect(caller.getUnreadCount()).resolves.toBe(3);
  });

  it("answers 0 rather than throwing when the session carries no user id", async () => {
    const caller = callerWith([], sessionFor(undefined));

    await expect(caller.getUnreadCount()).resolves.toBe(0);
  });
});

describe("alerts.markRead", () => {
  it("scopes the update to the caller's own alert, not any alert id a guesser might send", async () => {
    const calls: BuilderCall[] = [];
    const caller = callerWith([], sessionFor(USER_ID), calls);

    await caller.markRead({ alertId: ALERT_ID });

    const setCall = calls.find((call) => call.method === "set");
    expect(setCall?.args[0]).toMatchObject({ isRead: true });
    // Not merely "a where clause exists": the predicate must actually carry BOTH the
    // requested alert id and the caller's own user id, or a signed-in reader could flip
    // read state on an alert that belongs to someone else.
    const predicates = predicateStrings(calls);
    expect(predicates).toContain(ALERT_ID);
    expect(predicates).toContain(USER_ID);
  });
});

describe("alerts.markAllRead", () => {
  it("marks every one of the caller's own unread alerts read in one statement", async () => {
    const calls: BuilderCall[] = [];
    const caller = callerWith([], sessionFor(USER_ID), calls);

    await caller.markAllRead();

    const setCall = calls.find((call) => call.method === "set");
    expect(setCall?.args[0]).toMatchObject({ isRead: true });
    // Scoped to this caller, not every user's alerts.
    expect(predicateStrings(calls)).toContain(USER_ID);
  });
});

describe("alerts.addWatchedLocation", () => {
  it("saves a new watched location for the signed-in caller and returns it", async () => {
    const calls: BuilderCall[] = [];
    const inserted = {
      id: LOCATION_ID,
      userId: USER_ID,
      name: "Boise foothills",
      lat: 43.6,
      lon: -116.2,
      radiusKm: 50,
    };
    const caller = callerWith([[inserted]], sessionFor(USER_ID), calls);

    await expect(
      caller.addWatchedLocation({ name: "Boise foothills", lat: 43.6, lon: -116.2 })
    ).resolves.toMatchObject({ name: "Boise foothills" });

    const written = calls.find((call) => call.method === "values")?.args[0] as Row;
    expect(written).toMatchObject({ userId: USER_ID, name: "Boise foothills", radiusKm: 50 });
  });

  it("rejects a location name over the field's own bound before touching the database", async () => {
    const calls: BuilderCall[] = [];
    const caller = callerWith([], sessionFor(USER_ID), calls);

    await expect(
      caller.addWatchedLocation({ name: "x".repeat(101), lat: 0, lon: 0 })
    ).rejects.toMatchObject({ code: "BAD_REQUEST" });
    expect(calls.some((call) => call.method === "values")).toBe(false);
  });

  it("refuses a malformed session (no user id) as UNAUTHORIZED rather than a silent no-op", async () => {
    const calls: BuilderCall[] = [];
    const caller = callerWith([], sessionFor(undefined), calls);

    await expect(
      caller.addWatchedLocation({ name: "Boise foothills", lat: 43.6, lon: -116.2 })
    ).rejects.toMatchObject({ code: "UNAUTHORIZED" });
    expect(calls.some((call) => call.method === "values")).toBe(false);
  });
});
