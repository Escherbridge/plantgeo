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

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fetchBounded, UpstreamTimeoutError } from "@/lib/server/http/bounded-upstream";
import { db } from "@/lib/server/db";
import { appRouter } from "@/lib/server/trpc/router";
import {
  LANE_LOGICAL_CAPS,
  nextCronFireAfter,
  OPEN_INCIDENTS_SQL,
  PAID_POOL_BUDGET,
  PROVIDER_MONTH_TO_DATE_SQL,
  USAGE_POOLS,
  WEIGHTED_POOLS,
} from "@/lib/server/trpc/routers/jobs";

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

// --- G1 (config-driven ingestion spec §4.4 "Ledger detail", §4.9.2-4.9.3) --------------------------------

const AGRI_SERVICE = "services/agri-data-service";

function repositoryFile(path: string): string {
  return readFileSync(resolve(process.cwd(), path), "utf8");
}

function normalised(statement: string): string {
  return statement.replace(/\s+/g, " ").trim();
}

/** The Python `.sql` file as the router copies it: comments stripped, and the two documented respellings. */
function normalisedPythonStatement(relativePath: string): string {
  const source = repositoryFile(`${AGRI_SERVICE}/src/agri_data_service/sql/${relativePath}`);
  const body = source
    .split(/\r?\n/)
    .filter((line) => !line.trimStart().startsWith("--"))
    .join("\n")
    .replaceAll("\\:", ":")
    .replace(/([\w.]+) \? '([^']*)'/g, "jsonb_exists($1, '$2')");
  return normalised(body);
}

function quotedWords(text: string): string[] {
  return [...text.matchAll(/"([^"]+)"/g)].map((match) => match[1]).sort();
}

describe("the statements shared with the Python usage report", () => {
  it("copies select_provider_month_to_date.sql exactly, modulo whitespace and the two respellings", () => {
    expect(normalised(PROVIDER_MONTH_TO_DATE_SQL)).toBe(
      normalisedPythonStatement("execution/select_provider_month_to_date.sql")
    );
  });

  it("copies select_open_incidents.sql exactly", () => {
    expect(normalised(OPEN_INCIDENTS_SQL)).toBe(normalisedPythonStatement("execution/select_open_incidents.sql"));
  });

  it("binds the same pools, logical caps and weighted pools the Python loader binds", () => {
    const vocabulary = repositoryFile(`${AGRI_SERVICE}/src/agri_data_service/foundation/observability/vocabulary.py`);
    expect(quotedWords(/^PoolLabel = Literal\[([^\]]+)\]/m.exec(vocabulary)?.[1] ?? "")).toEqual([...USAGE_POOLS]);
    const caps = /^LANE_LOGICAL_CAPS[^=\n]*= MappingProxyType\((\{[^}]*\})\)/m.exec(vocabulary)?.[1] ?? "{}";
    expect(JSON.parse(caps)).toEqual(LANE_LOGICAL_CAPS);
    const usageReport = repositoryFile(`${AGRI_SERVICE}/src/agri_data_service/execution/usage_report.py`);
    const weighted = /^WEIGHTED_POOLS[^=\n]*= frozenset\(\{([^}]*)\}\)/m.exec(usageReport)?.[1] ?? "";
    expect(quotedWords(weighted)).toEqual([...WEIGHTED_POOLS]);
  });

  it("states the paid cap the provider file declares (WQ-4)", () => {
    const provider = repositoryFile(`${AGRI_SERVICE}/lanes/_providers/open-meteo.toml`);
    const declared = (key: string) =>
      Number((new RegExp(`^${key} = ([\\d._]+)\\s*$`, "m").exec(provider)?.[1] ?? "NaN").replaceAll("_", ""));
    expect(declared("weighted_calls")).toBe(PAID_POOL_BUDGET.weightedCalls);
    expect(declared("ceiling_fraction")).toBe(PAID_POOL_BUDGET.ceilingFraction);
    expect(declared("stop_fraction")).toBe(PAID_POOL_BUDGET.stopFraction);
    expect(/^charged_pool = "([^"]+)"\s*$/m.exec(provider)?.[1]).toBe(PAID_POOL_BUDGET.pool);
  });
});

describe("nextCronFireAfter", () => {
  it.each([
    ["50 */6 * * *", "2026-09-28T07:00:00Z", "2026-09-28T12:50:00.000Z"],
    ["50 */6 * * *", "2026-09-28T18:50:00Z", "2026-09-29T00:50:00.000Z"],
    // Both day fields restricted: vixie cron fires on EITHER (Thursday 1 October before Monday 5 October).
    ["0 0 1 * 1", "2026-09-28T00:00:00Z", "2026-10-01T00:00:00.000Z"],
    // A day field starting with `*` makes BOTH required: an odd day of the month that is also a Monday.
    ["0 0 */2 * 1", "2026-09-28T00:00:00Z", "2026-10-05T00:00:00.000Z"],
    ["0 0 * * 7", "2026-09-28T00:00:00Z", "2026-10-04T00:00:00.000Z"],
  ])("fires %s after %s at %s", (expression, after, expected) => {
    expect(nextCronFireAfter(expression, new Date(after))?.toISOString()).toBe(expected);
  });

  it.each([["0 0 31 2 *"], ["60 * * * *"], ["* * * *"], ["MON * * * *"], ["5-1 * * * *"]])(
    "answers null for %s, which names no real minute or is outside the grammar",
    (expression) => {
      expect(nextCronFireAfter(expression, new Date("2026-09-28T00:00:00Z"))).toBeNull();
    }
  );
});

describe("getLanes (G1)", () => {
  it("shows the next fire of the lane's schedule and which path its last run took", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-28T07:00:00Z"));
    try {
      nextRows = [
        {
          lane_id: "plantgeo.executor.soil-era5-land-direct-forward",
          version: "2",
          handler: "plantgeo.executor.command.v1",
          queue_name: "default",
          schedule_cron: "50 */6 * * *",
          schedule_timezone: "UTC",
          enabled: true,
          version_count: 1,
          lease_seconds: 900,
          time_budget_seconds: 600,
          definition_updated_at: new Date("2026-09-28T00:00:00Z"),
          last_run_id: "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
          last_run_status: "failed",
          last_run_key: "soil:20260928T065000Z",
          last_run_requested_by: "agri-service ops jobs-executor",
          last_run_started_at: new Date("2026-09-28T06:50:00Z"),
          last_run_completed_at: new Date("2026-09-28T06:55:00Z"),
          last_run_total_work_items: 1,
          last_run_succeeded_work_items: 0,
          last_run_failed_work_items: 1,
          last_run_error: "command exited with status 1\nKeyError: 'cell_id'",
          last_run_executor: "config",
        },
      ];

      const [lane] = await caller("admin").jobs.getLanes();

      expect(lane.nextFireAt?.toISOString()).toBe("2026-09-28T12:50:00.000Z");
      expect(lane.lastRun).toMatchObject({ executor: "config", error: expect.stringContaining("KeyError") });
      expect(executed[0]).toContain("target_partitions ->> 'executor'");
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("incidents", () => {
  const OPEN_HOLD = {
    id: "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    fingerprint: "lane_hold:soil-era5-land-direct-forward",
    incident_type: "lane_hold",
    severity: "warning",
    status: "open",
    summary: "lane 'soil-era5-land-direct-forward' is held (upstream); single-attempt probes re-try it",
    occurrence_count: 3,
    first_seen_at: new Date("2026-09-27T00:00:00Z"),
    last_seen_at: new Date("2026-09-28T00:00:00Z"),
    cooldown_until: null,
    owner: null,
    acknowledged_at: null,
    acknowledged_by: null,
    state: "held:1",
    rung: "2",
    exit_class: "upstream",
    chain_first_seen_at: "2026-09-27T00:00:00+00:00",
  };

  it("lists every open incident through the usage report's own statement", async () => {
    nextRows = [OPEN_HOLD];

    const [incident] = await caller("admin").jobs.getIncidents();

    expect(incident).toMatchObject({ kind: "lane_hold", state: "held:1", rung: "2", exitClass: "upstream" });
    expect(normalised(executed[0])).toBe(normalised(OPEN_INCIDENTS_SQL));
  });

  it("acknowledges an open incident in the admin's name", async () => {
    nextRows = [
      {
        id: OPEN_HOLD.id,
        fingerprint: OPEN_HOLD.fingerprint,
        acknowledged_at: new Date("2026-09-28T08:00:00Z"),
        acknowledged_by: "user-admin",
      },
    ];

    const result = await caller("admin").jobs.acknowledgeIncident({ incidentId: OPEN_HOLD.id });

    expect(result).toMatchObject({ incidentId: OPEN_HOLD.id, acknowledgedBy: "user-admin" });
    expect(executed[0]).toContain("UPDATE agri.job_incident");
    expect(executed[0]).toContain("status = 'open'");
  });

  it("refuses to acknowledge an incident that is not open instead of reporting success", async () => {
    nextRows = [];
    await expect(
      caller("admin").jobs.acknowledgeIncident({ incidentId: OPEN_HOLD.id })
    ).rejects.toMatchObject({ code: "CONFLICT" });
  });

  it("refuses the acknowledgement to an expert before any write", async () => {
    await expect(
      caller("expert").jobs.acknowledgeIncident({ incidentId: OPEN_HOLD.id })
    ).rejects.toMatchObject({ code: "FORBIDDEN" });
    expect(executed).toHaveLength(0);
  });
});

describe("getProviderUsage", () => {
  it("reads each pool through the month-to-date statement and states the paid pool's two lines", async () => {
    nextRows = [
      {
        pool: "open-meteo-paid",
        epoch_at: new Date("2026-09-27T00:00:00Z"),
        metered_count: 40,
        reported_count: "1",
        suspect_basis_count: 1,
        not_spawned_count: 2,
        lost_count: 0,
        charged: "62800",
        suspect: "1602",
      },
    ];

    const usage = await caller("admin").jobs.getProviderUsage();

    expect(usage.pools.map((pool) => pool.pool)).toEqual([...USAGE_POOLS]);
    const paid = usage.pools.find((pool) => pool.pool === "open-meteo-paid");
    expect(paid).toMatchObject({
      charged: 62800,
      suspect: 1602,
      basisSplit: { metered: 40, reported: 1, suspect: 1, notSpawned: 2, lost: 0 },
      budget: { monthlyCap: 5_000_000, gapFillCeiling: 3_000_000, forwardStop: 4_750_000, basisSuspect: false },
    });
    expect(usage.pools.find((pool) => pool.pool === "firms")?.budget).toBeNull();
    expect(executed).toHaveLength(USAGE_POOLS.length);
    const unbound = PROVIDER_MONTH_TO_DATE_SQL.replace(/(?<![:\w]):(pool|now|logical_caps|weighted_pools)\b/g, "");
    expect(normalised(executed[0])).toBe(normalised(unbound));
  });
});
