// Applies the parts of the pre-aggregation layer that a Drizzle migration CANNOT apply.
//
// OPS-ONLY. This script is never run by `preDeployCommand`, never by `npm run db:migrate`, and
// never by CI. It is run by hand, against production, in the order `docs/pending-migrations/
// 0029-pre-aggregation.md` sets out.
//
// WHY IT EXISTS -- the transaction trap, stated exactly.
//
// `node scripts/migrate.mjs` uses drizzle-orm/postgres-js's migrator, which wraps each migration
// FILE in a transaction. `--> statement-breakpoint` splits the file into statements, but those
// statements still share that one transaction. `CREATE INDEX CONCURRENTLY` and
// `DROP INDEX CONCURRENTLY` raise 25001 ("CREATE INDEX CONCURRENTLY cannot run inside a
// transaction block") there -- and `IF NOT EXISTS` does not save you, because PostgreSQL raises
// 25001 BEFORE it checks whether the index already exists. So no CONCURRENTLY DDL may appear in
// any drizzle migration file, ever, and the concurrent index builds live here instead.
//
// The mechanism this script relies on: postgres-js does NOT implicitly wrap a single query in a
// transaction. A statement issued through `sql.unsafe(...)` outside any `sql.begin(...)` runs in
// its own implicit transaction, which is exactly what CONCURRENTLY requires. Phase A therefore
// issues every statement bare. Phase B, which needs per-statement memory settings, uses
// `sql.begin(...)` on purpose -- a NON-concurrent `REFRESH MATERIALIZED VIEW` is perfectly legal
// inside a transaction, and `SET LOCAL` only has meaning inside one.
//
// WHAT THIS SCRIPT WILL NOT DO. It never drops anything. The design's six index drops are all
// `DROP INDEX CONCURRENTLY`, they reclaim ~5.4 GB, and every one of them is irreversible in the
// sense that rebuilding a 4,372 MB index on a 3 GB box is an outage. They are parked, unapplied,
// in `docs/pending-migrations/0030-drop-unused-indexes.sql` and require the owner to run them
// deliberately after reading the gates recorded beside each one.
//
// Usage:
//   node scripts/apply-pre-aggregation.mjs --phase=a            # concurrent index builds + ANALYZE
//   node scripts/apply-pre-aggregation.mjs --phase=b            # first populate of the nine matviews
//   node scripts/apply-pre-aggregation.mjs --phase=a --dry-run  # print the plan, touch nothing
//
// The phase flag is required and has no default: phase B reads roughly 11 GB and must never be
// started by a mistyped command.
import postgres from "postgres";

const args = new Set(process.argv.slice(2));
const dryRun = args.has("--dry-run");
const phase = args.has("--phase=a") ? "a" : args.has("--phase=b") ? "b" : null;

if (phase === null) {
  console.error(
    "apply-pre-aggregation: pass --phase=a (concurrent index builds) or --phase=b (first populate)"
  );
  process.exit(1);
}

// Same precedence as scripts/migrate.mjs, plus one dedicated override so an operator can point
// this at production without exporting the DSN the app itself would pick up.
const connectionString =
  process.env.PREAGGREGATION_DATABASE_URL ||
  process.env.MIGRATION_DATABASE_URL ||
  process.env.DATABASE_URL;
if (!connectionString) {
  console.error(
    "apply-pre-aggregation: set PREAGGREGATION_DATABASE_URL (or MIGRATION_DATABASE_URL / DATABASE_URL)"
  );
  process.exit(1);
}

/**
 * PHASE A -- the two concurrent index builds, and the ANALYZE without which neither is used.
 *
 * These are worth more than every matview in this design and are far cheaper. Sequence them first.
 */
const CONCURRENT_INDEXES = [
  {
    name: "geo.ix_features_layer_observation_day",
    // Partial on `status = 'published'` ONLY, and deliberately NOT also on
    // `geometry_id IS NOT NULL`: getMetricAtDate's summary counts the `geometry_id IS NULL` rows,
    // and excluding them here would make that count unanswerable from the index.
    //
    // `INCLUDE (geometry_id)` is what lets an index-only scan answer BOTH the census's
    // "geometry_id IS NOT NULL" filter and the metric's unlinked count without touching the heap --
    // which is the entire point, because the heap touch is what detoasts 1,467 MB of `properties`.
    //
    // Legal because `geo.feature_observation_day` is declared IMMUTABLE PARALLEL SAFE
    // (drizzle/0015_tile_observation_day.sql:21-27). The stale comment in
    // environmental-read-model.ts claiming 42P17 is true of the INLINE day expression, not of the
    // function. `src/__tests__/lib/observation-day-contract.test.ts` asserts the two agree, and it
    // is what makes reads safe to repoint at the index -- do not delete that test.
    //
    // Size, estimated: 4.97M rows x ~40 B is about 250 MB, paid for many times over by the
    // 5,392 MB of never-read index the drop script reclaims.
    sql: `CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_features_layer_observation_day
            ON geo.features (layer_id, geo.feature_observation_day(properties))
            INCLUDE (geometry_id)
            WHERE status = 'published'`,
  },
  {
    name: "geo.ix_features_updated_at",
    // Makes `SELECT max(updated_at) FROM geo.features` -- the watermark for every
    // geo.features-backed matview -- an O(1) backwards walk from the index's end. The existing
    // `idx_features_layer_updated_at` leads with `layer_id` and cannot serve it: it has read
    // 18,949,770,956 tuples to return 31,035 fetched (measured 2026-08-15). Estimated ~110 MB.
    sql: `CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_features_updated_at
            ON geo.features (updated_at DESC)`,
  },
];

/**
 * PHASE B -- the first populate, CHEAPEST FIRST so a failure is discovered cheaply.
 *
 * Every one of these is a plain, NON-concurrent `REFRESH MATERIALIZED VIEW`. That is not a
 * shortcut: `REFRESH ... CONCURRENTLY` is a hard error on a matview that has never been populated
 * ("cannot refresh materialized view ... concurrently"), and every matview drizzle/0029 creates is
 * created `WITH NO DATA`. Non-concurrent is safe here precisely because NOTHING READS THESE
 * RELATIONS YET -- the read-path deploy comes after this script. Every subsequent refresh is
 * CONCURRENTLY, driven by the `matview-refresh` lane.
 *
 * `estimate` is what the design predicts, printed beside the real count so a wildly-off number is
 * visible immediately rather than discovered by a reader weeks later. It is a sanity line, not an
 * assertion: if a source table is empty the rollup is empty, and that is the correct answer.
 */
const POPULATE_ORDER = [
  { view: "geo.mv_drought_release_index", estimate: "~208" },
  { view: "geo.mv_drought_observation_day", estimate: "~1,460" },
  { view: "geo.mv_layer_feature_stats", estimate: "11" },
  { view: "geo.mv_layer_hourly_activity", estimate: "~1,850" },
  { view: "geo.mv_soil_survey_grid", estimate: "hundreds" },
  // The largest transient allocation in the set: a hierarchical ST_Union over the whole SSURGO
  // extent. It is placed before the census views rather than last because it is the one most
  // likely to need its session settings raised, and discovering that before the 11 GB scan is
  // cheaper than discovering it after.
  { view: "geo.mv_soil_survey_union", estimate: "<= ~24" },
  // REQUIRES ix_features_layer_observation_day. Without it this is a sequential scan of a
  // 3,677 MB heap plus 1,467 MB of TOAST -- phase A is not optional before this line.
  { view: "geo.mv_feature_observation_day", estimate: "~16,000" },
  { view: "geo.mv_signal_observation_day", estimate: "~17,500" },
  // The heavy one, last: one ~11 GB governed-subset scan plus a sort.
  { view: "geo.mv_signal_cell_daily", estimate: "~17-18M" },
];

/**
 * Session settings for one populate statement.
 *
 * ONE session, so peak exposure is roughly 400 MB -- comfortably inside the 3 GB cgroup, and below
 * the ~800 MB the CURRENT global settings already risk (global work_mem 4 MB x 100 connections x
 * hash_mem_multiplier 2). This is the place extra memory is safe to spend; the global work_mem
 * must stay where it is.
 *
 * `max_parallel_workers_per_gather = 1` on purpose: two gather workers each get their own work_mem
 * allowance, and on this box the cost of a second worker's memory outweighs the wall-clock it saves
 * for an operation nobody is waiting on.
 */
const POPULATE_SESSION_SQL = [
  "SET LOCAL work_mem = '64MB'",
  "SET LOCAL maintenance_work_mem = '256MB'",
  "SET LOCAL max_parallel_workers_per_gather = 1",
  "SET LOCAL statement_timeout = '30min'",
];

const client = postgres(connectionString, {
  max: 1,
  idle_timeout: 5,
  connect_timeout: 30,
  onnotice: (notice) => {
    // The unpopulated-matview WARNING and the CONCURRENTLY build notices are the two things worth
    // seeing from an ops script; swallowing them the way the app does would hide the self-heal.
    if (notice.severity === "WARNING" || notice.severity === "NOTICE") {
      console.log(`  [${notice.severity}] ${notice.message}`);
    }
  },
});

function elapsed(startedAt) {
  return `${((Number(process.hrtime.bigint() - startedAt) / 1e9)).toFixed(1)}s`;
}

/**
 * Clears the trap that makes a retry of this script silently do nothing.
 *
 * A `CREATE INDEX CONCURRENTLY` that fails part-way -- a lock timeout, a killed session, a deploy
 * restarting the box -- leaves an INVALID index behind. An invalid index is never used by the
 * planner and never maintained, but it DOES occupy the name, so the `IF NOT EXISTS` on the retry
 * matches it and returns success while the query it was built for keeps sequential-scanning. This
 * is the single most likely way for this whole exercise to appear applied and do nothing.
 */
async function dropIfInvalid(indexName) {
  const rows = await client.unsafe(
    `SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass($1)`,
    [indexName]
  );
  if (rows.length === 0) return false;
  if (rows[0].indisvalid) return false;
  console.log(`  ${indexName} exists but is INVALID (a previous build failed); dropping it first`);
  if (dryRun) return true;
  await client.unsafe(`DROP INDEX CONCURRENTLY IF EXISTS ${indexName}`);
  return true;
}

async function runPhaseA() {
  console.log("phase A: concurrent index builds + ANALYZE\n");
  for (const index of CONCURRENT_INDEXES) {
    console.log(`- ${index.name}`);
    await dropIfInvalid(index.name);
    if (dryRun) {
      console.log(`  (dry run) ${index.sql.replace(/\s+/g, " ").trim()}`);
      continue;
    }
    const startedAt = process.hrtime.bigint();
    // Bare `unsafe`, NOT inside sql.begin: postgres-js does not implicitly open a transaction for
    // a single query, and CONCURRENTLY requires exactly that.
    await client.unsafe(index.sql);
    console.log(`  built in ${elapsed(startedAt)}`);
  }

  // NOT optional, and not a tidy-up. An expression index has NO statistics until the table is
  // analyzed, and without them the planner keeps choosing the sequential scan the index was built
  // to replace -- so a skipped ANALYZE looks exactly like an index that did not help.
  console.log("- ANALYZE geo.features");
  if (!dryRun) {
    const startedAt = process.hrtime.bigint();
    await client.unsafe("ANALYZE geo.features");
    console.log(`  analyzed in ${elapsed(startedAt)}`);
  }

  console.log(
    "\nphase A done. Next: EXPLAIN the census query and confirm the day bounds appear as an\n" +
      "Index Cond rather than a Filter, record the wall-clock, then apply drizzle/0029 and the\n" +
      "alembic revision out-of-band before running phase B."
  );
}

async function runPhaseB() {
  console.log("phase B: first populate, cheapest first\n");
  for (const target of POPULATE_ORDER) {
    console.log(`- ${target.view} (design estimate ${target.estimate} rows)`);
    if (dryRun) {
      console.log(`  (dry run) REFRESH MATERIALIZED VIEW ${target.view}`);
      continue;
    }
    const startedAt = process.hrtime.bigint();
    // A transaction HERE, unlike phase A: `SET LOCAL` has no effect outside one, and a
    // non-concurrent REFRESH is legal inside one.
    const rowCount = await client.begin(async (tx) => {
      for (const setting of POPULATE_SESSION_SQL) await tx.unsafe(setting);
      await tx.unsafe(`REFRESH MATERIALIZED VIEW ${target.view}`);
      const counted = await tx.unsafe(`SELECT count(*)::bigint AS row_count FROM ${target.view}`);
      return counted[0]?.row_count ?? null;
    });
    console.log(`  populated ${rowCount} rows in ${elapsed(startedAt)}`);
  }

  console.log(
    "\nphase B done. Every subsequent refresh is CONCURRENTLY, driven by the matview-refresh lane\n" +
      "in the jobs pulse. Deploy the pulse slice and the read-path slice next; only then add the\n" +
      "journal entries for 0028 and 0029 and re-pin migration-contract.ts."
  );
}

try {
  // `lock_timeout` so a build that cannot get its lock gives up in five seconds instead of queueing
  // behind an ingest and blocking every writer behind ITSELF; `statement_timeout = 0` because the
  // signal rollup's populate legitimately runs for minutes and must not be cut off half way.
  await client.unsafe("SET lock_timeout = '5s'");
  await client.unsafe("SET statement_timeout = 0");
  if (phase === "a") await runPhaseA();
  else await runPhaseB();
} catch (error) {
  console.error(`apply-pre-aggregation: phase ${phase} failed`);
  console.error(error);
  process.exitCode = 1;
} finally {
  await client.end({ timeout: 30 }).catch(() => {});
}
