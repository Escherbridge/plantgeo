import { describe, expect, it, vi } from "vitest";
import { PgDialect } from "drizzle-orm/pg-core";
import type { SQL } from "drizzle-orm";

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: vi.fn() }));

import type { Context } from "@/lib/server/trpc/init";
import { contributionsRouter } from "@/lib/server/trpc/routers/contributions";

const FEATURE = "66666666-6666-4666-8666-666666666666";
const LAYER = "77777777-7777-4777-8777-777777777777";
type Row = Record<string, unknown>;

function harness(options: { exists?: boolean; returned?: Row[]; role?: string } = {}) {
  const conditions: SQL[] = [];
  const writes: Row[] = [];
  const returning = vi.fn(async () => options.returned ?? []);
  const update = vi.fn(() => ({
    set: (values: Row) => {
      writes.push(values);
      return { where: (condition: SQL) => { conditions.push(condition); return { returning }; } };
    },
  }));
  const select = vi.fn(() => ({
    from: () => ({ where: () => ({ limit: async () => options.exists === false ? [] : [{ layerId: LAYER }] }) }),
  }));
  const db = {
    select,
    update,
  } as unknown as Context["db"];
  const caller = contributionsRouter.createCaller({
    db,
    session: {
      expires: "2099-01-01T00:00:00.000Z",
      user: { id: "22222222-2222-4222-8222-222222222222", platformRole: options.role ?? "expert" },
    },
  } as Context);
  return { caller, select, update, conditions, writes };
}

describe.each(["publish", "reject"] as const)("contributions %s pending-only decision", (action) => {
  const invoke = (h: ReturnType<typeof harness>) => action === "publish"
    ? h.caller.publishContribution({ featureId: FEATURE })
    : h.caller.rejectContribution({ featureId: FEATURE, reviewNote: "Insufficient supporting evidence" });

  it("binds the final UPDATE to pending status as well as identity and partition", async () => {
    const accepted = { id: FEATURE, status: action === "publish" ? "published" : "rejected", properties: { type: "data_submission", dataOrigin: "community", dataDetails: { lane: "water-gauges", collectionMethod: "Gauge reading", observedOn: "2024-02-29", dataUrl: "https://example.org/readings.csv" } } };
    const h = harness({ returned: [accepted] });
    await expect(invoke(h)).resolves.toEqual(accepted);
    expect(h.conditions).toHaveLength(1);
    const query = new PgDialect().sqlToQuery(h.conditions[0]);
    expect(query.params).toEqual([FEATURE, LAYER, "pending_review"]);
    expect(query.sql).toMatch(/"id" = \$1/);
    expect(query.sql).toMatch(/"layer_id" = \$2/);
    expect(query.sql).toMatch(/"status" = \$3/);
    expect(h.writes[0]).toMatchObject({
      status: accepted.status,
      reviewNote: action === "publish" ? null : "Insufficient supporting evidence",
    });
    expect(h.writes[0]).not.toHaveProperty("properties");
    expect(accepted.properties.dataOrigin).toBe("community");
  });

  it.each(["expert", "admin"])("refuses a stale zero-row update for %s without an override write", async (role) => {
    const h = harness({ role, returned: [] });
    await expect(invoke(h)).rejects.toMatchObject({ code: "CONFLICT", message: "Contribution is no longer awaiting review" });
    expect(h.update).toHaveBeenCalledTimes(1);
    expect(new PgDialect().sqlToQuery(h.conditions[0]).params).toEqual([FEATURE, LAYER, "pending_review"]);
  });

  it("refuses a missing feature instead of reporting an undefined success", async () => {
    const h = harness({ exists: false });
    await expect(invoke(h)).rejects.toMatchObject({ code: "NOT_FOUND" });
    expect(h.update).not.toHaveBeenCalled();
  });

  it.each(["contributor", "viewer"])("continues to refuse the %s platform role before querying", async (role) => {
    const h = harness({ role });
    await expect(invoke(h)).rejects.toMatchObject({ code: "FORBIDDEN" });
    expect(h.select).not.toHaveBeenCalled();
    expect(h.update).not.toHaveBeenCalled();
  });
});

describe("generic observations cannot bypass data provenance", () => {
  function observationHarness(allowedLayer: boolean, authenticated = true, role = "contributor") {
    const conditions: SQL[] = [];
    const writes: Row[] = [];
    const select = vi.fn(() => ({ from: () => ({ where: (condition: SQL) => {
      conditions.push(condition);
      return { limit: async () => allowedLayer ? [{ id: LAYER }] : [] };
    } }) }));
    const insert = vi.fn(() => ({ values: (value: Row) => {
      writes.push(value);
      return { returning: async () => [{ id: FEATURE, ...value }] };
    } }));
    const caller = contributionsRouter.createCaller({
      db: { select, insert } as unknown as Context["db"],
      session: authenticated ? { expires: "2099-01-01T00:00:00Z", user: { id: "22222222-2222-4222-8222-222222222222", platformRole: role } } : null,
    } as Context);
    return { caller, conditions, writes, select, insert };
  }

  it("refuses environmental and unknown layer IDs using an application-layer allowlist", async () => {
    const h = observationHarness(false);
    await expect(h.caller.submitObservation({ layerId: LAYER, properties: { temperature: 17 } })).rejects.toMatchObject({ code: "BAD_REQUEST" });
    const query = new PgDialect().sqlToQuery(h.conditions[0]);
    expect(query.params).toEqual([LAYER, "interventions"]);
    expect(query.sql).toContain('"name" in');
    expect(query.sql).toContain('"team_id" is null');
    expect(h.insert).not.toHaveBeenCalled();
  });

  it("retains community origin and the signed-in author on allowed observations", async () => {
    const h = observationHarness(true);
    await h.caller.submitObservation({ layerId: LAYER, properties: { name: "Local site note" } });
    expect(h.writes).toEqual([{
      layerId: LAYER, status: "pending_review",
      properties: { name: "Local site note", submittedByUserId: "22222222-2222-4222-8222-222222222222", dataOrigin: "community" },
    }]);
  });

  it.each([
    { dataOrigin: "verified_source" }, { provenance: "verified_source" },
    { source: "USGS NWIS" }, { submittedByUserId: "someone-else" },
    { type: "data_collection" }, { type: "data_submission" }, { category: "data" },
    { dataDetails: { lane: "water-gauges", collectionMethod: "Gauge reading" } },
  ])("refuses forged provenance and data workflow bypass %#", async (properties) => {
    const h = observationHarness(true);
    await expect(h.caller.submitObservation({ layerId: LAYER, properties })).rejects.toMatchObject({ code: "BAD_REQUEST" });
    expect(h.select).not.toHaveBeenCalled();
    expect(h.insert).not.toHaveBeenCalled();
  });

  it.each([{ authenticated: false, role: "contributor", code: "UNAUTHORIZED" }, { authenticated: true, role: "viewer", code: "FORBIDDEN" }])("requires contributor access: $code", async ({ authenticated, role, code }) => {
    const h = observationHarness(true, authenticated, role);
    await expect(h.caller.submitObservation({ layerId: LAYER })).rejects.toMatchObject({ code });
    expect(h.select).not.toHaveBeenCalled();
  });
});
