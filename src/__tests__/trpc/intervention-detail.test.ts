import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: vi.fn() }));

import type { Context } from "@/lib/server/trpc/init";
import { interventionsRouter } from "@/lib/server/trpc/routers/interventions";

/**
 * `interventions.getInterventionDetail` -- the by-id read the map detail modal
 * runs for a feature that arrived as a Martin tile.
 *
 * Two things are under test and nothing else: the projection really carries the
 * full drawn geometry (a tile's own geometry is simplified, so a centroid or a
 * tile-shaped approximation here would silently mislead the reader), and the
 * visibility rule is the one `listMySubmissions` / `listProposed` /
 * `interventionSocial.requireVisibleFeature` already enforce -- with NOT_FOUND,
 * never FORBIDDEN, for a correctly guessed id the caller may not see.
 */

const OWNER_USER_ID = "22222222-2222-4222-8222-222222222222";
const OTHER_USER_ID = "44444444-4444-4444-8444-444444444444";
const TEAM_ID = "11111111-1111-4111-8111-111111111111";
const LAYER_ID = "33333333-3333-4333-8333-333333333333";
const FEATURE_ID = "66666666-6666-4666-8666-666666666666";

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

function createScriptedDatabase(
  script: Row[][],
  calls: BuilderCall[] = []
): Context["db"] {
  const queue = script.map((rows) => [...rows]);
  const runner = {
    select: () => createQueryStub(queue, calls),
    insert: () => createQueryStub(queue, calls),
    update: () => createQueryStub(queue, calls),
    delete: () => createQueryStub(queue, calls),
    execute: async () => [],
    transaction: async (callback: (tx: unknown) => Promise<unknown>) =>
      callback(runner),
  };
  return runner as unknown as Context["db"];
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
  return interventionsRouter.createCaller({
    db: createScriptedDatabase(script, calls),
    session,
  } as Context);
}

const LAYER_ROW: Row[] = [{ id: LAYER_ID }];

const POLYGON: GeoJSON.Geometry = {
  type: "Polygon",
  coordinates: [
    [
      [-120.1, 46.1],
      [-120.0, 46.1],
      [-120.0, 46.2],
      [-120.1, 46.2],
      [-120.1, 46.1],
    ],
  ],
};

function detailRow(overrides: Row = {}): Row[] {
  return [
    {
      id: FEATURE_ID,
      name: "Ridge replanting",
      type: "reforestation",
      category: "land",
      description: "Two hundred acres of mixed conifer.",
      geometry: POLYGON,
      status: "published",
      reviewNote: null,
      submittedByUserId: OWNER_USER_ID,
      submittedByTeamId: null,
      publicationConsent: "true",
      createdAt: new Date("2026-09-01T00:00:00.000Z"),
      updatedAt: new Date("2026-09-02T00:00:00.000Z"),
      ...overrides,
    },
  ];
}

describe("interventions.getInterventionDetail", () => {
  it("returns the full drawn geometry and field set for a published row, signed out", async () => {
    // `publicProcedure`: the published Martin layer is already visible to a
    // signed-out reader on the map, so clicking one must not demand a session.
    const caller = callerWith([LAYER_ROW, detailRow()], null);

    const detail = await caller.getInterventionDetail({ featureId: FEATURE_ID });

    expect(detail.geometry).toEqual(POLYGON);
    expect(detail.hasFullGeometry).toBe(true);
    expect(detail).toMatchObject({
      id: FEATURE_ID,
      name: "Ridge replanting",
      type: "reforestation",
      category: "land",
      status: "published",
      description: "Two hundred acres of mixed conifer.",
      submittedByUserId: OWNER_USER_ID,
      reviewNote: null,
    });
    expect(detail.createdAt).toBeInstanceOf(Date);
    expect(detail.updatedAt).toBeInstanceOf(Date);
  });

  it("scopes the read to the interventions layer", async () => {
    const calls: BuilderCall[] = [];
    await callerWith([LAYER_ROW, detailRow()], null, calls).getInterventionDetail({
      featureId: FEATURE_ID,
    });

    const predicates = calls
      .filter((call) => call.method === "where")
      .flatMap((call) => call.args.flatMap((arg) => reachableStrings(arg)));
    expect(predicates).toContain("layer_id");
  });

  it("NOT_FOUNDs a pending_review row that is not the caller's and has no consent", async () => {
    const caller = callerWith(
      [
        LAYER_ROW,
        detailRow({
          status: "pending_review",
          submittedByUserId: OWNER_USER_ID,
          publicationConsent: "false",
        }),
      ],
      sessionFor(OTHER_USER_ID)
    );

    await expect(
      caller.getInterventionDetail({ featureId: FEATURE_ID })
    ).rejects.toMatchObject({ code: "NOT_FOUND" });
  });

  it("NOT_FOUNDs a pending_review row for a signed-out reader even with consent", async () => {
    const caller = callerWith(
      [LAYER_ROW, detailRow({ status: "pending_review", publicationConsent: "true" })],
      null
    );

    await expect(
      caller.getInterventionDetail({ featureId: FEATURE_ID })
    ).rejects.toMatchObject({ code: "NOT_FOUND" });
  });

  it("returns a consenting pending_review row to any signed-in reader, as listProposed does", async () => {
    const caller = callerWith(
      [
        LAYER_ROW,
        detailRow({
          status: "pending_review",
          submittedByUserId: OWNER_USER_ID,
          publicationConsent: "true",
        }),
      ],
      sessionFor(OTHER_USER_ID)
    );

    await expect(
      caller.getInterventionDetail({ featureId: FEATURE_ID })
    ).resolves.toMatchObject({ id: FEATURE_ID, status: "pending_review" });
  });

  it("returns the submitter their own rejected row, reviewer note included", async () => {
    const caller = callerWith(
      [
        LAYER_ROW,
        detailRow({
          status: "rejected",
          reviewNote: "Outside the eligible watershed.",
          publicationConsent: "false",
        }),
      ],
      sessionFor(OWNER_USER_ID)
    );

    await expect(
      caller.getInterventionDetail({ featureId: FEATURE_ID })
    ).resolves.toMatchObject({
      status: "rejected",
      reviewNote: "Outside the eligible watershed.",
    });
  });

  it("hides another contributor's rejected row rather than leaking its reviewer note", async () => {
    const caller = callerWith(
      [
        LAYER_ROW,
        detailRow({
          status: "rejected",
          reviewNote: "Outside the eligible watershed.",
          publicationConsent: "true",
        }),
      ],
      sessionFor(OTHER_USER_ID)
    );

    await expect(
      caller.getInterventionDetail({ featureId: FEATURE_ID })
    ).rejects.toMatchObject({ code: "NOT_FOUND" });
  });

  it("returns a workspace row to a fellow member, membership re-read per request", async () => {
    const caller = callerWith(
      [
        LAYER_ROW,
        detailRow({
          status: "pending_review",
          submittedByUserId: OWNER_USER_ID,
          submittedByTeamId: TEAM_ID,
          publicationConsent: "false",
        }),
        [{ userId: OTHER_USER_ID }],
      ],
      sessionFor(OTHER_USER_ID)
    );

    await expect(
      caller.getInterventionDetail({ featureId: FEATURE_ID })
    ).resolves.toMatchObject({ id: FEATURE_ID, submittedByTeamId: TEAM_ID });
  });

  it("NOT_FOUNDs an id that does not exist", async () => {
    const caller = callerWith([LAYER_ROW, []], sessionFor(OWNER_USER_ID));

    await expect(
      caller.getInterventionDetail({ featureId: FEATURE_ID })
    ).rejects.toMatchObject({ code: "NOT_FOUND" });
  });

  it("never answers FORBIDDEN, which would confirm the id exists", async () => {
    const caller = callerWith(
      [LAYER_ROW, detailRow({ status: "pending_review", publicationConsent: "false" })],
      sessionFor(OTHER_USER_ID)
    );

    await caller
      .getInterventionDetail({ featureId: FEATURE_ID })
      .then(
        () => expect.unreachable("an invisible row must not resolve"),
        (error: { code?: string }) => expect(error.code).not.toBe("FORBIDDEN")
      );
  });
});

/**
 * Every string reachable from a recorded builder argument. Drizzle's `SQL`
 * carries `Column` instances (whose `name` is the physical column), so a
 * predicate built from `eq(features.layerId, ...)` is observable without a
 * database. Mirrors the helper in `intervention-social.test.ts`.
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
