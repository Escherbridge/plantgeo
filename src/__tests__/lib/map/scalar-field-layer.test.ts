import { afterEach, describe, expect, it, vi } from "vitest";
import type { CustomRenderMethodInput, Map as MapLibreMap } from "maplibre-gl";
import { ScalarFieldLayer } from "@/lib/map/scalar-field-layer";
import { NDVI_COLOR_RAMP } from "@/lib/vegetation";

const collection: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [{
  type: "Feature", geometry: { type: "Polygon", coordinates: [[[0, 0], [0.25, 0], [0.25, 0.25], [0, 0.25], [0, 0]]] },
  properties: { ndvi: -0.4, observedDay: "2026-09-01", gridName: "grid", metricUnit: "1", supportId: "cell", supportKind: "tessellated_cell", cellWidthDegrees: 0.25, cellHeightDegrees: 0.25 },
}] };

function harness({ webgl2 = true, shader = true, projectionReady = true } = {}) {
  class Context {
    VERTEX_SHADER = 1; FRAGMENT_SHADER = 2; COMPILE_STATUS = 3; LINK_STATUS = 4;
    ARRAY_BUFFER = 5; FLOAT = 6; STATIC_DRAW = 7; TRIANGLES = 8; NO_ERROR = 0;
    CURRENT_PROGRAM = 9; ARRAY_BUFFER_BINDING = 10; VERTEX_ARRAY_BINDING = 11;
    getParameter = vi.fn((parameter: number) => ({ parameter }));
    createShader = vi.fn(() => ({})); shaderSource = vi.fn(); compileShader = vi.fn();
    getShaderParameter = vi.fn(() => shader); deleteShader = vi.fn();
    createProgram = vi.fn(() => ({})); attachShader = vi.fn(); linkProgram = vi.fn();
    getProgramParameter = vi.fn(() => true); createBuffer = vi.fn(() => ({}));
    createVertexArray = vi.fn(() => ({})); bindVertexArray = vi.fn(); bindBuffer = vi.fn();
    getAttribLocation = vi.fn(() => 0); enableVertexAttribArray = vi.fn(); vertexAttribPointer = vi.fn();
    useProgram = vi.fn(); getUniformLocation = vi.fn(() => ({})); uniform1fv = vi.fn(); uniform3fv = vi.fn();
    uniform1f = vi.fn(); uniformMatrix4fv = vi.fn(); bufferData = vi.fn(); getError = vi.fn(() => 0);
    drawArrays = vi.fn(); isContextLost = vi.fn(() => false); deleteBuffer = vi.fn(); deleteVertexArray = vi.fn(); deleteProgram = vi.fn();
  }
  vi.stubGlobal("WebGL2RenderingContext", Context);
  const gl = new Context();
  const state = { projection: projectionReady ? "mercator" : undefined, terrain: false, pitch: 0, scale: 10, west: -180, east: 180, worldCopies: false, sourceLoaded: true };
  const nativeSource = { setData: vi.fn(() => { state.sourceLoaded = false; }) };
  let currentSource = nativeSource;
  const layoutVisibility = new Map([ ["fill", "visible"], ["outline", "visible"], ["labels", "none"] ]);
  type Listener = (event: { sourceId: string; sourceDataType?: string }) => void;
  const events = new Map<string, Set<Listener>>();
  const map = {
    on: vi.fn((name: string, listener: Listener) => { const set = events.get(name) ?? new Set<Listener>(); set.add(listener); events.set(name, set); }),
    off: vi.fn((name: string, listener: Listener) => events.get(name)?.delete(listener)),
    getProjection: () => state.projection ? { type: state.projection } : undefined, getTerrain: () => state.terrain ? {} : null, getPitch: () => state.pitch,
    project: ([x, y]: [number, number]) => ({ x: x * state.scale, y: y * state.scale }),
    getLayer: () => ({}), getSource: () => currentSource,
    getLayoutProperty: (id: string) => layoutVisibility.get(id),
    setPaintProperty: vi.fn(), setLayoutProperty: vi.fn((id: string, _property: string, value: string) => { layoutVisibility.set(id, value); }), triggerRepaint: vi.fn(),
    getCenter: () => ({ lng: 0 }), getRenderWorldCopies: () => state.worldCopies,
    getBounds: () => ({ getWest: () => state.west, getEast: () => state.east }),
    isSourceLoaded: () => state.sourceLoaded,
  };
  const layer = new ScalarFieldLayer({ id: "field", nativeFillId: "fill", nativeOutlineId: "outline", nativeSourceId: "cells", labelId: "labels", valueProperty: "ndvi", range: [-1, 1], ramp: NDVI_COLOR_RAMP });
  layer.update(collection, 0.75, true);
  layer.onAdd(map as unknown as MapLibreMap, (webgl2 ? gl : {}) as WebGL2RenderingContext);
  const input: CustomRenderMethodInput = {
    farZ: 1, nearZ: 0, fov: 1,
    modelViewProjectionMatrix: new Float32Array(16),
    projectionMatrix: new Float32Array(16),
    shaderData: { variantName: "mercator", vertexShaderPrelude: "", define: "" },
    defaultProjectionData: {
      mainMatrix: new Float32Array(16), fallbackMatrix: new Float32Array(16),
      tileMercatorCoords: [0, 0, 1, 1], clippingPlane: [0, 0, 0, 0], projectionTransition: 0,
    },
  };
  const draw = () => layer.render(gl as unknown as WebGL2RenderingContext, input);
  const emit = (name: string, sourceId = "cells", sourceDataType = "content") => events.get(name)?.forEach((listener) => listener({ sourceId, sourceDataType }));
  return { layer, map, gl, state, events, draw, emit, nativeSource, replaceSource: () => { currentSource = { setData: vi.fn() }; return currentSource; } };
}

afterEach(() => vi.unstubAllGlobals());

describe("scalar layer native fallback and lifecycle", () => {
  it("draws exact values at low spacing and hands off to native fill/labels at inspection spacing", () => {
    const h = harness();
    expect(h.map.setPaintProperty).toHaveBeenLastCalledWith("fill", "fill-opacity", 0.75);
    h.draw();
    expect(h.map.setPaintProperty).toHaveBeenLastCalledWith("fill", "fill-opacity", 0);
    expect(h.gl.drawArrays).toHaveBeenCalledWith(h.gl.TRIANGLES, 0, 6);
    h.state.scale = 256;
    h.emit("move");
    expect(h.map.setPaintProperty).toHaveBeenLastCalledWith("fill", "fill-opacity", 0.75);
    expect(h.map.setLayoutProperty).toHaveBeenLastCalledWith("labels", "visibility", "visible");
    h.draw();
    expect(h.gl.drawArrays).toHaveBeenCalledTimes(1);
    h.state.scale = 10;
    h.emit("move");
    expect(h.map.setPaintProperty).toHaveBeenLastCalledWith("fill", "fill-opacity", 0);
  });
  it.each([{ webgl2: false }, { shader: false }])("keeps native paint on unsupported or failed setup %j", (options) => {
    const h = harness(options);
    expect(h.map.setPaintProperty).toHaveBeenLastCalledWith("fill", "fill-opacity", 0.75);
    h.draw();
    expect(h.gl.drawArrays).not.toHaveBeenCalled();
  });
  it("restores native cells for globe, terrain and pitch and permits return to mercator", () => {
    const h = harness();
    h.state.projection = "globe";
    h.emit("styledata");
    expect(h.map.setPaintProperty).toHaveBeenLastCalledWith("fill", "fill-opacity", 0.75);
    h.state.projection = "mercator";
    h.state.terrain = true;
    h.emit("styledata"); h.draw();
    expect(h.gl.drawArrays).not.toHaveBeenCalled();
    h.state.terrain = false; h.state.pitch = 30; h.emit("move"); h.draw();
    expect(h.gl.drawArrays).not.toHaveBeenCalled();
    h.state.pitch = 0; h.emit("move"); h.draw();
    expect(h.gl.drawArrays).toHaveBeenCalledTimes(1);
  });
  it("retries transient missing projection during style attachment without latching a GPU failure", () => {
    const h = harness({ projectionReady: false });
    h.draw();
    expect(h.gl.drawArrays).not.toHaveBeenCalled();
    expect(h.map.setPaintProperty).toHaveBeenLastCalledWith("fill", "fill-opacity", 0.75);
    h.state.projection = "mercator"; h.emit("styledata"); h.draw();
    expect(h.map.setPaintProperty).toHaveBeenLastCalledWith("fill", "fill-opacity", 0);
    expect(h.gl.drawArrays).toHaveBeenCalledTimes(1);
    h.state.projection = undefined; h.draw();
    expect(h.map.setPaintProperty).toHaveBeenLastCalledWith("fill", "fill-opacity", 0.75);
    h.state.projection = "mercator"; h.emit("styledata"); h.draw();
    expect(h.gl.drawArrays).toHaveBeenCalledTimes(2);
  });
  it("restores native fill on upload/draw errors and context loss", () => {
    for (const failure of ["upload", "draw", "context"]) {
      const h = harness();
      if (failure === "upload") { h.gl.getError.mockReturnValue(1); h.layer.update({ ...collection }, 0.4, true); }
      if (failure === "draw") { h.gl.drawArrays.mockImplementation(() => { throw new Error("draw"); }); h.draw(); }
      if (failure === "context") h.emit("webglcontextlost");
      expect(h.map.setPaintProperty).toHaveBeenLastCalledWith("fill", "fill-opacity", failure === "upload" ? 0.4 : 0.75);
    }
  });
  it("updates opacity without rebuilding buffers; hides field on source switches and invalid replacement", () => {
    const h = harness();
    h.draw();
    h.gl.drawArrays.mockClear();
    h.layer.update(collection, 0.3, true);
    h.draw();
    expect(h.gl.bufferData).toHaveBeenCalledTimes(1);
    expect(h.gl.uniform1f).toHaveBeenCalledWith(expect.anything(), 0.3);
    h.layer.update(collection, 0.3, false); h.draw();
    expect(h.gl.drawArrays).toHaveBeenCalledTimes(1);
    h.layer.update({ type: "FeatureCollection", features: [] }, 0.3, true); h.draw();
    expect(h.map.setPaintProperty).toHaveBeenLastCalledWith("fill", "fill-opacity", 0.3);
    expect(h.gl.drawArrays).toHaveBeenCalledTimes(1);
  });
  it("releases listeners and GPU objects on style removal", () => {
    const h = harness();
    h.layer.onRemove();
    expect([...h.events.values()].every((set) => set.size === 0)).toBe(true);
    expect(h.gl.deleteBuffer).toHaveBeenCalledTimes(1);
    expect(h.gl.deleteVertexArray).toHaveBeenCalledTimes(1);
    expect(h.gl.deleteProgram).toHaveBeenCalledTimes(1);
    expect(h.map.setPaintProperty).toHaveBeenLastCalledWith("fill", "fill-opacity", 0.75);
  });
  it("preserves MapLibre GL bindings across initialization and prop-driven uploads", () => {
    const h = harness();
    expect(h.gl.useProgram).toHaveBeenLastCalledWith({ parameter: h.gl.CURRENT_PROGRAM });
    expect(h.gl.bindVertexArray).toHaveBeenLastCalledWith({ parameter: h.gl.VERTEX_ARRAY_BINDING });
    expect(h.gl.bindBuffer).toHaveBeenLastCalledWith(h.gl.ARRAY_BUFFER, { parameter: h.gl.ARRAY_BUFFER_BINDING });
    h.layer.update({ ...collection }, 0.75, true);
    expect(h.gl.bindBuffer).toHaveBeenLastCalledWith(h.gl.ARRAY_BUFFER, { parameter: h.gl.ARRAY_BUFFER_BINDING });
  });
  it("unbinds owned objects before deletion so a style rebuild cannot restore a deleted program", () => {
    const h = harness();
    const owned: Record<number, object> = {
      [h.gl.CURRENT_PROGRAM]: h.gl.createProgram.mock.results[0].value,
      [h.gl.VERTEX_ARRAY_BINDING]: h.gl.createVertexArray.mock.results[0].value,
      [h.gl.ARRAY_BUFFER_BINDING]: h.gl.createBuffer.mock.results[0].value,
    };
    h.gl.getParameter.mockImplementation((parameter) => owned[parameter] as { parameter: number });
    h.layer.onRemove();
    expect(h.gl.useProgram).toHaveBeenLastCalledWith(null);
    expect(h.gl.bindVertexArray).toHaveBeenLastCalledWith(null);
    expect(h.gl.bindBuffer).toHaveBeenLastCalledWith(h.gl.ARRAY_BUFFER, null);
    expect(h.gl.useProgram.mock.invocationCallOrder.at(-1)).toBeLessThan(h.gl.deleteProgram.mock.invocationCallOrder[0]);
  });
  it("covers wide-view repeated worlds and bounds pathological viewport work with native fallback", () => {
    const h = harness();
    h.state.worldCopies = true; h.state.west = -900; h.state.east = 900;
    h.emit("move"); h.draw();
    expect(h.gl.drawArrays).toHaveBeenCalledTimes(6);
    for (const offset of [-2, -1, 0, 1, 2, 3]) expect(h.gl.uniform1f).toHaveBeenCalledWith(expect.anything(), offset);
    h.state.west = -3600; h.state.east = 3600; h.emit("move"); h.draw();
    expect(h.gl.drawArrays).toHaveBeenCalledTimes(6);
    expect(h.map.setPaintProperty).toHaveBeenLastCalledWith("fill", "fill-opacity", 0.75);
  });
  it("defers label relayout through empty-to-detail source replacement until matching source tiles are loaded", () => {
    const h = harness();
    h.layer.update({ type: "FeatureCollection", features: [] }, 0.75, true);
    h.map.setLayoutProperty.mockClear();
    h.state.sourceLoaded = false;
    h.state.scale = 256;
    h.layer.update(collection, 0.75, true);
    h.emit("styledata"); h.emit("sourcedata");
    expect(h.map.setLayoutProperty).not.toHaveBeenCalled();
    h.state.sourceLoaded = true;
    h.emit("sourcedata", "cells", "metadata");
    expect(h.map.setLayoutProperty).not.toHaveBeenCalled();
    h.emit("sourcedata", "unrelated");
    expect(h.map.setLayoutProperty).not.toHaveBeenCalled();
    h.events.get("sourcedata")?.forEach((listener) => listener({ sourceId: "cells" }));
    expect(h.map.setLayoutProperty).toHaveBeenLastCalledWith("labels", "visibility", "visible");
  });
  it("queues latest data behind in-flight symbol layout and activates only after native data completes", () => {
    const h = harness();
    h.draw();
    h.state.scale = 256; h.emit("move");
    const first = { ...collection, features: [...collection.features] };
    const latest = { ...collection, features: [...collection.features] };
    h.state.sourceLoaded = false;
    h.layer.update(first, 0.75, true);
    h.layer.update(latest, 0.75, true);
    expect(h.nativeSource.setData).not.toHaveBeenCalled();
    h.state.sourceLoaded = true; h.emit("idle");
    expect(h.nativeSource.setData).toHaveBeenCalledTimes(1);
    expect(h.nativeSource.setData).toHaveBeenCalledWith(latest);
    h.state.scale = 10; h.emit("move"); h.draw();
    expect(h.gl.drawArrays).toHaveBeenCalledTimes(1);
    h.state.sourceLoaded = true; h.emit("sourcedata"); h.draw();
    expect(h.gl.drawArrays).toHaveBeenCalledTimes(2);
  });
  it("queues data behind a native source-mode relayout and never drains into a replacement style source", () => {
    const h = harness();
    h.layer.update(collection, 0.75, false);
    h.layer.update({ ...collection }, 0.75, false);
    expect(h.nativeSource.setData).not.toHaveBeenCalled();
    const replacement = h.replaceSource();
    h.emit("idle");
    expect(replacement.setData).not.toHaveBeenCalled();
    expect(h.nativeSource.setData).not.toHaveBeenCalled();
    h.layer.onRemove(); h.emit("sourcedata");
    expect(replacement.setData).not.toHaveBeenCalled();
  });
  it("defers source-mode layout during data loading while suppressing measured fill immediately", () => {
    const h = harness();
    const latest = { ...collection };
    h.layer.update(latest, 0.75, true);
    expect(h.nativeSource.setData).toHaveBeenCalledWith(latest);
    h.map.setLayoutProperty.mockClear();
    h.layer.update(latest, 0.75, false);
    expect(h.map.setLayoutProperty).not.toHaveBeenCalled();
    expect(h.map.setPaintProperty).toHaveBeenLastCalledWith("fill", "fill-opacity", 0);
    h.state.sourceLoaded = true; h.emit("sourcedata");
    expect(h.map.setLayoutProperty).toHaveBeenCalledWith("fill", "visibility", "none");
    expect(h.map.setLayoutProperty).toHaveBeenCalledWith("outline", "visibility", "none");
  });
  it("does not release a scheduled layout for a stale source completion before map idle", () => {
    const h = harness();
    h.state.scale = 256; h.emit("move");
    const latest = { ...collection };
    h.layer.update(latest, 0.75, true);
    h.emit("sourcedata");
    expect(h.nativeSource.setData).not.toHaveBeenCalled();
    h.emit("idle");
    expect(h.nativeSource.setData).toHaveBeenCalledWith(latest);
  });
});
