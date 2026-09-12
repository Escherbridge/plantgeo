import { act, render } from "@testing-library/react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MapProvider } from "@/lib/map/map-context";
import InterventionPublicationSync from "@/components/map/InterventionPublicationSync";
import { INTERVENTION_PUBLICATION_KEY, notifyInterventionPublication } from "@/lib/map/intervention-publication";

const mocks = vi.hoisted(() => ({
  purge: vi.fn().mockResolvedValue(1),
  interventions: vi.fn(),
  contributions: vi.fn(),
}));
vi.mock("@/lib/offline/tile-cache", () => ({ refreshDynamicTiles: mocks.purge }));
const utils = {
  interventions: { invalidate: mocks.interventions },
  contributions: { invalidate: mocks.contributions },
};
vi.mock("@/lib/trpc/client", () => ({ trpc: { useUtils: () => utils } }));

function createMap() {
  const handlers = new Map<string, Set<() => void>>();
  const source = {
    tiles: ["https://plantgeo-martin.test/intervention_tiles/{z}/{x}/{y}?token=keep"],
    setTiles: vi.fn((tiles: string[]) => {
      source.tiles = tiles;
      handlers.get("sourcedata")?.forEach((handler) => handler());
    }),
  };
  const map = {
    getSource: vi.fn(() => source),
    on: vi.fn((event: string, handler: () => void) => {
      if (!handlers.has(event)) handlers.set(event, new Set());
      handlers.get(event)!.add(handler);
    }),
    off: vi.fn((event: string, handler: () => void) => handlers.get(event)?.delete(handler)),
  };
  return { map, source, handlers, emit: (event: string) => handlers.get(event)?.forEach((handler) => handler()) };
}

describe("publication refresh for mounted and returning maps", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  it("purges before resetting a warm source and invalidates outcomes without a source-event loop", async () => {
    const fixture = createMap();
    const view = render(<MapProvider value={fixture.map as unknown as MapLibreMap}><InterventionPublicationSync /></MapProvider>);
    await act(async () => { await notifyInterventionPublication(); });
    expect(mocks.purge).toHaveBeenCalledWith("intervention_tiles");
    expect(fixture.source.setTiles).toHaveBeenCalledTimes(1);
    expect(fixture.source.tiles[0]).toContain("token=keep&publication=");
    expect(mocks.purge.mock.invocationCallOrder[0]).toBeLessThan(fixture.source.setTiles.mock.invocationCallOrder[0]);
    expect(mocks.interventions).toHaveBeenCalledTimes(1);
    fixture.emit("sourcedata");
    window.dispatchEvent(new Event("focus"));
    expect(fixture.source.setTiles).toHaveBeenCalledTimes(1);
    expect(mocks.interventions).toHaveBeenCalledTimes(1);
    view.unmount();
    expect([...fixture.handlers.values()].every((set) => set.size === 0)).toBe(true);
    await notifyInterventionPublication();
    expect(fixture.source.setTiles).toHaveBeenCalledTimes(1);
  });

  it("refreshes a returning page, another tab, and a replaced basemap only once each", () => {
    localStorage.setItem(INTERVENTION_PUBLICATION_KEY, "existing");
    const fixture = createMap();
    const view = render(<MapProvider value={fixture.map as unknown as MapLibreMap}><InterventionPublicationSync /></MapProvider>);
    expect(fixture.source.tiles[0]).toContain("publication=existing");
    localStorage.setItem(INTERVENTION_PUBLICATION_KEY, "next");
    act(() => window.dispatchEvent(new StorageEvent("storage", { key: INTERVENTION_PUBLICATION_KEY, newValue: "next" })));
    expect(fixture.source.tiles[0]).toContain("publication=next");
    fixture.source.tiles = ["https://plantgeo-martin.test/intervention_tiles/{z}/{x}/{y}"];
    fixture.emit("style.load");
    fixture.emit("sourcedata");
    expect(fixture.source.setTiles).toHaveBeenCalledTimes(3);
    view.unmount();
    const next = render(<MapProvider value={fixture.map as unknown as MapLibreMap}><InterventionPublicationSync /></MapProvider>);
    expect(fixture.source.setTiles).toHaveBeenCalledTimes(3);
    next.unmount();
  });
});
