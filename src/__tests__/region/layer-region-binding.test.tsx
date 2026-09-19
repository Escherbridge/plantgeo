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
  REGION_LAYER_SLUG_BY_WAREHOUSE_NAME,
  TOGGLE_REACHABLE_REGION_LAYER_SLUGS,
  layerBindingInRegion,
  regionLayerSlugForToggle,
  toggleBindingInRegion,
  unboundLayerCaption,
} from "@/lib/map/layer-region-binding";
import { LAYER_REGISTRY, LAYER_TOGGLE_IDS, type LayerToggleId } from "@/lib/map/layer-registry";
import { getRegion } from "@/lib/region/region";
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
    const toggle = screen.getByRole("switch") as HTMLButtonElement;
    expect(toggle.disabled).toBe(true);
    expect(toggle.getAttribute("aria-checked")).toBe("false");
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
    expect((screen.getByRole("switch") as HTMLButtonElement).disabled).toBe(false);
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
    expect(toggleBindingInRegion(capabilities(globalOnlyBindings()), "interventions")).toBe("not_federated");
  });

  it("fails OPEN on payload silence about a layer the manifest BINDS", () => {
    // The deploy-window safety argument, as four assertions: a serving side that predates the
    // field, one that states an empty list, a payload that has not landed, and a layer the list
    // does not mention must all leave a manifest-bound toggle exactly where it is today.
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
      expect(toggleBindingInRegion(pilot, layerId)).not.toBe("unbound");
    }
  });
});

/**
 * The deploy-window matrix: payload present or absent, crossed with what the compiled manifest says.
 *
 * Two helpers used to answer this question in opposite directions three files apart -- payload
 * silence always fail-OPEN for a toggle, manifest silence always fail-CLOSED for land-context --
 * and the second one answered from a slug the vocabulary could not even state (STYLE-REVIEW-W5 B1).
 * One rule now covers all six cells, and these are the six.
 */
describe("layerBindingInRegion across the deploy window", () => {
  const statedBound: SliderLayerBinding[] = [
    { layerSlug: "land-context", binding: "bound_regional", sourceSlug: "a-parcel-source", reason: null },
  ];
  const statedUnbound: SliderLayerBinding[] = [
    { layerSlug: "soil-survey", binding: "unbound", sourceSlug: null, reason: "no_source_bound_in_region" },
  ];
  const statedUnboundUnknownSlug: SliderLayerBinding[] = [
    { layerSlug: "a-layer-this-build-never-heard-of", binding: "unbound", sourceSlug: null, reason: "x" },
  ];

  it("lets a stated payload row outrank the compiled manifest, in both directions", () => {
    // The manifest is a deploy behind: it does not bind land-context, and it does bind soil-survey.
    expect(layerBindingInRegion(capabilities(statedBound), "land-context")).toBe("bound");
    expect(layerBindingInRegion(capabilities(statedUnbound), "soil-survey")).toBe("unbound");
  });

  it("reads the manifest when the payload states nothing at all", () => {
    for (const silent of [null, capabilities(), capabilities([]), capabilities(statedUnbound)]) {
      expect(layerBindingInRegion(silent, "drought")).toBe("bound");
      // In the vocabulary, bound by nothing: a STATEMENT, which is why this one is not fail-open.
      expect(layerBindingInRegion(silent, "land-context")).toBe("unbound");
      // Outside the vocabulary: a genuine unknown, and an unknown layer is not an unbound one.
      expect(layerBindingInRegion(silent, "a-layer-this-build-never-heard-of")).toBe("not_federated");
    }
  });

  it("honours a payload that names a slug outside this build's vocabulary", () => {
    // A newer manifest binding a layer this bundle has never heard of is a real deployment; the
    // serving side is the authority on it, and the compiled fallback never gets to overrule it.
    expect(
      layerBindingInRegion(capabilities(statedUnboundUnknownSlug), "a-layer-this-build-never-heard-of")
    ).toBe("unbound");
  });
});

/**
 * The third enumeration the binding rule depends on, pinned to the vocabulary at last.
 *
 * `PLATFORM_LAYER_SLUGS` and the two manifests are diffed against each other in both trees; the
 * slugs the TOGGLE path actually produces were pinned to nothing, so one character in a 23-entry
 * hand-spelled table would route a federated layer to `not_federated` -- treated as available,
 * caption suppressed, empty map (STYLE-REVIEW-W6 S1, BACKLOG N9/N30).
 */
describe("REGION_LAYER_SLUG_BY_WAREHOUSE_NAME is pinned to the region vocabulary", () => {
  it("names only slugs the region manifest lists as platform layers", () => {
    const platformLayers = new Set(getRegion().platformLayers);
    for (const [warehouseLayerName, regionLayerSlug] of Object.entries(REGION_LAYER_SLUG_BY_WAREHOUSE_NAME)) {
      expect(
        platformLayers.has(regionLayerSlug),
        `${warehouseLayerName} binds through ${regionLayerSlug}, which is not a platform layer`
      ).toBe(true);
    }
  });

  it("covers every platform layer a toggle can reach, and names its one exception", () => {
    // Both directions at once. The one platform layer with no toggle-path value is stated here
    // rather than skipped, so adding a toggle for it fails this test instead of drifting:
    //   land-context           -- not a `LayerToggleId` at all; it has its own group store and
    //                             reaches `layerBindingInRegion` through LAND_CONTEXT_REGION_LAYER_SLUG.
    // `botanical-occurrences` used to be a second exception (BACKLOG N40): its three toggles all
    // carried `warehouseLayerName: null`, so no toggle of it could ever report "not available in
    // this region". N40 closed that gap with a dedicated `regionLayerSlug` field, decoupled from
    // the slider's `warehouseLayerName` key (`layer-registry.ts`, `layer-region-binding.ts`), so
    // its toggles now reach the vocabulary too.
    const PLATFORM_LAYERS_WITH_NO_TOGGLE_PATH = new Set(["land-context"]);
    const expected = new Set(
      getRegion().platformLayers.filter((slug) => !PLATFORM_LAYERS_WITH_NO_TOGGLE_PATH.has(slug))
    );

    expect(TOGGLE_REACHABLE_REGION_LAYER_SLUGS).toEqual(expected);
  });

  it("gives every toggle with a warehouse layer name a slug in the vocabulary", () => {
    // The warehouse namespace and the manifest namespace genuinely disagree (`drought-areas` ->
    // `drought`, twelve field streams -> `signal`), so the mapping cannot be identity -- but a
    // toggle that names a warehouse layer and resolves to nothing is a hole in the rule.
    for (const layerId of LAYER_TOGGLE_IDS) {
      if (LAYER_REGISTRY[layerId].warehouseLayerName === null) continue;
      const slug = regionLayerSlugForToggle(layerId);
      if (slug === null) continue; // `interventions` / `strategy-recommendations`: documented non-layers.
      expect(getRegion().platformLayers, `${layerId} binds through ${slug}`).toContain(slug);
    }
  });
});
