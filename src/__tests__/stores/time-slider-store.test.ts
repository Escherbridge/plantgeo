import { describe, it, expect, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  UNINITIALIZED_DATE,
  addDays,
  clampDateToDomain,
  dayOffset,
  findLayerCapability,
  focusedResourceDomain,
  isFutureDate,
  layerAvailabilityAt,
  resolveVariant,
  sliderDomain,
  sliderMaxOffset,
  todayOffset,
  useTimeSliderStore,
} from "@/stores/time-slider-store";
import type { SliderCapabilities, SliderLayerCapability } from "@/types/time-slider";

// Deliberately nowhere near the machine's own date: any stray `new Date()` in the
// logic under test flips these expectations and fails the suite.
const SERVER_CURRENT_DATE = "2019-03-07";

/** geo.layers.name values are real; geo.layers holds exactly these eight rows. */
const vegetationLayer: SliderLayerCapability = {
  layerName: "vegetation",
  temporalKind: "daily_series",
  forecastHorizonDays: 10,
  forecastVariants: ["monte_carlo"],
  earliestObservedDate: "2015-06-01",
};

const weatherLayer: SliderLayerCapability = {
  layerName: "weather-observations",
  temporalKind: "daily_series",
  forecastHorizonDays: 30,
  forecastVariants: ["monte_carlo", "ml"],
  earliestObservedDate: "2017-01-15",
};

const firePerimeterLayer: SliderLayerCapability = {
  layerName: "fire-perimeters",
  temporalKind: "event",
  forecastHorizonDays: 0,
  forecastVariants: [],
  earliestObservedDate: "2018-07-04",
};

const sensorLayer: SliderLayerCapability = {
  layerName: "sensors",
  temporalKind: "snapshot",
  forecastHorizonDays: 0,
  forecastVariants: [],
  earliestObservedDate: null,
};

const capabilities: SliderCapabilities = {
  serverCurrentDate: SERVER_CURRENT_DATE,
  // 0 so every domain assertion below still measures the FORECAST horizon alone; the
  // future-axis span has its own tests rather than shifting these.
  futureAxisDays: 0,
  layers: [vegetationLayer, weatherLayer, firePerimeterLayer, sensorLayer],
};

/** Runs `body` under a fixed IANA zone so local-midnight bugs surface as diffs. */
function withTimeZone<T>(timeZone: string, body: () => T): T {
  const previous = process.env.TZ;
  process.env.TZ = timeZone;
  try {
    return body();
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
}

describe("sliderDomain", () => {
  it("derives both ends from the payload, so a different payload moves both", () => {
    const shallowHistory: SliderCapabilities = {
      serverCurrentDate: SERVER_CURRENT_DATE,
      // 0 so every domain assertion below still measures the FORECAST horizon alone; the
      // future-axis span has its own tests rather than shifting these.
      futureAxisDays: 0,
      layers: [
        { ...vegetationLayer, earliestObservedDate: "2018-02-20", forecastHorizonDays: 3 },
        { ...weatherLayer, earliestObservedDate: "2018-11-05", forecastHorizonDays: 7 },
      ],
    };

    const deep = sliderDomain(capabilities);
    const shallow = sliderDomain(shallowHistory);

    expect(deep).toEqual({
      firstDay: "2015-06-01",
      today: "2019-03-07",
      lastDay: "2019-04-06",
    });
    expect(shallow).toEqual({
      firstDay: "2018-02-20",
      today: "2019-03-07",
      lastDay: "2019-03-14",
    });
    // Both ends must differ between payloads; a hardcoded domain cannot satisfy this.
    expect(shallow?.firstDay).not.toBe(deep?.firstDay);
    expect(shallow?.lastDay).not.toBe(deep?.lastDay);
  });

  it("takes lastDay from the longest horizon, not the first layer's", () => {
    const domain = sliderDomain(capabilities);
    expect(domain?.lastDay).toBe(addDays(SERVER_CURRENT_DATE, 30));
    expect(domain?.lastDay).not.toBe(addDays(SERVER_CURRENT_DATE, 10));
  });

  it("extends the axis past today by futureAxisDays when no layer forecasts", () => {
    // Production's exact shape: every horizon 0, so before futureAxisDays existed lastDay
    // WAS today and there was no future side of the track to colour at all.
    const nothingForecasts: SliderCapabilities = {
      serverCurrentDate: SERVER_CURRENT_DATE,
      futureAxisDays: 30,
      layers: [
        { ...vegetationLayer, forecastHorizonDays: 0, forecastVariants: [] },
        { ...firePerimeterLayer },
      ],
    };

    expect(sliderDomain(nothingForecasts)?.lastDay).toBe(addDays(SERVER_CURRENT_DATE, 30));
    expect(sliderDomain({ ...nothingForecasts, futureAxisDays: 0 })?.lastDay).toBe(
      SERVER_CURRENT_DATE
    );
  });

  it("keeps a forecast horizon that reaches further than the drawn axis span", () => {
    // The two spans are different claims -- how far a layer can be ANSWERED for, and how far
    // the axis is DRAWN -- so the axis must contain the horizon rather than truncate it.
    const longHorizon: SliderCapabilities = {
      ...capabilities,
      futureAxisDays: 5,
      layers: [{ ...weatherLayer, forecastHorizonDays: 30 }],
    };
    expect(sliderDomain(longHorizon)?.lastDay).toBe(addDays(SERVER_CURRENT_DATE, 30));
  });

  it("does not let a snapshot layer's publication date define the axis start", () => {
    // The case this exists for: `watersheds` persisted 9,396 HUC12 basins whose WBD loaddate is
    // 2013-01-18 for 96% of them. Counting that would pull the axis back past 2018 for a boundary
    // set that draws identically on every one of those days.
    const withSnapshot: SliderCapabilities = {
      ...capabilities,
      layers: [
        vegetationLayer,
        {
          ...sensorLayer,
          layerName: "watersheds",
          temporalKind: "snapshot",
          earliestObservedDate: "2013-01-18",
        },
      ],
    };

    expect(sliderDomain(withSnapshot)?.firstDay).toBe(vegetationLayer.earliestObservedDate);
    // An EVENT layer that far back is a different claim -- things really did happen on those days.
    const withEvent: SliderCapabilities = {
      ...withSnapshot,
      layers: [
        vegetationLayer,
        { ...firePerimeterLayer, temporalKind: "event", earliestObservedDate: "2013-01-18" },
      ],
    };
    expect(sliderDomain(withEvent)?.firstDay).toBe("2013-01-18");
  });

  it("falls back to snapshots when nothing on the map varies over time", () => {
    // A domain derived from snapshots alone still beats no slider at all.
    const snapshotsOnly: SliderCapabilities = {
      ...capabilities,
      layers: [
        { ...sensorLayer, temporalKind: "snapshot", earliestObservedDate: "2019-03-01" },
        { ...sensorLayer, layerName: "watersheds", temporalKind: "snapshot", earliestObservedDate: "2013-01-18" },
      ],
    };
    expect(sliderDomain(snapshotsOnly)?.firstDay).toBe("2013-01-18");
  });

  it("refuses a negative or absent futureAxisDays rather than inverting the axis", () => {
    // A payload from a server that predates the field, and a malformed one. Either would put
    // lastDay before today and make every offset on the track negative.
    const absent = { ...capabilities, futureAxisDays: undefined } as unknown as SliderCapabilities;
    expect(sliderDomain(absent)?.lastDay).toBe(addDays(SERVER_CURRENT_DATE, 30));
    expect(sliderDomain({ ...capabilities, futureAxisDays: -10 })?.lastDay).toBe(
      addDays(SERVER_CURRENT_DATE, 30)
    );
  });

  it("ignores layers with no observed history and returns null when none have any", () => {
    const onlySensors: SliderCapabilities = {
      serverCurrentDate: SERVER_CURRENT_DATE,
      // 0 so every domain assertion below still measures the FORECAST horizon alone; the
      // future-axis span has its own tests rather than shifting these.
      futureAxisDays: 0,
      layers: [sensorLayer, { ...firePerimeterLayer, earliestObservedDate: null }],
    };
    expect(sliderDomain(onlySensors)).toBeNull();
    expect(sliderDomain(null)).toBeNull();
    // The null-history sensors layer must not drag firstDay anywhere.
    expect(sliderDomain(capabilities)?.firstDay).toBe("2015-06-01");
  });

  it("exposes integer offsets for the thumb and the hatched region", () => {
    const domain = sliderDomain(capabilities);
    if (domain === null) throw new Error("expected a domain");
    expect(todayOffset(domain)).toBe(dayOffset("2015-06-01", "2019-03-07"));
    expect(sliderMaxOffset(domain)).toBe(todayOffset(domain) + 30);
    expect(Number.isInteger(sliderMaxOffset(domain))).toBe(true);
  });
});

/**
 * The per-resource axis (2026-08-08). The global domain above is assembled from every published
 * layer at once -- earliest observation anywhere on the left, longest horizon anywhere on the
 * right -- so it can never answer "how far back does THIS resource go?". Focusing one does.
 */
describe("sliderDomain with a focused resource", () => {
  it("draws the focused layer's own record, not the warehouse's", () => {
    // vegetation observed from 2015-06-01 and forecasts 10 days; the global axis starts on the
    // same day but ends 30 days out, on weather-observations' horizon.
    expect(sliderDomain(capabilities, "vegetation")).toEqual({
      firstDay: "2015-06-01",
      today: SERVER_CURRENT_DATE,
      lastDay: addDays(SERVER_CURRENT_DATE, 10),
    });
    // Both ends move with the resource: fire-perimeters starts three years later and ends today.
    expect(sliderDomain(capabilities, "fire-perimeters")).toEqual({
      firstDay: "2018-07-04",
      today: SERVER_CURRENT_DATE,
      lastDay: SERVER_CURRENT_DATE,
    });
  });

  it("adds no futureAxisDays padding to a focused axis", () => {
    // The padding exists to make the observed/future boundary visible on an axis assembled from
    // every layer at once. A focused axis states one layer's own horizon, so padding it would
    // redraw the very band the focus exists to size honestly.
    const padded: SliderCapabilities = { ...capabilities, futureAxisDays: 45 };
    expect(sliderDomain(padded, "vegetation")?.lastDay).toBe(addDays(SERVER_CURRENT_DATE, 10));
    // ...while the unfocused axis still takes the padding, unchanged.
    expect(sliderDomain(padded)?.lastDay).toBe(addDays(SERVER_CURRENT_DATE, 45));
  });

  it("ends a zero-horizon resource's axis at today, drawing no future band", () => {
    const domain = sliderDomain(capabilities, "fire-perimeters");
    if (domain === null) throw new Error("expected a domain");
    // todayOffset === maxOffset is what the render reads as "no future band at all".
    expect(todayOffset(domain)).toBe(sliderMaxOffset(domain));
    expect(domain.lastDay).toBe(SERVER_CURRENT_DATE);
  });

  it("falls back to the global axis for an unknown or unobserved resource", () => {
    const global = sliderDomain(capabilities);
    // Not in this payload at all: a stale name must never empty the track or throw.
    expect(sliderDomain(capabilities, "evacuation-zones")).toEqual(global);
    // Published, but has observed nothing -- there is no record to range over.
    expect(sliderDomain(capabilities, "sensors")).toEqual(global);
    // An absurd payload whose first observed day is after the server's today would invert the
    // axis, so it falls back too rather than drawing firstDay past lastDay.
    const observedInTheFuture: SliderCapabilities = {
      ...capabilities,
      layers: [{ ...vegetationLayer, earliestObservedDate: "2020-01-01" }],
    };
    expect(sliderDomain(observedInTheFuture, "vegetation")).toEqual(
      sliderDomain(observedInTheFuture)
    );
  });

  it("refuses a focused snapshot's publication date as an axis, however real that date is", () => {
    // The fallback case above only covers `earliestObservedDate === null`, which never reaches
    // the snapshot guard at all. This is the case the guard exists for: `watersheds` carries a
    // real 2013-01-18 WBD loaddate, so without the exclusion focusing it would draw six years of
    // axis for a boundary set that is identical on every one of those days -- the very range
    // sliderDomain already refuses to take from it.
    const withWatersheds: SliderCapabilities = {
      ...capabilities,
      layers: [
        ...capabilities.layers,
        {
          layerName: "watersheds",
          temporalKind: "snapshot",
          forecastHorizonDays: 0,
          forecastVariants: [],
          earliestObservedDate: "2013-01-18",
        },
      ],
    };

    expect(focusedResourceDomain(withWatersheds, "watersheds")).toBeNull();
    expect(sliderDomain(withWatersheds, "watersheds")).toEqual(sliderDomain(withWatersheds));
    expect(sliderDomain(withWatersheds, "watersheds")?.firstDay).toBe("2015-06-01");
    // The same date on an EVENT layer is a different claim -- things really did happen on those
    // days -- and does range the focused axis, which is what makes this a snapshot rule and not
    // a rule about old dates.
    const withOldEvents: SliderCapabilities = {
      ...capabilities,
      layers: [
        ...capabilities.layers,
        { ...firePerimeterLayer, layerName: "burn-severity", earliestObservedDate: "2013-01-18" },
      ],
    };
    expect(sliderDomain(withOldEvents, "burn-severity")?.firstDay).toBe("2013-01-18");
  });

  it("leaves the global domain untouched when no resource is focused", () => {
    const global = sliderDomain(capabilities);
    expect(sliderDomain(capabilities, null)).toEqual(global);
    expect(sliderDomain(capabilities, undefined)).toEqual(global);
    expect(sliderDomain(null, "vegetation")).toBeNull();
  });
});

describe("isFutureDate", () => {
  it("compares against the server's date, never the machine's", () => {
    // Every date here is years in the past for the test runner's own clock.
    expect(isFutureDate("2019-03-08", capabilities)).toBe(true);
    expect(isFutureDate("2019-04-01", capabilities)).toBe(true);
    expect(isFutureDate(SERVER_CURRENT_DATE, capabilities)).toBe(false);
    expect(isFutureDate("2019-03-06", capabilities)).toBe(false);
  });

  it("selects the observed series up to today and the forecast series after", () => {
    expect(resolveVariant(SERVER_CURRENT_DATE, capabilities, "ml")).toBe("observed");
    expect(resolveVariant("2019-03-06", capabilities, "ml")).toBe("observed");
    expect(resolveVariant("2019-03-08", capabilities, "ml")).toBe("ml");
    expect(resolveVariant("2019-03-08", capabilities, "monte_carlo")).toBe("monte_carlo");
  });
});

describe("layerAvailabilityAt", () => {
  it("returns not_forecastable for an event layer one day after the server's today", () => {
    expect(
      layerAvailabilityAt(firePerimeterLayer, "2019-03-08", "monte_carlo", capabilities)
    ).toBe("not_forecastable");
    // The same layer at the server's today is perfectly publishable.
    expect(
      layerAvailabilityAt(firePerimeterLayer, SERVER_CURRENT_DATE, "monte_carlo", capabilities)
    ).toBe("published");
  });

  it("returns not_forecastable for a non-event layer with a zero-day horizon", () => {
    const snapshotOnly: SliderLayerCapability = { ...vegetationLayer, forecastHorizonDays: 0 };
    expect(layerAvailabilityAt(snapshotOnly, "2019-03-08", "monte_carlo", capabilities)).toBe(
      "not_forecastable"
    );
  });

  it("returns not_yet_observed before the layer's earliest version", () => {
    expect(
      layerAvailabilityAt(firePerimeterLayer, "2018-07-03", "monte_carlo", capabilities)
    ).toBe("not_yet_observed");
    expect(
      layerAvailabilityAt(firePerimeterLayer, "2018-07-04", "monte_carlo", capabilities)
    ).toBe("published");
    // A layer with no versions at all has observed nothing on any date.
    expect(layerAvailabilityAt(sensorLayer, "2019-03-06", "monte_carlo", capabilities)).toBe(
      "not_yet_observed"
    );
  });

  it("returns beyond_horizon only past the layer's own horizon", () => {
    expect(layerAvailabilityAt(weatherLayer, "2019-04-06", "ml", capabilities)).toBe("published");
    expect(layerAvailabilityAt(weatherLayer, "2019-04-07", "ml", capabilities)).toBe(
      "beyond_horizon"
    );
    expect(layerAvailabilityAt(vegetationLayer, "2019-03-18", "monte_carlo", capabilities)).toBe(
      "beyond_horizon"
    );
  });

  it("returns variant_unavailable when the layer does not publish that variant", () => {
    expect(layerAvailabilityAt(vegetationLayer, "2019-03-09", "ml", capabilities)).toBe(
      "variant_unavailable"
    );
    expect(layerAvailabilityAt(vegetationLayer, "2019-03-09", "monte_carlo", capabilities)).toBe(
      "published"
    );
    // A missing variant in the past is irrelevant; observations have no variant.
    expect(layerAvailabilityAt(vegetationLayer, "2019-03-06", "ml", capabilities)).toBe(
      "published"
    );
  });

  it("prefers the most specific reason when several apply", () => {
    // Before the earliest version AND outside every horizon: history wins.
    expect(layerAvailabilityAt(weatherLayer, "2016-01-01", "ml", capabilities)).toBe(
      "not_yet_observed"
    );
    // Event layer far past its horizon still reads as not forecastable, not beyond horizon.
    expect(layerAvailabilityAt(firePerimeterLayer, "2020-01-01", "ml", capabilities)).toBe(
      "not_forecastable"
    );
  });
});

describe("day arithmetic", () => {
  it("crosses month and year boundaries in UTC", () => {
    expect(addDays("2019-02-28", 1)).toBe("2019-03-01");
    expect(addDays("2020-02-28", 1)).toBe("2020-02-29");
    expect(addDays("2019-12-31", 1)).toBe("2020-01-01");
    expect(addDays("2019-03-01", -1)).toBe("2019-02-28");
    expect(dayOffset("2019-02-28", "2019-03-01")).toBe(1);
    expect(dayOffset("2019-03-07", "2019-04-06")).toBe(30);
    expect(dayOffset("2019-04-06", "2019-03-07")).toBe(-30);
  });

  it("gives the same answer across DST boundaries in any local zone", () => {
    const zones = ["UTC", "America/Los_Angeles", "Pacific/Kiritimati", "Pacific/Niue"];
    for (const zone of zones) {
      // 2019-03-10 is the US spring-forward; 2019-11-03 is the fall-back.
      expect(withTimeZone(zone, () => addDays("2019-03-09", 2))).toBe("2019-03-11");
      expect(withTimeZone(zone, () => addDays("2019-11-02", 2))).toBe("2019-11-04");
      expect(withTimeZone(zone, () => addDays("2019-10-26", 2))).toBe("2019-10-28");
      expect(withTimeZone(zone, () => dayOffset("2019-03-09", "2019-03-11"))).toBe(2);
      expect(withTimeZone(zone, () => dayOffset("2019-11-02", "2019-11-04"))).toBe(2);
    }
  });

  it("round-trips offsets so slider positions stay integers", () => {
    for (let offset = -400; offset <= 30; offset += 37) {
      expect(dayOffset("2019-03-07", addDays("2019-03-07", offset))).toBe(offset);
    }
  });

  it("leaves non-calendar strings alone instead of inventing a date", () => {
    expect(addDays(UNINITIALIZED_DATE, 1)).toBe(UNINITIALIZED_DATE);
    expect(dayOffset(UNINITIALIZED_DATE, "2019-03-07")).toBe(0);
    expect(clampDateToDomain(UNINITIALIZED_DATE, capabilities)).toBe(UNINITIALIZED_DATE);
  });
});

describe("clampDateToDomain", () => {
  it("holds a date inside the payload's domain", () => {
    expect(clampDateToDomain("2010-01-01", capabilities)).toBe("2015-06-01");
    expect(clampDateToDomain("2019-12-31", capabilities)).toBe("2019-04-06");
    expect(clampDateToDomain("2018-08-08", capabilities)).toBe("2018-08-08");
    expect(clampDateToDomain("2018-08-08", null)).toBe("2018-08-08");
  });

  it("clamps to the focused resource's axis, not the global one", () => {
    // 2016-04-09 is inside the global axis and years before fire-perimeters observed anything;
    // clamping to the global domain while the focused axis is drawn would leave the thumb off
    // its own track.
    expect(clampDateToDomain("2016-04-09", capabilities, "fire-perimeters")).toBe("2018-07-04");
    // Past a zero-horizon resource's right end, which is the server's today.
    expect(clampDateToDomain("2019-04-01", capabilities, "fire-perimeters")).toBe(
      SERVER_CURRENT_DATE
    );
    // The same date under the global axis is untouched, which is what makes the two differ.
    expect(clampDateToDomain("2016-04-09", capabilities)).toBe("2016-04-09");
    // A focus that defines no axis clamps exactly as no focus does.
    expect(clampDateToDomain("2016-04-09", capabilities, "sensors")).toBe("2016-04-09");
  });
});

describe("findLayerCapability", () => {
  it("looks layers up by their geo.layers name", () => {
    expect(findLayerCapability(capabilities, "fire-perimeters")).toBe(firePerimeterLayer);
    expect(findLayerCapability(capabilities, "evacuation-zones")).toBeNull();
    expect(findLayerCapability(null, "vegetation")).toBeNull();
  });
});

describe("useTimeSliderStore", () => {
  beforeEach(() => {
    useTimeSliderStore.setState({
      selectedDate: UNINITIALIZED_DATE,
      forecastVariant: "monte_carlo",
      capabilities: null,
      focusedLayerName: null,
    });
  });

  it("starts on a sentinel date that cannot be mistaken for a real one", () => {
    const { result } = renderHook(() => useTimeSliderStore());
    expect(result.current.selectedDate).toBe(UNINITIALIZED_DATE);
    expect(result.current.capabilities).toBeNull();
    expect(result.current.forecastVariant).toBe("monte_carlo");
    // "All resources": the whole-warehouse axis this control has always drawn.
    expect(result.current.focusedLayerName).toBeNull();
  });

  it("takes its first real selectedDate from the server's date", () => {
    const { result } = renderHook(() => useTimeSliderStore());

    act(() => {
      result.current.setCapabilities(capabilities);
    });

    expect(result.current.selectedDate).toBe(SERVER_CURRENT_DATE);
  });

  it("keeps an existing selection across a capabilities refresh, clamped to the domain", () => {
    const { result } = renderHook(() => useTimeSliderStore());

    act(() => {
      result.current.setCapabilities(capabilities);
      result.current.setSelectedDate("2016-04-09");
    });
    expect(result.current.selectedDate).toBe("2016-04-09");

    act(() => {
      result.current.setCapabilities({
        serverCurrentDate: SERVER_CURRENT_DATE,
        // 0 so every domain assertion below still measures the FORECAST horizon alone; the
        // future-axis span has its own tests rather than shifting these.
        futureAxisDays: 0,
        layers: [{ ...vegetationLayer, earliestObservedDate: "2018-02-20" }],
      });
    });
    expect(result.current.selectedDate).toBe("2018-02-20");
  });

  it("resetToToday returns to the server's date and is a no-op without capabilities", () => {
    const { result } = renderHook(() => useTimeSliderStore());

    act(() => {
      result.current.resetToToday();
    });
    expect(result.current.selectedDate).toBe(UNINITIALIZED_DATE);

    act(() => {
      result.current.setCapabilities(capabilities);
      result.current.setSelectedDate("2019-03-20");
    });
    act(() => {
      result.current.resetToToday();
    });
    expect(result.current.selectedDate).toBe(SERVER_CURRENT_DATE);
  });

  it("setForecastVariant switches the forecast series", () => {
    const { result } = renderHook(() => useTimeSliderStore());

    act(() => {
      result.current.setForecastVariant("ml");
    });

    expect(result.current.forecastVariant).toBe("ml");
  });

  it("setFocusedLayerName pulls the selection onto the new axis in the same update", () => {
    const { result } = renderHook(() => useTimeSliderStore());

    act(() => {
      result.current.setCapabilities(capabilities);
      result.current.setSelectedDate("2016-04-09");
    });
    expect(result.current.selectedDate).toBe("2016-04-09");

    // fire-perimeters observed nothing before 2018-07-04, so the day that was legal a moment
    // ago now sits left of the track. A two-step (focus now, clamp later) would publish one
    // render with the thumb outside its own axis.
    act(() => {
      result.current.setFocusedLayerName("fire-perimeters");
    });
    expect(result.current.focusedLayerName).toBe("fire-perimeters");
    expect(result.current.selectedDate).toBe("2018-07-04");

    // Widening back to every resource clamps nothing away: the day is already inside.
    act(() => {
      result.current.setFocusedLayerName(null);
    });
    expect(result.current.focusedLayerName).toBeNull();
    expect(result.current.selectedDate).toBe("2018-07-04");
  });

  it("setFocusedLayerName is a view over the axis, never a claim about the map", () => {
    const { result } = renderHook(() => useTimeSliderStore());

    act(() => {
      result.current.setCapabilities(capabilities);
      result.current.setSelectedDate("2018-08-08");
      result.current.setFocusedLayerName("vegetation");
    });

    // Inside vegetation's axis already, so the day is untouched -- and it is still the day
    // EVERY layer draws as of. Focus changes the range, not what is drawn.
    expect(result.current.selectedDate).toBe("2018-08-08");
  });

  it("setCapabilities clamps against the focus that is on screen", () => {
    const { result } = renderHook(() => useTimeSliderStore());

    act(() => {
      result.current.setCapabilities(capabilities);
      result.current.setFocusedLayerName("weather-observations");
      result.current.setSelectedDate("2017-06-01");
    });
    expect(result.current.selectedDate).toBe("2017-06-01");

    // A refreshed payload moves weather-observations' record forward. The clamp must follow the
    // FOCUSED axis; against the global one 2017-06-01 is still legal and nothing would move.
    act(() => {
      result.current.setCapabilities({
        ...capabilities,
        layers: [
          vegetationLayer,
          { ...weatherLayer, earliestObservedDate: "2018-01-15" },
          firePerimeterLayer,
          sensorLayer,
        ],
      });
    });
    expect(result.current.selectedDate).toBe("2018-01-15");
  });
});
