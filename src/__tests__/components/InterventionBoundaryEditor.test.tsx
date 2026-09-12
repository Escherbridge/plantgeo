import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Map as MapLibreMap } from "maplibre-gl";
import { MapProvider } from "@/lib/map/map-context";
import { InterventionBoundaryEditor } from "@/components/map/InterventionBoundaryEditor";

function fakeMap() {
  const canvas = document.createElement("canvas");
  Object.defineProperties(canvas, { clientWidth: { value: 1000 }, clientHeight: { value: 800 } });
  canvas.getBoundingClientRect = () => ({ left: 0, top: 0, right: 1000, bottom: 800, width: 1000, height: 800, x: 0, y: 0, toJSON: () => ({}) });
  const handlers = Array.from({ length: 8 }, (_, i) => ({ isEnabled: () => i !== 0, enable: vi.fn(), disable: vi.fn() }));
  const sources = new Map<string, { setData: ReturnType<typeof vi.fn> }>();
  const layers = new Set<string>();
  const events = new Map<string, () => void>();
  const map = {
    getCanvas: () => canvas, getCenter: () => ({ lng: -116, lat: 44 }),
    project: ([lng, lat]: number[]) => ({ x: (lng + 117) * 100, y: (45 - lat) * 100 }),
    unproject: ([x, y]: number[]) => ({ lng: x / 100 - 117, lat: 45 - y / 100 }),
    isStyleLoaded: () => true,
    getSource: (id: string) => sources.get(id), addSource: (id: string) => sources.set(id, { setData: vi.fn() }), removeSource: (id: string) => sources.delete(id),
    getLayer: (id: string) => layers.has(id), addLayer: ({ id }: { id: string }) => layers.add(id), removeLayer: (id: string) => layers.delete(id),
    on: (event: string, callback: () => void) => events.set(event, callback), off: (event: string) => events.delete(event),
    dragPan: handlers[0], dragRotate: handlers[1], scrollZoom: handlers[2], boxZoom: handlers[3], doubleClickZoom: handlers[4], touchZoomRotate: handlers[5], touchPitch: handlers[6], keyboard: handlers[7],
  };
  return { map: map as unknown as MapLibreMap, handlers, sources, layers, events };
}

beforeEach(() => vi.clearAllMocks());

describe("intervention boundary editor", () => {
  it("supports keyboard polygon creation, undo, finish, and restores disabled drag-pan state", () => {
    const { map, handlers } = fakeMap();
    const onSave = vi.fn();
    const view = render(<MapProvider value={map}><InterventionBoundaryEditor mode="polygon" onSave={onSave} onCancel={vi.fn()} /></MapProvider>);
    const surface = screen.getByRole("application");
    fireEvent.keyDown(surface, { key: "Enter" });
    fireEvent.keyDown(surface, { key: "ArrowRight", shiftKey: true });
    fireEvent.keyDown(surface, { key: "Enter" });
    fireEvent.keyDown(surface, { key: "Backspace" });
    expect(screen.getByRole("status").textContent).toContain("1 vertices");
    fireEvent.keyDown(surface, { key: "Enter" });
    fireEvent.keyDown(surface, { key: "ArrowDown", shiftKey: true });
    fireEvent.keyDown(surface, { key: "Enter" });
    fireEvent.keyDown(surface, { key: "Enter", ctrlKey: true });
    expect(onSave).toHaveBeenCalledWith({ type: "Polygon", coordinates: [[[-115.7, 43.7], [-115.7, 44], [-116, 44], [-115.7, 43.7]]] });
    view.unmount();
    expect(handlers[0].disable).toHaveBeenCalledTimes(1);
    expect(handlers[0].enable).not.toHaveBeenCalled();
    handlers.slice(1).forEach((handler) => expect(handler.enable).toHaveBeenCalledTimes(1));
  });

  it("restores the preview after a style swap and cancels without saving", () => {
    const { map, sources, layers, events } = fakeMap();
    const onCancel = vi.fn();
    const onSave = vi.fn();
    const view = render(<MapProvider value={map}><InterventionBoundaryEditor mode="polygon" onSave={onSave} onCancel={onCancel} /></MapProvider>);
    fireEvent.keyDown(screen.getByRole("application"), { key: "Enter" });
    sources.clear(); layers.clear();
    act(() => events.get("style.load")?.());
    expect(sources.size).toBe(1);
    expect(layers.size).toBe(3);
    fireEvent.keyDown(screen.getByRole("application"), { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onSave).not.toHaveBeenCalled();
    view.unmount();
    expect(sources.size).toBe(0);
    expect(layers.size).toBe(0);
  });

  it("offers touch-sized rectangle controls and clear/restart", () => {
    const { map } = fakeMap();
    render(<MapProvider value={map}><InterventionBoundaryEditor mode="rectangle" onSave={vi.fn()} onCancel={vi.fn()} /></MapProvider>);
    const surface = screen.getByRole("application");
    fireEvent.keyDown(surface, { key: "Enter" });
    fireEvent.keyDown(surface, { key: "ArrowDown", shiftKey: true });
    fireEvent.keyDown(surface, { key: "ArrowRight", shiftKey: true });
    fireEvent.keyDown(surface, { key: "Enter" });
    expect(screen.getByRole("status").textContent).toContain("2 corners");
    fireEvent.click(screen.getByRole("button", { name: "Clear / restart" }));
    expect(screen.getByRole("status").textContent).toContain("0 corners");
  });

  it("accepts primary touch taps as rectangle corners without relying on mouse events", () => {
    const { map } = fakeMap();
    const onSave = vi.fn();
    render(<MapProvider value={map}><InterventionBoundaryEditor mode="rectangle" onSave={onSave} onCancel={vi.fn()} /></MapProvider>);
    const surface = screen.getByRole("application");
    for (const [clientX, clientY] of [[100, 100], [150, 150]]) {
      const event = new Event("pointerup", { bubbles: true });
      Object.defineProperties(event, { clientX: { value: clientX }, clientY: { value: clientY }, isPrimary: { value: true }, button: { value: 0 }, pointerType: { value: "touch" } });
      fireEvent(surface, event);
    }
    fireEvent.click(screen.getByRole("button", { name: "Finish boundary" }));
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ type: "Polygon" }));
    expect(onSave.mock.calls[0][0].coordinates[0]).toHaveLength(5);
  });
});
