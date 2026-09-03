import { describe, expect, it } from "vitest";
import { LAYER_TOGGLE_IDS, isLayerToggleId } from "@/lib/map/layer-registry";
import { ZOOM_TIERS } from "@/lib/map/zoom-tiers";
import {
  LAYER_RENDER_CONTRACT,
  PerimeterMisrepresentationError,
  SUPPORT_KINDS,
  ZOOM_BANDS,
  ZOOM_TIER_BANDS,
  assertNotPerimeter,
  isAggregateSupportKind,
  isFormPermitted,
  layerRenderContractEntries,
  permittedFormsFor,
  renderClassOf,
  resolveZoomBand,
  zoomBandForTier,
  type SupportKind,
} from "@/lib/map/layer-render-contract";

describe("the contract covers exactly the registry", () => {
  it("gives every registry layer an entry", () => {
    for (const toggleId of LAYER_TOGGLE_IDS) {
      expect(LAYER_RENDER_CONTRACT[toggleId], `no render contract for ${toggleId}`).toBeDefined();
    }
  });

  it("names only real registry layers", () => {
    for (const layerId of Object.keys(LAYER_RENDER_CONTRACT)) {
      expect(isLayerToggleId(layerId), `${layerId} is not a registry layer`).toBe(true);
    }
  });

  it("keys every entry by its own layerId", () => {
    for (const [key, entry] of Object.entries(LAYER_RENDER_CONTRACT)) {
      expect(entry.layerId).toBe(key);
    }
  });

  it("holds one entry per registry layer and no more", () => {
    expect(Object.keys(LAYER_RENDER_CONTRACT).length).toBe(LAYER_TOGGLE_IDS.length);
  });
});

describe("every band of every layer is renderable", () => {
  it("permits at least one form in each band", () => {
    for (const entry of layerRenderContractEntries()) {
      for (const band of ZOOM_BANDS) {
        expect(
          entry.permittedForms[band].length,
          `${entry.layerId} permits nothing at ${band} zoom`
        ).toBeGreaterThan(0);
      }
    }
  });

  it("permits only forms from the closed support vocabulary", () => {
    for (const entry of layerRenderContractEntries()) {
      for (const band of ZOOM_BANDS) {
        for (const form of entry.permittedForms[band]) {
          expect(SUPPORT_KINDS, `${entry.layerId}/${band} names ${form}`).toContain(form);
        }
      }
    }
  });
});

// Stated as the STRONG rule -- no band offers `raw_point` beside ANY aggregate form -- and kept
// that way deliberately on 2026-09-02. Fire's detail band needed `aggregate_cell`, and the two
// ways to get there were widening this rule to exempt `aggregate_cell`, or dropping `raw_point`
// from a lane that publishes no raw rung. The second was chosen: this rule is what keeps a summary
// from being captioned as an observation, so the contract narrowed rather than the invariant.
describe("a band never mixes raw observations with aggregates", () => {
  it("never permits raw_point beside an aggregate form in the same band", () => {
    for (const entry of layerRenderContractEntries()) {
      for (const band of ZOOM_BANDS) {
        const forms = entry.permittedForms[band];
        if (!forms.includes("raw_point")) continue;
        const aggregates = forms.filter(isAggregateSupportKind);
        expect(
          aggregates,
          `${entry.layerId} offers raw points and ${aggregates.join("/")} at ${band} zoom, so a ` +
            `reader cannot tell an observation from a summary`
        ).toEqual([]);
      }
    }
  });
});

describe("fire detections are never a perimeter", () => {
  it("permits no native_polygon form at any band", () => {
    for (const band of ZOOM_BANDS) {
      expect(LAYER_RENDER_CONTRACT.fire.permittedForms[band]).not.toContain("native_polygon");
    }
  });

  it("is classified as an event_point product", () => {
    expect(renderClassOf("fire")).toBe("event_point");
  });

  it("throws when asked to render native polygon geometry", () => {
    expect(() => assertNotPerimeter("fire", "native_polygon")).toThrow(
      PerimeterMisrepresentationError
    );
  });

  it("throws for every other event_point layer too", () => {
    for (const entry of layerRenderContractEntries()) {
      if (entry.renderClass !== "event_point") continue;
      expect(() => assertNotPerimeter(entry.layerId, "native_polygon")).toThrow(
        PerimeterMisrepresentationError
      );
    }
  });

  it("does not throw for its own aggregate cell form", () => {
    expect(() => assertNotPerimeter("fire", "aggregate_cell")).not.toThrow();
  });

  // FIRMS publishes no raw rung: z13 rows are cells, so the cell IS the detail form here. The
  // spec's "raw source points" detail column is about the layers its own carve-out calls genuine
  // stations -- water, weather, sensors -- and fire is not one of them.
  it("draws cells rather than raw points at its detail band", () => {
    expect(LAYER_RENDER_CONTRACT.fire.permittedForms.detail).toEqual(["aggregate_cell"]);
  });

  it("leaves every other event_point layer on raw points at detail", () => {
    for (const entry of layerRenderContractEntries()) {
      if (entry.renderClass !== "event_point" || entry.layerId === "fire") continue;
      expect(entry.permittedForms.detail, `${entry.layerId} detail band`).toEqual(["raw_point"]);
    }
  });
});

describe("burn severity is a perimeter", () => {
  it("passes assertNotPerimeter with native polygon geometry", () => {
    expect(() => assertNotPerimeter("burn-severity", "native_polygon")).not.toThrow();
  });

  it("renders as native geometry at every band", () => {
    for (const band of ZOOM_BANDS) {
      expect(LAYER_RENDER_CONTRACT["burn-severity"].permittedForms[band]).toEqual([
        "native_polygon",
      ]);
    }
  });
});

describe("vegetation keeps its measured support", () => {
  it("declares 0.25 degrees", () => {
    expect(LAYER_RENDER_CONTRACT.vegetation.declaredSupportDegrees).toBe(0.25);
  });

  it("is a continuous field", () => {
    expect(renderClassOf("vegetation")).toBe("continuous_field");
  });

  it("draws discrete cells at every band and never a smoothed surface", () => {
    for (const band of ZOOM_BANDS) {
      expect(LAYER_RENDER_CONTRACT.vegetation.permittedForms[band]).toEqual(["tessellated_cell"]);
    }
  });

  // The shipped renderer draws centre circles, which is `raw_point`. Recorded, never permitted:
  // widening the band would make the contract describe the code instead of governing it.
  it("records the centre-circle renderer as a deviation rather than permitting it", () => {
    const deviation = LAYER_RENDER_CONTRACT.vegetation.shippedDeviation;
    expect(deviation?.form).toBe("raw_point");
    for (const band of ZOOM_BANDS) {
      expect(LAYER_RENDER_CONTRACT.vegetation.permittedForms[band]).not.toContain("raw_point");
    }
  });
});

describe("a recorded deviation is always attributable", () => {
  it("names an owner and a date on every deviation", () => {
    for (const entry of layerRenderContractEntries()) {
      const deviation = entry.shippedDeviation;
      if (deviation === undefined) continue;
      expect(deviation.owner, `${entry.layerId} deviation owner`).toMatch(/\S/);
      expect(deviation.recordedOn, `${entry.layerId} deviation date`).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(deviation.note, `${entry.layerId} deviation note`).toMatch(/\S/);
    }
  });

  it("only ever names a form the closed vocabulary holds", () => {
    for (const entry of layerRenderContractEntries()) {
      if (entry.shippedDeviation === undefined) continue;
      expect(SUPPORT_KINDS).toContain(entry.shippedDeviation.form);
    }
  });
});

describe("each physical rung maps to exactly one band", () => {
  it("assigns a band to every published tier", () => {
    for (const tier of ZOOM_TIERS) {
      expect(ZOOM_BANDS, `z${tier} has no band`).toContain(ZOOM_TIER_BANDS[tier]);
    }
  });

  it("assigns exactly one band per tier", () => {
    for (const tier of ZOOM_TIERS) {
      const bands = ZOOM_BANDS.filter((band) => zoomBandForTier(tier) === band);
      expect(bands.length, `z${tier} resolves to ${bands.length} bands`).toBe(1);
    }
  });

  it("leaves no band without a rung to render it", () => {
    const covered = ZOOM_TIERS.map((tier) => zoomBandForTier(tier));
    for (const band of ZOOM_BANDS) {
      expect(covered, `no published rung renders the ${band} band`).toContain(band);
    }
  });

  it("puts both low rungs in coarse, z9 in middle and z13 in detail", () => {
    expect(ZOOM_TIER_BANDS).toEqual({ 0: "coarse", 5: "coarse", 9: "middle", 13: "detail" });
  });

  it("resolves a live map zoom through the same ladder", () => {
    expect(resolveZoomBand(3)).toBe("coarse");
    expect(resolveZoomBand(6.5)).toBe("coarse");
    expect(resolveZoomBand(9)).toBe("middle");
    expect(resolveZoomBand(12.999)).toBe("middle");
    expect(resolveZoomBand(13)).toBe("detail");
    expect(resolveZoomBand(18)).toBe("detail");
  });
});

describe("the zoom-aware helpers agree with the table", () => {
  it("returns the detail forms at a detail zoom", () => {
    expect(permittedFormsFor("fire", 14)).toEqual(["aggregate_cell"]);
  });

  it("returns the aggregate forms at a coarse zoom", () => {
    expect(permittedFormsFor("fire", 4)).toEqual(["aggregate_cell", "heatmap", "cluster"]);
  });

  it("answers isFormPermitted from the same table", () => {
    const cases: readonly { zoom: number; form: SupportKind; permitted: boolean }[] = [
      { zoom: 4, form: "heatmap", permitted: true },
      { zoom: 4, form: "raw_point", permitted: false },
      { zoom: 14, form: "raw_point", permitted: false },
      { zoom: 14, form: "aggregate_cell", permitted: true },
      { zoom: 14, form: "cluster", permitted: false },
    ];
    for (const { zoom, form, permitted } of cases) {
      expect(isFormPermitted("fire", zoom, form), `fire/${form} at z${zoom}`).toBe(permitted);
    }
  });

  it("keeps continuous fields filled rather than raw at every band", () => {
    for (const entry of layerRenderContractEntries()) {
      if (entry.renderClass !== "continuous_field") continue;
      for (const band of ZOOM_BANDS) {
        expect(
          entry.permittedForms[band],
          `${entry.layerId} draws raw points at ${band} zoom`
        ).not.toContain("raw_point");
      }
    }
  });

  it("gives unavailable products exactly the unavailable form", () => {
    for (const entry of layerRenderContractEntries()) {
      if (entry.renderClass !== "reference_or_unavailable") continue;
      for (const band of ZOOM_BANDS) {
        expect(entry.permittedForms[band]).toEqual(["unavailable"]);
      }
    }
  });
});
