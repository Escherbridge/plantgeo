import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const runAllIngestionJobsMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/server/services/ingestion-jobs", () => ({
  runAllIngestionJobs: runAllIngestionJobsMock,
}));

function requestWithSecret(secret?: string): Request {
  return new Request("http://localhost/api/cron/ingest", {
    headers: secret ? { "x-cron-secret": secret } : {},
  });
}

/** Fresh module per test so the in-flight flag starts clean. */
async function loadRoute() {
  vi.resetModules();
  return import("@/app/api/cron/ingest/route");
}

describe("GET /api/cron/ingest", () => {
  beforeEach(() => {
    vi.stubEnv("CRON_SECRET", "test-cron-secret-value");
    runAllIngestionJobsMock.mockReset();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("returns 503 when CRON_SECRET is not configured", async () => {
    vi.stubEnv("CRON_SECRET", "");
    const { GET } = await loadRoute();
    const response = await GET(requestWithSecret("anything"));
    expect(response.status).toBe(503);
    expect(runAllIngestionJobsMock).not.toHaveBeenCalled();
  });

  it("returns 401 for a wrong or missing secret", async () => {
    const { GET } = await loadRoute();
    expect((await GET(requestWithSecret("wrong"))).status).toBe(401);
    expect((await GET(requestWithSecret())).status).toBe(401);
    expect(runAllIngestionJobsMock).not.toHaveBeenCalled();
  });

  it("acknowledges with 202 and starts ingestion detached", async () => {
    let release!: () => void;
    runAllIngestionJobsMock.mockReturnValue(
      new Promise((resolve) => {
        release = () => resolve([]);
      })
    );
    const { GET } = await loadRoute();
    const response = await GET(requestWithSecret("test-cron-secret-value"));
    expect(response.status).toBe(202);
    expect(await response.json()).toEqual({ status: "started" });
    expect(runAllIngestionJobsMock).toHaveBeenCalledTimes(1);
    release();
  });

  it("returns 409 while a run is in flight, then allows the next run", async () => {
    let release!: () => void;
    runAllIngestionJobsMock.mockReturnValue(
      new Promise((resolve) => {
        release = () => resolve([]);
      })
    );
    const { GET } = await loadRoute();
    expect((await GET(requestWithSecret("test-cron-secret-value"))).status).toBe(202);
    expect((await GET(requestWithSecret("test-cron-secret-value"))).status).toBe(409);
    release();
    await vi.waitFor(async () => {
      const response = await GET(requestWithSecret("test-cron-secret-value"));
      expect(response.status).toBe(202);
    });
  });
});
