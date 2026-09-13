import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { INTERVENTION_STATUS_COLOR } from "@/lib/map/layers";

/**
 * Phase 3 of `public_strategy_requests_20260913`: `geo.intervention_tiles()` projects `kind`.
 *
 * The paint expression in `src/lib/map/layers.ts` leads with a `kind == "request"` arm, but the
 * PUBLISHED tile source is this SQL function -- an attribute it does not project cannot be read by
 * any expression, so a request served through Martin painted as a plain land-category feature.
 * These assertions pin the projection against the migration text (no live database: "Never run
 * PlantGeo locally") and pin that the client expression still depends on it, so deleting either
 * half alone fails here rather than silently in production.
 */

const CATEGORY_MIGRATION_SQL = readFileSync(
  "drizzle/0002_intervention_tiles_category.sql",
  "utf8"
);
const KIND_MIGRATION_SQL = readFileSync(
  "drizzle/0005_intervention_tiles_kind.sql",
  "utf8"
);

describe("geo.intervention_tiles() kind projection", () => {
  it("redefines the function rather than creating a second one", () => {
    expect(KIND_MIGRATION_SQL).toContain(
      "CREATE OR REPLACE FUNCTION geo.intervention_tiles(z integer, x integer, y integer)"
    );
  });

  it("projects kind alongside the category the previous migration added", () => {
    expect(KIND_MIGRATION_SQL).toContain(
      "f.properties ->> 'kind' AS kind"
    );
    expect(KIND_MIGRATION_SQL).toContain(
      "f.properties ->> 'category' AS category"
    );
  });

  it("keeps every attribute 0002 projected -- this widens the projection, never narrows it", () => {
    const projectedAttributes = [
      "f.properties ->> 'intervention_type' AS intervention_type",
      "f.properties ->> 'category' AS category",
      "f.properties ->> 'priority' AS priority",
      "f.properties ->> 'status' AS status",
      "f.properties ->> 'name' AS name",
      "f.properties ->> 'description' AS description",
    ];
    for (const attribute of projectedAttributes) {
      expect(CATEGORY_MIGRATION_SQL).toContain(attribute);
      expect(KIND_MIGRATION_SQL).toContain(attribute);
    }
  });

  it("leaves the published pin and the public-layer filter untouched", () => {
    expect(KIND_MIGRATION_SQL).toContain("WHERE l.name = 'interventions'");
    expect(KIND_MIGRATION_SQL).toContain("AND l.is_public IS TRUE");
    expect(KIND_MIGRATION_SQL).toContain("AND f.status = 'published'");
    expect(KIND_MIGRATION_SQL).toContain("LIMIT 10000");
  });

  /**
   * The caveat that has silently broken a tile migration on this platform before -- Martin caches
   * each function's column set at startup, so a correct migration with no restart keeps serving
   * the old attributes. `0002` carries the same warning and it applies again here.
   */
  it("carries the restart-Martin caveat in the migration itself", () => {
    expect(KIND_MIGRATION_SQL).toMatch(/RESTART MARTIN/);
    expect(CATEGORY_MIGRATION_SQL).toMatch(/RESTART MARTIN/);
  });
});

describe("the client expression this projection feeds", () => {
  it("still reads kind ahead of status and category", () => {
    const expression = JSON.stringify(INTERVENTION_STATUS_COLOR);
    expect(expression).toContain('["==",["get","kind"],"request"]');
    expect(expression.indexOf('["get","kind"]')).toBeLessThan(
      expression.indexOf('["get","status"]')
    );
  });
});
