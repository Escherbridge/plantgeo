// Shared by compare-baseline.mjs and update-baseline.mjs: runs every `vitest bench` suite once
// and flattens its --outputJson report into a comparable shape. Ad-hoc tooling per repo policy
// -- neither script runs in CI or gates a build; both are run by hand.

import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = fileURLToPath(new URL(".", import.meta.url));

export const repoRoot = join(scriptDir, "..", "..");
export const baselinePath = join(scriptDir, "baseline.json");

/** Runs every `src/__benchmarks__/**\/*.bench.ts` suite once and returns the parsed report. */
export function runBenchmarks() {
  const outputPath = join(mkdtempSync(join(tmpdir(), "plantgeo-bench-")), "current.json");
  const result = spawnSync(
    process.execPath,
    [
      join(repoRoot, "node_modules", "vitest", "vitest.mjs"),
      "bench",
      "--run",
      "--outputJson",
      outputPath,
      "--configLoader",
      "runner",
    ],
    { cwd: repoRoot, stdio: "inherit" }
  );
  if (result.status !== 0) {
    console.error("vitest bench exited non-zero; no report to read.");
    process.exit(result.status ?? 1);
  }
  return JSON.parse(readFileSync(outputPath, "utf8"));
}

/**
 * Flattens a `vitest bench --outputJson` report to `"describe > bench name" -> { mean, hz, p75 }`.
 *
 * `p75` is what regression comparison keys on, not `mean`: these are sub-millisecond, pure
 * functions run in a cold V8 process each time, and an arithmetic mean over thousands of
 * samples is dominated by the rare GC-pause or JIT-deopt sample that lands 100-1000x above the
 * typical one -- exactly what produced the reproducible ~900% and ~4700% "regressions" a p75
 * comparison never showed against the same two runs. p75 (like the tinybench-reported median)
 * describes the typical call and is far less sensitive to that tail.
 */
export function flattenBenchmarkReport(report) {
  const flat = {};
  for (const file of report.files ?? []) {
    for (const group of file.groups ?? []) {
      for (const benchmark of group.benchmarks ?? []) {
        flat[`${group.fullName} > ${benchmark.name}`] = {
          mean: benchmark.mean,
          hz: benchmark.hz,
          p75: benchmark.p75,
        };
      }
    }
  }
  return flat;
}
