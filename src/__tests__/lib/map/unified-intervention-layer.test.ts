/**
 * Phase 2 of the unified_intervention_layer_20260913 track: ONE layer-panel row for both the
 * published Martin-tile intervention layers and the client-GeoJSON draft/proposed overlay,
 * painted by one shared status/category expression.
 *
 * The three claims pinned here are the ones that fail silently if they regress:
 *  - a style layer left out of the merged entry's `styleLayerIds` is a layer the single switch
 *    cannot flip, which reads on the map as "my submission never showed up";
 *  - a second registry entry for the drafts is a second row in the dock, which is exactly the
 *    two-switches-for-one-idea shape FR-1 removed;
 *  - two paint expressions is one feature painting two colours depending on which source its
 *    bytes arrived from.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { dockReachableLayerToggleIds } from "@/components/map/layer-panel/dock-sections";
import {
  LAYER_REGISTRY,
  LAYER_TOGGLE_IDS,
  isLayerToggleId,
  styleBackedLayerEntries,
} from "@/lib/map/layer-registry";
import {
  INTERVENTION_PENDING_REVIEW_COLOR,
  INTERVENTION_STATUS_COLOR,
  UNCLASSIFIED_FILL_COLOR,
  getLayers,
} from "@/lib/map/layers";
import { EXPECTED_DRIZZLE_MIGRATION } from "@/lib/server/db/migration-contract";

const MERGED_STYLE_LAYER_IDS = [
  "interventions",
  "interventions-outline",
  "interventions-points",
  "intervention-drafts-fill",
  "intervention-drafts-outline",
  "intervention-drafts-points",
] as const;

describe("unified intervention toggle (FR-1, OQ-1: alias visibility)", () => {
  it("puts all six style layer ids under the single `interventions` toggle", () => {
    expect(LAYER_REGISTRY.interventions.styleLayerIds).toEqual([...MERGED_STYLE_LAYER_IDS]);
  });

  it("exposes no second, separately togglable drafts row anywhere the panel reads", () => {
    expect(isLayerToggleId("intervention-drafts")).toBe(false);
    expect(LAYER_TOGGLE_IDS).not.toContain("intervention-drafts");
    expect(styleBackedLayerEntries().map((entry) => entry.toggleId)).not.toContain(
      "intervention-drafts"
    );
    expect([...dockReachableLayerToggleIds()]).not.toContain("intervention-drafts");
    // Exactly one row in the dock mentions interventions at all.
    expect(
      [...dockReachableLayerToggleIds()].filter((id) => id.startsWith("intervention"))
    ).toEqual(["interventions"]);
  });

  /**
   * `applyVisibility` in LayerManager.tsx iterates `styleBackedLayerEntries()` and writes one
   * visibility per entry across every id it lists, so folding the ids into one entry IS the
   * aliasing -- there is no second code path to keep in step. This asserts the derivation that
   * makes that true, so a future split back into two entries fails here rather than on the map.
   */
  it("flips every merged id together, from one visibility read", () => {
    const entry = styleBackedLayerEntries().find((candidate) => candidate.toggleId === "interventions");
    if (entry === undefined) throw new Error("expected a style-backed interventions entry");

    // The same fold applyVisibility performs, over one toggle's boolean.
    const visibilityFor = (interventionsOn: boolean): Record<string, string> =>
      Object.fromEntries(
        styleBackedLayerEntries().flatMap((candidate) =>
          candidate.styleLayerIds.map((layerId) => [
            layerId,
            candidate.toggleId === "interventions" && interventionsOn ? "visible" : "none",
          ])
        )
      );

    const on = visibilityFor(true);
    const off = visibilityFor(false);
    for (const layerId of MERGED_STYLE_LAYER_IDS) {
      expect(on[layerId], layerId).toBe("visible");
      expect(off[layerId], layerId).toBe("none");
    }
    expect(entry.styleLayerIds).toEqual([...MERGED_STYLE_LAYER_IDS]);
  });

  it("keeps the drafts layer and source specs alive -- only the second switch is gone", () => {
    const bakedIds = getLayers().map((layer) => layer.id);
    for (const layerId of MERGED_STYLE_LAYER_IDS) {
      expect(bakedIds).toContain(layerId);
    }
    expect(new Set(bakedIds).size).toBe(bakedIds.length);
  });

  it("describes the merged behaviour in the row's own copy", () => {
    const entry = LAYER_REGISTRY.interventions;
    expect(entry.description).toBeDefined();
    expect(entry.description).not.toBe(entry.label);
    expect(entry.description?.toLowerCase()).toContain("review");
    expect(entry.description?.toLowerCase()).toContain("orange");
  });
});

describe("shared intervention paint expression (FR-1, OQ-2)", () => {
  const publishedLand = {
    status: "published",
    category: "land",
  };

  /** A tiny evaluator for the exact `case`/`match` shape this expression takes. */
  function paint(feature: Record<string, string | undefined>): string {
    const [, , pendingColor, categoryMatch] = INTERVENTION_STATUS_COLOR as unknown as [
      string,
      unknown,
      string,
      [string, unknown, ...string[]],
    ];
    if (feature.status === "pending_review") return pendingColor;
    const arms = categoryMatch.slice(2) as string[];
    const fallback = arms[arms.length - 1];
    for (let index = 0; index < arms.length - 1; index += 2) {
      if (arms[index] === feature.category) return arms[index + 1];
    }
    return fallback;
  }

  it("paints a pending-review feature orange regardless of category, from either source", () => {
    expect(paint({ status: "pending_review", category: "land" })).toBe(
      INTERVENTION_PENDING_REVIEW_COLOR
    );
    expect(paint({ status: "pending_review", category: "air" })).toBe(
      INTERVENTION_PENDING_REVIEW_COLOR
    );
    expect(paint({ status: "pending_review" })).toBe(INTERVENTION_PENDING_REVIEW_COLOR);
    expect(INTERVENTION_PENDING_REVIEW_COLOR).toBe("#f97316");
  });

  it("paints a published feature by category", () => {
    expect(paint(publishedLand)).toBe("#0d9488");
    expect(paint({ status: "published", category: "air" })).toBe("#7c3aed");
  });

  it("falls back to the shared neutral for a feature with no category reported", () => {
    expect(paint({ status: "published" })).toBe(UNCLASSIFIED_FILL_COLOR);
    expect(paint({ status: "published", category: "orbital" })).toBe(UNCLASSIFIED_FILL_COLOR);
  });

  it("is the identical expression on all six merged style layers", () => {
    const byId = new Map(getLayers().map((layer) => [layer.id, layer]));
    const colorProperties: Record<string, string> = {
      interventions: "fill-color",
      "interventions-outline": "line-color",
      "interventions-points": "circle-color",
      "intervention-drafts-fill": "fill-color",
      "intervention-drafts-outline": "line-color",
      "intervention-drafts-points": "circle-color",
    };
    for (const layerId of MERGED_STYLE_LAYER_IDS) {
      const layer = byId.get(layerId);
      if (layer === undefined) throw new Error(`${layerId} is not baked into the style`);
      const paintSpec = (layer as { paint?: Record<string, unknown> }).paint ?? {};
      expect(paintSpec[colorProperties[layerId]], layerId).toEqual(INTERVENTION_STATUS_COLOR);
    }
  });

  it("keys off no fabricated field: `priority` is painted nowhere", () => {
    expect(JSON.stringify(INTERVENTION_STATUS_COLOR)).not.toContain("priority");
  });
});

/**
 * The tile function's projection, asserted against the committed migration rather than a live
 * database: this repo's rule is never to run PlantGeo (or its Postgres) locally, and the SQL
 * text is the artefact the deploy actually applies. The migration-contract pairing is checked
 * by readiness-migration-contract.test.ts; this asserts what the SQL SAYS.
 */
describe("geo.intervention_tiles() projects category (OQ-2 migration)", () => {
  const migration = readFileSync("drizzle/0002_intervention_tiles_category.sql", "utf8");

  it("is committed at or below the migration contract's high-water mark", () => {
    // The contract names the LATEST migration; other tracks land migrations into the same
    // tree, so this one only has to exist at or before it -- never after it.
    expect(EXPECTED_DRIZZLE_MIGRATION.tag >= "0002_intervention_tiles_category").toBe(true);
  });

  it("redefines the function with a `category` attribute", () => {
    expect(migration).toContain("CREATE OR REPLACE FUNCTION geo.intervention_tiles");
    expect(migration).toContain("f.properties ->> 'category' AS category");
  });

  it("still projects status and stays published-only", () => {
    expect(migration).toContain("f.properties ->> 'status' AS status");
    expect(migration).toContain("f.status = 'published'");
    // No other status may leak out of the published tile source: the merged toggle's
    // pending-review half comes from the client-side overlay, never from Martin.
    expect(migration.match(/f\.status\b/g)).toEqual(["f.status"]);
  });

  it("records the Martin restart the migration depends on", () => {
    expect(migration).toContain("RESTART MARTIN");
  });
});
