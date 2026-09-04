import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, waitFor } from "@testing-library/react";
import { observable } from "@trpc/server/observable";
import type { TRPCLink } from "@trpc/client";
import type maplibregl from "maplibre-gl";
import { SOIL_FIELD_MEASURE_IDS } from "@/lib/environmental/soil-field";
import { CLIMATE_FIELD_SIGNAL_IDS } from "@/lib/environmental/climate-field";
import { MapProvider } from "@/lib/map/map-context";
import { trpc } from "@/lib/trpc/client";
import { useMapStore } from "@/stores/map-store";
import { usePanelStore } from "@/stores/panel-store";
import type { AppRouter } from "@/lib/server/trpc/router";

/**
 * The map and the dock section that describes it must issue ONE react-query entry per proxied
 * feed, not two that merely look alike. Nothing about `useQuery` is mocked here: a
 * recording tRPC link stands in for the network, so the queryKey, the `staleTime` and the
 * `retry` these cases read are the ones react-query actually resolved. Mocking `useQuery`
 * would let the two callers drift apart and still pass.
 *
 * The callers are the real `LayerManager` and the real `LayerPanel`, because the bbox a
 * details region receives is the dock's derivation, and that derivation is half of what must
 * not drift. (Until 2026-08-08 the second caller was `PanelManager` and its right-hand sheets;
 * the sheets are gone and the dock is where those queries live now.)
 *
 * The other half of the contract these cases pin is LAZINESS, and it moved with the merge: a
 * sheet stayed mounted with `enabled: open` false, so a closed panel still registered a
 * disabled observer. A collapsed dock section is not mounted at all. Both properties matter --
 * one entry when the section is open, and no observer at all when it is not, or every dock
 * open would fire all eight sections' queries at once.
 */

/**
 * The dock's Teams section resolves the active team from the auth store first and the session
 * second, so `useSession` throws without a `<SessionProvider>`. Mocked rather than wrapped:
 * nothing in these cases depends on a session, and a real provider would add a network-shaped
 * dependency to a file whose whole point is that the only network stand-in is the tRPC link
 * below. A null session is the signed-out case, and the store answers for the team either way.
 */
vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: null, status: "unauthenticated" }),
}));

const panelRegistry = vi.hoisted(
  () => ({}) as Record<string, (props: Record<string, unknown>) => React.ReactNode>
);

/**
 * The map sub-layers that must really render, because they own a proxied read.
 *
 * Only `ClimateFieldLayers`, and only since 2026-08-10. Every other proxied query in this file
 * is issued by `LayerManager` itself, so stubbing its sub-layers away costs nothing; the nine
 * NASA POWER rows moved one component down when each signal got its own day, and their reads
 * moved with them. Stubbing that component out would leave the map registering no climate
 * observer at all -- and the "one entry, two observers" case would then pass with one observer
 * short and prove nothing.
 */
const layerRegistry = vi.hoisted(
  () => ({}) as Record<string, (props: Record<string, unknown>) => React.ReactNode>
);

/**
 * `next/dynamic` resolves to the real details region for the two under test, to the real
 * climate layer container, and to nothing for everything else: the remaining map sub-layers
 * want a full MapLibre instance, and the other regions are not what is being measured. The
 * loader's source text is the only handle on identity a stubbed dynamic import has -- the
 * module path survives the transform. The lookup is deferred to render time because
 * `dynamic()` runs while the dock is imported, before the registries below are filled.
 */
vi.mock("next/dynamic", () => ({
  default: (loader: unknown) => {
    const source = String(loader);
    const panelName = /panels[/\\](\w+)/.exec(source)?.[1] ?? null;
    const layerName = /layers[/\\](\w+)/.exec(source)?.[1] ?? null;
    return function DynamicComponentUnderTest(props: Record<string, unknown>) {
      const Component =
        (panelName === null ? undefined : panelRegistry[panelName]) ??
        (layerName === null ? undefined : layerRegistry[layerName]);
      return Component ? <Component {...props} /> : null;
    };
  },
}));

/**
 * The leaf that actually talks to MapLibre, stubbed to nothing.
 *
 * `ClimateFieldLayers` is what this file needs to run -- it is where the nine per-signal reads
 * are issued -- and it touches no map at all; it renders one `ClimateFieldLayer` per signal and
 * those are what call addSource/addLayer against a style the fake map here does not have.
 * Stubbing the leaf keeps the queries real and the MapLibre calls out of it.
 */
vi.mock("@/components/map/layers/ClimateFieldLayer", () => ({
  ClimateFieldLayer: () => null,
}));

// No `useFireData` stub here since the 2026-09-01 cutover: fire detections are a tRPC read
// like every other dated feed, so they go through the recording link below and the map/panel
// pair is measured by the same "one entry, two observers" rule as the rest of this file.

import LayerManager from "@/components/map/LayerManager";
import { LayerPanel } from "@/components/map/layer-panel/LayerPanel";
import { ClimateFieldLayers } from "@/components/map/layers/ClimateFieldLayers";
import { ClimateDetails } from "@/components/panels/ClimateDetails";
import { FireDetails } from "@/components/panels/FireDetails";
import { SoilDetails } from "@/components/panels/SoilDetails";
import { WaterDetails } from "@/components/panels/WaterDetails";

// The props are whatever the dock passed; a stubbed dynamic import has no type for them, so
// each adapter re-asserts its own region's props at that one boundary.
panelRegistry.SoilDetails = function SoilDetailsAdapter(props) {
  return <SoilDetails {...(props as unknown as React.ComponentProps<typeof SoilDetails>)} />;
};
panelRegistry.WaterDetails = function WaterDetailsAdapter(props) {
  return <WaterDetails {...(props as unknown as React.ComponentProps<typeof WaterDetails>)} />;
};
panelRegistry.FireDetails = function FireDetailsAdapter(props) {
  return <FireDetails {...(props as unknown as React.ComponentProps<typeof FireDetails>)} />;
};
layerRegistry.ClimateFieldLayers = function ClimateFieldLayersAdapter(props) {
  return (
    <ClimateFieldLayers
      {...(props as unknown as React.ComponentProps<typeof ClimateFieldLayers>)}
    />
  );
};
panelRegistry.ClimateDetails = function ClimateDetailsAdapter(props) {
  return (
    <ClimateDetails {...(props as unknown as React.ComponentProps<typeof ClimateDetails>)} />
  );
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

/** Procedures that still expose the legacy array/collection shapes in this test. */
const ARRAY_PROCEDURES = new Set(["environmental.getGroundwater"]);

/**
 * The two collection reads whose panels caption themselves from a SUPPORT ENVELOPE.
 *
 * `SoilDetails` prints `field.support.cellWidthDegrees` on any rung that is not `detail`, and
 * `granularity` is what selects that branch -- so a stand-in collection with neither field takes
 * the aggregated branch and dereferences an envelope that is not there, which throws through the
 * render and leaves every query observer in this tree unregistered. The reader never sends that
 * shape: `soilFieldSupport` fills an envelope in EVERY arm, the empty ones included, precisely so
 * a panel can say which rung answered and at what pitch without guessing.
 *
 * Answered as the detail rung with no bands, which is the least this test can say and still be a
 * shape the server could have produced: nothing here is about the legend.
 */
const PARQUET_COLLECTION_PROCEDURES = new Set([
  "environmental.getSoilField",
  "environmental.getClimateField",
]);

const DETAIL_RUNG_SUPPORT = {
  zoomTier: 13,
  supportKind: "tessellated_cell",
  supportId: "viewport-sharing-test:2026-08-28:z13",
  origin: "cell_center",
  cellWidthDegrees: 0.25,
  cellHeightDegrees: 0.25,
  aggregationMethod: "mean",
  contributorCount: 0,
  provenance: {
    sourceLayer: "viewport-sharing-test",
    observedDay: "2026-08-28",
    newestObservedAt: null,
    attribution: "test",
  },
};

const PARQUET_ARRAY_PROCEDURES = new Set([
  "environmental.getStreamflow",
  "environmental.getDroughtClassification",
  "wildfire.getWeatherForBbox",
]);

/** Parquet readers whose `ready` payload is a day WINDOW rather than a bare row array. */
const PARQUET_WINDOW_PROCEDURES = new Set(["wildfire.getFireDetections"]);

/** The nearest-observation read `FireDetails` also makes; answered as nothing published. */
const POINT_WEATHER_PROCEDURES = new Set(["wildfire.getWeatherForPoint"]);

/** Records each operation and answers it out-of-band, the way a network link would. */
function recordingLink(): TRPCLink<AppRouter> {
  return () =>
    ({ op }) =>
      observable((observer) => {
        recordedOperations.push({ path: op.path, input: op.input });
        const timer = setTimeout(() => {
          const data = PARQUET_ARRAY_PROCEDURES.has(op.path)
            ? {
                state: "ready",
                requestedDay: "2026-08-28",
                servedDay: "2026-08-28",
                data: [],
                truncated: false,
              }
            : PARQUET_WINDOW_PROCEDURES.has(op.path)
              ? {
                  state: "ready",
                  requestedDay: "2026-08-28",
                  servedDay: "2026-08-28",
                  truncated: false,
                  data: {
                    firstDay: "2026-08-28",
                    lastDay: "2026-08-28",
                    cells: [],
                    days: [],
                  },
                }
              : POINT_WEATHER_PROCEDURES.has(op.path)
                ? {
                    availability: "unavailable",
                    reason: "no_fresh_weather_observation_published",
                    observation: null,
                  }
                : ARRAY_PROCEDURES.has(op.path)
                  ? []
                  : PARQUET_COLLECTION_PROCEDURES.has(op.path)
                    ? {
                        ...EMPTY_PROXIED_COLLECTION,
                        granularity: "detail",
                        zoomTier: 13,
                        cellCount: 0,
                        bands: [],
                        observedDay: null,
                        requestedDay: "2026-08-28",
                        newestAvailableDay: null,
                        maxObservationAgeDays: 0,
                        support: DETAIL_RUNG_SUPPORT,
                      }
                    : EMPTY_PROXIED_COLLECTION;
          observer.next({
            result: {
              data,
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

/** A MapLibre stand-in with only what LayerManager's and the dock's own effects call. */
function createFakeMap() {
  return {
    on: () => {},
    off: () => {},
    isStyleLoaded: () => true,
    getLayer: () => true,
    setLayoutProperty: () => {},
    // The four Parquet-fed GeoJSON sources LayerManager fills for the style-baked layers that
    // left Martin in wave C. `undefined` is the honest answer from a map with no style spec
    // behind it, and `applyParquetFeatureData` skips a missing source rather than erroring --
    // the same guard `applyVisibility` gets from `getLayer`. Without the method at all the fake
    // throws before any query runs, which is what these cases exist to observe.
    getSource: () => undefined,
    // LayerManager's opacity applier writes every style-baked layer on style.load and again
    // once the style settles; without this the fake map throws before any query runs.
    setPaintProperty: () => {},
    setFilter: () => {},
    // The dock shifts the camera's optical centre by setting padding when it opens.
    easeTo: () => {},
    jumpTo: () => {},
  };
}

/**
 * The map and the dock in one tree, on one QueryClient. `staleTime` mirrors
 * `src/lib/providers.tsx`, because inheriting that 60 s default instead of the feed's own
 * is precisely the drift these cases exist to catch.
 */
async function renderMapAndDock(): Promise<QueryClient> {
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
            <LayerPanel />
          </MapProvider>
        </QueryClientProvider>
      </trpc.Provider>
    );
  });

  return queryClient;
}

/** The dock open with one section's details expanded, which is what mounts that region. */
function openDockAt(section: "soil" | "water" | "climate" | "fire"): void {
  usePanelStore.setState({ layerPanelOpen: true, expandedDetails: [section] });
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

describe("viewport-proxied feeds are fetched once for the map and its dock section", () => {
  it("gives the SSURGO survey one query entry, one request and one set of options", async () => {
    useMapStore.setState({ activeLayers: ["soil-survey"] });
    openDockAt("soil");

    const queryClient = await renderMapAndDock();

    await settle(queryClient);
    expect(operationsFor("environmental.getSoilSurvey").length).toBeGreaterThan(0);

    const entries = cacheEntriesFor(queryClient, "getSoilSurvey");
    // Two entries would mean the map and the dock asked USDA for different things --
    // a different bbox derivation, or a different fallback for a missing one.
    expect(entries).toHaveLength(1);
    // Both callers must actually be on it: one observer would mean the section never
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

  // The HUC12 boundaries left this file's premise behind: the map draws them from
  // geo.watershed_tiles() now, so the dock's list is the ONLY caller of the proxy and
  // there is nothing left to share. That is not a weaker guarantee than the two-observer
  // one above -- the map's copy of the request is the one that could never succeed, since
  // environmental.getWatersheds caps a request at 1 square degree against a ~767 sq-deg
  // viewport bbox. A second observer reappearing here means the map went back to proxying.
  it("leaves the HUC12 proxy to the dock alone, now that the map draws it from tiles", async () => {
    useMapStore.setState({ activeLayers: ["watersheds"] });
    openDockAt("water");

    const queryClient = await renderMapAndDock();

    await settle(queryClient);

    const entries = cacheEntriesFor(queryClient, "getWatersheds");
    expect(entries).toHaveLength(1);
    expect(entries[0].observers).toHaveLength(1);
    expect(entries[0].observers[0].options.staleTime).toBe(60 * 60 * 1000);
    expect(entries[0].observers[0].options.retry).toBe(1);
    // And no request leaves the client at all. This viewport is ~767 sq deg against the
    // procedure's 1 sq deg ceiling, so the fetch could only ever come back a validation
    // error -- which the panel then had to render as though the provider were down. The
    // observer stays (the dock is still asking the question), the doomed round trip does not.
    expect(operationsFor("environmental.getWatersheds")).toHaveLength(0);
  });

  it("shares one zoom-routed Parquet streamflow query between the map and Water details", async () => {
    useMapStore.setState({ activeLayers: ["water"] });
    openDockAt("water");

    const queryClient = await renderMapAndDock();
    await settle(queryClient);

    const entries = cacheEntriesFor(queryClient, "getStreamflow");
    expect(entries).toHaveLength(1);
    expect(entries[0].observers).toHaveLength(2);
    expect(operationsFor("environmental.getStreamflow")).toHaveLength(1);
    const input = operationsFor("environmental.getStreamflow")[0].input as {
      bbox?: string;
      zoom?: number;
    };
    expect(input.bbox).toBeTypeOf("string");
    expect(input.zoom).toBeTypeOf("number");
    expect(Number.isFinite(input.zoom)).toBe(true);
  });

  // Warehouse-backed rather than proxied, and keyed on five inputs rather than two (bbox,
  // measure, date, depth AND zoom). More inputs is more surface for the map and the dock to
  // disagree on, which is exactly why it belongs in this file. `measure` is in the key, so
  // the soil fields are one entry each by design -- what must not happen is the map and the
  // dock splitting ONE measure across two.
  //
  // Counted off `SOIL_FIELD_MEASURE_IDS` rather than hard-coded: this case asserted a literal
  // `2` until `soil-vpd` became a third measure, at which point a correct dock (which renders
  // a section per measure, straight off that list) failed a test that had simply gone stale.
  it("gives each soil field one query entry, one request and one set of options", async () => {
    const activeMeasures = ["moisture", "temperature"];
    useMapStore.setState({ activeLayers: ["soil-moisture", "soil-temperature"] });
    openDockAt("soil");

    const queryClient = await renderMapAndDock();

    await settle(queryClient);
    expect(operationsFor("environmental.getSoilField").length).toBeGreaterThan(0);

    const entries = cacheEntriesFor(queryClient, "getSoilField");
    const measureOf = (entry: (typeof entries)[number]) =>
      (entry.queryKey[1] as { input?: { measure?: string } })?.input?.measure;

    // One key per measure the dock knows about, never one per reader.
    expect(entries).toHaveLength(SOIL_FIELD_MEASURE_IDS.length);
    expect(new Set(entries.map(measureOf))).toEqual(new Set(SOIL_FIELD_MEASURE_IDS));

    // Both readers land on every measure's key, whether or not that field is being drawn: a
    // disabled observer still registers, which is precisely what makes a divergent bbox or
    // depth visible here even for a field nobody has switched on.
    for (const entry of entries) {
      expect(entry.observers, measureOf(entry)).toHaveLength(2);
      expect(new Set(entry.observers.map((o) => o.options.staleTime))).toEqual(
        new Set([60 * 60 * 1000])
      );
    }

    // Registered is not fetched: one request per DRAWN field, and none for the measure whose
    // layer is off, however many keys are registered above.
    expect(operationsFor("environmental.getSoilField")).toHaveLength(activeMeasures.length);
    expect(
      new Set(
        operationsFor("environmental.getSoilField").map(
          (operation) => (operation.input as { measure?: string })?.measure
        )
      )
    ).toEqual(new Set(activeMeasures));
  });

  /**
   * The NASA POWER lane, keyed like the soil field's minus `depth`, which has no analogue here.
   * `zoom` IS in the key: it selects the one physical rung, so the map and the dock must agree
   * on it -- the note that stood here ("one serving tier, zoom deliberately absent") described
   * the reader's hard-coded z13, not the warehouse, which publishes z13/z9/z5/z0 for these lanes.
   * The sharing hazard is the same one -- the section describing a signal must key its read
   * exactly as the map drawing it does.
   *
   * Two signals switched on rather than one, because the sharing question got harder when the
   * lane split into nine rows on 2026-08-10. Each signal is its own layer with its own day and
   * its own form, so the map now issues one read PER DRAWN SIGNAL; what must not happen is a
   * second entry per signal, which is what a section deriving its own bbox or its own day would
   * produce.
   *
   * The same registered-is-not-fetched shape the soil field asserts above: the map registers a
   * key for every signal it knows, drawn or not, because a disabled observer still registers --
   * and that is exactly what makes a divergent bbox or day visible here even for a row nobody
   * has switched on. The DRAWN signals are the ones that carry two observers and cost a request.
   */
  it("gives each drawn climate signal one query entry, one request and one set of options", async () => {
    const drawnSignals = ["air-temperature", "precipitation"];
    useMapStore.setState({
      activeLayers: drawnSignals.map((signal) => `climate-${signal}`),
    });
    openDockAt("climate");

    const queryClient = await renderMapAndDock();

    await settle(queryClient);

    /** The signal a cache entry was keyed on, read off the key react-query actually built. */
    const signalOf = (entry: { queryKey: unknown }) =>
      (JSON.parse(JSON.stringify(entry.queryKey))[1]?.input as { signal?: string })?.signal;

    const entries = cacheEntriesFor(queryClient, "getClimateField");
    // One key per signal the dock knows about, never one per reader.
    expect(entries).toHaveLength(CLIMATE_FIELD_SIGNAL_IDS.length);
    expect(new Set(entries.map(signalOf))).toEqual(new Set(CLIMATE_FIELD_SIGNAL_IDS));

    for (const entry of entries) {
      const signal = signalOf(entry);
      // Two observers -- the map's layer and the section's report -- on the drawn signals'
      // keys; one, the map's own disabled observer, on the seven that are off. A section that
      // derived its own bbox or its own day would show up here as a drawn signal with one.
      expect(entry.observers, signal).toHaveLength(
        drawnSignals.includes(signal ?? "") ? 2 : 1
      );
      expect(new Set(entry.observers.map((o) => o.options.staleTime))).toEqual(
        new Set([60 * 60 * 1000])
      );
      expect(new Set(entry.observers.map((o) => o.options.retry))).toEqual(new Set([1]));
    }

    const operations = operationsFor("environmental.getClimateField");
    const inputs = operations.map(
      (operation) => operation.input as { signal?: string; zoom?: number }
    );
    // One request per DRAWN signal, and none for the seven that are off, however many keys
    // are registered above.
    expect(operations).toHaveLength(drawnSignals.length);
    expect(new Set(inputs.map((input) => input.signal))).toEqual(new Set(drawnSignals));
    // Zoom is in the key, and BOTH readers must have taken it from the one `useViewportBounds()`
    // derivation: a second derivation would show up here as two zooms for one viewport, which is
    // two physical rungs and therefore two different aggregations drawn at once.
    for (const input of inputs) expect(input.zoom).toBeTypeOf("number");
    expect(new Set(inputs.map((input) => input.zoom))).toHaveProperty("size", 1);
  });

  /**
   * The fire lane, joined to this contract by the 2026-09-01 Parquet cutover.
   *
   * Until then `FireDetails` and `LayerManager` each called `useFireData` — two private
   * `fetch` lanes with two ETag caches, agreeing on the day only because both happened to
   * read `useDebouncedLayerDay("fire")` and happening to disagree about the viewport always,
   * since that route took no bbox at all. `useParquetFireDetections` reads the day, the bbox
   * and the zoom ITSELF, so there is no argument for the two callers to differ on: one entry,
   * two observers, one request.
   */
  it("gives the fire map layer and the fire section one query entry and one request", async () => {
    useMapStore.setState({ activeLayers: ["fire"] });
    openDockAt("fire");

    const queryClient = await renderMapAndDock();
    await settle(queryClient);

    const entries = cacheEntriesFor(queryClient, "getFireDetections");
    expect(entries).toHaveLength(1);
    // The map's layer and the panel's count. A second entry here would mean the caption and
    // the cells beside it describe different days, different viewports, or both.
    expect(entries[0].observers).toHaveLength(2);
    expect(operationsFor("wildfire.getFireDetections")).toHaveLength(1);

    // The request itself carries all three key inputs, which is what `/api/fires` could not do.
    const [operation] = operationsFor("wildfire.getFireDetections");
    const input = operation.input as { bbox?: string; zoom?: number; dayRange?: number };
    expect(input.bbox).toBeTypeOf("string");
    expect(input.zoom).toBeTypeOf("number");
    expect(input.dayRange).toBe(1);
  });

  /**
   * The laziness half of the contract, restated for the dock.
   *
   * A sheet was always mounted and gated its queries with `enabled: open`, so a closed panel
   * still registered a *disabled* observer on the key -- which is what the predecessor of this
   * case asserted (two observers, one request). A collapsed dock section is not mounted, so it
   * registers nothing at all, and the entry belongs to the map alone.
   *
   * This is the property that makes one dock affordable: eight sections mounted on every dock
   * open would issue every section's queries at once, which is the regression to watch for
   * here. A second observer appearing with nothing expanded means a details region has escaped
   * its section and is mounting with the dock.
   */
  it("registers no dock observer at all while the section is collapsed", async () => {
    useMapStore.setState({ activeLayers: ["soil-survey"] });
    usePanelStore.setState({ layerPanelOpen: true, expandedDetails: [] });

    const queryClient = await renderMapAndDock();

    await settle(queryClient);
    expect(operationsFor("environmental.getSoilSurvey").length).toBeGreaterThan(0);

    const entries = cacheEntriesFor(queryClient, "getSoilSurvey");
    expect(entries).toHaveLength(1);
    expect(entries[0].observers).toHaveLength(1);
    expect(operationsFor("environmental.getSoilSurvey")).toHaveLength(1);
  });

  // And the same key on the way back: expanding the section joins the map's entry rather than
  // opening a second one, which is what a divergent bbox or zoom derivation would look like.
  it("joins the map's existing entry when the section is expanded later", async () => {
    useMapStore.setState({ activeLayers: ["soil-survey"] });
    usePanelStore.setState({ layerPanelOpen: true, expandedDetails: [] });

    const queryClient = await renderMapAndDock();
    await settle(queryClient);

    await act(async () => {
      usePanelStore.getState().toggleDetails("soil");
    });
    await settle(queryClient);

    const entries = cacheEntriesFor(queryClient, "getSoilSurvey");
    expect(entries).toHaveLength(1);
    expect(entries[0].observers).toHaveLength(2);
    // The second observer read the cache rather than the network: same key, still fresh.
    expect(operationsFor("environmental.getSoilSurvey")).toHaveLength(1);
  });
});
