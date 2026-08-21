// Re-creates every OID-bound matview (and anything layered on top of one) after the
// `geo.features` partition swap, so none of them keeps silently reading the orphaned legacy heap.
//
// OPS-ONLY, same as `scripts/partition-features.mjs`. Never run by `preDeployCommand`, `npm run
// db:migrate`, or CI. Run by hand, immediately after `partition-features.mjs --phase=swap`, per
// that phase's own printed step 2 ("Re-create every dependent relation listed above from its
// migration").
//
// THE TRAP, runbook section 0.11. A materialized view's (or a plain view's) defining query is
// stored as a `pg_rewrite` rule that references its source relations BY OID, not by name.
// `ALTER TABLE ... RENAME` -- which is exactly what `--phase=swap` does twice, first
// `geo.features -> geo.features_legacy`, then `geo.features_new -> geo.features` -- rewrites
// nothing. Every relation whose rule was built against the pre-swap `geo.features` OID keeps
// resolving that OID under its new name, `geo.features_legacy`, forever. It does not error. It
// answers, correctly-looking, from data that stopped moving the instant the swap committed.
//
// WHY THIS CANNOT BE A `DROP ... IF EXISTS` / re-run-the-migration script. Two independent
// reasons:
//   1. THE LIST IS NOT FIXED. `partition-features.mjs --phase=plan` found SIX live dependents on
//      2026-08-20 (`mv_feature_observation_day`, `mv_layer_feature_stats`,
//      `mv_layer_hourly_activity`, `mv_soil_survey_grid`, `mv_soil_survey_union`,
//      `watershed_rollup`). A SEVENTH, `mv_feature_observation_day_axis`, exists only once
//      `drizzle/0031` is registered -- so the count is a function of migration-registration
//      order at the moment this script runs, not a constant. This script therefore enumerates
//      dependents LIVE, from `pg_depend`/`pg_rewrite`, exactly the way `partition-features.mjs`'s
//      own `readDependentRelations` does, and never hardcodes a name list.
//   2. SOME OF WHAT CASCADES OUT OF A DROP IS NOT ONE OF THE SIX AND OWNS NO MIGRATION OF ITS
//      OWN TO RE-RUN. `geo.v_observation_day_census` (a plain view, `drizzle/0032`) selects FROM
//      `geo.mv_feature_observation_day` -- so `DROP MATERIALIZED VIEW geo.mv_feature_observation_day
//      CASCADE` takes the census view down with it. If this script only knew the six/seven
//      matview names, it would leave the census view dropped and every slider capability read
//      would 500 with "relation does not exist" -- a worse outage than the one it fixed. So this
//      script walks the FULL transitive closure of what depends on the legacy heap through a
//      stored rule, at any depth, and restores every layer of it: matviews (with their indexes
//      and comments) AND the plain views stacked on top of them.
//
// HOW A RELATION IS REPAIRED, PER NODE. `pg_get_viewdef(oid)` deparses the CURRENT defining query
// using each referenced relation's CURRENT name -- which, for anything still pointing at the old
// heap, prints `geo.features_legacy` literally. `SET search_path = pg_catalog` is set for the
// whole session before any capture, purely so that deparse always fully schema-qualifies (a bare
// `search_path` that happened to include `geo` would print unqualified `features_legacy`, and a
// naive text replace could then also clobber an unrelated identifier that happens to share the
// word `features`). The captured text is then walked with a WORD-BOUNDARY regex replacing
// `geo.features_legacy` with `geo.features` -- nothing else. If the corrected text still contains
// `features_legacy` afterward (a second alias, a subquery, anything this script did not
// anticipate) it refuses to create that relation rather than build a plausible-looking view that
// is still wrong; see `assertNoResidualLegacyReference`.
//
// WHY `WITH NO DATA` AND A SEPARATE, GATED REFRESH. Re-creating is cheap and instantaneous;
// POPULATING is the expensive, memory-hungry part, and it is exactly the class of operation this
// production box's 2 GB cap has already failed at more than once (`mv_signal_observation_day` has
// never once completed inside its 300 s window; `mv_soil_survey_union` has never once produced a
// row, ever, under the OLD unpartitioned heap). Structure first, population second, so an
// operator watching this run sees the OID trap closed (verifiable immediately, before any
// multi-minute refresh) even if every refresh after it has to wait for a maintenance window.
//
// WHY THE OBVIOUS SMOKE TEST DOESN'T WORK, per the runbook. Refreshing `mv_layer_feature_stats`
// and diffing its 11 layer counts against `geo.features` sounds like proof -- but if the matview
// was never re-created, that diff compares the legacy heap against ITSELF and matches trivially.
// `--phase=verify` in this script checks the thing that actually distinguishes fixed from
// trapped: whether ANY relation's stored rule still resolves to `geo.features_legacy`'s OID.
//
// Usage:
//   node scripts/recreate-features-matviews.mjs --phase=plan                 # read-only
//   node scripts/recreate-features-matviews.mjs --phase=recreate             # structure + comments
//   node scripts/recreate-features-matviews.mjs --phase=recreate --force     # + the expensive refreshes
//   node scripts/recreate-features-matviews.mjs --phase=recreate --matview=watershed_rollup
//   node scripts/recreate-features-matviews.mjs --phase=verify
//   ... any phase with --dry-run to print the plan and touch nothing.
import postgres from "postgres";

const PHASES = ["plan", "recreate", "verify"];

const args = process.argv.slice(2);
const flags = new Set(args.filter((arg) => !arg.includes("=")));
const options = new Map(
  args
    .filter((arg) => arg.includes("="))
    .map((arg) => [arg.slice(0, arg.indexOf("=")), arg.slice(arg.indexOf("=") + 1)])
);

const dryRun = flags.has("--dry-run");
const force = flags.has("--force");
const matviewOption = options.get("--matview") ?? null;
const phase = options.get("--phase") ?? null;

if (phase === null || !PHASES.includes(phase)) {
  console.error(`recreate-features-matviews: pass --phase=<${PHASES.join("|")}>`);
  process.exit(1);
}

// Identical precedence to scripts/partition-features.mjs, so the two scripts are always pointed
// at the same database without an operator having to remember two different env var names.
const connectionString =
  process.env.PARTITION_DATABASE_URL ||
  process.env.MIGRATION_DATABASE_URL ||
  process.env.DATABASE_URL;
if (!connectionString) {
  console.error(
    "recreate-features-matviews: set PARTITION_DATABASE_URL (or MIGRATION_DATABASE_URL / DATABASE_URL)"
  );
  process.exit(1);
}

const SOURCE_TABLE = "geo.features";
const LEGACY_TABLE = "geo.features_legacy";
const LEGACY_REFERENCE = /\bgeo\.features_legacy\b/g;

/** How many hops of "what depends on what depends on the legacy heap" we will walk before giving
 * up and refusing to proceed. Two is enough for everything seen in production today (a matview,
 * then a plain view over it); six is a generous ceiling against a future design nobody has
 * written yet, not a number anyone expects to be reached. */
const MAX_DEPENDENCY_DEPTH = 6;

/**
 * Measured full-REFRESH cost, most recent measurement, from the runbook's probe:storage-profile
 * and probe:partitionwise sections plus drizzle/0032's own header. These are PRE-SWAP numbers --
 * printed as the sanity baseline an operator can compare a post-swap refresh against, not as a
 * promise about how long the post-swap refresh will take. Partitioning may make some of these
 * faster; it will not make `mv_soil_survey_union` faster, because that view has never once
 * completed regardless of `geo.features`'s layout (see EXPECTED_EMPTY below).
 */
const KNOWN_REFRESH_COST_MS = {
  mv_feature_observation_day: { ms: 428_066, note: "measured 2026-08-21, healthy" },
  mv_layer_feature_stats: { ms: 41_865, note: "measured 2026-08-21, healthy" },
  mv_layer_hourly_activity: { ms: 5_069, note: "measured 2026-08-21, healthy" },
  mv_soil_survey_grid: {
    ms: 300_555,
    note: "standing failure pre-swap: 4 consecutive timeouts, watermark frozen 2026-08-10",
  },
  mv_soil_survey_union: {
    ms: 266_554,
    note: "never once populated, any layout -- see EXPECTED_EMPTY",
  },
  watershed_rollup: { ms: 230_207, note: "measured 2026-08-16, healthy but watermark-gated" },
  mv_feature_observation_day_axis: {
    ms: 286_800,
    note: "measured 2026-08-17 per drizzle/0032's header; only exists once 0031 is registered",
  },
};

/** A refresh at or above this cost needs an operator to say so explicitly. Below it, --phase=recreate
 * populates the matview on its own so the relation is not left silently unreadable. */
const EXPENSIVE_THRESHOLD_MS = 60_000;

/**
 * `mv_soil_survey_union` re-creating with zero rows is the ONE outcome this script must not treat
 * as a failure. `readAggregatedFeatures` was never repointed at it (`drizzle/0029` section 3e says
 * so directly), and it has never once successfully refreshed under the pre-swap heap either --
 * `relispopulated=false` in every measurement on record. A refresh failure here is expected,
 * pre-existing, and unrelated to whether the swap went well; treating it as fatal would tempt an
 * operator to roll back a correct partitioning swap over a defect that predates it.
 */
const EXPECTED_EMPTY = new Set(["mv_soil_survey_union"]);

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

function formatCount(value) {
  return Number(value).toLocaleString("en-US");
}

function formatMs(ms) {
  return ms >= 60_000 ? `~${(ms / 60_000).toFixed(1)}min` : `~${(ms / 1000).toFixed(1)}s`;
}

async function execute(sqlText, { label = null, params = [] } = {}) {
  if (dryRun) {
    console.log(`  (dry run) ${sqlText.replace(/\s+/g, " ").trim()}`);
    return null;
  }
  const startedAt = process.hrtime.bigint();
  const result = await client.unsafe(sqlText, params);
  if (label) console.log(`  ${label} in ${elapsed(startedAt)}`);
  return result;
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

async function swapIsDone() {
  return isPartitioned(SOURCE_TABLE);
}

/**
 * One hop of "what has a stored rule referencing this relation's OID" -- byte-for-byte the same
 * shape as `partition-features.mjs`'s `readDependentRelations`, generalised to take either a
 * relation name (for the first hop, off the legacy heap) or a qualified name discovered on a
 * previous hop.
 */
async function readDirectDependents(qualifiedName) {
  return client.unsafe(
    `SELECT DISTINCT n.nspname AS schema,
            dependent.relname AS name,
            dependent.oid AS oid,
            dependent.relkind AS relkind
       FROM pg_depend AS d
       JOIN pg_rewrite AS r ON r.oid = d.objid
       JOIN pg_class AS dependent ON dependent.oid = r.ev_class
       JOIN pg_namespace AS n ON n.oid = dependent.relnamespace
      WHERE d.classid = 'pg_rewrite'::regclass
        AND d.refclassid = 'pg_class'::regclass
        AND d.refobjid = to_regclass($1)
        AND dependent.oid <> to_regclass($1)
      ORDER BY 1, 2`,
    [qualifiedName]
  );
}

/**
 * The full transitive closure of "depends, through a stored rule, on something that depends on
 * `geo.features_legacy`" -- at any depth, discovered live rather than assumed to be exactly the
 * six or seven matviews the runbook has measured so far. `parents` records every immediate
 * qualified name that pulled a node in, which is what lets `--phase=recreate --matview=<name>`
 * compute "just this one plus whatever sits on top of it" without guessing at edges.
 */
async function buildLegacyDependencyGraph() {
  const nodes = new Map();
  let frontier = [LEGACY_TABLE];
  let depth = 0;
  const seenFrontiers = new Set([LEGACY_TABLE]);

  while (frontier.length > 0 && depth < MAX_DEPENDENCY_DEPTH) {
    const nextFrontier = [];
    for (const parentQualified of frontier) {
      const rows = await readDirectDependents(parentQualified);
      for (const row of rows) {
        const qualified = `${row.schema}.${row.name}`;
        if (!nodes.has(qualified)) {
          nodes.set(qualified, {
            schema: row.schema,
            name: row.name,
            oid: row.oid,
            relkind: row.relkind,
            depth,
            parents: new Set(),
          });
        }
        nodes.get(qualified).parents.add(parentQualified === LEGACY_TABLE ? "__legacy__" : parentQualified);
        if (!seenFrontiers.has(qualified)) {
          seenFrontiers.add(qualified);
          nextFrontier.push(qualified);
        }
      }
    }
    frontier = nextFrontier;
    depth += 1;
  }

  if (frontier.length > 0) {
    throw new Error(
      `recreate-features-matviews: dependency graph is still growing after ${MAX_DEPENDENCY_DEPTH} hops ` +
        `(frontier: ${frontier.join(", ")}). Either something unusual is stacked very deep, or there is a ` +
        "cycle pg_depend should not allow. Raise MAX_DEPENDENCY_DEPTH only after confirming which."
    );
  }

  return nodes;
}

/** Every node reachable FORWARD from `rootQualified` (itself included), using the same graph's
 * `parents` edges read in reverse. This is how `--matview=<name>` scopes a run to one matview plus
 * whatever plain view sits on top of it, without touching sibling matviews. */
function forwardClosure(nodes, rootQualified) {
  const children = new Map();
  for (const [qualified, node] of nodes) {
    for (const parent of node.parents) {
      if (parent === "__legacy__") continue;
      if (!children.has(parent)) children.set(parent, []);
      children.get(parent).push(qualified);
    }
  }
  const closure = new Set([rootQualified]);
  const queue = [rootQualified];
  while (queue.length > 0) {
    const current = queue.shift();
    for (const child of children.get(current) ?? []) {
      if (!closure.has(child)) {
        closure.add(child);
        queue.push(child);
      }
    }
  }
  return closure;
}

async function getViewDefinition(oid) {
  const rows = await client.unsafe(`SELECT pg_get_viewdef($1::oid) AS definition`, [oid]);
  return rows[0].definition;
}

async function getComment(oid) {
  const rows = await client.unsafe(`SELECT obj_description($1::oid, 'pg_class') AS comment`, [oid]);
  return rows[0].comment;
}

async function getIndexDefs(schema, name) {
  return client.unsafe(
    `SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = $1 AND tablename = $2 ORDER BY indexname`,
    [schema, name]
  );
}

async function getPopulationState(oid) {
  const rows = await client.unsafe(
    `SELECT relispopulated, reltuples FROM pg_class WHERE oid = $1::oid`,
    [oid]
  );
  return { populated: rows[0].relispopulated === true, reltuples: Number(rows[0].reltuples) };
}

/** Refuses to build a relation whose corrected text still names the legacy heap -- an alias, a
 * subquery, or any shape this script's single word-boundary replace did not anticipate. A relation
 * that "looks recreated" but still reads the frozen heap is strictly worse than one this script
 * loudly declined to touch. */
function assertNoResidualLegacyReference(qualifiedName, correctedText) {
  if (LEGACY_REFERENCE.test(correctedText)) {
    throw new Error(
      `recreate-features-matviews: ${qualifiedName}'s definition still references ${LEGACY_TABLE} ` +
        "after the word-boundary substitution. Refusing to create it blind -- read the captured " +
        "definition by hand and extend the substitution, or fix this relation manually."
    );
  }
  LEGACY_REFERENCE.lastIndex = 0; // stateful /g regex; reset between calls.
}

function correctLegacyReference(definitionText) {
  LEGACY_REFERENCE.lastIndex = 0;
  return definitionText.replace(LEGACY_REFERENCE, "geo.features");
}

/** Captures everything needed to rebuild one node: corrected query text, indexes (matviews only),
 * and its comment. Called for every node BEFORE anything is dropped. */
async function captureNode(node) {
  const qualified = `${node.schema}.${node.name}`;
  const rawDefinition = await getViewDefinition(node.oid);
  const correctedDefinition = correctLegacyReference(rawDefinition);
  const referencedLegacy = rawDefinition !== correctedDefinition;
  assertNoResidualLegacyReference(qualified, correctedDefinition);

  const comment = await getComment(node.oid);
  const population = await getPopulationState(node.oid);
  const indexes = node.relkind === "m" ? await getIndexDefs(node.schema, node.name) : [];

  return { ...node, qualified, referencedLegacy, correctedDefinition, comment, population, indexes };
}

// ---------------------------------------------------------------------------------------------
// PHASE plan -- read-only, safe in any state.
// ---------------------------------------------------------------------------------------------

async function runPlan() {
  const partitioned = await swapIsDone();
  const legacyPresent = await relationExists(LEGACY_TABLE);

  console.log(`geo.features: ${partitioned ? "partitioned (swap done)" : "plain heap (swap not done)"}`);
  console.log(`${LEGACY_TABLE}: ${legacyPresent ? "present" : "absent"}\n`);

  if (!partitioned || !legacyPresent) {
    console.log(
      "Nothing to plan against: the OID trap only exists once geo.features_legacy is the frozen\n" +
        "heap and geo.features is the new partitioned parent. Run partition-features.mjs --phase=swap first."
    );
    return;
  }

  const graph = await buildLegacyDependencyGraph();
  if (graph.size === 0) {
    console.log("No relation's stored rule points at geo.features_legacy. Nothing is trapped.");
    return;
  }

  console.log(`${graph.size} relation(s) reachable from ${LEGACY_TABLE} through a stored rule:\n`);
  const byDepth = [...graph.values()].sort((a, b) => a.depth - b.depth || a.name.localeCompare(b.name));
  for (const node of byDepth) {
    const qualified = `${node.schema}.${node.name}`;
    const raw = await getViewDefinition(node.oid);
    const trapped = LEGACY_REFERENCE.test(raw);
    LEGACY_REFERENCE.lastIndex = 0;
    const population = await getPopulationState(node.oid);
    const kind = node.relkind === "m" ? "matview" : node.relkind === "v" ? "view" : node.relkind;
    const cost = KNOWN_REFRESH_COST_MS[node.name];
    const costNote =
      node.relkind === "m"
        ? cost
          ? `  refresh ${formatMs(cost.ms)} (${cost.note})`
          : "  refresh cost: no prior measurement"
        : "";
    console.log(
      `  depth ${node.depth}  ${kind.padEnd(8)} ${qualified.padEnd(38)} ` +
        `${trapped ? "STILL READS geo.features_legacy" : "definition already clean"}` +
        `  rows=${formatCount(population.reltuples)} populated=${population.populated}${costNote}`
    );
    if (node.parents.size > 0 && !node.parents.has("__legacy__")) {
      console.log(`    depends on: ${[...node.parents].join(", ")}`);
    }
  }
  console.log(
    "\n^ every 'STILL READS geo.features_legacy' row is silently frozen at swap time. Run\n" +
      "--phase=recreate to fix the structure (cheap), then --phase=recreate --force to populate the\n" +
      "expensive ones, then --phase=verify."
  );
}

/**
 * The gated-refresh loop, factored out so a resumed run (structure already fixed, only a refresh
 * still pending) and a fresh run (structure just rebuilt) share one implementation. Mutates
 * nothing outside its return value; the caller decides what to print around it.
 */
async function refreshMatviews(names) {
  const failures = [];
  const expectedEmptyFailures = [];
  for (const name of [...names].sort()) {
    const cost = KNOWN_REFRESH_COST_MS[name];
    const expensive = cost ? cost.ms >= EXPENSIVE_THRESHOLD_MS : true; // unknown cost => treat as expensive
    const costLabel = cost ? `${formatMs(cost.ms)} (${cost.note})` : "no prior measurement";

    if (expensive && !force) {
      console.log(
        `  ${name}: SKIPPED -- expensive refresh (${costLabel}). Pass --force to populate it now, ` +
          "or run REFRESH MATERIALIZED VIEW by hand in a maintenance window."
      );
      continue;
    }

    const timeoutMs = cost ? Math.min(Math.max(cost.ms * 2, 300_000), 1_200_000) : 1_200_000;
    const timeoutSql = `${Math.ceil(timeoutMs / 1000)}s`;
    console.log(`  ${name}: refreshing (budget ${timeoutSql}, expected ${costLabel})`);
    const startedAt = process.hrtime.bigint();
    try {
      await client.begin(async (tx) => {
        await tx.unsafe(`SET LOCAL statement_timeout = '${timeoutSql}'`);
        await tx.unsafe(`REFRESH MATERIALIZED VIEW geo.${name}`);
      });
      console.log(`    populated in ${elapsed(startedAt)}`);
    } catch (error) {
      if (EXPECTED_EMPTY.has(name)) {
        expectedEmptyFailures.push(name);
        console.log(
          `    EXPECTED: refresh failed after ${elapsed(startedAt)} (${error.message.split("\n")[0]}). ` +
            "This matview has never once populated under any layout -- not a regression from the swap."
        );
      } else {
        failures.push({ name, message: error.message.split("\n")[0] });
        console.log(`    FAILED after ${elapsed(startedAt)}: ${error.message.split("\n")[0]}`);
      }
    }
  }
  return { failures, expectedEmptyFailures };
}

// ---------------------------------------------------------------------------------------------
// PHASE recreate -- capture, DROP ... CASCADE, rebuild, restore indexes/comments, gated refresh.
// ---------------------------------------------------------------------------------------------

async function runRecreate() {
  if (!(await swapIsDone())) {
    throw new Error(
      "recreate-features-matviews: geo.features is not partitioned -- the swap has not happened, " +
        "so there is no OID trap to fix yet. Running this now would drop working matviews for nothing."
    );
  }
  if (!(await relationExists(LEGACY_TABLE))) {
    throw new Error(
      `recreate-features-matviews: ${LEGACY_TABLE} does not exist. Either the swap has not run, or ` +
        "it already ran and the legacy heap was already cleaned up -- in which case whatever still " +
        "reads it is unfixable through this script (there is nothing left to point away from)."
    );
  }

  const graph = await buildLegacyDependencyGraph();
  if (graph.size === 0) {
    // RESUMABILITY. A previous run may have fixed the structure (dropped, recreated, restored
    // indexes and comments) and then had its --force refresh skipped, fail, or get interrupted.
    // At that point the OID trap is already closed, so this relation no longer shows up in the
    // legacy-dependency graph above -- but it can still be sitting there `WITH NO DATA`, unusable
    // to any reader. Re-running the whole capture/drop/recreate dance would be pointless (there
    // is nothing left to fix structurally) and destructive (it would drop and rebuild a perfectly
    // good matview just to populate it); this branch instead looks for exactly that "structurally
    // fine, still unpopulated" state among the well-known names and offers only the refresh.
    console.log(
      "No relation's stored rule points at geo.features_legacy. Nothing to DROP and re-create.\n" +
        "Checking for a well-known matview that is structurally fine but still unpopulated " +
        "(e.g. a --force refresh skipped or interrupted on a previous run)..."
    );
    const candidateNames = matviewOption ? [matviewOption] : Object.keys(KNOWN_REFRESH_COST_MS);
    const stillUnpopulated = [];
    for (const name of candidateNames) {
      const qualified = `geo.${name}`;
      if (!(await relationExists(qualified))) continue;
      const [row] = await client.unsafe(
        `SELECT relispopulated FROM pg_class WHERE oid = to_regclass($1)`,
        [qualified]
      );
      if (row && row.relispopulated === false) stillUnpopulated.push(name);
    }
    if (stillUnpopulated.length === 0) {
      console.log("Nothing left unpopulated either. Nothing to do.");
      return;
    }
    console.log(`\n${stillUnpopulated.length} matview(s) unpopulated, structure already correct:`);
    const { failures } = await refreshMatviews(stillUnpopulated);
    console.log("\nphase recreate done (refresh-only resume). Next: --phase=verify.");
    if (failures.length > 0) process.exitCode = 1;
    return;
  }

  let targetQualified;
  if (matviewOption !== null) {
    const root = [...graph.values()].find(
      (node) => node.depth === 0 && node.name === matviewOption
    );
    if (!root) {
      const depth0Names = [...graph.values()].filter((n) => n.depth === 0).map((n) => n.name);
      throw new Error(
        `recreate-features-matviews: no live OID-bound dependent named "${matviewOption}" ` +
          `(currently trapped: ${depth0Names.join(", ") || "none"}).`
      );
    }
    targetQualified = forwardClosure(graph, `${root.schema}.${root.name}`);
  } else {
    targetQualified = new Set(graph.keys());
  }

  const targetNodes = [...targetQualified].map((qualified) => graph.get(qualified));
  const depth0 = targetNodes.filter((node) => node.depth === 0);
  const deeper = targetNodes.filter((node) => node.depth > 0).sort((a, b) => a.depth - b.depth);

  console.log(
    `phase recreate: ${depth0.length} matview(s), ${deeper.length} dependent view(s)` +
      `${matviewOption ? ` (scoped to ${matviewOption} and its dependents)` : ""}\n`
  );

  console.log("- capturing corrected definitions, indexes, and comments before touching anything");
  const captured = new Map();
  for (const node of [...depth0, ...deeper]) {
    const info = await captureNode(node);
    captured.set(info.qualified, info);
    console.log(
      `  ${info.qualified}: ${info.referencedLegacy ? "corrected legacy reference" : "no legacy reference in this text"}, ` +
        `${info.indexes.length} index(es), comment ${info.comment ? "present" : "absent"}`
    );
  }

  // One statement: dropping every targeted depth-0 matview together lets CASCADE remove every
  // dependent view in the SAME transaction as the drop, instead of us guessing an order. The
  // onnotice handler above prints each cascaded drop as it happens, which is the operator's
  // confirmation that CASCADE only removed what this script already captured and will rebuild.
  const dropTargets = depth0.map((node) => `${node.schema}.${node.name}`);
  const dropStatement = `DROP MATERIALIZED VIEW IF EXISTS ${dropTargets.join(", ")} CASCADE`;
  console.log(`\n- dropping ${dropTargets.length} matview(s) (CASCADE):`);
  await execute(dropStatement, { label: "dropped" });

  if (dryRun) {
    console.log(
      "\n(dry run) would recreate:\n" +
        [...captured.keys()].map((name) => `  ${name}`).join("\n") +
        "\nnothing executed."
    );
    return;
  }

  console.log("\n- recreating, matviews first, then views stacked on top:");
  const orderedForCreate = [...captured.values()].sort((a, b) => a.depth - b.depth);
  for (const info of orderedForCreate) {
    if (info.relkind === "m") {
      await execute(
        `CREATE MATERIALIZED VIEW ${info.qualified} AS\n${info.correctedDefinition}\nWITH NO DATA`,
        { label: `${info.qualified} created (WITH NO DATA)` }
      );
      for (const index of info.indexes) {
        await execute(index.indexdef, { label: `  ${index.indexname}` });
      }
    } else {
      await execute(`CREATE VIEW ${info.qualified} AS\n${info.correctedDefinition}`, {
        label: `${info.qualified} created`,
      });
    }
    if (info.comment) {
      const objectKind = info.relkind === "m" ? "MATERIALIZED VIEW" : "VIEW";
      await execute(`COMMENT ON ${objectKind} ${info.qualified} IS $1`, {
        label: `  comment restored`,
        params: [info.comment],
      });
    }
  }

  console.log(
    "\nstructure recreated. No relation in this run still points at geo.features_legacy -- the OID\n" +
      "trap is closed even for anything left unpopulated below.\n"
  );

  console.log("- populating (each matview's first refresh must be non-concurrent; it is unpopulated):");
  const { failures, expectedEmptyFailures } = await refreshMatviews(depth0.map((node) => node.name));

  console.log(
    "\nphase recreate done." +
      (failures.length > 0
        ? `\n${failures.length} refresh(es) FAILED unexpectedly -- see above. Re-run --phase=verify.`
        : "") +
      (expectedEmptyFailures.length > 0
        ? `\n${expectedEmptyFailures.length} refresh(es) failed as EXPECTED (${expectedEmptyFailures.join(", ")}).`
        : "") +
      "\nNext: --phase=verify. If Martin's config was baked before this ran, restart it -- " +
      "watershed_tiles() reads geo.watershed_rollup directly."
  );
  if (failures.length > 0) process.exitCode = 1;
}

// ---------------------------------------------------------------------------------------------
// PHASE verify -- the actual proof, not the trivially-true self-comparison the runbook warns
// about. Passes only when NOTHING, anywhere, still has a stored rule pointing at the legacy heap.
// ---------------------------------------------------------------------------------------------

async function runVerify() {
  const partitioned = await swapIsDone();
  const legacyPresent = await relationExists(LEGACY_TABLE);

  if (!partitioned) {
    console.log("geo.features is not partitioned; there is nothing for this script to have fixed. Skipping.");
    return true;
  }
  if (!legacyPresent) {
    console.log(`${LEGACY_TABLE} is gone; the OID trap cannot exist without it. Nothing to check.`);
    return true;
  }

  console.log(`phase verify: is anything still reading ${LEGACY_TABLE} through a stored rule?\n`);
  const graph = await buildLegacyDependencyGraph();
  const failures = [];
  const warnings = [];

  if (graph.size > 0) {
    for (const node of graph.values()) {
      failures.push(`${node.schema}.${node.name} (depth ${node.depth}) still points at ${LEGACY_TABLE}`);
    }
    console.log(`FAILED: ${graph.size} relation(s) still point at the legacy heap:`);
    for (const failure of failures) console.log(`  - ${failure}`);
  } else {
    console.log("PASS: no relation's stored rule resolves to geo.features_legacy's OID.");
  }

  console.log("\nper-matview population state:");
  for (const name of Object.keys(KNOWN_REFRESH_COST_MS)) {
    const qualified = `geo.${name}`;
    if (!(await relationExists(qualified))) {
      console.log(`  ${name.padEnd(34)} absent (not registered yet -- e.g. drizzle/0031 for the axis)`);
      continue;
    }
    const [row] = await client.unsafe(
      `SELECT relispopulated, reltuples FROM pg_class WHERE oid = to_regclass($1)`,
      [qualified]
    );
    const populated = row.relispopulated === true;
    const expectedEmpty = EXPECTED_EMPTY.has(name);
    let status;
    if (populated) {
      status = "populated";
    } else if (expectedEmpty) {
      status = "unpopulated (EXPECTED -- see EXPECTED_EMPTY)";
    } else {
      status = "unpopulated";
      warnings.push(`${name} is unpopulated -- run --phase=recreate --force, or refresh it by hand`);
    }
    console.log(
      `  ${name.padEnd(34)} rows=${formatCount(Number(row.reltuples)).padStart(12)}  ${status}`
    );
  }

  console.log("\ndependent views (queryability, not just existence):");
  const viewChecks = [...graph.values()]
    .filter((node) => node.relkind === "v")
    .reduce((unique, node) => unique.set(`${node.schema}.${node.name}`, node), new Map());
  // v_observation_day_census exists whether or not anything is currently trapped, and its
  // queryability is exactly the thing a matview left `WITH NO DATA` breaks (PostgreSQL raises
  // 55000 reading an unpopulated matview, not an empty result) -- check it explicitly even when
  // the trap graph above came back empty, so a partial --phase=recreate is caught here too.
  if (await relationExists("geo.v_observation_day_census")) {
    viewChecks.set("geo.v_observation_day_census", { schema: "geo", name: "v_observation_day_census" });
  }
  for (const [qualified] of viewChecks) {
    try {
      await client.begin(async (tx) => {
        await tx.unsafe(`SET LOCAL statement_timeout = '60s'`);
        await tx.unsafe(`SELECT 1 FROM ${qualified} LIMIT 1`);
      });
      console.log(`  ${qualified.padEnd(38)} queryable`);
    } catch (error) {
      failures.push(`${qualified} is not queryable: ${error.message.split("\n")[0]}`);
      console.log(`  ${qualified.padEnd(38)} FAILED: ${error.message.split("\n")[0]}`);
    }
  }

  for (const warning of warnings) console.log(`\nWARNING: ${warning}`);
  if (failures.length > 0) {
    console.log("\nVERIFICATION FAILED.");
    return false;
  }
  console.log("\nverification passed.");
  return true;
}

// ---------------------------------------------------------------------------------------------

const RUNNERS = { plan: runPlan, recreate: runRecreate, verify: runVerify };

try {
  await client.unsafe("SET statement_timeout = 0");
  await client.unsafe("SET idle_in_transaction_session_timeout = '10min'");
  // See the header comment: forces pg_get_viewdef to fully schema-qualify every relation it
  // deparses, which is what makes the geo.features_legacy word-boundary replace safe.
  await client.unsafe("SET search_path = pg_catalog");
  const result = await RUNNERS[phase]();
  if (phase === "verify" && result === false) process.exitCode = 1;
} catch (error) {
  console.error(`\nrecreate-features-matviews: phase ${phase} failed`);
  console.error(error);
  process.exitCode = 1;
} finally {
  await client.end({ timeout: 30 }).catch(() => {});
}
