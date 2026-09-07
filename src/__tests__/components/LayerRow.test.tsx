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

/** The scrubber itself, which is a narrower question than "does this row speak about time". */
function timeRangeInputFor(layerId: LayerToggleId): HTMLElement | null {
  return screen.queryByTestId(`layer-time-slider-range-${layerId}`);
}

/** The uniform status block, and the state it declares. */
function timeStatusStateFor(layerId: LayerToggleId): string | null {
  return screen.queryByTestId(`layer-time-status-${layerId}`)?.dataset.state ?? null;
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

  /**
   * Rewritten 2026-09-07 from "gives a snapshot layer no time control". The CONTROL is still
   * refused, for the reason it always was -- `watersheds` publishes a real capability with a real
   * date, but 96% of its 9,396 HUC12 basins carry one 2013 WBD loaddate, and a track over them
   * would advertise years of scrubbing across a boundary set that draws identically on every one
   * of those days. What changed is that refusing the control no longer means saying nothing: the
   * row now states that this layer legitimately has no time axis, which is a first-class answer
   * and not the same blank space a still-loading row used to leave.
   */
  it("gives a snapshot layer no scrubber, and says why instead of saying nothing", () => {
    renderRow("watersheds");

    expect(screen.getByTestId("layer-row-watersheds")).not.toBeNull();
    expect(timeRangeInputFor("watersheds")).toBeNull();
    expect(timeStatusStateFor("watersheds")).toBe("no_time_axis");
  });

  it("gives a layer with no warehouse stream behind it no time control and no caption", () => {
    renderRow("soil");

    // `soil` was never in the census at all, so there is nothing about time to report -- an
    // "unavailable" or "loading" line here would be a claim about a layer the payload never
    // describes either way.
    expect(screen.getByTestId("layer-row-soil")).not.toBeNull();
    expect(timeSliderSlotFor("soil")).toBeNull();
    expect(timeStatusStateFor("soil")).toBeNull();
  });

  it("gives a layer this payload does not carry no scrubber, and names the absence", () => {
    // `fire-detections` is a real warehouse stream that this payload simply omits, which is what
    // an unpublished layer looks like from the client. In production it is omitted because its
    // availability index is still being built -- the case the reader most needs told apart from
    // a slow load, and the one that used to render as an empty row.
    renderRow("fire");

    expect(screen.getByTestId("layer-row-fire")).not.toBeNull();
    expect(timeRangeInputFor("fire")).toBeNull();
    expect(timeStatusStateFor("fire")).toBe("empty");
  });

  /**
   * The single rule, asserted as a single rule.
   *
   * `hasSelectableDay` decides both whether a layer's map read is date-filtered and whether its
   * row gets a SCRUBBER, and the two disagreeing is the defect it was written to close: a layer
   * filtered to a day its row gave no way to change, with no one place where that could be
   * noticed. This is that place.
   *
   * Asserted on the range input rather than on the slot since 2026-09-07: the slot now mounts for
   * every stream-backed layer so that each can state its own situation, so the slot is no longer
   * the control -- the control is. The rule itself is untouched.
   */
  it("mounts a scrubber for exactly the layers the store says have a selectable day", () => {
    renderEveryRow();

    for (const layerId of LAYER_TOGGLE_IDS) {
      if (LAYER_REGISTRY[layerId].permanentlyUnavailableReason !== null) continue;
      expect(timeRangeInputFor(layerId) !== null, layerId).toBe(
        hasSelectableDay(CAPABILITIES, layerId)
      );
    }
  });

  /**
   * The owner's ask, at the level it was made: "ideally all the layer UI interfaces can be
   * uniform". Every switched-on layer a stream backs says something about time, and every one of
   * them says it in the same place -- either a scrubber or a status block, never neither.
   */
  it("leaves no stream-backed layer silent about its own time state", () => {
    renderEveryRow();

    for (const layerId of LAYER_TOGGLE_IDS) {
      if (LAYER_REGISTRY[layerId].permanentlyUnavailableReason !== null) continue;
      if (LAYER_REGISTRY[layerId].warehouseLayerName === null) continue;
      const speaks = timeRangeInputFor(layerId) !== null || timeStatusStateFor(layerId) !== null;
      expect(speaks, layerId).toBe(true);
      // And never both: two surfaces stating one fact, one line apart, is the defect the dock
      // already recorded once.
      expect(
        timeRangeInputFor(layerId) !== null && timeStatusStateFor(layerId) !== null,
        layerId
      ).toBe(false);
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
    expect(timeStatusStateFor("water")).toBe("error");
    expect(screen.getByTestId("layer-time-status-detail-water").textContent).toContain(
      "not a gap in the record"
    );
  });

  it("says it on every layer a warehouse stream backs, not only on one", () => {
    renderEveryRow();

    for (const layerId of LAYER_TOGGLE_IDS) {
      if (LAYER_REGISTRY[layerId].permanentlyUnavailableReason !== null) continue;
      const backedByStream = LAYER_REGISTRY[layerId].warehouseLayerName !== null;
      expect(timeStatusStateFor(layerId) === "error", layerId).toBe(backedByStream);
    }
  });

  it("claims nothing about a layer that never had dates to fail to load", () => {
    renderRow("soil");

    // `soil` names no warehouse stream at all, so a loading failure says nothing about it and
    // the row must not imply otherwise.
    expect(timeSliderSlotFor("soil")).toBeNull();
    expect(timeStatusStateFor("soil")).toBeNull();
  });

  /**
   * Rewritten 2026-09-07, and the assertion is inverted on purpose. It used to read
   * `slot.textContent === ""`, pinning the deliberate silence of a first load -- which was right
   * while a warm payload was the only case anyone had measured, and wrong the moment a COLD
   * `getSliderCapabilities` was measured at 7.6-8.5s against production. For those eight seconds
   * every switched-on row rendered exactly what a layer with no dates renders, which is the
   * report this work came from. The flash the old rule guarded against is now handled by a CSS
   * delay inside the block (see `LayerTimeStatus`), so a fast payload still shows nothing while a
   * slow one is stated plainly.
   */
  it("states that the payload is still in flight rather than rendering an empty row", () => {
    useTimeSliderStore.setState({ capabilities: null, capabilitiesUnavailable: false });
    renderRow("water");

    expect(timeStatusStateFor("water")).toBe("loading");
    expect(screen.getByTestId("layer-time-status-detail-water").textContent).toContain(
      "few seconds"
    );
    // Loading is not failure, and the row must not say the stronger thing.
    expect(screen.getByTestId("layer-time-status-detail-water").textContent).not.toContain(
      "not a gap in the record"
    );
    expect(screen.queryByTestId("layer-time-slider-range-water")).toBeNull();
  });

  it("replaces the failure with a real axis once a payload finally lands", () => {
    renderRow("water");
    expect(timeStatusStateFor("water")).toBe("error");

    act(() => {
      useTimeSliderStore.getState().setCapabilities(CAPABILITIES);
    });

    expect(timeStatusStateFor("water")).toBeNull();
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
    expect(timeStatusStateFor("fire")).toBe("error");
  });

  it("says the history could not be read rather than that the layer is unpublished", () => {
    renderRow("fire");

    expect(screen.getByTestId("layer-time-status-detail-fire").textContent).toContain(
      "could not be read"
    );
    // The attribution is the whole point: the same absence is `empty` -- "not published yet" --
    // when the payload is whole, and an `error` only because the scan reported itself short.
    expect(screen.getByTestId("layer-time-status-detail-fire").textContent).not.toContain(
      "Not published"
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
    expect(timeStatusStateFor("water")).toBeNull();
  });
});

/**
 * The per-timeline reset control. Gated on `mountsDayControls` -- a layer with no timeline has no
 * per-day cache entries to offer resetting -- and, once mounted, armed by an explicit two-step
 * confirm rather than firing on the first click. See src/components/map/AGENTS.md
 * §synced-days-track for the full home decision and its rationale.
 *
 * That gate is now STRICTER than the slot's, which is the point of the split landed 2026-09-07:
 * the slot mounts for every stream-backed layer so each can state its own time situation, while
 * these two buttons still mount only where there is a timeline for them to act on. A sentence
 * claims nothing about what a button would do; a button that does nothing is the fabricated
 * affordance every other control on this row is guarded against.
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

  it("stays off a layer with no timeline, even though that row now speaks about time", () => {
    renderRow("watersheds");

    // The row states its situation -- a snapshot has no time axis -- and still offers nothing to
    // reset, because a snapshot has no per-day cache entries. The status block and the buttons
    // answer two different questions and must not share one gate.
    expect(timeStatusStateFor("watersheds")).toBe("no_time_axis");
    expect(screen.queryByTestId("layer-sync-reset-watersheds")).toBeNull();
    expect(screen.queryByTestId("layer-refresh-watersheds")).toBeNull();
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
