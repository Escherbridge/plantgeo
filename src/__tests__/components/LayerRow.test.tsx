import { beforeEach, describe, expect, it } from "vitest";
import { act, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import { LayerRow } from "@/components/map/layer-panel/LayerRow";
import { DEFAULT_LEGEND_CONTEXT } from "@/lib/map/layer-legends";
import {
  LAYER_REGISTRY,
  LAYER_TOGGLE_IDS,
  type LayerToggleId,
} from "@/lib/map/layer-registry";
import { useLayerStore } from "@/stores/layer-store";
import { useMapStore } from "@/stores/map-store";
import { hasSelectableDay, useTimeSliderStore } from "@/stores/time-slider-store";
import type { SliderCapabilities } from "@/types/time-slider";

/**
 * The gate these cases exist for.
 *
 * `LayerRow` decides whether a layer gets a time control at all, and `LayerTimeSlider` decides
 * what that control then says. Every case here therefore goes through the ROW: rendering the
 * slider directly is exactly what let two of its states pass as covered while being structurally
 * unreachable in the app -- the row's gate required an axis, and both states exist precisely for
 * when there is none.
 */
const SERVER_CURRENT_DATE = "2019-03-07";

const CAPABILITIES: SliderCapabilities = {
  serverCurrentDate: SERVER_CURRENT_DATE,
  futureAxisDays: 2,
  streamsUnavailable: false,
  layers: [
    {
      layerName: "water-gauges",
      temporalKind: "daily_series",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: "2019-02-01",
      latestObservedDate: SERVER_CURRENT_DATE,
      coverageGaps: [],
      thinRanges: [],
      describedFromDay: null,
    },
    {
      // A published capability with a real date that still defines no axis: the one case that
      // must get a published row and no control.
      layerName: "watersheds",
      temporalKind: "snapshot",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: "2013-01-18",
      latestObservedDate: "2013-01-18",
      coverageGaps: [],
      thinRanges: [],
      describedFromDay: null,
    },
  ],
};

function renderRow(layerId: LayerToggleId) {
  return renderWithProviders(
    <ul>
      <LayerRow layerId={layerId} legendContext={DEFAULT_LEGEND_CONTEXT} />
    </ul>
  );
}

function renderEveryRow() {
  return renderWithProviders(
    <ul>
      {LAYER_TOGGLE_IDS.map((layerId) => (
        <LayerRow key={layerId} layerId={layerId} legendContext={DEFAULT_LEGEND_CONTEXT} />
      ))}
    </ul>
  );
}

function timeSliderSlotFor(layerId: LayerToggleId): HTMLElement | null {
  return screen.queryByTestId(`layer-time-slider-slot-${layerId}`);
}

describe("LayerRow time control gate", () => {
  beforeEach(() => {
    useMapStore.setState({ activeLayers: [...LAYER_TOGGLE_IDS] });
    useLayerStore.setState({ layerOpacity: {} });
    useTimeSliderStore.setState({
      layerDates: {},
      forecastVariant: "monte_carlo",
      capabilities: CAPABILITIES,
      capabilitiesUnavailable: false,
    });
  });

  it("gives a layer with an axis of its own a scrubbable track", () => {
    renderRow("water");

    expect(timeSliderSlotFor("water")).not.toBeNull();
    expect(screen.getByTestId("layer-time-slider-range-water")).not.toBeNull();
  });

  it("offers no time control until the layer is switched on", () => {
    useMapStore.setState({ activeLayers: [] });
    renderRow("water");

    // A control that adjusts nothing is the fabricated affordance layer-legends.ts exists to
    // prevent, and it is the same rule the opacity slider follows.
    expect(timeSliderSlotFor("water")).toBeNull();
  });

  it("gives a snapshot layer no time control even though it carries a published date", () => {
    renderRow("watersheds");

    expect(screen.getByTestId("layer-row-watersheds")).not.toBeNull();
    expect(timeSliderSlotFor("watersheds")).toBeNull();
  });

  it("gives a layer with no warehouse stream behind it no time control", () => {
    renderRow("soil");

    expect(screen.getByTestId("layer-row-soil")).not.toBeNull();
    expect(timeSliderSlotFor("soil")).toBeNull();
  });

  it("gives a layer this payload does not carry no time control", () => {
    // `fire-detections` is a real warehouse stream that this payload simply omits, which is what
    // an unpublished layer looks like from the client.
    renderRow("fire");

    expect(screen.getByTestId("layer-row-fire")).not.toBeNull();
    expect(timeSliderSlotFor("fire")).toBeNull();
  });

  /**
   * The single rule, asserted as a single rule.
   *
   * `hasSelectableDay` decides both whether a layer's map read is date-filtered and whether its
   * row gets a control, and the two disagreeing is the defect it was written to close: a layer
   * filtered to a day its row gave no way to change, with no one place where that could be
   * noticed. This is that place.
   */
  it("mounts a control for exactly the layers the store says have a selectable day", () => {
    renderEveryRow();

    for (const layerId of LAYER_TOGGLE_IDS) {
      if (LAYER_REGISTRY[layerId].permanentlyUnavailableReason !== null) continue;
      expect(timeSliderSlotFor(layerId) !== null, layerId).toBe(
        hasSelectableDay(CAPABILITIES, layerId)
      );
    }
  });
});

/**
 * F2/F3. `getSliderCapabilities` 500s -- the `invalid input syntax for type bigint: "0.01"`
 * incident -- and every row loses its time control at once.
 *
 * Gating the mount on an axis made that outage indistinguishable from "none of these layers has
 * dates", and it made the control's own account of it unreachable: the message needs no
 * capabilities and the gate needed an axis, which needs capabilities. The row cannot know whether
 * a layer has dates while the payload is missing, so it must not answer that question -- it
 * mounts the control and lets it speak.
 */
describe("LayerRow when the capabilities payload does not arrive", () => {
  beforeEach(() => {
    useMapStore.setState({ activeLayers: [...LAYER_TOGGLE_IDS] });
    useLayerStore.setState({ layerOpacity: {} });
    useTimeSliderStore.setState({
      layerDates: {},
      forecastVariant: "monte_carlo",
      capabilities: null,
      capabilitiesUnavailable: true,
    });
  });

  it("says the dates could not be loaded rather than losing the control in silence", () => {
    renderRow("water");

    expect(timeSliderSlotFor("water")).not.toBeNull();
    expect(screen.getByTestId("layer-time-slider-unavailable-water").textContent).toContain(
      "not a gap in the record"
    );
  });

  it("says it on every layer a warehouse stream backs, not only on one", () => {
    renderEveryRow();

    for (const layerId of LAYER_TOGGLE_IDS) {
      if (LAYER_REGISTRY[layerId].permanentlyUnavailableReason !== null) continue;
      const backedByStream = LAYER_REGISTRY[layerId].warehouseLayerName !== null;
      expect(
        screen.queryByTestId(`layer-time-slider-unavailable-${layerId}`) !== null,
        layerId
      ).toBe(backedByStream);
    }
  });

  it("claims nothing about a layer that never had dates to fail to load", () => {
    renderRow("soil");

    // `soil` names no warehouse stream at all, so a loading failure says nothing about it and
    // the row must not imply otherwise.
    expect(timeSliderSlotFor("soil")).toBeNull();
    expect(screen.queryByTestId("layer-time-slider-unavailable-soil")).toBeNull();
  });

  it("states nothing at all while the payload is merely still in flight", () => {
    useTimeSliderStore.setState({ capabilities: null, capabilitiesUnavailable: false });
    renderRow("water");

    // The slot is mounted, because the row still does not know whether this layer has dates --
    // but nothing inside it makes a claim, so a normal fast load flashes no skeleton and no
    // error into every switched-on row.
    expect(timeSliderSlotFor("water")?.textContent).toBe("");
    expect(screen.queryByTestId("layer-time-slider-unavailable-water")).toBeNull();
    expect(screen.queryByTestId("layer-time-slider-range-water")).toBeNull();
  });

  it("replaces the failure with a real axis once a payload finally lands", () => {
    renderRow("water");
    expect(screen.getByTestId("layer-time-slider-unavailable-water")).not.toBeNull();

    act(() => {
      useTimeSliderStore.getState().setCapabilities(CAPABILITIES);
    });

    expect(screen.queryByTestId("layer-time-slider-unavailable-water")).toBeNull();
    expect(screen.getByTestId("layer-time-slider-range-water")).not.toBeNull();
  });
});

/**
 * The outage of 2026-08-15, which looked like a UI regression and was a partial payload.
 *
 * `getSliderCapabilities` answers from two scans. The second covers the thirteen streams with no
 * `geo.layers` row (drought, the three soil measures, the nine climate signals) and is a
 * whole-table pass that is allowed to fail. When it did, the payload still arrived -- carrying
 * only the geo.features layers -- so `capabilities` was non-null and every stream-backed layer
 * read as "no selectable day": the same answer a layer with genuinely no history gives. Every
 * one of those sliders vanished, with nothing anywhere saying why.
 *
 * `streamsUnavailable` is the distinction, and these cases pin that the row treats it as
 * UNKNOWN (keep the control, let it speak) rather than as absence.
 */
describe("LayerRow when the payload arrives without its stream scan", () => {
  beforeEach(() => {
    useMapStore.setState({ activeLayers: [...LAYER_TOGGLE_IDS] });
    useLayerStore.setState({ layerOpacity: {} });
    useTimeSliderStore.setState({
      layerDates: {},
      forecastVariant: "monte_carlo",
      capabilities: { ...CAPABILITIES, streamsUnavailable: true },
      capabilitiesUnavailable: false,
    });
  });

  it("keeps the control on a stream-backed layer the short payload omits", () => {
    // `fire-detections` is a real warehouse stream this payload does not carry. With the scan
    // reported unavailable that omission is OUR failure, not the record's, so the control stays.
    renderRow("fire");

    expect(timeSliderSlotFor("fire")).not.toBeNull();
    expect(screen.getByTestId("layer-time-slider-no-axis-fire")).not.toBeNull();
  });

  it("says the history could not be read rather than that the layer is unpublished", () => {
    renderRow("fire");

    expect(screen.getByTestId("layer-time-slider-no-axis-fire").textContent).toContain(
      "could not be read"
    );
  });

  it("still refuses a control to a layer no warehouse stream backs", () => {
    // A failed stream scan says nothing about `soil`, which names no stream at all. The flag
    // must not become a blanket "mount everything".
    renderRow("soil");

    expect(timeSliderSlotFor("soil")).toBeNull();
  });

  it("draws the real axis for a layer the short payload does carry", () => {
    // `water-gauges` rides the geo.features scan, which succeeded. A short stream list must not
    // downgrade the layers that were actually answered.
    renderRow("water");

    expect(screen.getByTestId("layer-time-slider-range-water")).not.toBeNull();
    expect(screen.queryByTestId("layer-time-slider-no-axis-water")).toBeNull();
  });
});
