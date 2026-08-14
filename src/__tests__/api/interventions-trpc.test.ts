import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/auth-options", () => ({ authOptions: {} }));

vi.mock("@/lib/server/db", () => {
  const mockIntervention = {
    id: "11111111-1111-1111-1111-111111111111",
    layerId: "layer-interventions-id",
    status: "proposed",
    properties: {
      name: "Biochar Application Plot A",
      type: "biochar",
      description: "Biochar soil enhancement pilot",
      publicationConsent: true,
    },
    createdAt: new Date(),
    updatedAt: new Date(),
  };

  return {
    db: {
      select: () => ({
        from: () => ({
          where: () => ({
            limit: () => Promise.resolve([{ id: "layer-interventions-id" }]),
          }),
        }),
      }),
      insert: () => ({
        values: (data: unknown) => ({
          returning: () => Promise.resolve([{ ...mockIntervention, ...(data as object) }]),
        }),
      }),
      update: () => ({
        set: (data: unknown) => ({
          where: () => ({
            returning: () => Promise.resolve([{ ...mockIntervention, ...(data as object) }]),
          }),
        }),
      }),
    },
  };
});

import { appRouter } from "@/lib/server/trpc/router";

describe("interventionsRouter Lifecycle & Moderation", () => {
  it("allows contributor to propose an intervention linked to strategy cell", async () => {
    const caller = appRouter.createCaller({
      session: { user: { id: "user-1", platformRole: "contributor" } },
      db: (await import("@/lib/server/db")).db,
    } as unknown as Parameters<typeof appRouter.createCaller>[0]);

    const result = await caller.interventions.proposeIntervention({
      title: "Biochar Application Plot A",
      strategyType: "biochar",
      lat: 44.5,
      lon: -122.5,
      cellId: "detail_44.5_-122.5",
      causalTauEst: 0.18,
    });

    expect(result.status).toBe("proposed");
  });

  it("requires expert/admin role to cast moderation vote", async () => {
    const caller = appRouter.createCaller({
      session: { user: { id: "user-expert", platformRole: "expert" } },
      db: (await import("@/lib/server/db")).db,
    } as unknown as Parameters<typeof appRouter.createCaller>[0]);

    const updated = await caller.interventions.castModerationVote({
      interventionId: "11111111-1111-1111-1111-111111111111",
      vote: "approve",
      note: "Validated against SoilGrids baseline",
    });

    expect(updated.status).toBe("approved");
  });

  it("allows expert to transition lifecycle state", async () => {
    const caller = appRouter.createCaller({
      session: { user: { id: "user-admin", platformRole: "admin" } },
      db: (await import("@/lib/server/db")).db,
    } as unknown as Parameters<typeof appRouter.createCaller>[0]);

    const transitioned = await caller.interventions.transitionLifecycleState({
      interventionId: "11111111-1111-1111-1111-111111111111",
      targetState: "active",
    });

    expect(transitioned.status).toBe("active");
  });
});
