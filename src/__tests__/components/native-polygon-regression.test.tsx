import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { URL as NodeURL, fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import type { Map as MapLibreMap } from "maplibre-gl";
import type { LayerSpecification } from "@maplibre/maplibre-gl-style-spec";
import { DroughtLayer } from "@/components/map/layers/DroughtLayer";
import { SoilSurveyLayer } from "@/components/map/layers/SoilSurveyLayer";
import { LAYER_REGISTRY, type LayerToggleId } from "@/lib/map/layer-registry";
import {
  MARTIN_SOURCE_BY_LAYER_TOGGLE,
  SOIL_SURVEY_SOURCE,
  getLayers,
  soilSurveySummaryLayer,
} from "@/lib/map/layers";
import {
  LAYER_RENDER_CONTRACT,
  PerimeterMisrepresentationError,
  ZOOM_BANDS,
  assertNotPerimeter,
  layerRenderContractEntries,
  permittedFormsFor,
  renderClassOf,
  resolveZoomBand,
  type SupportKind,
  type ZoomBand,
} from "@/lib/map/layer-render-contract";
import {
  DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS,
  dateFilterableStyleLayerIds,
} from "@/lib/map/tile-layer-date-filter";
import { DEFAULT_VIEWPORT } from "@/stores/map-store";

/*
 * The native-polygon regression gate for
 * conductor/tracks/multiscale_polygon_surface_20260901 (slice m4).
 *
 * It pins ONE claim in six places: a product with published source geometry is drawn as that
 * geometry, generalized only by the server for the zoom that asked, and never re-derived into a
 * dot, a cell or a heat blob on the way to the canvas. MTBS (`burn-severity`) is the continuity
 * reference the spec names -- "MTBS rendered coherent source polygons and is the native-geometry
 * reference" -- so its path is asserted end to end and every other native layer is measured
 * against it.
 *
 * The per-layer evidence, the tolerances, and the two gaps this file records rather than fixes
 * are in the slice's baseline:
 * conductor/tracks/multiscale_polygon_surface_20260901/evidence/native-polygon-baseline.md
 */

// Explicit node:url URL, not the ambient global: this file runs under vitest's jsdom
// environment, which shadows globalThis.URL with a browser polyfill that mis-resolves
// multi-level ".." against a Windows file:// base. Same reason as
// src/__tests__/lib/map/layer-registry.test.ts.
const SOURCE_DIR = fileURLToPath(new NodeURL("../../", import.meta.url));
const DRIZZLE_DIR = fileURLToPath(new NodeURL("../../../drizzle", import.meta.url));

/** The six products the spec's render table calls `native_polygon`, in registry order. */
const EXPECTED_NATIVE_POLYGON_LAYER_IDS: readonly LayerToggleId[] = [
  "fire-perimeters",
  "burn-severity",
  "drought",
  "evacuation-zones",
  "watersheds",
  "soil-survey",
];

/** Derived, never hand-listed, so a seventh native product joins this sweep by classification alone. */
const NATIVE_POLYGON_LAYER_IDS: readonly LayerToggleId[] = layerRenderContractEntries()
  .filter((entry) => entry.renderClass === "native_polygon")
  .map((entry) => entry.layerId);

/** The four that reach the map as Martin function tiles baked into the style. */
const STYLE_BACKED_NATIVE_LAYER_IDS: readonly LayerToggleId[] = [
  "fire-perimeters",
  "evacuation-zones",
  "burn-severity",
  "watersheds",
];

/** One representative map zoom per band, resolved through the one ladder in zoom-tiers.ts. */
const ZOOM_BY_BAND: Readonly<Record<ZoomBand, number>> = {
  coarse: 2,
  middle: 9,
  detail: 14,
};

/** The Martin tile functions behind the four style-backed native layers, newest definition wins. */
const NATIVE_TILE_FUNCTION_NAMES = [
  "fire_risk_tiles",
  "burn_severity_tiles",
  "evacuation_zone_tiles",
  "watershed_tiles",
] as const;

/**
 * Client modules on a native polygon's path from wire to canvas. Every one is scanned for a
 * geometry-mutating call below: the contract permits generalization and dissolve, but only in
 * the warehouse or the tile function, where the tolerance is declared and attributable. A
 * simplify in the browser is an unattributable second opinion about what the ground looks like.
 */
const NATIVE_POLYGON_CLIENT_MODULES = [
  "components/map/layers/DroughtLayer.tsx",
  "components/map/layers/SoilSurveyLayer.tsx",
  "lib/map/layers.ts",
  "lib/map/layer-utils.ts",
  "lib/environmental/parquet-presentation.ts",
] as const;

/**
 * Call-shaped, not prose: matching the bare word would fire on every comment that explains why
 * simplification is server-side, which is most of them.
 */
const CLIENT_GEOMETRY_MUTATION =
  /(?:@turf\/|\bfrom\s+["']turf["']|\b(?:simplify|dissolve|convexHull|concaveHull|polygonSmooth|cleanCoords)\s*\(|\.buffer\s*\(|\bbuffer\s*\(\s*(?:feature|geometry|geojson|polygon))/i;

/** Forms that stand for something other than the source shape. None may reach a native layer. */
const NON_NATIVE_FORMS: readonly SupportKind[] = [
  "raw_point",
  "aggregate_cell",
  "heatmap",
  "cluster",
  "tessellated_cell",
  "isoband",
  "raster_surface",
  "unavailable",
];

interface StyleLayerFacts {
  type: string;
  source: string;
  sourceLayer: string;
  minzoom: number | null;
}

/** The four facts this file judges a baked style layer on, read off the shipped specification. */
function styleLayerFacts(styleLayerId: string): StyleLayerFacts {
  const spec = getLayers().find((layer) => layer.id === styleLayerId);
  if (spec === undefined) {
    throw new Error(`getLayers() declares no style layer "${styleLayerId}"`);
  }
  return {
    type: spec.type,
    source: "source" in spec && typeof spec.source === "string" ? spec.source : "",
    sourceLayer:
      "source-layer" in spec && typeof spec["source-layer"] === "string"
        ? spec["source-layer"]
        : "",
    minzoom: "minzoom" in spec && typeof spec.minzoom === "number" ? spec.minzoom : null,
  };
}

/**
 * The newest migration text for one SQL object, from its `CREATE` to the next
 * `--> statement-breakpoint`.
 *
 * Migrations are `CREATE OR REPLACE`, so the highest-numbered file that names an object is the
 * definition production runs. Reading the whole directory rather than a pinned filename is what
 * makes this follow a future migration instead of silently going stale against one: a new file
 * that reintroduces simplification into a tile function fails here without being named.
 */
function newestMigrationStatement(createMarker: string): string {
  const files = readdirSync(DRIZZLE_DIR)
    .filter((name) => name.endsWith(".sql"))
    .sort();
  let statement: string | null = null;
  for (const file of files) {
    const sql = readFileSync(join(DRIZZLE_DIR, file), "utf8");
    let from = sql.indexOf(createMarker);
    while (from !== -1) {
      const breakpoint = sql.indexOf("--> statement-breakpoint", from);
      statement = sql.slice(from, breakpoint === -1 ? sql.length : breakpoint);
      from = sql.indexOf(createMarker, from + createMarker.length);
    }
  }
  if (statement === null) {
    throw new Error(`no migration in ${DRIZZLE_DIR} declares "${createMarker}"`);
  }
  return statement;
}

/** The body of a Martin tile function as production last defined it. */
function tileFunctionBody(functionName: string): string {
  return newestMigrationStatement(`CREATE OR REPLACE FUNCTION geo.${functionName}(`);
}

/**
 * Models the parts of maplibre-gl the two component-mounted native layers touch, and RECORDS
 * what they were handed: the layer specification object by reference, and the exact
 * FeatureCollection that reached `addSource`. Reference identity is the assertion that matters
 * here -- a renderer that generalized, re-projected or re-wrapped the served geometry could not
 * hand the same object back.
 *
 * Modelled on the fake in dark-mode-layer-visibility.test.tsx, minus its readiness machinery:
 * real add/get bookkeeping, so a duplicate id throws exactly as maplibre-gl would, and a
 * listener registry so the components' `on`/`off` lifecycle runs to completion.
 */
function createRecordingMap() {
  const listeners = new Map<string, Set<() => void>>();
  const sources = new Map<string, { data: unknown }>();
  const layers = new Map<string, LayerSpecification>();

  function on(type: string, handler: () => void) {
    const handlers = listeners.get(type) ?? new Set<() => void>();
    handlers.add(handler);
    listeners.set(type, handlers);
  }

  return {
    on,
    // Aliased rather than omitted: readiness is not what this file measures, and a component
    // that reached for `once` would otherwise fail as a missing method instead of a wrong form.
    once: on,
    off(type: string, handler: () => void) {
      listeners.get(type)?.delete(handler);
    },
    // Always ready: the style-load race is covered by dark-mode-layer-visibility.test.tsx.
    isStyleLoaded: () => true,
    // An empty layer list, so getFirstSymbolLayer resolves `beforeId` to undefined.
    getStyle: (): { layers: LayerSpecification[] } => ({ layers: [] }),
    addSource(id: string, options: { data: unknown }) {
      if (sources.has(id)) throw new Error(`Source "${id}" already exists.`);
      sources.set(id, { data: options.data });
    },
    getSource(id: string) {
      const source = sources.get(id);
      if (source === undefined) return undefined;
      return {
        setData(data: unknown) {
          source.data = data;
        },
      };
    },
    removeSource(id: string) {
      sources.delete(id);
    },
    addLayer(layer: LayerSpecification) {
      if (layers.has(layer.id)) {
        throw new Error(`Layer "${layer.id}" already exists on this map.`);
      }
      layers.set(layer.id, layer);
    },
    getLayer: (id: string) => layers.get(id),
    removeLayer(id: string) {
      layers.delete(id);
    },
    setPaintProperty: vi.fn(),
    /** The specification object as the component handed it over, by reference. */
    addedLayer: (id: string) => layers.get(id),
    addedLayerIds: () => Array.from(layers.keys()),
    /** The data as it reached addSource/setData, by reference. */
    sourceData: (id: string) => sources.get(id)?.data,
  };
}

/** One USDM release class: a MultiPolygon with the release identity the reader mints. */
const DROUGHT_GEOJSON: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      id: "usdm:2026-08-26:3",
      geometry: {
        type: "MultiPolygon",
        coordinates: [
          [
            [
              [-121.5, 44.0],
              [-121.0, 44.0],
              [-121.0, 44.5],
              [-121.5, 44.5],
              [-121.5, 44.0],
            ],
          ],
        ],
      },
      properties: { DM: 3, label: "D3", validDate: "2026-08-26" },
    },
  ],
};

/** One SSURGO delineation, keyed on SSURGO's own per-shape primary key. */
const SOIL_SURVEY_GEOJSON: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      id: "mupolygonkey:8412773",
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [-116.3, 43.6],
            [-116.2, 43.6],
            [-116.2, 43.7],
            [-116.3, 43.7],
            [-116.3, 43.6],
          ],
        ],
      },
      properties: { drainageClass: "well-drained", mukey: "460958", hydric: false },
    },
  ],
};

describe("the native polygon class", () => {
  it("holds exactly the six products with published source geometry", () => {
    expect([...NATIVE_POLYGON_LAYER_IDS].sort()).toEqual(
      [...EXPECTED_NATIVE_POLYGON_LAYER_IDS].sort()
    );
  });

  it("excludes every event-point product, fire detections above all", () => {
    const eventPointLayerIds = layerRenderContractEntries()
      .filter((entry) => entry.renderClass === "event_point")
      .map((entry) => entry.layerId);

    expect(eventPointLayerIds).toContain("fire");
    for (const layerId of eventPointLayerIds) {
      expect(NATIVE_POLYGON_LAYER_IDS).not.toContain(layerId);
    }
    expect(renderClassOf("fire")).toBe("event_point");
  });
});

describe("a native polygon layer may only ever be drawn as its source geometry", () => {
  it("permits native_polygon and nothing else in all three bands", () => {
    const permittedByLayer = Object.fromEntries(
      NATIVE_POLYGON_LAYER_IDS.map(
        (layerId): [string, Readonly<Record<ZoomBand, readonly SupportKind[]>>] => [
          layerId,
          LAYER_RENDER_CONTRACT[layerId].permittedForms,
        ]
      )
    );
    const nativeOnly = {
      coarse: ["native_polygon"],
      middle: ["native_polygon"],
      detail: ["native_polygon"],
    };

    expect(permittedByLayer).toEqual({
      "fire-perimeters": nativeOnly,
      "burn-severity": nativeOnly,
      drought: nativeOnly,
      "evacuation-zones": nativeOnly,
      watersheds: nativeOnly,
      "soil-survey": nativeOnly,
    });
  });

  it("answers the same through the zoom-aware helper at a zoom in each band", () => {
    for (const layerId of NATIVE_POLYGON_LAYER_IDS) {
      for (const band of ZOOM_BANDS) {
        const zoom = ZOOM_BY_BAND[band];
        expect(resolveZoomBand(zoom)).toBe(band);
        expect(permittedFormsFor(layerId, zoom)).toEqual(["native_polygon"]);
      }
    }
  });

  it("never permits a cell, a dot, a cluster or a smoothed surface at any band", () => {
    for (const layerId of NATIVE_POLYGON_LAYER_IDS) {
      for (const band of ZOOM_BANDS) {
        for (const form of NON_NATIVE_FORMS) {
          expect(LAYER_RENDER_CONTRACT[layerId].permittedForms[band]).not.toContain(form);
        }
      }
    }
  });

  it("declares no fixed support, because source geometry has no cell size", () => {
    for (const layerId of NATIVE_POLYGON_LAYER_IDS) {
      expect(LAYER_RENDER_CONTRACT[layerId].declaredSupportDegrees).toBeNull();
    }
  });

  it("still permits only source geometry at the default PNW camera", () => {
    // The map opens on the coverage bbox (src/stores/map-store.ts), which lands in the coarse
    // band -- the band the 2026-09-01 assessment found raw dots in. Every native product must
    // still be a polygon there.
    expect(resolveZoomBand(DEFAULT_VIEWPORT.zoom)).toBe("coarse");
    for (const layerId of NATIVE_POLYGON_LAYER_IDS) {
      expect(permittedFormsFor(layerId, DEFAULT_VIEWPORT.zoom)).toEqual(["native_polygon"]);
    }
  });
});

describe("assertNotPerimeter separates real perimeters from detection density", () => {
  it("passes for burn-severity and fire-perimeters drawn as native polygons", () => {
    expect(() => assertNotPerimeter("burn-severity", "native_polygon")).not.toThrow();
    expect(() => assertNotPerimeter("fire-perimeters", "native_polygon")).not.toThrow();
  });

  it("passes for every native polygon product", () => {
    for (const layerId of NATIVE_POLYGON_LAYER_IDS) {
      expect(() => assertNotPerimeter(layerId, "native_polygon")).not.toThrow();
    }
  });

  it("throws for fire detections, which are density cells and not a burned extent", () => {
    expect(() => assertNotPerimeter("fire", "native_polygon")).toThrow(
      PerimeterMisrepresentationError
    );
  });
});

describe("the four tile-backed native layers draw a fill from their source geometry", () => {
  it("gives each one a fill and a line over one Martin function source", () => {
    const formsByLayer = Object.fromEntries(
      STYLE_BACKED_NATIVE_LAYER_IDS.map((layerId): [string, string[]] => [
        layerId,
        LAYER_REGISTRY[layerId].styleLayerIds.map((id) => styleLayerFacts(id).type),
      ])
    );

    expect(formsByLayer).toEqual({
      "fire-perimeters": ["fill", "line"],
      "evacuation-zones": ["fill", "line"],
      "burn-severity": ["fill", "line"],
      watersheds: ["fill", "line"],
    });
  });

  it("binds every one of their style layers to the toggle's own tile source", () => {
    for (const layerId of STYLE_BACKED_NATIVE_LAYER_IDS) {
      const martinSource = MARTIN_SOURCE_BY_LAYER_TOGGLE[layerId];
      expect(martinSource).toBeDefined();
      for (const styleLayerId of LAYER_REGISTRY[layerId].styleLayerIds) {
        const facts = styleLayerFacts(styleLayerId);
        expect(facts.source).toBe(martinSource);
        // A `source-layer` is what makes the drawn shape the tile's own geometry rather than
        // anything the client assembled: an MVT layer tag, resolved inside the tile.
        expect(facts.sourceLayer).not.toBe("");
      }
    }
  });

  it("draws none of them as circles, heatmaps or extrusions", () => {
    for (const layerId of STYLE_BACKED_NATIVE_LAYER_IDS) {
      for (const styleLayerId of LAYER_REGISTRY[layerId].styleLayerIds) {
        expect(["fill", "line"]).toContain(styleLayerFacts(styleLayerId).type);
      }
    }
  });

  it("keeps all four on the style-baked tile path, not on a client-assembled source", () => {
    for (const layerId of STYLE_BACKED_NATIVE_LAYER_IDS) {
      expect(LAYER_REGISTRY[layerId].renderKind).toBe("style");
    }
  });
});

describe("the two component-mounted native layers draw the served geometry unmodified", () => {
  it("gives drought a fill and an outline over the collection it was handed", () => {
    const map = createRecordingMap();

    render(
      <DroughtLayer map={map as unknown as MapLibreMap} geojson={DROUGHT_GEOJSON} visible />
    );

    expect(map.addedLayer("drought-fill")?.type).toBe("fill");
    expect(map.addedLayer("drought-outline")?.type).toBe("line");
    expect(map.addedLayerIds()).toEqual(["drought-fill", "drought-outline"]);
    // By reference: the renderer neither re-derived nor re-wrapped the release's rings.
    expect(map.sourceData("drought-monitor")).toBe(DROUGHT_GEOJSON);
  });

  it("gives soil survey the shared fill spec over the collection it was handed", () => {
    const map = createRecordingMap();

    render(
      <SoilSurveyLayer
        map={map as unknown as MapLibreMap}
        geojson={SOIL_SURVEY_GEOJSON}
        visible
      />
    );

    expect(map.addedLayer("soil-survey-fill")?.type).toBe("fill");
    expect(map.addedLayer("soil-survey-outline")?.type).toBe("line");
    expect(map.sourceData(SOIL_SURVEY_SOURCE)).toBe(SOIL_SURVEY_GEOJSON);
  });

  it("adds nothing for either layer while it is switched off", () => {
    const droughtMap = createRecordingMap();
    render(
      <DroughtLayer
        map={droughtMap as unknown as MapLibreMap}
        geojson={DROUGHT_GEOJSON}
        visible={false}
      />
    );
    expect(droughtMap.addedLayerIds()).toEqual([]);

    const surveyMap = createRecordingMap();
    render(
      <SoilSurveyLayer
        map={surveyMap as unknown as MapLibreMap}
        geojson={SOIL_SURVEY_GEOJSON}
        visible={false}
      />
    );
    expect(surveyMap.addedLayerIds()).toEqual([]);
  });
});

describe("no client-side buffer, simplify or dissolve on a native polygon path", () => {
  it("finds no geometry-mutating call in any module between the wire and the canvas", () => {
    const offending = NATIVE_POLYGON_CLIENT_MODULES.filter((modulePath) =>
      CLIENT_GEOMETRY_MUTATION.test(readFileSync(join(SOURCE_DIR, modulePath), "utf8"))
    );

    expect(offending).toEqual([]);
  });
});

describe("generalization is server-side, topology-preserving and chosen by zoom", () => {
  it("leaves every native tile function's geometry unsimplified", () => {
    const simplifyingFunctions = NATIVE_TILE_FUNCTION_NAMES.filter((name) =>
      /ST_Simplify/i.test(tileFunctionBody(name))
    );

    expect(simplifyingFunctions).toEqual([]);
  });

  it("hands each tile function's stored geometry straight to ST_AsMVTGeom", () => {
    for (const name of NATIVE_TILE_FUNCTION_NAMES) {
      expect(tileFunctionBody(name)).toMatch(/ST_AsMVTGeom\(ST_Transform\(/);
    }
  });

  it("routes watersheds by zoom into the hierarchical HUC rollup rather than a cell grid", () => {
    const body = tileFunctionBody("watershed_tiles");

    // z >= 10 reads the published HUC12 rows; everything coarser reads the rollup, whose parent
    // basin is the exact union of its members rather than an invented grouping.
    expect(body).toMatch(/target_level\s*:=\s*CASE/);
    expect(body).toMatch(/WHEN z >= 10 THEN 12/);
    expect(body).toContain("geo.watershed_rollup");
  });

  it("builds the watershed rollup with the topology-preserving simplifier only", () => {
    const rollup = newestMigrationStatement(
      "CREATE MATERIALIZED VIEW IF NOT EXISTS geo.watershed_rollup"
    );

    expect(rollup).toContain("ST_SimplifyPreserveTopology");
    // Plain ST_Simplify may return a self-intersecting or empty ring, which draws as a bow tie
    // and answers point-in-polygon wrongly. The spec's word is "topology-preserving".
    expect(rollup).not.toMatch(/\bST_Simplify\s*\(/i);
  });

  it("admits no bare ST_Simplify anywhere in the migration tree", () => {
    const offending = readdirSync(DRIZZLE_DIR)
      .filter((name) => name.endsWith(".sql"))
      .filter((name) =>
        /\bST_Simplify\s*\(/i.test(readFileSync(join(DRIZZLE_DIR, name), "utf8"))
      );

    expect(offending).toEqual([]);
  });
});

describe("MTBS is the production continuity reference", () => {
  it("is classified as a native polygon and drawn as one at every band", () => {
    expect(renderClassOf("burn-severity")).toBe("native_polygon");
    for (const band of ZOOM_BANDS) {
      expect(permittedFormsFor("burn-severity", ZOOM_BY_BAND[band])).toEqual(["native_polygon"]);
    }
  });

  it("reads its fill and outline from the burn_severity tile layer", () => {
    const fill = styleLayerFacts("burn-severity");
    const outline = styleLayerFacts("burn-severity-outline");

    expect(fill.type).toBe("fill");
    expect(outline.type).toBe("line");
    expect(fill.source).toBe("burn_severity_tiles");
    expect(outline.source).toBe("burn_severity_tiles");
    expect(fill.sourceLayer).toBe("burn_severity");
    expect(outline.sourceLayer).toBe("burn_severity");
  });

  it("scrubs the time slider by re-filtering tiles in place, never by refetching other geometry", () => {
    expect(DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS).toContain("burn-severity");
    const filtered = dateFilterableStyleLayerIds();
    expect(filtered).toContain("burn-severity");
    expect(filtered).toContain("burn-severity-outline");
  });

  it("records no shipped deviation: the reference layer draws what the contract permits", () => {
    expect(LAYER_RENDER_CONTRACT["burn-severity"].shippedDeviation).toBeUndefined();
  });
});

/*
 * Two gaps this slice RECORDS rather than closes. Both are asserted as the state of the shipped
 * renderer, so closing either one fails here and forces the fix to be deliberate. The reasoning,
 * and what production pixels are owed for each, is in the slice's baseline document.
 */
describe("recorded gaps between the contract and the shipped native renderers", () => {
  it("floors three fire-family fills at zoom 4, inside the coarse band", () => {
    // The coarse band spans z0 to just under z9, so z0-z3.99 draws nothing for these three --
    // indistinguishable from a layer with no data, the confusion 0023 removed for watersheds
    // and did not remove here. Owner: m5.
    const minzoomByStyleLayer = Object.fromEntries(
      ["fire-perimeters", "evacuation-zones", "burn-severity", "watersheds-fill"].map(
        (id): [string, number | null] => [id, styleLayerFacts(id).minzoom]
      )
    );

    expect(minzoomByStyleLayer).toEqual({
      "fire-perimeters": 4,
      "evacuation-zones": 4,
      "burn-severity": 4,
      // No floor since 0023_watershed_zoom_generalization: payload is bounded by drawing the
      // HUC rung the zoom can carry, not by hiding the layer. The pattern the other three owe.
      "watersheds-fill": null,
    });
    expect(resolveZoomBand(0)).toBe("coarse");
    expect(resolveZoomBand(3.9)).toBe("coarse");
  });

  it("answers a wide soil-survey viewport with a counted circle lattice the contract forbids", () => {
    // readSummaryFeatures (src/lib/server/services/usda-soil.ts) emits one Point per lattice
    // cell once the viewport outgrows MAX_SOIL_UNION_SQUARE_DEGREES (~0.48 sq deg), and this
    // circle layer paints them. At the default PNW camera that is the answer, so a
    // `native_polygon` product is drawn as dots in the coarse band.
    // `in` rather than a direct read: a background layer carries no `filter`, so the union
    // needs narrowing before the property exists at all.
    const summaryFilter =
      "filter" in soilSurveySummaryLayer ? soilSurveySummaryLayer.filter : null;

    expect(soilSurveySummaryLayer.type).toBe("circle");
    expect(summaryFilter).toEqual(["==", ["get", "summary"], true]);

    // The contract is deliberately NOT widened to match: a dot whose radius means "how many
    // delineations were counted here" is not the survey's geometry, and legalising it would
    // delete the only record that the two disagree.
    expect(permittedFormsFor("soil-survey", DEFAULT_VIEWPORT.zoom)).toEqual(["native_polygon"]);
    expect(LAYER_RENDER_CONTRACT["soil-survey"].permittedForms.coarse).not.toContain("raw_point");
  });
});
