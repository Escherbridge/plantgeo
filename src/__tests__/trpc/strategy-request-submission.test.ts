import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: vi.fn() }));

import type { Context } from "@/lib/server/trpc/init";
import { interventionsRouter } from "@/lib/server/trpc/routers/interventions";

/**
 * Phase 2 of `public_strategy_requests_20260913`: `interventions.submitRequest`,
 * the public replacement for `community.submitRequest`.
 *
 * The four claims that would each be a silent product regression:
 *  - it writes to `geo.features` (one table, one social backend, one map layer),
 *    not to a parallel private table;
 *  - it writes `status = "published"` directly -- a request that landed in
 *    `pending_review` would be invisible to the readers it exists to reach, with
 *    no expert on the hook to release it;
 *  - it stamps `properties.kind = "request"`, which is the only thing the map
 *    paint and the detail modal have to tell an ask from a proposal;
 *  - it refuses an air-category type, so no request flow can ever offer
 *    `cloud_seeding` by omission.
 *
 * The database is the same scripted chainable stub `interventions.test.ts` uses.
 */

const CONTRIBUTOR_USER_ID = "22222222-2222-4222-8222-222222222222";
const LAYER_ID = "33333333-3333-4333-8333-333333333333";
const FEATURE_ID = "44444444-4444-4444-8444-444444444444";

type Row = Record<string, unknown>;
type BuilderCall = { method: string; args: unknown[] };

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

function createScriptedDatabase(script: Row[][], calls: BuilderCall[]): Context["db"] {
  const queue = script.map((rows) => [...rows]);
  const runner = {
    select: () => createQueryStub(queue, calls),
    insert: () => createQueryStub(queue, calls),
    update: () => createQueryStub(queue, calls),
    delete: () => createQueryStub(queue, calls),
    execute: async () => [],
    transaction: async (callback: (tx: unknown) => Promise<unknown>) => callback(runner),
  };
  return runner as unknown as Context["db"];
}

function insertedValues(calls: BuilderCall[]): Row {
  const call = calls.find((entry) => entry.method === "values");
  return (call?.args[0] ?? {}) as Row;
}

function insertedProperties(calls: BuilderCall[]): Row {
  return (insertedValues(calls).properties ?? {}) as Row;
}

function contributorSession(): NonNullable<Context["session"]> {
  return {
    expires: "2099-01-01T00:00:00.000Z",
    user: {
      id: CONTRIBUTOR_USER_ID,
      platformRole: "contributor",
    } as NonNullable<Context["session"]>["user"],
  };
}

/** Layer lookup, then the insert's RETURNING. */
const SUBMIT_SCRIPT: Row[][] = [[{ id: LAYER_ID }], [{ id: FEATURE_ID }]];

function callerWith(
  script: Row[][],
  session: Context["session"] | null,
  calls: BuilderCall[] = []
) {
  return interventionsRouter.createCaller({
    db: createScriptedDatabase(script, calls),
    session,
  } as Context);
}

const VALID_POINT = {
  type: "Point" as const,
  coordinates: [-122.6784, 45.5152] as [number, number],
};

function validRequestInput(overrides: Record<string, unknown> = {}) {
  return {
    name: "Swale the north slope",
    type: "water_harvesting" as const,
    description: "This hillside sheets water every spring.",
    geometry: VALID_POINT,
    publicationConsent: true as const,
    ...overrides,
  };
}

let calls: BuilderCall[];

beforeEach(() => {
  calls = [];
});

describe("interventions.submitRequest (FR-1)", () => {
  it("writes one `geo.features` row on the interventions layer", async () => {
    const caller = callerWith(SUBMIT_SCRIPT, contributorSession(), calls);

    const submitted = await caller.submitRequest(validRequestInput());

    expect(submitted).toEqual({ id: FEATURE_ID });
    expect(insertedValues(calls).layerId).toBe(LAYER_ID);
    expect(insertedProperties(calls).geometry).toEqual(VALID_POINT);
  });

  it("publishes directly -- never `pending_review`", async () => {
    const caller = callerWith(SUBMIT_SCRIPT, contributorSession(), calls);

    await caller.submitRequest(validRequestInput());

    expect(insertedValues(calls).status).toBe("published");
    expect(insertedValues(calls).status).not.toBe("pending_review");
  });

  it("stamps `properties.kind = \"request\"`", async () => {
    const caller = callerWith(SUBMIT_SCRIPT, contributorSession(), calls);

    await caller.submitRequest(validRequestInput());

    expect(insertedProperties(calls).kind).toBe("request");
  });

  it("records the submitter and the land category, and no workspace", async () => {
    const caller = callerWith(SUBMIT_SCRIPT, contributorSession(), calls);

    await caller.submitRequest(validRequestInput());

    const properties = insertedProperties(calls);
    expect(properties.submittedByUserId).toBe(CONTRIBUTOR_USER_ID);
    expect(properties.category).toBe("land");
    // The retired team-private boundary must not come back through
    // `isFeatureVisibleTo`'s workspace clause.
    expect(properties.submittedByTeamId).toBeNull();
    expect(properties.name).toBe("Swale the north slope");
    expect(properties.type).toBe("water_harvesting");
  });

  it("accepts the newly unified `water_harvesting` type", async () => {
    const caller = callerWith(SUBMIT_SCRIPT, contributorSession(), calls);

    await expect(
      caller.submitRequest(validRequestInput({ type: "water_harvesting" }))
    ).resolves.toBeDefined();
  });

  it("rejects an air-category type with a message naming the restriction", async () => {
    const caller = callerWith(SUBMIT_SCRIPT, contributorSession(), calls);

    await expect(
      caller.submitRequest(validRequestInput({ type: "cloud_seeding" }))
    ).rejects.toThrow(/land-category/i);
    expect(calls.some((entry) => entry.method === "values")).toBe(false);
  });

  it("rejects a type outside the vocabulary entirely", async () => {
    const caller = callerWith(SUBMIT_SCRIPT, contributorSession(), calls);

    await expect(
      caller.submitRequest(validRequestInput({ type: "orbital_mirrors" }))
    ).rejects.toThrow();
  });

  it("requires the same explicit publication consent a published intervention takes", async () => {
    const caller = callerWith(SUBMIT_SCRIPT, contributorSession(), calls);

    await expect(
      caller.submitRequest(validRequestInput({ publicationConsent: false }))
    ).rejects.toThrow();
    await expect(
      caller.submitRequest(
        validRequestInput({ publicationConsent: undefined })
      )
    ).rejects.toThrow();
    expect(calls.some((entry) => entry.method === "values")).toBe(false);
  });

  it("persists the recorded consent on the row", async () => {
    const caller = callerWith(SUBMIT_SCRIPT, contributorSession(), calls);

    await caller.submitRequest(validRequestInput());

    expect(insertedProperties(calls).publicationConsent).toBe(true);
  });

  it("validates the geometry through the shared schema, not a lat/lon pair", async () => {
    const caller = callerWith(SUBMIT_SCRIPT, contributorSession(), calls);

    await expect(
      caller.submitRequest(
        validRequestInput({ geometry: { type: "Point", coordinates: [-400, 91] } })
      )
    ).rejects.toThrow();
    await expect(
      caller.submitRequest(validRequestInput({ geometry: undefined }))
    ).rejects.toThrow();
  });

  it("is contributor-gated: a signed-out caller cannot post one", async () => {
    const caller = callerWith(SUBMIT_SCRIPT, null, calls);

    await expect(caller.submitRequest(validRequestInput())).rejects.toThrow();
    expect(calls.some((entry) => entry.method === "values")).toBe(false);
  });

  it("fails loudly when the interventions layer is not provisioned", async () => {
    const caller = callerWith([[]], contributorSession(), calls);

    await expect(caller.submitRequest(validRequestInput())).rejects.toThrow(
      /not provisioned/i
    );
  });
});
