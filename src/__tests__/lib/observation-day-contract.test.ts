import { readFileSync, readdirSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

// The rule is a plain constant, but it lives in a module that opens a warehouse handle at import.
vi.mock("@/lib/server/db", () => ({ db: {} }));

import { PUBLISHER_NAMED_DAY_RULE } from "@/lib/server/services/environmental-read-model";

/**
 * The slider axis and the baked tile layers must bucket a feature onto the SAME calendar day.
 *
 * `OBSERVATION_DAY` (src/lib/server/services/environmental-read-model.ts) answers the slider;
 * `geo.feature_observation_day` (defined in drizzle/0015_tile_observation_day.sql, redefined in
 * drizzle/0018_fire_discovery_observation_day.sql) is baked onto five Martin function sources as
 * the `observed_day` attribute the client filters on. If the two ever derive the day differently
 * the slider advertises a day as published and the tiles draw nothing for it -- the map renders
 * correctly and lies, which is the hardest gap to diagnose.
 *
 * This is a static agreement check, not a database round trip: it asserts both sides derive
 * from the one exported PUBLISHER_NAMED_DAY_RULE, that neither has picked up an instant-based
 * conversion, and that both refuse a well-shaped day that does not exist. It is deliberately
 * blind to the two sides' remaining textual difference -- the migration parses with
 * `to_date(..., 'YYYY-MM-DD')` and probes with `pg_input_is_valid`, where the read model casts
 * and calls `Date.parse`. Both read the same ten characters of the same string.
 */

/**
 * The migration that currently DEFINES the function: the last one to replace it, not 0015.
 *
 * Resolved rather than hardcoded because the definition moves. Every redefinition is a
 * `CREATE OR REPLACE`, so the highest-numbered migration carrying one is what production runs,
 * and a later migration automatically becomes this contract's subject instead of silently
 * escaping it while the assertions keep passing against a superseded file.
 */
const FUNCTION_SIGNATURE = "CREATE OR REPLACE FUNCTION geo.feature_observation_day";

function authoritativeMigrationPath(): string {
  const defining = readdirSync("drizzle")
    .filter((name) => name.endsWith(".sql"))
    .sort()
    .map((name) => `drizzle/${name}`)
    .filter((path) => readFileSync(path, "utf8").includes(FUNCTION_SIGNATURE));
  const newest = defining.at(-1);
  // Refused loudly at collection time: with no definition to read, every assertion below
  // would pass vacuously against an empty string and report the contract as held.
  if (newest === undefined) {
    throw new Error(`No drizzle migration contains "${FUNCTION_SIGNATURE}"`);
  }
  return newest;
}

const MIGRATION_PATH = authoritativeMigrationPath();
const MIGRATION_SOURCE = readFileSync(MIGRATION_PATH, "utf8");
const READ_MODEL_SOURCE = readFileSync(
  "src/lib/server/services/environmental-read-model.ts",
  "utf8"
);

/**
 * Keys the tile function may fall back to that the slider axis deliberately does not read.
 *
 * `fireDiscoveryDateTime` dates the INCIDENT, not the geometry on the row, so it is a last
 * resort rather than a co-equal member of PUBLISHER_NAMED_DAY_RULE.observationTimeKeys -- see
 * the ordering note in the migration. Listing it here rather than loosening the chain
 * assertion is what keeps the divergence a decision instead of a drift: any OTHER key added to
 * the SQL and not to the shared rule fails the chain test.
 */
const TILE_ONLY_FALLBACK_KEYS = ["fireDiscoveryDateTime"] as const;

/** `YYYY-MM-DD`, the shape both sides check before parsing. Mirrors the SQL's anchored regex. */
const CALENDAR_DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

/** The body of geo.feature_observation_day, between its `AS $$` and the closing `$$;`. */
function featureObservationDayBody(): string {
  const start = MIGRATION_SOURCE.indexOf(FUNCTION_SIGNATURE);
  expect(start).toBeGreaterThanOrEqual(0);
  const bodyStart = MIGRATION_SOURCE.indexOf("AS $$", start);
  const bodyEnd = MIGRATION_SOURCE.indexOf("$$;", bodyStart);
  expect(bodyStart).toBeGreaterThanOrEqual(0);
  expect(bodyEnd).toBeGreaterThan(bodyStart);
  return MIGRATION_SOURCE.slice(bodyStart, bodyEnd);
}

/** Everything the migration says outside a `--` comment, so a comment cannot satisfy an assertion. */
function withoutSqlComments(source: string): string {
  return source
    .split("\n")
    .map((line) => (line.trimStart().startsWith("--") ? "" : line))
    .join("\n");
}

/** The JSONB keys the function's COALESCE reads, in the order it reads them. */
function coalesceKeysInOrder(): string[] {
  const body = withoutSqlComments(featureObservationDayBody());
  const coalesce = /COALESCE\(([\s\S]*?)\)/.exec(body);
  expect(coalesce).not.toBeNull();
  const keys = [...(coalesce?.[1] ?? "").matchAll(/->>\s*'([^']+)'/g)].map((match) => match[1]);
  expect(keys.length).toBeGreaterThan(0);
  return keys;
}

/** The chain the migration is required to spell: the shared rule's keys, then the fallbacks. */
function expectedCoalesceKeys(): string[] {
  const ruleKeys: readonly string[] = PUBLISHER_NAMED_DAY_RULE.observationTimeKeys;
  // Filtered rather than appended, so promoting a fallback into the shared rule later is a
  // one-line change to the rule and leaves this contract passing unedited.
  return [...ruleKeys, ...TILE_ONLY_FALLBACK_KEYS.filter((key) => !ruleKeys.includes(key))];
}

/**
 * PostgreSQL `properties ->> key` over a decoded JSONB object.
 *
 * A JSON null and an absent key BOTH yield SQL NULL, which is the entire reason the chain
 * reaches past `polygonDateTime` on the rows this contract is written about. Production
 * stores these four keys as a JSON string or a JSON null and nothing else (measured against
 * `jsonb_typeof` on 2026-08-07), so string-or-null is a faithful model, not a simplification.
 */
function jsonbTextValue(properties: Record<string, unknown>, key: string): string | null {
  const value = properties[key];
  return typeof value === "string" ? value : null;
}

/**
 * The JavaScript mirror of geo.feature_observation_day, driven by the SQL's OWN key order.
 *
 * The order is parsed out of the migration rather than restated here, so these fixtures
 * exercise what the function will actually do rather than what this file believes it does.
 */
function tileObservationDay(properties: Record<string, unknown>): string | null {
  // COALESCE resolves to the FIRST non-null value and the guards then apply to that value
  // alone -- a present but malformed timestamp yields NULL, it does not fall through.
  let named: string | null = null;
  for (const key of coalesceKeysInOrder()) {
    named = jsonbTextValue(properties, key);
    if (named !== null) break;
  }
  if (named === null) return null;
  const day = named.slice(0, PUBLISHER_NAMED_DAY_RULE.prefixLength);
  if (!CALENDAR_DAY_PATTERN.test(day)) return null;
  // The `pg_input_is_valid(day, 'date')` half: a well-shaped day that does not exist.
  const parsed = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== day) return null;
  return day;
}

describe("publisher-named day contract", () => {
  it("derives the tile attribute from the rule's own JSONB keys, in order", () => {
    const body = withoutSqlComments(featureObservationDayBody());
    const positions = PUBLISHER_NAMED_DAY_RULE.observationTimeKeys.map((propertyName) =>
      body.indexOf(`'${propertyName}'`)
    );
    for (const position of positions) {
      expect(position).toBeGreaterThanOrEqual(0);
    }
    expect(positions).toEqual([...positions].sort((left, right) => left - right));
  });

  it("reads the tile attribute from the rule's ISO prefix, not from the instant", () => {
    const body = withoutSqlComments(featureObservationDayBody());
    expect(body).toMatch(
      new RegExp(`substring\\([\\s\\S]*?,\\s*1,\\s*${PUBLISHER_NAMED_DAY_RULE.prefixLength}\\s*\\)`)
    );
    // to_date over a fixed pattern and an unambiguous four-digit year reads neither TimeZone
    // nor DateStyle, which is what makes the function's IMMUTABLE declaration a safe promotion
    // rather than a misdeclaration -- see src/lib/server/db/AGENTS.md §tile-observation-day.
    expect(body).toContain("to_date(");
    expect(body).toContain("'YYYY-MM-DD'");
  });

  it("fails closed on a well-shaped day that does not exist", () => {
    const body = withoutSqlComments(featureObservationDayBody());
    // `2026-02-31` passes any shape check, and to_date RAISES on it ("date/time field value
    // out of range", PostgreSQL 16+). One raise inside ST_AsMVT blanks the whole tile, so the
    // guard must prove the day EXISTS before to_date can see it: the anchored pattern for the
    // shape, then the non-raising pg_input_is_valid probe, then the parse.
    expect(body).toMatch(
      /WHEN[\s\S]*?~\s*'\^\\d\{4\}-\\d\{2\}-\\d\{2\}\$'[\s\S]*?AND\s+pg_input_is_valid\([\s\S]*?,\s*'date'\s*\)[\s\S]*?THEN\s+to_date\(/
    );
    // The read model's twin: a shape check paired with an existence check, never one alone.
    expect(READ_MODEL_SOURCE).toMatch(
      /CALENDAR_DATE_PATTERN\.test\(date\)\s*\|\|\s*Number\.isNaN\(Date\.parse\(/
    );
    expect(tileObservationDay({ observedAt: "2026-02-31T00:00:00.000Z" })).toBeNull();
  });

  it("keeps the slider axis on the same ISO prefix", () => {
    // OBSERVATION_DAY is namedDaySql(OBSERVATION_TIME_TEXT), and namedDaySql takes its length
    // from the shared rule rather than restating 10 -- that reference is the agreement.
    expect(READ_MODEL_SOURCE).toContain("const OBSERVATION_DAY = namedDaySql(OBSERVATION_TIME_TEXT)");
    expect(READ_MODEL_SOURCE).toMatch(
      /function namedDaySql\([\s\S]*?PUBLISHER_NAMED_DAY_RULE\.prefixLength[\s\S]*?\n}/
    );
    expect(READ_MODEL_SOURCE).toMatch(
      /const OBSERVATION_TIME_TEXT = sql\.raw\([\s\S]*?PUBLISHER_NAMED_DAY_RULE\.observationTimeKeys/
    );
  });

  it("refuses an instant-based conversion on either side", () => {
    const body = withoutSqlComments(featureObservationDayBody());
    for (const conversion of PUBLISHER_NAMED_DAY_RULE.forbiddenInstantConversions) {
      expect(body).not.toContain(conversion);
    }
    const namedDaySqlBody = /function namedDaySql\([\s\S]*?\n}/.exec(READ_MODEL_SOURCE)?.[0] ?? "";
    expect(namedDaySqlBody).not.toBe("");
    for (const conversion of PUBLISHER_NAMED_DAY_RULE.forbiddenInstantConversions) {
      expect(namedDaySqlBody).not.toContain(conversion);
    }
  });

  it("keeps the function's definition in the newest migration that replaces it", () => {
    // 0015 introduced the function; a later migration may replace it, and this contract must
    // follow the definition production actually runs rather than the one it was born in.
    expect(MIGRATION_PATH).toBe("drizzle/0018_fire_discovery_observation_day.sql");
    // The five baked tile sources and any future functional index depend on the signature and
    // the volatility surviving every redefinition untouched.
    expect(MIGRATION_SOURCE).toContain("RETURNS date");
    expect(MIGRATION_SOURCE).toContain("IMMUTABLE");
    expect(MIGRATION_SOURCE).toContain("PARALLEL SAFE");
    expect(MIGRATION_SOURCE).toContain("SET search_path = public, pg_catalog");
  });

  it("reads the shared rule's keys first and only then the tile-only fallbacks", () => {
    expect(coalesceKeysInOrder()).toEqual(expectedCoalesceKeys());
  });

  it("ranks fireDiscoveryDateTime below every key that dates the geometry itself", () => {
    const keys = coalesceKeysInOrder();
    // Discovery is when the INCIDENT was found; polygonDateTime is when THIS geometry was
    // mapped, and a perimeter is reflown and republished for weeks after the fire starts.
    // Above polygonDateTime this would re-date all 106 production rows that carry a real
    // polygon timestamp onto their incident's discovery day.
    expect(keys.indexOf("fireDiscoveryDateTime")).toBe(keys.length - 1);
    for (const ruleKey of PUBLISHER_NAMED_DAY_RULE.observationTimeKeys) {
      expect(keys.indexOf(ruleKey)).toBeLessThan(keys.indexOf("fireDiscoveryDateTime"));
    }
    expect(
      tileObservationDay({
        polygonDateTime: "2026-08-05T18:22:00.000Z",
        fireDiscoveryDateTime: "2026-07-16T01:07:00.000Z",
      })
    ).toBe("2026-08-05");
  });

  it("dates a WFIGS perimeter whose polygonDateTime is JSON null from its discovery time", () => {
    // The 13 production fire-perimeters rows the 2026-08-07 validation report called undated.
    // `->>` yields SQL NULL for a JSON null exactly as it does for an absent key, so the chain
    // steps past polygonDateTime and reaches the discovery instant.
    expect(
      tileObservationDay({
        polygonDateTime: null,
        fireDiscoveryDateTime: "2026-07-16T01:07:00.000Z",
      })
    ).toBe("2026-07-16");
    expect(
      tileObservationDay({ fireDiscoveryDateTime: "2026-07-16T01:07:00.000Z" })
    ).toBe("2026-07-16");
  });

  it("leaves a feature with no datable key undated rather than inventing a day", () => {
    // NULL here is not a failure: tileLayerDateFilter keeps an undated feature at every date,
    // because dropping a row on a parse failure would silently delete data from the map.
    expect(tileObservationDay({ polygonDateTime: null, fireDiscoveryDateTime: null })).toBeNull();
    expect(tileObservationDay({ fireName: "Bootleg" })).toBeNull();
    // COALESCE takes the first non-null value and the guards judge THAT value alone -- a
    // malformed polygonDateTime must not fall through to a well-formed discovery time.
    expect(
      tileObservationDay({
        polygonDateTime: "not-a-timestamp",
        fireDiscoveryDateTime: "2026-07-16T01:07:00.000Z",
      })
    ).toBeNull();
  });
});
