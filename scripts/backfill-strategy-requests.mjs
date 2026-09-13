// One-time backfill: promote every `strategy_requests` row into a published, request-kind
// `geo.features` row, in the exact shape `interventions.submitRequest` writes.
//
// Track `public_strategy_requests_20260913`, Phase 3. Phase 1 verified production directly:
// `strategy_requests` holds 3 rows (all from one account) and `request_votes` holds 0. Phase 1
// chose to MIGRATE those rows rather than discard them -- a submitter's past ask vanishing is a
// worse outcome than it becoming public, which is the direction the product owner asked for.
//
// RUN ORDER IS LOAD-BEARING: this script must run BEFORE `drizzle/0004_public_strategy_requests.sql`
// drops `strategy_requests`. It reads a table the migration deletes, so after the migration lands
// there is nothing left to copy and the rows are gone for good. Running it first is what makes the
// window where a submitted request has nowhere to live not exist.
//
// It is deliberately NOT wired into `scripts/migrate.mjs` (the Railway `preDeployCommand`). That
// runner applies the Drizzle folder as one ordered batch and has no hook that can interleave a data
// step between two migrations; adding one would put a bespoke, one-shot data copy on the critical
// path of every future deploy. This is a human-run step, once, by whoever holds deploy authority.
//
// Usage (from the repo root, against the PRODUCTION DSN -- see "Run against prod, not local"):
//   node scripts/backfill-strategy-requests.mjs                 # dry run: reports, writes nothing
//   node scripts/backfill-strategy-requests.mjs --apply         # performs the insert
//
// Idempotent: each inserted row is stamped with `properties.migratedFromStrategyRequestId`, and a
// request whose stamp is already present is skipped. Re-running after a partial failure resumes.

import { pathToFileURL } from "node:url";
import postgres from "postgres";

/**
 * The six `strategy_requests` strategy types, mapped onto the unified `InterventionType`
 * vocabulary Phase 2 landed. All six are land-category members -- `water_harvesting` was the one
 * that did not exist before and was added to `LAND_INTERVENTION_TYPES` in Phase 2 precisely so
 * this mapping needs no lossy fallback. A type outside this table is a row the script refuses to
 * guess at rather than silently mislabel.
 */
export const REQUEST_TYPE_BY_STRATEGY = Object.freeze({
  keyline: "keyline",
  silvopasture: "silvopasture",
  reforestation: "reforestation",
  biochar: "biochar",
  water_harvesting: "water_harvesting",
  cover_cropping: "cover_cropping",
});

/** Status a request is written with; mirrors `REQUEST_STATUS` in the interventions router. */
export const REQUEST_STATUS = "published";

/** Discriminator the merged map layer paints on and the detail modal labels on. */
export const REQUEST_KIND = "request";

/**
 * Builds the `geo.features.properties` bag for one legacy request.
 *
 * Kept exported and pure so the shape is asserted in a test
 * (`src/__tests__/services/strategy-request-backfill.test.ts`) rather than only proven by running
 * the script against a live database, which this repo never does locally.
 *
 * The bag is `submitRequest`'s bag plus two provenance fields:
 *   - `migratedFromStrategyRequestId` is the idempotency key AND the audit trail back to the row
 *     the migration then drops.
 *   - `originalCreatedAt` preserves when the contributor actually asked. `geo.features.created_at`
 *     is set by the insert and would otherwise re-date every migrated request to backfill day.
 * `geom` is NOT set here: the `geo_features_sync_geom` BEFORE INSERT trigger derives it from
 * `properties.geometry`, which is the same path `submitRequest` relies on.
 */
export function buildRequestFeatureProperties(row) {
  const type = REQUEST_TYPE_BY_STRATEGY[row.strategy_type];
  if (!type) {
    throw new Error(
      `strategy_requests.${row.id}: unmapped strategy_type "${row.strategy_type}"`
    );
  }
  if (!Number.isFinite(row.lat) || !Number.isFinite(row.lon)) {
    throw new Error(
      `strategy_requests.${row.id}: lat/lon is not a finite coordinate pair`
    );
  }

  return {
    kind: REQUEST_KIND,
    name: row.title,
    type,
    // Requests are land-category-only (OQ-D); there is one value it can be.
    category: "land",
    description: row.description ?? null,
    // GeoJSON order is [longitude, latitude]. The legacy columns are named the other way round,
    // which is exactly the kind of swap a backfill quietly ships and a map then shows in the sea.
    geometry: { type: "Point", coordinates: [row.lon, row.lat] },
    submittedByUserId: row.user_id,
    // No team arm, deliberately. The private/team-scoped visibility `getRequests` enforced is the
    // boundary this track removes; writing `submittedByTeamId` here would re-create it through
    // `isFeatureVisibleTo`'s workspace clause for exactly the rows being made public.
    submittedByTeamId: null,
    // These rows predate the consent checkbox in its public form, but the product decision is that
    // a request IS public; the flag records that this row is one the platform may show, which is
    // what `listProposed` and the visibility rule read it for.
    publicationConsent: true,
    migratedFromStrategyRequestId: row.id,
    originalCreatedAt:
      row.created_at instanceof Date
        ? row.created_at.toISOString()
        : (row.created_at ?? null),
  };
}

async function main() {
  const apply = process.argv.includes("--apply");
  const connectionString =
    process.env.BACKFILL_DATABASE_URL || process.env.DATABASE_URL;
  if (!connectionString) {
    console.error(
      "backfill-strategy-requests: set BACKFILL_DATABASE_URL or DATABASE_URL"
    );
    process.exit(1);
  }

  // Mirrors scripts/migrate.mjs: single connection, no forced TLS (production is reached over
  // Railway's private network), notices silenced so the geom-sync trigger does not spam the log.
  const sql = postgres(connectionString, {
    max: 1,
    idle_timeout: 5,
    connect_timeout: 30,
    onnotice: () => {},
  });

  try {
    const [layer] = await sql`
      SELECT id FROM geo.layers WHERE name = 'interventions' LIMIT 1
    `;
    if (!layer) {
      throw new Error(
        "the 'interventions' geo.layers row does not exist; a request has no layer to land in"
      );
    }

    const tableExists = await sql`
      SELECT to_regclass('public.strategy_requests') AS table_name
    `;
    if (!tableExists[0]?.table_name) {
      console.log(
        "backfill-strategy-requests: strategy_requests no longer exists -- the drop migration has already run, nothing to do"
      );
      return;
    }

    const rows = await sql`
      SELECT id, user_id, strategy_type, title, description, lat, lon, created_at
      FROM public.strategy_requests
      ORDER BY created_at
    `;

    const alreadyMigrated = await sql`
      SELECT properties ->> 'migratedFromStrategyRequestId' AS source_id
      FROM geo.features
      WHERE properties ->> 'migratedFromStrategyRequestId' IS NOT NULL
    `;
    const migratedIds = new Set(alreadyMigrated.map((row) => row.source_id));

    let inserted = 0;
    let skipped = 0;

    for (const row of rows) {
      if (migratedIds.has(row.id)) {
        skipped += 1;
        console.log(`[skip] ${row.id} "${row.title}" already migrated`);
        continue;
      }

      const properties = buildRequestFeatureProperties(row);
      if (!apply) {
        console.log(
          `[dry-run] would insert ${row.id} "${properties.name}" (${properties.type}) at ` +
            `${properties.geometry.coordinates.join(", ")}`
        );
        continue;
      }

      const [feature] = await sql`
        INSERT INTO geo.features (layer_id, status, properties)
        VALUES (${layer.id}, ${REQUEST_STATUS}, ${sql.json(properties)})
        RETURNING id
      `;
      inserted += 1;
      console.log(`[insert] ${row.id} -> geo.features.${feature.id}`);
    }

    console.log(
      `backfill-strategy-requests: source=${rows.length} inserted=${inserted} ` +
        `skipped=${skipped} mode=${apply ? "apply" : "dry-run"}`
    );
    if (!apply) {
      console.log(
        "backfill-strategy-requests: nothing was written. Re-run with --apply, then land drizzle/0004_public_strategy_requests.sql."
      );
    }
  } catch (error) {
    console.error("backfill-strategy-requests: failed");
    console.error(error);
    process.exitCode = 1;
  } finally {
    await sql.end({ timeout: 5 }).catch(() => {});
  }
}

// Runs only when invoked as a script. Importing the module (the shape test does) must never open
// a database connection, so the entry point is compared as a resolved file URL rather than by
// name -- a basename match would also fire for any similarly named file.
if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  await main();
}
