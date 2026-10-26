import { beforeEach, describe, expect, it, vi } from "vitest";
import { PgDialect } from "drizzle-orm/pg-core";
import type { SQL } from "drizzle-orm";

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
    category: "land" as const,
    description: "Community-led planting along the river corridor",
    geometry: VALID_POINT_GEOMETRY,
    publicationConsent: true as const,
    ...overrides,
  };
}

/** A closed square ring covering roughly `sideMeters` per side near the equator. */
function squareRingOfSide(sideMeters: number): number[][] {
  const metersPerDegree = 111_320;
  const d = sideMeters / metersPerDegree;
  return [
    [0, 0],
    [d, 0],
    [d, d],
    [0, d],
    [0, 0],
  ];
}

/** A `geo.features` row the mocked `.returning()` hands back after insert. */
function featureRow(overrides: Row = {}): Row[] {
  return [
    {
      id: "feature-1",
      properties: {
        name: "Riverside reforestation pilot",
        type: "reforestation",
        category: "land",
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

  it("rejects a land polygon over the 500 acre cap", async () => {
    const caller = callerWith([], contributorSession());

    // ~3000m side ≈ 2224 acres, well over the 500 acre land cap.
    await expect(
      caller.submitIntervention(
        validSubmissionInput({
          category: "land",
          geometry: { type: "Polygon", coordinates: [squareRingOfSide(3000)] },
        })
      )
    ).rejects.toMatchObject({ code: "BAD_REQUEST" });
  });

  it("accepts an air polygon over the land cap but under the air ceiling", async () => {
    const caller = callerWith(
      [[{ id: LAYER_ID }], featureRow()],
      contributorSession()
    );

    // ~3000m side ≈ 2224 acres: over the 500 acre land cap, well under the
    // 50,000 acre air ceiling.
    await expect(
      caller.submitIntervention(
        validSubmissionInput({
          type: "cloud_seeding",
          category: "air",
          geometry: { type: "Polygon", coordinates: [squareRingOfSide(3000)] },
        })
      )
    ).resolves.toMatchObject({ status: "pending_review" });
  });

  it("persists the category on the submitted feature", async () => {
    const calls: BuilderCall[] = [];
    const caller = callerWith(
      [[{ id: LAYER_ID }], featureRow()],
      contributorSession(),
      calls
    );

    await caller.submitIntervention(validSubmissionInput({ category: "land" }));

    const written = insertedValues(calls);
    expect(written.properties).toMatchObject({ category: "land" });
  });
});

describe("community data intervention submission", () => {
  const collectionDetails = { lane: "water-gauges", collectionMethod: "Record staff gauge height" };
  const submittedDetails = { ...collectionDetails, observedOn: "2024-02-29", dataUrl: "https://example.org/gauges.csv" };

  it.each([
    { type: "data_collection", dataDetails: collectionDetails },
    { type: "data_submission", dataDetails: submittedDetails },
  ])("records $type as attributed community data awaiting review", async ({ type, dataDetails }) => {
    const calls: BuilderCall[] = [];
    const caller = callerWith([[{ id: LAYER_ID }], featureRow()], contributorSession(), calls);
    await caller.submitIntervention(validSubmissionInput({ type, category: "data", dataDetails }));
    expect(insertedValues(calls)).toMatchObject({
      layerId: LAYER_ID,
      status: "pending_review",
      properties: { type, category: "data", dataDetails, dataOrigin: "community", submittedByUserId: CONTRIBUTOR_USER_ID, publicationConsent: true },
    });
  });

  it.each([
    { type: "data_collection", category: "land", dataDetails: collectionDetails },
    { type: "data_submission", category: "air", dataDetails: submittedDetails },
    { type: "cloud_seeding", category: "land" },
    { type: "reforestation", category: "air" },
    { type: "reforestation", category: "data" },
    { type: "data_collection", category: "data" },
    { type: "data_submission", category: "data", dataDetails: collectionDetails },
    { type: "reforestation", category: "land", dataDetails: collectionDetails },
    { type: "data_collection", category: "data", dataDetails: { ...collectionDetails, dataOrigin: "verified_source" } },
    { type: "data_collection", category: "data", dataDetails: collectionDetails, dataOrigin: "verified_source" },
    { type: "data_collection", category: "data", dataDetails: collectionDetails, provenance: "verified_source" },
  ])("rejects invalid data/category/origin payload %# before writing", async (payload) => {
    const calls: BuilderCall[] = [];
    const caller = callerWith([], contributorSession(), calls);
    await expect(caller.submitIntervention(validSubmissionInput(payload))).rejects.toMatchObject({ code: "BAD_REQUEST" });
    expect(calls).toHaveLength(0);
  });

  it.each([
    { session: null, code: "UNAUTHORIZED" },
    { session: contributorSession({ platformRole: "viewer" }), code: "FORBIDDEN" },
  ])("requires contributor access for data submission: $code", async ({ session, code }) => {
    const caller = callerWith([], session);
    await expect(caller.submitIntervention(validSubmissionInput({ type: "data_submission", category: "data", dataDetails: submittedDetails }))).rejects.toMatchObject({ code });
  });

  it("retains the existing bounded collection footprint", async () => {
    const caller = callerWith([], contributorSession());
    await expect(caller.submitIntervention(validSubmissionInput({
      type: "data_collection", category: "data", dataDetails: collectionDetails,
      geometry: { type: "Polygon", coordinates: [squareRingOfSide(3000)] },
    }))).rejects.toMatchObject({ code: "BAD_REQUEST" });
  });
});

describe("interventions.listProposed", () => {
  /**
   * Every row this query returns is `pending_review` by construction (the
   * WHERE clause admits nothing else), so its evidence link is never public
   * yet -- `getInterventionDetail` is the only read that may reveal it, and
   * only to the submitter, their team, or an expert.
   */
  it("never includes a pending data submission's evidence link", async () => {
    const caller = callerWith(
      [
        [{ id: LAYER_ID }],
        [
          {
            id: "feature-1",
            name: "Stream gauge readings",
            type: "data_submission",
            category: "data",
            dataDetails: {
              lane: "water-gauges",
              collectionMethod: "Staff gauge reading",
              observedOn: "2024-02-29",
              dataUrl: "https://example.org/readings.csv",
            },
            dataOrigin: "community",
            description: null,
            longitude: -122.6784,
            latitude: 45.5152,
            createdAt: new Date("2026-09-01T00:00:00.000Z"),
          },
        ],
      ],
      contributorSession()
    );

    const [proposal] = await caller.listProposed({});

    expect(proposal.dataDetails).toMatchObject({
      lane: "water-gauges",
      collectionMethod: "Staff gauge reading",
    });
    expect(proposal.dataDetails).not.toHaveProperty("dataUrl");
  });
});

/**
 * The post-publication lifecycle state machine (src/components/panels/AGENTS.md §moderation).
 * Driven through the router caller with the scripted database: the assertions are on what
 * the procedure refuses, what it writes, and what its UPDATE is conditioned on.
 */
describe("interventions lifecycle transitions", () => {
  const INTERVENTION_ID = "44444444-4444-4444-8444-444444444444";
  const expertSession = () => contributorSession({ platformRole: "expert" });

  function whereAfterSet(calls: BuilderCall[]) {
    const setIndex = calls.findIndex((entry) => entry.method === "set");
    const where = calls.slice(setIndex).find((entry) => entry.method === "where");
    return {
      written: calls[setIndex]?.args[0] as Row,
      condition: new PgDialect().sqlToQuery(where?.args[0] as SQL),
    };
  }

  it("advances active to monitored with an UPDATE bound to the stage the moderator saw", async () => {
    const calls: BuilderCall[] = [];
    const caller = callerWith(
      [[{ id: LAYER_ID }], [{ id: INTERVENTION_ID, stage: "monitored" }]],
      expertSession(),
      calls
    );

    await expect(
      caller.transitionLifecycleState({ interventionId: INTERVENTION_ID, fromStage: "active", toStage: "monitored" })
    ).resolves.toEqual({ id: INTERVENTION_ID, stage: "monitored" });

    const { written, condition } = whereAfterSet(calls);
    // Identity, partition, still-published, not a request, still at the seen stage.
    expect(condition.params).toEqual([INTERVENTION_ID, LAYER_ID, "published", "request", "active"]);
    // The lifecycle lives in properties; `features.status` must stay `published` for the tiles.
    expect(written).not.toHaveProperty("status");
    expect(new PgDialect().sqlToQuery(written.properties as SQL).params).toEqual(["monitored"]);
  });

  it.each([
    { fromStage: "published", toStage: "monitored" },
    { fromStage: "active", toStage: "published" },
    { fromStage: "monitored", toStage: "active" },
    { fromStage: "active", toStage: "active" },
  ] as const)("refuses the illegal move $fromStage -> $toStage before touching the database", async (move) => {
    const calls: BuilderCall[] = [];
    const caller = callerWith([], expertSession(), calls);
    await expect(
      caller.transitionLifecycleState({ interventionId: INTERVENTION_ID, ...move })
    ).rejects.toMatchObject({ code: "BAD_REQUEST" });
    expect(calls).toHaveLength(0);
  });

  it("lets the first of two racing moderators win and refuses the stale second with CONFLICT", async () => {
    const caller = callerWith(
      [
        [{ id: LAYER_ID }], [{ id: INTERVENTION_ID, stage: "active" }],
        [{ id: LAYER_ID }], [], [{ id: INTERVENTION_ID }],
      ],
      expertSession()
    );
    const move = { interventionId: INTERVENTION_ID, fromStage: "published", toStage: "active" } as const;

    await expect(caller.transitionLifecycleState(move)).resolves.toMatchObject({ stage: "active" });
    await expect(caller.transitionLifecycleState(move)).rejects.toMatchObject({ code: "CONFLICT" });
  });

  it("reports a missing intervention as NOT_FOUND rather than a conflict", async () => {
    const caller = callerWith([[{ id: LAYER_ID }], [], []], expertSession());
    await expect(
      caller.transitionLifecycleState({ interventionId: INTERVENTION_ID, fromStage: "published", toStage: "active" })
    ).rejects.toMatchObject({ code: "NOT_FOUND" });
  });

  it.each([
    { session: null, label: "signed-out" },
    { session: contributorSession(), label: "contributor" },
  ])("denies a $label caller both lifecycle procedures before any query", async ({ session }) => {
    const calls: BuilderCall[] = [];
    const caller = callerWith([], session, calls);
    await expect(
      caller.transitionLifecycleState({ interventionId: INTERVENTION_ID, fromStage: "published", toStage: "active" })
    ).rejects.toMatchObject({ code: "FORBIDDEN" });
    await expect(caller.listLifecycle({})).rejects.toMatchObject({ code: "FORBIDDEN" });
    expect(calls).toHaveLength(0);
  });

  it("offers each published intervention only the moves the state machine allows", async () => {
    const caller = callerWith(
      [
        [{ id: LAYER_ID }],
        [
          { id: "a", name: "Never moved", stage: "published" },
          { id: "b", name: "Running", stage: "active" },
          { id: "c", name: "Done", stage: "monitored" },
          { id: "d", name: "Ingress wrote junk", stage: "paused" },
        ],
      ],
      contributorSession({ platformRole: "admin" })
    );

    const rows = await caller.listLifecycle({});

    expect(rows.map(({ id, fromStage, nextStages }) => ({ id, fromStage, nextStages }))).toEqual([
      { id: "a", fromStage: "published", nextStages: ["active"] },
      { id: "b", fromStage: "active", nextStages: ["monitored"] },
      { id: "c", fromStage: "monitored", nextStages: [] },
      { id: "d", fromStage: null, nextStages: [] },
    ]);
  });
});
