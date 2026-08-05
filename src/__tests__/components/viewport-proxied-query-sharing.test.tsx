import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, waitFor } from "@testing-library/react";
import { observable } from "@trpc/server/observable";
import type { TRPCLink } from "@trpc/client";
import type maplibregl from "maplibre-gl";
import { MapProvider } from "@/lib/map/map-context";
import { trpc } from "@/lib/trpc/client";
import { useMapStore } from "@/stores/map-store";
import { usePanelStore } from "@/stores/panel-store";
import type { AppRouter } from "@/lib/server/trpc/router";

/**
 * The map and the panel that describes it must issue ONE react-query entry per proxied
 * feed, not two that merely look alike. Nothing about `useQuery` is mocked here: a
 * recording tRPC link stands in for the network, so the queryKey, the `staleTime` and the
 * `retry` these cases read are the ones react-query actually resolved. Mocking `useQuery`
 * would let the two callers drift apart and still pass.
 *
 * The callers are the real `LayerManager` and the real `PanelManager`, because the bbox a
 * panel receives is `PanelManager`'s derivation, and that derivation is half of what must
 * not drift.
 */

const panelRegistry = vi.hoisted(
  () => ({}) as Record<string, (props: Record<string, unknown>) => React.ReactNode>
);

/**
 * `next/dynamic` resolves to the real panel for the panels under test and to nothing for
 * everything else: the map sub-layers want a full MapLibre instance, and the other panels
 * are not what is being measured. The loader's source text is the only handle on identity
 * a stubbed dynamic import has -- the module path survives the transform. The lookup is
 * deferred to render time because `dynamic()` runs while PanelManager is imported, before
 * the registry below is filled.
 */
vi.mock("next/dynamic", () => ({
  default: (loader: unknown) => {
    const name = /panels[/\\](\w+)/.exec(String(loader))?.[1] ?? null;
    return function DynamicPanelUnderTest(props: Record<string, unknown>) {
      const Panel = name === null ? undefined : panelRegistry[name];
      return Panel ? <Panel {...props} /> : null;
    };
  },
}));

vi.mock("@/hooks/useFireData", () => ({
  useFireData: () => ({
    data: { type: "FeatureCollection", features: [] },
    count: 0,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

import LayerManager from "@/components/map/LayerManager";
import PanelManager from "@/components/map/PanelManager";
import { SoilPanel } from "@/components/panels/SoilPanel";
import { WaterPanel } from "@/components/panels/WaterPanel";

// The props are whatever PanelManager passed; a stubbed dynamic import has no type for
// them, so each adapter re-asserts its own panel's props at that one boundary.
panelRegistry.SoilPanel = function SoilPanelAdapter(props) {
  return <SoilPanel {...(props as unknown as React.ComponentProps<typeof SoilPanel>)} />;
};
panelRegistry.WaterPanel = function WaterPanelAdapter(props) {
  return <WaterPanel {...(props as unknown as React.ComponentProps<typeof WaterPanel>)} />;
};

/** Every operation the client actually sent, in order. */
interface RecordedOperation {
  path: string;
  input: unknown;
}

const recordedOperations: RecordedOperation[] = [];

const EMPTY_PROXIED_COLLECTION = {
  type: "FeatureCollection",
  features: [],
  availability: "published",
  reason: null,
  truncated: false,
  unreadableGeometries: 0,
  observedAt: null,
  revision: null,
};

/** Procedures whose callers iterate the result as an array; everything else is a collection. */
const ARRAY_PROCEDURES = new Set([
  "environmental.getStreamflow",
  "environmental.getGroundwater",
  "wildfire.getWeatherForBbox",
]);

/** Records each operation and answers it out-of-band, the way a network link would. */
function recordingLink(): TRPCLink<AppRouter> {
  return () =>
    ({ op }) =>
      observable((observer) => {
        recordedOperations.push({ path: op.path, input: op.input });
        const timer = setTimeout(() => {
          observer.next({
            result: {
              data: ARRAY_PROCEDURES.has(op.path) ? [] : EMPTY_PROXIED_COLLECTION,
            },
          });
          observer.complete();
        }, 0);
        return () => clearTimeout(timer);
      });
}

/** Operations sent for one procedure. */
function operationsFor(path: string): RecordedOperation[] {
  return recordedOperations.filter((operation) => operation.path === path);
}

/** A MapLibre stand-in with only what LayerManager's own effects call. */
function createFakeMap() {
  return {
    on: () => {},
    off: () => {},
    isStyleLoaded: () => true,
    getLayer: () => true,
    setLayoutProperty: () => {},
  };
}

/**
 * The map and the panel toolbar in one tree, on one QueryClient. `staleTime` mirrors
 * `src/lib/providers.tsx`, because inheriting that 60 s default instead of the feed's own
 * is precisely the drift these cases exist to catch.
 */
async function renderMapAndPanels(): Promise<QueryClient> {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { staleTime: 60 * 1000, refetchOnWindowFocus: false } },
  });
  const trpcClient = trpc.createClient({ links: [recordingLink()] });
  const fakeMap = createFakeMap() as unknown as maplibregl.Map;

  await act(async () => {
    render(
      <trpc.Provider client={trpcClient} queryClient={queryClient}>
        <QueryClientProvider client={queryClient}>
          <MapProvider value={fakeMap}>
            <LayerManager />
            <PanelManager />
          </MapProvider>
        </QueryClientProvider>
      </trpc.Provider>
    );
  });

  return queryClient;
}

/** Waits out every in-flight query, so nothing lands after the assertions run. */
async function settle(queryClient: QueryClient): Promise<void> {
  await waitFor(() => expect(queryClient.isFetching()).toBe(0));
}

/** Cache entries belonging to one procedure. One entry means one shared query. */
function cacheEntriesFor(queryClient: QueryClient, procedure: string) {
  return queryClient
    .getQueryCache()
    .getAll()
    .filter((query) => JSON.stringify(query.queryKey[0]).includes(procedure));
}

const INITIAL_MAP_STATE = useMapStore.getState();
const INITIAL_PANEL_STATE = usePanelStore.getState();

beforeEach(() => {
  recordedOperations.length = 0;
  useMapStore.setState(INITIAL_MAP_STATE, true);
  usePanelStore.setState(INITIAL_PANEL_STATE, true);
});

afterEach(() => {
  vi.clearAllMocks();
  recordedOperations.length = 0;
  useMapStore.setState(INITIAL_MAP_STATE, true);
  usePanelStore.setState(INITIAL_PANEL_STATE, true);
});

describe("viewport-proxied feeds are fetched once for the map and its panel", () => {
  it("gives the SSURGO survey one query entry, one request and one set of options", async () => {
    useMapStore.setState({ activeLayers: ["soil-survey"] });
    usePanelStore.setState({ openPanel: "soil" });

    const queryClient = await renderMapAndPanels();

    await settle(queryClient);
    expect(operationsFor("environmental.getSoilSurvey").length).toBeGreaterThan(0);

    const entries = cacheEntriesFor(queryClient, "getSoilSurvey");
    // Two entries would mean the map and the panel asked USDA for different things --
    // a different bbox derivation, or a different fallback for a missing one.
    expect(entries).toHaveLength(1);
    // Both callers must actually be on it: one observer would mean the panel never
    // queried at all, which would satisfy the count above for the wrong reason.
    expect(entries[0].observers).toHaveLength(2);
    // staleTime is per-observer in TanStack v5. Divergence here is invisible on first
    // paint and shows up minutes later as a second full upstream fetch.
    expect(new Set(entries[0].observers.map((o) => o.options.staleTime))).toEqual(
      new Set([24 * 60 * 60 * 1000])
    );
    expect(new Set(entries[0].observers.map((o) => o.options.retry))).toEqual(
      new Set([1])
    );
    expect(operationsFor("environmental.getSoilSurvey")).toHaveLength(1);
  });

  it("gives the HUC12 boundaries one query entry, one request and one set of options", async () => {
    useMapStore.setState({ activeLayers: ["watersheds"] });
    usePanelStore.setState({ openPanel: "water" });

    const queryClient = await renderMapAndPanels();

    await settle(queryClient);
    expect(operationsFor("environmental.getWatersheds").length).toBeGreaterThan(0);

    const entries = cacheEntriesFor(queryClient, "getWatersheds");
    expect(entries).toHaveLength(1);
    expect(entries[0].observers).toHaveLength(2);
    expect(new Set(entries[0].observers.map((o) => o.options.staleTime))).toEqual(
      new Set([60 * 60 * 1000])
    );
    expect(new Set(entries[0].observers.map((o) => o.options.retry))).toEqual(
      new Set([1])
    );
    // The ~5 MB HUC12 viewport is fetched once, however many surfaces read it.
    expect(operationsFor("environmental.getWatersheds")).toHaveLength(1);
  });

  it("keeps one entry even with the panel closed, since the key cannot depend on it", async () => {
    useMapStore.setState({ activeLayers: ["soil-survey"] });
    usePanelStore.setState({ openPanel: null });

    const queryClient = await renderMapAndPanels();

    await settle(queryClient);
    expect(operationsFor("environmental.getSoilSurvey").length).toBeGreaterThan(0);

    // A disabled observer still registers on its key, so a divergent input would show up
    // here as a second entry even though the closed panel never fetches.
    const entries = cacheEntriesFor(queryClient, "getSoilSurvey");
    expect(entries).toHaveLength(1);
    expect(entries[0].observers).toHaveLength(2);
    expect(operationsFor("environmental.getSoilSurvey")).toHaveLength(1);
  });
});
