import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { FireLayer } from "@/components/map/layers/FireLayer";
import { WaterLayer } from "@/components/map/layers/WaterLayer";
import { VegetationLayer } from "@/components/map/layers/VegetationLayer";
import {
  presentParquetFireDetections,
  FIRE_CELL_DRAWN_FORM,
} from "@/lib/environmental/parquet-fire-presentation";
import {
  presentParquetVegetation,
  presentParquetWater,
  VEGETATION_CELL_DRAWN_FORM,
} from "@/lib/environmental/parquet-presentation";
import type {
  ParquetBrowserFireDetectionCell,
  ParquetBrowserFireWindow,
} from "@/lib/environmental/parquet-fire-presentation";
import type {
  ParquetBrowserReaderResult,
  ParquetBrowserVegetationObservation,
  ParquetBrowserVegetationWindow,
  ParquetBrowserWaterGauge,
} from "@/lib/environmental/parquet-presentation";
import {
  fireCellCaptionText,
  fireDetectionCellLines,
  FIRE_CELL_NOT_A_PERIMETER_NOTE,
} from "@/lib/map/fire-cell-caption";
import {
  assertNotPerimeter,
  isFormPermittedForTier,
  type AggregateEnvelopeSupport,
} from "@/lib/map/layer-render-contract";
import {
  cellSizeDegreesForTier,
  LANE_BASE_LATTICES,
  latticeCellIndex,
  latticeCellSpan,
  servedCellLattice,
  type CellLaneId,
  type ZoomTier,
} from "@/lib/map/zoom-tiers";
import { useVegetationStore } from "@/stores/vegetation-store";

/**
 * What a reader sees at each band, asserted on the geometry that actually reaches the map and
 * on the words drawn beside it.
 *
 * The 2026-09-01 production assessment found raw fire dots at continental zoom, which read as a
 * scattering of individual fires where the data supports only a density. The fix is not "draw
 * squares": it is that every drawn square is one the SERVER declared -- an origin, a size and a
 * contributor count on the envelope -- and that nothing in the fire, water or vegetation layers
 * can produce a polygon the envelope did not describe. These tests hold both halves: the shape
 * per band, and the refusal to invent one.
 */

/** The parts of maplibre-gl these three components touch, with every added layer kept. */
function createFakeMap() {
  const listeners = new Map<string, Set<(...args: unknown[]) => void>>();
  const sources = new Map<string, { data: unknown }>();
  const layers = new Map<string, Record<string, unknown>>();
  let styleLoaded = true;

  function on(type: string, a: unknown, b?: unknown) {
    const handler = (typeof b === "function" ? b : a) as (...args: unknown[]) => void;
    if (!listeners.has(type)) listeners.set(type, new Set());
    listeners.get(type)!.add(handler);
  }
  function off(type: string, a: unknown, b?: unknown) {
    const handler = (typeof b === "function" ? b : a) as (...args: unknown[]) => void;
    listeners.get(type)?.delete(handler);
  }

  return {
    on,
    off,
    isStyleLoaded: () => styleLoaded,
    setStyleLoaded(value: boolean) {
      styleLoaded = value;
    },
    emit(type: string) {
      for (const handler of Array.from(listeners.get(type) ?? [])) handler();
    },
    getStyle: () => ({ layers: [] }),
    addSource: (id: string, options: { data?: unknown }) => {
      sources.set(id, { data: options.data });
    },
    getSource: (id: string) => {
      const entry = sources.get(id);
      if (!entry) return undefined;
      return {
        ...entry,
        setData: (data: unknown) => {
          entry.data = data;
        },
      };
    },
    removeSource: (id: string) => sources.delete(id),
    addLayer: (spec: Record<string, unknown>) => {
      layers.set(spec.id as string, spec);
    },
    getLayer: (id: string) => (layers.has(id) ? { id } : undefined),
    removeLayer: (id: string) => layers.delete(id),
    setPaintProperty: vi.fn(),
    setLayoutProperty: vi.fn(),
    /** The GeoJSON a source is holding right now -- what the GPU would actually draw. */
    dataOf(id: string): GeoJSON.FeatureCollection {
      return sources.get(id)?.data as GeoJSON.FeatureCollection;
    },
    layerSpec(id: string): Record<string, unknown> | undefined {
      return layers.get(id);
    },
    hasLayer: (id: string) => layers.has(id),
  };
}

type FakeMap = ReturnType<typeof createFakeMap>;

function asMap(fakeMap: FakeMap): MapLibreMap {
  return fakeMap as unknown as MapLibreMap;
}

/**
 * The cell a fire rung is actually served on: the ladder's grid, or the lane's own base grain at
 * the base rung, which is where `cellSizeDegreesForTier` returns null because a base rung is not
 * derived. Read from the two published tables rather than hard-coded, so a fixture cannot
 * describe a lattice the writer never wrote.
 */
function fireCellDegreesAt(zoomTier: ZoomTier): number {
  return (
    cellSizeDegreesForTier(zoomTier) ?? LANE_BASE_LATTICES["fire-detections"].cellSizeDegrees
  );
}

/**
 * The south-west corner `cellSupport` snaps a served coordinate to, on a named lane's own rung.
 *
 * Every envelope below carries one, because the reader does: it is what makes the client's square
 * and the server's square the same square rather than two squares that happen to agree wherever
 * the lattice phase is zero. A fixture that omitted it would exercise the legacy fallback path and
 * quietly stop testing the wire shape the readers actually emit.
 */
function cellOriginAt(
  lane: CellLaneId,
  zoomTier: ZoomTier,
  longitude: number,
  latitude: number
): readonly [number, number] {
  const lattice = servedCellLattice(zoomTier, LANE_BASE_LATTICES[lane]);
  return [
    latticeCellSpan(latticeCellIndex(longitude, lattice), lattice)[0],
    latticeCellSpan(latticeCellIndex(latitude, lattice), lattice)[0],
  ];
}

/** One fire cell's envelope at a named position, exactly as `cellSupport` builds it. */
function fireEnvelopeAt(
  zoomTier: ZoomTier,
  longitude: number,
  latitude: number,
  overrides: Partial<AggregateEnvelopeSupport> = {}
): AggregateEnvelopeSupport {
  return {
    zoomTier,
    supportKind: "aggregate_cell",
    supportId: `z${zoomTier}:cell`,
    origin: "cell_origin",
    cellWidthDegrees: fireCellDegreesAt(zoomTier),
    cellHeightDegrees: fireCellDegreesAt(zoomTier),
    cellOriginDegrees: cellOriginAt("fire-detections", zoomTier, longitude, latitude),
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

/** The default fire cell's envelope: the position `fireCell` uses, at the requested rung. */
function envelope(
  zoomTier: ZoomTier,
  overrides: Partial<AggregateEnvelopeSupport> = {}
): AggregateEnvelopeSupport {
  return fireEnvelopeAt(zoomTier, FIRE_CELL_LONGITUDE, FIRE_CELL_LATITUDE, overrides);
}

/**
 * One streamflow envelope, off the water lane rather than off the fire one.
 *
 * The lane matters even though the two agree on cell SIZE at every derived rung: a gauge has no
 * base grain (`LANE_BASE_LATTICES["water-gauges"].cellSizeDegrees` is 0), so its detail rung is a
 * raw point with no footprint where fire's is a 0.005-degree cell. Borrowing the fire envelope
 * here made the z13 fixture claim a cell the streamflow lane never publishes.
 */
function waterEnvelope(
  zoomTier: ZoomTier,
  longitude: number,
  latitude: number,
  overrides: Partial<AggregateEnvelopeSupport> = {}
): AggregateEnvelopeSupport {
  const rawPoint = zoomTier === 13;
  return {
    zoomTier,
    supportKind: rawPoint ? "raw_point" : "aggregate_cell",
    supportId: `water:z${zoomTier}:${longitude}:${latitude}`,
    origin: rawPoint ? "cell_center" : "cell_origin",
    // A raw point declares no footprint at all: a gauge is a station, and a square drawn round it
    // would claim ground nobody measured.
    ...(rawPoint
      ? {}
      : {
          cellWidthDegrees: cellSizeDegreesForTier(zoomTier) ?? 0,
          cellHeightDegrees: cellSizeDegreesForTier(zoomTier) ?? 0,
          cellOriginDegrees: cellOriginAt("water-gauges", zoomTier, longitude, latitude),
        }),
    // `mean`, never `count`: what a coarse streamflow cell carries is mean discharge over the
    // gauges the derivation floored into it, and how many contributed is `contributorCount`.
    aggregationMethod: rawPoint ? "none" : "mean",
    contributorCount: 1,
    provenance: {
      sourceLayer: "water-gauges",
      observedDay: "2026-08-28",
      newestObservedAt: "2026-08-28T12:00:00Z",
      attribution: "U.S. Geological Survey NWIS",
    },
    ...overrides,
  };
}

const FIRE_CELL_LONGITUDE = -116;
const FIRE_CELL_LATITUDE = 43;

function fireCell(
  overrides: Partial<ParquetBrowserFireDetectionCell> = {}
): ParquetBrowserFireDetectionCell {
  return {
    longitude: FIRE_CELL_LONGITUDE,
    latitude: FIRE_CELL_LATITUDE,
    observedDay: "2026-08-28",
    detectionCount: 40,
    frpSum: 120.5,
    frpObservationCount: 4,
    highConfidenceDetectionCount: 2,
    newestObservedAt: "2026-08-28T19:12:00Z",
    support: envelope(5),
    ...overrides,
  };
}

function readyFireWindow(
  cells: ParquetBrowserFireDetectionCell[]
): ParquetBrowserReaderResult<ParquetBrowserFireWindow> {
  return {
    state: "ready",
    requestedDay: "2026-08-28",
    servedDay: "2026-08-28",
    truncated: false,
    data: { firstDay: "2026-08-28", lastDay: "2026-08-28", cells },
  };
}

function waterRow(overrides: Partial<ParquetBrowserWaterGauge>): ParquetBrowserWaterGauge {
  return {
    siteNumber: "13172500",
    observedAt: "2026-08-28T12:00:00Z",
    observedDay: "2026-08-28",
    siteName: "Boise River",
    latitude: 43,
    longitude: -116,
    flowCfs: 500,
    percentile: null,
    condition: "normal",
    trend: null,
    source: "USGS NWIS",
    geometryLinked: true,
    dataAvailableAt: null,
    ingestedAt: "2026-08-28T12:05:00Z",
    support: waterEnvelope(13, -116, 43, { supportId: "13172500" }),
    ...overrides,
  };
}

function readyWaterWindow(
  rows: ParquetBrowserWaterGauge[]
): ParquetBrowserReaderResult<readonly ParquetBrowserWaterGauge[]> {
  return {
    state: "ready",
    requestedDay: "2026-08-28",
    servedDay: "2026-08-28",
    truncated: false,
    data: rows,
  };
}

/**
 * The vegetation lane's fixed 0.25-degree support, at whatever rung it is served from.
 *
 * `tessellated_cell` and `mean`, matching `getParquetVegetation`: the lane's cell is the ground
 * this platform measured, and `LAYER_RENDER_CONTRACT.vegetation` permits that form and no other at
 * every band. The z9 rung KEEPS the quarter-degree grain rather than re-flooring onto the ladder's
 * 0.01 grid, which is why the corner has to come from `servedCellLattice` rather than from a
 * hand-written offset.
 *
 * The coordinates every caller passes are REAL centroids of the lane's lattice -- odd multiples of
 * 0.125, because `ingest/vegetation.py:344-347` centres each cell a half step above `row * 0.25` --
 * so the square built here is the one that actually holds the measurement.
 */
function vegetationSupport(longitude: number, latitude: number): AggregateEnvelopeSupport {
  const lattice = servedCellLattice(9, LANE_BASE_LATTICES.vegetation);
  return {
    zoomTier: 9,
    supportKind: "tessellated_cell",
    supportId: `veg:${longitude}:${latitude}`,
    origin: lattice.origin,
    cellWidthDegrees: lattice.cellSizeDegrees,
    cellHeightDegrees: lattice.cellSizeDegrees,
    cellOriginDegrees: cellOriginAt("vegetation", 9, longitude, latitude),
    aggregationMethod: "mean",
    contributorCount: 1,
    provenance: {
      sourceLayer: "vegetation_index",
      observedDay: "2026-08-27",
      newestObservedAt: "2026-08-28T00:00:00Z",
      attribution: "Sentinel-2",
    },
  };
}

function vegetationObservation(
  longitude: number,
  latitude: number
): ParquetBrowserVegetationObservation {
  return {
    cellId: `veg:${longitude}:${latitude}`,
    gridName: "sentinel-2",
    metricName: "ndvi",
    metricUnit: "index",
    observedDay: "2026-08-27",
    metricValue: 0.61,
    observationChecksum: null,
    dataAvailableAt: "2026-08-28T00:00:00Z",
    releaseCount: 1,
    allowedClientExposure: true,
    longitude,
    latitude,
    support: vegetationSupport(longitude, latitude),
  };
}

function readyVegetationWindow(
  observations: ParquetBrowserVegetationObservation[]
): ParquetBrowserReaderResult<ParquetBrowserVegetationWindow> {
  return {
    state: "ready",
    requestedDay: "2026-08-28",
    servedDay: "2026-08-27",
    truncated: false,
    data: { firstDay: "2026-07-30", lastDay: "2026-08-28", observations },
  };
}

/** The exterior ring of a feature the map is holding, for edge comparisons. */
function ringOf(collection: GeoJSON.FeatureCollection, index: number): GeoJSON.Position[] {
  const geometry = collection.features[index].geometry as GeoJSON.Polygon;
  return geometry.coordinates[0];
}

afterEach(() => {
  vi.clearAllMocks();
  useVegetationStore.setState({ source: "measured" });
});

describe("fire detections at a coarse rung", () => {
  it("draws each cell as the square its envelope declared, filled by detection count", () => {
    const fakeMap = createFakeMap();
    const geojson = presentParquetFireDetections(readyFireWindow([fireCell()]));
    render(<FireLayer map={asMap(fakeMap)} visible geojson={geojson} />);

    const drawn = fakeMap.dataOf("published-fire-source");
    expect(drawn.features[0].geometry.type).toBe("Polygon");

    const fill = fakeMap.layerSpec("published-fire-cells-fill");
    expect(fill?.type).toBe("fill");
    // The polygon half of the source and nothing else, so one rung's cells never draw twice.
    expect(fill?.filter).toEqual(["==", ["geometry-type"], "Polygon"]);
    // Filled by the count the cell aggregates -- the "count/intensity" a coarse-band event
    // aggregate is required to carry, and the only channel a square has.
    expect(JSON.stringify((fill?.paint as Record<string, unknown>)["fill-color"])).toContain(
      "detectionCount"
    );
  });

  it("says in words that the square is a density and not a burned extent", () => {
    const geojson = presentParquetFireDetections(readyFireWindow([fireCell()]));
    const lines = fireDetectionCellLines(
      geojson.features[0].properties as unknown as Record<string, unknown>
    ).map(fireCellCaptionText);

    // Drawn as a filled polygon, a fire cell is in the same visual language as fire-perimeters
    // and burn-severity. Only the caption distinguishes it, so the caption is asserted.
    expect(lines).toContain(FIRE_CELL_NOT_A_PERIMETER_NOTE);
    expect(lines).toContain("Detections: 40");
  });

  it("gives neighbouring cells bit-identical shared edges", () => {
    const size = fireCellDegreesAt(5);
    const fakeMap = createFakeMap();
    const geojson = presentParquetFireDetections(
      readyFireWindow([
        fireCell({
          longitude: FIRE_CELL_LONGITUDE,
          support: fireEnvelopeAt(5, FIRE_CELL_LONGITUDE, FIRE_CELL_LATITUDE, {
            supportId: "west",
          }),
        }),
        fireCell({
          longitude: FIRE_CELL_LONGITUDE + size,
          support: fireEnvelopeAt(5, FIRE_CELL_LONGITUDE + size, FIRE_CELL_LATITUDE, {
            supportId: "east",
          }),
        }),
      ])
    );
    render(<FireLayer map={asMap(fakeMap)} visible geojson={geojson} />);

    const drawn = fakeMap.dataOf("published-fire-source");
    const west = ringOf(drawn, 0);
    const east = ringOf(drawn, 1);
    // Equal to the bit, not merely to a tolerance: a sub-ULP disagreement is what leaves the
    // hairline cracks of map background the spec forbids between neighbouring cells.
    expect(Object.is(west[1][0], east[0][0])).toBe(true);
    expect(Object.is(west[2][0], east[3][0])).toBe(true);
  });
});

describe("fire detections at the detail rung", () => {
  it("keeps the count-scaled dot and labels it with the rung it was aggregated at", () => {
    const fakeMap = createFakeMap();
    const geojson = presentParquetFireDetections(
      readyFireWindow([fireCell({ support: envelope(13) })])
    );
    render(<FireLayer map={asMap(fakeMap)} visible geojson={geojson} />);

    const drawn = fakeMap.dataOf("published-fire-source");
    expect(drawn.features[0].geometry.type).toBe("Point");
    expect(fakeMap.layerSpec("published-fire-circles")?.filter).toEqual([
      "==",
      ["geometry-type"],
      "Point",
    ]);

    const lines = fireDetectionCellLines(
      geojson.features[0].properties as unknown as Record<string, unknown>
    ).map(fireCellCaptionText);
    // The shape changed; the truth claim did not. FIRMS publishes no raw rung, so a z13 dot is
    // still an aggregate and must say so.
    expect(lines).toContain("Aggregated at z13");
    expect(lines).toContain(FIRE_CELL_NOT_A_PERIMETER_NOTE);
  });
});

describe("water gauges across the bands", () => {
  it("draws coarse rungs as declared cells carrying a gauge count", () => {
    const fakeMap = createFakeMap();
    const presented = presentParquetWater(
      readyWaterWindow([
        waterRow({
          siteNumber: null,
          siteName: null,
          support: waterEnvelope(5, -116, 43, { contributorCount: 7 }),
        }),
      ])
    );
    render(
      <WaterLayer map={asMap(fakeMap)} visible gauges={[]} aggregateCells={presented.cells} />
    );

    const drawn = fakeMap.dataOf("water-gauge-cells");
    expect(drawn.features[0].geometry.type).toBe("Polygon");
    expect(drawn.features[0].properties?.gaugeCount).toBe(7);
    expect(fakeMap.layerSpec("water-gauge-cells-fill")?.filter).toEqual([
      "==",
      ["geometry-type"],
      "Polygon",
    ]);
  });

  it("leaves a real z13 gauge a point, with its own identity", () => {
    const fakeMap = createFakeMap();
    const presented = presentParquetWater(readyWaterWindow([waterRow({})]));
    expect(presented.cells).toEqual([]);
    render(
      <WaterLayer
        map={asMap(fakeMap)}
        visible
        gauges={presented.gauges}
        aggregateCells={presented.cells}
      />
    );

    const drawn = fakeMap.dataOf("water-gauges");
    expect(drawn.features[0].geometry.type).toBe("Point");
    expect(drawn.features[0].properties?.siteNo).toBe("13172500");
    // ...and the cell source stays empty, so one rung renders at a time.
    expect(fakeMap.dataOf("water-gauge-cells").features).toEqual([]);
  });
});

describe("measured vegetation", () => {
  it("draws the 0.25-degree cells the platform observed, with shared edges", () => {
    const fakeMap = createFakeMap();
    const geojson = presentParquetVegetation(
      readyVegetationWindow([
        vegetationObservation(-116.125, 43.625),
        vegetationObservation(-115.875, 43.625),
      ])
    );
    render(<VegetationLayer map={asMap(fakeMap)} visible geojson={geojson} />);

    const drawn = fakeMap.dataOf("vegetation-ndvi-cells");
    expect(drawn.features.map((feature) => feature.geometry.type)).toEqual([
      "Polygon",
      "Polygon",
    ]);
    expect(drawn.features[0].properties?.cellWidthDegrees).toBe(0.25);

    const west = ringOf(drawn, 0);
    const east = ringOf(drawn, 1);
    expect(Object.is(west[1][0], east[0][0])).toBe(true);
    expect(Object.is(west[2][0], east[3][0])).toBe(true);
  });

  it("has no circle layer left to draw a centre point with", () => {
    const fakeMap = createFakeMap();
    render(
      <VegetationLayer
        map={asMap(fakeMap)}
        visible
        geojson={presentParquetVegetation(readyVegetationWindow([vegetationObservation(-116.125, 43.625)]))}
      />
    );

    // The recorded `raw_point` deviation, closed structurally: there is no layer that COULD
    // paint a 0.25-degree measurement as a zoom-scaled dot.
    expect(fakeMap.hasLayer("vegetation-ndvi-cells-point")).toBe(false);
    expect(fakeMap.hasLayer("vegetation-ndvi-cells-fill")).toBe(true);
    expect(fakeMap.layerSpec("vegetation-ndvi-cells-fill")?.filter).toEqual([
      "==",
      ["geometry-type"],
      "Polygon",
    ]);
  });

  it("fades the cell outline out across the coarse band so the lattice is not all seams", () => {
    const fakeMap = createFakeMap();
    render(
      <VegetationLayer
        map={asMap(fakeMap)}
        visible
        geojson={presentParquetVegetation(readyVegetationWindow([vegetationObservation(-116.125, 43.625)]))}
      />
    );

    const outline = fakeMap.layerSpec("vegetation-ndvi-cells-outline");
    const opacity = (outline?.paint as Record<string, unknown>)["line-opacity"] as unknown[];
    expect(opacity[0]).toBe("interpolate");
    // Zero at the bottom of the ladder, on at the middle band: at continental zoom a
    // 0.25-degree cell is a few pixels wide and a stroke on every edge is most of the cell.
    expect(opacity).toContain(0);
  });
});

describe("nothing drawn is ever a perimeter", () => {
  it("passes assertNotPerimeter for every form these three layers draw", () => {
    expect(() => assertNotPerimeter("fire", FIRE_CELL_DRAWN_FORM)).not.toThrow();
    expect(() => assertNotPerimeter("water", "aggregate_cell")).not.toThrow();
    expect(() => assertNotPerimeter("water", "raw_point")).not.toThrow();
    expect(() => assertNotPerimeter("vegetation", VEGETATION_CELL_DRAWN_FORM)).not.toThrow();
  });

  it("draws only forms the contract permits at the rung each was served from", () => {
    for (const tier of [0, 5, 9, 13] as ZoomTier[]) {
      expect(isFormPermittedForTier("fire", tier, FIRE_CELL_DRAWN_FORM), `fire z${tier}`).toBe(
        true
      );
      expect(
        isFormPermittedForTier("vegetation", tier, VEGETATION_CELL_DRAWN_FORM),
        `vegetation z${tier}`
      ).toBe(true);
    }
    for (const tier of [0, 5, 9] as ZoomTier[]) {
      expect(isFormPermittedForTier("water", tier, "aggregate_cell"), `water z${tier}`).toBe(
        true
      );
    }
    expect(isFormPermittedForTier("water", 13, "raw_point")).toBe(true);
  });

  it("still throws the moment any of them is handed native polygon geometry", () => {
    // The tripwire has to be live, not merely unexercised: these three layers now emit real
    // polygons, and the only thing separating a density cell from a published burned extent is
    // the form it claims.
    for (const layerId of ["fire", "water"] as const) {
      expect(() => assertNotPerimeter(layerId, "native_polygon")).toThrow();
    }
  });
});

/**
 * The envelope itself is REQUIRED on every browser mirror as of 2026-09-02, so "no envelope" is no
 * longer a state a payload can be in and no longer a state these presenters can degrade through.
 * What remains -- and what actually needs guarding -- is an envelope that declares no FOOTPRINT:
 * the presenter must draw its marker rather than reach for the contract's nominal cell size, which
 * is the fabricated square this whole track exists to prevent.
 */
describe("no declared footprint, no square", () => {
  it("degrades every layer to its marker rather than fabricating one", () => {
    const sizeless = {
      cellWidthDegrees: undefined,
      cellHeightDegrees: undefined,
      cellOriginDegrees: undefined,
    };
    const fakeMap = createFakeMap();
    const fire = presentParquetFireDetections(
      readyFireWindow([fireCell({ support: envelope(5, sizeless) })])
    );
    render(<FireLayer map={asMap(fakeMap)} visible geojson={fire} />);
    expect(fakeMap.dataOf("published-fire-source").features[0].geometry.type).toBe("Point");

    const vegetationMap = createFakeMap();
    const observation = vegetationObservation(-116.125, 43.625);
    render(
      <VegetationLayer
        map={asMap(vegetationMap)}
        visible
        geojson={presentParquetVegetation(
          readyVegetationWindow([
            { ...observation, support: { ...observation.support, ...sizeless } },
          ])
        )}
      />
    );
    // A Point in the vegetation source draws NOTHING, because both cell layers filter to
    // Polygon. That is deliberate: a dot is the deviation this slice closed, and one drawn for
    // an observation whose footprint is unknown would look exactly like one whose is known.
    expect(vegetationMap.dataOf("vegetation-ndvi-cells").features[0].geometry.type).toBe(
      "Point"
    );
    expect(vegetationMap.hasLayer("vegetation-ndvi-cells-point")).toBe(false);
  });
});

describe("style readiness", () => {
  it("attaches the new polygon layers on the same retry the dots use", () => {
    const fakeMap = createFakeMap();
    fakeMap.setStyleLoaded(false);
    const geojson = presentParquetFireDetections(readyFireWindow([fireCell()]));
    render(<FireLayer map={asMap(fakeMap)} visible geojson={geojson} />);
    expect(fakeMap.hasLayer("published-fire-cells-fill")).toBe(false);

    act(() => {
      fakeMap.setStyleLoaded(true);
      fakeMap.emit("styledata");
    });

    // A fill that only appeared after a second style event would leave every zoom under 13
    // blank on a dark-mode hard load -- the bug `use-style-ready` exists for, now with a third
    // layer to cover.
    expect(fakeMap.hasLayer("published-fire-cells-fill")).toBe(true);
  });
});
