#!/usr/bin/env node
// Perf regression detector: runs `vitest bench`, compares every benchmark's mean sample time
// against the committed scripts/bench/baseline.json, and exits 1 if any benchmark got more
// than 30% slower. Ad-hoc, run by hand -- not a CI step and not a build gate (repo policy).

import { readFileSync } from "node:fs";
import { baselinePath, flattenBenchmarkReport, runBenchmarks } from "./bench-runner.mjs";

const REGRESSION_THRESHOLD = 0.3;

function loadBaseline() {
  try {
    const raw = JSON.parse(readFileSync(baselinePath, "utf8"));
    return raw.benchmarks ?? {};
  } catch {
    console.error(
      `No baseline found at ${baselinePath}.\nRun: node scripts/bench/update-baseline.mjs`
    );
    process.exit(1);
  }
}

/**
 * One row per benchmark key seen in either the baseline or the current run, compared on p75
 * sample time -- see the doc comment on `flattenBenchmarkReport` for why not the raw mean.
 */
function compare(baseline, current) {
  const rows = [];
  let regressed = false;

  for (const key of new Set([...Object.keys(baseline), ...Object.keys(current)])) {
    const base = baseline[key];
    const now = current[key];

    if (base === undefined) {
      rows.push({
        benchmark: key,
        baselineP75Ms: "—",
        currentP75Ms: now.p75.toFixed(4),
        change: "—",
        status: "NEW",
      });
      continue;
    }
    if (now === undefined) {
      // A benchmark that stopped running is a failure, not information: it is exactly how a
      // silent regression escapes (deleted/renamed bench, crash before registration).
      regressed = true;
      rows.push({
        benchmark: key,
        baselineP75Ms: base.p75.toFixed(4),
        currentP75Ms: "—",
        change: "—",
        status: "MISSING",
      });
      continue;
    }
    if (!Number.isFinite(now.p75)) {
      regressed = true;
      rows.push({
        benchmark: key,
        baselineP75Ms: base.p75.toFixed(4),
        currentP75Ms: String(now.p75),
        change: "—",
        status: "BROKEN",
      });
      continue;
    }

    const change = (now.p75 - base.p75) / base.p75;
    const isRegression = change > REGRESSION_THRESHOLD;
    if (isRegression) regressed = true;
    rows.push({
      benchmark: key,
      baselineP75Ms: base.p75.toFixed(4),
      currentP75Ms: now.p75.toFixed(4),
      change: `${(change * 100).toFixed(1)}%`,
      status: isRegression ? "REGRESSION" : "ok",
    });
  }
  return { rows, regressed };
}

const current = flattenBenchmarkReport(runBenchmarks());
const baseline = loadBaseline();
const { rows, regressed } = compare(baseline, current);

console.table(rows);

if (regressed) {
  console.error(
    `\nOne or more benchmarks regressed: more than ${(REGRESSION_THRESHOLD * 100).toFixed(0)}% ` +
      "slower than scripts/bench/baseline.json, missing from the current run, or broken (non-finite p75)."
  );
  process.exit(1);
}

console.log("\nNo benchmark regressed by more than 30% against the committed baseline.");
