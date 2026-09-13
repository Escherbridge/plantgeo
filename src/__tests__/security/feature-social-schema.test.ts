import { readFileSync } from "node:fs";
import { getTableConfig } from "drizzle-orm/pg-core";
import { describe, expect, it } from "vitest";
import {
  featureComments,
  featureLikes,
  features,
  users,
} from "@/lib/server/db/schema";

/**
 * The Phase 4 social tables, asserted against the Drizzle declarations and the
 * forward migration rather than a live database (this repo never runs
 * PlantGeo or its stack locally). The migration-contract coupling itself is
 * covered by `readiness-migration-contract.test.ts`, which pins the newest
 * journal entry's digest.
 */

const MIGRATION_SQL = readFileSync(
  "drizzle/0001_feature_social.sql",
  "utf8"
);

function referencedTableNames(table: Parameters<typeof getTableConfig>[0]) {
  return getTableConfig(table).foreignKeys.map((foreignKey) => {
    const reference = foreignKey.reference();
    return {
      columns: reference.columns.map((column) => column.name),
      foreignTable: getTableConfig(reference.foreignTable).name,
      foreignColumns: reference.foreignColumns.map((column) => column.name),
    };
  });
}

describe("geo.feature_likes", () => {
  const config = getTableConfig(featureLikes);

  it("lives in the geo schema beside the features it keys on", () => {
    expect(config.schema).toBe("geo");
    expect(config.name).toBe("feature_likes");
  });

  it("is unique on (feature_id, user_id) so a like can never be double-counted", () => {
    const uniqueColumns = config.uniqueConstraints.map((constraint) =>
      constraint.columns.map((column) => column.name)
    );
    expect(uniqueColumns).toContainEqual(["feature_id", "user_id"]);
  });

  it("carries foreign keys to geo.features and public.users", () => {
    expect(getTableConfig(features).name).toBe("features");
    expect(getTableConfig(users).name).toBe("users");
    expect(referencedTableNames(featureLikes)).toEqual(
      expect.arrayContaining([
        {
          columns: ["feature_id"],
          foreignTable: "features",
          foreignColumns: ["id"],
        },
        { columns: ["user_id"], foreignTable: "users", foreignColumns: ["id"] },
      ])
    );
  });

  it("is created by the forward migration with the same unique constraint", () => {
    expect(MIGRATION_SQL).toContain('CREATE TABLE "geo"."feature_likes"');
    expect(MIGRATION_SQL).toContain(
      'CONSTRAINT "uq_feature_likes_feature_user" UNIQUE("feature_id","user_id")'
    );
    expect(MIGRATION_SQL).toContain(
      'REFERENCES "geo"."features"("id") ON DELETE cascade'
    );
  });
});

describe("geo.feature_comments", () => {
  const config = getTableConfig(featureComments);
  const columnNames = config.columns.map((column) => column.name);

  it("lives in the geo schema", () => {
    expect(config.schema).toBe("geo");
    expect(config.name).toBe("feature_comments");
  });

  it("carries feature_id, author_user_id, body and created_at", () => {
    expect(columnNames).toEqual(
      expect.arrayContaining([
        "feature_id",
        "author_user_id",
        "body",
        "created_at",
      ])
    );
  });

  it("soft-deletes through deleted_at rather than removing the row", () => {
    expect(columnNames).toContain("deleted_at");
    expect(columnNames).toContain("deleted_by_user_id");
    const deletedAt = config.columns.find(
      (column) => column.name === "deleted_at"
    );
    expect(deletedAt?.notNull).toBe(false);
  });

  it("carries foreign keys to geo.features and public.users", () => {
    expect(referencedTableNames(featureComments)).toEqual(
      expect.arrayContaining([
        {
          columns: ["feature_id"],
          foreignTable: "features",
          foreignColumns: ["id"],
        },
        {
          columns: ["author_user_id"],
          foreignTable: "users",
          foreignColumns: ["id"],
        },
      ])
    );
  });

  it("is created by the forward migration with a soft-delete column", () => {
    expect(MIGRATION_SQL).toContain('CREATE TABLE "geo"."feature_comments"');
    expect(MIGRATION_SQL).toContain('"deleted_at" timestamp with time zone');
    expect(MIGRATION_SQL).toContain(
      'ALTER TABLE "geo"."feature_comments" ADD CONSTRAINT "feature_comments_feature_id_features_id_fk"'
    );
  });
});

describe("the forward migration", () => {
  it("does not touch the review vocabulary owned by the contributions router", () => {
    expect(MIGRATION_SQL).not.toMatch(/review_note/);
    expect(MIGRATION_SQL).not.toMatch(/ALTER TABLE "geo"\."features"/);
  });
});
