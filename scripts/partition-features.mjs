// Converts geo.features into a LIST-partitioned table on layer_id, out of band.
//
// OPS-ONLY. This script is never run by `preDeployCommand`, never by `npm run db:migrate`, and
// never by CI. It is run by hand, against production, in the order
// `docs/pending-migrations/0033-features-partitioning.md` sets out.
//
// WHY IT IS NOT A DRIZZLE MIGRATION. Every drizzle `.sql` file runs inside ONE transaction
// (`scripts/migrate.mjs` -> drizzle-orm/postgres-js's migrator; the rule is stated at
// `drizzle/0031_observation_day_axis.sql:27-28`). A 5,080,640-row / 7,872 MB copy inside one
// transaction holds its snapshot and its locks for the whole run, and the two big layers alone
// would keep an open transaction for the better part of an hour on a 2 GB box. So the copy lives
// here, one bounded transaction per chunk, and the migration that records the change carries
// nothing but a `DO $$` precondition assert -- the pattern `drizzle/0030:196-224` and
// `drizzle/0032:41-51` already set.
//
// NEVER `ALTER` IN PLACE. There is no in-place conversion of a heap to a partitioned table.
// Create new -> copy per layer -> verify -> rename. The old heap is kept under a new name so the
// swap is reversible; see `--phase=rollback` and the loss window it prints.
//
// THE THREE FACTS THIS SCRIPT IS BUILT ON, all verified against production PG 18.4 on 2026-08-20:
//   1. `PRIMARY KEY (id, layer_id)` on a LIST-partitioned parent is legal. The partition key must
//      appear in every unique index, which is why `src/lib/server/db/schema.ts` had to move off a
//      single-column PK in the same commit as this file.
//   2. A PARTIAL unique index on the parent is legal -- `features_layer_external_id_unique` over
//      `(layer_id, ((properties->>'id'))) WHERE (properties ? 'id')` builds and enforces. Global
//      uniqueness still holds because one `layer_id` implies exactly one partition.
//   3. `CREATE INDEX CONCURRENTLY` on a partitioned PARENT is NOT supported -- it raises
//      "cannot create index on partitioned table ... concurrently". That is why this script builds
//      every index with a plain `CREATE INDEX`: before the swap NOTHING reads `geo.features_new`,
//      so the ACCESS EXCLUSIVE lock a plain build takes is invisible to production. The
//      ON ONLY -> per-partition CONCURRENTLY -> `ALTER INDEX ... ATTACH PARTITION` dance is for
//      indexes added AFTER the swap, when the table is live; `scripts/apply-pre-aggregation.mjs`
//      owns that path.
//
// WHAT THIS SCRIPT WILL NOT DO. It never drops `geo.features_legacy`, never drops a dependent
// matview, and never swaps on a failed verification. It also does not quiesce the writers -- the
// three Railway crons and `plantgeo-main` are stopped by hand, and `--phase=swap` will tell you
// what it can see holding locks but cannot stop them for you.
//
// Usage (phase is required and has no default -- a mistyped command must not start an 8 GB copy):
//   node scripts/partition-features.mjs --phase=plan       # read-only: census, objects, dependents
//   node scripts/partition-features.mjs --phase=create     # parent + 11 partitions + DEFAULT + ledger
//   node scripts/partition-features.mjs --phase=copy       # per-layer chunked copy, smallest first
//   node scripts/partition-features.mjs --phase=copy --layer=vegetation
//   node scripts/partition-features.mjs --phase=copy --catchup   # reconcile a hot copy (after index)
//   node scripts/partition-features.mjs --phase=index      # PK, 8 parent indexes, per-partition geom, 2 FKs
//   node scripts/partition-features.mjs --phase=trigger    # geo_features_sync_geom, AFTER every copy
//   node scripts/partition-features.mjs --phase=verify     # per-layer counts + structural checks
//   node scripts/partition-features.mjs --phase=swap       # verify, then the two-step rename
//   node scripts/partition-features.mjs --phase=analyze    # every partition, then the parent
//   node scripts/partition-features.mjs --phase=adopt --layer=<name>   # partition a runtime layer
//   node scripts/partition-features.mjs --phase=rollback --force       # reverse the rename
//   ... any phase with --dry-run to print the plan and touch nothing.
import postgres from "postgres";

const PHASES = [
  "plan",
  "create",
  "copy",
  "index",
  "trigger",
  "verify",
  "swap",
  "analyze",
  "adopt",
  "rollback",
];

const args = process.argv.slice(2);
const flags = new Set(args.filter((arg) => !arg.includes("=")));
const options = new Map(
  args
    .filter((arg) => arg.includes("="))
    .map((arg) => [arg.slice(0, arg.indexOf("=")), arg.slice(arg.indexOf("=") + 1)])
);

const dryRun = flags.has("--dry-run");
const force = flags.has("--force");
const catchUp = flags.has("--catchup");
const layerOption = options.get("--layer") ?? null;
const phase = options.get("--phase") ?? null;

if (phase === null || !PHASES.includes(phase)) {
  console.error(`partition-features: pass --phase=<${PHASES.join("|")}>`);
  process.exit(1);
}
if (phase === "adopt" && layerOption === null) {
  console.error("partition-features: --phase=adopt requires --layer=<layer name>");
  process.exit(1);
}

// Same precedence as scripts/apply-pre-aggregation.mjs, plus one dedicated override so an operator
// can point this at production without exporting the DSN the app itself would pick up.
const connectionString =
  process.env.PARTITION_DATABASE_URL ||
  process.env.MIGRATION_DATABASE_URL ||
  process.env.DATABASE_URL;
if (!connectionString) {
  console.error(
    "partition-features: set PARTITION_DATABASE_URL (or MIGRATION_DATABASE_URL / DATABASE_URL)"
  );
  process.exit(1);
}

const SOURCE_TABLE = "geo.features";
const TARGET_TABLE = "geo.features_new";
const LEGACY_TABLE = "geo.features_legacy";
const DEFAULT_PARTITION = "geo.features_default";
const COPY_LEDGER = "geo.features_partition_copy_log";

/** Suffix every new index carries until the swap renames it; index names are schema-unique. */
const BUILD_SUFFIX = "_swap";
/** Prefix the outgoing heap's indexes are renamed to, so `\d geo.features_legacy` is unambiguous. */
const LEGACY_PREFIX = "legacy_";

/**
 * The column contract, in production's physical `attnum` order (runbook §0.3).
 *
 * Order is preserved deliberately: `SELECT *` appears in ad-hoc SQL and in the agri agent's SQL
 * files, and a reordered table changes what those return. `--phase=create` asserts the live table
 * matches this list exactly -- name, type, NOT NULL and default -- and refuses to build anything if
 * it does not, because every later phase names these columns explicitly.
 */
const FEATURE_COLUMNS = [
  { name: "id", type: "uuid", notNull: true, default: "gen_random_uuid()" },
  { name: "layer_id", type: "uuid", notNull: true, default: null },
  { name: "properties", type: "jsonb", notNull: true, default: "'{}'::jsonb" },
  {
    name: "status",
    type: "character varying(20)",
    notNull: false,
    default: "'published'::character varying",
  },
  { name: "review_note", type: "text", notNull: false, default: null },
  { name: "created_at", type: "timestamp with time zone", notNull: false, default: "now()" },
  { name: "updated_at", type: "timestamp with time zone", notNull: false, default: "now()" },
  { name: "geom", type: "geometry(Geometry,4326)", notNull: false, default: null },
  { name: "geometry_id", type: "uuid", notNull: false, default: null },
  { name: "data_available_at", type: "timestamp with time zone", notNull: false, default: null },
];

const COLUMN_LIST = FEATURE_COLUMNS.map((column) => column.name).join(", ");

/**
 * Measured per-layer counts, production 2026-08-20 (runbook §0.3).
 *
 * Printed beside the live census so a wildly-off number is visible immediately rather than
 * discovered by a reader weeks later. It is a sanity line, not an assertion -- ingestion is
 * continuous and these numbers only ever grow.
 */
const EXPECTED_CENSUS = {
  "fire-detections": 3019709,
  "water-gauges": 1413932,
  "soil-survey": 238986,
  vegetation: 185031,
  sensors: 180654,
  "weather-observations": 31569,
  watersheds: 9396,
  "evacuation-zones": 648,
  "burn-severity": 541,
  "fire-perimeters": 172,
  interventions: 2,
};

/**
 * Rows per copy statement.
 *
 * `INSERT ... SELECT` streams -- there is no sort and no hash -- so the working set is the tuple
 * buffer plus WAL, not the batch size. The chunking exists so that a failure, a lock timeout or a
 * killed session costs one bounded re-run instead of an hour, and so the two big layers cannot
 * hold a snapshot open long enough to matter. Nine of the eleven layers fit in a single chunk.
 */
const CHUNK_TARGET_ROWS = 250_000;

/**
 * The two layers that do NOT get a per-partition `geom` GiST index (runbook §0.3).
 *
 * They own 87.6% of the 313 MB `idx_features_geom` between them and are served by no tile
 * function. They keep spatial access through `ix_features_layer_geom`, which is a parent index and
 * therefore exists on every partition -- so what is actually given up is unindexed spatial access
 * to their UNPUBLISHED rows, which nothing queries.
 */
const GEOM_INDEX_EXCLUDED_LAYERS = new Set(["fire-detections", "water-gauges"]);

/**
 * The parent indexes, by their FINAL name.
 *
 * Nine of production's eleven indexes are here. The two that are not:
 *   * `idx_features_layer` (layer_id) -- 61 MB, DROPPED OUTRIGHT. `layer_id` is constant within a
 *     partition, so the index answers nothing the partition constraint does not already answer.
 *   * `idx_features_geom` USING GIST (geom) -- cannot be a parent index at all, because a parent
 *     index requires a matching child on EVERY partition and two partitions deliberately go
 *     without. It is created per-partition instead, see `partitionGeomIndexName`.
 *
 * `features_pkey` is a constraint, not a bare index, and is handled separately.
 *
 * The three composite indexes that lead with `layer_id` are re-created UNCHANGED. Runbook §0.3
 * proposes narrowing them (the leading column is redundant inside a partition, ~213 MB across the
 * three) -- deliberately NOT done here: it changes plan shapes on read paths that are being
 * rewritten in the same batch, and it is a cheap follow-up once post-swap `EXPLAIN` output exists.
 */
const PARENT_INDEXES = [
  {
    name: "features_layer_external_id_unique",
    // The readiness probe names this index literally -- `src/app/api/ready/route.ts:60` does
    // `to_regclass('geo.features_layer_external_id_unique')` and a missing one 503s the probe,
    // fails the Railway healthcheck and blocks every deploy. Partial unique indexes on a
    // partitioned parent are legal (verified 2026-08-20); global uniqueness holds because one
    // layer_id implies exactly one partition.
    definition: `UNIQUE (layer_id, (properties ->> 'id')) WHERE properties ? 'id'`,
  },
  {
    name: "ix_features_layer_geom",
    // The tile path's access method; see the COMMENT ON INDEX in drizzle/0030. Needs btree_gist
    // for the uuid leading column, which `--phase=create` asserts is installed.
    definition: `USING gist (layer_id, geom) WHERE status = 'published' AND geom IS NOT NULL`,
  },
  {
    name: "ix_features_layer_observation_day",
    // Built by scripts/apply-pre-aggregation.mjs --phase=a; re-created here byte-for-byte.
    // `geo.feature_observation_day` is IMMUTABLE PARALLEL SAFE (drizzle/0015:21-27), which is what
    // makes it indexable at all.
    definition: `(layer_id, geo.feature_observation_day(properties))
       INCLUDE (geometry_id)
       WHERE status = 'published'`,
  },
  { name: "ix_features_geometry_id", definition: `(geometry_id)` },
  { name: "idx_features_layer_updated_at", definition: `(layer_id, updated_at)` },
  { name: "idx_features_layer_created_at", definition: `(layer_id, created_at)` },
  { name: "idx_features_layer_status", definition: `(layer_id, status)` },
  {
    name: "ix_features_updated_at",
    // The watermark index for every geo.features-backed matview. Post-swap this is a MergeAppend
    // over N per-partition indexes rather than one backwards walk -- re-measure it, runbook §0.6.
    definition: `(updated_at DESC)`,
  },
];

const PRIMARY_KEY_CONSTRAINT = "features_pkey";

/** Both outbound FKs (runbook §0.3). Constraint names are per-table, so these need no suffix. */
const FOREIGN_KEYS = [
  {
    name: "features_layer_id_layers_id_fk",
    definition: `FOREIGN KEY (layer_id) REFERENCES geo.layers(id) ON DELETE CASCADE`,
    referenced: "geo.layers",
  },
  {
    name: "features_geometry_id_fkey",
    definition: `FOREIGN KEY (geometry_id) REFERENCES geo.geometry(geometry_id) ON DELETE RESTRICT`,
    referenced: "geo.geometry",
  },
];

const SYNC_TRIGGER = "geo_features_sync_geom";

/** Lock waits. A queued ACCESS EXCLUSIVE blocks every reader behind it, so never queue -- retry. */
const LOCK_TIMEOUT = "5s";
const LOCK_ATTEMPTS = 20;
const LOCK_RETRY_SECONDS = 15;

const client = postgres(connectionString, {
  max: 1,
  idle_timeout: 5,
  connect_timeout: 30,
  onnotice: (notice) => {
    if (notice.severity === "WARNING" || notice.severity === "NOTICE") {
      console.log(`  [${notice.severity}] ${notice.message}`);
    }
  },
});

function elapsed(startedAt) {
  return `${(Number(process.hrtime.bigint() - startedAt) / 1e9).toFixed(1)}s`;
}

function sleep(seconds) {
  return new Promise((resolve) => setTimeout(resolve, seconds * 1000));
}

function formatCount(value) {
  return Number(value).toLocaleString("en-US");
}

/** `fire-detections` -> `features_fire_detections`. Longest today is 29 bytes, well under 63. */
function partitionName(layerName) {
  return `features_${layerName.replace(/[^A-Za-z0-9]+/g, "_").toLowerCase()}`;
}

function qualifiedPartition(layerName) {
  return `geo.${partitionName(layerName)}`;
}

/** Matches the child-index naming `scripts/apply-pre-aggregation.mjs` uses for its own builds. */
function partitionGeomIndexName(layerName) {
  return `${partitionName(layerName)}_geom_idx`;
}

/** A SQL literal for a uuid we read out of geo.layers ourselves -- never client input. */
function uuidLiteral(value) {
  if (!/^[0-9a-fA-F-]{36}$/.test(value)) {
    throw new Error(`partition-features: refusing to interpolate a non-uuid layer id: ${value}`);
  }
  return `'${value}'::uuid`;
}

function timestampLiteral(value) {
  return `'${new Date(value).toISOString()}'::timestamptz`;
}

async function execute(sqlText, { label = null } = {}) {
  if (dryRun) {
    console.log(`  (dry run) ${sqlText.replace(/\s+/g, " ").trim()}`);
    return null;
  }
  const startedAt = process.hrtime.bigint();
  const result = await client.unsafe(sqlText);
  if (label) console.log(`  ${label} in ${elapsed(startedAt)}`);
  return result;
}

/**
 * Runs `work` under a short `lock_timeout`, retrying instead of queueing.
 *
 * Ingestion is continuous (runbook §0.7: a live ingest backend is what made the probe
 * `CREATE INDEX CONCURRENTLY` time out at 30 s). An ACCESS EXCLUSIVE request that waits does not
 * merely wait -- every reader that arrives behind it waits too, so a queued rename is a full
 * outage for as long as it queues. 55P03 is `lock_not_available`.
 */
async function withLockRetry(label, work) {
  for (let attempt = 1; attempt <= LOCK_ATTEMPTS; attempt += 1) {
    try {
      return await work();
    } catch (error) {
      if (error.code !== "55P03" || attempt === LOCK_ATTEMPTS) throw error;
      console.log(
        `  ${label}: lock not granted within ${LOCK_TIMEOUT} ` +
          `(attempt ${attempt}/${LOCK_ATTEMPTS}); retrying in ${LOCK_RETRY_SECONDS}s`
      );
      await sleep(LOCK_RETRY_SECONDS);
    }
  }
  return null;
}

async function relationExists(qualifiedName) {
  const rows = await client.unsafe(`SELECT to_regclass($1) IS NOT NULL AS present`, [qualifiedName]);
  return rows[0].present === true;
}

async function isPartitioned(qualifiedName) {
  const rows = await client.unsafe(
    `SELECT relkind = 'p' AS partitioned FROM pg_class WHERE oid = to_regclass($1)`,
    [qualifiedName]
  );
  return rows.length > 0 && rows[0].partitioned === true;
}

/** True once `geo.features` is the partitioned parent -- the one flag every phase branches on. */
async function swapIsDone() {
  return isPartitioned(SOURCE_TABLE);
}

async function readLayers() {
  return client.unsafe(`SELECT id, name FROM geo.layers ORDER BY name`);
}

/**
 * One index-only walk of `idx_features_layer` gives both the copy batches and the swap's proof.
 *
 * `dated` is the non-NULL `created_at` population, which is what the chunk boundaries are computed
 * over -- rows with a NULL `created_at` get their own final chunk.
 */
async function readCensus(table) {
  const rows = await client.unsafe(
    `SELECT l.name,
            f.layer_id,
            count(*)::bigint          AS total,
            count(f.created_at)::bigint AS dated
       FROM ${table} AS f
       JOIN geo.layers AS l ON l.id = f.layer_id
      GROUP BY l.name, f.layer_id
      ORDER BY count(*) ASC`
  );
  return rows.map((row) => ({
    name: row.name,
    layerId: row.layer_id,
    total: Number(row.total),
    dated: Number(row.dated),
  }));
}

/**
 * Views and matviews whose stored rule references the table -- the swap's quietest hazard.
 *
 * A rewrite rule stores the relation's OID, not its name. `ALTER TABLE ... RENAME` does not
 * rewrite it, so after the swap EVERY relation listed here still reads the orphaned legacy heap
 * and keeps doing so, successfully and silently, forever. At least seven exist today
 * (`mv_feature_observation_day`, `mv_layer_feature_stats`, `mv_layer_hourly_activity`,
 * `mv_soil_survey_grid`, `mv_soil_survey_union`, `mv_feature_observation_day_axis`,
 * `watershed_rollup`). Each must be dropped and re-created from its migration after the rename;
 * see `docs/pending-migrations/0033-features-partitioning.md` §"Dependent relations".
 */
async function readDependentRelations(table) {
  return client.unsafe(
    `SELECT DISTINCT dependent.relname AS name,
            CASE dependent.relkind WHEN 'm' THEN 'matview' WHEN 'v' THEN 'view' ELSE dependent.relkind::text END AS kind
       FROM pg_depend AS d
       JOIN pg_rewrite AS r ON r.oid = d.objid
       JOIN pg_class   AS dependent ON dependent.oid = r.ev_class
      WHERE d.classid = 'pg_rewrite'::regclass
        AND d.refclassid = 'pg_class'::regclass
        AND d.refobjid = to_regclass($1)
        AND dependent.oid <> to_regclass($1)
      ORDER BY 2, 1`,
    [table]
  );
}

/** Every index on a relation, with the constraint name when one owns it (the PK). */
async function readIndexes(table) {
  return client.unsafe(
    `SELECT c.relname AS index_name,
            con.conname AS constraint_name,
            i.indisvalid,
            i.indisready
       FROM pg_index AS i
       JOIN pg_class AS c ON c.oid = i.indexrelid
       LEFT JOIN pg_constraint AS con ON con.conindid = i.indexrelid
      WHERE i.indrelid = to_regclass($1)
      ORDER BY c.relname`,
    [table]
  );
}

async function readPartitions(parent) {
  return client.unsafe(
    `SELECT child.relname AS name,
            pg_get_expr(child.relpartbound, child.oid) AS bound
       FROM pg_inherits AS inh
       JOIN pg_class AS child ON child.oid = inh.inhrelid
      WHERE inh.inhparent = to_regclass($1)
      ORDER BY child.relname`,
    [parent]
  );
}

// ---------------------------------------------------------------------------------------------
// PHASE plan -- read-only. Everything an operator needs before touching anything.
// ---------------------------------------------------------------------------------------------

async function runPlan() {
  const done = await swapIsDone();
  const targetPresent = await relationExists(TARGET_TABLE);
  const legacyPresent = await relationExists(LEGACY_TABLE);

  console.log(`swap state: ${done ? "DONE -- geo.features is partitioned" : "not started"}`);
  console.log(`  ${TARGET_TABLE}: ${targetPresent ? "present" : "absent"}`);
  console.log(`  ${LEGACY_TABLE}: ${legacyPresent ? "present" : "absent"}\n`);

  const census = await readCensus(SOURCE_TABLE);
  console.log("live census (copy batches run in this order, smallest first):");
  let total = 0;
  for (const layer of census) {
    total += layer.total;
    const expected = EXPECTED_CENSUS[layer.name];
    const drift =
      expected === undefined
        ? " (not in the 2026-08-20 census)"
        : layer.total === expected
          ? ""
          : ` (2026-08-20: ${formatCount(expected)})`;
    const chunks =
      layer.total <= CHUNK_TARGET_ROWS ? 1 : Math.ceil(layer.dated / CHUNK_TARGET_ROWS) + 1;
    console.log(
      `  ${layer.name.padEnd(22)} ${formatCount(layer.total).padStart(11)} rows` +
        `  ${String(chunks).padStart(3)} chunk(s)${drift}`
    );
  }
  console.log(`  ${"total".padEnd(22)} ${formatCount(total).padStart(11)} rows\n`);

  if (targetPresent) {
    const copied = await readCensus(TARGET_TABLE);
    const copiedTotal = copied.reduce((sum, layer) => sum + layer.total, 0);
    console.log(`${TARGET_TABLE} holds ${formatCount(copiedTotal)} rows across ${copied.length} layer(s)\n`);
  }

  const dependents = await readDependentRelations(SOURCE_TABLE);
  console.log(`relations whose stored rule points at ${SOURCE_TABLE}'s OID (${dependents.length}):`);
  for (const dependent of dependents) console.log(`  ${dependent.kind.padEnd(8)} geo.${dependent.name}`);
  console.log(
    "  ^ every one of these follows the OID through the rename and would keep reading the\n" +
      "    orphaned legacy heap. Drop and re-create each from its migration AFTER the swap.\n"
  );

  const layers = await readLayers();
  console.log(`geo.layers holds ${layers.length} layer(s); the DEFAULT partition covers the rest.`);
}

// ---------------------------------------------------------------------------------------------
// PHASE create -- preconditions, the parent, one partition per layer, the DEFAULT, the ledger.
// ---------------------------------------------------------------------------------------------

/** Refuses to build anything that differs from the live table in any way it knows how to check. */
async function assertColumnContract() {
  const live = await client.unsafe(
    `SELECT a.attname AS name,
            format_type(a.atttypid, a.atttypmod) AS type,
            a.attnotnull AS not_null,
            pg_get_expr(d.adbin, d.adrelid) AS default_expr
       FROM pg_attribute AS a
       LEFT JOIN pg_attrdef AS d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
      WHERE a.attrelid = to_regclass($1) AND a.attnum > 0 AND NOT a.attisdropped
      ORDER BY a.attnum`,
    [SOURCE_TABLE]
  );

  const problems = [];
  if (live.length !== FEATURE_COLUMNS.length) {
    problems.push(`column count is ${live.length}, expected ${FEATURE_COLUMNS.length}`);
  }
  FEATURE_COLUMNS.forEach((expected, position) => {
    const actual = live[position];
    if (!actual) {
      problems.push(`column ${position + 1} (${expected.name}) is missing`);
      return;
    }
    if (actual.name !== expected.name) {
      problems.push(`column ${position + 1} is "${actual.name}", expected "${expected.name}"`);
      return;
    }
    if (actual.type !== expected.type) {
      problems.push(`${expected.name} is ${actual.type}, expected ${expected.type}`);
    }
    if (actual.not_null !== expected.notNull) {
      problems.push(`${expected.name} NOT NULL is ${actual.not_null}, expected ${expected.notNull}`);
    }
    if ((actual.default_expr ?? null) !== expected.default) {
      problems.push(
        `${expected.name} default is ${actual.default_expr ?? "none"}, expected ${expected.default ?? "none"}`
      );
    }
  });

  if (problems.length > 0) {
    throw new Error(
      "partition-features: geo.features has drifted from FEATURE_COLUMNS -- reconcile the constant " +
        "in this script before building anything:\n  " +
        problems.join("\n  ")
    );
  }
  console.log(`- column contract: ${FEATURE_COLUMNS.length} columns match, in order`);
}

async function assertPreconditions() {
  await assertColumnContract();

  const [extension] = await client.unsafe(
    `SELECT count(*)::int AS present FROM pg_extension WHERE extname = 'btree_gist'`
  );
  if (extension.present === 0) {
    throw new Error(
      "partition-features: btree_gist is not installed, so ix_features_layer_geom cannot be built " +
        "(GiST has no native uuid operator class). CREATE EXTENSION btree_gist first."
    );
  }
  console.log("- btree_gist: installed");

  const [dayFunction] = await client.unsafe(
    `SELECT to_regprocedure('geo.feature_observation_day(jsonb)') IS NOT NULL AS present`
  );
  if (dayFunction.present !== true) {
    throw new Error(
      "partition-features: geo.feature_observation_day(jsonb) is missing, so " +
        "ix_features_layer_observation_day cannot be built. Apply drizzle/0015 first."
    );
  }
  console.log("- geo.feature_observation_day(jsonb): present");

  if (await swapIsDone()) {
    throw new Error("partition-features: geo.features is already partitioned; nothing to create.");
  }
}

async function runCreate() {
  console.log("phase create: preconditions, parent, partitions, ledger\n");
  await assertPreconditions();

  const columnDdl = FEATURE_COLUMNS.map((column) => {
    const notNull = column.notNull ? " NOT NULL" : "";
    const defaultClause = column.default ? ` DEFAULT ${column.default}` : "";
    return `  ${column.name} ${column.type}${notNull}${defaultClause}`;
  }).join(",\n");

  console.log(`\n- ${TARGET_TABLE} PARTITION BY LIST (layer_id)`);
  await execute(
    `CREATE TABLE IF NOT EXISTS ${TARGET_TABLE} (\n${columnDdl}\n) PARTITION BY LIST (layer_id)`,
    { label: "created" }
  );

  const layers = await readLayers();
  for (const layer of layers) {
    const table = qualifiedPartition(layer.name);
    console.log(`- ${table} FOR VALUES IN (${layer.name})`);
    await execute(
      `CREATE TABLE IF NOT EXISTS ${table}
         PARTITION OF ${TARGET_TABLE}
         FOR VALUES IN (${uuidLiteral(layer.id)})`,
      { label: "created" }
    );
  }

  // MANDATORY, not a safety net. `layersRouter.create` is a contributorProcedure that mints a
  // geo.layers row at request time (src/lib/server/trpc/routers/layers.ts:140-149) and the new id
  // can receive features immediately. Without a DEFAULT partition the first such write fails with
  // "no partition of relation \"features\" found for row" -- a 500 on submitObservation and a
  // per-job hard failure that flips the ingest cron's exit code. Runbook §0.4.
  console.log(`- ${DEFAULT_PARTITION} DEFAULT (mandatory -- layers are created at runtime)`);
  await execute(`CREATE TABLE IF NOT EXISTS ${DEFAULT_PARTITION} PARTITION OF ${TARGET_TABLE} DEFAULT`, {
    label: "created",
  });

  console.log(`- ${COPY_LEDGER}`);
  await execute(
    `CREATE TABLE IF NOT EXISTS ${COPY_LEDGER} (
       layer_id        uuid        NOT NULL,
       layer_name      text        NOT NULL,
       chunk_index     integer     NOT NULL,
       chunk_predicate text        NOT NULL,
       copied_rows     bigint      NOT NULL,
       copied_at       timestamptz NOT NULL DEFAULT now(),
       PRIMARY KEY (layer_id, chunk_index)
     )`,
    { label: "created" }
  );

  console.log(
    `\nphase create done. ${layers.length} layer partitions + DEFAULT.\n` +
      "Next: quiesce the three Railway crons and plantgeo-main (runbook §0.8 step 1), then\n" +
      "`--phase=copy`. A copy run against live writers is supported but needs `--catchup` later."
  );
}

// ---------------------------------------------------------------------------------------------
// PHASE copy -- per layer, smallest first, chunked, resumable through the ledger.
// ---------------------------------------------------------------------------------------------

/**
 * Chunk predicates for one layer, as SQL text.
 *
 * Boundaries are walked off `idx_features_layer_created_at` with `ORDER BY created_at OFFSET n
 * LIMIT 1` -- an index-only seek, no sort, flat memory. `percentile_disc` or `ntile` would need
 * the whole layer in a tuplestore, which is exactly what a 2 GB box cannot afford.
 *
 * Rows sharing a boundary timestamp all fall into the later chunk (`>= lo AND < hi`), so the
 * chunks stay disjoint and exhaustive even when ingestion writes thousands of rows in one second.
 * The final time chunk is unbounded above so rows that land during a hot copy have somewhere to
 * go; `--catchup` is what actually collects them.
 */
async function chunkPredicates(layer) {
  if (layer.total <= CHUNK_TARGET_ROWS) return ["TRUE"];

  const chunkCount = Math.ceil(layer.dated / CHUNK_TARGET_ROWS);
  const boundaries = [];
  for (let index = 1; index < chunkCount; index += 1) {
    const rows = await client.unsafe(
      `SELECT created_at
         FROM ${SOURCE_TABLE}
        WHERE layer_id = $1 AND created_at IS NOT NULL
        ORDER BY created_at
        OFFSET $2 LIMIT 1`,
      [layer.layerId, index * CHUNK_TARGET_ROWS]
    );
    if (rows.length === 0) break;
    const literal = timestampLiteral(rows[0].created_at);
    if (!boundaries.includes(literal)) boundaries.push(literal);
  }

  const predicates = [];
  boundaries.forEach((boundary, index) => {
    predicates.push(
      index === 0 ? `created_at < ${boundary}` : `created_at >= ${boundaries[index - 1]} AND created_at < ${boundary}`
    );
  });
  predicates.push(
    boundaries.length === 0 ? "created_at IS NOT NULL" : `created_at >= ${boundaries[boundaries.length - 1]}`
  );
  // Always last, always present: NULL is excluded by every range predicate above.
  predicates.push("created_at IS NULL");
  return predicates;
}

async function readLedger(layerId) {
  const rows = await client.unsafe(
    `SELECT chunk_index, chunk_predicate, copied_rows FROM ${COPY_LEDGER} WHERE layer_id = $1 ORDER BY chunk_index`,
    [layerId]
  );
  return new Map(rows.map((row) => [row.chunk_index, row]));
}

/**
 * One chunk, one transaction, ledger row included.
 *
 * The ledger row commits with the rows it describes, so a crash rolls both back and a resume can
 * simply skip what is recorded. That is what makes this phase idempotent without needing the PK
 * (which is not built until `--phase=index`) or a DELETE-then-reinsert repair path.
 */
async function copyChunk(layer, chunkIndex, predicate) {
  const startedAt = process.hrtime.bigint();
  const copied = await client.begin(async (tx) => {
    await tx.unsafe("SET LOCAL work_mem = '32MB'");
    await tx.unsafe("SET LOCAL max_parallel_workers_per_gather = 1");
    await tx.unsafe("SET LOCAL statement_timeout = '60min'");
    const result = await tx.unsafe(
      `INSERT INTO ${TARGET_TABLE} (${COLUMN_LIST})
       SELECT ${COLUMN_LIST} FROM ${SOURCE_TABLE}
        WHERE layer_id = ${uuidLiteral(layer.layerId)} AND (${predicate})`
    );
    await tx.unsafe(
      `INSERT INTO ${COPY_LEDGER} (layer_id, layer_name, chunk_index, chunk_predicate, copied_rows)
       VALUES ($1, $2, $3, $4, $5)`,
      [layer.layerId, layer.name, chunkIndex, predicate, result.count]
    );
    return result.count;
  });
  console.log(`    chunk ${chunkIndex}: ${formatCount(copied)} rows in ${elapsed(startedAt)}`);
  return copied;
}

/**
 * The reconcile pass for a copy that ran against live writers.
 *
 * Needs the PK, so it runs AFTER `--phase=index`. It collects rows INSERTED during the copy
 * window; it cannot see an in-place UPDATE of a row that was already copied, because the row count
 * does not move. If the writers were quiesced for the whole copy (runbook §0.8 step 1) this phase
 * has nothing to do and is a no-op -- which is the honest reason to quiesce.
 */
async function catchUpLayer(layer) {
  const startedAt = process.hrtime.bigint();
  const copied = await client.begin(async (tx) => {
    await tx.unsafe("SET LOCAL work_mem = '32MB'");
    await tx.unsafe("SET LOCAL statement_timeout = '60min'");
    const result = await tx.unsafe(
      `INSERT INTO ${TARGET_TABLE} (${COLUMN_LIST})
       SELECT ${COLUMN_LIST} FROM ${SOURCE_TABLE} AS source
        WHERE source.layer_id = ${uuidLiteral(layer.layerId)}
          AND NOT EXISTS (
                SELECT 1 FROM ${TARGET_TABLE} AS copied
                 WHERE copied.id = source.id AND copied.layer_id = source.layer_id)`
    );
    return result.count;
  });
  console.log(`    caught up ${formatCount(copied)} row(s) in ${elapsed(startedAt)}`);
  return copied;
}

async function runCopy() {
  if (!(await relationExists(TARGET_TABLE))) {
    throw new Error(`partition-features: ${TARGET_TABLE} does not exist; run --phase=create first.`);
  }

  // A layer created between `create` and `copy` has no partition, and its rows would land in the
  // DEFAULT partition instead. Cheap to prevent, expensive to discover at verify time.
  const existingPartitions = new Set((await readPartitions(TARGET_TABLE)).map((row) => row.name));
  for (const layer of await readLayers()) {
    if (existingPartitions.has(partitionName(layer.name))) continue;
    console.log(`- ${qualifiedPartition(layer.name)} appeared since --phase=create; creating it`);
    await execute(
      `CREATE TABLE IF NOT EXISTS ${qualifiedPartition(layer.name)}
         PARTITION OF ${TARGET_TABLE} FOR VALUES IN (${uuidLiteral(layer.id)})`
    );
  }

  const census = (await readCensus(SOURCE_TABLE)).filter(
    (layer) => layerOption === null || layer.name === layerOption
  );
  if (census.length === 0) {
    throw new Error(`partition-features: no layer matched --layer=${layerOption}`);
  }

  if (catchUp) {
    console.log("phase copy --catchup: reconciling rows written during the copy window\n");
    const [pkPresent] = await client.unsafe(
      `SELECT count(*)::int AS present FROM pg_constraint
        WHERE conrelid = to_regclass($1) AND contype = 'p'`,
      [TARGET_TABLE]
    );
    if (pkPresent.present === 0) {
      throw new Error("partition-features: --catchup needs the primary key; run --phase=index first.");
    }
    for (const layer of census) {
      console.log(`- ${layer.name}`);
      if (dryRun) {
        console.log("    (dry run) anti-joined INSERT on (id, layer_id)");
        continue;
      }
      await catchUpLayer(layer);
    }
    console.log("\nphase copy --catchup done. Re-run --phase=verify before swapping.");
    return;
  }

  console.log("phase copy: per layer, smallest first\n");
  for (const layer of census) {
    console.log(`- ${layer.name} (${formatCount(layer.total)} rows)`);
    const predicates = await chunkPredicates(layer);
    const ledger = await readLedger(layer.layerId);

    const drifted = predicates
      .map((predicate, index) => ({ predicate, index, recorded: ledger.get(index) }))
      .filter((entry) => entry.recorded && entry.recorded.chunk_predicate !== entry.predicate);
    if (drifted.length > 0) {
      throw new Error(
        `partition-features: ${layer.name} was copied with different chunk boundaries ` +
          `(chunk ${drifted[0].index} recorded "${drifted[0].recorded.chunk_predicate}", now ` +
          `"${drifted[0].predicate}"). CHUNK_TARGET_ROWS changed, or the layer grew mid-run. ` +
          `Either finish with the original boundaries or empty ${qualifiedPartition(layer.name)} ` +
          `and delete the layer's ${COPY_LEDGER} rows and start it over.`
      );
    }

    let copied = 0;
    for (const [chunkIndex, predicate] of predicates.entries()) {
      const recorded = ledger.get(chunkIndex);
      if (recorded) {
        copied += Number(recorded.copied_rows);
        console.log(`    chunk ${chunkIndex}: already copied (${formatCount(recorded.copied_rows)} rows)`);
        continue;
      }
      if (dryRun) {
        console.log(`    (dry run) chunk ${chunkIndex}: WHERE ${predicate}`);
        continue;
      }
      copied += await copyChunk(layer, chunkIndex, predicate);
    }
    if (!dryRun) {
      console.log(`    ${formatCount(copied)} of ${formatCount(layer.total)} source rows`);
    }
  }

  console.log(
    "\nphase copy done. Next: --phase=index (PK, 8 parent indexes, per-partition geom, both FKs),\n" +
      "then --phase=trigger, then --phase=verify."
  );
}

// ---------------------------------------------------------------------------------------------
// PHASE index -- the PK, the parent indexes, the per-partition geom indexes, both FKs.
// ---------------------------------------------------------------------------------------------

async function indexExists(name) {
  return relationExists(`geo.${name}`);
}

async function constraintExists(table, name) {
  const rows = await client.unsafe(
    `SELECT count(*)::int AS present FROM pg_constraint WHERE conrelid = to_regclass($1) AND conname = $2`,
    [table, name]
  );
  return rows[0].present > 0;
}

async function runIndex() {
  if (!(await relationExists(TARGET_TABLE))) {
    throw new Error(`partition-features: ${TARGET_TABLE} does not exist; run --phase=create first.`);
  }
  console.log("phase index: primary key, parent indexes, per-partition geom, foreign keys\n");
  console.log(
    "  Plain CREATE INDEX throughout, NOT CONCURRENTLY: PostgreSQL refuses CONCURRENTLY on a\n" +
      "  partitioned parent, and nothing reads geo.features_new yet, so the ACCESS EXCLUSIVE lock\n" +
      "  a plain build takes is invisible to production.\n"
  );

  const buildName = (name) => `${name}${BUILD_SUFFIX}`;
  const settings = [
    "SET LOCAL maintenance_work_mem = '256MB'",
    "SET LOCAL max_parallel_maintenance_workers = 1",
    "SET LOCAL statement_timeout = '120min'",
  ];

  const primaryKeyBuild = buildName(PRIMARY_KEY_CONSTRAINT);
  console.log(`- ${primaryKeyBuild} PRIMARY KEY (id, layer_id)`);
  if (await constraintExists(TARGET_TABLE, primaryKeyBuild)) {
    console.log("  already present");
  } else if (dryRun) {
    console.log(`  (dry run) ALTER TABLE ${TARGET_TABLE} ADD CONSTRAINT ${primaryKeyBuild} PRIMARY KEY (id, layer_id)`);
  } else {
    const startedAt = process.hrtime.bigint();
    await client.begin(async (tx) => {
      for (const setting of settings) await tx.unsafe(setting);
      // (id, layer_id), not (id): a unique index on a partitioned table must contain the partition
      // key. Free here because geo.features has zero INBOUND foreign keys, so no referencing
      // constraint has to grow a column.
      await tx.unsafe(
        `ALTER TABLE ${TARGET_TABLE} ADD CONSTRAINT ${primaryKeyBuild} PRIMARY KEY (id, layer_id)`
      );
    });
    console.log(`  built in ${elapsed(startedAt)}`);
  }

  for (const index of PARENT_INDEXES) {
    const name = buildName(index.name);
    console.log(`- ${name}`);
    if (await indexExists(name)) {
      console.log("  already present");
      continue;
    }
    const statement = index.definition.startsWith("UNIQUE ")
      ? `CREATE UNIQUE INDEX ${name} ON ${TARGET_TABLE} ${index.definition.slice("UNIQUE ".length)}`
      : `CREATE INDEX ${name} ON ${TARGET_TABLE} ${index.definition}`;
    if (dryRun) {
      console.log(`  (dry run) ${statement.replace(/\s+/g, " ").trim()}`);
      continue;
    }
    const startedAt = process.hrtime.bigint();
    await client.begin(async (tx) => {
      for (const setting of settings) await tx.unsafe(setting);
      await tx.unsafe(statement);
    });
    console.log(`  built in ${elapsed(startedAt)}`);
  }

  // idx_features_geom cannot exist as a parent index, because a parent index demands a matching
  // child on EVERY partition and two partitions deliberately go without. Per-partition it is.
  console.log(
    `\n- per-partition GiST (geom), skipping ${[...GEOM_INDEX_EXCLUDED_LAYERS].join(" and ")}` +
      " (runbook §0.3: 87.6% of the index, served by no tile function)"
  );
  const geomTargets = (await readLayers())
    .filter((layer) => !GEOM_INDEX_EXCLUDED_LAYERS.has(layer.name))
    .map((layer) => ({ index: partitionGeomIndexName(layer.name), table: qualifiedPartition(layer.name) }));
  geomTargets.push({ index: "features_default_geom_idx", table: DEFAULT_PARTITION });

  for (const target of geomTargets) {
    if (await indexExists(target.index)) {
      console.log(`  ${target.index}: already present`);
      continue;
    }
    const statement = `CREATE INDEX ${target.index} ON ${target.table} USING gist (geom)`;
    if (dryRun) {
      console.log(`  (dry run) ${statement}`);
      continue;
    }
    const startedAt = process.hrtime.bigint();
    await client.begin(async (tx) => {
      for (const setting of settings) await tx.unsafe(setting);
      await tx.unsafe(statement);
    });
    console.log(`  ${target.index}: built in ${elapsed(startedAt)}`);
  }

  // Validated, not NOT VALID. ADD CONSTRAINT takes ShareRowExclusive on the REFERENCED table, so
  // geo.layers and geo.geometry are briefly closed to writers -- hence the lock_timeout and retry.
  console.log("\n- outbound foreign keys");
  for (const foreignKey of FOREIGN_KEYS) {
    if (await constraintExists(TARGET_TABLE, foreignKey.name)) {
      console.log(`  ${foreignKey.name}: already present`);
      continue;
    }
    const statement = `ALTER TABLE ${TARGET_TABLE} ADD CONSTRAINT ${foreignKey.name} ${foreignKey.definition}`;
    if (dryRun) {
      console.log(`  (dry run) ${statement}`);
      continue;
    }
    const startedAt = process.hrtime.bigint();
    await withLockRetry(foreignKey.name, () =>
      client.begin(async (tx) => {
        await tx.unsafe(`SET LOCAL lock_timeout = '${LOCK_TIMEOUT}'`);
        await tx.unsafe("SET LOCAL statement_timeout = '60min'");
        await tx.unsafe(statement);
      })
    );
    console.log(`  ${foreignKey.name}: validated against ${foreignKey.referenced} in ${elapsed(startedAt)}`);
  }

  console.log("\nphase index done. Next: --phase=trigger, then --phase=verify.");
}

// ---------------------------------------------------------------------------------------------
// PHASE trigger -- created last, and never before a copy.
// ---------------------------------------------------------------------------------------------

/**
 * `geo_features_sync_geom` BEFORE INSERT OR UPDATE OF properties, FOR EACH ROW.
 *
 * Deliberately its own phase and deliberately after every copy. The trigger recomputes `geom` from
 * `properties->'geometry'` and, since drizzle/0004, runs the result through `ST_MakeValid` -- so
 * copying 5,080,640 rows with it attached would both cost a geometry parse per row and silently
 * REWRITE any geometry stored before that repair landed. The copy must move bytes, not recompute
 * them. BEFORE-row triggers on a partitioned parent are legal and are cloned to every partition,
 * including partitions attached later.
 */
async function runTrigger() {
  const table = (await swapIsDone()) ? SOURCE_TABLE : TARGET_TABLE;
  console.log(`phase trigger: ${SYNC_TRIGGER} on ${table}\n`);

  if (await relationExists(COPY_LEDGER)) {
    const [ledgerRows] = await client.unsafe(`SELECT count(*)::int AS chunks FROM ${COPY_LEDGER}`);
    console.log(`  ${ledgerRows.chunks} chunk(s) recorded in ${COPY_LEDGER}`);
  }

  await execute(`DROP TRIGGER IF EXISTS ${SYNC_TRIGGER} ON ${table}`);
  await execute(
    `CREATE TRIGGER ${SYNC_TRIGGER}
       BEFORE INSERT OR UPDATE OF properties ON ${table}
       FOR EACH ROW EXECUTE FUNCTION geo.sync_feature_geom_from_properties()`,
    { label: "created" }
  );

  console.log(
    "\nphase trigger done. Any further copy into this table now recomputes geom -- run\n" +
      "--phase=copy --catchup BEFORE this phase, not after."
  );
}

// ---------------------------------------------------------------------------------------------
// PHASE verify -- the gate. Non-zero exit on any mismatch.
// ---------------------------------------------------------------------------------------------

async function runVerify() {
  const done = await swapIsDone();
  const partitionedTable = done ? SOURCE_TABLE : TARGET_TABLE;
  const heapTable = done ? LEGACY_TABLE : SOURCE_TABLE;
  const nameSuffix = done ? "" : BUILD_SUFFIX;

  console.log(`phase verify: ${partitionedTable} against ${heapTable}\n`);
  if (!(await relationExists(partitionedTable)) || !(await relationExists(heapTable))) {
    throw new Error(`partition-features: expected both ${partitionedTable} and ${heapTable} to exist.`);
  }

  const failures = [];
  const warnings = [];

  const heapCensus = await readCensus(heapTable);
  const partitionedCensus = new Map(
    (await readCensus(partitionedTable)).map((layer) => [layer.name, layer.total])
  );
  console.log("per-layer counts:");
  for (const layer of heapCensus) {
    const copied = partitionedCensus.get(layer.name) ?? 0;
    const status = copied === layer.total ? "ok" : "MISMATCH";
    if (status === "MISMATCH") {
      failures.push(`${layer.name}: ${formatCount(layer.total)} source vs ${formatCount(copied)} copied`);
    }
    console.log(
      `  ${layer.name.padEnd(22)} ${formatCount(layer.total).padStart(11)} -> ${formatCount(copied).padStart(11)}  ${status}`
    );
  }

  const [defaultRows] = await client.unsafe(`SELECT count(*)::bigint AS rows FROM ${DEFAULT_PARTITION}`);
  const defaultCount = Number(defaultRows.rows);
  console.log(`\n${DEFAULT_PARTITION}: ${formatCount(defaultCount)} row(s)`);
  if (defaultCount > 0 && !done) {
    // Not a failure: it means a layer was created mid-flight and its rows have nowhere better to
    // be yet. It IS a reason to run --phase=adopt before the swap, so the layer gets a real
    // partition while the table is still invisible to production.
    warnings.push(
      `${DEFAULT_PARTITION} holds ${formatCount(defaultCount)} row(s); run --phase=adopt --layer=<name> for each`
    );
  }

  console.log("\nstructural checks:");
  const expectedIndexes = [
    `${PRIMARY_KEY_CONSTRAINT}${nameSuffix}`,
    ...PARENT_INDEXES.map((index) => `${index.name}${nameSuffix}`),
  ];
  const actualIndexes = await readIndexes(partitionedTable);
  const actualNames = new Set(actualIndexes.map((row) => row.index_name));
  for (const name of expectedIndexes) {
    const present = actualNames.has(name);
    if (!present) failures.push(`parent index ${name} is missing`);
    console.log(`  ${name.padEnd(45)} ${present ? "present" : "MISSING"}`);
  }
  for (const index of actualIndexes) {
    if (index.indisvalid === false || index.indisready === false) {
      failures.push(`index ${index.index_name} is indisvalid=${index.indisvalid} indisready=${index.indisready}`);
    }
  }

  const partitions = await readPartitions(partitionedTable);
  console.log(`  ${String(partitions.length).padEnd(45)} partition(s), DEFAULT included`);
  if (!partitions.some((partition) => partition.bound === "DEFAULT")) {
    failures.push("there is no DEFAULT partition -- runtime layer creation would 500 (runbook §0.4)");
  }
  const invalidChildren = await client.unsafe(
    `SELECT c.relname AS index_name
       FROM pg_index AS i
       JOIN pg_class AS c ON c.oid = i.indexrelid
      WHERE i.indrelid IN (SELECT inhrelid FROM pg_inherits WHERE inhparent = to_regclass($1))
        AND (NOT i.indisvalid OR NOT i.indisready)`,
    [partitionedTable]
  );
  for (const child of invalidChildren) failures.push(`partition index ${child.index_name} is not valid`);
  console.log(`  ${"partition indexes valid".padEnd(45)} ${invalidChildren.length === 0 ? "yes" : "NO"}`);

  for (const foreignKey of FOREIGN_KEYS) {
    const rows = await client.unsafe(
      `SELECT convalidated FROM pg_constraint WHERE conrelid = to_regclass($1) AND conname = $2`,
      [partitionedTable, foreignKey.name]
    );
    const ok = rows.length > 0 && rows[0].convalidated === true;
    if (!ok) failures.push(`foreign key ${foreignKey.name} is missing or NOT VALID`);
    console.log(`  ${foreignKey.name.padEnd(45)} ${ok ? "validated" : "MISSING/NOT VALID"}`);
  }

  const triggerRows = await client.unsafe(
    `SELECT count(*)::int AS present FROM pg_trigger
      WHERE tgrelid = to_regclass($1) AND tgname = $2 AND NOT tgisinternal`,
    [partitionedTable, SYNC_TRIGGER]
  );
  const triggerPresent = triggerRows[0].present > 0;
  if (!triggerPresent) failures.push(`trigger ${SYNC_TRIGGER} is missing on ${partitionedTable}`);
  console.log(`  ${SYNC_TRIGGER.padEnd(45)} ${triggerPresent ? "present" : "MISSING"}`);

  if (done) {
    // The one name the deploy depends on. src/app/api/ready/route.ts:60 resolves it literally.
    const readinessName = "geo.features_layer_external_id_unique";
    const present = await relationExists(readinessName);
    if (!present) failures.push(`${readinessName} is missing -- /api/ready will 503 and block every deploy`);
    console.log(`  ${"readiness probe index".padEnd(45)} ${present ? "present" : "MISSING"}`);
  }

  for (const warning of warnings) console.log(`\nWARNING: ${warning}`);
  if (failures.length > 0) {
    console.log("\nVERIFICATION FAILED:");
    for (const failure of failures) console.log(`  - ${failure}`);
    return false;
  }
  console.log("\nverification passed.");
  return true;
}

// ---------------------------------------------------------------------------------------------
// PHASE swap -- the two-step rename, under a short lock_timeout, retried rather than queued.
// ---------------------------------------------------------------------------------------------

/**
 * The rename statements, generated from the live catalog rather than a hardcoded list.
 *
 * Index names are unique per SCHEMA, so the incoming indexes cannot hold their final names until
 * the outgoing ones let go. Constraint names are unique per TABLE and do not collide, which is why
 * both foreign keys and the trigger are created with their final names from the start and only the
 * indexes need the `_swap` dance. A constraint-backed index (the PK) is renamed through
 * `ALTER TABLE ... RENAME CONSTRAINT`, which renames its backing index too; if a PostgreSQL
 * version ever stopped doing that, the second rename would collide and the whole transaction would
 * roll back -- a failed swap, not a corrupt one.
 */
async function renameStatements() {
  const statements = [];

  const outgoing = await readIndexes(SOURCE_TABLE);
  statements.push(`ALTER TABLE ${SOURCE_TABLE} RENAME TO features_legacy`);
  for (const index of outgoing) {
    statements.push(
      index.constraint_name
        ? `ALTER TABLE ${LEGACY_TABLE} RENAME CONSTRAINT ${index.constraint_name} TO ${LEGACY_PREFIX}${index.constraint_name}`
        : `ALTER INDEX geo.${index.index_name} RENAME TO ${LEGACY_PREFIX}${index.index_name}`
    );
  }

  const incoming = await readIndexes(TARGET_TABLE);
  statements.push(`ALTER TABLE ${TARGET_TABLE} RENAME TO features`);
  for (const index of incoming) {
    if (index.constraint_name?.endsWith(BUILD_SUFFIX)) {
      statements.push(
        `ALTER TABLE ${SOURCE_TABLE} RENAME CONSTRAINT ${index.constraint_name} TO ${index.constraint_name.slice(0, -BUILD_SUFFIX.length)}`
      );
    } else if (!index.constraint_name && index.index_name.endsWith(BUILD_SUFFIX)) {
      statements.push(
        `ALTER INDEX geo.${index.index_name} RENAME TO ${index.index_name.slice(0, -BUILD_SUFFIX.length)}`
      );
    }
  }

  return statements;
}

async function runSwap() {
  if (await swapIsDone()) {
    throw new Error("partition-features: geo.features is already the partitioned parent.");
  }
  if (!(await relationExists(TARGET_TABLE))) {
    throw new Error(`partition-features: ${TARGET_TABLE} does not exist.`);
  }
  if (await relationExists(LEGACY_TABLE)) {
    throw new Error(
      `partition-features: ${LEGACY_TABLE} already exists -- a previous swap left it behind. ` +
        "Rename or drop it deliberately before running this again."
    );
  }

  if (!(await runVerify())) {
    throw new Error("partition-features: refusing to swap, verification failed (see above).");
  }

  const dependents = await readDependentRelations(SOURCE_TABLE);
  if (dependents.length > 0) {
    console.log(
      `\n!! ${dependents.length} relation(s) store ${SOURCE_TABLE}'s OID in a rewrite rule and will\n` +
        "!! follow the rename onto the frozen legacy heap. They do not error -- they keep answering,\n" +
        "!! from data that stops moving. Drop and re-create each from its migration AFTER the swap:"
    );
    for (const dependent of dependents) console.log(`!!   ${dependent.kind.padEnd(8)} geo.${dependent.name}`);
    console.log("");
  }

  const blockers = await client.unsafe(
    `SELECT pid, usename, application_name, state, left(query, 60) AS query
       FROM pg_stat_activity
      WHERE datname = current_database() AND pid <> pg_backend_pid() AND state <> 'idle'
      ORDER BY xact_start`
  );
  console.log(`${blockers.length} non-idle backend(s) on this database:`);
  for (const backend of blockers) {
    console.log(`  pid ${backend.pid} ${backend.application_name || "-"} [${backend.state}] ${backend.query}`);
  }
  console.log(
    "  ^ this script cannot stop them. Quiesce the three Railway crons and plantgeo-main first;\n" +
      "    the rename below asks for ACCESS EXCLUSIVE and gives up after " +
      `${LOCK_TIMEOUT} rather than queue.\n`
  );

  const statements = await renameStatements();
  console.log(`the swap transaction (${statements.length} statements):`);
  for (const statement of statements) console.log(`  ${statement}`);
  if (dryRun) {
    console.log("\n(dry run) nothing executed.");
    return;
  }

  const startedAt = process.hrtime.bigint();
  await withLockRetry("swap", () =>
    client.begin(async (tx) => {
      // Both renames in ONE transaction: there must never be an instant in which no relation is
      // named geo.features. The lock_timeout applies per statement, and only the first statement
      // can wait -- once it holds ACCESS EXCLUSIVE the rest are catalog updates.
      await tx.unsafe(`SET LOCAL lock_timeout = '${LOCK_TIMEOUT}'`);
      for (const statement of statements) await tx.unsafe(statement);
    })
  );
  console.log(`\nswapped in ${elapsed(startedAt)}`);

  console.log(
    "\nphase swap done. IN THIS ORDER:\n" +
      "  1. --phase=analyze (autovacuum never analyzes a partitioned parent).\n" +
      "  2. Re-create every dependent relation listed above from its migration.\n" +
      "  3. Restart Martin -- its config is read once at start and a cached plpgsql plan against the\n" +
      "     pre-swap OID serves from the orphaned heap. A broken tile function 404s the WHOLE\n" +
      "     composite and hides every layer.\n" +
      "  4. Bounce plantgeo-main (postgres-js, max 20, server-side prepared statements).\n" +
      "  5. Un-quiesce ingestion LAST and watch the first cron exit code.\n" +
      "  6. Refresh geo.mv_layer_feature_stats and diff the 11 per-layer counts.\n" +
      `  ${LEGACY_TABLE} is retained, ~7.9 GB. Do not drop it until the above is green.`
  );
}

// ---------------------------------------------------------------------------------------------
// PHASE analyze -- every partition, then the parent.
// ---------------------------------------------------------------------------------------------

/**
 * Not a tidy-up.
 *
 * Autovacuum never analyzes a partitioned PARENT, and an expression index
 * (`ix_features_layer_observation_day`) has no statistics at all until the table is analyzed --
 * without them the planner reverts to the sequential scan the index exists to replace. Children
 * first, so the parent's inheritance statistics are gathered over analyzed children.
 */
async function runAnalyze() {
  const table = (await swapIsDone()) ? SOURCE_TABLE : TARGET_TABLE;
  console.log(`phase analyze: ${table} and every partition\n`);
  for (const partition of await readPartitions(table)) {
    await execute(`ANALYZE geo.${partition.name}`, { label: `geo.${partition.name}` });
  }
  await execute(`ANALYZE ${table}`, { label: table });
  console.log(
    "\nphase analyze done. Re-run it after any bulk change -- and confirm\n" +
      "enable_partitionwise_aggregate before trusting the all-layer census matviews (runbook §0.6)."
  );
}

// ---------------------------------------------------------------------------------------------
// PHASE adopt -- give a runtime-created layer a real partition and drain it out of DEFAULT.
// ---------------------------------------------------------------------------------------------

/**
 * The recurring op that keeps the DEFAULT partition from becoming the table.
 *
 * `layersRouter.create` mints layers at request time (runbook §0.4), so new layer_ids keep
 * arriving and their rows land in DEFAULT. Rows are moved into a standalone table FIRST, with a
 * CHECK constraint that lets `ATTACH PARTITION` skip revalidating them; the DEFAULT partition is
 * still scanned to prove nothing left in it belongs to the new partition, which is exactly why
 * this should run often enough that DEFAULT stays small. The whole thing is one transaction
 * holding ACCESS EXCLUSIVE on the parent, so it runs under the same lock_timeout and retry as the
 * swap. The trigger and every parent index are cloned onto the partition by ATTACH; only the
 * per-partition geom index, which is not a parent index, has to be created by hand.
 */
async function runAdopt() {
  const table = (await swapIsDone()) ? SOURCE_TABLE : TARGET_TABLE;
  const layers = await readLayers();
  const layer = layers.find((candidate) => candidate.name === layerOption);
  if (!layer) {
    throw new Error(`partition-features: geo.layers has no layer named "${layerOption}".`);
  }

  const partitionTable = qualifiedPartition(layer.name);
  if (await relationExists(partitionTable)) {
    throw new Error(`partition-features: ${partitionTable} already exists; nothing to adopt.`);
  }

  const [pending] = await client.unsafe(
    `SELECT count(*)::bigint AS rows FROM ${DEFAULT_PARTITION} WHERE layer_id = $1`,
    [layer.id]
  );
  console.log(
    `phase adopt: ${layer.name} -> ${partitionTable} ` +
      `(${formatCount(pending.rows)} row(s) waiting in ${DEFAULT_PARTITION})\n`
  );

  const checkConstraint = `${partitionName(layer.name)}_partition_check`;
  const statements = [
    `CREATE TABLE ${partitionTable} (LIKE ${table} INCLUDING DEFAULTS INCLUDING CONSTRAINTS)`,
    `ALTER TABLE ${partitionTable} ADD CONSTRAINT ${checkConstraint} CHECK (layer_id = ${uuidLiteral(layer.id)})`,
    `WITH moved AS (
       DELETE FROM ${DEFAULT_PARTITION} WHERE layer_id = ${uuidLiteral(layer.id)} RETURNING ${COLUMN_LIST}
     )
     INSERT INTO ${partitionTable} (${COLUMN_LIST}) SELECT ${COLUMN_LIST} FROM moved`,
    `ALTER TABLE ${table} ATTACH PARTITION ${partitionTable} FOR VALUES IN (${uuidLiteral(layer.id)})`,
    `ALTER TABLE ${partitionTable} DROP CONSTRAINT ${checkConstraint}`,
  ];
  if (!GEOM_INDEX_EXCLUDED_LAYERS.has(layer.name)) {
    statements.push(
      `CREATE INDEX ${partitionGeomIndexName(layer.name)} ON ${partitionTable} USING gist (geom)`
    );
  }

  for (const statement of statements) console.log(`  ${statement.replace(/\s+/g, " ").trim()}`);
  if (dryRun) {
    console.log("\n(dry run) nothing executed.");
    return;
  }

  const startedAt = process.hrtime.bigint();
  await withLockRetry("adopt", () =>
    client.begin(async (tx) => {
      await tx.unsafe(`SET LOCAL lock_timeout = '${LOCK_TIMEOUT}'`);
      await tx.unsafe("SET LOCAL statement_timeout = '30min'");
      for (const statement of statements) await tx.unsafe(statement);
    })
  );
  console.log(`\nphase adopt done in ${elapsed(startedAt)}. ANALYZE the new partition.`);
}

// ---------------------------------------------------------------------------------------------
// PHASE rollback -- the exact inverse of the swap. Reads the catalog, not a saved script.
// ---------------------------------------------------------------------------------------------

async function runRollback() {
  if (!(await swapIsDone())) {
    throw new Error("partition-features: geo.features is not partitioned; there is no swap to reverse.");
  }
  if (!(await relationExists(LEGACY_TABLE))) {
    throw new Error(`partition-features: ${LEGACY_TABLE} is gone; the swap cannot be reversed.`);
  }

  const [partitionedRows] = await client.unsafe(`SELECT count(*)::bigint AS rows FROM ${SOURCE_TABLE}`);
  const [legacyRows] = await client.unsafe(`SELECT count(*)::bigint AS rows FROM ${LEGACY_TABLE}`);
  const lost = Number(partitionedRows.rows) - Number(legacyRows.rows);

  console.log("phase rollback: restoring the pre-swap heap\n");
  console.log(`  partitioned table: ${formatCount(partitionedRows.rows)} rows`);
  console.log(`  legacy heap:       ${formatCount(legacyRows.rows)} rows`);
  console.log(
    `  DISCARDS ~${formatCount(Math.max(lost, 0))} row(s) written since the swap. The legacy heap has\n` +
      "  had no writes since the rename, so everything ingestion and the app wrote after the swap\n" +
      "  lives only in the partitioned table and is thrown away here.\n"
  );
  if (!force) {
    throw new Error("partition-features: rollback discards post-swap writes; re-run with --force.");
  }

  const partitionedIndexes = await readIndexes(SOURCE_TABLE);
  const legacyIndexes = await readIndexes(LEGACY_TABLE);
  const statements = [`ALTER TABLE ${SOURCE_TABLE} RENAME TO features_new`];
  for (const index of partitionedIndexes) {
    statements.push(
      index.constraint_name
        ? `ALTER TABLE ${TARGET_TABLE} RENAME CONSTRAINT ${index.constraint_name} TO ${index.constraint_name}${BUILD_SUFFIX}`
        : `ALTER INDEX geo.${index.index_name} RENAME TO ${index.index_name}${BUILD_SUFFIX}`
    );
  }
  statements.push(`ALTER TABLE ${LEGACY_TABLE} RENAME TO features`);
  for (const index of legacyIndexes) {
    if (index.constraint_name?.startsWith(LEGACY_PREFIX)) {
      statements.push(
        `ALTER TABLE ${SOURCE_TABLE} RENAME CONSTRAINT ${index.constraint_name} TO ${index.constraint_name.slice(LEGACY_PREFIX.length)}`
      );
    } else if (!index.constraint_name && index.index_name.startsWith(LEGACY_PREFIX)) {
      statements.push(
        `ALTER INDEX geo.${index.index_name} RENAME TO ${index.index_name.slice(LEGACY_PREFIX.length)}`
      );
    }
  }

  for (const statement of statements) console.log(`  ${statement}`);
  if (dryRun) {
    console.log("\n(dry run) nothing executed.");
    return;
  }

  await withLockRetry("rollback", () =>
    client.begin(async (tx) => {
      await tx.unsafe(`SET LOCAL lock_timeout = '${LOCK_TIMEOUT}'`);
      for (const statement of statements) await tx.unsafe(statement);
    })
  );

  console.log(
    "\nphase rollback done. Restart Martin and bounce plantgeo-main again -- the OID moved back.\n" +
      "Any dependent matview re-created after the swap now points at the partitioned table and must\n" +
      "be re-created once more."
  );
}

// ---------------------------------------------------------------------------------------------

const RUNNERS = {
  plan: runPlan,
  create: runCreate,
  copy: runCopy,
  index: runIndex,
  trigger: runTrigger,
  verify: runVerify,
  swap: runSwap,
  analyze: runAnalyze,
  adopt: runAdopt,
  rollback: runRollback,
};

try {
  // `statement_timeout = 0` at session level because a copy chunk and an index build legitimately
  // run for minutes; every phase that needs a bound sets `SET LOCAL` inside its own transaction.
  await client.unsafe("SET statement_timeout = 0");
  await client.unsafe("SET idle_in_transaction_session_timeout = '10min'");
  const result = await RUNNERS[phase]();
  if (phase === "verify" && result === false) process.exitCode = 1;
} catch (error) {
  console.error(`\npartition-features: phase ${phase} failed`);
  console.error(error);
  process.exitCode = 1;
} finally {
  await client.end({ timeout: 30 }).catch(() => {});
}
