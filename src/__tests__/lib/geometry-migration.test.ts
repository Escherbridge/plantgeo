import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const MIGRATION_PATH = "drizzle/0008_geometry_dimension.sql";
const BACKFILL_PATH = "scripts/backfill-geometry.sql";

/** Reads a SQL file with its `--` comments removed, so prose about a mistake never satisfies a guard. */
function readSqlWithoutComments(path: string): string {
  return readFileSync(path, "utf8")
    .split("\n")
    .map((line) => line.replace(/--.*$/, ""))
    .join("\n");
}

describe("geometry dimension migration", () => {
  it("never declares a stored generated column", () => {
    expect(readSqlWithoutComments(MIGRATION_PATH)).not.toMatch(
      /GENERATED\s+ALWAYS/i
    );
  });

  it("never cascades a delete into or out of the dimension", () => {
    expect(readSqlWithoutComments(MIGRATION_PATH)).not.toMatch(
      /ON\s+DELETE\s+CASCADE/i
    );
  });

  it("has no first_seen_at column, because min(version_valid_from) is first-seen", () => {
    expect(readSqlWithoutComments(MIGRATION_PATH)).not.toMatch(/first_seen_at/i);
  });

  it("namespaces every natural_key by its producer, and allows the 'line' kind", () => {
    const migration = readSqlWithoutComments(MIGRATION_PATH);

    expect(migration).toMatch(/ck_geometry_natural_key_namespaced/);
    expect(migration).toMatch(/"natural_key"\s+LIKE\s+"producer"\s*\|\|\s*':%'/);
    expect(migration).toMatch(/'point','polygon','line','grid_cell'/);
  });

  it("defers the superseded_by self-FK, without which no version can ever be closed", () => {
    expect(readSqlWithoutComments(MIGRATION_PATH)).toMatch(
      /"superseded_by"\s+uuid\s+REFERENCES[^\n]*DEFERRABLE\s+INITIALLY\s+DEFERRED/i
    );
  });
});

describe("geometry dimension backfill", () => {
  it("never dates a v1 version from a write timestamp", () => {
    const backfill = readSqlWithoutComments(BACKFILL_PATH);

    expect(backfill).not.toMatch(/now\(\)/i);
    expect(backfill).not.toMatch(/created_at/i);
  });

  it("maps geom_kind with an explicit CASE, not by stripping the MULTI prefix", () => {
    const backfill = readSqlWithoutComments(BACKFILL_PATH);

    expect(backfill).not.toMatch(/replace\s*\(\s*GeometryType/i);
    expect(backfill).toMatch(/WHEN\s+'MULTILINESTRING'\s+THEN\s+'line'/);
  });

  it("locks geo.features, because REPEATABLE READ does not hide a concurrent INSERT", () => {
    expect(readSqlWithoutComments(BACKFILL_PATH)).toMatch(
      /LOCK\s+TABLE\s+geo\.features[^\n]*IN\s+SHARE\s+MODE/i
    );
  });

  it("reaches for the fire discovery time before falling back to '-infinity'", () => {
    expect(readSqlWithoutComments(BACKFILL_PATH)).toMatch(
      /coalesce\(\s*f\.properties ->> 'polygonDateTime',\s*f\.properties ->> 'fireDiscoveryDateTime'\s*\)/
    );
  });
});
