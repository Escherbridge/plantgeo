import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import maplibregl, { type StyleSpecification } from "maplibre-gl";
import TinySDF from "@mapbox/tiny-sdf";
import { VegetationLayer } from "../../src/components/map/layers/VegetationLayer";
import HoverTooltip from "../../src/components/map/HoverTooltip";
import { useVegetationStore } from "../../src/stores/vegetation-store";
import { isScalarFieldInspectionAllowed } from "../../src/lib/map/scalar-field-inspection";

type Scenario = "field" | "duplicate" | "half-opacity" | "missing" | "mixed-days" | "mixed-units" | "empty" | "detail" | "mode-race" | "refusal-layout" | "reload" | "globe" | "pitch";

interface ScalarCaseSnapshot {
  scenario: Scenario;
  zoom: number;
  errors: string[];
  layers: string[];
  customPresent: boolean;
  nativeOpacity: unknown;
  probes: Record<string, { x: number; y: number }>;
  labelCount: number;
  unrestrictedPickCount: number;
  inspectablePickCount: number;
  diagnostics: Record<string, unknown>;
  webgl2: boolean;
}

declare global {
  interface Window {
    fixtureReady: boolean;
    runScalarCase: (scenario: Scenario) => Promise<ScalarCaseSnapshot>;
    runScalarRefusalPhase: (phase: "empty" | "null" | "settle" | "recover") => Promise<ScalarCaseSnapshot>;
  }
}

// Local glyph protocol keeps the real MapLibre symbol path offline; see AGENTS.md.
function varint(value: number): number[] {
  const bytes: number[] = [];
  let remaining = Math.round(value);
  do {
    const byte = remaining % 128;
    remaining = Math.floor(remaining / 128);
    bytes.push(byte + (remaining ? 128 : 0));
  } while (remaining);
  return bytes;
}

function message(field: number, bytes: number[]): number[] {
  return [...varint(field * 8 + 2), ...varint(bytes.length), ...bytes];
}

function integer(field: number, value: number, signed = false): number[] {
  return [...varint(field * 8), ...varint(signed ? (value < 0 ? -2 * value - 1 : 2 * value) : value)];
}

maplibregl.addProtocol("fixtureglyph", async () => {
  const sdf = new TinySDF({ fontSize: 24, buffer: 3, fontFamily: "Arial" });
  const glyphs: number[] = [];
  for (let id = 32; id < 127; id++) {
    const glyph = sdf.draw(String.fromCharCode(id));
    const bytes = [
      ...integer(1, id), ...message(2, [...glyph.data]),
      ...integer(3, glyph.glyphWidth), ...integer(4, glyph.glyphHeight),
      ...integer(5, glyph.glyphLeft, true), ...integer(6, glyph.glyphTop, true),
      ...integer(7, glyph.glyphAdvance),
    ];
    glyphs.push(...message(3, bytes));
  }
  return { data: new Uint8Array(message(1, glyphs)).buffer };
});

function style(): StyleSpecification {
  return {
    version: 8,
    glyphs: "fixtureglyph://local/{fontstack}/{range}.pbf",
    sources: {},
    layers: [{ id: "background", type: "background", paint: { "background-color": "#15222b" } }],
  };
}

function collection(scenario: Scenario): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature<GeoJSON.Polygon>[] = [];
  for (let row = -1; row <= 1; row++) {
    for (let column = -1; column <= 1; column++) {
      if (scenario === "missing" && row === 0 && column === 0) continue;
      const x = -120 + column * 0.25;
      const y = 45 + row * 0.25;
      features.push({
        type: "Feature",
        id: features.length,
        geometry: { type: "Polygon", coordinates: [[
          [x - 0.125, y - 0.125], [x + 0.125, y - 0.125],
          [x + 0.125, y + 0.125], [x - 0.125, y + 0.125], [x - 0.125, y - 0.125],
        ]] },
        properties: {
          ndvi: [scenario === "mode-race" ? -0.4 : -0.5, 0.2, 0.8][column + 1],
          observedDay: scenario === "mixed-days" && column === 1 ? "2026-09-09" : "2026-09-08",
          gridName: "synthetic-quarter-degree",
          metricUnit: scenario === "mixed-units" && column === 1 ? "incompatible-fixture-unit" : "1",
          supportId: "synthetic-ndvi",
          supportKind: "tessellated_cell", cellWidthDegrees: 0.25, cellHeightDegrees: 0.25,
          cellId: `${column}:${row}`,
        },
      });
    }
  }
  if (scenario === "duplicate") features.push(...structuredClone(features));
  return { type: "FeatureCollection", features: scenario === "empty" ? [] : features };
}

const host = document.getElementById("root");
if (!host) throw new Error("Missing fixture root");
const root = createRoot(host);
const errors: string[] = [];
const glErrors: { code: number; stack?: string }[] = [];
const map = new maplibregl.Map({
  container: "map", style: style(), center: [-120, 45], zoom: 7,
  canvasContextAttributes: { preserveDrawingBuffer: true },
  fadeDuration: 0, attributionControl: false,
});
map.on("error", (event) => errors.push(String(event.error)));
if (new URLSearchParams(window.location.search).has("gl-diagnostics")) {
  const context = map.getCanvas().getContext("webgl2");
  if (context) {
    const originalGetError = context.getError.bind(context);
    context.getError = () => {
      const code = originalGetError();
      if (code !== context.NO_ERROR) glErrors.push({ code, stack: new Error("WebGL diagnostic").stack });
      return code;
    };
  }
}

async function settle(): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const onIdle = () => {
      queueMicrotask(() => {
        if (!map.loaded()) return;
        clearTimeout(timeout);
        map.off("idle", onIdle);
        resolve();
      });
    };
    const timeout = setTimeout(() => {
      map.off("idle", onIdle);
      reject(new Error("Fixture did not become idle within eight seconds"));
    }, 8000);
    map.on("idle", onIdle);
    map.triggerRepaint();
  });
}

let heldIdleEvents = 0;
let releaseIdle: (() => void) | null = null;

// Hold controller notifications after the SDK settles placement; see AGENTS.md.
function holdIdle(): Promise<void> {
  const eventMap = map as unknown as {
    fire: (event: string | { type: string }, properties?: unknown) => typeof map;
  };
  const originalFire = eventMap.fire;
  heldIdleEvents = 0;
  return new Promise<void>((resolve, reject) => {
    const timeout = setTimeout(() => {
      releaseIdle?.();
      reject(new Error("Fixture did not attempt idle during held label layout"));
    }, 8000);
    releaseIdle = () => {
      clearTimeout(timeout);
      eventMap.fire = originalFire;
      releaseIdle = null;
    };
    eventMap.fire = (event, properties) => {
      if ((typeof event === "string" ? event : event.type) === "idle") {
        heldIdleEvents++;
        clearTimeout(timeout);
        resolve();
        return map;
      }
      return originalFire.call(map, event, properties);
    };
  });
}

function renderRefusalData(data: GeoJSON.FeatureCollection | null): void {
  flushSync(() => root.render(<>
    <VegetationLayer map={map} geojson={data} opacity={1} />
    <HoverTooltip map={map} />
  </>));
}

window.runScalarRefusalPhase = async (phase) => {
  if (phase === "settle") {
    releaseIdle?.();
    await settle();
  } else if (phase === "recover") {
    renderRefusalData(collection("mode-race"));
    await settle();
  } else {
    if (!releaseIdle) throw new Error("Refusal replacement requires held idle delivery");
    renderRefusalData(phase === "null" ? null : collection("empty"));
    await new Promise<void>((resolve) => {
      map.once("render", () => requestAnimationFrame(() => resolve()));
      map.triggerRepaint();
    });
  }
  return snapshot("refusal-layout");
};

window.runScalarCase = async (scenario) => {
  errors.length = 0;
  glErrors.length = 0;
  releaseIdle?.();
  if (scenario === "refusal-layout") {
    map.setProjection({ type: "mercator" });
    map.jumpTo({ center: [-120, 45], zoom: 7, pitch: 0 });
    renderRefusalData(collection("field"));
    await settle();
    const coarse = snapshot(scenario);
    const pendingIdle = holdIdle();
    map.jumpTo({ center: [-120.25, 45], zoom: 10 });
    await pendingIdle;
    const locked = snapshot(scenario);
    locked.diagnostics.coarseCustomActive = coarse.diagnostics.active;
    locked.diagnostics.coarseCustomReady = coarse.diagnostics.ready;
    return locked;
  }
  if (scenario === "reload") {
    map.setStyle(style(), { diff: false });
    await settle();
  }
  map.setProjection({ type: scenario === "globe" ? "globe" : "mercator" });
  const detail = scenario === "detail" || scenario === "mode-race";
  map.jumpTo({
    center: detail ? [-120.25, 45] : [-120, 45],
    zoom: detail ? 10 : 7, pitch: scenario === "pitch" ? 30 : 0,
  });
  flushSync(() => root.render(<>
    <VegetationLayer map={map} geojson={collection(scenario)} opacity={scenario === "half-opacity" ? 0.5 : 1} />
    <HoverTooltip map={map} />
  </>));
  const modeRaceStartedWhileSourceLoading = scenario === "mode-race"
    ? !map.isSourceLoaded("vegetation-ndvi-cells") : undefined;
  if (scenario === "mode-race") {
    flushSync(() => useVegetationStore.getState().setSource("satellite"));
    flushSync(() => useVegetationStore.getState().setSource("measured"));
  }
  await settle();
  return snapshot(scenario, { modeRaceStartedWhileSourceLoading });
};

function snapshot(scenario: Scenario, extraDiagnostics: Record<string, unknown> = {}): ScalarCaseSnapshot {
  const probes: Record<string, { x: number; y: number }> = {};
  for (const [name, coordinate] of Object.entries({
    negative: [-120.25, 45], center: [-120, 45], positive: [-119.75, 45],
    leftEdge: [-120.13, 45], rightEdge: [-120.12, 45], outside: [-120, 45.5],
  })) {
    const point = map.project(coordinate as [number, number]);
    probes[name] = { x: point.x, y: point.y };
  }
  const labels = "vegetation-ndvi-cells-values";
  const scalar = (map.getLayer("vegetation-ndvi-scalar-field") as unknown as {
    implementation?: Record<string, unknown>;
  } | undefined)?.implementation;
  const mesh = scalar?.mesh as { cells?: unknown[] } | null | undefined;
  const picks = map.queryRenderedFeatures([probes.negative.x, probes.negative.y]);
  const nativeSource = map.getStyle().sources["vegetation-ndvi-cells"] as { data?: GeoJSON.FeatureCollection } | undefined;
  return {
    scenario, zoom: map.getZoom(), errors: [...errors],
    layers: map.getStyle().layers.map((layer) => layer.id),
    customPresent: !!map.getLayer("vegetation-ndvi-scalar-field"),
    nativeOpacity: map.getPaintProperty("vegetation-ndvi-cells-fill", "fill-opacity"),
    probes,
    labelCount: map.getLayer(labels) ? map.queryRenderedFeatures(undefined, { layers: [labels] }).length : 0,
    unrestrictedPickCount: picks.length,
    inspectablePickCount: picks.filter((feature) => isScalarFieldInspectionAllowed(map, feature.layer.id)).length,
    diagnostics: {
      failed: scalar?.failed, active: scalar?.active, ready: scalar?.ready,
      visible: scalar?.visible, meshCells: mesh?.cells?.length,
      nativeOpacity: scalar?.nativeOpacity,
      sameWebglContext: scalar?.gl === map.getCanvas().getContext("webgl2"),
      projection: map.getProjection().type, terrain: map.getTerrain(), pitch: map.getPitch(),
      bounds: map.getBounds().toArray(), sourceLoaded: map.isSourceLoaded("vegetation-ndvi-cells"),
      labelVisibility: map.getLayer(labels) ? map.getLayoutProperty(labels, "visibility") : null,
      outlineOpacity: map.getPaintProperty("vegetation-ndvi-cells-outline", "line-opacity"),
      labelOpacity: map.getPaintProperty(labels, "text-opacity"),
      layoutPending: scalar?.layoutPending, dataPending: scalar?.dataPending,
      nativeSuppressed: scalar?.nativeSuppressed,
      sourceFeatureCount: nativeSource?.data?.features.length,
      renderedSourceFeatureCount: map.querySourceFeatures("vegetation-ndvi-cells").length,
      idleHeld: releaseIdle !== null, heldIdleEvents,
      glErrors: [...glErrors],
      ...extraDiagnostics,
    },
    webgl2: map.getCanvas().getContext("webgl2") !== null,
  };
}
map.once("load", () => { window.fixtureReady = true; });
