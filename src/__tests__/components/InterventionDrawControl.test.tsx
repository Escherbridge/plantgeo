import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { Map as MapLibreMap } from "maplibre-gl";

/*
 * terra-draw's real TerraDraw class needs a live MapLibre map + DOM canvas to construct its
 * adapter; this suite fakes the module surface so the wrapper's own logic (mode toggling, change
 * wiring, clear behaviour) is what gets tested, not terra-draw's internals.
 */
const changeListeners: Array<(ids: string[], type: string) => void> = [];
const fakeDraw = {
  start: vi.fn(),
  stop: vi.fn(),
  setMode: vi.fn(),
  clear: vi.fn(),
  addFeatures: vi.fn(() => [{ valid: true }]),
  getSnapshot: vi.fn(() => [] as GeoJSON.Feature[]),
  on: vi.fn((event: string, listener: (ids: string[], type: string) => void) => {
    if (event === "change") changeListeners.push(listener);
  }),
  off: vi.fn(),
};

vi.mock("terra-draw", () => ({
  TerraDraw: vi.fn(() => fakeDraw),
  TerraDrawPointMode: vi.fn(),
  TerraDrawPolygonMode: vi.fn(),
  TerraDrawSelectMode: vi.fn(),
}));

vi.mock("terra-draw-maplibre-gl-adapter", () => ({
  TerraDrawMapLibreGLAdapter: vi.fn(),
}));

import { InterventionDrawControl } from "@/components/map/InterventionDrawControl";

const fakeMap = { isStyleLoaded: () => true, on: vi.fn(), off: vi.fn() } as unknown as MapLibreMap;

describe("InterventionDrawControl", () => {
  beforeEach(() => {
    changeListeners.length = 0;
    fakeDraw.start.mockClear();
    fakeDraw.stop.mockClear();
    fakeDraw.setMode.mockClear();
    fakeDraw.clear.mockClear();
    fakeDraw.addFeatures.mockReset().mockReturnValue([{ valid: true }]);
    fakeDraw.getSnapshot.mockReset().mockReturnValue([]);
  });

  it("starts terra-draw in point mode by default", () => {
    render(<InterventionDrawControl map={fakeMap} onGeometryChange={vi.fn()} />);

    expect(fakeDraw.start).toHaveBeenCalled();
    expect(fakeDraw.setMode).toHaveBeenCalledWith("point");
  });

  it("restores a saved point without emitting a new drawing change", () => {
    const initialGeometry = { type: "Point" as const, coordinates: [-116.2, 43.6] };
    const onGeometryChange = vi.fn();
    render(<InterventionDrawControl map={fakeMap} initialGeometry={initialGeometry} onGeometryChange={onGeometryChange} />);
    expect(fakeDraw.addFeatures).toHaveBeenCalledWith([{
      type: "Feature", geometry: initialGeometry, properties: { mode: "point" },
    }]);
    expect(onGeometryChange).not.toHaveBeenCalled();
  });

  it("restores only on attachment and uses the latest saved geometry if the map is replaced", () => {
    const point = { type: "Point" as const, coordinates: [-116.2, 43.6] };
    const polygon = { type: "Polygon" as const, coordinates: [[[-116.3, 43.6], [-116.2, 43.6], [-116.2, 43.7], [-116.3, 43.6]]] };
    const onGeometryChange = vi.fn();
    const { rerender } = render(<InterventionDrawControl map={fakeMap} initialGeometry={point} onGeometryChange={onGeometryChange} />);
    rerender(<InterventionDrawControl map={fakeMap} initialGeometry={polygon} onGeometryChange={onGeometryChange} />);
    expect(fakeDraw.addFeatures).toHaveBeenCalledTimes(1);
    expect(fakeDraw.stop).not.toHaveBeenCalled();
    const replacementMap = { isStyleLoaded: () => true, on: vi.fn(), off: vi.fn() } as unknown as MapLibreMap;
    rerender(<InterventionDrawControl map={replacementMap} initialGeometry={polygon} onGeometryChange={onGeometryChange} />);
    expect(fakeDraw.addFeatures).toHaveBeenLastCalledWith([{
      type: "Feature", geometry: polygon, properties: { mode: "polygon" },
    }]);
    expect(fakeDraw.stop).toHaveBeenCalledTimes(1);
    expect(onGeometryChange).not.toHaveBeenCalled();
  });

  it("reports failed recovery without replacing the saved geometry with an empty snapshot", () => {
    const initialGeometry = { type: "Point" as const, coordinates: [-116.2, 43.6] };
    const onGeometryChange = vi.fn();
    const onRestoreError = vi.fn();
    fakeDraw.addFeatures.mockReturnValue([{ valid: false }]);
    render(<InterventionDrawControl map={fakeMap} initialGeometry={initialGeometry} onGeometryChange={onGeometryChange} onRestoreError={onRestoreError} />);
    expect(fakeDraw.clear).toHaveBeenCalledTimes(1);
    expect(onRestoreError).toHaveBeenCalledWith(expect.stringContaining("It is still saved"));
    expect(onGeometryChange).not.toHaveBeenCalled();
  });

  it("waits for a fresh map to load and cancels the attachment if unmounted first", () => {
    const load = new Set<() => void>();
    let ready = false;
    const pendingMap = {
      isStyleLoaded: () => ready,
      on: (_event: string, listener: () => void) => load.add(listener),
      off: (_event: string, listener: () => void) => load.delete(listener),
    } as unknown as MapLibreMap;
    const first = render(<InterventionDrawControl map={pendingMap} onGeometryChange={vi.fn()} />);
    expect(fakeDraw.start).not.toHaveBeenCalled();
    first.unmount();
    expect(load.size).toBe(0);
    render(<InterventionDrawControl map={pendingMap} initialGeometry={{ type: "Point", coordinates: [-116.2, 43.6] }} onGeometryChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Polygon" }));
    ready = true;
    load.forEach((listener) => listener());
    load.forEach((listener) => listener());
    expect(fakeDraw.start).toHaveBeenCalledTimes(1);
    expect(fakeDraw.setMode).toHaveBeenCalledWith("polygon");
    expect(fakeDraw.addFeatures).toHaveBeenCalledTimes(1);
  });

  it("attaches after transient unreadiness on an already-loaded map without another load event", () => {
    let ready = false;
    const events = new Map<string, Set<() => void>>();
    const loadedMap = {
      isStyleLoaded: () => ready,
      on: (event: string, listener: () => void) => {
        const listeners = events.get(event) ?? new Set<() => void>();
        listeners.add(listener);
        events.set(event, listeners);
      },
      off: (event: string, listener: () => void) => events.get(event)?.delete(listener),
    } as unknown as MapLibreMap;
    render(<InterventionDrawControl map={loadedMap} initialGeometry={{ type: "Point", coordinates: [-116.2, 43.6] }} onGeometryChange={vi.fn()} />);
    const renderListeners = [...(events.get("render") ?? [])];
    renderListeners.forEach((listener) => listener());
    expect(fakeDraw.start).not.toHaveBeenCalled();
    ready = true;
    renderListeners.forEach((listener) => listener());
    renderListeners.forEach((listener) => listener());
    expect(fakeDraw.start).toHaveBeenCalledTimes(1);
    expect(fakeDraw.addFeatures).toHaveBeenCalledTimes(1);
    expect(events.get("render")?.size).toBe(0);
    expect(events.get("load")?.size).toBe(0);
  });

  it("switches to polygon mode when the polygon button is pressed", () => {
    render(<InterventionDrawControl map={fakeMap} onGeometryChange={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Polygon" }));

    expect(fakeDraw.setMode).toHaveBeenCalledWith("polygon");
  });

  it("clears the drawing and reports null geometry when Clear is pressed", () => {
    const onGeometryChange = vi.fn();
    render(<InterventionDrawControl map={fakeMap} onGeometryChange={onGeometryChange} />);

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));

    expect(fakeDraw.clear).toHaveBeenCalled();
    expect(onGeometryChange).toHaveBeenCalledWith(null);
  });

  it("reports the drawn geometry from terra-draw's change event", () => {
    const onGeometryChange = vi.fn();
    const drawnPolygon: GeoJSON.Feature = {
      type: "Feature",
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [-116.3, 43.6],
            [-116.2, 43.6],
            [-116.2, 43.7],
            [-116.3, 43.6],
          ],
        ],
      },
      properties: {},
    };
    fakeDraw.getSnapshot.mockReturnValue([drawnPolygon]);

    render(<InterventionDrawControl map={fakeMap} onGeometryChange={onGeometryChange} />);
    changeListeners.forEach((listener) => listener(["1"], "create"));

    expect(onGeometryChange).toHaveBeenCalledWith(drawnPolygon.geometry);
  });

  it("reports null geometry when the change event leaves no features", () => {
    const onGeometryChange = vi.fn();
    fakeDraw.getSnapshot.mockReturnValue([]);

    render(<InterventionDrawControl map={fakeMap} onGeometryChange={onGeometryChange} />);
    changeListeners.forEach((listener) => listener([], "delete"));

    expect(onGeometryChange).toHaveBeenCalledWith(null);
  });

  it("stops terra-draw on unmount", () => {
    const { unmount } = render(
      <InterventionDrawControl map={fakeMap} onGeometryChange={vi.fn()} />
    );

    unmount();

    expect(fakeDraw.stop).toHaveBeenCalled();
  });
});
