import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook } from "@testing-library/react";

/**
 * Every viewport read in `useViewportProxiedLayers.ts` publishes its own liveness, and that
 * liveness IS the enablement it gave its own observer.
 *
 * The botanical lane's version of this is in `useBotanicalOccurrences.test.ts`; this file covers
 * the other four, which were converted in the same sweep (style review W10, B1 residue 1 --
 * each one retained frames under `keepPreviousData` and had the same dynamic `requested !== null`
 * conjunct, so each was the same defect waiting for a consumer to trust `data`).
 *
 * The cases never restate a predicate. They assert the two spellings of one value agree, so a
 * conjunct added to any of these enablements is covered here without this file changing.
 */
const inputs = vi.hoisted(() => ({
  getWatersheds: vi.fn(),
  getSoilSurvey: vi.fn(),
  getSoilField: vi.fn(),
  getClimateField: vi.fn(),
  /** Which toggles the registry withholds. Empty in every case but the withheld ones. */
  withheld: new Set<string>(),
}));

vi.mock("@/lib/map/layer-registry", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/map/layer-registry")>();
  return {
    ...actual,
    isLayerPermanentlyWithheld: (toggleId: string) => inputs.withheld.has(toggleId),
  };
});

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    environmental: {
      getWatersheds: { useQuery: inputs.getWatersheds },
      getSoilSurvey: { useQuery: inputs.getSoilSurvey },
      getSoilField: { useQuery: inputs.getSoilField },
      getClimateField: { useQuery: inputs.getClimateField },
    },
  },
}));

import {
  drawnDayReadState,
  useClimateFieldQuery,
  useSoilFieldQuery,
  useSoilSurveyQuery,
  useWatershedsQuery,
} from "@/hooks/useViewportProxiedLayers";

/** Inside the 1 sq-deg ceiling `useWatershedsQuery` mirrors from the procedure. */
const VIEWPORT_BBOX = "-123.5,49.1,-123.1,49.4";

/** A landed answer with every flag a live read could publish set, so a leak is visible. */
const RETAINED_FRAME = { availability: "published", truncated: true, features: [] };

const RETAINED_RESULT = {
  data: RETAINED_FRAME,
  isError: true,
  isFetching: true,
  isLoading: true,
  isSuccess: true,
  isPlaceholderData: true,
};

/** The options object a stubbed `useQuery` was last called with. */
function lastOptions(stub: { mock: { calls: unknown[][] } }): Record<string, unknown> {
  const call = stub.mock.calls.at(-1);
  if (call === undefined) throw new Error("the query was never called");
  return call[1] as Record<string, unknown>;
}

beforeEach(() => {
  inputs.withheld = new Set();
  for (const stub of [
    inputs.getWatersheds,
    inputs.getSoilSurvey,
    inputs.getSoilField,
    inputs.getClimateField,
  ]) {
    stub.mockReset();
    stub.mockReturnValue(RETAINED_RESULT);
  }
});

afterEach(() => {
  cleanup();
});

/** One row per hook: how to call it, which stub it calls, and the toggle that withholds it. */
const LANES = [
  {
    label: "useWatershedsQuery",
    stub: inputs.getWatersheds,
    withheldToggle: "watersheds",
    // `use`-prefixed because it IS a hook call; the rules-of-hooks lint reads the name.
    useRead: (bbox: string | null, enabled: boolean) => useWatershedsQuery(bbox, { enabled }),
  },
  {
    label: "useSoilSurveyQuery",
    stub: inputs.getSoilSurvey,
    withheldToggle: "soil-survey",
    // `use`-prefixed because it IS a hook call; the rules-of-hooks lint reads the name.
    useRead: (bbox: string | null, enabled: boolean) =>
      useSoilSurveyQuery(bbox, { enabled, zoom: 12 }),
  },
  {
    label: "useSoilFieldQuery",
    stub: inputs.getSoilField,
    withheldToggle: "soil-moisture",
    // `use`-prefixed because it IS a hook call; the rules-of-hooks lint reads the name.
    useRead: (bbox: string | null, enabled: boolean) =>
      useSoilFieldQuery(bbox, {
        enabled,
        measure: "moisture",
        date: "2026-09-01",
        depth: "surface",
        zoom: 12,
      }),
  },
  {
    label: "useClimateFieldQuery",
    stub: inputs.getClimateField,
    withheldToggle: "climate-precipitation",
    // `use`-prefixed because it IS a hook call; the rules-of-hooks lint reads the name.
    useRead: (bbox: string | null, enabled: boolean) =>
      useClimateFieldQuery(bbox, {
        enabled,
        signal: "precipitation",
        variant: "mean",
        date: "2026-09-01",
        renderForm: "field",
        zoom: 12,
      }),
  },
] as const;

describe.each(LANES)("$label: reported liveness is the observer's own enablement", (lane) => {
  it("hands back the answer and its flags while every conjunct holds", () => {
    const { result } = renderHook(() => lane.useRead(VIEWPORT_BBOX, true));

    expect(lastOptions(lane.stub).enabled).toBe(true);
    expect(result.current.isAnswerLive).toBe(true);
    expect(result.current.answer).toEqual(RETAINED_FRAME);
    expect(result.current.isError).toBe(true);
    expect(result.current.isShowingRetainedAnswer).toBe(true);
  });

  it("withholds the answer and every flag when the caller's gate is closed", () => {
    const { result } = renderHook(() => lane.useRead(VIEWPORT_BBOX, false));

    expect(lastOptions(lane.stub).enabled).toBe(false);
    expect(result.current.isAnswerLive).toBe(false);
    expect(result.current.answer).toBeUndefined();
    expect(result.current.isError).toBe(false);
    expect(result.current.isFetching).toBe(false);
    expect(result.current.isLoading).toBe(false);
    expect(result.current.isSuccess).toBe(false);
    // The flag TanStack leaves true on a disabled `keepPreviousData` observer, which is what
    // made a hidden layer report itself permanently mid-load.
    expect(result.current.isShowingRetainedAnswer).toBe(false);
  });

  /**
   * The collapsed or hidden map container: `viewportBbox` cannot express a viewport
   * (`src/lib/map/viewport-bbox.ts:57-67`), every toggle is still on, and the retained frame is
   * still in hand. This is the conjunct three consecutive fixes missed on the botanical lane.
   */
  it("withholds the answer when no bbox is expressible, with the toggle still on", () => {
    const { result } = renderHook(() => lane.useRead(null, true));

    expect(lastOptions(lane.stub).enabled).toBe(false);
    expect(result.current.isAnswerLive).toBe(false);
    expect(result.current.answer).toBeUndefined();
  });

  it("withholds the answer when governance withholds the layer", () => {
    inputs.withheld = new Set([lane.withheldToggle]);
    const { result } = renderHook(() => lane.useRead(VIEWPORT_BBOX, true));

    expect(lastOptions(lane.stub).enabled).toBe(false);
    expect(result.current.isAnswerLive).toBe(false);
    expect(result.current.answer).toBeUndefined();
  });
});

/**
 * The drawn-day registry reads four react-query field names; this is the one translation into
 * them, so a layer that is not being served cannot publish a drawn day off a retained frame.
 */
describe("drawnDayReadState", () => {
  it("carries a live read's answer and flags through under the registry's names", () => {
    const { result } = renderHook(() => useSoilFieldQuery(VIEWPORT_BBOX, {
      enabled: true,
      measure: "moisture",
      date: "2026-09-01",
      depth: "surface",
      zoom: 12,
    }));

    expect(drawnDayReadState(result.current)).toEqual({
      data: RETAINED_FRAME,
      isSuccess: true,
      isFetching: true,
      isPlaceholderData: true,
    });
  });

  it("reports nothing at all for a read that is not live", () => {
    const { result } = renderHook(() => useSoilFieldQuery(null, {
      enabled: true,
      measure: "moisture",
      date: "2026-09-01",
      depth: "surface",
      zoom: 12,
    }));

    expect(drawnDayReadState(result.current)).toEqual({
      data: undefined,
      isSuccess: false,
      isFetching: false,
      isPlaceholderData: false,
    });
  });
});
