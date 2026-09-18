/**
 * The web half of the boot-with-global-lanes-only proof: what the catalogue draws for a layer this
 * deployment's region binds no source for.
 *
 * `conductor/code_styleguides/federation.md` §2 -- "a region with no soil source yet gets a soil
 * layer that reports `not available in this region` through the slider capability catalogue,
 * legends and agent tools. A missing binding is a governed absence with a named reason, never a
 * crash, never an empty map that looks like an outage, and never a silent fallback to the pilot's
 * source." The Python side of the same fixture is
 * `services/agri-data-service/tests/foundation/test_region_layer_availability.py`.
 *
 * The payload here is shaped like the one that service emits for a region binding only
 * `coverage: global` sources: `signal`, `fire-detections`, `vegetation`, `weather-observations`,
 * `watersheds` and `botanical-occurrences` bound, every US-sourced layer unbound.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import { LayerRow } from "@/components/map/layer-panel/LayerRow";
import { DEFAULT_LEGEND_CONTEXT } from "@/lib/map/layer-legends";
import {
  isLayerUnboundInRegion,
  regionLayerSlugForToggle,
  unboundLayerCaption,
} from "@/lib/map/layer-region-binding";
import { LAYER_TOGGLE_IDS, type LayerToggleId } from "@/lib/map/layer-registry";
import { useMapStore } from "@/stores/map-store";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import type { SliderCapabilities, SliderLayerBinding } from "@/types/time-slider";

vi.mock("@/stores/sync-index-store", () => ({
  useSyncedDays: vi.fn(() => new Set()),
  useSyncIndexReady: vi.fn(() => true),
  useLayerSyncedBytes: vi.fn(() => 0),
  clearLayerSyncedDays: vi.fn(),
}));

const SERVER_CURRENT_DATE = "2026-09-18";

const BOUND_GLOBAL_LAYER_SLUGS = [
  "botanical-occurrences",
  "fire-detections",
  "signal",
  "vegetation",
  "watersheds",
  "weather-observations",
] as const;

const UNBOUND_LAYER_SLUGS = [
  "burn-severity",
  "drought",
  "evacuation-zones",
  "fire-perimeters",
  "sensors",
  "soil-survey",
  "water-gauges",
] as const;

function globalOnlyBindings(): SliderLayerBinding[] {
  return [
    ...BOUND_GLOBAL_LAYER_SLUGS.map((layerSlug) => ({
      layerSlug,
      binding: "bound_global" as const,
      sourceSlug: "a-global-source",
      reason: null,
    })),
    ...UNBOUND_LAYER_SLUGS.map((layerSlug) => ({
      layerSlug,
      binding: "unbound" as const,
      sourceSlug: null,
      reason: "no_source_bound_in_region",
    })),
  ];
}

function capabilities(layerBindings?: SliderLayerBinding[]): SliderCapabilities {
  return {
    serverCurrentDate: SERVER_CURRENT_DATE,
    futureAxisDays: 2,
    streamsUnavailable: false,
    layers: [],
    ...(layerBindings === undefined ? {} : { layerBindings }),
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
  useMapStore.setState({ activeLayers: [...LAYER_TOGGLE_IDS] });
  useTimeSliderStore.setState({ capabilities: null, capabilitiesUnavailable: false });
});

describe("the catalogue under a region that binds only global sources", () => {
  it("captions an unbound layer as not available in this region", () => {
    useTimeSliderStore.setState({ capabilities: capabilities(globalOnlyBindings()) });
    renderRow("soil-survey");
    const caption = screen.getByTestId("layer-region-absence-soil-survey");
    expect(caption.textContent).toContain("not available in this region");
    expect(caption.textContent).toContain("no data source is bound for it here");
  });

  it("disables the toggle for an unbound layer and never claims the record has a gap", () => {
    useTimeSliderStore.setState({ capabilities: capabilities(globalOnlyBindings()) });
    renderRow("soil-survey");
    const toggle = screen.getByRole("switch");
    expect(toggle).toBeDisabled();
    expect(toggle).toHaveAttribute("aria-checked", "false");
    // The three captions a bound layer can carry are all claims about a publishing record, and an
    // unbound layer has none here to describe. Any of them beside the region sentence would be the
    // "two captions, one of them false" defect `LayerRow` already records once.
    expect(screen.queryByText(/no observations/i)).toBeNull();
  });

  it("mounts no time control and issues no read for an unbound layer", () => {
    useTimeSliderStore.setState({ capabilities: capabilities(globalOnlyBindings()) });
    renderRow("soil-survey");
    // The row renders the time block only for a layer that is ACTIVE, and `isWithheld` is what
    // keeps an unbound layer inactive. No slot means nothing beneath it ever asked for a day.
    expect(screen.queryByTestId("layer-time-slider-slot-soil-survey")).toBeNull();
  });

  it("leaves a globally-bound layer exactly as it is today", () => {
    useTimeSliderStore.setState({ capabilities: capabilities(globalOnlyBindings()) });
    renderRow("fire");
    expect(screen.queryByTestId("layer-region-absence-fire")).toBeNull();
    expect(screen.getByRole("switch")).not.toBeDisabled();
  });
});

describe("the binding lookup itself", () => {
  it("resolves the derived signal streams onto the one signal plane they come from", () => {
    // Twelve climate and soil field streams bind no source of their own. If the signal plane is
    // unbound they all go dark together, and this mapping is the only thing that says so.
    expect(regionLayerSlugForToggle("soil-moisture")).toBe("signal");
    expect(regionLayerSlugForToggle("climate-air-temperature")).toBe("signal");
    expect(regionLayerSlugForToggle("drought")).toBe("drought");
  });

  it("treats a toggle that is not a federated layer as always available", () => {
    // `interventions` is not a layer some region declined to bind a source for -- it has no source
    // binding concept at all -- so it can never be "unavailable in this region".
    expect(regionLayerSlugForToggle("interventions")).toBeNull();
    expect(isLayerUnboundInRegion(capabilities(globalOnlyBindings()), "interventions")).toBe(false);
  });

  it("fails OPEN on every form of silence", () => {
    // The whole deploy-window safety argument, as four assertions: a serving side that predates the
    // field, one that states an empty list, a payload that has not landed, and a layer the list
    // does not mention must all leave the toggle exactly where it is today.
    expect(unboundLayerCaption(capabilities(), "soil-survey")).toBeNull();
    expect(unboundLayerCaption(capabilities([]), "soil-survey")).toBeNull();
    expect(unboundLayerCaption(null, "soil-survey")).toBeNull();
    expect(
      unboundLayerCaption(
        capabilities([
          { layerSlug: "drought", binding: "unbound", sourceSlug: null, reason: "no_source_bound_in_region" },
        ]),
        "soil-survey"
      )
    ).toBeNull();
  });

  it("reports the pilot's every-layer-bound payload as no absence anywhere", () => {
    // The PNW regression: the field is present and additive, and nothing it says disables anything.
    const pilot = capabilities(
      [...BOUND_GLOBAL_LAYER_SLUGS, ...UNBOUND_LAYER_SLUGS].map((layerSlug) => ({
        layerSlug,
        binding: "bound_regional" as const,
        sourceSlug: "a-regional-source",
        reason: null,
      }))
    );
    for (const layerId of LAYER_TOGGLE_IDS) {
      expect(isLayerUnboundInRegion(pilot, layerId)).toBe(false);
    }
  });
});
