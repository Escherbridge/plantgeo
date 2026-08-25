import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import { LayerRow } from "@/components/map/layer-panel/LayerRow";
import { DEFAULT_LEGEND_CONTEXT } from "@/lib/map/layer-legends";
import { LAYER_PUBLICATION_STANDINGS } from "@/lib/map/layer-publication-standing";
import {
  LAYER_REGISTRY,
  LAYER_TOGGLE_IDS,
  type LayerToggleId,
} from "@/lib/map/layer-registry";
import { useLayerStore } from "@/stores/layer-store";
import { useMapStore } from "@/stores/map-store";
import { hasSelectableDay, useTimeSliderStore } from "@/stores/time-slider-store";
import {
  clearLayerSyncedDays,
  useLayerSyncedBytes,
  useSyncedDays,
  useSyncIndexReady,
} from "@/stores/sync-index-store";
import type { SliderCapabilities } from "@/types/time-slider";

/**
 * `sync-index-store` is a pinned contract owned by a parallel lane (see
 * src/components/map/AGENTS.md §synced-days-track) -- mocked exactly like `useOfflineSync` is in
 * `OfflinePanel.test.tsx`. Defaulted ready-and-empty here so every gate test above and below,
 * none of which cares about sync state, renders the reset control disabled and inert without
 * needing to know that.
 */
vi.mock("@/stores/sync-index-store", () => ({
  useSyncedDays: vi.fn(),
  useSyncIndexReady: vi.fn(),
  useLayerSyncedBytes: vi.fn(),
  clearLayerSyncedDays: vi.fn(),
}));

const mockUseSyncedDays = vi.mocked(useSyncedDays);
const mockUseSyncIndexReady = vi.mocked(useSyncIndexReady);
const mockUseLayerSyncedBytes = vi.mocked(useLayerSyncedBytes);
const mockClearLayerSyncedDays = vi.mocked(clearLayerSyncedDays);

beforeEach(() => {
  mockUseSyncedDays.mockReturnValue(new Set());
  mockUseSyncIndexReady.mockReturnValue(true);
  mockUseLayerSyncedBytes.mockReturnValue(0);
  mockClearLayerSyncedDays.mockReset().mockResolvedValue(undefined);
});

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

/**
 * The per-timeline reset control. Gated on the SAME `mountsTimeSlider` boolean as the slider
 * slot -- a layer with no timeline has no per-day cache entries to offer resetting -- and, once
 * mounted, armed by an explicit two-step confirm rather than firing on the first click. See
 * src/components/map/AGENTS.md §synced-days-track for the full home decision and its rationale.
 */
describe("layer sync reset control", () => {
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

  it("mounts only where the time slider itself mounts", () => {
    renderRow("watersheds");

    expect(timeSliderSlotFor("watersheds")).toBeNull();
    expect(screen.queryByTestId("layer-sync-reset-watersheds")).toBeNull();
  });

  /**
   * `aria-disabled`, not the native `disabled` attribute -- see the trigger button's own doc for
   * why the button must stay FOCUSABLE even when inert (a successful clear disables it in the
   * same commit that returns focus to it). `.disabled` (the DOM property) would read `false`
   * here regardless of state, since the native attribute is never set at all.
   */
  it("is inert and named honestly when the layer holds nothing yet", () => {
    renderRow("water");

    const button = screen.getByTestId("layer-sync-reset-water") as HTMLButtonElement;
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(button.title).toContain("Nothing saved");
  });

  it("is inert while the sync index is still hydrating, not falsely enabled", () => {
    mockUseSyncIndexReady.mockReturnValue(false);
    renderRow("water");

    expect(
      screen.getByTestId("layer-sync-reset-water").getAttribute("aria-disabled")
    ).toBe("true");
  });

  it("enables and names the count once the layer holds saved days", () => {
    mockUseSyncedDays.mockReturnValue(new Set(["2019-01-01", "2019-01-02"]));
    mockUseLayerSyncedBytes.mockReturnValue(2_400_000);
    renderRow("water");

    const button = screen.getByTestId("layer-sync-reset-water") as HTMLButtonElement;
    expect(button.getAttribute("aria-disabled")).toBeNull();
    expect(button.textContent).toContain("2");
  });

  it("requires a second, explicit confirmation before clearing anything", () => {
    mockUseSyncedDays.mockReturnValue(new Set(["2019-01-01"]));
    renderRow("water");

    fireEvent.click(screen.getByTestId("layer-sync-reset-water"));

    expect(mockClearLayerSyncedDays).not.toHaveBeenCalled();
    const confirmBlock = screen.getByTestId("layer-sync-reset-confirm-water");
    expect(confirmBlock).not.toBeNull();
    // Scoped honestly: must never read as "clear everything".
    expect(confirmBlock.textContent).toContain("Downloaded map tiles");
    expect(confirmBlock.textContent).toContain("not yet synced to the server are not affected");
  });

  it("clears nothing and returns focus to the trigger on Cancel", () => {
    mockUseSyncedDays.mockReturnValue(new Set(["2019-01-01"]));
    renderRow("water");

    fireEvent.click(screen.getByTestId("layer-sync-reset-water"));
    fireEvent.click(screen.getByTestId("layer-sync-reset-cancel-water"));

    expect(mockClearLayerSyncedDays).not.toHaveBeenCalled();
    expect(screen.queryByTestId("layer-sync-reset-confirm-water")).toBeNull();
    expect(document.activeElement).toBe(screen.getByTestId("layer-sync-reset-water"));
  });

  it("cancels on Escape as well as on the Cancel button", () => {
    mockUseSyncedDays.mockReturnValue(new Set(["2019-01-01"]));
    renderRow("water");

    fireEvent.click(screen.getByTestId("layer-sync-reset-water"));
    fireEvent.keyDown(screen.getByTestId("layer-sync-reset-confirm-water"), { key: "Escape" });

    expect(screen.queryByTestId("layer-sync-reset-confirm-water")).toBeNull();
  });

  /**
   * `clearLayerSyncedDays` is documented never to reject, so "the call resolved" alone must
   * never be read as "the clear happened" -- a silent no-op (quota pressure, a version-change
   * lock) resolves identically to a real deletion. The mock here simulates the ONLY honest
   * signal available: whether `useSyncedDays`'s own report actually changes.
   */
  it("calls clearLayerSyncedDays with this layer's id, and closes only once the index reflects it", async () => {
    let currentlySynced = new Set(["2019-01-01"]);
    mockUseSyncedDays.mockImplementation(() => currentlySynced);
    mockClearLayerSyncedDays.mockImplementation(async () => {
      currentlySynced = new Set();
    });
    renderRow("water");

    fireEvent.click(screen.getByTestId("layer-sync-reset-water"));
    await act(async () => {
      fireEvent.click(screen.getByTestId("layer-sync-reset-confirm-button-water"));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockClearLayerSyncedDays).toHaveBeenCalledTimes(1);
    expect(mockClearLayerSyncedDays).toHaveBeenCalledWith("water");
    expect(screen.queryByTestId("layer-sync-reset-confirm-water")).toBeNull();
  });

  it("returns focus to the trigger after a successful clear too, not only after Cancel", async () => {
    let currentlySynced = new Set(["2019-01-01"]);
    mockUseSyncedDays.mockImplementation(() => currentlySynced);
    mockClearLayerSyncedDays.mockImplementation(async () => {
      currentlySynced = new Set();
    });
    renderRow("water");

    fireEvent.click(screen.getByTestId("layer-sync-reset-water"));
    await act(async () => {
      fireEvent.click(screen.getByTestId("layer-sync-reset-confirm-button-water"));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(document.activeElement).toBe(screen.getByTestId("layer-sync-reset-water"));
  });

  /**
   * The defect the reviewer reproduced: a `Promise<void>` that resolves without rejecting is
   * not evidence anything was deleted. The index here is left completely unchanged by the
   * mocked call, exactly like a real silent no-op would leave it.
   */
  it("treats an unchanged index after the call resolves as a failure, since it never rejects", async () => {
    mockUseSyncedDays.mockReturnValue(new Set(["2019-01-01"]));
    mockClearLayerSyncedDays.mockResolvedValue(undefined);
    renderRow("water");

    fireEvent.click(screen.getByTestId("layer-sync-reset-water"));
    await act(async () => {
      fireEvent.click(screen.getByTestId("layer-sync-reset-confirm-button-water"));
      await Promise.resolve();
      await Promise.resolve();
    });

    // The dialog stays open and says so -- it must never close as though the clear had worked.
    const confirmBlock = screen.getByTestId("layer-sync-reset-confirm-water");
    expect(confirmBlock).not.toBeNull();
    expect(confirmBlock.textContent).toContain("Could not clear");
    expect(confirmBlock.textContent).toContain("nothing here changed");
  });

  it("is inert by the readiness half of the gate even while the index already reports days", () => {
    // A live write racing hydration: the index has entries, but `useSyncIndexReady` has not
    // flipped yet. `hasSyncedDays` is `syncIndexReady && count > 0`, and this pins the ORDER --
    // a future edit that swapped the operands or dropped the readiness half would still pass a
    // naive "is it inert with zero days" test, but not this one.
    mockUseSyncIndexReady.mockReturnValue(false);
    mockUseSyncedDays.mockReturnValue(new Set(["2019-01-01", "2019-01-02"]));
    renderRow("water");

    expect(
      screen.getByTestId("layer-sync-reset-water").getAttribute("aria-disabled")
    ).toBe("true");
  });

  it("is never nested inside the slider's own slot, where Latest lives", () => {
    mockUseSyncedDays.mockReturnValue(new Set(["2019-01-01"]));
    renderRow("water");

    const slot = screen.getByTestId("layer-time-slider-slot-water");
    const resetButton = screen.getByTestId("layer-sync-reset-water");
    expect(slot.contains(resetButton)).toBe(false);
  });

  it("threads the pending signal through to its time slider", () => {
    renderWithProviders(
      <ul>
        <LayerRow layerId="water" legendContext={DEFAULT_LEGEND_CONTEXT} isFetchingSelectedDay />
      </ul>
    );

    expect(screen.getByTestId("layer-time-slider-range-water").className).toContain("is-pending");
  });
});

/**
 * The four surfaces no Parquet lane backs, and the caption that replaces their blank map.
 *
 * `interventions`, `strategy-recommendations` and `demand-heatmap` all switch on, mount a live
 * renderer and paint nothing, because their blocker sits upstream of the map entirely -- a
 * publish step nothing invokes, an untrained model, an anonymity floor. None of them is
 * WITHHELD, so `permanentlyUnavailableReason` (which disables the switch and drops the row out
 * of the dock's group count) is the wrong instrument; `soil` is the one that genuinely is.
 */
describe("LayerRow publication standings", () => {
  /** A day before every axis below, so the derived availability caption is live for both rows. */
  const DAY_BEFORE_EVERY_AXIS = "2019-01-01";

  const CAPABILITIES_WITH_BOTH_AXES: SliderCapabilities = {
    ...CAPABILITIES,
    layers: [
      ...CAPABILITIES.layers,
      {
        layerName: "fire-detections",
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
        layerName: "interventions",
        temporalKind: "daily_series",
        forecastHorizonDays: 0,
        forecastVariants: [],
        earliestObservedDate: "2019-02-01",
        latestObservedDate: SERVER_CURRENT_DATE,
        coverageGaps: [],
        thinRanges: [],
        describedFromDay: null,
      },
    ],
  };

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

  it.each(["interventions", "strategy-recommendations", "demand-heatmap"] as const)(
    "states why %s is empty while it is switched on",
    (layerId) => {
      renderRow(layerId);

      const standing = LAYER_PUBLICATION_STANDINGS[layerId];
      if (standing === undefined) throw new Error(`expected a standing for ${layerId}`);
      const row = screen.getByTestId(`layer-row-${layerId}`);
      expect(row.textContent).toContain(standing.reason);
      expect(row.textContent).toContain(standing.unblockedBy);
    }
  );

  it("states it before the layer is ever switched on, not only after the empty map", () => {
    // The whole point: a reader who must flip the switch to learn the layer draws nothing has
    // already seen the blank this caption replaces. `unavailableReason` is gated on `isActive`
    // precisely because it is about the DAY; a standing is about the layer.
    useMapStore.setState({ activeLayers: [] });
    renderRow("interventions");

    const standing = LAYER_PUBLICATION_STANDINGS.interventions;
    if (standing === undefined) throw new Error("expected an interventions standing");
    expect(screen.getByTestId("layer-row-interventions").textContent).toContain(standing.reason);
  });

  it("keeps the switch live, because these layers are empty and not withheld", () => {
    renderRow("interventions");

    const toggle = screen.getByLabelText("Show Interventions on map");
    expect(toggle.hasAttribute("disabled")).toBe(false);
    expect(toggle.getAttribute("aria-disabled")).toBeNull();
    expect(toggle.getAttribute("aria-checked")).toBe("true");
  });

  it("replaces the history claim, which is about a day and not about the real blocker", () => {
    // Both rows resolve to a day before their axis, so `layerAvailabilityAt` answers
    // `not_yet_observed` for each and `describeAvailability` captions it "has no observations
    // this far back". That is true of `fire` and false of `interventions`, whose rows exist and
    // are approved -- they were simply never published. The positive control is what makes this
    // non-vacuous: the mechanism is demonstrably live on the row beside it.
    useTimeSliderStore.setState({
      capabilities: CAPABILITIES_WITH_BOTH_AXES,
      layerDates: { fire: DAY_BEFORE_EVERY_AXIS, interventions: DAY_BEFORE_EVERY_AXIS },
    });
    renderEveryRow();

    expect(screen.getByTestId("layer-row-fire").textContent).toContain(
      "fire-detections has no observations this far back."
    );
    const interventionsRow = screen.getByTestId("layer-row-interventions");
    expect(interventionsRow.textContent).not.toContain("has no observations this far back");
    const standing = LAYER_PUBLICATION_STANDINGS.interventions;
    if (standing === undefined) throw new Error("expected an interventions standing");
    expect(interventionsRow.textContent).toContain(standing.reason);
  });

  it("leaves the withheld raster captioned by the registry, with no second sentence", () => {
    renderRow("soil");

    const row = screen.getByTestId("layer-row-soil");
    expect(row.textContent).toContain(LAYER_REGISTRY.soil.permanentlyUnavailableReason!);
    expect(LAYER_PUBLICATION_STANDINGS.soil).toBeUndefined();
  });

  it("captions no lane-backed row, so a working layer carries no excuse", () => {
    renderEveryRow();

    for (const layerId of LAYER_TOGGLE_IDS) {
      const standing = LAYER_PUBLICATION_STANDINGS[layerId];
      if (standing === undefined) continue;
      // Only the declared non-lane surfaces may carry one; the rest of the dock stays silent.
      expect(["interventions", "strategy-recommendations", "demand-heatmap"]).toContain(layerId);
    }
  });
});
