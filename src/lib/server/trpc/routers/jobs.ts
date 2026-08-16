import { z } from "zod";
import { sql } from "drizzle-orm";
import { TRPCError } from "@trpc/server";
import { adminProcedure, router } from "@/lib/server/trpc/init";
import {
  fetchBounded,
  providerUrl,
  UpstreamConfigurationError,
} from "@/lib/server/http/bounded-upstream";
import { rethrowUpstreamFault } from "@/lib/server/trpc/upstream-fault";

/**
 * The platform admin view of the durable job runner. Every procedure here reads or writes the
 * REAL ledger — `agri.job_definition` and `agri.job_run`, owned by the agri-data-service's Alembic
 * tree — through raw SQL on the Next app's pool, which reaches the same database.
 *
 * 2026-08-14 rebuild. This router previously selected from `agri.job_schedules`, a table created
 * only by `drizzle/0026_agri_job_schedules.sql` (deleted: this repo's Drizzle tree may not emit DDL
 * against the `agri` schema) and never applied to production, so every procedure would have thrown
 * `relation does not exist` against a real database. `triggerJobRun` additionally swallowed the
 * Python service's response with `.catch(() => null)` and then reported the lane as "idle"
 * regardless — a button that could not fail. Nothing here catches without reporting now.
 *
 * There is deliberately no cadence editor. Nothing in the runtime parses cron:
 * `job_definition.schedule` is written by each lane and read by nothing, and the one periodic
 * driver ticks on a fixed asyncio interval. An editable cadence field would be a control that
 * changes a string no scheduler consults.
 */

/** `db.execute` on postgres-js resolves to a RowList (an array); older shapes carry `.rows`. */
function resultRows<RowT>(result: unknown): RowT[] {
  if (Array.isArray(result)) return result as RowT[];
  const wrapped = result as { rows?: unknown } | null;
  return Array.isArray(wrapped?.rows) ? (wrapped.rows as RowT[]) : [];
}

const MAX_TRIGGER_RESPONSE_BYTES = 256 * 1024;
const TRIGGER_TIMEOUT_MS = 30_000;

interface LaneRow {
  lane_id: string;
  version: string;
  handler: string;
  queue_name: string;
  schedule_cron: string | null;
  schedule_timezone: string;
  enabled: boolean;
  version_count: number;
  lease_seconds: number;
  time_budget_seconds: number;
  definition_updated_at: Date;
  last_run_id: string | null;
  last_run_status: string | null;
  last_run_key: string | null;
  last_run_requested_by: string | null;
  last_run_started_at: Date | null;
  last_run_completed_at: Date | null;
  last_run_total_work_items: number | null;
  last_run_succeeded_work_items: number | null;
  last_run_failed_work_items: number | null;
  last_run_error: string | null;
}

interface RunRow {
  run_id: string;
  lane_id: string;
  status: string;
  logical_run_key: string;
  requested_by: string | null;
  scheduled_for: Date;
  started_at: Date | null;
  completed_at: Date | null;
  total_work_items: number;
  succeeded_work_items: number;
  failed_work_items: number;
  last_error_summary: string | null;
}

interface ExhaustedGapWindowRow {
  shard_key: string;
  lane_id: string;
  window_start: string | null;
  window_end: string | null;
  layer_reference: string | null;
  first_missing_day: string | null;
  last_missing_day: string | null;
  missing_day_count: number;
  walk_generation: number;
  reopened_at: string | null;
}

/** The Python service route that actually runs a lane; see jobs/scheduler.py's `jobs_bp`. */
function triggerEndpoint(): URL {
  const url = providerUrl("AGRI_DATA_SERVICE_URL", "http://localhost:8000");
  url.pathname = `${url.pathname.replace(/\/$/, "")}/api/v1/jobs/trigger`;
  return url;
}

/** Read the upstream's own `error` message, falling back to its raw body. */
function upstreamMessage(body: string): string {
  try {
    const parsed: unknown = JSON.parse(body);
    if (parsed && typeof parsed === "object" && "error" in parsed) {
      const message = (parsed as { error: unknown }).error;
      if (typeof message === "string" && message.length > 0) return message;
    }
  } catch {
    // A non-JSON body (an HTML error page, an empty 502) still carries information.
  }
  return body.slice(0, 500) || "the job service returned no message";
}

/**
 * THREE READS IN THIS ROUTER ARE INDEX-DEPENDENT RATHER THAN MATVIEW-BACKED, deliberately, as
 * part of the 2026-08-15 pre-aggregation pass. Fewer relations wins where an index can do the
 * job, and here one can:
 *
 *   - `getLanes`'s two `DISTINCT ON (definition.name)` are served by
 *     `agri.job_run (job_definition_id, created_at DESC)`.
 *   - `getRunHistory`'s `ORDER BY run.created_at DESC LIMIT n` by `agri.job_run (created_at DESC)`.
 *   - `getExhaustedGapWindows`'s otherwise-unindexable jsonb predicate by the partial index
 *     `agri.job_work_item ((payload -> 'reopened_from_observed_gaps'))
 *      WHERE payload -> 'reopened_from_observed_gaps' IS NOT NULL`.
 *
 * All three ship in the alembic revision that accompanies drizzle/0029. The SQL below is
 * written to be servable by them and must stay that way: `agri.job_run` grows one row per lane
 * per tick forever, so any rewrite that stops these predicates being index-driven turns an
 * admin page into an unbounded scan. Rationale: src/lib/server/AGENTS.md §pre-aggregation.
 */
export const jobsRouter = router({
  /**
   * Every lane the ledger knows, one row per definition NAME, with its newest version's
   * configuration and its most recent run.
   *
   * A lane appears here once a deploy or a trigger has upserted its definition — the ledger is the
   * list, so a lane that has never registered is honestly absent rather than shown as a stub.
   */
  getLanes: adminProcedure.query(async ({ ctx }) => {
    const result = await ctx.db.execute(sql`
      WITH newest_version AS (
        SELECT DISTINCT ON (definition.name)
          definition.name,
          definition.version,
          definition.handler,
          definition.queue_name,
          definition.schedule,
          definition.schedule_timezone,
          definition.lease_seconds,
          definition.time_budget_seconds,
          definition.updated_at
        FROM agri.job_definition definition
        ORDER BY definition.name, definition.version DESC
      ),
      pause_state AS (
        -- Aggregated across versions on purpose: the runtime runs the newest ENABLED version, so
        -- the lane is live if ANY of its rows is enabled. The toggle below writes them all.
        SELECT name, bool_or(enabled) AS enabled, count(*)::int AS version_count
        FROM agri.job_definition
        GROUP BY name
      ),
      latest_run AS (
        SELECT DISTINCT ON (definition.name)
          definition.name,
          run.id,
          run.status,
          run.logical_run_key,
          run.requested_by,
          run.started_at,
          run.completed_at,
          run.total_work_items,
          run.succeeded_work_items,
          run.failed_work_items,
          run.last_error_summary
        FROM agri.job_run run
        JOIN agri.job_definition definition ON definition.id = run.job_definition_id
        ORDER BY definition.name, run.created_at DESC
      )
      SELECT
        newest_version.name AS lane_id,
        newest_version.version,
        newest_version.handler,
        newest_version.queue_name,
        newest_version.schedule AS schedule_cron,
        newest_version.schedule_timezone,
        newest_version.lease_seconds,
        newest_version.time_budget_seconds,
        newest_version.updated_at AS definition_updated_at,
        pause_state.enabled,
        pause_state.version_count,
        latest_run.id AS last_run_id,
        latest_run.status AS last_run_status,
        latest_run.logical_run_key AS last_run_key,
        latest_run.requested_by AS last_run_requested_by,
        latest_run.started_at AS last_run_started_at,
        latest_run.completed_at AS last_run_completed_at,
        latest_run.total_work_items AS last_run_total_work_items,
        latest_run.succeeded_work_items AS last_run_succeeded_work_items,
        latest_run.failed_work_items AS last_run_failed_work_items,
        latest_run.last_error_summary AS last_run_error
      FROM newest_version
      JOIN pause_state ON pause_state.name = newest_version.name
      LEFT JOIN latest_run ON latest_run.name = newest_version.name
      ORDER BY newest_version.name
    `);

    return resultRows<LaneRow>(result).map((row) => ({
      laneId: row.lane_id,
      version: row.version,
      handler: row.handler,
      queueName: row.queue_name,
      // Documentary only, and labelled as such in the UI: nothing in the runtime parses this
      // string. The in-process driver ticks on a fixed interval instead.
      scheduleCron: row.schedule_cron,
      scheduleTimezone: row.schedule_timezone,
      enabled: row.enabled,
      versionCount: row.version_count,
      leaseSeconds: row.lease_seconds,
      timeBudgetSeconds: row.time_budget_seconds,
      definitionUpdatedAt: row.definition_updated_at,
      lastRun:
        row.last_run_id === null
          ? null
          : {
              runId: row.last_run_id,
              status: row.last_run_status,
              runKey: row.last_run_key,
              requestedBy: row.last_run_requested_by,
              startedAt: row.last_run_started_at,
              completedAt: row.last_run_completed_at,
              totalWorkItems: row.last_run_total_work_items,
              succeededWorkItems: row.last_run_succeeded_work_items,
              failedWorkItems: row.last_run_failed_work_items,
              error: row.last_run_error,
            },
    }));
  }),

  /**
   * Pause or resume a lane by flipping `agri.job_definition.enabled`.
   *
   * Every version row of the name is written, not just the newest: `load_job_definition` takes the
   * newest ENABLED version, so pausing one row would silently fall through to an older enabled one
   * and keep running the lane.
   */
  toggleLane: adminProcedure
    .input(z.object({ laneId: z.string().min(1).max(150), enabled: z.boolean() }))
    .mutation(async ({ ctx, input }) => {
      const result = await ctx.db.execute(sql`
        UPDATE agri.job_definition
        SET enabled = ${input.enabled}, updated_at = now()
        WHERE name = ${input.laneId}
        RETURNING name, version, enabled
      `);
      const updated = resultRows<{ name: string; version: string; enabled: boolean }>(result);
      if (updated.length === 0) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: `No job definition named '${input.laneId}' is registered in the ledger`,
        });
      }
      return {
        laneId: input.laneId,
        enabled: input.enabled,
        versionsUpdated: updated.length,
      };
    }),

  /**
   * Run one slice of a lane now, by asking the agri-data-service to dispatch it.
   *
   * The upstream's answer is propagated, never swallowed: a 404 (no such lane), a 409 (the lane is
   * paused) and a 500 (the ledger refused) each become the matching tRPC error carrying the
   * service's own message, and an unreachable service becomes SERVICE_UNAVAILABLE. There is no
   * local fallback that reports success — the run either happened upstream or it did not.
   */
  triggerLane: adminProcedure
    .input(z.object({ laneId: z.string().min(1).max(150) }))
    .mutation(async ({ input }) => {
      let response;
      try {
        response = await fetchBounded(
          triggerEndpoint(),
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lane_id: input.laneId }),
          },
          { maxBytes: MAX_TRIGGER_RESPONSE_BYTES, timeoutMs: TRIGGER_TIMEOUT_MS }
        );
      } catch (error) {
        if (error instanceof UpstreamConfigurationError) {
          throw new TRPCError({
            code: "PRECONDITION_FAILED",
            message: "AGRI_DATA_SERVICE_URL is not configured, so no lane can be triggered",
          });
        }
        rethrowUpstreamFault(error, "The job service");
      }

      if (!response.ok) {
        const message = upstreamMessage(response.text);
        if (response.status === 404) {
          throw new TRPCError({ code: "NOT_FOUND", message });
        }
        if (response.status === 409) {
          throw new TRPCError({ code: "CONFLICT", message });
        }
        if (response.status >= 500) {
          throw new TRPCError({ code: "INTERNAL_SERVER_ERROR", message });
        }
        throw new TRPCError({ code: "BAD_REQUEST", message });
      }

      let payload: unknown;
      try {
        payload = JSON.parse(response.text);
      } catch {
        throw new TRPCError({
          code: "INTERNAL_SERVER_ERROR",
          message: "The job service answered 200 with a body that is not JSON",
        });
      }
      const parsed = triggerResponseSchema.safeParse(payload);
      if (!parsed.success) {
        throw new TRPCError({
          code: "INTERNAL_SERVER_ERROR",
          message: "The job service answered 200 with an unrecognised body",
        });
      }
      return parsed.data;
    }),

  /**
   * Recent runs, newest first — the execution history the "Logs Inspector" tab renders.
   *
   * Rows come straight out of `agri.job_run`, so a run that failed shows its own
   * `last_error_summary` rather than a status this app inferred.
   */
  getRunHistory: adminProcedure
    .input(
      z.object({
        laneId: z.string().min(1).max(150).optional(),
        limit: z.number().int().min(1).max(200).default(50),
      })
    )
    .query(async ({ ctx, input }) => {
      const laneFilter = input.laneId
        ? sql`WHERE definition.name = ${input.laneId}`
        : sql``;
      const result = await ctx.db.execute(sql`
        SELECT
          run.id AS run_id,
          definition.name AS lane_id,
          run.status,
          run.logical_run_key,
          run.requested_by,
          run.scheduled_for,
          run.started_at,
          run.completed_at,
          run.total_work_items,
          run.succeeded_work_items,
          run.failed_work_items,
          run.last_error_summary
        FROM agri.job_run run
        JOIN agri.job_definition definition ON definition.id = run.job_definition_id
        ${laneFilter}
        ORDER BY run.created_at DESC
        LIMIT ${input.limit}
      `);

      return resultRows<RunRow>(result).map((row) => ({
        runId: row.run_id,
        laneId: row.lane_id,
        status: row.status,
        runKey: row.logical_run_key,
        requestedBy: row.requested_by,
        scheduledFor: row.scheduled_for,
        startedAt: row.started_at,
        completedAt: row.completed_at,
        totalWorkItems: row.total_work_items,
        succeededWorkItems: row.succeeded_work_items,
        failedWorkItems: row.failed_work_items,
        error: row.last_error_summary,
      }));
    }),

  /**
   * Windows `jobs-plan-gaps` gave up on reopening — NOT failures. `reconcile.py`'s
   * `gap_window_action` moves a succeeded-but-missing window through up to
   * `MAX_GAP_REOPEN_GENERATIONS` (5) reopen passes; past that, a day the upstream still doesn't
   * serve is read as evidence the source never published it, not evidence of a hole, and the
   * window is reported (`reopen_exhausted`) instead of reopened a sixth time. Every row here is
   * one of those governed absences.
   *
   * `agri.job_work_item.payload` carries the marker `reopen_gap_windows.sql` stamps on: the
   * key `reopened_from_observed_gaps` (present only once a reopen has fired) and the counter
   * `walk_generation` it bumps every pass. `walk_generation >= 5` is this router's own copy of
   * `MAX_GAP_REOPEN_GENERATIONS` — the two are independent numbers by construction (Python owns
   * the constant, this file owns the read), so a future change to one without the other silently
   * drifts; there is no shared source across the language boundary.
   *
   * `payload -> 'reopened_from_observed_gaps' IS NOT NULL` stands in for the jsonb `?` (key
   * exists) operator: postgres-js's tagged-template parser treats a bare `?` as reserved, so the
   * equivalent null-check is used instead of `payload ? 'reopened_from_observed_gaps'`.
   *
   * `missing_day_count` reconstructs the window's TRUE count rather than the capped sample
   * `gap_reopen_marker` stores: `missing_days` is truncated to `MAX_REPORTED_WINDOWS` (50) with
   * the overflow counted separately in `omitted_missing_days`, exactly as `reconcile.py`'s own
   * report totals do, so this adds the two back together rather than reporting a truncated array
   * length as the whole story.
   */
  getExhaustedGapWindows: adminProcedure
    .input(
      z.object({
        laneId: z.string().min(1).max(150).optional(),
        limit: z.number().int().min(1).max(200).default(50),
      })
    )
    .query(async ({ ctx, input }) => {
      const laneFilter = input.laneId ? sql`AND exhausted.lane_id = ${input.laneId}` : sql``;
      const result = await ctx.db.execute(sql`
        WITH exhausted AS (
          SELECT
            item.shard_key,
            definition.name AS lane_id,
            item.payload AS payload,
            COALESCE(
              CASE
                WHEN jsonb_typeof(item.payload -> 'walk_generation') = 'number'
                  THEN (item.payload ->> 'walk_generation')::int
              END,
              0
            ) AS walk_generation
          FROM agri.job_work_item item
          JOIN agri.job_run run ON run.id = item.job_run_id
          JOIN agri.job_definition definition ON definition.id = run.job_definition_id
          WHERE item.payload -> 'reopened_from_observed_gaps' IS NOT NULL
        )
        SELECT
          exhausted.shard_key,
          exhausted.lane_id,
          exhausted.walk_generation,
          exhausted.payload ->> 'window_start' AS window_start,
          exhausted.payload ->> 'window_end' AS window_end,
          exhausted.payload -> 'reopened_from_observed_gaps' ->> 'layer' AS layer_reference,
          exhausted.payload -> 'reopened_from_observed_gaps' ->> 'first_day' AS first_missing_day,
          exhausted.payload -> 'reopened_from_observed_gaps' ->> 'last_day' AS last_missing_day,
          COALESCE(
            jsonb_array_length(exhausted.payload -> 'reopened_from_observed_gaps' -> 'missing_days'),
            0
          ) + COALESCE(
            (exhausted.payload -> 'reopened_from_observed_gaps' ->> 'omitted_missing_days')::int,
            0
          ) AS missing_day_count,
          exhausted.payload -> 'reopened_from_observed_gaps' ->> 'reopened_at' AS reopened_at
        FROM exhausted
        WHERE exhausted.walk_generation >= 5
        ${laneFilter}
        ORDER BY missing_day_count DESC, exhausted.shard_key
        LIMIT ${input.limit}
      `);

      return resultRows<ExhaustedGapWindowRow>(result).map((row) => ({
        shardKey: row.shard_key,
        laneId: row.lane_id,
        windowStart: row.window_start,
        windowEnd: row.window_end,
        layerReference: row.layer_reference,
        firstMissingDay: row.first_missing_day,
        lastMissingDay: row.last_missing_day,
        missingDayCount: row.missing_day_count,
        walkGeneration: row.walk_generation,
        reopenedAt: row.reopened_at,
      }));
    }),
});

/** The shape `jobs/scheduler.py`'s trigger route returns on success. */
const triggerResponseSchema = z.object({
  message: z.string(),
  lane_id: z.string(),
  state: z.enum(["dispatched", "paused"]),
  result: z
    .object({
      definition: z.string(),
      worker_id: z.string(),
      job_run_id: z.string().nullable(),
      stop_reason: z.string(),
      claimed: z.number().int(),
      succeeded: z.number().int(),
      retried: z.number().int(),
      dead_lettered: z.number().int(),
      deferred: z.number().int(),
      yielded: z.number().int(),
      released: z.number().int(),
      abandoned: z.number().int(),
      reclaimed: z.number().int(),
      elapsed_seconds: z.number(),
      run_status: z.string().nullable(),
    })
    .nullable(),
});
