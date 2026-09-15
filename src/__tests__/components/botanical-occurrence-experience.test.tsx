import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { renderWithProviders } from "@/test/utils";
import { useBotanicalOccurrenceStore } from "@/stores/botanical-occurrence-store";
import {
  BOTANICAL_RICHNESS_LEGEND,
} from "@/components/map/layers/BotanicalRichnessLayer";
import {
  BotanicalOccurrencesLayer,
  botanicalOccurrencesToGeoJSON,
} from "@/components/map/layers/BotanicalOccurrencesLayer";
import { GbifOccurrencesLayer } from "@/components/map/layers/GbifOccurrencesLayer";
import {
  BotanicalOccurrenceDetails,
  BOTANICAL_SPECIMEN_DISCLOSURE,
  formatEventInterval,
} from "@/components/panels/BotanicalOccurrenceDetails";
import { BotanicalFilters, BOTANICAL_NO_RELEASE_PINNED_MESSAGE } from "@/components/panels/BotanicalFilters";
import { BOTANICAL_DETAIL_MIN_ZOOM } from "@/lib/botanical-occurrences";
import type {
  BotanicalOccurrenceFeature,
  BotanicalOccurrenceResponse,
} from "@/lib/botanical-occurrences";

const mocks = vi.hoisted(() => ({
  fetchBotanicalOccurrences: vi.fn(),
}));

vi.mock("@/lib/botanical-occurrences", async () => {
  const actual = await vi.importActual<typeof import("@/lib/botanical-occurrences")>(
    "@/lib/botanical-occurrences"
  );
  return { ...actual, fetchBotanicalOccurrences: mocks.fetchBotanicalOccurrences };
});

function sampleFeature(overrides: Partial<BotanicalOccurrenceFeature> = {}): BotanicalOccurrenceFeature {
  return {
    occurrence_id: "occ-1",
    collection_key: "col-herb-1",
    source_record_key: "src-1",
    taxon_concept_id: "WFO-0000001",
    resolution_state: "resolved",
    scientific_name: "Quercus garryana",
    family: "Fagaceae",
    event_interval: { start: "1987", end: "1987", precision: "year" },
    longitude: -116.2,
    latitude: 43.6,
    coordinate_uncertainty_m: 500,
    spatial_class: "exact",
    membership: "confirmed",
    catalog_number: "CAT-001",
    recorded_by: "J. Smith",
    basis_of_record: "PreservedSpecimen",
    rights_uri: "https://example.org/rights",
    attribution_text: "Example Herbarium",
    ...overrides,
  };
}

describe("BotanicalFilters", () => {
  beforeEach(() => {
    useBotanicalOccurrenceStore.getState().resetFilters();
  });

  it("shows the no-release-resolved status and still renders every filter, since fetching is not gated on it", () => {
    renderWithProviders(<BotanicalFilters />);
    expect(screen.getByText(BOTANICAL_NO_RELEASE_PINNED_MESSAGE)).toBeTruthy();
    // The release line is status-only now: LayerManager resolves the generation server-side and
    // the query fires regardless, so every other filter must be usable even before any answer
    // has landed and written a release id back into the store. See BotanicalFilters.tsx's
    // docstring for why this field is no longer an editable gate.
    expect(screen.getByText(/no free-text name search/i)).toBeTruthy();
    expect(screen.queryByLabelText(/release set id/i)).toBeNull();
  });

  it("switches to a read-only release display once LayerManager writes a served generation id back, and never fetches on its own", async () => {
    renderWithProviders(<BotanicalFilters />);
    expect(mocks.fetchBotanicalOccurrences).not.toHaveBeenCalled();

    useBotanicalOccurrenceStore.getState().setReleaseSetId("release-2026-08");

    await waitFor(() => {
      expect(screen.getByText(/source snapshot/i)).toBeTruthy();
      expect(screen.getByText("release-2026-08")).toBeTruthy();
    });
    expect(mocks.fetchBotanicalOccurrences).not.toHaveBeenCalled();
    expect(useBotanicalOccurrenceStore.getState().filters.release_set_id).toBe("release-2026-08");
  });
});

describe("BotanicalOccurrenceDetails", () => {
  it("renders interval precision and the fixed disclosure line", () => {
    renderWithProviders(<BotanicalOccurrenceDetails feature={sampleFeature()} />);
    expect(screen.getByText("1987 (year precision)")).toBeTruthy();
    expect(screen.getByText(BOTANICAL_SPECIMEN_DISCLOSURE)).toBeTruthy();
  });

  it("formats a partial interval as start–end with precision", () => {
    expect(
      formatEventInterval({ start: "1987-04", end: "1987-06", precision: "interval" })
    ).toBe("1987-04 – 1987-06 (interval precision)");
  });

  it("renders a placeholder with no selected feature", () => {
    renderWithProviders(<BotanicalOccurrenceDetails feature={null} />);
    expect(screen.getByText(/select a specimen point/i)).toBeTruthy();
  });
});

describe("BotanicalRichnessLayer legend", () => {
  it("contains the four exact evaluation-state labels the spec requires", () => {
    const labels = BOTANICAL_RICHNESS_LEGEND.map((entry) => entry.label);
    expect(labels).toEqual(
      expect.arrayContaining([
        expect.stringContaining("zero documented records"),
        expect.stringContaining("outside admitted coverage"),
        expect.stringContaining("withheld/generalized only"),
        expect.stringContaining("not evaluated"),
      ])
    );
  });
});

describe("botanicalOccurrencesToGeoJSON", () => {
  it("never draws nonspatial records as features", () => {
    const spatial = sampleFeature({ occurrence_id: "occ-spatial" });
    const nonspatial = sampleFeature({
      occurrence_id: "occ-nonspatial",
      longitude: NaN,
      latitude: NaN,
    });
    const collection = botanicalOccurrencesToGeoJSON([spatial, nonspatial]);
    expect(collection.features).toHaveLength(1);
    expect(collection.features[0].properties?.occurrence_id).toBe("occ-spatial");
  });
});

describe("refused responses", () => {
  afterEach(() => {
    mocks.fetchBotanicalOccurrences.mockReset();
  });

  it("renders the refusal reason and draws no features", async () => {
    const refused: BotanicalOccurrenceResponse = {
      state: "refused",
      reason: "The requested claim is outside admitted evidence.",
    };
    mocks.fetchBotanicalOccurrences.mockResolvedValue(refused);

    const { fetchBotanicalOccurrences } = await import("@/lib/botanical-occurrences");
    const response = await fetchBotanicalOccurrences({
      release_set_id: "release-2026-08",
      bbox: "-117,43,-116,44",
      zoom: 6,
    });

    expect(response.state).toBe("refused");
    if (response.state === "refused") {
      expect(response.reason).toBe("The requested claim is outside admitted evidence.");
    }
  });

  it("shows nonspatial counts as a number, never as map features", async () => {
    const detail: BotanicalOccurrenceResponse = {
      state: "detail",
      release_set_id: "release-2026-08",
      published_at: "2026-08-08T06:00:00Z",
      taxonomy_recipe_version: "v1",
      qc_policy_version: "v1",
      support_id: null,
      truncated: false,
      next_cursor: null,
      counts: { returned: 1, matched: 4, withheld: 1, nonspatial: 2, excluded_by_qc: 0 },
      features: [sampleFeature()],
    };
    mocks.fetchBotanicalOccurrences.mockResolvedValue(detail);

    const { fetchBotanicalOccurrences } = await import("@/lib/botanical-occurrences");
    const response = await fetchBotanicalOccurrences({
      release_set_id: "release-2026-08",
      bbox: "-117,43,-116,44",
      zoom: 12,
    });

    expect(response.state).toBe("detail");
    if (response.state === "detail") {
      expect(response.counts.nonspatial).toBe(2);
      const geojson = botanicalOccurrencesToGeoJSON(response.features);
      expect(geojson.features).toHaveLength(1);
    }
  });
});

/**
 * Map-lifecycle regressions for the two occurrence renderers.
 *
 * Both used to gate source/layer creation on `isStyleLoaded()`, which is false until EVERY
 * source -- including basemap sources neither layer owns -- has finished its tiles. Worse than
 * the Fire/Water version of that bug, their draw effect's cleanup REMOVED the layers on every
 * data/zoom rerender before a globally-gated re-add, so one tick while readiness was pending
 * left a previously-installed layer removed and never re-added.
 *
 * The fixture below therefore models the two things MapLibre actually distinguishes:
 * PARSED style (`getStyle()` returns a style; `addSource`/`addLayer` are legal) and SOURCE
 * readiness (`isStyleLoaded()`), and it THROWS on creation against a genuinely unparsed style so
 * a regression to eager creation is caught rather than silently passing. Tile completion is
 * modelled as `sourcedata` only -- never a fabricated `styledata`/`style.load` standing in for it.
 */
function createOccurrenceFakeMap(parsed = true, sourcesReady = true) {
  const listeners = new Map<string, Array<(...args: unknown[]) => void>>();
  const sources = new Map<string, { data: unknown }>();
  const layers = new Map<string, Record<string, unknown>>();
  /** Source ids in creation order -- proves which component's style.load handler ran first. */
  const sourceAddOrder: string[] = [];
  let styleParsed = parsed;
  let sourcesLoaded = sourcesReady;
  let picked: Array<{ properties: Record<string, unknown> }> = [];

  const keyFor = (type: string, layerId?: string) => (layerId ? type + ":" + layerId : type);
  function on(type: string, a: unknown, b?: unknown) {
    const layerId = typeof b === "function" ? (a as string) : undefined;
    const handler = (typeof b === "function" ? b : a) as (...args: unknown[]) => void;
    const key = keyFor(type, layerId);
    if (!listeners.has(key)) listeners.set(key, []);
    listeners.get(key)!.push(handler);
  }
  function off(type: string, a: unknown, b?: unknown) {
    const layerId = typeof b === "function" ? (a as string) : undefined;
    const handler = (typeof b === "function" ? b : a) as (...args: unknown[]) => void;
    const bucket = listeners.get(keyFor(type, layerId));
    const index = bucket?.indexOf(handler) ?? -1;
    if (bucket && index >= 0) bucket.splice(index, 1);
  }

  return {
    on,
    off,
    isStyleLoaded: () => styleParsed && sourcesLoaded,
    setStyleParsed(value: boolean) {
      styleParsed = value;
    },
    setSourcesLoaded(value: boolean) {
      sourcesLoaded = value;
    },
    emit(type: string, layerId?: string, event?: unknown) {
      for (const handler of Array.from(listeners.get(keyFor(type, layerId)) ?? [])) handler(event);
    },
    handlerCount: (type: string, layerId?: string) =>
      listeners.get(keyFor(type, layerId))?.length ?? 0,
    getStyle: () => (styleParsed ? { layers: [] } : undefined),
    addSource: (id: string, options: { data?: unknown }) => {
      if (!styleParsed) throw new Error("Style is not done loading.");
      sources.set(id, { data: options.data });
      sourceAddOrder.push(id);
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
      if (!styleParsed) throw new Error("Style is not done loading.");
      layers.set(spec.id as string, spec);
    },
    getLayer: (id: string) => (layers.has(id) ? { id } : undefined),
    removeLayer: (id: string) => layers.delete(id),
    setPaintProperty: vi.fn(),
    setLayoutProperty: vi.fn(),
    queryRenderedFeatures: () => picked,
    setPicked(features: Array<{ properties: Record<string, unknown> }>) {
      picked = features;
    },
    dataOf: (id: string) => sources.get(id)?.data as GeoJSON.FeatureCollection | undefined,
    layerSpec: (id: string) => layers.get(id),
    hasLayer: (id: string) => layers.has(id),
    hasSource: (id: string) => sources.has(id),
    sourceAddOrder,
  };
}

type OccurrenceFakeMap = ReturnType<typeof createOccurrenceFakeMap>;
const asOccurrenceMap = (fake: OccurrenceFakeMap) => fake as unknown as MapLibreMap;

const BOTANICAL_SOURCE_ID = "botanical-occurrences";
const BOTANICAL_LAYER_IDS = [
  "botanical-occurrences-exact",
  "botanical-occurrences-generalized",
  "botanical-occurrences-possible",
  "botanical-occurrences-provisional-ring",
];
const GBIF_SOURCE_ID = "gbif-occurrences";
const GBIF_LAYER_IDS = [
  "gbif-occurrences-exact",
  "gbif-occurrences-generalized",
  "gbif-occurrences-provisional-ring",
];
const DETAIL_ZOOM = BOTANICAL_DETAIL_MIN_ZOOM;
const COARSE_ZOOM = BOTANICAL_DETAIL_MIN_ZOOM - 1;
const EMPTY_COLLECTION: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

/**
 * The two renderers share one lifecycle contract, so every case below runs against both. The
 * annotation is deliberate: a bare `as const` would give `Layer` a union component type that JSX
 * cannot call, and it also asserts structurally that GBIF keeps the same props as the UBC layer.
 */
interface OccurrenceRendererCase {
  name: string;
  Layer: typeof BotanicalOccurrencesLayer;
  sourceId: string;
  layerIds: string[];
  clickLayerIds: string[];
}

const OCCURRENCE_RENDERERS: OccurrenceRendererCase[] = [
  {
    name: "BotanicalOccurrencesLayer",
    Layer: BotanicalOccurrencesLayer,
    sourceId: BOTANICAL_SOURCE_ID,
    layerIds: BOTANICAL_LAYER_IDS,
    clickLayerIds: BOTANICAL_LAYER_IDS.slice(0, 3),
  },
  {
    name: "GbifOccurrencesLayer",
    Layer: GbifOccurrencesLayer,
    sourceId: GBIF_SOURCE_ID,
    layerIds: GBIF_LAYER_IDS,
    clickLayerIds: GBIF_LAYER_IDS.slice(0, 2),
  },
];

describe.each(OCCURRENCE_RENDERERS)(
  "$name parsed-style admission",
  ({ Layer, sourceId, layerIds, clickLayerIds }) => {
    it("installs on a delayed mount while isStyleLoaded() is still false, then survives tile completion", () => {
      const fakeMap = createOccurrenceFakeMap(true, false);
      fakeMap.emit("style.load"); // Parsing finished before this component ever mounted.
      const geojson = botanicalOccurrencesToGeoJSON([sampleFeature()]);

      render(<Layer map={asOccurrenceMap(fakeMap)} geojson={geojson} zoom={DETAIL_ZOOM} visible />);

      expect(fakeMap.isStyleLoaded()).toBe(false);
      expect(fakeMap.dataOf(sourceId)).toBe(geojson);
      for (const id of layerIds) expect(fakeMap.hasLayer(id)).toBe(true);

      act(() => {
        fakeMap.setSourcesLoaded(true);
        fakeMap.emit("sourcedata"); // Tiles landing is sourcedata, never a synthetic style event.
      });
      expect(fakeMap.isStyleLoaded()).toBe(true);
      expect(fakeMap.dataOf(sourceId)).toBe(geojson);
      for (const id of layerIds) expect(fakeMap.hasLayer(id)).toBe(true);
    });

    it("stays installed and current across data and zoom rerenders while source readiness is pending", () => {
      const fakeMap = createOccurrenceFakeMap(true, false);
      const first = botanicalOccurrencesToGeoJSON([sampleFeature()]);
      const second = botanicalOccurrencesToGeoJSON([sampleFeature({ occurrence_id: "occ-2" })]);
      const view = render(
        <Layer map={asOccurrenceMap(fakeMap)} geojson={first} zoom={DETAIL_ZOOM} visible />
      );
      for (const id of layerIds) expect(fakeMap.hasLayer(id)).toBe(true);

      // The old cleanup removed the layers here and only re-added them behind isStyleLoaded().
      view.rerender(
        <Layer map={asOccurrenceMap(fakeMap)} geojson={second} zoom={DETAIL_ZOOM + 2} visible />
      );

      expect(fakeMap.isStyleLoaded()).toBe(false);
      expect(fakeMap.dataOf(sourceId)).toBe(second);
      for (const id of layerIds) expect(fakeMap.hasLayer(id)).toBe(true);
    });

    it("keeps exactly one click handler per picking layer across repeated draw cycles", () => {
      const fakeMap = createOccurrenceFakeMap(true, false);
      const onSelectFeature = vi.fn();
      const view = render(
        <Layer
          map={asOccurrenceMap(fakeMap)}
          geojson={botanicalOccurrencesToGeoJSON([sampleFeature()])}
          zoom={DETAIL_ZOOM}
          visible
          onSelectFeature={onSelectFeature}
        />
      );
      for (const zoomTick of [DETAIL_ZOOM + 1, COARSE_ZOOM, DETAIL_ZOOM + 2]) {
        view.rerender(
          <Layer
            map={asOccurrenceMap(fakeMap)}
            geojson={botanicalOccurrencesToGeoJSON([
              sampleFeature({ occurrence_id: "occ-" + zoomTick }),
            ])}
            zoom={zoomTick}
            visible
            onSelectFeature={onSelectFeature}
          />
        );
      }

      for (const id of clickLayerIds) expect(fakeMap.handlerCount("click", id)).toBe(1);

      fakeMap.setPicked([{ properties: { occurrence_id: "occ-clicked" } }]);
      act(() => fakeMap.emit("click", clickLayerIds[0], { point: { x: 1, y: 1 } }));
      expect(onSelectFeature).toHaveBeenCalledTimes(1);
      expect(onSelectFeature).toHaveBeenCalledWith("occ-clicked");
    });

    it("waits for a genuinely unparsed style and then installs the latest pending props", () => {
      const fakeMap = createOccurrenceFakeMap(false, false);
      const stale = botanicalOccurrencesToGeoJSON([sampleFeature()]);
      const latest = botanicalOccurrencesToGeoJSON([sampleFeature({ occurrence_id: "occ-latest" })]);
      const view = render(
        <Layer map={asOccurrenceMap(fakeMap)} geojson={stale} zoom={DETAIL_ZOOM} visible />
      );
      view.rerender(
        <Layer map={asOccurrenceMap(fakeMap)} geojson={latest} zoom={DETAIL_ZOOM} visible />
      );
      act(() => fakeMap.emit("sourcedata"));
      expect(fakeMap.hasSource(sourceId)).toBe(false);
      for (const id of layerIds) expect(fakeMap.hasLayer(id)).toBe(false);

      act(() => {
        fakeMap.setStyleParsed(true);
        fakeMap.emit("style.load");
      });
      expect(fakeMap.isStyleLoaded()).toBe(false);
      expect(fakeMap.dataOf(sourceId)).toBe(latest);
      for (const id of layerIds) expect(fakeMap.hasLayer(id)).toBe(true);
    });

    it("clears an empty collection in place while still enabled", () => {
      const fakeMap = createOccurrenceFakeMap(true, true);
      const populated = botanicalOccurrencesToGeoJSON([sampleFeature()]);
      const view = render(
        <Layer map={asOccurrenceMap(fakeMap)} geojson={populated} zoom={DETAIL_ZOOM} visible />
      );
      expect(fakeMap.dataOf(sourceId)?.features).toHaveLength(1);

      // GBIF has no acquired rows yet, so an empty collection is the expected steady state.
      view.rerender(
        <Layer map={asOccurrenceMap(fakeMap)} geojson={EMPTY_COLLECTION} zoom={DETAIL_ZOOM} visible />
      );
      expect(fakeMap.dataOf(sourceId)?.features).toHaveLength(0);
      for (const id of layerIds) expect(fakeMap.hasLayer(id)).toBe(true);
    });

    it("removes its own source, layers and listeners on unmount", () => {
      const fakeMap = createOccurrenceFakeMap(true, true);
      const view = render(
        <Layer
          map={asOccurrenceMap(fakeMap)}
          geojson={botanicalOccurrencesToGeoJSON([sampleFeature()])}
          zoom={DETAIL_ZOOM}
          visible
        />
      );
      expect(fakeMap.hasSource(sourceId)).toBe(true);

      view.unmount();

      expect(fakeMap.hasSource(sourceId)).toBe(false);
      for (const id of layerIds) expect(fakeMap.hasLayer(id)).toBe(false);
      expect(fakeMap.handlerCount("style.load")).toBe(0);
      for (const id of clickLayerIds) expect(fakeMap.handlerCount("click", id)).toBe(0);
    });

    it("detaches from a replaced map and installs on the new one", () => {
      const first = createOccurrenceFakeMap(true, true);
      const second = createOccurrenceFakeMap(true, true);
      const geojson = botanicalOccurrencesToGeoJSON([sampleFeature()]);
      const view = render(
        <Layer map={asOccurrenceMap(first)} geojson={geojson} zoom={DETAIL_ZOOM} visible />
      );
      view.rerender(
        <Layer map={asOccurrenceMap(second)} geojson={geojson} zoom={DETAIL_ZOOM} visible />
      );

      expect(first.hasSource(sourceId)).toBe(false);
      expect(first.handlerCount("style.load")).toBe(0);
      expect(second.dataOf(sourceId)).toBe(geojson);
      for (const id of layerIds) expect(second.hasLayer(id)).toBe(true);
    });

    it("draws nothing below the detail zoom floor and only Point geometry above it", () => {
      const fakeMap = createOccurrenceFakeMap(true, true);
      const geojson = botanicalOccurrencesToGeoJSON([sampleFeature()]);
      const view = render(
        <Layer map={asOccurrenceMap(fakeMap)} geojson={geojson} zoom={COARSE_ZOOM} visible />
      );
      // The coarse band belongs to the Polygon richness layer; a detail Point layer must not
      // install there, and the floor is re-checked by the component, not just by the caller.
      expect(fakeMap.hasSource(sourceId)).toBe(false);
      for (const id of layerIds) expect(fakeMap.hasLayer(id)).toBe(false);

      view.rerender(
        <Layer map={asOccurrenceMap(fakeMap)} geojson={geojson} zoom={DETAIL_ZOOM} visible />
      );
      expect(fakeMap.dataOf(sourceId)?.features[0].geometry.type).toBe("Point");
      for (const id of layerIds) expect(fakeMap.layerSpec(id)?.type).toBe("circle");
    });

    it("keeps one style.load registration across a hidden to shown transition and a swap", () => {
      const fakeMap = createOccurrenceFakeMap(true, true);
      const geojson = botanicalOccurrencesToGeoJSON([sampleFeature()]);
      const view = render(
        <Layer map={asOccurrenceMap(fakeMap)} geojson={geojson} zoom={DETAIL_ZOOM} visible={false} />
      );
      expect(fakeMap.hasSource(sourceId)).toBe(false);
      expect(fakeMap.handlerCount("style.load")).toBe(1); // Registered even while hidden.

      view.rerender(
        <Layer map={asOccurrenceMap(fakeMap)} geojson={geojson} zoom={DETAIL_ZOOM} visible />
      );
      expect(fakeMap.dataOf(sourceId)).toBe(geojson);

      act(() => fakeMap.emit("style.load"));
      expect(fakeMap.handlerCount("style.load")).toBe(1);
      expect(fakeMap.dataOf(sourceId)).toBe(geojson);
    });
  }
);

describe("botanical and GBIF occurrence layers coexist", () => {
  it("own distinct sources and layer ids, and keep a stable style.load order across a swap", () => {
    const fakeMap = createOccurrenceFakeMap(true, false);
    const ubc = botanicalOccurrencesToGeoJSON([sampleFeature({ occurrence_id: "ubc-1" })]);
    const gbif = botanicalOccurrencesToGeoJSON([
      sampleFeature({ occurrence_id: "gbif-1", collection_key: "gbif" }),
    ]);

    // Mount order is the registration order MapLibre stacking depends on.
    const view = render(
      <>
        <BotanicalOccurrencesLayer
          map={asOccurrenceMap(fakeMap)}
          geojson={ubc}
          zoom={DETAIL_ZOOM}
          visible
        />
        <GbifOccurrencesLayer
          map={asOccurrenceMap(fakeMap)}
          geojson={gbif}
          zoom={DETAIL_ZOOM}
          visible
        />
      </>
    );

    expect(fakeMap.dataOf(BOTANICAL_SOURCE_ID)).toBe(ubc);
    expect(fakeMap.dataOf(GBIF_SOURCE_ID)).toBe(gbif);
    expect(fakeMap.sourceAddOrder).toEqual([BOTANICAL_SOURCE_ID, GBIF_SOURCE_ID]);
    for (const id of [...BOTANICAL_LAYER_IDS, ...GBIF_LAYER_IDS]) {
      expect(fakeMap.layerSpec(id)?.source).toBe(
        id.startsWith("gbif") ? GBIF_SOURCE_ID : BOTANICAL_SOURCE_ID
      );
    }

    // Hide and re-show GBIF, then swap the style: the botanical listener must still run first.
    view.rerender(
      <>
        <BotanicalOccurrencesLayer
          map={asOccurrenceMap(fakeMap)}
          geojson={ubc}
          zoom={DETAIL_ZOOM}
          visible
        />
        <GbifOccurrencesLayer
          map={asOccurrenceMap(fakeMap)}
          geojson={gbif}
          zoom={DETAIL_ZOOM}
          visible={false}
        />
      </>
    );
    expect(fakeMap.hasSource(GBIF_SOURCE_ID)).toBe(false);
    view.rerender(
      <>
        <BotanicalOccurrencesLayer
          map={asOccurrenceMap(fakeMap)}
          geojson={ubc}
          zoom={DETAIL_ZOOM}
          visible
        />
        <GbifOccurrencesLayer
          map={asOccurrenceMap(fakeMap)}
          geojson={gbif}
          zoom={DETAIL_ZOOM}
          visible
        />
      </>
    );

    fakeMap.removeSource(BOTANICAL_SOURCE_ID);
    fakeMap.removeSource(GBIF_SOURCE_ID);
    fakeMap.sourceAddOrder.length = 0;
    act(() => fakeMap.emit("style.load"));
    expect(fakeMap.sourceAddOrder).toEqual([BOTANICAL_SOURCE_ID, GBIF_SOURCE_ID]);
    expect(fakeMap.handlerCount("style.load")).toBe(2);
  });
});
