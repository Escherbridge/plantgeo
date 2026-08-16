// @vitest-environment node

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * `geo.mv_signal_cell_daily` (drizzle/0029_pre_aggregation_layer.sql) is scoped to the 19
 * governed `agri.signal_observation.signal_name` values under contract -- see the
 * pre-aggregation-layer design §3.2, "Scope" -- not all 46.1M rows the table holds. This file
 * asserts the DDL's own governed list equals the 19-name contract, hand-spelled here from
 * `services/agri-data-service/src/agri_data_service/execution/coverage_contract.py`'s
 * `LANE_COVERAGE_CONTRACTS` (three contracts: nasa-power-daily's eight meteorology signals,
 * its three soil-wetness-pilot signals, and open-meteo-era5-land-archive's seven soil-state
 * signals plus vapor_pressure_deficit), verified against `agri.data_source.key` 2026-08-11 per
 * that file's own citation on the era5-land-archive contract.
 *
 * Never import LANE_COVERAGE_CONTRACTS (it is Python, so there is nothing to import across the
 * language boundary anyway, but the principle is the same one STREAM_NAMES in
 * environmental-read-model.test.ts already follows): the whole point of this test is to catch
 * the SQL drifting away from the contract, and deriving the expected list from the contract
 * itself would make that drift invisible.
 */

/** Strips `--` line comments so a comment cannot satisfy a literal-text assertion. */
function withoutSqlComments(source: string): string {
  return source
    .split("\n")
    .map((line) => (line.trimStart().startsWith("--") ? "" : line))
    .join("\n");
}

/**
 * The 19 governed signal names, hand-spelled from coverage_contract.py's LANE_COVERAGE_CONTRACTS
 * as verified against production 2026-08-11. Grouped as the contract file groups them:
 * nasa-power-daily meteorology (8), nasa-power-daily soil-wetness pilot (3), and
 * open-meteo-era5-land-archive soil state plus VPD (8).
 */
const GOVERNED_SIGNAL_NAMES = [
  "air_temperature_mean",
  "air_temperature_max",
  "air_temperature_min",
  "dew_point_temperature",
  "precipitation",
  "relative_humidity",
  "surface_shortwave_radiation",
  "wind_speed",
  "soil_wetness_surface",
  "soil_wetness_root_zone",
  "soil_wetness_profile",
  "soil_water_content_layer_1",
  "soil_water_content_layer_2",
  "soil_water_content_layer_3",
  "soil_temperature_level_1",
  "soil_temperature_level_2",
  "soil_temperature_level_3",
  "soil_temperature_level_4",
  "vapor_pressure_deficit",
];

const MIGRATION_PATH = "drizzle/0029_pre_aggregation_layer.sql";

/**
 * The text of one `CREATE MATERIALIZED VIEW [IF NOT EXISTS] <name> ...` statement, comments
 * stripped. `IF NOT EXISTS` is optional in the match because 0029 writes every one of its nine
 * matviews with it (a re-run of the migration must be a no-op, not a 42P07), while 0027/0028
 * do not -- matching only the bare form silently found nothing and failed on the index probe
 * rather than on the thing under test.
 */
function materializedViewBlock(source: string, qualifiedViewName: string): string {
  const start = [
    `CREATE MATERIALIZED VIEW IF NOT EXISTS ${qualifiedViewName}`,
    `CREATE MATERIALIZED VIEW ${qualifiedViewName}`,
  ]
    .map((marker) => source.indexOf(marker))
    .find((index) => index >= 0) ?? -1;
  expect(start).toBeGreaterThanOrEqual(0);
  const end = source.indexOf("--> statement-breakpoint", start);
  expect(end).toBeGreaterThan(start);
  return withoutSqlComments(source.slice(start, end));
}

/**
 * `fraction_of_saturation` is the one known confounder in this MV's neighbourhood: it is the
 * `normalized_unit` the three soil-wetness signals carry in `geo.climate_field_observation`'s
 * governed list (drizzle/0020_climate_field.sql), and it is snake_case-shaped exactly like a
 * signal name. Excluded explicitly, by name, rather than by accident -- the rollup now carries
 * that unit beside each signal name in its own governed table, so a scan that reached the second
 * column would otherwise miscount it as a 20th governed signal.
 */
const KNOWN_NON_SIGNAL_SNAKE_CASE_LITERALS = ["fraction_of_saturation"];

/**
 * The FIRST quoted literal on a `VALUES` row, which in `mv_signal_cell_daily`'s `governed`
 * mapping table is the `signal_name` column and nothing else.
 *
 * WHY NOT SNAKE_CASE_LITERAL OVER THE WHOLE BLOCK. Two independent reasons, one of which was a
 * live false negative:
 *   - `precipitation` is a governed signal name with NO underscore in it, so the snake_case
 *     regex never matched it and the "no fewer" half of this assertion silently under-counted;
 *   - the governed table now carries `normalized_unit` and a lane key alongside each name (the
 *     contract is the TRIPLE, not the name -- see drizzle/0020's note on why support_key cannot
 *     substitute for source.key), so a whole-block scan would have to exclude a growing list of
 *     units and lane keys by hand.
 * Reading the one column that holds signal names removes both problems.
 */
const GOVERNED_VALUES_ROW = /^\s*\('([a-z][a-z0-9_]*)'/gm;

describe("geo.mv_signal_cell_daily's governed signal-name list", () => {
  it("equals the 19-name coverage_contract.py list, no more and no fewer", () => {
    const source = readFileSync(MIGRATION_PATH, "utf8");
    const block = materializedViewBlock(source, "geo.mv_signal_cell_daily");

    const emitted = new Set(
      [...block.matchAll(GOVERNED_VALUES_ROW)]
        .map((match) => match[1])
        .filter((literal) => !KNOWN_NON_SIGNAL_SNAKE_CASE_LITERALS.includes(literal))
    );

    const missing = GOVERNED_SIGNAL_NAMES.filter((name) => !emitted.has(name));
    expect(missing).toEqual([]);
    expect([...emitted].sort()).toEqual([...GOVERNED_SIGNAL_NAMES].sort());
  });

  it("scopes the view to agri.signal_observation, not a different table entirely", () => {
    // A cheap guard against the block-extraction marker matching something that isn't the
    // rollup this file thinks it is checking.
    const source = readFileSync(MIGRATION_PATH, "utf8");
    const block = materializedViewBlock(source, "geo.mv_signal_cell_daily");
    expect(block).toContain("agri.signal_observation");
  });
});
