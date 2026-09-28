import { z } from "zod";
import { sql, type SQL } from "drizzle-orm";
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
 * There is deliberately no cadence editor. `job_definition.schedule` is written by each lane: a
 * config-path lane's cron IS its schedule (config-driven ingestion spec S9), while a legacy lane's
 * string names the buckets its cadence opens. The next fire shown here is read from that string
 * (`nextCronFireAfter`, a port of `execution/cron_schedule.py::next_fire_after`); an editable field
 * would still be a control no scheduler consults, since lane TOMLs and `LANE_SPECS` are code.
 *
 * Since G1 (spec §4.4 "Ledger detail", §4.9.2-4.9.3) this router also shows open incidents with their
 * acknowledgement, and the paid provider's month-to-date usage. Both read the SAME statements the
 * Python usage report runs, copied here and pinned to the `.sql` files by a normalised parity test
 * (`src/__tests__/api/jobs-trpc.test.ts`), so the admin page and `agri-service ops jobs-usage-report`
 * can never disagree about a number.
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
  last_run_executor: string | null;
}

interface IncidentRow {
  id: string;
  fingerprint: string;
  incident_type: string;
  severity: string;
  status: string;
  summary: string;
  occurrence_count: number;
  first_seen_at: Date;
  last_seen_at: Date;
  cooldown_until: Date | null;
  owner: string | null;
  acknowledged_at: Date | null;
  acknowledged_by: string | null;
  state: string | null;
  rung: string | null;
  exit_class: string | null;
  chain_first_seen_at: string | null;
}

/** One pool's month-to-date usage as `getProviderUsage` returns it. */
interface ProviderUsage {
  pool: (typeof USAGE_POOLS)[number];
  epochAt: Date | null;
  charged: number;
  suspect: number;
  basisSplit: { metered: number; reported: number; suspect: number; notSpawned: number; lost: number };
  budget: {
    monthlyCap: number;
    gapFillCeiling: number;
    forwardStop: number;
    basisSuspect: boolean;
  } | null;
}

interface MonthToDateRow {
  pool: string;
  epoch_at: Date | null;
  metered_count: number | string;
  reported_count: number | string;
  suspect_basis_count: number | string;
  not_spawned_count: number | string;
  lost_count: number | string;
  charged: number | string | null;
  suspect: number | string | null;
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

// --- The cron clock: a port of the Python grammar and next-fire search (spec S9) -------------------------

const CRON_FIELD_BOUNDS = [
  { lowest: 0, highest: 59 }, // minute
  { lowest: 0, highest: 23 }, // hour
  { lowest: 1, highest: 31 }, // day of month
  { lowest: 1, highest: 12 }, // month
  { lowest: 0, highest: 7 }, // day of week; 0 and 7 are both Sunday
] as const;
/** Eight years and two days: a 29-February schedule across the skipped 2100 leap day (as the Python clock). */
const CRON_SEARCH_HORIZON_DAYS = 8 * 366 + 2;
const MINUTE_MS = 60_000;
const DAY_MS = 24 * 60 * MINUTE_MS;

interface ParsedCron {
  minutes: number[];
  hours: number[];
  daysOfMonth: ReadonlySet<number>;
  months: ReadonlySet<number>;
  daysOfWeek: ReadonlySet<number>;
  /** Vixie cron's rule: a day field whose text starts with `*` (even `*` + `/2`) is unrestricted. */
  eitherDayFieldUnrestricted: boolean;
}

function parseCronInteger(token: string, lowest: number, highest: number): number | null {
  if (!/^\d+$/.test(token)) return null;
  const value = Number(token);
  return value >= lowest && value <= highest ? value : null;
}

/** One comma item: `*`, `a`, `a-b`, `*` + `/n`, `a/n` (a to the field's end) or `a-b/n`; null when malformed. */
function expandCronItem(item: string, lowest: number, highest: number): number[] | null {
  const slash = item.indexOf("/");
  const rangePart = slash === -1 ? item : item.slice(0, slash);
  const step = slash === -1 ? 1 : parseCronInteger(item.slice(slash + 1), 1, highest);
  if (step === null) return null;
  let start: number | null;
  let stop: number | null;
  if (rangePart === "*") {
    [start, stop] = [lowest, highest];
  } else if (rangePart.includes("-")) {
    const dash = rangePart.indexOf("-");
    start = parseCronInteger(rangePart.slice(0, dash), lowest, highest);
    stop = parseCronInteger(rangePart.slice(dash + 1), lowest, highest);
    if (start !== null && stop !== null && start > stop) return null;
  } else {
    start = parseCronInteger(rangePart, lowest, highest);
    stop = slash === -1 ? start : highest;
  }
  if (start === null || stop === null) return null;
  const values: number[] = [];
  for (let value = start; value <= stop; value += step) values.push(value);
  return values;
}

/** The 5-field numeric UTC grammar of `foundation/lane_config/cron.py::parse_cron`; null for anything else. */
function parseCron(text: string): ParsedCron | null {
  const fields = text.trim().split(/\s+/);
  if (fields.length !== CRON_FIELD_BOUNDS.length) return null;
  const expanded: number[][] = [];
  for (const [index, field] of fields.entries()) {
    const { lowest, highest } = CRON_FIELD_BOUNDS[index];
    const values = new Set<number>();
    for (const item of field.split(",")) {
      const itemValues = item === "" ? null : expandCronItem(item, lowest, highest);
      if (itemValues === null) return null;
      for (const value of itemValues) values.add(value);
    }
    expanded.push([...values].sort((left, right) => left - right));
  }
  return {
    minutes: expanded[0],
    hours: expanded[1],
    daysOfMonth: new Set(expanded[2]),
    months: new Set(expanded[3]),
    daysOfWeek: new Set(expanded[4].map((day) => (day === 7 ? 0 : day))),
    eitherDayFieldUnrestricted: fields[2].startsWith("*") || fields[4].startsWith("*"),
  };
}

function cronFiresOnDay(cron: ParsedCron, day: Date): boolean {
  if (!cron.months.has(day.getUTCMonth() + 1)) return false;
  const dayOfMonthMatches = cron.daysOfMonth.has(day.getUTCDate());
  const dayOfWeekMatches = cron.daysOfWeek.has(day.getUTCDay());
  return cron.eitherDayFieldUnrestricted
    ? dayOfMonthMatches && dayOfWeekMatches
    : dayOfMonthMatches || dayOfWeekMatches;
}

/**
 * The first UTC minute strictly after `after` on which `expression` fires, or null when the string is
 * not in the grammar or names no real minute (`0 0 31 2 *`). A port of
 * `execution/cron_schedule.py::next_fire_after`, day rule and search horizon included.
 */
export function nextCronFireAfter(expression: string | null, after: Date): Date | null {
  const cron = expression === null ? null : parseCron(expression);
  if (cron === null) return null;
  const floor = Math.floor(after.getTime() / MINUTE_MS) * MINUTE_MS + MINUTE_MS;
  const firstDay = Math.floor(floor / DAY_MS) * DAY_MS;
  for (let offset = 0; offset <= CRON_SEARCH_HORIZON_DAYS; offset += 1) {
    const day = new Date(firstDay + offset * DAY_MS);
    if (!cronFiresOnDay(cron, day)) continue;
    for (const hour of cron.hours) {
      for (const minute of cron.minutes) {
        const fire = day.getTime() + (hour * 60 + minute) * MINUTE_MS;
        if (fire >= floor) return new Date(fire);
      }
    }
  }
  return null;
}

// --- Statements shared with the Python usage report (parity-pinned; see the header above) --------------

/**
 * `services/agri-data-service/src/agri_data_service/sql/execution/select_provider_month_to_date.sql`,
 * comments stripped. Two spellings differ, and the parity test normalises exactly these: `\:gap-repair`
 * (SQLAlchemy's escaped colon) is written `:gap-repair`, and the jsonb key test `metrics ? 'spawned'` is
 * written `jsonb_exists(metrics, 'spawned')` (this file's own note: postgres-js reserves a bare `?`).
 * Binds are `:pool`, `:now`, `:logical_caps` and `:weighted_pools`, bound by `bindNamedParameters`.
 */
export const PROVIDER_MONTH_TO_DATE_SQL = `
WITH epoch AS (
    SELECT min(attempt.started_at) AS epoch_at
    FROM agri.job_attempt AS attempt
    WHERE jsonb_exists(attempt.metrics, 'spawned')
),
scoped AS (
    SELECT
        attempt.status,
        attempt.metrics,
        regexp_replace(definition.name, ':gap-repair$', '') AS lane_id,
        EXISTS (
            SELECT 1
            FROM jsonb_each(
                CASE
                    WHEN jsonb_typeof(attempt.metrics -> 'usage' -> 'hosts') = 'object'
                    THEN attempt.metrics -> 'usage' -> 'hosts'
                    ELSE '{}'::jsonb
                END
            ) AS host_entry(host, entry)
            WHERE host_entry.entry ->> 'pool' = CAST(:pool AS text)
        ) AS touched_pool
    FROM agri.job_attempt AS attempt
    JOIN agri.job_work_item AS item ON item.id = attempt.job_work_item_id
    JOIN agri.job_run AS run ON run.id = item.job_run_id
    JOIN agri.job_definition AS definition ON definition.id = run.job_definition_id
    CROSS JOIN epoch
    WHERE attempt.status NOT IN ('running', 'deferred')
      AND attempt.started_at >= date_trunc('month', CAST(:now AS timestamptz), 'UTC')
      AND attempt.started_at < date_trunc('month', CAST(:now AS timestamptz), 'UTC') + interval '1 month'
      AND epoch.epoch_at IS NOT NULL
      AND attempt.started_at >= epoch.epoch_at
),
attributed AS (
    SELECT
        scoped.*,
        CASE
            WHEN jsonb_typeof(scoped.metrics -> 'usage' -> 'hosts') = 'object'
                 AND scoped.metrics -> 'usage' -> 'hosts' <> '{}'::jsonb
            THEN scoped.touched_pool
            ELSE
                scoped.lane_id IN (SELECT jsonb_object_keys(CAST(:logical_caps AS jsonb)))
                AND CAST(:pool AS text) = ANY(CAST(:weighted_pools AS text[]))
        END AS attributed_to_pool
    FROM scoped
),
metered AS (
    SELECT
        count(*) FILTER (
            WHERE attributed.metrics -> 'usage' ->> 'charged_basis' = 'metered' AND attributed.attributed_to_pool
        ) AS metered_count,
        count(*) FILTER (
            WHERE attributed.metrics -> 'usage' ->> 'charged_basis' = 'reported' AND attributed.attributed_to_pool
        ) AS reported_count,
        count(*) FILTER (
            WHERE attributed.metrics -> 'usage' ->> 'charged_basis' = 'suspect' AND attributed.attributed_to_pool
        ) AS suspect_basis_count,
        count(*) FILTER (
            WHERE attributed.metrics -> 'usage' ->> 'charged_basis' = 'not_spawned' AND attributed.attributed_to_pool
        ) AS not_spawned_count,
        COALESCE(
            sum(NULLIF(attributed.metrics -> 'usage' ->> 'charged', '')::numeric)
                FILTER (WHERE attributed.attributed_to_pool),
            0
        ) AS charged,
        COALESCE(
            sum(NULLIF(attributed.metrics -> 'usage' ->> 'suspect', '')::numeric)
                FILTER (WHERE attributed.attributed_to_pool),
            0
        ) AS suspect
    FROM attributed
),
lost AS (
    SELECT
        count(*) AS lost_count,
        COALESCE(
            sum(CAST(CAST(:logical_caps AS jsonb) ->> attributed.lane_id AS numeric)),
            0
        ) AS suspect_from_lost
    FROM attributed
    WHERE attributed.status = 'lost'
      AND NOT (jsonb_exists(attributed.metrics, 'spawned'))
      AND attributed.attributed_to_pool
)
SELECT
    CAST(:pool AS text) AS pool,
    epoch.epoch_at,
    metered.metered_count,
    metered.reported_count,
    (metered.suspect_basis_count + lost.lost_count) AS suspect_basis_count,
    metered.not_spawned_count,
    lost.lost_count,
    metered.charged,
    (metered.suspect + lost.suspect_from_lost) AS suspect
FROM epoch
CROSS JOIN metered
CROSS JOIN lost
`;

/** `sql/execution/select_open_incidents.sql`, comments stripped: every non-resolved incident, oldest first. */
export const OPEN_INCIDENTS_SQL = `
SELECT
    incident.id,
    incident.fingerprint,
    incident.incident_type,
    incident.severity,
    incident.status,
    incident.summary,
    incident.occurrence_count,
    incident.first_seen_at,
    incident.last_seen_at,
    incident.cooldown_until,
    incident.owner,
    incident.acknowledged_at,
    incident.acknowledged_by,
    incident.detail ->> 'state' AS state,
    incident.detail ->> 'rung' AS rung,
    incident.detail ->> 'exit_class' AS exit_class,
    incident.detail ->> 'chain_first_seen_at' AS chain_first_seen_at
FROM agri.job_incident AS incident
WHERE incident.status <> 'resolved'
ORDER BY incident.first_seen_at
`;

/**
 * The Python side's bind values, pinned to their sources by the parity test:
 * `foundation/observability/vocabulary.py::POOL_LABELS` and `::LANE_LOGICAL_CAPS`,
 * `execution/usage_report.py::WEIGHTED_POOLS`, and `lanes/_providers/open-meteo.toml [budget]`.
 */
export const USAGE_POOLS = ["firms", "open-meteo-free", "open-meteo-paid", "usgs-water-data"] as const;
export const LANE_LOGICAL_CAPS: Readonly<Record<string, number>> = { soil: 1602 };
export const WEIGHTED_POOLS = ["open-meteo-free", "open-meteo-paid"] as const;
export const PAID_POOL_BUDGET = {
  pool: "open-meteo-paid",
  weightedCalls: 5_000_000,
  ceilingFraction: 0.6,
  stopFraction: 0.95,
} as const;

/** A `:name` bind outside a `::` cast; names missing from the values (such as `:gap` in a literal) stay text. */
const NAMED_BIND = /(?<![:\w]):([a-z_][a-z0-9_]*)/g;

/** Turn a statement with SQLAlchemy-style `:name` binds into a drizzle query with one parameter per use. */
export function bindNamedParameters(statement: string, values: Readonly<Record<string, string>>): SQL {
  const chunks: SQL[] = [];
  let cursor = 0;
  for (const match of statement.matchAll(NAMED_BIND)) {
    const name = match[1];
    if (!Object.hasOwn(values, name)) continue;
    chunks.push(sql.raw(statement.slice(cursor, match.index ?? 0)), sql`${values[name]}`);
    cursor = (match.index ?? 0) + match[0].length;
  }
  chunks.push(sql.raw(statement.slice(cursor)));
  return sql.join(chunks);
}

/** A jsonb-derived numeric (postgres-js hands `numeric` back as a string) as a number, or null. */
function numeric(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
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
          run.last_error_summary,
          run.target_partitions ->> 'executor' AS executor
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
        latest_run.last_error_summary AS last_run_error,
        latest_run.executor AS last_run_executor
      FROM newest_version
      JOIN pause_state ON pause_state.name = newest_version.name
      LEFT JOIN latest_run ON latest_run.name = newest_version.name
      ORDER BY newest_version.name
    `);

    const now = new Date();
    return resultRows<LaneRow>(result).map((row) => ({
      laneId: row.lane_id,
      version: row.version,
      handler: row.handler,
      queueName: row.queue_name,
      // A config lane's cron is its schedule; a legacy lane's string names its cadence buckets.
      scheduleCron: row.schedule_cron,
      nextFireAt: nextCronFireAfter(row.schedule_cron, now),
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
              // `job_run.last_error_summary`, written by the rollup since G1: the child's own failure text.
              error: row.last_run_error,
              // CA1: the config path marks its runs; an unmarked run is the legacy path's.
              executor: row.last_run_executor === "config" ? ("config" as const) : ("legacy" as const),
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

  /**
   * Every open incident of every kind, oldest first: lane holds with their ladder position (`state` carries
   * `<phase>:<n>`, e.g. `held:2`), budget refusals, repair breakers, quarantines. The statement is the
   * usage report's section 3 (`OPEN_INCIDENTS_SQL`, parity-pinned), so both surfaces list the same rows.
   */
  getIncidents: adminProcedure.query(async ({ ctx }) => {
    const result = await ctx.db.execute(sql.raw(OPEN_INCIDENTS_SQL));
    return resultRows<IncidentRow>(result).map((row) => ({
      id: row.id,
      fingerprint: row.fingerprint,
      kind: row.incident_type,
      severity: row.severity,
      status: row.status,
      summary: row.summary,
      occurrenceCount: row.occurrence_count,
      firstSeenAt: row.first_seen_at,
      lastSeenAt: row.last_seen_at,
      cooldownUntil: row.cooldown_until,
      owner: row.owner,
      acknowledgedAt: row.acknowledged_at,
      acknowledgedBy: row.acknowledged_by,
      state: row.state,
      rung: row.rung,
      exitClass: row.exit_class,
      chainFirstSeenAt: row.chain_first_seen_at,
    }));
  }),

  /**
   * Acknowledge one OPEN incident: the executor then logs none of its escalation events (spec §4.9.3,
   * "from G1, `acknowledged` in `/admin/jobs` suppresses escalation"). Probes, holds and the row itself
   * carry on, and the incident still resolves by its own rule; acknowledging is not releasing. An incident
   * that is already acknowledged or resolved is a CONFLICT, never a silent success.
   */
  acknowledgeIncident: adminProcedure
    .input(z.object({ incidentId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      const operator = (ctx.session.user as { id?: string }).id ?? "admin";
      const result = await ctx.db.execute(sql`
        UPDATE agri.job_incident
        SET status = 'acknowledged', acknowledged_at = now(), acknowledged_by = ${operator}, updated_at = now()
        WHERE id = ${input.incidentId} AND status = 'open'
        RETURNING id, fingerprint, acknowledged_at, acknowledged_by
      `);
      const [updated] = resultRows<{
        id: string;
        fingerprint: string;
        acknowledged_at: Date;
        acknowledged_by: string;
      }>(result);
      if (updated === undefined) {
        throw new TRPCError({
          code: "CONFLICT",
          message: `Incident '${input.incidentId}' is not open (already acknowledged, resolved, or unknown)`,
        });
      }
      return {
        incidentId: updated.id,
        fingerprint: updated.fingerprint,
        acknowledgedAt: updated.acknowledged_at,
        acknowledgedBy: updated.acknowledged_by,
      };
    }),

  /**
   * Month-to-date source usage per metering pool: the Python admission's own figures (charged, the
   * separate suspect column, the basis split, the metering epoch), plus the paid pool's two lines
   * (gap-fill admitted under 60 %, forward stopped at 95 %; WQ-4). One `PROVIDER_MONTH_TO_DATE_SQL` read
   * per pool, exactly as `usage_report.py::month_to_date` does.
   */
  getProviderUsage: adminProcedure.query(async ({ ctx }) => {
    const now = new Date();
    const bindings = {
      now: now.toISOString(),
      logical_caps: JSON.stringify(LANE_LOGICAL_CAPS),
      weighted_pools: `{${WEIGHTED_POOLS.join(",")}}`,
    };
    const pools: ProviderUsage[] = [];
    for (const pool of USAGE_POOLS) {
      const result = await ctx.db.execute(
        bindNamedParameters(PROVIDER_MONTH_TO_DATE_SQL, { ...bindings, pool })
      );
      const [row] = resultRows<MonthToDateRow>(result);
      const charged = numeric(row?.charged) ?? 0;
      const suspect = numeric(row?.suspect) ?? 0;
      const paid = pool === PAID_POOL_BUDGET.pool;
      pools.push({
        pool,
        epochAt: row?.epoch_at ?? null,
        charged,
        suspect,
        basisSplit: {
          metered: numeric(row?.metered_count) ?? 0,
          reported: numeric(row?.reported_count) ?? 0,
          suspect: numeric(row?.suspect_basis_count) ?? 0,
          notSpawned: numeric(row?.not_spawned_count) ?? 0,
          lost: numeric(row?.lost_count) ?? 0,
        },
        budget: paid
          ? {
              monthlyCap: PAID_POOL_BUDGET.weightedCalls,
              gapFillCeiling: Math.round(PAID_POOL_BUDGET.weightedCalls * PAID_POOL_BUDGET.ceilingFraction),
              forwardStop: Math.round(PAID_POOL_BUDGET.weightedCalls * PAID_POOL_BUDGET.stopFraction),
              // WQ-4: suspect above 10 % of charged refuses gap-fill until it falls.
              basisSuspect: suspect > 0.1 * charged,
            }
          : null,
      });
    }
    return { observedAt: now, pools };
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
