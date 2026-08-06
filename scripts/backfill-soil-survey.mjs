// Bounded, resumable SSURGO backfill driver. See `backfillSoilSurvey` in
// src/lib/server/services/usda-soil.ts (the coverage ledger is the cursor) and
// src/lib/server/AGENTS.md §soil-survey-persistence for cost-per-cell.
//
// jiti loads the TS service in place (with its "@/..." alias) so this stays a thin
// driver instead of duplicating server logic; jiti itself is an already-installed
// transitive dep (eslint/vitest/tailwind), not a new dependency of this script.
import { createJiti } from "jiti";
import { fileURLToPath } from "node:url";

const maxCells = Number(process.argv[2] ?? 25);
const bbox = process.argv[3] ?? "-125,42,-111,49"; // PNW envelope: 112 x 56 = 6,272 cells

const jiti = createJiti(import.meta.url, {
  alias: { "@": fileURLToPath(new URL("../src", import.meta.url)) },
});
const { backfillSoilSurvey } = await jiti.import(
  "../src/lib/server/services/usda-soil.ts"
);

const report = await backfillSoilSurvey({
  bbox,
  maxCells,
  onCell: (result) => {
    const status = result.error ? `FAILED ${result.error}` : `${result.polygonCount} polygons`;
    console.log(`[soil-survey] ${result.cellKey} ${status} (${result.elapsedMs}ms)`);
  },
});

console.log(
  `[soil-survey] attempted=${report.attempted} failed=${report.failed} ` +
    `alreadyCovered=${report.alreadyCovered} polygonsStored=${report.polygonsStored} ` +
    `unreadableRows=${report.unreadableRows} remaining=${report.remaining} ` +
    `elapsedMs=${report.elapsedMs}`
);

if (report.failed > 0) {
  console.error(`[soil-survey] ${report.failed} cell(s) failed and were left uncovered for retry`);
  process.exitCode = 1;
}
