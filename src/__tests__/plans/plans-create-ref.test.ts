// @vitest-environment node
import { getTableName } from "drizzle-orm";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: vi.fn() }));
vi.mock("@/lib/server/middleware/api-auth", () => ({
  checkRateLimit: vi.fn(async () => ({ available: true, limited: false })),
}));

import type { Context } from "@/lib/server/trpc/init";
import { plansRouter } from "@/lib/server/trpc/routers/plans";
import type { AppDatabase } from "@/lib/server/affiliate/database";

type Row = Record<string, unknown>;

const SAMPLE = {
  bbox: { west: -116.7, south: 43.14, east: -115.99, north: 43.81 },
  viewport: null,
};
const CODE = "ada-planner-ab12cd";

function tableName(source: unknown): string {
  return getTableName(source as Parameters<typeof getTableName>[0]);
}

function createPlanDb(affiliateRows: Row[]) {
  const selects: Record<string, Row[][]> = {
    affiliates: [affiliateRows],
    site_plans: [[SAMPLE]],
  };
  const inserts: { table: string; values: Row }[] = [];

  function selectBuilder() {
    let table = "";
    const builder: Record<string, unknown> = {
      from(source: unknown) {
        table = tableName(source);
        return builder;
      },
      where: () => builder,
      limit: () => builder,
      then(resolve: (rows: Row[]) => unknown, reject?: (reason: unknown) => unknown) {
        return Promise.resolve(selects[table]?.shift() ?? []).then(resolve, reject);
      },
    };
    return builder;
  }

  const database = {
    select: () => selectBuilder(),
    insert: (source: unknown) => {
      const table = tableName(source);
      const builder: Record<string, unknown> = {
        values(values: Row) {
          inserts.push({ table, values });
          return builder;
        },
        returning: () => builder,
        then(resolve: (rows: Row[]) => unknown, reject?: (reason: unknown) => unknown) {
          return Promise.resolve([{ publicId: "plan_aaaaaaaaaaaaaaaa" }]).then(resolve, reject);
        },
      };
      return builder;
    },
  };

  return { database: database as unknown as AppDatabase, inserts };
}

function caller(database: AppDatabase, cookie?: string) {
  return plansRouter.createCaller({
    db: database,
    session: null,
    req: new Request("https://plantgeo.aevani.com/plan/new", {
      headers: cookie ? { cookie } : undefined,
    }),
  } as Context);
}

describe("plans.create ref codes", () => {
  it("keeps an active affiliate code on the new plan", async () => {
    const memory = createPlanDb([{ code: CODE, status: "active" }]);
    const created = await caller(memory.database).create({ title: "North bed", refCode: CODE });

    expect(created.publicId).toBe("plan_aaaaaaaaaaaaaaaa");
    expect(memory.inserts[0]?.values.refCode).toBe(CODE);
    expect(memory.inserts[0]?.values.title).toBe("North bed");
  });

  it("keeps an active code that arrived only in the cookie", async () => {
    const memory = createPlanDb([{ code: CODE, status: "active" }]);
    await caller(memory.database, `plantgeo_ref=${CODE}`).create({});
    expect(memory.inserts[0]?.values.refCode).toBe(CODE);
  });

  it("drops an unknown code, including one that arrived only in the cookie", async () => {
    const fromInput = createPlanDb([]);
    await caller(fromInput.database).create({ refCode: "not-issued" });
    expect(fromInput.inserts[0]?.values.refCode).toBeNull();

    const fromCookie = createPlanDb([]);
    await caller(fromCookie.database, "plantgeo_ref=ada-demo").create({ title: "Site plan" });
    expect(fromCookie.inserts[0]?.values.refCode).toBeNull();
  });

  it("does not fall back to a cookie when the page sends an empty ref", async () => {
    const memory = createPlanDb([{ code: CODE, status: "active" }]);
    await caller(memory.database, `plantgeo_ref=${CODE}`).create({ refCode: "" });
    expect(memory.inserts[0]?.values.refCode).toBeNull();
    expect(memory.inserts).toHaveLength(1);
  });
});
