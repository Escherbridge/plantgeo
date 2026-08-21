// Repeatable, read-only data-quality gate over geo.features, its three observation planes, its
// matview refresh ledger and its tile-serving reachability -- the assessment the 2026-08-20/21
// audit program did by hand (conductor/RUNBOOK.md section 0.13), turned into a command anyone can
// re-run. Nothing here writes, VACUUMs, REFRESHes or ANALYZEs anything. It only SELECTs.
//
// WHY THIS EXISTS. The audit found four independent classes of defect that a one-off probe cannot
// catch twice: a dropped matview leaving four agent SQL tools throwing hard errors with no gate
// noticing (probe:observation-planes); two soil-survey matviews in standing failure for 10+ days
// with no alert (probe:storage-profile); a layer (interventions) fully wired end-to-end and
// permanently invisible because nothing ever flips its rows to 'published' (audit:serving-matrix);
// and a documented false "dead lane" verdict from checking only one of the three observation planes
// (probe:observation-planes, agri-zero-landing-bug-class). None of those needed a human to notice --
// they needed a script that runs the same checks on a schedule and fails loudly.
//
// THE 2 GB / 30 s DISCIPLINE THIS SCRIPT IS BUILT ON, same rules the audit ran under:
//   * SET statement_timeout is issued ONCE, at session start, and therefore covers every statement
//     this script issues after that point -- there is no bare `client.unsafe(...)` anywhere below
//     that could run unbounded. See STATEMENT_TIMEOUT_MS in the config block.
//   * geo.features (7.9 GB, 5.09M live rows and climbing -- ingestion is live) is NEVER scanned in
//     full. Row counts come from `geo.mv_layer_feature_stats` (a matview snapshot, its own staleness
//     reported alongside every number pulled from it) or from `pg_class.reltuples` (a catalog
//     estimate, zero I/O). The one place this script reads feature rows directly is a single
//     TABLESAMPLE SYSTEM pass over the whole table, sized from SAMPLE_FRACTION_PCT -- the audit
//     measured 0.2-0.5% completing in 1-11 s and 3-5% reliably timing out at 30 s
//     (probe:layer-quality), so the default here sits inside the fast band with margin.
//   * Per-layer freshness/coverage queries filter on `layer_id` first, which is exactly the shape
//     the audit already proved safe at a 90-day window ("90-day per-day created_at row counts...
//     all sub-13s", probe:layer-quality) -- this script defaults to a shorter window and still
//     treats a timeout on any one layer as data, not a crash: that layer's row is marked
//     "unavailable (statement_timeout)" and the run continues.
//
// USAGE (never read the exit code through a pipe -- run the command, then check $? on its own):
//   node scripts/data-quality-report.mjs                     # human-readable tables
//   node scripts/data-quality-report.mjs --json               # machine-readable, same data
//   node scripts/data-quality-report.mjs --days=30             # widen the coverage window
//   node scripts/data-quality-report.mjs; echo $?               # WRONG in a pipeline, RIGHT alone
//   node scripts/data-quality-report.mjs | tee out.txt; echo $?  # WRONG -- $? is tee's, not this
//   node scripts/data-quality-report.mjs > out.txt
//   echo $?                                                       # RIGHT -- checked on its own line
//
// EXIT CODE: 0 when no declared threshold (see the CONFIG block) is breached, 1 when at least one
// FAIL-severity violation fires, 1 on any hard error (bad DSN, unreachable database, etc). WARN
// severity findings are printed but never change the exit code -- they are known, already-accepted
// states (e.g. interventions holding zero published rows) that would make this gate permanently red
// if they failed the build. Wire this into CI or a Railway cron once it has run clean a few times.
import postgres from "postgres";
import { readFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

// -------------------------------------------------------------------------------------------
// CONFIG -- every threshold this script can fail on, in one place, each with what breaching it
// actually means. Change a number here, not inline in a check function, so a future reader can
// see the whole gate surface in one read.
// -------------------------------------------------------------------------------------------
const CONFIG = {
  /**
   * Session-level statement_timeout, milliseconds. Applied ONCE after connecting so it covers
   * every statement this script issues (hard rule: 30 s ceiling on production; 25 s leaves
   * headroom for network latency between this process and Railway so a query that is genuinely
   * at the 30 s wall gets cancelled by Postgres, not by a client-side abort that leaves the
   * backend running -- the exact leak audit:render-path measured against Martin's pool).
   */
  STATEMENT_TIMEOUT_MS: 25_000,

  /**
   * TABLESAMPLE SYSTEM percentage for the one whole-table geometry/NULL-column pass. Measured
   * band (probe:layer-quality): 0.2-0.5% completes in 1-11 s; 3-5% reliably times out at 30 s on
   * this box. Breaching this means the sample itself times out -- lower it, do not raise it.
   */
  SAMPLE_FRACTION_PCT: 0.3,

  /** Fixed seed so re-runs sample the same rows and a diff between two runs means something. */
  SAMPLE_SEED: 42,

  /**
   * Default lookback for the per-day coverage table, days. 14 keeps every per-layer query well
   * inside the sub-13s band the audit measured at a 90-day window (probe:layer-quality); override
   * with --days=N for a deeper look when timeout risk is acceptable.
   */
  COVERAGE_WINDOW_DAYS: 14,

  /**
   * Per-layer "days since last row" ceiling. A layer with a threshold of `null` is exempt --
   * either it has no forward producer by design (a reference snapshot) or it is a known, already-
   * tracked absence (interventions, audit:ingestion). Breaching a numeric threshold means a
   * layer's forward producer stopped writing and nothing downstream noticed -- exactly the
   * "rotting layer" pattern probe:layer-quality named without a gate to catch it.
   *
   * Sources for the numbers: 3 days is one missed hourly-cron day plus slack for the daily-cadence
   * layers (fire-detections, water-gauges, sensors, weather-observations, fire-perimeters,
   * evacuation-zones -- all hourly forward producers per audit:ingestion). 7 days covers
   * vegetation's declared 5-day Sentinel-2 revisit cadence plus one missed pass. 21 days covers
   * burn-severity's weekly plantgeo-cron-mtbs against a quarterly upstream (audit:ingestion) and
   * soil-survey's lazy viewport-triggered warmer, which has no cadence at all by design but was
   * observed writing as recently as every few days during active use (probe:layer-quality).
   * watersheds and interventions are `null`: watersheds is a one-time NHDPlus HR snapshot with no
   * cron anywhere in infra/ (audit:ingestion finding "watersheds has a complete, working producer
   * that no cron or lane ever invokes"); interventions has no producer anywhere in the stack at
   * all (audit:ingestion) and is tracked instead by MAX_LAYERS_WITH_NO_PUBLISHED_ROWS below.
   */
  LAYER_STALENESS_DAYS: {
    "fire-detections": 3,
    "water-gauges": 3,
    sensors: 3,
    "weather-observations": 3,
    "fire-perimeters": 3,
    "evacuation-zones": 3,
    vegetation: 7,
    "burn-severity": 21,
    "soil-survey": 21,
    watersheds: null,
    interventions: null,
  },
  /** Applied to any geo.layers row not named above, so a twelfth layer still gets a real gate. */
  DEFAULT_LAYER_STALENESS_DAYS: 3,

  /**
   * Within the coverage window, how many zero-row days a CONTINUOUS layer (staleness threshold
   * <= this same number, i.e. expected to write roughly daily) may show before it counts as a
   * genuine hole rather than ordinary day-to-day variance. Breaching this on a continuous layer
   * means ingestion silently stopped for a stretch inside the window and then silently resumed --
   * the exact shape a "last row" check alone cannot see, because the layer looks fresh again by
   * the time anyone looks.
   */
  MAX_HOLE_DAYS_IN_WINDOW: 1,
  /** A layer's staleness threshold has to be at or below this to count as "continuous" above. */
  CONTINUOUS_LAYER_STALENESS_CEILING_DAYS: 3,

  /**
   * Sampled NULL-geom / invalid-geom / NULL-geometry_id fraction ceiling, per layer. The audit's
   * full sample (probe:layer-quality) found 0% across every layer and every column checked, so any
   * nonzero fraction above this floor is new -- either a producer regression or a migration that
   * skipped the geometry-repair trigger.
   */
  MAX_NULL_OR_INVALID_GEOM_FRACTION: 0.01,

  /**
   * Duplicate `properties->>'id'` count, per layer. This is NOT sampled: `geo.
   * features_layer_external_id_unique` is a VALID partial UNIQUE index on exactly
   * `(layer_id, properties->>'id') WHERE properties ? 'id'`, which makes a duplicate for any row
   * carrying an 'id' key structurally impossible for as long as the index stays valid and ready
   * (probe:layer-quality confirmed this directly against the live index, and confirmed 0 real
   * duplicates on every layer a full scan could reach in budget). Breaching this threshold means
   * the index itself is not valid/ready -- the uniqueness guarantee has silently stopped holding,
   * which is a materially worse finding than a handful of duplicate rows would be.
   */
  MAX_DUPLICATE_ID_COUNT: 0,

  /**
   * Consecutive failed refresh attempts (agri.matview_refresh_state.consecutive_failures) before a
   * matview counts as FAILing rather than merely backing off. 3 matches the pattern already
   * observed live on geo.mv_soil_survey_grid and geo.mv_soil_survey_union, both stuck at 4
   * consecutive failures for 10+ days with no alert anywhere (probe:storage-profile).
   */
  MAX_MATVIEW_CONSECUTIVE_FAILURES: 3,

  /**
   * How many of a source's ingested releases may land in NONE of the three observation planes
   * (agri.signal_observation / agri.forecast_observation / agri.normalized_source_feature) before
   * this counts as a regression rather than the known baseline. The audit measured exactly 8 such
   * releases on open-meteo-era5-land-archive out of 202 and explicitly did not root-cause them
   * (probe:observation-planes); 10 leaves room for that known baseline while catching a new source
   * that starts landing nowhere.
   */
  MAX_ZERO_LANDING_RELEASES_PER_SOURCE: 10,

  /**
   * How many geo.layers rows may have zero published features before this counts as a regression.
   * Exactly one today -- interventions, which has no ingestion producer anywhere in the stack
   * (audit:ingestion) and whose two seed rows are stuck at status='approved', never 'published'
   * (audit:serving-matrix). Breaching this means a SECOND layer went dark.
   */
  MAX_LAYERS_WITH_NO_PUBLISHED_ROWS: 1,

  /**
   * How many geo.layers rows may hold published rows while having no live path from Postgres to
   * the browser (no tile function found for the layer name, or a tile function exists but is not
   * registered in infra/martin/martin.yaml, or it is registered but the client has no source id
   * for it). This must always be 0: a layer in this state has real data that no map view can ever
   * render, silently, with no error anywhere -- audit:qa-gate's finding that nothing ties
   * martin.yaml to DYNAMIC_TILE_SOURCE_IDS is exactly the gap this check closes.
   */
  MAX_LAYERS_WITH_DATA_BUT_NO_TILE_PATH: 0,
};

// -------------------------------------------------------------------------------------------
// CLI args -- same flag/option split as scripts/partition-features.mjs.
// -------------------------------------------------------------------------------------------
const args = process.argv.slice(2);
const flags = new Set(args.filter((arg) => !arg.includes("=")));
const options = new Map(
  args
    .filter((arg) => arg.includes("="))
    .map((arg) => [arg.slice(0, arg.indexOf("=")), arg.slice(arg.indexOf("=") + 1)])
);

const jsonOutput = flags.has("--json");
const coverageWindowDays = Number.parseInt(options.get("--days") ?? "", 10) || CONFIG.COVERAGE_WINDOW_DAYS;

// Same precedence as scripts/partition-features.mjs and scripts/apply-pre-aggregation.mjs.
const connectionString =
  process.env.PARTITION_DATABASE_URL ||
  process.env.MIGRATION_DATABASE_URL ||
  process.env.DATABASE_URL;
if (!connectionString) {
  console.error(
    "data-quality-report: set PARTITION_DATABASE_URL (or MIGRATION_DATABASE_URL / DATABASE_URL)"
  );
  process.exit(1);
}

const REPO_ROOT = path.resolve(new URL(".", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"), "..");
const MARTIN_CONFIG_PATH = path.join(REPO_ROOT, "infra", "martin", "martin.yaml");
const SOURCES_TS_PATH = path.join(REPO_ROOT, "src", "lib", "map", "sources.ts");

const client = postgres(connectionString, {
  max: 1,
  idle_timeout: 5,
  connect_timeout: 30,
  onnotice: () => {}, // read-only report; catalog NOTICEs (e.g. "already exists") do not apply here
});

// -------------------------------------------------------------------------------------------
// Small helpers -- same shapes as scripts/partition-features.mjs.
// -------------------------------------------------------------------------------------------

function formatCount(value) {
  if (value === null || value === undefined) return "n/a";
  return Number(value).toLocaleString("en-US");
}

function formatPercent(fraction) {
  if (fraction === null || fraction === undefined || Number.isNaN(fraction)) return "n/a";
  return `${(fraction * 100).toFixed(2)}%`;
}

function daysSince(timestamp, now) {
  if (!timestamp) return null;
  const ms = now.getTime() - new Date(timestamp).getTime();
  return ms / (24 * 60 * 60 * 1000);
}

function isTimeoutError(error) {
  // 57014 = query_canceled, the SQLSTATE a statement_timeout cancellation raises.
  return error && error.code === "57014";
}

/** One finding. severity FAIL flips the exit code; WARN is printed only. */
function addViolation(violations, severity, code, message) {
  violations.push({ severity, code, message });
}

// -------------------------------------------------------------------------------------------
// SECTION layers -- the 11 (or N) geo.layers rows, joined against the per-layer count matview.
// -------------------------------------------------------------------------------------------

async function readLayers() {
  return client.unsafe(`SELECT id, name, is_public FROM geo.layers ORDER BY name`);
}

/**
 * geo.mv_layer_feature_stats: layer_id, layer_name, published_count, newest_created_at,
 * newest_updated_at -- one row per layer, LEFT JOINed so an empty layer still reports as empty
 * rather than as absent (drizzle/0029's own header). A matview SNAPSHOT, not a live count -- its
 * own refresh staleness is read separately below and printed alongside every number pulled here,
 * because a number whose provenance is unclear is how this project got its wrong row counts in
 * the first place (this build task's own instructions).
 */
async function readLayerStats() {
  return client.unsafe(
    `SELECT layer_id, layer_name, published_count, newest_created_at, newest_updated_at
       FROM geo.mv_layer_feature_stats
      ORDER BY layer_name`
  );
}

/**
 * geo.v_observation_day_census: a plain VIEW (not materialized) unioning the three per-surface day
 * matviews. Its own header calls it "free: it sits over roughly 35,000 rows in total, which is a
 * sort, not a scan" (drizzle/0029) -- so aggregating MAX(observed_day) per feature-kind surface
 * here costs nothing like a per-layer scan of geo.features would. surface_kind='feature' scopes
 * this to the 11 geo.layers-backed surfaces; signal/drought surfaces are out of scope for this
 * per-layer section (they have no geo.layers row) and are covered by the observation-planes
 * section instead.
 */
async function readNewestObservedDayByLayer() {
  const rows = await client.unsafe(
    `SELECT surface_name, MAX(observed_day) AS newest_observed_day
       FROM geo.v_observation_day_census
      WHERE surface_kind = 'feature'
      GROUP BY surface_name`
  );
  return new Map(rows.map((row) => [row.surface_name, row.newest_observed_day]));
}

/** How stale is the snapshot the layer numbers above are actually reading. */
async function readMatviewFreshness(qualifiedName) {
  const rows = await client.unsafe(
    `SELECT refreshed_at, outcome, consecutive_failures
       FROM agri.matview_refresh_state
      WHERE view_name = $1`,
    [qualifiedName]
  );
  return rows[0] ?? null;
}

// -------------------------------------------------------------------------------------------
// SECTION sample -- ONE TABLESAMPLE pass over the whole table, grouped by layer. This is the
// only place this script reads geo.features rows directly rather than through a matview.
// -------------------------------------------------------------------------------------------

/**
 * NULL geom, invalid geom (ST_IsValid), NULL geometry_id and data_available_at presence, all from
 * one bounded TABLESAMPLE SYSTEM pass, grouped by layer. Mirrors probe:layer-quality's exact
 * method ("TABLESAMPLE SYSTEM(0.2) REPEATABLE(7) JOIN layers GROUP BY name, 0.75s") rather than
 * per-layer exact scans, because an exact scan is a full-layer read for the two layers that carry
 * 87.6% of the table (fire-detections, water-gauges) and this script must never do that.
 * duplicate `properties->>'id'` is deliberately NOT computed here -- see
 * CONFIG.MAX_DUPLICATE_ID_COUNT for why the index-validity check below is the correct, cheaper
 * proof instead of a scan.
 */
async function readGeometrySample(sampleFractionPct, seed) {
  // TABLESAMPLE's method-argument list is not a reliable place to bind an extended-protocol
  // parameter across postgres.js/PostgreSQL version combinations, so the two values are
  // interpolated directly -- both come from CONFIG (trusted internal constants), never from user
  // input, the same trust boundary partition-features.mjs relies on for its own inline constants
  // (e.g. CHUNK_TARGET_ROWS). Validated as finite numbers first so a bad CONFIG edit fails loudly
  // instead of building an injectable string.
  if (!Number.isFinite(sampleFractionPct) || sampleFractionPct <= 0 || sampleFractionPct > 100) {
    throw new Error(`data-quality-report: CONFIG.SAMPLE_FRACTION_PCT must be in (0, 100], got ${sampleFractionPct}`);
  }
  if (!Number.isInteger(seed)) {
    throw new Error(`data-quality-report: CONFIG.SAMPLE_SEED must be an integer, got ${seed}`);
  }
  const startedAt = process.hrtime.bigint();
  const rows = await client.unsafe(
    `SELECT l.name AS layer_name,
            count(*)::bigint AS sampled_rows,
            count(*) FILTER (WHERE f.geom IS NULL)::bigint AS null_geom,
            count(*) FILTER (WHERE f.geom IS NOT NULL AND NOT ST_IsValid(f.geom))::bigint AS invalid_geom,
            count(*) FILTER (WHERE f.geometry_id IS NULL)::bigint AS null_geometry_id,
            count(*) FILTER (WHERE f.data_available_at IS NOT NULL)::bigint AS data_available_at_present
       FROM geo.features AS f TABLESAMPLE SYSTEM (${sampleFractionPct}) REPEATABLE (${seed})
       JOIN geo.layers AS l ON l.id = f.layer_id
      GROUP BY l.name
      ORDER BY l.name`
  );
  const elapsedSeconds = Number(process.hrtime.bigint() - startedAt) / 1e9;
  return { rows, elapsedSeconds };
}

/**
 * geo.features_layer_external_id_unique's validity, read from the catalog only -- no scan. See
 * CONFIG.MAX_DUPLICATE_ID_COUNT for the reasoning: while this index is VALID and READY, duplicate
 * `properties->>'id'` values within a layer are structurally impossible for any row carrying an
 * 'id' key, which is what makes this a real proof rather than a heuristic.
 */
async function readDuplicateIdGuarantee() {
  const rows = await client.unsafe(
    `SELECT indisvalid, indisready, indisunique
       FROM pg_index
      WHERE indexrelid = to_regclass('geo.features_layer_external_id_unique')`
  );
  return rows[0] ?? null;
}

// -------------------------------------------------------------------------------------------
// SECTION coverage -- per-day row counts over a configurable trailing window, per layer.
// -------------------------------------------------------------------------------------------

/**
 * One query per layer, filtered on layer_id first and bounded by a date-range predicate --
 * exactly the shape probe:layer-quality measured at "all sub-13s" over a 90-day window. A
 * per-layer timeout is caught and reported as unavailable rather than failing the whole run: a
 * slow layer is itself a finding, not a reason to lose every other layer's numbers.
 */
async function readCoverageForLayer(layerId, windowDays) {
  try {
    const rows = await client.unsafe(
      `SELECT date_trunc('day', created_at)::date AS day, count(*)::bigint AS rows
         FROM geo.features
        WHERE layer_id = $1 AND created_at >= (now() - ($2 || ' days')::interval)
        GROUP BY 1
        ORDER BY 1`,
      [layerId, String(windowDays)]
    );
    return { available: true, byDay: new Map(rows.map((row) => [row.day.toISOString().slice(0, 10), Number(row.rows)])) };
  } catch (error) {
    if (isTimeoutError(error)) return { available: false, reason: "statement_timeout", byDay: new Map() };
    throw error;
  }
}

/** Every calendar day from `windowDays - 1` days ago through yesterday (today is a partial day). */
function expectedWindowDays(windowDays, now) {
  const days = [];
  for (let offset = windowDays; offset >= 1; offset -= 1) {
    const day = new Date(now);
    day.setUTCDate(day.getUTCDate() - offset);
    days.push(day.toISOString().slice(0, 10));
  }
  return days;
}

// -------------------------------------------------------------------------------------------
// SECTION observation-planes -- all three, never just one. Checking only agri.signal_observation
// once produced a documented false "dead lane" report (probe:observation-planes,
// agri-zero-landing-bug-class): a source can land zero rows there and still be alive in
// agri.forecast_observation. This script always reads all three.
// -------------------------------------------------------------------------------------------

/**
 * signal_observation is read via pg_class.reltuples (catalog estimate, zero I/O) because it is
 * 46M rows / 26 GB -- an exact count would be exactly the kind of full scan this script must never
 * do. The other two are small enough for an exact COUNT(*) (established live: 184,409 and 0 rows;
 * probe:observation-planes) and are counted exactly so a regression from "small and exact" to
 * "unexpectedly large" is caught rather than laundered through an estimate.
 */
async function readObservationPlaneCounts() {
  const [signalEstimate] = await client.unsafe(
    `SELECT reltuples::bigint AS estimate FROM pg_class WHERE oid = to_regclass('agri.signal_observation')`
  );
  const [forecastExact] = await client.unsafe(`SELECT count(*)::bigint AS exact FROM agri.forecast_observation`);
  const [normalizedExact] = await client.unsafe(
    `SELECT count(*)::bigint AS exact FROM agri.normalized_source_feature`
  );
  return {
    signal_observation: { value: Number(signalEstimate.estimate), provenance: "pg_class.reltuples estimate" },
    forecast_observation: { value: Number(forecastExact.exact), provenance: "exact COUNT(*)" },
    normalized_source_feature: { value: Number(normalizedExact.exact), provenance: "exact COUNT(*)" },
  };
}

/**
 * Per data source: how many releases landed in each of the three planes, and how many landed in
 * NONE of them. Verbatim from
 * services/agri-data-service/src/agri_data_service/sql/execution/seasonal_source_landing_census.sql
 * (embedded rather than read off disk so this script has no dependency on the Python service's
 * layout) -- that file's own header explains why it pre-aggregates by DISTINCT source_release_id
 * rather than three correlated NOT EXISTS subqueries: the naive form measured 346 s against the
 * 46M-row signal plane, this form runs inside the statement_timeout set at session start.
 */
const SEASONAL_SOURCE_LANDING_CENSUS_SQL = `
WITH signal_landing AS (
    SELECT DISTINCT source_release_id FROM agri.signal_observation
),
forecast_landing AS (
    SELECT DISTINCT source_release_id FROM agri.forecast_observation
),
feature_landing AS (
    SELECT DISTINCT source_release_id FROM agri.normalized_source_feature
)
SELECT
    data_source.key AS source_key,
    count(*) AS release_count,
    count(*) FILTER (WHERE signal_landing.source_release_id IS NOT NULL) AS signal_observation_releases,
    count(*) FILTER (WHERE forecast_landing.source_release_id IS NOT NULL) AS forecast_observation_releases,
    count(*) FILTER (WHERE feature_landing.source_release_id IS NOT NULL) AS normalized_feature_releases,
    count(*) FILTER (
        WHERE signal_landing.source_release_id IS NULL
          AND forecast_landing.source_release_id IS NULL
          AND feature_landing.source_release_id IS NULL
    ) AS zero_landing_releases
FROM agri.source_release AS release
JOIN agri.data_source AS data_source
    ON data_source.id = release.data_source_id
LEFT JOIN signal_landing ON signal_landing.source_release_id = release.id
LEFT JOIN forecast_landing ON forecast_landing.source_release_id = release.id
LEFT JOIN feature_landing ON feature_landing.source_release_id = release.id
GROUP BY data_source.key
ORDER BY data_source.key`;

async function readLandingCensus() {
  return client.unsafe(SEASONAL_SOURCE_LANDING_CENSUS_SQL);
}

// -------------------------------------------------------------------------------------------
// SECTION matviews -- the refresh ledger, plus whether the relation is actually populated.
// -------------------------------------------------------------------------------------------

/**
 * Every row agri.matview_refresh_state holds (small table, unfiltered scan is safe), joined to
 * pg_class for relispopulated/reltuples/size. Covers both refresh lanes that write this table --
 * matview-refresh and strategy-mv-refresh (probe:observation-planes) -- because a row here is a
 * row here regardless of which lane wrote it.
 */
async function readMatviewLedger() {
  const rows = await client.unsafe(
    `SELECT mrs.view_name,
            mrs.refreshed_at,
            mrs.last_attempt_at,
            mrs.duration_ms,
            mrs.row_count,
            mrs.outcome,
            mrs.consecutive_failures,
            c.relispopulated,
            c.reltuples,
            pg_total_relation_size(c.oid) AS total_bytes
       FROM agri.matview_refresh_state AS mrs
       LEFT JOIN pg_class AS c ON c.oid = to_regclass(mrs.view_name)
      ORDER BY mrs.view_name`
  );
  return rows;
}

// -------------------------------------------------------------------------------------------
// SECTION tile-serving -- for each layer: does a tile function exist for it, is that function
// published in infra/martin/martin.yaml, and does the client have a source id for it.
// -------------------------------------------------------------------------------------------

/**
 * Every `*_tiles` function in schema geo, with the layer name (if any) its own body selects by.
 * Read from pg_get_functiondef rather than hardcoded, because the function name and the layer it
 * serves have already drifted once and silently (audit:serving-matrix's
 * `fire_risk_tiles`/`fire-perimeters` finding) -- a hardcoded map would have hidden that instead
 * of surfacing it. Every function this project ships filters with the literal shape
 * `l.name = '<layer>'` (drizzle/0033's header quotes it verbatim); a function with no such literal
 * (building_tiles, strategy_recommendations_tiles) legitimately serves no geo.layers row and is
 * reported as such, not as a parse failure.
 */
async function readTileFunctionLayerMap() {
  const rows = await client.unsafe(
    `SELECT p.proname AS function_name, pg_get_functiondef(p.oid) AS definition
       FROM pg_proc AS p
      WHERE p.pronamespace = 'geo'::regnamespace AND p.proname LIKE '%\\_tiles' ESCAPE '\\'
      ORDER BY p.proname`
  );
  const layerNameByFunction = new Map();
  for (const row of rows) {
    const match = /\bl\.name\s*=\s*'([^']+)'/.exec(row.definition);
    layerNameByFunction.set(row.function_name, match ? match[1] : null);
  }
  return layerNameByFunction;
}

/**
 * Parses infra/martin/martin.yaml's `postgres.functions:` block without a YAML dependency. The
 * block shape is fixed (2-space `functions:`, 4-space function ids, 6-space sub-keys, terminated
 * by the sibling `tables:` key at the same 2-space indent) and is asserted rather than assumed: if
 * the file's shape ever changes this throws instead of silently reporting zero published
 * functions, which would be a far more dangerous false negative for
 * CONFIG.MAX_LAYERS_WITH_DATA_BUT_NO_TILE_PATH.
 */
async function readMartinPublishedFunctions() {
  const text = await readFile(MARTIN_CONFIG_PATH, "utf8");
  const blockMatch = /\n {2}functions:\n([\s\S]*?)\n {2}\S/.exec(`${text}\n  \x00`);
  if (!blockMatch) {
    throw new Error(
      `data-quality-report: could not find a "  functions:" block in ${MARTIN_CONFIG_PATH} -- ` +
        "the file's shape has changed; update readMartinPublishedFunctions to match it."
    );
  }
  const ids = [...blockMatch[1].matchAll(/^ {4}(\w+):/gm)].map((match) => match[1]);
  return new Set(ids);
}

/**
 * Parses `DYNAMIC_TILE_SOURCE_IDS` out of src/lib/map/sources.ts by locating the array literal
 * rather than importing the module -- this is a Next.js/TypeScript file this script cannot import
 * directly from plain Node, and the array is a `const ... = [...] as const` of bare string
 * literals, which a bounded regex over the array body extracts exactly.
 */
async function readClientDynamicSourceIds() {
  const text = await readFile(SOURCES_TS_PATH, "utf8");
  const arrayMatch = /DYNAMIC_TILE_SOURCE_IDS\s*=\s*\[([\s\S]*?)\]/.exec(text);
  if (!arrayMatch) {
    throw new Error(
      `data-quality-report: could not find DYNAMIC_TILE_SOURCE_IDS in ${SOURCES_TS_PATH} -- ` +
        "the export was renamed or removed; update readClientDynamicSourceIds to match it."
    );
  }
  const ids = [...arrayMatch[1].matchAll(/"([^"]+)"/g)].map((match) => match[1]);
  return new Set(ids);
}

// -------------------------------------------------------------------------------------------
// Report assembly -- runs every section above, evaluates every CONFIG threshold, renders.
// -------------------------------------------------------------------------------------------

async function buildReport() {
  const now = new Date();
  const violations = [];
  const report = { generatedAt: now.toISOString(), config: CONFIG, coverageWindowDays };

  // --- layers -------------------------------------------------------------------------------
  const [layers, layerStats, newestObservedDayByLayer, layerStatsFreshness, geometrySample, duplicateGuarantee] =
    await Promise.all([
      readLayers(),
      readLayerStats(),
      readNewestObservedDayByLayer(),
      readMatviewFreshness("geo.mv_layer_feature_stats"),
      readGeometrySample(CONFIG.SAMPLE_FRACTION_PCT, CONFIG.SAMPLE_SEED),
      readDuplicateIdGuarantee(),
    ]);

  const statsByName = new Map(layerStats.map((row) => [row.layer_name, row]));
  const sampleByName = new Map(geometrySample.rows.map((row) => [row.layer_name, row]));

  if (!duplicateGuarantee || duplicateGuarantee.indisvalid !== true || duplicateGuarantee.indisready !== true) {
    addViolation(
      violations,
      "FAIL",
      "duplicate-id-guarantee-broken",
      "geo.features_layer_external_id_unique is missing, NOT VALID or NOT READY -- the structural " +
        "no-duplicate-ids proof no longer holds and duplicate properties->>'id' values are now " +
        "unchecked. See CONFIG.MAX_DUPLICATE_ID_COUNT."
    );
  }

  let layersWithNoPublishedRows = 0;
  report.layers = layers.map((layer) => {
    const stats = statsByName.get(layer.name) ?? null;
    const sample = sampleByName.get(layer.name) ?? null;
    const publishedCount = stats ? Number(stats.published_count) : null;
    const newestCreatedAt = stats?.newest_created_at ?? null;
    const newestUpdatedAt = stats?.newest_updated_at ?? null;
    const newestObservedDay = newestObservedDayByLayer.get(layer.name) ?? null;
    const lastActivityAt =
      newestCreatedAt && newestUpdatedAt
        ? new Date(newestCreatedAt) > new Date(newestUpdatedAt)
          ? newestCreatedAt
          : newestUpdatedAt
        : newestCreatedAt ?? newestUpdatedAt ?? null;
    const staleDays = daysSince(lastActivityAt, now);

    if (publishedCount === 0) layersWithNoPublishedRows += 1;

    const thresholdDays = Object.prototype.hasOwnProperty.call(CONFIG.LAYER_STALENESS_DAYS, layer.name)
      ? CONFIG.LAYER_STALENESS_DAYS[layer.name]
      : CONFIG.DEFAULT_LAYER_STALENESS_DAYS;
    if (thresholdDays !== null && publishedCount > 0) {
      if (staleDays === null) {
        addViolation(
          violations,
          "WARN",
          "layer-freshness-unknown",
          `${layer.name}: has ${formatCount(publishedCount)} published row(s) but no created_at/` +
            "updated_at in geo.mv_layer_feature_stats -- cannot evaluate freshness."
        );
      } else if (staleDays > thresholdDays) {
        addViolation(
          violations,
          "FAIL",
          "layer-stale",
          `${layer.name}: last row activity ${staleDays.toFixed(1)} day(s) ago, exceeds the ` +
            `${thresholdDays}-day threshold (CONFIG.LAYER_STALENESS_DAYS). Forward producer may ` +
            "have stopped writing -- see audit:ingestion for this layer's expected cadence."
        );
      }
    }

    let nullOrInvalidGeomFraction = null;
    let dataAvailableAtPresentFraction = null;
    if (sample && Number(sample.sampled_rows) > 0) {
      const sampledRows = Number(sample.sampled_rows);
      const nullOrInvalid = Number(sample.null_geom) + Number(sample.invalid_geom);
      nullOrInvalidGeomFraction = nullOrInvalid / sampledRows;
      dataAvailableAtPresentFraction = Number(sample.data_available_at_present) / sampledRows;
      if (nullOrInvalidGeomFraction > CONFIG.MAX_NULL_OR_INVALID_GEOM_FRACTION) {
        addViolation(
          violations,
          "FAIL",
          "geometry-quality",
          `${layer.name}: ${formatPercent(nullOrInvalidGeomFraction)} of ${formatCount(sampledRows)} ` +
            "sampled row(s) have a NULL or ST_IsValid()-failing geom, exceeds " +
            `${formatPercent(CONFIG.MAX_NULL_OR_INVALID_GEOM_FRACTION)} (CONFIG.` +
            "MAX_NULL_OR_INVALID_GEOM_FRACTION)."
        );
      }
      if (Number(sample.null_geometry_id) > 0 && layer.name !== "interventions") {
        // interventions is a known exception (probe:layer-quality: both seed rows have NULL
        // geometry_id, consistent with having no real producer) -- not gated, still surfaced.
        addViolation(
          violations,
          "WARN",
          "geometry-id-null",
          `${layer.name}: ${formatCount(sample.null_geometry_id)} of ${formatCount(sampledRows)} ` +
            "sampled row(s) have a NULL geometry_id (excluded from the geometry-history dimension)."
        );
      }
    }

    return {
      name: layer.name,
      isPublic: layer.is_public,
      publishedCount,
      publishedCountProvenance: "geo.mv_layer_feature_stats snapshot",
      newestCreatedAt,
      newestUpdatedAt,
      newestObservedDay,
      lastActivityDaysAgo: staleDays,
      stalenessThresholdDays: thresholdDays,
      geometrySample: sample
        ? {
            sampledRows: Number(sample.sampled_rows),
            nullGeomCount: Number(sample.null_geom),
            invalidGeomCount: Number(sample.invalid_geom),
            nullGeometryIdCount: Number(sample.null_geometry_id),
            nullOrInvalidGeomFraction,
            dataAvailableAtPresentFraction,
          }
        : null,
    };
  });

  report.snapshotFreshness = {
    "geo.mv_layer_feature_stats": layerStatsFreshness
      ? {
          refreshedAt: layerStatsFreshness.refreshed_at,
          refreshedMinutesAgo:
            layerStatsFreshness.refreshed_at != null
              ? (daysSince(layerStatsFreshness.refreshed_at, now) ?? 0) * 24 * 60
              : null,
          outcome: layerStatsFreshness.outcome,
        }
      : null,
  };

  if (layersWithNoPublishedRows > CONFIG.MAX_LAYERS_WITH_NO_PUBLISHED_ROWS) {
    addViolation(
      violations,
      "FAIL",
      "layers-with-no-published-rows",
      `${layersWithNoPublishedRows} layer(s) have zero published rows, exceeds ` +
        `${CONFIG.MAX_LAYERS_WITH_NO_PUBLISHED_ROWS} (CONFIG.MAX_LAYERS_WITH_NO_PUBLISHED_ROWS).`
    );
  } else if (layersWithNoPublishedRows > 0) {
    addViolation(
      violations,
      "WARN",
      "layers-with-no-published-rows",
      `${layersWithNoPublishedRows} layer(s) have zero published rows (within the accepted ` +
        `baseline of ${CONFIG.MAX_LAYERS_WITH_NO_PUBLISHED_ROWS}, currently "interventions" -- ` +
        "audit:serving-matrix)."
    );
  }

  report.geometrySampleMeta = {
    sampleFractionPct: CONFIG.SAMPLE_FRACTION_PCT,
    seed: CONFIG.SAMPLE_SEED,
    elapsedSeconds: Number(geometrySample.elapsedSeconds.toFixed(2)),
    note: "One TABLESAMPLE SYSTEM pass over geo.features, grouped by layer -- counts above are " +
      "estimates from the sampled rows, not exact per-layer totals.",
  };

  // --- coverage -------------------------------------------------------------------------------
  report.coverage = [];
  const expectedDays = expectedWindowDays(coverageWindowDays, now);
  for (const layer of layers) {
    const thresholdDays = Object.prototype.hasOwnProperty.call(CONFIG.LAYER_STALENESS_DAYS, layer.name)
      ? CONFIG.LAYER_STALENESS_DAYS[layer.name]
      : CONFIG.DEFAULT_LAYER_STALENESS_DAYS;
    const isContinuous = thresholdDays !== null && thresholdDays <= CONFIG.CONTINUOUS_LAYER_STALENESS_CEILING_DAYS;

    const coverage = await readCoverageForLayer(layer.id, coverageWindowDays);
    if (!coverage.available) {
      report.coverage.push({ layer: layer.name, available: false, reason: coverage.reason });
      addViolation(
        violations,
        "WARN",
        "coverage-unavailable",
        `${layer.name}: coverage query exceeded statement_timeout at a ${coverageWindowDays}-day ` +
          "window; try a shorter --days value for this layer."
      );
      continue;
    }

    const holeDays = expectedDays.filter((day) => !coverage.byDay.has(day) || coverage.byDay.get(day) === 0);
    report.coverage.push({
      layer: layer.name,
      available: true,
      windowDays: coverageWindowDays,
      daysWithRows: coverage.byDay.size,
      holeDays,
    });

    if (thresholdDays !== null && isContinuous && holeDays.length > CONFIG.MAX_HOLE_DAYS_IN_WINDOW) {
      addViolation(
        violations,
        "FAIL",
        "coverage-hole",
        `${layer.name}: ${holeDays.length} zero-row day(s) in the trailing ${coverageWindowDays}-day ` +
          `window (${holeDays.slice(0, 5).join(", ")}${holeDays.length > 5 ? ", ..." : ""}), exceeds ` +
          `${CONFIG.MAX_HOLE_DAYS_IN_WINDOW} for a continuous layer (CONFIG.MAX_HOLE_DAYS_IN_WINDOW).`
      );
    }
  }

  // --- observation planes ---------------------------------------------------------------------
  const [planeCounts, landingCensus] = await Promise.all([readObservationPlaneCounts(), readLandingCensus()]);
  report.observationPlanes = planeCounts;
  report.landingCensus = landingCensus.map((row) => ({
    sourceKey: row.source_key,
    releaseCount: Number(row.release_count),
    signalObservationReleases: Number(row.signal_observation_releases),
    forecastObservationReleases: Number(row.forecast_observation_releases),
    normalizedFeatureReleases: Number(row.normalized_feature_releases),
    zeroLandingReleases: Number(row.zero_landing_releases),
  }));
  for (const row of report.landingCensus) {
    if (row.zeroLandingReleases > CONFIG.MAX_ZERO_LANDING_RELEASES_PER_SOURCE) {
      addViolation(
        violations,
        "FAIL",
        "zero-landing-releases",
        `${row.sourceKey}: ${row.zeroLandingReleases} release(s) landed in NONE of the three ` +
          `observation planes, exceeds ${CONFIG.MAX_ZERO_LANDING_RELEASES_PER_SOURCE} ` +
          "(CONFIG.MAX_ZERO_LANDING_RELEASES_PER_SOURCE)."
      );
    } else if (row.zeroLandingReleases > 0) {
      addViolation(
        violations,
        "WARN",
        "zero-landing-releases",
        `${row.sourceKey}: ${row.zeroLandingReleases} release(s) landed in NONE of the three ` +
          "observation planes (within the accepted baseline)."
      );
    }
  }

  // --- matview ledger --------------------------------------------------------------------------
  const matviewRows = await readMatviewLedger();
  report.matviews = matviewRows.map((row) => ({
    viewName: row.view_name,
    refreshedAt: row.refreshed_at,
    lastAttemptAt: row.last_attempt_at,
    outcome: row.outcome,
    consecutiveFailures: row.consecutive_failures,
    rowCount: row.row_count !== null ? Number(row.row_count) : null,
    relisPopulated: row.relispopulated,
    reltuplesEstimate: row.reltuples !== null ? Number(row.reltuples) : null,
    totalBytes: row.total_bytes !== null ? Number(row.total_bytes) : null,
  }));
  for (const view of report.matviews) {
    // skipped_missing is a dormant-by-design state (an unregistered migration means the relation
    // genuinely does not exist yet, e.g. mv_feature_observation_day_axis) -- not a failure.
    if (view.outcome === "skipped_missing") continue;
    if (view.consecutiveFailures >= CONFIG.MAX_MATVIEW_CONSECUTIVE_FAILURES) {
      addViolation(
        violations,
        "FAIL",
        "matview-failing",
        `${view.viewName}: ${view.consecutiveFailures} consecutive failed refresh(es), meets or ` +
          `exceeds ${CONFIG.MAX_MATVIEW_CONSECUTIVE_FAILURES} (CONFIG.` +
          `MAX_MATVIEW_CONSECUTIVE_FAILURES). Last outcome: ${view.outcome ?? "unknown"}.`
      );
    } else if (view.relisPopulated === false) {
      addViolation(
        violations,
        "WARN",
        "matview-never-populated",
        `${view.viewName}: relispopulated=false -- has never once completed a refresh.`
      );
    }
  }

  // --- tile-serving reachability -----------------------------------------------------------
  const [tileFunctionLayerMap, martinPublishedFunctions, clientSourceIds] = await Promise.all([
    readTileFunctionLayerMap(),
    readMartinPublishedFunctions(),
    readClientDynamicSourceIds(),
  ]);

  const functionByLayerName = new Map();
  for (const [functionName, layerName] of tileFunctionLayerMap) {
    if (layerName) functionByLayerName.set(layerName, functionName);
  }

  let layersWithDataButNoTilePath = 0;
  report.tileServing = layers.map((layer) => {
    const functionName = functionByLayerName.get(layer.name) ?? null;
    const publishedInMartin = functionName ? martinPublishedFunctions.has(functionName) : false;
    const clientWired = functionName ? clientSourceIds.has(functionName) : false;
    const fullyReachable = Boolean(functionName) && publishedInMartin && clientWired;

    const publishedCount = statsByName.get(layer.name)
      ? Number(statsByName.get(layer.name).published_count)
      : 0;
    if (publishedCount > 0 && !fullyReachable) {
      layersWithDataButNoTilePath += 1;
      addViolation(
        violations,
        "FAIL",
        "layer-unreachable",
        `${layer.name}: has ${formatCount(publishedCount)} published row(s) but no complete tile ` +
          `path (function found: ${Boolean(functionName)}, published in martin.yaml: ` +
          `${publishedInMartin}, client source id: ${clientWired}). This layer has data no map ` +
          "view can ever render."
      );
    }

    return { layer: layer.name, functionName, publishedInMartin, clientWired, fullyReachable, publishedCount };
  });

  // Functions that exist and are published but map to no geo.layers row at all (building_tiles,
  // strategy_recommendations_tiles per audit:serving-matrix) -- informational, never gated.
  report.orphanTileFunctions = [...tileFunctionLayerMap.entries()]
    .filter(([, layerName]) => layerName === null)
    .map(([functionName]) => ({
      functionName,
      publishedInMartin: martinPublishedFunctions.has(functionName),
      clientWired: clientSourceIds.has(functionName),
    }));

  if (layersWithDataButNoTilePath > CONFIG.MAX_LAYERS_WITH_DATA_BUT_NO_TILE_PATH) {
    // Individual per-layer FAILs above already cover this; this is the aggregate confirmation the
    // threshold names explicitly, kept separate so the config's own number is traceable to one
    // violation rather than inferred from counting the per-layer ones.
    addViolation(
      violations,
      "FAIL",
      "layers-with-data-but-no-tile-path",
      `${layersWithDataButNoTilePath} layer(s) have published data with no complete tile path, ` +
        `exceeds ${CONFIG.MAX_LAYERS_WITH_DATA_BUT_NO_TILE_PATH} (CONFIG.` +
        "MAX_LAYERS_WITH_DATA_BUT_NO_TILE_PATH)."
    );
  }

  report.violations = violations;
  report.failed = violations.some((violation) => violation.severity === "FAIL");
  return report;
}

// -------------------------------------------------------------------------------------------
// Rendering -- human-readable tables by default, --json for machine output.
// -------------------------------------------------------------------------------------------

function padRow(cells, widths) {
  return cells.map((cell, index) => String(cell).padEnd(widths[index])).join("  ");
}

function renderHuman(report) {
  console.log(`data-quality-report -- generated ${report.generatedAt}`);
  console.log(`coverage window: ${report.coverageWindowDays} day(s)\n`);

  console.log("LAYERS");
  const layerWidths = [22, 12, 9, 20, 20, 18, 10];
  console.log(
    padRow(
      ["name", "published", "geom NULL", "newest created_at", "newest observed_day", "last activity", "stale?"],
      layerWidths
    )
  );
  for (const layer of report.layers) {
    const nullGeomText = layer.geometrySample
      ? `${layer.geometrySample.nullGeomCount}/${layer.geometrySample.sampledRows}`
      : "n/a";
    const staleText =
      layer.lastActivityDaysAgo === null
        ? "n/a"
        : layer.stalenessThresholdDays === null
          ? "exempt"
          : layer.lastActivityDaysAgo > layer.stalenessThresholdDays
            ? "YES"
            : "no";
    console.log(
      padRow(
        [
          layer.name,
          formatCount(layer.publishedCount),
          nullGeomText,
          layer.newestCreatedAt ? new Date(layer.newestCreatedAt).toISOString().slice(0, 16) : "n/a",
          layer.newestObservedDay ?? "n/a",
          layer.lastActivityDaysAgo === null ? "n/a" : `${layer.lastActivityDaysAgo.toFixed(1)}d ago`,
          staleText,
        ],
        layerWidths
      )
    );
  }
  console.log(
    `\n(published counts and timestamps: geo.mv_layer_feature_stats snapshot, refreshed ` +
      `${report.snapshotFreshness["geo.mv_layer_feature_stats"]?.refreshedAt ?? "unknown"}; ` +
      `NULL geom fractions: TABLESAMPLE SYSTEM(${report.geometrySampleMeta.sampleFractionPct}%), ` +
      `${report.geometrySampleMeta.elapsedSeconds}s, estimates not exact counts.)\n`
  );

  console.log("COVERAGE (holes in the trailing window)");
  for (const row of report.coverage) {
    if (!row.available) {
      console.log(`  ${row.layer.padEnd(22)} unavailable (${row.reason})`);
      continue;
    }
    const holeText = row.holeDays.length === 0 ? "none" : row.holeDays.join(", ");
    console.log(
      `  ${row.layer.padEnd(22)} ${String(row.daysWithRows).padStart(3)}/${row.windowDays} day(s) with rows` +
        `  holes: ${holeText}`
    );
  }
  console.log("");

  console.log("OBSERVATION PLANES (checking only one produces false dead-lane reports -- see all three)");
  for (const [plane, info] of Object.entries(report.observationPlanes)) {
    console.log(`  ${plane.padEnd(28)} ${formatCount(info.value).padStart(14)}  (${info.provenance})`);
  }
  console.log("\n  landing census, per source (releases landing in NONE of the three planes):");
  for (const row of report.landingCensus) {
    console.log(
      `    ${row.sourceKey.padEnd(28)} ${String(row.releaseCount).padStart(5)} release(s), ` +
        `${row.zeroLandingReleases} zero-landing`
    );
  }
  console.log("");

  console.log("MATVIEW REFRESH LEDGER");
  for (const view of report.matviews) {
    console.log(
      `  ${view.viewName.padEnd(38)} outcome=${(view.outcome ?? "unknown").padEnd(16)} ` +
        `consecutive_failures=${view.consecutiveFailures ?? 0}  populated=${view.relisPopulated}`
    );
  }
  console.log("");

  console.log("TILE-SERVING REACHABILITY");
  const tileWidths = [22, 26, 10, 10, 12];
  console.log(padRow(["layer", "function", "in martin.yaml", "client wired", "reachable"], tileWidths));
  for (const row of report.tileServing) {
    console.log(
      padRow(
        [row.layer, row.functionName ?? "(none)", row.publishedInMartin, row.clientWired, row.fullyReachable],
        tileWidths
      )
    );
  }
  if (report.orphanTileFunctions.length > 0) {
    console.log("\n  orphan tile functions (no geo.layers row matches -- informational, not gated):");
    for (const orphan of report.orphanTileFunctions) {
      console.log(`    ${orphan.functionName} (published=${orphan.publishedInMartin} clientWired=${orphan.clientWired})`);
    }
  }
  console.log("");

  console.log("VIOLATIONS");
  if (report.violations.length === 0) {
    console.log("  none");
  } else {
    for (const violation of report.violations) {
      console.log(`  [${violation.severity}] ${violation.code}: ${violation.message}`);
    }
  }
  console.log(`\n${report.failed ? "FAILED" : "PASSED"} (${report.violations.filter((v) => v.severity === "FAIL").length} FAIL, ${report.violations.filter((v) => v.severity === "WARN").length} WARN)`);
}

// -------------------------------------------------------------------------------------------

try {
  // Session-level, set once. See CONFIG.STATEMENT_TIMEOUT_MS -- this covers every statement this
  // script issues from here on, which is what satisfies "SET statement_timeout on every statement"
  // without repeating the SET before every single query below.
  await client.unsafe(`SET statement_timeout = ${CONFIG.STATEMENT_TIMEOUT_MS}`);

  const report = await buildReport();

  if (jsonOutput) {
    console.log(JSON.stringify(report, null, 2));
  } else {
    renderHuman(report);
  }

  if (report.failed) process.exitCode = 1;
} catch (error) {
  console.error("\ndata-quality-report: failed");
  console.error(error);
  process.exitCode = 1;
} finally {
  await client.end({ timeout: 30 }).catch(() => {});
}
