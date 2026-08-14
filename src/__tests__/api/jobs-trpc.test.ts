import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The rebuilt jobs router: role gating, the real ledger queries, and a trigger that propagates
 * whatever the Python job service actually answered.
 *
 * Before 2026-08-14 this suite passed against a router that selected from `agri.job_schedules` —
 * a table no applied migration creates — and whose trigger mutation swallowed the upstream call
 * with `.catch(() => null)` before reporting the lane "idle". The seams stubbed here are the two
 * the router genuinely owns: the database (so no test needs PostgreSQL) and `fetchBounded` (so no
 * test touches the network). Everything else — the zod input schemas, the role middleware, the
 * status→TRPCError mapping — runs for real.
 */

vi.mock("@/lib/server/auth-options", () => ({ authOptions: {} }));

const executed: string[] = [];
let nextRows: unknown[] = [];

/**
 * The literal SQL of a drizzle `sql` template, bound parameters omitted. A template is a tree of
 * StringChunks (whose `value` is a string array) and Params, and nested templates are themselves
 * nodes in it — so this walks rather than stringifies.
 */
function sqlText(statement: unknown): string {
  const chunks = (statement as { queryChunks?: unknown[] } | null)?.queryChunks;
  if (!Array.isArray(chunks)) return "";
  return chunks
    .map((chunk) => {
      const value = (chunk as { value?: unknown }).value;
      if (Array.isArray(value)) return value.join("");
      return sqlText(chunk);
    })
    .join("");
}

vi.mock("@/lib/server/db", () => ({
  db: {
    execute: (statement: unknown) => {
      executed.push(sqlText(statement));
      return Promise.resolve(nextRows);
    },
  },
}));

vi.mock("@/lib/server/http/bounded-upstream", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/server/http/bounded-upstream")>();
  return { ...actual, fetchBounded: vi.fn() };
});

import { fetchBounded, UpstreamTimeoutError } from "@/lib/server/http/bounded-upstream";
import { db } from "@/lib/server/db";
import { appRouter } from "@/lib/server/trpc/router";

const mockedFetchBounded = vi.mocked(fetchBounded);

type Role = "admin" | "expert" | "contributor" | undefined;

function caller(role: Role) {
  return appRouter.createCaller({
    session: role === undefined ? null : { user: { id: `user-${role}`, platformRole: role } },
    db,
  } as never);
}

function upstream(status: number, body: unknown) {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(),
    bytes: new TextEncoder().encode(text),
    text,
    bodyError: null,
  };
}

const SLICE_RESULT = {
  definition: "strategy-mv-refresh",
  worker_id: "strategy-mv-refresh:local",
  job_run_id: "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
  stop_reason: "no_claimable_work",
  claimed: 1,
  succeeded: 1,
  retried: 0,
  dead_lettered: 0,
  deferred: 0,
  yielded: 0,
  released: 0,
  abandoned: 0,
  reclaimed: 0,
  elapsed_seconds: 0.42,
  run_status: "succeeded",
};

beforeEach(() => {
  executed.length = 0;
  nextRows = [];
  mockedFetchBounded.mockReset();
  process.env.AGRI_DATA_SERVICE_URL = "http://agri-service.internal:8000";
});

describe("jobs router role gating", () => {
  it.each(["getLanes", "getRunHistory"] as const)(
    "refuses %s to a non-admin session",
    async (procedure) => {
      const contributor = caller("contributor");
      const input = procedure === "getRunHistory" ? { limit: 10 } : undefined;
      await expect(
        (contributor.jobs[procedure] as (arg?: unknown) => Promise<unknown>)(input)
      ).rejects.toMatchObject({ code: "FORBIDDEN" });
      expect(executed).toHaveLength(0);
    }
  );

  it("refuses the toggle to an expert, who may moderate but not operate the platform", async () => {
    await expect(
      caller("expert").jobs.toggleLane({ laneId: "strategy-mv-refresh", enabled: false })
    ).rejects.toMatchObject({ code: "FORBIDDEN" });
    expect(executed).toHaveLength(0);
  });

  it("refuses the trigger to an anonymous caller before any upstream call is made", async () => {
    await expect(
      caller(undefined).jobs.triggerLane({ laneId: "strategy-mv-refresh" })
    ).rejects.toMatchObject({ code: "FORBIDDEN" });
    expect(mockedFetchBounded).not.toHaveBeenCalled();
  });

  it("admits an admin", async () => {
    nextRows = [];
    await expect(caller("admin").jobs.getLanes()).resolves.toEqual([]);
    expect(executed).toHaveLength(1);
  });
});

describe("getLanes", () => {
  it("maps a ledger row, including a lane that has never run", async () => {
    nextRows = [
      {
        lane_id: "strategy-mv-refresh",
        version: "v1",
        handler: "jobs.strategy_mv_refresh",
        queue_name: "strategy-mv-refresh",
        schedule_cron: "*/15 * * * *",
        schedule_timezone: "UTC",
        enabled: true,
        version_count: 1,
        lease_seconds: 900,
        time_budget_seconds: 600,
        definition_updated_at: new Date("2026-08-14T00:00:00Z"),
        last_run_id: null,
        last_run_status: null,
        last_run_key: null,
        last_run_requested_by: null,
        last_run_started_at: null,
        last_run_completed_at: null,
        last_run_total_work_items: null,
        last_run_succeeded_work_items: null,
        last_run_failed_work_items: null,
        last_run_error: null,
      },
    ];

    const [lane] = await caller("admin").jobs.getLanes();

    expect(lane.laneId).toBe("strategy-mv-refresh");
    expect(lane.enabled).toBe(true);
    expect(lane.scheduleCron).toBe("*/15 * * * *");
    expect(lane.lastRun).toBeNull();
    expect(executed[0]).toContain("agri.job_definition");
    expect(executed[0]).toContain("agri.job_run");
    // The fabricated table must never reappear in a query this router issues.
    expect(executed[0]).not.toContain("job_schedules");
  });

  it("carries the last run's own counters and error text", async () => {
    nextRows = [
      {
        lane_id: "strategy-mv-refresh",
        version: "v1",
        handler: "jobs.strategy_mv_refresh",
        queue_name: "strategy-mv-refresh",
        schedule_cron: null,
        schedule_timezone: "UTC",
        enabled: false,
        version_count: 2,
        lease_seconds: 900,
        time_budget_seconds: 600,
        definition_updated_at: new Date("2026-08-14T00:00:00Z"),
        last_run_id: "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
        last_run_status: "failed",
        last_run_key: "strategy-mv-refresh:20260814T120000Z",
        last_run_requested_by: "jobs-trigger-route",
        last_run_started_at: new Date("2026-08-14T12:00:00Z"),
        last_run_completed_at: new Date("2026-08-14T12:00:04Z"),
        last_run_total_work_items: 1,
        last_run_succeeded_work_items: 0,
        last_run_failed_work_items: 1,
        last_run_error: "materialized view refresh failed for: geo.mv_strategy_recommendations_coarse",
      },
    ];

    const [lane] = await caller("admin").jobs.getLanes();

    expect(lane.enabled).toBe(false);
    expect(lane.versionCount).toBe(2);
    expect(lane.lastRun).toMatchObject({
      status: "failed",
      failedWorkItems: 1,
      requestedBy: "jobs-trigger-route",
    });
    expect(lane.lastRun?.error).toContain("materialized view refresh failed");
  });
});

describe("toggleLane", () => {
  it("writes every version row of the name and reports how many it changed", async () => {
    nextRows = [
      { name: "strategy-mv-refresh", version: "v1", enabled: false },
      { name: "strategy-mv-refresh", version: "v2", enabled: false },
    ];

    const result = await caller("admin").jobs.toggleLane({
      laneId: "strategy-mv-refresh",
      enabled: false,
    });

    expect(result).toEqual({
      laneId: "strategy-mv-refresh",
      enabled: false,
      versionsUpdated: 2,
    });
    expect(executed[0]).toContain("UPDATE agri.job_definition");
  });

  it("404s a lane the ledger does not hold rather than reporting a silent success", async () => {
    nextRows = [];
    await expect(
      caller("admin").jobs.toggleLane({ laneId: "firms-fire", enabled: true })
    ).rejects.toMatchObject({ code: "NOT_FOUND" });
  });
});

describe("triggerLane", () => {
  it("posts to the documented job-service path and returns the slice the ledger recorded", async () => {
    mockedFetchBounded.mockResolvedValue(
      upstream(200, {
        message: "Triggered execution for lane 'strategy-mv-refresh'",
        lane_id: "strategy-mv-refresh",
        state: "dispatched",
        result: SLICE_RESULT,
      })
    );

    const result = await caller("admin").jobs.triggerLane({ laneId: "strategy-mv-refresh" });

    expect(result.state).toBe("dispatched");
    expect(result.result?.succeeded).toBe(1);
    const [url, init] = mockedFetchBounded.mock.calls[0];
    expect(String(url)).toBe("http://agri-service.internal:8000/api/v1/jobs/trigger");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ lane_id: "strategy-mv-refresh" });
  });

  it("propagates a Python-side failure instead of swallowing it", async () => {
    mockedFetchBounded.mockResolvedValue(
      upstream(500, { error: "no enabled job definition named 'strategy-mv-refresh'" })
    );

    await expect(
      caller("admin").jobs.triggerLane({ laneId: "strategy-mv-refresh" })
    ).rejects.toMatchObject({
      code: "INTERNAL_SERVER_ERROR",
      message: "no enabled job definition named 'strategy-mv-refresh'",
    });
  });

  it("surfaces a paused lane as a conflict carrying the service's own words", async () => {
    mockedFetchBounded.mockResolvedValue(
      upstream(409, {
        error: "lane 'strategy-mv-refresh' is paused; enable it before triggering a run",
        lane_id: "strategy-mv-refresh",
        state: "paused",
        result: null,
      })
    );

    await expect(
      caller("admin").jobs.triggerLane({ laneId: "strategy-mv-refresh" })
    ).rejects.toMatchObject({ code: "CONFLICT", message: /is paused/ });
  });

  it("surfaces an unknown lane as NOT_FOUND", async () => {
    mockedFetchBounded.mockResolvedValue(
      upstream(404, { error: "lane 'firms-fire' is not a dispatchable lane" })
    );

    await expect(caller("admin").jobs.triggerLane({ laneId: "firms-fire" })).rejects.toMatchObject({
      code: "NOT_FOUND",
      message: /not a dispatchable lane/,
    });
  });

  it("reports an unreachable job service as temporarily unavailable, not as a run", async () => {
    mockedFetchBounded.mockRejectedValue(new UpstreamTimeoutError("Upstream request timed out"));

    await expect(
      caller("admin").jobs.triggerLane({ laneId: "strategy-mv-refresh" })
    ).rejects.toMatchObject({ code: "SERVICE_UNAVAILABLE" });
  });

  it("refuses a 200 whose body does not match the job service's contract", async () => {
    mockedFetchBounded.mockResolvedValue(upstream(200, { message: "ok" }));

    await expect(
      caller("admin").jobs.triggerLane({ laneId: "strategy-mv-refresh" })
    ).rejects.toMatchObject({ code: "INTERNAL_SERVER_ERROR" });
  });
});
