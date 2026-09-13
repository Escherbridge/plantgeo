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

const fakeMap = {} as MapLibreMap;

describe("InterventionDrawControl", () => {
  beforeEach(() => {
    changeListeners.length = 0;
    fakeDraw.start.mockClear();
    fakeDraw.stop.mockClear();
    fakeDraw.setMode.mockClear();
    fakeDraw.clear.mockClear();
    fakeDraw.getSnapshot.mockClear();
  });

  it("starts terra-draw in point mode by default", () => {
    render(<InterventionDrawControl map={fakeMap} onGeometryChange={vi.fn()} />);

    expect(fakeDraw.start).toHaveBeenCalled();
    expect(fakeDraw.setMode).toHaveBeenCalledWith("point");
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
