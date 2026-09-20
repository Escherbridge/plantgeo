/**
 * The catalogue under the SECOND manifest, selected the way a deployment selects it.
 *
 * `src/__tests__/region/layer-region-binding.test.tsx` proves the same behaviour from a fabricated
 * coverage PAYLOAD. This file proves it from the compiled manifest alone, with
 * `NEXT_PUBLIC_PLANTGEO_REGION=kenya-highlands` and a payload that states no `layerBindings` at all
 * -- which is what a client sees before the serving side answers, and what every client sees if
 * the field is ever dropped. `conductor/code_styleguides/federation.md` §2: a governed absence with
 * a named reason, never an empty map that reads as an outage.
 *
 * The service half of the proof is
 * `services/agri-data-service/tests/foundation/test_second_region_manifest.py`; the manifest parity
 * half is `second-region-manifest.test.ts`.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import { LayerRow } from "@/components/map/layer-panel/LayerRow";
import { DEFAULT_LEGEND_CONTEXT } from "@/lib/map/layer-legends";
import { layerBindingInRegion, toggleBindingInRegion } from "@/lib/map/layer-region-binding";
import { LAYER_TOGGLE_IDS, type LayerToggleId } from "@/lib/map/layer-registry";
import { useLayerVisibility } from "@/lib/map/layer-toggle-context";
import { getRegion } from "@/lib/region/region";
import { useMapStore } from "@/stores/map-store";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import type { SliderCapabilities } from "@/types/time-slider";

vi.mock("@/stores/sync-index-store", () => ({
  useSyncedDays: vi.fn(() => new Set()),
  useSyncIndexReady: vi.fn(() => true),
  useLayerSyncedBytes: vi.fn(() => 0),
  clearLayerSyncedDays: vi.fn(),
}));

const SECOND_REGION_SLUG = "kenya-highlands";
const SERVER_CURRENT_DATE = "2026-09-18";

/** The four layers this proof names: every one has a US-scoped source in the pilot and none here. */
const REGIONALLY_SOURCED_UNBOUND_SLUGS = ["burn-severity", "drought", "soil-survey", "land-context"] as const;

/** The six layers a global source fills anywhere on the planet, including here. */
const GLOBALLY_BOUND_SLUGS = [
  "botanical-occurrences",
  "fire-detections",
  "climate-field-air-temperature",
  "climate-field-dew-point",
  "climate-field-precipitation",
  "climate-field-relative-humidity",
  "climate-field-shortwave-radiation",
  "climate-field-soil-wetness-profile",
  "climate-field-soil-wetness-root-zone",
  "climate-field-soil-wetness-surface",
  "climate-field-wind-speed",
  "soil-field-moisture",
  "soil-field-temperature",
  "soil-field-vpd",
  "vegetation",
  "watersheds",
  "weather-observations",
] as const;

/** Toggles over an unbound layer, and the toggles over a globally bound one, as the panel knows them. */
const UNBOUND_TOGGLE_IDS = ["soil-survey", "drought", "burn-severity"] as const satisfies readonly LayerToggleId[];
const BOUND_TOGGLE_IDS = ["fire", "vegetation", "weather"] as const satisfies readonly LayerToggleId[];

/** A capability payload that states NOTHING about bindings: the manifest is the only evidence left. */
function capabilitiesWithoutLayerBindings(): SliderCapabilities {
  return {
    serverCurrentDate: SERVER_CURRENT_DATE,
    futureAxisDays: 2,
    streamsUnavailable: false,
    layers: [],
  };
}

function renderRow(layerId: LayerToggleId) {
  return renderWithProviders(
    <ul>
      <LayerRow layerId={layerId} legendContext={DEFAULT_LEGEND_CONTEXT} />
    </ul>
  );
}

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", SECOND_REGION_SLUG);
  useMapStore.setState({ activeLayers: [...LAYER_TOGGLE_IDS] });
  useTimeSliderStore.setState({
    capabilities: capabilitiesWithoutLayerBindings(),
    capabilitiesUnavailable: false,
  });
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("the compiled manifest this bundle selects", () => {
  it("is the second region, and it binds only global sources", () => {
    const region = getRegion();
    expect(region.slug).toBe(SECOND_REGION_SLUG);
    expect(region.enabledLayers.every((binding) => binding.coverage === "global")).toBe(true);
  });
});

describe("layerBindingInRegion under the second manifest", () => {
  it("answers unbound for every regionally-sourced layer", () => {
    for (const layerSlug of REGIONALLY_SOURCED_UNBOUND_SLUGS) {
      expect(layerBindingInRegion(capabilitiesWithoutLayerBindings(), layerSlug), layerSlug).toBe("unbound");
      // Same verdict with no payload at all: the manifest states the region's complete binding set.
      expect(layerBindingInRegion(null, layerSlug), layerSlug).toBe("unbound");
    }
  });

  it("answers bound for every globally-sourced layer", () => {
    for (const layerSlug of GLOBALLY_BOUND_SLUGS) {
      expect(layerBindingInRegion(capabilitiesWithoutLayerBindings(), layerSlug), layerSlug).toBe("bound");
    }
  });

  it("still calls a slug outside the vocabulary not federated rather than unbound", () => {
    // The distinction the second manifest must preserve: an absence this region STATES is not the
    // same as a slug no manifest can name, and only the first disables anything.
    expect(layerBindingInRegion(null, "a-layer-this-build-never-heard-of")).toBe("not_federated");
  });

  it("carries the verdict through to the toggles the panel actually draws", () => {
    for (const layerId of UNBOUND_TOGGLE_IDS) {
      expect(toggleBindingInRegion(capabilitiesWithoutLayerBindings(), layerId), layerId).toBe("unbound");
    }
    for (const layerId of BOUND_TOGGLE_IDS) {
      expect(toggleBindingInRegion(capabilitiesWithoutLayerBindings(), layerId), layerId).toBe("bound");
    }
  });
});

describe("LayerRow under the second manifest", () => {
  it("captions an unbound layer as not available in this region", () => {
    renderRow("soil-survey");
    const caption = screen.getByTestId("layer-region-absence-soil-survey");
    expect(caption.textContent).toContain("not available in this region");
    expect(caption.textContent).toContain("no data source is bound for it here");
  });

  it("disables the toggle for an unbound layer and never claims the record has a gap", () => {
    renderRow("drought");
    const toggle = screen.getByRole("switch") as HTMLButtonElement;
    expect(toggle.disabled).toBe(true);
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    // The captions a bound layer can carry are claims about a publishing record, and an unbound
    // layer has no record here to describe. Either one beside the region sentence would be false.
    expect(screen.queryByText(/no observations/i)).toBeNull();
  });

  it("leaves a globally-bound layer exactly as the pilot draws it", () => {
    renderRow("fire");
    expect(screen.queryByTestId("layer-region-absence-fire")).toBeNull();
    expect((screen.getByRole("switch") as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("useLayerVisibility under the second manifest", () => {
  it("reads false for every unbound toggle even though all of them are switched on", () => {
    // `activeLayers` holds every toggle (see `beforeEach`), so a false here can only come from the
    // region gate. This record is the seam `federation.md` §2's "no fetch issued" rests on: every
    // layer component mounts off it, so a layer that reads false adds no source and asks for no day.
    const { result } = renderHook(() => useLayerVisibility());
    for (const layerId of UNBOUND_TOGGLE_IDS) {
      expect(result.current[layerId], layerId).toBe(false);
    }
    for (const layerId of BOUND_TOGGLE_IDS) {
      expect(result.current[layerId], layerId).toBe(true);
    }
  });

  it("issues no network read while resolving an unbound layer", () => {
    // The direct form of the same claim: reading the region costs nothing but the compiled
    // manifest, so nothing beneath an unbound layer ever gets the chance to fetch a day for it.
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderHook(() => useLayerVisibility());
    renderRow("soil-survey");
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});
