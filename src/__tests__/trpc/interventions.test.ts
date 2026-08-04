import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: vi.fn() }));

import type { Context } from "@/lib/server/trpc/init";
import { interventionsRouter } from "@/lib/server/trpc/routers/interventions";
import { MAX_INTERVENTION_GEOMETRY_POSITIONS } from "@/lib/server/services/intervention-geometry";

const CONTRIBUTOR_USER_ID = "22222222-2222-4222-8222-222222222222";
const TEAM_ID = "11111111-1111-4111-8111-111111111111";
const LAYER_ID = "33333333-3333-4333-8333-333333333333";

type Row = Record<string, unknown>;
type BuilderCall = { method: string; args: unknown[] };

/**
 * Chainable drizzle stand-in: every builder method records its arguments and
 * returns itself, and awaiting any terminal shifts the next scripted result
 * set off the queue. Mirrors the stub in
 * `src/__tests__/security/org-invitations.test.ts`.
 */
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

/** The payload of the first `.values(...)` the router wrote. */
function insertedValues(calls: BuilderCall[]): Row {
  const call = calls.find((entry) => entry.method === "values");
  return (call?.args[0] ?? {}) as Row;
}

function contributorSession(
  overrides: { id?: string; platformRole?: string } = {}
): NonNullable<Context["session"]> {
  return {
    expires: "2099-01-01T00:00:00.000Z",
    user: {
      id: overrides.id ?? CONTRIBUTOR_USER_ID,
      platformRole: overrides.platformRole ?? "contributor",
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

const VALID_POINT_GEOMETRY = {
  type: "Point" as const,
  coordinates: [-122.6784, 45.5152] as [number, number],
};

/** A closed linear ring of exactly `positionCount` positions. */
function closedRing(positionCount: number): number[][] {
  const ring = Array.from({ length: positionCount }, (_unused, index) => [
    -122.6784 + index * 1e-6,
    45.5152,
  ]);
  ring[ring.length - 1] = ring[0];
  return ring;
}

/**
 * A MultiPolygon carrying exactly `totalPositions` positions, spread over
 * polygons small enough to stay under the 4,096-position per-ring maximum, so
 * the only bound a test can trip is the total-vertex ceiling.
 */
function multiPolygonOf(totalPositions: number) {
  const positionsPerRing = 2_500;
  const coordinates: number[][][][] = [];
  let remaining = totalPositions;
  while (remaining > 0) {
    const size = Math.min(positionsPerRing, remaining);
    coordinates.push([closedRing(size)]);
    remaining -= size;
  }
  return { type: "MultiPolygon" as const, coordinates };
}

function validSubmissionInput(overrides: Record<string, unknown> = {}) {
  return {
    name: "Riverside reforestation pilot",
    type: "reforestation" as const,
    description: "Community-led planting along the river corridor",
    geometry: VALID_POINT_GEOMETRY,
    publicationConsent: true as const,
    ...overrides,
  };
}

/** A `geo.features` row the mocked `.returning()` hands back after insert. */
function featureRow(overrides: Row = {}): Row[] {
  return [
    {
      id: "feature-1",
      properties: {
        name: "Riverside reforestation pilot",
        type: "reforestation",
        description: "Community-led planting along the river corridor",
        geometry: VALID_POINT_GEOMETRY,
        submittedByUserId: CONTRIBUTOR_USER_ID,
        submittedByTeamId: null,
      },
      status: "pending_review",
      reviewNote: null,
      createdAt: new Date("2026-08-01T00:00:00.000Z"),
      updatedAt: new Date("2026-08-01T00:00:00.000Z"),
      ...overrides,
    },
  ];
}

describe("interventions.submitIntervention", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("rejects an unauthenticated submission", async () => {
    const caller = callerWith([], null);
    await expect(
      caller.submitIntervention(validSubmissionInput())
    ).rejects.toMatchObject({ code: "UNAUTHORIZED" });
  });

  it("accepts an authenticated submission scoped to the submitting user", async () => {
    const calls: BuilderCall[] = [];
    const caller = callerWith(
      [[{ id: LAYER_ID }], featureRow()],
      contributorSession(),
      calls
    );

    const result = await caller.submitIntervention(validSubmissionInput());

    expect(result.status).toBe("pending_review");
    const written = insertedValues(calls);
    expect(written).toMatchObject({ layerId: LAYER_ID, status: "pending_review" });
    expect(written.properties).toMatchObject({
      submittedByUserId: CONTRIBUTOR_USER_ID,
      submittedByTeamId: null,
      type: "reforestation",
    });
  });

  it("stamps a team-scoped submission with the team id once editor access is confirmed", async () => {
    const calls: BuilderCall[] = [];
    const caller = callerWith(
      [
        [{ teamRole: "member" }], // requireTeamAccess re-read, editor role
        [{ id: LAYER_ID }],
        featureRow({
          properties: {
            name: "Riverside reforestation pilot",
            type: "reforestation",
            description: "Community-led planting along the river corridor",
            geometry: VALID_POINT_GEOMETRY,
            submittedByUserId: CONTRIBUTOR_USER_ID,
            submittedByTeamId: TEAM_ID,
          },
        }),
      ],
      contributorSession(),
      calls
    );

    await caller.submitIntervention(validSubmissionInput({ teamId: TEAM_ID }));

    const written = insertedValues(calls);
    expect(written.properties).toMatchObject({
      submittedByUserId: CONTRIBUTOR_USER_ID,
      submittedByTeamId: TEAM_ID,
    });
  });

  it("refuses a team-scoped submission from a non-editor team member", async () => {
    const caller = callerWith(
      [[{ teamRole: "viewer" }]],
      contributorSession()
    );

    await expect(
      caller.submitIntervention(validSubmissionInput({ teamId: TEAM_ID }))
    ).rejects.toMatchObject({ code: "NOT_FOUND" });
  });

  it("rejects geometry that fails InterventionGeometrySchema", async () => {
    const caller = callerWith([], contributorSession());

    await expect(
      caller.submitIntervention(
        validSubmissionInput({
          geometry: { type: "Point", coordinates: [200, 45] },
        })
      )
    ).rejects.toMatchObject({ code: "BAD_REQUEST" });
  });

  it("rejects a geometry above the interactive vertex ceiling", async () => {
    // The per-shape maxima alone allow a MultiPolygon of 8,388,608 positions. The byte cap in
    // `/api/trpc/[trpc]/route.ts` stops the allocation; this ceiling is the semantic half of it,
    // and it has to bite across polygons, not just inside one 4,096-position ring.
    const caller = callerWith([], contributorSession());

    await expect(
      caller.submitIntervention(
        validSubmissionInput({
          geometry: multiPolygonOf(MAX_INTERVENTION_GEOMETRY_POSITIONS + 4),
        })
      )
    ).rejects.toMatchObject({ code: "BAD_REQUEST" });
  });

  it("accepts a geometry exactly at the vertex ceiling", async () => {
    const caller = callerWith(
      [[{ id: LAYER_ID }], featureRow()],
      contributorSession()
    );

    await expect(
      caller.submitIntervention(
        validSubmissionInput({
          geometry: multiPolygonOf(MAX_INTERVENTION_GEOMETRY_POSITIONS),
        })
      )
    ).resolves.toMatchObject({ status: "pending_review" });
  });

  it("rejects an intervention type outside the InterventionType vocabulary", async () => {
    const caller = callerWith([], contributorSession());

    await expect(
      caller.submitIntervention(
        validSubmissionInput({ type: "wildfire_mitigation" })
      )
    ).rejects.toMatchObject({ code: "BAD_REQUEST" });
  });

  it("always writes recommendation status, never the published default", async () => {
    const calls: BuilderCall[] = [];
    const caller = callerWith(
      [[{ id: LAYER_ID }], featureRow()],
      contributorSession(),
      calls
    );

    await caller.submitIntervention(validSubmissionInput());

    const written = insertedValues(calls);
    expect(written.status).toBe("pending_review");
    expect(written.status).not.toBe("published");
  });

  it("refuses to submit when the interventions layer is not provisioned", async () => {
    const caller = callerWith([[]], contributorSession());

    await expect(
      caller.submitIntervention(validSubmissionInput())
    ).rejects.toMatchObject({ code: "PRECONDITION_FAILED" });
  });
});
