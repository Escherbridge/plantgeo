import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Characterization tests for LayerManager: the ORDERED log of everything it does to the map, the
 * order it issues its reads in, and what it hands each child layer -- recorded per scenario and
 * pinned as explicit arrays, so a refactor that moves code between hooks must reproduce the log
 * exactly. See src/components/map/AGENTS.md §LayerManager structure.
 *
 * One shared log carries three kinds of line, in the order they happened:
 *   `map ...`    -- a call on the fake MapLibre map (mutations, listener registration, setData);
 *   `query ...`  -- a tRPC `useQuery` call (procedure, input, enabled, keepPreviousData);
 *   `-- ...`     -- a scenario marker this file writes between steps.
 * Consecutive identical render passes are folded into one block with a repeat count, so the
 * render COUNT stays asserted without the arrays growing per re-render.
 */

vi.mock("next-auth/react", () => ({
  useSession: () => ({ status: "unauthenticated" }),
}));
vi.mock("@/hooks/useCropCover", () => ({
  useCropCover: () => ({
    enabled: false,
    day: null,
    availability: { data: undefined, isError: false },
    query: { data: undefined, isError: false, isFetching: false },
  }),
}));

const recorder = vi.hoisted(() => ({
  log: [] as string[],
  renders: [] as { component: string; props: Record<string, unknown> }[],
}));

vi.mock("next/dynamic", () => ({
  default: (loader: unknown) => {
    const source = String(loader);
    const component = /(?:layers|land-context)[/\\](\w+)/.exec(source)?.[1] ?? "unknown";
    return function DynamicLayerStub(props: Record<string, unknown>) {
      recorder.renders.push({ component, props });
      return null;
    };
  },
}));

const queryAnswers = vi.hoisted(() => ({} as Record<string, unknown>));

vi.mock("@/lib/trpc/client", async () => {
  const { keepPreviousData: keepPrevious } = await import("@tanstack/react-query");
  const procedure = (name: string, fallback: unknown) => ({
    useQuery: (input?: unknown, options?: { enabled?: boolean; placeholderData?: unknown }) => {
      recorder.log.push(
        `query ${name} ${JSON.stringify(input ?? null)} enabled=${String(options?.enabled)}` +
          `${options?.placeholderData === keepPrevious ? " keep" : ""}`
      );
      return name in queryAnswers ? queryAnswers[name] : fallback;
    },
  });
  return {
    trpc: {
      environmental: {
        getDroughtClassification: procedure("getDroughtClassification", { data: undefined }),
        getStreamflow: procedure("getStreamflow", { data: [] }),
        getGroundwater: procedure("getGroundwater", { data: [] }),
        getSensorStations: procedure("getSensorStations", { data: undefined }),
        getEvacuationZones: procedure("getEvacuationZones", { data: undefined }),
        getBurnSeverity: procedure("getBurnSeverity", { data: undefined }),
        getWatershedBoundaries: procedure("getWatershedBoundaries", { data: undefined }),
        getFirePerimeters: procedure("getFirePerimeters", { data: undefined }),
        getSoilSurvey: procedure("getSoilSurvey", { data: undefined }),
        getSoilField: procedure("getSoilField", { data: undefined }),
        getPublishedSoilRasters: procedure("getPublishedSoilRasters", { data: [] }),
        getClimateField: procedure("getClimateField", { data: undefined }),
        getVegetationIndex: procedure("getVegetationIndex", { data: undefined }),
      },
      wildfire: {
        getWeatherForBbox: procedure("getWeatherForBbox", { data: [] }),
        getFireDetections: procedure("getFireDetections", { data: undefined }),
      },
      interventions: {
        listMySubmissions: procedure("listMySubmissions", {
          data: undefined,
          isLoading: false,
          isError: false,
        }),
        listProposed: procedure("listProposed", {
          data: undefined,
          isLoading: false,
          isError: false,
        }),
      },
    },
  };
});

vi.mock("@/hooks/useLandContextViewport", () => ({
  useLandContextViewport: () => ({
    bbox: null,
    servedZoomTier: null,
    state: "no_group_enabled",
    query: { data: undefined, isError: false, isFetching: false },
  }),
}));

import { act } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import { MapProvider } from "@/lib/map/map-context";
import { useLayerStore } from "@/stores/layer-store";
import { useMapStore } from "@/stores/map-store";
import { useSoilStore } from "@/stores/soil-store";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import { SCRUB_SETTLE_MS, useDrawnLayerDayStore } from "@/stores/useMetricAtDate";
import type { SliderCapabilities, SliderLayerCapability } from "@/types/time-slider";
import type maplibregl from "maplibre-gl";
import LayerManager from "@/components/map/LayerManager";

/** A MapLibre stand-in that appends every call LayerManager can make on it to the shared log. */
function createRecordingMap() {
  const listeners = new Map<string, Set<() => void>>();
  const sources = new Map<string, { setData: (collection: GeoJSON.FeatureCollection) => void }>();
  let styleLoaded = false;
  const write = (line: string) => recorder.log.push(`map ${line}`);
  const target = (type: string, layerOrHandler: unknown, handler: unknown) =>
    typeof handler === "function" ? `${type}@${String(layerOrHandler)}` : type;

  return {
    on(type: string, layerOrHandler: unknown, handler?: unknown) {
      write(`on ${target(type, layerOrHandler, handler)}`);
      if (typeof handler === "function") return;
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type)!.add(layerOrHandler as () => void);
    },
    off(type: string, layerOrHandler: unknown, handler?: unknown) {
      write(`off ${target(type, layerOrHandler, handler)}`);
      if (typeof handler !== "function") listeners.get(type)?.delete(layerOrHandler as () => void);
    },
    isStyleLoaded: () => styleLoaded,
    getLayer: () => true,
    getSource(sourceId: string) {
      let source = sources.get(sourceId);
      if (source === undefined) {
        source = {
          setData: (collection) =>
            write(`setData ${sourceId} features=${collection.features.length}`),
        };
        sources.set(sourceId, source);
      }
      return source;
    },
    setLayoutProperty: (layerId: string, property: string, value: unknown) =>
      write(`setLayoutProperty ${layerId} ${property}=${JSON.stringify(value)}`),
    setPaintProperty: (layerId: string, property: string, value: unknown) =>
      write(`setPaintProperty ${layerId} ${property}=${JSON.stringify(value)}`),
    setFilter: (layerId: string, filter: unknown) =>
      write(`setFilter ${layerId} ${JSON.stringify(filter ?? null)}`),
    setStyleLoaded(value: boolean) {
      styleLoaded = value;
    },
    emit(type: string) {
      for (const handler of Array.from(listeners.get(type) ?? [])) handler();
    },
  };
}

type RecordingMap = ReturnType<typeof createRecordingMap>;

function mark(step: string) {
  recorder.log.push(`-- ${step}`);
}

/** Lets every settle timer and every coalesced animation frame run, inside act(). */
async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, SCRUB_SETTLE_MS + 20));
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  });
}

function renderLayerManager(fakeMap: RecordingMap) {
  return renderWithProviders(
    <MapProvider value={fakeMap as unknown as maplibregl.Map}>
      <LayerManager />
    </MapProvider>
  );
}

/** The viewport bbox every read keys on, which never moves in these scenarios. */
const VIEWPORT_BBOX = /"bbox":"[-0-9.,]+"/g;

/**
 * The log with each render pass's reads folded. A pass is the consecutive run of `query` lines
 * that begins at `listMySubmissions`, the first read LayerManager's body issues. A pass identical
 * to the one before it collapses to a single `query pass unchanged` line, so the render COUNT is
 * still pinned while the arrays stay readable.
 */
function foldedLog(): string[] {
  const folded: string[] = [];
  let previousPass: string | null = null;
  let currentPass: string[] | null = null;
  const closePass = () => {
    if (currentPass === null) return;
    const key = currentPass.join("\n");
    if (key === previousPass) folded.push("query pass unchanged");
    else folded.push(...currentPass);
    previousPass = key;
    currentPass = null;
  };
  for (const rawLine of recorder.log) {
    const line = rawLine.replace(VIEWPORT_BBOX, '"bbox":"<viewport>"');
    if (line.startsWith("query listMySubmissions")) {
      closePass();
      currentPass = [line];
    } else if (line.startsWith("query ") && currentPass !== null) {
      currentPass.push(line);
    } else {
      closePass();
      folded.push(line);
    }
  }
  closePass();
  return folded;
}

function summarize(value: unknown): string {
  if (value === null || value === undefined) return String(value);
  if (typeof value === "function") return "fn";
  if (Array.isArray(value)) return `[${value.length}]`;
  if (typeof value === "object") {
    const features = (value as { features?: unknown[] }).features;
    if (Array.isArray(features)) return `fc(${features.length})`;
    if ("getSource" in (value as object)) return "map";
    return JSON.stringify(value);
  }
  return JSON.stringify(value);
}

/** What each child layer was handed on the LAST render pass, one line per child. */
function lastChildProps(): string[] {
  let start = -1;
  for (let index = recorder.renders.length - 1; index >= 0; index--) {
    if (recorder.renders[index].component === "FireLayer") {
      start = index;
      break;
    }
  }
  return recorder.renders.slice(start).map(({ component, props }) => {
    const fields = Object.keys(props)
      .sort()
      .map((key) => `${key}=${summarize(props[key])}`);
    return `${component} ${fields.join(" ")}`;
  });
}

function drawnDays(): string {
  return JSON.stringify(useDrawnLayerDayStore.getState().drawnDays);
}

const SERVER_CURRENT_DATE = "2026-08-04";

function publishedLayer(layerName: string, latestObservedDate: string): SliderLayerCapability {
  return {
    layerName,
    temporalKind: "daily_series",
    forecastHorizonDays: 0,
    forecastVariants: [],
    earliestObservedDate: "2026-05-24",
    latestObservedDate,
    coverageGaps: [],
    thinRanges: [],
    describedFromDay: null,
  };
}

const capabilities: SliderCapabilities = {
  serverCurrentDate: SERVER_CURRENT_DATE,
  futureAxisDays: 0,
  streamsUnavailable: false,
  layers: [
    publishedLayer("fire-detections", SERVER_CURRENT_DATE),
    publishedLayer("water-gauges-daily", "2026-07-30"),
    publishedLayer("vegetation", "2026-06-14"),
    publishedLayer("weather-observations", "2026-08-02"),
    publishedLayer("fire-perimeters", "2026-07-21"),
    publishedLayer("evacuation-zones", "2026-07-22"),
    publishedLayer("burn-severity", "2026-07-23"),
    publishedLayer("sensors", "2026-07-24"),
  ],
};

const INITIAL_MAP_STATE = useMapStore.getState();
const INITIAL_SOIL_STATE = useSoilStore.getState();
const INITIAL_LAYER_STATE = useLayerStore.getState();

/** Mounts against a style that is already loaded, lets everything settle, then clears the log. */
async function mountSettled(): Promise<RecordingMap> {
  const fakeMap = createRecordingMap();
  fakeMap.setStyleLoaded(true);
  renderLayerManager(fakeMap);
  await settle();
  recorder.log.length = 0;
  recorder.renders.length = 0;
  return fakeMap;
}

beforeEach(() => {
  useMapStore.setState(INITIAL_MAP_STATE, true);
  useSoilStore.setState(INITIAL_SOIL_STATE, true);
  useLayerStore.setState({ ...INITIAL_LAYER_STATE, layerOpacity: {} }, true);
  useTimeSliderStore.setState({ layerDates: {}, forecastVariant: "monte_carlo", capabilities: null });
  useDrawnLayerDayStore.setState({ drawnDays: {}, publications: {} });
  for (const key of Object.keys(queryAnswers)) delete queryAnswers[key];
  recorder.log.length = 0;
  recorder.renders.length = 0;
});

afterEach(() => {
  useMapStore.setState(INITIAL_MAP_STATE, true);
  useSoilStore.setState(INITIAL_SOIL_STATE, true);
  useLayerStore.setState({ ...INITIAL_LAYER_STATE, layerOpacity: {} }, true);
});

type Observed = { log: string[]; children: string[]; drawnDays: string; banner: string };

function observe(): Observed {
  return {
    log: foldedLog(),
    children: lastChildProps(),
    drawnDays: drawnDays(),
    // The fault/notice stack is real DOM (ParquetLayerFaultBanner is a static import).
    banner: document.body.textContent ?? "",
  };
}

const TRIANGLE: GeoJSON.Polygon = {
  type: "Polygon",
  coordinates: [
    [
      [-122.5, 43.8],
      [-122.4, 43.8],
      [-122.4, 43.9],
      [-122.5, 43.8],
    ],
  ],
};

function landed<T>(data: T) {
  return { data, isSuccess: true, isFetching: false, isPlaceholderData: false };
}

/**
 * Capture mode: `LM_CAPTURE=<file>` writes what each scenario observed instead of asserting, which
 * is how the EXPECTED table below was produced against the pre-refactor component.
 */
const captured: Record<string, Observed> = {};
async function pin(name: string, observed: Observed) {
  if (process.env.LM_CAPTURE) {
    captured[name] = observed;
    const { writeFileSync } = await import("node:fs");
    writeFileSync(process.env.LM_CAPTURE, JSON.stringify(captured, null, 2));
    return;
  }
  expect(observed).toEqual(EXPECTED[name]);
}

describe("LayerManager characterization: the ordered map-call and read log", () => {
  it("mounts with defaults, then converges on style.load and styledata", async () => {
    const fakeMap = createRecordingMap();
    mark("render");
    renderLayerManager(fakeMap);
    mark("style.load while sources are still loading");
    act(() => fakeMap.emit("style.load"));
    mark("styledata once the style reports loaded");
    act(() => {
      fakeMap.setStyleLoaded(true);
      fakeMap.emit("styledata");
    });
    await settle();
    await pin("mount", observe());
  });

  it("toggles a style-baked Martin tile layer (interventions) on and off", async () => {
    await mountSettled();
    mark("interventions on");
    act(() => useMapStore.setState({ activeLayers: ["interventions"] }));
    await settle();
    mark("interventions off");
    act(() => useMapStore.setState({ activeLayers: [] }));
    await settle();
    await pin("styleBakedTile", observe());
  });

  it("toggles Parquet-backed layers (drought, component-mounted; fire-perimeters, style-baked source)", async () => {
    await mountSettled();
    queryAnswers.getDroughtClassification = landed({
      state: "ready",
      requestedDay: "2026-07-28",
      servedDay: "2026-07-28",
      truncated: false,
      data: [
        {
          areaId: "qa-drought",
          validDate: "2026-07-28",
          droughtCategory: 1,
          sourceUrl: "https://example.test/drought",
          ingestedAt: "2026-07-28T00:00:00Z",
          geometry: TRIANGLE,
        },
      ],
    });
    // Truncated, so the wave-C lane report reaches the fault banner as a notice.
    queryAnswers.getFirePerimeters = landed({
      state: "ready",
      requestedDay: "2026-08-04",
      servedDay: "2026-08-02",
      truncated: true,
      data: [
        {
          featureId: "feat-1",
          uniqueFireIdentifier: "2026-ORWIF-000412",
          snapshotDay: "2026-08-02",
          observedDay: "2026-08-01",
          severity: "high",
          geometry: TRIANGLE,
        },
      ],
    });
    mark("drought + fire-perimeters on");
    act(() => useMapStore.setState({ activeLayers: ["drought", "fire-perimeters"] }));
    await settle();
    const whileOn = [...lastChildProps(), `banner: ${document.body.textContent ?? ""}`];
    mark("both off");
    act(() => useMapStore.setState({ activeLayers: [] }));
    await settle();
    await pin("parquet", { ...observe(), children: [...whileOn, "-- after off", ...lastChildProps()] });
  });

  it("toggles the soil-field triplet on and off", async () => {
    await mountSettled();
    mark("soil triplet on");
    act(() =>
      useMapStore.setState({ activeLayers: ["soil-moisture", "soil-temperature", "soil-vpd"] })
    );
    await settle();
    const whileOn = lastChildProps();
    mark("soil triplet off");
    act(() => useMapStore.setState({ activeLayers: [] }));
    await settle();
    await pin("soilTriplet", { ...observe(), children: [...whileOn, "-- after off", ...lastChildProps()] });
  });

  it("scales opacity for a style-baked layer and a component-mounted one", async () => {
    await mountSettled();
    mark("dim watersheds and drought");
    act(() => {
      useLayerStore.getState().setLayerOpacity("watersheds", 0.5);
      useLayerStore.getState().setLayerOpacity("drought", 0.3);
    });
    await settle();
    await pin("opacity", observe());
  });

  it("moves a style-baked layer's filter and a feed's request when their own rows are scrubbed", async () => {
    useTimeSliderStore.setState({ capabilities });
    useMapStore.setState({ activeLayers: ["fire-perimeters", "water"] });
    await mountSettled();
    mark("scrub fire-perimeters and water");
    act(() => {
      useTimeSliderStore.getState().setLayerDate("fire-perimeters", "2026-07-01");
      useTimeSliderStore.getState().setLayerDate("water", "2026-07-02");
    });
    await settle();
    await pin("dateChange", observe());
  });

  it("re-applies data, visibility, filter and opacity after a basemap swap", async () => {
    useTimeSliderStore.setState({ capabilities });
    useMapStore.setState({ activeLayers: ["sensors", "drought"] });
    useLayerStore.getState().setLayerOpacity("sensors", 0.6);
    const fakeMap = await mountSettled();
    mark("style swap");
    act(() => {
      fakeMap.setStyleLoaded(false);
      fakeMap.emit("style.load");
    });
    mark("new style settles");
    act(() => {
      fakeMap.setStyleLoaded(true);
      fakeMap.emit("styledata");
    });
    await settle();
    await pin("styleSwap", observe());
  });
});

/**
 * What each scenario observed against the pre-split LayerManager (ac37b690). Regenerate only for a
 * deliberate behaviour change, with LM_CAPTURE=<file>, and review the diff line by line.
 */
const EXPECTED: Record<string, Observed> = {
  "mount": {
    "log": [
      "-- render",
      "query listMySubmissions {} enabled=false",
      "query listProposed {} enabled=false",
      "query getPublishedSoilRasters null enabled=undefined",
      "query getFireDetections {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062,\"dayRange\":1} enabled=false keep",
      "query getDroughtClassification {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSensorStations {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getEvacuationZones {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getBurnSeverity {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getFirePerimeters {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getWatershedBoundaries {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getStreamflow {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getGroundwater {\"bbox\":\"<viewport>\"} enabled=false keep",
      "query getVegetationIndex {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilSurvey {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"moisture\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"temperature\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"vpd\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getWeatherForBbox {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "map on click@interventions",
      "map on click@interventions-outline",
      "map on click@interventions-points",
      "map on click@intervention-drafts-fill",
      "map on click@intervention-drafts-outline",
      "map on click@intervention-drafts-points",
      "map on style.load",
      "map on styledata",
      "map setData sensor-station-features features=0",
      "map setData evacuation-zone-features features=0",
      "map setData burn-severity-features features=0",
      "map setData watershed-features features=0",
      "map setData fire-perimeter-features features=0",
      "map setData intervention-drafts-source features=0",
      "map setLayoutProperty fire-perimeters visibility=\"none\"",
      "map setLayoutProperty fire-perimeters-outline visibility=\"none\"",
      "map setLayoutProperty sensors visibility=\"none\"",
      "map setLayoutProperty watersheds-fill visibility=\"none\"",
      "map setLayoutProperty watersheds-outline visibility=\"none\"",
      "map setLayoutProperty interventions visibility=\"none\"",
      "map setLayoutProperty interventions-outline visibility=\"none\"",
      "map setLayoutProperty interventions-points visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-fill visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-outline visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-points visibility=\"none\"",
      "map setLayoutProperty evacuation-zones visibility=\"none\"",
      "map setLayoutProperty evacuation-zones-outline visibility=\"none\"",
      "map setLayoutProperty burn-severity visibility=\"none\"",
      "map setLayoutProperty burn-severity-outline visibility=\"none\"",
      "map setFilter fire-perimeters null",
      "map setFilter fire-perimeters-outline null",
      "map setFilter evacuation-zones null",
      "map setFilter evacuation-zones-outline null",
      "map setFilter burn-severity null",
      "map setFilter burn-severity-outline null",
      "map setFilter sensors null",
      "-- style.load while sources are still loading",
      "map setData sensor-station-features features=0",
      "map setData evacuation-zone-features features=0",
      "map setData burn-severity-features features=0",
      "map setData watershed-features features=0",
      "map setData fire-perimeter-features features=0",
      "map setData intervention-drafts-source features=0",
      "map setLayoutProperty fire-perimeters visibility=\"none\"",
      "map setLayoutProperty fire-perimeters-outline visibility=\"none\"",
      "map setLayoutProperty sensors visibility=\"none\"",
      "map setLayoutProperty watersheds-fill visibility=\"none\"",
      "map setLayoutProperty watersheds-outline visibility=\"none\"",
      "map setLayoutProperty interventions visibility=\"none\"",
      "map setLayoutProperty interventions-outline visibility=\"none\"",
      "map setLayoutProperty interventions-points visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-fill visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-outline visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-points visibility=\"none\"",
      "map setLayoutProperty evacuation-zones visibility=\"none\"",
      "map setLayoutProperty evacuation-zones-outline visibility=\"none\"",
      "map setLayoutProperty burn-severity visibility=\"none\"",
      "map setLayoutProperty burn-severity-outline visibility=\"none\"",
      "map setFilter fire-perimeters null",
      "map setFilter fire-perimeters-outline null",
      "map setFilter evacuation-zones null",
      "map setFilter evacuation-zones-outline null",
      "map setFilter burn-severity null",
      "map setFilter burn-severity-outline null",
      "map setFilter sensors null",
      "map setPaintProperty fire-perimeters fill-opacity=0.5",
      "map setPaintProperty fire-perimeters-outline line-opacity=1",
      "map setPaintProperty sensors circle-opacity=0.85",
      "map setPaintProperty sensors circle-stroke-opacity=1",
      "map setPaintProperty watersheds-fill fill-opacity=0.05",
      "map setPaintProperty watersheds-outline line-opacity=0.8",
      "map setPaintProperty interventions fill-opacity=0.4",
      "map setPaintProperty interventions-outline line-opacity=1",
      "map setPaintProperty interventions-points circle-opacity=0.9",
      "map setPaintProperty interventions-points circle-stroke-opacity=1",
      "map setPaintProperty intervention-drafts-fill fill-opacity=0.2",
      "map setPaintProperty intervention-drafts-outline line-opacity=1",
      "map setPaintProperty intervention-drafts-points circle-opacity=0.45",
      "map setPaintProperty intervention-drafts-points circle-stroke-opacity=1",
      "map setPaintProperty evacuation-zones fill-opacity=0.45",
      "map setPaintProperty evacuation-zones-outline line-opacity=1",
      "map setPaintProperty burn-severity fill-opacity=0.4",
      "map setPaintProperty burn-severity-outline line-opacity=0.8",
      "-- styledata once the style reports loaded",
      "map setLayoutProperty fire-perimeters visibility=\"none\"",
      "map setLayoutProperty fire-perimeters-outline visibility=\"none\"",
      "map setLayoutProperty sensors visibility=\"none\"",
      "map setLayoutProperty watersheds-fill visibility=\"none\"",
      "map setLayoutProperty watersheds-outline visibility=\"none\"",
      "map setLayoutProperty interventions visibility=\"none\"",
      "map setLayoutProperty interventions-outline visibility=\"none\"",
      "map setLayoutProperty interventions-points visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-fill visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-outline visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-points visibility=\"none\"",
      "map setLayoutProperty evacuation-zones visibility=\"none\"",
      "map setLayoutProperty evacuation-zones-outline visibility=\"none\"",
      "map setLayoutProperty burn-severity visibility=\"none\"",
      "map setLayoutProperty burn-severity-outline visibility=\"none\"",
      "query pass unchanged",
      "map setData sensor-station-features features=0",
      "map setData evacuation-zone-features features=0",
      "map setData burn-severity-features features=0",
      "map setData watershed-features features=0",
      "map setData fire-perimeter-features features=0",
      "map setData intervention-drafts-source features=0",
      "map setLayoutProperty fire-perimeters visibility=\"none\"",
      "map setLayoutProperty fire-perimeters-outline visibility=\"none\"",
      "map setLayoutProperty sensors visibility=\"none\"",
      "map setLayoutProperty watersheds-fill visibility=\"none\"",
      "map setLayoutProperty watersheds-outline visibility=\"none\"",
      "map setLayoutProperty interventions visibility=\"none\"",
      "map setLayoutProperty interventions-outline visibility=\"none\"",
      "map setLayoutProperty interventions-points visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-fill visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-outline visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-points visibility=\"none\"",
      "map setLayoutProperty evacuation-zones visibility=\"none\"",
      "map setLayoutProperty evacuation-zones-outline visibility=\"none\"",
      "map setLayoutProperty burn-severity visibility=\"none\"",
      "map setLayoutProperty burn-severity-outline visibility=\"none\"",
      "map setFilter fire-perimeters null",
      "map setFilter fire-perimeters-outline null",
      "map setFilter evacuation-zones null",
      "map setFilter evacuation-zones-outline null",
      "map setFilter burn-severity null",
      "map setFilter burn-severity-outline null",
      "map setFilter sensors null",
      "map setFilter fire-perimeters null",
      "map setFilter fire-perimeters-outline null",
      "map setFilter evacuation-zones null",
      "map setFilter evacuation-zones-outline null",
      "map setFilter burn-severity null",
      "map setFilter burn-severity-outline null",
      "map setFilter sensors null",
      "map setPaintProperty fire-perimeters fill-opacity=0.5",
      "map setPaintProperty fire-perimeters-outline line-opacity=1",
      "map setPaintProperty sensors circle-opacity=0.85",
      "map setPaintProperty sensors circle-stroke-opacity=1",
      "map setPaintProperty watersheds-fill fill-opacity=0.05",
      "map setPaintProperty watersheds-outline line-opacity=0.8",
      "map setPaintProperty interventions fill-opacity=0.4",
      "map setPaintProperty interventions-outline line-opacity=1",
      "map setPaintProperty interventions-points circle-opacity=0.9",
      "map setPaintProperty interventions-points circle-stroke-opacity=1",
      "map setPaintProperty intervention-drafts-fill fill-opacity=0.2",
      "map setPaintProperty intervention-drafts-outline line-opacity=1",
      "map setPaintProperty intervention-drafts-points circle-opacity=0.45",
      "map setPaintProperty intervention-drafts-points circle-stroke-opacity=1",
      "map setPaintProperty evacuation-zones fill-opacity=0.45",
      "map setPaintProperty evacuation-zones-outline line-opacity=1",
      "map setPaintProperty burn-severity fill-opacity=0.4",
      "map setPaintProperty burn-severity-outline line-opacity=0.8",
      "map setPaintProperty fire-perimeters fill-opacity=0.5",
      "map setPaintProperty fire-perimeters-outline line-opacity=1",
      "map setPaintProperty sensors circle-opacity=0.85",
      "map setPaintProperty sensors circle-stroke-opacity=1",
      "map setPaintProperty watersheds-fill fill-opacity=0.05",
      "map setPaintProperty watersheds-outline line-opacity=0.8",
      "map setPaintProperty interventions fill-opacity=0.4",
      "map setPaintProperty interventions-outline line-opacity=1",
      "map setPaintProperty interventions-points circle-opacity=0.9",
      "map setPaintProperty interventions-points circle-stroke-opacity=1",
      "map setPaintProperty intervention-drafts-fill fill-opacity=0.2",
      "map setPaintProperty intervention-drafts-outline line-opacity=1",
      "map setPaintProperty intervention-drafts-points circle-opacity=0.45",
      "map setPaintProperty intervention-drafts-points circle-stroke-opacity=1",
      "map setPaintProperty evacuation-zones fill-opacity=0.45",
      "map setPaintProperty evacuation-zones-outline line-opacity=1",
      "map setPaintProperty burn-severity fill-opacity=0.4",
      "map setPaintProperty burn-severity-outline line-opacity=0.8"
    ],
    "children": [
      "FireLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "WaterLayer aggregateCells=[0] gauges=[0] map=map opacityScale=1 visible=false wells=[0]",
      "DroughtLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "VegetationLayer geojson=fc(0) map=map mode=\"ndvi\" month=null ndviMode=\"absolute\" opacityScale=1 showNDWI=false visible=false year=null",
      "SoilSurveyLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"moisture\" opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"temperature\" opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"vpd\" opacityScale=1 visible=false",
      "ClimateFieldLayers bbox=\"-129.859167,39.570417,-106.140833,51.429583\" map=map zoom=5.92392237523062",
      "DemandHeatmapLayer bbox=\"-129.859167,39.570417,-106.140833,51.429583\" map=map opacityScale=1 visible=false zoom=5.92392237523062",
      "WeatherLayer data=[0] map=map opacityScale=1 visible=false",
      "LandContextViewportLayer geojson=null map=map visible=false",
      "CropCoverLayer geojson=null visible=false",
      "QueryPointLayer map=map point=null"
    ],
    "drawnDays": "{}",
    "banner": ""
  },
  "styleBakedTile": {
    "log": [
      "-- interventions on",
      "query listMySubmissions {} enabled=false",
      "query listProposed {} enabled=false",
      "query getPublishedSoilRasters null enabled=undefined",
      "query getFireDetections {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062,\"dayRange\":1} enabled=false keep",
      "query getDroughtClassification {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSensorStations {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getEvacuationZones {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getBurnSeverity {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getFirePerimeters {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getWatershedBoundaries {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getStreamflow {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getGroundwater {\"bbox\":\"<viewport>\"} enabled=false keep",
      "query getVegetationIndex {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilSurvey {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"moisture\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"temperature\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"vpd\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getWeatherForBbox {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "map setLayoutProperty fire-perimeters visibility=\"none\"",
      "map setLayoutProperty fire-perimeters-outline visibility=\"none\"",
      "map setLayoutProperty sensors visibility=\"none\"",
      "map setLayoutProperty watersheds-fill visibility=\"none\"",
      "map setLayoutProperty watersheds-outline visibility=\"none\"",
      "map setLayoutProperty interventions visibility=\"visible\"",
      "map setLayoutProperty interventions-outline visibility=\"visible\"",
      "map setLayoutProperty interventions-points visibility=\"visible\"",
      "map setLayoutProperty intervention-drafts-fill visibility=\"visible\"",
      "map setLayoutProperty intervention-drafts-outline visibility=\"visible\"",
      "map setLayoutProperty intervention-drafts-points visibility=\"visible\"",
      "map setLayoutProperty evacuation-zones visibility=\"none\"",
      "map setLayoutProperty evacuation-zones-outline visibility=\"none\"",
      "map setLayoutProperty burn-severity visibility=\"none\"",
      "map setLayoutProperty burn-severity-outline visibility=\"none\"",
      "query pass unchanged",
      "-- interventions off",
      "query pass unchanged",
      "map setLayoutProperty fire-perimeters visibility=\"none\"",
      "map setLayoutProperty fire-perimeters-outline visibility=\"none\"",
      "map setLayoutProperty sensors visibility=\"none\"",
      "map setLayoutProperty watersheds-fill visibility=\"none\"",
      "map setLayoutProperty watersheds-outline visibility=\"none\"",
      "map setLayoutProperty interventions visibility=\"none\"",
      "map setLayoutProperty interventions-outline visibility=\"none\"",
      "map setLayoutProperty interventions-points visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-fill visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-outline visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-points visibility=\"none\"",
      "map setLayoutProperty evacuation-zones visibility=\"none\"",
      "map setLayoutProperty evacuation-zones-outline visibility=\"none\"",
      "map setLayoutProperty burn-severity visibility=\"none\"",
      "map setLayoutProperty burn-severity-outline visibility=\"none\""
    ],
    "children": [
      "FireLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "WaterLayer aggregateCells=[0] gauges=[0] map=map opacityScale=1 visible=false wells=[0]",
      "DroughtLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "VegetationLayer geojson=fc(0) map=map mode=\"ndvi\" month=null ndviMode=\"absolute\" opacityScale=1 showNDWI=false visible=false year=null",
      "SoilSurveyLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"moisture\" opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"temperature\" opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"vpd\" opacityScale=1 visible=false",
      "ClimateFieldLayers bbox=\"-129.859167,39.570417,-106.140833,51.429583\" map=map zoom=5.92392237523062",
      "DemandHeatmapLayer bbox=\"-129.859167,39.570417,-106.140833,51.429583\" map=map opacityScale=1 visible=false zoom=5.92392237523062",
      "WeatherLayer data=[0] map=map opacityScale=1 visible=false",
      "LandContextViewportLayer geojson=null map=map visible=false",
      "CropCoverLayer geojson=null visible=false",
      "QueryPointLayer map=map point=null"
    ],
    "drawnDays": "{}",
    "banner": ""
  },
  "parquet": {
    "log": [
      "-- drought + fire-perimeters on",
      "query listMySubmissions {} enabled=false",
      "query listProposed {} enabled=false",
      "query getPublishedSoilRasters null enabled=undefined",
      "query getFireDetections {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062,\"dayRange\":1} enabled=false keep",
      "query getDroughtClassification {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=true keep",
      "query getSensorStations {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getEvacuationZones {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getBurnSeverity {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getFirePerimeters {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=true keep",
      "query getWatershedBoundaries {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getStreamflow {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getGroundwater {\"bbox\":\"<viewport>\"} enabled=false keep",
      "query getVegetationIndex {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilSurvey {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"moisture\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"temperature\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"vpd\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getWeatherForBbox {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "map setData sensor-station-features features=0",
      "map setData evacuation-zone-features features=0",
      "map setData burn-severity-features features=0",
      "map setData watershed-features features=0",
      "map setData fire-perimeter-features features=1",
      "map setLayoutProperty fire-perimeters visibility=\"visible\"",
      "map setLayoutProperty fire-perimeters-outline visibility=\"visible\"",
      "map setLayoutProperty sensors visibility=\"none\"",
      "map setLayoutProperty watersheds-fill visibility=\"none\"",
      "map setLayoutProperty watersheds-outline visibility=\"none\"",
      "map setLayoutProperty interventions visibility=\"none\"",
      "map setLayoutProperty interventions-outline visibility=\"none\"",
      "map setLayoutProperty interventions-points visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-fill visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-outline visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-points visibility=\"none\"",
      "map setLayoutProperty evacuation-zones visibility=\"none\"",
      "map setLayoutProperty evacuation-zones-outline visibility=\"none\"",
      "map setLayoutProperty burn-severity visibility=\"none\"",
      "map setLayoutProperty burn-severity-outline visibility=\"none\"",
      "query pass unchanged",
      "-- both off",
      "query listMySubmissions {} enabled=false",
      "query listProposed {} enabled=false",
      "query getPublishedSoilRasters null enabled=undefined",
      "query getFireDetections {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062,\"dayRange\":1} enabled=false keep",
      "query getDroughtClassification {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSensorStations {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getEvacuationZones {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getBurnSeverity {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getFirePerimeters {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getWatershedBoundaries {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getStreamflow {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getGroundwater {\"bbox\":\"<viewport>\"} enabled=false keep",
      "query getVegetationIndex {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilSurvey {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"moisture\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"temperature\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"vpd\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getWeatherForBbox {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "map setLayoutProperty fire-perimeters visibility=\"none\"",
      "map setLayoutProperty fire-perimeters-outline visibility=\"none\"",
      "map setLayoutProperty sensors visibility=\"none\"",
      "map setLayoutProperty watersheds-fill visibility=\"none\"",
      "map setLayoutProperty watersheds-outline visibility=\"none\"",
      "map setLayoutProperty interventions visibility=\"none\"",
      "map setLayoutProperty interventions-outline visibility=\"none\"",
      "map setLayoutProperty interventions-points visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-fill visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-outline visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-points visibility=\"none\"",
      "map setLayoutProperty evacuation-zones visibility=\"none\"",
      "map setLayoutProperty evacuation-zones-outline visibility=\"none\"",
      "map setLayoutProperty burn-severity visibility=\"none\"",
      "map setLayoutProperty burn-severity-outline visibility=\"none\""
    ],
    "children": [
      "FireLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "WaterLayer aggregateCells=[0] gauges=[0] map=map opacityScale=1 visible=false wells=[0]",
      "DroughtLayer geojson=fc(1) map=map opacityScale=1 visible=true",
      "VegetationLayer geojson=fc(0) map=map mode=\"ndvi\" month=null ndviMode=\"absolute\" opacityScale=1 showNDWI=false visible=false year=null",
      "SoilSurveyLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"moisture\" opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"temperature\" opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"vpd\" opacityScale=1 visible=false",
      "ClimateFieldLayers bbox=\"-129.859167,39.570417,-106.140833,51.429583\" map=map zoom=5.92392237523062",
      "DemandHeatmapLayer bbox=\"-129.859167,39.570417,-106.140833,51.429583\" map=map opacityScale=1 visible=false zoom=5.92392237523062",
      "WeatherLayer data=[0] map=map opacityScale=1 visible=false",
      "LandContextViewportLayer geojson=null map=map visible=false",
      "CropCoverLayer geojson=null visible=false",
      "QueryPointLayer map=map point=null",
      "banner: The Parquet row budget was reached. The fire perimeters drawn are a subset of this viewport.",
      "-- after off",
      "FireLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "WaterLayer aggregateCells=[0] gauges=[0] map=map opacityScale=1 visible=false wells=[0]",
      "DroughtLayer geojson=fc(1) map=map opacityScale=1 visible=false",
      "VegetationLayer geojson=fc(0) map=map mode=\"ndvi\" month=null ndviMode=\"absolute\" opacityScale=1 showNDWI=false visible=false year=null",
      "SoilSurveyLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"moisture\" opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"temperature\" opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"vpd\" opacityScale=1 visible=false",
      "ClimateFieldLayers bbox=\"-129.859167,39.570417,-106.140833,51.429583\" map=map zoom=5.92392237523062",
      "DemandHeatmapLayer bbox=\"-129.859167,39.570417,-106.140833,51.429583\" map=map opacityScale=1 visible=false zoom=5.92392237523062",
      "WeatherLayer data=[0] map=map opacityScale=1 visible=false",
      "LandContextViewportLayer geojson=null map=map visible=false",
      "CropCoverLayer geojson=null visible=false",
      "QueryPointLayer map=map point=null"
    ],
    "drawnDays": "{}",
    "banner": ""
  },
  "soilTriplet": {
    "log": [
      "-- soil triplet on",
      "query listMySubmissions {} enabled=false",
      "query listProposed {} enabled=false",
      "query getPublishedSoilRasters null enabled=undefined",
      "query getFireDetections {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062,\"dayRange\":1} enabled=false keep",
      "query getDroughtClassification {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSensorStations {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getEvacuationZones {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getBurnSeverity {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getFirePerimeters {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getWatershedBoundaries {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getStreamflow {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getGroundwater {\"bbox\":\"<viewport>\"} enabled=false keep",
      "query getVegetationIndex {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilSurvey {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"moisture\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=true keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"temperature\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=true keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"vpd\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=true keep",
      "query getWeatherForBbox {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "map setLayoutProperty fire-perimeters visibility=\"none\"",
      "map setLayoutProperty fire-perimeters-outline visibility=\"none\"",
      "map setLayoutProperty sensors visibility=\"none\"",
      "map setLayoutProperty watersheds-fill visibility=\"none\"",
      "map setLayoutProperty watersheds-outline visibility=\"none\"",
      "map setLayoutProperty interventions visibility=\"none\"",
      "map setLayoutProperty interventions-outline visibility=\"none\"",
      "map setLayoutProperty interventions-points visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-fill visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-outline visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-points visibility=\"none\"",
      "map setLayoutProperty evacuation-zones visibility=\"none\"",
      "map setLayoutProperty evacuation-zones-outline visibility=\"none\"",
      "map setLayoutProperty burn-severity visibility=\"none\"",
      "map setLayoutProperty burn-severity-outline visibility=\"none\"",
      "query pass unchanged",
      "-- soil triplet off",
      "query listMySubmissions {} enabled=false",
      "query listProposed {} enabled=false",
      "query getPublishedSoilRasters null enabled=undefined",
      "query getFireDetections {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062,\"dayRange\":1} enabled=false keep",
      "query getDroughtClassification {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSensorStations {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getEvacuationZones {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getBurnSeverity {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getFirePerimeters {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getWatershedBoundaries {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getStreamflow {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getGroundwater {\"bbox\":\"<viewport>\"} enabled=false keep",
      "query getVegetationIndex {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilSurvey {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"moisture\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"temperature\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"vpd\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getWeatherForBbox {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "map setLayoutProperty fire-perimeters visibility=\"none\"",
      "map setLayoutProperty fire-perimeters-outline visibility=\"none\"",
      "map setLayoutProperty sensors visibility=\"none\"",
      "map setLayoutProperty watersheds-fill visibility=\"none\"",
      "map setLayoutProperty watersheds-outline visibility=\"none\"",
      "map setLayoutProperty interventions visibility=\"none\"",
      "map setLayoutProperty interventions-outline visibility=\"none\"",
      "map setLayoutProperty interventions-points visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-fill visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-outline visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-points visibility=\"none\"",
      "map setLayoutProperty evacuation-zones visibility=\"none\"",
      "map setLayoutProperty evacuation-zones-outline visibility=\"none\"",
      "map setLayoutProperty burn-severity visibility=\"none\"",
      "map setLayoutProperty burn-severity-outline visibility=\"none\""
    ],
    "children": [
      "FireLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "WaterLayer aggregateCells=[0] gauges=[0] map=map opacityScale=1 visible=false wells=[0]",
      "DroughtLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "VegetationLayer geojson=fc(0) map=map mode=\"ndvi\" month=null ndviMode=\"absolute\" opacityScale=1 showNDWI=false visible=false year=null",
      "SoilSurveyLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"moisture\" opacityScale=1 visible=true",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"temperature\" opacityScale=1 visible=true",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"vpd\" opacityScale=1 visible=true",
      "ClimateFieldLayers bbox=\"-129.859167,39.570417,-106.140833,51.429583\" map=map zoom=5.92392237523062",
      "DemandHeatmapLayer bbox=\"-129.859167,39.570417,-106.140833,51.429583\" map=map opacityScale=1 visible=false zoom=5.92392237523062",
      "WeatherLayer data=[0] map=map opacityScale=1 visible=false",
      "LandContextViewportLayer geojson=null map=map visible=false",
      "CropCoverLayer geojson=null visible=false",
      "QueryPointLayer map=map point=null",
      "-- after off",
      "FireLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "WaterLayer aggregateCells=[0] gauges=[0] map=map opacityScale=1 visible=false wells=[0]",
      "DroughtLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "VegetationLayer geojson=fc(0) map=map mode=\"ndvi\" month=null ndviMode=\"absolute\" opacityScale=1 showNDWI=false visible=false year=null",
      "SoilSurveyLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"moisture\" opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"temperature\" opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"vpd\" opacityScale=1 visible=false",
      "ClimateFieldLayers bbox=\"-129.859167,39.570417,-106.140833,51.429583\" map=map zoom=5.92392237523062",
      "DemandHeatmapLayer bbox=\"-129.859167,39.570417,-106.140833,51.429583\" map=map opacityScale=1 visible=false zoom=5.92392237523062",
      "WeatherLayer data=[0] map=map opacityScale=1 visible=false",
      "LandContextViewportLayer geojson=null map=map visible=false",
      "CropCoverLayer geojson=null visible=false",
      "QueryPointLayer map=map point=null"
    ],
    "drawnDays": "{}",
    "banner": ""
  },
  "opacity": {
    "log": [
      "-- dim watersheds and drought",
      "query listMySubmissions {} enabled=false",
      "query listProposed {} enabled=false",
      "query getPublishedSoilRasters null enabled=undefined",
      "query getFireDetections {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062,\"dayRange\":1} enabled=false keep",
      "query getDroughtClassification {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSensorStations {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getEvacuationZones {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getBurnSeverity {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getFirePerimeters {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getWatershedBoundaries {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getStreamflow {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getGroundwater {\"bbox\":\"<viewport>\"} enabled=false keep",
      "query getVegetationIndex {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilSurvey {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"moisture\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"temperature\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"vpd\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getWeatherForBbox {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query pass unchanged",
      "map setPaintProperty fire-perimeters fill-opacity=0.5",
      "map setPaintProperty fire-perimeters-outline line-opacity=1",
      "map setPaintProperty sensors circle-opacity=0.85",
      "map setPaintProperty sensors circle-stroke-opacity=1",
      "map setPaintProperty watersheds-fill fill-opacity=0.025",
      "map setPaintProperty watersheds-outline line-opacity=0.4",
      "map setPaintProperty interventions fill-opacity=0.4",
      "map setPaintProperty interventions-outline line-opacity=1",
      "map setPaintProperty interventions-points circle-opacity=0.9",
      "map setPaintProperty interventions-points circle-stroke-opacity=1",
      "map setPaintProperty intervention-drafts-fill fill-opacity=0.2",
      "map setPaintProperty intervention-drafts-outline line-opacity=1",
      "map setPaintProperty intervention-drafts-points circle-opacity=0.45",
      "map setPaintProperty intervention-drafts-points circle-stroke-opacity=1",
      "map setPaintProperty evacuation-zones fill-opacity=0.45",
      "map setPaintProperty evacuation-zones-outline line-opacity=1",
      "map setPaintProperty burn-severity fill-opacity=0.4",
      "map setPaintProperty burn-severity-outline line-opacity=0.8"
    ],
    "children": [
      "FireLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "WaterLayer aggregateCells=[0] gauges=[0] map=map opacityScale=1 visible=false wells=[0]",
      "DroughtLayer geojson=fc(0) map=map opacityScale=0.3 visible=false",
      "VegetationLayer geojson=fc(0) map=map mode=\"ndvi\" month=null ndviMode=\"absolute\" opacityScale=1 showNDWI=false visible=false year=null",
      "SoilSurveyLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"moisture\" opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"temperature\" opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"vpd\" opacityScale=1 visible=false",
      "ClimateFieldLayers bbox=\"-129.859167,39.570417,-106.140833,51.429583\" map=map zoom=5.92392237523062",
      "DemandHeatmapLayer bbox=\"-129.859167,39.570417,-106.140833,51.429583\" map=map opacityScale=1 visible=false zoom=5.92392237523062",
      "WeatherLayer data=[0] map=map opacityScale=1 visible=false",
      "LandContextViewportLayer geojson=null map=map visible=false",
      "CropCoverLayer geojson=null visible=false",
      "QueryPointLayer map=map point=null"
    ],
    "drawnDays": "{}",
    "banner": ""
  },
  "dateChange": {
    "log": [
      "-- scrub fire-perimeters and water",
      "query listMySubmissions {} enabled=false",
      "query listProposed {} enabled=false",
      "query getPublishedSoilRasters null enabled=undefined",
      "query getFireDetections {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062,\"dayRange\":1} enabled=false keep",
      "query getDroughtClassification {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSensorStations {\"bbox\":\"<viewport>\",\"date\":\"2026-07-24\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getEvacuationZones {\"bbox\":\"<viewport>\",\"date\":\"2026-07-22\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getBurnSeverity {\"bbox\":\"<viewport>\",\"date\":\"2026-07-23\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getFirePerimeters {\"bbox\":\"<viewport>\",\"date\":\"2026-07-01\",\"zoom\":5.92392237523062} enabled=true keep",
      "query getWatershedBoundaries {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getStreamflow {\"bbox\":\"<viewport>\",\"date\":\"2026-07-02\",\"zoom\":5.92392237523062} enabled=true keep",
      "query getGroundwater {\"bbox\":\"<viewport>\",\"date\":\"2026-07-02\"} enabled=true keep",
      "query getVegetationIndex {\"bbox\":\"<viewport>\",\"date\":\"2026-06-14\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilSurvey {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"moisture\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"temperature\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"vpd\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getWeatherForBbox {\"bbox\":\"<viewport>\",\"date\":\"2026-08-02\",\"zoom\":5.92392237523062} enabled=false keep",
      "map setFilter fire-perimeters [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-01\"]]",
      "map setFilter fire-perimeters-outline [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-01\"]]",
      "map setFilter evacuation-zones [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-22\"]]",
      "map setFilter evacuation-zones-outline [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-22\"]]",
      "map setFilter burn-severity [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-23\"]]",
      "map setFilter burn-severity-outline [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-23\"]]",
      "map setFilter sensors [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-24\"]]"
    ],
    "children": [
      "FireLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "WaterLayer aggregateCells=[0] gauges=[0] map=map opacityScale=1 visible=true wells=[0]",
      "DroughtLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "VegetationLayer geojson=fc(0) map=map mode=\"ndvi\" month=6 ndviMode=\"absolute\" opacityScale=1 showNDWI=false visible=false year=2026",
      "SoilSurveyLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"moisture\" opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"temperature\" opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"vpd\" opacityScale=1 visible=false",
      "ClimateFieldLayers bbox=\"-129.859167,39.570417,-106.140833,51.429583\" map=map zoom=5.92392237523062",
      "DemandHeatmapLayer bbox=\"-129.859167,39.570417,-106.140833,51.429583\" map=map opacityScale=1 visible=false zoom=5.92392237523062",
      "WeatherLayer data=[0] map=map opacityScale=1 visible=false",
      "LandContextViewportLayer geojson=null map=map visible=false",
      "CropCoverLayer geojson=null visible=false",
      "QueryPointLayer map=map point=null"
    ],
    "drawnDays": "{\"water\":{\"drawnDate\":\"2026-07-02\",\"requestedDate\":\"2026-07-02\",\"isLoading\":false},\"fire-perimeters\":{\"drawnDate\":null,\"pendingDate\":null,\"requestedDate\":\"2026-07-01\",\"isLoading\":false}}",
    "banner": ""
  },
  "styleSwap": {
    "log": [
      "-- style swap",
      "map setData sensor-station-features features=0",
      "map setData evacuation-zone-features features=0",
      "map setData burn-severity-features features=0",
      "map setData watershed-features features=0",
      "map setData fire-perimeter-features features=0",
      "map setData intervention-drafts-source features=0",
      "map setLayoutProperty fire-perimeters visibility=\"none\"",
      "map setLayoutProperty fire-perimeters-outline visibility=\"none\"",
      "map setLayoutProperty sensors visibility=\"visible\"",
      "map setLayoutProperty watersheds-fill visibility=\"none\"",
      "map setLayoutProperty watersheds-outline visibility=\"none\"",
      "map setLayoutProperty interventions visibility=\"none\"",
      "map setLayoutProperty interventions-outline visibility=\"none\"",
      "map setLayoutProperty interventions-points visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-fill visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-outline visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-points visibility=\"none\"",
      "map setLayoutProperty evacuation-zones visibility=\"none\"",
      "map setLayoutProperty evacuation-zones-outline visibility=\"none\"",
      "map setLayoutProperty burn-severity visibility=\"none\"",
      "map setLayoutProperty burn-severity-outline visibility=\"none\"",
      "map setFilter fire-perimeters [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-21\"]]",
      "map setFilter fire-perimeters-outline [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-21\"]]",
      "map setFilter evacuation-zones [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-22\"]]",
      "map setFilter evacuation-zones-outline [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-22\"]]",
      "map setFilter burn-severity [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-23\"]]",
      "map setFilter burn-severity-outline [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-23\"]]",
      "map setFilter sensors [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-24\"]]",
      "map setPaintProperty fire-perimeters fill-opacity=0.5",
      "map setPaintProperty fire-perimeters-outline line-opacity=1",
      "map setPaintProperty sensors circle-opacity=0.51",
      "map setPaintProperty sensors circle-stroke-opacity=0.6",
      "map setPaintProperty watersheds-fill fill-opacity=0.05",
      "map setPaintProperty watersheds-outline line-opacity=0.8",
      "map setPaintProperty interventions fill-opacity=0.4",
      "map setPaintProperty interventions-outline line-opacity=1",
      "map setPaintProperty interventions-points circle-opacity=0.9",
      "map setPaintProperty interventions-points circle-stroke-opacity=1",
      "map setPaintProperty intervention-drafts-fill fill-opacity=0.2",
      "map setPaintProperty intervention-drafts-outline line-opacity=1",
      "map setPaintProperty intervention-drafts-points circle-opacity=0.45",
      "map setPaintProperty intervention-drafts-points circle-stroke-opacity=1",
      "map setPaintProperty evacuation-zones fill-opacity=0.45",
      "map setPaintProperty evacuation-zones-outline line-opacity=1",
      "map setPaintProperty burn-severity fill-opacity=0.4",
      "map setPaintProperty burn-severity-outline line-opacity=0.8",
      "-- new style settles",
      "map setLayoutProperty fire-perimeters visibility=\"none\"",
      "map setLayoutProperty fire-perimeters-outline visibility=\"none\"",
      "map setLayoutProperty sensors visibility=\"visible\"",
      "map setLayoutProperty watersheds-fill visibility=\"none\"",
      "map setLayoutProperty watersheds-outline visibility=\"none\"",
      "map setLayoutProperty interventions visibility=\"none\"",
      "map setLayoutProperty interventions-outline visibility=\"none\"",
      "map setLayoutProperty interventions-points visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-fill visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-outline visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-points visibility=\"none\"",
      "map setLayoutProperty evacuation-zones visibility=\"none\"",
      "map setLayoutProperty evacuation-zones-outline visibility=\"none\"",
      "map setLayoutProperty burn-severity visibility=\"none\"",
      "map setLayoutProperty burn-severity-outline visibility=\"none\"",
      "query listMySubmissions {} enabled=false",
      "query listProposed {} enabled=false",
      "query getPublishedSoilRasters null enabled=undefined",
      "query getFireDetections {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062,\"dayRange\":1} enabled=false keep",
      "query getDroughtClassification {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=true keep",
      "query getSensorStations {\"bbox\":\"<viewport>\",\"date\":\"2026-07-24\",\"zoom\":5.92392237523062} enabled=true keep",
      "query getEvacuationZones {\"bbox\":\"<viewport>\",\"date\":\"2026-07-22\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getBurnSeverity {\"bbox\":\"<viewport>\",\"date\":\"2026-07-23\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getFirePerimeters {\"bbox\":\"<viewport>\",\"date\":\"2026-07-21\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getWatershedBoundaries {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getStreamflow {\"bbox\":\"<viewport>\",\"date\":\"2026-07-30\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getGroundwater {\"bbox\":\"<viewport>\",\"date\":\"2026-07-30\"} enabled=false keep",
      "query getVegetationIndex {\"bbox\":\"<viewport>\",\"date\":\"2026-06-14\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilSurvey {\"bbox\":\"<viewport>\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"moisture\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"temperature\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getSoilField {\"bbox\":\"<viewport>\",\"measure\":\"vpd\",\"depth\":\"surface\",\"zoom\":5.92392237523062} enabled=false keep",
      "query getWeatherForBbox {\"bbox\":\"<viewport>\",\"date\":\"2026-08-02\",\"zoom\":5.92392237523062} enabled=false keep",
      "map setData sensor-station-features features=0",
      "map setData evacuation-zone-features features=0",
      "map setData burn-severity-features features=0",
      "map setData watershed-features features=0",
      "map setData fire-perimeter-features features=0",
      "map setData intervention-drafts-source features=0",
      "map setLayoutProperty fire-perimeters visibility=\"none\"",
      "map setLayoutProperty fire-perimeters-outline visibility=\"none\"",
      "map setLayoutProperty sensors visibility=\"visible\"",
      "map setLayoutProperty watersheds-fill visibility=\"none\"",
      "map setLayoutProperty watersheds-outline visibility=\"none\"",
      "map setLayoutProperty interventions visibility=\"none\"",
      "map setLayoutProperty interventions-outline visibility=\"none\"",
      "map setLayoutProperty interventions-points visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-fill visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-outline visibility=\"none\"",
      "map setLayoutProperty intervention-drafts-points visibility=\"none\"",
      "map setLayoutProperty evacuation-zones visibility=\"none\"",
      "map setLayoutProperty evacuation-zones-outline visibility=\"none\"",
      "map setLayoutProperty burn-severity visibility=\"none\"",
      "map setLayoutProperty burn-severity-outline visibility=\"none\"",
      "map setFilter fire-perimeters [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-21\"]]",
      "map setFilter fire-perimeters-outline [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-21\"]]",
      "map setFilter evacuation-zones [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-22\"]]",
      "map setFilter evacuation-zones-outline [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-22\"]]",
      "map setFilter burn-severity [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-23\"]]",
      "map setFilter burn-severity-outline [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-23\"]]",
      "map setFilter sensors [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-24\"]]",
      "map setFilter fire-perimeters [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-21\"]]",
      "map setFilter fire-perimeters-outline [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-21\"]]",
      "map setFilter evacuation-zones [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-22\"]]",
      "map setFilter evacuation-zones-outline [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-22\"]]",
      "map setFilter burn-severity [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-23\"]]",
      "map setFilter burn-severity-outline [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-23\"]]",
      "map setFilter sensors [\"any\",[\"!\",[\"has\",\"observed_day\"]],[\"<=\",[\"get\",\"observed_day\"],\"2026-07-24\"]]",
      "map setPaintProperty fire-perimeters fill-opacity=0.5",
      "map setPaintProperty fire-perimeters-outline line-opacity=1",
      "map setPaintProperty sensors circle-opacity=0.51",
      "map setPaintProperty sensors circle-stroke-opacity=0.6",
      "map setPaintProperty watersheds-fill fill-opacity=0.05",
      "map setPaintProperty watersheds-outline line-opacity=0.8",
      "map setPaintProperty interventions fill-opacity=0.4",
      "map setPaintProperty interventions-outline line-opacity=1",
      "map setPaintProperty interventions-points circle-opacity=0.9",
      "map setPaintProperty interventions-points circle-stroke-opacity=1",
      "map setPaintProperty intervention-drafts-fill fill-opacity=0.2",
      "map setPaintProperty intervention-drafts-outline line-opacity=1",
      "map setPaintProperty intervention-drafts-points circle-opacity=0.45",
      "map setPaintProperty intervention-drafts-points circle-stroke-opacity=1",
      "map setPaintProperty evacuation-zones fill-opacity=0.45",
      "map setPaintProperty evacuation-zones-outline line-opacity=1",
      "map setPaintProperty burn-severity fill-opacity=0.4",
      "map setPaintProperty burn-severity-outline line-opacity=0.8",
      "map setPaintProperty fire-perimeters fill-opacity=0.5",
      "map setPaintProperty fire-perimeters-outline line-opacity=1",
      "map setPaintProperty sensors circle-opacity=0.51",
      "map setPaintProperty sensors circle-stroke-opacity=0.6",
      "map setPaintProperty watersheds-fill fill-opacity=0.05",
      "map setPaintProperty watersheds-outline line-opacity=0.8",
      "map setPaintProperty interventions fill-opacity=0.4",
      "map setPaintProperty interventions-outline line-opacity=1",
      "map setPaintProperty interventions-points circle-opacity=0.9",
      "map setPaintProperty interventions-points circle-stroke-opacity=1",
      "map setPaintProperty intervention-drafts-fill fill-opacity=0.2",
      "map setPaintProperty intervention-drafts-outline line-opacity=1",
      "map setPaintProperty intervention-drafts-points circle-opacity=0.45",
      "map setPaintProperty intervention-drafts-points circle-stroke-opacity=1",
      "map setPaintProperty evacuation-zones fill-opacity=0.45",
      "map setPaintProperty evacuation-zones-outline line-opacity=1",
      "map setPaintProperty burn-severity fill-opacity=0.4",
      "map setPaintProperty burn-severity-outline line-opacity=0.8"
    ],
    "children": [
      "FireLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "WaterLayer aggregateCells=[0] gauges=[0] map=map opacityScale=1 visible=false wells=[0]",
      "DroughtLayer geojson=fc(0) map=map opacityScale=1 visible=true",
      "VegetationLayer geojson=fc(0) map=map mode=\"ndvi\" month=6 ndviMode=\"absolute\" opacityScale=1 showNDWI=false visible=false year=2026",
      "SoilSurveyLayer geojson=fc(0) map=map opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"moisture\" opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"temperature\" opacityScale=1 visible=false",
      "SoilFieldLayer geojson=fc(0) map=map measure=\"vpd\" opacityScale=1 visible=false",
      "ClimateFieldLayers bbox=\"-129.859167,39.570417,-106.140833,51.429583\" map=map zoom=5.92392237523062",
      "DemandHeatmapLayer bbox=\"-129.859167,39.570417,-106.140833,51.429583\" map=map opacityScale=1 visible=false zoom=5.92392237523062",
      "WeatherLayer data=[0] map=map opacityScale=1 visible=false",
      "LandContextViewportLayer geojson=null map=map visible=false",
      "CropCoverLayer geojson=null visible=false",
      "QueryPointLayer map=map point=null"
    ],
    "drawnDays": "{\"drought\":{\"drawnDate\":null,\"pendingDate\":null,\"requestedDate\":\"2026-08-04\",\"isLoading\":false},\"sensors\":{\"drawnDate\":null,\"pendingDate\":null,\"requestedDate\":\"2026-07-24\",\"isLoading\":false}}",
    "banner": ""
  }
};
