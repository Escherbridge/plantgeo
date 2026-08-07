import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

// The rule is a plain constant, but it lives in a module that opens a warehouse handle at import.
vi.mock("@/lib/server/db", () => ({ db: {} }));

import { PUBLISHER_NAMED_DAY_RULE } from "@/lib/server/services/environmental-read-model";

/**
 * The slider axis and the baked tile layers must bucket a feature onto the SAME calendar day.
 *
 * `OBSERVATION_DAY` (src/lib/server/services/environmental-read-model.ts) answers the slider;
 * `geo.feature_observation_day` (drizzle/0015_tile_observation_day.sql) is baked onto four
 * Martin function sources as the `observed_day` attribute the client filters on. If the two
 * ever derive the day differently the slider advertises a day as published and the tiles draw
 * nothing for it -- the map renders correctly and lies, which is the hardest gap to diagnose.
 *
 * This is a static agreement check, not a database round trip: it asserts both sides derive
 * from the one exported PUBLISHER_NAMED_DAY_RULE, that neither has picked up an instant-based
 * conversion, and that both refuse a well-shaped day that does not exist. It is deliberately
 * blind to the two sides' remaining textual difference -- the migration parses with
 * `to_date(..., 'YYYY-MM-DD')` and probes with `pg_input_is_valid`, where the read model casts
 * and calls `Date.parse`. Both read the same ten characters of the same string.
 */
const MIGRATION_SOURCE = readFileSync("drizzle/0015_tile_observation_day.sql", "utf8");
const READ_MODEL_SOURCE = readFileSync(
  "src/lib/server/services/environmental-read-model.ts",
  "utf8"
);

/** The body of geo.feature_observation_day, between its `AS $$` and the closing `$$;`. */
function featureObservationDayBody(): string {
  const start = MIGRATION_SOURCE.indexOf("CREATE OR REPLACE FUNCTION geo.feature_observation_day");
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
});
