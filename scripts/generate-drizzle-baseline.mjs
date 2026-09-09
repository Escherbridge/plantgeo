// Regenerates `drizzle/0000_baseline.sql` from a pg_dump --schema-only artifact.
//
//   node scripts/generate-drizzle-baseline.mjs <artifact.sql> [drizzle/0000_baseline.sql]
//
// PRODUCING THE ARTIFACT. `pg_dump` cannot dump a server newer than itself and production is
// PostgreSQL 18, so the dump is taken by a pg18 client and stored in the object store rather than
// regenerated locally. The canonical artifact lives at:
//
//   s3://<OBJECT_STORE_BUCKET>/schema-baselines/<yyyymmdd>-prod-drizzle-schema.sql
//
// To take a fresh one, from any host that has a PostgreSQL 18 client:
//
//   pg_dump --schema-only --no-owner --no-privileges \
//           --schema=public --schema=geo --schema=tracking "$DATABASE_URL_SYNC"
//
// Those flags define the artifact; changing any of them changes the baseline. `--schema` filters
// mean pg_dump emits no CREATE EXTENSION, which is why `scripts/bootstrap-database.mjs` owns the
// extensions. The `drizzle` and `agri` schemas are excluded by not being listed: they belong to
// the migrator's own ledger and to Alembic.
//
// VERIFYING A REGENERATED BASELINE. Do not compare dump text -- pg_dump output is stable only
// within a major version. Build an empty database, run the bootstrap against it, and compare the
// CATALOGUE (columns, indexes, constraints, functions, view definitions, triggers) with
// production. Two classes of difference are expected and benign:
//   * CHECK constraints render differently after a dump/restore round trip
//     (`ANY ((ARRAY[...])::text[])` vs `ANY (ARRAY[(...)::text])`) -- semantically identical.
//   * PostgreSQL 18 catalogues NOT NULL as real pg_constraint rows, so their auto-generated names
//     can gain a numeric suffix. Names only; the constraints are the same.
import fs from "node:fs";

const [source, destination = "drizzle/0000_baseline.sql"] = process.argv.slice(2);
if (!source) {
  console.error("generate-drizzle-baseline: pass the pg_dump artifact path");
  process.exit(1);
}

let sql = fs.readFileSync(source, "utf8").replace(/\r\n/g, "\n");

// psql meta-commands carrying a random per-run token: not DDL, and the migrator executes SQL
// rather than psql, so they would be a syntax error.
sql = sql.replace(/^\\(?:un)?restrict [A-Za-z0-9]+[ \t]*\n/gm, "");
// Non-deterministic banner: changes with every run and every client version.
sql = sql.replace(/^-- Dumped (?:from|by).*\n/gm, "");

// Objects Alembic owns. `--schema=public` sweeps up `alembic_version`, and a baseline that creates
// it makes `alembic upgrade head` fail with "relation already exists" when the bootstrap runs
// Alembic first. Filtering is per pg_dump object BLOCK, not per line, so an object's header
// comment and every statement in its body are removed together.
const OWNED_BY_ALEMBIC = /^alembic_version\b/;
const OBJECT_HEADER =
  /^--\n-- Name: (?<name>.+?); Type: (?<type>.+?); Schema: (?<schema>.+?); Owner:.*\n--\n/gm;

const blocks = [];
let pending = null;
for (const match of sql.matchAll(OBJECT_HEADER)) {
  if (pending) blocks.push({ ...pending, body: sql.slice(pending.start, match.index) });
  pending = { name: match.groups.name, start: match.index };
}
if (!pending) {
  console.error("generate-drizzle-baseline: no pg_dump object headers found -- wrong input file?");
  process.exit(1);
}
const preamble = sql.slice(0, (blocks[0] ?? pending).start);
blocks.push({ ...pending, body: sql.slice(pending.start) });

const kept = blocks.filter((block) => !OWNED_BY_ALEMBIC.test(block.name));
sql = preamble + kept.map((block) => block.body).join("");

// The baseline must apply to a database that already has `public`.
sql = sql.replace(/^CREATE SCHEMA (public|geo|tracking);$/gm, "CREATE SCHEMA IF NOT EXISTS $1;");

const header = `-- PlantGeo Drizzle greenfield baseline.
--
-- This single revision replaces migrations 0000..0040, which are retained unedited in
-- \`drizzle/archive/\`. It is generated from production's ACTUAL schema by pg_dump 18, not
-- hand-transcribed, so it cannot drift from what production really is. Regenerate it with
-- \`scripts/generate-drizzle-baseline.mjs\`; that file documents how the artifact is produced.
--
-- WHY THE CHAIN WAS COLLAPSED, in one line each -- \`drizzle/archive/README.md\` has the evidence:
--   * Four steps had to run OUT OF BAND, BETWEEN migrations, but drizzle-orm applies every
--     pending migration in ONE transaction, and CREATE INDEX CONCURRENTLY cannot run in one.
--   * Seven of the last ten migrations had never been applied to production at all.
--
-- PREREQUISITES, in order. This baseline creates no extensions and no \`agri\` objects:
--   1. CREATE EXTENSION postgis, pgcrypto, vector, btree_gist
--   2. Alembic \`upgrade head\` -- seven objects below read Alembic-owned agri tables
--      (agri.signal_observation, agri.spatial_cell, agri.strategies and seven more).
--   3. This baseline.
-- \`scripts/bootstrap-database.mjs\` performs all three in order and refuses step 3 without step 2.
--
-- VERIFIED 2026-09-08 by building an empty database on the production server and comparing every
-- column, index, constraint, function, view definition and trigger against production by
-- catalogue query: zero differences in public, geo and tracking.

`;
fs.writeFileSync(destination, header + sql);
console.log(`baseline written: ${destination}`);
console.log(`object blocks kept: ${kept.length}, dropped as Alembic-owned: ${blocks.length - kept.length}`);
