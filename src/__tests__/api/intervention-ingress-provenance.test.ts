import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const mocks = vi.hoisted(() => ({ upsertInterventionFeature: vi.fn() }));
vi.mock("@/lib/server/services/intervention-store", () => mocks);

import { POST } from "@/app/api/ingest/interventions/route";

function request(properties: Record<string, unknown>, authorized = true) {
  return new NextRequest("https://plantgeo.test/api/ingest/interventions", {
    method: "POST",
    headers: { "content-type": "application/json", ...(authorized ? { "x-ingest-secret": "test-ingress-secret" } : {}) },
    body: JSON.stringify({
      id: "external-site-1",
      geometry: { type: "Point", coordinates: [1, 1] },
      properties: { name: "External site proposal", type: "reforestation", status: "proposed", ...properties },
    }),
  });
}

describe("machine intervention ingress provenance", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubEnv("INGEST_SECRET", "test-ingress-secret");
    mocks.upsertInterventionFeature.mockResolvedValue(true);
  });
  afterEach(() => { vi.unstubAllEnvs(); });

  it.each(["data_collection", "data_submission"])("refuses %s even with an authorized ingress credential", async (type) => {
    const response = await POST(request({ type }));
    expect(response.status).toBe(422);
    expect(JSON.stringify(await response.json())).toContain("community submission form");
    expect(mocks.upsertInterventionFeature).not.toHaveBeenCalled();
  });

  it.each([{ dataOrigin: "verified_source" }, { provenance: { verified: true } }, { category: "data" }, { dataDetails: { lane: "water-gauges", collectionMethod: "Gauge reading" } }])("refuses claimed provenance and data payloads hidden under legacy types %#", async (properties) => {
    expect((await POST(request(properties))).status).toBe(422);
    expect(mocks.upsertInterventionFeature).not.toHaveBeenCalled();
  });

  it("does not confer verified source status on a legacy machine-authored intervention", async () => {
    expect((await POST(request({}))).status).toBe(201);
    expect(mocks.upsertInterventionFeature).toHaveBeenCalledWith(expect.objectContaining({ properties: expect.objectContaining({ dataOrigin: "community" }) }));
  });

  it("retains the ingress credential check", async () => {
    expect((await POST(request({}, false))).status).toBe(401);
    expect(mocks.upsertInterventionFeature).not.toHaveBeenCalled();
  });
});
