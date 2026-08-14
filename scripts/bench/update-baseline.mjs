#!/usr/bin/env node
// Refreshes scripts/bench/baseline.json from a fresh `vitest bench` run. Run this by hand,
// deliberately, after a real and reviewed performance change -- never automatically, and never
// as a CI step; a baseline that regenerates itself can never catch a regression.

import { writeFileSync } from "node:fs";
import { baselinePath, flattenBenchmarkReport, runBenchmarks } from "./bench-runner.mjs";

const report = runBenchmarks();
const benchmarks = flattenBenchmarkReport(report);

writeFileSync(
  baselinePath,
  JSON.stringify(
    {
      generatedAt: new Date().toISOString(),
      nodeVersion: process.version,
      platform: process.platform,
      benchmarks,
    },
    null,
    2
  ) + "\n"
);

console.log(`Wrote ${Object.keys(benchmarks).length} benchmark baselines to ${baselinePath}`);
