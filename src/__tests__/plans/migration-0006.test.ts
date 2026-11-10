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
  "0006_site_plans_and_affiliate_applications.sql"
);

describe("site plan migration", () => {
  const sql = readFileSync(migrationPath, "utf8");
  const statements = sql.replace(/--.*$/gm, "");

  it("adds the three tables and an idempotent sample row", () => {
    expect(sql).toContain('CREATE TABLE "site_plans"');
    expect(sql).toContain('CREATE TABLE "site_plan_items"');
    expect(sql).toContain('CREATE TABLE "affiliate_applications"');
    expect(sql).toContain("sample-ada-county");
    expect(sql).toContain("ada-demo");
    expect(sql).toContain("Sample plan — not a real customer");
    expect(sql).toContain('ON CONFLICT ("public_id") DO NOTHING');
  });

  it("does not change existing tables, users, or map features", () => {
    expect(statements).not.toMatch(/\bDROP\b/i);
    expect(statements).not.toMatch(/\bALTER\s+TABLE\s+"(?!site_plans"|site_plan_items")/i);
    expect(statements).not.toMatch(/\bDELETE\s+FROM\b/i);
    expect(statements).not.toMatch(/\bTRUNCATE\b/i);
    expect(statements).not.toMatch(/INSERT\s+INTO\s+"users"/i);
    expect(statements).not.toMatch(/geo\.features/i);
    expect(statements).not.toMatch(/"geo"\."features"/i);
  });
});
