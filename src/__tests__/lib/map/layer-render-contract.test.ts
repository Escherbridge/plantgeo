import { describe, expect, it } from "vitest";
import { LAYER_TOGGLE_IDS, isLayerToggleId } from "@/lib/map/layer-registry";
import {
  LANE_BASE_LATTICES,
  latticeCellIndex,
  latticeCellSpan,
  servedCellLattice,
  tessellatedCellPolygon,
  ZOOM_TIERS,
  type CellLaneId,
  type ZoomTier,
} from "@/lib/map/zoom-tiers";
import {
  LAYER_RENDER_CONTRACT,
  PerimeterMisrepresentationError,
  SUPPORT_KINDS,
  ZOOM_BANDS,
  ZOOM_TIER_BANDS,
  assertNotPerimeter,
  isAggregateSupportKind,
  isFormPermitted,
  isFormPermittedForTier,
  layerRenderContractEntries,
  permittedFormsFor,
  permittedFormsForTier,
  renderClassOf,
  resolveZoomBand,
  supportCellPolygon,
  zoomBandForTier,
  type AggregateEnvelopeSupport,
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

  // The centre-circle renderer was recorded as a `raw_point` deviation on 2026-09-02 and closed
  // the same day by slice m3: `presentParquetVegetation` now emits the declared square and
  // `VegetationLayer` has no circle layer left. The deviation is gone because the gap is, not
  // because the band was widened -- `raw_point` is still permitted at no band.
  it("no longer records a deviation, and still permits no raw point at any band", () => {
    expect(LAYER_RENDER_CONTRACT.vegetation.shippedDeviation).toBeUndefined();
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

/** A minimal envelope; every test below overrides only the fields it is about. */
function envelope(overrides: Partial<AggregateEnvelopeSupport> = {}): AggregateEnvelopeSupport {
  return {
    zoomTier: 9,
    supportKind: "aggregate_cell",
    supportId: "z09:-116.25:43.5",
    origin: "cell_origin",
    cellWidthDegrees: 0.25,
    cellHeightDegrees: 0.25,
    aggregationMethod: "count",
    contributorCount: 4,
    provenance: {
      sourceLayer: "fire_detections",
      observedDay: "2026-08-28",
      newestObservedAt: "2026-08-28T19:12:00Z",
      attribution: "NASA FIRMS",
    },
    ...overrides,
  };
}

describe("the tier-keyed helpers answer the same table as the zoom-keyed ones", () => {
  it("returns the detail forms for the detail rung", () => {
    expect(permittedFormsForTier("fire", 13)).toEqual(permittedFormsFor("fire", 13));
    expect(permittedFormsForTier("fire", 13)).toEqual(["aggregate_cell"]);
  });

  it("puts both low rungs on the same coarse answer", () => {
    expect(permittedFormsForTier("water", 0)).toEqual(permittedFormsForTier("water", 5));
    expect(permittedFormsForTier("water", 9)).toEqual(["aggregate_cell", "heatmap", "cluster"]);
  });

  it("permits exactly the form each renderer draws", () => {
    // The three forms slice m3 actually paints. A renderer whose form left its band would have
    // to change the contract to get here, which is the whole point of the table.
    for (const tier of ZOOM_TIERS) {
      expect(isFormPermittedForTier("fire", tier, "aggregate_cell"), `fire z${tier}`).toBe(true);
      expect(
        isFormPermittedForTier("vegetation", tier, "tessellated_cell"),
        `vegetation z${tier}`
      ).toBe(true);
    }
    expect(isFormPermittedForTier("water", 5, "aggregate_cell")).toBe(true);
    expect(isFormPermittedForTier("water", 13, "raw_point")).toBe(true);
    // ...and the one that would be a lie at the detail rung.
    expect(isFormPermittedForTier("water", 13, "aggregate_cell")).toBe(false);
  });
});

describe("supportCellPolygon draws only what the envelope declares", () => {
  it("builds the square from the declared origin and size", () => {
    expect(supportCellPolygon(-116.25, 43.5, envelope())).toEqual({
      type: "Polygon",
      coordinates: [
        [
          [-116.25, 43.5],
          [-116, 43.5],
          [-116, 43.75],
          [-116.25, 43.75],
          [-116.25, 43.5],
        ],
      ],
    });
  });

  it("offsets by half a cell when the coordinates locate the centre", () => {
    const centred = supportCellPolygon(-116.125, 43.625, envelope({ origin: "cell_center" }));
    // The same square as above: a centre at -116.125/43.625 is the middle of the corner cell.
    // Getting this branch wrong shifts a whole field by half a cell.
    expect(centred?.coordinates[0][0]).toEqual([-116.25, 43.5]);
    expect(centred?.coordinates[0][2]).toEqual([-116, 43.75]);
  });

  it("gives neighbouring cells bit-identical shared edges", () => {
    // 0.1 is the size that exposes the bug: 0.2 + 0.1 is 0.30000000000000004, and the cell
    // starting at 0.3 would begin somewhere else entirely if east were computed by addition.
    const size = { cellWidthDegrees: 0.1, cellHeightDegrees: 0.1 };
    const left = supportCellPolygon(0.2, 0.2, envelope(size));
    const right = supportCellPolygon(0.3, 0.2, envelope(size));
    const leftEast = left?.coordinates[0][1][0];
    const rightWest = right?.coordinates[0][0][0];
    expect(leftEast).toBe(rightWest);
    expect(Object.is(leftEast, rightWest)).toBe(true);
  });

  it("keeps an off-lattice cell where the producer put it rather than snapping it", () => {
    // A corner 0.05 off its own lattice is a phase the producer chose, not float noise.
    // Snapping it would move the cell by half its width, which reads as a registration error.
    const offLattice = supportCellPolygon(-116.2, 43.6, envelope());
    expect(offLattice?.coordinates[0][0]).toEqual([-116.2, 43.6]);
  });

  it("declares no footprint at all rather than guessing one", () => {
    for (const missing of [
      { cellWidthDegrees: undefined },
      { cellHeightDegrees: undefined },
      { cellWidthDegrees: 0 },
      { cellHeightDegrees: Number.NaN },
    ] as Partial<AggregateEnvelopeSupport>[]) {
      expect(supportCellPolygon(-116.25, 43.5, envelope(missing)), JSON.stringify(missing)).toBeNull();
    }
    expect(supportCellPolygon(Number.NaN, 43.5, envelope())).toBeNull();
  });

  it("takes a declared corner verbatim instead of re-snapping it to its own lattice", () => {
    // A corner on a HALF-OFFSET phase, which is what the quarter-degree base lattices actually
    // publish. Re-snapping it onto a lattice anchored at multiples of 0.25 -- what this builder
    // did before `cellOriginDegrees` existed -- moves the cell by half its width, which reads on
    // screen as a registration error rather than as a bug.
    const declared = supportCellPolygon(
      -116,
      43.7,
      envelope({ origin: "cell_center", cellOriginDegrees: [-116.125, 43.625] })
    );

    expect(declared?.coordinates[0][0]).toEqual([-116.125, 43.625]);
    expect(declared?.coordinates[0][2]).toEqual([-115.875, 43.875]);
  });

  it("ignores the anchor entirely once a corner is declared", () => {
    // The declared corner is the whole answer, so a nonsense anchor beside it changes nothing.
    // That is what makes the field usable: the serving side has already decided which cell this
    // row is in, and nothing here re-decides it.
    const support = envelope({ cellOriginDegrees: [-116.25, 43.5] });
    expect(supportCellPolygon(0, 0, support)).toEqual(supportCellPolygon(-116.25, 43.5, support));
  });
});

/**
 * The seam this contract and `zoom-tiers.ts` used to fall through.
 *
 * `tessellatedCellPolygon` is what the SERVING side draws for a row, from the lane and the rung.
 * `supportCellPolygon` is what a RENDERER draws for the same row, from the envelope alone. They
 * are two callers of one span builder now, and these cases are the proof: for every lane and every
 * rung, the two must return the same ring. Before `cellOriginDegrees` carried the lattice phase,
 * vegetation at z9 and z5 disagreed by up to half a cell -- a 0.25-degree base grain kept across
 * the ladder's 0.01 and 0.2 grids, on a phase the wire never mentioned.
 */
describe("the renderer's square is the server's square", () => {
  /** The envelope `cellSupport` builds for one row of one lane at one rung. */
  function servedEnvelope(
    lane: CellLaneId,
    zoomTier: ZoomTier,
    longitude: number,
    latitude: number
  ): AggregateEnvelopeSupport {
    const lattice = servedCellLattice(zoomTier, LANE_BASE_LATTICES[lane]);
    return {
      zoomTier,
      supportKind: "tessellated_cell",
      supportId: `${lane}:${zoomTier}:${longitude}:${latitude}`,
      origin: lattice.origin,
      cellWidthDegrees: lattice.cellSizeDegrees,
      cellHeightDegrees: lattice.cellSizeDegrees,
      cellOriginDegrees: [
        latticeCellSpan(latticeCellIndex(longitude, lattice), lattice)[0],
        latticeCellSpan(latticeCellIndex(latitude, lattice), lattice)[0],
      ],
      aggregationMethod: "mean",
      contributorCount: 1,
      provenance: {
        sourceLayer: lane,
        observedDay: "2026-09-02",
        newestObservedAt: null,
        attribution: "test",
      },
    };
  }

  it("agrees with the serving builder for vegetation at z9 and z5, the case that diverged", () => {
    // -116.125 is a served vegetation coordinate on the quarter-degree lattice, and both rungs
    // KEEP that grain: the ladder's 0.01 and 0.2 grids are finer than the ground the lane
    // measured, so re-flooring onto them would merge nothing and paint a fictitious footprint.
    const longitude = -116.125;
    const latitude = 43.625;
    for (const zoomTier of [9, 5] as ZoomTier[]) {
      const lattice = servedCellLattice(zoomTier, LANE_BASE_LATTICES.vegetation);
      expect(
        supportCellPolygon(
          longitude,
          latitude,
          servedEnvelope("vegetation", zoomTier, longitude, latitude)
        ),
        `vegetation z${zoomTier}`
      ).toEqual(tessellatedCellPolygon(longitude, latitude, lattice));
    }
  });

  it("agrees with the serving builder for every cell-bearing lane at every rung", () => {
    const lanes = Object.keys(LANE_BASE_LATTICES) as CellLaneId[];
    for (const lane of lanes) {
      for (const zoomTier of ZOOM_TIERS) {
        const lattice = servedCellLattice(zoomTier, LANE_BASE_LATTICES[lane]);
        // A gauge and a weather station have no base grain, so their detail rung is a point with
        // no cell to compare. Every other lane/rung pair has one.
        if (lattice.cellSizeDegrees === 0) continue;
        const [longitude, latitude] = latticeCellSpan(
          latticeCellIndex(-116.125, lattice),
          lattice
        );
        expect(
          supportCellPolygon(
            longitude,
            latitude,
            servedEnvelope(lane, zoomTier, longitude, latitude)
          ),
          `${lane} z${zoomTier}`
        ).toEqual(tessellatedCellPolygon(longitude, latitude, lattice));
      }
    }
  });

  it("gives fire's z5 cells bit-identical edges across a whole run of neighbours", () => {
    const lattice = servedCellLattice(5, LANE_BASE_LATTICES["fire-detections"]);
    const rings = Array.from({ length: 12 }, (_unused, step) => {
      const [longitude] = latticeCellSpan(
        latticeCellIndex(-116.2, lattice) + step,
        lattice
      );
      const [latitude] = latticeCellSpan(latticeCellIndex(43.4, lattice), lattice);
      return supportCellPolygon(
        longitude,
        latitude,
        servedEnvelope("fire-detections", 5, longitude, latitude)
      )!.coordinates[0];
    });

    for (let index = 1; index < rings.length; index++) {
      const west = rings[index - 1];
      const east = rings[index];
      // `Object.is` and not a tolerance: a one-ULP disagreement is invisible to `toBeCloseTo`
      // and is exactly the hairline of map background the spec's shared-boundary gate forbids.
      expect(Object.is(west[1][0], east[0][0]), `cell ${index} south edge`).toBe(true);
      expect(Object.is(west[2][0], east[3][0]), `cell ${index} north edge`).toBe(true);
    }
  });
});

describe("supportCellPolygon ring shape", () => {
  it("closes the ring and winds it counter-clockwise", () => {
    const ring: GeoJSON.Position[] =
      supportCellPolygon(-116.25, 43.5, envelope())?.coordinates[0] ?? [];
    expect(ring).toHaveLength(5);
    expect(ring[0]).toEqual(ring[4]);
    // Shoelace: positive area means counter-clockwise, which is what RFC 7946 asks of an
    // exterior ring.
    const area = ring
      .slice(0, -1)
      .reduce(
        (sum, [x, y], index) => {
          const [nextX, nextY] = ring[index + 1];
          return sum + (x * nextY - nextX * y);
        },
        0
      );
    expect(area).toBeGreaterThan(0);
  });
});
