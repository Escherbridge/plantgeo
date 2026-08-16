// @vitest-environment node

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * The 24-entry slider capability catalogue: the denominator the pre-aggregation-layer design
 * (2026-08-15) is built against. 11 `geo.layers.name` rows (persisted geo.features surfaces)
 * plus 13 hand-spelled stream names (4 `SLIDER_STREAM_LAYER_NAMES` + 9
 * `CLIMATE_FIELD_SIGNAL_IDS.map(climateFieldStreamName)`) -- see docs/layer-lane-standard.md
 * §9.1 and src/__tests__/services/environmental-read-model.test.ts's registry cross-check,
 * which asserts LAYER_REGISTRY resolves against this same 24-name shape.
 *
 * This file checks the OTHER two directions the 24 names must agree with: the actual
 * `geo.layers` seed migrations (not a fixture -- the real INSERT statements), and the census
 * matviews' own DDL in drizzle/0029_pre_aggregation_layer.sql (not a fixture either -- the
 * literal `surface_name` values that migration's SELECT can emit). Three independent
 * hand-spellings of the same 24 names, each checked against a different source of truth, so a
 * drift in any ONE of {registry, seed migrations, census DDL} fails a test even if the other
 * two still agree with each other.
 */

/** Strips `--` line comments so a comment cannot satisfy a literal-text assertion. */
function withoutSqlComments(source: string): string {
  return source
    .split("\n")
    .map((line) => (line.trimStart().startsWith("--") ? "" : line))
    .join("\n");
}

/**
 * 11 names, hand-spelled independently of the registry cross-check in
 * environmental-read-model.test.ts. Verified below against the actual `geo.layers` seed
 * migrations rather than a mocked fixture.
 */
const FEATURE_LAYER_NAMES = [
  "burn-severity",
  "evacuation-zones",
  "fire-detections",
  "fire-perimeters",
  "interventions",
  "sensors",
  "soil-survey",
  "vegetation",
  "water-gauges",
  "watersheds",
  "weather-observations",
];

/**
 * 13 names: the 4 `SLIDER_STREAM_LAYER_NAMES` entries plus the 9 climate-field streams. Never
 * imported from src/types/time-slider.ts or src/lib/environmental/climate-field.ts -- the
 * whole point of this file is to catch one of those constants drifting away from what
 * drizzle/0029's census matviews actually emit, and importing them here would let both sides
 * drift together and still pass.
 */
const STREAM_LAYER_NAMES = [
  "climate-field-air-temperature",
  "climate-field-dew-point",
  "climate-field-precipitation",
  "climate-field-relative-humidity",
  "climate-field-shortwave-radiation",
  "climate-field-soil-wetness-profile",
  "climate-field-soil-wetness-root-zone",
  "climate-field-soil-wetness-surface",
  "climate-field-wind-speed",
  "drought-areas",
  "soil-field-moisture",
  "soil-field-temperature",
  "soil-field-vpd",
];

const ALL_CATALOGUE_SURFACE_NAMES = [...FEATURE_LAYER_NAMES, ...STREAM_LAYER_NAMES].sort();

/**
 * The `geo.layers` seed migrations, in the order they landed. Read directly rather than
 * mocked: these files already exist and are the actual fixture data the feature-side scan
 * (`SELECT name FROM geo.layers`) reads against in production.
 */
const GEO_LAYERS_SEED_MIGRATIONS = [
  "drizzle/0001_handy_riptide.sql",
  "drizzle/0011_burn_severity_layer.sql",
  "drizzle/0013_soil_survey_persistence.sql",
  "drizzle/0017_watershed_persistence.sql",
];

/** Every name inserted into geo.layers across the seed migrations above. */
function seededGeoLayersNames(): string[] {
  const names: string[] = [];
  for (const path of GEO_LAYERS_SEED_MIGRATIONS) {
    const source = withoutSqlComments(readFileSync(path, "utf8"));
    const marker = "INSERT INTO geo.layers (name, type, description, is_public)";
    let cursor = 0;
    for (;;) {
      const start = source.indexOf(marker, cursor);
      if (start === -1) break;
      const end = source.indexOf("ON CONFLICT (name) DO NOTHING;", start);
      expect(end).toBeGreaterThan(start);
      const block = source.slice(start, end);
      for (const match of block.matchAll(/\(\s*'([a-z][a-z0-9-]*)'/g)) names.push(match[1]);
      cursor = end;
    }
  }
  return names;
}

const CENSUS_MIGRATION_PATH = "drizzle/0029_pre_aggregation_layer.sql";

/**
 * The text of one `CREATE MATERIALIZED VIEW [IF NOT EXISTS] <name> ...` statement, comments
 * stripped. `IF NOT EXISTS` is optional in the match because 0029 writes every one of its nine
 * matviews with it (a re-run of the migration must be a no-op, not a 42P07), while 0027/0028
 * do not.
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
 * Kebab-case quoted literals: the shape every `geo.layers.name` and every stream name in this
 * codebase takes (see FEATURE_LAYER_NAMES/STREAM_LAYER_NAMES above). Deliberately requires at
 * least one hyphen, which is what keeps this from also matching the snake_case
 * `agri.signal_observation.signal_name` literals `geo.mv_signal_cell_daily` carries (those are
 * signal-census-contract.test.ts's subject) or single-word literals like the census contract's
 * own `surface_kind` values ('feature' | 'signal' | 'polygon').
 */
const KEBAB_LITERAL = /'([a-z]+(?:-[a-z]+)+)'/g;

/**
 * The SECOND quoted literal on a `VALUES` row, which in every mapping table in this migration is
 * the slider stream name -- `('air-temperature'::text, 'climate-field-air-temperature'::text)`.
 * A whole-block literal scan cannot be used for those tables because their FIRST column is
 * kebab-shaped too and is a source-side signal/measure name, not a catalogue name.
 */
const STREAM_VALUES_ROW = /^\s*\('[a-z-]+'(?:::text)?,\s*'([a-z-]+)'/gm;

describe("the 24-entry slider capability catalogue (pre-aggregation-layer denominator)", () => {
  it("is exactly 24 distinct names: 11 geo.layers rows plus the 13 hand-spelled streams", () => {
    expect(ALL_CATALOGUE_SURFACE_NAMES.length).toBe(24);
    expect(new Set(ALL_CATALOGUE_SURFACE_NAMES).size).toBe(24);
  });

  it("matches the names actually seeded into geo.layers, not a guess at them", () => {
    expect([...seededGeoLayersNames()].sort()).toEqual([...FEATURE_LAYER_NAMES].sort());
  });

  it("geo.mv_signal_observation_day emits exactly the 12 non-drought stream names", () => {
    const source = readFileSync(CENSUS_MIGRATION_PATH, "utf8");
    const block = materializedViewBlock(source, "geo.mv_signal_observation_day");
    // The SECOND literal of each VALUES row, not every kebab literal in the block. Both of that
    // view's mapping tables are `(measure|signal, layer_name)` pairs, and the FIRST column is
    // itself kebab -- 'air-temperature', 'dew-point', 'wind-speed' and friends are the
    // `geo.climate_field_observation.signal` values the layer names are derived FROM, not stream
    // names, and counting them here reported nine phantom 13th names.
    const emitted = new Set([...block.matchAll(STREAM_VALUES_ROW)].map((match) => match[1]));
    const expected = STREAM_LAYER_NAMES.filter((name) => name !== "drought-areas");

    const missing = expected.filter((name) => !emitted.has(name));
    expect(missing).toEqual([]);
    // No more than expected either: a name emitted here that is not one of the 12 is either a
    // typo (never reaches the catalogue) or a 13th name nothing in FEATURE_LAYER_NAMES /
    // STREAM_LAYER_NAMES accounts for (the exact silent-drop shape this design exists to end).
    expect([...emitted].sort()).toEqual([...expected].sort());
  });

  it("geo.mv_drought_observation_day emits exactly 'drought-areas'", () => {
    const source = readFileSync(CENSUS_MIGRATION_PATH, "utf8");
    const block = materializedViewBlock(source, "geo.mv_drought_observation_day");
    const emitted = new Set([...block.matchAll(KEBAB_LITERAL)].map((match) => match[1]));
    expect([...emitted]).toEqual(["drought-areas"]);
  });
});
