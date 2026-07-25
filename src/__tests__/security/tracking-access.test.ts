import { describe, expect, it, vi } from "vitest";
import type { Context } from "@/lib/server/trpc/init";

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: vi.fn() }));

import { trackingRouter } from "@/lib/server/trpc/routers/tracking";

const UUID = "11111111-1111-4111-8111-111111111111";

function contributorContext(): Context {
  return {
    db: {} as Context["db"],
    session: {
      expires: "2099-01-01T00:00:00.000Z",
      user: {
        id: UUID,
        platformRole: "contributor",
      } as NonNullable<Context["session"]>["user"],
    },
  };
}

describe("tracking access", () => {
  it("fails closed for every non-admin tracking operation", async () => {
    const caller = trackingRouter.createCaller(contributorContext());
    const operations = [
      caller.listAssets(),
      caller.getAsset({ id: UUID }),
      caller.createAsset({ name: "crew vehicle" }),
      caller.updateAsset({ id: UUID, name: "crew vehicle" }),
      caller.getRouteHistory({
        assetId: UUID,
        from: "2026-07-20T00:00:00.000Z",
        to: "2026-07-20T01:00:00.000Z",
      }),
      caller.listGeofences(),
      caller.createGeofence({
        name: "operations area",
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [-105, 39],
              [-104, 39],
              [-104, 40],
              [-105, 39],
            ],
          ],
        },
      }),
      caller.deleteGeofence({ id: UUID }),
      caller.getAlerts(),
      caller.acknowledgeAlert({ id: UUID }),
    ];

    for (const operation of operations) {
      await expect(operation).rejects.toMatchObject({ code: "FORBIDDEN" });
    }
  });
});
