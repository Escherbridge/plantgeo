import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import TimeSlider from "@/components/map/TimeSlider";
import { addDays, dayOffset, useTimeSliderStore } from "@/stores/time-slider-store";
import { useMetricAtDate, type MetricAtDateFetcher } from "@/stores/useMetricAtDate";
import type {
  MetricAtDateCollection,
  MetricAtDateInput,
  SliderCapabilities,
} from "@/types/time-slider";

/**
 * Deliberately nowhere near the machine's own date: a stray `new Date()` deciding "today"
 * flips every past/future assertion below.
 */
const SERVER_CURRENT_DATE = "2019-03-07";
const FIRST_DAY = "2019-01-01";
/** Longest horizon in the fixture is weather-observations' 14 days. */
const LAST_DAY = "2019-03-21";

/** All eight geo.layers names that exist in production. Nothing invented. */
const CAPABILITIES: SliderCapabilities = {
  serverCurrentDate: SERVER_CURRENT_DATE,
  // 0 so every domain assertion below still measures the FORECAST horizon alone; the
  // future-axis span has its own tests rather than shifting these.
  futureAxisDays: 0,
  layers: [
    {
      layerName: "vegetation",
      temporalKind: "daily_series",
      forecastHorizonDays: 10,
      forecastVariants: ["monte_carlo"],
      earliestObservedDate: FIRST_DAY,
    },
    {
      layerName: "weather-observations",
      temporalKind: "daily_series",
      forecastHorizonDays: 14,
      forecastVariants: ["monte_carlo", "ml"],
      earliestObservedDate: FIRST_DAY,
    },
    {
      layerName: "water-gauges",
      temporalKind: "daily_series",
      forecastHorizonDays: 5,
      forecastVariants: ["monte_carlo"],
      earliestObservedDate: FIRST_DAY,
    },
    {
      layerName: "fire-detections",
      temporalKind: "event",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: FIRST_DAY,
    },
    {
      layerName: "fire-perimeters",
      temporalKind: "event",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: "2019-01-05",
    },
    {
      layerName: "interventions",
      temporalKind: "snapshot",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: "2019-02-01",
    },
    {
      layerName: "evacuation-zones",
      temporalKind: "snapshot",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: "2019-02-01",
    },
    {
      // Published on exactly one day, so a scrub's ±7-day prefetch neighbourhood is empty
      // and the fetcher call count measures the debounce alone.
      layerName: "sensors",
      temporalKind: "snapshot",
      forecastHorizonDays: 0,
      forecastVariants: [],
      earliestObservedDate: SERVER_CURRENT_DATE,
    },
  ],
};

/** A date inside the domain that no probe layer has observed yet. */
const BEFORE_ANY_PROBE_DATA = "2019-01-15";

function emptyPublishedCollection(): MetricAtDateCollection {
  return { type: "FeatureCollection", features: [], availability: "published", reason: null };
}

/** Surfaces what `useMetricAtDate` resolved, so the debounce is observable from the DOM. */
function MetricProbe({
  layerName,
  fetchMetricAtDate,
}: {
  layerName: string;
  fetchMetricAtDate: MetricAtDateFetcher;
}) {
  const { collection, availability, resolvedDate } = useMetricAtDate({
    layerName,
    metric: "soil_moisture",
    fetchMetricAtDate,
  });
  return (
    <output>
      <span data-testid="probe-availability">{availability}</span>
      <span data-testid="probe-feature-count">{collection.features.length}</span>
      <span data-testid="probe-resolved-date">{resolvedDate}</span>
    </output>
  );
}

function variantButton(label: string): HTMLButtonElement {
  return screen.getByRole("radio", { name: label }) as HTMLButtonElement;
}

describe("TimeSlider", () => {
  beforeEach(() => {
    useTimeSliderStore.setState({
      selectedDate: BEFORE_ANY_PROBE_DATA,
      forecastVariant: "monte_carlo",
      capabilities: CAPABILITIES,
      // Explicit, because one test below drives the failure branch: leaving it to whatever the
      // previous test left behind would make the in-flight and failed states swap silently.
      capabilitiesUnavailable: false,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders nothing until capabilities have landed", () => {
    useTimeSliderStore.setState({ capabilities: null });
    const { container } = renderWithProviders(<TimeSlider />);
    expect(container.querySelector("[data-testid='time-slider']")).toBeNull();
  });

  it("spans the payload's domain in whole-day steps", () => {
    renderWithProviders(<TimeSlider />);
    const slider = screen.getByRole("slider") as HTMLInputElement;

    expect(slider.min).toBe("0");
    expect(slider.max).toBe(String(dayOffset(FIRST_DAY, LAST_DAY)));
    expect(slider.step).toBe("1");
    expect(slider.value).toBe(String(dayOffset(FIRST_DAY, BEFORE_ANY_PROBE_DATA)));
    expect(slider.getAttribute("aria-valuetext")).toBe(BEFORE_ANY_PROBE_DATA);
    expect(screen.getByTestId("time-slider-today-label").textContent).toContain(
      SERVER_CURRENT_DATE
    );
    expect(screen.getByTestId("time-slider-future-hatch")).not.toBeNull();
  });

  it("disables the variant toggle on and before the server's today, and enables it after", () => {
    renderWithProviders(<TimeSlider />);

    for (const pastOrToday of [FIRST_DAY, BEFORE_ANY_PROBE_DATA, "2019-03-06", SERVER_CURRENT_DATE]) {
      act(() => {
        useTimeSliderStore.getState().setSelectedDate(pastOrToday);
      });
      expect(variantButton("Monte Carlo").disabled).toBe(true);
      expect(variantButton("Monte Carlo").getAttribute("title")).toBe(
        "forecasts apply to future dates"
      );
      expect(screen.getByTestId("time-slider-variant-hint").textContent).toBe(
        "forecasts apply to future dates"
      );
    }

    // The day after the server's today, and only the server's today, unlocks the toggle.
    act(() => {
      useTimeSliderStore.getState().setSelectedDate("2019-03-08");
    });
    expect(variantButton("Monte Carlo").disabled).toBe(false);
  });

  it("renders ml as a visible option that is itself disabled", () => {
    renderWithProviders(<TimeSlider />);
    act(() => {
      useTimeSliderStore.getState().setSelectedDate("2019-03-10");
    });

    const machineLearning = variantButton("ML");
    expect(machineLearning.textContent).toBe("ML");
    expect(machineLearning.disabled).toBe(true);
    expect(machineLearning.getAttribute("title")).toBe("no trained model yet");
    // The date is in the future, so nothing but the missing model is holding ML back.
    expect(variantButton("Monte Carlo").disabled).toBe(false);
  });

  it("issues exactly one query for a multi-day scrub, after the debounce settles", async () => {
    vi.useFakeTimers();
    const requestedDates: string[] = [];
    const fetchMetricAtDate = vi.fn(
      async (input: MetricAtDateInput): Promise<MetricAtDateCollection> => {
        requestedDates.push(input.date);
        return emptyPublishedCollection();
      }
    );

    renderWithProviders(
      <>
        <TimeSlider />
        <MetricProbe layerName="sensors" fetchMetricAtDate={fetchMetricAtDate} />
      </>
    );

    const slider = screen.getByRole("slider");
    const todayIndex = dayOffset(FIRST_DAY, SERVER_CURRENT_DATE);

    for (const offset of [20, 35, 50, todayIndex]) {
      fireEvent.change(slider, { target: { value: String(offset) } });
      act(() => {
        vi.advanceTimersByTime(60);
      });
    }

    // The store moved with the pointer on every tick; the query has not moved at all.
    // The day is read off the date field, which is where it lives now that it is editable.
    expect((screen.getByTestId("time-slider-date-input") as HTMLInputElement).value).toBe(
      SERVER_CURRENT_DATE
    );
    expect(screen.getByTestId("probe-resolved-date").textContent).toBe(BEFORE_ANY_PROBE_DATA);
    expect(fetchMetricAtDate).toHaveBeenCalledTimes(0);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    expect(fetchMetricAtDate).toHaveBeenCalledTimes(1);
    expect(requestedDates).toEqual([SERVER_CURRENT_DATE]);
    expect(fetchMetricAtDate.mock.calls[0][0].variant).toBe("observed");
  });

  it("greys an event layer at a future date and fetches no data for it", () => {
    useTimeSliderStore.setState({ selectedDate: "2019-03-10" });
    const fetchMetricAtDate = vi.fn(
      async (): Promise<MetricAtDateCollection> => emptyPublishedCollection()
    );

    renderWithProviders(
      <>
        <TimeSlider />
        <MetricProbe layerName="fire-detections" fetchMetricAtDate={fetchMetricAtDate} />
      </>
    );

    const row = screen.getByTestId("layer-availability-fire-detections");
    expect(row.getAttribute("data-availability")).toBe("not_forecastable");
    expect(row.textContent).toContain("Fire detections are events; no forecast exists");
    expect(row.className).toContain("opacity-60");

    // Unavailable is not the same as hidden: the row stays, and nothing is drawn or fetched.
    expect(screen.getByTestId("probe-availability").textContent).toBe("not_forecastable");
    expect(screen.getByTestId("probe-feature-count").textContent).toBe("0");
    expect(fetchMetricAtDate).not.toHaveBeenCalled();
  });

  it("explains what a forecast band means only while the selection is in the future, via a disclosure that defaults open", () => {
    const { container } = renderWithProviders(<TimeSlider />);
    expect(container.querySelector("[data-testid='forecast-band-key']")).toBeNull();

    act(() => {
      useTimeSliderStore.getState().setSelectedDate("2019-03-10");
    });

    const heading = screen.getByTestId("forecast-band-key-heading");
    const bandKey = screen.getByTestId("forecast-band-key");

    // Defaults open: a user who has actively picked a forecast day isn't made to find an
    // extra disclosure just to read what the band means.
    expect(heading.getAttribute("aria-expanded")).toBe("true");
    expect(heading.getAttribute("aria-controls")).toBe(bandKey.getAttribute("id"));
    expect(bandKey.className).not.toContain("hidden");
    expect(bandKey.textContent).toContain("(high - low) normalised");
    expect(bandKey.textContent).toContain("no isolines");

    // Still a real, keyboard-operable disclosure -- it can be collapsed to reclaim vertical
    // space on a short viewport, without hiding the content from anyone who hasn't asked to
    // collapse it.
    fireEvent.click(heading);
    expect(heading.getAttribute("aria-expanded")).toBe("false");
    expect(bandKey.className).toContain("hidden");

    fireEvent.click(heading);
    expect(heading.getAttribute("aria-expanded")).toBe("true");
    expect(bandKey.className).not.toContain("hidden");
  });

  it("names a beyond-horizon layer's own horizon in its reason", () => {
    renderWithProviders(<TimeSlider />);
    // water-gauges forecasts 5 days; 2019-03-14 is a week past the server's today.
    act(() => {
      useTimeSliderStore.getState().setSelectedDate("2019-03-14");
    });

    const row = screen.getByTestId("layer-availability-water-gauges");
    expect(row.getAttribute("data-availability")).toBe("beyond_horizon");
    expect(row.textContent).toContain("No forecast beyond +5 days");
  });

  it("marks a layer that has no observations this far back", () => {
    renderWithProviders(<TimeSlider />);
    act(() => {
      useTimeSliderStore.getState().setSelectedDate(FIRST_DAY);
    });

    const row = screen.getByTestId("layer-availability-interventions");
    expect(row.getAttribute("data-availability")).toBe("not_yet_observed");
    expect(row.textContent).toContain("Not yet observed at this date");
  });

  it("keeps the per-layer warehouse record collapsed behind a real, keyboard-operable disclosure", () => {
    renderWithProviders(<TimeSlider />);

    const disclosure = screen.getByTestId("time-slider-record-heading");
    const list = screen.getByTestId("time-slider-record-list");

    // A real <button>, not a hover-only affordance -- role comes for free.
    expect(disclosure.tagName).toBe("BUTTON");
    expect(disclosure.getAttribute("aria-expanded")).toBe("false");
    expect(disclosure.getAttribute("aria-controls")).toBe(list.getAttribute("id"));
    expect(list.className).toContain("hidden");

    fireEvent.click(disclosure);

    expect(disclosure.getAttribute("aria-expanded")).toBe("true");
    expect(list.className).not.toContain("hidden");
    // The rows underneath are unaffected by the disclosure -- same testids, same content.
    expect(screen.getByTestId("layer-availability-vegetation")).not.toBeNull();

    fireEvent.click(disclosure);
    expect(disclosure.getAttribute("aria-expanded")).toBe("false");
    expect(list.className).toContain("hidden");
  });

  it("carries no anchor of its own, and fails over to the notice without moving or resizing", () => {
    const { unmount } = renderWithProviders(<TimeSlider />);
    const loadedClassName = screen.getByTestId("time-slider").className;
    unmount();

    useTimeSliderStore.setState({ capabilities: null, capabilitiesUnavailable: true });
    renderWithProviders(<TimeSlider />);

    // The documented invariant: a fetch that later succeeds must not reposition or resize
    // anything, so both states render from one shared class list.
    expect(screen.getByTestId("time-slider-unavailable").className).toBe(loadedClassName);
    // Positioning now belongs to the panel-region shell in TimeSliderPanel. The old
    // bottom-centre dock (`absolute bottom-24 left-1/2 -translate-x-1/2`) is gone: the slider
    // fills the region's column instead of anchoring itself over the canvas.
    expect(loadedClassName).toContain("w-full");
    expect(loadedClassName).not.toMatch(/absolute|bottom-24|left-1\/2/);
  });

  it("no longer claims the map itself is dateless -- a sibling change made that untrue", () => {
    renderWithProviders(<TimeSlider />);
    act(() => {
      useTimeSliderStore.getState().setSelectedDate("2019-01-20");
    });

    expect(screen.queryByTestId("time-slider-dateless-map-notice")).toBeNull();
  });

  it("commits a typed date and bounds the field to the domain", () => {
    renderWithProviders(<TimeSlider />);
    const field = screen.getByTestId("time-slider-date-input") as HTMLInputElement;

    // The whole reason this field exists: at ~384px wide the track gives a quarter-pixel per
    // day, so a specific day four years back is unreachable by dragging.
    expect(field.min).toBe(FIRST_DAY);
    expect(field.max).toBe(LAST_DAY);

    fireEvent.change(field, { target: { value: "2019-02-14" } });
    expect(useTimeSliderStore.getState().selectedDate).toBe("2019-02-14");
    expect((screen.getByTestId("time-slider-date-input") as HTMLInputElement).value).toBe(
      "2019-02-14"
    );
  });

  it("holds a partly typed date without pushing the map anywhere", () => {
    renderWithProviders(<TimeSlider />);
    act(() => {
      useTimeSliderStore.getState().setSelectedDate("2019-02-14");
    });
    const field = screen.getByTestId("time-slider-date-input") as HTMLInputElement;

    // A `<input type="date">` reports "" for every incomplete entry. Committing that would
    // clamp the map to the domain's edge on the first keystroke of a typed date.
    fireEvent.change(field, { target: { value: "" } });
    expect(useTimeSliderStore.getState().selectedDate).toBe("2019-02-14");

    // And the field never gets left blank beside a map that is drawing some day.
    fireEvent.blur(field);
    expect((screen.getByTestId("time-slider-date-input") as HTMLInputElement).value).toBe(
      "2019-02-14"
    );
  });

  it("clamps a typed day that lies outside the domain", () => {
    renderWithProviders(<TimeSlider />);
    const field = screen.getByTestId("time-slider-date-input");

    // min/max are a hint the DOM can be driven around; the domain is not.
    fireEvent.change(field, { target: { value: "1997-01-01" } });
    expect(useTimeSliderStore.getState().selectedDate).toBe(FIRST_DAY);

    fireEvent.change(field, { target: { value: "2099-01-01" } });
    expect(useTimeSliderStore.getState().selectedDate).toBe(LAST_DAY);
  });

  it("names both halves of the track, and says which side the selection is on", () => {
    renderWithProviders(<TimeSlider />);

    // The colours are a convention a first-time reader cannot look up, and "is the right-hand
    // end a prediction?" is the question this control was failing to answer.
    const key = screen.getByTestId("time-slider-track-key");
    expect(key.textContent).toContain("Observed");
    expect(key.textContent).toContain("Forecast");

    act(() => {
      useTimeSliderStore.getState().setSelectedDate("2019-02-14");
    });
    expect(screen.getByTestId("time-slider-day-kind").textContent).toBe("Observed");

    act(() => {
      useTimeSliderStore.getState().setSelectedDate("2019-03-14");
    });
    expect(screen.getByTestId("time-slider-day-kind").textContent).toBe("Beyond the record");
  });

  it("keeps the observed fill out of the future band", () => {
    renderWithProviders(<TimeSlider />);
    act(() => {
      useTimeSliderStore.getState().setSelectedDate("2019-03-14");
    });

    // Green means observed. Running it past the today tick would paint the exact claim this
    // control exists to avoid -- that there is a record over there.
    const observedFill = screen.getByTestId("time-slider-observed-fill");
    const todayPercent = (dayOffset(FIRST_DAY, SERVER_CURRENT_DATE) /
      dayOffset(FIRST_DAY, LAST_DAY)) * 100;
    expect(observedFill.style.width).toBe(`${todayPercent}%`);
    expect(screen.getByTestId("time-slider-future-fill")).not.toBeNull();
  });

  it("jumps back to the server's today, and cannot be pressed while already there", () => {
    renderWithProviders(<TimeSlider />);
    act(() => {
      useTimeSliderStore.getState().setSelectedDate(FIRST_DAY);
    });

    const todayButton = screen.getByTestId("time-slider-today-button") as HTMLButtonElement;
    expect(todayButton.disabled).toBe(false);
    fireEvent.click(todayButton);

    expect(useTimeSliderStore.getState().selectedDate).toBe(SERVER_CURRENT_DATE);
    expect(
      (screen.getByTestId("time-slider-today-button") as HTMLButtonElement).disabled
    ).toBe(true);
  });
});

/**
 * Production's own shape: every layer reports `forecastHorizonDays: 0` and no variants,
 * because no forecast producer exists anywhere in the warehouse. The fixture above is the
 * opposite case, so neither covers the other.
 */
describe("TimeSlider with nothing forecasting", () => {
  const NO_FORECAST_CAPABILITIES: SliderCapabilities = {
    serverCurrentDate: SERVER_CURRENT_DATE,
    futureAxisDays: 30,
    layers: CAPABILITIES.layers.map((layer) => ({
      ...layer,
      forecastHorizonDays: 0,
      forecastVariants: [],
    })),
  };

  beforeEach(() => {
    useTimeSliderStore.setState({
      capabilities: NO_FORECAST_CAPABILITIES,
      selectedDate: SERVER_CURRENT_DATE,
      forecastVariant: "monte_carlo",
      capabilitiesUnavailable: false,
    });
  });

  it("shows no forecast picker at all", () => {
    renderWithProviders(<TimeSlider />);

    // Two permanently inert buttons reading "Monte Carlo" and "ML" were the most prominent
    // thing in this card, and made a scrubber over four years of measured history read as a
    // forecast tool. Withholding is now stated in words instead.
    expect(screen.queryByRole("radiogroup", { name: "Forecast variant" })).toBeNull();
    expect(screen.queryByTestId("time-slider-variant-hint")).toBeNull();
  });

  it("still draws a future band, and labels it as publishing nothing", () => {
    renderWithProviders(<TimeSlider />);

    expect(screen.getByTestId("time-slider-future-hatch")).not.toBeNull();
    const key = screen.getByTestId("time-slider-track-key");
    expect(key.textContent).toContain("Nothing published");
    expect(key.textContent).not.toContain("Forecast");
  });

  it("replaces the forecast-band key with the truth when the selection is past today", () => {
    renderWithProviders(<TimeSlider />);
    act(() => {
      useTimeSliderStore.getState().setSelectedDate(addDays(SERVER_CURRENT_DATE, 10));
    });

    // The band key describes median colour and uncertainty opacity for a band no producer
    // emits, so here it would be a straight falsehood.
    expect(screen.queryByTestId("forecast-band-key")).toBeNull();
    expect(screen.getByTestId("time-slider-no-forecast-notice").textContent).toContain(
      SERVER_CURRENT_DATE
    );
  });
});
