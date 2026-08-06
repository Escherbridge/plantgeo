import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act, cleanup, render } from "@testing-library/react";
import type maplibregl from "maplibre-gl";
import { useMapQueryPoint } from "@/hooks/useMapQueryPoint";
import { useMapStore } from "@/stores/map-store";

/**
 * `SoilPanel.queryPoint` had no producer: nothing in `src/` registered a map click handler
 * that could supply one, so the point query was unreachable from the UI. These cases pin
 * the producer, and in particular the three ways out of it -- a pin the user cannot get rid
 * of is worse than no pin.
 */

/** A MapLibre stand-in with just the event surface the hook uses. */
function createFakeMap() {
  const listeners = new Map<string, Set<(event: unknown) => void>>();
  return {
    on: (type: string, handler: (event: unknown) => void) => {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type)!.add(handler);
    },
    off: (type: string, handler: (event: unknown) => void) => {
      listeners.get(type)?.delete(handler);
    },
    listenerCount: (type: string) => listeners.get(type)?.size ?? 0,
    clickAt(lon: number, lat: number) {
      for (const handler of Array.from(listeners.get("click") ?? [])) {
        handler({ lngLat: { lng: lon, lat } });
      }
    },
  };
}

type FakeMap = ReturnType<typeof createFakeMap>;

function CaptureHarness({ map, active }: { map: FakeMap | null; active: boolean }) {
  useMapQueryPoint(map as unknown as maplibregl.Map | null, active);
  return null;
}

const INITIAL_MAP_STATE = useMapStore.getState();

beforeEach(() => useMapStore.setState(INITIAL_MAP_STATE, true));
afterEach(() => {
  // Unmount inside act(): the hook's cleanup disarms capture, which writes the store, and
  // testing-library's automatic cleanup would do that outside a React batch.
  act(() => cleanup());
  useMapStore.setState(INITIAL_MAP_STATE, true);
});

describe("useMapQueryPoint", () => {
  it("registers no click handler and arms nothing while inactive", () => {
    const map = createFakeMap();
    act(() => {
      render(<CaptureHarness map={map} active={false} />);
    });

    expect(map.listenerCount("click")).toBe(0);
    expect(useMapStore.getState().isCapturingQueryPoint).toBe(false);
  });

  it("turns a map click into a query point while active", () => {
    const map = createFakeMap();
    act(() => {
      render(<CaptureHarness map={map} active />);
    });

    expect(useMapStore.getState().isCapturingQueryPoint).toBe(true);
    act(() => map.clickAt(-116.2, 43.6));

    expect(useMapStore.getState().queryPoint).toEqual({ lat: 43.6, lon: -116.2 });
  });

  it("moves the pin when the user asks about a different place", () => {
    const map = createFakeMap();
    act(() => {
      render(<CaptureHarness map={map} active />);
    });

    act(() => map.clickAt(-116.2, 43.6));
    act(() => map.clickAt(-120.5, 45.1));

    expect(useMapStore.getState().queryPoint).toEqual({ lat: 45.1, lon: -120.5 });
  });

  it("clears the pin when the same place is clicked again", () => {
    const map = createFakeMap();
    act(() => {
      render(<CaptureHarness map={map} active />);
    });

    act(() => map.clickAt(-116.2, 43.6));
    act(() => map.clickAt(-116.2, 43.6));

    expect(useMapStore.getState().queryPoint).toBeNull();
  });

  it("clears the pin on Escape", () => {
    const map = createFakeMap();
    act(() => {
      render(<CaptureHarness map={map} active />);
    });

    act(() => map.clickAt(-116.2, 43.6));
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });

    expect(useMapStore.getState().queryPoint).toBeNull();
  });

  it("disarms and clears when the panel that armed it closes", () => {
    const map = createFakeMap();
    let rendered: ReturnType<typeof render> | null = null;
    act(() => {
      rendered = render(<CaptureHarness map={map} active />);
    });
    act(() => map.clickAt(-116.2, 43.6));

    act(() => rendered!.rerender(<CaptureHarness map={map} active={false} />));

    expect(useMapStore.getState().queryPoint).toBeNull();
    expect(useMapStore.getState().isCapturingQueryPoint).toBe(false);
    // MapView's own handler reads `isCapturingQueryPoint` to stand down; a leaked listener
    // here would keep dropping pins nothing is reading.
    expect(map.listenerCount("click")).toBe(0);
  });

  it("registers exactly one click handler however many points are picked", () => {
    const map = createFakeMap();
    act(() => {
      render(<CaptureHarness map={map} active />);
    });

    act(() => map.clickAt(-116.2, 43.6));
    act(() => map.clickAt(-117.4, 44.2));

    expect(map.listenerCount("click")).toBe(1);
  });
});
