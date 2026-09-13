import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: vi.fn() }));

import type { Context } from "@/lib/server/trpc/init";
import {
  MAX_DISPLAY_NAME_IDS,
  usersRouter,
} from "@/lib/server/trpc/routers/users";

/**
 * `users.getDisplayNames` (Phase 4 / FR-4, OQ-E).
 *
 * The load-bearing assertion is the NEGATIVE one: the response object's keys
 * are exactly `{id, name, image}`, so a future `select()` widening -- or a
 * projection edited to add "just one more field" -- fails here rather than
 * shipping `email`/`passwordHash`/`platformRole` to every signed-in reader.
 */

const VIEWER_ID = "11111111-1111-4111-8111-111111111111";
const NAMED_ID = "22222222-2222-4222-8222-222222222222";
const UNNAMED_ID = "33333333-3333-4333-8333-333333333333";

type Row = Record<string, unknown>;
type BuilderCall = { method: string; args: unknown[] };

/** Chainable drizzle stand-in, mirroring `src/__tests__/trpc/intervention-social.test.ts`. */
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

function createScriptedDatabase(
  script: Row[][],
  calls: BuilderCall[] = []
): Context["db"] {
  const queue = script.map((rows) => [...rows]);
  const runner = {
    select: (...args: unknown[]) => {
      calls.push({ method: "select", args });
      return createQueryStub(queue, calls);
    },
    insert: () => createQueryStub(queue, calls),
    update: () => createQueryStub(queue, calls),
    delete: () => createQueryStub(queue, calls),
    execute: async () => [],
    transaction: async (callback: (tx: unknown) => Promise<unknown>) =>
      callback(runner),
  };
  return runner as unknown as Context["db"];
}

/**
 * Every string reachable from a recorded builder argument. Drizzle's `SQL`
 * carries `Column` instances and the bound parameter values, and the graph is
 * cyclic (a column points back at its table), so it is walked rather than
 * serialized. Mirrors `intervention-social.test.ts`.
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

function sessionFor(userId: string): NonNullable<Context["session"]> {
  return {
    expires: "2099-01-01T00:00:00.000Z",
    user: {
      id: userId,
      platformRole: "contributor",
    } as NonNullable<Context["session"]>["user"],
  };
}

function callerWith(
  script: Row[][],
  session: Context["session"] | null,
  calls: BuilderCall[] = []
) {
  return usersRouter.createCaller({
    db: createScriptedDatabase(script, calls),
    session,
  } as Context);
}

const DIRECTORY_ROWS: Row[] = [
  { id: NAMED_ID, name: "Ada Okafor", image: "https://cdn.example/ada.png" },
  { id: UNNAMED_ID, name: null, image: null },
];

beforeEach(() => {
  vi.clearAllMocks();
});

describe("users.getDisplayNames", () => {
  it("rejects an unauthenticated caller", async () => {
    const caller = callerWith([], null);

    await expect(
      caller.getDisplayNames({ userIds: [NAMED_ID] })
    ).rejects.toMatchObject({ code: "UNAUTHORIZED" });
  });

  it("returns id, name and image for each requested id", async () => {
    const caller = callerWith([DIRECTORY_ROWS], sessionFor(VIEWER_ID));

    await expect(
      caller.getDisplayNames({ userIds: [NAMED_ID, UNNAMED_ID] })
    ).resolves.toEqual([
      { id: NAMED_ID, name: "Ada Okafor", image: "https://cdn.example/ada.png" },
      { id: UNNAMED_ID, name: null, image: null },
    ]);
  });

  it("returns exactly the keys {id, name, image} and no other users column", async () => {
    const calls: BuilderCall[] = [];
    const caller = callerWith([DIRECTORY_ROWS], sessionFor(VIEWER_ID), calls);

    const rows = await caller.getDisplayNames({
      userIds: [NAMED_ID, UNNAMED_ID],
    });

    for (const row of rows) {
      expect(Object.keys(row).sort()).toEqual(["id", "image", "name"]);
    }

    // The projection itself, not just this scripted response: the recorded
    // `select()` argument is the column list the query would have asked for.
    const [projection] = calls.find((call) => call.method === "select")?.args ?? [];
    expect(Object.keys(projection as Record<string, unknown>).sort()).toEqual([
      "id",
      "image",
      "name",
    ]);
    for (const leaked of ["email", "passwordHash", "platformRole", "verified"]) {
      expect(Object.keys(projection as Record<string, unknown>)).not.toContain(
        leaked
      );
    }
  });

  it("never serializes a private column even when the row carries one", async () => {
    // A row the driver hands back with extra columns must not reach the caller
    // widened; this pins the response shape independently of the projection.
    const caller = callerWith([[DIRECTORY_ROWS[0]]], sessionFor(VIEWER_ID));

    const [row] = await caller.getDisplayNames({ userIds: [NAMED_ID] });
    const serialized = JSON.stringify(row);

    expect(serialized).not.toContain("email");
    expect(serialized).not.toContain("passwordHash");
    expect(serialized).not.toContain("platformRole");
  });

  it("de-duplicates repeated ids before querying", async () => {
    const calls: BuilderCall[] = [];
    const caller = callerWith([[DIRECTORY_ROWS[0]]], sessionFor(VIEWER_ID), calls);

    await caller.getDisplayNames({ userIds: [NAMED_ID, NAMED_ID, NAMED_ID] });

    const where = calls.find((call) => call.method === "where");
    const bound = where?.args
      .flatMap((arg) => reachableStrings(arg))
      .filter((value) => value === NAMED_ID);
    expect(bound).toEqual([NAMED_ID]);
  });

  it("bounds the batch: an empty list and an over-cap list are both rejected", async () => {
    const caller = callerWith([[]], sessionFor(VIEWER_ID));

    await expect(caller.getDisplayNames({ userIds: [] })).rejects.toMatchObject({
      code: "BAD_REQUEST",
    });

    const overCap = Array.from({ length: MAX_DISPLAY_NAME_IDS + 1 }, () => NAMED_ID);
    await expect(
      caller.getDisplayNames({ userIds: overCap })
    ).rejects.toMatchObject({ code: "BAD_REQUEST" });

    expect(MAX_DISPLAY_NAME_IDS).toBe(100);
  });

  it("rejects an id that is not a uuid", async () => {
    const caller = callerWith([[]], sessionFor(VIEWER_ID));

    await expect(
      caller.getDisplayNames({ userIds: ["not-a-uuid"] })
    ).rejects.toMatchObject({ code: "BAD_REQUEST" });
  });
});
