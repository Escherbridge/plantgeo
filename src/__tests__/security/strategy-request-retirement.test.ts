import { readFileSync } from "node:fs";
import { getTableConfig } from "drizzle-orm/pg-core";
import { describe, expect, it } from "vitest";
import * as schema from "@/lib/server/db/schema";
import { requestVotes } from "@/lib/server/db/schema";

/**
 * Phase 3 of `public_strategy_requests_20260913`: the private strategy-request path reaches its
 * final shape.
 *
 * Asserted against the Drizzle declarations and the forward migration SQL rather than a live
 * database -- this repo never runs PlantGeo or its stack locally ("Never run PlantGeo locally"),
 * so a migration test's job is to pin the DDL text and the schema module that must agree with it.
 * The migration-contract coupling itself lives in `readiness-migration-contract.test.ts`.
 */

const MIGRATION_SQL = readFileSync(
  "drizzle/0004_public_strategy_requests.sql",
  "utf8"
);

describe("the retired tables", () => {
  it("no longer declares strategy_requests or priority_zones in the schema module", () => {
    expect(schema).not.toHaveProperty("strategyRequests");
    expect(schema).not.toHaveProperty("priorityZones");
  });

  it("drops both tables in the forward migration", () => {
    expect(MIGRATION_SQL).toContain('DROP TABLE IF EXISTS "strategy_requests"');
    expect(MIGRATION_SQL).toContain('DROP TABLE IF EXISTS "priority_zones"');
  });

  it("drops strategy_requests only after request_votes has stopped referencing it", () => {
    const dropsForeignKey = MIGRATION_SQL.indexOf(
      'DROP CONSTRAINT IF EXISTS "request_votes_request_id_strategy_requests_id_fk"'
    );
    const dropsTable = MIGRATION_SQL.indexOf(
      'DROP TABLE IF EXISTS "strategy_requests"'
    );
    expect(dropsForeignKey).toBeGreaterThan(-1);
    expect(dropsTable).toBeGreaterThan(dropsForeignKey);
  });
});

describe("request_votes after the foreign-key move", () => {
  const config = getTableConfig(requestVotes);
  const columnNames = config.columns.map((column) => column.name);

  it("survives the drop, in the public schema, under its own name", () => {
    expect(config.schema).toBeUndefined();
    expect(config.name).toBe("request_votes");
  });

  it("keys on feature_id, not the dropped request_id", () => {
    expect(columnNames).toContain("feature_id");
    expect(columnNames).not.toContain("request_id");
  });

  it("references geo.features(id) instead of strategy_requests(id)", () => {
    const references = config.foreignKeys.map((foreignKey) => {
      const reference = foreignKey.reference();
      return {
        columns: reference.columns.map((column) => column.name),
        foreignTable: getTableConfig(reference.foreignTable).name,
        foreignColumns: reference.foreignColumns.map((column) => column.name),
      };
    });

    expect(references).toEqual(
      expect.arrayContaining([
        {
          columns: ["feature_id"],
          foreignTable: "features",
          foreignColumns: ["id"],
        },
        { columns: ["user_id"], foreignTable: "users", foreignColumns: ["id"] },
      ])
    );
    expect(
      references.some((reference) => reference.foreignTable === "strategy_requests")
    ).toBe(false);
  });

  it("keeps a composite primary key, now on (feature_id, user_id)", () => {
    const primaryKeyColumns = config.primaryKeys.map((key) =>
      key.columns.map((column) => column.name)
    );
    expect(primaryKeyColumns).toContainEqual(["feature_id", "user_id"]);
  });

  it("performs the rename and re-key in the forward migration", () => {
    expect(MIGRATION_SQL).toContain(
      'ALTER TABLE "request_votes" RENAME COLUMN "request_id" TO "feature_id"'
    );
    expect(MIGRATION_SQL).toContain(
      'ADD CONSTRAINT "request_votes_feature_id_user_id_pk" PRIMARY KEY("feature_id","user_id")'
    );
    expect(MIGRATION_SQL).toContain(
      'FOREIGN KEY ("feature_id") REFERENCES "geo"."features"("id") ON DELETE cascade'
    );
  });

  it("has no counter column -- the denormalized vote_count went with strategy_requests", () => {
    expect(columnNames).not.toContain("vote_count");
  });
});

describe("the forward migration's preconditions", () => {
  it("names the backfill script that must run before the drop", () => {
    expect(MIGRATION_SQL).toContain("scripts/backfill-strategy-requests.mjs");
  });

  it("does not touch geo.features itself", () => {
    expect(MIGRATION_SQL).not.toMatch(/ALTER TABLE "geo"\."features"/);
    expect(MIGRATION_SQL).not.toMatch(/DROP TABLE[^\n]*"geo"\."features"/);
  });
});
