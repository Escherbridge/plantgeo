import { describe, expect, it, vi } from "vitest";
vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: vi.fn() }));
import type { Context } from "@/lib/server/trpc/init";
import { contributionsRouter } from "@/lib/server/trpc/routers/contributions";
import { interventionsRouter } from "@/lib/server/trpc/routers/interventions";

const FEATURE_ID = "11111111-1111-4111-8111-111111111111";
const LAYER_ID = "22222222-2222-4222-8222-222222222222";
const AUTHOR_ID = "33333333-3333-4333-8333-333333333333";
const properties = { name: "Creek boundary", submittedByUserId: AUTHOR_ID, publicationConsent: true, geometry: { type: "Polygon", coordinates: [[[-122, 44], [-121, 44], [-121, 45], [-122, 44]]] } };
const feature = { id: FEATURE_ID, layerId: LAYER_ID, layerName: "interventions", authorExists: true, properties, status: "pending_review", reviewNote: null, updatedAt: new Date("2026-01-01T00:00:00Z"), versionTimestamp: "2026-01-01 00:00:00+00" };
type Row = Record<string, unknown>;

function setup(rows: Row[][], platformRole: string | null = "expert", validGeometry = true) {
  const pending = [...rows];
  const writes: Row[] = [];
  const execute = vi.fn(async () => [{ valid: validGeometry }]);
  const builder: unknown = new Proxy(() => {}, {
    get(_target, name) {
      if (name === "then") return (resolve: (data: Row[]) => unknown) => resolve(pending.shift() ?? []);
      return (...args: unknown[]) => {
        if (name === "set" || name === "values") writes.push(args[0] as Row);
        return builder;
      };
    },
  });
  const db = { select: () => builder, update: () => builder, insert: () => builder, execute } as unknown as Context["db"];
  const ctx = { db, session: platformRole ? { user: { id: AUTHOR_ID, platformRole }, expires: "2099-01-01" } : null } as Context;
  const reviewer = contributionsRouter.createCaller({ ...ctx, session: { user: { id: AUTHOR_ID, platformRole: "expert" }, expires: "2099-01-01" } } as Context);
  const reviewInput = async () => {
    pending.unshift([rows[0]?.[0] ?? feature]);
    const snapshot = await reviewer.reviewContribution({ featureId: FEATURE_ID });
    return { featureId: FEATURE_ID, expectedReviewVersion: snapshot.reviewVersion };
  };
  return { caller: contributionsRouter.createCaller(ctx), interventions: interventionsRouter.createCaller(ctx), writes, execute, reviewInput };
}

describe("canonical contribution publication", () => {
  it("persists published, never approved, for a reviewed intervention", async () => {
    const { caller, writes, execute, reviewInput } = setup([[feature], [{ ...feature, status: "published" }]]);
    expect((await caller.publishContribution(await reviewInput())).status).toBe("published");
    expect(writes[0]).toMatchObject({ status: "published", reviewNote: null });
    expect(execute).toHaveBeenCalledOnce();
  });

  it.each([null, "viewer", "contributor"])("denies publication and queue access to %s", async (role) => {
    const { caller, writes, reviewInput } = setup([], role);
    await expect(caller.publishContribution(await reviewInput())).rejects.toMatchObject({ code: "FORBIDDEN" });
    await expect(caller.listPendingReview()).rejects.toMatchObject({ code: "FORBIDDEN" });
    expect(writes).toHaveLength(0);
  });

  it.each(["published", "active", "monitored", "rejected", "revision_requested"])("refuses publication from %s", async (status) => {
    const { caller, writes, reviewInput } = setup([[{ ...feature, status }]]);
    await expect(caller.publishContribution(await reviewInput())).rejects.toMatchObject({ code: "PRECONDITION_FAILED" });
    expect(writes).toHaveLength(0);
  });

  it("requires a fresh individual review note to recover an approved row", async () => {
    const { caller, writes, reviewInput } = setup([[{ ...feature, status: "approved" }]]);
    await expect(caller.publishContribution(await reviewInput())).rejects.toMatchObject({ code: "PRECONDITION_FAILED" });
    expect(writes).toHaveLength(0);
    const recovery = setup([[{ ...feature, status: "approved" }], [{ ...feature, status: "published" }]]);
    await recovery.caller.publishContribution({ ...await recovery.reviewInput(), recoveryReviewNote: "  Verified this site and original consent  " });
    expect(recovery.writes[0]).toMatchObject({ status: "published", reviewNote: "Verified this site and original consent" });
  });

  it("refuses legacy recovery when the stored author does not identify an existing user", async () => {
    const { caller, writes, reviewInput } = setup([[{ ...feature, status: "approved", authorExists: false, properties: { ...properties, submittedByUserId: "not-a-real-uuid" } }]]);
    await expect(caller.publishContribution({ ...await reviewInput(), recoveryReviewNote: "Reviewed legacy site" })).rejects.toMatchObject({ code: "PRECONDITION_FAILED" });
    expect(writes).toHaveLength(0);
  });

  it.each([
    { ...properties, publicationConsent: false },
    { ...properties, submittedByUserId: null },
    { ...properties, geometry: { type: "Point", coordinates: [181, 0] } },
  ])("never recovers missing consent/authorship or malformed geometry", async (badProperties) => {
    const { caller, writes, reviewInput } = setup([[{ ...feature, status: "approved", properties: badProperties }]]);
    await expect(caller.publishContribution({ ...await reviewInput(), recoveryReviewNote: "Reviewed" })).rejects.toMatchObject({ code: "PRECONDITION_FAILED" });
    expect(writes).toHaveLength(0);
  });

  it("rejects invalid original topology even if the storage trigger repaired its geom", async () => {
    const { caller, writes, reviewInput } = setup([[feature]], "expert", false);
    await expect(caller.publishContribution(await reviewInput())).rejects.toMatchObject({ code: "BAD_REQUEST" });
    expect(writes).toHaveLength(0);
  });

  it("reports concurrent deletion or review as conflict rather than false success", async () => {
    const { caller, reviewInput } = setup([[feature], []]);
    await expect(caller.publishContribution(await reviewInput())).rejects.toMatchObject({ code: "CONFLICT" });
  });

  it.each(["publishContribution", "rejectContribution", "requestRevisionContribution"] as const)("rejects a stale displayed geometry before %s", async (action) => {
    const snapshotA = await setup([[feature]]).reviewInput();
    const revised = { ...feature, properties: { ...properties, geometry: { type: "Point", coordinates: [-120, 45] } } };
    const current = setup([[revised]]);
    await expect(current.caller[action]({ ...snapshotA, reviewNote: "Reviewed old geometry" })).rejects.toMatchObject({ code: "CONFLICT" });
    expect(current.writes).toHaveLength(0);
    expect(current.execute).not.toHaveBeenCalled();
  });

  it("accepts a freshly read revised boundary and retains submillisecond review versions", async () => {
    const snapshotA = await setup([[feature]]).reviewInput();
    const revised = { ...feature, versionTimestamp: "2026-01-01 00:00:00.000001+00" };
    const current = setup([[revised], [{ ...revised, status: "published" }]]);
    const snapshotB = await current.reviewInput();
    expect(snapshotB.expectedReviewVersion).not.toBe(snapshotA.expectedReviewVersion);
    expect((await current.caller.publishContribution(snapshotB)).status).toBe("published");
  });

  it("returns the same displayed review version from the queue and individual review", async () => {
    const { caller } = setup([[{ id: LAYER_ID }], [feature]]);
    const [queued] = await caller.listPendingReview();
    const reviewed = await setup([[feature]]).reviewInput();
    expect(queued.reviewVersion).toBe(reviewed.expectedReviewVersion);
  });

  it("requires notes on rejection and revision, and prevents publication while revision is requested", async () => {
    for (const action of ["rejectContribution", "requestRevisionContribution"] as const) {
      const invalid = setup([]);
      await expect(invalid.caller[action]({ ...await invalid.reviewInput(), reviewNote: "  " })).rejects.toMatchObject({ code: "BAD_REQUEST" });
      const valid = setup([[feature], [feature]]);
      await valid.caller[action]({ ...await valid.reviewInput(), reviewNote: "  Correct the boundary  " });
      expect(valid.writes[0]).toMatchObject({ status: action === "rejectContribution" ? "rejected" : "revision_requested", reviewNote: "Correct the boundary" });
    }
  });

  it("blocks generic observations from forging intervention authorship or workspace consent", async () => {
    const { caller, writes } = setup([[{ name: "interventions" }]], "contributor");
    await expect(caller.submitObservation({ layerId: LAYER_ID, properties })).rejects.toMatchObject({ code: "PRECONDITION_FAILED" });
    expect(writes).toHaveLength(0);
  });

  it("does not strand generic observations in an unsupported revision workflow", async () => {
    const { caller, writes, reviewInput } = setup([[{ ...feature, layerName: "observations" }]]);
    await expect(caller.requestRevisionContribution({ ...await reviewInput(), reviewNote: "Fix location" })).rejects.toMatchObject({ code: "PRECONDITION_FAILED" });
    expect(writes).toHaveLength(0);
  });

  it("excludes unknown legacy history from the recovery queue", async () => {
    const { caller } = setup([[{ id: LAYER_ID }], [feature, { ...feature, id: "eligible", status: "approved" }, { ...feature, id: "unknown", status: "approved", properties: {} }, { ...feature, id: "fake-author", status: "approved", authorExists: false }]]);
    expect((await caller.listPendingReview()).map((row) => row.id)).toEqual([FEATURE_ID, "eligible"]);
  });

  it("reports missing layer provisioning independently from an empty review queue", async () => {
    await expect(setup([[]]).caller.listPendingReview()).rejects.toMatchObject({ code: "PRECONDITION_FAILED" });
    expect(await setup([[{ id: LAYER_ID }], []]).caller.listPendingReview()).toEqual([]);
  });
});

describe("contributor revision", () => {
  const revision = { featureId: FEATURE_ID, name: "Corrected creek site", type: "reforestation" as const, geometry: { type: "Point" as const, coordinates: [-122, 44] }, publicationConsent: true as const };
  it("preserves ownership and resubmits the corrected site for expert review", async () => {
    const { interventions, writes } = setup([[{ id: LAYER_ID }], [{ ...feature, status: "revision_requested" }], [{ ...feature, status: "pending_review" }]], "contributor");
    await interventions.reviseIntervention(revision);
    expect(writes[0]).toMatchObject({ status: "pending_review", reviewNote: null, properties: { submittedByUserId: AUTHOR_ID, name: revision.name, geometry: revision.geometry } });
  });

  it("hides absent or differently owned rows", async () => {
    const { interventions, writes } = setup([[{ id: LAYER_ID }], []], "contributor");
    await expect(interventions.reviseIntervention(revision)).rejects.toMatchObject({ code: "NOT_FOUND" });
    expect(writes).toHaveLength(0);
  });

  it("rechecks current workspace editor access before resubmitting", async () => {
    const { interventions, writes } = setup([[{ id: LAYER_ID }], [{ ...feature, status: "rejected", properties: { ...properties, submittedByTeamId: LAYER_ID } }], [{ teamRole: "viewer" }]], "contributor");
    await expect(interventions.reviseIntervention(revision)).rejects.toMatchObject({ code: "NOT_FOUND" });
    expect(writes).toHaveLength(0);
  });

  it("does not let a contributor revise a published site", async () => {
    const { interventions, writes } = setup([[{ id: LAYER_ID }], [{ ...feature, status: "published" }]], "contributor");
    await expect(interventions.reviseIntervention(revision)).rejects.toMatchObject({ code: "PRECONDITION_FAILED" });
    expect(writes).toHaveLength(0);
  });

  it("rejects invalid topology on revision before a write", async () => {
    const { interventions, writes } = setup([[{ id: LAYER_ID }], [{ ...feature, status: "revision_requested" }]], "contributor", false);
    await expect(interventions.reviseIntervention(revision)).rejects.toMatchObject({ code: "BAD_REQUEST" });
    expect(writes).toHaveLength(0);
  });
});
