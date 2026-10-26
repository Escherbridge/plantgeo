import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  existing: null as null | { id: string; properties: Record<string, unknown> },
  written: [] as Record<string, unknown>[],
  published: [] as unknown[],
}));

function selectChain(rows: () => unknown[]) {
  const chain = { from: () => chain, where: () => chain, limit: async () => rows() };
  return chain;
}

vi.mock("@/lib/server/db", () => ({
  db: {
    select: () => selectChain(() => [{ id: "layer-1" }]),
    transaction: async (work: (tx: unknown) => Promise<unknown>) =>
      work({
        execute: async () => undefined,
        select: () => selectChain(() => (state.existing ? [state.existing] : [])),
        insert: () => ({
          values: ({ properties }: { properties: Record<string, unknown> }) => {
            state.written.push(properties);
            return { returning: async () => [{ id: "feature-new" }] };
          },
        }),
        update: () => ({
          set: ({ properties }: { properties: Record<string, unknown> }) => {
            state.written.push(properties);
            return { where: () => ({ returning: async () => [{ id: state.existing!.id }] }) };
          },
        }),
      }),
  },
}));
vi.mock("@/lib/server/services/realtime", () => ({
  publish: async (_channel: string, message: unknown) => {
    state.published.push(message);
  },
}));

import { upsertInterventionFeature } from "@/lib/server/services/intervention-store";

describe("upsertInterventionFeature lifecycle ownership", () => {
  beforeEach(() => {
    state.existing = null;
    state.written = [];
    state.published = [];
  });

  it("keeps a moderator's lifecycle stage when a partner re-ingests the intervention", async () => {
    state.existing = { id: "feature-1", properties: { id: "p-1", title: "old", lifecycleStage: "active" } };

    await upsertInterventionFeature({
      layerId: "interventions",
      featureId: "p-1",
      properties: { title: "new", lifecycleStage: "published" },
    });

    expect(state.written).toEqual([{ id: "p-1", title: "new", lifecycleStage: "active" }]);
    expect(state.published).toEqual([expect.objectContaining({ id: "feature-1", properties: state.written[0] })]);
  });

  it("never lets ingress mint a lifecycle stage on a new intervention", async () => {
    await upsertInterventionFeature({
      layerId: "interventions",
      featureId: "p-2",
      properties: { title: "fresh", lifecycleStage: "monitored" },
    });

    expect(state.written).toEqual([{ id: "p-2", title: "fresh" }]);
  });
});
