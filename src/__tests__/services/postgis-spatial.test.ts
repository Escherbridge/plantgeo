// @vitest-environment node

import { randomUUID } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import postgres from "postgres";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

/**
 * Runs the repo's manual-only spatial SQL tests (`drizzle/tests/*.test.sql`) through a REAL
 * driver, against a REAL PostGIS. Until this file, those four scripts existed only as psql
 * instructions in their own headers -- nothing in `vitest run` or CI ever executed them, so a
 * regression in `geo.sync_feature_geom_from_properties()`, `geo.evacuation_zone_tiles()`,
 * `geo.sensor_tiles()` or `geo.burn_severity_tiles()` would surface only when someone happened
 * to run psql by hand.
 *
 * Same env-gated shape as `climate-field-sql-contract.test.ts`, but that file SHIMS PostGIS
 * (the reference instance it targets has none) because its statements only need `ST_MakeEnvelope`
 * / `ST_AsGeoJSON` / `&&` at their real arities. These four scripts call `ST_MakeValid`,
 * `ST_CollectionExtract`, `ST_Buffer`, `ST_AsMVT` and friends -- there is no honest shim for a
 * tile encoder, so this suite requires the real extension and gates on a DIFFERENT env var
 * (`POSTGIS_TEST_DSN`) so the two suites can be pointed at two different local Postgres
 * instances (a plain one for the shimmed contract tests, a postgis-enabled one for this file)
 * without either accidentally running against the other's target.
 *
 * ## Running it
 *
 * Set `POSTGIS_TEST_DSN` to a DISPOSABLE local PostgreSQL superuser DSN whose server has the
 * `postgis` extension available -- the repo's own podman postgis container is exactly this,
 * e.g. `postgresql://postgres:<pw>@127.0.0.1:5434/postgres` (see any `drizzle/tests/*.test.sql`
 * header for the container's usual port). The suite creates its own randomly named database,
 * enables `postgis` in it, applies the real migration files up through the ceiling below, and
 * drops the database again in `afterAll` -- it never writes to the database named in the DSN.
 *
 * Three distinct outcomes, all informative rather than a bare failure:
 *   - `POSTGIS_TEST_DSN` unset: the whole `describe` is skipped at declaration time.
 *   - Set, but the server's `pg_available_extensions` has no `postgis` row (true of the plain
 *     local Postgres instance this repo also uses for other suites): every case skips itself
 *     at runtime with that reason. This is the expected outcome against a non-PostGIS Postgres,
 *     and is intentionally NOT a failure.
 *   - Set, reachable, and `postgis` is available, but something else in setup fails (no
 *     CREATEDB privilege, a migration that will not apply): every case skips with the captured
 *     error rather than reporting four unrelated-looking failures for one root cause.
 *
 * ## What is real and what is scoped
 *
 * Real: `postgis` itself, and the migration files exactly as committed. Scoped: only migrations
 * `0000` through `0012` are applied, not the full set. Each `.test.sql` script only exercises
 * objects created by or before its OWN numbered migration -- 0010's script wants the
 * `geo.sensor_tiles()` that 0010 defines, not the observation-day variant 0015 later replaces
 * it with -- so applying anything past the highest numbered script here would test a state none
 * of these scripts were written against.
 */

const ADMIN_DSN = process.env.POSTGIS_TEST_DSN;

if (ADMIN_DSN === undefined) {
  console.warn(
    "\n[postgis-spatial] SKIPPED — no real-PostGIS coverage for drizzle/tests/*.test.sql.\n" +
      "  These scripts exercise geo.sync_feature_geom_from_properties(), geo.evacuation_zone_tiles(),\n" +
      "  geo.sensor_tiles() and geo.burn_severity_tiles() against real PostGIS geometry functions no\n" +
      "  shim can honestly stand in for. Set POSTGIS_TEST_DSN to a disposable local superuser DSN\n" +
      "  whose server has the postgis extension available, e.g.\n" +
      "    POSTGIS_TEST_DSN=postgresql://postgres:<pw>@127.0.0.1:5434/postgres\n"
  );
}

/**
 * Only migrations at or below this number are applied. See the file header: every
 * `drizzle/tests/000N*.test.sql` script targets the state as of migration N, not HEAD.
 */
const MIGRATION_CEILING = 12;

/** Every manual spatial test script this harness runs, paired with the notice that marks a
 * clean pass all the way through its DO block (see each file's own final RAISE NOTICE). */
const SPATIAL_TEST_FILES: readonly { file: string; finalNotice: string }[] = [
  {
    file: "0004_repair_ingested_geometries.test.sql",
    finalNotice: "ALL 0004 geometry-repair trigger tests passed",
  },
  {
    file: "0009_evacuation_zone_tiles.test.sql",
    finalNotice: "ALL 0009 evacuation_zone_tiles tests passed",
  },
  {
    file: "0010_sensor_tile_properties.test.sql",
    finalNotice: "ALL 0010 sensor_tile_properties tests passed",
  },
  {
    file: "0012_burn_severity_tiles.test.sql",
    finalNotice: "ALL 0012 burn_severity_tiles tests passed",
  },
] as const;

/** Committed migration files at or below `MIGRATION_CEILING`, in numeric (== lexical, zero-padded) order. */
function migrationFilesThroughCeiling(): string[] {
  return readdirSync("drizzle")
    .filter((name) => /^\d{4}_.*\.sql$/.test(name))
    .filter((name) => Number(name.slice(0, 4)) <= MIGRATION_CEILING)
    .sort();
}

/** Applies one committed migration file, honouring drizzle's statement breakpoints. */
async function applyMigration(client: postgres.Sql, file: string): Promise<void> {
  const body = readFileSync(`drizzle/${file}`, "utf8");
  for (const statement of body.split("--> statement-breakpoint")) {
    if (statement.trim() === "") continue;
    await client.unsafe(statement);
  }
}

/**
 * Runs one `drizzle/tests/*.test.sql` script exactly as psql would (`-f`): the whole file in
 * one call, never split on `;`. Every script is `BEGIN; DO $$ ... $$; ROLLBACK;` and the DO
 * block's own body contains semicolons a naive statement-by-statement split would sever mid
 * declaration -- postgres-js's simple query protocol (the path `unsafe()` takes with no bound
 * parameters) runs a whole multi-statement script exactly the way psql does.
 *
 * The header instructions on every one of these scripts assume ONE `psql -f` invocation per
 * file -- a fresh connection each time, so a script whose DO block raises an uncaught exception
 * can never affect the next one. This harness reuses a single connection across all four for
 * efficiency, and when a script's own DO block raises (an assertion failure, not the one
 * SQLSTATE-22023 case it catches itself), the aborted-transaction state that leaves behind
 * would otherwise fail EVERY later script with a misleading "current transaction is aborted"
 * rather than that script's own real outcome. The leading `ROLLBACK` is the isolation a fresh
 * connection would have given for free; it is a no-op (with a harmless notice) whenever the
 * previous script already rolled back cleanly on its own.
 */
async function runSpatialTestFile(client: postgres.Sql, file: string): Promise<void> {
  await client.unsafe("ROLLBACK").catch(() => {});
  const body = readFileSync(`drizzle/tests/${file}`, "utf8");
  await client.unsafe(body);
}

type SetupOutcome = "ready" | "postgis-unavailable" | "setup-failed";

describe.skipIf(ADMIN_DSN === undefined)("manual spatial SQL scripts against real PostGIS", () => {
  const databaseName = `plantgeo_postgis_spatial_${randomUUID().replace(/-/g, "").slice(0, 16)}`;
  let admin: postgres.Sql | undefined;
  let client: postgres.Sql | undefined;
  let outcome: SetupOutcome = "setup-failed";
  let failureReason = "";
  let notices: string[] = [];
  // Tracked separately from `outcome`: a migration can fail AFTER the database was created,
  // which must still be dropped even though `outcome` ends up "setup-failed" rather than
  // "ready". Guarding cleanup on `outcome` alone either leaks that database or -- the actual
  // bug this flag replaced -- attempts a DROP DATABASE over a connection whose CREATE DATABASE
  // never ran (e.g. an auth failure before either), turning one informative skip into an
  // unhandled second failure from afterAll.
  let databaseCreated = false;

  beforeAll(async () => {
    try {
      admin = postgres(ADMIN_DSN as string, { max: 1, onnotice: () => {} });

      const available = await admin.unsafe(
        `SELECT 1 FROM pg_available_extensions WHERE name = 'postgis'`
      );
      if (available.length === 0) {
        outcome = "postgis-unavailable";
        return;
      }

      await admin.unsafe(`CREATE DATABASE ${databaseName}`);
      databaseCreated = true;

      const target = new URL(ADMIN_DSN as string);
      target.pathname = `/${databaseName}`;
      client = postgres(target.toString(), {
        max: 1,
        onnotice: (notice) => notices.push(notice.message ?? String(notice)),
      });

      await client.unsafe(`CREATE EXTENSION postgis`);
      for (const file of migrationFilesThroughCeiling()) {
        await applyMigration(client, file);
      }

      outcome = "ready";
    } catch (error) {
      outcome = "setup-failed";
      failureReason = error instanceof Error ? error.message : String(error);
      console.warn(`[postgis-spatial] setup failed — every case will skip: ${failureReason}`);
    }
  }, 120_000);

  afterAll(async () => {
    await client?.end();
    if (databaseCreated && admin !== undefined) {
      try {
        await admin.unsafe(`DROP DATABASE IF EXISTS ${databaseName} WITH (FORCE)`);
      } catch (error) {
        console.warn(
          `[postgis-spatial] failed to drop ${databaseName}: ` +
            (error instanceof Error ? error.message : String(error))
        );
      }
    }
    await admin?.end();
  }, 60_000);

  for (const { file, finalNotice } of SPATIAL_TEST_FILES) {
    it(`${file} runs clean against a real PostGIS-migrated database`, async (ctx) => {
      ctx.skip(
        outcome === "postgis-unavailable",
        "postgis extension is not available on POSTGIS_TEST_DSN's server"
      );
      ctx.skip(outcome === "setup-failed", `harness setup failed: ${failureReason}`);
      if (client === undefined) throw new Error("unreachable: client undefined once outcome is ready");

      notices = [];
      // No throw is the base claim every one of these scripts makes about itself (BEGIN /
      // ROLLBACK, so nothing it does outlives this call regardless of pass or fail) -- but a
      // script could in principle stay silent and still not throw, so the specific final
      // notice is the actual evidence that every assertion inside its DO block ran and passed.
      await expect(runSpatialTestFile(client, file)).resolves.toBeUndefined();
      expect(notices.some((notice) => notice.includes(finalNotice))).toBe(true);
    });
  }
});
