import { describe, expect, it } from "vitest";
import type {
  CircleLayerSpecification,
  FillLayerSpecification,
  LineLayerSpecification,
} from "@maplibre/maplibre-gl-style-spec";
import {
  INTERVENTION_DRAFTS_SOURCE,
  interventionDraftsFillLayer as interventionDraftsFillLayerSpec,
  interventionDraftsOutlineLayer as interventionDraftsOutlineLayerSpec,
  interventionDraftsPointsLayer as interventionDraftsPointsLayerSpec,
  getLayers,
  INTERVENTION_STATUS_COLOR,
  UNCLASSIFIED_FILL_COLOR,
} from "@/lib/map/layers";
import {
  DYNAMIC_TILE_SOURCE_IDS_FOR_TEST,
  getSources,
  INTERVENTION_DRAFTS_SOURCE_ID,
} from "@/lib/map/sources";
import { LAYER_REGISTRY, styleBackedLayerEntries } from "@/lib/map/layer-registry";

// Narrowed to their concrete spec types: `getLayers()` and the exported constants are typed as
// the union `LayerSpecification`, whose members (fill/line/circle/...) share no `paint`/`filter`/
// `source` shape. These three are always exactly a fill, a line and a circle layer respectively.
const interventionDraftsFillLayer = interventionDraftsFillLayerSpec as FillLayerSpecification;
const interventionDraftsOutlineLayer = interventionDraftsOutlineLayerSpec as LineLayerSpecification;
const interventionDraftsPointsLayer = interventionDraftsPointsLayerSpec as CircleLayerSpecification;

describe("intervention-drafts overlay: registration under the merged toggle", () => {
  // ADDITIVE UNTIL 2026-09-13, MERGED SINCE: the overlay had a toggle of its own
  // ("intervention-drafts") from the intervention_drawing_visibility track until the
  // unified_intervention_layer track's OQ-1 folded its three style layer ids into the
  // `interventions` entry. The LAYER SPECS below are untouched by that merge -- only the second
  // switch is gone -- which is exactly what this file still pins.
  it("draws under the single interventions toggle, with no entry of its own", () => {
    const interventions = LAYER_REGISTRY.interventions;

    expect(interventions.styleLayerIds).toEqual([
      "interventions",
      "interventions-outline",
      "interventions-points",
      "intervention-drafts-fill",
      "intervention-drafts-outline",
      "intervention-drafts-points",
    ]);
    expect(interventions.renderKind).toBe("style");
    expect(styleBackedLayerEntries().map((e) => e.toggleId)).not.toContain("intervention-drafts");
  });

  it("carries a tooltip description distinct from its short label", () => {
    const interventions = LAYER_REGISTRY.interventions;
    expect(interventions.description).toBeDefined();
    expect(interventions.description).not.toBe(interventions.label);
    expect(interventions.description).toContain("review");
  });

  it("uses its own plain GeoJSON source, distinct from the Martin intervention_tiles source", () => {
    expect(interventionDraftsFillLayer.source).toBe(INTERVENTION_DRAFTS_SOURCE);
    expect(interventionDraftsOutlineLayer.source).toBe(INTERVENTION_DRAFTS_SOURCE);
    expect(interventionDraftsPointsLayer.source).toBe(INTERVENTION_DRAFTS_SOURCE);
    expect(INTERVENTION_DRAFTS_SOURCE).toBe(INTERVENTION_DRAFTS_SOURCE_ID);

    // A GeoJSON source layer must carry no `source-layer` key (MapLibre rejects it silently
    // otherwise -- see the header comment above INTERVENTION_SOURCE in layers.ts).
    expect("source-layer" in interventionDraftsFillLayer).toBe(false);
  });

  it("bakes all three new layer ids into the style exactly once", () => {
    const ids = getLayers().map((l) => l.id);
    expect(ids).toContain("intervention-drafts-fill");
    expect(ids).toContain("intervention-drafts-outline");
    expect(ids).toContain("intervention-drafts-points");
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("stays out of DYNAMIC_TILE_SOURCE_IDS -- it is never Martin-served", () => {
    expect(DYNAMIC_TILE_SOURCE_IDS_FOR_TEST).not.toContain(INTERVENTION_DRAFTS_SOURCE_ID);
  });

  it("is declared in the style's sources map as an empty geojson source", () => {
    const sources = getSources();
    expect(sources[INTERVENTION_DRAFTS_SOURCE_ID]).toEqual({
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
  });
});

describe("intervention-drafts overlay: distinct, category-differentiated styling", () => {
  it("paints a reduced opacity relative to the published fill/points layers", () => {
    expect(interventionDraftsFillLayer.paint?.["fill-opacity"]).toBeLessThan(0.4);
    expect(interventionDraftsPointsLayer.paint?.["circle-opacity"]).toBeLessThan(0.9);
  });

  it("draws a dashed outline, unlike a plain solid line", () => {
    expect(interventionDraftsOutlineLayer.paint?.["line-dasharray"]).toBeDefined();
  });

  it("keys color and dash pattern off category so land and air read apart", () => {
    const dasharray = interventionDraftsOutlineLayer.paint?.["line-dasharray"];
    expect(dasharray).toEqual(
      expect.arrayContaining([
        "match",
        ["get", "category"],
        "air",
        ["literal", [1, 1]],
        "land",
        ["literal", [3, 2]],
        ["literal", [3, 2]],
      ])
    );

    // The SHARED expression since the merge -- the same object the three published layers
    // paint with -- so a draft and a published site of one category never read apart by hue.
    // Its unclassified fallback is the map-wide neutral grey, not a category colour.
    const fillColor = interventionDraftsFillLayer.paint?.["fill-color"];
    expect(fillColor).toEqual(INTERVENTION_STATUS_COLOR);
    expect(fillColor).toEqual([
      "case",
      ["==", ["get", "status"], "pending_review"],
      "#f97316",
      ["match", ["get", "category"], "land", "#0d9488", "air", "#7c3aed", UNCLASSIFIED_FILL_COLOR],
    ]);
  });

  it("colors a pending-review submission orange regardless of category", () => {
    for (const paint of [
      interventionDraftsFillLayer.paint?.["fill-color"],
      interventionDraftsOutlineLayer.paint?.["line-color"],
      interventionDraftsPointsLayer.paint?.["circle-color"],
    ]) {
      expect(paint).toEqual(
        expect.arrayContaining(["case", ["==", ["get", "status"], "pending_review"], "#f97316"])
      );
    }
  });

  it("splits polygons from points by geometry-type filter, matching the published interventions split", () => {
    expect(interventionDraftsFillLayer.filter).toEqual(["!=", ["geometry-type"], "Point"]);
    expect(interventionDraftsOutlineLayer.filter).toEqual(["!=", ["geometry-type"], "Point"]);
    expect(interventionDraftsPointsLayer.filter).toEqual(["==", ["geometry-type"], "Point"]);
  });

  it("starts hidden, like every other toggleable style layer", () => {
    expect(interventionDraftsFillLayer.layout).toEqual({ visibility: "none" });
    expect(interventionDraftsOutlineLayer.layout).toEqual({ visibility: "none" });
    expect(interventionDraftsPointsLayer.layout).toEqual({ visibility: "none" });
  });
});
