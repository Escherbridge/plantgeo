// @vitest-environment node
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const migrationPath = join(
  fileURLToPath(new URL(".", import.meta.url)),
  "..",
  "..",
  "..",
  "drizzle",
  "0007_affiliates.sql"
);

describe("affiliate migration", () => {
  const sql = readFileSync(migrationPath, "utf8");
  const statements = sql.replace(/--.*$/gm, "");

  it("adds the affiliates table and one active code per email", () => {
    expect(sql).toContain('CREATE TABLE "affiliates"');
    expect(sql).toContain('"code" text NOT NULL');
    expect(sql).toContain('CONSTRAINT "affiliates_code_unique" UNIQUE("code")');
    expect(sql).toContain('CONSTRAINT "affiliates_code_shape"');
    expect(sql).toContain("affiliates_active_email_unique");
    expect(sql).toContain("WHERE \"status\" = 'active'");
    expect(sql).toContain("'active', 'inactive'");
  });

  it("does not change existing tables, seed rows, or record a payout", () => {
    expect(statements).not.toMatch(/\bDROP\b/i);
    expect(statements).not.toMatch(/\bDELETE\s+FROM\b/i);
    expect(statements).not.toMatch(/\bTRUNCATE\b/i);
    expect(statements).not.toMatch(/\bINSERT\s+INTO\b/i);
    expect(statements).not.toMatch(/\bALTER\s+TABLE\s+"(?!affiliates")/i);
    expect(statements).not.toMatch(/INSERT\s+INTO\s+"users"/i);
    expect(statements).not.toMatch(/payout|commission|rate_bps|paid_at|amount_cents/i);
  });
});
