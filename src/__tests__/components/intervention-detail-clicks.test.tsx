import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useInterventionDetailClicks } from "@/lib/map/use-intervention-detail-clicks";
import { useInterventionDetailStore } from "@/stores/intervention-detail-store";
import { INTERVENTION_STYLE_LAYER_IDS } from "@/lib/map/layer-registry";
import { INTERVENTION_DRAFTS_SOURCE_ID } from "@/lib/map/sources";
import type { InterventionDetailRecord } from "@/lib/map/intervention-detail";

/**
 * Click-to-inspect on the merged intervention layer.
 *
 * The assertions that matter are about WHICH resolution path a click takes:
 * an overlay feature must resolve from memory (a round trip here would be a
 * regression against NFR-1), and a Martin-tile feature must open by id so the
 * modal can fetch the unsimplified geometry the tile cannot carry.
 */

const DRAFT_ID = "66666666-6666-4666-8666-666666666666";
const PUBLISHED_ID = "77777777-7777-4777-8777-777777777777";

type Handler = (event: unknown) => void;

/** A MapLibre stand-in that records per-layer click bindings. */
function createFakeMap() {
  const handlers = new Map<string, Set<Handler>>();
  return {
    handlers,
    on(type: string, layerId: string, handler: Handler) {
      const key = `${type}:${layerId}`;
      const set = handlers.get(key) ?? new Set<Handler>();
      set.add(handler);
      handlers.set(key, set);
    },
    off(type: string, layerId: string, handler: Handler) {
      handlers.get(`${type}:${layerId}`)?.delete(handler);
    },
    fire(layerId: string, event: unknown) {
      for (const handler of handlers.get(`click:${layerId}`) ?? []) handler(event);
    },
  };
}

const DRAFT_RECORD: InterventionDetailRecord = {
  id: DRAFT_ID,
  name: "Creekside buffer",
  type: "reforestation",
  category: "land",
  status: "pending_review",
  description: "Willow and alder along 400m of bank.",
  geometry: {
    type: "Polygon",
    coordinates: [
      [
        [-120.1, 46.1],
        [-120.0, 46.1],
        [-120.0, 46.2],
        [-120.1, 46.1],
      ],
    ],
  },
  submittedByUserId: "22222222-2222-4222-8222-222222222222",
  submittedByTeamId: null,
  createdAt: "2026-09-01T00:00:00.000Z",
  updatedAt: null,
  reviewNote: null,
  hasFullGeometry: true,
};

function mount(records = new Map([[DRAFT_ID, DRAFT_RECORD]])) {
  const map = createFakeMap();
  const view = renderHook(
    ({ recordsById }: { recordsById: Map<string, InterventionDetailRecord> }) =>
      useInterventionDetailClicks(map as never, recordsById),
    { initialProps: { recordsById: records } }
  );
  return { map, view };
}

beforeEach(() => {
  useInterventionDetailStore.getState().close();
});

describe("intervention click-to-inspect", () => {
  it("binds a click handler to every one of the six merged style layers", () => {
    const { map } = mount();

    expect(INTERVENTION_STYLE_LAYER_IDS).toHaveLength(6);
    for (const layerId of INTERVENTION_STYLE_LAYER_IDS) {
      expect(map.handlers.get(`click:${layerId}`)?.size ?? 0).toBe(1);
    }
  });

  it("resolves a drafts-overlay feature from memory, with no network call", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { map } = mount();

    map.fire("intervention-drafts-fill", {
      features: [
        {
          source: INTERVENTION_DRAFTS_SOURCE_ID,
          properties: { id: DRAFT_ID, status: "pending_review" },
        },
      ],
    });

    const state = useInterventionDetailStore.getState();
    expect(state.featureId).toBe(DRAFT_ID);
    expect(state.record).toEqual(DRAFT_RECORD);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("opens a published Martin-tile feature by id, leaving the record to be fetched", () => {
    const { map } = mount();

    map.fire("interventions", {
      features: [
        {
          source: "intervention_tiles",
          properties: { id: PUBLISHED_ID, status: "published" },
        },
      ],
    });

    const state = useInterventionDetailStore.getState();
    expect(state.featureId).toBe(PUBLISHED_ID);
    // Null, not a record stitched out of the tile's own properties: the tile's
    // geometry is simplified and must never be shown as the drawn shape.
    expect(state.record).toBeNull();
  });

  it("falls back to a by-id open when an overlay record is missing", () => {
    const { map } = mount(new Map());

    map.fire("intervention-drafts-points", {
      features: [
        { source: INTERVENTION_DRAFTS_SOURCE_ID, properties: { id: DRAFT_ID } },
      ],
    });

    expect(useInterventionDetailStore.getState().featureId).toBe(DRAFT_ID);
    expect(useInterventionDetailStore.getState().record).toBeNull();
  });

  it("ignores a feature carrying no database id", () => {
    const { map } = mount();

    map.fire("interventions-points", {
      features: [{ source: "intervention_tiles", properties: { id: 42 } }],
    });

    expect(useInterventionDetailStore.getState().featureId).toBeNull();
  });

  it("unbinds every handler on unmount", () => {
    const { map, view } = mount();
    view.unmount();

    for (const layerId of INTERVENTION_STYLE_LAYER_IDS) {
      expect(map.handlers.get(`click:${layerId}`)?.size ?? 0).toBe(0);
    }
  });

  it("does not re-register handlers when the overlay records change", () => {
    const { map, view } = mount();
    const before = map.handlers.get("click:interventions");

    view.rerender({ recordsById: new Map([[DRAFT_ID, DRAFT_RECORD]]) });

    expect(map.handlers.get("click:interventions")).toBe(before);
    expect(before?.size).toBe(1);
  });
});
