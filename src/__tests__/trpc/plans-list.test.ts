import { TRPCError } from "@trpc/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: vi.fn() }));

import type { Context } from "@/lib/server/trpc/init";
import { plansRouter } from "@/lib/server/trpc/routers/plans";

const OWNER = "11111111-1111-4111-8111-111111111111";

function database(rows: unknown[]) {
  const selected: unknown[] = [];
  const chain = {
    from() {
      return chain;
    },
    where() {
      return chain;
    },
    orderBy() {
      return Promise.resolve(rows);
    },
  };
  return {
    selected,
    db: {
      select(shape: unknown) {
        selected.push(shape);
        return chain;
      },
    } as unknown as Context["db"],
  };
}

describe("plans.list", () => {
  it("returns only the signed-in owner's title, id, and last edit", async () => {
    const edited = new Date("2026-11-02T00:00:00.000Z");
    const { db, selected } = database([
      { publicId: "plan_owned", title: "North bed", updatedAt: edited },
    ]);
    const caller = plansRouter.createCaller({
      db,
      session: { user: { id: OWNER } },
    } as Context);

    await expect(caller.list()).resolves.toEqual([
      { publicId: "plan_owned", title: "North bed", updatedAt: edited },
    ]);
    const projection = selected[0] as Record<string, unknown>;
    expect(Object.keys(projection).sort()).toEqual(["publicId", "title", "updatedAt"]);
  });

  it("refuses a signed-out caller", async () => {
    const { db } = database([]);
    const caller = plansRouter.createCaller({ db, session: null } as Context);
    await expect(caller.list()).rejects.toBeInstanceOf(TRPCError);
  });
});
