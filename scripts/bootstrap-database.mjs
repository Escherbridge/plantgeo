// Builds a PlantGeo database from empty, in the one order that works.
//
// This exists because the order used to live only inside migration error messages, so a new
// region could not bring the schema up without reading them one failure at a time. See
// `drizzle/archive/README.md` for what the previous 41-migration chain required and why it was
// collapsed into `drizzle/0000_baseline.sql`.
//
//   node scripts/bootstrap-database.mjs                # extensions, preflight, baseline, seed
//   node scripts/bootstrap-database.mjs --dry-run      # print the plan, touch nothing
//   node scripts/bootstrap-database.mjs --skip-extensions
//   node scripts/bootstrap-database.mjs --skip-seed    # schema only, no reference rows
//
// The `agri` schema is Alembic's, not Drizzle's, and seven objects in the baseline read Alembic
// owned tables (agri.signal_observation, agri.spatial_cell, agri.strategies and seven more). This
// script therefore REFUSES to apply the baseline until `agri` is present, rather than failing
// halfway through with a bare "relation does not exist". Run Alembic first, from
// `services/agri-data-service`:
//
//   DATABASE_URL_SYNC=<dsn> UV_NO_SYNC=1 uv run --no-sync alembic upgrade head
//
// `alembic upgrade head` reads DATABASE_URL_SYNC and never DATABASE_URL; overriding the latter
// does nothing and migrates whatever DATABASE_URL_SYNC points at.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { drizzle } from "drizzle-orm/postgres-js";
import { migrate } from "drizzle-orm/postgres-js/migrator";
import postgres from "postgres";

// Required by the baseline: PostGIS for every geometry column, pgcrypto for id defaults, vector
// for the embedding columns, btree_gist for the composite (layer_id, geom) GiST index.
const REQUIRED_EXTENSIONS = ["postgis", "pgcrypto", "vector", "btree_gist"];
// Present in production for diagnostics and read by nothing. Best-effort: a managed Postgres that
// does not offer them is still a valid target.
const DIAGNOSTIC_EXTENSIONS = ["hypopg", "pg_buffercache"];

const KNOWN_FLAGS = new Set(["--dry-run", "--skip-extensions", "--skip-seed"]);
const args = new Set(process.argv.slice(2));
// An unrecognised flag is refused rather than ignored. `--dryrun` silently ignored would fall
// through to the real run against `...|| DATABASE_URL`, which in this repo means production.
const unknownFlags = [...args].filter((flag) => !KNOWN_FLAGS.has(flag));
if (unknownFlags.length > 0) {
  console.error(
    `bootstrap-database: unrecognised flag(s): ${unknownFlags.join(", ")}\n` +
      `known flags: ${[...KNOWN_FLAGS].join(", ")}`
  );
  process.exit(1);
}
const dryRun = args.has("--dry-run");
const skipExtensions = args.has("--skip-extensions");
const skipSeed = args.has("--skip-seed");

const connectionString =
  process.env.BOOTSTRAP_DATABASE_URL ||
  process.env.MIGRATION_DATABASE_URL ||
  process.env.DATABASE_URL;
if (!connectionString) {
  console.error(
    "bootstrap-database: set BOOTSTRAP_DATABASE_URL (or MIGRATION_DATABASE_URL / DATABASE_URL)"
  );
  process.exit(1);
}

const migrationsFolder = fileURLToPath(new URL("../drizzle", import.meta.url));
const client = postgres(connectionString, {
  max: 1,
  idle_timeout: 5,
  connect_timeout: 30,
  onnotice: () => {},
});

async function createExtensions() {
  console.log("step 1: extensions");
  for (const extension of REQUIRED_EXTENSIONS) {
    if (dryRun) {
      console.log(`  (dry run) CREATE EXTENSION IF NOT EXISTS ${extension}`);
      continue;
    }
    await client.unsafe(`CREATE EXTENSION IF NOT EXISTS ${extension}`);
    console.log(`  ${extension}`);
  }
  for (const extension of DIAGNOSTIC_EXTENSIONS) {
    if (dryRun) {
      console.log(`  (dry run, optional) CREATE EXTENSION IF NOT EXISTS ${extension}`);
      continue;
    }
    try {
      await client.unsafe(`CREATE EXTENSION IF NOT EXISTS ${extension}`);
      console.log(`  ${extension}`);
    } catch {
      console.log(`  ${extension} unavailable on this server -- skipped (diagnostics only)`);
    }
  }
}

/** Fail before the baseline rather than partway through it. */
async function requireAlembicSchema() {
  console.log("step 2: preflight -- the Alembic-owned `agri` schema");
  if (dryRun) {
    // A dry run is for planning a database that does not exist yet, so refusing here would make
    // `--dry-run` fail on exactly the target it is meant to describe.
    console.log("  (dry run) would require agri.signal_observation before continuing");
    return;
  }
  const [{ present }] = await client`
    SELECT count(*) > 0 AS present
      FROM information_schema.tables
     WHERE table_schema = 'agri' AND table_name = 'signal_observation'
  `;
  if (present) {
    console.log("  agri.signal_observation present");
    return;
  }
  console.error(
    "\nbootstrap-database: the `agri` schema is missing, so the baseline cannot be applied.\n" +
      "Run Alembic first, from services/agri-data-service:\n\n" +
      "  DATABASE_URL_SYNC=<dsn> UV_NO_SYNC=1 uv run --no-sync alembic upgrade head\n"
  );
  throw new Error("agri schema absent");
}

async function applyBaseline() {
  console.log("step 3: drizzle baseline");
  if (dryRun) {
    console.log(`  (dry run) migrate from ${migrationsFolder}`);
    return;
  }
  await migrate(drizzle(client), { migrationsFolder });
  console.log("  drizzle migrations are up to date");
}

/**
 * Reference rows the schema cannot carry. The baseline is `--schema-only`, so without this a
 * bootstrapped database has an empty `geo.layers`, `/api/ready` fails its
 * `count(DISTINCT name) = 8` check and returns 503, and Railway's healthcheck kills the deploy.
 */
async function applySeeds() {
  console.log("step 4: seed data");
  const seedDirectory = fileURLToPath(new URL("../drizzle/seed", import.meta.url));
  const seedFiles = readdirSync(seedDirectory)
    .filter((name) => name.endsWith(".sql"))
    .sort();
  for (const file of seedFiles) {
    if (dryRun) {
      console.log(`  (dry run) ${file}`);
      continue;
    }
    await client.unsafe(readFileSync(join(seedDirectory, file), "utf8"));
    console.log(`  ${file}`);
  }
}

try {
  if (!skipExtensions) await createExtensions();
  else console.log("step 1: extensions -- skipped");
  await requireAlembicSchema();
  await applyBaseline();
  if (!skipSeed) await applySeeds();
  else console.log("step 4: seed data -- skipped");
  console.log("\nbootstrap done.");
} catch (error) {
  console.error("\nbootstrap-database: failed");
  console.error(error.message ?? error);
  process.exitCode = 1;
} finally {
  await client.end({ timeout: 5 }).catch(() => {});
}
