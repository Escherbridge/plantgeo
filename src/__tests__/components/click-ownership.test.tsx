import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Map as MapLibreMap } from "maplibre-gl";
import {
  CLICK_OWNING_LAYER_IDS,
  DEDICATED_CLICK_LAYER_IDS,
  isClickOwnedByAnotherSurface,
} from "@/components/map/land-context/click-ownership";
import { HOVERABLE_LAYER_IDS, TOOLTIP_TAP_LAYER_IDS } from "@/lib/map/hover-fields";
import { INTERVENTION_STYLE_LAYER_IDS } from "@/lib/map/layer-registry";
import { setScalarFieldInspectionSuppressed } from "@/lib/map/scalar-field-inspection";
import { climateFieldLayerIdsFor } from "@/lib/map/climate-field-layer-ids";
import { useMapStore } from "@/stores/map-store";

/**
 * The shared "one click, one meaning" predicate, pinned owner by owner -- and pinned NOT to
 * own plain ground. The basemap's `earth`/`water` fills sit under every land pixel and are never
 * inspection-suppressed, so a predicate that trusted `isScalarFieldInspectionAllowed` alone
 * would be true everywhere; membership in an explicit set comes first, and for the tap-only
 * layers that membership counts only on a coarse pointer, as `HoverTooltip` itself gates.
 */
function mapWith(features: { layer: { id: string } }[]) {
  const map = { queryRenderedFeatures: vi.fn(() => features) };
  return map as unknown as MapLibreMap;
}

function setPointer(coarse: boolean) {
  window.matchMedia = vi.fn().mockReturnValue({ matches: coarse }) as unknown as typeof window.matchMedia;
}

// `PointLike` is `Point | [number, number]`; a tuple avoids constructing a MapLibre `Point`.
const POINT: [number, number] = [10, 10];
const EARTH = { layer: { id: "earth" } };
const WATER = { layer: { id: "water" } };
// Registrants of their own `map.on("click", id)` outside hover-fields' private dedicated six:
// GBIF (`GbifOccurrencesLayer.tsx:204-205`) and botanical points (`BotanicalOccurrencesLayer.tsx:263-265`).
const CLICK_HANDLER_IDS_OUTSIDE_THE_SIX = [
  "gbif-occurrences-exact",
  "gbif-occurrences-generalized",
  "botanical-occurrences-exact",
  "botanical-occurrences-generalized",
  "botanical-occurrences-possible",
];
const GBIF_IDS = CLICK_HANDLER_IDS_OUTSIDE_THE_SIX.filter((id) => id.startsWith("gbif-"));

beforeEach(() => {
  useMapStore.setState({ isCapturingQueryPoint: false });
  setPointer(false);
});

afterEach(() => {
  vi.clearAllMocks();
  // @ts-expect-error -- jsdom implements no matchMedia by default; undo the per-test stub.
  delete window.matchMedia;
});

describe("isClickOwnedByAnotherSurface", () => {
  it("is false on empty ground with nothing capturing", () => {
    expect(isClickOwnedByAnotherSurface(mapWith([]), POINT)).toBe(false);
  });

  it.each([EARTH, WATER])("is false over the basemap fill %o -- ground is not an owner", (fill) => {
    expect(isClickOwnedByAnotherSurface(mapWith([fill]), POINT)).toBe(false);
  });

  it("is false over a drawn land-context feature itself (that click is the layer's own)", () => {
    expect(
      isClickOwnedByAnotherSurface(mapWith([EARTH, { layer: { id: "land-context-fill-blm-lands" } }]), POINT)
    ).toBe(false);
  });

  it("is true while a panel is capturing query points, without even querying features", () => {
    useMapStore.setState({ isCapturingQueryPoint: true });
    const map = mapWith([]);
    expect(isClickOwnedByAnotherSurface(map, POINT)).toBe(true);
    expect(map.queryRenderedFeatures).not.toHaveBeenCalled();
  });

  it.each([...INTERVENTION_STYLE_LAYER_IDS])("is true over intervention layer %s, even when inspection-suppressed", (layerId) => {
    const map = mapWith([EARTH, { layer: { id: layerId } }]);
    setScalarFieldInspectionSuppressed(map, [layerId], true);
    expect(isClickOwnedByAnotherSurface(map, POINT)).toBe(true);
  });

  it("the owning set is the hover registry plus the click-handler ids outside the six, and nothing else", () => {
    expect([...CLICK_OWNING_LAYER_IDS].sort()).toEqual([...HOVERABLE_LAYER_IDS, ...GBIF_IDS].sort());
    // Dedicated = the registry minus the tap set, plus every layer with its own click handler.
    expect([...DEDICATED_CLICK_LAYER_IDS].sort()).toEqual(
      [
        ...HOVERABLE_LAYER_IDS.filter((id) => !TOOLTIP_TAP_LAYER_IDS.includes(id)),
        ...CLICK_HANDLER_IDS_OUTSIDE_THE_SIX,
      ].sort()
    );
    // Hover-only botanical fills stay tap-only.
    expect(DEDICATED_CLICK_LAYER_IDS.has("botanical-richness-fill")).toBe(false);
    expect(DEDICATED_CLICK_LAYER_IDS.has("botanical-collection-effort-fill")).toBe(false);
  });

  it.each([false, true])("dedicated-popup layers (fire/water/GBIF) own the click with coarse pointer = %s", (coarse) => {
    setPointer(coarse);
    for (const layerId of DEDICATED_CLICK_LAYER_IDS) {
      expect(isClickOwnedByAnotherSurface(mapWith([EARTH, { layer: { id: layerId } }]), POINT)).toBe(true);
    }
  });

  it("tap-only layers (drought, watersheds, weather...) do NOT own a fine-pointer click", () => {
    setPointer(false);
    // Two exclusions from the tooltip's tap set: `interventions`/`interventions-points` are
    // intervention ids (arm 2 owns them on every pointer), and the botanical points register
    // their own click handler (dedicated owners, listed in CLICK_HANDLER_IDS_OUTSIDE_THE_SIX).
    const tapOnlyNonIntervention = TOOLTIP_TAP_LAYER_IDS.filter(
      (id) => !INTERVENTION_STYLE_LAYER_IDS.includes(id) && !CLICK_HANDLER_IDS_OUTSIDE_THE_SIX.includes(id)
    );
    expect(tapOnlyNonIntervention.length).toBeGreaterThan(0);
    for (const layerId of tapOnlyNonIntervention) {
      expect(isClickOwnedByAnotherSurface(mapWith([EARTH, { layer: { id: layerId } }]), POINT)).toBe(false);
    }
  });

  it("tap-only layers DO own a coarse-pointer click, as HoverTooltip's tap handler pins them", () => {
    setPointer(true);
    for (const layerId of TOOLTIP_TAP_LAYER_IDS) {
      expect(isClickOwnedByAnotherSurface(mapWith([EARTH, { layer: { id: layerId } }]), POINT)).toBe(true);
    }
  });

  it("climate geometry is tap-owned only on a coarse pointer", () => {
    const layerId = climateFieldLayerIdsFor("shortwave-radiation").isobandFillId;
    setPointer(false);
    expect(
      isClickOwnedByAnotherSurface(mapWith([EARTH, { layer: { id: layerId } }]), POINT)
    ).toBe(false);
    setPointer(true);
    expect(
      isClickOwnedByAnotherSurface(mapWith([EARTH, { layer: { id: layerId } }]), POINT)
    ).toBe(true);
  });

  it("releases a click-owning layer while it is inspection-suppressed", () => {
    setPointer(true);
    const map = mapWith([EARTH, { layer: { id: "vegetation-ndvi-cells-fill" } }]);
    setScalarFieldInspectionSuppressed(map, ["vegetation-ndvi-cells-fill"], true);
    expect(isClickOwnedByAnotherSurface(map, POINT)).toBe(false);
    setScalarFieldInspectionSuppressed(map, ["vegetation-ndvi-cells-fill"], false);
    expect(isClickOwnedByAnotherSurface(map, POINT)).toBe(true);
  });
});
