import { render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Map as MapLibreMap } from "maplibre-gl";
import { MapProvider } from "@/lib/map/map-context";

const coverage = vi.hoisted(() => ({ data: undefined as undefined | { configured: boolean; bbox: { west: number; south: number; east: number; north: number } } }));
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(window.location.search) }));
vi.mock("@/lib/trpc/client", () => ({ trpc: { layers: { getIngestionCoverage: { useQuery: () => coverage } } } }));
vi.mock("@/lib/map/layer-utils", () => ({ getFirstSymbolLayer: vi.fn(), safeRemoveLayerAndSource: vi.fn() }));

import { MapFocus } from "@/components/map/MapFocus";
import { ServiceAreaLayer } from "@/components/map/ServiceAreaLayer";

afterEach(() => { window.history.replaceState({}, "", "/"); coverage.data = undefined; });

it("retains a saved camera target when coverage arrives after the initial map navigation", () => {
  window.history.replaceState({}, "", "/?focusLng=-116.78&focusLat=45.94&focusZoom=13");
  const map = {
    jumpTo: vi.fn(), on: vi.fn(), off: vi.fn(), isStyleLoaded: () => false,
    setMaxBounds: vi.fn(), setMinZoom: vi.fn(), fitBounds: vi.fn(), getLayer: () => undefined,
  };
  const mapValue = map as unknown as MapLibreMap;
  const content = () => <MapProvider value={mapValue}><MapFocus /><ServiceAreaLayer map={mapValue} /></MapProvider>;
  const view = render(content());
  expect(map.jumpTo).toHaveBeenCalledWith({ center: [-116.78, 45.94], zoom: 13 });
  coverage.data = { configured: true, bbox: { west: -125, south: 42, east: -110, north: 50 } };
  view.rerender(content());
  expect(map.setMaxBounds).toHaveBeenCalled();
  expect(map.fitBounds).not.toHaveBeenCalled();
});
