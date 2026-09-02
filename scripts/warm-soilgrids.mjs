// Bounded, resumable SoilGrids warm driver -- persists the six topsoil properties
// (pH, organic carbon, nitrogen, bulk density, CEC, OCD) into `public.soil_grid_cache`.
//
// Why a warm driver exists at all: SoilGrids v2.0 is a STATIC raster, not a time series,
// so there is no backfill lane for it -- each cell is fetched once and stays valid. The
// only path that ever populated it was `app/api/ingest/soil/route.ts`, a hand-invoked
// endpoint capped at 16 points per call, which is why the table held a single row.
//
// Why it samples `agri.spatial_cell` rather than a bbox grid: `soilgrids.ts` quantizes its
// cache to CACHE_CELL_DEGREES = 0.001 (~111 m), finer than the 250 m source pixel, so a
// naive PNW sweep is ~98M cells -- not a coverage strategy at 13 s/point. The
// `sentinel2-ndvi-0p25deg` grid is the same one soil moisture and temperature already
// populate, so sampling its centroids yields values that join to the existing soil views
// and costs ~1568 requests instead.
//
// jiti loads the TS service in place (with its "@/..." alias) so this stays a thin driver
// rather than duplicating the response validation, d_factor handling and coverage-gap
// semantics in soilgrids.ts. Same pattern as scripts/backfill-soil-survey.mjs.
import { createJiti } from "jiti";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import postgres from "postgres";

const maxCells = Number(process.argv[2] ?? 50);
const gridName = process.argv[3] ?? "sentinel2-ndvi-0p25deg";

// app/api/ingest/soil/route.ts paces at 13 s on ISRIC's documented ~5 req/min. Measured
// 2026-08-07: 7 requests at that spacing (~3.6 req/min, comfortably under the documented
// limit) still drew an upstream refusal, so the published rate is not the whole contract
// and 20 s is the conservative floor this walk uses instead.
const REQUEST_SPACING_MS = 20_000;

// A refusal is a throttle signal, not a dead end: the walk sleeps and retries the SAME
// point rather than abandoning it. The one-shot route breaks on first fault because it
// answers a request; this walk has 1500+ cells to place and would otherwise need days of
// 20-minute wakes to finish what one paced run can.
const THROTTLE_BACKOFF_MS = 120_000;
const MAX_CONSECUTIVE_THROTTLES = 3;

// A point that is refused twice and then succeeds never counts toward the run-scoped skip
// budget above, so a sustained *partial* throttle (roughly one admission in three) is not
// bounded by it: every point could cost two backoffs before landing, ~260 s each, which is
// ~8.7 h across 120 points -- close to what the run-scoped budget exists to prevent. This
// deadline is the backstop for that shape and leaves headroom inside its hourly executor lane.
const RUN_DEADLINE_MS = 45 * 60 * 1000;

// Matches CACHE_CELL_DEGREES in soilgrids.ts. The cache row a centroid resolves to is the
// quantized cell, so resumption has to compare on the same grid or every cell looks new.
const CACHE_CELL_DEGREES = 0.001;

function loadDatabaseUrl() {
  if (process.env.DATABASE_URL) return process.env.DATABASE_URL;
  for (const file of [".env.local", ".env"]) {
    try {
      for (const line of readFileSync(file, "utf8").split(/\r?\n/)) {
        const match = line.match(/^DATABASE_URL\s*=\s*(.*)$/);
        if (match) return match[1].replace(/^["']|["']$/g, "");
      }
    } catch {}
  }
  throw new Error("DATABASE_URL is not set and was not found in .env.local or .env");
}

function toCacheCell(value) {
  return Number((Math.round(value / CACHE_CELL_DEGREES) * CACHE_CELL_DEGREES).toFixed(3));
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

process.env.DATABASE_URL = loadDatabaseUrl();

const jiti = createJiti(import.meta.url, {
  alias: { "@": fileURLToPath(new URL("../src", import.meta.url)) },
});
const { getSoilProperties, SoilEvidenceUnavailableError, SoilUpstreamUnavailableError } =
  await jiti.import("../src/lib/server/services/soilgrids.ts");

const sql = postgres(process.env.DATABASE_URL, { ssl: "prefer", max: 1 });

const centroids = await sql`
  select st_x(centroid) as lon, st_y(centroid) as lat
  from agri.spatial_cell
  where grid_name = ${gridName}
`;
if (centroids.length === 0) {
  console.error(`[soilgrids] no cells found for grid ${gridName}`);
  await sql.end();
  process.exit(2);
}

const cachedRows = await sql`select lat, lon from public.soil_grid_cache`;
const cachedKeys = new Set(cachedRows.map((row) => `${row.lat},${row.lon}`));

// De-duplicated because several 0.25-degree centroids can quantize onto one cache cell.
const pending = [];
const seen = new Set();
for (const row of centroids) {
  const lat = toCacheCell(Number(row.lat));
  const lon = toCacheCell(Number(row.lon));
  const key = `${lat},${lon}`;
  if (cachedKeys.has(key) || seen.has(key)) continue;
  seen.add(key);
  pending.push({ lat, lon });
}

const targets = pending.slice(0, Math.max(0, Math.trunc(maxCells)));
console.log(
  `[soilgrids] grid=${gridName} cells=${centroids.length} cached=${cachedKeys.size} ` +
    `pending=${pending.length} thisRun=${targets.length} ` +
    `eta=${Math.round((targets.length * REQUEST_SPACING_MS) / 60_000)}min`
);

let cached = 0;
let noData = 0;
let skipped = 0;
let stoppedEarly = false;
let deadlineReached = false;

// Two budgets, two scopes. Per-point: a refused point must not consume the whole run's
// budget. Per-run: a run-wide refusal streak (ISRIC down, or the whole run banned) must
// still stop the run rather than spending ~9h retrying every remaining point in turn.
let consecutiveSkips = 0;
const runStartedAt = Date.now();

for (const [index, point] of targets.entries()) {
  if (Date.now() - runStartedAt > RUN_DEADLINE_MS) {
    deadlineReached = true;
    console.error(`[soilgrids] wall-clock deadline reached after ${index}/${targets.length} points; stopping`);
    break;
  }
  if (index > 0) await sleep(REQUEST_SPACING_MS);
  const label = `${index + 1}/${targets.length} ${point.lat},${point.lon}`;
  let placed = false;
  let consecutiveThrottles = 0;

  while (!placed) {
    const startedAt = Date.now();
    try {
      await getSoilProperties(point.lat, point.lon);
      cached += 1;
      consecutiveSkips = 0;
      placed = true;
      console.log(`[soilgrids] ${label} cached (${Date.now() - startedAt}ms)`);
    } catch (error) {
      // A verified coverage gap is a claim SoilGrids actually made; it is stored as
      // complete=false and must not be retried, so it counts as progress.
      if (error instanceof SoilEvidenceUnavailableError) {
        noData += 1;
        consecutiveSkips = 0;
        placed = true;
        console.log(`[soilgrids] ${label} no_data`);
        break;
      }
      if (error instanceof SoilUpstreamUnavailableError) {
        consecutiveThrottles += 1;
        if (consecutiveThrottles >= MAX_CONSECUTIVE_THROTTLES) {
          skipped += 1;
          consecutiveSkips += 1;
          placed = true;
          console.error(
            `[soilgrids] ${label} refused ${consecutiveThrottles}x; skipping to the next point`
          );
          if (consecutiveSkips >= MAX_CONSECUTIVE_THROTTLES) {
            stoppedEarly = true;
            console.error(
              `[soilgrids] ${consecutiveSkips} consecutive points refused; stopping the run`
            );
          }
          break;
        }
        console.warn(
          `[soilgrids] ${label} throttled (${consecutiveThrottles}/${MAX_CONSECUTIVE_THROTTLES}); ` +
            `backing off ${THROTTLE_BACKOFF_MS / 1000}s`
        );
        await sleep(THROTTLE_BACKOFF_MS);
        continue;
      }
      // Not known to be point-specific -- a Postgres proxy timeout or a code defect fails
      // the next point identically, so the run stops rather than burning its budget on
      // guaranteed repeats.
      console.error(`[soilgrids] ${label} failed:`, error?.message ?? error);
      stoppedEarly = true;
      break;
    }
  }

  if (stoppedEarly) break;
}

const remaining = pending.length - cached - noData;
console.log(
  `[soilgrids] done cached=${cached} noData=${noData} skipped=${skipped} remaining=${remaining} ` +
    `stoppedEarly=${stoppedEarly} deadlineReached=${deadlineReached}`
);

await sql.end({ timeout: 5 });
if (stoppedEarly) process.exitCode = 1;
