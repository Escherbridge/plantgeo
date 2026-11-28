// @vitest-environment node
import { getTableName } from "drizzle-orm";
import { describe, expect, it } from "vitest";
import { grantAffiliateRoleOnSignIn } from "@/lib/server/affiliate/role";
import {
  AffiliateReviewError,
  activeRefCode,
  approveAffiliateApplication,
  declineAffiliateApplication,
  loadAffiliateDashboard,
  resolvePlanInvite,
} from "@/lib/server/affiliate/review";
import type { AppDatabase } from "@/lib/server/affiliate/database";

type Row = Record<string, unknown>;

function tableName(source: unknown): string {
  return getTableName(source as Parameters<typeof getTableName>[0]);
}

function createMemoryDb(selects: Record<string, Row[][]>, options?: { codeConflicts?: number }) {
  const queues = Object.fromEntries(
    Object.entries(selects).map(([table, rows]) => [table, rows.map((batch) => [...batch])])
  );
  const inserts: { table: string; values: Row }[] = [];
  const updates: { table: string; values: Row }[] = [];
  let codeConflicts = options?.codeConflicts ?? 0;

  function selectBuilder() {
    let table = "";
    const builder: Record<string, unknown> = {
      from(source: unknown) {
        table = tableName(source);
        return builder;
      },
      leftJoin: () => builder,
      where: () => builder,
      orderBy: () => builder,
      limit: () => builder,
      then(resolve: (rows: Row[]) => unknown, reject?: (reason: unknown) => unknown) {
        const queue = queues[table] ?? [];
        return Promise.resolve(queue.shift() ?? []).then(resolve, reject);
      },
    };
    return builder;
  }

  function insertBuilder(source: unknown) {
    const table = tableName(source);
    const builder: Record<string, unknown> = {
      values(values: Row) {
        inserts.push({ table, values });
        return builder;
      },
      returning: () => builder,
      then(resolve: (rows: Row[]) => unknown, reject?: (reason: unknown) => unknown) {
        if (table === "affiliates" && codeConflicts > 0) {
          codeConflicts -= 1;
          const error = Object.assign(new Error("duplicate key"), {
            code: "23505",
            constraint_name: "affiliates_code_unique",
          });
          return Promise.reject(error).then(resolve, reject);
        }
        return Promise.resolve([]).then(resolve, reject);
      },
    };
    return builder;
  }

  function updateBuilder(source: unknown) {
    const table = tableName(source);
    const builder: Record<string, unknown> = {
      set(values: Row) {
        updates.push({ table, values });
        return builder;
      },
      where: () => builder,
      returning: () => builder,
      then(resolve: (rows: Row[]) => unknown, reject?: (reason: unknown) => unknown) {
        return Promise.resolve([{ id: "updated" }]).then(resolve, reject);
      },
    };
    return builder;
  }

  const database = {
    select: () => selectBuilder(),
    insert: (source: unknown) => insertBuilder(source),
    update: (source: unknown) => updateBuilder(source),
    transaction: (callback: (tx: AppDatabase) => Promise<unknown>) =>
      callback(database as unknown as AppDatabase),
  };

  return { database: database as unknown as AppDatabase, inserts, updates };
}

const APPLICATION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const REVIEWER_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const USER_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";

const pendingApplication = {
  id: APPLICATION_ID,
  name: "Ada Planner",
  email: "Ada@Example.com",
  organization: "Ada Gardens",
  status: "pending",
};

describe("affiliate approval", () => {
  it("issues one active code and marks the application approved", async () => {
    const memory = createMemoryDb({
      affiliate_applications: [[pendingApplication]],
      affiliates: [[]],
    });

    const approved = await approveAffiliateApplication(memory.database, {
      applicationId: APPLICATION_ID,
      reviewerId: REVIEWER_ID,
      suffix: () => "ab12cd",
    });

    expect(approved.code).toBe("ada-planner-ab12cd");
    expect(memory.updates).toEqual([
      { table: "affiliate_applications", values: { status: "approved" } },
    ]);
    expect(memory.inserts).toEqual([
      {
        table: "affiliates",
        values: {
          applicationId: APPLICATION_ID,
          email: "ada@example.com",
          name: "Ada Planner",
          organization: "Ada Gardens",
          code: "ada-planner-ab12cd",
          status: "active",
          approvedBy: REVIEWER_ID,
        },
      },
    ]);
  });

  it("retries when the issued code collides", async () => {
    const suffixes = ["aaaaaa", "bbbbbb"];
    const memory = createMemoryDb(
      { affiliate_applications: [[pendingApplication]], affiliates: [[]] },
      { codeConflicts: 1 }
    );

    const approved = await approveAffiliateApplication(memory.database, {
      applicationId: APPLICATION_ID,
      reviewerId: REVIEWER_ID,
      suffix: () => suffixes.shift() ?? "cccccc",
    });

    expect(approved.code).toBe("ada-planner-bbbbbb");
    expect(memory.inserts.map((insert) => insert.values.code)).toEqual([
      "ada-planner-aaaaaa",
      "ada-planner-bbbbbb",
    ]);
  });

  it("declines a pending application without issuing a code", async () => {
    const memory = createMemoryDb({
      affiliate_applications: [[{ id: APPLICATION_ID, status: "pending" }]],
    });

    await declineAffiliateApplication(memory.database, APPLICATION_ID);

    expect(memory.updates).toEqual([
      { table: "affiliate_applications", values: { status: "declined" } },
    ]);
    expect(memory.inserts).toEqual([]);
  });

  it("refuses a missing, decided, or already-coded application", async () => {
    const missing = createMemoryDb({ affiliate_applications: [[]] });
    await expect(
      approveAffiliateApplication(missing.database, {
        applicationId: APPLICATION_ID,
        reviewerId: REVIEWER_ID,
      })
    ).rejects.toMatchObject({ reason: "not_found" });
    expect(missing.inserts).toEqual([]);

    const decided = createMemoryDb({
      affiliate_applications: [[{ ...pendingApplication, status: "declined" }]],
    });
    await expect(
      declineAffiliateApplication(decided.database, APPLICATION_ID)
    ).rejects.toBeInstanceOf(AffiliateReviewError);
    expect(decided.updates).toEqual([]);

    const coded = createMemoryDb({
      affiliate_applications: [[pendingApplication]],
      affiliates: [[{ id: "existing" }]],
    });
    await expect(
      approveAffiliateApplication(coded.database, {
        applicationId: APPLICATION_ID,
        reviewerId: REVIEWER_ID,
      })
    ).rejects.toMatchObject({ reason: "email_has_code" });
    expect(coded.inserts).toEqual([]);
  });
});

describe("active invite codes", () => {
  const active = {
    code: "ada-planner-ab12cd",
    name: "Ada Planner",
    organization: "Ada Gardens",
    status: "active",
  };

  it("keeps an active code and drops an unknown or inactive one", async () => {
    const known = createMemoryDb({ affiliates: [[active]] });
    expect(await activeRefCode(known.database, "ada-planner-ab12cd")).toBe("ada-planner-ab12cd");

    const unknown = createMemoryDb({ affiliates: [[]] });
    expect(await activeRefCode(unknown.database, "not-a-code")).toBeNull();
    expect(await activeRefCode(unknown.database, null)).toBeNull();

    const inactive = createMemoryDb({
      affiliates: [[{ ...active, status: "inactive" }]],
    });
    expect(await activeRefCode(inactive.database, active.code)).toBeNull();
  });

  it("shows the invite line for an active code and nothing for an unknown one", async () => {
    const known = createMemoryDb({ affiliates: [[active]] });
    await expect(resolvePlanInvite(known.database, "ADA-Planner-ab12cd")).resolves.toEqual({
      line: "Ada Planner invited you to plan a site. They can see that you started a plan, but not what's in it.",
      refCode: "ada-planner-ab12cd",
      dropStoredCode: false,
    });

    const organizationOnly = createMemoryDb({
      affiliates: [[{ ...active, name: "  ", organization: "Ada Gardens" }]],
    });
    await expect(resolvePlanInvite(organizationOnly.database, active.code)).resolves.toMatchObject({
      line: "Ada Gardens invited you to plan a site. They can see that you started a plan, but not what's in it.",
      dropStoredCode: false,
    });

    const unknown = createMemoryDb({ affiliates: [[]] });
    await expect(resolvePlanInvite(unknown.database, "missing-code")).resolves.toEqual({
      line: null,
      refCode: null,
      dropStoredCode: true,
    });
    await expect(resolvePlanInvite(unknown.database, undefined)).resolves.toEqual({
      line: null,
      refCode: null,
      dropStoredCode: false,
    });
  });
});

describe("affiliate role on sign-in", () => {
  it("grants affiliate to a contributor with a matching email", async () => {
    const memory = createMemoryDb({
      users: [[{ email: "Ada@Example.com", platformRole: "contributor", emailVerified: new Date() }]],
      affiliates: [[{ id: "aff-1" }]],
    });

    await expect(grantAffiliateRoleOnSignIn(memory.database, USER_ID)).resolves.toBe("granted");
    expect(memory.updates).toEqual([
      { table: "affiliates", values: { userId: USER_ID } },
      { table: "users", values: { platformRole: "affiliate" } },
    ]);
  });

  it("links the row and leaves an admin role in place", async () => {
    const memory = createMemoryDb({
      users: [[{ email: "ada@example.com", platformRole: "admin", emailVerified: new Date() }]],
      affiliates: [[{ id: "aff-1" }]],
    });

    await expect(grantAffiliateRoleOnSignIn(memory.database, USER_ID)).resolves.toBe("preserved");
    expect(memory.updates).toEqual([{ table: "affiliates", values: { userId: USER_ID } }]);
  });

  it("ignores an unverified address, so a claimed email inherits nothing", async () => {
    const memory = createMemoryDb({
      users: [[{ email: "ada@example.com", platformRole: "contributor", emailVerified: null }]],
      affiliates: [[{ id: "aff-1" }]],
    });

    await expect(grantAffiliateRoleOnSignIn(memory.database, USER_ID)).resolves.toBe("none");
    expect(memory.updates).toEqual([]);
  });

  it("does nothing when the email has no active code", async () => {
    const memory = createMemoryDb({
      users: [[{ email: "ada@example.com", platformRole: "contributor", emailVerified: new Date() }]],
      affiliates: [[]],
    });

    await expect(grantAffiliateRoleOnSignIn(memory.database, USER_ID)).resolves.toBe("none");
    expect(memory.updates).toEqual([]);
  });
});

describe("affiliate dashboard count", () => {
  it("shows nothing to an unverified address", async () => {
    const memory = createMemoryDb({
      users: [[{ email: "ada@example.com", emailVerified: null }]],
      affiliates: [[{ code: "ada-planner-ab12cd", status: "active" }]],
    });

    await expect(loadAffiliateDashboard(memory.database, USER_ID)).resolves.toBeNull();
  });

  it("counts plans started from the active code", async () => {
    const memory = createMemoryDb({
      users: [[{ email: "ada@example.com", emailVerified: new Date() }]],
      affiliates: [[{ code: "ada-planner-ab12cd", status: "active" }]],
      site_plans: [[{ plansStarted: 1 }]],
    });

    await expect(loadAffiliateDashboard(memory.database, USER_ID)).resolves.toEqual({
      inviteUrl: "https://plantgeo.aevani.com/plan/new?ref=ada-planner-ab12cd",
      plansStarted: 1,
    });
  });
});
