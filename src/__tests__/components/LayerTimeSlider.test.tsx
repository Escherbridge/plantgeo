import { beforeEach, describe, expect, it, vi } from "vitest";
import { createEvent, fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import { LayerTimeSlider } from "@/components/map/layer-panel/LayerTimeSlider";
import {
  buildCoverageSegments,
  buildSyncedDayRuns,
  dayCoverageState,
  describeCoverageBand,
  describeCoverageTopology,
  describeDayCoverage,
  describeSyncedDayBand,
  drawCoverageBands,
  drawSyncedDayBands,
  MAX_SPOKEN_COVERAGE_RUNS,
  MINIMUM_DRAWN_BAND_PERCENT,
  percentOfDayOffset,
  SYNCED_DAY_APPEARANCE,
  TRACK_REGION_APPEARANCE,
} from "@/components/map/layer-panel/layer-coverage-track";
import { layerLabel } from "@/lib/map/layer-registry";
import { addDays, dayOffset, useTimeSliderStore } from "@/stores/time-slider-store";
import { useSyncedDays, useSyncIndexReady } from "@/stores/sync-index-store";
import type { SliderCapabilities, SliderLayerCapability } from "@/types/time-slider";

/**
 * `sync-index-store` is a pinned contract owned by a parallel lane (see
 * src/components/map/AGENTS.md §synced-days-track) -- mocked here exactly like
 * `useOfflineSync` is in `OfflinePanel.test.tsx`, rather than exercised for real, so these cases
 * assert against a controlled set/readiness pair instead of a real IndexedDB-backed hydration.
 */
vi.mock("@/stores/sync-index-store", () => ({
  useSyncedDays: vi.fn(),
  useSyncIndexReady: vi.fn(),
}));

const mockUseSyncedDays = vi.mocked(useSyncedDays);
const mockUseSyncIndexReady = vi.mocked(useSyncIndexReady);

/**
 * Deliberately nowhere near the machine's own date: a stray `new Date()` deciding "today" flips
 * every observed/future assertion below.
 */
const SERVER_CURRENT_DATE = "2019-03-07";
const FIRST_DAY = "2019-01-01";
/** futureAxisDays is 2 and every fixture horizon is 0, so every axis ends two days past today. */
const LAST_DAY = "2019-03-09";

/** Two days short of the server's today, so vegetation opens BEHIND the live edge on purpose. */
const VEGETATION_LATEST_DATE = "2019-03-05";
const VEGETATION_GAP = { from: "2019-01-10", to: "2019-01-12" };
const VEGETATION_THIN = { from: "2019-02-01", to: "2019-02-03" };

/**
 * The day `weather-observations` reports its truncated range lists as reaching back to.
 *
 * Everything below it is UNDESCRIBED: the server capped both lists at MAX_REPORTED_DAY_RANGES
 * and told us where they stop, so their silence about an earlier day is not evidence the day
 * was published. See `SliderLayerCapability.describedFromDay`.
 */
const WEATHER_DESCRIBED_FROM_DAY = "2019-02-10";

const CAPABILITIES: SliderCapabilities = {
  serverCurrentDate: SERVER_CURRENT_DATE,
  futureAxisDays: 2,
  streamsUnavailable: false,
  layers: [
    {
      layerName: "vegetation",
      temporalKind: "daily_series",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: FIRST_DAY,
      latestObservedDate: VEGETATION_LATEST_DATE,
      coverageGaps: [VEGETATION_GAP],
      thinRanges: [VEGETATION_THIN],
      describedFromDay: null,
    },
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
      // A published layer whose newest day the server could not name. The row must not claim
      // staleness it cannot measure.
      layerName: "sensors",
      temporalKind: "daily_series",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: FIRST_DAY,
      latestObservedDate: null,
      coverageGaps: [],
      thinRanges: [],
      describedFromDay: null,
    },
    {
      layerName: "interventions",
      temporalKind: "snapshot",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: "2019-02-01",
      latestObservedDate: "2019-02-01",
      coverageGaps: [],
      thinRanges: [],
      describedFromDay: null,
    },
    {
      // A layer whose range lists were TRUNCATED. Both lists are empty below the boundary and
      // that says nothing at all about those days, which is the whole point of the fixture.
      layerName: "weather-observations",
      temporalKind: "daily_series",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: FIRST_DAY,
      latestObservedDate: SERVER_CURRENT_DATE,
      coverageGaps: [{ from: "2019-02-20", to: "2019-02-22" }],
      thinRanges: [],
      describedFromDay: WEATHER_DESCRIBED_FROM_DAY,
    },
  ],
};

/** The domain LayerTimeSlider draws for vegetation, rebuilt here for the pure-geometry cases. */
const VEGETATION_DOMAIN = { firstDay: FIRST_DAY, today: SERVER_CURRENT_DATE, lastDay: LAST_DAY };

function vegetationCapability(
  overrides: Partial<SliderLayerCapability> = {}
): SliderLayerCapability {
  return { ...CAPABILITIES.layers[0], ...overrides };
}

function bandsOfKind(
  layerId: string,
  kind: "absent" | "governed_absence" | "thin"
): HTMLElement[] {
  return screen.queryAllByTestId(`layer-time-slider-band-${layerId}-${kind}`);
}

function leftPercentOf(element: HTMLElement): number {
  return Number.parseFloat(element.style.left);
}

describe("LayerTimeSlider", () => {
  beforeEach(() => {
    useTimeSliderStore.setState({
      layerDates: {},
      forecastVariant: "monte_carlo",
      capabilities: CAPABILITIES,
      // Explicit, because one case below drives the failure branch: leaving it to whatever the
      // previous test left behind would make the in-flight and failed states swap silently.
      capabilitiesUnavailable: false,
    });
    // Ready-and-empty by default: harmless for every case above that predates the synced-days
    // line (see "the synced-days line" below for why this specific pair renders nothing extra).
    mockUseSyncIndexReady.mockReturnValue(true);
    mockUseSyncedDays.mockReturnValue(new Set());
  });

  it("draws a coverage gap and a thin range as separate bands over one dense base", () => {
    renderWithProviders(<LayerTimeSlider layerId="vegetation" />);

    const absent = bandsOfKind("vegetation", "absent");
    const thin = bandsOfKind("vegetation", "thin");

    expect(absent).toHaveLength(1);
    expect(absent[0].dataset.from).toBe(VEGETATION_GAP.from);
    expect(absent[0].dataset.to).toBe(VEGETATION_GAP.to);

    expect(thin).toHaveLength(1);
    expect(thin[0].dataset.from).toBe(VEGETATION_THIN.from);
    expect(thin[0].dataset.to).toBe(VEGETATION_THIN.to);

    // January's hole is left of February's thin run, and neither is drawn at the axis start.
    expect(leftPercentOf(absent[0])).toBeGreaterThan(0);
    expect(leftPercentOf(absent[0])).toBeLessThan(leftPercentOf(thin[0]));

    // One element for every dense run would be hundreds on a real axis; the dense base is a
    // single bar and the exceptions are painted over it.
    expect(screen.getByTestId("layer-time-slider-dense-base-vegetation")).not.toBeNull();
    expect(screen.getByTestId("layer-time-slider-future-band-vegetation")).not.toBeNull();
  });

  it("draws no band at all for a layer whose record has no holes and no thin days", () => {
    renderWithProviders(<LayerTimeSlider layerId="water" />);

    expect(bandsOfKind("water", "absent")).toHaveLength(0);
    expect(bandsOfKind("water", "thin")).toHaveLength(0);
    expect(screen.getByTestId("layer-time-slider-dense-base-water")).not.toBeNull();
  });

  it("marks a layer that is behind its own latest published day, and offers the way back", () => {
    useTimeSliderStore.setState({ layerDates: { vegetation: "2019-02-14" } });
    renderWithProviders(<LayerTimeSlider layerId="vegetation" />);

    expect(screen.getByTestId("layer-time-slider-behind-mark-vegetation").textContent).toBe(
      "Not latest"
    );
    // How far behind, in words, beside the day it is behind -- a mixed-time map read from a
    // screenshot has nothing else to go on.
    expect(screen.getByTestId("layer-time-slider-note-vegetation").textContent).toContain(
      `19 days behind its latest, ${VEGETATION_LATEST_DATE}`
    );

    fireEvent.click(screen.getByTestId("layer-time-slider-latest-button-vegetation"));

    // The override is DROPPED, not rewritten to today's value of the latest day: the layer goes
    // back to following its own live edge as later payloads land.
    expect(useTimeSliderStore.getState().layerDates.vegetation).toBeUndefined();
    expect(screen.queryByTestId("layer-time-slider-behind-mark-vegetation")).toBeNull();
    expect(
      (screen.getByTestId("layer-time-slider-date-input-vegetation") as HTMLInputElement).value
    ).toBe(VEGETATION_LATEST_DATE);
  });

  it("never marks a layer whose latest published day the server could not name", () => {
    renderWithProviders(<LayerTimeSlider layerId="sensors" />);

    expect(screen.queryByTestId("layer-time-slider-behind-mark-sensors")).toBeNull();
    expect(
      (screen.getByTestId("layer-time-slider-latest-button-sensors") as HTMLButtonElement).disabled
    ).toBe(true);
  });

  it("opens each layer on its own latest published day rather than on a shared today", () => {
    renderWithProviders(
      <>
        <LayerTimeSlider layerId="vegetation" />
        <LayerTimeSlider layerId="water" />
      </>
    );

    expect(
      (screen.getByTestId("layer-time-slider-date-input-vegetation") as HTMLInputElement).value
    ).toBe(VEGETATION_LATEST_DATE);
    expect(
      (screen.getByTestId("layer-time-slider-date-input-water") as HTMLInputElement).value
    ).toBe(SERVER_CURRENT_DATE);
  });

  it("clamps a typed date to that layer's own domain at both ends", () => {
    renderWithProviders(<LayerTimeSlider layerId="vegetation" />);
    const field = screen.getByTestId("layer-time-slider-date-input-vegetation") as HTMLInputElement;

    expect(field.min).toBe(FIRST_DAY);
    expect(field.max).toBe(LAST_DAY);

    fireEvent.change(field, { target: { value: "2030-01-01" } });
    expect(useTimeSliderStore.getState().layerDates.vegetation).toBe(LAST_DAY);

    fireEvent.change(field, { target: { value: "2018-06-01" } });
    expect(useTimeSliderStore.getState().layerDates.vegetation).toBe(FIRST_DAY);

    // A partly typed day leaves the input empty; committing that would clamp the layer to an
    // axis edge on the first keystroke of a typed date.
    fireEvent.change(field, { target: { value: "" } });
    expect(useTimeSliderStore.getState().layerDates.vegetation).toBe(FIRST_DAY);
  });

  it("clamps a typed date against the layer's own axis and not some other layer's", () => {
    renderWithProviders(<LayerTimeSlider layerId="water" />);
    const field = screen.getByTestId("layer-time-slider-date-input-water") as HTMLInputElement;

    // Water's record starts 2019-02-01; vegetation's starts 2019-01-01. A day between the two is
    // in range for one layer and out of range for the other, which is the whole point of a
    // per-row axis.
    expect(field.min).toBe("2019-02-01");
    fireEvent.change(field, { target: { value: "2019-01-15" } });
    expect(useTimeSliderStore.getState().layerDates.water).toBe("2019-02-01");
  });

  it("steps one day per arrow key by delegating to the native range, and swallows no key", () => {
    renderWithProviders(<LayerTimeSlider layerId="vegetation" />);
    const slider = screen.getByTestId("layer-time-slider-range-vegetation") as HTMLInputElement;

    // The value is a whole-day offset with step 1, which is what makes one arrow press one day
    // and Home/End the two ends of this layer's own record.
    expect(slider.step).toBe("1");
    expect(slider.min).toBe("0");
    expect(slider.max).toBe(String(dayOffset(FIRST_DAY, LAST_DAY)));
    expect(slider.value).toBe(String(dayOffset(FIRST_DAY, VEGETATION_LATEST_DATE)));

    // jsdom does not implement the native step, so the two halves are asserted separately: the
    // key is not intercepted, and one offset up is exactly one day.
    const arrowRight = createEvent.keyDown(slider, { key: "ArrowRight" });
    fireEvent(slider, arrowRight);
    expect(arrowRight.defaultPrevented).toBe(false);

    fireEvent.change(slider, { target: { value: String(Number(slider.value) + 1) } });
    expect(useTimeSliderStore.getState().layerDates.vegetation).toBe(
      addDays(VEGETATION_LATEST_DATE, 1)
    );
  });

  it("names the layer in every control, so a dozen sliders on one screen are told apart", () => {
    renderWithProviders(
      <>
        <LayerTimeSlider layerId="vegetation" />
        <LayerTimeSlider layerId="water" />
      </>
    );

    expect(
      screen.getByRole("slider", { name: `${layerLabel("vegetation")} date` })
    ).not.toBeNull();
    expect(screen.getByRole("slider", { name: `${layerLabel("water")} date` })).not.toBeNull();
    expect(
      screen.getByRole("button", {
        name: `Return ${layerLabel("vegetation")} to its latest published day`,
      })
    ).not.toBeNull();
    // The date field is named through a visually hidden <label>: `input[type=date]` maps to no
    // ARIA role, so a label is the only thing that can name it for a screen reader.
    expect(screen.getByLabelText(`${layerLabel("water")} date, YYYY-MM-DD`)).not.toBeNull();
    expect(screen.getByLabelText(`${layerLabel("vegetation")} date, YYYY-MM-DD`)).not.toBeNull();
  });

  it("says the layer has nothing on a date inside a coverage gap, in the value a reader hears", () => {
    useTimeSliderStore.setState({ layerDates: { vegetation: VEGETATION_GAP.from } });
    renderWithProviders(<LayerTimeSlider layerId="vegetation" />);

    const slider = screen.getByTestId("layer-time-slider-range-vegetation");
    expect(slider.getAttribute("aria-valuetext")).toBe(
      `${VEGETATION_GAP.from}, No data on this date`
    );
    expect(screen.getByTestId("layer-time-slider-note-vegetation").textContent).toContain(
      "No data on this date."
    );
  });

  it("calls a thin day sparse rather than empty, because something was published on it", () => {
    useTimeSliderStore.setState({ layerDates: { vegetation: VEGETATION_THIN.from } });
    renderWithProviders(<LayerTimeSlider layerId="vegetation" />);

    expect(
      screen.getByTestId("layer-time-slider-range-vegetation").getAttribute("aria-valuetext")
    ).toBe(`${VEGETATION_THIN.from}, Fewer readings than usual on this date`);
  });

  it("says nothing about coverage on an ordinary dense day", () => {
    useTimeSliderStore.setState({ layerDates: { vegetation: "2019-02-20" } });
    renderWithProviders(<LayerTimeSlider layerId="vegetation" />);

    // Symmetry with the drawing: a dense day carries no hatch, so it earns no commentary either.
    expect(
      screen.getByTestId("layer-time-slider-range-vegetation").getAttribute("aria-valuetext")
    ).toBe("2019-02-20");
    expect(screen.getByTestId("layer-time-slider-note-vegetation").textContent).not.toContain(
      "No data"
    );
  });

  it("calls the padding right of today beyond the record rather than a hole in it", () => {
    useTimeSliderStore.setState({ layerDates: { vegetation: LAST_DAY } });
    renderWithProviders(<LayerTimeSlider layerId="vegetation" />);

    expect(
      screen.getByTestId("layer-time-slider-range-vegetation").getAttribute("aria-valuetext")
    ).toBe(`${LAST_DAY}, Beyond the record; nothing is published after today`);
  });

  /**
   * The PRECONDITION refusal, reached only by mounting this control the way `LayerRow` never
   * does. `LayerRow.test.tsx` owns what the row actually mounts; this owns what the control does
   * when its own gate has been violated, and the answer must not be a bare silent return.
   */
  it("names the axis it was mounted without instead of returning nothing", () => {
    renderWithProviders(<LayerTimeSlider layerId="interventions" />);

    expect(screen.queryByTestId("layer-time-slider-range-interventions")).toBeNull();
    const refusal = screen.getByTestId("layer-time-slider-no-axis-interventions");
    expect(refusal.textContent).toContain("snapshot");
    // The layer is still on the map as of some day, and a mixed-time composite is only readable
    // while every row admits its own.
    expect(refusal.textContent).toContain("2019-02-01");
  });

  /**
   * F7. Scrub away and back -- or type the latest day in -- and the store holds an explicit
   * override that merely EQUALS the latest day. Comparing the two values disabled the only
   * control that deletes that override, so the layer stayed pinned and stopped following its own
   * live edge from the next payload on, with no way left to resume.
   */
  it("still offers the way back when a layer is pinned to the very day that is its latest", () => {
    renderWithProviders(<LayerTimeSlider layerId="vegetation" />);
    const field = screen.getByTestId(
      "layer-time-slider-date-input-vegetation"
    ) as HTMLInputElement;
    const latestButton = screen.getByTestId(
      "layer-time-slider-latest-button-vegetation"
    ) as HTMLButtonElement;

    // Following its own live edge: nothing to undo, so nothing to offer.
    expect(useTimeSliderStore.getState().layerDates.vegetation).toBeUndefined();
    expect(latestButton.disabled).toBe(true);

    // Away, then back onto the same day by hand. The day looks identical; the STATE does not.
    fireEvent.change(field, { target: { value: "2019-02-14" } });
    fireEvent.change(field, { target: { value: VEGETATION_LATEST_DATE } });
    expect(useTimeSliderStore.getState().layerDates.vegetation).toBe(VEGETATION_LATEST_DATE);
    expect(latestButton.disabled).toBe(false);

    fireEvent.click(latestButton);

    // The override is gone, so the row follows its own newest day again as later payloads land.
    expect(useTimeSliderStore.getState().layerDates.vegetation).toBeUndefined();
    expect(latestButton.disabled).toBe(true);
  });

  it("offers the way back even to a layer whose latest published day is unknown", () => {
    useTimeSliderStore.setState({ layerDates: { sensors: "2019-02-14" } });
    renderWithProviders(<LayerTimeSlider layerId="sensors" />);

    // Nothing can name a jump target here, but the pin is still real and still removable, and
    // the title says which of the two the button is doing.
    const latestButton = screen.getByTestId(
      "layer-time-slider-latest-button-sensors"
    ) as HTMLButtonElement;
    expect(latestButton.disabled).toBe(false);
    expect(latestButton.title).toContain("not known");

    fireEvent.click(latestButton);
    expect(useTimeSliderStore.getState().layerDates.sensors).toBeUndefined();
  });

  /**
   * F9. `step={1}` is one day a press, and vegetation's real axis is roughly 1,460 of them: a
   * keyboard-only reader had no way across the record at all. The span is this component's, not
   * the engine's undocumented fraction of the range, so it is the same promise on every row --
   * and the press is claimed completely, or the browser's own Page step would land on top of it.
   */
  it("crosses the axis a month at a time, and a year with Shift, without double-stepping", () => {
    useTimeSliderStore.setState({ layerDates: { vegetation: "2019-02-14" } });
    renderWithProviders(<LayerTimeSlider layerId="vegetation" />);
    const slider = screen.getByTestId("layer-time-slider-range-vegetation");

    const pageDown = createEvent.keyDown(slider, { key: "PageDown" });
    fireEvent(slider, pageDown);
    expect(pageDown.defaultPrevented).toBe(true);
    expect(useTimeSliderStore.getState().layerDates.vegetation).toBe(addDays("2019-02-14", -30));

    const pageUp = createEvent.keyDown(slider, { key: "PageUp" });
    fireEvent(slider, pageUp);
    expect(useTimeSliderStore.getState().layerDates.vegetation).toBe("2019-02-14");

    // A year at a time, clamped to the layer's own axis rather than running off the start of it.
    const shiftPageDown = createEvent.keyDown(slider, { key: "PageDown", shiftKey: true });
    fireEvent(slider, shiftPageDown);
    expect(useTimeSliderStore.getState().layerDates.vegetation).toBe(FIRST_DAY);
  });

  it("leaves every key it does not claim to the native range", () => {
    renderWithProviders(<LayerTimeSlider layerId="vegetation" />);
    const slider = screen.getByTestId("layer-time-slider-range-vegetation");

    for (const key of ["ArrowRight", "ArrowLeft", "Home", "End"]) {
      const event = createEvent.keyDown(slider, { key });
      fireEvent(slider, event);
      expect(event.defaultPrevented, key).toBe(false);
    }
    // None of them wrote a day: the native range moves its own value, and this component only
    // hears about it through `change`.
    expect(useTimeSliderStore.getState().layerDates.vegetation).toBeUndefined();
  });

  /**
   * F9. The bands are `aria-hidden`, and `aria-valuetext` can only ever report the ONE day the
   * thumb is on -- so without this a screen-reader user could learn where a three-day hole is
   * only by landing on each of 1,460 days in turn.
   */
  it("describes the whole record's shape to a reader who cannot see the bands", () => {
    renderWithProviders(<LayerTimeSlider layerId="vegetation" />);
    const slider = screen.getByTestId("layer-time-slider-range-vegetation");

    const summaryId = (slider.getAttribute("aria-describedby") ?? "").split(" ")[0];
    const summary = document.getElementById(summaryId);
    expect(summary).not.toBeNull();

    const spoken = summary?.textContent ?? "";
    expect(spoken).toContain(`Coverage from ${FIRST_DAY} to ${SERVER_CURRENT_DATE}`);
    expect(spoken).toContain(`1 gap with no data: ${VEGETATION_GAP.from} to ${VEGETATION_GAP.to}`);
    expect(spoken).toContain(
      `1 sparse range: ${VEGETATION_THIN.from} to ${VEGETATION_THIN.to}`
    );
    // The two coarse keys, named where the reader who needs them will hear them.
    expect(spoken).toContain("Page Up and Page Down");
  });

  it("adds the note about the current day to the slider's description, not just the page", () => {
    useTimeSliderStore.setState({ layerDates: { vegetation: VEGETATION_GAP.from } });
    renderWithProviders(<LayerTimeSlider layerId="vegetation" />);

    const describedBy = (
      screen
        .getByTestId("layer-time-slider-range-vegetation")
        .getAttribute("aria-describedby") ?? ""
    ).split(" ");
    expect(describedBy).toHaveLength(2);
    // The paragraph a sighted reader sees under the track, reachable from the control that
    // caused it rather than sitting unlinked beside it.
    expect(document.getElementById(describedBy[1])).toBe(
      screen.getByTestId("layer-time-slider-note-vegetation")
    );
  });

  /**
   * F5, at the surface it reaches a reader. Below `describedFromDay` the gap list was truncated,
   * so "not in coverageGaps" is not evidence of coverage -- and reporting the bare date, as a
   * dense day does, would let the day read as an ordinary published one.
   */
  it("says the coverage is unknown on a day the truncated record does not reach", () => {
    useTimeSliderStore.setState({ layerDates: { weather: "2019-01-15" } });
    renderWithProviders(<LayerTimeSlider layerId="weather" />);

    expect(
      screen.getByTestId("layer-time-slider-range-weather").getAttribute("aria-valuetext")
    ).toBe("2019-01-15, Coverage on this date is unknown; the record's gap list does not reach this far back");
    // And the same fact is drawn, in a band of its own rather than as solid coverage.
    const undescribed = screen.queryAllByTestId("layer-time-slider-band-weather-undescribed");
    expect(undescribed).toHaveLength(1);
    expect(undescribed[0].dataset.from).toBe(FIRST_DAY);
    expect(undescribed[0].dataset.to).toBe(addDays(WEATHER_DESCRIBED_FROM_DAY, -1));
  });

  it("tells a reader where its record stops being described at all", () => {
    renderWithProviders(<LayerTimeSlider layerId="weather" />);

    const slider = screen.getByTestId("layer-time-slider-range-weather");
    const summaryId = (slider.getAttribute("aria-describedby") ?? "").split(" ")[0];
    expect(document.getElementById(summaryId)?.textContent).toContain(
      `Not described before ${WEATHER_DESCRIBED_FROM_DAY}`
    );
  });

  describe("coverage geometry", () => {
    it("partitions every covered day into exactly one coverage state", () => {
      const segments = buildCoverageSegments(VEGETATION_DOMAIN, vegetationCapability());
      const coveredDayCount = dayOffset(FIRST_DAY, SERVER_CURRENT_DATE) + 1;

      expect(segments[0].startOffset).toBe(0);
      expect(segments[segments.length - 1].endOffsetExclusive).toBe(coveredDayCount);
      segments.forEach((segment, index) => {
        if (index === 0) return;
        expect(segment.startOffset).toBe(segments[index - 1].endOffsetExclusive);
        expect(segment.kind).not.toBe(segments[index - 1].kind);
      });

      expect(segments.map((segment) => segment.kind)).toEqual([
        "dense",
        "absent",
        "dense",
        "thin",
        "dense",
      ]);
    });

    it("stops claiming coverage at the server's today, never across the drawn padding", () => {
      const segments = buildCoverageSegments(VEGETATION_DOMAIN, vegetationCapability());
      const lastOffset = segments[segments.length - 1].endOffsetExclusive - 1;

      // The axis runs to LAST_DAY, but coverageGaps runs only to serverCurrentDate; painting the
      // padding would legend days that have not happened as a hole in a record.
      expect(addDays(FIRST_DAY, lastOffset)).toBe(SERVER_CURRENT_DATE);
    });

    it("resolves a day claimed both thin and absent to thin, so no published day reads as a hole", () => {
      const contradictory = vegetationCapability({
        coverageGaps: [{ from: "2019-02-10", to: "2019-02-12" }],
        thinRanges: [{ from: "2019-02-10", to: "2019-02-12" }],
      });

      expect(dayCoverageState(VEGETATION_DOMAIN, contradictory, "2019-02-11")).toBe("thin");
      expect(
        drawCoverageBands(VEGETATION_DOMAIN, contradictory).map((band) => band.kind)
      ).toEqual(["thin"]);
    });

    it("keeps a governed upstream absence distinct from an ingest gap", () => {
      const governed = vegetationCapability({
        coverageGaps: [],
        governedAbsenceRanges: [{ from: "2019-02-10", to: "2019-02-12" }],
        thinRanges: [],
      });

      expect(dayCoverageState(VEGETATION_DOMAIN, governed, "2019-02-11")).toBe(
        "governed_absence"
      );
      const bands = drawCoverageBands(VEGETATION_DOMAIN, governed);
      expect(bands.map((band) => band.kind)).toEqual(["governed_absence"]);
      expect(describeCoverageBand(bands[0])).toBe(
        "Governed absence: 2019-02-10 to 2019-02-12"
      );
      expect(describeDayCoverage("governed_absence")).toContain("source was checked");
      expect(describeCoverageTopology(VEGETATION_DOMAIN, governed)).toContain(
        "1 governed-absence range: 2019-02-10 to 2019-02-12"
      );
    });

    it("agrees day for day between the painted partition and the single-day lookup", () => {
      const layer = vegetationCapability();
      const segments = buildCoverageSegments(VEGETATION_DOMAIN, layer);

      for (const segment of segments) {
        for (let offset = segment.startOffset; offset < segment.endOffsetExclusive; offset += 1) {
          expect(dayCoverageState(VEGETATION_DOMAIN, layer, addDays(FIRST_DAY, offset))).toBe(
            segment.kind
          );
        }
      }
    });

    it("keeps a one-day hole visible on a four-year axis instead of rounding it away", () => {
      const longDomain = { firstDay: "2015-03-07", today: SERVER_CURRENT_DATE, lastDay: LAST_DAY };
      const bands = drawCoverageBands(
        longDomain,
        vegetationCapability({
          earliestObservedDate: longDomain.firstDay,
          coverageGaps: [{ from: "2017-06-14", to: "2017-06-14" }],
          thinRanges: [],
        })
      );

      // One day of 1462 is 0.07% -- a sixth of a pixel in the manager column. Unfloored, the
      // track would draw solid coverage over a hole.
      expect(bands).toHaveLength(1);
      expect(bands[0].widthPercent).toBe(MINIMUM_DRAWN_BAND_PERCENT);
      expect(bands[0].fromDate).toBe("2017-06-14");
      expect(bands[0].toDate).toBe("2017-06-14");
      expect(bands[0].rangeCount).toBe(1);
    });

    it("coalesces holes too close to draw apart, and counts them rather than merging their claim", () => {
      const longDomain = { firstDay: "2015-03-07", today: SERVER_CURRENT_DATE, lastDay: LAST_DAY };
      const bands = drawCoverageBands(
        longDomain,
        vegetationCapability({
          earliestObservedDate: longDomain.firstDay,
          coverageGaps: [
            { from: "2017-06-14", to: "2017-06-14" },
            { from: "2017-06-16", to: "2017-06-16" },
            { from: "2017-06-18", to: "2017-06-18" },
          ],
          thinRanges: [],
        })
      );

      // One element, because three sub-pixel bands floored to a visible width overlap. The count
      // is kept so the description says "3 ranges between" rather than claiming five dead days.
      expect(bands).toHaveLength(1);
      expect(bands[0].rangeCount).toBe(3);
      expect(bands[0].fromDate).toBe("2017-06-14");
      expect(bands[0].toDate).toBe("2017-06-18");
    });

    it("paints nothing for a range that runs backwards, off the axis, or past today", () => {
      const layer = vegetationCapability({
        coverageGaps: [
          { from: "2019-01-20", to: "2019-01-18" },
          { from: "2018-05-01", to: "2018-05-09" },
          { from: "2019-06-01", to: "2019-06-09" },
        ],
        thinRanges: [],
      });

      expect(drawCoverageBands(VEGETATION_DOMAIN, layer)).toEqual([]);
    });

    it("clips a range that starts before the axis to the part of it the axis covers", () => {
      const layer = vegetationCapability({
        coverageGaps: [{ from: "2018-12-25", to: "2019-01-03" }],
        thinRanges: [],
      });
      const bands = drawCoverageBands(VEGETATION_DOMAIN, layer);

      expect(bands).toHaveLength(1);
      expect(bands[0].fromDate).toBe(FIRST_DAY);
      expect(bands[0].toDate).toBe("2019-01-03");
    });

    it("says nothing about a day outside the layer's own axis rather than calling it empty", () => {
      const layer = vegetationCapability();

      expect(dayCoverageState(VEGETATION_DOMAIN, layer, "2018-12-31")).toBe("off_axis");
      expect(dayCoverageState(VEGETATION_DOMAIN, layer, "not-a-date")).toBe("off_axis");
      expect(dayCoverageState(VEGETATION_DOMAIN, layer, addDays(LAST_DAY, 1))).toBe("off_axis");
    });

    it("survives a payload from a server that predates the coverage fields", () => {
      const olderPayload = {
        ...vegetationCapability(),
        coverageGaps: undefined,
        thinRanges: undefined,
      } as unknown as SliderLayerCapability;

      expect(drawCoverageBands(VEGETATION_DOMAIN, olderPayload)).toEqual([]);
      expect(dayCoverageState(VEGETATION_DOMAIN, olderPayload, "2019-01-11")).toBe("dense");
    });

    /**
     * F5. The cap drops the oldest ranges and the server says where the surviving ones start;
     * below that the lists are silent about days nobody claims either way. Filling the array
     * "dense" made that silence draw as solid coverage AND report as `dense`, so
     * `describeDayCoverage` returned null and the day read as an ordinary published one.
     */
    it("refuses to call a day dense when the record's lists never reached it", () => {
      const truncated = vegetationCapability({
        coverageGaps: [],
        thinRanges: [],
        describedFromDay: "2019-02-10",
      });

      expect(dayCoverageState(VEGETATION_DOMAIN, truncated, "2019-02-09")).toBe("undescribed");
      expect(dayCoverageState(VEGETATION_DOMAIN, truncated, "2019-02-10")).toBe("dense");
      // And it is SAID, where a dense day deliberately says nothing at all.
      expect(describeDayCoverage("undescribed")).toContain("unknown");
      expect(describeDayCoverage("dense")).toBeNull();
    });

    it("draws the undescribed span as its own band rather than as coverage", () => {
      const truncated = vegetationCapability({
        coverageGaps: [],
        thinRanges: [],
        describedFromDay: "2019-02-10",
      });
      const bands = drawCoverageBands(VEGETATION_DOMAIN, truncated);

      expect(bands.map((band) => band.kind)).toEqual(["undescribed"]);
      expect(bands[0].fromDate).toBe(FIRST_DAY);
      expect(bands[0].toDate).toBe("2019-02-09");
      // Named as unknown in its own tooltip too -- not as a hole, which would be a claim the
      // truncated list cannot support in either direction.
      expect(describeCoverageBand(bands[0])).toBe("Coverage unknown: 2019-01-01 to 2019-02-09");
    });

    /**
     * A range the server DID report below the boundary is a positive claim and outranks "we were
     * not told" -- the boundary bounds what silence means, not what a report means.
     */
    it("keeps a reported gap below the boundary as a gap, not as unknown coverage", () => {
      const truncated = vegetationCapability({
        coverageGaps: [{ from: "2019-01-20", to: "2019-01-22" }],
        thinRanges: [],
        describedFromDay: "2019-02-10",
      });

      expect(dayCoverageState(VEGETATION_DOMAIN, truncated, "2019-01-21")).toBe("absent");
      expect(dayCoverageState(VEGETATION_DOMAIN, truncated, "2019-01-19")).toBe("undescribed");
      expect(drawCoverageBands(VEGETATION_DOMAIN, truncated).map((band) => band.kind)).toEqual([
        "undescribed",
        "absent",
        "undescribed",
      ]);
    });

    it("agrees day for day with the single-day lookup once a boundary is in play", () => {
      const truncated = vegetationCapability({ describedFromDay: "2019-02-10" });
      const segments = buildCoverageSegments(VEGETATION_DOMAIN, truncated);

      for (const segment of segments) {
        for (let offset = segment.startOffset; offset < segment.endOffsetExclusive; offset += 1) {
          expect(
            dayCoverageState(VEGETATION_DOMAIN, truncated, addDays(FIRST_DAY, offset))
          ).toBe(segment.kind);
        }
      }
    });

    it("treats a payload carrying no boundary as describing the whole axis", () => {
      const olderPayload = {
        ...vegetationCapability(),
        describedFromDay: undefined,
      } as unknown as SliderLayerCapability;

      expect(dayCoverageState(VEGETATION_DOMAIN, olderPayload, FIRST_DAY)).toBe("dense");
      expect(
        drawCoverageBands(VEGETATION_DOMAIN, olderPayload).map((band) => band.kind)
      ).toEqual(["absent", "thin"]);
    });

    it("speaks the same runs the track paints, and counts the rest rather than reciting them", () => {
      const many = vegetationCapability({
        coverageGaps: [
          { from: "2019-01-10", to: "2019-01-12" },
          { from: "2019-01-20", to: "2019-01-20" },
          { from: "2019-02-05", to: "2019-02-06" },
          { from: "2019-02-15", to: "2019-02-16" },
          { from: "2019-02-25", to: "2019-02-26" },
        ],
        thinRanges: [],
      });
      const spoken = describeCoverageTopology(VEGETATION_DOMAIN, many);

      expect(spoken).toContain("5 gaps with no data");
      // A one-day run is named once rather than as a range from a day to itself.
      expect(spoken).toContain("2019-01-10 to 2019-01-12; 2019-01-20; 2019-02-05 to 2019-02-06");
      expect(spoken).toContain(`and ${5 - MAX_SPOKEN_COVERAGE_RUNS} more`);
      expect(spoken).not.toContain("2019-02-25");
    });

    it("says a record with nothing to flag has nothing to flag, rather than saying nothing", () => {
      const clean = vegetationCapability({ coverageGaps: [], thinRanges: [] });

      expect(describeCoverageTopology(VEGETATION_DOMAIN, clean)).toBe(
        `Coverage from ${FIRST_DAY} to ${SERVER_CURRENT_DATE}. No gaps and no sparse days.`
      );
    });

    it("encodes each track region with a hatch angle of its own, so texture alone tells them apart", () => {
      const hatchAngle = (
        region: "thin" | "absent" | "future" | "undescribed"
      ): string | undefined =>
        /(\d+)deg/.exec(TRACK_REGION_APPEARANCE[region].backgroundImage ?? "")?.[1];

      expect([
        hatchAngle("thin"),
        hatchAngle("absent"),
        hatchAngle("future"),
        hatchAngle("undescribed"),
      ]).toEqual(["90", "135", "45", "0"]);
      // The undescribed region asserts less than any other and must not shout louder than the
      // ones that assert something, so it is the faintest fill on the track.
      expect(TRACK_REGION_APPEARANCE.undescribed.backgroundColor).toContain("--muted-foreground");
      expect(TRACK_REGION_APPEARANCE.undescribed.backgroundColor).not.toBe(
        TRACK_REGION_APPEARANCE.absent.backgroundColor
      );
      // Dense is the absence of texture: the ordinary case, and the only one needing no key.
      expect(TRACK_REGION_APPEARANCE.dense.backgroundImage).toBeUndefined();
      // A thin day is an observation, so it keeps the layer's own hue; both "nothing published"
      // regions take the neutral one and differ from each other by their mirrored hatch alone.
      expect(TRACK_REGION_APPEARANCE.thin.backgroundColor).toContain("--primary");
      expect(TRACK_REGION_APPEARANCE.absent.backgroundColor).toContain("--muted-foreground");
      expect(TRACK_REGION_APPEARANCE.future.backgroundColor).toContain("--muted-foreground");
      expect(TRACK_REGION_APPEARANCE.absent.backgroundImage).not.toBe(
        TRACK_REGION_APPEARANCE.future.backgroundImage
      );
      expect(TRACK_REGION_APPEARANCE.governed_absence.backgroundImage).not.toBe(
        TRACK_REGION_APPEARANCE.absent.backgroundImage
      );
    });
  });

  describe("synced-days geometry", () => {
    it("draws one band per contiguous run of synced days", () => {
      const bands = drawSyncedDayBands(
        VEGETATION_DOMAIN,
        new Set(["2019-01-05", "2019-01-06", "2019-01-07"])
      );

      expect(bands).toHaveLength(1);
      expect(bands[0].kind).toBe("synced");
      expect(bands[0].fromDate).toBe("2019-01-05");
      expect(bands[0].toDate).toBe("2019-01-07");
    });

    it("draws nothing for an empty synced set", () => {
      expect(drawSyncedDayBands(VEGETATION_DOMAIN, new Set())).toEqual([]);
      expect(buildSyncedDayRuns(VEGETATION_DOMAIN, new Set())).toEqual([]);
    });

    it("keeps two runs separate when they are far enough apart not to floor together", () => {
      const bands = drawSyncedDayBands(VEGETATION_DOMAIN, new Set(["2019-01-05", "2019-03-01"]));
      expect(bands).toHaveLength(2);
    });

    it("keeps a single synced day visible on a long axis instead of rounding it away", () => {
      const longDomain = { firstDay: "2015-03-07", today: SERVER_CURRENT_DATE, lastDay: LAST_DAY };
      const bands = drawSyncedDayBands(longDomain, new Set(["2017-06-14"]));

      expect(bands).toHaveLength(1);
      expect(bands[0].widthPercent).toBe(MINIMUM_DRAWN_BAND_PERCENT);
    });

    it("sweeps the whole drawn axis, not just through today, so a synced forecast day counts", () => {
      // LAST_DAY sits two days past SERVER_CURRENT_DATE (the axis's future padding).
      // buildCoverageSegments stops at today; a synced-days run must not inherit that bound.
      const runs = buildSyncedDayRuns(VEGETATION_DOMAIN, new Set([LAST_DAY]));
      expect(runs).toHaveLength(1);
      expect(runs[0].startOffset).toBe(dayOffset(FIRST_DAY, LAST_DAY));
    });

    it("shares its geometry with the coverage track, registering pixel-exactly", () => {
      const coverageBands = drawCoverageBands(VEGETATION_DOMAIN, vegetationCapability());
      const gapBand = coverageBands.find((band) => band.kind === "absent");
      expect(gapBand).toBeDefined();

      const syncedBands = drawSyncedDayBands(
        VEGETATION_DOMAIN,
        new Set([VEGETATION_GAP.from, "2019-01-11", VEGETATION_GAP.to])
      );

      expect(syncedBands[0].leftPercent).toBe(gapBand!.leftPercent);
      expect(syncedBands[0].widthPercent).toBe(gapBand!.widthPercent);
    });

    it("describes a synced band in words for its tooltip", () => {
      const bands = drawSyncedDayBands(VEGETATION_DOMAIN, new Set(["2019-01-05", "2019-01-06"]));
      expect(describeSyncedDayBand(bands[0])).toBe("Saved on this device: 2019-01-05 to 2019-01-06");
    });

    it("counts coalesced runs rather than naming them individually, like the coverage track does", () => {
      // Same three dates the coverage track's own "coalesces holes too close to draw apart" case
      // uses, on the same long axis where a single day floors below MINIMUM_DRAWN_BAND_PERCENT:
      // proven there to merge, so a mismatch here would mean the shared floor/merge core diverged
      // between the two callers.
      const longDomain = { firstDay: "2015-03-07", today: SERVER_CURRENT_DATE, lastDay: LAST_DAY };
      const bands = drawSyncedDayBands(
        longDomain,
        new Set(["2017-06-14", "2017-06-16", "2017-06-18"])
      );

      expect(bands).toHaveLength(1);
      expect(bands[0].rangeCount).toBe(3);
      expect(bands[0].fromDate).toBe("2017-06-14");
      expect(bands[0].toDate).toBe("2017-06-18");
      expect(describeSyncedDayBand(bands[0])).toBe(
        "Saved on this device: 3 ranges between 2017-06-14 and 2017-06-18"
      );
    });

    /**
     * Locks in the fix for a reused-fill defect: the first version painted synced runs in the
     * exact same solid `TRACK_REGION_APPEARANCE.dense.backgroundColor` the coverage track's own
     * dense base uses a few pixels below it, which read as one bar with a fainter echo rather
     * than two separate claims. Asserted against the exported constants directly (no rendering,
     * no dependence on how a test environment's CSSOM round-trips `hsl(var(...))`/gradient
     * syntax) -- the same style the existing "encodes each track region with a hatch angle of
     * its own" case already uses for `TRACK_REGION_APPEARANCE`.
     */
    it("gives the synced-days line its own fill, never a token TRACK_REGION_APPEARANCE already uses", () => {
      expect(SYNCED_DAY_APPEARANCE.backgroundImage).toContain("radial-gradient");
      expect(SYNCED_DAY_APPEARANCE.backgroundColor).not.toBe(
        TRACK_REGION_APPEARANCE.dense.backgroundColor
      );
      for (const region of Object.values(TRACK_REGION_APPEARANCE)) {
        expect(SYNCED_DAY_APPEARANCE.backgroundColor).not.toBe(region.backgroundColor);
        if (region.backgroundImage === undefined) continue;
        // Every coverage region's image is a line hatch; the synced line's is a dot pattern --
        // a third vocabulary, not a fifth angle.
        expect(SYNCED_DAY_APPEARANCE.backgroundImage).not.toBe(region.backgroundImage);
      }
    });
  });

  describe("the synced-days line", () => {
    it("registers pixel-exactly above the coverage track for the same days", () => {
      mockUseSyncedDays.mockReturnValue(new Set([VEGETATION_GAP.from, "2019-01-11", VEGETATION_GAP.to]));
      renderWithProviders(<LayerTimeSlider layerId="vegetation" />);

      const coverageBand = screen.getByTestId("layer-time-slider-band-vegetation-absent");
      const syncedBand = screen.getByTestId("layer-time-slider-synced-band-vegetation");
      expect(syncedBand.style.left).toBe(coverageBand.style.left);
      expect(syncedBand.style.width).toBe(coverageBand.style.width);
    });

    /**
     * The hydrated-but-empty state renders a visually DISTINCT baseline (no hatch, no dots) --
     * a sighted reader can already tell it apart from "not yet known" and from "holding days".
     * An aural reader must be able to tell the SAME three states apart, which means this state
     * cannot be silent the way a dense coverage day is: dense has no visual mark of its own to
     * be silent about, where this state's plain baseline is itself a real, distinct render.
     */
    it("states nothing is saved yet, in words, once the index is known to be empty", () => {
      renderWithProviders(<LayerTimeSlider layerId="water" />);

      expect(screen.queryAllByTestId("layer-time-slider-synced-band-water")).toHaveLength(0);
      expect(screen.queryByTestId("layer-time-slider-synced-unknown-water")).toBeNull();
      expect(screen.getByTestId("layer-time-slider-note-water").textContent).toContain(
        "Nothing saved on this device yet."
      );
    });

    it("draws the not-yet-known placeholder distinctly from an empty track, and says so", () => {
      mockUseSyncIndexReady.mockReturnValue(false);
      renderWithProviders(<LayerTimeSlider layerId="water" />);

      expect(screen.getByTestId("layer-time-slider-synced-unknown-water")).not.toBeNull();
      expect(screen.queryAllByTestId("layer-time-slider-synced-band-water")).toHaveLength(0);
      expect(screen.getByTestId("layer-time-slider-note-water").textContent).toContain(
        "Checking what's saved"
      );
    });

    /**
     * A live write racing hydration: the index already has entries for this layer, but
     * `useSyncIndexReady` has not flipped yet. The track must still draw as "not yet known" --
     * this pins the branch reading `syncIndexReady` first, not `syncedDayBands.length`, since a
     * future reorder could pass a naive "renders hatch when nothing is synced" test while
     * failing this one.
     */
    it("draws the not-yet-known placeholder even when the index already reports days", () => {
      mockUseSyncIndexReady.mockReturnValue(false);
      mockUseSyncedDays.mockReturnValue(new Set(["2019-01-01"]));
      renderWithProviders(<LayerTimeSlider layerId="water" />);

      expect(screen.getByTestId("layer-time-slider-synced-unknown-water")).not.toBeNull();
      expect(screen.queryAllByTestId("layer-time-slider-synced-band-water")).toHaveLength(0);
    });

    it("states the held count and that the current day is one of them, in words", () => {
      mockUseSyncedDays.mockReturnValue(new Set([VEGETATION_LATEST_DATE, "2019-01-05"]));
      renderWithProviders(<LayerTimeSlider layerId="vegetation" />);

      const note = screen.getByTestId("layer-time-slider-note-vegetation").textContent ?? "";
      expect(note).toContain("2 days saved on this device");
      expect(note).not.toContain("this day is not");
    });

    it("says the current day is not held when it sits outside the synced set", () => {
      mockUseSyncedDays.mockReturnValue(new Set(["2019-01-05"]));
      renderWithProviders(<LayerTimeSlider layerId="vegetation" />);

      expect(screen.getByTestId("layer-time-slider-note-vegetation").textContent).toContain(
        "this day is not one of them"
      );
    });

    it("marks the selected day on its own row at the same offset the main track uses", () => {
      renderWithProviders(<LayerTimeSlider layerId="vegetation" />);

      const tick = screen.getByTestId("layer-time-slider-synced-selected-tick-vegetation");
      const expectedPercent = percentOfDayOffset(
        VEGETATION_DOMAIN,
        dayOffset(FIRST_DAY, VEGETATION_LATEST_DATE)
      );
      expect(tick.style.left).toBe(`${expectedPercent}%`);
    });
  });

  describe("the pending fetch signal", () => {
    it("marks the range input pending and the track busy when told the fetch is in flight", () => {
      renderWithProviders(<LayerTimeSlider layerId="vegetation" isFetchingCurrentDay />);

      expect(screen.getByTestId("layer-time-slider-range-vegetation").className).toContain(
        "is-pending"
      );
      expect(
        screen.getByTestId("layer-time-slider-track-vegetation").getAttribute("aria-busy")
      ).toBe("true");
    });

    it("leaves the range input and track unmarked when nothing is pending", () => {
      renderWithProviders(<LayerTimeSlider layerId="vegetation" />);

      expect(screen.getByTestId("layer-time-slider-range-vegetation").className).not.toContain(
        "is-pending"
      );
      expect(
        screen.getByTestId("layer-time-slider-track-vegetation").getAttribute("aria-busy")
      ).toBeNull();
    });

    it("says so in words, never claiming the retained frame is a specific previous DAY", () => {
      renderWithProviders(<LayerTimeSlider layerId="vegetation" isFetchingCurrentDay />);

      const note = screen.getByTestId("layer-time-slider-note-vegetation").textContent ?? "";
      expect(note).toContain("moment behind");
      expect(note).not.toContain("previous day");
    });

    it("orders the pending note ahead of the coverage note", () => {
      useTimeSliderStore.setState({ layerDates: { vegetation: VEGETATION_GAP.from } });
      renderWithProviders(<LayerTimeSlider layerId="vegetation" isFetchingCurrentDay />);

      const note = screen.getByTestId("layer-time-slider-note-vegetation").textContent ?? "";
      const pendingIndex = note.indexOf("moment behind");
      const coverageIndex = note.indexOf("No data on this date");
      expect(pendingIndex).toBeGreaterThanOrEqual(0);
      expect(coverageIndex).toBeGreaterThan(pendingIndex);
    });
  });
});
