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

describe("intervention-drafts overlay: additive registration", () => {
  it("registers a new toggle alongside the existing Martin-backed interventions entry, not in place of it", () => {
    const interventions = LAYER_REGISTRY.interventions;
    const drafts = LAYER_REGISTRY["intervention-drafts"];

    // The existing Martin entry is untouched.
    expect(interventions.styleLayerIds).toEqual([
      "interventions",
      "interventions-outline",
      "interventions-points",
    ]);

    expect(drafts.renderKind).toBe("style");
    expect(drafts.styleLayerIds).toEqual([
      "intervention-drafts-fill",
      "intervention-drafts-outline",
      "intervention-drafts-points",
    ]);
    expect(styleBackedLayerEntries().map((e) => e.toggleId)).toContain("intervention-drafts");
  });

  it("carries a tooltip description distinct from its short label", () => {
    const drafts = LAYER_REGISTRY["intervention-drafts"];
    expect(drafts.description).toBeDefined();
    expect(drafts.description).not.toBe(drafts.label);
    expect(drafts.description).toContain("published interventions");
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

    const fillColor = interventionDraftsFillLayer.paint?.["fill-color"];
    expect(fillColor).toEqual(["match", ["get", "category"], "land", "#0d9488", "air", "#7c3aed", "#0d9488"]);
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
