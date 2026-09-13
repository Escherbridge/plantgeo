/**
 * Phase 2 of `public_strategy_requests_20260913`: a public strategy request and a
 * drawn recommendation now share one map toggle and one paint expression, so the
 * ONE thing that must not regress is that they do not look the same.
 *
 * A request is always `published` (it skips review), so if the `kind` arm were
 * removed or ordered after the status arm, every request would quietly take its
 * category colour and be indistinguishable from a recommendation on the map --
 * which is the failure this file exists to catch.
 */
import { describe, expect, it } from "vitest";
import {
  INTERVENTION_PENDING_REVIEW_COLOR,
  INTERVENTION_REQUEST_COLOR,
  INTERVENTION_STATUS_COLOR,
  UNCLASSIFIED_FILL_COLOR,
  getLayers,
} from "@/lib/map/layers";

const MERGED_STYLE_LAYER_IDS = [
  "interventions",
  "interventions-outline",
  "interventions-points",
  "intervention-drafts-fill",
  "intervention-drafts-outline",
  "intervention-drafts-points",
] as const;

/** The same `case`-over-`match` walk the sibling unified-layer test performs. */
function paint(feature: Record<string, string | undefined>): string {
  const expression = INTERVENTION_STATUS_COLOR as unknown as unknown[];
  let index = 1;
  while (index + 1 < expression.length) {
    const [, getter, expected] = expression[index] as [string, [string, string], string];
    if (feature[getter[1]] === expected) return expression[index + 1] as string;
    index += 2;
  }
  const arms = (expression[index] as [string, unknown, ...string[]]).slice(2) as string[];
  for (let arm = 0; arm < arms.length - 1; arm += 2) {
    if (arms[arm] === feature.category) return arms[arm + 1];
  }
  return arms[arms.length - 1];
}

describe("request features are visually distinct on the merged layer", () => {
  it("paints a published request its own colour, not its category colour", () => {
    expect(paint({ kind: "request", status: "published", category: "land" })).toBe(
      INTERVENTION_REQUEST_COLOR
    );
    // The land recommendation it sits next to, for contrast.
    expect(paint({ status: "published", category: "land" })).not.toBe(
      INTERVENTION_REQUEST_COLOR
    );
  });

  it("keeps the request colour distinct from every other colour the expression can produce", () => {
    const others = new Set([
      paint({ status: "published", category: "land" }),
      paint({ status: "published", category: "air" }),
      INTERVENTION_PENDING_REVIEW_COLOR,
      UNCLASSIFIED_FILL_COLOR,
    ]);
    expect(others.has(INTERVENTION_REQUEST_COLOR)).toBe(false);
  });

  it("reads a missing `kind` as a recommendation -- pre-2026-09-13 rows are not backfilled", () => {
    expect(paint({ status: "published", category: "land" })).toBe("#0d9488");
    expect(paint({ kind: "intervention", status: "published", category: "air" })).toBe(
      "#7c3aed"
    );
    expect(paint({ status: "published" })).toBe(UNCLASSIFIED_FILL_COLOR);
  });

  it("leaves the in-review recommendation rule untouched", () => {
    expect(paint({ status: "pending_review", category: "land" })).toBe(
      INTERVENTION_PENDING_REVIEW_COLOR
    );
  });

  it("distinguishes requests on all six merged style layers, from one expression", () => {
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
      expect(JSON.stringify(paintSpec[colorProperties[layerId]]), layerId).toContain(
        INTERVENTION_REQUEST_COLOR
      );
    }
  });

  it("keys the request arm ahead of the status arm", () => {
    const expression = INTERVENTION_STATUS_COLOR as unknown as unknown[];
    const serialized = JSON.stringify(expression);
    expect(serialized.indexOf('"kind"')).toBeGreaterThan(-1);
    expect(serialized.indexOf('"kind"')).toBeLessThan(serialized.indexOf('"status"'));
  });
});
